"""Terminal spinner with inline updates via \\r.

Mode-aware: braille spinner in unicode, ascii art in ascii mode,
text label in accessible, silent in minimal.
"""

from __future__ import annotations

import sys
from typing import TextIO

from wisp.terminal_width import OutputMode

_BRAILLE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_ASCII_FRAMES = ["|", "/", "-", "\\"]
_ACCESSIBLE_FRAMES = ["[busy]"]


class Spinner:
    """Inline terminal spinner for tool execution feedback.

    Writes to ``stdout`` using ``\\r`` to overwrite the current line.
    Testable via ``io.StringIO`` injection.
    """

    def __init__(self, stdout: TextIO | None = None, mode: OutputMode = OutputMode.UNICODE) -> None:
        self._stdout: TextIO = stdout or sys.stdout
        self._mode = mode
        self._active: bool = False
        self._index: int = 0
        self._current_label: str = ""

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
        """Write initial spinner frame with label."""
        self._active = True
        self._current_label = label
        self._index = 0
        self._write_frame()

    def update(self, label: str) -> None:
        """Cycle to next frame, optionally updating label."""
        if not self._active:
            return
        self._current_label = label
        self._index = (self._index + 1) % len(self._frames)
        self._write_frame()

    def succeed(self, label: str) -> None:
        """Replace spinner with success marker."""
        self._active = False
        if self._mode == OutputMode.MINIMAL:
            return
        icon = _success_icon(self._mode)
        self._write_line(f"{icon} {label}\n")

    def fail(self, label: str) -> None:
        """Replace spinner with failure marker."""
        self._active = False
        if self._mode == OutputMode.MINIMAL:
            return
        icon = _fail_icon(self._mode)
        self._write_line(f"{icon} {label}\n")

    def stop(self) -> None:
        """Clear spinner line without leaving a result."""
        if not self._active:
            return
        self._active = False
        self._write_line("\r\033[K")

    # ── Internal ────────────────────────────────────────────────

    def _write_frame(self) -> None:
        frame = self._frames[self._index]
        self._write_line(f"\r{frame} {self._current_label}")

    def _write_line(self, text: str) -> None:
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
