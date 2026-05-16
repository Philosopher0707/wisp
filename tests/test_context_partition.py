"""Tests for context partitioning."""
import pytest
from wisp.multi_agent.context_partition import ContextPartitioner, partition_context


class TestContextPartitioner:
    def test_empty_messages(self):
        result = partition_context([], "test task")
        assert result == []

    def test_single_message(self):
        messages = [{"role": "user", "content": "hello"}]
        result = partition_context(messages, "test task")
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_system_message_always_included(self):
        messages = [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "hello"},
        ]
        result = partition_context(messages, "test task")
        assert any(m["role"] == "system" for m in result)

    def test_system_message_excluded_when_flag_false(self):
        messages = [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "hello"},
        ]
        result = partition_context(messages, "test task", include_system=False)
        assert not any(m["role"] == "system" for m in result)

    def test_last_user_message_always_included(self):
        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "response"},
            {"role": "user", "content": "last"},
        ]
        result = partition_context(messages, "test task", max_messages=2)
        assert any(m.get("content") == "last" for m in result)

    def test_file_mention_relevance(self):
        messages = [
            {"role": "user", "content": "Check auth.py for issues"},
            {"role": "assistant", "content": "I found no issues"},
            {"role": "user", "content": "Now check main.py"},
        ]
        result = partition_context(messages, "Review auth.py", max_messages=2)
        # Should include the message mentioning auth.py
        assert any("auth.py" in str(m.get("content", "")) for m in result)

    def test_max_messages_limit(self):
        messages = [{"role": "user", "content": f"message {i}"} for i in range(20)]
        result = partition_context(messages, "test", max_messages=5)
        # max_messages=5 but last user message is always added, so could be 6
        assert len(result) <= 6

    def test_tool_mention_relevance(self):
        messages = [
            {"role": "user", "content": "Use web_search to find info"},
            {"role": "assistant", "content": "Here are results"},
        ]
        result = partition_context(messages, "Search for Python docs", max_messages=2)
        assert len(result) > 0

    def test_preserves_order(self):
        messages = [
            {"role": "user", "content": "first"},
            {"role": "user", "content": "second"},
            {"role": "user", "content": "third"},
        ]
        result = partition_context(messages, "test", max_messages=2)
        # Should preserve relative order
        contents = [m["content"] for m in result]
        if len(contents) >= 2:
            assert contents[0] < contents[1]  # "first" < "second" < "third"
