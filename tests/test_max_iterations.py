"""Regression test for max-iterations warning behavior."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import warnings
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from wisp.core.agent import WispAgentCore as _LegacyWispAgentCore
# Tests that use old API — keep as reference until Phase 7 migration


class _AsyncIteratorMock:
    """A mock that behaves as an async iterator (what async for expects)."""
    def __init__(self, items):
        self._items = list(items)
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._idx]
        self._idx += 1
        return item


class _MockToolExecutor:
    """Minimal mock that satisfies the agent's async-for-on-execute contract."""
    def __init__(self):
        self._result_events = []

    def set_result_events(self, events):
        self._result_events = list(events)

    def execute(self, **kwargs):
        return _AsyncIteratorMock(self._result_events)

    async def build_tool_message(self, **kwargs):
        return {"role": "tool", "content": "mock result"}


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
    agent.config.max_reflections = 3
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
    agent.tool_executor = _MockToolExecutor()
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
        done_reasons = [e.data.get("reason", "") for e in events if str(e.type) == "done"]
        assert "max_iterations" not in done_reasons
        assert "max_reflections" not in done_reasons

    def test_warning_when_loop_exhausts(self):
        """Model always returns tool calls — loop exhausts without break."""
        agent = _make_agent()
        agent.client.stream_response = {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "run_bash", "arguments": {}}}],
            },
        }
        ev_stream = [MagicMock(type="tool_call", text="", data={})]
        agent.tool_executor.set_result_events([
            MagicMock(type="tool_result", text="", data={"result": "ok"}),
        ])
        with patch.object(agent, '_run_turn_streaming_events', side_effect=lambda s: iter(ev_stream)):
            events = _evts_to_list(agent)
        done_reasons = [e.data.get("reason", "") for e in events if str(e.type) == "done"]
        assert "max_iterations" in done_reasons

    def test_reflection_loop_detected(self):
        """Same tool call repeated > max_reflections triggers break."""
        agent = _make_agent()
        agent.max_iterations = 100  # make sure max_iterations doesn't fire first
        agent.client.stream_response = {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "run_bash", "arguments": {"cmd": "echo hi"}}}],
            },
        }
        ev_stream = [MagicMock(type="tool_call", text="", data={})]
        agent.tool_executor.set_result_events([
            MagicMock(type="tool_result", text="", data={"result": "ok"}),
        ])
        with patch.object(agent, '_run_turn_streaming_events', side_effect=lambda s: iter(ev_stream)):
            events = _evts_to_list(agent)
        done_reasons = [e.data.get("reason", "") for e in events if str(e.type) == "done"]
        assert "max_reflections" in done_reasons

    def test_reflection_disabled(self):
        """With max_reflections=0 we never detect loops."""
        agent = _make_agent()
        agent.config.max_reflections = 0
        agent.max_iterations = 2
        agent.client.stream_response = {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "run_bash", "arguments": {"cmd": "echo hi"}}}],
            },
        }
        ev_stream = [MagicMock(type="tool_call", text="", data={})]
        agent.tool_executor.set_result_events([
            MagicMock(type="tool_result", text="", data={"result": "ok"}),
        ])
        with patch.object(agent, '_run_turn_streaming_events', side_effect=lambda s: iter(ev_stream)):
            events = _evts_to_list(agent)
        done_reasons = [e.data.get("reason", "") for e in events if str(e.type) == "done"]
        # Should max out iterations but NOT emit reflection warning
        assert "max_iterations" in done_reasons
        assert "max_reflections" not in done_reasons
