"""Tests for spawn and fanout tools — role-driven subagent execution."""

import asyncio
import json
from pathlib import Path
import pytest
from unittest.mock import MagicMock, AsyncMock

from wisp.config import WispConfig
from wisp.tool_executor import ToolExecutor
from wisp.multi_agent.task import SubagentResult


def _mock_orchestrator(success=True, files=None, output="done", elapsed=1.5,
                       tokens=100, task_id="spawn-generalist"):
    """Build a mock orchestrator that returns a controlled SubagentResult."""
    orch = MagicMock()
    orch.run = AsyncMock(return_value=SubagentResult(
        task_id=task_id,
        success=success,
        output=output,
        files_changed=files or [],
        elapsed_seconds=elapsed,
        tokens_used=tokens,
        error=None if success else "mock failure",
        timed_out=False,
    ))
    orch._run_with_retry = AsyncMock(return_value=SubagentResult(
        task_id=task_id,
        success=success,
        output=output,
        files_changed=files or [],
        elapsed_seconds=elapsed,
        tokens_used=tokens,
        error=None if success else "mock failure",
        timed_out=False,
    ))
    orch.run_parallel = AsyncMock(return_value=[
        SubagentResult(
            task_id=f"fanout-{i}-generalist",
            success=success,
            output=output,
            files_changed=files or [],
            elapsed_seconds=elapsed,
            tokens_used=tokens,
        )
        for i in range(2)
    ])
    return orch


def _mk_te(tmp_path, orch=None):
    """Build a ToolExecutor with mock orchestrator."""
    cfg = WispConfig()
    cfg = cfg.replace(workspace=str(tmp_path))
    cfg = cfg.replace(auto_approve=True)
    return ToolExecutor(
        config=cfg,
        hook_manager=MagicMock(),
        subagent_orchestrator=orch or _mock_orchestrator(),
    )


class TestSpawnTool:
    """Tests for the spawn tool via ToolExecutor._spawn()."""

    @pytest.mark.asyncio
    async def test_spawn_requires_task(self, tmp_path):
        te = _mk_te(tmp_path)
        result = await te._spawn({}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "error"
        assert "requires a 'task'" in data["data"]

    @pytest.mark.asyncio
    async def test_spawn_unknown_role(self, tmp_path):
        te = _mk_te(tmp_path)
        result = await te._spawn({"task": "do stuff", "role": "nonexistent"}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "error"
        assert "Unknown role" in data["data"]

    @pytest.mark.asyncio
    async def test_spawn_no_orchestrator(self, tmp_path):
        cfg = WispConfig()
        cfg = cfg.replace(workspace=str(tmp_path))
        te = ToolExecutor(config=cfg)
        result = await te._spawn({"task": "do stuff"}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "error"
        assert "not available" in data["data"]

    @pytest.mark.asyncio
    async def test_spawn_success_returns_structured_result(self, tmp_path):
        orch = _mock_orchestrator(success=True, files=["a.py"], output="All done")
        te = _mk_te(tmp_path, orch)
        result = await te._spawn({"task": "write a.py", "role": "coder"}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "ok"
        inner = data["data"]
        assert inner["ok"] is True
        assert inner["summary"] == "All done"
        assert inner["files"] == ["a.py"]
        assert inner["role"] == "coder"
        assert inner["elapsed_seconds"] == 1.5
        assert inner["error"] is None

    @pytest.mark.asyncio
    async def test_spawn_failure_returns_error(self, tmp_path):
        orch = _mock_orchestrator(success=False, output="")
        te = _mk_te(tmp_path, orch)
        result = await te._spawn({"task": "break things"}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "ok"  # tool call succeeded, subagent failed
        inner = data["data"]
        assert inner["ok"] is False
        assert inner["error"] == "mock failure"

    @pytest.mark.asyncio
    async def test_spawn_role_defaults_to_generalist(self, tmp_path):
        orch = _mock_orchestrator()
        te = _mk_te(tmp_path, orch)
        result = await te._spawn({"task": "do stuff"}, str(tmp_path))
        data = json.loads(result)
        assert data["data"]["role"] == "generalist"

    @pytest.mark.asyncio
    async def test_spawn_passes_advanced_params(self, tmp_path):
        orch = _mock_orchestrator()
        te = _mk_te(tmp_path, orch)
        await te._spawn({
            "task": "audit",
            "role": "reviewer",
            "timeout_seconds": 90,
            "max_iterations": 5,
            "tools": ["read_file", "git_diff"],
            "output_format": "json",
            "output_schema": {"type": "object"},
            "max_tokens": 2000,
            "auto_retry": False,
        }, str(tmp_path))

        # Verify contract was built correctly
        contract = orch._run_with_retry.call_args[0][0]
        assert contract.role == "reviewer"
        assert contract.timeout_seconds == 90
        assert contract.max_iterations == 5
        assert contract.tools == ["read_file", "git_diff"]
        assert contract.output_format == "json"
        assert contract.output_schema == {"type": "object"}
        assert contract.max_tokens == 2000
        assert contract.max_retries == 0  # auto_retry=False

    @pytest.mark.asyncio
    async def test_spawn_legacy_prompt_alias(self, tmp_path):
        """Legacy 'prompt' key works as alias for 'task'."""
        orch = _mock_orchestrator(success=True, output="ok")
        te = _mk_te(tmp_path, orch)
        result = await te._spawn({"prompt": "old style task"}, str(tmp_path))
        data = json.loads(result)
        assert data["data"]["ok"] is True

    @pytest.mark.asyncio
    async def test_spawn_hook_manager_at_construction(self, tmp_path):
        """hook_manager wired at construction, not runtime-patched by _spawn."""
        hm = MagicMock()
        orch = _mock_orchestrator()
        orch.hook_manager = hm  # Set at construction as CompositionRoot does
        cfg = WispConfig()
        cfg = cfg.replace(workspace=str(tmp_path))
        cfg = cfg.replace(auto_approve=True)
        te = ToolExecutor(config=cfg, hook_manager=hm, subagent_orchestrator=orch)
        await te._spawn({"task": "test"}, str(tmp_path))
        assert orch.hook_manager is hm  # Was set by constructor, not patched by _spawn


class TestFanoutTool:
    """Tests for the fanout tool via ToolExecutor._fanout()."""

    @pytest.mark.asyncio
    async def test_fanout_requires_tasks_array(self, tmp_path):
        te = _mk_te(tmp_path)
        result = await te._fanout({}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "error"
        assert "requires a 'tasks'" in data["data"]

    @pytest.mark.asyncio
    async def test_fanout_empty_tasks(self, tmp_path):
        te = _mk_te(tmp_path)
        result = await te._fanout({"tasks": []}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "error"
        assert "requires a 'tasks'" in data["data"]

    @pytest.mark.asyncio
    async def test_fanout_task_must_be_dict(self, tmp_path):
        te = _mk_te(tmp_path)
        result = await te._fanout({"tasks": ["not a dict"]}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "error"
        assert "must be an object" in data["data"]

    @pytest.mark.asyncio
    async def test_fanout_task_requires_task_field(self, tmp_path):
        te = _mk_te(tmp_path)
        result = await te._fanout({"tasks": [{"role": "coder"}]}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "error"
        assert "requires a 'task'" in data["data"]

    @pytest.mark.asyncio
    async def test_fanout_unknown_role(self, tmp_path):
        te = _mk_te(tmp_path)
        result = await te._fanout({"tasks": [{"task": "do", "role": "invalid"}]}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "error"
        assert "unknown role" in data["data"]

    @pytest.mark.asyncio
    async def test_fanout_no_orchestrator(self, tmp_path):
        cfg = WispConfig()
        cfg = cfg.replace(workspace=str(tmp_path))
        te = ToolExecutor(config=cfg)
        result = await te._fanout({"tasks": [{"task": "do"}]}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "error"
        assert "not available" in data["data"]

    @pytest.mark.asyncio
    async def test_fanout_success_returns_aggregated_result(self, tmp_path):
        orch = _mock_orchestrator(success=True, files=["a.py", "b.py"])
        te = _mk_te(tmp_path, orch)
        result = await te._fanout({
            "tasks": [
                {"task": "do A", "role": "coder"},
                {"task": "do B", "role": "tester"},
            ],
            "max_concurrent": 2,
            "mode": "blocking",
        }, str(tmp_path))

        data = json.loads(result)
        assert data["status"] == "ok"
        inner = data["data"]
        assert inner["ok"] is True
        assert len(inner["results"]) == 2
        assert inner["results"][0]["ok"] is True
        assert "2/2 subagents succeeded" in inner["summary"]
        assert inner["total_elapsed_seconds"] == 3.0  # 2 × 1.5

    @pytest.mark.asyncio
    async def test_fanout_partial_failure(self, tmp_path):
        orch = MagicMock()
        orch.run_parallel = AsyncMock(return_value=[
            SubagentResult(task_id="fanout-0-coder", success=True, output="ok",
                           elapsed_seconds=1.0, tokens_used=50),
            SubagentResult(task_id="fanout-1-tester", success=False, output="",
                           error="test failed", elapsed_seconds=2.0, tokens_used=30),
        ])
        te = _mk_te(tmp_path, orch)
        result = await te._fanout({
            "tasks": [{"task": "do A"}, {"task": "do B"}],
            "mode": "blocking",
        }, str(tmp_path))

        data = json.loads(result)
        inner = data["data"]
        assert inner["ok"] is False
        assert "1/2 subagents succeeded" in inner["summary"]
        assert inner["results"][0]["ok"] is True
        assert inner["results"][1]["ok"] is False
        assert inner["results"][1]["error"] == "test failed"

    @pytest.mark.asyncio
    async def test_fanout_passes_role_defaults_to_contracts(self, tmp_path):
        orch = MagicMock()
        orch.run_parallel = AsyncMock(return_value=[
            SubagentResult(task_id="fanout-0-coder", success=True, output="ok",
                           elapsed_seconds=1.0),
        ])
        orch.hook_manager = None
        te = _mk_te(tmp_path, orch)
        await te._fanout({
            "tasks": [
                {"task": "write feature", "role": "coder",
                 "timeout_seconds": 300, "max_iterations": 20},
            ],
            "mode": "blocking",
        }, str(tmp_path))

        contracts = orch.run_parallel.call_args[0][0]
        assert len(contracts) == 1
        c = contracts[0]
        assert c.role == "coder"
        assert c.timeout_seconds == 300
        assert c.max_iterations == 20
        from wisp.multi_agent.roles import ROLE_CONFIGS
        assert c.tools == ROLE_CONFIGS["coder"].allowed_tools


class TestSpawnLegacy:
    """Legacy tool names route through _spawn()."""

    @pytest.mark.asyncio
    async def test_legacy_name_works(self, tmp_path):
        orch = _mock_orchestrator(success=True, output="legacy works")
        te = _mk_te(tmp_path, orch)
        result = await te._spawn({"task": "old tool name"}, str(tmp_path))
        data = json.loads(result)
        assert data["data"]["ok"] is True
        assert data["data"]["summary"] == "legacy works"


# ── Orchestration patterns ────────────────────────────────────────────



class _PatternOrchestrator:
    """Records pattern calls and returns a canned SubagentResult."""

    def __init__(self, success=True, output="consensus reached"):
        self.success = success
        self.output = output
        self.calls = {}

    async def _run_with_retry(self, contract):
        return SubagentResult(task_id=contract.name, success=self.success,
                              output=self.output, session_id="sess-p")

    async def run_vote(self, task, agents, consensus_threshold=0.6, max_concurrent=4):
        self.calls["vote"] = {"task": task, "agents": agents, "threshold": consensus_threshold}
        return SubagentResult(task_id="vote", success=self.success, output=self.output)

    async def run_map_reduce(self, task, items, mapper, reducer, max_concurrent=4, retry_failed=True):
        mapped = [mapper(i) for i in items]
        self.calls["map_reduce"] = {"task": task, "items": items, "mapped": mapped,
                                    "reducer": reducer}
        return SubagentResult(task_id="map-reduce", success=self.success, output=self.output)

    async def run_chain(self, contracts, pass_context=True, max_concurrent=1, continue_on_error=False):
        self.calls["chain"] = {"contracts": contracts, "pass_context": pass_context}
        return SubagentResult(task_id="chain", success=self.success, output=self.output)


def _mk_pattern_te(tmp_path, orch=None):
    cfg = WispConfig()
    cfg = cfg.replace(workspace=str(tmp_path), auto_approve=True)
    return ToolExecutor(config=cfg, hook_manager=MagicMock(),
                        subagent_orchestrator=orch or _PatternOrchestrator())


class TestOrchestrateVote:
    @pytest.mark.asyncio
    async def test_builds_n_voters_and_passes_threshold(self, tmp_path):
        orch = _PatternOrchestrator()
        te = _mk_pattern_te(tmp_path, orch)
        data = json.loads(await te._orchestrate_vote(
            {"task": "is this code safe?", "voters": 4, "consensus_threshold": 0.75},
            str(tmp_path)))
        assert data["status"] == "ok"
        call = orch.calls["vote"]
        assert len(call["agents"]) == 4
        assert call["threshold"] == 0.75
        assert call["agents"][0].name == "vote-0"

    @pytest.mark.asyncio
    async def test_clamps_voters_to_range(self, tmp_path):
        orch = _PatternOrchestrator()
        te = _mk_pattern_te(tmp_path, orch)
        await te._orchestrate_vote({"task": "x", "voters": 99}, str(tmp_path))
        assert len(orch.calls["vote"]["agents"]) == 6

    @pytest.mark.asyncio
    async def test_requires_task(self, tmp_path):
        te = _mk_pattern_te(tmp_path)
        data = json.loads(await te._orchestrate_vote({}, str(tmp_path)))
        assert data["status"] == "error"
        assert "requires a 'task'" in data["data"]

    @pytest.mark.asyncio
    async def test_no_orchestrator(self, tmp_path):
        cfg = WispConfig()
        cfg = cfg.replace(workspace=str(tmp_path))
        te = ToolExecutor(config=cfg)
        data = json.loads(await te._orchestrate_vote({"task": "x"}, str(tmp_path)))
        assert data["status"] == "error"


class TestOrchestrateMapReduce:
    @pytest.mark.asyncio
    async def test_mapper_embeds_item(self, tmp_path):
        orch = _PatternOrchestrator()
        te = _mk_pattern_te(tmp_path, orch)
        data = json.loads(await te._orchestrate_map_reduce(
            {"task": "review file", "items": ["a.py", "b.py"]}, str(tmp_path)))
        assert data["status"] == "ok"
        call = orch.calls["map_reduce"]
        assert call["mapped"][0].task.count("a.py") >= 1
        assert "Synthesize" in call["reducer"]

    @pytest.mark.asyncio
    async def test_rejects_empty_items(self, tmp_path):
        te = _mk_pattern_te(tmp_path)
        data = json.loads(await te._orchestrate_map_reduce({"task": "x", "items": []}, str(tmp_path)))
        assert data["status"] == "error"

    @pytest.mark.asyncio
    async def test_caps_items_at_twenty(self, tmp_path):
        orch = _PatternOrchestrator()
        te = _mk_pattern_te(tmp_path, orch)
        await te._orchestrate_map_reduce({"task": "x", "items": [f"f{i}.py" for i in range(30)]},
                                         str(tmp_path))
        assert len(orch.calls["map_reduce"]["items"]) == 20


class TestOrchestrateChain:
    @pytest.mark.asyncio
    async def test_builds_sequential_contracts(self, tmp_path):
        orch = _PatternOrchestrator()
        te = _mk_pattern_te(tmp_path, orch)
        data = json.loads(await te._orchestrate_chain({
            "steps": [
                {"task": "implement", "role": "coder"},
                {"task": "review", "role": "reviewer"},
                {"task": "fix findings", "role": "coder"},
            ],
        }, str(tmp_path)))
        assert data["status"] == "ok"
        call = orch.calls["chain"]
        assert [c.name for c in call["contracts"]] == ["chain-0", "chain-1", "chain-2"]
        assert call["pass_context"] is True

    @pytest.mark.asyncio
    async def test_requires_two_steps(self, tmp_path):
        te = _mk_pattern_te(tmp_path)
        data = json.loads(await te._orchestrate_chain({"steps": [{"task": "only"}]}, str(tmp_path)))
        assert data["status"] == "error"

    @pytest.mark.asyncio
    async def test_step_missing_task(self, tmp_path):
        te = _mk_pattern_te(tmp_path)
        data = json.loads(await te._orchestrate_chain(
            {"steps": [{"task": "a"}, {"role": "coder"}]}, str(tmp_path)))
        assert data["status"] == "error"
        assert "step 1" in data["data"]

    @pytest.mark.asyncio
    async def test_registry_declares_all_three(self):
        from wisp.tools.registry import TOOL_SCHEMAS, TOOL_IMPLS
        names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        for n in ("orchestrate_vote", "orchestrate_map_reduce", "orchestrate_chain"):
            assert n in names and n in TOOL_IMPLS

class TestOrchestrateDag:
    @pytest.mark.asyncio
    async def test_builds_dag_and_runs(self, tmp_path):
        orch = _PatternOrchestrator()

        async def fake_run_dag(dag, max_parallelism=4, timeout_per_node=300.0):
            orch.calls["dag"] = {"dag": dag, "max_parallelism": max_parallelism}
            from wisp.multi_agent.dag import DAGResult
            return DAGResult(
                node_results={
                    "scaffold": SubagentResult(task_id="scaffold", success=True, output="made dirs"),
                    "impl": SubagentResult(task_id="impl", success=True, output="wrote code"),
                },
                level_order=[["scaffold"], ["impl"]],
                total_elapsed=1.23,
                success=True,
            )

        orch.run_dag = fake_run_dag
        te = _mk_pattern_te(tmp_path, orch)
        data = json.loads(await te._orchestrate_dag({
            "nodes": [
                {"name": "scaffold", "task": "scaffold project"},
                {"name": "impl", "task": "implement features", "depends_on": ["scaffold"]},
            ],
            "max_parallelism": 3,
        }, str(tmp_path)))
        assert data["status"] == "ok"
        dag = orch.calls["dag"]["dag"]
        assert set(dag.nodes) == {"scaffold", "impl"}
        assert dag.nodes["impl"].dependencies == ["scaffold"]
        assert isinstance(dag.nodes["impl"].task.task, str)  # contracts built
        assert orch.calls["dag"]["max_parallelism"] == 3
        assert data["data"]["ok"] is True
        assert data["data"]["level_order"] == [["scaffold"], ["impl"]]

    @pytest.mark.asyncio
    async def test_rejects_unknown_dependency(self, tmp_path):
        te = _mk_pattern_te(tmp_path)
        data = json.loads(await te._orchestrate_dag({
            "nodes": [{"name": "a", "task": "x", "depends_on": ["ghost"]}],
        }, str(tmp_path)))
        assert data["status"] == "error"
        assert "ghost" in data["data"]

    @pytest.mark.asyncio
    async def test_rejects_cycles(self, tmp_path):
        te = _mk_pattern_te(tmp_path)
        data = json.loads(await te._orchestrate_dag({
            "nodes": [
                {"name": "a", "task": "x", "depends_on": ["b"]},
                {"name": "b", "task": "y", "depends_on": ["a"]},
            ],
        }, str(tmp_path)))
        assert data["status"] == "error"
        assert "invalid DAG" in data["data"]

    @pytest.mark.asyncio
    async def test_rejects_duplicate_names(self, tmp_path):
        te = _mk_pattern_te(tmp_path)
        data = json.loads(await te._orchestrate_dag({
            "nodes": [{"name": "a", "task": "x"}, {"name": "a", "task": "y"}],
        }, str(tmp_path)))
        assert data["status"] == "error"
        assert "duplicate" in data["data"].lower()

    @pytest.mark.asyncio
    async def test_requires_nodes(self, tmp_path):
        te = _mk_pattern_te(tmp_path)
        for bad in ({}, {"nodes": []}, {"nodes": [{"task": "no name"}]}):
            data = json.loads(await te._orchestrate_dag(bad, str(tmp_path)))
            assert data["status"] == "error", bad


class TestCaptureSkillTool:
    @pytest.fixture(autouse=True)
    def _fresh_capture(self):
        from wisp.skill_capture import reset_capture
        reset_capture()
        yield
        reset_capture()

    @pytest.mark.asyncio
    async def test_explicit_steps_write_skill_file(self, tmp_path):
        te = _mk_pattern_te(tmp_path)
        data = json.loads(await te._capture_skill({
            "name": "Add Endpoint",
            "description": "Add an HTTP endpoint with tests",
            "steps": ["open routes.py", "add handler", "write tests"],
        }, str(tmp_path)))
        assert data["status"] == "ok"
        path = Path(data["data"]["path"])
        assert path.exists()
        assert path.parent.name == "add-endpoint"
        body = path.read_text(encoding="utf-8")
        assert "1. open routes.py" in body

    @pytest.mark.asyncio
    async def test_falls_back_to_recorded_history(self, tmp_path):
        te = _mk_pattern_te(tmp_path)
        te.skill_capture.record("list_files")
        te.skill_capture.record("run_tests")
        data = json.loads(await te._capture_skill(
            {"name": "check", "description": "sanity check"}, str(tmp_path)))
        assert data["status"] == "ok"
        body = Path(data["data"]["path"]).read_text(encoding="utf-8")
        assert "list_files" in body and "run_tests" in body

    @pytest.mark.asyncio
    async def test_no_history_and_no_steps_errors(self, tmp_path):
        te = _mk_pattern_te(tmp_path)
        data = json.loads(await te._capture_skill(
            {"name": "empty", "description": "d"}, str(tmp_path)))
        assert data["status"] == "error"
        assert "history" in data["data"]

    @pytest.mark.asyncio
    async def test_name_and_description_required(self, tmp_path):
        te = _mk_pattern_te(tmp_path)
        data = json.loads(await te._capture_skill({"name": "x"}, str(tmp_path)))
        assert data["status"] == "error"

    @pytest.mark.asyncio
    async def test_recapture_merges_into_existing_skill(self, tmp_path):
        te = _mk_pattern_te(tmp_path)
        first = json.loads(await te._capture_skill({
            "name": "flow", "description": "d",
            "steps": ["step one", "step two"],
        }, str(tmp_path)))
        second = json.loads(await te._capture_skill({
            "name": "flow", "description": "d",
            "steps": ["step one", "step two"],
        }, str(tmp_path)))
        assert first["data"]["merged"] is False
        assert second["data"]["merged"] is True
        assert "Merged" in second["data"]["note"]

    @pytest.mark.asyncio
    async def test_foreign_skill_gets_sibling_not_overwrite(self, tmp_path):
        foreign_dir = Path(tmp_path) / ".agents" / "skills" / "taken"
        foreign_dir.mkdir(parents=True)
        (foreign_dir / "SKILL.md").write_text(
            "---\nname: taken\ndescription: human\n---\nmanual steps")
        te = _mk_pattern_te(tmp_path)
        data = json.loads(await te._capture_skill(
            {"name": "taken", "description": "d", "steps": ["s"]}, str(tmp_path)))
        assert data["status"] == "ok"
        assert data["data"]["skill_name"] == "taken-2"
        assert "human" in (foreign_dir / "SKILL.md").read_text(encoding="utf-8")

    def test_registry_declares_dag_and_capture(self):
        from wisp.tools.registry import TOOL_SCHEMAS, TOOL_IMPLS
        names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        for n in ("orchestrate_dag", "capture_skill"):
            assert n in names and n in TOOL_IMPLS

class TestRunDagContractInjection:
    """Regression (live E2E catch): dep-result injection must not destroy
    the node's SubagentContract — run() dereferences contract fields and
    a bare string crashed with AttributeError on _subagent_depth."""

    def _orch(self):
        from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator
        from tests.test_subagent_orchestrator import _child_config
        agent = MagicMock()
        agent.config = _child_config({})
        agent.config.model = "test-model"
        agent.config.workspace = "/tmp"
        agent.config.show_thinking = False
        agent.config.chars_per_token = 4
        agent.config.ollama_url = "http://localhost:11434"
        agent.config.temperature = 0.2
        agent.config.max_context_tokens = 128000
        agent.config._context_tokens_explicit = True
        agent.config.permission_mode = "auto"
        agent.config.max_iterations = 30
        agent.config.subagent_pool_size = 4
        agent.config.max_subagent_depth = 2
        agent.config.max_subagent_branching = 3
        return SubagentOrchestrator(parent_agent=agent)

    async def _run_dag(self, orch):
        from unittest.mock import patch
        from wisp.multi_agent.dag import TaskDAG, TaskNode
        from wisp.multi_agent.task import SubagentContract

        received: dict = {}

        class RecordingCore:
            def __init__(self, *args, **kwargs):
                pass

            async def turn(self, session_dict, task):
                received[task] = True
                yield {"type": "content", "text": f"out::{task[:40]}"}
                yield {"type": "done"}

        dag = TaskDAG()
        dag.add_node(TaskNode(name="up",
                              task=SubagentContract(name="up", task="do upstream")))
        dag.add_node(TaskNode(name="down",
                              task=SubagentContract(name="down", task="do downstream"),
                              dependencies=["up"]))
        with patch("wisp.core.engine.WispAgentCore", RecordingCore):
            result = await orch.run_dag(dag)
        return result, received

    @pytest.mark.asyncio
    async def test_dependent_node_keeps_contract_with_injected_output(self):
        result, _ = await self._run_dag(self._orch())
        assert result.success is True, result.errors
        down_result = result.node_results["down"]
        assert down_result.success is True

    @pytest.mark.asyncio
    async def test_downstream_prompt_carries_upstream_output(self):
        orch = self._orch()
        result, received = await self._run_dag(orch)
        downstream_prompts = [t for t in received if t.startswith("do downstream")]
        assert downstream_prompts, sorted(received)
        # The upstream node's RESULT text reached the downstream prompt.
        assert "out::do upstream" in downstream_prompts[0]
        assert "do downstream" in downstream_prompts[0]

class TestStreamingHeartbeat:
    """Blocking subagent tools must show a heartbeat while children run.

    Live evidence: a 240s researcher emits only task_started/task_completed,
    so the terminal sat silent for minutes and users read it as a hang."""

    @pytest.mark.asyncio
    async def test_heartbeat_fires_during_slow_spawn(self, tmp_path, monkeypatch):
        import time as _t
        import wisp.tool_executor as te_mod

        class SlowOrch:
            async def _run_with_retry(self, contract):
                await asyncio.sleep(0.8)
                return SubagentResult(task_id=contract.name, success=True,
                                      output="done", session_id="s")

        monkeypatch.setattr(te_mod, "_HEARTBEAT_FIRST_S", 0.3)
        monkeypatch.setattr(te_mod, "_HEARTBEAT_EVERY_S", 0.3)
        te = _mk_te(tmp_path, SlowOrch())
        events = []
        async for ev in te.execute("spawn", {"task": "research"}, str(tmp_path)):
            events.append(ev)
        kinds = [getattr(e.type, "value", e.get("type") if isinstance(e, dict) else "?")
                 for e in events]
        assert "system" in kinds, kinds
        heartbeats = [e for e in events
                      if (getattr(e, "data", None) or {}).get("message", "").startswith("⏳")
                      or (isinstance(e, dict) and str(e.get("message", "")).startswith("⏳"))]
        assert heartbeats, "no heartbeat emitted"
        # Heartbeats precede the final tool_result.
        last = events[-1]
        assert str(getattr(last.type, "value", last)).endswith("tool_result") or \
               (isinstance(last, dict) and last.get("type") == "tool_result")

    @pytest.mark.asyncio
    async def test_orchestrate_vote_streams_too(self, tmp_path, monkeypatch):
        import wisp.tool_executor as te_mod

        class SlowVoteOrch:
            async def run_vote(self, *args, **kwargs):
                await asyncio.sleep(0.5)
                return SubagentResult(task_id="vote", success=True,
                                      output="consensus reached", session_id="s")

        monkeypatch.setattr(te_mod, "_HEARTBEAT_FIRST_S", 0.2)
        te = _mk_te(tmp_path, SlowVoteOrch())
        events = []
        async for ev in te.execute("orchestrate_vote",
                                   {"task": "pick one",
                                    "variants": [{"answer": "a"}, {"answer": "b"}]},
                                   str(tmp_path)):
            events.append(ev)
        assert any(isinstance(e, dict) and str(e.get("message", "")).startswith("⏳")
                   for e in events) or any(
            getattr(e.type, "value", "") == "system" for e in events), \
            [getattr(e.type, "value", type(e)) for e in events]

