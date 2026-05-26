"""Tests for subagent lifecycle hooks — SUBAGENT_SPAWN, COMPLETE, FAIL."""

import pytest

from wisp.config import WispConfig
from wisp.infra.hook_types import HookEvent, HookManager, HookResult
from wisp.multi_agent.task import SubagentContract


class SpyHookManager(HookManager):
    """HookManager that records fired events for assertions."""

    def __init__(self):
        super().__init__()
        self.fired: list[dict] = []

    async def arun_hooks(self, event, context):
        event_type = event.event_type if hasattr(event, 'event_type') else str(event)
        self.fired.append({"event": event_type, "context": dict(context) if context else {}})
        return [HookResult(decision="allow")]


class TestHookEvents:
    """Verify the new hook event constants exist and are distinct."""

    def test_subagent_spawn_event_exists(self):
        assert HookEvent.SUBAGENT_SPAWN == "subagent_spawn"

    def test_subagent_complete_event_exists(self):
        assert HookEvent.SUBAGENT_COMPLETE == "subagent_complete"

    def test_subagent_fail_event_exists(self):
        assert HookEvent.SUBAGENT_FAIL == "subagent_fail"

    def test_hook_event_instance_creation(self):
        e = HookEvent(HookEvent.SUBAGENT_SPAWN)
        assert e.event_type == "subagent_spawn"


class TestOrchestratorHooks:
    """Verify orchestrator fires hooks during subagent lifecycle."""

    @pytest.mark.asyncio
    async def test_spawn_and_complete_hooks_fire(self):
        """Successful subagent: spawn → complete."""
        from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator

        hm = SpyHookManager()
        config = WispConfig()
        config = config.replace(workspace="/tmp")

        orch = SubagentOrchestrator(config=config, hook_manager=hm)
        contract = SubagentContract(
            name="test-sub",
            role="generalist",
            task="do stuff",
            max_iterations=1,
            timeout_seconds=10,
            worktree_isolated=False,
        )

        result = await orch.run(contract)
        events = [f["event"] for f in hm.fired]
        assert "subagent_spawn" in events, f"Missing spawn, got {events}"
        if result.success:
            assert "subagent_complete" in events, f"Missing complete, got {events}"
        else:
            assert "subagent_fail" in events, f"Missing fail, got {events}"

    @pytest.mark.asyncio
    async def test_spawn_hook_context_has_contract_data(self):
        """Spawn hook receives contract fields in context."""
        from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator

        hm = SpyHookManager()
        config = WispConfig()
        config = config.replace(workspace="/tmp")

        orch = SubagentOrchestrator(config=config, hook_manager=hm)
        contract = SubagentContract(
            name="audit-sub",
            role="reviewer",
            task="Review security of auth.py",
            max_iterations=8,
            timeout_seconds=90,
            worktree_isolated=True,
        )

        await orch.run(contract)
        spawn_ctx = hm.fired[0]["context"]
        assert spawn_ctx["event"] == "subagent_spawn"
        assert spawn_ctx["subagent_name"] == "audit-sub"
        assert spawn_ctx["role"] == "reviewer"
        assert "Review security" in spawn_ctx["task"]
        assert spawn_ctx["timeout_seconds"] == 90
        assert spawn_ctx["max_iterations"] == 8
        assert spawn_ctx["worktree_isolated"] is True

    @pytest.mark.asyncio
    async def test_complete_hook_context_has_result_data(self):
        """Complete hook receives result fields in context."""
        from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator

        hm = SpyHookManager()
        config = WispConfig()
        config = config.replace(workspace="/tmp")

        orch = SubagentOrchestrator(config=config, hook_manager=hm)
        contract = SubagentContract(
            name="test-sub",
            role="coder",
            task="write utils.py",
            max_iterations=5,
            timeout_seconds=30,
            worktree_isolated=False,
        )

        result = await orch.run(contract)
        if result.success:
            complete_ctx = hm.fired[-1]["context"]
            assert complete_ctx["event"] == "subagent_complete"
            assert complete_ctx["success"] is True
            assert complete_ctx["elapsed_seconds"] >= 0
            assert "tokens_used" in complete_ctx
            assert "output_preview" in complete_ctx
            assert complete_ctx["error"] == ""

    @pytest.mark.asyncio
    async def test_no_hooks_when_hook_manager_none(self):
        """When hook_manager is None, hooks are silently skipped."""
        from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator

        config = WispConfig()
        config = config.replace(workspace="/tmp")
        orch = SubagentOrchestrator(config=config, hook_manager=None)
        contract = SubagentContract(
            name="test-sub", role="generalist", task="hi",
            max_iterations=1, timeout_seconds=10, worktree_isolated=False,
        )
        # Should not raise
        result = await orch.run(contract)
        assert result.task_id == "test-sub"

    @pytest.mark.asyncio
    async def test_fail_hook_on_error(self):
        """Subagent that fails should fire subagent_fail."""
        from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator

        hm = SpyHookManager()
        config = WispConfig()
        config = config.replace(workspace="/tmp")

        orch = SubagentOrchestrator(config=config, hook_manager=hm)

        # Force a failure: invalid timeout
        contract = SubagentContract(
            name="bad-sub", role="generalist", task="doom",
            max_iterations=0,  # this will be rejected by run() guard
            timeout_seconds=10,
            worktree_isolated=False,
        )

        result = await orch.run(contract)
        assert result.success is False

        events = [f["event"] for f in hm.fired]
        assert "subagent_spawn" in events, f"Got {events}"
        assert "subagent_fail" in events, f"Got {events}"

        fail_ctx = hm.fired[-1]["context"]
        assert fail_ctx["success"] is False
        assert fail_ctx["error"]

    @pytest.mark.asyncio
    async def test_depth_limit_fires_fail_hook(self):
        """Subagent exceeding depth limit fires fail hook."""
        from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator

        hm = SpyHookManager()
        config = WispConfig()
        config = config.replace(workspace="/tmp")

        orch = SubagentOrchestrator(config=config, hook_manager=hm)
        orch._max_depth = 1  # Force depth limit

        contract = SubagentContract(
            name="deep-sub", role="generalist", task="recursive",
            max_iterations=5, timeout_seconds=30, worktree_isolated=False,
            _subagent_depth=2,  # exceeds max
        )

        result = await orch.run(contract)
        assert result.success is False
        assert "DEPTH LIMIT" in result.output

        # Spawn hook still fires (before depth check fails)
        events = [f["event"] for f in hm.fired]
        assert "subagent_spawn" in events


class TestHookContextTruncation:
    """Verify large fields are safely truncated in hook context."""

    @pytest.mark.asyncio
    async def test_task_truncated_in_context(self):
        from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator

        hm = SpyHookManager()
        config = WispConfig()
        config = config.replace(workspace="/tmp")

        orch = SubagentOrchestrator(config=config, hook_manager=hm)
        long_task = "x" * 1000
        contract = SubagentContract(
            name="test", role="generalist", task=long_task,
            max_iterations=1, timeout_seconds=10, worktree_isolated=False,
        )

        await orch.run(contract)
        spawn_ctx = hm.fired[0]["context"]
        assert len(spawn_ctx["task"]) <= 500
