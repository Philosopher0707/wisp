"""Approval prompts must own stdin exclusively.

The typeahead reader and the approval reader both select() on fd 0.
While a turn runs (typeahead active), a keystroke typed at the approval
prompt can be consumed by the typeahead thread first — the answer never
reaches the gate and the user is stuck in the "still waiting" reminder
loop. Regression tests for that theft.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time

import pytest

from wisp.transport.typeahead import TypeAheadBuffer


def _make_buffer(fd: int) -> TypeAheadBuffer:
    """Buffer bound to an explicit fd, bypassing the tty check."""
    tb = TypeAheadBuffer()
    tb.enabled = True
    tb._fd = fd
    return tb


#: How long a delivery may take before it counts as lost. The reader wakes
#: within one `_SELECT_TICK` of resume, but the thread still has to be
#: scheduled: a fixed sleep followed by `get_nowait()` failed on a loaded CI
#: runner with the line late, not lost. A bounded wait returns as soon as the
#: line arrives, and a genuinely dropped line still fails.
_DELIVERY_TIMEOUT = 2.0


def _start_reader(tb: TypeAheadBuffer) -> threading.Thread:
    """Run the read loop on a thread `stop_drain_for_test` can join.

    The join matters: the fixture closes the pipe at teardown, and the next
    test's `os.pipe()` reuses those fd numbers. A reader that outlived its
    test could `os.read` the next test's pipe and swallow its line.
    """
    thread = threading.Thread(target=tb._read_loop, daemon=True)
    tb._thread = thread
    thread.start()
    return thread


def _next_line(tb: TypeAheadBuffer, thread: threading.Thread) -> str:
    """Wait for a delivered line; on timeout say where the reader was."""
    try:
        return tb._queue.get(timeout=_DELIVERY_TIMEOUT)
    except queue.Empty:
        raise AssertionError(
            f"no line within {_DELIVERY_TIMEOUT}s: reader alive={thread.is_alive()} "
            f"parked={tb._parked.is_set()} gate_open={tb._read_gate.is_set()} "
            f"stopped={tb._stop.is_set()} partial={bytes(tb._buf)!r}"
        ) from None


@pytest.fixture()
def pty_pair():
    # A pipe exercises the same select()+os.read() fd semantics as a tty;
    # openpty() is unavailable under some sandboxes.
    read_fd, write_fd = os.pipe()
    yield write_fd, read_fd
    os.close(write_fd)
    os.close(read_fd)


class TestPauseSemantics:
    def test_paused_reader_ignores_bytes(self, pty_pair):
        master, slave = pty_pair
        tb = _make_buffer(slave)
        tb.pause()
        _start_reader(tb)
        try:
            os.write(master, b"Y\n")
            time.sleep(0.2)
            assert tb._queue.empty()
            assert not tb._buf
        finally:
            tb.stop_drain_for_test()

    def test_resumed_reader_captures_bytes(self, pty_pair):
        master, slave = pty_pair
        tb = _make_buffer(slave)
        tb.pause()
        thread = _start_reader(tb)
        try:
            time.sleep(0.1)
            tb.resume()
            os.write(master, b"a\n")
            assert _next_line(tb, thread) == "a"
        finally:
            tb.stop_drain_for_test()

    def test_active_reader_consumes_bytes_theft_repro(self, pty_pair):
        """Documents the bug shape: unpauseed capture steals the line."""
        master, slave = pty_pair
        tb = _make_buffer(slave)
        thread = _start_reader(tb)
        try:
            os.write(master, b"Y\n")
            assert _next_line(tb, thread) == "Y"
        finally:
            tb.stop_drain_for_test()

    def test_pause_resume_roundtrip_no_loss(self, pty_pair):
        master, slave = pty_pair
        tb = _make_buffer(slave)
        thread = _start_reader(tb)
        try:
            tb.pause()
            os.write(master, b"before\n")
            time.sleep(0.15)
            assert tb._queue.empty()  # nothing consumed while paused
            tb.resume()
            # Bytes written while paused are still in the kernel queue;
            # the reader must pick them up after resume, not drop them.
            assert _next_line(tb, thread) == "before"
        finally:
            tb.stop_drain_for_test()


class TestActiveRegistry:
    def test_active_instance_tracks_start_and_drain(self):
        tb = TypeAheadBuffer()
        assert TypeAheadBuffer.active_instance() is None
        TypeAheadBuffer._active = tb
        try:
            assert TypeAheadBuffer.active_instance() is tb
            # drain() clears the registration only for its own instance.
            tb.enabled = True
            tb._thread = None
            tb.drain()
            assert TypeAheadBuffer.active_instance() is None
        finally:
            TypeAheadBuffer._active = None

    def test_non_tty_never_activates(self):
        tb = TypeAheadBuffer()
        fake = object()
        orig = sys.stdin
        sys.stdin = fake
        try:
            tb.start()  # no isatty/fileno -> silently disabled
        finally:
            sys.stdin = orig
        assert tb.enabled is False


class TestApproveExclusiveStdin:
    """approve() must pause active typeahead around its blocking read."""

    @staticmethod
    def _bare_transport():
        from wisp.approval_state import ApprovalSessionState
        from wisp.transport.cli import CLITransport

        transport = CLITransport.__new__(CLITransport)
        transport._spinner = None
        transport._force_approval_mode = False
        transport._approval_state = ApprovalSessionState()
        return transport

    @pytest.mark.asyncio
    async def test_approve_pauses_and_resumes_capture(self, monkeypatch):
        tb = TypeAheadBuffer()
        calls: list[str] = []
        tb.pause = lambda: calls.append("pause")  # type: ignore[method-assign]
        tb.resume = lambda: calls.append("resume")  # type: ignore[method-assign]
        tb.enabled = True
        TypeAheadBuffer._active = tb

        transport = self._bare_transport()

        async def fake_read(**kwargs):
            calls.append("read")
            return "y"

        monkeypatch.setattr(
            transport, "_read_approval_answer_with_reminders", fake_read
        )
        result = await transport.approve({"name": "fanout", "arguments": {}})
        assert result is True
        assert calls == ["pause", "read", "resume"]
        TypeAheadBuffer._active = None

    @pytest.mark.asyncio
    async def test_approve_resumes_on_read_error(self, monkeypatch):
        tb = TypeAheadBuffer()
        calls: list[str] = []
        tb.pause = lambda: calls.append("pause")  # type: ignore[method-assign]
        tb.resume = lambda: calls.append("resume")  # type: ignore[method-assign]
        tb.enabled = True
        TypeAheadBuffer._active = tb

        transport = self._bare_transport()

        async def boom(**kwargs):
            calls.append("read")
            raise EOFError

        monkeypatch.setattr(
            transport, "_read_approval_answer_with_reminders", boom
        )
        result = await transport.approve({"name": "spawn", "arguments": {}})
        assert result is False
        assert calls == ["pause", "read", "resume"]
        TypeAheadBuffer._active = None
