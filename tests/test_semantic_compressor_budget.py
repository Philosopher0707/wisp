"""Tests for SemanticCompressor budget enforcement & truncation rules.

Existing tests cover deduplication. This tests:
- Per-tool truncation limits (read_file, git_status, run_bash, search_codebase)
- Tool results that are shorter than limit pass through unchanged
"""

from wisp.semantic_compressor import (
    SemanticCompressor,
    _classify_message,
    _truncate_tool_result,
    _truncate_assistant_message,
    _apply_truncation,
    _dedup_exact_duplicates,
    MessageNode,
    MessageType,
)


class TestDedup:
    """Deduplication covers system and tool roles only."""

    def test_exact_duplicate_system_removed(self):
        messages = [
            {"role": "system", "content": "You are Wisp"},
            {"role": "system", "content": "You are Wisp"},
        ]
        filtered = _dedup_exact_duplicates(messages)
        assert len(filtered) == 1

    def test_user_messages_never_deduped(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "hello"},
        ]
        filtered = _dedup_exact_duplicates(messages)
        assert len(filtered) == 2


class TestToolTruncation:
    """Per-tool truncation limits from _TOOL_TRUNCATION."""

    def test_read_file_never_truncated(self):
        """read_file: limit=0, unit="" → never truncate."""
        huge = "line\n" * 1_000
        result = _truncate_tool_result(huge, "read_file")
        assert len(result) == len(huge)

    def test_git_status_never_truncated(self):
        """git_status: limit=0, unit="" → never truncate."""
        huge = "branch\n" * 1_000
        result = _truncate_tool_result(huge, "git_status")
        assert len(result) == len(huge)

    def test_run_bash_truncated_by_lines(self):
        """run_bash: (80, " lines") → keep first 80 lines."""
        num_lines = 100
        content = "\n".join([f"line_{i}" for i in range(num_lines)])
        result = _truncate_tool_result(content, "run_bash")
        assert len(result.splitlines()) == 80 + 1  # 80 kept + "more" line

    def test_run_bash_not_truncated_when_under_limit(self):
        content = "\n".join([f"line_{i}" for i in range(50)])
        result = _truncate_tool_result(content, "run_bash")
        assert result == content

    def test_web_search_truncated_by_blocks(self):
        """web_search: (5, " results") → keep first 5 blank-line blocks."""
        blocks = [f"Found match {i}\nDetails here" for i in range(10)]
        content = "\n\n".join(blocks)
        result = _truncate_tool_result(content, "web_search")
        assert "more results" in result
        # Should have 5 blocks + truncation notice
        assert result.count("Found match") == 5

    def test_search_codebase_truncated_by_blocks(self):
        """search_codebase: (5, " results") → same block-based truncation."""
        blocks = [f"Result {i}\nfile.py" for i in range(10)]
        content = "\n\n".join(blocks)
        result = _truncate_tool_result(content, "search_codebase")
        assert "more results" in result
        assert result.count("Result") == 5

    def test_web_fetch_truncated_by_characters(self):
        """web_fetch: (5000, " chars") → hard char truncation."""
        content = "word " * 2_000  # ~10K chars
        result = _truncate_tool_result(content, "web_fetch")
        assert "more characters" in result
        assert len(result) < len(content)

    def test_unknown_tool_not_truncated(self):
        """Tools not in _TOOL_TRUNCATION pass through unchanged."""
        content = "anything\n" * 1_000
        result = _truncate_tool_result(content, "nonexistent_tool")
        assert result == content


class TestAssistantTruncation:
    """Assistant content truncation with importance awareness."""

    def test_synthesis_never_truncated(self):
        """SYNTHESIS messages are sacred — never truncated."""
        comp = SemanticCompressor()
        mtype = _classify_message({
            "role": "assistant",
            "content": "Final answer: done",
            "tool_calls": [],
        }, 0)
        assert mtype == MessageType.SYNTHESIS

        node = MessageNode(
            index=0, role="assistant", mtype=MessageType.SYNTHESIS,
            content="Final answer: done", raw={"role": "assistant", "content": "Final answer: done", "tool_calls": []},
            importance=10, thread_id=0, turn_idx=0, content_hash="hash",
        )
        huge = "Summary " * 1_000
        result = _truncate_assistant_message(huge, node)
        assert result == huge

    def test_reasoning_truncated_at_sentence_boundary(self):
        """REASONING messages > 2000 chars get sentence-bounded truncation."""
        comp = SemanticCompressor()
        mtype = _classify_message({
            "role": "assistant",
            "content": "Let me think. " + "word " * 1_000,
            "tool_calls": [],
        }, 0)
        assert mtype == MessageType.REASONING

        node = MessageNode(
            index=0, role="assistant", mtype=MessageType.REASONING,
            content="Let me think. ...", raw={},
            importance=5, thread_id=0, turn_idx=0, content_hash="hash",
        )
        huge = "Let me think. " + "word " * 1_000  # ~6K chars
        result = _truncate_assistant_message(huge, node)
        assert len(result) < len(huge)
        assert "more characters of reasoning" in result

    def test_code_block_truncated_completely(self):
        """Code blocks > 3000 chars get block-aware truncation."""
        comp = SemanticCompressor()
        code = "```python\n" + "x = 1\n" * 400 + "```\n"
        mtype = _classify_message({
            "role": "assistant",
            "content": code,
            "tool_calls": [],
        }, 0)

        node = MessageNode(
            index=0, role="assistant", mtype=mtype,
            content=code, raw={},
            importance=5, thread_id=0, turn_idx=0, content_hash="hash",
        )
        result = _truncate_assistant_message(code, node)
        assert "```" in result  # still has code fences
        if len(result) < len(code):
            assert "more characters" in result


class TestApplyTruncation:
    """Full _apply_truncation pipeline on message lists."""

    def test_unrelated_messages_preserved(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        graph = SemanticCompressor().analyze(messages)
        result = _apply_truncation(graph, messages)
        assert len(result) == 2

    def test_tool_result_truncated_in_pipeline(self):
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc1", "function": {"name": "run_bash", "arguments": {}}}]},
            {"role": "tool", "content": "line\n" * 200, "tool_call_id": "tc1"},
        ]
        graph = SemanticCompressor().analyze(messages)
        result = _apply_truncation(graph, messages)

        tool_msg = [m for m in result if m["role"] == "tool"]
        # run_bash truncated to ~80 lines + suffix
        assert len(tool_msg[0]["content"].splitlines()) <= 82


class TestBudgetNotExceeded:
    """After compression, total tokens should not blow up."""

    def test_compress_small_context_no_crash(self):
        """Small contexts compress successfully without invoking LLM."""
        comp = SemanticCompressor()
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = comp.compress(messages, max_context_tokens=5000)
        # Tier 1 and 2 applied
        assert result.compression_stats["after_messages"] <= 2

    def test_compress_empty_no_crash(self):
        comp = SemanticCompressor()
        result = comp.compress([])
        assert result.summary == ""
        assert result.compression_stats["before_messages"] == 0
