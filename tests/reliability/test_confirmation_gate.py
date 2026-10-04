"""The confirmation gate is STRUCTURAL: no approver is not an approval.

The spec asks for *"a tool boundary with a confirmation gate"*. wisp had the gate
and a hole in it, documented in `tool_executor.py`'s own comment until ADR-0069:

    "No handler, and the approval is not forced: fall through and execute. …
    ADR-0055 §3 residual 3 measured the consequence: with `approval_handler=None`
    a `write_file` in `auto_edit` **runs**."

So the gate was a **convention** — it held for the wiring the author had in mind —
rather than a **structure** that cannot be bypassed by wiring. This file is the
forcing test for the structural version.

**Why these tests are the load-bearing ones.** A gate is easy to test in the
direction that already works: *"with a handler, the handler is asked"*. That test
passed before the fix and says nothing about the hole. Every test below either
drives the **no-handler** path or asserts that the tool **did not execute** — the
thing the old fall-through did.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wisp.config import PermissionMode, WispConfig
from wisp.core.events import (
    DENIAL_NO_APPROVER,
    DENIAL_POLICY_DENIED,
    DENIAL_USER_DENIED,
)
from wisp.tool_executor import ToolExecutor


def _cfg(workspace: str, *, mode=PermissionMode.AUTO_EDIT, auto_approve=False):
    return WispConfig().replace(workspace=workspace, permission_mode=mode,
                                auto_approve=auto_approve)


def _hooks():
    mgr = MagicMock()
    mgr.arun_hooks = AsyncMock(return_value=[])
    mgr.maybe_reload_hooks = MagicMock()
    mgr.load_project_hooks = MagicMock()
    return mgr


def _patch_dispatch():
    """Keep the whole dispatch path live; only the blocking tool body is stubbed.

    The observation point matters: a test that patches `execute` itself would
    observe a copy of the gate rather than the gate. This patches the *pool seam*
    the gate calls through, so a refusal is visible as "the seam was never
    reached".
    """
    return patch.object(ToolExecutor, "_run_blocking", new_callable=AsyncMock,
                        return_value='{"status": "ok"}')


async def _run(te, name, args, workspace, handler=None):
    events = []
    async for ev in te.execute(name, args, workspace, approval_handler=handler):
        events.append(ev)
    return events


def _denial_status(events):
    """The structured status of the turn's single tool_result, or None."""
    results = [e for e in events
               if getattr(e, "type", None) == "tool_result"
               or (isinstance(e, dict) and e.get("type") == "tool_result")]
    assert len(results) == 1, f"expected one tool_result, got {len(results)}"
    r = results[0]
    res = r.data.get("result", {}) if hasattr(r, "data") else r.get("result", {})
    return res.get("status") if isinstance(res, dict) else None


# ── The hole, closed ────────────────────────────────────────────────────


class TestNoApproverIsNotAnApproval:
    @pytest.mark.asyncio
    async def test_a_mutating_tool_with_no_approver_is_denied(self, tmp_path):
        """The case ADR-0055 §3 residual 3 measured as *executing*."""
        te = ToolExecutor(config=_cfg(str(tmp_path)), hook_manager=_hooks())
        with _patch_dispatch() as seam:
            events = await _run(te, "write_file",
                                {"path": "a.txt", "content": "x"}, str(tmp_path),
                                handler=None)
        assert _denial_status(events) == DENIAL_NO_APPROVER, (
            "a mutating tool ran — or was refused for the wrong reason — with no "
            "approver available")
        seam.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_tool_did_not_execute(self, tmp_path):
        """**The forcing assertion.** Before ADR-0069 this path fell through and
        executed; a test that only checks the denial *code* would pass on a
        change that denied and executed anyway."""
        target = tmp_path / "written.txt"
        te = ToolExecutor(config=_cfg(str(tmp_path)), hook_manager=_hooks())
        with _patch_dispatch():
            await _run(te, "write_file",
                       {"path": str(target), "content": "x"}, str(tmp_path),
                       handler=None)
        assert not target.exists(), (
            "the file exists — the mutating tool ran unconfirmed, which is the "
            "defect the gate exists to prevent")

    @pytest.mark.asyncio
    async def test_the_denial_is_distinguishable_from_a_human_refusal(self, tmp_path):
        """ADR-0061 R4's rule, applied to the agent path: *"nobody could be
        asked"* and *"the human said no"* are different facts.

        A caller that cannot tell them apart cannot decide whether to retry, ask
        someone else, or reconfigure — and one code for both is how "no
        approver" gets read as a refusal.
        """
        te = ToolExecutor(config=_cfg(str(tmp_path)), hook_manager=_hooks())
        with _patch_dispatch():
            events = await _run(te, "write_file",
                                {"path": "a.txt", "content": "x"}, str(tmp_path),
                                handler=None)
        status = _denial_status(events)
        assert status not in (DENIAL_USER_DENIED, DENIAL_POLICY_DENIED), (
            f"the no-approver refusal reports {status!r}, which is a code a human "
            "refusal or a policy decision also uses")
        assert status == DENIAL_NO_APPROVER

    @pytest.mark.asyncio
    async def test_the_reason_says_nobody_could_be_asked(self, tmp_path):
        te = ToolExecutor(config=_cfg(str(tmp_path)), hook_manager=_hooks())
        with _patch_dispatch():
            events = await _run(te, "write_file",
                                {"path": "a.txt", "content": "x"}, str(tmp_path),
                                handler=None)
        text = str(events)
        assert "no approver is available" in text
        assert "nobody could be asked" in text, (
            "the message does not distinguish an absent approver from a refusal")


# ── Explicit authorisation still works ──────────────────────────────────


class TestExplicitAuthorisationIsTheOnlyWayThrough:
    @pytest.mark.asyncio
    async def test_auto_approve_authorises_the_write(self, tmp_path):
        """`auto_approve=True` is a CALLER's decision, which is the point: the
        model does not authorise a side effect, and neither does an accident of
        wiring. A caller with no human says so explicitly."""
        te = ToolExecutor(config=_cfg(str(tmp_path), auto_approve=True),
                          hook_manager=_hooks())
        with _patch_dispatch() as seam:
            events = await _run(te, "write_file",
                                {"path": "a.txt", "content": "x"}, str(tmp_path),
                                handler=None)
        seam.assert_called_once()
        assert _denial_status(events) != DENIAL_NO_APPROVER

    @pytest.mark.asyncio
    async def test_full_mode_authorises_the_write(self, tmp_path):
        te = ToolExecutor(config=_cfg(str(tmp_path), mode=PermissionMode.FULL),
                          hook_manager=_hooks())
        with _patch_dispatch() as seam:
            await _run(te, "write_file", {"path": "a.txt", "content": "x"},
                       str(tmp_path), handler=None)
        seam.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_handler_is_still_consulted_and_can_approve(self, tmp_path):
        """The direction that already worked must keep working — the fix removes
        a fall-through, not the gate."""
        handler = AsyncMock(return_value=(True, None))
        te = ToolExecutor(config=_cfg(str(tmp_path)), hook_manager=_hooks())
        with _patch_dispatch() as seam:
            await _run(te, "write_file", {"path": "a.txt", "content": "x"},
                       str(tmp_path), handler=handler)
        handler.assert_called_once()
        seam.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_handler_that_refuses_still_refuses(self, tmp_path):
        handler = AsyncMock(return_value=(False, None))
        te = ToolExecutor(config=_cfg(str(tmp_path)), hook_manager=_hooks())
        with _patch_dispatch() as seam:
            events = await _run(te, "write_file",
                                {"path": "a.txt", "content": "x"}, str(tmp_path),
                                handler=handler)
        seam.assert_not_called()
        assert _denial_status(events) == DENIAL_USER_DENIED, (
            "a human's refusal must still be reported as a human's refusal")


# ── The floor ───────────────────────────────────────────────────────────


class TestTheGateIsWired:
    def test_the_denial_code_exists_and_is_its_own(self):
        """A floor for the vocabulary: if `NO_APPROVER` collapsed into an
        existing code, every distinguishable-denial test above would pass while
        the distinction was gone."""
        codes = {DENIAL_NO_APPROVER, DENIAL_POLICY_DENIED, DENIAL_USER_DENIED}
        assert len(codes) == 3, "the no-approver code is not its own code"

    @pytest.mark.asyncio
    async def test_a_read_tool_needs_no_approver(self, tmp_path):
        """The gate must not over-apply. A read is not a side effect, so it runs
        with no approver — otherwise the fix would refuse the whole tool surface
        and be reverted."""
        te = ToolExecutor(config=_cfg(str(tmp_path)), hook_manager=_hooks())
        with _patch_dispatch() as seam:
            await _run(te, "read_file", {"path": "a.txt"}, str(tmp_path),
                       handler=None)
        seam.assert_called_once()
