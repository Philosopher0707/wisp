"""Tests for Q22: structured audit trail for auto-approved destructive operations.

When WISP_HEADLESS_AUTO_APPROVE=1 bypasses explicit approval, every
invocation of a _WRITE_TOOLS tool is persisted to .wisp/audit.jsonl so
CI/compliance can retrospectively audit what ran without operator consent.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

# We can't easily exercise ToolExecutor.execute() end-to-end without
# mocking the LLM client, so we test:
#   1. AuditLog directly (unit-level)
#   2. ToolExecutor.execute() via mocked backend (integration-level)

from wisp.tools.audit import AuditLog, _result_status


# ── 1. Direct AuditLog tests ────────────────────────────────────────────

class TestAuditLogUnit:
    """Unit tests for the AuditLog helper in isolation."""

    def test_auto_approved_entry_structure(self, tmp_path):
        """Auto-approved entries contain all required fields."""
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLog(log_path)
        audit.log_auto_approved(
            func_name="write_file",
            func_args={"path": "test.md", "content": "hello world" * 100},
            workspace=str(tmp_path),
            result='{"status": "ok", "path": "test.md"}',
            duration_ms=42.5,
            mode="full",
            forced=False,
        )

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])

        assert entry["tool"] == "write_file"
        assert entry["decision"] == "auto_approved"
        assert entry["forced"] is False
        assert entry["mode"] == "full"
        assert entry["duration_ms"] == 42.5
        assert entry["result_status"] == "ok"
        assert "timestamp" in entry
        assert entry["args_keys"] == ["content", "path"]
        # args should be scrubbed: content truncated, path kept
        assert len(entry["arg_summary"]["content"]) <= 120
        assert entry["arg_summary"]["path"] == "test.md"

    def test_explicit_approved_entry(self, tmp_path):
        """Explicit approvals record decision="approved"."""
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLog(log_path)
        audit.log_explicit_approved(
            func_name="run_bash",
            func_args={"command": "echo hello"},
            workspace=str(tmp_path),
            result="hello\n",
            duration_ms=15.0,
            mode="auto_edit",
        )

        entry = json.loads(log_path.read_text().strip().split("\n")[0])
        assert entry["decision"] == "approved"
        assert entry["forced"] is False
        assert entry["mode"] == "auto_edit"

    def test_blocked_entry(self, tmp_path):
        """Blocked tools record no result and include block_reason."""
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLog(log_path)
        audit.log_blocked(
            func_name="run_bash",
            func_args={"command": "rm -rf /"},
            workspace=str(tmp_path),
            reason="Dangerous command blocked: rm -rf",
            mode="auto_edit",
        )

        entry = json.loads(log_path.read_text().strip().split("\n")[0])
        assert entry["decision"] == "blocked"
        assert entry["block_reason"] == "Dangerous command blocked: rm -rf"
        assert entry["duration_ms"] == 0.0
        assert entry["result_status"] == "ok"
    def test_scrub_truncates_long_content(self, tmp_path):
        """Content fields >120 chars get truncated with '...'."""
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLog(log_path)
        long_text = "x" * 500
        audit.log_auto_approved(
            func_name="edit_file",
            func_args={
                "path": "f.txt",
                "old_text": "short",
                "new_text": long_text,
            },
            workspace=str(tmp_path),
            result="ok",
            duration_ms=1.0,
            mode="full",
        )
        entry = json.loads(log_path.read_text().strip().split("\n")[0])
        assert len(entry["arg_summary"]["new_text"]) == 120
        assert entry["arg_summary"]["new_text"].endswith("...")

    def test_multiple_entries_are_append(self, tmp_path):
        """Sequential writes append to the JSONL file."""
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLog(log_path)
        audit.log_auto_approved("write_file", {"path": "a"}, str(tmp_path), "ok", 1.0, "full")
        audit.log_auto_approved("write_file", {"path": "b"}, str(tmp_path), "ok", 2.0, "full")

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["arg_summary"]["path"] == "a"
        assert json.loads(lines[1])["arg_summary"]["path"] == "b"

    def test_concurrent_append_no_corruption(self, tmp_path):
        """Advisory locking prevents interleaved writes from concurrent threads."""
        import threading
        log_path = tmp_path / "audit.jsonl"

        def writer(val):
            audit = AuditLog(log_path)
            for _ in range(10):
                audit.log_auto_approved("write_file", {"path": val}, str(tmp_path), "ok", 1.0, "full")

        threads = [threading.Thread(target=writer, args=(f"t{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 40  # 4 threads × 10 entries
        # Every line must be parseable JSON (no interleaving)
        for line in lines:
            assert json.loads(line)["arg_summary"]["path"].startswith("t")



# ── 2. Consolidated audit trail tests ───────────────────────────────────

class TestConsolidatedAuditTrail:
    """AuditLog delegates to ImmutableAuditTrail when a store is provided."""

    def test_audit_log_delegates_to_sqlite_when_store_given(self, tmp_path):
        from wisp.infra.store import UnifiedStore
        from wisp.infra.audit import ImmutableAuditTrail

        store = UnifiedStore(tmp_path / "test.db")
        audit = AuditLog(store=store)
        audit.log_auto_approved(
            func_name="write_file",
            func_args={"path": "test.md", "content": "hello"},
            workspace=str(tmp_path),
            result='{"status": "ok", "path": "test.md"}',
            duration_ms=42.5,
            mode="full",
            forced=False,
        )

        trail = ImmutableAuditTrail(store)
        entries = trail.entries()
        assert len(entries) >= 1
        last = entries[0]
        assert last["tool_name"] == "write_file"
        assert last["workspace"] == str(tmp_path)
        assert last["allowed"] == 1

    def test_blocked_entry_delegates_to_sqlite(self, tmp_path):
        from wisp.infra.store import UnifiedStore
        from wisp.infra.audit import ImmutableAuditTrail

        store = UnifiedStore(tmp_path / "test.db")
        audit = AuditLog(store=store)
        audit.log_blocked(
            func_name="run_bash",
            func_args={"command": "rm -rf /"},
            workspace=str(tmp_path),
            reason="Dangerous command blocked",
            mode="auto_edit",
        )

        trail = ImmutableAuditTrail(store)
        entries = trail.entries()
        assert len(entries) >= 1
        last = entries[0]
        assert last["tool_name"] == "run_bash"
        assert last["allowed"] == 0
        assert "Dangerous command blocked" in last["reason"]

    def test_tool_executor_wires_audit_trail(self, tmp_path):
        """ToolExecutor passes audit_trail to AuditLog for consolidated storage."""
        from wisp.infra.store import UnifiedStore
        from wisp.infra.audit import ImmutableAuditTrail
        from wisp.tool_executor import ToolExecutor
        from wisp.config import WispConfig

        store = UnifiedStore(tmp_path / "test.db")
        trail = ImmutableAuditTrail(store)
        te = ToolExecutor(
            config=WispConfig(),
            audit_trail=trail,
        )
        assert te.audit_trail is trail
