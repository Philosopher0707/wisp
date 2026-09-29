"""NetService: the network platform the agent talks to — sense and state, read-only.

Composition: the simulated lab (the world), the gNMI facade, the telemetry bus, the
collector with alert rules, the time-series store, the digital twin and the knowledge
base. Time is the lab clock: `advance()` steps the world and runs a collection cycle
every `collect_interval_s`, and a background thread can drive it in real time.

Every public read method returns plain JSON-able data, bounded in size, because its
caller is a language model with a finite context.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from wisp_net.actuation.engine import Actuator
from wisp_net.governance.control import ApprovalQueue, KillSwitch
from wisp_net.governance.ledger import Ledger
from wisp_net.reasoning import compliance, diagnostics, intents, security, traffic
from wisp_net.safety.change import ChangeSet
from wisp_net.safety.policy import ChangePolicy
from wisp_net.safety.verify import SegmentationPolicy, acl_findings, segmentation_violations
from wisp_net.safety.whatif import what_if
from wisp_net.sim.config import apply_set
from wisp_net.sim.gnmi import SimGnmi
from wisp_net.sim.network import FAULT_KINDS, SimNetwork, rfc3339
from wisp_net.sim.topology import build_lab
from wisp_net.state.knowledge import KnowledgeBase
from wisp_net.state.tsdb import MetricStore
from wisp_net.state.twin import DigitalTwin, Snapshot
from wisp_net.telemetry.alerts import SEVERITY_RANK, AlertEngine
from wisp_net.telemetry.bus import TelemetryBus
from wisp_net.telemetry.collector import Collector

MAX_ROWS = 200


class NetService:
    def __init__(self, seed: int = 7, collect_interval_s: float = 5.0, leaves: int = 4, spines: int = 2,
                 cores: int = 2, tsdb_path: str = ":memory:", scenario: list[dict[str, Any]] | None = None,
                 warmup_s: float = 60.0, ledger_path: str | None = None) -> None:
        self._lock = threading.RLock()
        self.net = SimNetwork(build_lab(cores=cores, spines=spines, leaves=leaves), seed=seed)
        self.gnmi = SimGnmi(self.net)
        self.bus = TelemetryBus()
        self.tsdb = MetricStore(tsdb_path)
        self.twin = DigitalTwin()
        self.alerts = AlertEngine()
        self.collector = Collector(self.gnmi, self.bus, self.tsdb, self.twin, self.alerts)
        self.kb = KnowledgeBase.bundled()
        self.segmentation = SegmentationPolicy.bundled()
        self.change_policy = ChangePolicy.bundled()
        self.verifications: dict[str, dict[str, Any]] = {}
        self.ledger = Ledger(ledger_path)
        self.approvals = ApprovalQueue()
        self.kill_switch = KillSwitch()
        self.config_epoch = 0
        self.actuator = Actuator(self)
        self.watcher: Any = None
        self.collect_interval_s = collect_interval_s
        self.started = self.net.now
        self._next_collect = self.net.now
        self._scenario = sorted(scenario or [], key=lambda s: float(s.get("at", 0)))
        self._scenario_ids: dict[str, str] = {}
        self._runner: threading.Thread | None = None
        self._stop = threading.Event()
        self.collect_now()
        if warmup_s > 0:
            self.advance(warmup_s)

    # ── clock ────────────────────────────────────────────────────────────────

    def collect_now(self) -> None:
        with self._lock:
            self.collector.ingest_syslog(self.net.drain_syslog())
            self.collector.ingest_flows(self.net.drain_flows())
            self.collector.collect(self.net.now)
            self._next_collect = self.net.now + self.collect_interval_s
            if self.watcher is not None:
                self.watcher.poll()

    def advance(self, seconds: float, dt: float = 1.0) -> dict[str, Any]:
        if seconds < 0:
            raise ValueError("seconds must be >= 0")
        with self._lock:
            target = self.net.now + seconds
            while self.net.now + 1e-9 < target:
                self._run_scenario()
                self.net.step(min(dt, target - self.net.now))
                if self.net.now + 1e-9 >= self._next_collect:
                    self.collect_now()
            return {"now": rfc3339(self.net.now), "elapsed_s": round(self.net.now - self.started, 1)}

    def _run_scenario(self) -> None:
        elapsed = self.net.now - self.started
        while self._scenario and float(self._scenario[0].get("at", 0)) <= elapsed:
            step = self._scenario.pop(0)
            if "inject" in step:
                fid = self.net.inject(step["inject"], step["target"], **step.get("params", {}))
                if step.get("name"):
                    self._scenario_ids[step["name"]] = fid
            elif "clear" in step:
                self.net.clear(self._scenario_ids.get(step["clear"], step["clear"]))
            elif "config" in step:
                # An out-of-band commit (someone on the CLI): drift the compliance agent should find.
                c = step["config"]
                dev = c["device"]
                updates = [(c["path"], c["value"])] if "value" in c else []
                deletes = [] if "value" in c else [c["path"]]
                self.net.apply_config(dev, apply_set(self.net.devices[dev].config, updates, deletes),
                                      user=c.get("user", "unknown"))

    def start_realtime(self, speed: float = 1.0) -> None:
        """Advance the lab `speed` seconds per wall-clock second on a daemon thread."""
        if self._runner is not None:
            return
        self._stop.clear()

        def run() -> None:
            last = time.monotonic()
            while not self._stop.wait(0.5):
                now = time.monotonic()
                self.advance((now - last) * speed)
                last = now

        self._runner = threading.Thread(target=run, name="wisp-net-clock", daemon=True)
        self._runner.start()

    def stop(self) -> None:
        self._stop.set()
        if self._runner is not None:
            self._runner.join(timeout=5)
            self._runner = None
        self.tsdb.close()

    # ── lab control (operator, not agent) ────────────────────────────────────

    def inject(self, kind: str, target: str, **params: Any) -> dict[str, Any]:
        with self._lock:
            return {"fault_id": self.net.inject(kind, target, **params), "kind": kind, "target": target}

    def clear_fault(self, fault_id: str) -> dict[str, Any]:
        with self._lock:
            self.net.clear(fault_id)
            return {"cleared": fault_id}

    def faults(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(f) for f in self.net.faults.values()]

    # ── reads ────────────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        with self._lock:
            open_alerts = self.alerts.list("open")
            by_sev: dict[str, int] = {}
            for a in open_alerts:
                by_sev[a.severity] = by_sev.get(a.severity, 0) + 1
            reachable = [d for d, st in self.twin.devices.items() if st.reachable]
            return {
                "lab_time": rfc3339(self.net.now), "elapsed_s": round(self.net.now - self.started, 1),
                "devices": len(self.twin.devices), "reporting": len(reachable),
                "links": len(self.twin.links), "links_down": sum(1 for v in self.twin.links.values() if not v["up"]),
                "open_alerts": by_sev, "twin_version": self.twin.snapshots[-1].version if self.twin.snapshots else 0,
                "collector": {"cycles": self.collector.cycles, "interval_s": self.collect_interval_s,
                              "syslog_parse_errors": self.collector.parse_errors,
                              "events_suppressed": self.collector.dedup.suppressed},
                "bus": {t: self.bus.end_offset(t) for t in self.bus.topics()},
            }

    def devices(self) -> list[dict[str, Any]]:
        with self._lock:
            view = self.collector.last_view
            rows = []
            for name, st in sorted(self.twin.devices.items()):
                peers = st.bgp.values()
                rows.append({
                    "name": name, "reporting": st.reachable, "software": st.system.get("software-version"),
                    "asn": st.bgp_global.get("as"), "router_id": st.bgp_global.get("router-id"),
                    "cpu_pct": view.get("cpu", {}).get(name), "last_seen": rfc3339(st.last_seen) if st.last_seen else None,
                    "bgp": f"{sum(1 for p in peers if p.get('session-state') == 'ESTABLISHED')}/{len(st.bgp)} established",
                    "networks": st.networks})
            return rows

    def topology(self) -> list[dict[str, Any]]:
        with self._lock:
            ifs = self.collector.last_view.get("interfaces", {})
            rows = []
            for key, link in sorted(self.twin.links.items(), key=lambda kv: sorted(kv[0])):
                (a, ai), (b, bi) = sorted(key)
                ma, mb = ifs.get(a, {}).get(ai, {}), ifs.get(b, {}).get(bi, {})
                rows.append({"a": f"{a}:{ai}", "b": f"{b}:{bi}", "up": link["up"],
                             "speed_gbps": round(link["speed_bps"] / 1e9),
                             "util_a_to_b": _pct(ma.get("tx_bps"), link["speed_bps"]),
                             "util_b_to_a": _pct(mb.get("tx_bps"), link["speed_bps"])})
            return rows

    def interfaces(self, device: str) -> list[dict[str, Any]]:
        with self._lock:
            st = self._device(device)
            view = self.collector.last_view.get("interfaces", {}).get(device, {})
            rows = []
            for ifname, iface in sorted(st.interfaces.items(), key=lambda kv: _natural(kv[0])):
                m = view.get(ifname, {})
                rows.append({"name": ifname, "description": iface.get("description"),
                             "admin": iface.get("admin-status"), "oper": iface.get("oper-status"),
                             "speed_gbps": round(iface.get("speed_bps", 0) / 1e9), "ip": iface.get("ip"),
                             "peer": "{}:{}".format(*st.lldp[ifname]) if ifname in st.lldp else None,
                             "rx_gbps": _gbps(m.get("rx_bps")), "tx_gbps": _gbps(m.get("tx_bps")),
                             "util_pct": _pct(max(m.get("tx_bps", 0.0), m.get("rx_bps", 0.0)),
                                              iface.get("speed_bps", 0)),
                             "fcs_errors_per_s": round(m.get("fcs_per_s", 0.0), 1),
                             "out_discards_per_s": round(m.get("out_discards_per_s", 0.0), 1),
                             "rx_power_dbm": iface.get("rx_power_dbm"),
                             "fcs_errors_total": iface.get("counters", {}).get("in-fcs-errors")})
            return rows

    def bgp(self, device: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            names = [device] if device else sorted(self.twin.devices)
            rows = []
            for name in names:
                st = self._device(name)
                for ip, p in sorted(st.bgp.items()):
                    rows.append({"device": name, "neighbor": ip, "peer_as": p.get("peer-as"),
                                 "description": p.get("description"), "enabled": p.get("enabled"),
                                 "state": p.get("session-state"), "prefixes_installed": p.get("installed"),
                                 "established_transitions": p.get("established-transitions")})
            return rows

    def routes(self, device: str, prefix: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            st = self._device(device)
            rows = []
            for p, e in sorted(st.fib.items()):
                if prefix and p != prefix:
                    continue
                rows.append({"prefix": p, "origin": e.get("origin-protocol"), "as_path": e.get("as-path"),
                             "next_hops": [f"{h['interface']} -> {h['peer']} ({h['ip-address']})"
                                           for h in e.get("next-hops") or []]})
            return rows

    def trace(self, source: str, destination: str) -> dict[str, Any]:
        with self._lock:
            self._device(source)
            r = self.twin.trace(source, destination)
            return {"source": r.source, "destination": r.destination, "matched_prefix": r.prefix,
                    "outcome": r.outcome, "paths": [" -> ".join(p) for p in r.paths[:32]],
                    "failures": list(r.failures[:16]), "stale_hops": list(r.stale)}

    def physical_paths(self, source: str, target: str) -> dict[str, Any]:
        with self._lock:
            paths = self.twin.physical_paths(source, target)
            return {"source": source, "target": target, "hops": len(paths[0]) - 1 if paths else None,
                    "paths": [" -> ".join(p) for p in paths]}

    def reachability(self) -> dict[str, Any]:
        with self._lock:
            r = self.twin.reachability()
            return {"pairs_checked": r["pairs"], "delivered": r["delivered"], "broken": r["broken"][:MAX_ROWS],
                    "verified_on_stale_state": r["verified_on_stale_state"]}

    def alert_list(self, state: str = "open", min_severity: str = "info", limit: int = 50) -> list[dict[str, Any]]:
        if state not in ("open", "resolved", "all"):
            raise ValueError("state must be open, resolved or all")
        if min_severity not in SEVERITY_RANK:
            raise ValueError(f"min_severity must be one of {', '.join(SEVERITY_RANK)}")
        with self._lock:
            return [{**a.to_dict(), "opened": rfc3339(a.opened), "updated": rfc3339(a.updated),
                     "resolved": rfc3339(a.resolved) if a.resolved else None}
                    for a in self.alerts.list(state, min_severity)[:_cap(limit)]]

    def events(self, device: str | None = None, kind: str | None = None, since_s: float = 3600.0,
               limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            floor = self.net.now - since_s
            rows = [e for e in self.collector.events
                    if e.timestamp >= floor and (not device or e.device == device) and (not kind or e.kind == kind)]
            return [{"time": rfc3339(e.timestamp), "device": e.device, "kind": e.kind, "severity": e.severity,
                     "subject": e.subject, "message": e.message} for e in rows[-_cap(limit):]]

    def metrics(self, pattern: str, window_s: float = 900.0, step_s: float = 0.0,
                max_points: int = 60) -> dict[str, Any]:
        with self._lock:
            end = self.net.now
            start = end - window_s
            tier = self.tsdb.tier_for(start, step_s)
            data = self.tsdb.query(pattern, start, end, step_s)
            out: dict[str, Any] = {"tier": tier, "window_s": window_s, "series": {}}
            for series, points in list(sorted(data.items()))[:50]:
                values = [p.value for p in points]
                stride = max(1, len(points) // max(1, max_points))
                out["series"][series] = {
                    "last": round(values[-1], 4), "min": round(min(values), 4), "max": round(max(values), 4),
                    "avg": round(sum(values) / len(values), 4), "count": len(values),
                    "first_ts": rfc3339(points[0].ts), "last_ts": rfc3339(points[-1].ts),
                    "points": [[round(p.ts - end, 1), round(p.value, 4)] for p in points[::stride]]}
            if len(data) > 50:
                out["truncated_series"] = len(data) - 50
            return out

    def series(self, pattern: str = "*") -> list[str]:
        with self._lock:
            return self.tsdb.series(pattern)[:500]

    def top_flows(self, window_s: float = 300.0, n: int = 10, group_by: str = "pair") -> list[dict[str, Any]]:
        with self._lock:
            return self.collector.top_flows(self.net.now - window_s, _cap(n), group_by)

    def knowledge(self, query: str, k: int = 4) -> list[dict[str, Any]]:
        return [{"doc": h.chunk.doc, "title": h.chunk.title, "section": h.chunk.section, "score": h.score,
                 "text": h.chunk.text} for h in self.kb.search(query, max(1, min(k, 10)))]

    def gnmi_get(self, device: str, path: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            notes = self.gnmi.get(device, path)
            return [{"path": n.path, "value": n.value} for n in notes[:_cap(limit)]]

    def changes(self, since_s: float = 3600.0) -> dict[str, Any]:
        """What changed in the twin: diff from the snapshot `since_s` ago to now."""
        with self._lock:
            if not self.twin.snapshots:
                return {"changes": None}
            base = self.twin.snapshot_at(self.net.now - since_s) or self.twin.snapshots[0]
            current = Snapshot(0, self.net.now, "now", self.twin.entities())
            diff = self.twin.diff(base, current)
            diff["to"] = "now"
            diff["from_ts"], diff["to_ts"] = rfc3339(diff["from_ts"]), rfc3339(diff["to_ts"])
            return diff

    # ── safety (analysis only: runs on clones and the twin, never on the lab) ──

    def what_if(self, change: dict[str, Any], settle_s: float = 30.0) -> dict[str, Any]:
        """Verify a proposed change on a copy of the network; the lab is not touched."""
        parsed = ChangeSet.from_dict(change)
        settle_s = max(5.0, min(float(settle_s), 300.0))
        with self._lock:
            report = what_if(self.net, parsed, settle_s, self.segmentation, self.change_policy).to_dict()
            report["verified_at"] = rfc3339(self.net.now)
            self.verifications[parsed.fingerprint] = {"report": report, "change": parsed.to_dict(),
                                                      "at": self.net.now, "epoch": self.config_epoch}
            self.ledger.append("change_verified", {
                "fingerprint": parsed.fingerprint, "intent": parsed.intent, "confidence": parsed.confidence,
                "verification_passed": report["verification_passed"],
                "failed_checks": [c["name"] for c in report["checks"] if not c["passed"]],
                "policy": report["policy"]}, self.net.now)
            while len(self.verifications) > 100:
                self.verifications.pop(next(iter(self.verifications)))
            return report

    def acl_audit(self, device: str | None = None) -> dict[str, Any]:
        with self._lock:
            findings = [f for f in acl_findings(self.twin) if not device or f["device"] == device]
            acls = {dev: sorted(st.acls) for dev, st in sorted(self.twin.devices.items()) if st.acls}
            return {"acls": acls, "dead_rules": findings}

    def segmentation_audit(self) -> dict[str, Any]:
        with self._lock:
            violations = segmentation_violations(self.twin, self.segmentation)
            return {"zones": {z.name: list(z.prefixes) for z in self.segmentation.zones.values()},
                    "rules": [{"from": r.source, "to": r.destination, "allow": list(r.allow)}
                              for r in self.segmentation.rules],
                    "compliant": not violations, "violations": violations[:MAX_ROWS]}

    def change_policy_view(self) -> dict[str, Any]:
        with self._lock:
            p = self.change_policy
            return {"max_port_changes": p.max_port_changes, "hitl_confidence_threshold": p.hitl_confidence_threshold,
                    "require_verification": p.require_verification,
                    "windows": [{"name": w.name, "weekdays": list(w.weekdays), "start": w.start, "end": w.end,
                                 "from_date": w.from_date, "to_date": w.to_date} for w in p.windows],
                    "lab_time": rfc3339(self.net.now), "windows_active_now": p.active_windows(self.net.now)}

    # ── domain analyses (the reasoning layer's instruments) ──────────────────

    def optics_forecast(self, device: str | None = None, window_s: float = 1800.0) -> list[dict[str, Any]]:
        with self._lock:
            return diagnostics.optics_forecast(self.tsdb, self.net.now, device, window_s)

    def error_correlation(self, device: str, port: str, window_s: float = 1800.0) -> dict[str, Any]:
        with self._lock:
            self._device(device)
            return diagnostics.error_correlation(self.tsdb, self.net.now, device, port, window_s)

    def config_drift(self, device: str | None = None) -> dict[str, Any]:
        with self._lock:
            return compliance.config_drift(self.gnmi, compliance.load("golden.json"), [device] if device else None)

    def advisories(self) -> dict[str, Any]:
        with self._lock:
            return compliance.advisories(self.twin, compliance.load("advisories.json"))

    def te_assess(self, limit: float = 0.8) -> dict[str, Any]:
        with self._lock:
            return traffic.te_assess(self, limit)

    def flow_anomalies(self, window_s: float = 60.0) -> list[dict[str, Any]]:
        with self._lock:
            return security.flow_anomalies(list(self.collector.flows), self.net.now, window_s)

    def compile_intent(self, intent: dict[str, Any], confidence: float = 0.9) -> dict[str, Any]:
        with self._lock:
            return intents.compile_intent(self, intent, confidence)

    # ── actuation and governance ─────────────────────────────────────────────

    def apply_change(self, fingerprint: str, confirm_window_s: float = 60.0, rationale: str = "") -> dict[str, Any]:
        return self.actuator.apply(fingerprint, confirm_window_s, rationale).to_dict()

    def approval_list(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [r.to_dict() for r in self.approvals.list(status, self.net.now)]

    def ledger_view(self, since_seq: int = 0, fingerprint: str | None = None, limit: int = 50) -> dict[str, Any]:
        with self._lock:
            return {"head": self.ledger.head, "length": len(self.ledger), "intact": not self.ledger.verify(),
                    "records": [r.to_dict() for r in self.ledger.records(since_seq, None, fingerprint, _cap(limit))]}

    def explain(self, fingerprint: str) -> dict[str, Any]:
        """Human-readable rationale for one change: what triggered it, what was checked, who approved,
        what was applied and what the network looked like afterwards — from the ledger."""
        with self._lock:
            records = self.ledger.records(0, None, fingerprint, 200)
            if not records:
                raise KeyError(f"no ledger record for change {fingerprint!r}")
            lines = []
            for r in records:
                d = r.data
                when = rfc3339(r.ts)
                if r.kind == "change_verified":
                    verdict = "passed" if d["verification_passed"] else f"failed ({', '.join(d['failed_checks'])})"
                    lines.append(f"{when} verified: {d['intent']!r}, confidence {d['confidence']:.2f}; "
                                 f"verification {verdict}; policy {d['policy']['verdict']}")
                elif r.kind == "change_committed":
                    ports = sum(len(v) for v in d["applied_diff"].values())
                    who = (d.get("approval") or {}).get("decided_by")
                    lines.append(f"{when} committed {ports} config change(s) on {', '.join(d['applied_diff'])}"
                                 + (f", approved by {who}" if who else ", within autonomous policy")
                                 + (f"; rationale: {d['rationale']}" if d.get("rationale") else ""))
                else:
                    lines.append(f"{when} {r.kind.replace('_', ' ')}: {'; '.join(d.get('reasons', []) + d.get('triggers', []))}")
            return {"fingerprint": fingerprint, "narrative": lines, "records": [r.to_dict() for r in records]}

    def operator_decide(self, request_id: str, grant: bool, operator: str, note: str = "") -> dict[str, Any]:
        with self._lock:
            req = self.approvals.decide(request_id, grant, operator, self.net.now, note)
            self.ledger.append("approval_granted" if grant else "approval_denied",
                               {"fingerprint": req.fingerprint, **req.to_dict()}, self.net.now)
            return req.to_dict()

    def operator_kill_switch(self, engaged: bool, operator: str, reason: str = "") -> dict[str, Any]:
        with self._lock:
            self.kill_switch.set(engaged, operator, reason, self.net.now)
            self.ledger.append("kill_switch_engaged" if engaged else "kill_switch_released",
                               {"operator": operator, "reason": reason}, self.net.now)
            return {"engaged": engaged, "by": operator, "reason": reason, "at": rfc3339(self.net.now)}

    def operator_rollback(self, apply_id: str, operator: str, reason: str = "") -> dict[str, Any]:
        return self.actuator.rollback(apply_id, operator, reason)

    def _device(self, name: str) -> Any:
        st = self.twin.devices.get(name)
        if st is None:
            raise KeyError(f"unknown device {name!r}; known: {', '.join(sorted(self.twin.devices))}")
        return st


def load_scenario(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    steps = data.get("steps", data) if isinstance(data, dict) else data
    for s in steps:
        if "inject" in s and s["inject"] not in FAULT_KINDS:
            raise ValueError(f"scenario step injects unknown fault {s['inject']!r}")
    return list(steps)


def _cap(n: int) -> int:
    return max(1, min(int(n), MAX_ROWS))


def _gbps(bps: float | None) -> float | None:
    return None if bps is None else round(bps / 1e9, 2)


def _pct(bps: float | None, speed: float) -> float | None:
    if bps is None or not speed:
        return None
    return round(100.0 * bps / speed, 1)


def _natural(name: str) -> tuple[str, int]:
    head = name.rstrip("0123456789")
    tail = name[len(head):]
    return head, int(tail) if tail else -1
