"""Tests for tools.py — file ops, security boundaries, validation, bash."""

import pytest
from pathlib import Path
from wisp.tools import (
    tool_read_file,
    tool_write_file,
    tool_edit_file,
    tool_run_bash,
    tool_list_files,
    execute_tool,
    ToolError,
)


class TestToolReadFile:

    def test_read_whole_file(self, temp_workspace, sample_file):
        result = tool_read_file(str(sample_file.name), str(temp_workspace))
        assert result.startswith("line 1\nline 2\n")

    def test_read_with_offset(self, temp_workspace, sample_file):
        result = tool_read_file(str(sample_file.name), str(temp_workspace), offset=2)
        assert result.startswith("line 3")

    def test_read_with_limit(self, temp_workspace, sample_file):
        result = tool_read_file(str(sample_file.name), str(temp_workspace), limit=2)
        assert "line 2" in result
        assert "line 3" not in result
        assert "[showing lines 1-2 of 5]" in result

    def test_read_nonexistent_file(self, temp_workspace):
        with pytest.raises(ToolError, match="File not found"):
            tool_read_file("nope.txt", str(temp_workspace))

    def test_read_path_traversal_blocked(self, temp_workspace):
        with pytest.raises(ToolError, match="Access denied"):
            tool_read_file("/etc/passwd", str(temp_workspace))

    def test_read_relative_traversal_blocked(self, temp_workspace):
        with pytest.raises(ToolError, match="Access denied"):
            tool_read_file("../outside.txt", str(temp_workspace))

    def test_read_empty_path(self, temp_workspace):
        with pytest.raises(ToolError, match="cannot be empty"):
            tool_read_file("", str(temp_workspace))


class TestToolWriteFile:

    def test_write_new_file(self, temp_workspace):
        result = tool_write_file("new.txt", str(temp_workspace), "hello")
        assert "Wrote" in result
        actual = (temp_workspace / "new.txt").read_text()
        assert actual == "hello"

    def test_write_creates_parent_dirs(self, temp_workspace):
        result = tool_write_file("a/b/c/deep.txt", str(temp_workspace), "deep")
        assert "Wrote" in result
        assert (temp_workspace / "a/b/c/deep.txt").exists()

    def test_write_path_traversal_blocked(self, temp_workspace):
        with pytest.raises(ToolError, match="Access denied"):
            tool_write_file("/etc/evil", str(temp_workspace), "bad")

    def test_write_empty_content(self, temp_workspace):
        result = tool_write_file("empty.txt", str(temp_workspace), "")
        assert "Wrote" in result


class TestToolEditFile:

    def test_edit_replacement(self, temp_workspace, sample_file):
        result = tool_edit_file(str(sample_file.name), str(temp_workspace),
                                old_text="line 2", new_text="edited line")
        assert "Edited" in result
        content = sample_file.read_text()
        assert "edited line" in content
        assert "line 2" not in content

    def test_edit_nonexistent_file(self, temp_workspace):
        with pytest.raises(ToolError, match="File not found"):
            tool_edit_file("nope.txt", str(temp_workspace), "x", "y")

    def test_edit_old_text_not_found(self, temp_workspace, sample_file):
        with pytest.raises(ToolError, match="old_text not found"):
            tool_edit_file(str(sample_file.name), str(temp_workspace),
                           old_text="does not exist", new_text="x")

    def test_edit_duplicate_match(self, temp_workspace):
        f = temp_workspace / "dup.txt"
        f.write_text("abc\nabc\n")
        with pytest.raises(ToolError, match="appears 2 times"):
            tool_edit_file("dup.txt", str(temp_workspace), "abc", "xyz")

    def test_edit_path_traversal_blocked(self, temp_workspace):
        with pytest.raises(ToolError, match="Access denied"):
            tool_edit_file("/etc/passwd", str(temp_workspace), "x", "y")


class TestToolRunBash:

    def test_simple_command(self, temp_workspace):
        result = tool_run_bash("echo hello", str(temp_workspace))
        assert "hello" in result

    def test_command_timeout(self, temp_workspace):
        with pytest.raises(ToolError, match="Command timed out"):
            tool_run_bash("sleep 10", str(temp_workspace), timeout=1)

    def test_command_failure(self, temp_workspace):
        result = tool_run_bash("false", str(temp_workspace))
        assert "[exit code: 1]" in result

    def test_stderr_captured(self, temp_workspace):
        result = tool_run_bash("echo err >&2", str(temp_workspace))
        assert "err" in result

    def test_command_too_long(self, temp_workspace):
        with pytest.raises(ToolError, match="too long"):
            tool_run_bash("x" * 5000, str(temp_workspace))

    def test_output_truncated(self, temp_workspace):
        result = tool_run_bash("printf 'a%.0s' {1..15000}", str(temp_workspace))
        assert "[output truncated]" in result
        assert len(result) <= 10050  # 10K max + overhead


class TestToolListFiles:

    def test_list_root(self, temp_workspace, sample_file):
        result = tool_list_files(".", str(temp_workspace))
        assert "sample.txt" in result

    def test_list_with_pattern(self, temp_workspace, sample_file):
        result = tool_list_files(".", str(temp_workspace), pattern="*.txt")
        assert "sample.txt" in result
        assert ".DS_Store" not in result

    def test_list_nonexistent(self, temp_workspace):
        with pytest.raises(ToolError, match="Access denied"):
            tool_list_files("/nonexistent", str(temp_workspace))

    def test_list_traversal_pattern_blocked(self, temp_workspace):
        with pytest.raises(ToolError, match="path traversal not allowed"):
            tool_list_files(".", str(temp_workspace), pattern="../foo")

    def test_list_path_traversal_blocked(self, temp_workspace):
        with pytest.raises(ToolError, match="Access denied"):
            tool_list_files("/etc", str(temp_workspace))

    def test_list_deeply_nested(self, temp_workspace, nested_dir):
        result = tool_list_files(".", str(temp_workspace))
        assert "a/" in result


class TestExecuteTool:

    def test_dispatch_read_file(self, temp_workspace, sample_file):
        result = execute_tool("read_file", {"path": str(sample_file.name)}, str(temp_workspace))
        assert "line 1" in result

    def test_unknown_tool(self, temp_workspace):
        with pytest.raises(ToolError, match="Unknown tool"):
            execute_tool("nonexistent", {}, str(temp_workspace))

    def test_only_known_args_passed(self, temp_workspace, sample_file):
        result = execute_tool("read_file", {
            "path": str(sample_file.name),
            "unknown_arg": "ignored_me",
        }, str(temp_workspace))
        assert "line 1" in result
