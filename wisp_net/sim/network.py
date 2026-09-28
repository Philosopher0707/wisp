"""SimNetwork: the lab's physical world — devices, links, BGP, traffic, optics and faults.

Everything above this layer (collectors, twin, agents) sees it only through the gNMI
facade and the syslog/flow exports, exactly as it would see real hardware. Time is a
logical clock advanced by `step(dt)`, and all randomness comes from one seeded RNG, so
a run is reproducible tick for tick.

Model, deliberately simple where it does not change what an operator would observe:
  * eBGP/iBGP path-vector routing with AS-path loop prevention, iBGP split horizon and
    ECMP across equal-length paths; a session is ESTABLISHED a few seconds after its
    link is up and both sides are configured to agree.
  * Traffic is a demand matrix (east-west between leaves, north-south via the cores),
    forwarded hop by hop over the FIB and split evenly across ECMP next hops.
  * Optics: receive power drives the FCS error rate; below loss-of-signal the link drops.
"""

from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from wisp_net.sim.topology import (
    EXTERNAL_PREFIX,
    LabSpec,
    LinkSpec,
)

SIM_EPOCH = 1_767_614_400.0  # Monday 2026-01-05T12:00:00Z: the lab starts at a busy hour
BGP_ESTABLISH_DELAY_S = 3.0
AVG_PACKET_BYTES = 800
RX_NOMINAL_DBM = -2.5
RX_FCS_ONSET_DBM = -12.0
RX_LOS_DBM = -28.0
MAX_HOPS = 16
SYSLOG_ENTERPRISE = 32473  # RFC 5612: reserved for documentation
FACILITY_LOCAL7 = 23


class DeviceUnreachable(RuntimeError):
    """The management plane of a device does not answer."""


@dataclass
class Counters:
    in_octets: int = 0
    out_octets: int = 0
    in_pkts: int = 0
    out_pkts: int = 0
    in_errors: int = 0
    in_fcs_errors: int = 0
    in_discards: int = 0
    out_discards: int = 0


@dataclass
class SimInterface:
    name: str
    kind: str  # fabric | access | edge
    speed_bps: int
    peer: tuple[str, str] | None = None
    ip: str | None = None
    oper_up: bool = True
    last_change: float = SIM_EPOCH
    rx_power_dbm: float = RX_NOMINAL_DBM
    counters: Counters = field(default_factory=Counters)
    tx_bps: float = 0.0
    rx_bps: float = 0.0
    fcs_error_rate: float = 0.0


@dataclass
class BgpSession:
    device: str
    local_if: str
    local_ip: str
    neighbor_ip: str
    peer_device: str
    state: str = "IDLE"
    since: float = SIM_EPOCH
    ready_at: float | None = None
    transitions: int = 0
    last_established: float | None = None


@dataclass(frozen=True)
class Route:
    prefix: str
    as_path: tuple[int, ...]
    origin: str  # local | ebgp | ibgp
    next_hops: tuple[tuple[str, str, str], ...]  # (local_if, peer_device, peer_ip)


@dataclass
class Fault:
    fault_id: str
    kind: str
    target: str
    params: dict[str, Any]
    started: float
    active: bool = True


@dataclass
class SimDevice:
    name: str
    role: str
    asn: int
    loopback: str
    nos: str
    version: str
    config: dict[str, Any]
    interfaces: dict[str, SimInterface]
    boot_time: float = SIM_EPOCH
    cpu_pct: float = 12.0
    mem_total: int = 16 * 1024**3
    mem_used: int = 5 * 1024**3
    rib: dict[str, Route] = field(default_factory=dict)


FAULT_KINDS = (
    "link_down", "link_flap", "optic_degrade", "crc_surge", "bgp_down",
    "cpu_spike", "congestion", "mgmt_unreachable",
)


def rfc3339(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class SimNetwork:
    def __init__(self, spec: LabSpec, seed: int = 7) -> None:
        self.spec = spec
        self.now = SIM_EPOCH
        self.rng = random.Random(seed)
        self.devices: dict[str, SimDevice] = {}
        self.links: dict[str, LinkSpec] = {link.name: link for link in spec.links}
        self.failed_links: set[str] = set()
        self.sessions: dict[tuple[str, str], BgpSession] = {}
        self.faults: dict[str, Fault] = {}
        self._fault_seq = 0
        self._syslog: list[str] = []
        self._flows: dict[tuple[str, str, str, str, int, int], list[int]] = {}
        self._routes_dirty = True
        self._fcs_window: dict[tuple[str, str], int] = {}
        self._fcs_window_start = self.now
        self._rx_alarm: set[tuple[str, str]] = set()
        self.drops: dict[str, float] = {}
        self._build()
        self._update_links()
        self._update_sessions(initial=True)
        self._recompute_routes()

    # ── construction ─────────────────────────────────────────────────────────

    def _build(self) -> None:
        for d in self.spec.devices:
            self.devices[d.name] = SimDevice(
                name=d.name, role=d.role, asn=d.asn, loopback=d.loopback, nos=d.nos, version=d.version,
                config=self._base_config(d.name, d.asn, d.loopback), interfaces={})
        for link in self.spec.links:
            for dev, ifname, peer, peer_if, ip in ((link.a, link.a_if, link.b, link.b_if, link.a_ip),
                                                   (link.b, link.b_if, link.a, link.a_if, link.b_ip)):
                self._add_interface(dev, SimInterface(ifname, "fabric", link.speed_bps, (peer, peer_if), ip),
                                    f"to {peer} {peer_if}")
        for host in self.spec.hosts:
            self._add_interface(host.leaf, SimInterface(host.port, "access", host.speed_bps), f"server {host.name}")
        for dev, port in self.spec.edge_ports.items():
            self._add_interface(dev, SimInterface(port, "edge", 400_000_000_000), "transit edge")
        for link in self.spec.links:
            a, b = self.devices[link.a], self.devices[link.b]
            a.config["bgp"]["neighbors"][link.b_ip] = {"peer-as": b.asn, "enabled": True,
                                                       "description": f"{link.b} {link.b_if}"}
            b.config["bgp"]["neighbors"][link.a_ip] = {"peer-as": a.asn, "enabled": True,
                                                       "description": f"{link.a} {link.a_if}"}
            self.sessions[(link.a, link.b_ip)] = BgpSession(link.a, link.a_if, link.a_ip, link.b_ip, link.b)
            self.sessions[(link.b, link.a_ip)] = BgpSession(link.b, link.b_if, link.b_ip, link.a_ip, link.a)
        for dev, prefixes in self.spec.networks.items():
            self.devices[dev].config["bgp"]["networks"] = list(prefixes)

    @staticmethod
    def _base_config(name: str, asn: int, loopback: str) -> dict[str, Any]:
        return {
            "system": {"hostname": name, "ntp-servers": ["192.0.2.250", "192.0.2.251"],
                       "ssh-server": {"enable": True, "protocol-version": "V2"},
                       "login-banner": "Authorized access only"},
            "interfaces": {},
            "bgp": {"as": asn, "router-id": loopback, "networks": [], "neighbors": {}, "export-deny": []},
            "acls": {},
            "acl-bindings": {},
        }

    def _add_interface(self, dev: str, iface: SimInterface, description: str) -> None:
        self.devices[dev].interfaces[iface.name] = iface
        self.devices[dev].config["interfaces"][iface.name] = {
            "enabled": True, "description": description, "mtu": 9214}

    # ── faults ───────────────────────────────────────────────────────────────

    def inject(self, kind: str, target: str, **params: Any) -> str:
        if kind not in FAULT_KINDS:
            raise ValueError(f"unknown fault kind {kind!r}; one of {', '.join(FAULT_KINDS)}")
        self._validate_target(kind, target)
        self._fault_seq += 1
        fault_id = f"f{self._fault_seq}"
        self.faults[fault_id] = Fault(fault_id, kind, target, dict(params), self.now)
        if kind in ("link_down", "bgp_down"):
            self._routes_dirty = True
        return fault_id

    def clear(self, fault_id: str) -> None:
        fault = self.faults.get(fault_id)
        if fault is None:
            raise KeyError(fault_id)
        fault.active = False
        if fault.kind == "optic_degrade":
            dev, ifname = fault.target.split(":", 1)
            self.devices[dev].interfaces[ifname].rx_power_dbm = RX_NOMINAL_DBM
        self._routes_dirty = True

    def active_faults(self, kind: str | None = None) -> list[Fault]:
        return [f for f in self.faults.values() if f.active and (kind is None or f.kind == kind)]

    def _validate_target(self, kind: str, target: str) -> None:
        if kind in ("link_down", "link_flap"):
            self._link_for(target)
        elif kind in ("optic_degrade", "crc_surge"):
            dev, _, ifname = target.partition(":")
            if dev not in self.devices or ifname not in self.devices[dev].interfaces:
                raise ValueError(f"no interface {target!r}")
        elif kind == "bgp_down":
            dev, _, ip = target.partition(":")
            if (dev, ip) not in self.sessions:
                raise ValueError(f"no BGP session {target!r}")
        elif kind in ("cpu_spike", "mgmt_unreachable"):
            if target not in self.devices:
                raise ValueError(f"no device {target!r}")
        elif kind == "congestion":
            src, _, dst = target.partition("->")
            if not dst or any(end != "external" and end not in self.devices for end in (src, dst)):
                raise ValueError(f"congestion target must be '<leaf|external>-><leaf|external>': {target!r}")

    def _link_for(self, target: str) -> LinkSpec:
        if target in self.links:
            return self.links[target]
        dev, _, ifname = target.partition(":")
        for link in self.links.values():
            if (link.a, link.a_if) == (dev, ifname) or (link.b, link.b_if) == (dev, ifname):
                return link
        raise ValueError(f"no link {target!r}")

    # ── time ─────────────────────────────────────────────────────────────────

    def step(self, dt: float = 1.0) -> None:
        if dt <= 0:
            raise ValueError("dt must be positive")
        self.now += dt
        self._apply_faults(dt)
        self._update_links()
        self._update_sessions()
        if self._routes_dirty:
            self._recompute_routes()
        self._carry_traffic(dt)
        self._update_system(dt)
        self._emit_periodic_syslog()

    def _apply_faults(self, dt: float) -> None:
        for f in self.active_faults("optic_degrade"):
            dev, ifname = f.target.split(":", 1)
            iface = self.devices[dev].interfaces[ifname]
            rate = float(f.params.get("rate_db_per_min", 0.5))
            iface.rx_power_dbm = max(-40.0, iface.rx_power_dbm - rate * dt / 60.0)

    def _link_failed(self, link: LinkSpec) -> bool:
        for f in self.active_faults():
            if f.kind == "link_down" and self._link_for(f.target) == link:
                return True
            if f.kind == "link_flap" and self._link_for(f.target) == link:
                period = float(f.params.get("period_s", 20.0))
                if int((self.now - f.started) // (period / 2)) % 2 == 0:
                    return True
        return False

    def _update_links(self) -> None:
        for link in self.links.values():
            a = self.devices[link.a].interfaces[link.a_if]
            b = self.devices[link.b].interfaces[link.b_if]
            admin = (self._if_enabled(link.a, link.a_if) and self._if_enabled(link.b, link.b_if))
            los = a.rx_power_dbm <= RX_LOS_DBM or b.rx_power_dbm <= RX_LOS_DBM
            up = admin and not los and not self._link_failed(link)
            for dev, iface in ((link.a, a), (link.b, b)):
                if iface.oper_up != up:
                    iface.oper_up = up
                    iface.last_change = self.now
                    self._routes_dirty = True
                    self._log_link(dev, iface.name, up)
            if up:
                self.failed_links.discard(link.name)
            else:
                self.failed_links.add(link.name)
        for device in self.devices.values():
            for iface in device.interfaces.values():
                if iface.kind != "fabric":
                    up = self._if_enabled(device.name, iface.name)
                    if iface.oper_up != up:
                        iface.oper_up = up
                        iface.last_change = self.now
                        self._log_link(device.name, iface.name, up)

    def _if_enabled(self, dev: str, ifname: str) -> bool:
        return bool(self.devices[dev].config["interfaces"][ifname]["enabled"])

    def _session_should_be_up(self, s: BgpSession) -> bool:
        dev, peer = self.devices[s.device], self.devices[s.peer_device]
        mine = dev.config["bgp"]["neighbors"].get(s.neighbor_ip)
        theirs = peer.config["bgp"]["neighbors"].get(s.local_ip)
        if not mine or not theirs or not mine.get("enabled") or not theirs.get("enabled"):
            return False
        if mine.get("peer-as") != peer.asn or theirs.get("peer-as") != dev.asn:
            return False
        if not dev.interfaces[s.local_if].oper_up:
            return False
        for f in self.active_faults("bgp_down"):
            fdev, _, fip = f.target.partition(":")
            if (fdev, fip) in ((s.device, s.neighbor_ip), (s.peer_device, s.local_ip)):
                return False
        return True

    def _update_sessions(self, initial: bool = False) -> None:
        for s in self.sessions.values():
            want = self._session_should_be_up(s)
            if want and s.state != "ESTABLISHED":
                if initial:
                    self._set_session(s, "ESTABLISHED", log=False)
                elif s.ready_at is None:
                    s.ready_at = self.now + BGP_ESTABLISH_DELAY_S
                    s.state = "ACTIVE"
                elif self.now >= s.ready_at:
                    self._set_session(s, "ESTABLISHED")
            elif not want and s.state != "IDLE":
                s.ready_at = None
                if s.state == "ESTABLISHED":
                    self._set_session(s, "IDLE")
                else:
                    s.state = "IDLE"

    def _set_session(self, s: BgpSession, state: str, log: bool = True) -> None:
        old = s.state
        s.state = state
        s.since = self.now
        s.ready_at = None
        if state == "ESTABLISHED":
            s.transitions += 1
            s.last_established = self.now
        self._routes_dirty = True
        if log:
            peer_as = self.devices[s.peer_device].asn
            sev = 5 if state == "ESTABLISHED" else 4
            self._log(s.device, sev, "bgpd", "BGP_ADJCHANGE",
                      f'[bgp@{SYSLOG_ENTERPRISE} peer="{s.neighbor_ip}" vrf="default"]',
                      f"BGP neighbor {s.neighbor_ip} (AS {peer_as}) changed state from "
                      f"{old.title()} to {state.title()}")

    # ── routing ──────────────────────────────────────────────────────────────

    def _recompute_routes(self) -> None:
        self._routes_dirty = False
        local: dict[str, dict[str, Route]] = {}
        for dev in self.devices.values():
            local[dev.name] = {p: Route(p, (), "local", ()) for p in dev.config["bgp"]["networks"]}
        best = {name: dict(routes) for name, routes in local.items()}
        established = sorted((s for s in self.sessions.values() if s.state == "ESTABLISHED"),
                             key=lambda s: (s.device, s.neighbor_ip))
        for _ in range(4 * len(self.devices) + 4):
            candidates: dict[str, dict[str, list[Route]]] = {n: {} for n in self.devices}
            for s in established:
                receiver, sender = self.devices[s.device], self.devices[s.peer_device]
                ibgp = receiver.asn == sender.asn
                deny = set(sender.config["bgp"].get("export-deny", []))
                for prefix, route in best[sender.name].items():
                    if prefix in deny or (ibgp and route.origin == "ibgp"):
                        continue
                    as_path = route.as_path if ibgp else (sender.asn,) + route.as_path
                    if not ibgp and receiver.asn in as_path:
                        continue
                    candidates[receiver.name].setdefault(prefix, []).append(Route(
                        prefix, as_path, "ibgp" if ibgp else "ebgp", ((s.local_if, sender.name, s.neighbor_ip),)))
            new_best: dict[str, dict[str, Route]] = {}
            for name in self.devices:
                table = dict(local[name])
                for prefix, cands in candidates[name].items():
                    if prefix in table:
                        continue
                    table[prefix] = _select(prefix, cands)
                new_best[name] = table
            if new_best == best:
                break
            best = new_best
        for name, dev in self.devices.items():
            dev.rib = best[name]

    # ── traffic ──────────────────────────────────────────────────────────────

    def _demands(self) -> list[tuple[str, str, str, float]]:
        """(ingress device, ingress interface, destination prefix, bps) for this tick.

        A leaf's demand to each destination is spread over its servers (access ports), and
        what a port receives from its server is capped at the port's line rate: a host cannot
        send faster than its NIC, so overload shows up where it really does — at a shared
        egress (fabric uplink, destination port), as discards.
        """
        hour = (self.now / 3600.0) % 24
        diurnal = 0.6 + 0.4 * math.sin(2 * math.pi * (hour - 9) / 24)
        leaves = [d for d in self.devices.values() if d.role == "leaf"]
        cores = [d for d in self.devices.values() if d.role == "core"]
        demands: list[tuple[str, str, str, float]] = []

        def scaled(src: str, dst: str, base: float) -> float:
            mult = 1.0
            for f in self.active_faults("congestion"):
                if f.target == f"{src}->{dst}":
                    mult *= float(f.params.get("multiplier", 8.0))
            return base * diurnal * mult * self.rng.uniform(0.9, 1.1)

        for src in leaves:
            access = [i.name for i in src.interfaces.values() if i.kind == "access"]
            wanted = [(dst.config["bgp"]["networks"][0], scaled(src.name, dst.name, 3e9))
                      for dst in leaves if dst is not src]
            if cores:
                wanted.append((EXTERNAL_PREFIX, scaled(src.name, "external", 6e9)))
            for port in access:
                for prefix, bps in wanted:
                    demands.append((src.name, port, prefix, bps / len(access)))
        for core in cores:
            edge = self.spec.edge_ports.get(core.name, "")
            for dst in leaves:
                demands.append((core.name, edge, dst.config["bgp"]["networks"][0],
                                scaled("external", dst.name, 12e9 / len(cores))))
        return self._cap_ingress(demands)

    def _cap_ingress(self, demands: list[tuple[str, str, str, float]]) -> list[tuple[str, str, str, float]]:
        offered: dict[tuple[str, str], float] = {}
        for dev, port, _, bps in demands:
            offered[(dev, port)] = offered.get((dev, port), 0.0) + bps
        out = []
        for dev, port, prefix, bps in demands:
            iface = self.devices[dev].interfaces.get(port)
            total = offered[(dev, port)]
            if iface is not None and total > iface.speed_bps:
                bps *= iface.speed_bps / total
            out.append((dev, port, prefix, bps))
        return out

    def _carry_traffic(self, dt: float) -> None:
        tx: dict[tuple[str, str], float] = {}
        rx: dict[tuple[str, str], float] = {}
        self.drops = {}
        for dev, in_if, prefix, bps in self._demands():
            if in_if:
                rx[(dev, in_if)] = rx.get((dev, in_if), 0.0) + bps
            self._forward(dev, prefix, bps, 0, tx, rx)
            self._export_flows(dev, in_if, prefix, bps, dt)
        for device in self.devices.values():
            for iface in device.interfaces.values():
                t = tx.get((device.name, iface.name), 0.0) if iface.oper_up else 0.0
                r = rx.get((device.name, iface.name), 0.0) if iface.oper_up else 0.0
                dropped = max(0.0, t - iface.speed_bps)
                delivered = t - dropped
                iface.tx_bps, iface.rx_bps = delivered, r
                c = iface.counters
                c.out_octets += int(delivered * dt / 8)
                c.out_pkts += int(delivered * dt / 8 / AVG_PACKET_BYTES)
                c.out_discards += int(dropped * dt / 8 / AVG_PACKET_BYTES)
                in_pkts = int(r * dt / 8 / AVG_PACKET_BYTES)
                c.in_octets += int(r * dt / 8)
                c.in_pkts += in_pkts
                iface.fcs_error_rate = self._fcs_rate(device.name, iface)
                fcs = int(in_pkts * iface.fcs_error_rate)
                c.in_fcs_errors += fcs
                c.in_errors += fcs
                if fcs:
                    key = (device.name, iface.name)
                    self._fcs_window[key] = self._fcs_window.get(key, 0) + fcs

    def _forward(self, dev: str, prefix: str, bps: float, hops: int,
                 tx: dict[tuple[str, str], float], rx: dict[tuple[str, str], float]) -> None:
        if hops > MAX_HOPS:
            self.drops["ttl-expired"] = self.drops.get("ttl-expired", 0.0) + bps
            return
        device = self.devices[dev]
        route = device.rib.get(prefix)
        if route is None:
            self.drops[f"{dev}:no-route"] = self.drops.get(f"{dev}:no-route", 0.0) + bps
            return
        if route.origin == "local":
            egress = [i for i in device.interfaces.values()
                      if i.kind in ("access", "edge") and i.oper_up]
            if prefix == EXTERNAL_PREFIX:
                egress = [i for i in egress if i.kind == "edge"]
            else:
                egress = [i for i in egress if i.kind == "access"]
            if not egress:
                self.drops[f"{dev}:no-egress"] = self.drops.get(f"{dev}:no-egress", 0.0) + bps
                return
            for iface in egress:
                tx[(dev, iface.name)] = tx.get((dev, iface.name), 0.0) + bps / len(egress)
            return
        share = bps / len(route.next_hops)
        for local_if, peer, _ in route.next_hops:
            peer_if = device.interfaces[local_if].peer
            tx[(dev, local_if)] = tx.get((dev, local_if), 0.0) + share
            if peer_if is not None:
                rx[peer_if] = rx.get(peer_if, 0.0) + share
            self._forward(peer, prefix, share, hops + 1, tx, rx)

    def _fcs_rate(self, dev: str, iface: SimInterface) -> float:
        rate = 0.0
        if iface.rx_power_dbm < RX_FCS_ONSET_DBM:
            rate = min(0.05, 1e-7 * 10 ** ((RX_FCS_ONSET_DBM - iface.rx_power_dbm) / 2))
        for f in self.active_faults("crc_surge"):
            if f.target == f"{dev}:{iface.name}":
                rate += float(f.params.get("error_rate", 1e-4))
        return rate

    def _export_flows(self, dev: str, in_if: str, prefix: str, bps: float, dt: float) -> None:
        """Account traffic to flow keys; `drain_flows` exports them, like an IPFIX active timeout."""
        dst_net = prefix.split("/")[0].rsplit(".", 1)[0]
        src_host = next((h for h in self.spec.hosts if h.leaf == dev and h.port == in_if), None)
        src_ip = src_host.ip if src_host else f"198.51.100.{10 + sum(map(ord, dev + prefix)) % 200}"
        dst_ip = f"{dst_net}.{11 + sum(map(ord, in_if + dev)) % 4}"
        for share, dport in ((0.6, 443), (0.25, 8443), (0.15, 5201)):
            key = (dev, in_if, src_ip, dst_ip, 6, dport)
            octets = int(bps * share * dt / 8)
            acc = self._flows.setdefault(key, [0, 0])
            acc[0] += octets
            acc[1] += max(1, octets // AVG_PACKET_BYTES)

    # ── system + periodic logs ───────────────────────────────────────────────

    def _update_system(self, dt: float) -> None:
        spikes = {f.target: float(f.params.get("pct", 97.0)) for f in self.active_faults("cpu_spike")}
        for dev in self.devices.values():
            base = 10.0 + 6.0 * self.rng.random()
            dev.cpu_pct = spikes.get(dev.name, base)

    def _emit_periodic_syslog(self) -> None:
        for dev in self.devices.values():
            for iface in dev.interfaces.values():
                key = (dev.name, iface.name)
                low = iface.rx_power_dbm < RX_FCS_ONSET_DBM + 2 and iface.kind == "fabric"
                if low and key not in self._rx_alarm:
                    self._rx_alarm.add(key)
                    self._log(dev.name, 4, "xcvr", "RX_POWER_LOW",
                              f'[xcvr@{SYSLOG_ENTERPRISE} interface="{iface.name}" '
                              f'value="{iface.rx_power_dbm:.2f}"]',
                              f"Rx power low alarm on {iface.name}: {iface.rx_power_dbm:.2f} dBm")
                elif not low and key in self._rx_alarm:
                    self._rx_alarm.discard(key)
        if self.now - self._fcs_window_start >= 60.0:
            for (dev_name, ifname), count in sorted(self._fcs_window.items()):
                if count >= 100:
                    self._log(dev_name, 4, "phy", "FCS_ERRORS",
                              f'[phy@{SYSLOG_ENTERPRISE} interface="{ifname}" count="{count}"]',
                              f"Interface {ifname}: {count} FCS errors in last 60s")
            self._fcs_window = {}
            self._fcs_window_start = self.now

    def _log_link(self, dev: str, ifname: str, up: bool) -> None:
        self._log(dev, 5 if up else 3, "ifmgr", "LINK_UP" if up else "LINK_DOWN",
                  f'[if@{SYSLOG_ENTERPRISE} interface="{ifname}"]',
                  f"Interface {ifname} changed state to {'up' if up else 'down'}")

    def _log(self, dev: str, severity: int, app: str, msgid: str, sd: str, msg: str) -> None:
        pri = FACILITY_LOCAL7 * 8 + severity
        self._syslog.append(f"<{pri}>1 {rfc3339(self.now)} {dev} {app} - {msgid} {sd} {msg}")

    # ── exports (what collectors can see) ────────────────────────────────────

    def drain_syslog(self) -> list[str]:
        out, self._syslog = self._syslog, []
        return out

    def drain_flows(self) -> list[dict[str, Any]]:
        out = [{"ts": self.now, "exporter": dev, "ingress_if": in_if, "src": src, "dst": dst, "proto": proto,
                "dport": dport, "octets": octets, "packets": packets}
               for (dev, in_if, src, dst, proto, dport), (octets, packets) in sorted(self._flows.items())]
        self._flows = {}
        return out

    def check_reachable(self, device: str) -> None:
        if device not in self.devices:
            raise KeyError(device)
        for f in self.active_faults("mgmt_unreachable"):
            if f.target == device:
                raise DeviceUnreachable(f"{device}: management plane unreachable (gNMI timeout)")

    def clone(self) -> "SimNetwork":
        """An independent copy for what-if simulation; the original is untouched."""
        return copy.deepcopy(self)


def _select(prefix: str, cands: list[Route]) -> Route:
    """Best path: shortest AS path, eBGP over iBGP; ECMP across equal candidates."""
    shortest = min(len(c.as_path) for c in cands)
    tier = [c for c in cands if len(c.as_path) == shortest]
    if any(c.origin == "ebgp" for c in tier):
        tier = [c for c in tier if c.origin == "ebgp"]
    tier.sort(key=lambda c: (c.as_path, c.next_hops))
    hops = tuple(sorted({h for c in tier for h in c.next_hops}))
    return Route(prefix, tier[0].as_path, tier[0].origin, hops)
