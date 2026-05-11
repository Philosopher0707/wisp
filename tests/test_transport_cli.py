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
        assert _render_event(event) == "hello"

    def test_render_thinking_hidden(self):
        event = thinking("deep thoughts")
        assert _render_event(event, show_thinking=False) is None

    def test_render_thinking_shown(self):
        event = thinking("deep thoughts")
        rendered = _render_event(event, show_thinking=True)
        assert rendered is not None
        assert "Thinking" in rendered

    def test_render_tool_call(self):
        event = tool_call("read_file", {"path": "/tmp/test.py"})
        rendered = _render_event(event)
        assert "read_file" in rendered
        assert "/tmp/test.py" in rendered

    def test_render_tool_result(self):
        event = tool_result("read_file", "file contents here")
        rendered = _render_event(event)
        assert "→" in rendered
        assert "file contents" in rendered

    def test_render_error(self):
        event = error("something broke")
        rendered = _render_event(event)
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
        assert _prompt_dangerous("run_bash", "rm -rf /") is False

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
        assert _prompt_dangerous("run_bash", "rm -rf /") is True


class TestCLITransport:

    def test_init(self, core):
        transport = CLITransport(core)
        assert transport.core is core
        assert transport.show_thinking == core.config.show_thinking


class TestRenderEventPlanTools:
    """Planning tool results must render with full multi-line formatting."""

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
        rendered = _render_event(event)
        assert "Created plan" in rendered
        assert "  1." in rendered
        assert "  2." in rendered
        assert "  3." in rendered
        newline_count = rendered.count("\n")
        assert newline_count >= 4, f"Expected multi-line output, got {newline_count} newlines"

    def test_mark_step_done_full_output(self):
        result_text = "✓ Marked task auth-login as done. Progress: 2/3"
        event = tool_result("mark_step_done", result_text)
        rendered = _render_event(event)
        assert "✓ Marked task" in rendered

    def test_update_plan_full_output(self):
        result_text = "✓ Updated task auth-login to 'done'. Progress: 2/3"
        event = tool_result("update_plan", result_text)
        rendered = _render_event(event)
        assert "✓ Updated task" in rendered

    def test_non_plan_tool_still_truncated(self):
        long_result = "x" * 300 + "\nline2"
        event = tool_result("read_file", long_result)
        rendered = _render_event(event)
        assert ("..." in rendered or len(rendered) < 250)
        assert "→" in rendered

    def test_plan_tool_with_json_result_is_safe(self):
        event = tool_result("plan_task", '{"ok": true, "id": "p-1"}')
        rendered = _render_event(event)
        assert isinstance(rendered, str)
