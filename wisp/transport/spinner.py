"""Terminal spinner with inline updates via \\r.

Mode-aware: braille spinner in unicode, ascii art in ascii mode,
text label in accessible, silent in minimal.

Animates frames via background thread at 120ms intervals during
tool execution. Thread stops on succeed/fail/stop.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import TextIO

from wisp.terminal_width import OutputMode

_BRAILLE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_ASCII_FRAMES = ["|", "/", "-", "\\"]
_ACCESSIBLE_FRAMES = ["[busy]"]


class Spinner:
    """Inline terminal spinner for tool execution feedback.

    Writes to ``stdout`` using ``\\r`` to overwrite the current line.
    Background thread cycles frames at 120ms intervals.
    Testable via ``io.StringIO`` injection.
    """

    def __init__(self, stdout: TextIO | None = None, mode: OutputMode = OutputMode.UNICODE) -> None:
        self._stdout: TextIO = stdout or sys.stdout
        self._mode = mode
        self._active: bool = False
        self._index: int = 0
        self._current_label: str = ""
        self._thread: threading.Thread | None = None
        self._lock: threading.Lock = threading.Lock()

        if mode == OutputMode.MINIMAL:
            self._frames = [""]
        elif mode == OutputMode.ACCESSIBLE:
            self._frames = _ACCESSIBLE_FRAMES
        elif mode == OutputMode.ASCII:
            self._frames = _ASCII_FRAMES
        else:
            self._frames = _BRAILLE_FRAMES

    # ── Public API ──────────────────────────────────────────────

    def start(self, label: str) -> None:
        """Write initial spinner frame with label and start animation."""
        self._active = True
        self._current_label = label
        self._index = 0
        self._write_frame()
        self._start_animation()

    def update(self, label: str) -> None:
        """Update label without resetting animation."""
        if not self._active:
            return
        with self._lock:
            self._current_label = label

    def succeed(self, label: str) -> None:
        """Replace spinner with success marker and stop animation."""
        self._active = False
        if self._mode == OutputMode.MINIMAL:
            return
        icon = _success_icon(self._mode)
        self._write_line(f"\r{icon} {label}\n")

    def fail(self, label: str) -> None:
        """Replace spinner with failure marker and stop animation."""
        self._active = False
        if self._mode == OutputMode.MINIMAL:
            return
        icon = _fail_icon(self._mode)
        self._write_line(f"\r{icon} {label}\n")

    def stop(self) -> None:
        """Clear spinner line without leaving a result."""
        self._active = False
        self._write_line("\r\033[K")

    # ── Animation ───────────────────────────────────────────────

    def _start_animation(self) -> None:
        """Start background thread to cycle frames."""
        if len(self._frames) <= 1:
            return  # nothing to animate
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def _animate(self) -> None:
        """Cycle spinner frames at 120ms intervals until deactivated."""
        interval = 0.12
        while self._active:
            time.sleep(interval)
            if not self._active:
                break
            self._index = (self._index + 1) % len(self._frames)
            self._write_frame()

    # ── Internal ────────────────────────────────────────────────

    def _write_frame(self) -> None:
        with self._lock:
            if not self._active:
                return
            frame = self._frames[self._index]
            label = self._current_label
            self._stdout.write(f"\r{frame} {label}")
            self._stdout.flush()

    def _write_line(self, text: str) -> None:
        with self._lock:
            self._stdout.write(text)
            self._stdout.flush()


def _success_icon(mode: OutputMode) -> str:
    if mode == OutputMode.ACCESSIBLE:
        return "[PASS]"
    if mode == OutputMode.ASCII:
        return "[OK]"
    return "✓"


def _fail_icon(mode: OutputMode) -> str:
    if mode == OutputMode.ACCESSIBLE:
        return "[FAIL]"
    if mode == OutputMode.ASCII:
        return "[X]"
    return "✗"
