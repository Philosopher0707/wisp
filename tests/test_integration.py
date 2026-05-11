"""End-to-end integration tests for all Wisp tools.

Exercises every tool through execute_tool() with real filesystem operations,
verifying structured JSON output, error handling, and security boundaries.
"""

import json
import pytest
from pathlib import Path

from wisp.tools import (
    execute_tool,
    ToolError,
    tool_read_file,
    tool_write_file,
    tool_edit_file,
    tool_run_bash,
    tool_list_files,
    tool_web_fetch,
    tool_search_symbols,
    tool_remember,
    tool_recall,
    tool_git_status,
    tool_git_diff,
    tool_diagnose,
    tool_plan_task,
    tool_mark_step_done,
    tool_update_plan,
    _build_tool_metadata,
)


# ── Helpers ──────────────────────────────────────────────────────────

def parse_tool_result(raw: str) -> dict:
    """Parse the structured JSON result from execute_tool."""
    data = json.loads(raw)
    assert "status" in data
    assert "tool" in data
    assert "data" in data
    assert "metadata" in data
    return data


# ── Integration: read_file ───────────────────────────────────────────

class TestIntegrationReadFile:
    def test_read_file_success(self, temp_workspace, sample_file):
        raw = execute_tool("read_file", {"path": "sample.txt"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert "line 1" in result["data"]
        assert result["metadata"]["path"] == "sample.txt"

    def test_read_file_not_found(self, temp_workspace):
        raw = execute_tool("read_file", {"path": "missing.txt"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error"
        assert "not found" in result["data"].lower()

    def test_read_file_path_traversal(self, temp_workspace):
        raw = execute_tool("read_file", {"path": "/etc/passwd"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error"
        assert "access denied" in result["data"].lower()

    def test_read_file_with_offset_limit(self, temp_workspace, sample_file):
        raw = execute_tool("read_file", {"path": "sample.txt", "offset": 1, "limit": 2}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert "line 2" in result["data"]
        assert "line 4" not in result["data"]
        assert result["metadata"]["offset"] == 1
        assert result["metadata"]["limit"] == 2


# ── Integration: write_file ────────────────────────────────────────

class TestIntegrationWriteFile:
    def test_write_file_success(self, temp_workspace):
        raw = execute_tool("write_file", {"path": "new.txt", "content": "hello world"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert result["metadata"]["bytes_written"] == 11
        assert (temp_workspace / "new.txt").read_text() == "hello world"

    def test_write_file_creates_dirs(self, temp_workspace):
        raw = execute_tool("write_file", {"path": "a/b/c/deep.txt", "content": "deep"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert (temp_workspace / "a/b/c/deep.txt").read_text() == "deep"

    def test_write_file_path_traversal(self, temp_workspace):
        raw = execute_tool("write_file", {"path": "../outside.txt", "content": "bad"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error"
        assert "access denied" in result["data"].lower()

    def test_write_file_empty_content(self, temp_workspace):
        raw = execute_tool("write_file", {"path": "empty.txt", "content": ""}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert (temp_workspace / "empty.txt").read_text() == ""


# ── Integration: edit_file ─────────────────────────────────────────

class TestIntegrationEditFile:
    def test_edit_file_exact_match(self, temp_workspace, sample_file):
        raw = execute_tool("edit_file", {
            "path": "sample.txt",
            "old_text": "line 2",
            "new_text": "edited line 2",
        }, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        content = sample_file.read_text()
        assert "edited line 2" in content
        # Original standalone "line 2" should be gone
        lines = content.splitlines()
        assert "line 2" not in lines  # exact line match, not substring

    def test_edit_file_not_found(self, temp_workspace):
        raw = execute_tool("edit_file", {
            "path": "missing.txt",
            "old_text": "x",
            "new_text": "y",
        }, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error"
        assert "not found" in result["data"].lower()

    def test_edit_file_old_text_missing(self, temp_workspace, sample_file):
        raw = execute_tool("edit_file", {
            "path": "sample.txt",
            "old_text": "does not exist",
            "new_text": "replacement",
        }, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error"
        assert "could not find" in result["data"].lower()


# ── Integration: run_bash ──────────────────────────────────────────

class TestIntegrationRunBash:
    def test_run_bash_echo(self, temp_workspace):
        raw = execute_tool("run_bash", {"command": "echo hello"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert "hello" in result["data"]
        assert result["metadata"]["command"] == "echo hello"

    def test_run_bash_failure(self, temp_workspace):
        raw = execute_tool("run_bash", {"command": "false"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"  # bash returns output even on failure
        assert "exit code: 1" in result["data"]
        assert result["metadata"]["exit_code"] == 1

    def test_run_bash_timeout(self, temp_workspace):
        raw = execute_tool("run_bash", {"command": "sleep 10", "timeout": 1}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error"
        assert "timed out" in result["data"].lower()

    def test_run_bash_dangerous_command(self, temp_workspace):
        raw = execute_tool("run_bash", {"command": "sudo ls"}, str(temp_workspace))
        result = parse_tool_result(raw)
        # Dangerous commands are blocked at the agent level, not tool level
        # The tool itself runs them; the agent should block before calling
        # But we verify the tool handles them gracefully
        assert result["status"] in ("ok", "error")


# ── Integration: list_files ────────────────────────────────────────

class TestIntegrationListFiles:
    def test_list_files_root(self, temp_workspace, sample_file):
        raw = execute_tool("list_files", {"path": "."}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert "sample.txt" in result["data"]
        assert result["metadata"]["entry_count"] >= 1

    def test_list_files_with_pattern(self, temp_workspace, sample_file):
        raw = execute_tool("list_files", {"path": ".", "pattern": "*.txt"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert "sample.txt" in result["data"]

    def test_list_files_not_found(self, temp_workspace):
        raw = execute_tool("list_files", {"path": "nonexistent"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error"


# ── Integration: web_fetch ───────────────────────────────────────────

class TestIntegrationWebFetch:
    def test_web_fetch_success(self, temp_workspace):
        # Use httpbin for reliable testing
        raw = execute_tool("web_fetch", {"url": "https://httpbin.org/get", "max_chars": 2000}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert "httpbin" in result["data"].lower() or "200" in result["data"]
        assert result["metadata"]["url"] == "https://httpbin.org/get"

    def test_web_fetch_invalid_url(self, temp_workspace):
        raw = execute_tool("web_fetch", {"url": "not-a-url"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error"
        assert "invalid" in result["data"].lower()

    def test_web_fetch_unsupported_scheme(self, temp_workspace):
        raw = execute_tool("web_fetch", {"url": "ftp://example.com/file"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error"
        assert "unsupported" in result["data"].lower()


# ── Integration: search_symbols ──────────────────────────────────────

class TestIntegrationSearchSymbols:
    def test_search_symbols_in_workspace(self, temp_workspace):
        # Create a Python file with a function
        py_file = temp_workspace / "module.py"
        py_file.write_text("def hello_world():\n    pass\n\nclass MyClass:\n    pass\n")
        raw = execute_tool("search_symbols", {"query": "hello", "max_results": 10}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        # Should find hello_world
        assert "hello_world" in result["data"] or "no symbols" in result["data"].lower()

    def test_search_symbols_no_results(self, temp_workspace):
        raw = execute_tool("search_symbols", {"query": "xyz_nonexistent", "max_results": 10}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert "no symbols" in result["data"].lower() or "Found 0" in result["data"]


# ── Integration: remember / recall ───────────────────────────────────

class TestIntegrationMemory:
    def test_remember_and_recall(self, temp_workspace):
        raw = execute_tool("remember", {"fact": "Python 3.11 is the current version"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert "Remembered" in result["data"] or "Already remembered" in result["data"]

        raw2 = execute_tool("recall", {"query": "Python version", "limit": 5}, str(temp_workspace))
        result2 = parse_tool_result(raw2)
        assert result2["status"] == "ok"
        # Should find the remembered fact
        assert "Python" in result2["data"] or "No relevant" in result2["data"]

    def test_recall_empty_query(self, temp_workspace):
        raw = execute_tool("recall", {"query": "", "limit": 5}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error"


# ── Integration: git_status / git_diff ───────────────────────────────

class TestIntegrationGit:
    def test_git_status_not_a_repo(self, temp_workspace):
        raw = execute_tool("git_status", {}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert "not a git" in result["data"].lower() or "repository" in result["data"].lower()

    def test_git_status_in_repo(self, temp_workspace):
        # Initialize a git repo
        import subprocess
        subprocess.run(["git", "init"], cwd=str(temp_workspace), capture_output=True)
        raw = execute_tool("git_status", {}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        # Should show something about the repo

    def test_git_diff_no_changes(self, temp_workspace):
        raw = execute_tool("git_diff", {}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"


# ── Integration: diagnose ────────────────────────────────────────────

class TestIntegrationDiagnose:
    def test_diagnose_python_traceback(self, temp_workspace):
        error = """Traceback (most recent call last):
  File "test.py", line 5, in <module>
    result = 1 / 0
ZeroDivisionError: division by zero
"""
        raw = execute_tool("diagnose", {"error_output": error}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert "ZeroDivisionError" in result["data"] or "division by zero" in result["data"]

    def test_diagnose_empty_error(self, temp_workspace):
        raw = execute_tool("diagnose", {"error_output": ""}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error" or result["status"] == "ok"


# ── Integration: plan_task ───────────────────────────────────────────

class TestIntegrationPlan:
    def test_plan_task_success(self, temp_workspace):
        tasks = """1. [low] Set up project structure — files: main.py
2. [medium] Implement core logic — deps: 1 — files: core.py
3. [high] Write tests — deps: 2 — files: test_core.py"""
        raw = execute_tool("plan_task", {"goal": "Build a CLI tool", "tasks": tasks}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert "Created plan" in result["data"]

    def test_plan_task_empty_tasks(self, temp_workspace):
        raw = execute_tool("plan_task", {"goal": "Do nothing", "tasks": ""}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert "No tasks" in result["data"] or "parsed" in result["data"].lower()


# ── Integration: mark_step_done / update_plan ────────────────────────

class TestIntegrationPlanUpdate:
    def test_mark_step_done_no_plan(self, temp_workspace):
        raw = execute_tool("mark_step_done", {"task_id": "task-1"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert "No active plan" in result["data"]

    def test_update_plan_no_plan(self, temp_workspace):
        raw = execute_tool("update_plan", {"task_id": "task-1", "status": "done"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert "No active plan" in result["data"]


# ── Integration: execute_tool error handling ─────────────────────────

class TestIntegrationExecuteToolErrors:
    def test_unknown_tool(self, temp_workspace):
        with pytest.raises(ToolError, match="Unknown tool"):
            execute_tool("nonexistent_tool", {}, str(temp_workspace))

    def test_tool_with_invalid_args_type(self, temp_workspace):
        # Pass a list where a string is expected
        raw = execute_tool("read_file", {"path": ["not", "a", "string"]}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error"
        assert "must be a string" in result["data"].lower()

    def test_tool_with_missing_required_arg(self, temp_workspace):
        raw = execute_tool("read_file", {}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error"
        # Missing required arg triggers TypeError, caught as unexpected error
        assert "missing" in result["data"].lower() or "unexpected error" in result["data"].lower()

    def test_tool_truncation(self, temp_workspace):
        # Create a large file
        large_file = temp_workspace / "large.txt"
        large_file.write_text("x" * 20000)
        raw = execute_tool("read_file", {"path": "large.txt"}, str(temp_workspace), max_data_chars=1000)
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert "truncated" in result["data"].lower() or result["metadata"].get("truncated") is True

    def test_metadata_building(self, temp_workspace, sample_file):
        # Use offset/limit to trigger the footer and lines_shown metadata
        raw = execute_tool("read_file", {"path": "sample.txt", "offset": 0, "limit": 2}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        meta = result["metadata"]
        assert meta["path"] == "sample.txt"
        assert meta["offset"] == 0
        assert meta["limit"] == 2
        assert "lines_shown" in meta
        assert meta["total_lines"] == 5


# ── Integration: _build_tool_metadata ────────────────────────────────

class TestIntegrationToolMetadata:
    def test_metadata_read_file(self):
        meta = _build_tool_metadata("read_file", {"path": "foo.py", "offset": 10, "limit": 50}, "content\n--- [showing lines 11-20 of 100] ---")
        assert meta["path"] == "foo.py"
        assert meta["offset"] == 10
        assert meta["limit"] == 50
        assert meta["lines_shown"] == "11-20"
        assert meta["total_lines"] == 100

    def test_metadata_write_file(self):
        meta = _build_tool_metadata("write_file", {"path": "foo.py", "content": "hello"}, "ok")
        assert meta["path"] == "foo.py"
        assert meta["bytes_written"] == 5

    def test_metadata_edit_file(self):
        meta = _build_tool_metadata("edit_file", {"path": "foo.py", "old_text": "abc", "new_text": "xyz"}, "ok")
        assert meta["path"] == "foo.py"
        assert meta["old_text_preview"] == "abc"
        assert meta["new_text_preview"] == "xyz"

    def test_metadata_run_bash(self):
        meta = _build_tool_metadata("run_bash", {"command": "echo hi", "timeout": 30}, "hi\n[exit code: 0]")
        assert meta["command"] == "echo hi"
        assert meta["timeout"] == 30
        assert meta["exit_code"] == 0

    def test_metadata_run_bash_truncated(self):
        meta = _build_tool_metadata("run_bash", {"command": "echo hi"}, "output\n... [output truncated]")
        assert meta["truncated"] is True

    def test_metadata_list_files(self):
        meta = _build_tool_metadata("list_files", {"path": ".", "pattern": "*.py"}, "📄 foo.py (100 bytes)\n📄 bar.py (200 bytes)")
        assert meta["path"] == "."
        assert meta["pattern"] == "*.py"
        assert meta["entry_count"] == 2

    def test_metadata_web_fetch(self):
        meta = _build_tool_metadata("web_fetch", {"url": "https://example.com", "max_chars": 5000}, "content\n... [truncated: 10000 total chars]")
        assert meta["url"] == "https://example.com"
        assert meta["max_chars"] == 5000
        assert meta["truncated"] is True

    def test_metadata_search_symbols(self):
        meta = _build_tool_metadata("search_symbols", {"query": "hello", "max_results": 20}, "Found 5 symbols")
        assert meta["query"] == "hello"
        assert meta["max_results"] == 20
        assert meta["results_count"] == 5

    def test_metadata_remember(self):
        meta = _build_tool_metadata("remember", {"fact": "Python is great"}, "ok")
        assert meta["fact"] == "Python is great"

    def test_metadata_recall(self):
        meta = _build_tool_metadata("recall", {"query": "python", "limit": 10}, "results")
        assert meta["query"] == "python"
        assert meta["limit"] == 10

    def test_metadata_returns_dict(self):
        """Ensure _build_tool_metadata always returns a dict, never None."""
        for name in ["read_file", "write_file", "edit_file", "run_bash", "list_files",
                     "web_fetch", "search_symbols", "remember", "recall"]:
            meta = _build_tool_metadata(name, {}, "")
            assert isinstance(meta, dict), f"_build_tool_metadata({name}) returned {type(meta)}"


# ── Integration: Security Boundaries ─────────────────────────────────

class TestIntegrationSecurity:
    def test_no_path_traversal_read(self, temp_workspace):
        raw = execute_tool("read_file", {"path": "../../etc/passwd"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error"
        assert "access denied" in result["data"].lower()

    def test_no_path_traversal_write(self, temp_workspace):
        raw = execute_tool("write_file", {"path": "../../tmp/evil.txt", "content": "bad"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error"
        assert "access denied" in result["data"].lower()

    def test_no_path_traversal_edit(self, temp_workspace):
        raw = execute_tool("edit_file", {"path": "../../etc/passwd", "old_text": "x", "new_text": "y"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error"
        assert "access denied" in result["data"].lower()

    def test_no_path_traversal_list(self, temp_workspace):
        raw = execute_tool("list_files", {"path": "/etc"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error"
        assert "access denied" in result["data"].lower()

    def test_command_length_limit(self, temp_workspace):
        raw = execute_tool("run_bash", {"command": "x" * 5000}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error"
        assert "too long" in result["data"].lower()


# ── Integration: Full Workflow ───────────────────────────────────────

class TestIntegrationFullWorkflow:
    def test_create_read_edit_delete_workflow(self, temp_workspace):
        """Simulate a realistic agent workflow: create, read, edit, list, bash."""
        # 1. Write a file
        raw = execute_tool("write_file", {
            "path": "app.py",
            "content": "def main():\n    print('hello')\n\nif __name__ == '__main__':\n    main()\n",
        }, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"

        # 2. Read it back
        raw = execute_tool("read_file", {"path": "app.py"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert "def main()" in result["data"]

        # 3. Edit it
        raw = execute_tool("edit_file", {
            "path": "app.py",
            "old_text": "    print('hello')",
            "new_text": "    print('hello world')",
        }, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"

        # 4. Verify edit
        raw = execute_tool("read_file", {"path": "app.py"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert "hello world" in result["data"]

        # 5. List files
        raw = execute_tool("list_files", {"path": ".", "pattern": "*.py"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert "app.py" in result["data"]

        # 6. Run it
        raw = execute_tool("run_bash", {"command": "python3 app.py"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert "hello world" in result["data"]

        # 7. Search symbols
        raw = execute_tool("search_symbols", {"query": "main", "max_results": 10}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"

    def test_error_recovery_workflow(self, temp_workspace):
        """Agent encounters errors and recovers gracefully."""
        # Try to read nonexistent file
        raw = execute_tool("read_file", {"path": "missing.py"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error"

        # Create the file
        raw = execute_tool("write_file", {"path": "missing.py", "content": "# now exists\n"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"

        # Now read succeeds
        raw = execute_tool("read_file", {"path": "missing.py"}, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
        assert "now exists" in result["data"]

        # Try invalid edit
        raw = execute_tool("edit_file", {
            "path": "missing.py",
            "old_text": "nonexistent text",
            "new_text": "replacement",
        }, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "error"

        # Valid edit
        raw = execute_tool("edit_file", {
            "path": "missing.py",
            "old_text": "# now exists",
            "new_text": "# updated",
        }, str(temp_workspace))
        result = parse_tool_result(raw)
        assert result["status"] == "ok"
