"""A standing, narrow yes for callers with no human to ask: `unattended_auto_approve_tools`.

Background research agents were refused `web_fetch` / `web_search` with NO_APPROVER: `auto_edit` gates every non-READ tool
and a background agent has nobody to ask (ADR-0055 §3: "no approver is not an approval"). The only existing remedies were
global (`auto_approve`, `permission_mode=full`) and approve every write and shell call too. This setting lets the USER name
the tools an approver-less caller may run, and nothing else changes. It is deliberately narrow by construction:

- it applies only when there is NO approver (an interactive session still asks);
- it can only name READ- or NETWORK-class tools (a `write_file` / `run_bash` / MCP entry is ignored, with a warning);
- it never overrides a policy bundle's forced approval, the permission-mode guard, or the dangerous-command guard;
- the default is empty (nothing changes until the user sets it) and every use leaves an audit record.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from wisp.config import WispConfig
from wisp.tool_executor import ToolExecutor, standing_grant_applies


def _cfg(allow: tuple[str, ...] = (), mode: str = "auto_edit", auto_approve: bool = False) -> WispConfig:
    cfg = WispConfig()
    object.__setattr__(cfg, "permission_mode", mode)
    object.__setattr__(cfg, "auto_approve", auto_approve)
    object.__setattr__(cfg, "unattended_auto_approve_tools", allow)
    return cfg


def _run(tool: str, cfg: WispConfig, tmp_path, approval_handler=None, args: dict | None = None):
    """Drive the production executor; return (text of all events, tools that actually ran, approver asked)."""
    ex = ToolExecutor(cfg)
    ran: list[str] = []
    asked: list[str] = []
    audited: list[tuple[str, str]] = []

    async def body(name, a, ws):
        ran.append(name)
        return '{"status": "ok", "data": "done"}', 0.0

    ex._execute_tool = body
    original = ex._audit_standing_grant

    def spy(func_name, func_args, workspace):
        audited.append((func_name, "standing-grant"))
        return original(func_name, func_args, workspace)

    ex._audit_standing_grant = spy

    async def approver(name, a, reason):
        asked.append(name)
        return True, None

    handler = approver if approval_handler == "ask" else None

    async def go() -> str:
        text = ""
        async for ev in ex.execute(tool, args if args is not None else {}, str(tmp_path), approval_handler=handler):
            text += str(ev)
        return text

    return asyncio.run(go()), ran, asked, audited


def test_default_is_empty_and_nothing_changes(tmp_path):
    assert WispConfig().unattended_auto_approve_tools == ()
    text, ran, _, _ = _run("web_fetch", _cfg(), tmp_path)
    assert "NO_APPROVER" in text and ran == []


def test_a_listed_network_tool_runs_without_an_approver(tmp_path):
    text, ran, asked, audited = _run("web_fetch", _cfg(("web_fetch",)), tmp_path)
    assert "NO_APPROVER" not in text and ran == ["web_fetch"] and asked == []
    assert ("web_fetch", "standing-grant") in audited, "a use of the standing grant is audited, with its layer"


def test_only_the_listed_tool_is_granted(tmp_path):
    text, ran, _, _ = _run("web_search", _cfg(("web_fetch",)), tmp_path)
    assert "NO_APPROVER" in text and ran == []


@pytest.mark.parametrize("tool", ["write_file", "edit_file", "run_bash", "git_commit", "git_push", "gh_pr_merge"])
def test_a_mutating_or_exec_tool_cannot_be_granted_this_way(tool, tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        text, ran, _, _ = _run(tool, _cfg((tool,)), tmp_path)
    assert "NO_APPROVER" in text and ran == [], f"{tool} must still need a real approver or an explicit auto_approve"


def test_an_mcp_tool_cannot_be_granted_this_way(tmp_path):
    name = "mcp__someserver__do_thing"
    assert standing_grant_applies(_cfg((name,)), name) is False


def test_an_interactive_session_still_asks(tmp_path):
    text, ran, asked, _ = _run("web_fetch", _cfg(("web_fetch",)), tmp_path, approval_handler="ask")
    assert asked == ["web_fetch"] and ran == ["web_fetch"], "the grant is for callers with nobody to ask"


def test_read_only_mode_still_blocks_it(tmp_path):
    text, ran, _, _ = _run("web_fetch", _cfg(("web_fetch",), mode="read_only"), tmp_path)
    assert ran == [] and "NO_APPROVER" not in text, "the permission-mode guard comes first and is unchanged"


def test_a_policy_bundle_that_forces_approval_still_wins(tmp_path):
    from types import SimpleNamespace

    cfg = _cfg(("web_fetch",))
    ex = ToolExecutor(cfg)
    ex.policy = SimpleNamespace(approval_matrix={"web_fetch": "approve"})
    ran: list[str] = []

    async def body(name, a, ws):
        ran.append(name)
        return '{"status": "ok", "data": "done"}', 0.0

    ex._execute_tool = body

    async def go() -> str:
        return "".join([str(ev) async for ev in ex.execute("web_fetch", {}, str(tmp_path), approval_handler=None)])

    text = asyncio.run(go())
    assert "NO_APPROVER" in text and ran == [], "an organization 'approve' is not something a user setting can waive"


def test_the_setting_is_parsed_from_the_environment(monkeypatch):
    monkeypatch.setenv("WISP_UNATTENDED_AUTO_APPROVE_TOOLS", " web_fetch , web_search ,, ")
    assert WispConfig().unattended_auto_approve_tools == ("web_fetch", "web_search")
    monkeypatch.setenv("WISP_UNATTENDED_AUTO_APPROVE_TOOLS", "")
    assert WispConfig().unattended_auto_approve_tools == ()
