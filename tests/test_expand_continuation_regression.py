"""TDD for Bug 4: AgentAdapter._expand_continuation() is a no-op.

The /continue command relies on _expand_continuation() to inject context
from the last assistant message. When it's a no-op, /continue always bails.

The fix should restore the continuation expansion logic from the old
WispAgentCore._expand_continuation().
"""

import pytest

from wisp.transport.cli import AgentAdapter


class _MockRuntime:
    pass


class _MockConfig:
    model = "qwen"
    workspace = "/tmp"


def _make_adapter():
    session = {
        "id": "test-session",
        "model": "qwen",
        "workspace": "/tmp",
        "messages": [],
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
        "compaction_history": [],
    }
    return AgentAdapter(_MockRuntime(), _MockConfig(), session)


class TestExpandContinuation:
    """_expand_continuation must inject context from last assistant message."""

    def test_non_continuation_text_unchanged(self):
        """Non-continuation prompts should pass through unchanged."""
        adapter = _make_adapter()
        assert adapter._expand_continuation("hello world") == "hello world"
        assert adapter._expand_continuation("explain python") == "explain python"

    def test_continue_with_no_messages(self):
        """Continue with no history should return 'continue' (no expansion)."""
        adapter = _make_adapter()
        result = adapter._expand_continuation("continue")
        assert result == "continue"

    def test_continue_with_last_assistant_message(self):
        """Continue should inject tail of last assistant message."""
        adapter = _make_adapter()
        adapter.messages.append({"role": "user", "content": "tell me about python"})
        adapter.messages.append({"role": "assistant", "content": "Python is a programming language. It is widely used for..."})

        result = adapter._expand_continuation("continue")

        # Should NOT be just "continue"
        assert result != "continue"
        # Should contain context marker
        assert "[Context:" in result
        # Should contain tail of last assistant message
        assert "Python is a programming language" in result

    def test_go_on_with_last_assistant_message(self):
        """'go on' is also a continuation trigger."""
        adapter = _make_adapter()
        adapter.messages.append({"role": "user", "content": "tell me about python"})
        adapter.messages.append({"role": "assistant", "content": "Python is great..."})

        result = adapter._expand_continuation("go on")
        assert result != "go on"
        assert "[Context:" in result

    def test_continue_does_not_mutate_original(self):
        """The original prompt string should not be mutated."""
        adapter = _make_adapter()
        adapter.messages.append({"role": "assistant", "content": "Some previous response..."})

        original = "continue"
        result = adapter._expand_continuation(original)

        # Original should be unchanged
        assert original == "continue"
        # Result should be a new string
        assert result is not original
        assert "[Context:" in result

    def test_continue_with_long_assistant_message_truncates_tail(self):
        """Long assistant messages should have tail truncated to ~200 chars."""
        adapter = _make_adapter()
        long_text = "A" * 500
        adapter.messages.append({"role": "assistant", "content": long_text})

        result = adapter._expand_continuation("continue")
        assert "[Context:" in result
        # The tail should be included, not the full 500 chars
        tail_part = result.split("Pick up exactly after: ")[-1].rstrip("]")
        assert len(tail_part) <= 250  # ~200 chars + margin

    def test_continue_with_only_user_messages(self):
        """If no assistant message exists, continue should return unchanged."""
        adapter = _make_adapter()
        adapter.messages.append({"role": "user", "content": "hello"})
        adapter.messages.append({"role": "user", "content": "world"})

        result = adapter._expand_continuation("continue")
        assert result == "continue"
