"""gNMI facade over the simulated lab: Capabilities, Get and sampled Subscribe.

Paths follow OpenConfig (openconfig-interfaces, -platform/transceiver, -lldp,
-network-instance/BGP, -system). Two deliberate simplifications, both leaf-level:
the AFT entry carries its next hops and AS path inline instead of through a
next-hop-group indirection, and a transceiver component is named after its port.
A real gNMI client (pygnmi against containerlab or hardware) can replace this class
behind the same three methods.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wisp_net.paths import Path, matches, parse_path
from wisp_net.sim.network import SimDevice, SimNetwork

NI = "/network-instances/network-instance[name=default]"
BGP = f"{NI}/protocols/protocol[identifier=BGP][name=BGP]/bgp"
MODELS = ("openconfig-interfaces", "openconfig-if-ethernet", "openconfig-platform-transceiver",
          "openconfig-lldp", "openconfig-network-instance", "openconfig-bgp", "openconfig-aft",
          "openconfig-system")
_SPEED_ENUM = {25_000_000_000: "SPEED_25GB", 100_000_000_000: "SPEED_100GB", 400_000_000_000: "SPEED_400GB"}


@dataclass(frozen=True)
class Notification:
    timestamp_ns: int
    target: str
    path: str
    value: Any


def _ns(ts: float) -> int:
    return int(ts * 1e9)


def render_device(net: SimNetwork, dev: SimDevice) -> dict[str, Any]:
    """Every state and config leaf of one device, keyed by canonical path."""
    out: dict[str, Any] = {
        "/system/state/hostname": dev.config["system"]["hostname"],
        "/system/state/software-version": f"{dev.nos}-{dev.version}",
        "/system/state/boot-time": _ns(dev.boot_time),
        "/system/state/current-datetime": _ns(net.now),
        "/system/cpus/cpu[index=ALL]/state/total/instant": round(dev.cpu_pct, 1),
        "/system/memory/state/physical": dev.mem_total,
        "/system/memory/state/used": dev.mem_used,
        "/system/ssh-server/config/enable": dev.config["system"]["ssh-server"]["enable"],
        "/system/config/login-banner": dev.config["system"]["login-banner"],
    }
    for server in dev.config["system"]["ntp-servers"]:
        out[f"/system/ntp/servers/server[address={server}]/config/address"] = server
    for name, iface in dev.interfaces.items():
        base = f"/interfaces/interface[name={name}]"
        cfg = dev.config["interfaces"][name]
        out[f"{base}/config/enabled"] = cfg["enabled"]
        out[f"{base}/config/description"] = cfg["description"]
        out[f"{base}/config/mtu"] = cfg["mtu"]
        out[f"{base}/state/admin-status"] = "UP" if cfg["enabled"] else "DOWN"
        out[f"{base}/state/oper-status"] = "UP" if iface.oper_up else "DOWN"
        out[f"{base}/state/last-change"] = _ns(iface.last_change)
        out[f"{base}/ethernet/state/port-speed"] = _SPEED_ENUM.get(iface.speed_bps, "SPEED_UNKNOWN")
        c = iface.counters
        for leaf, value in (("in-octets", c.in_octets), ("out-octets", c.out_octets),
                            ("in-pkts", c.in_pkts), ("out-pkts", c.out_pkts),
                            ("in-errors", c.in_errors), ("in-fcs-errors", c.in_fcs_errors),
                            ("in-discards", c.in_discards), ("out-discards", c.out_discards)):
            out[f"{base}/state/counters/{leaf}"] = value
        if iface.ip:
            out[f"{base}/subinterfaces/subinterface[index=0]/ipv4/addresses/address[ip={iface.ip}]"
                f"/state/prefix-length"] = 31
        if iface.kind == "fabric":
            comp = f"/components/component[name={name}]/transceiver"
            out[f"{comp}/physical-channels/channel[index=0]/state/input-power/instant"] = \
                round(iface.rx_power_dbm, 2)
            out[f"{comp}/state/form-factor"] = "QSFP56_DD" if iface.speed_bps >= 400_000_000_000 else "QSFP28"
            if iface.oper_up and iface.peer:
                peer_dev, peer_if = iface.peer
                nb = f"/lldp/interfaces/interface[name={name}]/neighbors/neighbor[id={peer_dev}]/state"
                out[f"{nb}/system-name"] = peer_dev
                out[f"{nb}/port-id"] = peer_if
    bgp_cfg = dev.config["bgp"]
    out[f"{BGP}/global/config/as"] = bgp_cfg["as"]
    out[f"{BGP}/global/config/router-id"] = bgp_cfg["router-id"]
    received: dict[str, int] = {}
    for route in dev.rib.values():
        for _, _, peer_ip in route.next_hops:
            received[peer_ip] = received.get(peer_ip, 0) + 1
    for ip, ncfg in bgp_cfg["neighbors"].items():
        nb = f"{BGP}/neighbors/neighbor[neighbor-address={ip}]"
        out[f"{nb}/config/peer-as"] = ncfg["peer-as"]
        out[f"{nb}/config/enabled"] = ncfg["enabled"]
        out[f"{nb}/config/description"] = ncfg.get("description", "")
        session = net.sessions.get((dev.name, ip))
        if session is not None:
            out[f"{nb}/state/session-state"] = session.state
            out[f"{nb}/state/established-transitions"] = session.transitions
            if session.last_established is not None:
                out[f"{nb}/state/last-established"] = _ns(session.last_established)
            out[f"{nb}/afi-safis/afi-safi[afi-safi-name=IPV4_UNICAST]/state/prefixes/installed"] = \
                received.get(ip, 0)
    for prefix in bgp_cfg["networks"]:
        out[f"{BGP}/global/afi-safis/afi-safi[afi-safi-name=IPV4_UNICAST]/network[prefix={prefix}]/config/prefix"] = \
            prefix
    for prefix in bgp_cfg.get("export-deny", []):
        out[f"{NI}/policy/export-deny[prefix={prefix}]/config/prefix"] = prefix
    for prefix, route in dev.rib.items():
        entry = f"{NI}/afts/ipv4-unicast/ipv4-entry[prefix={prefix}]/state"
        out[f"{entry}/origin-protocol"] = "LOCAL" if route.origin == "local" else "BGP"
        out[f"{entry}/as-path"] = " ".join(str(a) for a in route.as_path)
        out[f"{entry}/next-hops"] = [{"interface": i, "ip-address": ip, "peer": p}
                                     for i, p, ip in route.next_hops]
    for acl_name, acl in dev.config["acls"].items():
        for entry in acl.get("entries", []):
            out[f"/acl/acl-sets/acl-set[name={acl_name}][type=ACL_IPV4]/acl-entries/acl-entry"
                f"[sequence-id={entry['seq']}]/config"] = dict(entry)
    for ifname, binding in dev.config["acl-bindings"].items():
        for direction, acl_name in binding.items():
            out[f"/acl/interfaces/interface[id={ifname}]/{direction}-acl-sets/"
                f"{direction}-acl-set[set-name={acl_name}][type=ACL_IPV4]/config/set-name"] = acl_name
    return out


class SimGnmi:
    """What a gNMI client sees of the lab."""

    def __init__(self, net: SimNetwork) -> None:
        self.net = net

    def capabilities(self) -> dict[str, Any]:
        return {"gnmi_version": "0.10.0", "supported_models": list(MODELS), "supported_encodings": ["JSON_IETF"]}

    def targets(self) -> list[str]:
        return sorted(self.net.devices)

    def get(self, target: str, path: str = "/") -> list[Notification]:
        """All leaves under `path` on `target` (`*` for every reachable device).

        A named unreachable target raises `DeviceUnreachable`, as a gNMI RPC would time out;
        with `*` it is skipped, as a collector fanning out would skip it.
        """
        return self._select(target, [path])

    def subscribe_sample(self, paths: list[str], target: str = "*") -> list[Notification]:
        """One SAMPLE-mode update covering every subscribed path."""
        return self._select(target, paths)

    def _select(self, target: str, paths: list[str]) -> list[Notification]:
        patterns: list[Path] = [() if p.strip() in ("", "/") else parse_path(p) for p in paths]
        names = self.targets() if target == "*" else [target]
        out: list[Notification] = []
        for name in names:
            if name not in self.net.devices:
                raise KeyError(f"unknown target {name!r}")
            if target == "*":
                try:
                    self.net.check_reachable(name)
                except Exception:
                    continue
            else:
                self.net.check_reachable(name)
            ts = _ns(self.net.now)
            for text, value in render_device(self.net, self.net.devices[name]).items():
                parsed = _parsed(text)
                if any(not p or matches(p, parsed) for p in patterns):
                    out.append(Notification(ts, name, text, value))
        return out


_PARSE_CACHE: dict[str, Path] = {}


def _parsed(text: str) -> Path:
    cached = _PARSE_CACHE.get(text)
    if cached is None:
        if len(_PARSE_CACHE) > 200_000:
            _PARSE_CACHE.clear()
        cached = _PARSE_CACHE[text] = parse_path(text)
    return cached
