"""Regression tests for REPL — updated for current API (May 2026).

Covers signal propagation, _expand_continuation (now a no-op), and
interrupt state management on the CLITransport + AgentAdapter API.
"""

from unittest.mock import patch, MagicMock

import pytest

from wisp.transport.cli import CLITransport, _handle_sigint, _transport_instances


def _make_transport(messages=None):
    """Build a CLITransport with a mock runtime."""
    runtime = MagicMock()
    config = MagicMock()
    transport = CLITransport(runtime)
    return transport, runtime


def _cleanup_transport_instances():
    """Clean the global _transport_instances list between tests."""
    _transport_instances.clear()


class TestSigintPropagation:
    """Ctrl+C handler must set _interrupted on all live transport instances."""

    def setup_method(self):
        _cleanup_transport_instances()

    def teardown_method(self):
        _cleanup_transport_instances()

    def test_sigint_sets_interrupted_flag(self):
        transport, _ = _make_transport()
        transport._interrupted = False
        _handle_sigint(None, None)
        assert transport._interrupted is True

    def test_sigint_propagates_to_runtime(self):
        transport, runtime = _make_transport()
        runtime._interrupted = False
        transport._interrupted = False
        _handle_sigint(None, None)
        assert runtime._interrupted is True

    def test_sigint_multiple_instances(self):
        t1, r1 = _make_transport()
        t2, r2 = _make_transport()
        t1._interrupted = False
        t2._interrupted = False
        _handle_sigint(None, None)
        assert t1._interrupted is True
        assert t2._interrupted is True


class TestExpandContinuation:
    """_expand_continuation is now a no-op — returns text unchanged."""

    def test_returns_text_unchanged(self):
        adapter = _make_agent_adapter()
        assert adapter._expand_continuation("explain python") == "explain python"

    def test_continue_unchanged(self):
        adapter = _make_agent_adapter()
        assert adapter._expand_continuation("continue") == "continue"

    def test_go_on_unchanged(self):
        adapter = _make_agent_adapter()
        assert adapter._expand_continuation("go on") == "go on"

    def test_do_it_unchanged(self):
        adapter = _make_agent_adapter()
        assert adapter._expand_continuation("do it") == "do it"

    def test_write_it_unchanged(self):
        adapter = _make_agent_adapter()
        assert adapter._expand_continuation("write it") == "write it"

    def test_long_phrase_unchanged(self):
        adapter = _make_agent_adapter()
        long_text = "How do I continue using this API in production?"
        assert adapter._expand_continuation(long_text) == long_text


class TestInterruptFlag:
    """_interrupted flag state management."""

    def setup_method(self):
        _cleanup_transport_instances()

    def teardown_method(self):
        _cleanup_transport_instances()

    def test_interrupt_defaults_false(self):
        transport, _ = _make_transport()
        assert transport._interrupted is False

    def test_interrupt_can_be_set(self):
        transport, _ = _make_transport()
        transport._interrupted = True
        assert transport._interrupted is True

    def test_interrupt_can_be_cleared(self):
        transport, _ = _make_transport()
        transport._interrupted = True
        transport._interrupted = False
        assert transport._interrupted is False

    def test_agent_adapter_interrupt_default(self):
        adapter = _make_agent_adapter()
        assert adapter._interrupted is False

    def test_agent_adapter_interrupt_set(self):
        adapter = _make_agent_adapter()
        adapter._interrupted = True
        assert adapter._interrupted is True


def _make_agent_adapter(messages=None):
    """Build an AgentAdapter with minimal dependencies."""
    from wisp.transport.cli import AgentAdapter

    runtime = MagicMock()
    session = {
        "id": "test-session",
        "messages": messages or [],
        "title": "test",
        "model": "test-model",
        "workspace": "/tmp",
    }
    config = MagicMock()
    return AgentAdapter(runtime=runtime, config=config, session=session)
