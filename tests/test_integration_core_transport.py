"""Integration tests for WispAgentCore + transport layer wiring.

Verifies that the event-driven architecture works end-to-end:
- Core yields events → Transport consumes them correctly
- Session state is preserved across the boundary
- Dangerous commands are blocked at core level, transport handles approval
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from wisp.core.agent import WispAgentCore
from wisp.transport.cli import CLITransport, _render_event
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
from wisp.config import WispConfig


@pytest.fixture
def core():
    config = WispConfig()
    config.model = "test-model"
    config.workspace = "/tmp"
    config.auto_compact = False
    return WispAgentCore(config=config)


class TestCoreTransportIntegration:

    @pytest.mark.asyncio
    async def test_core_yields_content_event(self, core):
        """Verify core.run() yields content events that transport can render."""
        # Mock the streaming turn to return a simple text response
        with patch.object(core, '_run_turn_streaming_events') as mock_events:
            mock_events.return_value = iter([])
            core.client.stream_response = {
                "message": {"content": "Hello from core"}
            }
            events = []
            async for event in core.run("say hello"):
                events.append(event)

            # Should get content + done
            assert any(e.type == "content" for e in events)
            assert any(e.type == "done" for e in events)

            content_event = next(e for e in events if e.type == "content")
            assert "Hello from core" in content_event.text

    @pytest.mark.asyncio
    async def test_core_yields_tool_call_events(self, core):
        """Verify core.run() yields tool_call events for transport."""
        async def mock_tool_gen(*args, **kwargs):
            yield tool_result("read_file", "file contents")

        with patch.object(core, '_run_turn_streaming_events') as mock_events:
            mock_events.return_value = iter([])
            core.client.stream_response = {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "read_file", "arguments": {"path": "/tmp/test.py"}}}
                    ],
                }
            }
            # Mock tool execution as async generator
            with patch.object(core, '_run_tool_calls', side_effect=mock_tool_gen):
                events = []
                async for event in core.run("read file"):
                    events.append(event)

                # Should have tool_call event
                tool_events = [e for e in events if e.type == "tool_call"]
                assert len(tool_events) >= 1
                assert tool_events[0].data["name"] == "read_file"

    def test_transport_renders_core_events(self, core):
        """Verify CLITransport can render all event types from core."""
        transport = CLITransport(core)
        events = [
            content("Hello"),
            thinking("Deep thought"),
            tool_call("read_file", {"path": "/tmp/test.py"}),
            tool_result("read_file", "contents"),
            system("compacted", "info"),
            error("oops"),
            done("sid", 1),
        ]
        for event in events:
            rendered = _render_event(event, show_thinking=True)
            # Should not crash
            assert rendered is not None or event.type in ("done",)

    @pytest.mark.asyncio
    async def test_dangerous_command_blocked_at_core(self, core):
        """Verify dangerous commands are blocked by core and yield approval_request."""
        with patch.object(core, '_run_turn_streaming_events') as mock_events:
            mock_events.return_value = iter([])
            core.client.stream_response = {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "run_bash", "arguments": {"command": "rm -rf /"}}}
                    ],
                }
            }
            events = []
            async for event in core.run("run rm -rf /"):
                events.append(event)

            # Should get approval_request + blocked tool_result
            assert any(e.type == "approval_request" for e in events)
            tool_results = [e for e in events if e.type == "tool_result"]
            assert any("Blocked" in e.data["result"] for e in tool_results)

    @pytest.mark.asyncio
    async def test_session_preserved_across_core_transport(self, core):
        """Verify session state is maintained when core yields events."""
        with patch.object(core, '_run_turn_streaming_events') as mock_events:
            mock_events.return_value = iter([])
            core.client.stream_response = {
                "message": {"content": "Response"}
            }
            async for _ in core.run("test prompt"):
                pass

            # Session should be created
            assert core.session is not None
            assert core.session.model == "test-model"
            # Messages should include user + assistant
            assert len(core.messages) >= 2
            assert core.messages[0]["role"] == "user"
            assert core.messages[1]["role"] == "assistant"


class TestBackwardCompat:
    """Verify old WispAgent API still works (thin wrapper tests)."""

    def test_wisp_agent_still_importable(self):
        from wisp.agent import WispAgent
        assert WispAgent is not None

    def test_wisp_agent_inherits_from_core(self):
        from wisp.agent import WispAgent
        from wisp.core.agent import WispAgentCore
        assert issubclass(WispAgent, WispAgentCore)

    def test_helper_functions_re_exported(self):
        from wisp.agent import _is_interactive
        from wisp.core.agent import WispAgentCore
        assert callable(_is_interactive)
        assert callable(WispAgentCore._parse_tool_call)
