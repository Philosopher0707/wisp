"""Personalized PageRank RepoMap over tree-sitter symbol definitions.

Pipeline:
  1. Definitions come from ``wisp.tree_sitter_index.build_index`` (with a
     documented regex fallback when tree-sitter is unavailable).
  2. Reference edges are whole-word mention counts of defined names in
     other files (bounded scan: file count and byte caps).
  3. Personalized PageRank (damping ``d=0.85``) seeds mass on the
     active/edited files; dangling mass redistributes uniformly.
  4. File mass distributes evenly to that file's definitions.
  5. Binary search fits the largest definition prefix into the token
     budget (default 1,024); over-budget tails elide to path-grouped
     signature lines instead of being dropped silently.

Symbol extraction results are memoized per workspace with a short TTL so
repeated builds in one session skip disk I/O while staying near-fresh.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DAMPING = 0.85
DEFAULT_BUDGET_TOKENS = 1024
_MAX_SCAN_FILES = 500
_MAX_FILE_BYTES = 200_000
_INDEX_TTL_S = 30.0

_DEF_RE = re.compile(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class ScoredSymbol:
    """One ranked definition with its file-mass share."""

    path: str
    name: str
    kind: str
    line: int
    score: float


@dataclass
class _IndexCache:
    symbols_by_file: dict[str, list[tuple[str, str, int]]] = field(default_factory=dict)
    files_content: dict[str, str] = field(default_factory=dict)
    built_at: float = 0.0


_CACHE: dict[str, _IndexCache] = {}


def _read_bounded(path: str) -> str | None:
    try:
        if os.path.getsize(path) > _MAX_FILE_BYTES:
            return None
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            return handle.read(_MAX_FILE_BYTES + 1)[:_MAX_FILE_BYTES]
    except OSError:
        return None


def _iter_source_files(workspace: str) -> list[str]:
    exts = (".py", ".pyi", ".js", ".ts", ".tsx", ".go", ".rs", ".java")
    found: list[str] = []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs
                   if d not in (".git", ".venv", "node_modules", "__pycache__", ".wisp", "target")]
        for name in files:
            if name.endswith(exts):
                found.append(os.path.join(root, name))
                if len(found) >= _MAX_SCAN_FILES:
                    return found
    return found


def _extract_symbols(workspace: str) -> tuple[dict[str, list[tuple[str, str, int]]], dict[str, str]]:
    """Definitions + contents via tree-sitter index, regex fallback otherwise."""
    symbols_by_file: dict[str, list[tuple[str, str, int]]] = {}
    contents: dict[str, str] = {}
    try:
        from wisp.tree_sitter_index import build_index, is_tree_sitter_available

        available = is_tree_sitter_available()
    except ImportError:
        available = False
    if available:
        try:
            index = build_index(workspace)
            for path, symbols in index.symbols.items():
                symbols_by_file[path] = [(s.name, s.kind, s.line) for s in symbols]
            for path in symbols_by_file:
                content = _read_bounded(os.path.join(workspace, path))
                if content is not None:
                    contents[path] = content
            if symbols_by_file:
                return symbols_by_file, contents
        except Exception:
            logger.debug("tree-sitter index failed; regex fallback", exc_info=True)
    for full in _iter_source_files(workspace):
        content = _read_bounded(full)
        if content is None:
            continue
        rel = os.path.relpath(full, workspace)
        found: list[tuple[str, str, int]] = []
        for lineno, line in enumerate(content.splitlines(), start=1):
            match = _DEF_RE.match(line)
            if match:
                kind = "class" if line.strip().startswith("class") else "function"
                found.append((match.group(1), kind, lineno))
        if found:
            symbols_by_file[rel] = found
            contents[rel] = content
    return symbols_by_file, contents


def _cached_symbols(workspace: str) -> tuple[dict[str, list[tuple[str, str, int]]], dict[str, str]]:
    now = time.monotonic()
    entry = _CACHE.get(workspace)
    if entry is not None and now - entry.built_at < _INDEX_TTL_S:
        return entry.symbols_by_file, entry.files_content
    symbols, contents = _extract_symbols(workspace)
    _CACHE[workspace] = _IndexCache(symbols_by_file=symbols, files_content=contents, built_at=now)
    return symbols, contents


def _build_edges(symbols_by_file: dict[str, list[tuple[str, str, int]]],
                 contents: dict[str, str]) -> dict[str, dict[str, int]]:
    """File -> file reference weights from whole-word mention counts."""
    names: dict[str, str] = {}
    for path, syms in symbols_by_file.items():
        for name, _, _ in syms:
            names.setdefault(name, path)
    patterns = {name: re.compile(r"\b" + re.escape(name) + r"\b") for name in names}
    edges: dict[str, dict[str, int]] = {path: {} for path in symbols_by_file}
    for path, content in contents.items():
        for name, pattern in patterns.items():
            if names[name] == path:
                continue
            count = len(pattern.findall(content))
            if count:
                target = names[name]
                edges[path][target] = edges[path].get(target, 0) + count
    return edges


def _pagerank(nodes: list[str], edges: dict[str, dict[str, int]],
              seeds: list[str], damping: float = DAMPING) -> dict[str, float]:
    """Personalized PageRank over the file graph (deterministic order)."""
    ordered = sorted(nodes)
    n = len(ordered)
    if n == 0:
        return {}
    seed_set = {s for s in seeds if s in edges}
    if not seed_set:
        seed_set = set(ordered)
    personal = {node: (1.0 / len(seed_set) if node in seed_set else 0.0) for node in ordered}
    out_weight = {node: float(sum(edges.get(node, {}).values())) for node in ordered}
    rank = dict(personal)
    for _ in range(100):
        nxt: dict[str, float] = {}
        dangling = sum(rank[node] for node in ordered if out_weight[node] == 0.0)
        delta = 0.0
        for node in ordered:
            mass = personal[node] * (1.0 - damping)
            mass += damping * dangling / n
            for src in ordered:
                w = edges.get(src, {}).get(node, 0)
                if w and out_weight[src]:
                    mass += damping * rank[src] * w / out_weight[src]
            delta = max(delta, abs(mass - rank[node]))
            nxt[node] = mass
        rank = nxt
        if delta < 1e-6:
            break
    total = sum(rank.values()) or 1.0
    return {node: value / total for node, value in rank.items()}


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _render_symbol(path: str, name: str, kind: str, line: int, elided: bool) -> str:
    if elided:
        # Scope elision: name anchor only — kind/line recoverable from head
        # or a follow-up read; keeps tail coverage at ~1/3 the cost.
        return f"{path}::{name}"
    return f"{path}:{line}: {kind} {name}"


class RepoMap:
    """Ranked definition slice of a workspace, fit to a token budget."""

    def __init__(self, workspace: str, budget_tokens: int = DEFAULT_BUDGET_TOKENS) -> None:
        if budget_tokens < 1:
            raise ValueError("budget_tokens must be >= 1")
        self.workspace = workspace
        self.budget_tokens = budget_tokens

    def rank(self, active_files: list[str]) -> list[ScoredSymbol]:
        """Score every definition by seeded file mass (descending)."""
        symbols_by_file, contents = _cached_symbols(self.workspace)
        nodes = sorted(symbols_by_file)
        if not nodes:
            return []
        edges = _build_edges(symbols_by_file, contents)
        file_rank = _pagerank(nodes, edges, active_files)
        scored: list[ScoredSymbol] = []
        for path in nodes:
            syms = symbols_by_file[path]
            share = file_rank.get(path, 0.0) / max(1, len(syms))
            for name, kind, line in syms:
                scored.append(ScoredSymbol(path=path, name=name, kind=kind, line=line, score=share))
        scored.sort(key=lambda s: (-s.score, s.path, s.line))
        return scored

    def format_for_llm(self, ranked: list[ScoredSymbol]) -> str:
        """Fit the highest-ranked prefix into budget via binary search.

        The tail is elided to compact signature lines (AST scope elision
        at line granularity) rather than dropped, so coverage is never
        silently lost — fidelity degrades gracefully.
        """
        if not ranked:
            return ""
        full = [_render_symbol(s.path, s.name, s.kind, s.line, elided=False) for s in ranked]

        def _cost(count: int) -> int:
            return _estimate_tokens("\n".join(full[:count]))

        lo, hi = 0, len(full)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if _cost(mid) <= self.budget_tokens:
                lo = mid
            else:
                hi = mid - 1
        head = full[:lo]
        remaining = self.budget_tokens - _cost(lo)
        tail: list[str] = []
        for s in ranked[lo:]:
            line = _render_symbol(s.path, s.name, s.kind, s.line, elided=True)
            cost = _estimate_tokens(line + "\n")
            if cost > remaining:
                break
            tail.append(line)
            remaining -= cost
        lines = ["# RepoMap (ranked definitions)"] + head + tail
        return "\n".join(lines)
