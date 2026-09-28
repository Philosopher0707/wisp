"""Digital twin: the network as telemetry reports it, with history.

Built only from gNMI notifications — never from the simulator's internals — so it holds
exactly what an operator could know, including stale knowledge of a device whose
management plane stopped answering. Entities: devices, interfaces, links (from LLDP,
remembered while down), BGP peers, advertised networks, FIB entries, ACLs, VRFs.

Queries: physical shortest paths, forwarding traces that follow each hop's FIB (ECMP
fan-out, blackhole and loop detection), a reachability matrix, and versioned snapshots
with structural diffs. Neo4j/Neptune can host the same model; the queries are the
contract.
"""

from __future__ import annotations

import copy
import ipaddress
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

from wisp_net.paths import parse_path

_SPEED_BPS = {"SPEED_25GB": 25e9, "SPEED_100GB": 100e9, "SPEED_400GB": 400e9}
MAX_TRACE_HOPS = 16
MAX_SNAPSHOTS = 500


@dataclass(frozen=True)
class TraceResult:
    source: str
    destination: str
    prefix: str | None
    outcome: str  # delivered | partial | blackhole | loop | unknown
    paths: tuple[tuple[str, ...], ...]
    failures: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()  # devices traversed on last-known state (not reporting now)


@dataclass
class Snapshot:
    version: int
    ts: float
    label: str
    entities: dict[str, Any]


@dataclass
class _DeviceState:
    reachable: bool = True
    last_seen: float = 0.0
    system: dict[str, Any] = field(default_factory=dict)
    interfaces: dict[str, dict[str, Any]] = field(default_factory=dict)
    lldp: dict[str, tuple[str, str]] = field(default_factory=dict)
    bgp: dict[str, dict[str, Any]] = field(default_factory=dict)
    bgp_global: dict[str, Any] = field(default_factory=dict)
    networks: list[str] = field(default_factory=list)
    fib: dict[str, dict[str, Any]] = field(default_factory=dict)
    acls: dict[str, dict[int, dict[str, Any]]] = field(default_factory=dict)
    acl_bindings: dict[str, dict[str, str]] = field(default_factory=dict)
    export_deny: list[str] = field(default_factory=list)


class DigitalTwin:
    def __init__(self) -> None:
        self.devices: dict[str, _DeviceState] = {}
        self.links: dict[frozenset[tuple[str, str]], dict[str, Any]] = {}
        self.snapshots: list[Snapshot] = []
        self._version = 0
        self.last_sync: float = 0.0

    # ── ingest ───────────────────────────────────────────────────────────────

    def ingest(self, target: str, notifications: Iterable[Any], ts: float) -> None:
        """Replace everything known about `target` with one full sample of it."""
        st = _DeviceState(reachable=True, last_seen=ts)
        for n in notifications:
            self._apply(st, n.path, n.value)
        st.networks.sort()
        self.devices[target] = st

    def mark_unreachable(self, target: str, ts: float) -> None:
        st = self.devices.setdefault(target, _DeviceState(last_seen=0.0))
        st.reachable = False

    def _apply(self, st: _DeviceState, path_text: str, value: Any) -> None:
        path = parse_path(path_text)
        names = [e.name for e in path]
        head = names[0]
        if head == "system":
            if names[-2:] == ["total", "instant"]:
                st.system["cpu_pct"] = value
            elif names[:2] == ["system", "memory"]:
                st.system[f"mem_{names[-1]}"] = value
            elif names[:2] == ["system", "state"]:
                st.system[names[-1]] = value
        elif head == "interfaces":
            ifname = path[1].key("name") or ""
            iface = st.interfaces.setdefault(ifname, {"name": ifname, "counters": {}})
            if "counters" in names:
                iface["counters"][names[-1]] = value
            elif names[-1] == "port-speed":
                iface["speed_bps"] = _SPEED_BPS.get(value, 0.0)
            elif names[-1] == "prefix-length":
                iface["ip"] = path[-3].key("ip")
            elif len(names) == 4 and names[2] in ("state", "config"):
                iface[names[3]] = value
        elif head == "components" and names[-1] == "instant":
            ifname = path[1].key("name") or ""
            st.interfaces.setdefault(ifname, {"name": ifname, "counters": {}})["rx_power_dbm"] = value
        elif head == "lldp":
            ifname = path[2].key("name") or ""
            peer = path[4].key("id") or ""
            if names[-1] == "port-id":
                st.lldp[ifname] = (peer, str(value))
        elif head == "network-instances":
            self._apply_ni(st, path, names, value)
        elif head == "acl":
            if names[1] == "acl-sets":
                acl = path[2].key("name") or ""
                seq = int(path[4].key("sequence-id") or 0)
                st.acls.setdefault(acl, {})[seq] = dict(value)
            elif names[1] == "interfaces":
                ifname = path[2].key("id") or ""
                direction = names[3].split("-")[0]
                st.acl_bindings.setdefault(ifname, {})[direction] = str(value)

    def _apply_ni(self, st: _DeviceState, path: Any, names: list[str], value: Any) -> None:
        if "neighbors" in names:
            ip = path[names.index("neighbor")].key("neighbor-address") or ""
            peer = st.bgp.setdefault(ip, {"neighbor": ip})
            key = names[-1]
            if key in ("peer-as", "enabled", "description", "session-state", "established-transitions",
                       "last-established", "installed"):
                peer[key] = value
        elif "afts" in names:
            prefix = path[names.index("ipv4-entry")].key("prefix") or ""
            st.fib.setdefault(prefix, {"prefix": prefix})[names[-1]] = value
        elif "network" in names and names[-1] == "prefix":
            st.networks.append(str(value))
        elif names[-3:] == ["global", "config", "as"]:
            st.bgp_global["as"] = value
        elif names[-1] == "router-id":
            st.bgp_global["router-id"] = value
        elif "export-deny" in names:
            st.export_deny.append(str(value))

    def rebuild(self, ts: float) -> bool:
        """Recompute links from LLDP plus interface state; True if the topology changed."""
        before = {k: (v["up"],) for k, v in self.links.items()}
        seen: set[frozenset[tuple[str, str]]] = set()
        for dev, st in self.devices.items():
            for ifname, (peer, peer_if) in st.lldp.items():
                key = frozenset({(dev, ifname), (peer, peer_if)})
                seen.add(key)
                speed = st.interfaces.get(ifname, {}).get("speed_bps", 0.0)
                self.links[key] = {"ends": sorted(key), "up": True, "speed_bps": speed, "since": ts}
        for key, link in self.links.items():
            if key in seen:
                continue
            ends = sorted(key)
            states = [self._oper(d, i) for d, i in ends]
            known = [s for s in states if s is not None]
            if any(s == "DOWN" for s in known) or all(not self._reachable(d) for d, _ in ends):
                if link["up"]:
                    link["up"], link["since"] = False, ts
        self.last_sync = ts
        after = {k: (v["up"],) for k, v in self.links.items()}
        return before != after

    def _oper(self, dev: str, ifname: str) -> str | None:
        st = self.devices.get(dev)
        if st is None or not st.reachable:
            return None
        return st.interfaces.get(ifname, {}).get("oper-status")

    def _reachable(self, dev: str) -> bool:
        st = self.devices.get(dev)
        return bool(st and st.reachable)

    # ── queries ──────────────────────────────────────────────────────────────

    def neighbors(self, dev: str, up_only: bool = True) -> list[tuple[str, str, str]]:
        """(local_if, peer, peer_if) for every link at `dev`."""
        out = []
        for key, link in self.links.items():
            if up_only and not link["up"]:
                continue
            ends = sorted(key)
            for (d, i), (p, pi) in ((ends[0], ends[1]), (ends[1], ends[0])):
                if d == dev:
                    out.append((i, p, pi))
        return sorted(out)

    def physical_paths(self, src: str, dst: str, limit: int = 16) -> list[list[str]]:
        """All shortest device paths over up links (BFS), at most `limit`."""
        if src not in self.devices or dst not in self.devices:
            raise KeyError(f"unknown device: {src if src not in self.devices else dst}")
        dist = {src: 0}
        parents: dict[str, list[str]] = {src: []}
        queue = deque([src])
        while queue:
            node = queue.popleft()
            for _, peer, _ in self.neighbors(node):
                if peer not in dist:
                    dist[peer] = dist[node] + 1
                    parents[peer] = [node]
                    queue.append(peer)
                elif dist[peer] == dist[node] + 1 and node not in parents[peer]:
                    parents[peer].append(node)
        if dst not in dist:
            return []
        paths: list[list[str]] = []

        def walk(node: str, acc: list[str]) -> None:
            if len(paths) >= limit:
                return
            if node == src:
                paths.append([src] + acc[::-1])
                return
            for parent in sorted(parents[node]):
                walk(parent, acc + [node])

        walk(dst, [])
        return paths

    def lookup(self, dev: str, destination: str) -> str | None:
        """Longest-prefix match of `destination` (an IP or a prefix) in `dev`'s FIB."""
        st = self.devices.get(dev)
        if st is None:
            return None
        if "/" in destination and destination in st.fib:
            return destination
        try:
            addr = ipaddress.ip_network(destination, strict=False)
        except ValueError:
            return None
        best: tuple[int, str] | None = None
        for prefix in st.fib:
            net = ipaddress.ip_network(prefix, strict=False)
            if addr.subnet_of(net) and (best is None or net.prefixlen > best[0]):  # type: ignore[arg-type]
                best = (net.prefixlen, prefix)
        return best[1] if best else None

    def trace(self, src: str, destination: str) -> TraceResult:
        """Follow the FIB hop by hop from `src`, fanning out over ECMP next hops.

        A device that stopped reporting is traversed on its last known FIB and listed in
        `stale`: its management plane being silent says nothing about its data plane. A
        device the twin never heard from ends that branch as `unknown`.
        """
        if src not in self.devices:
            raise KeyError(f"unknown device {src!r}")
        first = self.lookup(src, destination)
        paths: list[tuple[str, ...]] = []
        failures: list[str] = []
        stale: set[str] = set()
        unknown: list[str] = []

        def walk(dev: str, acc: tuple[str, ...]) -> None:
            if len(paths) + len(failures) >= 64:
                return
            if dev in acc:
                failures.append(f"loop: {' -> '.join(acc + (dev,))}")
                return
            acc = acc + (dev,)
            if len(acc) > MAX_TRACE_HOPS:
                failures.append(f"hop limit: {' -> '.join(acc)}")
                return
            st = self.devices.get(dev)
            if st is None or (not st.reachable and not st.fib):
                unknown.append(f"no state for {dev}: it has never reported telemetry")
                return
            if not st.reachable:
                stale.add(dev)
            prefix = self.lookup(dev, destination)
            entry = st.fib.get(prefix) if prefix else None
            if entry is None:
                failures.append(f"blackhole: {dev} has no route to {destination}")
                return
            if entry.get("origin-protocol") == "LOCAL":
                paths.append(acc)
                return
            hops = entry.get("next-hops") or []
            if not hops:
                failures.append(f"blackhole: {dev} route {prefix} has no next hop")
                return
            for hop in hops:
                walk(str(hop.get("peer")), acc)

        walk(src, ())
        if paths and not failures and not unknown:
            outcome = "delivered"
        elif paths:
            outcome = "partial"
        elif any(f.startswith("loop") for f in failures):
            outcome = "loop"
        elif failures:
            outcome = "blackhole"
        else:
            outcome = "unknown"
        return TraceResult(src, destination, first, outcome, tuple(paths), tuple(failures + unknown),
                           tuple(sorted(stale)))

    def advertised(self) -> dict[str, list[str]]:
        return {dev: list(st.networks) for dev, st in sorted(self.devices.items()) if st.networks}

    def reachability(self, sources: list[str] | None = None) -> dict[str, Any]:
        """Trace every source device to every advertised prefix it does not originate."""
        adv = self.advertised()
        prefixes = sorted({p for ps in adv.values() for p in ps})
        srcs = sources or sorted(adv)
        matrix: dict[str, dict[str, str]] = {}
        broken: list[dict[str, Any]] = []
        stale: set[str] = set()
        for s in srcs:
            row: dict[str, str] = {}
            for p in prefixes:
                if p in adv.get(s, []):
                    continue
                result = self.trace(s, p)
                row[p] = result.outcome
                stale.update(result.stale)
                if result.outcome != "delivered":
                    broken.append({"source": s, "prefix": p, "outcome": result.outcome,
                                   "why": list(result.failures[:3])})
            matrix[s] = row
        total = sum(len(r) for r in matrix.values())
        return {"pairs": total, "delivered": total - len(broken), "broken": broken, "matrix": matrix,
                "verified_on_stale_state": sorted(stale)}

    # ── history ──────────────────────────────────────────────────────────────

    def entities(self) -> dict[str, Any]:
        return {
            "links": {" <-> ".join(f"{d}:{i}" for d, i in sorted(k)): {"up": v["up"], "speed_bps": v["speed_bps"]}
                      for k, v in sorted(self.links.items(), key=lambda kv: sorted(kv[0]))},
            "bgp": {f"{dev}|{ip}": {"state": p.get("session-state"), "peer-as": p.get("peer-as"),
                                    "enabled": p.get("enabled")}
                    for dev, st in sorted(self.devices.items()) for ip, p in sorted(st.bgp.items())},
            "routes": {f"{dev}|{prefix}": sorted(str(h.get("peer")) for h in (e.get("next-hops") or []))
                       for dev, st in sorted(self.devices.items()) for prefix, e in sorted(st.fib.items())},
            "devices": {dev: {"reachable": st.reachable, "software": st.system.get("software-version")}
                        for dev, st in sorted(self.devices.items())},
        }

    def snapshot(self, ts: float, label: str = "") -> Snapshot:
        self._version += 1
        snap = Snapshot(self._version, ts, label, copy.deepcopy(self.entities()))
        self.snapshots.append(snap)
        if len(self.snapshots) > MAX_SNAPSHOTS:
            self.snapshots.pop(0)
        return snap

    def snapshot_at(self, ts: float) -> Snapshot | None:
        best = None
        for s in self.snapshots:
            if s.ts <= ts:
                best = s
        return best

    def get_snapshot(self, version: int) -> Snapshot:
        for s in self.snapshots:
            if s.version == version:
                return s
        raise KeyError(f"snapshot {version} is not retained")

    @staticmethod
    def diff(a: Snapshot, b: Snapshot) -> dict[str, Any]:
        out: dict[str, Any] = {"from": a.version, "to": b.version, "from_ts": a.ts, "to_ts": b.ts}
        for kind in ("links", "bgp", "routes", "devices"):
            old, new = a.entities.get(kind, {}), b.entities.get(kind, {})
            changes = {
                "added": sorted(set(new) - set(old)),
                "removed": sorted(set(old) - set(new)),
                "changed": [{"key": k, "from": old[k], "to": new[k]}
                            for k in sorted(set(old) & set(new)) if old[k] != new[k]],
            }
            if any(changes.values()):
                out[kind] = changes
        return out
