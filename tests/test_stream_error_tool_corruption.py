"""Tests for Q15: StreamError must not commit partial tool calls to conversation.

When a stream fails mid-tool-call, the partial content contains JSON fragments
that would corrupt the conversation state. The fix detects and discards these
while preserving partial natural-language content.
"""

import asyncio
import os

import pytest
from unittest.mock import MagicMock

from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.stream_events import StreamError, TokenBatch


def _make_agent(ws: str = "/tmp/q15_test_ws"):
    """Build an agent with a valid config that bypasses Ollama health check."""
    os.makedirs(ws + "/.wisp", exist_ok=True)

    config = WispConfig()
    config.model = "test"
    config.workspace = ws
    config.ollama_url = "http://localhost:9999"
    config.auto_approve = False
    config.permission_mode = "auto_edit"
    config.show_thinking = False
    config.max_context_tokens = 8000
    config.chars_per_token = 4
    config.max_iterations = 20
    config.max_reflections = 3
    config.plan_mode = False

    agent = WispAgentCore(config=config, session=None)
    # Prevent Ollama health check
    agent.provider._session = MagicMock()
    agent.client = agent.provider
    agent.messages = [{"role": "user", "content": "create a file"}]
    return agent


class TestPartialToolCallDiscarding:
    """Q15: partial tool-calling content must not enter conversation history."""

    @pytest.mark.parametrize(
        "partial_content,is_tool_call",
        [
            ('{"name": "write_file", "arguments": {"path": "x", ', True),
            ('[{"name": "run_bash"', True),
            ('\u003cfunctions\u003e\n\u003cinvoke name="write_file"\u003e\n\u003cparameter name="path"\u003ex', True),
            ('"name": "edit_file"\n"arguments": {"old"', True),
            ("I will create a new file for you. Let me use write_file.", False),
            ("write_file", False),
            ("", False),
        ],
    )
    def test_is_partial_tool_call_detection(self, partial_content, is_tool_call):
        """_is_partial_tool_call detects JSON/XML tool-call fragments."""
        assert WispAgentCore._is_partial_tool_call(partial_content) is is_tool_call

    def test_partial_tool_call_discarded_on_stream_error(self):
        """JSON partial tool-call content must NOT enter conversation messages."""
        agent = _make_agent()
        mock_client = MagicMock()
        mock_client.generate_stream_events.return_value = iter([
            TokenBatch(phase="content", text="", batch_index=0),
            StreamError(
                phase="error",
                error_type="OllamaError",
                message="Connection dropped",
                partial_thinking="",
                partial_content='{"name": "write_file", "arguments": {"path": "secret.txt"',
            ),
        ])
        agent.client = mock_client

        async def collect():
            return [e async for e in agent._arun("create a file", system="")]

        events = asyncio.run(collect())

        error_events = [e for e in events if e.type == "error"]
        assert len(error_events) == 1
        assert "tool call discarded" in error_events[0].data.get("message", "")

        assert len(agent.messages) == 3
        assistant = agent.messages[-1]
        assert assistant["role"] == "assistant"
        assert assistant["content"][0]["text"] == ""

    def test_natural_language_partial_preserved(self):
        """Partial natural-language streams ARE still preserved."""
        agent = _make_agent()
        mock_client = MagicMock()
        partial = "I was going to explain that the function "
        mock_client.generate_stream_events.return_value = iter([
            StreamError(
                phase="error",
                error_type="TimeoutError",
                message="timed out",
                partial_thinking="",
                partial_content=partial,
            ),
        ])
        agent.client = mock_client

        async def collect():
            return [e async for e in agent._arun("explain", system="")]

        events = asyncio.run(collect())

        error_events = [e for e in events if e.type == "error"]
        assert len(error_events) == 1
        msg = error_events[0].data.get("message", "")
        assert "Partial output:" in msg
        assert partial in msg

        assistant = agent.messages[-1]
        assert assistant["role"] == "assistant"
        assert assistant["content"][0]["text"] == partial

    def test_empty_partial_after_discard(self):
        """When partial is discarded, we still get a valid (empty) assistant msg."""
        agent = _make_agent()
        mock_client = MagicMock()
        mock_client.generate_stream_events.return_value = iter([
            StreamError(
                phase="error",
                error_type="ConnectionError",
                message="dropped",
                partial_thinking="",
                partial_content='{"name": "run_bash", "arguments": {"command": "rm"',
            ),
        ])
        agent.client = mock_client

        async def collect():
            return [e async for e in agent._arun("run", system="")]

        asyncio.run(collect())

        for m in agent.messages:
            content = m.get("content", "")
            if isinstance(content, str):
                assert "run_bash" not in content
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        assert "run_bash" not in str(part.get("text", ""))
                    else:
                        assert "run_bash" not in str(part)

    def test_next_turn_no_parsing_error(self):
        """After discarding tool-call partial, the message list is clean."""
        agent = _make_agent()
        mock_client = MagicMock()
        mock_client.generate_stream_events.return_value = iter([
            StreamError(
                phase="error",
                error_type="APIError",
                message="model overloaded",
                partial_thinking="",
                partial_content='{"name": "write_file", "arguments": {"path": "x"',
            ),
        ])
        agent.client = mock_client

        async def collect():
            return [e async for e in agent._arun("create", system="")]

        events = asyncio.run(collect())
        err1 = [e for e in events if e.type == "error"]
        assert len(err1) == 1

        for msg in agent.messages:
            if msg.get("role") == "assistant":
                parts = msg.get("content", [])
                if isinstance(parts, list):
                    texts = [str(p.get("text", "")) for p in parts]
                else:
                    texts = [str(parts)]
                for text in texts:
                    assert '"name"' not in text or '"role"' in text, (
                        f"Corrupted assistant message: {text[:80]}"
                    )
