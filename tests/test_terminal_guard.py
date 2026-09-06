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


def test_context_slice_and_legend_render():
    with _mode(OutputMode.UNICODE):
        s = R.render_context_slice({"task": "migrate auth", "files": ["a.py", "b.py"]})
        assert "migrate auth" in s and "a.py" in s
        legend = R.render_gate_legend("spawn")
        assert "y" in legend and "n" in legend and "v" in legend

def test_run_spawn_gate_view_then_approve(monkeypatch):
    import wisp.transport.renderer as rend
    from wisp.cli.ui import guard as G
    keys = iter(["v", "y"])
    monkeypatch.setattr(G, "read_gate_key", lambda timeout_s=30.0: next(keys))
    seen = []
    monkeypatch.setattr(rend, "render_context_slice", lambda p: seen.append(p) or "CTX")
    assert G.run_spawn_gate({"task": "t"}) == "approve"
    assert seen == [{"task": "t"}]

def test_run_spawn_gate_help_repeatable(monkeypatch):
    from wisp.cli.ui import guard as G
    keys = iter(["?", "?", "n"])
    monkeypatch.setattr(G, "read_gate_key", lambda timeout_s=30.0: next(keys))
    assert G.run_spawn_gate({"task": "t"}) == "reject"

def test_run_spawn_gate_renders_slice_and_failcloses(monkeypatch, capsys):
    from wisp.cli.ui import guard as G
    keys = iter(["v", "n"])
    monkeypatch.setattr(G, "read_gate_key", lambda timeout_s=30.0: next(keys))
    assert G.run_spawn_gate({"task": "migrate auth", "files": ["a.py"]}) == "reject"
    assert "Scoped context" in capsys.readouterr().err
    monkeypatch.setattr(G, "read_gate_key", lambda timeout_s=30.0: "")
    assert G.run_spawn_gate({"task": "t"}) == "reject"
    monkeypatch.setattr(G, "read_gate_key", lambda timeout_s=30.0: "q")
    assert G.run_spawn_gate({"task": "t"}) == "reject"


def test_maybe_arm_gate_noop_off_tty(monkeypatch):
    import sys
    import types
    from wisp.cli.ui.guard import maybe_arm_gate
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    t = types.SimpleNamespace()
    assert maybe_arm_gate(t) is None
    assert not hasattr(t, "_gate_key_reader")

def test_maybe_arm_gate_arms_on_tty(monkeypatch):
    import sys
    import types
    from wisp.cli.ui import guard as G
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdin, "fileno", lambda: 7)
    monkeypatch.setitem(sys.modules, "termios", types.ModuleType("termios"))
    sys.modules["termios"].TCSADRAIN = 1
    sys.modules["termios"].tcgetattr = lambda fd: ["a"]
    sys.modules["termios"].tcsetattr = lambda fd, w, a: None
    t = types.SimpleNamespace()
    g = G.maybe_arm_gate(t)
    assert g is not None
    assert t._gate_key_reader is G.read_gate_key
    g.restore()


def test_sigint_two_stage_mechanism_pinned():
    """First SIGINT cancels _turn_task and re-arms the default handler; the
    second raises KeyboardInterrupt into the shutdown path. No custom exit-130."""
    import inspect
    import wisp.cli.repl as repl
    src = inspect.getsource(repl.ReplRunner._on_sigint)
    assert "task.cancel()" in src
    assert "default_int_handler" in src
    assert "KeyboardInterrupt" in src

def test_ctrl_c_byte_maps_to_keyboard_interrupt():
    """Raw-mode \x03 arrivals already mean KeyboardInterrupt (existing mapping)."""
    import pytest as _pytest
    from wisp.cli.approval import prompt_for_approval
    with _pytest.raises(KeyboardInterrupt):
        prompt_for_approval("\x03")
