"""Tests for wisp.transport.cli — CLITransport event rendering and helpers."""

import pytest
from unittest.mock import MagicMock, patch

from wisp.transport.cli import (
    CLITransport,
    _render_event,
    _args_preview,
    _is_interactive,
    _prompt_approve,
    _prompt_dangerous,
)
from wisp.core.events import (
    content,
    thinking,
    tool_call,
    tool_result,
    error,
    done,
    system,
    approval_request,
)
from wisp.core.agent import WispAgentCore
from wisp.config import WispConfig


@pytest.fixture
def core():
    config = WispConfig()
    config.model = "test-model"
    config.workspace = "/tmp"
    config.auto_compact = False
    return WispAgentCore(config=config)


class TestRenderEvent:

    def test_render_content(self):
        event = content("hello")
        rendered = _render_event(event, box_mode=False)
        assert "hello" in rendered

    def test_render_content_boxed(self):
        event = content("hello")
        rendered = _render_event(event, box_mode=True)
        assert "hello" in rendered
        assert "Response" in rendered

    def test_render_thinking_hidden(self):
        event = thinking("deep thoughts")
        rendered = _render_event(event, show_thinking=False)
        assert rendered is not None
        assert "Thinking..." in rendered
        assert "1 lines" in rendered

    def test_render_thinking_shown(self):
        event = thinking("deep thoughts")
        rendered = _render_event(event, show_thinking=True, box_mode=False)
        assert rendered is not None
        assert "Reasoning" in rendered
        assert "deep thoughts" in rendered

    def test_render_tool_call(self):
        event = tool_call("read_file", {"path": "/tmp/test.py"})
        rendered = _render_event(event, box_mode=False)
        assert "read_file" in rendered
        assert "/tmp/test.py" in rendered

    def test_render_tool_result(self):
        event = tool_result("read_file", "file contents here")
        rendered = _render_event(event, box_mode=False)
        assert "→" in rendered
        assert "file contents" in rendered

    def test_render_error(self):
        event = error("something broke")
        rendered = _render_event(event, box_mode=False)
        assert "something broke" in rendered

    def test_render_system_info(self):
        event = system("compacted session", "info")
        rendered = _render_event(event)
        assert "compacted session" in rendered

    def test_render_system_debug_hidden(self):
        event = system("debug msg", "debug")
        assert _render_event(event) is None

    def test_render_approval(self):
        event = approval_request("run_bash", {}, reason="dangerous")
        rendered = _render_event(event)
        assert "Approval required" in rendered

    def test_render_done(self):
        event = done("sid", 1)
        assert _render_event(event) is None


class TestArgsPreview:

    def test_args_preview_path(self):
        assert _args_preview({"path": "/tmp/test.py"}) == "/tmp/test.py"

    def test_args_preview_command(self):
        assert _args_preview({"command": "ls -la"}) == "ls -la"

    def test_args_preview_content(self):
        preview = _args_preview({"content": "a" * 100})
        assert "(100 chars)" in preview

    def test_args_preview_empty(self):
        assert _args_preview({}) == "..."


class TestPrompts:

    @patch("wisp.transport.cli._is_interactive", return_value=False)
    def test_prompt_approve_non_interactive(self, mock_interactive):
        assert _prompt_approve("read_file") is True

    @patch("wisp.transport.cli._is_interactive", return_value=False)
    def test_prompt_dangerous_non_interactive(self, mock_interactive):
        assert _prompt_dangerous("run_bash", "rm -rf /") == (False, False)

    @patch("wisp.transport.cli._is_interactive", return_value=True)
    @patch("builtins.input", return_value="")
    def test_prompt_approve_yes(self, mock_input, mock_interactive):
        assert _prompt_approve("read_file") is True

    @patch("wisp.transport.cli._is_interactive", return_value=True)
    @patch("builtins.input", return_value="s")
    def test_prompt_approve_skip(self, mock_input, mock_interactive):
        assert _prompt_approve("read_file") is False

    @patch("wisp.transport.cli._is_interactive", return_value=True)
    @patch("builtins.input", return_value="yes")
    def test_prompt_dangerous_yes(self, mock_input, mock_interactive):
        assert _prompt_dangerous("run_bash", "rm -rf /") == (True, False)


class TestCLITransport:

    def test_init(self, core):
        transport = CLITransport(core)
        assert transport.core is core
        assert transport.show_thinking == core.config.show_thinking


class TestRenderEventFullOutputTools:
    """Tools whose output is meant for human consumption must preserve formatting."""

    def test_plan_task_full_output(self):
        plan_text = (
            "✓ Created plan: plan-123\n"
            "Goal: Implement auth\n"
            "Tasks: 3\n\n"
            "  1. [low] Add login — files: auth.py\n"
            "  2. [medium] Add JWT — deps: 1\n"
            "  3. [high] Add tests — deps: 1, 2"
        )
        event = tool_result("plan_task", plan_text)
        rendered = _render_event(event, box_mode=False)
        assert "Created plan" in rendered
        assert "  1." in rendered
        assert "  2." in rendered
        assert "  3." in rendered
        newline_count = rendered.count("\n")
        assert newline_count >= 4, f"Expected multi-line output, got {newline_count} newlines"

    def test_mark_step_done_full_output(self):
        result_text = "✓ Marked task auth-login as done. Progress: 2/3"
        event = tool_result("mark_step_done", result_text)
        rendered = _render_event(event, box_mode=False)
        assert "✓ Marked task" in rendered

    def test_update_plan_full_output(self):
        result_text = "✓ Updated task auth-login to 'done'. Progress: 2/3"
        event = tool_result("update_plan", result_text)
        rendered = _render_event(event, box_mode=False)
        assert "✓ Updated task" in rendered

    def test_web_search_full_output(self):
        """web_search returns multi-line JSON/text that must be readable."""
        result_text = (
            "1. DuckDuckGo – Privacy-focused search engine\n"
            "   https://duckduckgo.com\n"
            "   DuckDuckGo is an internet search engine that emphasizes protecting searchers' privacy.\n\n"
            "2. Python Documentation\n"
            "   https://docs.python.org\n"
            "   Official Python language documentation."
        )
        event = tool_result("web_search", result_text)
        rendered = _render_event(event, box_mode=False)
        assert "DuckDuckGo" in rendered
        assert "https://duckduckgo.com" in rendered
        assert rendered.count("\n") >= 4

    def test_git_status_full_output(self):
        """git_status returns multi-line output that must preserve newlines."""
        result_text = (
            "On branch main\n"
            "Your branch is ahead of 'origin/main' by 2 commits.\n"
            "Changes not staged for commit:\n"
            "  (use \"git add <file>...\" to update what will be committed)\n"
            "\tmodified:   wisp/transport/cli.py\n"
            "\tmodified:   tests/test_transport_cli.py"
        )
        event = tool_result("git_status", result_text)
        rendered = _render_event(event, box_mode=False)
        assert "On branch main" in rendered
        assert "modified:" in rendered
        assert rendered.count("\n") >= 4

    def test_run_bash_full_output(self):
        """run_bash can return multi-line command output."""
        result_text = (
            "total 24\n"
            "drwxr-xr-x  5 user  staff   160 May 11 10:00 .\n"
            "drwxr-xr-x  3 user  staff    96 May 10 09:00 ..\n"
            "-rw-r--r--  1 user  staff  2048 May 11 10:00 cli.py"
        )
        event = tool_result("run_bash", result_text)
        rendered = _render_event(event, box_mode=False)
        assert "total 24" in rendered
        assert "cli.py" in rendered

    def test_lsp_diagnostics_full_output(self):
        """lsp_diagnostics returns structured multi-line output."""
        result_text = (
            "wisp/transport/cli.py:42:1: error: Cannot find name '_FULL_OUTPUT_TOOLS'\n"
            "wisp/transport/cli.py:58:5: warning: Variable 'preview' is unused"
        )
        event = tool_result("lsp_diagnostics", result_text)
        rendered = _render_event(event, box_mode=False)
        assert "error:" in rendered
        assert "warning:" in rendered

    def test_compact_tool_still_truncated(self):
        """Tools NOT in _FULL_OUTPUT_TOOLS get compact single-line preview."""
        long_result = "x" * 300 + "\nline2"
        event = tool_result("read_file", long_result)
        rendered = _render_event(event, box_mode=False)
        assert "..." in rendered
        assert "→" in rendered
        assert "line2" not in rendered  # newline replaced by space, then truncated

    def test_compact_tool_ellipsis_at_exactly_200(self):
        """Ellipsis appears only when result is longer than 200 chars."""
        short_result = "short"
        event = tool_result("read_file", short_result)
        rendered = _render_event(event, box_mode=False)
        assert "..." not in rendered
        assert "short" in rendered

        exactly_200 = "a" * 200
        event = tool_result("read_file", exactly_200)
        rendered = _render_event(event, box_mode=False)
        assert "..." not in rendered  # exactly 200, no truncation

        over_200 = "a" * 201
        event = tool_result("read_file", over_200)
        rendered = _render_event(event, box_mode=False)
        assert "..." in rendered

    def test_full_output_tool_with_json_result_is_safe(self):
        """A full-output tool that happens to return a JSON string still renders."""
        event = tool_result("plan_task", '{"ok": true, "id": "p-1"}')
        rendered = _render_event(event, box_mode=False)
        assert isinstance(rendered, str)
        assert "plan_task" in rendered

    def test_search_codebase_full_output(self):
        """search_codebase returns multi-line results."""
        result_text = (
            "Result 1 – score 0.92\n"
            "  File: wisp/core/agent.py:145\n"
            "  ```python\n"
            "  def _expand_continuation(self, user_text: str) -> str:\n"
            "  ```\n\n"
            "Result 2 – score 0.87\n"
            "  File: wisp/transport/cli.py:200\n"
            "  ```python\n"
            "  def _render_event(event: AgentEvent):\n"
            "  ```"
        )
        event = tool_result("search_codebase", result_text)
        rendered = _render_event(event, box_mode=False)
        assert "Result 1" in rendered
        assert "Result 2" in rendered
        assert rendered.count("\n") >= 6
