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

import pytest

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
