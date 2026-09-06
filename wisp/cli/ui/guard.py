"""Terminal state ownership: raw mode, alt-screen, cursor.

All control sequences go to stderr, and only when stderr is a TTY
(stdout is reserved for the --json machine contract). Never leave the TTY dirty.
"""
from __future__ import annotations

import atexit
import sys


class TerminalGuard:
    """Context manager owning all terminal mutations."""

    def __init__(self) -> None:
        self._fd = None
        self._saved_attrs = None
        self._in_alt = False
        self._registered = False

    def __enter__(self) -> "TerminalGuard":
        try:
            if sys.stdin.isatty():
                import termios
                self._fd = sys.stdin.fileno()
                if self._saved_attrs is None:
                    self._saved_attrs = termios.tcgetattr(self._fd)
        except Exception:
            self._fd = None
        if not self._registered:
            atexit.register(self.restore)
            self._registered = True
        return self

    def raw(self) -> None:
        """Single-keystroke mode for gate prompts (tty.setraw).

        ISIG is also disabled while raw, so Ctrl-C arrives as the \x03 byte,
        which prompt_for_approval already maps to KeyboardInterrupt.
        """
        if self._fd is None:
            return
        import tty
        try:
            tty.setraw(self._fd)
        except Exception:
            pass

    def _show_cursor(self) -> None:
        try:
            if sys.stderr.isatty():
                sys.stderr.write("\x1b[?25h")
                sys.stderr.flush()
        except Exception:
            pass

    def restore(self) -> None:
        try:
            atexit.unregister(self.restore)
        except Exception:
            pass
        if self._saved_attrs is not None and self._fd is not None:
            try:
                import termios
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved_attrs)
            except Exception:
                pass
            self._saved_attrs = None
        if self._in_alt:
            self.leave_alt()
        self._show_cursor()

    def enter_alt(self) -> None:
        try:
            if sys.stderr.isatty():
                sys.stderr.write("\x1b[?1049h")
                sys.stderr.flush()
                self._in_alt = True
        except Exception:
            pass

    def leave_alt(self) -> None:
        if self._in_alt:
            self._in_alt = False
            try:
                if sys.stderr.isatty():
                    sys.stderr.write("\x1b[?1049l")
                    sys.stderr.flush()
            except Exception:
                pass

    def __exit__(self, *exc) -> None:
        try:
            atexit.unregister(self.restore)
        except Exception:
            pass
        self.restore()
