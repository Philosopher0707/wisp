"""AUTO_EDIT (the default mode) asks before a git/gh write; it no longer refuses it outright.

Reported: *"✗ Blocked: AUTO_EDIT mode blocks git_push — execution is a hard deny in this mode and
is never prompted."* An operator in the default mode could not push at all without switching the
whole session to `ask_all` or `full`. Since 2026-09-28 `git_push`, `git_commit`, `git_branch` and
`gh_pr_create` are REQUIRE_APPROVAL in AUTO_EDIT, like `run_bash`: an operator's yes **every
time**, never unprompted.

Held here, on the production executor (`ToolExecutor.execute`, the path every transport uses):

* **yes** → the operator is asked exactly once, naming the tool, and it runs;
* **no** → it does not run, and the model is told the user declined;
* **no approval handler** (headless, a REST call without a bridge) → it does not run;
* **subagent children** never receive these tools (`filter_allowed_for_mode`).

Only the tool body is replaced (`_execute_tool`), so no real `git push` happens; every gate in
front of it is real. Floor (F81): the "yes" case asserts the body ran, so the "no" cases cannot
pass because nothing ever runs.
"""
from __future__ import annotations

import pytest

from wisp.config import WispConfig
from wisp.infra.policy_engine import filter_allowed_for_mode
from wisp.tool_executor import ToolExecutor

GIT_WRITES = ("git_push", "git_commit", "git_branch", "gh_pr_create")
_ARGS = {"git_push": {}, "git_commit": {"message": "m"},
         "git_branch": {"action": "create", "name": "b"}, "gh_pr_create": {"title": "t"}}


def _executor(tmp_path) -> tuple[ToolExecutor, list[str]]:
    cfg = WispConfig().replace(workspace=str(tmp_path), permission_mode="auto_edit",
                               auto_approve=False)
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


async def _run(ex, tool, tmp_path, handler) -> list[dict]:
    return [_flat(ev) async for ev in ex.execute(tool, _ARGS[tool], str(tmp_path),
                                                 tool_call_id="c1", approval_handler=handler)]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", GIT_WRITES)
async def test_yes_runs_it_after_asking_once(tmp_path, tool) -> None:
    ex, ran = _executor(tmp_path)
    asked: list[str] = []

    async def yes(name, args, reason):
        asked.append(name)
        return True, None

    await _run(ex, tool, tmp_path, yes)
    assert asked == [tool], f"the operator must be asked exactly once for {tool}"
    assert ran == [tool]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", GIT_WRITES)
async def test_no_does_not_run_it(tmp_path, tool) -> None:
    ex, ran = _executor(tmp_path)

    async def no(name, args, reason):
        return False, None

    events = await _run(ex, tool, tmp_path, no)
    assert ran == []
    results = [ev for ev in events if ev.get("type") == "tool_result"]
    assert len(results) == 1 and results[0].get("tool_call_id") == "c1"
    assert "USER_DENIED" in str(results[0].get("result")), (
        "the model must be told the operator declined, not that the mode forbids it")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", GIT_WRITES)
async def test_without_an_approval_handler_it_does_not_run(tmp_path, tool) -> None:
    ex, ran = _executor(tmp_path)
    await _run(ex, tool, tmp_path, None)
    assert ran == [], f"{tool} must never run unprompted"


def test_subagent_children_never_receive_git_writes() -> None:
    offered = filter_allowed_for_mode("auto_edit", ["read_file", "write_file", *GIT_WRITES])
    assert offered == ["read_file", "write_file"]
