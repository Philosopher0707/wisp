"""Intent compiler: a declared goal becomes the exact gNMI operations that achieve it.

The language model states *what* it wants as a small structured intent; this module owns
*how*, deterministically, and refuses intents that do not fit the network as telemetry
reports it. The output is a change set for `net_what_if` — never applied directly.

Intents:
  drain_link      {port: "leaf1:Ethernet49"}                 shut one end of a fabric link
  restore_link    {port}                                     undo a drain
  quarantine_host {ip}                                       shut the server's access port
  release_host    {ip}                                       undo a quarantine
  guard_zone      {zone, allow: [{proto, dport}], at: "destination"|"source"}
                                                             enforce the segmentation policy with ingress ACLs
  set_bgp_neighbor {device, neighbor, enabled}
"""

from __future__ import annotations

from typing import Any

from wisp_net.reasoning.security import host_location

IF_ENABLED = "/interfaces/interface[name={}]/config/enabled"
ACL = "/acl/acl-sets/acl-set[name={n}][type=ACL_IPV4]/acl-entries/acl-entry[sequence-id={q}]/config"
BIND = "/acl/interfaces/interface[id={i}]/ingress-acl-sets/ingress-acl-set[set-name={n}][type=ACL_IPV4]/config/set-name"
BGP_NEIGHBOR = ("/network-instances/network-instance[name=default]/protocols/protocol[identifier=BGP][name=BGP]"
                "/bgp/neighbors/neighbor[neighbor-address={}]/config/enabled")
FABRIC = "10.255.0.0/16"
KINDS = ("drain_link", "restore_link", "quarantine_host", "release_host", "guard_zone", "set_bgp_neighbor")


class IntentError(ValueError):
    pass


def compile_intent(service: Any, intent: dict[str, Any], confidence: float = 0.9) -> dict[str, Any]:
    kind = intent.get("kind")
    if kind not in KINDS:
        raise IntentError(f"unknown intent kind {kind!r}; one of {', '.join(KINDS)}")
    twin = service.twin
    ops: list[dict[str, Any]]
    if kind in ("drain_link", "restore_link"):
        dev, _, port = str(intent.get("port", "")).partition(":")
        st = twin.devices.get(dev)
        if st is None or port not in st.interfaces:
            raise IntentError(f"no port {intent.get('port')!r}")
        if kind == "drain_link":
            if port not in st.lldp:
                raise IntentError(f"{dev}:{port} is not an up fabric link (no LLDP neighbor)")
            others = [i for i, (_, _) in st.lldp.items() if i != port]
            if not others:
                raise IntentError(f"draining {dev}:{port} would isolate {dev}: it is its last up fabric link")
        ops = [{"device": dev, "path": IF_ENABLED.format(port), "value": kind == "restore_link"}]
        text = f"{'drain' if kind == 'drain_link' else 'restore'} {dev}:{port}"
    elif kind in ("quarantine_host", "release_host"):
        where = host_location(service.net.spec, str(intent.get("ip", "")))
        if where is None:
            raise IntentError(f"no server with address {intent.get('ip')!r} in the inventory")
        ops = [{"device": where["device"], "path": IF_ENABLED.format(where["port"]),
                "value": kind == "release_host"}]
        text = f"{'quarantine' if kind == 'quarantine_host' else 'release'} {where['host']} ({where['ip']}) " \
               f"on {where['device']}:{where['port']}"
    elif kind == "guard_zone":
        policy = service.segmentation
        zone = policy.zones.get(str(intent.get("zone", "")))
        if zone is None:
            raise IntentError(f"no zone {intent.get('zone')!r}; zones: {', '.join(policy.zones)}")
        allow = list(intent.get("allow", []))
        name = f"GUARD-{zone.name.upper()}"
        entries: list[dict[str, Any]] = [{"action": "permit", "proto": "tcp", "src": FABRIC, "dst": FABRIC,
                                          "dport": "179"}]
        for prefix in zone.prefixes:
            for svc in allow:
                entries.append({"action": "permit", "proto": svc.get("proto", "tcp"), "dst": prefix,
                                "dport": str(svc.get("dport", "any"))})
            entries.append({"action": "deny", "dst": prefix})
        entries.append({"action": "permit"})
        at = intent.get("at", "destination")
        if at == "destination":
            # The zone's own switches, on every fabric port: traffic is cut as it arrives.
            devices = sorted({a.split(":", 1)[0] for a in zone.attachments})
            ports = [(d, i) for d in devices if d in twin.devices for i in sorted(twin.devices[d].lldp)]
        elif at == "source":
            # Every other zone's entry ports: traffic is cut where it enters the network.
            ports = sorted({(a.split(":", 1)[0], a.split(":", 1)[1]) for z in policy.zones.values()
                            if z.name != zone.name for a in z.attachments})
        else:
            raise IntentError("guard_zone `at` is destination or source")
        ops = []
        for dev in sorted({d for d, _ in ports}):
            ops += [{"device": dev, "path": ACL.format(n=name, q=10 * (k + 1)), "value": e}
                    for k, e in enumerate(entries)]
        ops += [{"device": d, "path": BIND.format(i=i, n=name), "value": name} for d, i in ports]
        text = f"guard zone {zone.name} at its {intent.get('at', 'destination')} ports, allowing " \
               + (", ".join(f"{s.get('proto', 'tcp')}/{s.get('dport', 'any')}" for s in allow) or "nothing")
    else:
        dev, neighbor = str(intent.get("device", "")), str(intent.get("neighbor", ""))
        st = twin.devices.get(dev)
        if st is None or neighbor not in st.bgp:
            raise IntentError(f"no BGP neighbor {neighbor!r} on {dev!r}")
        enabled = bool(intent.get("enabled", True))
        ops = [{"device": dev, "path": BGP_NEIGHBOR.format(neighbor), "value": enabled}]
        text = f"{'enable' if enabled else 'disable'} BGP neighbor {neighbor} on {dev}"
    return {"intent": str(intent.get("reason") or text), "confidence": confidence, "ops": ops,
            "compiled_from": intent, "summary": text}
