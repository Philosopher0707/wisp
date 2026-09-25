"""M8 — `multi_agent/dag.py` vs `wisp/graph/`: what is equivalent, what diverges, what is wrong.

M8's premise (CONTEXT.md §12) is that `multi_agent/dag.py` is *"a duplicate of `wisp/graph/`'s
executor — a strictly weaker one"* and should be retired. This file drives that premise instead
of assuming it, and pins the three things a retirement must not lose:

1. **What the two agree on.** Cycle detection and unknown-dependency detection: for the same
   DAG both must reach the same validity verdict. This is the property a re-point may rely on,
   and the test that fails if either implementation drifts.
2. **What diverges, measured — a RECORDED DIVERGENCE, not a bug.** `validate_graph` is stricter
   in two places, and the stricter rule is a *different definition of a valid DAG*:
   `wisp/graph/` requires a non-empty graph and every node reachable from the entrypoint;
   `TaskDAG` is a general partial order and permits disconnected components. So a naive re-point
   would **reject legitimate `orchestrate_dag` inputs**. That is why M8's removal is blocked on a
   decision, and this test is the record of why.
3. **A defect in `dag.py`, pinned.** An unknown dependency is reported as a *cycle*
   (`Node 'a' depends on unknown 'nope'` **and** `Cycle detected involving: a`) — the Kahn
   in-degree count is short by the unknown dep, so the node never reaches degree 0. The graph's
   validator reports it correctly (`edge nope->a: unknown source`).

Non-vacuity: each class was checked by breaking it — a removed cycle check, a changed
reachability rule, and a "fixed" unknown-dep message — and confirming the corresponding test fails.
"""
from __future__ import annotations

import ast
import pathlib

from wisp.graph.compat import dag_to_graph
from wisp.graph.validator import validate_graph
from wisp.multi_agent.dag import TaskDAG, TaskNode

REPO = pathlib.Path(__file__).resolve().parents[2]

#: Well-formed and connected — both implementations must accept these.
AGREED_VALID = {
    "single": [{"name": "a", "task": "t", "depends_on": []}],
    "chain": [
        {"name": "a", "task": "t", "depends_on": []},
        {"name": "b", "task": "t", "depends_on": ["a"]},
        {"name": "c", "task": "t", "depends_on": ["b"]},
    ],
    "diamond": [
        {"name": "a", "task": "t", "depends_on": []},
        {"name": "b", "task": "t", "depends_on": ["a"]},
        {"name": "c", "task": "t", "depends_on": ["a"]},
        {"name": "d", "task": "t", "depends_on": ["b", "c"]},
    ],
}

#: Both must reject these, for their own reasons.
AGREED_INVALID = {
    "cycle2": [
        {"name": "a", "task": "t", "depends_on": ["b"]},
        {"name": "b", "task": "t", "depends_on": ["a"]},
    ],
    "self_cycle": [{"name": "a", "task": "t", "depends_on": ["a"]}],
    "cycle3": [
        {"name": "a", "task": "t", "depends_on": ["c"]},
        {"name": "b", "task": "t", "depends_on": ["a"]},
        {"name": "c", "task": "t", "depends_on": ["b"]},
    ],
    "unknown_dep": [{"name": "a", "task": "t", "depends_on": ["nope"]}],
}


def _build(spec: list[dict]) -> TaskDAG:
    dag = TaskDAG()
    for n in spec:
        dag.add_node(TaskNode(name=n["name"], task=n["task"],
                              dependencies=list(n["depends_on"])))
    return dag


def _dag_errors(spec: list[dict]) -> list[str]:
    return _build(spec).validate()


def _graph_errors(spec: list[dict]) -> list[str]:
    return validate_graph(dag_to_graph("probe", spec))


# ── 1. What the two agree on — the property a re-point may rely on ──────────


class TestTheTwoValidatorsAgreeOnWhatTheyShare:
    """Cycle detection and unknown-dependency detection must not drift."""

    def test_wellformed_connected_dags_are_accepted_by_both(self):
        for name, spec in AGREED_VALID.items():
            assert _dag_errors(spec) == [], f"TaskDAG rejected {name}"
            assert _graph_errors(spec) == [], f"validate_graph rejected {name}"

    def test_cycles_are_rejected_by_both(self):
        for name in ("cycle2", "self_cycle", "cycle3"):
            assert _dag_errors(AGREED_INVALID[name]), f"TaskDAG accepted {name}"
            assert _graph_errors(AGREED_INVALID[name]), f"validate_graph accepted {name}"

    def test_unknown_dependencies_are_rejected_by_both(self):
        spec = AGREED_INVALID["unknown_dep"]
        assert _dag_errors(spec), "TaskDAG accepted an unknown dependency"
        assert _graph_errors(spec), "validate_graph accepted an unknown dependency"


# ── 2. What diverges, measured — the reason M8's removal is a decision ──────


class TestTheMeasuredDivergences:
    """The two implementations disagree on **what a valid DAG is**.

    This is the load-bearing finding for M8's scope. A naive re-point onto `validate_graph`
    would reject inputs `orchestrate_dag` accepts today — a behaviour change to a live,
    model-callable tool. If these assertions start failing, the definitions have converged
    and the removal is unblocked.
    """

    def test_a_disconnected_dag_is_valid_for_taskdag_and_invalid_for_the_graph(self):
        """`TaskDAG` is a general partial order; `wisp/graph` requires a single reachable
        entrypoint. Two independent chains are legitimate DAG input."""
        spec = [
            {"name": "a", "task": "t", "depends_on": []},
            {"name": "b", "task": "t", "depends_on": []},
        ]
        assert _dag_errors(spec) == [], "TaskDAG now rejects a disconnected DAG"
        graph_errs = _graph_errors(spec)
        assert graph_errs, "validate_graph now accepts a disconnected DAG — re-check M8's scope"
        assert any("unreachable" in e for e in graph_errs), graph_errs

    def test_an_empty_dag_is_valid_for_taskdag_and_invalid_for_the_graph(self):
        assert _dag_errors([]) == [], "TaskDAG now rejects an empty DAG"
        graph_errs = _graph_errors([])
        assert graph_errs, "validate_graph now accepts an empty graph"
        assert any("no nodes" in e for e in graph_errs), graph_errs


# ── 3. A defect in dag.py, pinned rather than repaired ─────────────────────


class TestTheUnknownDependencyIsMisReportedAsACycle:
    """DEFECT-PIN — `dag.py` reports an unknown dependency as a cycle.

    `validate()` computes `in_degree = len(dependencies_of(name))` from the *reverse* edge
    map. An unknown dep never gets a node, so it contributes to the count but can never be
    dequeued, so the node is left with degree > 0 and is reported as part of a cycle. The
    graph's validator reports the real cause.

    Pinned, not repaired: repairing it changes `orchestrate_dag`'s error text on a live path,
    and M8's scope is the retirement, not a bug fix inside the module being retired.
    """

    def test_dag_py_reports_a_cycle_for_an_unknown_dependency(self):
        errors = _dag_errors(AGREED_INVALID["unknown_dep"])
        assert any("unknown" in e for e in errors), errors
        assert any("Cycle detected" in e for e in errors), (
            "dag.py no longer mis-reports the unknown dependency as a cycle — "
            "the defect is fixed; update this DEFECT-PIN and re-check M8's premise"
        )

    def test_the_graph_reports_the_real_cause(self):
        errors = _graph_errors(AGREED_INVALID["unknown_dep"])
        assert any("unknown source" in e for e in errors), errors
        assert not any("cycle" in e.lower() for e in errors), (
            f"the graph now also reports a cycle for an unknown dep: {errors}"
        )


# ── 4. The residual, pinned ────────────────────────────────────────────────


class TestTheResidualIsPinned:
    """TRIPWIRE — the execution path still goes through `multi_agent/dag.py`.

    M8 is **not** closed by this phase: the removal is blocked on the semantic divergence
    above. These assertions fail the moment the execution path is re-pointed, which is the
    signal that M8's residual should be closed and this file updated.
    """

    def test_the_orchestrator_still_imports_dag(self):
        src = (REPO / "wisp/multi_agent/subagent_orchestrator.py").read_text()
        assert "from .dag import DAGScheduler" in src, (
            "the orchestrator no longer uses dag.DAGScheduler — if the execution path was "
            "re-pointed onto wisp/graph, close M8's residual and update this tripwire"
        )

    def test_the_orchestrate_dag_tool_still_imports_dag(self):
        src = (REPO / "wisp/tools/orchestration.py").read_text()
        assert "from wisp.multi_agent.dag import TaskDAG, TaskNode" in src, (
            "orchestrate_dag no longer uses multi_agent.dag — see above"
        )

    def test_the_graph_lowering_has_no_production_caller(self):
        """`compat.dag_to_graph` is the intended lowering path and is **test-only**.

        Recorded, not repaired: wiring it is the change that would make M8 removable.

        **AST-based on purpose.** A bare string scan reports this module's own docstring
        (which *names* `dag_to_graph` while describing the blocker) as a caller — the guard
        was written as a string scan first and failed on exactly that. Only an import of the
        name, or a call to it, counts as a caller.
        """
        callers = []
        for path in (REPO / "wisp").rglob("*.py"):
            if path.name == "compat.py":
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and any(
                    a.name == "dag_to_graph" for a in node.names
                ):
                    callers.append(str(path.relative_to(REPO)))
                    break
                if isinstance(node, ast.Call) and (
                    (isinstance(node.func, ast.Name) and node.func.id == "dag_to_graph")
                    or (isinstance(node.func, ast.Attribute)
                        and node.func.attr == "dag_to_graph")
                ):
                    callers.append(str(path.relative_to(REPO)))
                    break
        assert callers == [], (
            f"dag_to_graph now has a production caller: {callers} — M8 may be unblocked; "
            f"re-run the equivalence probe and close the residual"
        )
