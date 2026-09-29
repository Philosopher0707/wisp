"""Actuation: apply a verified, allowed change with commit-confirm and automatic rollback.

A change reaches the devices only through `Actuator.apply`, and only when, in order:
  1. the kill switch is not engaged;
  2. `net_what_if` verified *this exact* change (by fingerprint), recently, and no other
     change has been committed since (the verification is about the network as it is);
  3. the change policy, re-decided now, allows it — or requires approval and an operator
     granted it through the cockpit (the grant is single-use);
  4. it would change something (applying it twice is a recorded no-op).

Then: checkpoint the running configs, commit, and watch health through telemetry for the
confirm window. Rollback triggers (blueprint layer 5): BGP flap rate above 5/min on the
touched devices, packet loss above 0.05% on the touched ports, a touched device's
management plane unreachable — plus any source/prefix pair losing reachability that the
change did not declare. Any trigger restores the checkpoint; a clean window confirms.

In the lab the confirm window is lab time: the platform fast-forwards it under its lock.
On real devices it is wall time, and the device's own commit-confirm timer backs it up.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from wisp_net.safety.change import ChangeSet
from wisp_net.safety.policy import decide
from wisp_net.sim.config import SetError, apply_set

VERIFICATION_TTL_S = 600.0
MAX_CONFIRM_WINDOW_S = 60.0
FLAP_PER_MIN_LIMIT = 5.0
LOSS_LIMIT = 0.0005
CHECK_EVERY_S = 5.0


@dataclass
class ApplyResult:
    apply_id: str
    fingerprint: str
    outcome: str  # refused | awaiting_approval | already_applied | confirmed | rolled_back
    reasons: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    diff: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    approval: dict[str, Any] | None = None
    post_change: dict[str, Any] = field(default_factory=dict)
    ledger_seq: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"apply_id": self.apply_id, "fingerprint": self.fingerprint, "outcome": self.outcome,
                "reasons": self.reasons, "triggers": self.triggers, "diff": self.diff,
                "approval": self.approval, "post_change": self.post_change, "ledger_seq": self.ledger_seq}


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}/{k}"))
        return out
    return {prefix: obj}


def config_diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    a, b = _flatten(before), _flatten(after)
    changes = []
    for key in sorted(set(a) | set(b)):
        if a.get(key, _MISSING) != b.get(key, _MISSING):
            changes.append({"path": key, "from": a.get(key), "to": b.get(key)})
    return changes


_MISSING = object()


class Actuator:
    def __init__(self, service: Any) -> None:
        self.s = service
        self._seq = 0
        self.applied: dict[str, dict[str, Any]] = {}

    # ── apply ────────────────────────────────────────────────────────────────

    def apply(self, fingerprint: str, confirm_window_s: float = MAX_CONFIRM_WINDOW_S,
              rationale: str = "") -> ApplyResult:
        s = self.s
        with s._lock:
            self._seq += 1
            result = ApplyResult(f"X{self._seq:04d}", fingerprint, "refused")
            refusal = self._refusal(fingerprint)
            if refusal:
                result.reasons = [refusal]
                return self._record(result, rationale)
            entry = s.verifications[fingerprint]
            change = ChangeSet.from_dict(entry["change"])
            decision = decide(change, s.net.now, True, s.change_policy)
            if decision.verdict == "deny":
                result.reasons = list(decision.reasons)
                return self._record(result, rationale)
            if decision.verdict == "require_approval":
                grant = s.approvals.consume(fingerprint, s.net.now)
                if grant is None:
                    req = s.approvals.request(fingerprint, change.intent, s.net.now, list(decision.reasons),
                                              {"ops": len(change.ops), "ports": decision.facts["touched_ports"]})
                    result.outcome = "awaiting_approval"
                    result.reasons = list(decision.reasons) + [
                        f"approval request {req.request_id} is {req.status}; an operator must grant it in the "
                        "cockpit, then apply again"]
                    result.approval = req.to_dict()
                    return self._record(result, rationale)
                result.approval = grant.to_dict()

            try:
                targets = {dev: apply_set(s.net.devices[dev].config, updates, deletes)
                           for dev, (updates, deletes) in change.by_device().items()}
            except (SetError, KeyError) as exc:
                result.reasons = [f"the change no longer applies to the running config: {exc}"]
                return self._record(result, rationale)
            result.diff = {dev: config_diff(s.net.devices[dev].config, cfg) for dev, cfg in targets.items()}
            result.diff = {dev: d for dev, d in result.diff.items() if d}
            if not result.diff:
                result.outcome = "already_applied"
                result.reasons = ["the running config already matches the change"]
                return self._record(result, rationale)

            checkpoint = {dev: copy.deepcopy(s.net.devices[dev].config) for dev in targets}
            before = self._health_baseline(change)
            s.ledger.append("change_committed", {"apply_id": result.apply_id, "fingerprint": fingerprint,
                                                  "intent": change.intent, "rationale": rationale,
                                                  "proposed": change.to_dict()["ops"], "applied_diff": result.diff,
                                                  "approval": result.approval}, s.net.now)
            for dev, cfg in targets.items():
                s.net.apply_config(dev, cfg, user="wisp-net-actuator")
            s.config_epoch += 1

            window = max(CHECK_EVERY_S, min(float(confirm_window_s), MAX_CONFIRM_WINDOW_S))
            elapsed = 0.0
            while elapsed < window and not result.triggers:
                step = min(CHECK_EVERY_S, window - elapsed)
                s.advance(step)
                elapsed += step
                result.triggers = self._triggers(change, before, elapsed)

            if result.triggers:
                for dev, cfg in checkpoint.items():
                    s.net.apply_config(dev, cfg, user="wisp-net-rollback")
                s.config_epoch += 1
                s.advance(10.0)
                result.outcome = "rolled_back"
                result.reasons = [f"rolled back after {elapsed:.0f}s of the {window:.0f}s confirm window"]
            else:
                result.outcome = "confirmed"
                result.reasons = [f"healthy through the {window:.0f}s confirm window"]
                self.applied[result.apply_id] = {"fingerprint": fingerprint, "checkpoint": checkpoint,
                                                 "intent": change.intent, "at": s.net.now}
            result.post_change = self._post_change(change, before)
            return self._record(result, rationale)

    def rollback(self, apply_id: str, operator: str, reason: str) -> dict[str, Any]:
        """Operator-driven revert of a confirmed change to its checkpoint."""
        s = self.s
        with s._lock:
            entry = self.applied.pop(apply_id, None)
            if entry is None:
                raise KeyError(f"no confirmed change {apply_id!r} to roll back")
            for dev, cfg in entry["checkpoint"].items():
                s.net.apply_config(dev, cfg, user=f"operator:{operator}")
            s.config_epoch += 1
            rec = s.ledger.append("change_reverted_by_operator", {"apply_id": apply_id, "fingerprint":
                                                                  entry["fingerprint"], "operator": operator,
                                                                  "reason": reason}, s.net.now)
            return {"apply_id": apply_id, "reverted": True, "ledger_seq": rec.seq}

    # ── checks ───────────────────────────────────────────────────────────────

    def _refusal(self, fingerprint: str) -> str | None:
        s = self.s
        if s.kill_switch.engaged:
            return (f"the kill switch is engaged ({s.kill_switch.reason or 'no reason given'}, by "
                    f"{s.kill_switch.by}): the platform is in passive monitoring")
        entry = s.verifications.get(fingerprint)
        if entry is None:
            return "no what-if verification for this change: run net_what_if first and apply its fingerprint"
        report = entry["report"]
        if not report.get("verification_passed"):
            failed = [c["name"] for c in report.get("checks", []) if not c.get("passed")] or report.get("errors")
            return f"its verification did not pass ({', '.join(map(str, failed))})"
        if entry.get("epoch") != s.config_epoch:
            return "the network's configuration changed since the verification: verify again"
        if s.net.now - entry["at"] > VERIFICATION_TTL_S:
            return f"the verification is older than {VERIFICATION_TTL_S:.0f}s: verify again"
        return None

    def _health_baseline(self, change: ChangeSet) -> dict[str, Any]:
        s = self.s
        devices = set(change.by_device())
        view = s.collector.last_view
        transitions = {(dev, ip): int(p.get("transitions") or 0)
                       for dev in devices for ip, p in view.get("bgp", {}).get(dev, {}).items()}
        counters = self._port_counters(change)
        broken = {(b["source"], b["prefix"]) for b in s.twin.reachability()["broken"]}
        return {"t": s.net.now, "transitions": transitions, "counters": counters, "broken": broken,
                "devices": devices}

    def _port_counters(self, change: ChangeSet) -> dict[tuple[str, str], tuple[int, int]]:
        out = {}
        drained = change.drained_ports()
        for dev, ifname in change.touched_ports() - drained:
            c = self.s.twin.devices.get(dev).interfaces.get(ifname, {}).get("counters", {}) \
                if dev in self.s.twin.devices else {}
            packets = int(c.get("in-pkts", 0)) + int(c.get("out-pkts", 0))
            lost = int(c.get("out-discards", 0)) + int(c.get("in-errors", 0))
            out[(dev, ifname)] = (packets, lost)
        return out

    def _triggers(self, change: ChangeSet, before: dict[str, Any], elapsed: float) -> list[str]:
        s = self.s
        view = s.collector.last_view
        triggers = []
        for dev in sorted(before["devices"]):
            if dev in view.get("unreachable", []):
                triggers.append(f"management plane of {dev} unreachable")
        flaps = 0
        for (dev, ip), start in before["transitions"].items():
            now = int(view.get("bgp", {}).get(dev, {}).get(ip, {}).get("transitions") or 0)
            flaps += max(0, now - start)
        rate = flaps / max(elapsed / 60.0, 1.0)
        if rate > FLAP_PER_MIN_LIMIT:
            triggers.append(f"BGP flap rate {rate:.1f}/min on the touched devices exceeds {FLAP_PER_MIN_LIMIT:.0f}/min")
        now_counters = self._port_counters(change)
        for port, (pkts0, lost0) in before["counters"].items():
            pkts1, lost1 = now_counters.get(port, (pkts0, lost0))
            if pkts1 > pkts0:
                loss = (lost1 - lost0) / (pkts1 - pkts0)
                if loss > LOSS_LIMIT:
                    triggers.append(f"packet loss {loss:.3%} on {port[0]}:{port[1]} exceeds {LOSS_LIMIT:.2%}")
        expected = set(change.expected_unreachable)
        lost_pairs = sorted({(b["source"], b["prefix"]) for b in s.twin.reachability()["broken"]}
                            - before["broken"] - expected)
        if lost_pairs:
            triggers.append(f"{len(lost_pairs)} undeclared source/prefix pair(s) lost reachability, e.g. "
                            f"{lost_pairs[0][0]} -> {lost_pairs[0][1]}")
        return triggers

    def _post_change(self, change: ChangeSet, before: dict[str, Any]) -> dict[str, Any]:
        reach = self.s.twin.reachability()
        broken_now = {(b["source"], b["prefix"]) for b in reach["broken"]}
        return {"reachability": f"{reach['delivered']}/{reach['pairs']} delivered",
                "newly_unreachable": sorted(f"{a} -> {b}" for a, b in broken_now - before["broken"]),
                "restored": sorted(f"{a} -> {b}" for a, b in before["broken"] - broken_now),
                "open_major_alerts": len(self.s.alerts.list("open", "major"))}

    def _record(self, result: ApplyResult, rationale: str) -> ApplyResult:
        kind = {"refused": "apply_refused", "awaiting_approval": "approval_requested",
                "already_applied": "apply_noop", "confirmed": "change_confirmed",
                "rolled_back": "change_rolled_back"}[result.outcome]
        rec = self.s.ledger.append(kind, {**result.to_dict(), "rationale": rationale}, self.s.net.now)
        result.ledger_seq = rec.seq
        return result
