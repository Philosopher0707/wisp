"""Migration P5 — runtime graph mutation.

The phase that creates the Persistent Graph Loop property: the graph can change
*during* execution.

The plan names seven assertions. Six are here; the seventh
(`test_executor_determinism_retained`) is present in the form this phase can
honestly test — insertion-order independence of the resulting graph — because
the "executor" it refers to is Layer B's, which P5 does not modify. See
`PHASE_P5_REPORT.md` §7.

The two properties that make this safe rather than merely working:

1. **Immutability.** Every function returns a new `TaskGraph`; nothing mutates
   in place. A task is never mutated into a different task — a replan creates a
   new node and marks the old `SUPERSEDED` with a pointer. A mutated-in-place
   graph cannot be reconstructed from a log, so replay would be impossible.
2. **The growth budget is enforced, not documented.** A non-terminating
   expansion is the plan's first named risk, and a bound that is not enforced
   is a comment.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from wisp.core.task_graph import (
    GrowthBudgetExceeded,
    GraphGrowthBudget,
    NodeTransition,
    TaskGraph,
    TaskNode,
    TaskNodeState,
    apply_transition,
    build_turn_graph,
    coerce_node_state,
    create_node,
    expand,
    invalidate,
    materialize,
    supersede,
)
from wisp.graph.types import NodeStatus, NodeType

REPO = Path(__file__).resolve().parents[1]


def _graph(n: int = 3) -> TaskGraph:
    # `build_turn_graph` takes work-unit identities, not a count (M11).
    return materialize(build_turn_graph("r1", [f"call:c{i}" for i in range(n)]))


def _settle(graph, node_id, to, seq=1):
    return apply_transition(graph, NodeTransition(
        run_id=graph.run_id, node_id=node_id,
        from_status=graph.node(node_id).status, to_status=to, seq=seq))


# ── The extended vocabulary ─────────────────────────────────────────────


class TestExtendedVocabulary:
    def test_task_node_state_is_a_superset_of_node_status(self):
        """Pinned as a ratchet, as ADR-0003 recommended for the analogous
        `RunState ⊃ RunStatus` relationship. If a future phase adds a
        `NodeStatus` member, this fails loudly rather than silently making the
        two vocabularies diverge."""
        assert {s.value for s in NodeStatus} <= {s.value for s in TaskNodeState}

    def test_the_shared_values_are_identical_strings(self):
        for s in NodeStatus:
            assert TaskNodeState(s.value).value == s.value

    def test_the_seven_plan_named_states_exist(self):
        for name in ("INVALIDATED", "SUPERSEDED", "WAITING", "BLOCKED",
                     "OBSERVED", "VERIFYING", "INCONCLUSIVE"):
            assert hasattr(TaskNodeState, name), name

    def test_blocked_is_distinct_from_skipped(self):
        """The ambiguity P5 exists to resolve: SKIPPED meant both
        'predecessor failed' and 'dead branch'."""
        assert TaskNodeState.BLOCKED != TaskNodeState.SKIPPED

    def test_inconclusive_is_distinct_from_failure(self):
        assert TaskNodeState.INCONCLUSIVE != TaskNodeState.FAILURE

    def test_blocked_and_inconclusive_are_resumable(self):
        """Separating them from FAILURE is pointless if they are dead ends."""
        from wisp.core.task_graph import LEGAL_NODE_TRANSITIONS
        assert TaskNodeState.RUNNING in LEGAL_NODE_TRANSITIONS[
            TaskNodeState.BLOCKED]
        assert TaskNodeState.RUNNING in LEGAL_NODE_TRANSITIONS[
            TaskNodeState.INCONCLUSIVE]

    def test_invalidated_and_superseded_are_final(self):
        from wisp.core.task_graph import LEGAL_NODE_TRANSITIONS
        assert LEGAL_NODE_TRANSITIONS[TaskNodeState.INVALIDATED] == ()
        assert LEGAL_NODE_TRANSITIONS[TaskNodeState.SUPERSEDED] == ()

    def test_a_settled_node_can_still_be_invalidated(self):
        """A stale SUCCESS is exactly what invalidation exists to demote."""
        from wisp.core.task_graph import LEGAL_NODE_TRANSITIONS
        assert TaskNodeState.INVALIDATED in LEGAL_NODE_TRANSITIONS[
            TaskNodeState.SUCCESS]

    def test_every_state_is_in_the_machine(self):
        from wisp.core.task_graph import LEGAL_NODE_TRANSITIONS
        assert set(LEGAL_NODE_TRANSITIONS) == set(TaskNodeState)

    def test_coerce_accepts_both_vocabularies(self):
        assert coerce_node_state(NodeStatus.SUCCESS) is TaskNodeState.SUCCESS
        assert coerce_node_state("success") is TaskNodeState.SUCCESS
        assert coerce_node_state(TaskNodeState.VERIFYING) is \
            TaskNodeState.VERIFYING

    def test_coerce_refuses_an_unknown_state(self):
        with pytest.raises(ValueError, match="unknown node state"):
            coerce_node_state("not-a-state")

    def test_old_persisted_graphs_still_load(self):
        """A P4 graph stored with `NodeStatus` values must round-trip."""
        node = TaskNode.from_dict({"node_id": "n", "status": "success"})
        assert node.status is TaskNodeState.SUCCESS

    def test_the_observe_verify_leg_is_expressible(self):
        """OBSERVED → VERIFYING → INCONCLUSIVE is the leg P5 adds; it must be
        walkable end to end. `OBSERVED` is reachable only from `RUNNING` — a
        node observes an outcome because it acted, so there is no direct
        `PENDING → OBSERVED` edge."""
        g = create_node(_graph(1), TaskNode("v", NodeType.VERIFIER),
                        deps=["turn:0"])
        g = _settle(g, "v", TaskNodeState.RUNNING, 1)
        g = _settle(g, "v", TaskNodeState.OBSERVED, 2)
        g = _settle(g, "v", TaskNodeState.VERIFYING, 3)
        g = _settle(g, "v", TaskNodeState.INCONCLUSIVE, 4)
        assert g.node("v").status is TaskNodeState.INCONCLUSIVE

    def test_pending_cannot_jump_straight_to_observed(self):
        """Pinned deliberately: observing requires having acted."""
        g = create_node(_graph(1), TaskNode("v"), deps=["turn:0"])
        with pytest.raises(ValueError, match="illegal node transition"):
            _settle(g, "v", TaskNodeState.OBSERVED)


# ── 1. Runtime node insertion ───────────────────────────────────────────


class TestRuntimeNodeInsertion:
    def test_a_node_created_mid_run_joins_the_graph(self):
        g = _graph(2)
        g = create_node(g, TaskNode("extra", NodeType.FUNCTION), deps=["turn:0"])
        assert [n.node_id for n in g.nodes] == ["turn:0", "turn:1", "extra"]

    def test_the_new_node_is_not_ready_until_its_dep_settles(self):
        g = create_node(_graph(2), TaskNode("extra"), deps=["turn:0"])
        assert "extra" not in g.ready_ids()
        g = _settle(g, "turn:0", TaskNodeState.SUCCESS)
        assert "extra" in g.ready_ids()

    def test_a_dependency_free_node_is_ready(self):
        """An independent root is a legitimate request at runtime — a parallel
        task. `_compute_ready` therefore treats any node with no incoming edges
        as ready, deliberately unlike the compiled-graph rule in
        `graph/scheduler.py:41` where such a root is a dead definition."""
        g = create_node(_graph(1), TaskNode("orphan"), deps=[])
        assert "orphan" in g.ready_ids()

    def test_a_dependency_free_node_becomes_the_entrypoint_of_an_empty_graph(self):
        g = create_node(TaskGraph(run_id="r1"), TaskNode("orphan"), deps=[])
        assert g.entrypoint == "orphan"
        assert "orphan" in g.ready_ids()

    def test_a_created_node_can_then_be_transitioned(self):
        g = create_node(_graph(1), TaskNode("extra"), deps=["turn:0"])
        g = _settle(g, "turn:0", TaskNodeState.SUCCESS)
        g = _settle(g, "extra", TaskNodeState.SUCCESS, seq=2)
        assert g.node("extra").status is TaskNodeState.SUCCESS

    def test_duplicate_id_is_refused(self):
        with pytest.raises(ValueError, match="duplicate node id"):
            create_node(_graph(2), TaskNode("turn:0"))

    def test_unknown_dependency_is_refused(self):
        """Silently accepting it would produce a node that hangs forever."""
        with pytest.raises(ValueError, match="unknown dependency"):
            create_node(_graph(2), TaskNode("x"), deps=["nope"])

    def test_creating_into_an_empty_graph_sets_the_entrypoint(self):
        g = create_node(TaskGraph(run_id="r1"), TaskNode("first"))
        assert g.entrypoint == "first"

    def test_the_existing_entrypoint_is_not_overwritten(self):
        g = create_node(_graph(2), TaskNode("extra"))
        assert g.entrypoint == "turn:0"

    def test_insertion_does_not_mutate_the_input(self):
        g = _graph(2)
        before = [n.node_id for n in g.nodes]
        create_node(g, TaskNode("extra"))
        assert [n.node_id for n in g.nodes] == before


# ── 2. Expansion is acyclic ─────────────────────────────────────────────


class TestGraphExpandAcyclic:
    def test_expansion_adds_nodes_and_edges(self):
        g = expand(_graph(1),
                   [TaskNode("a"), TaskNode("b")], [("a", "b")])
        assert g.node("a") is not None and g.node("b") is not None
        assert ("a", "b") in g.edges

    def test_a_self_loop_is_refused(self):
        with pytest.raises(ValueError, match="cycle"):
            expand(_graph(1), [TaskNode("a")], [("a", "a")])

    def test_a_two_cycle_is_refused(self):
        with pytest.raises(ValueError, match="cycle"):
            expand(_graph(1), [TaskNode("a"), TaskNode("b")],
                   [("a", "b"), ("b", "a")])

    def test_a_cycle_through_an_existing_node_is_refused(self):
        """The dangerous case: expansion closes a loop through the graph that
        already exists."""
        with pytest.raises(ValueError, match="cycle"):
            expand(_graph(3), [TaskNode("loop")],
                   [("turn:2", "loop"), ("loop", "turn:0")])

    def test_the_refusal_names_the_cycle(self):
        with pytest.raises(ValueError) as exc:
            expand(_graph(1), [TaskNode("a"), TaskNode("b")],
                   [("a", "b"), ("b", "a")])
        assert "a" in str(exc.value) and "b" in str(exc.value)

    def test_an_edge_to_an_unknown_node_is_refused(self):
        with pytest.raises(ValueError, match="unknown node"):
            expand(_graph(1), [TaskNode("a")], [("a", "ghost")])

    def test_a_longer_chain_is_accepted(self):
        g = expand(_graph(1),
                   [TaskNode(f"c{i}") for i in range(4)],
                   [(f"c{i}", f"c{i + 1}") for i in range(3)])
        assert len(g.nodes) == 5

    def test_duplicate_id_in_an_expansion_is_refused(self):
        with pytest.raises(ValueError, match="duplicate node id"):
            expand(_graph(2), [TaskNode("turn:0")])

    def test_the_cycle_detector_is_deterministic(self):
        """A non-deterministic detector makes a failure unreproducible."""
        from wisp.core.task_graph import _find_cycle
        nodes = ["a", "b", "c", "d"]
        edges = [("a", "b"), ("b", "c"), ("c", "a"), ("c", "d")]
        first = _find_cycle(nodes, edges)
        assert first is not None
        assert all(_find_cycle(nodes, list(reversed(edges))) is not None
                   for _ in range(5))
        assert _find_cycle(nodes, edges) == first

    def test_an_acyclic_graph_reports_no_cycle(self):
        from wisp.core.task_graph import _find_cycle
        assert _find_cycle(["a", "b", "c"], [("a", "b"), ("b", "c")]) is None


# ── 3. Invalidation cascades ────────────────────────────────────────────


class TestInvalidationCascades:
    def test_invalidating_a_node_invalidates_its_dependents(self):
        g = _graph(3)
        for i in range(3):
            g = _settle(g, f"turn:{i}", TaskNodeState.SUCCESS, seq=i + 1)
        g = invalidate(g, "turn:0")
        assert [n.status for n in g.nodes] == [TaskNodeState.INVALIDATED] * 3

    def test_a_stale_success_is_demoted(self):
        """The property that matters: a conclusion drawn from an invalidated
        premise must not remain SUCCESS."""
        g = _settle(_graph(2), "turn:0", TaskNodeState.SUCCESS)
        g = _settle(g, "turn:1", TaskNodeState.SUCCESS, seq=2)
        g = invalidate(g, "turn:0")
        assert g.node("turn:1").status is TaskNodeState.INVALIDATED

    def test_the_cascade_is_transitive(self):
        g = expand(_graph(1), [TaskNode("a"), TaskNode("b")],
                   [("turn:0", "a"), ("a", "b")])
        g = invalidate(g, "turn:0")
        assert g.node("b").status is TaskNodeState.INVALIDATED

    def test_unrelated_nodes_are_untouched(self):
        """A node hanging off `turn:1` IS reachable from `turn:0`, so it must
        be invalidated. A genuinely unrelated node is an independent root."""
        g = _graph(2)
        g = create_node(g, TaskNode("side"), deps=["turn:1"])
        g = invalidate(g, "turn:0")
        assert g.node("side").status is TaskNodeState.INVALIDATED, \
            "the cascade is transitive and must reach turn:1's dependents"

    def test_a_node_on_a_disjoint_root_is_untouched(self):
        g = _graph(1)
        g = create_node(g, TaskNode("root2"), deps=[])   # independent root
        g = create_node(g, TaskNode("leaf2"), deps=["root2"])
        g = invalidate(g, "root2")
        assert g.node("turn:0").status is not TaskNodeState.INVALIDATED
        assert g.node("leaf2").status is TaskNodeState.INVALIDATED

    def test_invalidating_a_leaf_affects_only_itself(self):
        g = _settle(_graph(3), "turn:0", TaskNodeState.SUCCESS)
        g = invalidate(g, "turn:2")
        assert g.node("turn:0").status is TaskNodeState.SUCCESS
        assert g.node("turn:2").status is TaskNodeState.INVALIDATED

    def test_an_unknown_node_is_refused(self):
        with pytest.raises(ValueError, match="unknown node"):
            invalidate(_graph(2), "ghost")

    def test_the_reason_is_recorded(self):
        g = invalidate(_graph(1), "turn:0", reason="premise changed")
        assert g.node("turn:0").detail == "premise changed"

    def test_invalidated_nodes_are_not_ready(self):
        g = invalidate(_graph(2), "turn:0")
        assert g.node("turn:0").ready is False

    def test_invalidation_does_not_mutate_the_input(self):
        g = _settle(_graph(2), "turn:0", TaskNodeState.SUCCESS)
        invalidate(g, "turn:0")
        assert g.node("turn:0").status is TaskNodeState.SUCCESS

    def test_already_invalidated_nodes_are_left_alone(self):
        g = invalidate(_graph(1), "turn:0")
        again = invalidate(g, "turn:0")
        assert again.node("turn:0").status is TaskNodeState.INVALIDATED


# ── 4. Supersession preserves history ───────────────────────────────────


class TestSupersessionPreservesHistory:
    def test_the_old_node_is_retained(self):
        g = supersede(_graph(2), "turn:1", TaskNode("turn:1-replan"))
        assert g.node("turn:1") is not None, "history was deleted"

    def test_the_old_node_is_marked_superseded(self):
        g = supersede(_graph(2), "turn:1", TaskNode("turn:1-replan"))
        assert g.node("turn:1").status is TaskNodeState.SUPERSEDED

    def test_the_pointer_is_written(self):
        g = supersede(_graph(2), "turn:1", TaskNode("turn:1-replan"))
        assert g.node("turn:1").superseded_by == "turn:1-replan"

    def test_the_replacement_inherits_the_dependencies(self):
        g = supersede(_graph(3), "turn:1", TaskNode("turn:1-replan"))
        assert g.node("turn:1-replan").deps == ("turn:0",)

    def test_dependents_are_rewired_to_the_replacement(self):
        g = supersede(_graph(3), "turn:1", TaskNode("turn:1-replan"))
        assert ("turn:1-replan", "turn:2") in g.edges
        assert ("turn:1", "turn:2") not in g.edges

    def test_the_superseded_node_is_no_longer_ready(self):
        g = supersede(_graph(2), "turn:1", TaskNode("turn:1-replan"))
        assert g.node("turn:1").ready is False

    def test_a_task_is_never_mutated_into_a_different_task(self):
        """P5 item 4. The old node's IDENTITY survives; only its status and
        the pointer change."""
        before = _graph(2).node("turn:1")
        g = supersede(_graph(2), "turn:1", TaskNode("turn:1-replan"))
        after = g.node("turn:1")
        assert after.node_id == before.node_id
        assert after.kind is before.kind
        assert after.iteration == before.iteration

    def test_supersession_that_would_cycle_is_refused(self):
        """Superseding a node with one that already depends on it would close
        a loop: the replacement would inherit the old node's incoming edges
        while the old node's dependents moved to the replacement."""
        g = _graph(3)
        # turn:2 depends (transitively) on turn:0. Replacing turn:0 with a node
        # that inherits turn:0's incoming edges AND takes over its dependents
        # reconnects turn:2's predecessor chain to turn:2's own successor.
        with pytest.raises(ValueError):
            supersede(g, "turn:1", TaskNode("turn:2"))

    def test_the_refusal_names_the_cycle(self):
        with pytest.raises(ValueError) as exc:
            supersede(_graph(3), "turn:1", TaskNode("turn:2"))
        assert "cycle" in str(exc.value) or "duplicate" in str(exc.value)

    def test_an_unknown_node_is_refused(self):
        with pytest.raises(ValueError, match="unknown node"):
            supersede(_graph(2), "ghost", TaskNode("x"))

    def test_a_duplicate_replacement_id_is_refused(self):
        with pytest.raises(ValueError, match="duplicate node id"):
            supersede(_graph(2), "turn:1", TaskNode("turn:0"))

    def test_supersession_does_not_mutate_the_input(self):
        g = _graph(2)
        supersede(g, "turn:1", TaskNode("turn:1-replan"))
        assert g.node("turn:1").status is not TaskNodeState.SUPERSEDED


# ── 5. The growth budget ────────────────────────────────────────────────


class TestExpansionBudget:
    def test_creation_beyond_the_node_budget_is_refused(self):
        g = _graph(2)
        with pytest.raises(GrowthBudgetExceeded):
            create_node(g, TaskNode("extra"), budget=GraphGrowthBudget(max_nodes=2))

    def test_expansion_beyond_the_node_budget_is_refused(self):
        with pytest.raises(GrowthBudgetExceeded):
            expand(_graph(2), [TaskNode("a"), TaskNode("b")],
                   budget=GraphGrowthBudget(max_nodes=3))

    def test_expansion_beyond_the_edge_budget_is_refused(self):
        with pytest.raises(GrowthBudgetExceeded):
            expand(_graph(1), [TaskNode("a"), TaskNode("b")], [("a", "b")],
                   budget=GraphGrowthBudget(max_nodes=99, max_edges=0))

    def test_supersession_counts_against_the_budget(self):
        with pytest.raises(GrowthBudgetExceeded):
            supersede(_graph(2), "turn:1", TaskNode("x"),
                      budget=GraphGrowthBudget(max_nodes=2))

    def test_exactly_at_the_budget_is_allowed(self):
        g = create_node(_graph(1), TaskNode("extra"),
                        budget=GraphGrowthBudget(max_nodes=2))
        assert len(g.nodes) == 2

    def test_the_budget_is_enforced_not_documented(self):
        """A bound that is not enforced is a comment. This drives the exact
        failure mode the plan names as P5's first risk — non-terminating
        expansion — and shows it terminates."""
        g = _graph(1)
        budget = GraphGrowthBudget(max_nodes=5)
        with pytest.raises(GrowthBudgetExceeded):
            for i in range(100):
                g = create_node(g, TaskNode(f"grow{i}"), budget=budget)
        assert len(g.nodes) == 5

    def test_the_refusal_names_the_numbers(self):
        with pytest.raises(GrowthBudgetExceeded) as exc:
            create_node(_graph(2), TaskNode("x"),
                        budget=GraphGrowthBudget(max_nodes=2))
        assert "2" in str(exc.value) and "1" in str(exc.value)


# ── 6. Determinism across insertion orderings ───────────────────────────


class TestInsertionDeterminism:
    """The plan's `test_executor_determinism_retained`, in the form this phase
    can honestly test: the resulting graph must not depend on the order in
    which independent insertions happened."""

    def test_independent_insertions_are_order_independent(self):
        a = _graph(1)
        for nid in ("x", "y", "z"):
            a = create_node(a, TaskNode(nid))
        b = _graph(1)
        for nid in ("z", "y", "x"):
            b = create_node(b, TaskNode(nid))
        assert sorted(n.node_id for n in a.nodes) == \
               sorted(n.node_id for n in b.nodes)
        assert sorted(a.edges) == sorted(b.edges)

    def test_ready_ids_are_sorted(self):
        """The scheduler's determinism came from sorted selection
        (`scheduler.py:33`); materialized readiness must preserve it."""
        g = _graph(1)
        for nid in ("z", "y", "x"):
            g = create_node(g, TaskNode(nid))
        assert g.ready_ids() == sorted(g.ready_ids())

    def test_the_cycle_detector_is_order_independent(self):
        from wisp.core.task_graph import _find_cycle
        nodes = ["a", "b", "c"]
        edges = [("a", "b"), ("b", "c")]
        assert _find_cycle(nodes, edges) is None
        assert _find_cycle(list(reversed(nodes)), list(reversed(edges))) is None

    def test_a_replayed_mutation_sequence_reproduces_the_graph(self):
        """Every mutation is a pure function, so replaying the same sequence
        reproduces the same graph — which is what makes divergence detectable."""
        def _run():
            g = _graph(2)
            g = create_node(g, TaskNode("extra"), deps=["turn:0"])
            g = _settle(g, "turn:0", TaskNodeState.SUCCESS)
            g = invalidate(g, "turn:0")
            return g
        assert _run() == _run()


# ── 7. No topology mutation outside the controller ──────────────────────


class TestNoTopologyMutationOutsideController:
    def test_no_module_constructs_a_task_graph_directly(self):
        """AST: only `core/task_graph.py` may build a `TaskGraph` from parts.
        Anywhere else, topology changes must go through `create_node` /
        `expand` / `invalidate` / `supersede`, which validate and bound."""
        offenders: list[str] = []
        for path in sorted((REPO / "wisp").rglob("*.py")):
            if "__pycache__" in path.parts or path.name == "task_graph.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "TaskGraph"
                        and node.args):
                    offenders.append(str(path.relative_to(REPO)))
        assert not offenders, (
            f"TaskGraph built from parts outside the controller: {sorted(set(offenders))}")

    def test_the_mutation_api_is_the_only_edge_writer(self):
        """AST: inside `task_graph.py`, `edges=` is only ever set inside the
        four mutation functions plus construction/materialization."""
        tree = ast.parse((REPO / "wisp" / "core" / "task_graph.py")
                         .read_text(encoding="utf-8"))
        allowed = {"build_turn_graph", "materialize", "create_node", "expand",
                   "supersede", "apply_transition", "with_node", "to_dict",
                   "from_dict", "node", "ready_ids", "rebuild_task_graph"}
        writers: set[str] = set()
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "replace"
                        and any(k.arg == "edges" for k in node.keywords)):
                    writers.add(fn.name)
        assert writers <= allowed, f"unexpected edge writers: {sorted(writers - allowed)}"

    def test_the_mutation_functions_are_reachable_from_the_package(self):
        """RULE 11: a controller nothing calls is the ninth unwired subsystem."""
        import wisp.core.task_graph as tg
        for name in ("create_node", "expand", "invalidate", "supersede",
                     "GraphGrowthBudget", "GrowthBudgetExceeded"):
            assert hasattr(tg, name), name
