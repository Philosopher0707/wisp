"""`core.tool_surface`: what an agent will actually be offered, and why a tool is absent.

The offer is the composition of four filters that live in four places: the role's tool list, the child permission-mode
filter (`filter_allowed_for_mode`), the tool profile, and the capability partition. A researcher "has no run_bash" because of
the second one, and nothing could say so. `explain_surface` composes the REAL filters (it re-implements none of them) and
reports, for every tool that is missing, the first stage that removed it and why.

Held by a differential test: for a grid of parent / subagent / child sessions x modes x profiles, its `offered` set equals the
names the real pipeline (`_effective_child_tools` then `WispAgentCore._provider_tools`) puts in a provider request.
"""
from __future__ import annotations

import itertools

import pytest

from tests.test_tool_profile import BUILTIN, SUB, _core, _names, _session
from wisp.core.contracts import ToolRisk, declare_tool_risk, forget_declared_risk
from wisp.core.tool_surface import Absence, Surface, explain_surface
from wisp.multi_agent._runner import _effective_child_tools
from wisp.multi_agent.roles import ROLE_CONFIGS

EXT = {"skill__alpha", "mcp__srv__lookup"}  # what the fake extension host in test_tool_profile offers
ALL = sorted(BUILTIN | EXT)
MODES = ("full", "auto_edit", "ask_all", "read_only")


def _why(surface: Surface, tool: str) -> Absence:
    return next(a for a in surface.absent if a.tool == tool)


# ── reasons ──────────────────────────────────────────────────────────────────────────────────────────────────


def test_a_researcher_child_in_auto_edit_has_no_shell_and_the_reason_says_why():
    s = explain_surface(ALL, role_tools=ROLE_CONFIGS["researcher"].allowed_tools, child=True, permission_mode="auto_edit",
                        builtin_names=BUILTIN)
    assert "run_bash" not in s.offered
    a = _why(s, "run_bash")
    assert a.stage == "mode" and "approv" in a.reason.lower() and "auto_edit" in a.reason
    assert {"web_fetch", "web_search", "grep", "glob", "read_file"} <= set(s.offered)


def test_a_tool_outside_the_role_list_is_a_role_absence():
    s = explain_surface(ALL, role_tools=["read_file"], builtin_names=BUILTIN)
    a = _why(s, "write_file")
    assert a.stage == "role" and "role" in a.reason.lower()


def test_the_core_profile_hides_spawn_and_names_the_switch():
    s = explain_surface(ALL, profile="core", builtin_names=BUILTIN)
    a = _why(s, "spawn")
    assert a.stage == "profile" and "WISP_TOOL_PROFILE=full" in a.reason
    assert "spawn" in explain_surface(ALL, profile="full", builtin_names=BUILTIN).offered


def test_extension_tools_are_never_hidden_by_the_profile():
    s = explain_surface(ALL, profile="core", builtin_names=BUILTIN)
    assert EXT <= set(s.offered)


def test_the_capability_partition_applies_only_when_enabled_and_read_only():
    on = explain_surface(ALL, permission_mode="read_only", capability_filtering=True, profile="full", builtin_names=BUILTIN)
    assert _why(on, "write_file").stage == "capability"
    off = explain_surface(ALL, permission_mode="read_only", capability_filtering=False, profile="full", builtin_names=BUILTIN)
    assert "write_file" in off.offered, "with the flag off the legacy surface is unchanged"


def test_an_unrestricted_subagent_loses_the_parents_skill_menu():
    s = explain_surface(ALL, subagent=True, profile="full", builtin_names=BUILTIN)
    assert _why(s, "skill__alpha").stage == "subagent"


def test_a_generalist_child_inherits_only_the_extension_tools_it_could_actually_run():
    """The runner expands a child's "all" into the built-in registry plus the MCP tools it may inherit (declared tool_risk: read in
    any mode; any MCP tool in full mode); skill tools never. As the list is explicit, the tool profile does not apply to it."""
    ext = sorted(EXT)
    full = explain_surface(ALL, role_tools=["all"], child=True, permission_mode="full", builtin_names=BUILTIN,
                           extension_tools=ext)
    assert "mcp__srv__lookup" in full.offered and "skill__alpha" not in full.offered
    assert _why(full, "skill__alpha").stage == "role" and "skill tools are never inherited" in _why(full, "skill__alpha").reason
    assert "spawn" in full.offered, "an explicit child list is not narrowed by the tool profile"

    auto = explain_surface(ALL, role_tools=["all"], child=True, permission_mode="auto_edit", builtin_names=BUILTIN,
                           extension_tools=ext)
    assert "mcp__srv__lookup" not in auto.offered, "undeclared: it would need an approver a child does not have"

    declare_tool_risk("mcp__srv__lookup", ToolRisk.READ)
    try:
        read = explain_surface(ALL, role_tools=["all"], child=True, permission_mode="auto_edit", builtin_names=BUILTIN,
                               extension_tools=ext)
    finally:
        forget_declared_risk("srv")
    assert "mcp__srv__lookup" in read.offered, "declared tool_risk: read executes unprompted, so it is inherited in any mode"


def test_every_input_tool_is_either_offered_or_explained_exactly_once():
    s = explain_surface(ALL, role_tools=ROLE_CONFIGS["coder"].allowed_tools, child=True, permission_mode="ask_all",
                        builtin_names=BUILTIN)
    assert sorted(s.offered + tuple(a.tool for a in s.absent)) == sorted(ALL)
    assert len({a.tool for a in s.absent}) == len(s.absent)


def test_it_is_pure():
    names = list(ALL)
    role = list(ROLE_CONFIGS["tester"].allowed_tools)
    explain_surface(names, role_tools=role, child=True, builtin_names=BUILTIN)
    assert names == ALL and role == list(ROLE_CONFIGS["tester"].allowed_tools)


# ── differential: the surface equals what the real pipeline puts in a provider request ──────────────────────────


@pytest.mark.parametrize("mode,profile,cap", list(itertools.product(MODES, ("core", "full"), (False, True))))
def test_parent_surface_matches_the_real_pipeline(mode, profile, cap, tmp_path):
    core = _core(tmp_path, permission_mode=mode, tool_profile=profile, capability_filtering=cap)
    real = _names(core._provider_tools(_session(tmp_path)))
    got = explain_surface(ALL, permission_mode=mode, profile=profile, capability_filtering=cap, builtin_names=BUILTIN)
    assert set(got.offered) == real


@pytest.mark.parametrize("mode,profile,cap", list(itertools.product(MODES, ("core", "full"), (False, True))))
def test_unrestricted_subagent_surface_matches_the_real_pipeline(mode, profile, cap, tmp_path):
    core = _core(tmp_path, permission_mode=mode, tool_profile=profile, capability_filtering=cap)
    real = _names(core._provider_tools(_session(tmp_path, **SUB)))
    got = explain_surface(ALL, subagent=True, permission_mode=mode, profile=profile, capability_filtering=cap,
                          builtin_names=BUILTIN)
    assert set(got.offered) == real


@pytest.mark.parametrize("role", list(ROLE_CONFIGS))
@pytest.mark.parametrize("mode,cap,declared", list(itertools.product(MODES, (False, True), (False, True))))
def test_child_surface_matches_the_runner_then_the_real_pipeline(role, mode, cap, declared, tmp_path):
    tools = list(ROLE_CONFIGS[role].allowed_tools)
    ext = sorted(EXT)
    if declared:
        declare_tool_risk("mcp__srv__lookup", ToolRisk.READ)
    try:
        allowed = _effective_child_tools(tools, mode, ext)  # what the runner stores as the child's allowed_tools
        core = _core(tmp_path, permission_mode=mode, capability_filtering=cap)
        real = _names(core._provider_tools(_session(tmp_path, allowed_tools=allowed, **SUB)))
        got = explain_surface(ALL, role_tools=tools, child=True, permission_mode=mode, capability_filtering=cap,
                              builtin_names=BUILTIN, extension_tools=ext)
    finally:
        forget_declared_risk("srv")
    assert set(got.offered) == real, f"{role}/{mode}/cap={cap}/declared={declared}: {sorted(set(got.offered) ^ real)}"
