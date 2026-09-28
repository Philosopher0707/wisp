"""The wisp-net MCP server, driven by wisp's own MCP client over a real stdio subprocess.

This is the production observation point: `wisp.mcp.manager.connect_server` spawns
`python -m wisp_net mcp` exactly as a `.wisp/mcp.json` entry would, speaks its
newline-delimited JSON-RPC, and reads one response line per request.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from wisp.mcp.manager import MCPServerConfig, call_tool, connect_server, disconnect_server
from wisp_net.mcp_server import LAB_TOOLS, READ_TOOLS, McpServer
from wisp_net.service import NetService

REPO = str(Path(__file__).resolve().parents[2])


@pytest.fixture(scope="module")
def server():
    config = MCPServerConfig(name="net", command=sys.executable, args=["-m", "wisp_net", "mcp", "--lab-control",
                                                                       "--speed", "20"],
                             env={"PYTHONPATH": REPO})
    srv = connect_server(config)
    yield srv
    process = srv.process
    disconnect_server(srv)
    if process is not None:
        assert process.stderr.read() == "", "the server must never write to stderr (the client does not drain it)"


def _json(text: str):
    return json.loads(text)


def test_wisp_client_lists_every_tool_with_hints(server):
    names = [t.name for t in server.tools]
    assert names == [t.name for t in READ_TOOLS + LAB_TOOLS]
    assert all(t.input_schema["type"] == "object" for t in server.tools)


def test_read_tools_answer_through_the_real_client(server):
    status = _json(call_tool(server, "net_status", {}))
    assert status["devices"] == 8 and status["reporting"] == 8
    trace = _json(call_tool(server, "net_trace", {"source": "leaf1", "destination": "10.3.0.20"}))
    assert trace["outcome"] == "delivered" and len(trace["paths"]) == 2
    iface = _json(call_tool(server, "net_interfaces", {"device": "leaf2"}))
    assert {r["name"] for r in iface} >= {"Ethernet1", "Ethernet49", "Ethernet50"}
    kb = _json(call_tool(server, "net_knowledge", {"query": "FCS errors", "k": 2}))
    assert kb[0]["doc"] == "fcs-crc-errors"


def test_diagnosis_loop_over_mcp(server):
    fault = _json(call_tool(server, "lab_inject_fault", {"kind": "bgp_down", "target": "leaf3:10.255.4.1"}))
    _json(call_tool(server, "lab_advance", {"seconds": 15}))
    alerts = _json(call_tool(server, "net_alerts", {"min_severity": "major"}))
    assert {(a["rule"], a["device"]) for a in alerts} >= {("bgp_session_down", "leaf3"),
                                                          ("bgp_session_down", "spine1")}
    peers = {p["neighbor"]: p for p in _json(call_tool(server, "net_bgp", {"device": "leaf3"}))}
    assert peers["10.255.4.1"]["state"] == "IDLE" and peers["10.255.5.1"]["state"] == "ESTABLISHED"
    reach = _json(call_tool(server, "net_reachability", {}))
    assert reach["delivered"] == reach["pairs_checked"], "one spine lost is redundancy spent, not an outage"
    _json(call_tool(server, "lab_clear_fault", {"fault_id": fault["fault_id"]}))


def test_errors_come_back_as_tool_errors_not_crashes(server):
    assert call_tool(server, "net_interfaces", {"device": "leaf99"}).startswith("[MCP Error: unknown device")
    assert "missing required argument" in call_tool(server, "net_trace", {"source": "leaf1"})
    assert "unknown argument" in call_tool(server, "net_status", {"verbose": True})
    assert "unknown tool" in call_tool(server, "net_nope", {})
    assert _json(call_tool(server, "net_status", {}))["devices"] == 8, "still serving"


class TestProtocolInProcess:
    @pytest.fixture(scope="class")
    def mcp(self):
        service = NetService(warmup_s=10)
        yield McpServer(service)
        service.stop()

    def test_lab_tools_are_opt_in(self, mcp):
        listed = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        assert not any(t["name"].startswith("lab_") for t in listed)
        assert all(t["annotations"]["readOnlyHint"] is True for t in listed)

    def test_lab_tools_are_never_read_only(self):
        assert all(t.read_only is False for t in LAB_TOOLS)

    def test_notifications_get_no_reply_and_unknown_methods_error(self, mcp):
        assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
        err = mcp.handle({"jsonrpc": "2.0", "id": 7, "method": "resources/list"})
        assert err["error"]["code"] == -32601 and err["id"] == 7

    def test_stream_framing(self, mcp):
        stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n\nnot json\n'
                            '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
                            '{"jsonrpc":"2.0","id":2,"method":"initialize","params":{"protocolVersion":"2025-03-26"}}\n')
        stdout = io.StringIO()
        mcp.serve(stdin, stdout)
        replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
        assert [r.get("id") for r in replies] == [1, None, 2]
        assert replies[1]["error"]["code"] == -32700
        assert replies[2]["result"]["serverInfo"]["name"] == "wisp-net"
