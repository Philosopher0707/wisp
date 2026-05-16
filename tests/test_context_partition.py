"""Tests for wisp.multi_agent.context_partition."""

import pytest
from wisp.multi_agent.context_partition import ContextPartitioner, partition_context


class TestContextPartitioner:
    """Test the ContextPartitioner class."""

    def test_empty_messages(self):
        """Partitioning empty messages should return empty list."""
        cp = ContextPartitioner()
        result = cp.partition([], "test task")
        assert result == []

    def test_single_user_message(self):
        """Single user message should be included."""
        cp = ContextPartitioner()
        messages = [{"role": "user", "content": "Hello"}]
        result = cp.partition(messages, "test task")
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_system_message_always_included(self):
        """System messages should always be included."""
        cp = ContextPartitioner()
        messages = [
            {"role": "system", "content": "You are a coding agent"},
            {"role": "user", "content": "Hello"},
        ]
        result = cp.partition(messages, "test task")
        assert any(m["role"] == "system" for m in result)

    def test_system_message_excluded_when_flag_false(self):
        """System messages excluded when include_system=False."""
        cp = ContextPartitioner()
        messages = [
            {"role": "system", "content": "You are a coding agent"},
            {"role": "user", "content": "Hello"},
        ]
        result = cp.partition(messages, "test task", include_system=False)
        assert not any(m["role"] == "system" for m in result)

    def test_max_messages_limit(self):
        """Should not exceed max_messages + 1 (last user message always added)."""
        cp = ContextPartitioner(max_messages=3)
        messages = [
            {"role": "user", "content": f"Message {i}"}
            for i in range(10)
        ]
        result = cp.partition(messages, "test task")
        # max_messages=3 but last user message is always added, so <= 4
        assert len(result) <= 4

    def test_last_user_message_always_included(self):
        """Last user message should always be in result."""
        cp = ContextPartitioner(max_messages=2)
        messages = [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Response"},
            {"role": "user", "content": "Last question"},
        ]
        result = cp.partition(messages, "test task")
        assert any(
            m["role"] == "user" and m["content"] == "Last question"
            for m in result
        )

    def test_file_mention_scoring(self):
        """Messages mentioning task files should score higher."""
        cp = ContextPartitioner(max_messages=2)
        messages = [
            {"role": "user", "content": "Fix auth.py"},
            {"role": "assistant", "content": "Looking at auth.py"},
            {"role": "user", "content": "Also check login.py"},
            {"role": "assistant", "content": "Done"},
        ]
        result = cp.partition(messages, "Fix auth.py and login.py")
        # Should include messages mentioning auth.py or login.py
        contents = [m["content"] for m in result]
        assert any("auth.py" in c for c in contents)

    def test_tool_mention_scoring(self):
        """Messages mentioning task tools should score higher."""
        cp = ContextPartitioner(max_messages=2)
        messages = [
            {"role": "user", "content": "Use read_file to check main.py"},
            {"role": "assistant", "content": "OK"},
            {"role": "user", "content": "Run tests"},
        ]
        result = cp.partition(messages, "read_file and edit_file")
        contents = [m["content"] for m in result]
        assert any("read_file" in c for c in contents)

    def test_keyword_overlap_scoring(self):
        """Messages with keyword overlap should score higher."""
        cp = ContextPartitioner(max_messages=2)
        messages = [
            {"role": "user", "content": "Refactor the authentication module"},
            {"role": "assistant", "content": "Sure"},
            {"role": "user", "content": "Also update tests"},
        ]
        result = cp.partition(messages, "refactor authentication")
        contents = [m["content"] for m in result]
        assert any("Refactor" in c for c in contents)

    def test_order_preserved(self):
        """Selected messages should preserve original order."""
        cp = ContextPartitioner(max_messages=3)
        messages = [
            {"role": "system", "content": "Sys"},
            {"role": "user", "content": "A"},
            {"role": "assistant", "content": "B"},
            {"role": "user", "content": "C"},
        ]
        result = cp.partition(messages, "test")
        # Check that indices are in ascending order
        indices = [messages.index(m) for m in result]
        assert indices == sorted(indices)

    def test_score_capped_at_1(self):
        """Score should be capped at 1.0."""
        cp = ContextPartitioner()
        msg = {
            "role": "system",
            "content": "read_file auth.py write_file auth.py " * 100,
        }
        score = cp._score_message(
            msg, "read_file auth.py", {"auth.py"}, {"read_file"}
        )
        assert score <= 1.0


class TestPartitionContext:
    """Test the partition_context convenience function."""

    def test_basic_usage(self):
        """Basic usage should work."""
        messages = [
            {"role": "system", "content": "Sys"},
            {"role": "user", "content": "Hello"},
        ]
        result = partition_context(messages, "test task", max_messages=5)
        assert len(result) <= 5
        assert len(result) >= 1

    def test_default_max_messages(self):
        """Default max_messages should be 10 (+1 for last user message)."""
        messages = [{"role": "user", "content": f"Msg {i}"} for i in range(20)]
        result = partition_context(messages, "test")
        assert len(result) <= 11

    def test_include_system_default(self):
        """System messages included by default."""
        messages = [
            {"role": "system", "content": "Sys"},
            {"role": "user", "content": "Hello"},
        ]
        result = partition_context(messages, "test")
        assert any(m["role"] == "system" for m in result)
