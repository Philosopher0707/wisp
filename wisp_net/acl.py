"""ACLs as exact header-space algebra over (src, dst, proto, dport) boxes.

A rule matches a box: four inclusive integer intervals (source address, destination
address, IP protocol, destination port). First-match evaluation of an ACL over a box is
computed by box intersection and subtraction, so every answer is exact — no sampling,
the same idea as Batfish/HSA equivalence classes, sized for ACLs a person writes.

Semantics: rules are evaluated in sequence order; the first match decides; anything no
rule matches is denied (the implicit deny). `dport` constrains the destination port and
is meaningful for TCP/UDP; a rule with a port and `proto: any` is treated as TCP+UDP.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any, Iterable

Interval = tuple[int, int]
Box = tuple[Interval, Interval, Interval, Interval]  # src, dst, proto, dport

ANY_ADDR: Interval = (0, 2**32 - 1)
ANY_PROTO: Interval = (0, 255)
ANY_PORT: Interval = (0, 65535)
UNIVERSE: Box = (ANY_ADDR, ANY_ADDR, ANY_PROTO, ANY_PORT)
PROTOCOLS = {"icmp": 1, "tcp": 6, "udp": 17}
ACTIONS = ("permit", "deny")


class AclError(ValueError):
    pass


def prefix_interval(prefix: str) -> Interval:
    try:
        net = ipaddress.ip_network(prefix, strict=False)
    except ValueError as exc:
        raise AclError(f"bad prefix {prefix!r}") from exc
    if net.version != 4:
        raise AclError(f"only IPv4 is modelled: {prefix!r}")
    return int(net.network_address), int(net.broadcast_address)


def _proto(value: Any) -> list[Interval]:
    if value in (None, "any", "ip"):
        return [ANY_PROTO]
    if isinstance(value, str) and value.lower() in PROTOCOLS:
        n = PROTOCOLS[value.lower()]
        return [(n, n)]
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise AclError(f"bad protocol {value!r}") from None
    if not 0 <= n <= 255:
        raise AclError(f"protocol out of range: {n}")
    return [(n, n)]


def _port(value: Any) -> Interval:
    if value in (None, "any"):
        return ANY_PORT
    text = str(value)
    lo_s, _, hi_s = text.partition("-")
    try:
        lo, hi = int(lo_s), int(hi_s or lo_s)
    except ValueError:
        raise AclError(f"bad port {value!r}") from None
    if not (0 <= lo <= hi <= 65535):
        raise AclError(f"port range out of bounds: {value!r}")
    return lo, hi


@dataclass(frozen=True)
class Rule:
    seq: int
    action: str
    src: str = "0.0.0.0/0"
    dst: str = "0.0.0.0/0"
    proto: str = "any"
    dport: str = "any"

    @classmethod
    def from_entry(cls, entry: dict[str, Any]) -> "Rule":
        action = str(entry.get("action", "")).lower()
        if action not in ACTIONS:
            raise AclError(f"rule {entry.get('seq')}: action must be permit or deny, got {entry.get('action')!r}")
        try:
            seq = int(entry["seq"])
        except (KeyError, TypeError, ValueError):
            raise AclError(f"rule needs an integer seq: {entry!r}") from None
        rule = cls(seq, action, str(entry.get("src", "0.0.0.0/0")), str(entry.get("dst", "0.0.0.0/0")),
                   str(entry.get("proto", "any")), str(entry.get("dport", "any")))
        rule.boxes()  # validate every field now, not at first use
        return rule

    def boxes(self) -> list[Box]:
        src, dst, port = prefix_interval(self.src), prefix_interval(self.dst), _port(self.dport)
        protos = _proto(self.proto)
        if port != ANY_PORT and protos == [ANY_PROTO]:
            protos = [(6, 6), (17, 17)]
        return [(src, dst, p, port) for p in protos]

    def describe(self) -> str:
        return f"{self.seq} {self.action} {self.proto} {self.src} -> {self.dst} dport {self.dport}"


def parse_acl(entries: Iterable[dict[str, Any]]) -> list[Rule]:
    rules = sorted((Rule.from_entry(e) for e in entries), key=lambda r: r.seq)
    seqs = [r.seq for r in rules]
    if len(seqs) != len(set(seqs)):
        raise AclError("duplicate sequence numbers")
    return rules


# ── box algebra ──────────────────────────────────────────────────────────────

def intersect(a: Box, b: Box) -> Box | None:
    out = []
    for (alo, ahi), (blo, bhi) in zip(a, b):
        lo, hi = max(alo, blo), min(ahi, bhi)
        if lo > hi:
            return None
        out.append((lo, hi))
    return (out[0], out[1], out[2], out[3])


def subtract(a: Box, b: Box) -> list[Box]:
    """a minus b as disjoint boxes (at most two per dimension)."""
    if intersect(a, b) is None:
        return [a]
    pieces: list[Box] = []
    rest = list(a)
    for d in range(4):
        (lo, hi), (blo, bhi) = rest[d], b[d]
        if lo < blo:
            piece = list(rest)
            piece[d] = (lo, blo - 1)
            pieces.append((piece[0], piece[1], piece[2], piece[3]))
        if bhi < hi:
            piece = list(rest)
            piece[d] = (bhi + 1, hi)
            pieces.append((piece[0], piece[1], piece[2], piece[3]))
        rest[d] = (max(lo, blo), min(hi, bhi))
    return pieces


def subtract_all(boxes: list[Box], cut: Box) -> list[Box]:
    return [p for box in boxes for p in subtract(box, cut)]


def volume(box: Box) -> int:
    v = 1
    for lo, hi in box:
        v *= hi - lo + 1
    return v


def evaluate(rules: list[Rule], space: Box = UNIVERSE) -> tuple[list[Box], list[Box]]:
    """First-match over `space`: (permitted boxes, denied boxes), disjoint and exhaustive."""
    remaining = [space]
    permitted: list[Box] = []
    denied: list[Box] = []
    for rule in rules:
        for rbox in rule.boxes():
            matched = [m for m in (intersect(r, rbox) for r in remaining) if m is not None]
            (permitted if rule.action == "permit" else denied).extend(matched)
            remaining = subtract_all(remaining, rbox)
        if not remaining:
            break
    denied.extend(remaining)
    return permitted, denied


def restrict(boxes: list[Box], rules: list[Rule] | None) -> list[Box]:
    """What of `boxes` an ACL lets through (no ACL: everything)."""
    if rules is None:
        return list(boxes)
    out: list[Box] = []
    for box in boxes:
        out.extend(evaluate(rules, box)[0])
    return out


@dataclass(frozen=True)
class Finding:
    kind: str  # shadowed | redundant
    rule: Rule
    covered_by: tuple[Rule, ...]

    def describe(self) -> str:
        by = ", ".join(str(r.seq) for r in self.covered_by)
        verb = "can never match: earlier rules with a different action" if self.kind == "shadowed" \
            else "is redundant: earlier rules with the same action"
        return f"rule {self.rule.describe()} {verb} ({by}) cover everything it matches"


def dead_rules(rules: list[Rule]) -> list[Finding]:
    """Rules no packet can reach: fully covered by the union of the rules before them."""
    findings: list[Finding] = []
    for i, rule in enumerate(rules):
        remaining = rule.boxes()
        cover: list[Rule] = []
        for earlier in rules[:i]:
            before = sum(volume(b) for b in remaining)
            for ebox in earlier.boxes():
                remaining = subtract_all(remaining, ebox)
            if sum(volume(b) for b in remaining) < before:
                cover.append(earlier)
            if not remaining:
                break
        if not remaining and cover:
            kind = "shadowed" if any(c.action != rule.action for c in cover) else "redundant"
            findings.append(Finding(kind, rule, tuple(cover)))
    return findings


def describe_box(box: Box) -> str:
    def addr(iv: Interval) -> str:
        lo, hi = iv
        if (lo, hi) == ANY_ADDR:
            return "any"
        nets = list(ipaddress.summarize_address_range(ipaddress.IPv4Address(lo), ipaddress.IPv4Address(hi)))
        return ",".join(str(n) for n in nets[:3]) + ("…" if len(nets) > 3 else "")

    def rng(iv: Interval, full: Interval) -> str:
        return "any" if iv == full else (str(iv[0]) if iv[0] == iv[1] else f"{iv[0]}-{iv[1]}")

    names = {v: k for k, v in PROTOCOLS.items()}
    proto = names.get(box[2][0], rng(box[2], ANY_PROTO)) if box[2][0] == box[2][1] else rng(box[2], ANY_PROTO)
    return f"{proto} {addr(box[0])} -> {addr(box[1])} dport {rng(box[3], ANY_PORT)}"
