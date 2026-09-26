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
3. **A defect in `dag.py`, repaired.** An unknown dependency *was* reported as a cycle as well
   as itself: the Kahn in-degree counted the unknown dep, so the node never reached degree 0.
   The in-degree now counts only edges whose source exists, so the unknown dep is reported
   once and no false cycle follows. The verdict is unchanged — still non-empty, still
   rejected. Class 3 below pins the corrected behaviour, replacing the pin that held the old
   one, as that pin instructed.

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


def _imports_from(rel_path: str, module_suffix: str, names: set[str]) -> set[str]:
    """The subset of `names` that `rel_path` imports from a module ending in `module_suffix`.

    **AST-based on purpose** — `PHASE_LAYER_B_BOUNDARY.md`'s instrument-defect class. A bare
    string scan asserts *presence of a line*: it passes on a commented-out import, fails on an
    equivalent one split across lines or reordered, and cannot tell an import from a mention in
    a docstring. Only a real `ImportFrom` node counts here.

    Relative imports resolve by their tail, so `from .dag import X` inside `wisp.multi_agent`
    matches the suffix `dag`, and `from wisp.multi_agent.dag import X` matches `multi_agent.dag`.
    """
    tree = ast.parse((REPO / rel_path).read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        dotted = (node.module or "").lstrip(".")
        if not (dotted == module_suffix or dotted.endswith("." + module_suffix)):
            continue
        found.update(alias.name for alias in node.names)
    return found & names


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


# ── 3. A defect in dag.py, repaired — the pin now holds the corrected behaviour ─────


class TestTheUnknownDependencyIsReportedOnceAndNotAsACycle:
    """FIXED-PIN — `dag.py` reports an unknown dependency, and no longer a cycle with it.

    `validate()` computed `in_degree = len(dependencies_of(name))` from the *reverse* edge
    map. An unknown dep never gets a node, so it contributed to the count but could never be
    dequeued, leaving the node above degree 0 and reported as part of a cycle — a false
    second diagnosis of a cause already named on the line above.

    Repaired: the in-degree counts only edges whose source exists. This class replaces the
    DEFECT-PIN that held the old behaviour, which instructed exactly this update on repair.

    Class 1 pins the *verdict* (both implementations reject an unknown dep); this class pins
    the *message*, which is what changed.
    """

    def test_dag_py_reports_the_unknown_dependency_and_not_a_cycle(self):
        errors = _dag_errors(AGREED_INVALID["unknown_dep"])
        assert any("unknown" in e for e in errors), errors
        assert not any("Cycle detected" in e for e in errors), (
            "dag.py reports a cycle for an unknown dependency again — the mis-report is back"
        )

    def test_the_verdict_is_unchanged_by_the_repair(self):
        """The repair changes the message, not which graphs are valid."""
        assert _dag_errors(AGREED_INVALID["unknown_dep"]) != [], (
            "the repair made an unknown dependency acceptable — that would be a semantic change"
        )

    def test_a_real_cycle_is_still_reported_when_an_unknown_dep_is_also_present(self):
        """The case that keeps the in-degree filter honest.

        Filtering unknown deps must not filter *real* ones. Here `a` has an unknown dep and
        `b`/`c` form a genuine cycle: both must be reported.
        """
        spec = [
            {"name": "a", "task": "t", "depends_on": ["nope"]},
            {"name": "b", "task": "t", "depends_on": ["c"]},
            {"name": "c", "task": "t", "depends_on": ["b"]},
        ]
        errors = _dag_errors(spec)
        assert any("unknown" in e for e in errors), errors
        assert any("Cycle detected" in e for e in errors), (
            f"the real cycle is no longer reported once an unknown dep is present — "
            f"the in-degree filter is too broad: {errors}"
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
        """AST-based rather than a string scan (`PHASE_LAYER_B_BOUNDARY.md` R2).

        The old form was `assert "from .dag import DAGScheduler" in src` — it asserted an
        exact line, so it passed on a commented-out import and failed on an equivalent one
        reordered or wrapped. Only a real import counts here.
        """
        found = _imports_from("wisp/multi_agent/subagent_orchestrator.py", "dag",
                              {"DAGScheduler"})
        assert found == {"DAGScheduler"}, (
            f"the orchestrator no longer imports dag.DAGScheduler (found {found or 'nothing'})"
            f" — if the execution path was re-pointed onto wisp/graph, close M8's residual and"
            f" update this tripwire"
        )

    def test_the_orchestrate_dag_tool_still_imports_dag(self):
        """AST-based, for the same reason as above."""
        found = _imports_from("wisp/tools/orchestration.py", "multi_agent.dag",
                              {"TaskDAG", "TaskNode"})
        assert found == {"TaskDAG", "TaskNode"}, (
            f"orchestrate_dag no longer imports multi_agent.dag's TaskDAG/TaskNode "
            f"(found {found or 'nothing'}) — see above"
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
