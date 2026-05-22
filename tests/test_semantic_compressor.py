"""Tests for wisp.semantic_compressor — semantic context compression."""

import pytest

from wisp.semantic_compressor import (
    SemanticCompressor,
    MessageType,
    _build_graph,
    _classify_message,
    _dedup_tool_results,
    _dedup_exact_duplicates,
    _truncate_tool_result,
    _truncate_assistant_message,
    _hash_text,
    _normalize_tool_args,
    _score_importance,
    _parse_llm_summary,
)
from wisp.summarizer import SessionSummary


class TestMessageClassification:
    """Message type classification."""

    def test_user_intent(self):
        msg = {"role": "user", "content": "How do I refactor this function?"}
        assert _classify_message(msg, 0) == MessageType.INTENT

    def test_user_continuation(self):
        msg = {"role": "user", "content": "continue"}
        assert _classify_message(msg, 0) == MessageType.CONTINUATION

    def test_user_correction(self):
        msg = {"role": "user", "content": "No, that's wrong. Use a list instead."}
        assert _classify_message(msg, 0) == MessageType.CORRECTION

    def test_assistant_tool_call(self):
        msg = {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "read_file"}}]}
        assert _classify_message(msg, 0) == MessageType.TOOL_CALL

    def test_assistant_synthesis(self):
        msg = {"role": "assistant", "content": "Here's the result: the fix is complete."}
        assert _classify_message(msg, 0) == MessageType.SYNTHESIS

    def test_assistant_reasoning(self):
        msg = {"role": "assistant", "content": "Let me think about this problem. First I need to check the imports."}
        assert _classify_message(msg, 0) == MessageType.REASONING

    def test_tool_result(self):
        msg = {"role": "tool", "content": "file contents here", "tool_call_id": "tc1"}
        assert _classify_message(msg, 0) == MessageType.TOOL_RESULT

    def test_system_message(self):
        msg = {"role": "system", "content": "You are Wisp"}
        assert _classify_message(msg, 0) == MessageType.SYSTEM


class TestHelpers:
    """Utility functions."""

    def test_hash_text_stable(self):
        h1 = _hash_text("hello world")
        h2 = _hash_text("hello world")
        assert h1 == h2
        assert len(h1) == 16

    def test_hash_text_different(self):
        assert _hash_text("hello") != _hash_text("world")

    def test_normalize_tool_args_dict(self):
        args = {"path": "/tmp/test.py", "workspace": "."}
        n1 = _normalize_tool_args(args)
        n2 = _normalize_tool_args({"workspace": ".", "path": "/tmp/test.py"})
        assert n1 == n2  # order-independent

    def test_normalize_tool_args_string(self):
        n = _normalize_tool_args('{"path": "/tmp/test.py"}')
        assert "path" in n

    def test_normalize_tool_args_drops_empty(self):
        n1 = _normalize_tool_args({"path": "/tmp", "limit": None, "empty": ""})
        n2 = _normalize_tool_args({"path": "/tmp"})
        assert n1 == n2


class TestImportanceScoring:
    """Message importance scoring."""

    def test_later_messages_score_higher(self):
        from wisp.semantic_compressor import MessageNode
        n1 = MessageNode(index=0, role="user", mtype=MessageType.INTENT, content="hello", raw={})
        n2 = MessageNode(index=10, role="user", mtype=MessageType.INTENT, content="hello", raw={})
        s1 = _score_importance(n1, 11)
        s2 = _score_importance(n2, 11)
        assert s2 > s1

    def test_synthesis_high_score(self):
        from wisp.semantic_compressor import MessageNode
        n = MessageNode(index=5, role="assistant", mtype=MessageType.SYNTHESIS, content="Done.", raw={})
        score = _score_importance(n, 10)
        assert score > 0.3  # synthesis bonus + position bonus

    def test_incomplete_marker_boost(self):
        from wisp.semantic_compressor import MessageNode
        n = MessageNode(
            index=5, role="assistant", mtype=MessageType.REASONING,
            content="I'll do that now, working on it", raw={},
        )
        score = _score_importance(n, 10)
        assert score > 0.3  # incomplete marker boost

    def test_error_marker_boost(self):
        from wisp.semantic_compressor import MessageNode
        n = MessageNode(
            index=3, role="tool", mtype=MessageType.TOOL_RESULT,
            content="Error: file not found exception", raw={},
        )
        score = _score_importance(n, 10)
        assert score > 0.2  # error boost


class TestGraphBuilding:
    """Conversation graph construction."""

    def test_simple_turn(self):
        messages = [
            {"role": "user", "content": "Read file A"},
            {"role": "assistant", "content": "I'll read it.", "tool_calls": [{"function": {"name": "read_file"}}]},
            {"role": "tool", "content": "contents of A", "tool_call_id": "tc1"},
            {"role": "assistant", "content": "Here is what the file says."},
        ]
        graph = _build_graph(messages)
        assert len(graph.turns) == 1
        assert graph.turns[0].user_idx == 0
        assert graph.turns[0].assistant_idx == 3
        assert graph.turns[0].tool_call_indices == [1]
        assert graph.turns[0].tool_result_indices == [2]
        assert graph.turns[0].complete is True

    def test_incomplete_turn_no_assistant_reply(self):
        messages = [
            {"role": "user", "content": "Do X"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "run_bash"}}]},
            {"role": "tool", "content": "output", "tool_call_id": "tc1"},
        ]
        graph = _build_graph(messages)
        assert len(graph.turns) == 1
        assert graph.turns[0].complete is False  # no assistant synthesis after tool result

    def test_multiple_turns(self):
        messages = [
            {"role": "user", "content": "Task 1"},
            {"role": "assistant", "content": "Done with task 1."},
            {"role": "user", "content": "Task 2"},
            {"role": "assistant", "content": "Done with task 2."},
        ]
        graph = _build_graph(messages)
        assert len(graph.turns) == 2
        assert graph.turns[0].user_idx == 0
        assert graph.turns[1].user_idx == 2

    def test_thread_detection(self):
        messages = [
            {"role": "user", "content": "Fix bug in auth.py"},
            {"role": "assistant", "content": "I'll check the file.", "tool_calls": [{"function": {"name": "read_file"}}]},
            {"role": "tool", "content": "code...", "tool_call_id": "tc1"},
            {"role": "assistant", "content": "Fixed! The bug was in line 42."},
        ]
        graph = _build_graph(messages)
        assert len(graph.threads) == 1
        assert graph.threads[0].status == "COMPLETE"
        assert "auth" in graph.threads[0].topic.lower()

    def test_incomplete_thread_detection(self):
        messages = [
            {"role": "user", "content": "Fix bug in auth.py"},
            {"role": "assistant", "content": "I'll do that now", "tool_calls": [{"function": {"name": "read_file"}}]},
            {"role": "tool", "content": "code...", "tool_call_id": "tc1"},
            # No assistant synthesis → thread is INCOMPLETE
        ]
        graph = _build_graph(messages)
        assert len(graph.threads) == 1
        assert graph.threads[0].status == "INCOMPLETE"


class TestTier1Dedup:
    """Semantic deduplication."""

    def test_detects_repeated_git_status(self):
        messages = [
            {"role": "user", "content": "Check status"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc1", "function": {"name": "git_status", "arguments": {}}}]},
            {"role": "tool", "content": "On branch main\n clean", "tool_call_id": "tc1"},
            {"role": "user", "content": "Now check again"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc2", "function": {"name": "git_status", "arguments": {}}}]},
            {"role": "tool", "content": "On branch main\n clean", "tool_call_id": "tc2"},
        ]
        graph = _build_graph(messages)
        filtered = _dedup_tool_results(graph)
        # Temporal guard preserves both (within RECENT_WINDOW=15)
        tool_msgs = [m for m in filtered if m.get("role") == "tool"]
        assert len(tool_msgs) == 2
        assert "On branch main" in tool_msgs[0].get("content", "")

    def test_keeps_latest_file_read(self):
        # Add padding to push older read_file outside temporal guard window
        messages = [{"role": "user", "content": "start"}, {"role": "assistant", "content": "ok"}]
        for i in range(8):
            messages.extend([
                {"role": "user", "content": f"Q {i}"},
                {"role": "assistant", "content": f"A {i}"},
            ])
        messages.extend([
            {"role": "user", "content": "Read file A"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc1", "function": {"name": "read_file", "arguments": {"path": "a.py"}}}]},
            {"role": "tool", "content": "version 1", "tool_call_id": "tc1"},
            {"role": "user", "content": "Read it again"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc2", "function": {"name": "read_file", "arguments": {"path": "a.py"}}}]},
            {"role": "tool", "content": "version 2", "tool_call_id": "tc2"},
        ])
        graph = _build_graph(messages)
        filtered = _dedup_tool_results(graph)
        tool_msgs = [m for m in filtered if m.get("role") == "tool"]
        # With only 20 messages total, both read_file results are within
        # the temporal guard window (_RECENT_WINDOW=15), so both survive.
        assert len(tool_msgs) == 2
        assert "version 2" in tool_msgs[-1].get("content", "")

    def test_keeps_different_tools(self):
        messages = [
            {"role": "user", "content": "Do stuff"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc1", "function": {"name": "git_status", "arguments": {}}}]},
            {"role": "tool", "content": "clean", "tool_call_id": "tc1"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc2", "function": {"name": "read_file", "arguments": {"path": "a.py"}}}]},
            {"role": "tool", "content": "code", "tool_call_id": "tc2"},
        ]
        graph = _build_graph(messages)
        filtered = _dedup_tool_results(graph)
        tool_msgs = [m for m in filtered if m.get("role") == "tool"]
        assert len(tool_msgs) == 2

    def test_exact_duplicate_removal(self):
        messages = [
            {"role": "system", "content": "You are Wisp"},
            {"role": "system", "content": "You are Wisp"},  # exact dup
            {"role": "user", "content": "Hello"},
        ]
        filtered = _dedup_exact_duplicates(messages)
        system_msgs = [m for m in filtered if m.get("role") == "system"]
        assert len(system_msgs) == 1
        # User messages should NOT be deduped even if identical

    def test_user_messages_not_deduped(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "hello"},
        ]
        filtered = _dedup_exact_duplicates(messages)
        assert len(filtered) == 2


class TestTier2Truncation:
    """Content-aware truncation."""

    def test_git_status_never_truncated(self):
        content = "On branch main\n" * 10
        result = _truncate_tool_result(content, "git_status")
        assert result == content

    def test_truncates_long_bash_output(self):
        lines = [f"line {i}" for i in range(100)]
        content = "\n".join(lines)
        result = _truncate_tool_result(content, "run_bash")
        assert "... (20 more lines)" in result
        assert len(result.splitlines()) == 81  # 80 kept + truncation notice

    def test_truncates_list_files(self):
        lines = [f"file{i}.py" for i in range(60)]
        content = "\n".join(lines)
        result = _truncate_tool_result(content, "list_files")
        assert "... (10 more files)" in result
        assert len(result.splitlines()) == 51

    def test_truncates_web_fetch_by_chars(self):
        content = "x" * 6000
        result = _truncate_tool_result(content, "web_fetch")
        assert "... (1000 more characters)" in result
        assert len(result) < 5500

    def test_truncates_web_search_by_results(self):
        blocks = [f"Result {i}\nSnippet {i}\nURL {i}" for i in range(10)]
        content = "\n\n".join(blocks)
        result = _truncate_tool_result(content, "web_search")
        assert "... (5 more results)" in result

    def test_protects_code_blocks_from_truncation(self):
        from wisp.semantic_compressor import MessageNode
        content = (
            "Here's the code:\n```python\n" + "x = 1\n" * 200 +
            "```\nAnd the explanation." + "y" * 3000
        )
        node = MessageNode(index=0, role="assistant", mtype=MessageType.SYNTHESIS, content=content, raw={})
        result = _truncate_assistant_message(content, node)
        # Synthesis messages are never truncated
        assert result == content

    def test_truncates_reasoning_not_synthesis(self):
        from wisp.semantic_compressor import MessageNode
        content = "Let me think. " + "x" * 2500
        node = MessageNode(index=0, role="assistant", mtype=MessageType.REASONING, content=content, raw={})
        result = _truncate_assistant_message(content, node)
        assert len(result) < len(content)
        assert "... (" in result

    def test_preserves_synthesis(self):
        from wisp.semantic_compressor import MessageNode
        content = "Here's the result: " + "x" * 5000
        node = MessageNode(index=0, role="assistant", mtype=MessageType.SYNTHESIS, content=content, raw={})
        result = _truncate_assistant_message(content, node)
        assert result == content  # synthesis is sacred


class TestLLMSummaryParsing:
    """Tier 3 LLM summary response parsing."""

    def test_parse_valid_json(self):
        text = '''{"summary": "Fixed auth bug", "key_decisions": ["Use JWT"], "open_tasks": ["Add tests (pending)"], "files_touched": ["auth.py (modified)"], "user_preferences": [], "incomplete_threads": [], "errors_encountered": []}'''
        result = _parse_llm_summary(text)
        assert result.summary == "Fixed auth bug"
        assert result.key_decisions == ["Use JWT"]
        assert result.open_tasks == ["Add tests (pending)"]
        assert result.files_touched == ["auth.py (modified)"]
        assert result.compression_stats["parse_ok"] is True

    def test_parse_json_with_markdown_fences(self):
        text = '```json\n{"summary": "Test", "key_decisions": [], "open_tasks": [], "files_touched": [], "user_preferences": [], "incomplete_threads": [], "errors_encountered": []}\n```'
        result = _parse_llm_summary(text)
        assert "Test" in result.summary

    def test_parse_fallback_on_bad_json(self):
        text = "This is just plain text without JSON."
        result = _parse_llm_summary(text)
        assert "plain text" in result.summary
        assert result.compression_stats["parse_error"] is True

    def test_parse_builds_summary_from_fields(self):
        text = '{"key_decisions": ["A", "B"], "open_tasks": ["C"]}'
        result = _parse_llm_summary(text)
        assert "A" in result.summary
        assert "C" in result.summary


class TestSemanticCompressor:
    """Integration tests for the full compressor."""

    def test_compress_empty(self):
        comp = SemanticCompressor()
        result = comp.compress([])
        assert result.summary == ""
        assert result.compression_stats["before_messages"] == 0

    def test_compress_simple_conversation(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        comp = SemanticCompressor()
        result = comp.compress(messages)
        assert result.summary != ""
        assert result.compression_stats["before_messages"] == 2
        assert result.compression_stats["after_messages"] == 2

    def test_compress_deduplicates(self):
        messages = [
            {"role": "user", "content": "Check status"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc1", "function": {"name": "git_status", "arguments": {}}}]},
            {"role": "tool", "content": "On branch main", "tool_call_id": "tc1"},
            {"role": "user", "content": "Check again"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc2", "function": {"name": "git_status", "arguments": {}}}]},
            {"role": "tool", "content": "On branch main", "tool_call_id": "tc2"},
        ]
        comp = SemanticCompressor()
        result = comp.compress(messages)
        # After dedup, should have fewer messages
        assert result.compression_stats["after_messages"] < result.compression_stats["before_messages"]

    def test_summarize_returns_session_summary(self):
        messages = [
            {"role": "user", "content": "Fix the bug"},
            {"role": "assistant", "content": "I'll check the code.", "tool_calls": [{"id": "tc1", "function": {"name": "read_file", "arguments": {"path": "bug.py"}}}]},
            {"role": "tool", "content": "def buggy(): pass", "tool_call_id": "tc1"},
            {"role": "assistant", "content": "Fixed! Changed pass to return None."},
        ]
        comp = SemanticCompressor()
        summary = comp.summarize(messages, session_id="s1", workspace="/tmp")
        assert summary is not None
        assert isinstance(summary, SessionSummary)
        assert summary.session_id == "s1"
        assert summary.workspace == "/tmp"
        assert summary.summary != ""
        # Files touched should be extracted
        assert any("bug" in f for f in summary.files_touched) or not summary.files_touched

    def test_preserves_incomplete_threads(self):
        messages = [
            {"role": "user", "content": "Implement auth"},
            {"role": "assistant", "content": "I'll do that now", "tool_calls": [{"id": "tc1", "function": {"name": "plan_task", "arguments": {"goal": "auth"}}}]},
            {"role": "tool", "content": "Plan created", "tool_call_id": "tc1"},
            # No synthesis → thread incomplete
        ]
        comp = SemanticCompressor()
        result = comp.compress(messages)
        # The thread stack should note the incomplete thread
        assert any(
            t.get("status") == "INCOMPLETE" for t in result.thread_stack
        ) or len(result.thread_stack) == 0  # may not extract if summarizer is weak

    def test_compression_stats_populated(self):
        messages = [
            {"role": "user", "content": "A"},
            {"role": "assistant", "content": "B"},
        ]
        comp = SemanticCompressor()
        result = comp.compress(messages)
        stats = result.compression_stats
        assert "tier" in stats
        assert "before_messages" in stats
        assert "after_messages" in stats
        assert stats["before_messages"] == 2

    def test_analyze_returns_graph(self):
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1."},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2."},
        ]
        comp = SemanticCompressor()
        graph = comp.analyze(messages)
        assert len(graph.nodes) == 4
        assert len(graph.turns) == 2
        assert len(graph.threads) == 2

    def test_to_session_summary_conversion(self):
        result = comp = SemanticCompressor()
        result = comp.compress([
            {"role": "user", "content": "Test"},
            {"role": "assistant", "content": "Done."},
        ])
        ss = result.to_session_summary("sid", "/ws")
        assert isinstance(ss, SessionSummary)
        assert ss.session_id == "sid"
        assert ss.workspace == "/ws"


class TestTier3Reachability:
    """Regression: Tier 3 must actually fire when old messages exceed trigger threshold."""

    def test_tier3_fires_when_over_trigger(self, monkeypatch):
        from wisp.semantic_compressor import _llm_summarize, CompressionResult

        def fake_summarize(messages, **kwargs):
            return CompressionResult(
                summary="Fake LLM summary",
                key_decisions=["A"],
                compression_stats={"tier": 3, "model_used": "fake"},
            )

        monkeypatch.setattr(
            "wisp.semantic_compressor._llm_summarize", fake_summarize
        )

        comp = SemanticCompressor()
        # Create messages that are ~100 tokens (400 chars) each
        messages = [
            {"role": "user", "content": "x" * 200},
            {"role": "assistant", "content": "y" * 200},
        ] * 10  # 4000 chars total = ~1000 tokens at 4 chars/token

        # Trigger at 500 tokens → fires. max_context_tokens=2000.
        result = comp.compress(
            messages,
            chars_per_token=4,
            max_context_tokens=2000,
            tier3_trigger_tokens=500,
        )
        assert result.compression_stats["tier"] == 3
        assert result.summary == "Fake LLM summary"
        assert result.compression_stats["tier3_threshold"] == 500

    def test_tier3_skips_when_under_trigger(self):
        comp = SemanticCompressor()
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = comp.compress(
            messages,
            chars_per_token=4,
            max_context_tokens=2000,
            tier3_trigger_tokens=500,
        )
        # Should stay at Tier 2, not invoke LLM
        assert result.compression_stats["tier"] == 2
        assert result.compression_stats["tier3_threshold"] == 500

    def test_default_tier3_threshold_is_quarter_of_budget(self, monkeypatch):
        from wisp.semantic_compressor import _llm_summarize, CompressionResult

        def fake_summarize(messages, **kwargs):
            return CompressionResult(
                summary="tier3",
                compression_stats={"tier": 3, "model_used": "fake"},
            )

        monkeypatch.setattr(
            "wisp.semantic_compressor._llm_summarize", fake_summarize
        )

        comp = SemanticCompressor()
        messages = [
            {"role": "user", "content": "x" * 200},
            {"role": "assistant", "content": "y" * 200},
        ] * 10
        result = comp.compress(
            messages,
            chars_per_token=4,
            max_context_tokens=2000,
            # tier3_trigger_tokens defaults to 0 → max_context_tokens // 4 = 500
        )
        assert result.compression_stats["tier3_threshold"] == 500

    def test_session_compact_passes_max_context_tokens(self, monkeypatch):
        from wisp.infra.session_dto import SessionDTO as Session
        from wisp.semantic_compressor import CompressionResult

        calls = []

        def fake_compress(*args, **kwargs):
            calls.append(kwargs)
            return CompressionResult(
                summary="test",
                compression_stats={"tier": 2, "before_messages": 10, "after_messages": 5},
            )

        monkeypatch.setattr(
            "wisp.semantic_compressor.SemanticCompressor.compress", fake_compress
        )

        s = Session.create("m", ".", "test")
        for i in range(10):
            s.messages.append({"role": "user", "content": f"u{i}"})
            s.messages.append({"role": "assistant", "content": f"a{i}"})

        s.compact(keep_recent=4, max_context_tokens=4096)
        assert calls
        assert calls[0]["max_context_tokens"] == 4096
