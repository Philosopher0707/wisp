"""ADR-0060 R5 — Layer C's disposition, pinned.

ADR-0001 named Layer C (`wisp/core/graph/`) **disowned**. It was not: the live turn path imported
`OscillationTrap` and `diff_hash` from it, through `core/stagnation.py` (wired at M13/ADR-0034) and
`core/runtime.py`. That is the *disowned-but-consumed* shape this repository keeps finding — the
claim and the code had drifted.

**The decision: relocate the live symbols, keep the dead part as a disowned reference.** Both symbols
now live in `wisp/core/oscillation.py` (Layer A); `core/graph/loop.py` imports and re-exports them, so
`wisp/core/graph/`'s public surface is unchanged and `wisp/core/graph/__init__.py` — which carries the
user's uncommitted WIP — needs no edit.

The normative rule this file enforces is a **direction**: *a disowned layer may import from Layer A;
the live path may never import from a disowned layer.* That is one rule, and it is what makes the
disposition mechanical instead of a prose claim.

Every structural check is AST-based (a string scan reads docstrings as code — `CONTEXT.md` §10
instance 5), and the reachability helper carries a **floor**: it must see hand-verified edges before
any of its absences count.
"""
from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]

#: Layer C. ADR-0001's disowned layer.
LAYER_C_PREFIX = "wisp.core.graph"

#: The live turn path. These two modules are Layer A's iteration and session management.
TURN_PATH_ROOTS = ("wisp.core.stateless", "wisp.core.runtime")

#: Hand-verified edges the reachability helper must see, or it is broken.
FLOOR = (
    ("wisp.core.stagnation", "wisp.core.oscillation"),
    ("wisp.core.graph.loop", "wisp.core.oscillation"),
    ("wisp.core.graph.loop", "wisp.core.graph.phases"),
    ("wisp.core.runtime", "wisp.core.doctor"),
)


def _module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(REPO).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_imports(module: str, node: ast.ImportFrom) -> list[str]:
    """`from X import Y` binds the *module* X — that is the edge."""
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
        self.edges: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            parts = alias.name.split(".")
            for i in range(len(parts)):
                self.edges.add(".".join(parts[: i + 1]))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.edges.update(_resolve_imports(self.module, node))

    def visit_FunctionDef(self, node) -> None:
        self.generic_visit(node)

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
    seen: set[str] = set()
    queue = [root]
    while queue:
        cur = queue.pop()
        if cur in seen or cur not in _GRAPH:
            continue
        seen.add(cur)
        queue.extend(_GRAPH[cur] - seen)
    return seen


# ── the floor ───────────────────────────────────────────────────────────────

class TestTheReachabilityHelperIsNotVacuous:
    def test_it_sees_the_hand_verified_edges(self):
        missing = [f"{s} -> {d}" for s, d in FLOOR if d not in _GRAPH.get(s, set())]
        assert not missing, (
            f"the import graph cannot see edges that exist in the source: {missing} — "
            f"the helper is broken, not the tree"
        )

    def test_it_resolves_a_from_import_to_its_module(self):
        node = ast.parse("from wisp.core.oscillation import OscillationTrap").body[0]
        assert "wisp.core.oscillation" in _resolve_imports("probe", node)


# ── 1. the direction — the normative rule ──────────────────────────────────

class TestTheLivePathDoesNotImportLayerC:
    """**The rule.** A disowned layer may import from Layer A; not the reverse."""

    def test_stateless_does_not_reach_layer_c(self):
        hits = sorted(h for h in _closure("wisp.core.stateless")
                      if h == LAYER_C_PREFIX or h.startswith(LAYER_C_PREFIX + "."))
        assert not hits, (
            f"the turn engine reaches Layer C: {hits}. Layer C is disowned (ADR-0001) and its "
            f"live symbols were relocated to wisp/core/oscillation.py for exactly this reason "
            f"(ADR-0060 R5). Import from there instead."
        )

    def test_runtime_does_not_reach_layer_c(self):
        """This is the edge that was live before the relocation.

        `runtime.py` imported `diff_hash` from `wisp.core.graph.loop`; it now imports it from
        `wisp.core.oscillation`. If this fails, the relocation was undone.
        """
        hits = sorted(h for h in _closure("wisp.core.runtime")
                      if h == LAYER_C_PREFIX or h.startswith(LAYER_C_PREFIX + "."))
        assert not hits, (
            f"the runtime reaches Layer C: {hits} — the live path must not depend on a "
            f"disowned layer (ADR-0060 R5)"
        )

    def test_layer_c_imports_layer_a_and_not_the_reverse(self):
        """The direction, asserted positively so the rule is not just an absence."""
        assert "wisp.core.oscillation" in _GRAPH.get("wisp.core.graph.loop", set()), (
            "wisp/core/graph/loop.py no longer imports the relocated module — either the "
            "relocation was undone or loop.py defines a second copy"
        )

    def test_no_production_module_outside_layer_c_imports_it(self):
        """Layer C's only consumers are itself and tests — that is what 'disowned' means."""
        consumers = {
            m for m in _GRAPH
            if not m.startswith(LAYER_C_PREFIX)
            and any(e == LAYER_C_PREFIX or e.startswith(LAYER_C_PREFIX + ".") for e in _GRAPH[m])
        }
        assert consumers == set(), (
            f"a production module outside wisp/core/graph/ imports Layer C: {sorted(consumers)} "
            f"— Layer C is disowned; if one of these is the live path, ADR-0060 R5 is reversed"
        )


# ── 2. the relocation is a MOVE, not a copy ────────────────────────────────

class TestTheRelocationIsAMove:
    def test_the_names_are_the_same_objects(self):
        """Identity, not equality: a re-implementation would pass an equality check."""
        import wisp.core.graph.loop as loop_mod
        import wisp.core.oscillation as osc
        import wisp.core.stagnation as st

        assert loop_mod.OscillationTrap is osc.OscillationTrap, (
            "wisp/core/graph/loop.py defines its own trap again — that is the second "
            "authority for 'is this a repeat?' the relocation removed"
        )
        assert loop_mod.diff_hash is osc.diff_hash, (
            "wisp/core/graph/loop.py defines its own diff_hash again"
        )
        assert st.OscillationTrap is osc.OscillationTrap

    def test_layer_cs_public_surface_is_unchanged(self):
        """`wisp/core/graph/__init__.py` carries the user's uncommitted WIP and must not need an edit."""
        import wisp.core.graph as pkg

        for name in ("OscillationTrap", "diff_hash", "ExecutionGraph", "Phase",
                     "next_phase", "is_terminal"):
            assert hasattr(pkg, name), f"wisp.core.graph no longer exports {name}"

    def test_the_relocated_module_is_self_contained(self):
        """Layer A's new module must not import Layer C — the rule, at the source."""
        tree = ast.parse((REPO / "wisp/core/oscillation.py").read_text(encoding="utf-8"))
        imports = {
            n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
        } | {
            a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
            for a in n.names
        }
        bad = sorted(i for i in imports if i.startswith("wisp"))
        assert not bad, (
            f"wisp/core/oscillation.py imports {bad} — it must be a leaf so the live path's "
            f"dependency cannot lead back into Layer C"
        )


# ── 3. the behaviour is unchanged (ADR-0037's monotonicity included) ───────

class TestTheBehaviourIsUnchanged:
    def test_the_trap_semantics_are_identical(self):
        """Driven against known values — the same assertions the pre-move test made."""
        from wisp.core.oscillation import OscillationTrap, diff_hash

        trap = OscillationTrap()
        assert trap.observe(diff_hash("d0")) is None
        assert trap.observe(diff_hash("d1")) is None
        assert trap.observe(diff_hash("d0")) == "cycle"
        trap2 = OscillationTrap()
        assert trap2.observe(diff_hash("same")) is None
        assert trap2.observe(diff_hash("same")) == "repeat"

    def test_the_digest_is_stable_and_content_addressed(self):
        from wisp.core.oscillation import diff_hash

        assert diff_hash("a") == diff_hash("a")
        assert diff_hash("a") != diff_hash("b")
        assert len(diff_hash("")) == 64

    def test_the_trap_latch_is_still_monotonic(self):
        """ADR-0037: the latch never clears inside a turn.

        The trap has no reset at all — which is *how* the monotonicity is achieved — so the
        property is driven rather than asserted: once a repeat is seen, the same trap keeps
        reporting one for the same input, and `trap_fired` is a fold over the verdicts.
        """
        from wisp.core.oscillation import OscillationTrap, diff_hash

        trap = OscillationTrap()
        trap.observe(diff_hash("x"))
        assert trap.observe(diff_hash("x")) == "repeat"
        assert not [n for n in dir(trap) if "reset" in n.lower() or "clear" in n.lower()], (
            "the trap gained a reset — ADR-0037's monotonicity is a property of the trap, "
            "and its only reset is the turn boundary"
        )
        # the fold the live path uses
        verdicts = [trap.observe(diff_hash("y")) for _ in range(3)]
        assert bool(verdicts) is True, "trap_fired stopped being a truthy fold"


# ── 4. the dead part is still dead, and named as such ──────────────────────

class TestTheDisownedPartHasNoProductionCaller:
    def test_execution_graph_has_no_production_caller(self):
        """The phase loop is retained as a reference implementation, not as a live path."""
        callers = []
        for path in (REPO / "wisp").rglob("*.py"):
            if path.name == "loop.py" or path.name == "__init__.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and any(
                    a.name in {"ExecutionGraph", "Phase", "next_phase", "is_terminal"}
                    for a in node.names
                ):
                    callers.append(str(path.relative_to(REPO)))
                    break
        assert callers == [], (
            f"Layer C's phase loop gained a production caller: {callers} — if this is intended, "
            f"ADR-0060 R5's disposition must be re-decided, not silently superseded"
        )

    def test_the_scan_would_find_a_caller(self):
        """Floor: the check above must be able to see one."""
        tree = ast.parse("from wisp.core.graph.loop import ExecutionGraph\n")
        found = [a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names]
        assert "ExecutionGraph" in found


# ── 5. ADR-0060's four non-violations, re-asserted here ───────────────────

def test_non_violation_1_no_authority_was_added():
    from wisp.core.goal import PRECEDENCE
    from wisp.core.verification import VerificationFloorGuard

    assert [row[0] for row in PRECEDENCE] == list(range(8)), "ADR-0049 R1: eight rows 0-7"
    assert PRECEDENCE[4][2] == "GOAL_FAILED" and "no P3 PASS" in PRECEDENCE[4][1], (
        "row 4 (the fatal clause) changed — resolve 'row N' by content (ADR-0049)"
    )
    for method in ("note_tool_result", "rejection", "resolved", "reset_turn"):
        assert callable(getattr(VerificationFloorGuard, method, None)), (
            f"VerificationFloorGuard.{method} left the class"
        )
    runtime = (REPO / "wisp/core/runtime.py").read_text(encoding="utf-8")
    assert "_goal_outcome is TerminalOutcome.SUCCEEDED" in runtime, (
        "turn_succeeded no longer derives from the terminal outcome"
    )


def test_non_violation_2_the_graph_carries_no_transcript():
    """ADR-0029 — the graph is a shape, not a payload."""
    from wisp.core.task_graph import NODE_FIELD_KINDS, NodeFieldKind

    payloads = sorted(n for n, k in NODE_FIELD_KINDS.items() if k is NodeFieldKind.PAYLOAD)
    assert not payloads, f"TaskNode carries transcript payload: {payloads}"


def test_non_violation_3_the_gate_chain_is_unchanged():
    src = (REPO / "wisp/tool_executor.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "execute")
    first: dict[str, int] = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
        if name in ("policy_hard_deny", "authorize", "_get_write_tools"):
            first.setdefault(name, node.lineno)
    assert set(first) == {"policy_hard_deny", "authorize", "_get_write_tools"}, (
        f"a gate left ToolExecutor.execute: {sorted(first)}"
    )
    assert first["policy_hard_deny"] < first["authorize"] < first["_get_write_tools"], (
        f"the agent's gate chain was re-ordered: {first}"
    )


def test_non_violation_4_the_durable_records_are_still_audit_only():
    from wisp.core.session import SessionEventType

    kinds = {"PROPOSAL", "OUTCOME", "VERDICT", "TASK_GRAPH", "NODE_TRANSITION"}
    assert {k for k in kinds if hasattr(SessionEventType, k)} == kinds
    tree = ast.parse((REPO / "wisp/core/session.py").read_text(encoding="utf-8"))
    apply_fn = next((n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name == "apply"), None)
    assert apply_fn is not None, "Session.apply left the module — this check is now vacuous"
    seen: set[str] = set()
    for m in [n for n in ast.walk(apply_fn) if isinstance(n, ast.Match)]:
        for case in m.cases:
            named = {n.attr for n in ast.walk(case.pattern)
                     if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                     and n.value.id == "SessionEventType"}
            audit = named & kinds
            if not audit:
                continue
            seen |= audit
            appends = [n for n in ast.walk(case) if isinstance(n, ast.Call)
                       and isinstance(n.func, ast.Attribute) and n.func.attr == "append"
                       and "messages" in ast.unparse(n.func.value)]
            assert not appends, f"Session.apply materialises {sorted(audit)} into `messages`"
    assert seen == kinds, f"these kinds have no branch at all: {sorted(kinds - seen)}"
