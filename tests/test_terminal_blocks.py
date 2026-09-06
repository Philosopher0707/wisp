import pytest  # noqa: F401 -- Task 2
import wisp.transport.renderer as R  # noqa: F401 -- Task 2
from wisp.terminal_width import OutputMode  # noqa: F401 -- Task 2
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


from wisp.cli.ui.blocks import ScreenModel


def test_append_prune_and_collapsed_set():
    m = ScreenModel(cap=3)
    for i in range(5):
        m.append("log", {"text": f"line {i}"})
    assert [b.payload["text"] for b in m.blocks] == ["line 2", "line 3", "line 4"]
    assert [b.id for b in m.blocks] == ["blk-2", "blk-3", "blk-4"]


def test_toggle_collapse_and_newest():
    m = ScreenModel()
    m.append("log", {"text": "a"})
    b = m.append("thought", {"text": "hmm"})
    assert m.is_collapsed(b.id) is True
    m.toggle(b.id)
    assert m.is_collapsed(b.id) is False
    assert m.toggle_newest_collapsible().id == b.id
    assert m.is_collapsed(b.id) is True


def test_viewport_telemetry_pending_gate_fields():
    m = ScreenModel()
    assert m.viewport == "scrollback"
    m.viewport = "altscreen"
    m.telemetry = {"state": "EXECUTING"}
    m.pending_gate = {"kind": "tool", "name": "run_bash"}
    assert m.telemetry["state"] == "EXECUTING"
    assert m.pending_gate["name"] == "run_bash"


def test_thought_row_all_modes():
    with _mode(OutputMode.UNICODE):
        s = R.render_thought_row("Planning next step", 1.2, True, 80)
        assert "\u25b8" in s and "1.2s" in s and "Planning next step" in s
    with _mode(OutputMode.ASCII):
        assert ">" in R.render_thought_row("Planning", 1.2, True, 80)
    with _mode(OutputMode.ACCESSIBLE):
        from wisp.terminal_width import status_symbols as syms
        assert syms()["thinking"] in R.render_thought_row("Planning", 1.2, True, 80)
    with _mode(OutputMode.MINIMAL):
        assert "think:" in R.render_thought_row("Planning", 1.2, True, 80)


def test_tool_head_and_done_rows_all_modes():
    from wisp.terminal_width import status_symbols as syms
    with _mode(OutputMode.UNICODE):
        h = R.render_tool_head("read_file", {"path": "a.py"}, 80)
        assert "read_file" in h and "a.py" in h
        d = R.render_tool_done("read_file", 12.3, True, "64 lines read", 80)
        assert "read_file" in d and "12ms" in d and "64 lines read" in d
    with _mode(OutputMode.ASCII):
        assert "[OK]" in R.render_tool_done("read_file", 5, True, "ok", 80)
        assert "!" in R.render_tool_head("read_file", {}, 80)
    with _mode(OutputMode.ACCESSIBLE):
        assert syms()["ok"] in R.render_tool_done("read_file", 5, True, "ok", 80)
        assert "[TOOL]" in R.render_tool_head("read_file", {}, 80)
    with _mode(OutputMode.MINIMAL):
        assert R.render_tool_done("read_file", 5, True, "ok", 80).startswith("    ok")
    with _mode(OutputMode.UNICODE):
        assert "x" in R.render_tool_head("x", None, 80)
        h4 = R.render_tool_head("x", {"a": 1, "b": 2, "c": 3, "d": 4}, 80)
        assert "..." in h4
        f = R.render_tool_done("x", 1, False, "boom", 80)
        assert "\u2716" in f and "boom" in f
        assert "None" not in R.render_tool_done("x", 1, True, None, 80)
    with _mode(OutputMode.ACCESSIBLE):
        f = R.render_tool_done("x", 1, False, "boom", 80)
        assert syms()["fail"] in f and "boom" in f


def test_reduce_returns_affected_blocks():
    from wisp.cli.ui.blocks import ScreenModel, reduce_event
    m = ScreenModel()
    r1 = reduce_event(m, {"type": "thinking", "data": {"text": "hmm"}})
    assert len(r1) == 1 and r1[0].kind == "thought" and r1[0].id == m.blocks[0].id
    r2 = reduce_event(m, {"type": "tool_call", "data": {"name": "x", "arguments": {}}})
    assert len(r2) == 1 and r2[0].payload["status"] == "running"
    r3 = reduce_event(m, {"type": "tool_result", "data": {"result": "ok"}})
    assert len(r3) == 1 and r3[0] is r2[0] and r3[0].payload["status"] == "done"
    assert [b.kind for b in m.blocks] == ["thought", "tool"]
    assert m.is_collapsed(m.blocks[0].id) is True
    assert reduce_event(m, {"type": "unknown-bogus"}) == []

def test_block_timestamps_monotonic():
    import time
    from wisp.cli.ui.blocks import ScreenModel
    m = ScreenModel()
    a = m.append("log", {"text": "a"})
    assert a.ts <= time.monotonic()
    b = m.append("log", {"text": "b"})
    assert b.ts >= a.ts


def test_cli_event_renderer_appends_blocks():
    from wisp.cli.repl import CLIEventRenderer
    from wisp.cli.ui.blocks import ScreenModel
    from unittest.mock import MagicMock
    m = ScreenModel()
    t = MagicMock()
    r = CLIEventRenderer(t, model=m)
    r.render_event(MagicMock(), {"type": "thinking", "data": {"text": "hmm"}})
    assert [b.kind for b in m.blocks] == ["thought"]
    t._render_event.assert_called_once()


def test_repl_wiring_present_in_source():
    import inspect
    import wisp.cli.repl as repl
    src_full = inspect.getsource(repl)
    assert "_injected_model" in src_full and "or ScreenModel()" in src_full
    assert "make_input_fn(model=" in src_full
    src_fn = inspect.getsource(repl.make_input_fn)
    assert "toggle_newest_collapsible" in src_fn
    assert "key_bindings" in src_fn


def test_cli_event_renderer_without_model_is_noop_for_model_path():
    from wisp.cli.repl import CLIEventRenderer
    from unittest.mock import MagicMock
    t = MagicMock()
    r = CLIEventRenderer(t)
    assert r._model is None
    r.render_event(MagicMock(), {"type": "thinking", "data": {"text": "hmm"}})
    t._render_event.assert_called_once()


def test_tool_result_completes_running_tool_block():
    from wisp.cli.ui.blocks import ScreenModel, reduce_event
    m = ScreenModel()
    reduce_event(m, {"type": "tool_call", "data": {"name": "read_file", "arguments": {"path": "a.py"}}})
    assert m.blocks[0].payload["status"] == "running"
    reduce_event(m, {"type": "tool_result", "data": {"summary": "x" * 200}})
    assert m.blocks[0].payload["status"] == "done"
    assert m.blocks[0].payload["summary"] == "x" * 120
    m2 = ScreenModel()
    reduce_event(m2, {"type": "tool_call", "data": {"name": "run_bash", "arguments": {}}})
    reduce_event(m2, {"type": "tool_result", "data": {"summary": None, "result": "fallback"}})
    assert m2.blocks[0].payload["summary"] == "fallback"
    m3 = ScreenModel()
    reduce_event(m3, {"type": "tool_call", "data": {"name": "run_bash", "arguments": {}}})
    reduce_event(m3, {"type": "tool_result", "data": {"summary": None}})
    assert m3.blocks[0].payload["summary"] == ""


def test_repl_runner_reuses_injected_model():
    import asyncio
    from unittest.mock import MagicMock
    from wisp.cli.dispatcher import Dispatcher
    from wisp.cli.repl import CLIEventRenderer, ReplRunner
    from wisp.cli.ui.blocks import ScreenModel
    m = ScreenModel()
    r = CLIEventRenderer(MagicMock(), model=m)
    loop = asyncio.new_event_loop()
    try:
        runner = ReplRunner(
            runtime=MagicMock(),
            transport=MagicMock(),
            renderer=r,
            dispatcher=Dispatcher(),
            config=MagicMock(),
            session={"id": "s1"},
            loop=loop,
            input_fn=lambda prompt: "exit",
        )
        assert runner.screen_model is m
        assert runner.renderer is r
    finally:
        loop.close()
