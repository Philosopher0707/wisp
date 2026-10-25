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

A second methodology error is recorded here: the first version counted imports
from `tests/` as usage, so a module only a test imported looked alive. That is
how `runtime/idempotency.py`, `runtime/redaction.py` and the dead duplicates of
live modules survived — reachable by a test, and by nothing a user runs.
Production reachability is now asserted separately from "anything imports it".
"""

from __future__ import annotations

import ast
import pathlib
from functools import lru_cache

REPO = pathlib.Path(__file__).resolve().parent.parent
PKG_ROOT = REPO / "wisp"

PRODUCTION_ROOTS = ("wisp", "agent", "wisp_net")
NON_PRODUCTION_ROOTS = ("tests", "scripts", "examples", "benchmarks")


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
                    # `from . import runner` and `from pkg import sub` name a
                    # submodule in the alias, not in `module`.
                    out.update(f"{target}.{a.name}" for a in node.names)
            elif node.module and node.module.startswith("wisp"):
                out.add(node.module)
                out.update(f"{node.module}.{a.name}" for a in node.names)
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


@lru_cache(maxsize=None)
def _edges(roots: tuple[str, ...]) -> set[tuple[str, str]]:
    """(importer, imported) pairs from the given source roots.

    Cached: the tree does not change during a run, and the scan is the slow part.
    """
    found: set[tuple[str, str]] = set()
    for root in roots:
        d = REPO / root
        if not d.exists():
            continue
        for py in d.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            importer = _module_name(py)
            found.update((importer, target) for target in _collect_imports(py))
    return found


def _referenced(edges: set[tuple[str, str]], module: str) -> bool:
    """A module is referenced if code OUTSIDE it imports it, or a submodule of it.

    A package's own wiring is not evidence it is reachable: `wisp/core/graph/`
    importing its siblings says nothing about whether anything imports the
    package. Keeping the importer is what makes that distinguishable.
    """
    return any(
        importer != module
        and not importer.startswith(module + ".")
        and (target == module or target.startswith(module + "."))
        for importer, target in edges
    )


@lru_cache(maxsize=None)
def _unwired_modules() -> set[str]:
    """Modules with no importer in shipped code (`wisp/`, `agent/`, `wisp_net/`)."""
    edges = _edges(PRODUCTION_ROOTS)
    return {m for m in _all_modules() if not _referenced(edges, m)}


# Modules with no importer, each with a recorded reason for existing.
KNOWN_UNREFERENCED = {
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

# Modules with no PRODUCTION importer, each with the reason it still exists.
# These are reachable from tests (or scripts), so the older detector — which
# counted every importer — called them live. Every entry is a deletion
# candidate, a staged subsystem, or a deliberate external-compatibility shim;
# this ledger is the worklist.
KNOWN_UNWIRED: dict[str, str] = {
    # Entry points: reached by name, never by import.
    "wisp.__main__": "console entry point ([project.scripts]); tests import it, production does not",
    "wisp.jobs.supervisor": "detached supervisor launched with `-m`; one test imports it",
    "wisp.jobs": "namespace for the detached supervisor; entered with `-m`, never imported",
    # Staged subsystem with no production caller yet (docs/harness/background-jobs-design.md).
    "wisp.jobs.spawn": "job launcher for the supervisor; nothing in `wisp/` calls it yet",
    # Kept for external consumers of the published package.
    "wisp.agent": "backwards-compatible WispAgent shim; no in-repo importer",
    # Dead duplicates of live modules (W1 deletion targets).
    "wisp.core.context.compactor": "duplicate of core/compaction.py; no importer",
    "wisp.core.context.repomap": "duplicate of repo_map.py; no importer",
    "wisp.runtime.idempotency": "no production importer; the live path uses core/action_key.py",
    "wisp.runtime.redaction": "no production importer; auth/secrets.py owns redaction on the live path",
    "wisp.transport.protocol": "unused transport-message schema; core/events.py is the live event model",
    # Test-only subsystems and prototypes.
    "wisp.core.graph": "experimental Layer C graph; test-only by ADR-0060",
    "wisp.core.mutator": "disk-safe mutation primitives; test-only",
    "wisp.core.speculative": "speculative-search prototype; test-only",
    "wisp.eval": "evaluation harness; tests only",
    "wisp.benchmark.adapters": "terminal-bench backends; tests only",
    "wisp.markdown_parser": "markdown response parser; no production importer",
    "wisp.multi_agent.capability_matcher": "capability-mismatch detector; test-only",
    "wisp.structured_output": "Ollama JSON-output enforcer; no production importer",
    # Compatibility shim pinned by its own tests (11 of them).
    "wisp.graph.compat": "graph compatibility shim; tests pin it, production does not use it",
}


def test_no_unexpected_orphan_modules():
    """Catches real dead code, with relative imports resolved."""
    edges = _edges(PRODUCTION_ROOTS + NON_PRODUCTION_ROOTS)
    orphans = {m for m in _all_modules() if not _referenced(edges, m)}
    unexpected = orphans - KNOWN_UNREFERENCED
    assert not unexpected, (
        "new module(s) with no importer anywhere — either wire them up or "
        f"add to KNOWN_UNREFERENCED with a reason: {sorted(unexpected)}"
    )


def test_no_unexpected_unwired_modules():
    """A module only a test imports is not a shipped feature."""
    unwired = _unwired_modules() - KNOWN_UNREFERENCED
    unexpected = unwired - set(KNOWN_UNWIRED)
    assert not unexpected, (
        "module(s) with no production importer — wire them up or record why "
        f"in KNOWN_UNWIRED: {sorted(unexpected)}"
    )


def test_the_known_set_is_still_accurate():
    """If a listed module gains a real importer, the list is stale."""
    edges = _edges(PRODUCTION_ROOTS + NON_PRODUCTION_ROOTS)
    now_referenced = {m for m in KNOWN_UNREFERENCED if _referenced(edges, m)}
    assert not now_referenced, (
        f"these modules now have importers and should leave KNOWN_UNREFERENCED: "
        f"{sorted(now_referenced)}"
    )


def test_the_unwired_set_is_still_accurate():
    """If a listed module gains a production importer, the ledger is stale."""
    now_wired = set(KNOWN_UNWIRED) - _unwired_modules()
    assert not now_wired, (
        f"these now have a production importer and should leave KNOWN_UNWIRED: {sorted(now_wired)}"
    )


def test_every_unwired_reason_is_recorded():
    missing = [m for m, why in KNOWN_UNWIRED.items() if not why.strip()]
    assert not missing, f"KNOWN_UNWIRED entries need a reason: {missing}"


def test_every_unwired_entry_is_reachable_from_somewhere():
    """The ledger is for test/script-reachable modules, not for dead ones.

    A module nothing imports at all belongs in KNOWN_UNREFERENCED; one that a
    test imports belongs here. Without this, a ledger entry could hide a module
    that is simply dead.
    """
    all_edges = _edges(PRODUCTION_ROOTS + NON_PRODUCTION_ROOTS)
    unexplained = sorted(m for m in KNOWN_UNWIRED if not _referenced(all_edges, m))
    assert not unexplained, (
        f"no importer anywhere — move to KNOWN_UNREFERENCED: {unexplained}"
    )


def test_the_two_lenses_disagree_on_a_test_only_module():
    """Positive control: the production lens must not count test imports.

    `wisp.__main__` is imported by tests and by nothing in `wisp/`. If this
    fails, the production lens has widened and the gate is blind again.
    """
    module = "wisp.__main__"
    assert module in _unwired_modules()
    assert _referenced(_edges(PRODUCTION_ROOTS + NON_PRODUCTION_ROOTS), module)


# ── The two specific false claims, pinned ────────────────────────────

def test_resource_budget_is_live_via_relative_imports():
    """Regression pin for the blind spot: absolute-only scanning missed it."""
    edges = _edges(PRODUCTION_ROOTS + NON_PRODUCTION_ROOTS)
    assert _referenced(edges, "wisp.multi_agent.resource_budget"), (
        "resource_budget.py has no importer — if you removed the relative "
        "imports, that is a real change; otherwise this detector regressed"
    )


def test_speculative_is_test_covered():
    """Not dead: a test imports it directly, even though production does not."""
    src = (REPO / "tests/test_speculative_search.py").read_text(encoding="utf-8")
    assert "wisp.core.speculative" in src
    assert _referenced(_edges(PRODUCTION_ROOTS + NON_PRODUCTION_ROOTS), "wisp.core.speculative")


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


def test_from_import_names_are_resolved():
    """`from pkg import sub` names the submodule only in the alias."""
    imports = _collect_imports(REPO / "tests/jobs/conftest.py")
    assert "wisp.jobs.procs" in imports
