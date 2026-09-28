"""MCP server (stdio, JSON-RPC 2.0) exposing the network platform to wisp.

Framing matches wisp's client (`wisp/mcp/manager.py`): one JSON object per line, one
response line per request, nothing else on stdout. Nothing is written to stderr either:
the client pipes it without draining, so a chatty server would eventually block.

Read tools carry `readOnlyHint`. Lab-control tools (fault injection, clock) change the
simulated world and exist only with `--lab-control`; they are never read-only.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from typing import Any, Callable, TextIO

from wisp_net.service import NetService

PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "wisp-net"
SERVER_VERSION = "0.1.0"

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable[[NetService, dict[str, Any]], Any]
    read_only: bool = True


def _obj(props: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


_S = {"type": "string"}
_N = {"type": "number"}
_I = {"type": "integer"}

READ_TOOLS: tuple[Tool, ...] = (
    Tool("net_status", "Lab clock, device/link counts, links down, open alerts by severity, collector health. "
         "Start here.", _obj({}), lambda s, a: s.status()),
    Tool("net_devices", "Inventory: every device with software, ASN, router-id, CPU, BGP sessions up/total, "
         "advertised networks, and whether it is still reporting telemetry.", _obj({}), lambda s, a: s.devices()),
    Tool("net_topology", "Every link (from LLDP, remembered while down) with up/down, speed and utilization "
         "in each direction.", _obj({}), lambda s, a: s.topology()),
    Tool("net_interfaces", "One device's interfaces: admin/oper state, peer, rates, utilization, FCS errors/s, "
         "discards/s, optic receive power (dBm).",
         _obj({"device": _S}, ("device",)), lambda s, a: s.interfaces(a["device"])),
    Tool("net_bgp", "BGP neighbors (one device, or all when device is omitted): state, peer AS, prefixes "
         "installed, established-transitions (rising = flapping).",
         _obj({"device": _S}), lambda s, a: s.bgp(a.get("device"))),
    Tool("net_routes", "A device's forwarding table: prefix, origin, AS path, ECMP next hops.",
         _obj({"device": _S, "prefix": _S}, ("device",)), lambda s, a: s.routes(a["device"], a.get("prefix"))),
    Tool("net_trace", "Follow the actual forwarding path from a device to an IP or prefix, hop by hop through "
         "each FIB, fanning out over ECMP. Outcome: delivered, partial, blackhole or loop, with the reasons.",
         _obj({"source": _S, "destination": _S}, ("source", "destination")),
         lambda s, a: s.trace(a["source"], a["destination"])),
    Tool("net_paths", "All shortest physical paths between two devices over links that are up.",
         _obj({"source": _S, "target": _S}, ("source", "target")),
         lambda s, a: s.physical_paths(a["source"], a["target"])),
    Tool("net_reachability", "Trace every device that originates prefixes to every other advertised prefix; "
         "lists every pair that is not delivered and why.", _obj({}), lambda s, a: s.reachability()),
    Tool("net_alerts", "Alerts (state: open|resolved|all; min_severity: info|minor|major|critical) with "
         "evidence. Alerts dedupe by rule+device+subject and resolve when the condition clears.",
         _obj({"state": _S, "min_severity": _S, "limit": _I}),
         lambda s, a: s.alert_list(a.get("state", "open"), a.get("min_severity", "info"), a.get("limit", 50))),
    Tool("net_events", "Normalized syslog events (link up/down, BGP adjacency changes, FCS error bursts, "
         "rx-power alarms), filterable by device and kind, newest last.",
         _obj({"device": _S, "kind": _S, "since_s": _N, "limit": _I}),
         lambda s, a: s.events(a.get("device"), a.get("kind"), a.get("since_s", 3600.0), a.get("limit", 50))),
    Tool("net_metrics", "Time series matching a glob `<device>|<metric>|<subject>`, e.g. "
         "`leaf2|if.rx_power_dbm|*` or `*|if.util|Ethernet49`. Metrics: if.rx_bps, if.tx_bps, if.util, "
         "if.fcs_per_s, if.out_discards_per_s, if.rx_power_dbm, if.oper_up, sys.cpu_pct, sys.mem_pct, "
         "bgp.established. Returns min/max/avg/last and points as [seconds_before_now, value]; step_s >= 60 "
         "reads the 1-minute tier, >= 3600 the 1-hour tier.",
         _obj({"pattern": _S, "window_s": _N, "step_s": _N}, ("pattern",)),
         lambda s, a: s.metrics(a["pattern"], a.get("window_s", 900.0), a.get("step_s", 0.0))),
    Tool("net_series", "List stored metric series names matching a glob.",
         _obj({"pattern": _S}), lambda s, a: s.series(a.get("pattern", "*"))),
    Tool("net_top_flows", "Top talkers from flow records (IPFIX-style) over a window, grouped by "
         "src/dst/port pair or by destination port.",
         _obj({"window_s": _N, "n": _I, "group_by": {"type": "string", "enum": ["pair", "dport"]}}),
         lambda s, a: s.top_flows(a.get("window_s", 300.0), a.get("n", 10), a.get("group_by", "pair"))),
    Tool("net_knowledge", "Search the operations knowledge base (runbooks: BGP down, FCS/CRC errors, optics, "
         "congestion, flaps, unreachable devices, ACL safety, drift, change management). Cite what you use.",
         _obj({"query": _S, "k": _I}, ("query",)), lambda s, a: s.knowledge(a["query"], a.get("k", 4))),
    Tool("net_gnmi_get", "Raw gNMI Get of OpenConfig paths on one device, e.g. "
         "`/interfaces/interface[name=Ethernet49]/state/counters`. Fails if the device does not answer.",
         _obj({"device": _S, "path": _S, "limit": _I}, ("device", "path")),
         lambda s, a: s.gnmi_get(a["device"], a["path"], a.get("limit", 100))),
    Tool("net_what_if", "Verify a proposed change WITHOUT touching the network: it is committed on a copy of the "
         "lab, both copies run `settle_s` seconds, and the outcome is checked — forwarding loops, pairs that lose "
         "reachability, BGP sessions lost as collateral damage, new dead ACL rules, new leaks between security "
         "zones, new congestion, new major alerts, convergence — then judged by the change policy (blast radius, "
         "change windows, confidence threshold). `change` = {intent, confidence 0..1, expected_unreachable: "
         "[[source_device, prefix]...], ops: [{device, op: update|delete, path, value}]}. Settable paths: "
         "`/interfaces/interface[name=X]/config/{enabled,description,mtu}`; `/network-instances/network-instance"
         "[name=default]/protocols/protocol[identifier=BGP][name=BGP]/bgp/neighbors/neighbor[neighbor-address=IP]"
         "/config/{enabled,peer-as}`; `/network-instances/network-instance[name=default]/policy/export-deny"
         "[prefix=P]/config/prefix` (value P); `/acl/acl-sets/acl-set[name=N][type=ACL_IPV4]/acl-entries/acl-entry"
         "[sequence-id=S]/config` (value {action: permit|deny, src, dst, proto: tcp|udp|icmp|any, dport: 443|"
         "1000-2000|any}); `/acl/interfaces/interface[id=X]/ingress-acl-sets/ingress-acl-set[set-name=N]"
         "[type=ACL_IPV4]/config/set-name` (value N). ACLs are first-match with an implicit deny.",
         _obj({"change": {"type": "object"}, "settle_s": _N}, ("change",)),
         lambda s, a: s.what_if(a["change"], a.get("settle_s", 30.0))),
    Tool("net_acl_audit", "Every ACL on every device (or one device) and its dead rules: rules no packet can reach "
         "because earlier rules cover them (shadowed: an earlier rule with the opposite action wins; redundant: same "
         "action). Exact header-space analysis.", _obj({"device": _S}), lambda s, a: s.acl_audit(a.get("device"))),
    Tool("net_segmentation_audit", "Check the network against the segmentation policy (security zones and which "
         "services may cross between them). Each violation names the entry port, the forwarding path and the "
         "exact flow classes that leak.", _obj({}), lambda s, a: s.segmentation_audit()),
    Tool("net_change_policy", "The change guardrails: blast-radius limit, prohibited change windows (and whether "
         "one is active now), and the confidence below which a human must approve.", _obj({}),
         lambda s, a: s.change_policy_view()),
    Tool("net_changes", "What changed in the digital twin over the last since_s seconds: links, BGP sessions, "
         "routes, device reachability (added/removed/changed).",
         _obj({"since_s": _N}), lambda s, a: s.changes(a.get("since_s", 3600.0))),
)

LAB_TOOLS: tuple[Tool, ...] = (
    Tool("lab_inject_fault", "LAB ONLY: inject a fault into the simulated network. kinds: link_down, "
         "link_flap, optic_degrade, crc_surge, bgp_down, cpu_spike, congestion, mgmt_unreachable. target: "
         "`dev:iface` (links/optics/crc), `dev:neighbor_ip` (bgp_down), `dev` (cpu/mgmt), `src->dst` "
         "(congestion). params e.g. {\"rate_db_per_min\": 2}.",
         _obj({"kind": _S, "target": _S, "params": {"type": "object"}}, ("kind", "target")),
         lambda s, a: s.inject(a["kind"], a["target"], **(a.get("params") or {})), read_only=False),
    Tool("lab_clear_fault", "LAB ONLY: clear an injected fault by id.",
         _obj({"fault_id": _S}, ("fault_id",)), lambda s, a: s.clear_fault(a["fault_id"]), read_only=False),
    Tool("lab_faults", "LAB ONLY: list injected faults.", _obj({}), lambda s, a: s.faults(), read_only=False),
    Tool("lab_advance", "LAB ONLY: advance the lab clock by seconds (max 3600), running telemetry cycles.",
         _obj({"seconds": _N}, ("seconds",)),
         lambda s, a: s.advance(min(3600.0, float(a["seconds"]))), read_only=False),
)


class McpServer:
    def __init__(self, service: NetService, lab_control: bool = False) -> None:
        self.service = service
        tools = READ_TOOLS + (LAB_TOOLS if lab_control else ())
        self.tools = {t.name: t for t in tools}

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        msg_id = message.get("id")
        if msg_id is None:
            return None
        try:
            if method == "initialize":
                result: Any = {
                    "protocolVersion": (message.get("params") or {}).get("protocolVersion", PROTOCOL_VERSION),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [self._describe(t) for t in self.tools.values()]}
            elif method == "tools/call":
                result = self._call(message.get("params") or {})
            else:
                return _error(msg_id, -32601, f"method not found: {method}")
        except Exception as exc:  # a bad request must never take the server down
            logger.exception("request failed")
            return _error(msg_id, -32603, f"{type(exc).__name__}: {exc}")
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _describe(tool: Tool) -> dict[str, Any]:
        return {"name": tool.name, "description": tool.description, "inputSchema": tool.schema,
                "annotations": {"readOnlyHint": tool.read_only, "destructiveHint": False,
                                "idempotentHint": tool.read_only, "openWorldHint": False}}

    def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        tool = self.tools.get(str(name))
        if tool is None:
            return _tool_error(f"unknown tool {name!r}")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _tool_error("arguments must be an object")
        missing = [r for r in tool.schema.get("required", []) if r not in args]
        if missing:
            return _tool_error(f"missing required argument(s): {', '.join(missing)}")
        unknown = [k for k in args if k not in tool.schema["properties"]]
        if unknown:
            return _tool_error(f"unknown argument(s): {', '.join(unknown)}")
        try:
            data = tool.handler(self.service, args)
        except (KeyError, ValueError, TypeError) as exc:
            return _tool_error(str(exc).strip("'\""))
        except Exception as exc:
            return _tool_error(f"{type(exc).__name__}: {exc}")
        return {"content": [{"type": "text", "text": json.dumps(data, separators=(",", ":"), default=str)}],
                "isError": False}

    def serve(self, stdin: TextIO, stdout: TextIO) -> None:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                reply: dict[str, Any] | None = _error(None, -32700, f"parse error: {exc}")
            else:
                reply = self.handle(message) if isinstance(message, dict) else _error(None, -32600, "invalid request")
            if reply is not None:
                stdout.write(json.dumps(reply, separators=(",", ":"), default=str) + "\n")
                stdout.flush()


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def run_stdio(service: NetService, lab_control: bool = False) -> None:
    McpServer(service, lab_control).serve(sys.stdin, sys.stdout)
