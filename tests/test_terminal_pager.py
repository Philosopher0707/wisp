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
