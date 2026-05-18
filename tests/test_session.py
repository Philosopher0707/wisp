"""Tests for session.py — session CRUD, fragment resolution, formatting."""

import pytest
import json
from pathlib import Path
from wisp.session import Session, SessionManager, format_session_preview, SESSIONS_DIR


class TestSession:

    def test_create(self):
        s = Session.create("deepseek-v4", "/workspace", "hello world")
        assert s.model == "deepseek-v4"
        assert s.workspace == "/workspace"
        assert s.title == "hello world"
        assert s.id.endswith("-hello-world")
        assert s.messages == []

    def test_to_dict_roundtrip(self):
        s = Session.create("m", ".", "test")
        s.messages = [{"role": "user", "content": "hi"}]
        d = s.to_dict()
        s2 = Session.from_dict(d)
        assert s2.id == s.id
        assert s2.model == s.model
        assert s2.messages == s.messages

    def test_slugify_special_chars(self):
        from wisp.session import _slugify
        assert _slugify("Hello World!") == "hello-world"
        assert _slugify("  spaces  ") == "spaces"
        assert _slugify("a" * 100) == "a" * 40

    def test_touch(self):
        s = Session.create("m", ".", "t")
        before = s.updated_at
        s.touch()
        assert s.updated_at >= before


class TestSessionManager:

    @pytest.fixture(autouse=True)
    def isolate_sessions(self, tmp_path, monkeypatch):
        """Point sessions dir to a temp dir so tests don't touch ~/.config/wisp."""
        import wisp.session as sess_mod
        test_dir = tmp_path / "sessions"
        sess_mod.SESSIONS_DIR = test_dir
        yield

    @pytest.fixture
    def mgr(self):
        return SessionManager()

    def test_save_and_load(self, mgr):
        s = Session.create("model", ".", "test prompt")
        mgr.save(s)
        loaded = mgr.load(s.id)
        assert loaded is not None
        assert loaded.id == s.id
        assert loaded.model == "model"

    def test_load_nonexistent(self, mgr):
        assert mgr.load("nonexistent") is None

    def test_delete(self, mgr):
        s = Session.create("m", ".", "del")
        mgr.save(s)
        assert mgr.delete(s.id) is True
        assert mgr.load(s.id) is None

    def test_delete_nonexistent(self, mgr):
        assert mgr.delete("nope") is False

    def test_list_sessions(self, mgr):
        s1 = Session.create("m1", ".", "first")
        s2 = Session.create("m2", ".", "second")
        mgr.save(s1)
        mgr.save(s2)
        sessions = mgr.list_sessions()
        assert len(sessions) == 2

    def test_fragment_resolution_exact(self, mgr):
        s = Session.create("m", ".", "exact")
        mgr.save(s)
        resolved = mgr.get_session_id_from_fragment(s.id)
        assert resolved == s.id

    def test_fragment_resolution_partial(self, mgr):
        s = Session.create("m", ".", "partial test")
        mgr.save(s)
        # The ID format is YYYYMMDD-HHMMSS-partial-test
        # Fragment match on the date prefix
        prefix = s.id[:8]
        resolved = mgr.get_session_id_from_fragment(prefix)
        assert resolved == s.id

    def test_fragment_ambiguous(self, mgr):
        """Two sessions with same prefix should return None."""
        s1 = Session(id="20260430-120000-aaa", created_at="2026-04-30T12:00:00",
                      updated_at="2026-04-30T12:00:00", model="m", workspace=".")
        s2 = Session(id="20260430-120001-bbb", created_at="2026-04-30T12:00:01",
                      updated_at="2026-04-30T12:00:01", model="m", workspace=".")
        mgr.save(s1)
        mgr.save(s2)
        resolved = mgr.get_session_id_from_fragment("20260430")
        assert resolved is None  # ambiguous

    def test_list_sessions_limit(self, mgr):
        for i in range(5):
            s = Session.create("m", ".", f"session {i}")
            mgr.save(s)
        sessions = mgr.list_sessions(limit=3)
        assert len(sessions) <= 3

    def test_list_sessions_empty(self, mgr):
        assert mgr.list_sessions() == []


class TestFormatSessionPreview:

    def test_format_preview(self):
        s = Session.create("m", ".", "my session")
        s.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        preview = format_session_preview(s)
        assert "my session" in preview
        assert "hello" in preview
        assert "world" in preview
        assert s.id in preview


class TestSessionCompact:

    def test_compact_reduces_messages(self):
        s = Session.create("m", ".", "compact test")
        # Create 10 messages
        for i in range(5):
            s.messages.append({"role": "user", "content": f"prompt {i}"})
            s.messages.append({"role": "assistant", "content": f"response {i}"})
        assert len(s.messages) == 10

        result = s.compact(keep_recent=4)
        assert result["compacted"] is True
        assert result["before_count"] == 10
        assert result["after_count"] == 5  # 1 summary + 4 kept
        assert len(s.messages) == 5
        assert s.messages[0]["role"] == "system"
        assert s.messages[0].get("compacted") is True
        assert len(s.compaction_history) == 1

    def test_compact_skips_when_too_few(self):
        s = Session.create("m", ".", "small")
        s.messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = s.compact(keep_recent=6)
        assert result["compacted"] is False
        assert len(s.messages) == 2
        assert s.compaction_history == []

    def test_estimate_tokens(self):
        s = Session.create("m", ".", "token test")
        s.messages = [
            {"role": "user", "content": "a" * 400},
            {"role": "assistant", "content": "b" * 400},
        ]
        tokens = s.estimate_tokens(chars_per_token=4)
        assert tokens == 200  # 800 chars / 4

    def test_compact_roundtrip(self):
        s = Session.create("m", ".", "roundtrip")
        for i in range(10):
            s.messages.append({"role": "user", "content": f"msg {i}"})
        s.compact(keep_recent=4)
        d = s.to_dict()
        s2 = Session.from_dict(d)
        assert len(s2.compaction_history) == 1
        assert s2.compaction_history[0]["before_count"] == 10
        assert len(s2.messages) == 5

    def test_compact_preserves_recent(self):
        s = Session.create("m", ".", "preserve")
        # Build realistic turns: user → assistant
        for i in range(8):
            s.messages.append({"role": "user", "content": f"message {i}"})
            s.messages.append({"role": "assistant", "content": f"reply {i}"})
        result = s.compact(keep_recent=3)
        assert result["compacted"] is True
        # Turn-symmetry guard expands keep_recent=3 → 4 so window ends with assistant
        assert result["keep_recent"] == 4
        # Last 4 messages should be preserved verbatim
        assert s.messages[-4]["content"] == "message 6"
        assert s.messages[-3]["content"] == "reply 6"
        assert s.messages[-2]["content"] == "message 7"
        assert s.messages[-1]["content"] == "reply 7"

    def test_compact_symmetry_guard_with_tools(self):
        s = Session.create("m", ".", "tool test")
        s.messages.append({"role": "user", "content": "run test"})
        s.messages.append({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "run_bash", "arguments": {"command": "echo hi"}}}]})
        s.messages.append({"role": "tool", "content": "hi", "name": "run_bash"})
        s.messages.append({"role": "assistant", "content": "Done."})
        s.messages.append({"role": "user", "content": "next"})
        s.messages.append({"role": "assistant", "content": "Sure."})
        result = s.compact(keep_recent=2)
        assert result["compacted"] is True
        # keep_recent=2 starts with user("next") + assistant("Sure.") → valid
        assert result["keep_recent"] == 2
        assert s.messages[-2]["content"] == "next"
        assert s.messages[-1]["content"] == "Sure."

    def test_compact_symmetry_guard_adjusts_odd_window(self):
        s = Session.create("m", ".", "odd window")
        for i in range(6):
            s.messages.append({"role": "user", "content": f"u{i}"})
            s.messages.append({"role": "assistant", "content": f"a{i}"})
        # Ask to keep 3: would give [user, assistant, user] — bad symmetry
        result = s.compact(keep_recent=3)
        assert result["compacted"] is True
        # Guard expands to 4 so window is [user, assistant, user, assistant]
        assert result["keep_recent"] == 4
        assert s.messages[-4]["content"] == "u4"
        assert s.messages[-3]["content"] == "a4"
        assert s.messages[-2]["content"] == "u5"
        assert s.messages[-1]["content"] == "a5"

    def test_compact_handles_list_content(self):
        """Regression for compact() crash when assistant content is a list.

        OpenAI-format multimodal messages use ``[{"type":"text","text":"..."}]``
        instead of plain strings.  _is_complete_assistant() must call
        extract_text() before .lower().
        """
        s = Session.create("m", ".", "list content")
        # Build 12 messages alternating user/assistant with list content in last assistant
        for i in range(5):
            s.messages.append({"role": "user", "content": f"prompt {i}"})
            s.messages.append({"role": "assistant", "content": f"response {i}"})
        # Last turn: user then assistant with LIST content (the regression target)
        s.messages.append({"role": "user", "content": "fix the bug"})
        s.messages.append({
            "role": "assistant",
            "content": [{"type": "text", "text": "Done thinking."}],
        })
        assert len(s.messages) == 12
        # This used to raise: AttributeError: 'list' object has no attribute 'lower'
        result = s.compact(keep_recent=6)
        assert result["compacted"] is True
