"""Migration P2 — the authorization verdict is recorded for BOTH outcomes.

Before P2 the `AuthorizationDecision` was consumed **only on the deny path**,
where `controlling_layer` was interpolated into a denial message
(`tool_executor.py:722`). An *allowed* call left no record that the layered
authority had been consulted, let alone which layer permitted it.

The audit trail's allow-side writers (`log_auto_approved` /
`log_explicit_approved`) fire on the **approval** path, not the authority path
— verified by grep: `log_auto_approved` is called at `tool_executor.py:942`,
inside the approval block. So `allow` and `approval` were indistinguishable
from "no gate ran".

These tests pin the closure.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from wisp.config import PermissionMode, WispConfig
from wisp.infra.audit import ImmutableAuditTrail
from wisp.infra.store import UnifiedStore


# ── Harness ─────────────────────────────────────────────────────────────


def _hook_mgr():
    from unittest.mock import AsyncMock, MagicMock
    mgr = MagicMock()
    mgr.arun_hooks = AsyncMock(return_value=[])
    mgr.maybe_reload_hooks = MagicMock()
    mgr.load_project_hooks = MagicMock()
    return mgr


def _executor(tmp_path, mode=PermissionMode.FULL, with_trail=True):
    from wisp.tool_executor import ToolExecutor
    ws = str(tmp_path)
    config = WispConfig().replace(workspace=ws, permission_mode=mode,
                                  auto_approve=False)
    trail = None
    if with_trail:
        trail = ImmutableAuditTrail(UnifiedStore(tmp_path / "wisp.db"))
    te = ToolExecutor(config=config, hook_manager=_hook_mgr(),
                      audit_trail=trail)
    return te, trail


def _shutdown(te):
    te._tool_pool.shutdown(wait=False)
    te._network_pool.shutdown(wait=False)


def _run(te, tool, args, workspace, call_id="c1"):
    async def _main():
        return [e async for e in te.execute(
            tool, args, workspace, tool_call_id=call_id)]
    return asyncio.run(_main())


def _verdict_rows(trail):
    """Rows written by the P2 authorization recorder."""
    return [r for r in trail.entries(limit=200)
            if r["action"] and "Allowed by" in (r["reason"] or "")]


def _denial_rows(trail):
    return [r for r in trail.entries(limit=200)
            if r["action"] and "Denied by" in (r["reason"] or "")]


# ── The allow path now leaves a record ──────────────────────────────────


class TestAllowVerdictRecorded:
    def test_allowed_call_records_a_verdict(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        te, trail = _executor(tmp_path)
        try:
            _run(te, "read_file", {"path": str(tmp_path / "a.txt")},
                 str(tmp_path))
        finally:
            _shutdown(te)

        rows = _verdict_rows(trail)
        assert rows, "an allowed call left no authorization record"

    def test_the_layer_is_named(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        te, trail = _executor(tmp_path)
        try:
            _run(te, "read_file", {"path": str(tmp_path / "a.txt")},
                 str(tmp_path))
        finally:
            _shutdown(te)

        row = _verdict_rows(trail)[0]
        assert "Allowed by allow layer" in row["reason"], row["reason"]

    def test_the_layer_is_also_stored_structurally(self, tmp_path):
        """Prose is for humans; the envelope is for queries. A schema change
        would have invalidated the hash chain for existing rows, so the layer
        rides in `args_summary` instead."""
        (tmp_path / "a.txt").write_text("hello")
        te, trail = _executor(tmp_path)
        try:
            _run(te, "read_file", {"path": str(tmp_path / "a.txt")},
                 str(tmp_path))
        finally:
            _shutdown(te)

        row = _verdict_rows(trail)[0]
        envelope = json.loads(row["args_summary"])
        assert envelope["decision"] == "authorized"
        assert envelope["layer"] == "allow"
        assert envelope["args_keys"] == ["path"]

    def test_row_is_marked_allowed(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        te, trail = _executor(tmp_path)
        try:
            _run(te, "read_file", {"path": str(tmp_path / "a.txt")},
                 str(tmp_path))
        finally:
            _shutdown(te)

        assert _verdict_rows(trail)[0]["allowed"] == 1


# ── The deny path is unchanged and still names its layer ────────────────


class TestDenyVerdictRecorded:
    def test_denied_call_still_records_the_layer(self, tmp_path):
        (tmp_path / ".wisp-quarantine").write_text("untrusted")
        te, trail = _executor(tmp_path)
        try:
            _run(te, "write_file",
                 {"path": str(tmp_path / "x.py"), "content": "x"},
                 str(tmp_path))
        finally:
            _shutdown(te)

        rows = _denial_rows(trail)
        assert rows, "a denied call left no audit record"
        assert "Denied by workspace layer" in rows[0]["reason"]

    def test_denial_is_not_recorded_as_allowed(self, tmp_path):
        (tmp_path / ".wisp-quarantine").write_text("untrusted")
        te, trail = _executor(tmp_path)
        try:
            _run(te, "write_file",
                 {"path": str(tmp_path / "x.py"), "content": "x"},
                 str(tmp_path))
        finally:
            _shutdown(te)

        assert all(r["allowed"] == 0 for r in _denial_rows(trail))

    def test_exactly_one_verdict_per_call(self, tmp_path):
        """The recorder sits AFTER the allow/deny fork, so a call is recorded
        once — on one path or the other, never both."""
        (tmp_path / ".wisp-quarantine").write_text("untrusted")
        te, trail = _executor(tmp_path)
        try:
            _run(te, "write_file",
                 {"path": str(tmp_path / "x.py"), "content": "x"},
                 str(tmp_path), "c9")
        finally:
            _shutdown(te)

        assert len(_denial_rows(trail)) == 1
        assert len(_verdict_rows(trail)) == 0

    def test_allowed_call_writes_no_denial_row(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        te, trail = _executor(tmp_path)
        try:
            _run(te, "read_file", {"path": str(tmp_path / "a.txt")},
                 str(tmp_path))
        finally:
            _shutdown(te)

        assert _denial_rows(trail) == []


# ── The hash chain stays valid ──────────────────────────────────────────


class TestAuditChainIntegrity:
    def test_chain_verifies_after_mixed_allow_and_deny(self, tmp_path):
        """The verdict record joins an existing hash-chained sink. If it broke
        the chain, tamper-evidence would be lost — a worse outcome than the
        missing record it fixes."""
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / ".wisp-quarantine").write_text("untrusted")
        te, trail = _executor(tmp_path)
        try:
            _run(te, "read_file", {"path": str(tmp_path / "a.txt")},
                 str(tmp_path), "c1")
            _run(te, "write_file",
                 {"path": str(tmp_path / "x.py"), "content": "x"},
                 str(tmp_path), "c2")
        finally:
            _shutdown(te)

        assert trail.verify() is None, "audit hash chain broken by P2"

    def test_chain_verifies_with_no_trail_configured(self, tmp_path):
        """No trail → the recorder is a silent no-op, not a crash."""
        te, _ = _executor(tmp_path, with_trail=False)
        try:
            (tmp_path / "a.txt").write_text("hello")
            events = _run(te, "read_file", {"path": str(tmp_path / "a.txt")},
                          str(tmp_path))
            assert events, "the call still produced events"
        finally:
            _shutdown(te)
