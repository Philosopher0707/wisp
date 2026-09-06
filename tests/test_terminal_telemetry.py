import wisp.transport.renderer as R
from wisp.terminal_width import OutputMode
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


def test_telemetry_line_all_modes():
    stats = {"turn_number": 7, "tools_run": 5, "tools_succeeded": 4,
             "tools_failed": 1, "files_changed": ["a.py"],
             "elapsed": 18.2, "ctx_tokens": 18200, "ctx_limit": 200000}
    with _mode(OutputMode.UNICODE):
        s = R.render_telemetry_line("EXECUTING", stats, "main", 100)
        assert "EXECUTING" in s and "main" in s and "18.2" in s and "ctx" in s
    with _mode(OutputMode.ASCII):
        assert "EXECUTING" in R.render_telemetry_line("EXECUTING", stats, "main", 100)
    with _mode(OutputMode.ACCESSIBLE):
        assert "EXECUTING" in R.render_telemetry_line("EXECUTING", stats, "main", 100)
    with _mode(OutputMode.MINIMAL):
        s = R.render_telemetry_line("EXECUTING", stats, "main", 100)
        assert "EXECUTING" in s and "ctx" not in s
