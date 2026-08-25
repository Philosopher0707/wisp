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

    def __init__(self, on_line=None) -> None:
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._buf = bytearray()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._fd = -1
        self.enabled = False
        # Called from the reader thread for every complete line as it
        # arrives — used for live mid-turn steering injection.
        self._on_line = on_line

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
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._read_loop, name="wisp-typeahead", daemon=True,
        )
        self._thread.start()

    def _read_loop(self) -> None:
        buf = self._buf
        fd = self._fd
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([fd], [], [], _SELECT_TICK)
            except (OSError, ValueError):
                break
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
