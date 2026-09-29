"""Lab topology: a spine-leaf fabric with a core pair, described as plain data.

The spec is what a cabling plan and an IPAM export would say. It carries no runtime
state; `SimNetwork` builds the live network from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CORE_ASN = 65000
SPINE_ASN = 65100
LEAF_ASN_BASE = 65200
EXTERNAL_PREFIX = "198.51.100.0/24"
LEAF_UPLINK_BASE = 49
ACCESS_PORTS_PER_LEAF = 4
SPEED_100G = 100_000_000_000
SPEED_400G = 400_000_000_000
SPEED_25G = 25_000_000_000


@dataclass(frozen=True)
class DeviceSpec:
    name: str
    role: str
    asn: int
    loopback: str
    nos: str
    version: str


@dataclass(frozen=True)
class LinkSpec:
    a: str
    a_if: str
    b: str
    b_if: str
    speed_bps: int
    a_ip: str
    b_ip: str

    @property
    def name(self) -> str:
        return f"{self.a}:{self.a_if}<->{self.b}:{self.b_if}"


@dataclass(frozen=True)
class HostSpec:
    """A server on a leaf access port — a traffic endpoint, not a managed device."""

    name: str
    leaf: str
    port: str
    ip: str
    speed_bps: int = SPEED_25G


@dataclass(frozen=True)
class LabSpec:
    devices: tuple[DeviceSpec, ...]
    links: tuple[LinkSpec, ...]
    hosts: tuple[HostSpec, ...]
    networks: dict[str, tuple[str, ...]] = field(default_factory=dict)
    edge_ports: dict[str, str] = field(default_factory=dict)

    def device(self, name: str) -> DeviceSpec:
        for d in self.devices:
            if d.name == name:
                return d
        raise KeyError(name)


def leaf_prefix(index: int) -> str:
    return f"10.{index}.0.0/24"


def build_lab(cores: int = 2, spines: int = 2, leaves: int = 4) -> LabSpec:
    """Build the reference lab: every leaf to every spine, every spine to every core.

    Interface numbering follows common fixed-form hardware: leaf access ports are
    Ethernet1..4, leaf uplinks start at Ethernet49, spine downlinks at Ethernet1 and
    spine uplinks at Ethernet31; core downlinks at Ethernet1, the core-to-core DCI on
    Ethernet9 and the transit edge on Ethernet10.
    """
    if not (1 <= cores <= 4 and 1 <= spines <= 8 and 1 <= leaves <= 32):
        raise ValueError("lab size out of range: cores 1..4, spines 1..8, leaves 1..32")
    devices: list[DeviceSpec] = []
    for i in range(1, cores + 1):
        devices.append(DeviceSpec(f"core{i}", "core", CORE_ASN, f"192.0.2.{i}", "simnos", "4.32.2"))
    for i in range(1, spines + 1):
        devices.append(DeviceSpec(f"spine{i}", "spine", SPINE_ASN, f"192.0.2.{10 + i}", "simnos", "4.31.0"))
    for i in range(1, leaves + 1):
        devices.append(DeviceSpec(f"leaf{i}", "leaf", LEAF_ASN_BASE + i, f"192.0.2.{100 + i}", "simnos",
                                  "4.30.1"))

    links: list[LinkSpec] = []

    def p2p(a: str, a_if: str, b: str, b_if: str, speed: int) -> None:
        n = len(links)
        links.append(LinkSpec(a, a_if, b, b_if, speed, f"10.255.{n}.0", f"10.255.{n}.1"))

    for li in range(1, leaves + 1):
        for si in range(1, spines + 1):
            p2p(f"leaf{li}", f"Ethernet{LEAF_UPLINK_BASE + si - 1}", f"spine{si}", f"Ethernet{li}", SPEED_100G)
    for si in range(1, spines + 1):
        for ci in range(1, cores + 1):
            p2p(f"spine{si}", f"Ethernet{30 + ci}", f"core{ci}", f"Ethernet{si}", SPEED_400G)
    for ci in range(1, cores):
        p2p(f"core{ci}", "Ethernet9", f"core{ci + 1}", "Ethernet9", SPEED_400G)

    hosts = tuple(
        HostSpec(f"srv{li}-{p}", f"leaf{li}", f"Ethernet{p}", f"10.{li}.0.{10 + p}")
        for li in range(1, leaves + 1)
        for p in range(1, ACCESS_PORTS_PER_LEAF + 1)
    )
    networks: dict[str, tuple[str, ...]] = {f"leaf{li}": (leaf_prefix(li),) for li in range(1, leaves + 1)}
    for ci in range(1, cores + 1):
        networks[f"core{ci}"] = (EXTERNAL_PREFIX,)
    edge_ports = {f"core{ci}": "Ethernet10" for ci in range(1, cores + 1)}
    return LabSpec(tuple(devices), tuple(links), hosts, networks, edge_ports)
