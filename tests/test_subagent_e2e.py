"""End-to-end subagent execution tests with real orchestrator.

Verifies the full lifecycle: Contract → Orchestrator → Runner → Core → Provider → Result.
Uses mock providers to avoid needing a running Ollama instance.
"""

from __future__ import annotations

import asyncio
import pytest
from pathlib import Path

from wisp.config import WispConfig
from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator
from wisp.multi_agent.task import SubagentContract, SubagentResult


# ── Mock providers ────────────────────────────────────────────────

class _SimpleProvider:
    """Provider that yields a single content event then done."""

    def __init__(self, response: str = "Task completed successfully."):
        self._response = response
        self._stream_response = None

    def generate_stream_events(self, system_prompt: str, messages: list[dict],
                                tools: list | None = None, checkpoint_every: int = 50):
        yield {"type": "content", "text": self._response}
        yield {"type": "done", "done_reason": "stop"}

    def generate_stream_events_async(self, system_prompt: str, messages: list[dict],
                                      tools: list | None = None, checkpoint_every: int = 50):
        return self._async_gen(system_prompt, messages, tools, checkpoint_every)

    async def _async_gen(self, system_prompt, messages, tools, checkpoint_every):
        yield {"type": "content", "text": self._response}
        yield {"type": "done", "done_reason": "stop"}

    def health_check(self):
        return {"status": "healthy", "models": 1}

    def list_models(self):
        return [{"id": "mock-model", "name": "mock-model"}]

    def get_model_info(self, model):
        return {"id": model, "context_length": 128000}

    def close(self):
        pass


class _ToolCallProvider:
    """Provider that makes a tool call then returns content with the result."""

    def __init__(self):
        self._stream_response = None
        self._call_count = 0

    def generate_stream_events(self, system_prompt: str, messages: list[dict],
                                tools: list | None = None, checkpoint_every: int = 50):
        if self._call_count == 0:
            self._call_count += 1
            yield {
                "type": "tool_call",
                "name": "list_files",
                "arguments": {"path": "."},
                "id": "call_1",
            }
            yield {"type": "done", "done_reason": "tool_calls"}
        else:
            # Second iteration — produce final content
            yield {"type": "content", "text": "I listed the files. The project has src/, tests/, and README.md."}
            yield {"type": "done", "done_reason": "stop"}

    def generate_stream_events_async(self, system_prompt: str, messages: list[dict],
                                      tools: list | None = None, checkpoint_every: int = 50):
        return self._async_gen(system_prompt, messages, tools, checkpoint_every)

    async def _async_gen(self, system_prompt, messages, tools, checkpoint_every):
        if self._call_count == 0:
            self._call_count += 1
            yield {
                "type": "tool_call",
                "name": "list_files",
                "arguments": {"path": "."},
                "id": "call_1",
            }
            yield {"type": "done", "done_reason": "tool_calls"}
        else:
            yield {"type": "content", "text": "I listed the files. The project has src/, tests/, and README.md."}
            yield {"type": "done", "done_reason": "stop"}

    def health_check(self):
        return {"status": "healthy", "models": 1}

    def list_models(self):
        return [{"id": "mock-model", "name": "mock-model"}]

    def get_model_info(self, model):
        return {"id": model, "context_length": 128000}

    def close(self):
        pass


class _UnreachableProvider:
    """Provider that simulates an unreachable model."""

    def __init__(self):
        self._stream_response = None

    def generate_stream_events(self, **kwargs):
        yield {"type": "error", "message": "Connection refused"}
        yield {"type": "done", "done_reason": "error"}

    def generate_stream_events_async(self, **kwargs):
        return self._async_gen(**kwargs)

    async def _async_gen(self, **kwargs):
        yield {"type": "error", "message": "Connection refused"}
        yield {"type": "done", "done_reason": "error"}

    def health_check(self):
        return {"status": "unhealthy", "error": "Connection refused"}

    def list_models(self):
        return []

    def get_model_info(self, model):
        return {"id": model, "context_length": 128000}

    def close(self):
        pass


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def config(tmp_path):
    cfg = WispConfig()
    return cfg.replace(workspace=str(tmp_path), model="mock-model", auto_approve=True)


@pytest.fixture
def orchestrator(config, tmp_path):
    orch = SubagentOrchestrator(config=config, workspace=tmp_path)
    # Inject mock provider into the runner's cache
    orch._runner._provider_cache["ollama:mock-model"] = _SimpleProvider()
    return orch


# ── Tests ────────────────────────────────────────────────────────

class TestSubagentEndToEnd:
    """Full lifecycle tests: Contract → Orchestrator → Runner → Result."""

    @pytest.mark.asyncio
    async def test_simple_subagent_run(self, orchestrator):
        """A simple subagent with a valid contract returns content."""
        contract = SubagentContract(
            name="test-agent",
            role="generalist",
            task="Say hello",
            timeout_seconds=10.0,
            max_iterations=3,
            worktree_isolated=False,
        )
        result = await orchestrator.run(contract)

        assert result.success is True
        assert result.task_id == "test-agent"
        assert "Task completed" in result.output
        assert result.elapsed_seconds < 10.0
        assert result.error is None

    @pytest.mark.asyncio
    async def test_subagent_with_tool_calls(self, orchestrator):
        """A subagent that makes tool calls gets results fed back."""
        # Replace provider with one that makes tool calls
        orchestrator._runner._provider_cache["ollama:mock-model"] = _ToolCallProvider()

        contract = SubagentContract(
            name="file-lister",
            role="generalist",
            task="List files in the current directory",
            timeout_seconds=10.0,
            max_iterations=5,
            worktree_isolated=False,
        )
        result = await orchestrator.run(contract)

        assert result.success is True
        assert result.iterations_used >= 1  # At least one tool call
        assert len(result.tool_calls) >= 1
        assert result.tool_calls[0]["name"] == "list_files"

    @pytest.mark.asyncio
    async def test_unreachable_provider_fails_fast(self, tmp_path):
        """When the provider is unreachable, the subagent should fail fast."""
        cfg = WispConfig()
        cfg = cfg.replace(workspace=str(tmp_path), model="mock-model", auto_approve=True)
        orch = SubagentOrchestrator(config=cfg, workspace=tmp_path)
        orch._runner._provider_cache["ollama:mock-model"] = _UnreachableProvider()

        contract = SubagentContract(
            name="doomed-agent",
            role="generalist",
            task="Do something",
            timeout_seconds=10.0,
            max_iterations=3,
            worktree_isolated=False,
        )
        result = await orch.run(contract)

        # The health check should catch this before the timeout
        assert result.success is False
        assert result.elapsed_seconds < 5.0  # Should fail fast

    @pytest.mark.asyncio
    async def test_subagent_result_is_cached(self, orchestrator):
        """Running the same contract twice should return cached result on second run."""
        contract = SubagentContract(
            name="cached-agent",
            role="generalist",
            task="Do a thing",
            timeout_seconds=10.0,
            max_iterations=3,
            worktree_isolated=False,
        )
        result1 = await orchestrator.run(contract)
        assert result1.success is True

        # Second run should hit cache
        result2 = await orchestrator.run(contract)
        assert result2.success is True
        # Same output (cached)
        assert result2.output == result1.output

        # Cache stats should show a hit
        stats = orchestrator.get_cache_stats()
        assert stats["hits"] >= 1

    @pytest.mark.asyncio
    async def test_depth_limit_blocks_nested_spawn(self, orchestrator):
        """Subagent at max depth should be rejected."""
        contract = SubagentContract(
            name="deep-agent",
            role="generalist",
            task="Do something",
            timeout_seconds=10.0,
            max_iterations=3,
            worktree_isolated=False,
            _subagent_depth=99,  # Way over the limit
        )
        result = await orchestrator.run(contract)

        assert result.success is False
        assert "DEPTH LIMIT" in result.output

    @pytest.mark.asyncio
    async def test_parallel_subagents_complete(self, orchestrator):
        """Multiple subagents run in parallel and all return results."""
        contracts = [
            SubagentContract(
                name=f"agent-{i}",
                role="generalist",
                task=f"Do task {i}",
                timeout_seconds=10.0,
                max_iterations=3,
                worktree_isolated=False,
            )
            for i in range(3)
        ]
        results = await orchestrator.run_parallel(contracts)

        assert len(results) == 3
        assert all(r.success for r in results)
        assert all(r.task_id.startswith("agent-") for r in results)

    @pytest.mark.asyncio
    async def test_parallel_subagents_share_context(self, orchestrator):
        """Parallel subagents get a SharedContext injected."""
        from wisp.multi_agent.shared_context import SharedContext

        contracts = [
            SubagentContract(
                name=f"agent-{i}",
                role="generalist",
                task=f"Research topic {i}",
                timeout_seconds=10.0,
                max_iterations=3,
                worktree_isolated=False,
            )
            for i in range(3)
        ]
        results = await orchestrator.run_parallel(contracts, shared_context=True)

        assert len(results) == 3
        # Each contract should have a shared context set
        for c in contracts:
            assert c._shared_context is not None
            assert isinstance(c._shared_context, SharedContext)

    @pytest.mark.asyncio
    async def test_timeout_produces_diagnostic_message(self, tmp_path):
        """Timeout error includes diagnostic info."""
        cfg = WispConfig()
        cfg = cfg.replace(workspace=str(tmp_path), model="mock-model", auto_approve=True)
        class _HangingProvider:
            def __init__(self):
                self._stream_response = None
            def generate_stream_events(self, **kwargs):
                # Simulate a provider that yields nothing for a long time
                import time as _t
                _t.sleep(100)  # Will be interrupted by asyncio.timeout
                yield {"type": "done"}
            def generate_stream_events_async(self, **kwargs):
                return self._async_gen(**kwargs)
            async def _async_gen(self, **kwargs):
                await asyncio.sleep(100)
                yield {"type": "done"}
            def health_check(self):
                return {"status": "healthy", "models": 1}
            def list_models(self):
                return [{"id": "mock-model"}]
            def get_model_info(self, model):
                return {"id": model, "context_length": 128000}
            def close(self):
                pass

        orch = SubagentOrchestrator(config=cfg, workspace=tmp_path)
        orch._runner._provider_cache["ollama:mock-model"] = _HangingProvider()

        contract = SubagentContract(
            name="hanging-agent",
            role="generalist",
            task="Do something slow",
            timeout_seconds=1.0,  # Very short timeout
            max_iterations=3,
            worktree_isolated=False,
        )
        result = await orch.run(contract)

        assert result.success is False
        assert result.timed_out is True
        assert "no tool calls" in result.output or "TIMED OUT" in result.output

    @pytest.mark.asyncio
    async def test_spawn_with_guards_returns_output(self, orchestrator):
        """spawn_with_guards returns the output string."""
        output = await orchestrator.spawn_with_guards(
            task="Say hello",
            timeout_seconds=10.0,
            max_iterations=3,
            worktree_isolated=False,
            auto_approve=True,
        )
        assert "Task completed" in output

    @pytest.mark.asyncio
    async def test_telemetry_records_run(self, orchestrator):
        """Telemetry records the subagent run."""
        contract = SubagentContract(
            name="telemetry-agent",
            role="generalist",
            task="Do something",
            timeout_seconds=10.0,
            max_iterations=3,
            worktree_isolated=False,
        )
        await orchestrator.run(contract)

        telemetry = orchestrator.get_telemetry_summary()
        assert "mock-model" in telemetry
        assert telemetry["mock-model"]["count"] >= 1

    @pytest.mark.asyncio
    async def test_persistence_saves_result(self, orchestrator):
        """Subagent results are persisted to JSONL."""
        contract = SubagentContract(
            name="persist-agent",
            role="generalist",
            task="Do something",
            timeout_seconds=10.0,
            max_iterations=3,
            worktree_isolated=False,
        )
        await orchestrator.run(contract)

        records = orchestrator.get_persisted_results()
        assert len(records) >= 1
        assert records[-1]["task_id"] == "persist-agent"
        assert records[-1]["success"] is True
