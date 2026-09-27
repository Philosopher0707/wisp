"""Layer direction: the core must not depend on presentation layers.

Context (see PHASE_BOUNDARY_FORENSIC.md B1, PHASE_ARCHITECTURAL_INVARIANTS.md INV-6):

`wisp/core/` is the layer everything else depends on. It had a hard, module-level
import edge upward into the CLI:

    wisp/core/approval_gate.py:  from wisp.cli.approval import ApprovalCancelled, ...

which meant any CLI refactor could break the core, and the core could not be
used without the CLI. The verdict types now live in `wisp/exceptions.py` (below
both layers) and `cli/approval.py` re-exports them.

These tests pin the direction. Deferred (function-level) imports are permitted
and counted separately: `wisp/core/doctor.py` introspects the renderer as part
of a *diagnostic* check, which is a diagnostics module inspecting the system
rather than core logic performing presentation.
"""

from __future__ import annotations

import ast
import pathlib


REPO = pathlib.Path(__file__).resolve().parent.parent

# Layers the core must not depend on.
PRESENTATION = ("cli", "transport", "server", "repl", "tui")

# Known, tracked DEFERRED diagnostic imports. A deferred import creates no
# module-load dependency; these are introspection checks inside doctor.
_KNOWN_DEFERRED = {
    ("wisp/core/doctor.py", "wisp.transport"),
}


def _imports_in(path: pathlib.Path):
    """Yield (module, level, lineno, is_deferred) for wisp.* imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"))

    def walk(node, deferred):
        for child in ast.iter_child_nodes(node):
            child_deferred = deferred or isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef))
            if isinstance(child, ast.ImportFrom) and child.module:
                if child.module.startswith("wisp"):
                    yield child.module, child.lineno, child_deferred
            elif isinstance(child, ast.Import):
                for a in child.names:
                    if a.name.startswith("wisp"):
                        yield a.name, child.lineno, child_deferred
            yield from walk(child, child_deferred)

    yield from walk(tree, False)


def _presentation_target(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) >= 2 and parts[1] in PRESENTATION:
        return ".".join(parts[:2])
    return None


def test_core_has_no_module_level_imports_from_presentation():
    """The inversion that existed was module-level; this forbids it."""
    violations = []
    for py in (REPO / "wisp/core").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = str(py.relative_to(REPO))
        for module, lineno, deferred in _imports_in(py):
            target = _presentation_target(module)
            if target and not deferred:
                violations.append(f"{rel}:{lineno} -> {module}")
    assert not violations, (
        "wisp/core/ imports upward into a presentation layer at module level: "
        f"{violations}"
    )


def test_core_deferred_presentation_imports_are_ratcheted():
    """Deferred imports are tolerated only where already known."""
    found = set()
    for py in (REPO / "wisp/core").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = str(py.relative_to(REPO))
        for module, _lineno, deferred in _imports_in(py):
            target = _presentation_target(module)
            if target and deferred:
                found.add((rel, target))
    assert found <= _KNOWN_DEFERRED, (
        "a new deferred presentation import appeared in wisp/core/: "
        f"{sorted(found - _KNOWN_DEFERRED)}"
    )


# ── The relocated verdict types ──────────────────────────────────────

def test_approval_verdict_types_live_below_both_layers():
    from wisp.exceptions import ApprovalCancelled, ApprovalTimeout
    assert ApprovalCancelled.__module__ == "wisp.exceptions"
    assert ApprovalTimeout.__module__ == "wisp.exceptions"


def test_cli_reexport_preserves_identity_for_existing_importers():
    """wisp/transport/cli.py still imports these from wisp.cli.approval."""
    from wisp.cli.approval import ApprovalCancelled as via_cli
    from wisp.exceptions import ApprovalCancelled as canonical
    assert via_cli is canonical


def test_verdict_semantics_preserved():
    """The move must not change behaviour: tool_name and message shape."""
    from wisp.exceptions import ApprovalCancelled, ApprovalTimeout

    cancelled = ApprovalCancelled("run_bash")
    assert cancelled.tool_name == "run_bash"
    assert "run_bash" in str(cancelled)

    timed_out = ApprovalTimeout("write_file")
    assert timed_out.tool_name == "write_file"
    assert "write_file" in str(timed_out)

    # A verdict, not an interruption.
    assert not issubclass(ApprovalCancelled, (KeyboardInterrupt,))
    assert issubclass(ApprovalCancelled, Exception)


def test_approval_gate_imports_the_canonical_home():
    src = (REPO / "wisp/core/approval_gate.py").read_text(encoding="utf-8")
    assert "from wisp.cli" not in src, (
        "wisp/core/approval_gate.py imports from the CLI layer again"
    )
    assert "from wisp.exceptions import ApprovalCancelled" in src


def test_tool_executor_imports_the_canonical_home():
    src = (REPO / "wisp/tool_executor.py").read_text(encoding="utf-8")
    assert "from wisp.cli.approval import" not in src
    assert "from wisp.exceptions import ApprovalCancelled" in src


# ── Phase 10: complete edge classification ───────────────────────────
# A full audit of `wisp/core/**` found exactly ONE core -> presentation edge:
#   wisp/core/doctor.py:325  ->  wisp.transport   [deferred]
# It is classified INTENTIONAL INTROSPECTION: doctor inspects the renderer
# for diagnostic purposes (checking that the rendering symbols exist and that
# the module is mode-aware). It is not core logic performing presentation.
#
# These tests pin the classification and stop the edge from spreading.

#: The complete set of core -> presentation edges, with their classification.
CLASSIFIED_EDGES = {
    ("wisp/core/doctor.py", "wisp.transport"): "intentional-introspection",
}


def _type_only_imports(path: pathlib.Path) -> list[str]:
    """Modules imported only under `if TYPE_CHECKING:`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
            for sub in ast.walk(node):
                if isinstance(sub, ast.ImportFrom) and sub.module:
                    out.append(sub.module)
                elif isinstance(sub, ast.Import):
                    out.extend(a.name for a in sub.names)
    return out


def test_core_has_no_type_only_presentation_edges():
    """A type-only import still couples the layers at type-check time."""
    offenders = []
    for py in (REPO / "wisp/core").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = str(py.relative_to(REPO))
        for module in _type_only_imports(py):
            target = _presentation_target(module)
            if target:
                offenders.append(f"{rel} -> {module}")
    assert not offenders, (
        f"wisp/core/ type-only imports from presentation: {offenders}"
    )


def test_every_core_to_presentation_edge_is_classified():
    """An unclassified edge is an unreviewed dependency."""
    found = set()
    for py in (REPO / "wisp/core").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = str(py.relative_to(REPO))
        for module, _lineno, _deferred in _imports_in(py):
            target = _presentation_target(module)
            if target:
                found.add((rel, target))
    assert found <= set(CLASSIFIED_EDGES), (
        "unclassified core -> presentation edge(s): "
        f"{sorted(found - set(CLASSIFIED_EDGES))}. Either remove the edge or "
        "add it to CLASSIFIED_EDGES with a reason."
    )


def test_the_introspection_edge_stays_isolated():
    """The one intentional edge must remain a single, deferred site.

    If it spreads to a second function or becomes module-level, it is no
    longer 'one diagnostic check' — it is a dependency.
    """
    path = REPO / "wisp/core/doctor.py"
    deferred = [(m, ln) for m, ln, d in _imports_in(path)
                if d and _presentation_target(m)]
    module_level = [(m, ln) for m, ln, d in _imports_in(path)
                    if not d and _presentation_target(m)]
    assert module_level == [], (
        f"the doctor introspection edge became module-level: {module_level}"
    )
    assert len(deferred) == 1, (
        f"the doctor introspection edge multiplied: {deferred}"
    )


def test_the_introspection_edge_is_inside_a_guard():
    """It must stay wrapped in a try/except so a missing renderer cannot
    break a diagnostic run."""
    src = (REPO / "wisp/core/doctor.py").read_text(encoding="utf-8")
    lines = src.splitlines()
    # Find the deferred import and look back for the guard.
    for i, line in enumerate(lines):
        if "from wisp.transport import renderer" in line:
            window = "\n".join(lines[max(0, i - 4):i + 1])
            assert "try:" in window, (
                "the renderer introspection import is no longer guarded"
            )
            return
    raise AssertionError("the renderer introspection import disappeared")
