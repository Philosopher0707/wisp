"""No approver, no yes: the agent path and the REST gate share one rule.

`0c6bcf2` made the executor deny a gated tool when nobody could be asked (`NO_APPROVER`), ADR-0061 R4's
"no client ⇒ deny" applied to the agent path. REST still asked a different model — `SecurityPolicy`,
which auto-approves file writes in `auto_edit` — so `POST /api/files` was allowed where the same
`write_file` tool call was refused. `tests/test_authorization_parity.py` pinned that they must agree.

The fix is one predicate, `tool_executor.approval_needed(config, tool)`: would the executor stop for an
approver here? REST asks it too, and `authorized` means the same thing on both surfaces: full mode or
`auto_approve` (the caller's explicit decision), never an accident of wiring.

Held here by driving the production executor, not by restating its logic: for every mode × tool ×
`auto_approve`, the predicate must equal "the executor denied with NO_APPROVER".
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from wisp.config import WispConfig
from wisp.tool_executor import ToolExecutor, approval_needed

MODES = ("full", "auto_edit", "ask_all", "read_only")
TOOLS = (
    "write_file", "edit_file", "run_bash", "git_branch", "git_commit", "git_push", "gh_pr_create",
    "gh_pr_close", "gh_pr_merge", "gh_pr_comment", "git_sync_base",
    "read_file", "list_files", "git_status", "git_log", "gh_pr_view",
)


def _cfg(mode: str, auto_approve: bool) -> WispConfig:
    cfg = WispConfig()
    object.__setattr__(cfg, "permission_mode", mode)
    object.__setattr__(cfg, "auto_approve", auto_approve)
    return cfg


def _executor_asked_for_an_approver(tool: str, mode: str, auto_approve: bool, tmp_path) -> bool:
    ex = ToolExecutor(_cfg(mode, auto_approve))
    ran: list[str] = []

    async def body(name, args, ws):
        ran.append(name)
        return '{"status": "ok", "data": "done"}', 0.0

    ex._execute_tool = body

    async def go() -> str:
        text = ""
        async for ev in ex.execute(tool, {}, str(tmp_path), approval_handler=None):
            text += str(ev)
        return text

    return "NO_APPROVER" in asyncio.run(go())


@pytest.mark.parametrize("auto_approve", [False, True])
@pytest.mark.parametrize("mode", MODES)
def test_the_predicate_is_exactly_the_executors_behaviour(mode, auto_approve, tmp_path):
    for tool in TOOLS:
        predicted = approval_needed(_cfg(mode, auto_approve), tool)
        actual = _executor_asked_for_an_approver(tool, mode, auto_approve, tmp_path)
        assert predicted == actual, f"{tool} in {mode} (auto_approve={auto_approve}): predicted {predicted}, executor {actual}"


def test_the_table_is_not_vacuous():
    """Both answers must occur, or the equality above could hold trivially."""
    answers = {approval_needed(_cfg(m, a), t) for m in MODES for a in (False, True) for t in TOOLS}
    assert answers == {True, False}


# ── the REST gate ────────────────────────────────────────────────────────────

class _Request:
    """The minimum `request_policy` and the gate read: app.state.root.config."""

    def __init__(self, mode: str, auto_approve: bool):
        cfg = SimpleNamespace(permission_mode=mode, auto_approve=auto_approve)
        self.app = SimpleNamespace(state=SimpleNamespace(root=SimpleNamespace(config=cfg)))


def _rest(action: str, mode: str, auto_approve: bool, ws) -> str:
    from fastapi import HTTPException

    from wisp.server.deps import require_tool_allowed

    try:
        require_tool_allowed(_Request(mode, auto_approve), action, {"path": "a.py"}, str(ws))
        return "ALLOW"
    except HTTPException as exc:
        return exc.detail


@pytest.mark.parametrize("action", ["write_file", "edit_file"])
def test_rest_denies_a_gated_write_when_nobody_authorised_it(action, tmp_path):
    detail = _rest(action, "auto_edit", False, tmp_path)
    assert detail != "ALLOW"
    assert "no approver is present over REST" in detail and "auto_approve" in detail and "full" in detail


@pytest.mark.parametrize("action", ["write_file", "edit_file"])
def test_rest_allows_it_when_the_server_authorised_it(action, tmp_path):
    assert _rest(action, "auto_edit", True, tmp_path) == "ALLOW"
    assert _rest(action, "full", False, tmp_path) == "ALLOW"


def test_rest_reads_are_unaffected(tmp_path):
    assert _rest("read_file", "auto_edit", False, tmp_path) == "ALLOW"


def test_the_two_surfaces_agree_on_every_pair(tmp_path):
    """The property parity exists for, over the same grid the predicate is checked on."""
    ws = tmp_path
    for mode in ("full", "auto_edit", "ask_all"):
        for auto_approve in (False, True):
            for tool in ("write_file", "edit_file", "read_file"):
                agent_denies = _executor_asked_for_an_approver(tool, mode, auto_approve, tmp_path)
                rest_denies = _rest(tool, mode, auto_approve, ws) != "ALLOW"
                assert agent_denies == rest_denies, (tool, mode, auto_approve, agent_denies, rest_denies)


# ── an independent statement of the rule (the executor now calls the same function, so comparing the two
#    would be circular for the "forced" part) ──

@pytest.mark.parametrize("tool,expected", [
    ("run_bash", True), ("git_branch", True), ("git_commit", True), ("git_push", True), ("gh_pr_create", True),
    ("gh_pr_close", True), ("gh_pr_merge", True), ("gh_pr_comment", True), ("git_sync_base", True),
    ("write_file", False), ("edit_file", False), ("read_file", False), ("git_log", False),
])
def test_auto_edit_with_auto_approve_still_asks_for_exec_and_git_but_not_file_edits(tool, expected):
    """auto_approve waives the ask for file edits and nothing else in auto_edit."""
    assert approval_needed(_cfg("auto_edit", True), tool) is expected


@pytest.mark.parametrize("tool", ["write_file", "edit_file", "run_bash", "git_push"])
def test_ask_all_asks_for_every_gated_tool_even_with_auto_approve(tool):
    assert approval_needed(_cfg("ask_all", True), tool) is True


@pytest.mark.parametrize("tool", ["write_file", "run_bash", "git_push", "gh_pr_merge"])
def test_full_mode_never_asks(tool):
    assert approval_needed(_cfg("full", False), tool) is False


@pytest.mark.parametrize("tool", ["write_file", "run_bash", "git_push"])
def test_without_auto_approve_auto_edit_asks_for_every_gated_tool(tool):
    assert approval_needed(_cfg("auto_edit", False), tool) is True
