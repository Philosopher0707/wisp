"""Tests for wisp.test_distill (GH#24)."""

from wisp.test_distill import distill_traceback

TB_SHORT = """\
=================================== FAILURES ===================================
_______________________________ test_sum_to ________________________________

tests/test_math.py:9: in test_sum_to
    assert sum_to(3) == 7
E   AssertionError: assert 6 == 7
E    +  where 6 = sum_to(3)

=========================== warnings summary ============================
tests/test_math.py:1
  /some/path.py:12: DeprecationWarning: old api
    import old

---------------------------- Captured stdout call -----------------------------
spam line one
spam line two

=========================== short test summary info ============================
FAILED tests/test_math.py::test_sum_to - AssertionError: assert 6 == 7
============================== 1 failed in 0.42s ==============================
"""

TB_LONG = """\
Traceback (most recent call last):
  File "/repo/wisp/tools/filesystem.py", line 44, in read_file
    return path.read_text()
  File "/usr/lib/python3.11/pathlib.py", line 1059, in read_text
    return f.read()
TypeError: read_text() takes 1 positional argument but 2 were given
"""

TB_CHAINED = """\
tests/test_db.py:20: in test_save
    repo.save(obj)
wisp/db.py:55: in save
    raise ConnectionError("down")
E   ConnectionError: down

During handling of the above exception, another exception occurred:
tests/test_db.py:22: in test_save
    assert exc.value.retries == 3
E   assert 0 == 3
E   AssertionError: assert 0 == 3
"""


class TestDistillShort:
    def test_keeps_frame_assertion_and_exception(self):
        out = distill_traceback(TB_SHORT)
        assert "tests/test_math.py:9 in test_sum_to" in out
        assert "assert sum_to(3) == 7" in out
        assert "AssertionError: assert 6 == 7" in out
        assert "test_sum_to" in out

    def test_drops_noise_sections(self):
        out = distill_traceback(TB_SHORT)
        assert "DeprecationWarning" not in out
        assert "spam line" not in out
        assert "warnings summary" not in out
        assert "Captured stdout" not in out
        assert "===" not in out

    def test_keeps_failed_header(self):
        out = distill_traceback(TB_SHORT)
        assert "FAILED tests/test_math.py::test_sum_to" in out


class TestDistillLong:
    def test_long_frames_normalized(self):
        out = distill_traceback(TB_LONG)
        assert "/repo/wisp/tools/filesystem.py:44 in read_file" in out
        assert "TypeError: read_text()" in out
        assert "Traceback (most recent call last):" not in out


class TestDistillChained:
    def test_both_exceptions_and_marker_kept(self):
        out = distill_traceback(TB_CHAINED)
        assert "ConnectionError: down" in out
        assert "AssertionError: assert 0 == 3" in out
        assert "During handling of the above exception" in out


class TestDistillDegenerate:
    def test_empty(self):
        assert distill_traceback("") == ""
        assert distill_traceback("   \n  ") == ""

    def test_bare_exception_line_passes_through(self):
        assert "Error: boom" in distill_traceback("Error: boom")

    def test_unrecognized_input_truncated_not_lost(self):
        blob = "some random tool spam\n" * 200
        out = distill_traceback(blob, max_chars=100)
        assert out.startswith("some random tool spam")
        assert "truncated" in out

    def test_many_frames_elided_with_marker(self):
        frames = "".join(
            f"pkg/mod{i}.py:{i}: in func{i}\n    do_something({i})\n"
            for i in range(30)
        )
        out = distill_traceback(frames + "ValueError: boom\n")
        assert "frames elided" in out
        assert "pkg/mod0.py:0 in func0" in out
        assert "pkg/mod29.py:29 in func29" in out
        assert "ValueError: boom" in out


class TestFormatForLlmWiring:
    def test_wiring_distills_not_dumb_truncates(self):
        from wisp.test_runner import UnitTestResult, UnitTestRunSummary

        long_tb = TB_SHORT + ("E   filler detail line\n" * 10)
        summary = UnitTestRunSummary(
            total=2, passed=1, failed=1, duration=1.0,
            results=[UnitTestResult("test_x", "failed", 0.5, traceback=long_tb)],
        )
        text = summary.format_for_llm()
        assert "AssertionError: assert 6 == 7" in text
        assert "DeprecationWarning" not in text
        assert "spam line" not in text
        assert "truncated)" not in text  # distilled, not cut mid-frame
