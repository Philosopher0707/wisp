"""Tests for transport rendering functions.

Verifies that extracted rendering logic produces correct output.
"""

import pytest


class TestFormatDuration:

    def test_format_duration_none(self):
        from wisp.transport.renderer import format_duration
        assert format_duration(None) == ""

    def test_format_duration_microseconds(self):
        from wisp.transport.renderer import format_duration
        assert format_duration(0.5) == "500μs"
        assert format_duration(0.001) == "1μs"

    def test_format_duration_milliseconds(self):
        from wisp.transport.renderer import format_duration
        assert format_duration(1) == "1ms"
        assert format_duration(999) == "999ms"

    def test_format_duration_seconds(self):
        from wisp.transport.renderer import format_duration
        assert format_duration(1000) == "1.0s"
        assert format_duration(1500) == "1.5s"
        assert format_duration(59999) == "60.0s"

    def test_format_duration_minutes(self):
        from wisp.transport.renderer import format_duration
        assert format_duration(60000) == "1m 0s"
        assert format_duration(90000) == "1m 30s"
        assert format_duration(125000) == "2m 5s"


class TestFormatArgValue:

    def test_format_path_arg(self):
        from wisp.transport.renderer import format_arg_value
        assert format_arg_value("path", "/tmp/test.txt") == "/tmp/test.txt"

    def test_format_long_path_truncated(self):
        from wisp.transport.renderer import format_arg_value
        long_path = "/a" * 50
        result = format_arg_value("path", long_path)
        assert result.endswith("...")
        assert len(result) == 60

    def test_format_content_arg(self):
        from wisp.transport.renderer import format_arg_value
        assert format_arg_value("content", "hello world") == "(11 chars)"

    def test_format_command_arg(self):
        from wisp.transport.renderer import format_arg_value
        assert format_arg_value("command", "ls -la") == "ls -la"

    def test_format_args_dict(self):
        from wisp.transport.renderer import format_arg_value
        assert format_arg_value("arguments", {"a": 1, "b": 2}) == "(2 keys)"

    def test_format_generic_long_value(self):
        from wisp.transport.renderer import format_arg_value
        long_val = "x" * 100
        result = format_arg_value("other", long_val)
        assert result.endswith("...")
        assert len(result) == 80


class TestWrapText:

    def test_wrap_short_text(self):
        from wisp.transport.renderer import wrap_text
        result = wrap_text("hello", 10)
        assert result == ["hello"]

    def test_wrap_long_text(self):
        from wisp.transport.renderer import wrap_text
        result = wrap_text("hello world this is a test", 10)
        assert len(result) > 1
        assert all(len(line) <= 10 for line in result)

    def test_wrap_with_indent(self):
        from wisp.transport.renderer import wrap_text
        # Indent is only applied to continuation lines when there are
        # already lines from previous paragraphs
        result = wrap_text("first para\nsecond para long text", 10, indent="  ")
        # First paragraph: no indent (first para in list)
        assert result[0] == "first para"
        # Second paragraph: first line NOT indented (first of its para),
        # but continuation lines ARE indented
        assert result[1] == "second"       # first line of second para, no indent
        assert result[2] == "  para long"  # continuation, indented
        assert result[3] == "  text"       # continuation, indented

    def test_wrap_preserves_newlines(self):
        from wisp.transport.renderer import wrap_text
        result = wrap_text("line1\nline2", 20)
        assert "line1" in result
        assert "line2" in result


class TestRenderToolCall:

    def test_render_simple_tool_call(self):
        from wisp.transport.renderer import render_tool_call
        result = render_tool_call("read_file", {"path": "test.txt"})
        assert "read_file" in result
        assert "test.txt" in result

    def test_render_tool_call_no_args(self):
        from wisp.transport.renderer import render_tool_call
        result = render_tool_call("git_status", {})
        assert "git_status" in result


class TestRenderThinkingBlock:

    def test_render_thinking(self):
        from wisp.transport.renderer import render_thinking_block
        result = render_thinking_block("Thinking about this...", box_mode=True, width=40)
        assert result is not None
        assert "Thinking" in result

    def test_render_empty_thinking(self):
        from wisp.transport.renderer import render_thinking_block
        result = render_thinking_block("   ", box_mode=True, width=40)
        assert result is None


class TestRenderContentBlock:

    def test_render_content(self):
        from wisp.transport.renderer import render_content_block
        result = render_content_block("Hello world", box_mode=True, width=40)
        assert result is not None
        assert "Hello" in result

    def test_render_empty_content(self):
        from wisp.transport.renderer import render_content_block
        result = render_content_block("   ", box_mode=True, width=40)
        assert result is None


class TestRenderDoneReason:

    def test_render_max_iterations(self):
        from wisp.transport.renderer import render_done_reason
        from wisp.core.events import AgentEvent, TYPE_DONE
        event = AgentEvent(type=TYPE_DONE, data={"reason": "max_iterations", "turns": 5})
        result = render_done_reason(event, iterations=5)
        assert result is not None
        assert "max iterations" in result.lower()

    def test_render_interrupted(self):
        from wisp.transport.renderer import render_done_reason
        from wisp.core.events import AgentEvent, TYPE_DONE
        event = AgentEvent(type=TYPE_DONE, data={"reason": "interrupted"})
        result = render_done_reason(event, iterations=3)
        assert result is not None
        assert "Interrupted" in result

    def test_render_natural_completion(self):
        from wisp.transport.renderer import render_done_reason
        from wisp.core.events import AgentEvent, TYPE_DONE
        event = AgentEvent(type=TYPE_DONE, data={"reason": "natural"})
        result = render_done_reason(event, iterations=2)
        assert result is None
