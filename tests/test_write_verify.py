"""Tests for write-verify pipeline — auto-lint + auto-test on file write."""

import pytest
from unittest.mock import MagicMock, patch

from wisp.config import WispConfig
from wisp.tool_executor import ToolExecutor


def _mk_te(tmp_path):
    cfg = WispConfig()
    cfg.workspace = str(tmp_path)
    cfg.auto_approve = True
    return ToolExecutor(
        config=cfg,
        hook_manager=MagicMock(),
    )


class TestWriteVerify:
    """Tests for _run_write_verify — lint + test after file write."""

    @pytest.mark.asyncio
    async def test_no_lint_for_clean_file(self, tmp_path):
        """When lsp_diagnostics returns 'No issues', result is empty."""
        te = _mk_te(tmp_path)
        with patch("wisp.tools.lsp.tool_lsp_diagnostics", return_value="✓ No issues found."):
            result = await te._run_write_verify("test.py", str(tmp_path))
        assert result == ""

    @pytest.mark.asyncio
    async def test_no_lint_for_no_diagnostics(self, tmp_path):
        """When no linter available for extension, result is empty."""
        te = _mk_te(tmp_path)
        with patch("wisp.tools.lsp.tool_lsp_diagnostics",
                   return_value="No diagnostics available for .txt files."):
            result = await te._run_write_verify("test.txt", str(tmp_path))
        assert result == ""

    @pytest.mark.asyncio
    async def test_lint_feedback_on_issues(self, tmp_path):
        """When lint finds issues, they're included in result."""
        te = _mk_te(tmp_path)
        with patch("wisp.tools.lsp.tool_lsp_diagnostics",
                   return_value="error: unused variable 'x' on line 5"):
            with patch("wisp.tools.tests.tool_run_tests",
                       return_value="## Test Results (1/1 passed)\n- Duration: 0.00s\n- Failed: 0, Errors: 0, Skipped: 0"):
                result = await te._run_write_verify("test.py", str(tmp_path))
        assert "[Lint:" in result
        assert "unused variable" in result

    @pytest.mark.asyncio
    async def test_lint_error_start_skipped(self, tmp_path):
        """Error: prefix from lsp means file not found or similar — skip."""
        te = _mk_te(tmp_path)
        with patch("wisp.tools.lsp.tool_lsp_diagnostics",
                   return_value="Error: file not found: bad.py"):
            result = await te._run_write_verify("bad.py", str(tmp_path))
        assert result == ""

    @pytest.mark.asyncio
    async def test_test_results_on_file_change(self, tmp_path):
        """When tests are affected and run, results appear in feedback."""
        te = _mk_te(tmp_path)
        with patch("wisp.tools.lsp.tool_lsp_diagnostics",
                   return_value="✓ No issues found."):
            with patch("wisp.tools.tests.tool_run_tests",
                       return_value=(
                           "## Test Results (2/3 passed)\n"
                           "- Duration: 1.23s\n"
                           "- Failed: 1, Errors: 0, Skipped: 0\n"
                           "\n### Failures\n- test_bad — AssertionError: expected 5 got 4"
                       )):
                result = await te._run_write_verify("src/core.py", str(tmp_path))
        assert "[Tests:" in result
        assert "2/3 passed" in result

    @pytest.mark.asyncio
    async def test_no_test_noise(self, tmp_path):
        """When no tests found (0/0 passed), don't include test block."""
        te = _mk_te(tmp_path)
        with patch("wisp.tools.lsp.tool_lsp_diagnostics",
                   return_value="✓ No issues found."):
            with patch("wisp.tools.tests.tool_run_tests",
                       return_value="## Test Results (0/0 passed)\n- Duration: 0.00s"):
                result = await te._run_write_verify("test.py", str(tmp_path))
        assert "Tests:" not in result

    @pytest.mark.asyncio
    async def test_both_lint_and_test_feedback(self, tmp_path):
        """When both lint and tests have issues, both appear."""
        te = _mk_te(tmp_path)
        with patch("wisp.tools.lsp.tool_lsp_diagnostics",
                   return_value="warning: line too long (120 > 88)"):
            with patch("wisp.tools.tests.tool_run_tests",
                       return_value="## Test Results (0/1 passed)\n- Failed: 1"):
                result = await te._run_write_verify("app.py", str(tmp_path))
        assert "[Lint:" in result
        assert "[Tests:" in result

    @pytest.mark.asyncio
    async def test_lint_exception_silent(self, tmp_path):
        """If lint itself crashes, it's caught silently."""
        te = _mk_te(tmp_path)
        with patch("wisp.tools.lsp.tool_lsp_diagnostics",
                   side_effect=RuntimeError("crash")):
            result = await te._run_write_verify("test.py", str(tmp_path))
        assert result == ""

    @pytest.mark.asyncio
    async def test_test_exception_silent(self, tmp_path):
        """If test runner crashes, it's caught silently."""
        te = _mk_te(tmp_path)
        with patch("wisp.tools.lsp.tool_lsp_diagnostics",
                   return_value="✓ No issues found."):
            with patch("wisp.tools.tests.tool_run_tests",
                       side_effect=RuntimeError("crash")):
                result = await te._run_write_verify("test.py", str(tmp_path))
        assert result == ""

    @pytest.mark.asyncio
    async def test_lint_result_truncated(self, tmp_path):
        """Long lint output is truncated to 500 chars."""
        te = _mk_te(tmp_path)
        with patch("wisp.tools.lsp.tool_lsp_diagnostics",
                   return_value="error: " + ("x" * 600)):
            result = await te._run_write_verify("test.py", str(tmp_path))
        lint_part = result.split("[Lint: ")[1].rstrip("]")
        assert len(lint_part) <= 500

    @pytest.mark.asyncio
    async def test_test_result_truncated(self, tmp_path):
        """Long test output is truncated to 600 chars."""
        te = _mk_te(tmp_path)
        with patch("wisp.tools.lsp.tool_lsp_diagnostics",
                   return_value="✓ No issues found."):
            with patch("wisp.tools.tests.tool_run_tests",
                       return_value="x" * 1000):
                result = await te._run_write_verify("test.py", str(tmp_path))
        test_part = result.split("[Tests: ")[1].rstrip("]")
        assert len(test_part) <= 603  # 600 + "..." if truncated
