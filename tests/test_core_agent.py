"""Tests for AgentAdapter — backward-compat API for slash commands.

These methods were originally on WispAgentCore (old agent.py). They now live on
AgentAdapter in wisp/transport/cli.py, which adapts the new runtime+session to
the old API that wisp/commands.py expects.
"""

from unittest.mock import MagicMock, patch

import pytest

from wisp.transport.cli import AgentAdapter


def _make_adapter(messages=None, runtime=None, session=None):
    """Build an AgentAdapter with minimal dependencies."""
    if runtime is None:
        runtime = MagicMock()
    if session is None:
        session = {
            "id": "test-session",
            "messages": messages or [],
            "title": "test",
            "model": "test-model",
            "workspace": "/tmp",
        }
    config = MagicMock()
    config.model = "test-model"
    config.workspace = "/tmp"
    config.auto_compact = False
    return AgentAdapter(runtime=runtime, config=config, session=session)


class TestAgentAdapterBasics:

    def test_init(self):
        adapter = _make_adapter()
        assert adapter.messages == []
        assert adapter._interrupted is False

    def test_add_message(self):
        adapter = _make_adapter()
        adapter._add_message("user", "hello")
        assert len(adapter.messages) == 1
        assert adapter.messages[0]["role"] == "user"
        assert adapter.messages[0]["content"] == "hello"

    def test_expand_continuation_noop(self):
        """_expand_continuation is now a no-op — returns text unchanged."""
        adapter = _make_adapter()
        assert adapter._expand_continuation("explain python") == "explain python"
        assert adapter._expand_continuation("continue") == "continue"
        assert adapter._expand_continuation("go on") == "go on"
        assert adapter._expand_continuation("do it") == "do it"

    def test_estimate_tokens(self):
        adapter = _make_adapter()
        msgs = [
            {"role": "user", "content": "a" * 400},
            {"role": "assistant", "content": "b" * 400},
        ]
        tokens = adapter._estimate_tokens(msgs)
        assert tokens == 200  # 800 chars / 4

    def test_estimate_tokens_empty(self):
        adapter = _make_adapter()
        assert adapter._estimate_tokens([]) == 0

    def test_estimate_tokens_short(self):
        adapter = _make_adapter()
        msgs = [{"role": "user", "content": "abc"}]
        assert adapter._estimate_tokens(msgs) == 0  # 3 / 4 = 0

    def test_estimate_tokens_two_messages(self):
        adapter = _make_adapter()
        msgs = [
            {"role": "user", "content": "hello wo"},  # 8 chars
            {"role": "assistant", "content": "rld! hel"},  # 8 chars
        ]
        assert adapter._estimate_tokens(msgs) == 4  # 16 / 4


class TestAgentAdapterSession:

    def test_session_created(self):
        session = {
            "id": "test-id",
            "messages": [],
            "title": "test",
            "model": "test-model",
            "workspace": "/tmp",
        }
        adapter = _make_adapter(session=session)
        assert adapter.session is not None
        assert adapter.session.title == "test"

    def test_save_session(self):
        runtime = MagicMock()
        adapter = _make_adapter(runtime=runtime)
        adapter._save_session()
        runtime.store.save_session.assert_called_once()


class TestAgentAdapterCompaction:

    def test_maybe_compact(self):
        runtime = MagicMock()
        adapter = _make_adapter(runtime=runtime)
        with patch("wisp.transport.cli.asyncio.create_task") as mock_create:
            adapter._maybe_compact_session()
            mock_create.assert_called_once()


class TestAgentAdapterInterrupted:

    def test_interrupted_default_false(self):
        adapter = _make_adapter()
        assert adapter._interrupted is False

    def test_interrupted_can_be_set(self):
        adapter = _make_adapter()
        adapter._interrupted = True
        assert adapter._interrupted is True


class TestAgentAdapterBuildSystemPrompt:

    def test_build_system_prompt_fallback(self):
        """When core doesn't have _build_system_prompt, uses fallback."""
        runtime = MagicMock()
        core = MagicMock()
        del core._build_system_prompt
        runtime._get_core.return_value = core
        adapter = _make_adapter(runtime=runtime)
        result = adapter._build_system_prompt()
        assert "Wisp" in result
        assert "coding assistant" in result

    def test_build_system_prompt_delegates(self):
        """When core has _build_system_prompt, delegates to it."""
        runtime = MagicMock()
        core = MagicMock()
        core._build_system_prompt.return_value = "custom prompt"
        runtime._get_core.return_value = core
        adapter = _make_adapter(runtime=runtime)
        result = adapter._build_system_prompt(query="test query")
        assert result == "custom prompt"
