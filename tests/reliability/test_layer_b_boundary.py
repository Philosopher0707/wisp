"""ADR-0060 — the Layer B boundary, pinned.

ADR-0060 decides that **Layer A is the driver and `wisp/graph/` is a record**, and that
this boundary is **permanent rather than an open item**. This file is that decision's
contract, not a tripwire on unfinished work.

Two things are asserted, and the file proves it can fail:

1. **The boundary.** The turn loop neither imports nor consults Layer B's executor, and
   the executor's callers are exactly the four the ADR names.
2. **The four non-violations.** The decision adds no authority, no transcript payload, no
   gate and no new path into `messages`.

Every structural check is **AST-based**. `CONTEXT.md` §10 instance 5 is the precedent — a
string scan over a Python tree reads docstrings as code — and `PHASE_DAG_RETIREMENT.md`
§7.1 is the same defect inside this decision's own predecessor, where a bare string scan
counted `dag.py`'s new docstring as a caller.

**The reachability helper carries a floor.** It must see four hand-verified edges before
any of its absences count. The probe this guard is derived from resolved `from X import Y`
to `X.Y`, dropped every edge, and reported **zero** importers for a module with four —
the Grep tool contradicted it, which is the only reason it was caught (F101). A check that
can pass by finding nothing has not run.
"""
from __future__ import annotations

import ast
import dataclasses
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]

#: The Layer B modules that make up an *execution* path. `wisp.graph.types` is excluded
#: deliberately: it is a shared vocabulary Layer A legitimately imports (ADR-0021).
LAYER_B_EXECUTION = frozenset({
    "wisp.graph.executor",
    "wisp.graph.scheduler",
    "wisp.graph.runner",
    "wisp.graph.cli",
})

#: ADR-0060 R3 — the executor's callers, named. A new one is a decision, not an edit.
#: `wisp.graph.runner` and `wisp.graph.api` are **inside** Layer B (`api.py` is its own
#: typed SDK); the other four are production entry points.
NAMED_CALLERS = frozenset({
    "wisp.graph.runner",   # default_executor() — the only construction site
    "wisp.graph.api",      # Layer B's own typed SDK (GraphHandle) — no importer, see below
    "wisp.graph.cli",      # the `graph` verb
    "wisp.sdk",            # execute_proposal()
    "wisp.coding",         # run_coding_template()
    "wisp.core.doctor",    # the integrity check — a diagnostic, not a driver
})

#: Hand-verified edges. If the helper cannot see these it is broken and every absence
#: below is an artifact of that defect rather than a finding.
FLOOR = (
    ("wisp.sdk", "wisp.graph.runner"),
    ("wisp.graph.runner", "wisp.graph.executor"),
    ("wisp.core.task_graph", "wisp.graph.types"),
    ("wisp.core.runtime", "wisp.core.doctor"),
)


# ── the import graph, AST-parsed ────────────────────────────────────────────

def _module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(REPO).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_imports(module: str, node: ast.ImportFrom) -> list[str]:
    """`from X import Y` binds the *module* X — that is the edge.

    Returning `X.Y` (the first version's behaviour) makes every `from wisp.graph.executor
    import GraphExecutor` invisible, because `wisp.graph.executor.GraphExecutor` is not a
    module. That defect reported zero importers for a module with four (F101).
    """
    if node.level:
        parts = module.split(".")
        pkg = parts[: len(parts) - 1]
        if node.level > 1:
            pkg = pkg[: len(pkg) - (node.level - 1)]
        prefix = ".".join(pkg)
    else:
        prefix = node.module or ""
    out = [prefix] if prefix else []
    out.extend(f"{prefix}.{a.name}" if prefix else a.name for a in node.names)
    return out


class _Collect(ast.NodeVisitor):
    def __init__(self, module: str) -> None:
        self.module = module
        self.scope = ""
        self.edges: set[str] = set()

    def _record(self, name: str) -> None:
        self.edges.add(name)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            parts = alias.name.split(".")
            self._record(".".join(parts))
            for i in range(len(parts)):
                self._record(".".join(parts[: i + 1]))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for name in _resolve_imports(self.module, node):
            self._record(name)

    def visit_FunctionDef(self, node) -> None:
        prev, self.scope = self.scope, node.name
        self.generic_visit(node)
        self.scope = prev

    visit_AsyncFunctionDef = visit_FunctionDef


def _build_graph() -> dict[str, set[str]]:
    modules: dict[str, pathlib.Path] = {}
    trees: dict[str, ast.Module] = {}
    for path in sorted((REPO / "wisp").rglob("*.py")):
        name = _module_name(path)
        modules[name] = path
        trees[name] = ast.parse(path.read_text(encoding="utf-8"))
    known = set(modules)
    graph: dict[str, set[str]] = {}
    for name, tree in trees.items():
        c = _Collect(name)
        c.visit(tree)
        graph[name] = {e for e in c.edges if e in known}
    return graph


_GRAPH = _build_graph()


def _closure(root: str) -> set[str]:
    """Every module reachable from *root* by import, at any nesting depth."""
    seen: set[str] = set()
    queue = [root]
    while queue:
        cur = queue.pop()
        if cur in seen or cur not in _GRAPH:
            continue
        seen.add(cur)
        queue.extend(_GRAPH[cur] - seen)
    return seen


def _closure_without(root: str, excluded: str) -> set[str]:
    """The closure of *root* with *excluded* treated as a leaf.

    Used for R1's precise statement: `runtime.py` may reach Layer B **only** through the
    doctor's pre-flight report, so cutting that edge must leave nothing behind.
    """
    seen: set[str] = set()
    queue = [root]
    while queue:
        cur = queue.pop()
        if cur in seen or cur not in _GRAPH:
            continue
        seen.add(cur)
        if cur == excluded:
            continue
        queue.extend(_GRAPH[cur] - seen)
    return seen


# ── the floor ───────────────────────────────────────────────────────────────

class TestTheReachabilityHelperIsNotVacuous:
    def test_it_sees_the_hand_verified_edges(self):
        """Without this, every 'does not reach' below is worthless."""
        missing = [f"{s} -> {d}" for s, d in FLOOR if d not in _GRAPH.get(s, set())]
        assert not missing, (
            f"the import graph cannot see edges that exist in the source: {missing} — "
            f"the helper is broken, not the tree"
        )

    def test_the_resolver_keeps_the_module_and_the_symbol(self):
        """The exact defect that made the first probe report zero importers (F101)."""
        node = ast.parse("from wisp.graph.executor import GraphExecutor").body[0]
        got = _resolve_imports("probe", node)
        assert "wisp.graph.executor" in got, (
            "the resolver dropped the module edge — `from X import Y` must keep X"
        )

    def test_a_synthetic_edge_is_visible(self):
        """A helper that only ever sees the real tree cannot be falsified."""
        node = ast.parse("from wisp.graph import cli").body[0]
        assert "wisp.graph" in _resolve_imports("probe", node)


# ── 1. the boundary ─────────────────────────────────────────────────────────

class TestTheTurnLoopDoesNotReachLayerB:
    """ADR-0060 R1 — Layer A is the driver."""

    def test_stateless_does_not_reach_layer_b_at_all(self):
        """`WispAgentCore.turn` is the iteration. It has no Layer B import, at any depth.

        This is the literal half of ADR-0019/ADR-0021's claim that is **still true**.
        """
        closure = _closure("wisp.core.stateless")
        hits = sorted(closure & LAYER_B_EXECUTION)
        assert not hits, (
            f"the turn engine now reaches Layer B: {hits}. That is Position B, which "
            f"ADR-0060 rejected as not expressible — supersede it, do not edit this test."
        )

    def test_runtime_reaches_layer_b_only_through_the_doctors_preflight_report(self):
        """The literal half of the claim that is **not** true as worded (F103).

        `runtime.py` does reach `wisp.graph.executor` — through `get_doctor_report()` →
        `core.doctor.last_report` → `_check_graph_integrity()`. That is a diagnostic for
        UI layers, not the turn loop. Cutting the doctor edge must leave nothing, which is
        what makes 'the turn loop does not consult Layer B' a measurement rather than a
        reading of the import list.
        """
        without = _closure_without("wisp.core.runtime", "wisp.core.doctor")
        hits = sorted(without & LAYER_B_EXECUTION)
        assert not hits, (
            f"the runtime reaches Layer B by a path other than the doctor: {hits} — "
            f"either a new diagnostic path (name it in ADR-0060 R3) or Position B"
        )
        # and the doctor path is real, so the exclusion above is not hiding an absence
        assert "wisp.core.doctor" in _GRAPH.get("wisp.core.runtime", set()), (
            "runtime no longer imports the doctor — the exclusion above is now vacuous"
        )

    def test_the_turn_loop_does_not_ask_the_graph_what_to_run(self):
        """The readiness question, asked of BOTH turn-path files.

        `test_the_graph_still_does_not_drive_execution` (`test_node_identity.py`) asks it of
        `runtime.py` alone. The boundary spans the turn engine too, so it is asked here of
        both — same property, wider subject.
        """
        for rel in ("wisp/core/runtime.py", "wisp/core/stateless.py"):
            src = (REPO / rel).read_text(encoding="utf-8")
            consulted = sorted({
                n.attr for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.Attribute) and n.attr in {"ready_ids", "ready_nodes"}
            })
            assert not consulted, (
                f"{rel} now asks the graph what to run: {consulted}. The graph drives "
                f"execution — that is M11's original wording and not what ADR-0060 decided."
            )

    def test_the_executors_callers_are_exactly_the_named_set(self):
        """ADR-0060 R3. A new caller is a decision, not an edit.

        AST, and the module-level/function-level distinction is kept: every external one of
        these is a **lazy** import, so a check that required a top-level import would see
        none of them and pass vacuously.
        """
        callers = {m for m in _GRAPH
                   if m != "wisp.graph.executor"
                   and _GRAPH[m] & {"wisp.graph.executor", "wisp.graph.runner"}}
        unexpected = sorted(callers - NAMED_CALLERS)
        assert not unexpected, (
            f"Layer B's executor gained a caller ADR-0060 does not name: {unexpected}. "
            f"If it is the turn loop, that is Position B. If it is a new diagnostic, add it "
            f"to ADR-0060 R3 and to NAMED_CALLERS together."
        )
        assert callers, "no caller found at all — the graph helper is broken (see the floor)"

    def test_no_module_outside_layer_b_reaches_the_executor_except_the_named_three(self):
        """The same property, scoped to consumers — the modules that are *not* Layer B.

        Stated separately because the two failures mean different things: an in-package
        module is Layer B growing, an outside module is a new consumer.
        """
        outside = {m for m in _GRAPH
                   if not m.startswith("wisp.graph.")
                   and _GRAPH[m] & {"wisp.graph.executor", "wisp.graph.runner"}}
        assert outside == {"wisp.sdk", "wisp.coding", "wisp.core.doctor"}, (
            f"a module outside wisp/graph/ now reaches the executor: {sorted(outside)}"
        )
        assert "wisp.core.stateless" not in outside and "wisp.core.runtime" not in outside, (
            "a turn-path module reaches the executor directly — that is Position B"
        )


class TestTheReversalCondition:
    """These pin the *reasons* ADR-0060 rejected Position B.

    If one of these fails, Position B became expressible and ADR-0060 must be superseded
    rather than patched. That is the reversal condition, mechanised.
    """

    def test_the_graph_is_still_immutable_mid_run(self):
        from wisp.graph.types import Graph, GraphNode, NodeType

        assert Graph.__dataclass_params__.frozen, (
            "Graph is mutable — an executor could now grow a graph mid-drive. "
            "Re-run the transition probe and reconsider ADR-0060."
        )
        assert GraphNode.__dataclass_params__.frozen
        g = Graph(id="probe", nodes=(GraphNode(id="a", type=NodeType.AGENT),))
        try:
            g.nodes = ()  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            pass
        else:
            raise AssertionError("a node was appended to a frozen Graph")

    def test_the_executor_has_no_growth_api(self):
        from wisp.graph.executor import GraphExecutor

        public = [n for n in dir(GraphExecutor) if not n.startswith("_")]
        growth = [n for n in public
                  if any(k in n.lower() for k in ("add", "insert", "expand", "grow", "mutate"))]
        assert not growth, (
            f"GraphExecutor gained a growth API: {growth}. Position B's transition may now "
            f"be expressible — supersede ADR-0060."
        )
        assert public == ["cancel", "register_function", "resume", "run"], (
            f"GraphExecutor's public surface changed: {sorted(public)}"
        )

    def test_run_still_refuses_a_graph_that_is_not_complete_up_front(self):
        """`run()` validates before it does any work, so a graph built *as the turn
        proceeds* cannot be handed to it."""
        import asyncio

        from wisp.graph.executor import GraphExecutor
        from wisp.graph.types import Graph

        ex = GraphExecutor(workspace=str(REPO))
        try:
            asyncio.run(ex.run(Graph(id="empty", nodes=()), {}))
        except ValueError as exc:
            assert "no nodes" in str(exc), f"the refusal changed shape: {exc}"
        else:
            raise AssertionError("run() accepted an empty graph")

    def test_there_is_still_no_lowering_from_layer_as_graph_to_layer_bs(self):
        """`compat.py` lowers `TaskDAG`, which is not Layer A's graph."""
        from wisp.graph.compat import dag_to_graph  # noqa: F401  (exists, as documented)
        from wisp.core.task_graph import TaskGraph
        from wisp.graph.types import Graph

        assert not issubclass(TaskGraph, Graph)
        assert not issubclass(Graph, TaskGraph)
        offenders = []
        for path in (REPO / "wisp").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                args = [a.arg.lower() for a in node.args.args + node.args.kwonlyargs]
                ret = ast.unparse(node.returns) if node.returns else ""
                if any("task_graph" in a for a in args) and "Graph" in ret \
                        and "TaskGraph" not in ret:
                    offenders.append(f"{path.relative_to(REPO)}::{node.name}")
        assert not offenders, (
            f"a TaskGraph -> Graph lowering appeared: {offenders} — Position B may now be "
            f"reachable; supersede ADR-0060"
        )


# ── 2. the four non-violations ──────────────────────────────────────────────

def test_non_violation_1_no_authority_was_added():
    """`turn_succeeded`, `VerificationFloorGuard`, `goal.PRECEDENCE` — by content.

    By content, not by count: ADR-0049's rule is that "row N" is resolved against the
    canonical table, and a guard that pinned only the length would miss a swapped row.
    """
    from wisp.core.goal import PRECEDENCE
    from wisp.core.verification import VerificationFloorGuard

    assert [row[0] for row in PRECEDENCE] == list(range(8)), (
        "the canonical precedence table is eight rows 0-7 (ADR-0049 R1)"
    )
    assert PRECEDENCE[4][2] == "GOAL_FAILED" and "no P3 PASS" in PRECEDENCE[4][1], (
        "row 4 (the fatal clause) changed — resolve 'row N' by content (ADR-0049)"
    )
    assert PRECEDENCE[6][2] == "GOAL_MET", "row 6 (P3 PASS) changed"
    for method in ("note_tool_result", "rejection", "resolved", "reset_turn"):
        assert callable(getattr(VerificationFloorGuard, method, None)), (
            f"VerificationFloorGuard.{method} left the class"
        )
    runtime = (REPO / "wisp/core/runtime.py").read_text(encoding="utf-8")
    assert "_goal_outcome is TerminalOutcome.SUCCEEDED" in runtime, (
        "turn_succeeded no longer derives from the terminal outcome"
    )


def test_non_violation_2_the_executor_produces_no_transcript():
    """ADR-0029's constraint, re-driven at the executor rather than cited.

    `test_node_identity.py` pins that `TaskNode` carries no payload. This pins the *other*
    half: Layer B's executor never builds or names a message list, so it cannot become a
    second producer of the transcript. Identifiers only — docstrings are not Name nodes,
    which is the P8 trap M12 hit.
    """
    src = (REPO / "wisp/graph/executor.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    offending = sorted(names & {"messages", "transcript", "history", "conversation"})
    assert not offending, (
        f"the executor now handles a transcript: {offending} — ADR-0029's constraint "
        f"(the graph is a shape, not a payload) is violated"
    )
    literals = [s.value for s in ast.walk(tree)
                if isinstance(s, ast.Constant) and isinstance(s.value, str)
                and any(k in s.value.lower() for k in ("messages", "transcript"))]
    assert not literals, f"the executor names a transcript in a literal: {literals}"


def test_non_violation_3_the_gate_chain_is_unchanged():
    """`policy_hard_deny` -> `authorize` -> approval, parsed not scanned."""
    src = (REPO / "wisp/tool_executor.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "execute")
    first: dict[str, int] = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if name in ("policy_hard_deny", "authorize", "_get_write_tools"):
            first.setdefault(name, node.lineno)
    assert set(first) == {"policy_hard_deny", "authorize", "_get_write_tools"}, (
        f"a gate left ToolExecutor.execute: {sorted(first)}"
    )
    assert first["policy_hard_deny"] < first["authorize"] < first["_get_write_tools"], (
        f"the agent's gate chain was re-ordered: {first}"
    )


def test_non_violation_4_the_durable_records_are_still_audit_only():
    """The five record kinds, none of which may append to `messages`.

    Asserted at the **branch**, not at the mention. `Session.apply` is a `match` over
    `event.event_type`, so it necessarily *names* every kind — naming one is not the
    violation; appending to `self.messages` inside its branch is.

    The first version of this check asserted the absence of the *name* and failed on all
    five. That is a guard whose claim and whose subject had drifted apart: it reported a
    violation of "audit-only" while actually measuring "does not appear in a match" — the
    F92 class, and the reason this one is written per-branch with a floor.
    """
    from wisp.core.session import SessionEventType

    kinds = {"PROPOSAL", "OUTCOME", "VERDICT", "TASK_GRAPH", "NODE_TRANSITION"}
    present = {k for k in kinds if hasattr(SessionEventType, k)}
    assert present == kinds, f"a durable record kind was removed: {sorted(kinds - present)}"

    tree = ast.parse((REPO / "wisp/core/session.py").read_text(encoding="utf-8"))
    apply_fn = next((n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name == "apply"), None)
    assert apply_fn is not None, "Session.apply left the module — this check is now vacuous"
    matches = [n for n in ast.walk(apply_fn) if isinstance(n, ast.Match)]
    assert matches, "Session.apply is no longer a match — the guard would pass vacuously"

    seen: set[str] = set()
    for m in matches:
        for case in m.cases:
            named = {n.attr for n in ast.walk(case.pattern)
                     if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                     and n.value.id == "SessionEventType"}
            audit = named & kinds
            if not audit:
                continue
            seen |= audit
            appends = [
                n for n in ast.walk(case)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "append"
                and "messages" in ast.unparse(n.func.value)
            ]
            assert not appends, (
                f"Session.apply materialises {sorted(audit)} into `messages`. These records "
                f"are audit-only (ADR-0029) — a second path into messages duplicates every "
                f"tool reply on replay"
            )
    assert seen == kinds, (
        f"these audit-only kinds have no branch in Session.apply at all: "
        f"{sorted(kinds - seen)} — the check found nothing for them, so it did not run"
    )
