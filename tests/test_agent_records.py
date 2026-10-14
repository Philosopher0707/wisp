"""Every child leaves one durable record, linked to its parent (design S1, docs/harness/subagent-orchestration-design.md).

Before: background children were rows in `background_runs`; sync `spawn`, `orchestrate_*` and DAG
children only reached `.wisp/subagent_results.jsonl`; graph nodes had their own tables; refusals
were recorded nowhere; nothing linked a child to its parent or its session; `subagent_list` showed
background children only. Driven through the composition root and a local SSE stub.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.test_subagent_controls_reach_the_child import _contract, _wire  # noqa: F401


@pytest.fixture
def wired(tmp_path, monkeypatch):
    root, stub, ws = _wire(tmp_path, monkeypatch)
    yield root, stub, ws
    stub.server.shutdown()
    stub.server.server_close()


@pytest.fixture
def wired_full(tmp_path, monkeypatch):
    monkeypatch.setenv("WISP_TOOL_PROFILE", "full")
    root, stub, ws = _wire(tmp_path, monkeypatch, permission_mode="full")
    yield root, stub, ws
    stub.server.shutdown()
    stub.server.server_close()


def _records(root, **filters):
    return root.agent_registry.list(**filters)


class TestEveryChildIsRecorded:
    def test_a_child_run_leaves_one_succeeded_record(self, wired):
        root, _, _ = wired
        asyncio.run(root.subagent_orchestrator.run(_contract("a", "SCN{say:a}", role="researcher")))
        [rec] = _records(root)
        assert rec.status == "succeeded" and rec.name == "a" and rec.role == "researcher"
        assert rec.depth == 0 and rec.parent_agent_id == ""
        assert rec.tokens_used > 0 and rec.ended_at >= rec.started_at > 0
        assert "read_file" in rec.tools and "write_file" not in rec.tools

    def test_a_child_that_times_out_is_recorded_failed(self, wired):
        root, _, _ = wired
        asyncio.run(root.subagent_orchestrator.run(_contract("slow", "SCN{silent:6}", timeout_seconds=2)))
        [rec] = _records(root)
        assert rec.status == "failed" and "deadline" in rec.error

    @pytest.mark.parametrize("kwargs, reason", [
        ({"_subagent_depth": 2}, "depth"),
        ({"role": "wizard"}, "role"),
        ({"timeout_seconds": 0}, "contract"),
    ])
    def test_a_refusal_is_recorded_with_its_reason(self, wired, kwargs, reason):
        root, stub, _ = wired
        r = asyncio.run(root.subagent_orchestrator.run(_contract("r", "SCN{say:refused}", **kwargs)))
        assert not r.success and not stub.of("say", "refused")
        [rec] = _records(root)
        assert rec.status == "refused" and rec.refusal_reason == reason, rec

    def test_a_budget_refusal_is_recorded(self, wired):
        root, _, _ = wired
        orch = root.subagent_orchestrator
        asyncio.run(orch.run(_contract("first", "SCN{say:first}")))
        orch.set_global_token_budget(orch.get_tokens_consumed())
        asyncio.run(orch.run(_contract("second", "SCN{say:second}")))
        second = [r for r in _records(root) if r.name == "second"]
        assert [(r.status, r.refusal_reason) for r in second] == [("refused", "budget")]


class TestLineage:
    def test_a_parent_turns_child_is_linked_to_its_session(self, wired_full):
        root, _, ws = wired_full

        async def turn():
            session = await root.runtime.get_or_create_session("parent-s", "stub-model", str(ws))
            [ev async for ev in root.runtime.run_turn(session, "SCN{parent:kid}")]
            return session
        session = asyncio.run(turn())
        [child] = _records(root)
        assert child.root_session_id == session["id"]
        assert child.parent_agent_id == "" and child.origin == "spawn" and child.status == "succeeded"

    def test_grandchildren_link_to_their_parent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_TOOL_PROFILE", "full")
        root, stub, _ = _wire(tmp_path, monkeypatch, permission_mode="full", max_subagent_depth=3)
        try:
            asyncio.run(root.subagent_orchestrator.run(_contract("r0", "SCN{recurse}", timeout_seconds=30)))
        finally:
            stub.server.shutdown()
            stub.server.server_close()
        by_depth = sorted(_records(root), key=lambda r: (r.depth, r.started_at))
        assert [r.depth for r in by_depth] == [0, 1, 2], [(r.depth, r.status) for r in by_depth]
        top, mid, last = by_depth
        assert top.parent_agent_id == "" and mid.parent_agent_id == top.agent_id
        assert last.parent_agent_id == mid.agent_id

    def test_a_refusal_inside_a_child_links_to_that_child(self, wired):
        root, _, _ = wired
        from wisp.tools.context import current_agent_id

        async def inside():
            current_agent_id.set("ag-parent")
            return await root.subagent_orchestrator.run(_contract("deep", "SCN{say:deep}", _subagent_depth=2))
        asyncio.run(inside())
        [rec] = _records(root)
        assert (rec.status, rec.refusal_reason, rec.parent_agent_id) == ("refused", "depth", "ag-parent")


class TestSubagentListShowsTheTree:
    def test_a_sync_child_appears_in_subagent_list(self, wired_full):
        root, stub, ws = wired_full

        async def turn():
            session = await root.runtime.get_or_create_session("list-s", "stub-model", str(ws))
            [ev async for ev in root.runtime.run_turn(session, "SCN{parent:listed}")]
        asyncio.run(turn())
        [child] = _records(root)
        listed = json.loads(stub.of("parent", "listed")[-1]["tool_results"][-1])
        tree = listed["data"]["tree"]
        assert [n["agent_id"] for n in tree] == [child.agent_id]
        assert tree[0]["status"] == "succeeded" and tree[0]["children"] == []


class TestDurable:
    def test_records_survive_a_new_store_on_the_same_file(self, wired):
        root, _, _ = wired
        asyncio.run(root.subagent_orchestrator.run(_contract("kept", "SCN{say:kept}")))
        from wisp.infra.store import UnifiedStore
        from wisp.runs.agents import AgentRegistry

        [rec] = AgentRegistry(UnifiedStore(root.store.db_path)).list()
        assert rec.name == "kept" and rec.status == "succeeded"


class TestOrigin:
    def test_a_background_child_is_recorded_as_background_and_linked(self, wired):
        root, _, _ = wired
        from wisp.multi_agent.background import BackgroundAgentManager

        manager = BackgroundAgentManager(root.subagent_orchestrator, run_store=root.run_store)

        async def go():
            launched = await manager.launch(_contract("bg", "SCN{say:bg}"))
            await asyncio.wait_for(manager.get(launched["agent_id"]).handle, 30)
            return launched["agent_id"]
        bg_id = asyncio.run(go())
        [rec] = _records(root)
        assert rec.origin == "background" and rec.links == {"background_agent_id": bg_id}
        assert rec.status == "succeeded"

    def test_a_graph_node_is_recorded_as_graph(self, wired):
        root, _, _ = wired
        from wisp.graph.runner import SubagentNodeRunner
        from wisp.graph.types import GraphNode, NodeType

        node = GraphNode(id="analyze", type=NodeType.AGENT)
        runner = SubagentNodeRunner(root.subagent_orchestrator, workspace=str(root.config.workspace))
        asyncio.run(runner(node, {"task": "SCN{say:node}"}))
        [rec] = _records(root)
        assert rec.origin == "graph" and rec.links == {"node_id": "analyze"}

    def test_a_dag_node_is_recorded_as_dag(self, wired):
        root, _, ws = wired
        asyncio.run(root.tool_executor._orchestrate_dag(
            {"nodes": [{"name": "n1", "task": "SCN{say:dag}"}]}, str(ws)))
        [rec] = _records(root)
        assert rec.origin == "dag" and rec.links == {"node_id": "n1"} and rec.status == "succeeded"
