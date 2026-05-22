"""Regression tests for REPL bug fixes — May 2026.

Covers P0 signal-propagation, P0 exception state corruption, P1 continuation false-positives.
"""

import asyncio
from unittest.mock import patch, MagicMock

import pytest

from wisp.core.engine import WispAgentCore
from wisp.transport.cli import CLITransport, _handle_sigint
from wisp.config import WispConfig


def _make_core(config=None):
    """Build a WispAgentCore with all required dependencies mocked."""
    if config is None:
        config = WispConfig()
    return WispAgentCore(
        config=config,
        provider=MagicMock(),
        security=MagicMock(),
        extensions=MagicMock(),
        telemetry=MagicMock(),
    )


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
        core = _make_core(config)
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
        core = _make_core(config)
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
        core = _make_core(config)
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
        core = _make_core(config)
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
        core = _make_core(config)
        text = "How do I continue using this API in production?"
        assert core._expand_continuation(text) == text

    def test_medium_question_no_expansion(self):
        config = WispConfig()
        core = _make_core(config)
        assert core._expand_continuation("Can you continue with the fix?") == "Can you continue with the fix?"

    def test_short_continue_still_expands(self):
        config = WispConfig()
        core = _make_core(config)
        core.messages = [{"role": "assistant", "content": "Step one..."}]
        result = core._expand_continuation("continue")
        assert result.startswith("continue")
        assert "Context:" in result

    def test_short_go_on_still_expands(self):
        config = WispConfig()
        core = _make_core(config)
        core.messages = [{"role": "assistant", "content": "Then add..."}]
        result = core._expand_continuation("go on")
        assert result.startswith("go on")
        assert "Context:" in result

    def test_removed_triggers_gone(self):
        config = WispConfig()
        core = _make_core(config)
        for removed in ("and?", "what else", "tell me more"):
            assert removed not in core._CONTINUATION_TRIGGERS, removed


class TestInterruptResetPerTurn:
    """_interrupted must be fresh at the start of each _execute_turn call."""

    def test_interrupt_resets(self):
        config = WispConfig()
        core = _make_core(config)
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


class TestActionTriggers:
    """_expand_continuation must expand 'do it' / 'write it' into action context."""

    def test_do_it_after_thinking_injects_action_context(self):
        """After the assistant was thinking (no tool_calls), 'do it' should
        inject context telling the model to EXECUTE immediately."""
        config = WispConfig()
        core = _make_core(config)
        # Simulate the conversation from the bug report:
        # - user asked for research
        # - assistant thought about what to write (no tool_calls)
        # - user said "write it"
        core.messages = [
            {"role": "user", "content": "research autogen v0.4 and write a deep dive"},
            {"role": "assistant", "content": "I should cover the actor model, three-layer architecture, AgentChat API, Core API, Extensions layer, and developer tooling like Studio and Bench. I'll structure this as a comprehensive markdown file with code examples."},
            # No tool_calls — assistant was purely thinking
        ]
        result = core._expand_continuation("write it")
        assert result.startswith("write it")
        assert "EXECUTE" in result or "execute" in result.lower()
        assert "STOP" in result or "stop" in result.lower()
        assert "Context:" in result

    def test_do_it_no_prior_assistant_no_expansion(self):
        """If there's no prior assistant message, 'do it' is a fresh command.
        Don't expand."""
        config = WispConfig()
        core = _make_core(config)
        assert core._expand_continuation("do it") == "do it"

    def test_do_it_after_tool_call_no_expansion(self):
        """If the assistant already executed a tool, 'do it' is unrelated.
        Don't expand."""
        config = WispConfig()
        core = _make_core(config)
        core.messages = [
            {"role": "user", "content": "read file A"},
            {"role": "assistant", "content": "Done.", "tool_calls": [{"function": {"name": "read_file"}}]},
        ]
        assert core._expand_continuation("do it") == "do it"

    def test_various_action_triggers_expand(self):
        """Multiple direct-action phrases should expand with action context."""
        config = WispConfig()
        core = _make_core(config)
        core.messages = [
            {"role": "user", "content": "write a test"},
            {"role": "assistant", "content": "I need to create a pytest fixture..."},
        ]
        # Core action triggers
        for trigger in ("do it", "go ahead", "write it", "proceed", "execute"):
            result = core._expand_continuation(trigger)
            assert result.startswith(trigger), f"{trigger} didn't expand"
            assert "Context:" in result, f"{trigger} missing context"
            assert "EXECUTE" in result, f"{trigger} missing EXECUTE directive"

        # "now" and "start" are NOT in the trigger set — too ambiguous on their own
        for ambiguous in ("now", "start", "go", "act"):
            assert core._expand_continuation(ambiguous) == ambiguous

    def test_long_phrase_not_action_trigger(self):
        """Multi-sentence requests should NOT be treated as action triggers."""
        config = WispConfig()
        core = _make_core(config)
        core.messages = [
            {"role": "user", "content": "write a test"},
            {"role": "assistant", "content": "I need to create a pytest fixture..."},
        ]
        # Too long to be an action trigger (>40 chars)
        long = "do it now please write the file immediately thanks"
        assert core._expand_continuation(long) == long

    def test_question_with_trigger_not_action(self):
        """Questions containing trigger words are not action triggers."""
        config = WispConfig()
        core = _make_core(config)
        core.messages = [
            {"role": "user", "content": "how do I write tests?"},
            {"role": "assistant", "content": "Use pytest..."},
        ]
        # "write" by itself is an action trigger, but "write" in a question context...
        # Actually "write" is a short trigger — but without a prior thinking-only
        # assistant message, it won't expand
        assert core._expand_continuation("write") == "write"

    def test_action_trigger_preserves_analysis_tail(self):
        """The injected context should include the last part of the assistant's
        analysis so the model knows what to execute."""
        config = WispConfig()
        core = _make_core(config)
        core.messages = [
            {"role": "user", "content": "write a summary"},
            {"role": "assistant", "content": "I should cover architecture, API changes, migration path, and code examples."},
        ]
        result = core._expand_continuation("write it")
        # Should contain the tail of the assistant's analysis
        assert "architecture" in result or "API changes" in result or "migration" in result or "code examples" in result
        assert "your prior work" in result.lower() or "previous analysis" in result.lower()

    def test_repeated_do_it_escalates(self):
        """If user says 'do it' multiple times, each should expand with action context."""
        config = WispConfig()
        core = _make_core(config)
        core.messages = [
            {"role": "user", "content": "fix the bug"},
            {"role": "assistant", "content": "The issue is in the auth module. I need to add validation."},
        ]
        r1 = core._expand_continuation("do it")
        # Simulate the model thinking again after first "do it"
        core.messages.append({"role": "user", "content": r1})
        core.messages.append({"role": "assistant", "content": "I should first check the imports before editing."})
        r2 = core._expand_continuation("do it")
        assert r2.startswith("do it")
        assert "Context:" in r2
