"""Which extension (MCP, skill) tools a subagent child inherits.

A child's tool list is built by `_effective_child_tools`; for an unrestricted role (generalist: "all") the runner expanded "all"
into the built-in registry only, so a generalist child was NEVER offered an extension tool: not your fleet workers' reads, nothing.
That list is also the child's authorization identity (a tool outside it is refused at the authority layer), so it is the one place
to fix.

The rule is narrow, and it is one function shared with `wisp tools` (`core.tool_surface.inherited_extension_tools`):

* a child inherits every MCP tool whose operator declared `tool_risk: read`, in every mode: such a tool executes unprompted, and
  `read_only` mode already permits it, so this adds no authority;
* in `full` mode (the user has already authorised everything: the child gets `run_bash` too) it inherits the other MCP tools as
  well;
* in `auto_edit` and `ask_all` it does NOT inherit an undeclared MCP tool: that tool always needs an approver and a child has none,
  so offering it only buys a turn that ends in a guaranteed block;
* it NEVER inherits `skill__*` (the parent's skill menu: ~10k tokens per call, measured);
* an explicit role list (researcher, coder, ...) is unchanged.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from wisp.config import WispConfig
from wisp.core.contracts import ToolRisk, declare_tool_risk, forget_declared_risk
from wisp.core.tool_surface import inherited_extension_tools
from wisp.multi_agent._runner import SubagentRunner, _effective_child_tools
from wisp.multi_agent.task import SubagentContract
from wisp.tool_executor import ToolExecutor
from wisp.tools.registry import TOOL_SCHEMAS

BUILTIN = [s.get("function", {}).get("name", "") for s in TOOL_SCHEMAS]
READ = "mcp__fleet__status"      # declared `tool_risk: read`
WRITE = "mcp__fleet__restart"    # undeclared: EXEC by default
SKILL = "skill__alpha"
HOST = [READ, WRITE, SKILL]       # what the extension host offers
MODES = ("full", "auto_edit", "ask_all", "read_only")


@pytest.fixture(autouse=True)
def declared_read():
    declare_tool_risk(READ, ToolRisk.READ)
    yield
    forget_declared_risk("fleet")


@pytest.mark.parametrize("mode", MODES)
def test_an_unrestricted_child_inherits_declared_read_mcp_tools_in_every_mode(mode):
    assert READ in _effective_child_tools(["all"], mode, HOST)
    assert READ in _effective_child_tools(None, mode, HOST)


@pytest.mark.parametrize("mode", ("auto_edit", "ask_all", "read_only"))
def test_an_undeclared_mcp_tool_is_not_inherited_where_a_child_could_not_run_it(mode):
    assert WRITE not in _effective_child_tools(["all"], mode, HOST)


def test_in_full_mode_the_other_mcp_tools_are_inherited_too():
    tools = _effective_child_tools(["all"], "full", HOST)
    assert READ in tools and WRITE in tools


@pytest.mark.parametrize("mode", MODES)
def test_the_skill_menu_is_never_inherited(mode):
    assert SKILL not in _effective_child_tools(["all"], mode, HOST)


@pytest.mark.parametrize("mode", MODES)
def test_an_explicit_role_list_is_unchanged(mode):
    tools = _effective_child_tools(["read_file", "grep"], mode, HOST)
    assert READ not in tools and WRITE not in tools and set(tools) <= {"read_file", "grep"}


def test_with_nothing_declared_and_no_host_the_result_is_the_old_builtin_only_list():
    forget_declared_risk("fleet")
    assert set(_effective_child_tools(["all"], "full")) == set(BUILTIN)
    assert set(_effective_child_tools(["all"], "auto_edit")) <= set(BUILTIN)


def test_the_rule_is_one_shared_function():
    assert inherited_extension_tools("auto_edit", HOST) == frozenset({READ})
    assert inherited_extension_tools("full", HOST) == frozenset({READ, WRITE})
    assert inherited_extension_tools("read_only", HOST) == frozenset({READ})


# ── through the real runner: the list the child is actually given, and the identity it authorizes as ──────────────


class _Host:
    def tools(self):
        return [{"type": "function", "function": {"name": n, "description": "", "parameters": {"type": "object"}}} for n in HOST]


def _run(mode: str, role_tools):
    seen: dict = {}

    class Runtime:
        extensions = _Host()

        async def get_or_create_session(self, sid, model, ws):
            return {"id": sid, "model": model, "workspace": ws, "messages": [], "compaction_history": [],
                    "created_at": "2024-01-01T00:00:00", "updated_at": "2024-01-01T00:00:00"}

        async def run_turn(self, session, prompt):
            seen["allowed"] = list(session.get("allowed_tools") or [])
            seen["principal"] = session.get("principal")
            yield {"type": "content", "text": "ok"}
            yield {"type": "done"}

    cfg = WispConfig().replace(model="m", provider="ollama", workspace="/tmp", permission_mode=mode,
                               ollama_url="http://localhost:11434", max_context_tokens=128000)
    runner = SubagentRunner(cfg, Path("/tmp"), agent_runtime=Runtime(), tool_executor=ToolExecutor(cfg))
    contract = SubagentContract(name="t", task="do it", role="generalist", tools=role_tools, timeout_seconds=30,
                                max_iterations=5, auto_approve=True)
    asyncio.run(runner.run(contract, "/tmp", "prompt"))
    return seen


def test_the_runner_gives_a_generalist_child_the_declared_read_tool_in_auto_edit():
    seen = _run("auto_edit", ["all"])
    assert READ in seen["allowed"] and WRITE not in seen["allowed"] and SKILL not in seen["allowed"]


def test_the_runner_reads_the_extension_host_for_full_mode():
    seen = _run("full", ["all"])
    assert READ in seen["allowed"] and WRITE in seen["allowed"] and SKILL not in seen["allowed"]


def test_the_child_is_authorized_to_call_what_it_was_offered():
    """The list is also the authorization identity: a tool outside it is refused at the authority layer, not just hidden."""
    principal = _run("auto_edit", ["all"])["principal"]
    assert principal.allows_tool(READ) and not principal.allows_tool(WRITE)
