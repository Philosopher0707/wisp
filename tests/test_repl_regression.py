"""Regression tests for REPL bug fixes — May 2026.

Covers P0 signal-propagation, P0 exception state corruption, P1 continuation false-positives.
"""

import asyncio
from unittest.mock import patch

import pytest

from wisp.core.agent import WispAgentCore
from wisp.transport.cli import CLITransport, _handle_sigint
from wisp.config import WispConfig


def _make_failing_gen(exc_type):
    """Return a callable that produces an async gen which immediately raises exc."""
    def _factory(*a, **k):
        class _Iter:
            def __aiter__(self):
                return self
            async def __anext__(self):
                raise exc_type()
        return _Iter()
    return _factory


class TestSigintPropagationToCore:
    """Ctrl+C must set both transport._interrupted AND core._interrupted."""

    def test_sigint_sets_core_flag(self):
        config = WispConfig()
        core = WispAgentCore(config=config)
        transport = CLITransport(core)
        transport._interrupted = False
        core._interrupted = False
        _handle_sigint(None, None)
        assert transport._interrupted is True
        assert core._interrupted is True


class TestExecuteTurnExceptionHandling:
    """Exceptions must propagate cleanly without corrupting core.messages."""

    def test_keyboard_interrupt_propagates_and_sets_flags(self):
        config = WispConfig()
        core = WispAgentCore(config=config)
        transport = CLITransport(core)
        core.messages = [{"role": "user", "content": "hi"}]

        with patch.object(core, "_arun", _make_failing_gen(KeyboardInterrupt)):
            with pytest.raises(KeyboardInterrupt):
                asyncio.run(transport._execute_turn("sys", "."))

        assert core._interrupted is True
        assert transport._interrupted is True

    def test_runtime_error_propagates_without_appending_user_message(self):
        """Previously a broad `except Exception` re-added a fake user message."""
        config = WispConfig()
        core = WispAgentCore(config=config)
        transport = CLITransport(core)
        core.messages = [{"role": "user", "content": "foo"}]

        with patch.object(core, "_arun", _make_failing_gen(RuntimeError)):
            with pytest.raises(RuntimeError):
                asyncio.run(transport._execute_turn("sys", "."))

        # The transport pops the user message before calling _arun (existing design).
        # The bug was that a broad `except Exception` then RE-ADDED a fake user message.
        # With the fix, no fake message is appended — the list stays empty (popped).
        assert core.messages == []
        assert not any(
            m.get("role") == "user" and m.get("content", "") == [{"type": "text", "text": "foo"}]
            for m in core.messages
        )

    def test_cancelled_error_propagates_and_sets_flags(self):
        config = WispConfig()
        core = WispAgentCore(config=config)
        transport = CLITransport(core)
        core.messages = [{"role": "user", "content": "bar"}]

        with patch.object(core, "_arun", _make_failing_gen(asyncio.CancelledError)):
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(transport._execute_turn("sys", "."))

        assert core._interrupted is True
        assert transport._interrupted is True
        # No fake user message was re-added after the exception.
        assert core.messages == []


class TestExpandContinuation:
    """_expand_continuation must not fire on long/normal user questions."""

    def test_long_question_no_expansion(self):
        config = WispConfig()
        core = WispAgentCore(config=config)
        text = "How do I continue using this API in production?"
        assert core._expand_continuation(text) == text

    def test_medium_question_no_expansion(self):
        config = WispConfig()
        core = WispAgentCore(config=config)
        assert core._expand_continuation("Can you continue with the fix?") == "Can you continue with the fix?"

    def test_short_continue_still_expands(self):
        config = WispConfig()
        core = WispAgentCore(config=config)
        core.messages = [{"role": "assistant", "content": "Step one..."}]
        result = core._expand_continuation("continue")
        assert result.startswith("continue")
        assert "Context:" in result

    def test_short_go_on_still_expands(self):
        config = WispConfig()
        core = WispAgentCore(config=config)
        core.messages = [{"role": "assistant", "content": "Then add..."}]
        result = core._expand_continuation("go on")
        assert result.startswith("go on")
        assert "Context:" in result

    def test_removed_triggers_gone(self):
        config = WispConfig()
        core = WispAgentCore(config=config)
        for removed in ("and?", "what else", "tell me more"):
            assert removed not in core._CONTINUATION_TRIGGERS, removed


class TestInterruptResetPerTurn:
    """_interrupted must be fresh at the start of each _execute_turn call."""

    def test_interrupt_resets(self):
        config = WispConfig()
        core = WispAgentCore(config=config)
        transport = CLITransport(core)
        transport._interrupted = True
        core._interrupted = True

        async def _ok(*a, **k):
            from wisp.core.events import content, done
            yield content("ok")
            yield done("sid")

        core.messages = [{"role": "user", "content": "hi"}]
        with patch.object(core, "_arun", _ok):
            asyncio.run(transport._execute_turn("sys", "."))

        assert transport._interrupted is False
        assert core._interrupted is False
