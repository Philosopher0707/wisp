"""Migration P4 — the turn materialized as a task graph.

The plan requires five assertions. The fourth (`test_dag_retired_into_graph`) is
**not** here: retiring `multi_agent/dag.py` into `wisp/graph/` is P4 item 5 and is
deferred with a reason — see `PHASE_P4_REPORT.md` §7. The other four are, plus the
legality and projection properties that make them meaningful.

The load-bearing property is `test_ready_materialized`: readiness must be
**stored**, not recomputed. That is what turns the graph from a view over the
message list into persistent state, and it is testable in a way that a
recomputation cannot pass.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from wisp.core.task_graph import (
    LEGAL_NODE_TRANSITIONS,
    TERMINAL_NODE_STATUSES,
    NodeTransition,
    TaskGraph,
    TaskNode,
    apply_transition,
    build_turn_graph,
    divergences,
    is_legal_node_transition,
    materialize,
    replay_transitions,
)
from wisp.graph.types import NodeStatus, NodeType

REPO = Path(__file__).resolve().parents[1]


def _settle(graph, node_id, to_status, seq=1, reason="test"):
    node = graph.node(node_id)
    return apply_transition(graph, NodeTransition(
        run_id=graph.run_id, node_id=node_id, from_status=node.status,
        to_status=to_status, seq=seq, reason=reason))


# ── 1. A turn materializes a graph with nodes and edges ─────────────────


class TestTurnMaterializesGraph:
    def test_build_turn_graph_has_nodes_and_edges(self):
        g = build_turn_graph("r1", 3)
        assert [n.node_id for n in g.nodes] == ["turn:0", "turn:1", "turn:2"]
        assert g.edges == (("turn:0", "turn:1"), ("turn:1", "turn:2"))

    def test_every_node_is_an_agent_node(self):
        """The plan's mapping: one AGENT node per iteration."""
        g = build_turn_graph("r1", 4)
        assert all(n.kind is NodeType.AGENT for n in g.nodes)

    def test_iteration_index_matches_node_order(self):
        g = build_turn_graph("r1", 3)
        assert [n.iteration for n in g.nodes] == [0, 1, 2]

    def test_entrypoint_is_the_first_node(self):
        assert build_turn_graph("r1", 2).entrypoint == "turn:0"

    def test_deps_chain_is_real(self):
        g = build_turn_graph("r1", 3)
        assert g.node("turn:0").deps == ()
        assert g.node("turn:1").deps == ("turn:0",)
        assert g.node("turn:2").deps == ("turn:1",)

    def test_zero_iterations_is_an_empty_graph(self):
        g = build_turn_graph("r1", 0)
        assert g.nodes == () and g.edges == () and g.entrypoint == ""

    def test_graph_round_trips(self):
        g = materialize(build_turn_graph("r1", 3))
        assert TaskGraph.from_dict(g.to_dict()) == g

    def test_a_real_turn_persists_a_graph(self, tmp_path):
        """End-to-end: the graph reaches the session log."""
        from wisp.config import WispConfig
        from wisp.core.engine import WispAgentCore
        from wisp.core.runtime import AgentRuntime
        from wisp.core.session import SessionEventType
        from wisp.core.session_repo import SessionRepository
        from wisp.infra.extensions import ExtensionHost
        from wisp.infra.security import SecurityPolicy
        from wisp.infra.store import UnifiedStore
        from wisp.infra.telemetry import Telemetry

        class _P:
            def generate_stream_events(self, system_prompt, messages, tools=None):
                yield {"type": "content", "text": "hi"}
                yield {"type": "done", "done_reason": "stop"}

        ws = tmp_path / "ws"
        ws.mkdir()
        config = WispConfig().replace(workspace=str(ws), task_graph=True)
        store = UnifiedStore(tmp_path / "wisp.db")
        repo = SessionRepository(store)

        def factory():
            return WispAgentCore(config=config, provider=_P(),
                                 security=SecurityPolicy(), tool_executor=None)

        runtime = AgentRuntime(
            store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
            telemetry=Telemetry(), core_factory=factory,
            session_repo=repo, config=config)
        session = {"id": "g1", "model": "mock", "workspace": str(ws),
                   "messages": []}

        async def _main():
            return [ev async for ev in runtime.run_turn(session, prompt="go")]
        asyncio.run(_main())

        events = repo.load_events("g1")
        kinds = [str(e.event_type) for e in events]
        assert SessionEventType.TASK_GRAPH.value in kinds, kinds

        graph_event = [e for e in events
                       if e.event_type == SessionEventType.TASK_GRAPH][0]
        graph = graph_event.payload["graph"]
        assert graph["run_id"] == "g1"
        assert len(graph["nodes"]) >= 1

    def test_flag_off_records_no_graph(self, tmp_path):
        from wisp.config import WispConfig
        from wisp.core.engine import WispAgentCore
        from wisp.core.runtime import AgentRuntime
        from wisp.core.session import SessionEventType
        from wisp.core.session_repo import SessionRepository
        from wisp.infra.extensions import ExtensionHost
        from wisp.infra.security import SecurityPolicy
        from wisp.infra.store import UnifiedStore
        from wisp.infra.telemetry import Telemetry

        class _P:
            def generate_stream_events(self, system_prompt, messages, tools=None):
                yield {"type": "content", "text": "hi"}
                yield {"type": "done", "done_reason": "stop"}

        ws = tmp_path / "ws"
        ws.mkdir()
        config = WispConfig().replace(workspace=str(ws))
        store = UnifiedStore(tmp_path / "wisp.db")
        repo = SessionRepository(store)

        def factory():
            return WispAgentCore(config=config, provider=_P(),
                                 security=SecurityPolicy(), tool_executor=None)

        runtime = AgentRuntime(
            store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
            telemetry=Telemetry(), core_factory=factory,
            session_repo=repo, config=config)
        session = {"id": "g2", "model": "mock", "workspace": str(ws),
                   "messages": []}

        async def _main():
            return [ev async for ev in runtime.run_turn(session, prompt="go")]
        asyncio.run(_main())

        kinds = [str(e.event_type) for e in repo.load_events("g2")]
        assert SessionEventType.TASK_GRAPH.value not in kinds


# ── 2. READY is materialized, not recomputed ────────────────────────────


class TestReadyMaterialized:
    def test_materialize_stores_readiness(self):
        g = materialize(build_turn_graph("r1", 3))
        assert g.node("turn:0").ready is True
        assert g.node("turn:1").ready is False

    def test_ready_is_a_stored_field(self):
        """The proof that readiness is read from storage: hand-build a graph
        whose stored flag disagrees with what a derivation would produce, and
        show `ready_ids()` believes the STORED value."""
        g = TaskGraph(
            run_id="r1", entrypoint="a",
            nodes=(TaskNode("a", status=NodeStatus.PENDING, ready=False),
                   TaskNode("b", status=NodeStatus.PENDING, ready=True)),
            edges=(("a", "b"),),
        )
        # A derivation would say only "a" is ready; the stored flag says "b".
        assert g.ready_ids() == ["b"], "ready_ids() recomputed instead of reading"

    def test_divergence_is_detectable(self):
        """Materializing buys inspectability at the cost of a value that can
        go stale. This is how staleness becomes visible."""
        g = TaskGraph(
            run_id="r1", entrypoint="a",
            nodes=(TaskNode("a", status=NodeStatus.PENDING, ready=False),
                   TaskNode("b", status=NodeStatus.PENDING, ready=True)),
            edges=(("a", "b"),),
        )
        assert divergences(g) == ["a", "b"]

    def test_a_freshly_materialized_graph_has_no_divergence(self):
        g = materialize(build_turn_graph("r1", 4))
        assert divergences(g) == []

    def test_settling_a_node_advances_readiness(self):
        g = materialize(build_turn_graph("r1", 3))
        assert g.ready_ids() == ["turn:0"]
        g = _settle(g, "turn:0", NodeStatus.SUCCESS)
        assert g.ready_ids() == ["turn:1"]
        assert divergences(g) == []

    def test_readiness_is_rematerialized_by_a_transition(self):
        """A transition must not leave `ready` stale behind it."""
        g = materialize(build_turn_graph("r1", 2))
        g = _settle(g, "turn:0", NodeStatus.SUCCESS)
        assert divergences(g) == []

    def test_materialize_is_idempotent(self):
        g = materialize(build_turn_graph("r1", 3))
        assert materialize(g) == g

    def test_materialize_does_not_mutate(self):
        g = build_turn_graph("r1", 3)
        materialize(g)
        assert all(n.ready is False for n in g.nodes)

    def test_a_failed_predecessor_still_releases_the_next_node(self):
        """A settled predecessor releases downstream work regardless of
        outcome — the graph records what CAN run, not what should."""
        g = materialize(build_turn_graph("r1", 2))
        g = _settle(g, "turn:0", NodeStatus.FAILURE)
        assert g.ready_ids() == ["turn:1"]


# ── 3. One transition API ───────────────────────────────────────────────


class TestSingleTransitionApi:
    def test_apply_transition_is_the_only_status_writer(self):
        """AST: inside `core/task_graph.py`, `status` is only ever set via
        `replace(node, status=...)` inside `apply_transition`. A second write
        site would be the ~30-mutation problem the audit found, rebuilt."""
        tree = ast.parse((REPO / "wisp" / "core" / "task_graph.py")
                         .read_text(encoding="utf-8"))
        writers: list[str] = []
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "replace"
                        and any(k.arg == "status" for k in node.keywords)):
                    writers.append(fn.name)
        assert set(writers) <= {"apply_transition"}, (
            f"status is written outside apply_transition: {sorted(set(writers))}")

    def test_no_module_outside_task_graph_writes_node_status(self):
        """No other module constructs a `TaskNode` with a status, which would
        bypass the transition API."""
        offenders: list[str] = []
        for path in sorted((REPO / "wisp").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            if path.name == "task_graph.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "TaskNode"
                        and any(k.arg == "status" for k in node.keywords)):
                    offenders.append(str(path.relative_to(REPO)))
        assert not offenders, f"TaskNode built with a status outside the API: {offenders}"

    def test_illegal_transition_is_refused(self):
        g = materialize(build_turn_graph("r1", 1))
        g = _settle(g, "turn:0", NodeStatus.SUCCESS)
        with pytest.raises(ValueError, match="illegal node transition"):
            _settle(g, "turn:0", NodeStatus.RUNNING, seq=2)

    def test_stale_from_status_is_refused(self):
        g = materialize(build_turn_graph("r1", 1))
        with pytest.raises(ValueError, match="stale transition"):
            apply_transition(g, NodeTransition(
                run_id="r1", node_id="turn:0",
                from_status=NodeStatus.RUNNING, to_status=NodeStatus.SUCCESS))

    def test_unknown_node_is_refused(self):
        g = materialize(build_turn_graph("r1", 1))
        with pytest.raises(ValueError, match="unknown node"):
            apply_transition(g, NodeTransition(
                run_id="r1", node_id="turn:99",
                from_status=NodeStatus.PENDING, to_status=NodeStatus.RUNNING))

    def test_terminal_states_have_no_outgoing_edges(self):
        for st in TERMINAL_NODE_STATUSES:
            assert LEGAL_NODE_TRANSITIONS[st] == ()

    def test_every_status_appears_in_the_machine(self):
        assert set(LEGAL_NODE_TRANSITIONS) == set(NodeStatus)

    def test_legality_helper_agrees_with_the_table(self):
        for frm, allowed in LEGAL_NODE_TRANSITIONS.items():
            for to in NodeStatus:
                assert is_legal_node_transition(frm, to) == (to in allowed)

    def test_apply_transition_does_not_mutate_the_input(self):
        g = materialize(build_turn_graph("r1", 2))
        before = [n.status for n in g.nodes]
        _settle(g, "turn:0", NodeStatus.SUCCESS)
        assert [n.status for n in g.nodes] == before


# ── 4. Every transition is persisted ────────────────────────────────────


class TestTransitionPersisted:
    def test_transitions_reach_the_session_log(self, tmp_path):
        from wisp.config import WispConfig
        from wisp.core.engine import WispAgentCore
        from wisp.core.runtime import AgentRuntime
        from wisp.core.session import SessionEventType
        from wisp.core.session_repo import SessionRepository
        from wisp.infra.extensions import ExtensionHost
        from wisp.infra.security import SecurityPolicy
        from wisp.infra.store import UnifiedStore
        from wisp.infra.telemetry import Telemetry

        class _P:
            def generate_stream_events(self, system_prompt, messages, tools=None):
                yield {"type": "content", "text": "hi"}
                yield {"type": "done", "done_reason": "stop"}

        ws = tmp_path / "ws"
        ws.mkdir()
        config = WispConfig().replace(workspace=str(ws), task_graph=True)
        store = UnifiedStore(tmp_path / "wisp.db")
        repo = SessionRepository(store)

        def factory():
            return WispAgentCore(config=config, provider=_P(),
                                 security=SecurityPolicy(), tool_executor=None)

        runtime = AgentRuntime(
            store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
            telemetry=Telemetry(), core_factory=factory,
            session_repo=repo, config=config)
        session = {"id": "t1", "model": "mock", "workspace": str(ws),
                   "messages": []}

        async def _main():
            return [ev async for ev in runtime.run_turn(session, prompt="go")]
        asyncio.run(_main())

        kinds = [str(e.event_type) for e in repo.load_events("t1")]
        assert SessionEventType.NODE_TRANSITION.value in kinds, kinds

    def test_every_transition_event_has_a_durable_row(self, tmp_path):
        from wisp.config import WispConfig
        from wisp.core.engine import WispAgentCore
        from wisp.core.runtime import AgentRuntime
        from wisp.core.session import SessionEventType
        from wisp.core.session_repo import SessionRepository
        from wisp.infra.extensions import ExtensionHost
        from wisp.infra.security import SecurityPolicy
        from wisp.infra.store import UnifiedStore
        from wisp.infra.telemetry import Telemetry

        class _P:
            def generate_stream_events(self, system_prompt, messages, tools=None):
                yield {"type": "content", "text": "hi"}
                yield {"type": "done", "done_reason": "stop"}

        ws = tmp_path / "ws"
        ws.mkdir()
        config = WispConfig().replace(workspace=str(ws), task_graph=True)
        store = UnifiedStore(tmp_path / "wisp.db")
        repo = SessionRepository(store)

        def factory():
            return WispAgentCore(config=config, provider=_P(),
                                 security=SecurityPolicy(), tool_executor=None)

        runtime = AgentRuntime(
            store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
            telemetry=Telemetry(), core_factory=factory,
            session_repo=repo, config=config)
        session = {"id": "t2", "model": "mock", "workspace": str(ws),
                   "messages": []}

        async def _main():
            return [ev async for ev in runtime.run_turn(session, prompt="go")]
        asyncio.run(_main())

        rows = store._get_conn().execute(
            "SELECT COUNT(*) AS n FROM session_events "
            "WHERE session_id = ? AND event_type = ?",
            ("t2", "node_transition")).fetchone()["n"]
        assert rows >= 1

    def test_sequence_numbers_stay_gapless(self, tmp_path):
        from wisp.config import WispConfig
        from wisp.core.engine import WispAgentCore
        from wisp.core.runtime import AgentRuntime
        from wisp.core.session_repo import SessionRepository
        from wisp.infra.extensions import ExtensionHost
        from wisp.infra.security import SecurityPolicy
        from wisp.infra.store import UnifiedStore
        from wisp.infra.telemetry import Telemetry

        class _P:
            def generate_stream_events(self, system_prompt, messages, tools=None):
                yield {"type": "content", "text": "hi"}
                yield {"type": "done", "done_reason": "stop"}

        ws = tmp_path / "ws"
        ws.mkdir()
        config = WispConfig().replace(workspace=str(ws), task_graph=True)
        store = UnifiedStore(tmp_path / "wisp.db")
        repo = SessionRepository(store)

        def factory():
            return WispAgentCore(config=config, provider=_P(),
                                 security=SecurityPolicy(), tool_executor=None)

        runtime = AgentRuntime(
            store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
            telemetry=Telemetry(), core_factory=factory,
            session_repo=repo, config=config)
        session = {"id": "t3", "model": "mock", "workspace": str(ws),
                   "messages": []}

        async def _main():
            return [ev async for ev in runtime.run_turn(session, prompt="go")]
        asyncio.run(_main())

        seqs = [e.sequence_num for e in repo.load_events("t3")]
        assert seqs == list(range(len(seqs))), seqs


# ── The projection property ─────────────────────────────────────────────


class TestGraphIsAProjection:
    def test_replay_rebuilds_the_graph(self):
        g = materialize(build_turn_graph("r1", 3))
        live = _settle(g, "turn:0", NodeStatus.SUCCESS, seq=1)
        live = _settle(live, "turn:1", NodeStatus.FAILURE, seq=2)

        rebuilt = replay_transitions(build_turn_graph("r1", 3), [
            NodeTransition("r1", "turn:0", NodeStatus.PENDING,
                           NodeStatus.SUCCESS, seq=1),
            NodeTransition("r1", "turn:1", NodeStatus.PENDING,
                           NodeStatus.FAILURE, seq=2),
        ])
        assert rebuilt == live

    def test_replay_is_order_independent_of_input_order(self):
        ts = [
            NodeTransition("r1", "turn:0", NodeStatus.PENDING,
                           NodeStatus.SUCCESS, seq=1),
            NodeTransition("r1", "turn:1", NodeStatus.PENDING,
                           NodeStatus.SUCCESS, seq=2),
        ]
        a = replay_transitions(build_turn_graph("r1", 2), ts)
        b = replay_transitions(build_turn_graph("r1", 2), list(reversed(ts)))
        assert a == b

    def test_session_rebuilds_its_graph_from_the_log(self):
        from wisp.core.session import Session, SessionEvent

        g = materialize(build_turn_graph("s1", 2))
        s = Session(session_id="s1")
        s.replay([
            SessionEvent.task_graph_event(1, g.to_dict()),
            SessionEvent.node_transition_event(2, NodeTransition(
                "s1", "turn:0", NodeStatus.PENDING, NodeStatus.SUCCESS,
                seq=1).to_dict()),
        ])
        assert s.unknown_events == 0
        rebuilt = s.rebuild_task_graph()
        assert rebuilt["nodes"][0]["status"] == "success"

    def test_the_graph_never_touches_the_transcript(self):
        """Audit-only, like every other migration record: the message list is
        still built from ASSISTANT_MESSAGE + TOOL_RESULT alone."""
        from wisp.core.session import Session, SessionEvent

        g = materialize(build_turn_graph("s2", 1))
        s = Session(session_id="s2")
        s.apply(SessionEvent.task_graph_event(1, g.to_dict()))
        s.apply(SessionEvent.node_transition_event(2, NodeTransition(
            "s2", "turn:0", NodeStatus.PENDING, NodeStatus.SUCCESS,
            seq=1).to_dict()))
        assert s.messages == []

    def test_the_message_list_remains_authoritative(self):
        """P4's rollback contract: with the flag off nothing is recorded, so
        the message list is untouched and remains the truth."""
        from pathlib import Path as _P
        src = (_P(__file__).resolve().parents[1] / "wisp" / "core"
               / "runtime.py").read_text(encoding="utf-8")
        assert '"task_graph", False' in src, (
            "the P4 flag must default OFF — the message list is authoritative")
