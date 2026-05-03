"""Tests for wisp.error_diagnosis — error classification and diagnosis."""

import pytest

from wisp.error_diagnosis import (
    Diagnosis,
    diagnose,
    extract_error_message,
    parse_traceback,
)


class TestParseTraceback:
    """Unit tests for traceback parsing."""

    def test_parse_simple_traceback(self):
        output = """Traceback (most recent call last):
  File \"/path/to/file.py\", line 42, in my_function
    result = 1 / 0
ZeroDivisionError: division by zero
"""
        file_path, line, func = parse_traceback(output)
        assert file_path == "/path/to/file.py"
        assert line == 42
        assert func == "my_function"

    def test_parse_multiple_frames(self):
        output = """Traceback (most recent call last):
  File \"/a.py\", line 10, in outer
    inner()
  File \"/b.py\", line 20, in inner
    raise ValueError("bad")
ValueError: bad
"""
        file_path, line, func = parse_traceback(output)
        assert file_path == "/b.py"
        assert line == 20
        assert func == "inner"

    def test_parse_no_traceback(self):
        file_path, line, func = parse_traceback("just some text")
        assert file_path == ""
        assert line == 0


class TestExtractErrorMessage:
    """Unit tests for error message extraction."""

    def test_extract_exception(self):
        output = "Some text\nValueError: invalid value\n"
        msg = extract_error_message(output)
        assert "ValueError" in msg

    def test_extract_test_failure(self):
        output = "tests/test_foo.py::test_bar FAILED\n"
        msg = extract_error_message(output)
        assert "FAILED" in msg

    def test_extract_last_line_fallback(self):
        output = "line1\nline2\nfinal line"
        msg = extract_error_message(output)
        assert msg == "final line"


class TestDiagnose:
    """Unit tests for the diagnosis engine."""

    def test_diagnose_import_error(self):
        output = "ImportError: cannot import name 'foo' from 'bar'"
        diag = diagnose(output)
        assert diag.error_type == "ImportError"
        assert "foo" in diag.suggestion

    def test_diagnose_module_not_found(self):
        output = "ModuleNotFoundError: No module named 'nonexistent'"
        diag = diagnose(output)
        assert diag.error_type == "ModuleNotFoundError"
        assert "pip install" in diag.suggestion

    def test_diagnose_attribute_error(self):
        output = "AttributeError: 'str' object has no attribute 'append'"
        diag = diagnose(output)
        assert diag.error_type == "AttributeError"
        assert "spelling" in diag.suggestion

    def test_diagnose_syntax_error(self):
        output = "SyntaxError: invalid syntax"
        diag = diagnose(output)
        assert diag.error_type == "SyntaxError"
        assert "brackets" in diag.suggestion

    def test_diagnose_indentation_error(self):
        output = "IndentationError: unexpected indent"
        diag = diagnose(output)
        assert diag.error_type == "IndentationError"
        assert "tabs/spaces" in diag.suggestion

    def test_diagnose_key_error(self):
        output = "KeyError: 'missing_key'"
        diag = diagnose(output)
        assert diag.error_type == "KeyError"
        assert "missing_key" in diag.suggestion

    def test_diagnose_index_error(self):
        output = "IndexError: list index out of range"
        diag = diagnose(output)
        assert diag.error_type == "IndexError"
        assert "length" in diag.suggestion

    def test_diagnose_assertion_error(self):
        output = "AssertionError: assert 1 == 2"
        diag = diagnose(output)
        assert diag.error_type == "AssertionError"
        assert "Test expectation" in diag.suggestion

    def test_diagnose_file_not_found(self):
        output = "FileNotFoundError: [Errno 2] No such file or directory: 'missing.txt'"
        diag = diagnose(output)
        assert diag.error_type == "FileNotFoundError"
        assert "missing.txt" in diag.suggestion

    def test_diagnose_name_error(self):
        output = "NameError: name 'undefined_var' is not defined"
        diag = diagnose(output)
        assert diag.error_type == "NameError"
        assert "undefined_var" in diag.suggestion

    def test_diagnose_zero_division(self):
        output = "ZeroDivisionError: division by zero"
        diag = diagnose(output)
        assert diag.error_type == "ZeroDivisionError"
        assert "zero" in diag.suggestion

    def test_diagnose_recursion(self):
        output = "RecursionError: maximum recursion depth exceeded"
        diag = diagnose(output)
        assert diag.error_type == "RecursionError"
        assert "recursion" in diag.suggestion

    def test_diagnose_test_failure(self):
        output = "tests/test_app.py::test_login FAILED\nAssertionError: assert 200 == 401"
        diag = diagnose(output)
        assert diag.error_type == "TestFailure"
        assert "test_login" in diag.likely_cause

    def test_diagnose_empty(self):
        diag = diagnose("")
        assert diag.error_type == "None"

    def test_diagnose_unknown(self):
        diag = diagnose("Some random error that doesn't match anything")
        assert diag.error_type == "Unknown"

    def test_diagnosis_format(self):
        diag = Diagnosis(
            error_type="ValueError",
            message="bad value",
            suggestion="Check the input",
            failing_file="app.py",
            failing_line=10,
        )
        text = diag.format()
        assert "ValueError" in text
        assert "app.py:10" in text
        assert "Check the input" in text

    def test_diagnose_with_traceback(self):
        output = """Traceback (most recent call last):
  File \"/project/app.py\", line 25, in handler
    data = json.loads(raw)
json.decoder.JSONDecodeError: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)
"""
        diag = diagnose(output)
        assert diag.error_type == "JSONDecodeError"
        assert diag.failing_file == "/project/app.py"
        assert diag.failing_line == 25
