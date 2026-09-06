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


def test_ndjson_event_schema_matches_canonical():
    import json
    s = R.render_ndjson_event({"type": "tool_call", "data": {"name": "read_file"},
                               "trace_id": "t1", "span_id": "s1"})
    obj = json.loads(s)
    assert obj["type"] == "tool_call"
    assert isinstance(obj["timestamp"], float)
    assert obj["schema_version"] == 1
    assert obj["data"]["name"] == "read_file"
    assert obj["trace_id"] == "t1" and obj["span_id"] == "s1"


def test_paint_status_pauses_spinner_writes_crlf_clear():
    """Pause ACTIVE_SPINNER across the write; sequence \r + text + \x1b[K."""
    import io
    from wisp.cli.ui.guard import paint_status
    import wisp.transport.spinner as sp
    order = []
    class FakeSpinner:
        def pause(self): order.append("pause")
        def resume(self): order.append("resume")
    sp.ACTIVE_SPINNER = FakeSpinner()
    try:
        out = io.StringIO()
        paint_status(out, "EXECUTING | turn 7")
        assert order == ["pause", "resume"]
        assert out.getvalue() == "\rEXECUTING | turn 7\x1b[K"
    finally:
        sp.ACTIVE_SPINNER = None


def test_spinner_collapse_uses_cr_and_el():
    """Spinner.succeed/fail finalize in place with \\r + \\x1b[K (no scrollback smear)."""
    import inspect
    import wisp.transport.spinner as sp
    src = inspect.getsource(sp.Spinner.succeed) + inspect.getsource(sp.Spinner.fail)
    assert r"\r" in src and (r"\x1b[K" in src or r"\033[K" in src)

def test_paint_status_uses_cr_and_el():
    """paint_status shares the same \\r + \\x1b[K contract (no silent divergence)."""
    import inspect
    import wisp.cli.ui.guard as G
    src = inspect.getsource(G.paint_status)
    assert r"\r" in src and (r"\x1b[K" in src or r"\033[K" in src)
