"""What a subagent role can actually use in the default mode.

In `auto_edit` a child has no approver, so `_effective_child_tools` withholds every tool that needs a human's yes
(`run_bash`, `spawn`, `fanout`, the git/gh writes). That is deliberate: advertising them buys a turn that ends in a
guaranteed block. But the researcher role still listed `run_bash` "for diagnostics only (grep, find)" and its prompt
promised it could "run diagnostics", while the `grep` and `glob` tools that do the same job without a shell (added in
#69) were in no role's list. A researcher in `auto_edit` therefore could not search file contents at all, and told its
parent "this session has no run_bash". It must be given the read-only equivalents, not a shell: a role that reads
untrusted web pages must not hold one.
"""
from __future__ import annotations

import pytest

from wisp.core.contracts import ToolRisk, risk_for_tool
from wisp.multi_agent._runner import _effective_child_tools
from wisp.multi_agent.roles import ROLE_CONFIGS, AgentRole
from wisp.tools.registry import TOOL_SCHEMAS

SEARCHING_ROLES = [AgentRole.CODER, AgentRole.REVIEWER, AgentRole.TESTER, AgentRole.RESEARCHER,
                   AgentRole.PLANNER, AgentRole.DEBUGGER]
BUILTIN = {s.get("function", {}).get("name", "") for s in TOOL_SCHEMAS}


def _offered(role, mode: str = "auto_edit") -> list[str]:
    return _effective_child_tools(list(ROLE_CONFIGS[role].allowed_tools), mode)


@pytest.mark.parametrize("role", SEARCHING_ROLES)
def test_every_searching_role_can_grep_and_glob_in_the_default_mode(role):
    offered = _offered(role)
    assert "grep" in offered and "glob" in offered


def test_grep_and_glob_need_no_approval_so_a_child_can_actually_run_them():
    assert risk_for_tool("grep") is ToolRisk.READ and risk_for_tool("glob") is ToolRisk.READ


def test_the_researcher_still_gets_no_shell_in_the_default_mode():
    """By design: a role that ingests untrusted web content must not hold a shell it cannot even be approved for."""
    offered = _offered(AgentRole.RESEARCHER)
    assert "run_bash" not in offered
    assert {"web_fetch", "web_search", "read_file", "list_files", "grep", "glob"} <= set(offered)


def test_the_researcher_can_never_modify_anything_through_its_tool_list():
    tools = set(ROLE_CONFIGS[AgentRole.RESEARCHER].allowed_tools)
    assert not tools & {"write_file", "edit_file", "edit_file_multi", "git_commit", "git_push", "rewind"}


def test_the_researcher_prompt_does_not_promise_a_shell():
    prompt = ROLE_CONFIGS[AgentRole.RESEARCHER].system_prompt.lower()
    assert "run diagnostics" not in prompt, "it cannot, in the default mode"
    assert "grep" in prompt and "glob" in prompt
    assert "web_fetch" in prompt, "HTTP is fetched with web_fetch, not curl through a shell"


@pytest.mark.parametrize("role", list(ROLE_CONFIGS))
def test_every_tool_a_role_names_exists(role):
    names = {t for t in ROLE_CONFIGS[role].allowed_tools if t.lower() != "all"}
    assert names <= BUILTIN, f"{role} lists tools that do not exist: {sorted(names - BUILTIN)}"


def test_in_full_mode_a_researcher_still_gets_run_bash():
    """Unchanged: the user who sets full mode has authorised the shell; the role list still carries it."""
    assert "run_bash" in _offered(AgentRole.RESEARCHER, "full")
