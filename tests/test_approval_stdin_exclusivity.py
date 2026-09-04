"""Approval prompts must own stdin exclusively.

The typeahead reader and the approval reader both select() on fd 0.
While a turn runs (typeahead active), a keystroke typed at the approval
prompt can be consumed by the typeahead thread first — the answer never
reaches the gate and the user is stuck in the "still waiting" reminder
loop. Regression tests for that theft.
"""

from __future__ import annotations

import os
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
        thread = threading.Thread(target=tb._read_loop, daemon=True)
        thread.start()
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
        thread = threading.Thread(target=tb._read_loop, daemon=True)
        thread.start()
        try:
            time.sleep(0.1)
            tb.resume()
            os.write(master, b"a\n")
            time.sleep(0.2)
            assert tb._queue.get_nowait() == "a"
        finally:
            tb.stop_drain_for_test()

    def test_active_reader_consumes_bytes_theft_repro(self, pty_pair):
        """Documents the bug shape: unpauseed capture steals the line."""
        master, slave = pty_pair
        tb = _make_buffer(slave)
        thread = threading.Thread(target=tb._read_loop, daemon=True)
        thread.start()
        try:
            os.write(master, b"Y\n")
            time.sleep(0.2)
            assert tb._queue.get_nowait() == "Y"
        finally:
            tb.stop_drain_for_test()

    def test_pause_resume_roundtrip_no_loss(self, pty_pair):
        master, slave = pty_pair
        tb = _make_buffer(slave)
        thread = threading.Thread(target=tb._read_loop, daemon=True)
        thread.start()
        try:
            tb.pause()
            os.write(master, b"before\n")
            time.sleep(0.15)
            assert tb._queue.empty()  # nothing consumed while paused
            tb.resume()
            time.sleep(0.15)
            # Bytes written while paused are still in the kernel queue;
            # the reader must pick them up after resume, not drop them.
            assert tb._queue.get_nowait() == "before"
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
