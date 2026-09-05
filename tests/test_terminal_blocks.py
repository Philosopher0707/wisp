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
