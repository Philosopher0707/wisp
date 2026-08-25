"""Tests for background subagents — spawn_background + lifecycle tools.

Covers:
- BackgroundAgentManager: launch/wait/list/cancel/send/prune semantics
- SubagentRunner resume: continuing a stored session via _resume_session_id
- ToolExecutor routing for the five background tool names
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from wisp.config import WispConfig
from wisp.infra.store import UnifiedStore
from wisp.multi_agent.background import (
    BackgroundAgentManager,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
)
from wisp.multi_agent._runner import SubagentRunner
from wisp.multi_agent.task import SubagentContract, SubagentResult
from wisp.tool_executor import ToolExecutor
from wisp.tools.registry import TOOL_IMPLS, TOOL_SCHEMAS


# ── Fakes ─────────────────────────────────────────────────────────────────


class FakeOrchestrator:
    """Stands in for SubagentOrchestrator; records contracts it runs."""

    def __init__(self, delay: float = 0.05, success: bool = True, session_id: str = "sess-fake"):
        self.delay = delay
        self.success = success
        self.session_id = session_id
        self.contracts: list[SubagentContract] = []

    async def _run_with_retry(self, contract: SubagentContract) -> SubagentResult:
        self.contracts.append(contract)
        await asyncio.sleep(self.delay)
        return SubagentResult(
            task_id=contract.name,
            success=self.success,
            output="did the thing" if self.success else "",
            files_changed=["a.py"] if self.success else [],
            elapsed_seconds=self.delay,
            error=None if self.success else "mock failure",
            session_id=self.session_id,
        )


def _contract(task: str = "audit auth.py", **overrides) -> SubagentContract:
    return SubagentContract(name="bg-test", role="coder", task=task, **overrides)


def _mk_te(tmp_path, orch=None, manager=None) -> ToolExecutor:
    cfg = WispConfig()
    cfg = cfg.replace(workspace=str(tmp_path), auto_approve=True)
    return ToolExecutor(
        config=cfg,
        hook_manager=MagicMock(),
        subagent_orchestrator=orch if orch is not None else FakeOrchestrator(),
        background_agents=manager,
    )


# ── Manager lifecycle ─────────────────────────────────────────────────────


class TestBackgroundAgentManagerLaunch:
    @pytest.mark.asyncio
    async def test_launch_returns_immediately_running(self):
        orch = FakeOrchestrator(delay=1.0)
        mgr = BackgroundAgentManager(orch)
        snap = await mgr.launch(_contract())
        assert snap["ok"] is True
        assert snap["agent_id"].startswith("bg-")
        assert snap["status"] == STATUS_RUNNING

        entry = mgr.get(snap["agent_id"])
        assert entry is not None and entry.status == STATUS_RUNNING
        mgr.cancel(snap["agent_id"])

    @pytest.mark.asyncio
    async def test_wait_collects_completed_result(self):
        orch = FakeOrchestrator(delay=0.01, success=True, session_id="sess-x")
        mgr = BackgroundAgentManager(orch)
        launch = await mgr.launch(_contract(task="review diff"))
        snap = await mgr.result(launch["agent_id"], wait_seconds=2.0)

        assert snap["status"] == STATUS_COMPLETED
        assert snap["result"]["ok"] is True
        assert "did the thing" in snap["result"]["summary"]
        assert snap["result"]["files"] == ["a.py"]
        assert snap["result"]["session_id"] == "sess-x"

    @pytest.mark.asyncio
    async def test_failed_run_reports_error(self):
        orch = FakeOrchestrator(delay=0.0, success=False)
        mgr = BackgroundAgentManager(orch)
        launch = await mgr.launch(_contract())
        snap = await mgr.result(launch["agent_id"], wait_seconds=2.0)

        assert snap["status"] == STATUS_FAILED
        assert snap["result"]["ok"] is False
        assert snap["result"]["error"] == "mock failure"

    @pytest.mark.asyncio
    async def test_result_unknown_agent(self):
        mgr = BackgroundAgentManager(FakeOrchestrator())
        snap = await mgr.result("bg-nope")
        assert snap["ok"] is False
        assert "Unknown agent_id" in snap["error"]

    @pytest.mark.asyncio
    async def test_list_filters_finished(self):
        orch = FakeOrchestrator(delay=0.0)
        mgr = BackgroundAgentManager(orch)
        launch = await mgr.launch(_contract())
        await mgr.result(launch["agent_id"], wait_seconds=2.0)

        everything = mgr.list(include_finished=True)
        only_running = mgr.list(include_finished=False)
        assert len(everything) == 1
        assert only_running == []


class TestBackgroundAgentManagerLimits:
    @pytest.mark.asyncio
    async def test_capacity_limit_rejects_extra_launches(self):
        orch = FakeOrchestrator(delay=1.0)
        mgr = BackgroundAgentManager(orch, max_running=1)
        first = await mgr.launch(_contract())
        second = await mgr.launch(_contract())

        assert second["ok"] is False
        assert "limit reached" in second["error"]
        mgr.cancel(first["agent_id"])

    def test_prune_drops_oldest_finished(self):
        mgr = BackgroundAgentManager(FakeOrchestrator(), max_finished=2)
        entries = []
        for i in range(4):
            from wisp.multi_agent.background import BackgroundAgentEntry
            entry = BackgroundAgentEntry(id=f"bg-{i}", label=f"e{i}", contract=_contract())
            entry.status = STATUS_COMPLETED
            entry.finished_at = float(i)
            mgr._entries[entry.id] = entry
            entries.append(entry)

        pruned = mgr.prune()
        assert pruned == 2
        remaining_ids = set(mgr._entries)
        assert remaining_ids == {"bg-2", "bg-3"}

    @pytest.mark.asyncio
    async def test_cancel_running_then_status_terminal(self):
        orch = FakeOrchestrator(delay=5.0)
        mgr = BackgroundAgentManager(orch)
        launch = await mgr.launch(_contract())
        out = mgr.cancel(launch["agent_id"])
        assert out["ok"] is True
        # Let the cancelled task settle.
        await asyncio.sleep(0.05)
        entry = mgr.get(launch["agent_id"])
        assert entry.status == STATUS_CANCELLED
        again = mgr.cancel(launch["agent_id"])
        assert again["ok"] is False


class TestBackgroundAgentSend:
    @pytest.mark.asyncio
    async def test_send_while_running_rejected(self):
        orch = FakeOrchestrator(delay=1.0)
        mgr = BackgroundAgentManager(orch)
        launch = await mgr.launch(_contract())
        out = await mgr.send(launch["agent_id"], "follow up")
        assert out["ok"] is False
        assert "still running" in out["error"]
        mgr.cancel(launch["agent_id"])

    @pytest.mark.asyncio
    async def test_send_unknown_agent_rejected(self):
        mgr = BackgroundAgentManager(FakeOrchestrator())
        out = await mgr.send("bg-nope", "hello")
        assert out["ok"] is False

    @pytest.mark.asyncio
    async def test_send_resumes_same_agent_with_session(self):
        orch = FakeOrchestrator(delay=0.0, session_id="sess-42")
        mgr = BackgroundAgentManager(orch)
        launch = await mgr.launch(_contract(task="first pass"))
        first = await mgr.result(launch["agent_id"], wait_seconds=2.0)
        assert first["status"] == STATUS_COMPLETED
        assert first["turns"] == 1

        sent = await mgr.send(launch["agent_id"], "now fix what you found")
        assert sent["ok"] is True
        assert sent["agent_id"] == launch["agent_id"]

        second = await mgr.result(launch["agent_id"], wait_seconds=2.0)
        assert second["status"] == STATUS_COMPLETED
        assert second["turns"] == 2

        assert len(orch.contracts) == 2
        resume_contract = orch.contracts[1]
        assert getattr(resume_contract, "_resume_session_id", "") == "sess-42"
        assert resume_contract.task == "now fix what you found"
        # Depth inheritance survives continuation (unbounded recursion guard).
        assert resume_contract._subagent_depth == orch.contracts[0]._subagent_depth

    @pytest.mark.asyncio
    async def test_send_without_stored_session_rejected(self):
        class NoSessionOrch(FakeOrchestrator):
            async def _run_with_retry(self, contract):
                await super()._run_with_retry(contract)
                return SubagentResult(task_id=contract.name, success=True, output="", session_id="")

        mgr = BackgroundAgentManager(NoSessionOrch(delay=0.0))
        launch = await mgr.launch(_contract())
        await mgr.result(launch["agent_id"], wait_seconds=2.0)
        out = await mgr.send(launch["agent_id"], "go on")
        assert out["ok"] is False
        assert "No storable session" in out["error"]

    @pytest.mark.asyncio
    async def test_send_empty_message_rejected(self):
        orch = FakeOrchestrator(delay=0.0)
        mgr = BackgroundAgentManager(orch)
        launch = await mgr.launch(_contract())
        await mgr.result(launch["agent_id"], wait_seconds=2.0)
        out = await mgr.send(launch["agent_id"], "   ")
        assert out["ok"] is False


# ── Runner resume ────────────────────────────────────────────────────────


def _mk_runner(tmp_path, store) -> SubagentRunner:
    cfg = WispConfig()
    cfg = cfg.replace(model="test-model", provider="ollama", workspace=str(tmp_path))
    return SubagentRunner(cfg, Path(tmp_path), store=store)


class TestRunnerResume:
    def _seed_session(self, store: UnifiedStore, session_id: str) -> None:
        store.create_session(session_id, "test-model", "/tmp", title="[sub] old")
        store.save_session({
            "id": session_id,
            "model": "test-model",
            "workspace": "/tmp",
            "title": "[sub] old",
            "messages": [
                {"role": "user", "content": "original task"},
                {"role": "assistant", "content": "original answer"},
            ],
            "compaction_history": [],
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T00:00:00+00:00",
        })

    @pytest.mark.asyncio
    async def test_resume_loads_history_and_keeps_session_id(self, tmp_path, monkeypatch):
        store = UnifiedStore(Path(tmp_path) / "s.db")
        self._seed_session(store, "sess-r1")
        runner = _mk_runner(tmp_path, store)

        captured: dict = {}

        async def fake_run_agent(contract, config, session, system_prompt, ws, log, deadline):
            captured["session"] = dict(session)
            return {
                "success": True,
                "output": "follow-up done",
                "error": None,
                "files_changed": [],
                "iterations_used": 1,
                "messages": list(session.get("messages", [])),
            }

        monkeypatch.setattr(runner, "_run_agent", fake_run_agent)

        contract = _contract(task="continue please")
        setattr(contract, "_resume_session_id", "sess-r1")
        result = await runner.run(contract, str(tmp_path), system_prompt="", progress_callback=None)

        assert result.success is True
        assert result.session_id == "sess-r1"
        msgs = captured["session"]["messages"]
        assert [m["content"] for m in msgs] == ["original task", "original answer"]

    @pytest.mark.asyncio
    async def test_resume_missing_session_fails_cleanly(self, tmp_path):
        store = UnifiedStore(Path(tmp_path) / "s.db")
        runner = _mk_runner(tmp_path, store)
        contract = _contract()
        setattr(contract, "_resume_session_id", "sess-missing")
        result = await runner.run(contract, str(tmp_path), system_prompt="", progress_callback=None)

        assert result.success is False
        assert "RESUME FAILED" in result.output
        assert "sess-missing" in result.error

    @pytest.mark.asyncio
    async def test_fresh_run_still_creates_new_session(self, tmp_path, monkeypatch):
        store = UnifiedStore(Path(tmp_path) / "s.db")
        runner = _mk_runner(tmp_path, store)

        async def fake_run_agent(contract, config, session, system_prompt, ws, log, deadline):
            return {
                "success": True,
                "output": "done",
                "error": None,
                "files_changed": [],
                "iterations_used": 1,
                "messages": list(session.get("messages", [])),
            }

        monkeypatch.setattr(runner, "_run_agent", fake_run_agent)
        result = await runner.run(_contract(), str(tmp_path), system_prompt="", progress_callback=None)

        assert result.success is True
        assert result.session_id.startswith("sess-")
        assert result.session_id != "sess-r1"
        loaded = store.load_session(result.session_id)
        assert loaded is not None


# ── Registry wiring ───────────────────────────────────────────────────────


class TestRegistryWiring:
    def test_all_five_tools_declared(self):
        names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        for expected in ("spawn_background", "subagent_list", "subagent_result", "subagent_send", "subagent_cancel"):
            assert expected in names

    def test_impls_route_to_not_direct(self):
        for name in ("spawn_background", "subagent_list", "subagent_result", "subagent_send", "subagent_cancel"):
            assert name in TOOL_IMPLS


# ── ToolExecutor routing ──────────────────────────────────────────────────


class TestSpawnBackgroundTool:
    @pytest.mark.asyncio
    async def test_requires_task(self, tmp_path):
        te = _mk_te(tmp_path)
        data = json.loads(await te._spawn_background({}, str(tmp_path)))
        assert data["status"] == "error"
        assert "requires a 'task'" in data["data"]

    @pytest.mark.asyncio
    async def test_unknown_role(self, tmp_path):
        te = _mk_te(tmp_path)
        data = json.loads(await te._spawn_background({"task": "x", "role": "wizard"}, str(tmp_path)))
        assert data["status"] == "error"
        assert "Unknown role" in data["data"]

    @pytest.mark.asyncio
    async def test_no_orchestrator(self, tmp_path):
        cfg = WispConfig()
        cfg = cfg.replace(workspace=str(tmp_path))
        te = ToolExecutor(config=cfg)
        data = json.loads(await te._spawn_background({"task": "x"}, str(tmp_path)))
        assert data["status"] == "error"
        assert "not available" in data["data"]

    @pytest.mark.asyncio
    async def test_launch_returns_agent_id_and_runs(self, tmp_path):
        orch = FakeOrchestrator(delay=0.02)
        mgr = BackgroundAgentManager(orch)
        te = _mk_te(tmp_path, orch, manager=mgr)

        data = json.loads(await te._spawn_background(
            {"task": "write tests", "role": "tester", "label": "t-writer"}, str(tmp_path)
        ))
        assert data["status"] == "ok"
        agent_id = data["data"]["agent_id"]
        assert agent_id.startswith("bg-")

        snap = await mgr.result(agent_id, wait_seconds=2.0)
        assert snap["status"] == STATUS_COMPLETED
        # Depth inherited from executor config (parent depth 0 → child 1).
        assert orch.contracts[0]._subagent_depth == 1

    @pytest.mark.asyncio
    async def test_lazy_manager_from_orchestrator_only(self, tmp_path):
        te = _mk_te(tmp_path)  # no explicit manager
        assert te.background_agents is None
        data = json.loads(await te._subagent_list({}))
        assert data["status"] == "ok"
        assert te.background_agents is not None


class TestSubagentLifecycleTools:
    @pytest.mark.asyncio
    async def test_list_empty_and_populated(self, tmp_path):
        orch = FakeOrchestrator(delay=0.0)
        mgr = BackgroundAgentManager(orch)
        te = _mk_te(tmp_path, orch, manager=mgr)

        empty = json.loads(await te._subagent_list({}))
        assert empty["data"]["count"] == 0

        launch = json.loads(await te._spawn_background({"task": "x"}, str(tmp_path)))
        # Settle the run before testing the running-only filter.
        await mgr.result(launch["data"]["agent_id"], wait_seconds=2.0)
        populated = json.loads(await te._subagent_list({}))
        assert populated["data"]["count"] == 1

        filtered = json.loads(await te._subagent_list({"include_finished": False}))
        assert filtered["data"]["count"] == 0

    @pytest.mark.asyncio
    async def test_result_unknown_and_known(self, tmp_path):
        orch = FakeOrchestrator(delay=0.0)
        mgr = BackgroundAgentManager(orch)
        te = _mk_te(tmp_path, orch, manager=mgr)

        missing = json.loads(await te._subagent_result({"agent_id": "bg-none"}))
        assert missing["status"] == "error"

        launch = json.loads(await te._spawn_background({"task": "y"}, str(tmp_path)))
        got = json.loads(await te._subagent_result({"agent_id": launch["data"]["agent_id"], "wait_seconds": 2}))
        assert got["status"] == "ok"
        assert got["data"]["status"] == STATUS_COMPLETED

    @pytest.mark.asyncio
    async def test_send_via_executor_completes_continuation(self, tmp_path):
        orch = FakeOrchestrator(delay=0.0, session_id="sess-exec")
        mgr = BackgroundAgentManager(orch)
        te = _mk_te(tmp_path, orch, manager=mgr)

        launch = json.loads(await te._spawn_background({"task": "round one"}, str(tmp_path)))
        agent_id = launch["data"]["agent_id"]
        await mgr.result(agent_id, wait_seconds=2.0)

        sent = json.loads(await te._subagent_send({"agent_id": agent_id, "message": "round two"}))
        assert sent["status"] == "ok"

        final = await mgr.result(agent_id, wait_seconds=2.0)
        assert final["turns"] == 2
        assert getattr(orch.contracts[1], "_resume_session_id", "") == "sess-exec"

    @pytest.mark.asyncio
    async def test_send_requires_args(self, tmp_path):
        te = _mk_te(tmp_path)
        data = json.loads(await te._subagent_send({"agent_id": "bg-1"}))
        assert data["status"] == "error"
        assert "'agent_id' and 'message'" in data["data"]

    @pytest.mark.asyncio
    async def test_cancel_via_executor(self, tmp_path):
        orch = FakeOrchestrator(delay=5.0)
        mgr = BackgroundAgentManager(orch)
        te = _mk_te(tmp_path, orch, manager=mgr)

        launch = json.loads(await te._spawn_background({"task": "long job"}, str(tmp_path)))
        agent_id = launch["data"]["agent_id"]

        cancelled = json.loads(await te._subagent_cancel({"agent_id": agent_id}))
        assert cancelled["status"] == "ok"
        await asyncio.sleep(0.05)
        assert mgr.get(agent_id).status == STATUS_CANCELLED

        repeat = json.loads(await te._subagent_cancel({"agent_id": agent_id}))
        assert repeat["status"] == "error"


class TestExecuteGeneratorRouting:
    """Background tools must flow through execute()'s plain (non-streaming) path."""

    @pytest.mark.asyncio
    async def test_execute_spawn_background_yields_result_event(self, tmp_path):
        orch = FakeOrchestrator(delay=0.0)
        mgr = BackgroundAgentManager(orch)
        te = _mk_te(tmp_path, orch, manager=mgr)

        events = []
        async for ev in te.execute("spawn_background", {"task": "e2e"}, str(tmp_path)):
            events.append(ev)

        assert len(events) == 1  # no interleaved subagent lifecycle events
        assert str(events[0].type).endswith("tool_result")
        payload = json.loads(events[0].data["result"])
        agent_id = payload["data"]["agent_id"]
        snap = await mgr.result(agent_id, wait_seconds=2.0)
        assert snap["status"] == STATUS_COMPLETED

    @pytest.mark.asyncio
    async def test_execute_subagent_list_roundtrip(self, tmp_path):
        orch = FakeOrchestrator(delay=1.0)
        mgr = BackgroundAgentManager(orch)
        te = _mk_te(tmp_path, orch, manager=mgr)

        await mgr.launch(_contract())
        events = []
        async for ev in te.execute("subagent_list", {}, str(tmp_path)):
            events.append(ev)
        assert len(events) == 1
        payload = json.loads(events[0].data["result"])
        assert payload["data"]["count"] >= 1
        assert payload["data"]["agents"][0]["status"] in (STATUS_RUNNING, STATUS_COMPLETED)


# ── Notifications & counts ────────────────────────────────────────────


class TestNotificationsAndCounts:
    @pytest.mark.asyncio
    async def test_counts_by_status(self):
        orch = FakeOrchestrator(delay=0.0)
        mgr = BackgroundAgentManager(orch)
        done = await mgr.launch(_contract())
        await mgr.result(done["agent_id"], wait_seconds=2.0)
        running = await mgr.launch(_contract())

        counts = mgr.counts()
        assert counts["completed"] == 1
        assert counts["running"] == 1
        mgr.cancel(running["agent_id"])

    @pytest.mark.asyncio
    async def test_drain_surfaces_each_settlement_exactly_once(self):
        orch = FakeOrchestrator(delay=0.0, success=True)
        mgr = BackgroundAgentManager(orch)
        launch = await mgr.launch(_contract(task="audit things"))
        await mgr.result(launch["agent_id"], wait_seconds=2.0)

        first = mgr.drain_notifications()
        assert len(first) == 1
        assert "completed" in first[0]
        assert "audit things" in first[0]
        assert f"subagent_result('{launch['agent_id']}')" in first[0]

        second = mgr.drain_notifications()
        assert second == []

    @pytest.mark.asyncio
    async def test_drain_reports_failures(self):
        orch = FakeOrchestrator(delay=0.0, success=False)
        mgr = BackgroundAgentManager(orch)
        launch = await mgr.launch(_contract())
        await mgr.result(launch["agent_id"], wait_seconds=2.0)

        lines = mgr.drain_notifications()
        assert len(lines) == 1
        assert "FAILED" in lines[0]
        assert "mock failure" in lines[0]

    @pytest.mark.asyncio
    async def test_running_agents_are_not_drained(self):
        orch = FakeOrchestrator(delay=5.0)
        mgr = BackgroundAgentManager(orch)
        await mgr.launch(_contract())
        assert mgr.drain_notifications() == []
        # settle
        entries = list(mgr._entries.values())
        for e in entries:
            mgr.cancel(e.id)

    @pytest.mark.asyncio
    async def test_send_clears_notified_flag(self):
        orch = FakeOrchestrator(delay=0.0, session_id="sess-d")
        mgr = BackgroundAgentManager(orch)
        launch = await mgr.launch(_contract())
        await mgr.result(launch["agent_id"], wait_seconds=2.0)

        assert len(mgr.drain_notifications()) == 1
        await mgr.send(launch["agent_id"], "go again")
        await mgr.result(launch["agent_id"], wait_seconds=2.0)

        lines = mgr.drain_notifications()
        assert len(lines) == 1
        assert "go again" in lines[0]
