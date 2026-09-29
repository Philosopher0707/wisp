"""What-if: run a proposed change on a copy of the network and verify the outcome.

Two clones of the live lab advance side by side for `settle_s` seconds: the baseline as
it is, the candidate with the change committed. Each has its own telemetry pipeline, so
the candidate's twin is built from what its devices would report — the same evidence an
operator would get after a real commit. Both clones share the live lab's random state,
so every difference between them is caused by the change. The live lab is never touched.

Convergence is measured on the candidate: the last second its routing tables changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from wisp_net.safety.change import ChangeSet
from wisp_net.safety.policy import ChangePolicy, PolicyDecision, decide
from wisp_net.safety.verify import (
    Check,
    session_port,
    SegmentationPolicy,
    check_acl_hygiene,
    check_blackholes,
    check_loops,
    check_segmentation,
    check_sessions,
)
from wisp_net.sim.config import SetError, apply_set
from wisp_net.sim.gnmi import SimGnmi
from wisp_net.sim.network import SimNetwork
from wisp_net.state.tsdb import MetricStore
from wisp_net.state.twin import DigitalTwin
from wisp_net.telemetry.alerts import SEVERITY_RANK, AlertEngine
from wisp_net.telemetry.bus import TelemetryBus
from wisp_net.telemetry.collector import Collector

UTIL_LIMIT = 0.9


class _Pipeline:
    def __init__(self, net: SimNetwork) -> None:
        self.net = net
        self.twin = DigitalTwin()
        self.alerts = AlertEngine()
        self.collector = Collector(SimGnmi(net), TelemetryBus(), MetricStore(), self.twin, self.alerts)

    def collect(self) -> None:
        self.collector.ingest_syslog(self.net.drain_syslog())
        self.collector.ingest_flows(self.net.drain_flows())
        self.collector.collect(self.net.now)

    def close(self) -> None:
        self.collector.tsdb.close()


@dataclass
class WhatIfReport:
    fingerprint: str
    intent: str
    applied: bool
    errors: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    convergence: dict[str, Any] = field(default_factory=dict)
    new_alerts: list[dict[str, Any]] = field(default_factory=list)
    hot_links: list[dict[str, Any]] = field(default_factory=list)
    decision: PolicyDecision | None = None

    @property
    def passed(self) -> bool:
        return self.applied and not self.errors and all(c.passed for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint, "intent": self.intent, "applied": self.applied,
            "verification_passed": self.passed, "errors": self.errors,
            "checks": [c.to_dict() for c in self.checks], "convergence": self.convergence,
            "new_alerts": self.new_alerts, "hot_links": self.hot_links,
            "policy": None if self.decision is None else {
                "verdict": self.decision.verdict, "reasons": list(self.decision.reasons), **self.decision.facts},
        }


def _ribs(net: SimNetwork) -> dict[str, dict[str, Any]]:
    return {name: dict(dev.rib) for name, dev in net.devices.items()}


def what_if(live: SimNetwork, change: ChangeSet, settle_s: float = 30.0,
            segmentation: SegmentationPolicy | None = None, policy: ChangePolicy | None = None,
            collect_every_s: float = 5.0) -> WhatIfReport:
    """Verify `change` against a copy of `live`. The caller holds whatever lock guards `live`."""
    report = WhatIfReport(change.fingerprint, change.intent, applied=False)
    baseline, candidate = live.clone(), live.clone()
    for device, (updates, deletes) in change.by_device().items():
        if device not in candidate.devices:
            report.errors.append(f"unknown device {device!r}")
            continue
        try:
            candidate.apply_config(device, apply_set(candidate.devices[device].config, updates, deletes),
                                   user="what-if")
        except SetError as exc:
            report.errors.append(f"{device}: {exc}")
    policy = policy or ChangePolicy.bundled()
    if report.errors:
        report.decision = decide(change, live.now, False, policy)
        return report
    report.applied = True

    base_pipe, cand_pipe = _Pipeline(baseline), _Pipeline(candidate)
    try:
        for pipe in (base_pipe, cand_pipe):
            pipe.collect()
        last_change, previous = 0.0, _ribs(candidate)
        elapsed, next_collect = 0.0, collect_every_s
        while elapsed < settle_s:
            baseline.step(1.0)
            candidate.step(1.0)
            elapsed += 1.0
            ribs = _ribs(candidate)
            if ribs != previous:
                last_change, previous = elapsed, ribs
            if elapsed >= next_collect or elapsed >= settle_s:
                base_pipe.collect()
                cand_pipe.collect()
                next_collect += collect_every_s
        report.convergence = {
            "settle_s": settle_s, "routing_stable_after_s": last_change,
            "converged": last_change < settle_s,
            "sessions_established": {
                "baseline": sum(1 for s in baseline.sessions.values() if s.state == "ESTABLISHED"),
                "candidate": sum(1 for s in candidate.sessions.values() if s.state == "ESTABLISHED")},
        }
        segmentation = segmentation or SegmentationPolicy.bundled()
        intended_ports = _intended_ports(base_pipe.twin, change)
        report.checks = [
            check_loops(cand_pipe.twin),
            check_blackholes(base_pipe.twin, cand_pipe.twin, change.expected_unreachable),
            check_sessions(base_pipe.twin, cand_pipe.twin, intended_ports),
            check_acl_hygiene(base_pipe.twin, cand_pipe.twin),
            check_segmentation(base_pipe.twin, cand_pipe.twin, segmentation),
            _check_capacity(base_pipe, cand_pipe, report),
        ]
        if not report.convergence["converged"]:
            report.checks.append(Check("converges", False,
                                       f"routing still changing at the end of the {settle_s:.0f}s settle window"))
        base_open = {(a.rule, a.device, a.subject) for a in base_pipe.alerts.list("open")}
        intended = _drained_link_ends(base_pipe.twin, intended_ports)
        report.new_alerts = [
            {"rule": a.rule, "device": a.device, "subject": a.subject, "severity": a.severity, "message": a.message,
             "intended": _alert_port(base_pipe.twin, a.device, a.subject) in intended}
            for a in cand_pipe.alerts.list("open") if (a.rule, a.device, a.subject) not in base_open]
        serious = [a for a in report.new_alerts
                   if SEVERITY_RANK[a["severity"]] >= SEVERITY_RANK["major"] and not a["intended"]]
        report.checks.append(Check("no_new_major_alerts", not serious,
                                   "no new major or critical alert" if not serious
                                   else f"{len(serious)} new major/critical alert(s)", serious))
    finally:
        base_pipe.close()
        cand_pipe.close()
    report.decision = decide(change, live.now, report.passed, policy)
    return report


def _intended_ports(twin: DigitalTwin, change: ChangeSet) -> set[tuple[str, str]]:
    """Ports whose sessions the change means to take down: drained ports, and the ports that
    carry BGP sessions it disables."""
    ports = set(change.drained_ports())
    for dev, neighbor in change.disabled_neighbors():
        if dev in twin.devices:
            port = session_port(twin, dev, neighbor)
            if port:
                ports.add((dev, port))
    return ports


def _drained_link_ends(twin: DigitalTwin, drained: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """Both ends of every link the change shuts."""
    ends = set(drained)
    for dev, ifname in drained:
        st = twin.devices.get(dev)
        peer = st.lldp.get(ifname) if st is not None else None
        if peer:
            ends.add((peer[0], peer[1]))
    return ends


def _alert_port(twin: DigitalTwin, device: str, subject: str) -> tuple[str, str] | None:
    """The port an alert is about: the interface itself, or the one a BGP session runs over."""
    if device not in twin.devices:
        return None
    if subject in twin.devices[device].interfaces:
        return device, subject
    port = session_port(twin, device, subject) if subject.count(".") == 3 else None
    return (device, port) if port else None


def _check_capacity(base: _Pipeline, cand: _Pipeline, report: WhatIfReport) -> Check:
    def hot(pipe: _Pipeline) -> dict[str, dict[str, Any]]:
        out = {}
        for dev, ifaces in pipe.collector.last_view.get("interfaces", {}).items():
            for ifname, m in ifaces.items():
                if m.get("tx_util", 0.0) >= UTIL_LIMIT or m.get("out_discards_per_s", 0.0) > 0:
                    out[f"{dev}:{ifname}"] = {"port": f"{dev}:{ifname}", "tx_util": round(m.get("tx_util", 0.0), 3),
                                              "discards_per_s": round(m.get("out_discards_per_s", 0.0), 1)}
        return out

    before, after = hot(base), hot(cand)
    report.hot_links = list(after.values())
    new = [v for k, v in after.items() if k not in before]
    return Check("no_new_congestion", not new,
                 "no port newly above 90% egress or discarding" if not new
                 else f"{len(new)} port(s) newly congested", new, [v for k, v in after.items() if k in before])
