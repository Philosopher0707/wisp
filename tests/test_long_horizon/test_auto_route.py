"""Tests for long-horizon auto-routing in WispAgentCore.

Covers: auto-detection, routing decision, event streaming, config override,
and integration with the normal agent flow.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from wisp.core.agent import WispAgentCore
from wisp.core.events import (
    AgentEvent,
    TYPE_SYSTEM,
    TYPE_TASK_STARTED,
    TYPE_TASK_STEP_STARTED,
    TYPE_TASK_STEP_COMPLETED,
    TYPE_TASK_COMPLETED,
    TYPE_TASK_FAILED,
    TYPE_CONTENT,
    TYPE_DONE,
    TYPE_ERROR,
)
from wisp.config import WispConfig


# ══════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_config(tmp_path, monkeypatch):
    """Create a minimal WispConfig for testing."""
    monkeypatch.setenv("WISP_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("WISP_MODEL", "test-model")
    cfg = WispConfig()
    cfg.max_iterations = 5
    return cfg


@pytest.fixture
def mock_agent(mock_config, tmp_path):
    """Create a WispAgentCore with mocked dependencies."""
    agent = WispAgentCore(config=mock_config)
    # Mock the provider/client to avoid actual LLM calls
    agent.provider = MagicMock()
    agent.client = MagicMock()
    agent.client.generate_stream_events = MagicMock(return_value=[])
    agent.client.stream_response = {
        "message": {"role": "assistant", "content": "mock response", "thinking": ""}
    }
    return agent


# ══════════════════════════════════════════════════════════════════════
# Auto-routing detection
# ══════════════════════════════════════════════════════════════════════

class TestAutoRoutingDetection:
    @pytest.mark.asyncio
    async def test_short_prompt_not_routed(self, mock_agent):
        """Brief prompts should use normal flow, not long-horizon."""
        events = []
        async for event in mock_agent.run("What is 2+2?"):
            events.append(event)

        # Should get normal flow events (content, done)
        types = [e.type for e in events]
        assert TYPE_CONTENT in types or TYPE_DONE in types
        assert TYPE_TASK_STARTED not in types

    @pytest.mark.asyncio
    async def test_long_prompt_auto_routed(self, mock_agent):
        """Complex prompts should trigger auto-routing."""
        # Patch run_long_task to avoid actual execution
        mock_events = [
            AgentEvent(TYPE_TASK_STARTED, {"task_id": "test-123", "goal": "Migrate Flask", "total_steps": 3}),
            AgentEvent(TYPE_TASK_COMPLETED, {"task_id": "test-123", "goal": "Migrate Flask", "completed_steps": 3, "total_steps": 3}),
        ]

        with patch.object(mock_agent, 'run_long_task', return_value=async_iter(mock_events)):
            events = []
            async for event in mock_agent.run("Migrate Flask to FastAPI"):
                events.append(event)

            types = [e.type for e in events]
            assert TYPE_SYSTEM in types  # Should explain routing
            assert TYPE_TASK_STARTED in types

    @pytest.mark.asyncio
    async def test_auto_routing_disabled(self, mock_agent):
        """When auto_long_task=False, should not route."""
        mock_agent.config.auto_long_task = False

        events = []
        async for event in mock_agent.run("Migrate Flask to FastAPI"):
            events.append(event)

        types = [e.type for e in events]
        assert TYPE_TASK_STARTED not in types

    @pytest.mark.asyncio
    async def test_system_event_explains_routing(self, mock_agent):
        """Should yield a system event explaining why it routed."""
        mock_events = [
            AgentEvent(TYPE_TASK_STARTED, {"task_id": "test-123", "goal": "Refactor", "total_steps": 2}),
            AgentEvent(TYPE_TASK_COMPLETED, {"task_id": "test-123", "goal": "Refactor", "completed_steps": 2, "total_steps": 2}),
        ]

        with patch.object(mock_agent, 'run_long_task', return_value=async_iter(mock_events)):
            events = []
            async for event in mock_agent.run("Refactor the auth module"):
                events.append(event)

            system_events = [e for e in events if e.type == TYPE_SYSTEM]
            assert len(system_events) >= 1
            assert "long-horizon" in system_events[0].data.get("message", "").lower()


# ══════════════════════════════════════════════════════════════════════
# run_long_task method
# ══════════════════════════════════════════════════════════════════════

class TestRunLongTaskMethod:
    @pytest.mark.asyncio
    async def test_run_long_task_yields_events(self, mock_agent):
        """run_long_task should yield task events from the runner."""
        # Mock the runner to avoid actual execution
        mock_runner = MagicMock()
        mock_runner.run = MagicMock(return_value=async_iter([
            AgentEvent(TYPE_TASK_STARTED, {"task_id": "t1", "goal": "G", "total_steps": 1}),
            AgentEvent(TYPE_TASK_STEP_STARTED, {"task_id": "t1", "step_id": "s1", "step_index": 0, "description": "D"}),
            AgentEvent(TYPE_TASK_STEP_COMPLETED, {"task_id": "t1", "step_id": "s1", "step_index": 0, "result": "R", "duration_ms": 100}),
            AgentEvent(TYPE_TASK_COMPLETED, {"task_id": "t1", "goal": "G", "completed_steps": 1, "total_steps": 1}),
        ]))

        with patch('wisp.long_horizon.runner.LongHorizonRunner', return_value=mock_runner):
            events = []
            async for event in mock_agent.run_long_task(goal="Test goal"):
                events.append(event)

            types = [e.type for e in events]
            assert TYPE_TASK_STARTED in types
            assert TYPE_TASK_STEP_STARTED in types
            assert TYPE_TASK_STEP_COMPLETED in types
            assert TYPE_TASK_COMPLETED in types

    @pytest.mark.asyncio
    async def test_run_long_task_with_resume(self, mock_agent):
        """run_long_task should support resume_from parameter."""
        mock_runner = MagicMock()
        mock_runner.run = MagicMock(return_value=async_iter([
            AgentEvent(TYPE_TASK_COMPLETED, {"task_id": "existing", "goal": "G", "completed_steps": 5, "total_steps": 5}),
        ]))

        with patch('wisp.long_horizon.runner.LongHorizonRunner', return_value=mock_runner):
            events = []
            async for event in mock_agent.run_long_task(goal="", resume_from="existing-task"):
                events.append(event)

            # Verify runner.run was called with resume_from
            mock_runner.run.assert_called_once()
            call_kwargs = mock_runner.run.call_args[1]
            assert call_kwargs.get('resume_from') == "existing-task"


# ══════════════════════════════════════════════════════════════════════
# Integration with normal flow
# ══════════════════════════════════════════════════════════════════════

class TestIntegrationWithNormalFlow:
    @pytest.mark.asyncio
    async def test_normal_flow_unchanged_for_short_prompts(self, mock_agent):
        """Short prompts should still work exactly as before."""
        events = []
        async for event in mock_agent.run("Hello"):
            events.append(event)

        # Should complete without task events
        types = [e.type for e in events]
        assert TYPE_TASK_STARTED not in types
        assert any(t in types for t in [TYPE_CONTENT, TYPE_DONE, TYPE_ERROR])

    @pytest.mark.asyncio
    async def test_auto_route_skips_normal_loop(self, mock_agent):
        """When auto-routed, should not enter the normal iteration loop."""
        mock_events = [
            AgentEvent(TYPE_TASK_STARTED, {"task_id": "t1", "goal": "G", "total_steps": 1}),
            AgentEvent(TYPE_TASK_COMPLETED, {"task_id": "t1", "goal": "G", "completed_steps": 1, "total_steps": 1}),
        ]

        with patch.object(mock_agent, 'run_long_task', return_value=async_iter(mock_events)):
            # Track if _run_turn_streaming_events is called (normal loop)
            with patch.object(mock_agent, '_run_turn_streaming_events') as mock_stream:
                events = []
                async for event in mock_agent.run("Migrate Flask to FastAPI"):
                    events.append(event)

                # Normal loop should NOT be called
                mock_stream.assert_not_called()


# ══════════════════════════════════════════════════════════════════════
# Edge cases
# ══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_prompt_not_routed(self, mock_agent):
        """Empty prompts should not trigger long-horizon routing."""
        events = []
        async for event in mock_agent.run(""):
            events.append(event)

        types = [e.type for e in events]
        assert TYPE_TASK_STARTED not in types

    @pytest.mark.asyncio
    async def test_whitespace_prompt_not_routed(self, mock_agent):
        """Whitespace-only prompts should not trigger routing."""
        events = []
        async for event in mock_agent.run("   \n  "):
            events.append(event)

        types = [e.type for e in events]
        assert TYPE_TASK_STARTED not in types

    @pytest.mark.asyncio
    async def test_failed_task_event_propagated(self, mock_agent):
        """Task failure events should be yielded to the transport."""
        mock_events = [
            AgentEvent(TYPE_TASK_STARTED, {"task_id": "t1", "goal": "G", "total_steps": 2}),
            AgentEvent(TYPE_TASK_FAILED, {"task_id": "t1", "goal": "G", "reason": "Step timeout"}),
        ]

        with patch.object(mock_agent, 'run_long_task', return_value=async_iter(mock_events)):
            events = []
            async for event in mock_agent.run("Refactor everything"):
                events.append(event)

            types = [e.type for e in events]
            assert TYPE_TASK_FAILED in types


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

async def async_iter(items):
    """Helper to create an async iterator from a list."""
    for item in items:
        yield item
