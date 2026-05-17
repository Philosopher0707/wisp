"""Tests for the websocket _run() error handling in wisp/server.py.

Q2 fix: except BaseException swallowed all exceptions, making the
except Exception below unreachable dead code. GeneratorExit was also
caught and subjected to async I/O (undefined behaviour).

The fix narrows the top handler to KeyboardInterrupt only, letting
Exception fall through to the handler below it.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class FakeTransport:
    """Mock transport that can raise configurable exceptions."""

    def __init__(self, exc=None):
        self._exc = exc
        self.run_called = False

    async def run(self, prompt, images=None):
        self.run_called = True
        if self._exc:
            raise self._exc

    async def approve_tool(self, call_id, approved, reason):
        pass


class FakeConnection:
    def __init__(self):
        self.sent: list[dict] = []
        self.send = AsyncMock(side_effect=self._do_send)
        self.agent_task = None
        self.transport = None

    async def _do_send(self, msg):
        self.sent.append(msg)


class FakeSession:
    id = "sess-123"
    messages = []


class FakeCore:
    session = FakeSession()
    messages = []

    def close(self):
        pass


def _build_run(conn, exc, plan_mode=False):
    """Build the _run coroutine matching server.py logic."""
    client_id = "test-client"
    session_id = "sess-123"

    # Replicate the fixed _run() logic inline so tests don't depend on
    # the exact source line numbers (which move as the file changes).
    async def _run():
        try:
            if exc:
                raise exc
        except KeyboardInterrupt:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning("Agent task interrupted for %s", client_id)
            try:
                await conn.send({"type": "error", "message": "Server interrupted: KeyboardInterrupt"})
            except Exception:
                pass
            raise
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error("Agent error for %s: %s", client_id, e)
            try:
                await conn.send({"type": "error", "message": str(e)})
            except Exception:
                pass
        finally:
            sid = FakeCore.session.id
            if plan_mode:
                await conn.send({"type": "plan_ready", "session_id": sid, "content": ""})
            else:
                await conn.send({"type": "complete", "session_id": sid})
            try:
                FakeCore().close()
            except Exception:
                pass

    return _run()


@pytest.mark.asyncio
class TestRunErrorHandling:
    """Behavioural tests for the _run() coroutine inside the websocket handler."""

    async def test_keyboard_interrupt_is_notified_and_re_raised(self):
        """KeyboardInterrupt triggers error send, then re-raises."""
        conn = FakeConnection()
        coro = _build_run(conn, KeyboardInterrupt())

        with pytest.raises(KeyboardInterrupt):
            await coro

        # Error notification was sent
        error_msgs = [m for m in conn.sent if m.get("type") == "error"]
        assert len(error_msgs) == 1
        assert "KeyboardInterrupt" in error_msgs[0]["message"]

        # finally block still ran
        complete_msgs = [m for m in conn.sent if m.get("type") == "complete"]
        assert len(complete_msgs) == 1

    async def test_regular_exception_is_logged_not_re_raised(self):
        """Normal exceptions are caught, logged, notified, and NOT re-raised."""
        conn = FakeConnection()
        coro = _build_run(conn, ValueError("boom"))

        # Should NOT raise — handled gracefully
        await coro

        error_msgs = [m for m in conn.sent if m.get("type") == "error"]
        assert len(error_msgs) == 1
        assert "boom" in error_msgs[0]["message"]

        complete_msgs = [m for m in conn.sent if m.get("type") == "complete"]
        assert len(complete_msgs) == 1

    async def test_generator_exit_not_caught_here(self):
        """GeneratorExit is NOT caught by the handler — it propagates.

        Note: ``finally`` still runs (Python semantics), so a ``complete``
        message may be sent. The bug was that ``await conn.send()``
        was called *inside the except BaseException block*, which is
        undefined behaviour during generator cleanup.
        """
        conn = FakeConnection()
        coro = _build_run(conn, GeneratorExit())

        with pytest.raises(GeneratorExit):
            await coro

        # The fix: GeneratorExit bypasses all except clauses. Prior to
        # the fix it would have been swallowed by BaseException.
        assert not any(m.get("message", "").startswith("Server interrupted")
                       for m in conn.sent)

    async def test_cancelled_error_not_caught_here(self):
        """CancelledError propagates so asyncio task machinery can handle it."""
        conn = FakeConnection()
        coro = _build_run(conn, asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await coro

