"""Tests for wisp.summarizer — extractive session summarization."""

import pytest
from wisp.summarizer import ExtractiveSummarizer, SessionSummary


class TestExtractiveSummarizer:
    """Unit tests for the extractive summarizer heuristics."""

    def test_empty_messages_returns_none(self):
        summarizer = ExtractiveSummarizer()
        result = summarizer.summarize([], "sid", "/tmp")
        assert result is None

    def test_basic_summary_extraction(self):
        summarizer = ExtractiveSummarizer()
        messages = [
            {"role": "user", "content": "Can you help me refactor the auth module?"},
            {"role": "assistant", "content": "I'll refactor the auth module for you. First, I will extract the token validation logic into a separate function. Then I will add proper error handling. Finally, I will write tests for the new functions."},
            {"role": "user", "content": "Great, thanks!"},
        ]
        result = summarizer.summarize(messages, "sid", "/tmp")
        assert result is not None
        assert result.session_id == "sid"
        assert result.workspace == "/tmp"
        assert len(result.summary) > 0
        # Should contain action verbs
        assert any(verb in result.summary.lower() for verb in ["refactor", "extract", "add", "write"])

    def test_key_decisions_extraction(self):
        summarizer = ExtractiveSummarizer()
        messages = [
            {"role": "assistant", "content": "We decided to use the Dice coefficient for fuzzy matching because it has no dependencies."},
            {"role": "assistant", "content": "Let's go with tree-sitter as the primary parser and regex as fallback."},
            {"role": "user", "content": "Sounds good."},
        ]
        result = summarizer.summarize(messages, "sid", "/tmp")
        assert result is not None
        assert len(result.key_decisions) >= 1
        decisions_lower = " ".join(result.key_decisions).lower()
        assert "dice coefficient" in decisions_lower or "tree-sitter" in decisions_lower

    def test_user_preferences_extraction(self):
        summarizer = ExtractiveSummarizer()
        messages = [
            {"role": "user", "content": "I prefer Hindi-English mix in responses."},
            {"role": "assistant", "content": "Noted, I will use Hindi-English mix."},
            {"role": "user", "content": "I like detailed explanations with code examples."},
        ]
        result = summarizer.summarize(messages, "sid", "/tmp")
        assert result is not None
        assert len(result.user_preferences) >= 1
        prefs_lower = " ".join(result.user_preferences).lower()
        assert "prefer" in prefs_lower or "like" in prefs_lower

    def test_open_tasks_extraction(self):
        summarizer = ExtractiveSummarizer()
        messages = [
            {"role": "assistant", "content": "TODO: implement semantic search next."},
            {"role": "assistant", "content": "We still need to add auto-test on change."},
            {"role": "user", "content": "Ok."},
        ]
        result = summarizer.summarize(messages, "sid", "/tmp")
        assert result is not None
        assert len(result.open_tasks) >= 1
        tasks_lower = " ".join(result.open_tasks).lower()
        assert "semantic search" in tasks_lower or "auto-test" in tasks_lower

    def test_files_touched_extraction(self):
        summarizer = ExtractiveSummarizer()
        messages = [
            {"role": "assistant", "content": "I updated wisp/agent.py and wisp/tools.py."},
            {"role": "assistant", "content": "Also modified wisp/mcp.py for the new client."},
        ]
        result = summarizer.summarize(messages, "sid", "/tmp")
        assert result is not None
        assert len(result.files_touched) >= 2
        files_lower = [f.lower() for f in result.files_touched]
        assert any("agent.py" in f for f in files_lower)
        assert any("tools.py" in f for f in files_lower)

    def test_deduplication(self):
        summarizer = ExtractiveSummarizer()
        messages = [
            {"role": "assistant", "content": "I implemented the feature. I implemented the feature again."},
        ]
        result = summarizer.summarize(messages, "sid", "/tmp")
        assert result is not None
        # Should not duplicate identical sentences
        assert result.summary.count("implemented") <= 2

    def test_similar_dedup(self):
        summarizer = ExtractiveSummarizer()
        a = "I updated the auth module."
        b = "I updated the auth module"
        assert summarizer._similar(a, b)

    def test_score_sentence_position_bonus(self):
        summarizer = ExtractiveSummarizer()
        # Last message (conclusion) should score higher than first message (boilerplate intro)
        score_first = summarizer._score_sentence("First sentence.", msg_idx=0, sent_idx=0, total_msgs=3)
        score_last = summarizer._score_sentence("Conclusion sentence.", msg_idx=2, sent_idx=0, total_msgs=3)
        assert score_last > score_first

    def test_score_sentence_action_verb_bonus(self):
        summarizer = ExtractiveSummarizer()
        score_action = summarizer._score_sentence("I implemented the feature.", msg_idx=0, sent_idx=0)
        score_plain = summarizer._score_sentence("This is a plain sentence.", msg_idx=0, sent_idx=0)
        assert score_action > score_plain

    def test_dedup_limit(self):
        summarizer = ExtractiveSummarizer()
        items = ["a", "a", "b", "b", "c", "c", "d", "e", "f"]
        result = summarizer._dedup_limit(items, 3)
        assert len(result) <= 3
        assert "a" in result
        assert "b" in result


class TestSessionSummary:
    """Tests for the SessionSummary dataclass."""

    def test_to_dict_roundtrip(self):
        summary = SessionSummary(
            session_id="test-id",
            timestamp="2026-01-01T00:00:00Z",
            workspace="/tmp",
            summary="Did some work.",
            key_decisions=["Use X"],
            user_preferences=["Prefer Y"],
            open_tasks=["Do Z"],
            files_touched=["a.py"],
            thread_stack=[{"topic": "auth refactor", "status": "INCOMPLETE"}],
        )
        d = summary.to_dict()
        restored = SessionSummary.from_dict(d)
        assert restored.session_id == "test-id"
        assert restored.summary == "Did some work."
        assert restored.key_decisions == ["Use X"]
        assert restored.thread_stack == [{"topic": "auth refactor", "status": "INCOMPLETE"}]

    def test_extract_thread_stack_complete(self):
        summarizer = ExtractiveSummarizer()
        messages = [
            {"role": "user", "content": "Explain Python decorators."},
            {"role": "assistant", "content": "Python decorators are functions that wrap other functions to extend their behavior. They use the @ syntax."},
        ]
        stack = summarizer._extract_thread_stack(messages)
        assert len(stack) == 1
        assert stack[0]["status"] == "COMPLETE"
        assert "decorators" in stack[0]["topic"].lower()

    def test_extract_thread_stack_incomplete(self):
        summarizer = ExtractiveSummarizer()
        messages = [
            {"role": "user", "content": "Explain Python decorators."},
            {"role": "assistant", "content": "Here are the steps: first, we define a wrapper function"},
        ]
        stack = summarizer._extract_thread_stack(messages)
        assert len(stack) == 1
        assert stack[0]["status"] == "INCOMPLETE"
        assert "steps" in stack[0]["topic"].lower()

    def test_extract_thread_stack_no_assistant(self):
        summarizer = ExtractiveSummarizer()
        messages = [
            {"role": "user", "content": "Hello?"},
        ]
        stack = summarizer._extract_thread_stack(messages)
        assert stack == []
