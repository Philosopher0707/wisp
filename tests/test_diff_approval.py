"""Tests for structured diff viewer, approval parameter sanitization, and compact badge rendering."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.panel import Panel
from rich.syntax import Syntax

from wisp.cli.approval import (
    ToolApprovalInfo,
    parse_tool_approval,
    render_approval_badge,
    render_approval_options,
    render_diff_view,
    sanitize_arg_value,
)
from wisp.ui.diff_viewer import (
    compute_diff_stats,
    create_diff_panel,
    extract_change_summary,
    generate_unified_diff,
    render_diff_string,
)


class TestDiffViewerComputation:
    """Test difflib-based diff and delta computation."""

    def test_compute_diff_stats_basic(self):
        old_text = "def hello():\n    return 'world'\n"
        new_text = "def hello():\n    print('greeting')\n    return 'world'\n"
        added, removed, summary = compute_diff_stats(old_text, new_text)
        assert added == 1
        assert removed == 0
        assert "hello" in summary

    def test_compute_diff_stats_modifications(self):
        old_text = "line1\nline2\nline3\n"
        new_text = "line1\nline2_modified\nline4\n"
        added, removed, summary = compute_diff_stats(old_text, new_text)
        assert added == 2
        assert removed == 2

    def test_compute_diff_stats_empty_old(self):
        old_text = ""
        new_text = "def new_func():\n    pass\n"
        added, removed, summary = compute_diff_stats(old_text, new_text)
        assert added == 2
        assert removed == 0
        assert "new_func" in summary

    def test_compute_diff_stats_empty_new(self):
        old_text = "line1\nline2\n"
        new_text = ""
        added, removed, summary = compute_diff_stats(old_text, new_text)
        assert added == 0
        assert removed == 2
        assert "deleted file" in summary

    def test_compute_diff_stats_identical(self):
        text = "def same():\n    return 42\n"
        added, removed, summary = compute_diff_stats(text, text)
        assert added == 0
        assert removed == 0
        assert summary == "no changes"

    def test_extract_change_summary_class(self):
        old_text = "class DataService:\n    pass\n"
        new_text = "class DataService:\n    def connect(self):\n        pass\n"
        summary = extract_change_summary(old_text, new_text)
        assert "DataService" in summary or "connect" in summary

    def test_generate_unified_diff_format(self):
        old_text = "a = 1\nb = 2\n"
        new_text = "a = 1\nb = 3\n"
        diff_str = generate_unified_diff(old_text, new_text, file_path="src/calc.py")
        assert "--- a/src/calc.py" in diff_str
        assert "+++ b/src/calc.py" in diff_str
        assert "-b = 2" in diff_str
        assert "+b = 3" in diff_str


class TestRichDiffPanel:
    """Test rich Panel and Syntax construction."""

    def test_create_diff_panel_structure(self):
        old_text = "def compute():\n    return 10\n"
        new_text = "def compute():\n    return 20\n"
        panel = create_diff_panel(old_text, new_text, file_path="math.py")
        assert isinstance(panel, Panel)
        assert isinstance(panel.renderable, Syntax)
        assert panel.renderable.lexer.name.lower() in ("diff", "udiff")

    def test_render_diff_string_plain(self):
        old_text = "alpha\n"
        new_text = "beta\n"
        output = render_diff_string(old_text, new_text, file_path="test.txt", plain=True)
        assert "Diff: test.txt" in output
        assert "-alpha" in output
        assert "+beta" in output

    def test_render_diff_string_rich(self):
        old_text = "alpha\n"
        new_text = "beta\n"
        output = render_diff_string(old_text, new_text, file_path="test.txt", plain=False)
        assert len(output) > 0
        assert "test.txt" in output


class TestParameterSanitization:
    """Test that raw string payloads are never leaked into approval headers."""

    def test_sanitize_arg_value_large_string(self):
        huge = "line1\nline2\nline3\n" + ("x" * 500)
        res = sanitize_arg_value("old_text", huge)
        assert "\n" not in res
        assert "<4 lines, 518 chars>" in res

    def test_sanitize_arg_value_command_newline_collapsed(self):
        cmd = "echo 'hello'\npytest -v\nexit 0"
        res = sanitize_arg_value("command", cmd)
        assert "\n" not in res
        assert "echo 'hello' pytest -v" in res

    def test_parse_tool_approval_edit_file(self):
        tool_call = {
            "name": "edit_file",
            "arguments": {
                "path": "wisp/core/engine.py",
                "old_text": "def old_engine():\n    # 100 lines\n    return 1",
                "new_text": "def old_engine():\n    # modified\n    return 2",
            },
        }
        info = parse_tool_approval(tool_call)
        assert info.is_file_edit is True
        assert info.target_path == "wisp/core/engine.py"
        assert info.added_lines >= 1
        assert info.removed_lines >= 1
        assert "old_engine" in info.summary
        # Header string should not contain raw unescaped multi-line code
        assert "def old_engine" not in info.sanitized_args_str
        assert "\n" not in info.sanitized_args_str

    def test_parse_tool_approval_edit_file_multi(self):
        tool_call = {
            "name": "edit_file_multi",
            "arguments": {
                "path": "wisp/config.py",
                "edits": [
                    {"old_text": "TIMEOUT = 10\n", "new_text": "TIMEOUT = 20\n"},
                    {"old_text": "RETRIES = 3\n", "new_text": "RETRIES = 5\n"},
                ],
            },
        }
        info = parse_tool_approval(tool_call)
        assert info.is_file_edit is True
        assert info.target_path == "wisp/config.py"
        assert info.added_lines == 2
        assert info.removed_lines == 2

    def test_parse_tool_approval_non_file_tool(self):
        tool_call = {
            "name": "run_bash",
            "arguments": {
                "command": "git status",
            },
        }
        info = parse_tool_approval(tool_call)
        assert info.is_file_edit is False
        assert "git status" in info.sanitized_args_str


class TestApprovalBadgeRendering:
    """Test compact 2-line badge and interactive diff rendering."""

    def test_render_approval_badge_edit_file_compact(self):
        info = ToolApprovalInfo(
            tool_name="edit_file",
            is_file_edit=True,
            target_path="wisp/main.py",
            added_lines=5,
            removed_lines=2,
            summary="in start_server()",
        )
        badge = render_approval_badge(info, plain=True)
        lines = badge.splitlines()
        assert len(lines) == 2
        assert "edit_file: wisp/main.py (+5 / -2 lines)" in lines[0]
        assert "Scope: in start_server()" in lines[1]

    def test_render_approval_badge_non_file(self):
        info = ToolApprovalInfo(
            tool_name="run_bash",
            is_file_edit=False,
            sanitized_args_str="command='pytest'",
        )
        badge = render_approval_badge(info, plain=True)
        assert "run_bash(command='pytest')" in badge
        assert len(badge.splitlines()) == 1

    def test_render_approval_options(self):
        edit_opts = render_approval_options(is_file_edit=True)
        assert "[v] view diff" in edit_opts
        assert "[y] yes" in edit_opts
        assert "[n] no" in edit_opts

    def test_render_diff_view(self):
        info = ToolApprovalInfo(
            tool_name="edit_file",
            is_file_edit=True,
            target_path="app.py",
            old_text="def a(): pass\n",
            new_text="def a(): return True\n",
        )
        diff_view = render_diff_view(info, plain=True)
        assert "Diff: app.py" in diff_view
        assert "-def a(): pass" in diff_view
        assert "+def a(): return True" in diff_view


class _MockRuntime:
    def __init__(self):
        self.sessions = {}
        self.turns = []


@pytest.mark.asyncio
class TestCLITransportApprovalIntegration:
    """Test interactive approval flow with diff viewing toggle in CLITransport."""

    async def test_cli_approve_yes(self):
        from wisp.transport.cli import CLITransport
        from wisp.config import WispConfig

        transport = CLITransport(_MockRuntime(), config=WispConfig())
        transport._force_approval_mode = False
        tool_call = {
            "name": "edit_file",
            "arguments": {
                "path": "test.py",
                "old_text": "x = 1\n",
                "new_text": "x = 2\n",
            },
        }

        with patch.object(transport, "_read_approval_answer_with_reminders", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = "y"
            approved = await transport.approve(tool_call)
            assert approved is True

    async def test_cli_approve_view_diff_then_yes(self):
        from wisp.transport.cli import CLITransport
        from wisp.config import WispConfig

        transport = CLITransport(_MockRuntime(), config=WispConfig())
        transport._force_approval_mode = False
        tool_call = {
            "name": "edit_file",
            "arguments": {
                "path": "test.py",
                "old_text": "x = 1\n",
                "new_text": "x = 2\n",
            },
        }

        # User first types 'v' (view diff), then 'y' (approve)
        with patch.object(transport, "_read_approval_answer_with_reminders", new_callable=AsyncMock) as mock_read:
            mock_read.side_effect = ["v", "y"]
            approved = await transport.approve(tool_call)
            assert approved is True
            assert mock_read.call_count == 2

    async def test_cli_approve_deny(self):
        from wisp.transport.cli import CLITransport
        from wisp.config import WispConfig

        transport = CLITransport(_MockRuntime(), config=WispConfig())
        transport._force_approval_mode = False
        tool_call = {
            "name": "edit_file",
            "arguments": {
                "path": "test.py",
                "old_text": "x = 1\n",
                "new_text": "x = 2\n",
            },
        }

        with patch.object(transport, "_read_approval_answer_with_reminders", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = "n"
            approved = await transport.approve(tool_call)
            assert approved is False


