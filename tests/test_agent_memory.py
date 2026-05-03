"""Tests for wisp.agent_memory — session summary persistence."""

import json
import tempfile
from pathlib import Path

import pytest

from wisp.agent_memory import AgentMemory, AGENT_MEMORY_DIR, SESSIONS_FILE
from wisp.summarizer import SessionSummary


class TestAgentMemory:
    """Unit tests for AgentMemory persistence and retrieval."""

    def setup_method(self):
        """Use a temporary directory for each test."""
        self._orig_dir = AGENT_MEMORY_DIR
        self.tmp = tempfile.TemporaryDirectory()
        # Monkey-patch the module-level paths
        import wisp.agent_memory as am
        am.AGENT_MEMORY_DIR = Path(self.tmp.name)
        am.SESSIONS_FILE = am.AGENT_MEMORY_DIR / "sessions.jsonl"
        self.mem = AgentMemory()
        self._am = am  # keep reference for assertions

    def teardown_method(self):
        """Restore original paths."""
        import wisp.agent_memory as am
        am.AGENT_MEMORY_DIR = self._orig_dir
        am.SESSIONS_FILE = self._orig_dir / "sessions.jsonl"
        self.tmp.cleanup()

    def _make_summary(self, session_id: str, workspace: str = "/tmp", **kwargs) -> SessionSummary:
        return SessionSummary(
            session_id=session_id,
            timestamp="2026-01-01T00:00:00Z",
            workspace=workspace,
            summary=kwargs.get("summary", "Test summary."),
            key_decisions=kwargs.get("key_decisions", []),
            user_preferences=kwargs.get("user_preferences", []),
            open_tasks=kwargs.get("open_tasks", []),
            files_touched=kwargs.get("files_touched", []),
        )

    def test_save_creates_file(self):
        summary = self._make_summary("sid-1")
        self.mem.save(summary)
        assert self._am.SESSIONS_FILE.exists()

    def test_save_appends(self):
        s1 = self._make_summary("sid-1")
        s2 = self._make_summary("sid-2")
        self.mem.save(s1)
        self.mem.save(s2)
        lines = self._am.SESSIONS_FILE.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_load_all_returns_summaries(self):
        s1 = self._make_summary("sid-1")
        s2 = self._make_summary("sid-2")
        self.mem.save(s1)
        self.mem.save(s2)
        all_summaries = self.mem.load_all()
        assert len(all_summaries) == 2
        assert all_summaries[0].session_id == "sid-1"
        assert all_summaries[1].session_id == "sid-2"

    def test_load_recent_limits(self):
        for i in range(5):
            s = self._make_summary(f"sid-{i}")
            self.mem.save(s)
        recent = self.mem.load_recent(limit=3)
        assert len(recent) == 3

    def test_load_recent_filters_workspace(self):
        ws_a = str(Path("/tmp").resolve() / "ws-a")
        ws_b = str(Path("/tmp").resolve() / "ws-b")
        s1 = self._make_summary("sid-1", workspace=ws_a)
        s2 = self._make_summary("sid-2", workspace=ws_b)
        self.mem.save(s1)
        self.mem.save(s2)
        recent = self.mem.load_recent(workspace=ws_a, limit=5)
        assert len(recent) == 1
        assert recent[0].session_id == "sid-1"

    def test_load_recent_newest_first(self):
        s1 = self._make_summary("sid-1")
        s1.timestamp = "2026-01-01T00:00:00Z"
        s2 = self._make_summary("sid-2")
        s2.timestamp = "2026-01-02T00:00:00Z"
        self.mem.save(s1)
        self.mem.save(s2)
        recent = self.mem.load_recent(limit=2)
        assert recent[0].session_id == "sid-2"  # newer
        assert recent[1].session_id == "sid-1"  # older

    def test_clear_removes_file(self):
        s = self._make_summary("sid-1")
        self.mem.save(s)
        assert self._am.SESSIONS_FILE.exists()
        self.mem.clear()
        assert not self._am.SESSIONS_FILE.exists()

    def test_format_for_prompt_empty(self):
        block = self.mem.format_for_prompt([])
        assert block == ""

    def test_format_for_prompt_non_empty(self):
        s = self._make_summary(
            "sid-1",
            summary="Did some work.",
            key_decisions=["Use X"],
            open_tasks=["Do Y"],
        )
        block = self.mem.format_for_prompt([s])
        assert "## Previous Session Context" in block
        assert "Did some work." in block
        assert "Use X" in block
        assert "Do Y" in block

    def test_rotation_drops_old_entries(self):
        import wisp.agent_memory as am
        orig_max = am._MAX_SUMMARIES
        am._MAX_SUMMARIES = 3
        try:
            for i in range(5):
                s = self._make_summary(f"sid-{i}")
                self.mem.save(s)
            all_summaries = self.mem.load_all()
            assert len(all_summaries) == 3
            assert all_summaries[0].session_id == "sid-2"
            assert all_summaries[-1].session_id == "sid-4"
        finally:
            am._MAX_SUMMARIES = orig_max

    def test_load_all_skips_corrupt_lines(self):
        self._am.SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._am.SESSIONS_FILE.write_text(
            '{"session_id": "good", "timestamp": "2026-01-01T00:00:00Z", "workspace": "/tmp", "summary": "ok"}\n'
            'this is not json\n'
            '{"session_id": "good2", "timestamp": "2026-01-01T00:00:00Z", "workspace": "/tmp", "summary": "ok2"}\n'
        )
        all_summaries = self.mem.load_all()
        assert len(all_summaries) == 2
        assert all_summaries[0].session_id == "good"
        assert all_summaries[1].session_id == "good2"
