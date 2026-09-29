"""A `read_only` session can use an MCP tool the operator declared `read`, and nothing else from MCP.

`tool_risk` in `mcp.json` is the operator's statement that a tool only reads (PR #47). It was honoured
by `authorize()` but not by the policy engine's `mode.read_only` rule, which allowed a fixed set of
built-in names and denied every other tool. On the production executor path — where both run — a declared
read was blocked in `read_only` ("READ_ONLY mode blocks mcp__net__net_alerts"). The network watcher's
diagnose and propose tiers run wisp in exactly that mode, so they could not read the network they were
started to look at. #47's test called `authorize()` directly and never saw the second gate.

Held here on `ToolExecutor.execute`, the path every transport uses, and on the schema filter that decides
what a read-only session is even shown. Only the tool body is replaced; every gate in front of it is real.
"""
from __future__ import annotations

import pytest

from wisp.capability_filter import filter_schemas_for_mode
from wisp.config import WispConfig
from wisp.core.contracts import ToolRisk, declare_tool_risk, forget_declared_risk
from wisp.tool_executor import ToolExecutor


@pytest.fixture()
def declared():
    declare_tool_risk("mcp__fake__look", ToolRisk.READ)
    declare_tool_risk("mcp__fake__poke", ToolRisk.WRITE)
    declare_tool_risk("mcp__fake__wipe", ToolRisk.EXEC)
    yield
    forget_declared_risk("fake")


def _executor(tmp_path, mode="read_only"):
    cfg = WispConfig().replace(workspace=str(tmp_path), permission_mode=mode, auto_approve=False)
    ex = ToolExecutor(config=cfg, hook_manager=None, mcp=None, file_lock=None,
                      lsp_manager=None, subagent_orchestrator=None, extensions=None)
    ran: list[str] = []

    async def body(name, args, ws):
        ran.append(name)
        return '{"status": "ok", "data": "done"}', 0.0

    ex._execute_tool = body
    return ex, ran


def _flat(ev) -> dict:
    return ev if isinstance(ev, dict) else {"type": str(getattr(ev, "type", "")),
                                            **dict(getattr(ev, "data", {}))}


async def _run(ex, tool, tmp_path, handler=None) -> list[dict]:
    return [_flat(ev) async for ev in ex.execute(tool, {}, str(tmp_path), tool_call_id="c1",
                                                 approval_handler=handler)]


@pytest.mark.asyncio
async def test_a_declared_read_runs_in_read_only_without_asking(tmp_path, declared):
    ex, ran = _executor(tmp_path)
    asked: list[str] = []

    async def handler(name, args, reason):
        asked.append(name)
        return True, None

    await _run(ex, "mcp__fake__look", tmp_path, handler)
    assert ran == ["mcp__fake__look"] and asked == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["mcp__fake__poke", "mcp__fake__wipe", "mcp__fake__never_declared"])
async def test_anything_not_declared_read_stays_blocked(tmp_path, declared, tool):
    ex, ran = _executor(tmp_path)

    async def yes(name, args, reason):
        return True, None

    events = await _run(ex, tool, tmp_path, yes)
    assert ran == [], f"{tool} must not run in read_only"
    results = [e for e in events if e.get("type") == "tool_result"]
    assert results and results[0]["result"]["status"] == "POLICY_DENIED" and results[0]["result"]["executed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["write_file", "run_bash", "git_push", "gh_pr_close"])
async def test_built_in_writes_stay_blocked(tmp_path, declared, tool):
    ex, ran = _executor(tmp_path)
    await _run(ex, tool, tmp_path, None)
    assert ran == []


@pytest.mark.asyncio
async def test_a_declared_read_is_not_special_outside_read_only(tmp_path, declared):
    """Other modes are unchanged: an undeclared MCP tool still needs an operator's yes in auto_edit."""
    ex, ran = _executor(tmp_path, "auto_edit")
    await _run(ex, "mcp__fake__wipe", tmp_path, None)
    assert ran == []


def _schema(name):
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object", "properties": {}}}}


def test_a_read_only_session_is_shown_declared_reads_and_no_other_mcp_tool(declared):
    shown = {s["function"]["name"] for s in filter_schemas_for_mode(
        [_schema(n) for n in ("read_file", "write_file", "mcp__fake__look", "mcp__fake__poke",
                              "mcp__fake__wipe", "mcp__fake__never_declared")], "read_only")}
    assert shown == {"read_file", "mcp__fake__look"}


def test_the_schema_filter_is_unchanged_outside_read_only(declared):
    schemas = [_schema(n) for n in ("write_file", "mcp__fake__wipe")]
    assert filter_schemas_for_mode(schemas, "auto_edit") == schemas


def test_declaring_a_built_in_name_as_read_does_not_widen_read_only():
    """Only MCP tools take a declared risk (the contract refuses the rest), so a built-in cannot be promoted."""
    with pytest.raises(ValueError):
        declare_tool_risk("write_file", ToolRisk.READ)


# ── the other places that enforce or advertise read_only by name ─────────────────────────────

def test_the_mode_hard_deny_lets_a_declared_read_through(declared):
    from wisp.infra.security import policy_hard_deny

    assert policy_hard_deny("mcp__fake__look", "read_only") is None
    for tool in ("mcp__fake__poke", "mcp__fake__wipe", "mcp__fake__never_declared", "write_file"):
        assert policy_hard_deny(tool, "read_only") is not None, tool


def test_a_read_only_subagent_is_given_the_declared_reads(declared):
    """The orchestrator delegates to subagents; a child that cannot see the network tools is useless."""
    from wisp.infra.policy_engine import filter_allowed_for_mode

    offered = filter_allowed_for_mode("read_only", ["read_file", "write_file", "mcp__fake__look",
                                                    "mcp__fake__wipe", "mcp__fake__never_declared"])
    assert offered == ["read_file", "mcp__fake__look"]


def test_the_prompt_menu_of_a_read_only_session_lists_the_declared_reads(declared):
    from wisp.capability_filter import visible_tool_names

    menu = visible_tool_names(None, "read_only", True)
    assert "mcp__fake__look" in menu and "read_file" in menu
    assert not menu & {"mcp__fake__poke", "mcp__fake__wipe", "write_file"}
    assert visible_tool_names({"read_file", "mcp__fake__look", "mcp__fake__wipe"}, "read_only", True) == {
        "read_file", "mcp__fake__look"}


def test_the_declared_read_registry_is_cleared_with_its_server(declared):
    from wisp.core.contracts import is_declared_read

    assert is_declared_read("mcp__fake__look") and is_declared_read("mcp:fake/look")
    forget_declared_risk("fake")
    assert not is_declared_read("mcp__fake__look")


def test_the_policy_engine_rule_allows_a_declared_read_and_denies_the_rest(declared, tmp_path):
    """`SecurityPolicy.check` is where the engine's `mode.read_only` rule runs (REST and the gate chain)."""
    from wisp.infra.security import Action, Context, PermissionMode, SecurityPolicy

    policy = SecurityPolicy(permission_mode=PermissionMode.READ_ONLY).with_trusted_workspaces({tmp_path})
    ctx = Context(workspace=tmp_path)

    assert policy.check(Action("mcp__fake__look", {}), ctx).allowed
    for tool in ("mcp__fake__poke", "mcp__fake__wipe", "mcp__fake__never_declared", "write_file"):
        decision = policy.check(Action(tool, {}), ctx)
        assert not decision.allowed and "READ_ONLY" in decision.reason, tool
