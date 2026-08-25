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

from wisp.terminal_width import OutputMode, display_width, truncate

_BRAILLE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_ASCII_FRAMES = ["|", "/", "-", "\\"]
_ACCESSIBLE_FRAMES = ["[busy]"]



def truncate_spinner_label(
    label: str, width: int | None = None, unicode_ok: bool = True
) -> str:
    """Clip a spinner label to the terminal width, wide-char aware.

    The frame + space + result suffix need room too; an overflowing
    label wraps and destroys the single-line \r redraw.
    """
    if width is None:
        try:
            import shutil
            width = shutil.get_terminal_size().columns
        except Exception:
            width = 80
    from wisp.terminal_width import truncate as _tw_truncate
    suffix = "\u2026" if unicode_ok else "..."
    return _tw_truncate(label, max(10, width - 8), suffix=suffix)


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
        self._current_label = truncate_spinner_label(
            label,
            unicode_ok=self._mode == OutputMode.UNICODE,
        )
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
            self._write_line("\r\033[K\n")
            return
        icon = _success_icon(self._mode)
        # Clear to EOL: the result line is usually SHORTER than the running
        # label (name+args), and leftover characters otherwise stay visible —
        # seen live as '✓ web_fetch 403ms=500' (tail of '...limit=500').
        self._write_line(f"\r{icon} {label}\033[K\n")

    def fail(self, label: str) -> None:
        """Replace spinner with failure marker and stop animation."""
        self._active = False
        if self._mode == OutputMode.MINIMAL:
            self._write_line("\r\033[K\n")
            return
        icon = _fail_icon(self._mode)
        self._write_line(f"\r{icon} {label}\033[K\n")

    def stop(self) -> None:
        """Clear spinner line without leaving a result."""
        self._active = False
        if self._mode == OutputMode.MINIMAL:
            self._write_line("\r\033[K\n")
            return
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
            # Truncate to terminal width so \r can overwrite a single physical line.
            # Long labels (e.g. bash commands) wrap across multiple lines and \r
            # only returns to the start of the last wrapped line, leaking old frames.
            max_width = _term_width()
            prefix = f"{frame} "
            max_label = max_width - display_width(prefix) - 1
            if max_label < 10:
                max_label = 10
            if display_width(label) > max_label:
                label = truncate(label, max_label)
            try:
                self._stdout.write(f"\r{prefix}{label}\033[K")
                self._stdout.flush()
            except (ValueError, OSError):
                # Stream closed between the _active check and the write —
                # the animation thread lost the race with stop()/exit.
                # Silence this frame; the thread exits on its next tick.
                self._active = False

    def _write_line(self, text: str) -> None:
        with self._lock:
            try:
                self._stdout.write(text)
                self._stdout.flush()
            except (ValueError, OSError):
                # Terminal went away (interpreter shutdown, closed capture
                # buffer) — nothing sensible to do with the line.
                self._active = False


def _term_width() -> int:
    try:
        import shutil
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


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
