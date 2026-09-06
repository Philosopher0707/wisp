import os  # noqa: F401  # Tasks 10-14 forward import
import pytest  # noqa: F401  # Tasks 10-14 forward import
import wisp.transport.renderer as R  # noqa: F401  # Tasks 10-14 forward import
from wisp.terminal_width import OutputMode  # noqa: F401  # Tasks 10-14 forward import
from contextlib import contextmanager


@contextmanager
def _mode(m):
    from wisp.terminal_width import get_output_mode, set_output_mode
    prev = get_output_mode()
    set_output_mode(m)
    try:
        yield
    finally:
        set_output_mode(prev)


def test_guard_restores_saved_attrs_without_tty(monkeypatch):
    """Stub termios: restore() must tcsetattr the saved attrs even headless."""
    import sys
    import types
    calls = {}
    fake = types.ModuleType("termios")
    fake.TCSADRAIN = 1
    fake.tcgetattr = lambda fd: ["saved-attrs"]
    def tcsetattr(fd, when, attrs):
        calls["restored"] = attrs
    fake.tcsetattr = tcsetattr
    monkeypatch.setitem(sys.modules, "termios", fake)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdin, "fileno", lambda: 7)
    from wisp.cli.ui.guard import TerminalGuard
    with TerminalGuard():
        pass
    assert calls.get("restored") == ["saved-attrs"]

def test_guard_cursor_show_targets_tty_stderr(monkeypatch, capsys):
    import sys
    from wisp.cli.ui.guard import TerminalGuard
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    TerminalGuard().restore()
    assert "\x1b[?25h" in capsys.readouterr().err
