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


def test_diff_aggregate_all_modes_with_offer():
    with _mode(OutputMode.UNICODE):
        s = R.render_diff_aggregate([("a.py", 30, 10), ("b.py", 12, 2)])
        assert "2 files" in s and "+42" in s and "-12" in s and "[v]" in s
    with _mode(OutputMode.ASCII):
        s = R.render_diff_aggregate([("a.py", 30, 10)])
        assert "diff:" in s and "[v]" in s
    with _mode(OutputMode.ACCESSIBLE):
        s = R.render_diff_aggregate([("a.py", 30, 10)])
        assert "[DIFF]" in s and "[v]" in s
    with _mode(OutputMode.MINIMAL):
        s = R.render_diff_aggregate([("a.py", 30, 10)])
        assert s.startswith("  diff:") and "[v]" in s


def test_summarize_uses_diff_viewer_counts():
    from wisp.cli.ui.pager import summarize_diffs, should_page_diff
    assert summarize_diffs([("a.py", "x\n", "x\ny\n")]) == [("a.py", 1, 0)]
    pairs = [("a.py", "x\n", "x\n" + "l\n" * 50)]
    counts = summarize_diffs(pairs)
    assert should_page_diff(pairs) == (sum(a + r for _, a, r in counts) > 60)
    assert should_page_diff([("a.py", "x\n", "x\ny\n")]) is False
    from wisp.cli.ui.pager import DIFF_PAGE_THRESHOLD
    big = [("a.py", "x\n", "x\n" + "l\n" * 61)]
    assert should_page_diff(big) is True
    assert summarize_diffs(big) == [("a.py", 61, 0)]
    edge = [("a.py", "x\n", "x\n" + "l\n" * 60)]
    assert summarize_diffs(edge) == [("a.py", 60, 0)]
    assert should_page_diff(edge) is (60 > DIFF_PAGE_THRESHOLD)


def test_pager_key_verdicts_full_mapping():
    from wisp.cli.ui.pager import _pager_verdict
    assert _pager_verdict("y") == "apply"
    assert _pager_verdict("Y") == "apply"
    assert _pager_verdict("n") == "abort"
    assert _pager_verdict("N") == "abort"
    assert _pager_verdict("q") == "abort"
    assert _pager_verdict("Q") == "abort"
    assert _pager_verdict("escape") == "abort"
    assert _pager_verdict("x") is None

def test_pager_refuses_without_tty(monkeypatch):
    import sys
    from wisp.cli.ui import pager as pg
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert pg.show_diff([("a.py", "x\n", "y\n")]) == "abort"

def test_pager_module_top_has_no_textual_import():
    import pathlib
    import wisp.cli.ui.pager as pg
    top = pathlib.Path(pg.__file__).read_text().split("def show_diff")[0]
    assert "textual" not in top

def test_pager_missing_textual_aborts(monkeypatch):
    import sys
    import wisp.cli.ui.pager as pg
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setitem(sys.modules, "textual.app", None)
    assert pg.show_diff([("a.py", "x\n", "y\n")]) == "abort"


def test_v_key_emits_open_pager_with_texts():
    from wisp.cli.ui.blocks import ScreenModel, diff_pager_effect
    m = ScreenModel()
    m.append("log", {"text": "hi"})
    assert diff_pager_effect(m, "v") is None
    m.append("diff", {"files": [("a.py", "x\n", "x\n" + "l\n" * 70)]})
    effect = diff_pager_effect(m, "v")
    assert effect is not None and effect[0] == "open_pager"
    assert effect[1][0][0] == "a.py"
    assert diff_pager_effect(m, "x") is None


def test_v_binding_consumes_open_pager_effect():
    import inspect
    import wisp.cli.repl as repl
    src = inspect.getsource(repl.make_input_fn)
    assert "diff_pager_effect" in src
    assert "show_diff" in src
    assert "enter_alt" not in src  # single owner: Textual manages alt-screen
    assert "TerminalGuard" not in src
