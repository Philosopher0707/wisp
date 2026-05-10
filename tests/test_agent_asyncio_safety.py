"""Regression tests for asyncio.run() safety in WispAgent wrapper."""

import asyncio
from unittest.mock import MagicMock, patch

from wisp.agent import WispAgent


def _make_agent():
    """Return a WispAgent with a mocked core to avoid real Ollama calls."""
    agent = WispAgent.__new__(WispAgent)
    agent.client = MagicMock()
    agent.client.stream_response = {"message": {"content": "ok"}}
    agent.messages = []
    agent.config = MagicMock()
    agent.config.auto_approve = True
    # Prevent side effects from real init
    agent.agent_memory = MagicMock()
    agent.file_lock = MagicMock()
    agent.change_tracker = MagicMock()
    agent.lsp = MagicMock()
    agent.mcp = MagicMock()
    agent.session_mgr = MagicMock()
    agent.session = None
    agent._interrupted = False
    agent._paused = asyncio.Event()
    agent._paused.set()
    agent._injected_text = None
    agent._system_prompt = ""
    agent._active_skill = None
    agent._allowed_tools = None
    agent.agent_id = "wisp-test"
    agent.role = "agent"
    agent._role_system_extra = ""
    return agent


async def _mock_events(self, system=""):
    """Async generator returning nothing — matches _run_turn_streaming_events signature."""
    return
    yield


class TestAsyncioRunSafety:
    """Ensure WispAgent._run_turn_streaming and _execute_loop work inside running loops."""

    def test_run_turn_streaming_standalone(self):
        """Should work when no event loop is running."""
        agent = _make_agent()
        with patch.object(agent, "_run_turn_streaming_events", side_effect=_mock_events):
            result = agent._run_turn_streaming("test")
        assert result == {"message": {"content": "ok"}}

    def test_run_turn_streaming_inside_loop(self):
        """Must not crash when an event loop is already running."""

        async def inner():
            agent = _make_agent()
            with patch.object(agent, "_run_turn_streaming_events", side_effect=_mock_events):
                result = agent._run_turn_streaming("test")
            return result

        result = asyncio.run(inner())
        assert result == {"message": {"content": "ok"}}

    def test_execute_loop_inside_loop(self):
        """Must not crash when called inside a running loop."""

        async def _arun_patched(prompt, system=None, approval_handler=None, images=None):
            return
            yield

        async def inner():
            agent = _make_agent()
            with patch.object(agent, "_run_turn_streaming_events", side_effect=_mock_events):
                with patch.object(agent, "_arun", side_effect=_arun_patched):
                    agent._execute_loop("test", "/tmp")
            return True

        result = asyncio.run(inner())
        assert result is True
