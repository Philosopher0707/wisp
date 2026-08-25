"""Typed-ahead input capture for the REPL.

While a turn runs, anything the user types lands in this buffer instead
of being lost or racing the next prompt. On turn completion the caller
gets complete lines back (to run as follow-up prompts) plus any partial
line (to re-insert into the next readline prompt).

POSIX ttys only: the reader polls stdin fd with a short select timeout,
which is what makes shutdown deterministic — the thread can never be
stuck inside a blocking read longer than one tick. Everywhere else the
feature disables itself silently.
"""

from __future__ import annotations

import os
import queue
import re
import select
import sys
import threading

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[@-_]")
_SELECT_TICK = 0.05


def _clean(raw: str) -> str:
    """Strip carriage returns and leaked ANSI escapes; keep printable text."""
    text = _ANSI_RE.sub("", raw.replace("\r", ""))
    # A fragment made purely of control remnants is not user intent.
    if text and not any(ch.isprintable() for ch in text):
        return ""
    return text.strip()


def extract_lines(buf: bytearray) -> tuple[list[str], bytearray]:
    """Split accumulated bytes into clean complete lines + remaining partial."""
    lines: list[str] = []
    while True:
        idx = buf.find(b"\n")
        if idx == -1:
            break
        raw = bytes(buf[:idx])
        del buf[: idx + 1]
        text = _clean(raw.decode("utf-8", "replace"))
        if text:
            lines.append(text)
    return lines, buf


class TypeAheadBuffer:
    """Captures stdin lines typed while a turn executes."""

    _active: "TypeAheadBuffer | None" = None

    def __init__(self, on_line=None) -> None:
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._buf = bytearray()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._fd = -1
        self.enabled = False
        # Handshake making pause() deterministic: the reader parks itself
        # when _read_gate is closed and pause() blocks until it observes
        # that, so no os.read() can happen after pause() returns.
        self._read_gate = threading.Event()
        self._read_gate.set()
        self._parked = threading.Event()
        # Called from the reader thread for every complete line as it
        # arrives — used for live mid-turn steering injection.
        self._on_line = on_line

    @classmethod
    def active_instance(cls) -> "TypeAheadBuffer | None":
        """The buffer capturing stdin right now, if any."""
        return cls._active

    def pause(self) -> None:
        """Stop reading fd 0; another reader (approval prompt) owns stdin.

        Returns only once the reader thread has acknowledged the pause.
        """
        self._parked.clear()
        self._read_gate.clear()
        self._parked.wait(timeout=0.5)

    def resume(self) -> None:
        """Re-enable capture; bytes queued while paused are still read."""
        self._read_gate.set()

    def stop_drain_for_test(self, timeout: float = 1.0) -> None:
        """Kill the reader thread without the queue/buffer reset of drain()."""
        self._stop.set()
        self._read_gate.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def start(self) -> None:
        if self.enabled or os.name != "posix":
            return
        if not hasattr(sys.stdin, "isatty") or not sys.stdin.isatty():
            return
        try:
            self._fd = sys.stdin.fileno()
        except (OSError, ValueError):
            return
        if self._fd < 0:
            return
        self.enabled = True
        self._read_gate.set()
        self._parked.clear()
        self._stop.clear()
        TypeAheadBuffer._active = self
        self._thread = threading.Thread(
            target=self._read_loop, name="wisp-typeahead", daemon=True,
        )
        self._thread.start()

    def _read_loop(self) -> None:
        buf = self._buf
        fd = self._fd
        while not self._stop.is_set():
            if not self._read_gate.is_set():
                # Someone else owns stdin (approval prompt). Park until
                # resume; pause() waits for this ack before returning.
                self._parked.set()
                if self._stop.wait(_SELECT_TICK):
                    break
                continue
            self._parked.clear()
            try:
                ready, _, _ = select.select([fd], [], [], _SELECT_TICK)
            except (OSError, ValueError):
                break
            if not ready:
                continue
            if not self._read_gate.is_set():
                # Paused mid-tick: don't os.read bytes meant for the
                # other owner; park and let them wait in the kernel.
                self._parked.set()
                continue
            if not ready:
                continue
            try:
                chunk = os.read(fd, 256)
            except OSError:
                break
            if not chunk:
                break  # EOF
            buf.extend(chunk)
            lines, remainder = extract_lines(buf)
            buf[:] = remainder
            for line in lines:
                # With live steering wired, the inbox owns the line and
                # replay-dedup is the runtime's job; queue stays empty.
                if self._on_line is None:
                    self._queue.put(line)
                else:
                    try:
                        self._on_line(line)
                    except Exception:
                        pass

    def drain(self, timeout: float = 2.0) -> tuple[list[str], str]:
        """Stop capturing; return (complete_lines, partial_unsubmitted_text)."""
        if not self.enabled:
            return [], ""
        self._stop.set()
        if TypeAheadBuffer._active is self:
            TypeAheadBuffer._active = None
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        lines = list(self._drain_queue())
        text = _clean(bytes(self._buf).decode("utf-8", "replace"))
        self._buf.clear()
        return lines, text

    def _drain_queue(self):
        while True:
            try:
                yield self._queue.get_nowait()
            except queue.Empty:
                return
