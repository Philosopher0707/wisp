"""Regression test for max-iterations warning behavior."""

import asyncio
import pytest
from unittest.mock import MagicMock, patch

from wisp.core.agent import WispAgentCore


def _make_agent():
    """Build a minimal WispAgentCore with mocked provider."""
    agent = WispAgentCore.__new__(WispAgentCore)
    agent.config = MagicMock()
    agent.config.max_iterations = 3
    agent.config.max_context_tokens = 100000
    agent.config.auto_compact = False
    agent.config.chars_per_token = 4
    agent.config.compact_threshold_tokens = 75
    agent.config.compact_keep_recent = 6
    agent.config.permission_mode = "full"
    agent.config.show_thinking = False
    agent.config.model = "test-model"
    agent.config.workspace = "/tmp"
    agent.config.plan_mode = False
    agent.config.plan_context = ""
    agent.config._context_tokens_explicit = True
    agent.client = MagicMock()
    agent.client.stream_response = {"message": {"content": "done"}}
    agent.messages = []
    agent.session = None
    agent.agent_memory = MagicMock()
    agent.file_lock = MagicMock()
    agent.change_tracker = MagicMock()
    agent.lsp = MagicMock()
    agent.mcp = MagicMock()
    agent.session_mgr = MagicMock()
    agent.hook_manager = None
    agent._interrupted = False
    agent._paused = asyncio.Event()
    agent._paused.set()
    agent._injected_text = None
    agent._system_prompt = ""
    agent._active_skill = None
    agent._allowed_tools = None
    agent._mcp_initialized = False
    agent.agent_id = "wisp-test"
    agent.role = "agent"
    agent._role_system_extra = ""
    agent.max_iterations = agent.config.max_iterations
    return agent


def _evts_to_list(agent):
    """Consume _arun generator and return events."""
    async def _collect():
        out = []
        async for e in agent._arun("hi", system="test"):
            out.append(e)
        return out
    return asyncio.run(_collect())


class TestMaxIterationsNotWarnOnBreak:
    """Ensure max-iterations warning only appears when loop actually exhausts."""

    def test_no_warning_on_content_break(self):
        """Model returns content on turn 1 — should NOT emit max-iter warning."""
        agent = _make_agent()
        ev = [MagicMock(type="content", text="hello", data={}),
              MagicMock(type="complete", text="", data={})]
        with patch.object(agent, '_run_turn_streaming_events', return_value=iter(ev)):
            events = _evts_to_list(agent)
        types = [e.type for e in events]
        assert "done" in types
        assert "system" not in types  # max-iter warning is typed "system"

    def test_warning_when_loop_exhausts(self):
        """Model always returns tool calls — loop exhausts without break."""
        agent = _make_agent()
        agent.client.stream_response = {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "run_bash", "arguments": {}}}],
            },
        }
        ev = [MagicMock(type="tool_call", text="", data={"name": "run_bash", "arguments": {}})]
        with patch.object(agent, '_run_turn_streaming_events', side_effect=lambda s: iter(ev)):
            events = _evts_to_list(agent)
        done_events = [e for e in events if e.type == "done"]
        assert len(done_events) == 1
        assert done_events[0].data.get("reason") == "max_iterations"
