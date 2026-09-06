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


def test_aliases_map_to_shipped_verdicts_only():
    from wisp.cli.ui.guard import resolve_gate_alias
    assert resolve_gate_alias("spawn", "y") == "approve"
    assert resolve_gate_alias("spawn", "n") == "reject"
    assert resolve_gate_alias("spawn", "v") == "view"
    assert resolve_gate_alias("spawn", "") is None
    assert resolve_gate_alias("spawn", "zzz") is None
    assert resolve_gate_alias("tool", "e") is None  # dropped: no verdict mapping
    assert resolve_gate_alias("tool", "r") is None
    # Shipped Y/a/N/d/c keys are never remapped here (prompt_for_approval owns them)
    for k in ("Y", "a", "N", "d", "c"):
        assert resolve_gate_alias("spawn", k) is None


def test_read_gate_key_line_fallback():
    """Non-selectable stdin (StringIO): first char of the line wins."""
    import io
    import sys
    from wisp.cli.ui.guard import read_gate_key
    real_stdin = sys.stdin
    sys.stdin = io.StringIO("y\n")
    try:
        assert read_gate_key() == "y"
    finally:
        sys.stdin = real_stdin

def test_read_gate_key_pty(monkeypatch):
    """PTY: single keystroke returned without Enter."""
    if os.name != "posix":
        pytest.skip("POSIX only")
    import pty
    import sys as _sys
    master, slave = pty.openpty()
    r = os.fdopen(slave, "r")
    monkeypatch.setattr(_sys, "stdin", r)
    os.write(master, b"n")
    try:
        from wisp.cli.ui.guard import read_gate_key
        assert read_gate_key(timeout_s=2.0) == "n"
    finally:
        r.close()
        os.close(master)
