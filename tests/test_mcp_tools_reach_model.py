"""MCP tools reach the model — under names providers accept, once, with the operator's risk.

Measured on `main` with a real `CompositionRoot` and a real stdio server:
  * `MCPExtension.tools()` tested each server for a `list_tools()` method `MCPServer` does
    not have, so the model was shown **no** MCP tool;
  * `initialize()` connected a second copy of every server `MCPExtension.start()` had
    already auto-loaded (two processes, every tool listed twice);
  * the canonical name `mcp:server/tool` is not a valid function name for providers that
    require `[a-zA-Z0-9_-]` — so tools are advertised as `mcp__server__tool`;
  * every MCP tool was EXEC risk, so a `read_only` session could not use even a read, and
    an organization `deny` on `mcp:srv/tool` did not match a call named `mcp__srv__tool`.

A private HOME holds the operator's `~/.config/wisp/mcp.json`; the server is a small stdio
script speaking the same newline-delimited JSON-RPC as any MCP server.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys

import pytest

FAKE_SERVER = r'''
import json, sys
TOOLS = [
    {"name": "echo", "description": "Echo the arguments back.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "wipe", "description": "Pretend to wipe something.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "bad.name", "description": "A name strict providers reject.",
     "inputSchema": {"type": "object", "properties": {}}},
]
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method = msg["method"]
    if method == "initialize":
        result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        args = msg["params"].get("arguments", {})
        result = {"content": [{"type": "text", "text": "echo:" + json.dumps(args, sort_keys=True)}], "isError": False}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "no"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
'''

PROVIDER_FUNCTION_NAME = re.compile(r"[a-zA-Z0-9_-]{1,64}")


@pytest.fixture
def session(tmp_path, monkeypatch):
    from wisp.composition import CompositionRoot
    from wisp.config import WispConfig

    script = tmp_path / "fake_mcp.py"
    script.write_text(FAKE_SERVER)
    home = tmp_path / "home"
    (home / ".config" / "wisp").mkdir(parents=True)
    (home / ".config" / "wisp" / "mcp.json").write_text(json.dumps({"mcpServers": [{
        "name": "fake", "command": sys.executable, "args": [str(script)], "always_load": True,
        "tool_risk": {"echo": "read", "wipe": "superuser", "ghost": "read"}}]}))
    monkeypatch.setenv("HOME", str(home))
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.chdir(ws)
    root = CompositionRoot(config=WispConfig().replace(workspace=str(ws)))
    yield root
    root.shutdown()
    try:
        from wisp.core.contracts import forget_declared_risk
    except ImportError:  # the registry predates this fix; nothing to clean
        return
    forget_declared_risk("fake")


def _engine_tool_names(root) -> list[str]:
    """The tool list the turn loop sends the provider (`WispAgentCore._get_tool_schemas`)."""
    from wisp.core.stateless import WispAgentCore

    core = WispAgentCore(config=root.config, provider=None, security=root.security,
                         extensions=root.extensions, tool_executor=root.tool_executor)
    return [s["function"]["name"] for s in core._get_tool_schemas()]


def test_the_model_is_shown_the_servers_tools(session):
    names = _engine_tool_names(session)
    assert "mcp__fake__echo" in names and "mcp__fake__wipe" in names
    assert not any("bad.name" in n for n in names), "a name providers reject is withheld, not sent"


def test_every_advertised_name_is_a_valid_provider_function_name(session):
    bad = [n for n in _engine_tool_names(session) if not PROVIDER_FUNCTION_NAME.fullmatch(n)]
    assert bad == []


def test_a_server_is_connected_once(session):
    manager = session._mcp_manager
    manager.get_all_tools()
    assert [s.config.name for s in manager.servers] == ["fake"]
    assert _engine_tool_names(session).count("mcp__fake__echo") == 1


def test_the_model_can_call_what_it_is_shown(session, tmp_path):
    async def run():
        return [ev async for ev in session.tool_executor.execute(
            "mcp__fake__echo", {"text": "hi"}, str(tmp_path / "ws"))]

    events = asyncio.run(run())
    results = [ev.data for ev in events if ev.type == "tool_result"]
    assert results and results[-1]["result"] == 'echo:{"text": "hi"}'
    assert not [ev for ev in events if ev.type == "approval_request"], "echo is declared read"


def test_operator_declared_risk_applies_to_both_names(session):
    from wisp.core.contracts import ToolRisk, risk_for_tool

    assert risk_for_tool("mcp__fake__echo") is ToolRisk.READ
    assert risk_for_tool("mcp:fake/echo") is ToolRisk.READ
    assert risk_for_tool("mcp:fake/wipe") is ToolRisk.EXEC, "an unknown risk value stays fail-closed"
    assert risk_for_tool("mcp:fake/ghost") is ToolRisk.EXEC, "only tools the server lists are declared"


def test_read_only_session_can_use_declared_reads_only(session):
    from wisp.auth.decision import authorize
    from wisp.auth.principal import local_principal
    from wisp.auth.workspace_trust import WorkspaceTrust

    principal = local_principal(workspace="/w", profile="personal")
    read = authorize(principal, "mcp__fake__echo", {}, WorkspaceTrust.TRUSTED, permission_mode="read_only")
    wipe = authorize(principal, "mcp__fake__wipe", {}, WorkspaceTrust.TRUSTED, permission_mode="read_only")
    assert read.allowed and not read.approval_required
    assert not wipe.allowed


def test_disconnect_forgets_the_declaration(session):
    from wisp.core.contracts import ToolRisk, risk_for_tool

    session.shutdown()
    assert risk_for_tool("mcp:fake/echo") is ToolRisk.EXEC


def test_a_declaration_cannot_reclassify_a_builtin():
    from wisp.core.contracts import ToolRisk, declare_tool_risk, risk_for_tool

    with pytest.raises(ValueError):
        declare_tool_risk("run_bash", ToolRisk.READ)
    assert risk_for_tool("run_bash") is not ToolRisk.READ


def test_organization_deny_on_the_canonical_name_covers_the_wire_name():
    from wisp.auth.decision import authorize
    from wisp.auth.principal import local_principal
    from wisp.auth.workspace_trust import WorkspaceTrust
    from wisp.policy.loader import EffectivePolicy

    policy = EffectivePolicy(mcp_allowlist=(), approval_matrix={"mcp:fake/echo": "deny"}, provenance={})
    decision = authorize(local_principal(workspace="/w", profile="personal"), "mcp__fake__echo", {},
                         WorkspaceTrust.TRUSTED, permission_mode="full", effective_policy=policy)
    assert not decision.allowed and decision.controlling_layer == "organization"


def _call(root, tool: str, handler):
    async def run():
        return [ev async for ev in root.tool_executor.execute(
            tool, {}, str(root.config.workspace), approval_handler=handler)]
    return asyncio.run(run())


async def _deny(*_args, **_kwargs):
    return False, None


def test_an_undeclared_mcp_tool_asks_before_it_runs(session):
    """On `main` an EXEC-risk MCP tool ran in `auto_edit` even when the approver said no:
    approval was keyed only on the built-in `write_tools` list."""
    events = _call(session, "mcp__fake__wipe", _deny)
    assert [ev.type for ev in events][0] == "approval_request"
    final = events[-1].data
    assert "echo:" not in str(final.get("result")), "the tool ran although approval was refused"


def test_a_declared_read_runs_without_asking(session):
    events = _call(session, "mcp__fake__echo", _deny)
    assert [ev.type for ev in events] == ["tool_result"]
    assert events[-1].data["result"] == "echo:{}"
