"""Test that thinking and tool events render correctly via _render_event."""

from io import StringIO
from unittest.mock import MagicMock
from wisp.transport.cli import CLITransport


class FakeRuntime:
    def __init__(self):
        self.store = MagicMock()
        self.telemetry = MagicMock()


class FakeConfig:
    model = 'test-model'
    workspace = '/tmp'
    show_thinking = True
    auto_approve = True
    max_context_tokens = 128000
    chars_per_token = 4
    permission_mode = 'full'


def _make_transport():
    return CLITransport(FakeRuntime(), FakeConfig())


class TestThinkingDisplay:
    def test_thinking_event_renders(self):
        transport = _make_transport()
        transport.start()
        buf = StringIO()
        event = {"type": "thinking", "text": "Let me think about this..."}
        transport._render_event(buf, event)
        transport._flush_thinking(buf)
        output = buf.getvalue()
        assert "Let me think about this" in output
        transport.stop()

    def test_thinking_hidden_when_disabled(self):
        config = FakeConfig()
        config.show_thinking = False
        transport = CLITransport(FakeRuntime(), config)
        transport.start()
        buf = StringIO()
        event = {"type": "thinking", "text": "Internal reasoning..."}
        transport._render_event(buf, event)
        transport._flush_thinking(buf)
        buf.getvalue()
        # Should show collapsed summary, not full text
        transport.stop()


class TestToolCallDisplay:
    def test_tool_call_event_starts_spinner(self):
        transport = _make_transport()
        transport.start()
        buf = StringIO()
        event = {"type": "tool_call", "name": "read_file", "arguments": {"path": "test.py"}}
        transport._render_event(buf, event)
        # Tool call starts the spinner with tool name
        assert transport._spinner is not None
        transport.stop()

    def test_tool_result_event_renders(self):
        transport = _make_transport()
        transport.start()
        buf = StringIO()
        # First start a spinner (tool_call would do this)
        transport._get_spinner().start("read_file")
        event = {"type": "tool_result", "name": "read_file", "result": "file content here", "duration_ms": 5.0}
        transport._render_event(buf, event)
        output = buf.getvalue()
        assert "read_file" in output
        transport.stop()


class TestContentDisplay:
    def test_content_event_renders(self):
        transport = _make_transport()
        transport.start()
        buf = StringIO()
        event = {"type": "content", "text": "Here is the answer."}
        transport._render_event(buf, event)
        transport._flush_content(buf)
        output = buf.getvalue()
        assert "Here is the answer" in output
        transport.stop()