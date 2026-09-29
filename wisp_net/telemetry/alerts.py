"""Alert rules over normalized telemetry: open, update and resolve with stable keys.

An alert is identified by (rule, device, subject), so a condition that persists is one
alert, not one per sample, and it resolves when the condition clears. Every open,
severity change and resolution is emitted once, for the alerts topic.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SEVERITY_RANK = {"info": 0, "minor": 1, "major": 2, "critical": 3}

FCS_MINOR_PER_S = 10.0
FCS_MAJOR_PER_S = 1000.0
RX_MINOR_DBM = -10.0
RX_MAJOR_DBM = -14.0
UTIL_MINOR = 0.80
UTIL_MAJOR = 0.95
CPU_MINOR = 90.0
BGP_ACTIVE_GRACE_S = 10.0
FLAP_WINDOW_S = 300.0
FLAP_COUNT = 3


REOPEN_WINDOW_S = 300.0


@dataclass
class Alert:
    alert_id: str
    rule: str
    device: str
    subject: str
    severity: str
    message: str
    opened: float
    updated: float
    value: Any = None
    state: str = "open"
    resolved: float | None = None
    occurrences: int = 1
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Condition:
    rule: str
    device: str
    subject: str
    severity: str
    message: str
    value: Any = None
    evidence: dict[str, Any] = field(default_factory=dict)


class AlertEngine:
    def __init__(self) -> None:
        self.active: dict[tuple[str, str, str], Alert] = {}
        self.recently_resolved: dict[tuple[str, str, str], Alert] = {}
        self.history: list[Alert] = []
        self._seq = 0
        self._active_since: dict[tuple[str, str], float] = {}
        self._transitions: dict[tuple[str, str], list[tuple[float, int]]] = {}

    def conditions(self, now: float, sample: dict[str, Any]) -> list[Condition]:
        """Evaluate every rule against one collector cycle's derived view."""
        out: list[Condition] = []
        for dev in sample.get("unreachable", []):
            out.append(Condition("device_unreachable", dev, dev, "critical",
                                 f"{dev}: management plane not answering gNMI; data plane state unknown"))
        for dev, ifaces in sample.get("interfaces", {}).items():
            for ifname, m in ifaces.items():
                subj = ifname
                if m.get("kind") == "fabric" and m.get("admin") == "UP" and m.get("oper") == "DOWN":
                    out.append(Condition("interface_down", dev, subj, "major",
                                         f"{dev} {ifname} is admin UP but oper DOWN",
                                         evidence={"peer": m.get("peer")}))
                fcs = m.get("fcs_per_s", 0.0)
                if fcs >= FCS_MINOR_PER_S:
                    sev = "major" if fcs >= FCS_MAJOR_PER_S else "minor"
                    out.append(Condition("fcs_errors", dev, subj, sev,
                                         f"{dev} {ifname} receiving {fcs:.0f} FCS errors/s",
                                         round(fcs, 1), {"rx_power_dbm": m.get("rx_power_dbm")}))
                rx = m.get("rx_power_dbm")
                if rx is not None and m.get("oper") == "UP" and rx <= RX_MINOR_DBM:
                    sev = "major" if rx <= RX_MAJOR_DBM else "minor"
                    out.append(Condition("rx_power_low", dev, subj, sev,
                                         f"{dev} {ifname} receive power {rx:.2f} dBm", rx))
                util = m.get("tx_util", 0.0)
                drops = m.get("out_discards_per_s", 0.0)
                if util >= UTIL_MINOR or drops > 0:
                    sev = "major" if util >= UTIL_MAJOR or drops > 0 else "minor"
                    out.append(Condition("congestion", dev, subj, sev,
                                         f"{dev} {ifname} egress {util:.0%} utilized, {drops:.0f} discards/s",
                                         round(util, 3), {"tx_bps": m.get("tx_bps"), "discards_per_s": drops}))
        for dev, cpu in sample.get("cpu", {}).items():
            if cpu >= CPU_MINOR:
                out.append(Condition("cpu_high", dev, dev, "minor", f"{dev} CPU at {cpu:.0f}%", cpu))
        for dev, peers in sample.get("bgp", {}).items():
            for ip, p in peers.items():
                key = (dev, ip)
                state = p.get("state")
                if not p.get("enabled", True):
                    self._active_since.pop(key, None)
                    continue
                if state != "ESTABLISHED":
                    since = self._active_since.setdefault(key, now)
                    if state == "IDLE" or now - since >= BGP_ACTIVE_GRACE_S:
                        out.append(Condition("bgp_session_down", dev, ip, "major",
                                             f"{dev} BGP neighbor {ip} (AS {p.get('peer_as')}) is {state}",
                                             state, {"description": p.get("description")}))
                else:
                    self._active_since.pop(key, None)
                trans = self._transitions.setdefault(key, [])
                count = int(p.get("transitions", 0))
                trans.append((now, count))
                while trans and now - trans[0][0] > FLAP_WINDOW_S:
                    trans.pop(0)
                if trans and count - trans[0][1] >= FLAP_COUNT:
                    out.append(Condition("bgp_flapping", dev, ip, "major",
                                         f"{dev} BGP neighbor {ip} re-established {count - trans[0][1]} times "
                                         f"in {FLAP_WINDOW_S / 60:.0f} min", count - trans[0][1]))
        return out

    def evaluate(self, now: float, sample: dict[str, Any]) -> list[dict[str, Any]]:
        """Apply this cycle's conditions; return the alert changes to publish.

        A condition that returns within `REOPEN_WINDOW_S` of resolving reopens the same
        alert and counts the occurrence, so a flapping link is one alert with a count,
        not a new alert per flap.
        """
        changes: list[dict[str, Any]] = []
        current = {(c.rule, c.device, c.subject): c for c in self.conditions(now, sample)}
        for key in [k for k, a in self.recently_resolved.items() if now - (a.resolved or now) > REOPEN_WINDOW_S]:
            del self.recently_resolved[key]
        for key, cond in current.items():
            alert = self.active.get(key)
            if alert is None and key in self.recently_resolved:
                alert = self.recently_resolved.pop(key)
                alert.state, alert.resolved, alert.updated = "open", None, now
                alert.occurrences += 1
                alert.severity, alert.message, alert.value = cond.severity, cond.message, cond.value
                self.active[key] = alert
                changes.append({"change": "reopened", **alert.to_dict()})
                continue
            if alert is None:
                self._seq += 1
                alert = Alert(f"A{self._seq:05d}", cond.rule, cond.device, cond.subject, cond.severity,
                              cond.message, now, now, cond.value, evidence=dict(cond.evidence))
                self.active[key] = alert
                self.history.append(alert)
                changes.append({"change": "opened", **alert.to_dict()})
            else:
                escalated = SEVERITY_RANK[cond.severity] != SEVERITY_RANK[alert.severity]
                alert.updated, alert.value, alert.message = now, cond.value, cond.message
                alert.evidence = dict(cond.evidence)
                if escalated:
                    alert.severity = cond.severity
                    changes.append({"change": "severity", **alert.to_dict()})
        for key in [k for k in self.active if k not in current]:
            alert = self.active.pop(key)
            alert.state, alert.resolved, alert.updated = "resolved", now, now
            self.recently_resolved[key] = alert
            changes.append({"change": "resolved", **alert.to_dict()})
        if len(self.history) > 5000:
            recent = self.history[-4000:]
            kept = {a.alert_id for a in recent}
            self.history = [a for a in self.history[:-4000] if a.state == "open" and a.alert_id not in kept] + recent
        return changes

    def list(self, state: str = "open", min_severity: str = "info") -> list[Alert]:
        floor = SEVERITY_RANK[min_severity]
        pool = self.active.values() if state == "open" else self.history
        return sorted((a for a in pool if SEVERITY_RANK[a.severity] >= floor
                       and (state == "all" or a.state == state)),
                      key=lambda a: (-SEVERITY_RANK[a.severity], a.opened, a.alert_id))
