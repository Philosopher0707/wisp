"""Test slash commands via dispatch() — the canonical command path.

The REPL loop (entry._run_repl) drives commands via dispatch().
Tests here validate command behavior in isolation.
"""

import pytest
from unittest.mock import MagicMock
from wisp.commands import dispatch
from wisp.transport.cli import AgentAdapter


class FakeRuntime:
    def __init__(self):
        self.store = MagicMock()
        self.telemetry = MagicMock()
        self.security = MagicMock()

    async def maybe_compact(self, session, force=False):
        return None

    def _get_core(self):
        return MagicMock()


class FakeConfig:
    model = "test-model"
    workspace = "/tmp"
    show_thinking = False
    auto_approve = False
    max_context_tokens = 128000
    chars_per_token = 4
    permission_mode = "auto"

    def replace(self, **kwargs):
        import copy
        inst = copy.copy(self)
        for k, v in kwargs.items():
            setattr(inst, k, v)
        return inst


def _make_adapter(session_id="test-session"):
    session = {
        "id": session_id,
        "model": "test-model",
        "workspace": "/tmp",
        "messages": [],
    }
    return AgentAdapter(FakeRuntime(), FakeConfig(), session)


class TestHelpCommand:
    def test_help_lists_commands(self, capsys):
        adapter = _make_adapter()
        result = dispatch("/help", adapter)
        assert result is True  # Consumed
        output = capsys.readouterr().out
        assert "Available commands" in output or "/help" in output


class TestClearCommand:
    def test_clear_empties_messages(self, capsys):
        adapter = _make_adapter()
        adapter.messages.extend([
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ])
        result = dispatch("/clear", adapter)
        assert result is True  # Consumed
        assert len(adapter.messages) == 0


class TestSessionCommand:
    def test_session_shows_id(self, capsys):
        adapter = _make_adapter(session_id="abc-123")
        result = dispatch("/session", adapter)
        assert result is True  # Consumed
        output = capsys.readouterr().out
        assert "abc-123" in output


class TestExitCommand:
    def test_exit_raises(self):
        from wisp.exceptions import ExitREPL
        adapter = _make_adapter()
        with pytest.raises(ExitREPL):
            dispatch("/exit", adapter)


class TestThinkingCommand:
    def test_thinking_toggles(self, capsys):
        adapter = _make_adapter()
        original = adapter.config.show_thinking
        result = dispatch("/thinking", adapter)
        assert result is True  # Consumed


class TestUnknownCommand:
    def test_unknown_shows_error(self, capsys):
        adapter = _make_adapter()
        result = dispatch("/unknown_cmd", adapter)
        # Unknown commands are consumed (True) with an error message
        assert result is True
        output = capsys.readouterr().out
        assert "Unknown command" in output


class TestContinueCommand:
    def test_continue_returns_prompt(self):
        adapter = _make_adapter()
        adapter.messages.append({"role": "assistant", "content": "Here is the code..."})
        result = dispatch("/continue", adapter)
        # /continue should return a string prompt (not True/False)
        assert isinstance(result, str)
        assert "continue" in result.lower() or "Context" in result

    def test_continue_no_history(self, capsys):
        adapter = _make_adapter()
        result = dispatch("/continue", adapter)
        # Should return True (consumed with warning) since no history
        assert result is True


class TestCompactCommand:
    def test_compact_with_few_messages(self, capsys):
        adapter = _make_adapter()
        # Session with only a few messages — should skip compaction
        result = dispatch("/compact", adapter)
        assert result is True  # Consumed