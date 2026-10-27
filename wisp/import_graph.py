"""Build a module dependency graph from Python source files.

Used by the auto-test system to determine which tests to run when
files change.  The graph maps every ``*.py`` file to the set of
module paths it imports (resolved relative to the workspace root).
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Optional

from wisp.core.workspace_walk import STANDARD_SKIP_DIRS, WalkBudget, WalkBudgetExceeded, walk_files

logger = logging.getLogger(__name__)

#: Past either bound the graph is refused, not truncated: a partial graph would silently miss affected tests, and
#: this runs after every write, so an unbounded walk made `write_file` take minutes from a $HOME workspace.
MAX_FILES = 5000
BUDGET_SECONDS = 5.0


class ImportGraphTooLarge(RuntimeError):
    """The workspace has more Python files (or takes longer to read) than the affected-test lookup may spend."""


def _resolve_import(
    workspace: Path,
    source_file: Path,
    module_parts: list[str],
    level: int,
) -> Optional[Path]:
    """Resolve an import statement to an absolute file path.

    Parameters
    ----------
    workspace:
        Project root (where imports are resolved from).
    source_file:
        The file containing the import (needed for relative imports).
    module_parts:
        Dotted module name split into components, e.g. ["wisp", "tools"].
    level:
        Relative import level (0 = absolute, 1 = ``.foo``, 2 = ``..foo``).

    Returns
    -------
    Absolute :class:`~pathlib.Path` to the resolved ``*.py`` file, or
    ``None`` if it cannot be resolved inside *workspace*.
    """
    if level > 0:
        # Relative import: start from the package containing source_file
        base = source_file.parent
        for _ in range(level - 1):
            base = base.parent
        parts = list(base.relative_to(workspace).parts) + module_parts
    else:
        parts = module_parts

    if not parts:
        return None

    # Try as a package (__init__.py)
    pkg_path = workspace.joinpath(*parts, "__init__.py")
    if pkg_path.exists():
        return pkg_path.resolve()

    # Try as a module (.py)
    mod_path = workspace.joinpath(*parts).with_suffix(".py")
    if mod_path.exists():
        return mod_path.resolve()

    return None


def _extract_imports_from_file(source_file: Path, workspace: Path) -> set[Path]:
    """Parse a single Python file and return the set of workspace-local
    files it imports."""
    try:
        source = source_file.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug("Cannot read %s: %s", source_file, exc)
        return set()

    try:
        tree = ast.parse(source, str(source_file))
    except SyntaxError as exc:
        logger.debug("Syntax error in %s: %s", source_file, exc)
        return set()

    imports: set[Path] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                resolved = _resolve_import(workspace, source_file, parts, level=0)
                if resolved:
                    imports.add(resolved)

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            parts = module.split(".") if module else []
            level = node.level or 0
            resolved = _resolve_import(workspace, source_file, parts, level)
            if resolved:
                imports.add(resolved)
            # The alias may name a submodule: `from pkg import sub` loads both
            # pkg/__init__.py and pkg/sub.py. An alias naming a function or
            # class resolves to no file and is skipped.
            for alias in node.names:
                if alias.name == "*":
                    continue
                resolved = _resolve_import(workspace, source_file, [*parts, alias.name], level)
                if resolved:
                    imports.add(resolved)

    return imports


def build_import_graph(
    workspace: str | Path,
    *,
    max_files: int | None = None,
    budget_seconds: float | None = None,
) -> dict[Path, set[Path]]:
    """Build a dependency graph for every ``*.py`` file under *workspace*.

    Returns a dict mapping each source file to the set of workspace-local
    files it imports.  Only files inside *workspace* are included.

    The bounds are parameters because the per-write affected-test lookup is not
    the only caller: a one-off scan of a known repository can afford more. They
    are read here, not bound as default arguments, so a test can patch the
    module constants.
    """
    max_files = MAX_FILES if max_files is None else max_files
    budget_seconds = BUDGET_SECONDS if budget_seconds is None else budget_seconds
    root = Path(workspace).resolve()
    graph: dict[Path, set[Path]] = {}
    budget = WalkBudget(max_files=max_files, max_seconds=budget_seconds)

    try:
        # Refuse, not truncate: a partial graph would silently miss affected tests.
        for name in walk_files(root, skip_dirs=STANDARD_SKIP_DIRS, skip_hidden=True, skip_venvs=True,
                               suffixes=(".py",), budget=budget, on_budget="raise"):
            pyfile = Path(name).resolve()
            imports = _extract_imports_from_file(pyfile, root)
            # Only keep imports that are inside the workspace
            graph[pyfile] = {p for p in imports if root in p.parents or p == root}
    except WalkBudgetExceeded as exc:
        raise ImportGraphTooLarge(
            f"{root} has more than {max_files} Python files or took longer than "
            f"{budget_seconds:g}s to read") from exc

    return graph


def find_affected_tests(
    changed_files: list[str | Path],
    graph: dict[Path, set[Path]],
    test_pattern: str = "test_*.py",
) -> list[Path]:
    """Return test files that may be affected by *changed_files*.

    A test file is "affected" if it directly imports a changed file,
    or if it imports a file that transitively imports a changed file
    (up to 2 levels of indirection).
    """
    changed = {Path(f).resolve() for f in changed_files}

    # Build reverse graph: file -> files that import it
    reverse: dict[Path, set[Path]] = {}
    for src, deps in graph.items():
        for dep in deps:
            reverse.setdefault(dep, set()).add(src)

    # BFS up to 2 levels
    affected: set[Path] = set()
    frontier = set(changed)
    for _ in range(2):
        next_frontier: set[Path] = set()
        for f in frontier:
            for importer in reverse.get(f, set()):
                if importer not in affected:
                    affected.add(importer)
                    next_frontier.add(importer)
        frontier = next_frontier

    # Filter to test files
    test_files = [p for p in affected if is_test_file(p)]
    return sorted(test_files)


def is_test_file(path: Path) -> bool:
    return path.name.startswith("test_") or path.name.endswith("_test.py")


def find_named_tests(named_files: list[str | Path], graph: dict[Path, set[Path]]) -> list[Path]:
    """The test files among *named_files* themselves. Only a file the workspace scan found counts: the graph is the containment, so a path outside the workspace, or
    one that does not exist, never becomes a test to run."""
    return sorted({p for p in (Path(f).resolve() for f in named_files) if p in graph and is_test_file(p)})


def find_tests_for_file(
    source_file: str | Path,
    graph: dict[Path, set[Path]],
) -> list[Path]:
    """Convenience wrapper: find tests affected by a single file change."""
    return find_affected_tests([source_file], graph)
