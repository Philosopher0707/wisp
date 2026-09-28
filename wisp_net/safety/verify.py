"""Formal checks over digital twins: loops, blackholes, ACL hygiene, segmentation, sessions.

Every check reads twins only, so the same code verifies the live network and a what-if
candidate. Checks that compare a candidate against a baseline fail on *regressions*: a
violation the network already had is reported, not blamed on the change.

Segmentation is exact: the flow space between two zones (source prefixes x destination
prefixes x every protocol and port) is carried hop by hop along each forwarding path, cut
by the ingress ACL at every port it enters (`wisp_net.acl` box algebra). What survives to
the destination, minus the services the policy allows, is leakage — reported as concrete
flow classes, not a yes/no.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from wisp_net.acl import ANY_PORT, ANY_PROTO, Box, Rule, describe_box, dead_rules, prefix_interval, restrict
from wisp_net.acl import subtract_all
from wisp_net.state.twin import DigitalTwin

POLICIES = Path(__file__).resolve().parent.parent / "policies"


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str
    evidence: list[Any] = field(default_factory=list)
    baseline: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── ACLs as the twin knows them ──────────────────────────────────────────────

def twin_rules(twin: DigitalTwin, device: str, ifname: str) -> list[Rule] | None:
    st = twin.devices.get(device)
    if st is None:
        return None
    acl = st.acl_bindings.get(ifname, {}).get("ingress")
    if not acl:
        return None
    return sorted((Rule.from_entry({**e, "seq": seq}) for seq, e in st.acls.get(acl, {}).items()),
                  key=lambda r: r.seq)


def acl_findings(twin: DigitalTwin) -> list[dict[str, Any]]:
    out = []
    for dev, st in sorted(twin.devices.items()):
        for acl, entries in sorted(st.acls.items()):
            rules = sorted((Rule.from_entry({**e, "seq": seq}) for seq, e in entries.items()), key=lambda r: r.seq)
            for f in dead_rules(rules):
                out.append({"device": dev, "acl": acl, "rule": f.rule.seq, "kind": f.kind, "why": f.describe()})
    return out


# ── segmentation ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Zone:
    name: str
    prefixes: tuple[str, ...]
    attachments: tuple[str, ...]  # "device:interface" where the zone's traffic enters the network


@dataclass(frozen=True)
class SegmentationRule:
    source: str
    destination: str
    allow: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class SegmentationPolicy:
    zones: dict[str, Zone]
    rules: tuple[SegmentationRule, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SegmentationPolicy":
        zones = {z["name"]: Zone(z["name"], tuple(z["prefixes"]), tuple(z.get("attachments", [])))
                 for z in data["zones"]}
        rules = []
        for r in data["rules"]:
            if r["from"] not in zones or r["to"] not in zones:
                raise ValueError(f"segmentation rule names an unknown zone: {r}")
            rules.append(SegmentationRule(r["from"], r["to"], tuple(r.get("allow", []))))
        return cls(zones, tuple(rules))

    @classmethod
    def bundled(cls) -> "SegmentationPolicy":
        return cls.from_dict(json.loads((POLICIES / "segmentation.json").read_text(encoding="utf-8")))


def _service_boxes(src: str, dst: str, allow: tuple[dict[str, str], ...]) -> list[Box]:
    boxes: list[Box] = []
    for service in allow:
        boxes.extend(Rule(0, "permit", src, dst, service.get("proto", "any"), service.get("dport", "any")).boxes())
    return boxes


def _hops_with_ingress(twin: DigitalTwin, path: tuple[str, ...], prefix: str) -> list[tuple[str, str]]:
    """(device, ingress interface) for every hop after the first along a device path."""
    out: list[tuple[str, str]] = []
    for u, v in zip(path, path[1:]):
        st = twin.devices[u]
        entry = st.fib.get(twin.lookup(u, prefix) or "", {})
        for hop in entry.get("next-hops") or []:
            if hop.get("peer") == v and hop.get("interface") in st.lldp:
                out.append((v, st.lldp[hop["interface"]][1]))
                break
    return out


def segmentation_violations(twin: DigitalTwin, policy: SegmentationPolicy) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for rule in policy.rules:
        src_zone, dst_zone = policy.zones[rule.source], policy.zones[rule.destination]
        for attachment in src_zone.attachments:
            device, _, port = attachment.partition(":")
            if device not in twin.devices:
                continue
            for src_prefix in src_zone.prefixes:
                for dst_prefix in dst_zone.prefixes:
                    space: list[Box] = [(prefix_interval(src_prefix), prefix_interval(dst_prefix), ANY_PROTO,
                                         ANY_PORT)]
                    space = restrict(space, twin_rules(twin, device, port))
                    if not space:
                        continue
                    trace = twin.trace(device, dst_prefix)
                    for path in trace.paths:
                        reach = list(space)
                        for hop_dev, hop_if in _hops_with_ingress(twin, path, dst_prefix):
                            reach = restrict(reach, twin_rules(twin, hop_dev, hop_if))
                            if not reach:
                                break
                        for allowed in _service_boxes(src_prefix, dst_prefix, rule.allow):
                            reach = subtract_all(reach, allowed)
                        if reach:
                            violations.append({
                                "rule": f"{rule.source} -> {rule.destination}", "entry": attachment,
                                "path": " -> ".join(path),
                                "leaks": [describe_box(b) for b in reach[:4]] + (["…"] if len(reach) > 4 else [])})
                            break
    return violations


# ── checks ───────────────────────────────────────────────────────────────────

def _key(v: dict[str, Any]) -> str:
    return f"{v['rule']}|{v['entry']}"


def check_loops(candidate: DigitalTwin) -> Check:
    loops = [b for b in candidate.reachability()["broken"] if b["outcome"] == "loop"]
    return Check("loop_free", not loops, "no forwarding loop" if not loops else f"{len(loops)} forwarding loop(s)",
                 loops[:10])


def check_blackholes(baseline: DigitalTwin, candidate: DigitalTwin,
                     expected: tuple[tuple[str, str], ...] = ()) -> Check:
    before = baseline.reachability()["matrix"]
    after = candidate.reachability()
    lost = []
    for b in after["broken"]:
        if before.get(b["source"], {}).get(b["prefix"]) == "delivered" and (b["source"], b["prefix"]) not in expected:
            lost.append(b)
    ok = not lost
    return Check("no_new_blackholes", ok,
                 "every previously delivered pair is still delivered" if ok
                 else f"{len(lost)} source/prefix pair(s) lose reachability", lost[:10])


def session_port(twin: DigitalTwin, dev: str, neighbor_ip: str) -> str | None:
    """The local interface a session runs over: the one whose /31 holds the neighbor's address."""
    import ipaddress

    peer = ipaddress.ip_address(neighbor_ip)
    for ifname, iface in twin.devices[dev].interfaces.items():
        ip = iface.get("ip")
        if ip and peer in ipaddress.ip_network(f"{ip}/31", strict=False):
            return str(ifname)
    return None


def check_sessions(baseline: DigitalTwin, candidate: DigitalTwin,
                   touched_ports: set[tuple[str, str]] | None = None) -> Check:
    """BGP sessions the change takes down. Sessions over a port the change itself touches
    (a drain, a shutdown) are the change's intent: reported, not failed."""
    def up(twin: DigitalTwin) -> set[tuple[str, str]]:
        return {(dev, ip) for dev, st in twin.devices.items() for ip, p in st.bgp.items()
                if p.get("session-state") == "ESTABLISHED"}

    lost = sorted(up(baseline) - up(candidate))
    ends = set(touched_ports or ())
    expected: list[str] = []
    unexpected: list[str] = []
    for dev, ip in lost:
        port = session_port(baseline, dev, ip)
        peer_end = baseline.devices[dev].lldp.get(port or "")
        (expected if (dev, port) in ends or (peer_end and tuple(peer_end) in ends) else unexpected).append(
            f"{dev}|{ip}")
    detail = "no BGP session goes down" if not lost else (
        f"{len(unexpected)} BGP session(s) go down that the change does not touch" if unexpected
        else f"{len(expected)} session(s) over the ports the change touches go down, as intended")
    return Check("bgp_sessions_kept", not unexpected, detail, unexpected, expected)


def check_acl_hygiene(baseline: DigitalTwin, candidate: DigitalTwin) -> Check:
    base = {(f["device"], f["acl"], f["rule"]) for f in acl_findings(baseline)}
    cand = acl_findings(candidate)
    new = [f for f in cand if (f["device"], f["acl"], f["rule"]) not in base]
    return Check("acl_no_new_dead_rules", not new,
                 "no new shadowed or redundant ACL rule" if not new else f"{len(new)} new dead ACL rule(s)",
                 new, [f for f in cand if f not in new])


def check_segmentation(baseline: DigitalTwin, candidate: DigitalTwin, policy: SegmentationPolicy) -> Check:
    base = {_key(v) for v in segmentation_violations(baseline, policy)}
    cand = segmentation_violations(candidate, policy)
    new = [v for v in cand if _key(v) not in base]
    still = [v for v in cand if _key(v) in base]
    return Check("segmentation_no_new_leaks", not new,
                 ("no new leak between zones" + (f" ({len(still)} pre-existing)" if still else ""))
                 if not new else f"{len(new)} new leak(s) between zones", new, still)
