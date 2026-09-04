"""Phase 2.2 RED tests — cancellation-first + typed transient classification.

These tests pin the target architecture. They FAIL on the current
implementation (substring fallback misclassifies cancellations) and PASS
after the Phase 2.2 fix.
"""

from __future__ import annotations

import asyncio

from wisp.core.transport import is_transient_error


def test_cancelled_error_never_transient_even_with_transient_message():
    # Current bug: substring fallback matches "connection reset" inside
    # CancelledError message → returns True → retry instead of propagate.
    exc = asyncio.CancelledError("connection reset by peer")
    assert is_transient_error(exc) is False


def test_keyboard_interrupt_never_transient():
    exc = KeyboardInterrupt("write operation timed out")
    assert is_transient_error(exc) is False


def test_system_exit_never_transient():
    exc = SystemExit("broken pipe")
    assert is_transient_error(exc) is False


def test_genuine_transient_still_detected():
    assert is_transient_error(TimeoutError("timed out")) is True
    assert is_transient_error(ConnectionResetError("reset")) is True


def test_hardened_post_propagates_cancellation_without_retry():
    from wisp.core.transport import hardened_post

    calls = []

    class _Session:
        _wisp_hardened_timeout = None

        def post(self, url, **kw):
            calls.append(1)
            raise asyncio.CancelledError("connection reset")

    import pytest

    with pytest.raises(asyncio.CancelledError):
        hardened_post(_Session(), "http://x", json={}, max_attempts=3)
    assert len(calls) == 1  # no retries on cancellation


def test_guarded_stream_propagates_cancellation():
    import pytest

    from wisp.core.provider_stream import guarded_provider_stream

    async def _go():
        def _open():
            async def _gen():
                raise asyncio.CancelledError("connection reset")
                yield  # pragma: no cover

            return _gen()

        events = []
        with pytest.raises(asyncio.CancelledError):
            async for ev in guarded_provider_stream(
                _open,
                lambda e: e,
                set(),
                first_token_deadline_s=5,
                chunk_deadline_s=5,
                max_attempts=3,
            ):
                events.append(ev)
        return events

    asyncio.run(_go())
