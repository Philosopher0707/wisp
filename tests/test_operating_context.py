"""Tests for the operating-context block in the system prompt.

Verifies the agent's own posture (model/provider, permission mode,
subagent depth, workspace, session) and live background-agent state
(including settled-work notifications) reach the model every turn —
and that per-turn state is NOT poisoned by the static prompt cache.
"""


import pytest
from unittest.mock import MagicMock

from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.multi_agent.background import BackgroundAgentManager
from wisp.multi_agent.task import SubagentContract
from wisp.tool_executor import ToolExecutor
from tests.test_background_agents import FakeOrchestrator


def _mk_core(permission_mode="full", depth=0, manager=None, workspace="/tmp") -> WispAgentCore:
    cfg = WispConfig()
    cfg = cfg.replace(
        model="test-model",
        provider="mockprov",
        workspace=workspace,
        permission_mode=permission_mode,
    )
    object.__setattr__(cfg, "_subagent_depth", depth)
    executor = ToolExecutor(
        config=cfg,
        hook_manager=MagicMock(),
        subagent_orchestrator=FakeOrchestrator() if manager is None else manager._orchestrator,
        background_agents=manager,
    )
    return WispAgentCore(config=cfg, provider=MagicMock(), tool_executor=executor)


class TestOperatingContextBlock:
    def test_contains_identity_workspace_session(self):
        core = _mk_core()
        session = {"id": "sess-op", "workspace": "/tmp", "messages": []}
        block = core._build_operating_context(session)
        assert "## Operating context" in block
        assert "test-model" in block
        assert "mockprov" in block
        assert "/tmp" in block
        assert "sess-op" in block

    def test_permission_mode_shown_when_not_full(self):
        core = _mk_core(permission_mode="auto_edit")
        block = core._build_operating_context({"id": "s", "workspace": "/w"})
        assert "permission mode" in block.lower()
        assert "auto_edit" in block

    def test_full_mode_omits_permission_line(self):
        core = _mk_core(permission_mode="full")
        block = core._build_operating_context({"id": "s", "workspace": "/w"})
        assert "permission" not in block.lower()

    def test_subagent_depth_declared(self):
        core = _mk_core(depth=2)
        block = core._build_operating_context({"id": "s", "workspace": "/w"})
        assert "subagent" in block.lower()
        assert "depth 2" in block

    def test_root_agent_has_no_depth_line(self):
        core = _mk_core(depth=0)
        block = core._build_operating_context({"id": "s", "workspace": "/w"})
        assert "nesting depth" not in block

    def test_empty_without_config_or_manager(self):
        from wisp.core.engine import WispAgentCore as Core
        bare = Core(provider=MagicMock())
        assert bare._build_operating_context({}) == ""

    @pytest.mark.asyncio
    async def test_counts_and_notifications_appear_once(self, tmp_path):
        orch = FakeOrchestrator(delay=0.0, success=True)
        mgr = BackgroundAgentManager(orch)
        core = _mk_core(manager=mgr, workspace=str(tmp_path))
        session = {"id": "s1", "workspace": str(tmp_path), "messages": []}

        # Nothing yet → no agents line.
        assert "background agents:" not in core._build_system_prompt(session, query=None)

        launch = await mgr.launch(SubagentContract(name="bg-n", task="notify me"))
        await mgr.result(launch["agent_id"], wait_seconds=2.0)

        first = core._build_system_prompt(session, query=None)
        assert "## Operating context" in first
        assert "1 finished" in first
        assert "finished since your last turn" in first
        assert "notify me" in first

        # Drained: counts remain, notification line is gone.
        second = core._build_system_prompt(session, query=None)
        assert "1 finished" in second
        assert "finished since your last turn" not in second

    @pytest.mark.asyncio
    async def test_running_agent_counted(self):
        orch = FakeOrchestrator(delay=5.0)
        mgr = BackgroundAgentManager(orch)
        core = _mk_core(manager=mgr)
        launch = await mgr.launch(SubagentContract(name="bg-r", task="t"))
        try:
            block = core._build_operating_context({"id": "s", "workspace": "/w"})
            assert "1 running" in block
        finally:
            mgr.cancel(launch["agent_id"])

    def test_block_is_per_turn_not_cached(self, tmp_path):
        """Two different sessions in the same workspace must each see their
        own operating context despite the shared static-prompt cache."""
        core = _mk_core(workspace=str(tmp_path))
        a = core._build_system_prompt({"id": "A", "workspace": str(tmp_path)}, query=None)
        b = core._build_system_prompt({"id": "B", "workspace": str(tmp_path)}, query=None)
        assert "session: A" in a and "session: B" in b
