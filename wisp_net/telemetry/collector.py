"""Collector: one cycle turns raw telemetry into metrics, twin state, events and alerts.

Inputs are what real collectors receive: a gNMI SAMPLE across all targets, RFC 5424
syslog lines, and IPFIX-style flow records. Counters become rates from consecutive
samples (a counter that goes backwards is a reset, not a negative rate).
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict
from typing import Any, Protocol

from wisp_net.state.tsdb import MetricStore
from wisp_net.state.twin import DigitalTwin
from wisp_net.telemetry import syslog
from wisp_net.telemetry.alerts import AlertEngine
from wisp_net.telemetry.bus import (
    TOPIC_ALERTS,
    TOPIC_EVENTS,
    TOPIC_FLOWS,
    TOPIC_METRICS,
    TOPIC_TOPOLOGY,
    TelemetryBus,
)


class GnmiClient(Protocol):
    def targets(self) -> list[str]: ...
    def subscribe_sample(self, paths: list[str], target: str = "*") -> list[Any]: ...


_RATE_COUNTERS = {"in-octets": "rx_bps", "out-octets": "tx_bps", "in-fcs-errors": "fcs_per_s",
                  "in-discards": "in_discards_per_s", "out-discards": "out_discards_per_s"}


class Collector:
    def __init__(self, gnmi: GnmiClient, bus: TelemetryBus, tsdb: MetricStore, twin: DigitalTwin,
                 alerts: AlertEngine, event_buffer: int = 5000, flow_window: int = 20_000) -> None:
        self.gnmi, self.bus, self.tsdb, self.twin, self.alerts = gnmi, bus, tsdb, twin, alerts
        self._prev: dict[tuple[str, str, str], tuple[float, float]] = {}
        self.events: deque[syslog.NetEvent] = deque(maxlen=event_buffer)
        self.flows: deque[dict[str, Any]] = deque(maxlen=flow_window)
        self.dedup = syslog.Deduplicator()
        self.parse_errors = 0
        self.last_view: dict[str, Any] = {}
        self.cycles = 0

    def collect(self, now: float) -> dict[str, Any]:
        notes = self.gnmi.subscribe_sample(["/"], "*")
        by_target: dict[str, list[Any]] = {}
        for n in notes:
            by_target.setdefault(n.target, []).append(n)
        unreachable = [t for t in self.gnmi.targets() if t not in by_target]
        for target, tnotes in by_target.items():
            self.twin.ingest(target, tnotes, now)
        for target in unreachable:
            self.twin.mark_unreachable(target, now)
        changed = self.twin.rebuild(now)
        view = self._derive(now, by_target, unreachable)
        self._store(now, view)
        if changed or self.cycles == 0:
            snap = self.twin.snapshot(now, "topology change" if self.cycles else "initial")
            self.bus.publish(TOPIC_TOPOLOGY, "fabric", {"version": snap.version, "ts": now,
                                                        "links": snap.entities["links"]}, now)
        for change in self.alerts.evaluate(now, view):
            self.bus.publish(TOPIC_ALERTS, f"{change['rule']}|{change['device']}|{change['subject']}", change, now)
        self.last_view = view
        self.cycles += 1
        return view

    def _derive(self, now: float, by_target: dict[str, list[Any]], unreachable: list[str]) -> dict[str, Any]:
        view: dict[str, Any] = {"ts": now, "unreachable": unreachable, "interfaces": {}, "cpu": {}, "bgp": {},
                                "mem_pct": {}}
        for target, notes in by_target.items():
            st = self.twin.devices[target]
            ifaces: dict[str, dict[str, Any]] = {}
            for ifname, iface in st.interfaces.items():
                m: dict[str, Any] = {"oper": iface.get("oper-status"), "admin": iface.get("admin-status"),
                                     "speed_bps": iface.get("speed_bps", 0.0),
                                     "rx_power_dbm": iface.get("rx_power_dbm"),
                                     "kind": "fabric" if "rx_power_dbm" in iface else "edge-or-access",
                                     "peer": st.lldp.get(ifname)}
                for counter, metric in _RATE_COUNTERS.items():
                    value = iface.get("counters", {}).get(counter)
                    if value is not None:
                        m[metric] = self._rate(target, ifname, counter, now, float(value))
                speed = m["speed_bps"] or 0.0
                m["util"] = (max(m.get("tx_bps", 0.0), m.get("rx_bps", 0.0)) / speed) if speed else 0.0
                m["tx_util"] = (m.get("tx_bps", 0.0) / speed) if speed else 0.0
                ifaces[ifname] = m
            view["interfaces"][target] = ifaces
            view["cpu"][target] = st.system.get("cpu_pct", 0.0)
            total = st.system.get("mem_physical") or 0
            view["mem_pct"][target] = 100.0 * (st.system.get("mem_used") or 0) / total if total else 0.0
            view["bgp"][target] = {
                ip: {"state": p.get("session-state"), "enabled": p.get("enabled", True),
                     "peer_as": p.get("peer-as"), "transitions": p.get("established-transitions", 0),
                     "installed": p.get("installed", 0), "description": p.get("description")}
                for ip, p in st.bgp.items()}
        return view

    def _rate(self, target: str, ifname: str, counter: str, now: float, value: float) -> float:
        key = (target, ifname, counter)
        prev = self._prev.get(key)
        self._prev[key] = (now, value)
        if prev is None or now <= prev[0] or value < prev[1]:
            return 0.0
        delta = value - prev[1]
        if counter.endswith("octets"):
            delta *= 8
        return delta / (now - prev[0])

    def _store(self, now: float, view: dict[str, Any]) -> None:
        points: list[tuple[str, float, float]] = []
        for dev, ifaces in view["interfaces"].items():
            summary: dict[str, Any] = {}
            for ifname, m in ifaces.items():
                points.append((f"{dev}|if.oper_up|{ifname}", now, 1.0 if m["oper"] == "UP" else 0.0))
                for metric in ("rx_bps", "tx_bps", "fcs_per_s", "out_discards_per_s", "util"):
                    if metric in m:
                        points.append((f"{dev}|if.{metric}|{ifname}", now, float(m[metric])))
                if m.get("rx_power_dbm") is not None:
                    points.append((f"{dev}|if.rx_power_dbm|{ifname}", now, float(m["rx_power_dbm"])))
                summary[ifname] = {k: m.get(k) for k in ("oper", "util", "fcs_per_s", "rx_power_dbm")}
            points.append((f"{dev}|sys.cpu_pct|{dev}", now, float(view["cpu"].get(dev, 0.0))))
            points.append((f"{dev}|sys.mem_pct|{dev}", now, float(view["mem_pct"].get(dev, 0.0))))
            peers = view["bgp"].get(dev, {})
            established = sum(1 for p in peers.values() if p["state"] == "ESTABLISHED")
            points.append((f"{dev}|bgp.established|{dev}", now, float(established)))
            self.bus.publish(TOPIC_METRICS, dev, {"ts": now, "device": dev, "interfaces": summary,
                                                  "cpu_pct": view["cpu"].get(dev),
                                                  "bgp_established": established}, now)
        self.tsdb.write_many(points)

    def ingest_syslog(self, lines: list[str]) -> list[syslog.NetEvent]:
        admitted: list[syslog.NetEvent] = []
        for line in lines:
            try:
                event = syslog.normalize(syslog.parse(line))
            except syslog.SyslogParseError:
                self.parse_errors += 1
                continue
            kept = self.dedup.admit(event)
            if kept is None:
                continue
            self.events.append(kept)
            admitted.append(kept)
            self.bus.publish(TOPIC_EVENTS, f"{kept.device}|{kept.kind}|{kept.subject}", asdict(kept),
                             kept.timestamp)
        return admitted

    def ingest_flows(self, records: list[dict[str, Any]]) -> None:
        for r in records:
            self.flows.append(r)
            self.bus.publish(TOPIC_FLOWS, str(r.get("exporter")), r, float(r.get("ts", 0.0)))

    def top_flows(self, since: float, n: int = 10, group_by: str = "pair") -> list[dict[str, Any]]:
        """Heaviest traffic since `since`, grouped by src/dst pair or by destination port."""
        agg: dict[tuple[Any, ...], dict[str, Any]] = {}
        for r in self.flows:
            if r["ts"] < since:
                continue
            key: tuple[Any, ...] = (r["dport"],) if group_by == "dport" else (r["src"], r["dst"], r["dport"])
            a = agg.setdefault(key, {"octets": 0, "packets": 0, "exporters": set()})
            a["octets"] += r["octets"]
            a["packets"] += r["packets"]
            a["exporters"].add(r["exporter"])
        rows = []
        for key, a in sorted(agg.items(), key=lambda kv: -kv[1]["octets"])[:n]:
            row = {"dport": key[0]} if group_by == "dport" else {"src": key[0], "dst": key[1], "dport": key[2]}
            row.update({"octets": a["octets"], "packets": a["packets"], "exporters": sorted(a["exporters"])})
            rows.append(row)
        return rows
