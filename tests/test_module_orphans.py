"""Orphan detection that resolves RELATIVE imports correctly.

Context — this test exists because of a methodology error worth recording.

An earlier audit claimed four modules were dead:
`core/speculative/`, `multi_agent/resource_budget.py`,
`cli/commands/doctor.py`, `cli/commands/model.py`.

Three of those claims were **wrong**, for two different reasons:

1. `multi_agent/resource_budget.py` is imported via **relative** imports
   (`from .resource_budget import ResourceBudget`, in `_runner.py:470,640` and
   `subagent_orchestrator.py:1306`). An absolute-path-only AST scan cannot see
   those, so it reported a live module as dead. Deleting it would have broken
   the subagent budget path.
2. `cli/commands/doctor.py` is a **deliberate spec-compliance shim** — its
   docstring states it exists so an external spec's import path resolves.
   Being unreferenced is its purpose, not a defect.

Only `cli/commands/model.py` remains genuinely unreferenced, and it duplicates
the live selection UX in `repl/commands/provider.py`.

This detector resolves both absolute and relative imports, and ratchets the
known-unreferenced set so (a) a real orphan is caught, and (b) the false
"dead code" conclusion cannot be reached by tooling again.
"""

from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
PKG_ROOT = REPO / "wisp"


def _module_name(path: pathlib.Path) -> str:
    rel = str(path.relative_to(REPO))[:-3]     # strip .py
    parts = rel.split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_package(path: pathlib.Path) -> bool:
    return path.name == "__init__.py"


def _resolve_relative(current: str, is_package: bool, level: int, module: str | None) -> str | None:
    """Resolve a relative import to an absolute module name.

    `level` is the number of leading dots. For a module `a.b.c`, `from .x import`
    (level 1) targets `a.b.x`; for a package `a.b`, it targets `a.b.x`.
    """
    base = current if is_package else current.rsplit(".", 1)[0] if "." in current else ""
    for _ in range(level - 1):
        base = base.rsplit(".", 1)[0] if "." in base else ""
    if not base:
        return module
    return f"{base}.{module}" if module else base


def _collect_imports(path: pathlib.Path) -> set[str]:
    current = _module_name(path)
    is_pkg = _is_package(path)
    out: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return out

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                target = _resolve_relative(current, is_pkg, node.level, node.module)
                if target:
                    out.add(target)
            elif node.module and node.module.startswith("wisp"):
                out.add(node.module)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("wisp"):
                    out.add(a.name)
    return out


def _all_modules() -> dict[str, pathlib.Path]:
    mods: dict[str, pathlib.Path] = {}
    for py in PKG_ROOT.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        mods[_module_name(py)] = py
    return mods


def _all_imports() -> set[str]:
    """Imports from the package, the test suite, and scripts."""
    found: set[str] = set()
    sources = list(PKG_ROOT.rglob("*.py"))
    for extra in ("tests", "scripts", "examples", "benchmarks"):
        d = REPO / extra
        if d.exists():
            sources.extend(d.rglob("*.py"))
    for py in sources:
        if "__pycache__" in py.parts:
            continue
        if not str(py).startswith(str(REPO)):
            continue
        found |= _collect_imports(py)
    return found


def _referenced(imported: set[str], module: str) -> bool:
    """A module is referenced if it, or a submodule of it, is imported."""
    return any(i == module or i.startswith(module + ".") for i in imported)


# Modules with no importer, each with a recorded reason for existing.
KNOWN_UNREFERENCED = {
    # Background-job supervisor: launched as a detached process (`python -m wisp.jobs.supervisor`), never imported.
    "wisp.jobs.supervisor",
    # Spec-compliance shim: an external spec names this import path, so being
    # unreferenced internally is the point (see its module docstring).
    "wisp.cli.commands.doctor",
    # Duplicate of the live selection UX in repl/commands/provider.py.
    # Genuinely unreferenced; deletion is sequenced, not urgent.
    "wisp.cli.commands.model",
    # TUI packages loaded by Textual conventions (CSS_PATH / themes), not imports.
    "wisp.tui.css",
    "wisp.tui.themes",
    "wisp.tui.widgets.tools",
    "wisp.tui.widgets.tools.tool_history",
    # MCP server entrypoint: launched as a process, never imported.
    "wisp.mcp_servers",
    "wisp.mcp_servers.vscode_server",
    # CLI command package namespace (holds doctor/model above).
    "wisp.cli.commands",
    # The owner's WIP, tracked so CI can import its sibling capability_filter.
    # A near-duplicate of the wired infra/circuit_breaker.py; P9 recommends
    # deletion (see test_structured_delegation.py), which is the owner's call.
    "wisp.multi_agent._circuit_breaker",
}


def test_no_unexpected_orphan_modules():
    """Catches real dead code, with relative imports resolved."""
    imported = _all_imports()
    orphans = {m for m in _all_modules() if not _referenced(imported, m)}
    unexpected = orphans - KNOWN_UNREFERENCED
    assert not unexpected, (
        "new module(s) with no importer anywhere — either wire them up or "
        f"add to KNOWN_UNREFERENCED with a reason: {sorted(unexpected)}"
    )


def test_the_known_set_is_still_accurate():
    """If a listed module gains a real importer, the list is stale."""
    imported = _all_imports()
    now_referenced = {m for m in KNOWN_UNREFERENCED if _referenced(imported, m)}
    assert not now_referenced, (
        f"these modules now have importers and should leave KNOWN_UNREFERENCED: "
        f"{sorted(now_referenced)}"
    )


# ── The two specific false claims, pinned ────────────────────────────

def test_resource_budget_is_live_via_relative_imports():
    """Regression pin for the blind spot: absolute-only scanning missed this."""
    imported = _all_imports()
    assert _referenced(imported, "wisp.multi_agent.resource_budget"), (
        "resource_budget.py has no importer — if you removed the relative "
        "imports, that is a real change; otherwise this detector regressed"
    )


def test_speculative_is_test_covered():
    """Not dead: a test imports it directly, even though production does not."""
    src = (REPO / "tests/test_speculative_search.py").read_text(encoding="utf-8")
    assert "wisp.core.speculative" in src
    assert _referenced(_all_imports(), "wisp.core.speculative")


def test_relative_import_resolution_is_correct():
    """Unit-pin the resolver, since a bug here silently fabricates orphans.

    PEP 328: from module `wisp.a.b.c`, `.` is the package `wisp.a.b` and `..`
    is its parent `wisp.a`.
    """
    assert _resolve_relative("wisp.multi_agent._runner", False, 1,
                             "resource_budget") == "wisp.multi_agent.resource_budget"
    assert _resolve_relative("wisp.multi_agent", True, 1,
                             "resource_budget") == "wisp.multi_agent.resource_budget"
    # level 1 -> wisp.a.b.d ; level 2 -> wisp.a.d
    assert _resolve_relative("wisp.a.b.c", False, 1, "d") == "wisp.a.b.d"
    assert _resolve_relative("wisp.a.b.c", False, 2, "d") == "wisp.a.d"
    assert _resolve_relative("wisp.a.b.c", False, 1, None) == "wisp.a.b"
