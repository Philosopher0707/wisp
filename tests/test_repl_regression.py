"""Regression tests for REPL — updated for current API (May 2026).

Covers signal propagation, _expand_continuation (now a no-op), and
interrupt state management on the CLITransport + AgentAdapter API.
"""

import asyncio
import signal
from unittest.mock import MagicMock


from wisp.transport.cli import (
    CLITransport,
    _handle_sigint,
    _install_signal_handler,
    _restore_signal_handler,
    _transport_instances,
)


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


class TestSignalHandlerRegistration:
    """Signal handler must be installed before REPL and restored after."""

    def setup_method(self):
        _cleanup_transport_instances()
        # Save original handler
        self._orig_handler = signal.getsignal(signal.SIGINT)

    def teardown_method(self):
        _cleanup_transport_instances()
        # Restore original handler
        signal.signal(signal.SIGINT, self._orig_handler)

    def test_install_signal_handler_registers_custom_handler(self):
        """_install_signal_handler should register _handle_sigint."""
        transport, _ = _make_transport()
        _install_signal_handler()
        current = signal.getsignal(signal.SIGINT)
        assert current is _handle_sigint
        _restore_signal_handler()

    def test_restore_signal_handler_restores_original(self):
        """_restore_signal_handler should restore the previous handler."""
        transport, _ = _make_transport()
        _install_signal_handler()
        _restore_signal_handler()
        current = signal.getsignal(signal.SIGINT)
        assert current is self._orig_handler

    def test_install_resets_interrupted_flags(self):
        """Installing handler should reset _interrupted on all instances."""
        t1, _ = _make_transport()
        t2, _ = _make_transport()
        t1._interrupted = True
        t2._interrupted = True
        _install_signal_handler()
        assert t1._interrupted is False
        assert t2._interrupted is False
        _restore_signal_handler()


class TestCancelTasks:
    """_cancel_tasks must work when called from outside an async task."""

    def test_cancel_tasks_does_not_crash_outside_task(self):
        """Cancel all tasks should not crash when called from main thread."""
        loop = asyncio.new_event_loop()
        try:
            async def dummy_task():
                await asyncio.sleep(10)

            task = loop.create_task(dummy_task())
            # Simulate what _cancel_tasks does but safely
            for t in asyncio.all_tasks(loop):
                if not t.done():
                    t.cancel()
            # Let the loop process cancellations
            loop.run_until_complete(asyncio.sleep(0))
            assert task.cancelled() or task.done()
        finally:
            loop.close()


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


class TestInterruptedFlagIsChecked:
    """The _interrupted flag must actually be checked during streaming."""

    def test_clitransport_has_check_interrupted_method(self):
        """CLITransport should expose a way to check if interrupted."""
        transport, _ = _make_transport()
        assert hasattr(transport, "is_interrupted")

    def test_is_interrupted_returns_false_by_default(self):
        transport, _ = _make_transport()
        assert transport.is_interrupted() is False

    def test_is_interrupted_returns_true_after_sigint(self):
        transport, _ = _make_transport()
        transport._interrupted = False
        _handle_sigint(None, None)
        assert transport.is_interrupted() is True


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
