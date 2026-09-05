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
