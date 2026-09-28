"""Device-side gNMI Set: validate a whole transaction, then apply it to the running config.

A Set is atomic, as on a real target: every update and delete is validated against a
copy of the running config first, and the device's config is replaced only if all of
them succeed. The settable surface is the part of OpenConfig the lab models — interface
admin state/description/MTU, BGP neighbor enable and peer AS, export policy, ACL entries
and ingress ACL bindings.
"""

from __future__ import annotations

import copy
from typing import Any

from wisp_net.paths import Path, parse_path
from wisp_net.acl import AclError, Rule

BGP_NEIGHBOR = ("network-instances", "network-instance", "protocols", "protocol", "bgp", "neighbors", "neighbor",
                "config")
EXPORT_DENY = ("network-instances", "network-instance", "policy", "export-deny", "config", "prefix")
ACL_ENTRY = ("acl", "acl-sets", "acl-set", "acl-entries", "acl-entry", "config")
ACL_BINDING = ("acl", "interfaces", "interface", "ingress-acl-sets", "ingress-acl-set", "config", "set-name")


class SetError(ValueError):
    pass


def _names(path: Path) -> tuple[str, ...]:
    return tuple(e.name for e in path)


def apply_set(config: dict[str, Any], updates: list[tuple[str, Any]], deletes: list[str]) -> dict[str, Any]:
    """Return a new running config with the transaction applied, or raise SetError."""
    new = copy.deepcopy(config)
    for path_text in deletes:
        _apply(new, path_text, None, delete=True)
    for path_text, value in updates:
        _apply(new, path_text, value, delete=False)
    for ifname, binding in new["acl-bindings"].items():
        acl = binding.get("ingress")
        if acl and not new["acls"].get(acl, {}).get("entries"):
            raise SetError(f"interface {ifname} would be bound to ACL {acl!r}, which has no entries")
    return new


def _apply(cfg: dict[str, Any], path_text: str, value: Any, delete: bool) -> None:
    try:
        path = parse_path(path_text)
    except ValueError as exc:
        raise SetError(str(exc)) from None
    names = _names(path)
    if names[:3] == ("interfaces", "interface", "config") and len(names) == 4:
        ifname = path[1].key("name") or ""
        if ifname not in cfg["interfaces"]:
            raise SetError(f"no interface {ifname!r}")
        if delete:
            raise SetError("interface config leaves can be updated, not deleted")
        _set_interface_leaf(cfg["interfaces"][ifname], names[3], value)
    elif names[:8] == BGP_NEIGHBOR and len(names) == 9:
        ip = path[6].key("neighbor-address") or ""
        neighbors = cfg["bgp"]["neighbors"]
        if ip not in neighbors:
            raise SetError(f"no BGP neighbor {ip!r} (the lab does not add neighbors)")
        if delete:
            raise SetError("BGP neighbor leaves can be updated, not deleted")
        leaf = names[8]
        if leaf == "enabled":
            neighbors[ip]["enabled"] = _bool(value, path_text)
        elif leaf == "peer-as":
            neighbors[ip]["peer-as"] = _int(value, path_text, 1, 4_294_967_295)
        elif leaf == "description":
            neighbors[ip]["description"] = str(value)
        else:
            raise SetError(f"unsupported BGP neighbor leaf {leaf!r}")
    elif names == EXPORT_DENY:
        prefix = path[3].key("prefix") or ""
        deny = cfg["bgp"].setdefault("export-deny", [])
        if delete:
            if prefix in deny:
                deny.remove(prefix)
        elif prefix not in deny:
            deny.append(prefix)
    elif names == ACL_ENTRY:
        acl = path[2].key("name") or ""
        seq = path[4].key("sequence-id") or ""
        entries = cfg["acls"].setdefault(acl, {"entries": []})["entries"]
        entries[:] = [e for e in entries if str(e["seq"]) != seq]
        if not delete:
            if not isinstance(value, dict):
                raise SetError(f"{path_text}: an ACL entry is an object")
            entry = {**value, "seq": int(seq)}
            try:
                Rule.from_entry(entry)
            except AclError as exc:
                raise SetError(f"{path_text}: {exc}") from None
            entries.append(entry)
            entries.sort(key=lambda e: int(e["seq"]))
        if not entries:
            cfg["acls"].pop(acl, None)
    elif names == ACL_BINDING:
        ifname = path[2].key("id") or ""
        acl = path[4].key("set-name") or ""
        if ifname not in cfg["interfaces"]:
            raise SetError(f"no interface {ifname!r}")
        if delete:
            cfg["acl-bindings"].get(ifname, {}).pop("ingress", None)
            if not cfg["acl-bindings"].get(ifname):
                cfg["acl-bindings"].pop(ifname, None)
        else:
            if value != acl:
                raise SetError(f"{path_text}: set-name {value!r} does not match the key {acl!r}")
            if acl not in cfg["acls"]:
                raise SetError(f"cannot bind unknown ACL {acl!r}")
            cfg["acl-bindings"].setdefault(ifname, {})["ingress"] = acl
    else:
        raise SetError(f"path is not settable in the lab: {path_text}")


def _set_interface_leaf(iface: dict[str, Any], leaf: str, value: Any) -> None:
    if leaf == "enabled":
        iface["enabled"] = _bool(value, leaf)
    elif leaf == "description":
        iface["description"] = str(value)
    elif leaf == "mtu":
        iface["mtu"] = _int(value, leaf, 1280, 9216)
    else:
        raise SetError(f"unsupported interface leaf {leaf!r}")


def _bool(value: Any, where: str) -> bool:
    if isinstance(value, bool):
        return value
    raise SetError(f"{where}: expected true/false, got {value!r}")


def _int(value: Any, where: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise SetError(f"{where}: expected an integer in {lo}..{hi}, got {value!r}")
    return int(value)


def ingress_rules(config: dict[str, Any], ifname: str) -> list[Rule] | None:
    """The parsed ingress ACL bound to `ifname`, or None when nothing is bound."""
    acl = config["acl-bindings"].get(ifname, {}).get("ingress")
    if not acl:
        return None
    return sorted((Rule.from_entry(e) for e in config["acls"].get(acl, {}).get("entries", [])),
                  key=lambda r: r.seq)
