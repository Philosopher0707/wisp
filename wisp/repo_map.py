"""Repo map — token-efficient codebase index for AI coding agent context.

Builds a dependency-aware map of the workspace with PageRank-style importance
scores. The map is injected into the system prompt as a compact hierarchical
text block (Aider-style) so the LLM has project-level awareness.

Uses tree-sitter for accurate parsing when available (via
wisp.tree_sitter_index), falling back to regex-based extraction
(via wisp.code_index) for common languages.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import subprocess
import time
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────

# Directories to always skip during file discovery
_SKIP_DIRS: set[str] = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "target", "build", "dist", ".eggs", "egg-info",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".next", ".nuxt", ".cache", ".turbo", ".wisp",
    "bower_components", ".tox", ".nox", "coverage",
    ".idea", ".vscode", ".vs",
}

# File extensions we can parse (mapped to language name)
_EXT_TO_LANG: dict[str, str] = {
    ".py": "Python",
    ".pyi": "Python",
    ".rs": "Rust",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rb": "Ruby",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".swift": "Swift",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".hxx": "C++",
}

# Maximum files to scan (configurable, 0 = no limit)
_DEFAULT_MAX_FILES: int = 500
_DEFAULT_MAX_FILE_LINES: int = 3000

# Module-level cache for tree-sitter parsers (expensive to recreate per file)
_PARSER_CACHE: dict = {}
_PARSER_LOCK = threading.Lock()

# Entry-point filenames that get an initial PageRank boost
_ENTRY_POINT_PATTERNS: list[str] = [
    "main.py", "main.rs", "main.go", "index.ts", "index.tsx",
    "index.js", "index.jsx", "__init__.py", "__main__.py",
    "app.py", "server.py", "cli.py", "src/main.rs", "src/main.go",
    "cmd/*/main.go",
]

# Test file indicators
_TEST_PATTERNS: list[str] = [
    "test_*.py", "*_test.py", "*_test.rs", "*_test.go",
    "*.test.ts", "*.test.tsx", "*.test.js", "*.test.jsx",
    "*.spec.ts", "*.spec.tsx", "*.spec.js", "*.spec.jsx",
    "test_*.rb", "*_spec.rb",
]


def _is_test_file(path: str) -> bool:
    """Check whether a file path looks like a test file."""
    name = os.path.basename(path)
    for pattern in _TEST_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return True
    # Also check if path contains test directories
    parts = Path(path).parts
    for part in parts[:-1]:
        if part in ("tests", "test", "spec", "__tests__"):
            return True
    return False


def _is_entry_point(path: str) -> bool:
    """Check whether a file path looks like an entry point."""
    name = os.path.basename(path)
    for pattern in _ENTRY_POINT_PATTERNS:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(path, pattern):
            return True
    return False


def _get_git_head(workspace: Path) -> Optional[str]:
    """Get the current git HEAD hash, or None if not a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=str(workspace),
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


# ── RepoMapEntry ──────────────────────────────────────────────────────


@dataclass
class RepoMapEntry:
    """A single file or symbol in the repo map."""

    path: str            # relative path from workspace root
    name: str            # symbol/file name
    kind: str            # "file", "module", "class", "function", "method", "struct", "interface"
    line: int            # starting line number (1-based)
    signature: str       # brief signature (e.g., "def login(user: User) -> Token")
    importance: float    # PageRank-style importance score (0.0-1.0)
    dependencies: list[str] = field(default_factory=list)  # other files/symbols this references
    summary: str = ""    # one-line summary of what it does

    def to_dict(self) -> dict:
        """Serialize to a plain dict (JSON-safe)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> RepoMapEntry:
        """Deserialize from a plain dict."""
        return cls(**data)


# ── RepoMap ────────────────────────────────────────────────────────────


class RepoMap:
    """Token-efficient, dependency-aware codebase index.

    Builds a map of the workspace that includes file outlines,
    import/dependency graphs, and PageRank-style importance scores.
    The formatted output is designed for injection into the LLM's
    system prompt.

    Usage::

        rm = RepoMap(Path("/path/to/project"))
        entries = rm.build()
        prompt_block = rm.format_for_llm()
        deps = rm.get_dependencies("src/auth.py")
    """

    def __init__(self, workspace: Path, max_entries: int = 200):
        """Index workspace codebase.

        Args:
            workspace: Root directory of the project.
            max_entries: Maximum number of entries in the map (limits
                         output size for the LLM prompt).
        """
        self.workspace = Path(workspace).resolve()
        self.max_entries = max_entries
        self._entries: list[RepoMapEntry] = []
        self._built: bool = False
        self._build_time_ms: float = 0.0
        # Dependency graph: file_path -> set of files it depends on
        self._deps: dict[str, set[str]] = {}
        # Reverse dependency graph: file_path -> set of files that depend on it
        self._rev_deps: dict[str, set[str]] = {}

    # ── Build ───────────────────────────────────────────────────────

    def build(self, use_cache: bool = True, fast_mode: bool = False) -> list[RepoMapEntry]:
        """Build or load cached repo map.

        Cache is stored at ``.wisp/repo_map.json`` inside the workspace.
        Invalidation checks: git HEAD changed, or any tracked file has
        an mtime newer than the cache.

        Args:
            use_cache: If True, attempt to load from cache first.
                       If False, force a full rebuild.
            fast_mode: If True and no cache exists, build a skeleton map
                       (file listing only, no symbol parsing). This is used
                       for the first system prompt to avoid blocking the user
                       for 5-10 seconds on large codebases. The skeleton is
                       cached and upgraded to full on the next build call.

        Returns:
            The list of RepoMapEntry objects.
        """
        start = time.perf_counter()

        if use_cache:
            cached = self._try_load_cache()
            if cached is not None:
                # Check if this is a skeleton cache that needs upgrading
                is_skeleton = self._cache_is_skeleton()
                if is_skeleton and not fast_mode:
                    logger.info("Upgrading skeleton cache to full build")
                    # Fall through to full build below
                else:
                    self._entries = cached
                    self._built = True
                    self._build_time_ms = (time.perf_counter() - start) * 1000
                    logger.info(
                        "Repo map loaded from cache: %d entries in %.1fms",
                        len(self._entries), self._build_time_ms,
                    )
                    return self._entries

        if fast_mode:
            self._entries = self._do_build_skeleton()
            self._built = True
            self._build_time_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "Repo map skeleton built: %d entries in %.1fms",
                len(self._entries), self._build_time_ms,
            )
            self._save_cache(skeleton=True)
            return self._entries

        self._entries = self._do_build()
        self._built = True
        self._build_time_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "Repo map built: %d entries in %.1fms",
            len(self._entries), self._build_time_ms,
        )
        self._save_cache(skeleton=False)
        return self._entries

    # ── Formatting ──────────────────────────────────────────────────

    def format_for_llm(self, max_tokens: int = 1500) -> str:
        """Format as a compact text block for injection into system prompt.

        Produces an Aider-style hierarchical tree with importance stars.
        Files are grouped by directory; the most important entries are
        listed first within each group.

        Example output::

            src/
            ├─ main.py (★★★)
            │  └─ class App
            │  └─ def main() -> None
            ├─ auth/
            │  ├─ login.py (★★★)
            │  │  └─ def authenticate(token: str) -> User
            │  │  └─ class Session
            │  └─ middleware.py (★★☆)
            │     └─ def verify_request(req: Request) -> bool
            tests/
            ├─ test_auth.py (★★☆)

        Args:
            max_tokens: Approximate token budget (characters / 4).

        Returns:
            A formatted string suitable for embedding in a system prompt.
        """
        if not self._entries:
            return ""

        max_chars = max_tokens * 4  # rough estimate

        # Group symbol entries by their file path
        symbol_entries: dict[str, list[RepoMapEntry]] = {}
        # Track all unique file paths
        all_file_paths: set[str] = set()

        for entry in self._entries:
            if entry.kind != "file":
                symbol_entries.setdefault(entry.path, []).append(entry)
            all_file_paths.add(entry.path)

        # Build directory hierarchy
        dir_files: dict[str, list[str]] = {}  # dir -> files

        for fpath in sorted(all_file_paths):
            parent = str(Path(fpath).parent)
            if parent == ".":
                parent = ""
            dir_files.setdefault(parent, []).append(fpath)

        # Get importance for a file path
        def _get_importance(fpath: str) -> float:
            for e in self._entries:
                if e.path == fpath and e.kind == "file":
                    return e.importance
            # Try to infer from symbol entries
            scores = [e.importance for e in self._entries if e.path == fpath]
            return max(scores) if scores else 0.1

        # Star rating
        def _stars(imp: float) -> str:
            if imp >= 0.5:
                return "★★★"  # 3 filled stars
            elif imp >= 0.3:
                return "★★☆"  # 2 filled, 1 empty
            elif imp >= 0.15:
                return "★☆☆"  # 1 filled, 2 empty
            else:
                return "☆☆☆"  # 3 empty stars

        lines: list[str] = []

        # Collect all top-level items (files and dirs at root)
        top_items: list[tuple[str, bool]] = []  # (name, is_dir)

        # Add root files
        root_files_sorted = sorted(
            dir_files.get("", []),
            key=lambda f: _get_importance(f),
            reverse=True
        )
        for f in root_files_sorted:
            top_items.append((f, False))

        # Add top-level dirs
        top_dirs_set: set[str] = set()
        for d in dir_files:
            if d:
                parts = Path(d).parts
                if parts:
                    top_dirs_set.add(parts[0])
        for d in sorted(top_dirs_set):
            top_items.append((d, True))

        def _render_tree(items: list[tuple[str, bool]], prefix: str) -> None:
            """Render a list of (name, is_dir) items with tree lines."""
            for idx, (name, is_dir) in enumerate(items):
                if len("".join(lines)) > max_chars:
                    return
                is_last = (idx == len(items) - 1)
                connector = "└" if is_last else "├"
                line_prefix = f"{prefix}{connector}── "

                if is_dir:
                    lines.append(f"{line_prefix}{name}/")
                    # Collect children
                    child_prefix = prefix + ("    " if is_last else "│   ")
                    child_items: list[tuple[str, bool]] = []

                    # Files in this dir
                    dir_files_list = sorted(
                        dir_files.get(name, []),
                        key=lambda f: _get_importance(f),
                        reverse=True,
                    )
                    for f in dir_files_list:
                        child_items.append((f, False))

                    # Subdirs
                    subdir_set: set[str] = set()
                    for d in dir_files:
                        if d and Path(d).parts[0] == name and len(Path(d).parts) > 1:
                            subdir_set.add(str(Path(*Path(d).parts[:2])))
                    for sd in sorted(subdir_set):
                        child_items.append((sd, True))

                    _render_tree(child_items, child_prefix)
                else:
                    fname = os.path.basename(name)
                    imp = _get_importance(name)
                    lines.append(f"{line_prefix}{fname} ({_stars(imp)})")

                    # Show symbols
                    syms = symbol_entries.get(name, [])
                    if syms and len("".join(lines)) < max_chars:
                        sym_prefix = prefix + ("    " if is_last else "│   ")
                        syms_sorted = sorted(syms, key=lambda s: s.importance, reverse=True)[:5]
                        for j, sym in enumerate(syms_sorted):
                            is_last_sym = (j == len(syms_sorted) - 1)
                            sym_conn = "└" if is_last_sym else "├"
                            kind_icon = _kind_icon(sym.kind)
                            sig = sym.signature if sym.signature else sym.name
                            lines.append(f"{sym_prefix}{sym_conn}── {kind_icon} {sig}")

        _render_tree(top_items, "")

        result = "\n".join(lines)
        # Truncate if needed
        if len(result) > max_chars:
            result = result[:max_chars] + "\n... (truncated)"
        return result

    # ── Queries ─────────────────────────────────────────────────────

    def get_relevant_files(self, query: str, top_k: int = 10) -> list[str]:
        """Find files most relevant to a query.

        Uses a combination of keyword matching and importance score.
        Query tokens are matched against file paths and symbol names.

        Args:
            query: Search query (space-separated keywords).
            top_k: Maximum number of results to return.

        Returns:
            List of file paths (relative to workspace), most relevant first.
        """
        if not self._entries:
            return []

        query_lower = query.lower()
        tokens = query_lower.split()

        scored: list[tuple[str, float]] = []

        # Score each unique file
        seen: set[str] = set()
        for entry in self._entries:
            fpath = entry.path
            if fpath in seen:
                continue
            seen.add(fpath)

            score = entry.importance * 0.3  # base importance weight

            path_lower = fpath.lower()
            # Check path match
            for token in tokens:
                if token in path_lower:
                    score += 0.5
                # Exact path component match
                if token == os.path.basename(fpath).lower().split(".")[0]:
                    score += 0.3

            # Check name match
            name_lower = entry.name.lower()
            for token in tokens:
                if token in name_lower:
                    score += 0.2

            scored.append((fpath, score))

        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)

        return [f for f, _ in scored[:top_k]]

    def get_dependencies(self, file_path: str) -> list[str]:
        """Get files that the given file depends on (imports/references).

        Args:
            file_path: Relative path from workspace root.

        Returns:
            Sorted list of file paths this file depends on.
        """
        deps = self._deps.get(file_path, set())
        return sorted(deps)

    def get_dependents(self, file_path: str) -> list[str]:
        """Get files that depend on the given file.

        Args:
            file_path: Relative path from workspace root.

        Returns:
            Sorted list of file paths that depend on this file.
        """
        rev = self._rev_deps.get(file_path, set())
        return sorted(rev)

    def _do_build_skeleton(self) -> list[RepoMapEntry]:
        """Fast skeleton build: file listing only, no parsing.

        Returns file entries with directory structure but no symbols.
        This takes ~100-200ms even on large codebases.
        """
        source_files = self._discover_files()
        entries: list[RepoMapEntry] = []

        # Group by directory
        dirs: dict[str, list[str]] = {}
        for fpath in source_files:
            parent = str(Path(fpath).parent)
            dirs.setdefault(parent, []).append(fpath)

        # Create file entries (no symbol parsing)
        for fpath in source_files:
            entries.append(RepoMapEntry(
                path=fpath,
                name=Path(fpath).name,
                kind="file",
                line=0,
                signature="",
                importance=0.5,  # neutral importance
            ))

        return entries[:self.max_entries]

    def _do_build(self) -> list[RepoMapEntry]:
        """Perform the full index build."""
        # Step 1: Discover source files
        source_files = self._discover_files()

        # Step 2: Parse each file — extract symbols, dependencies, signatures
        file_infos: dict[str, _FileInfo] = {}
        for fpath in source_files:
            info = _parse_file(self.workspace, fpath)
            file_infos[fpath] = info

        # Step 3: Build dependency graph
        self._build_dep_graph(file_infos)

        # Step 4: Compute PageRank importance
        importances = _compute_pagerank(
            list(file_infos.keys()),
            self._deps,
            self._rev_deps,
        )

        # Step 5: Assemble entries
        entries = _assemble_entries(file_infos, importances, self._deps, self.max_entries)

        return entries

    def _discover_files(self) -> list[str]:
        """Find all parsable source files in the workspace.

        Uses a single os.walk pass with early directory pruning instead of
        multiple rglob() calls. This avoids descending into node_modules,
        .venv, and other large irrelevant directories.
        """
        ws = self.workspace
        supported_exts = frozenset(_EXT_TO_LANG.keys())
        found: list[Path] = []

        # Single os.walk with early pruning — 100-200x faster than 19 rglob walks
        # because we skip node_modules/.venv/etc at the directory level.
        for root, dirs, files in os.walk(ws):
            # Prune hidden and irrelevant directories in-place before descending
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")  # skip hidden dirs (.git, .venv, etc.)
                and d not in _SKIP_DIRS   # skip known irrelevant dirs
            ]

            # Collect files matching our extensions
            for fname in files:
                ext = Path(fname).suffix.lower()
                if ext in supported_exts:
                    found.append(Path(root) / fname)

        # Filter: keep only files within workspace
        filtered: list[Path] = []
        for f in found:
            try:
                f.relative_to(ws)
            except ValueError:
                continue
            filtered.append(f)

        # Deduplicate and sort deterministically
        filtered = sorted(set(filtered))

        # Score and sort: prioritize primary source-code files
        def _lang_priority(ext: str) -> int:
            if ext in (".py", ".pyi"):
                return 0
            if ext in (".rs", ".go", ".rb"):
                return 1
            if ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
                return 2
            if ext in (".java", ".kt", ".kts"):
                return 3
            if ext in (".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hxx"):
                return 4
            if ext in (".swift",):
                return 5
            return 6

        def _dir_priority(parts: tuple[str, ...]) -> int:
            if not parts:
                return 0
            top = parts[0]
            if top in ("src", "lib", "app", "pkg", "internal"):
                return 0
            if top == ws.name:
                return 0
            if top not in (
                "tests", "test", "spec", "__tests__",
                "examples", "example", "demo", "sample",
                "docs", "doc",
                "scripts", "tools", "bin",
                "benchmarks", "bench", "perf",
                "vendor", "third_party", "third-party",
                "android", "ios", "mobile",
                "deploy", "deployment", "ci", "infra",
                "node_modules", "dist", "build",
                "migrations", "fixtures",
            ):
                if (ws / top / "__init__.py").exists():
                    return 1
                return 2
            if top in ("tests", "test", "spec", "__tests__"):
                return 3
            if top in ("examples", "example", "demo", "sample"):
                return 4
            if top in ("scripts", "tools", "bin"):
                return 5
            if top in ("docs", "doc", "benchmarks", "bench", "perf"):
                return 6
            return 7

        rel_paths = [str(f.relative_to(ws)) for f in filtered]
        rel_paths.sort(key=lambda p: (
            _lang_priority(Path(p).suffix.lower()),
            _dir_priority(Path(p).parts[:-1]),
            p,
        ))

        # Limit to max files
        max_files = _DEFAULT_MAX_FILES
        if len(rel_paths) > max_files:
            logger.warning(
                "Found %d source files, limiting to %d for performance",
                len(rel_paths), max_files,
            )
            rel_paths = rel_paths[:max_files]

        return rel_paths

    def _build_dep_graph(self, file_infos: dict[str, _FileInfo]) -> None:
        """Construct forward and reverse dependency graphs."""
        self._deps = {}
        self._rev_deps = {}

        # Build file path -> module path mapping for resolution
        module_to_file: dict[str, str] = {}
        for fpath in file_infos:
            # Map module paths to file paths
            for mod_path in _generate_module_names(fpath):
                module_to_file[mod_path] = fpath
                module_to_file[mod_path.replace("/", ".")] = fpath

        for fpath, info in file_infos.items():
            resolved: set[str] = set()
            for dep in info.dependency_names:
                if dep in module_to_file and module_to_file[dep] != fpath:
                    resolved.add(module_to_file[dep])
                    continue
                # Try resolving as a relative path
                dep_path = _resolve_dep_path(self.workspace, fpath, dep)
                if dep_path and dep_path in file_infos and dep_path != fpath:
                    resolved.add(dep_path)

            self._deps[fpath] = resolved
            for target in resolved:
                self._rev_deps.setdefault(target, set()).add(fpath)

        # Ensure every file has an entry in _rev_deps
        for fpath in file_infos:
            self._deps.setdefault(fpath, set())
            self._rev_deps.setdefault(fpath, set())

    # ── Internal: Caching ───────────────────────────────────────────

    def _cache_path(self) -> Path:
        """Return the path to the cache file."""
        return self.workspace / ".wisp" / "repo_map.json"

    def _try_load_cache(self) -> Optional[list[RepoMapEntry]]:
        """Try to load and validate the cache. Returns None if invalid."""
        cache_path = self._cache_path()
        if not cache_path.exists():
            return None

        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.debug("Cache file corrupt or unreadable, rebuilding")
            return None

        meta = data.get("_meta")
        if not meta:
            return None

        # Check git HEAD
        current_head = _get_git_head(self.workspace)
        cached_head = meta.get("git_hash")
        if current_head and cached_head and current_head != cached_head:
            logger.debug("Cache invalidated: git HEAD changed")
            return None

        # Check if any tracked file is newer than cache
        cache_ts = meta.get("timestamp", 0)
        cached_files = set(meta.get("files", []))

        for fpath in list(cached_files)[:50]:  # Sample check for speed
            full_path = self.workspace / fpath
            try:
                mtime = full_path.stat().st_mtime
                if mtime > cache_ts:
                    logger.debug("Cache invalidated: %s modified after cache", fpath)
                    return None
            except OSError:
                logger.debug("Cache invalidated: %s no longer exists", fpath)
                return None

        entries: list[RepoMapEntry] = []
        for edata in data.get("entries", []):
            entries.append(RepoMapEntry.from_dict(edata))

        # Restore dependency graphs from serialized entries
        self._deps = {}
        self._rev_deps = {}
        for e in entries:
            if e.kind == "file":
                deps = set(e.dependencies)
                self._deps[e.path] = deps
                for d in deps:
                    self._rev_deps.setdefault(d, set()).add(e.path)
                self._deps.setdefault(e.path, set())
                self._rev_deps.setdefault(e.path, set())

        logger.debug("Cache valid, loaded %d entries", len(entries))
        return entries

    def _save_cache(self, skeleton: bool = False) -> None:
        """Persist the current map to cache."""
        cache_path = self._cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        file_entries = [e for e in self._entries if e.kind == "file"]
        meta = {
            "timestamp": time.time(),
            "git_hash": _get_git_head(self.workspace),
            "file_count": len(file_entries),
            "entry_count": len(self._entries),
            "cache_mtime": datetime.now(timezone.utc).isoformat(),
            "files": [e.path for e in file_entries],
            "skeleton": skeleton,
        }

        data = {
            "_meta": meta,
            "entries": [e.to_dict() for e in self._entries],
        }

        try:
            cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            logger.debug("Cache saved: %d entries (skeleton=%s)", len(self._entries), skeleton)
        except OSError as e:
            logger.warning("Failed to save cache: %s", e)

    def _cache_is_skeleton(self) -> bool:
        """Check if the on-disk cache was created from a skeleton build."""
        cache_path = self._cache_path()
        if not cache_path.exists():
            return False
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            return data.get("_meta", {}).get("skeleton", False)
        except Exception:
            return False


# ── Internal: File parsing ──────────────────────────────────────────────


@dataclass
class _FileInfo:
    """Internal: extracted info for a single source file."""

    path: str
    language: str
    symbols: list[_SymbolInfo] = field(default_factory=list)
    dependency_names: list[str] = field(default_factory=list)  # raw import strings
    is_test: bool = False
    is_entry: bool = False
    mtime: float = 0.0
    file_size: int = 0


@dataclass
class _SymbolInfo:
    """Internal: extracted info for a single symbol."""

    name: str
    kind: str
    line: int
    signature: str
    parent: Optional[str] = None


def _parse_file(workspace: Path, rel_path: str) -> _FileInfo:
    """Parse a single source file, extracting symbols and dependencies.

    Tries tree-sitter first (accurate), falls back to regex.
    Files over the line limit are skipped without being fully read.
    """
    full_path = workspace / rel_path
    ext = full_path.suffix.lower()
    language = _EXT_TO_LANG.get(ext, "Unknown")

    info = _FileInfo(
        path=rel_path,
        language=language,
        is_test=_is_test_file(rel_path),
        is_entry=_is_entry_point(rel_path),
    )

    # Get file metadata — check size before reading
    try:
        st = full_path.stat()
        info.mtime = st.st_mtime
        info.file_size = st.st_size
    except OSError:
        return info

    # Skip large files by line count without reading the whole file first.
    # Approximate: average line is ~50 bytes; conservative check.
    if st.st_size > _DEFAULT_MAX_FILE_LINES * 100:
        try:
            with open(full_path, "rb") as fh:
                lines = 0
                for _ in fh:
                    lines += 1
                    if lines > _DEFAULT_MAX_FILE_LINES:
                        logger.debug("Skipping large file %s (%d+ lines)", rel_path, lines)
                        return info
        except OSError:
            return info

    # Read file content
    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return info

    lines = content.splitlines()
    if len(lines) > _DEFAULT_MAX_FILE_LINES:
        logger.debug("Skipping large file %s (%d lines)", rel_path, len(lines))
        return info

    # Try tree-sitter for symbol extraction
    symbols = _extract_symbols_ts(content, language, rel_path)
    if not symbols:
        # Fall back to regex
        symbols = _extract_symbols_regex(lines, language, rel_path)

    info.symbols = symbols

    # Extract dependencies (imports)
    info.dependency_names = _extract_dependencies(lines, language, rel_path, workspace)

    return info


def _extract_symbols_ts(content: str, language: str, rel_path: str) -> list[_SymbolInfo]:
    """Extract symbols using tree-sitter, if available.

    Parsers are cached per language to avoid re-creation overhead
    (which dominates for small files).
    """
    try:
        from wisp.tree_sitter_index import (
            is_tree_sitter_available,
            _LANGUAGE_PARSERS,
            _TS_QUERIES,
            _EXT_TO_TS_LANG,
        )
    except ImportError:
        return []

    if not is_tree_sitter_available():
        return []

    ext = Path(rel_path).suffix.lower()
    ts_lang = _EXT_TO_TS_LANG.get(ext)
    if not ts_lang:
        return []

    parser_lang = _LANGUAGE_PARSERS.get(ts_lang)
    if not parser_lang:
        return []

    query_str = _TS_QUERIES.get(ts_lang, "")
    if not query_str:
        return []

    try:
        import tree_sitter as ts_module
    except ImportError:
        return []

    # Cache parsers per language — creation is expensive (~20-50ms each)
    cache_key = id(ts_module.Parser)
    with _PARSER_LOCK:
        cache = _PARSER_CACHE.get(cache_key)
        if cache is None:
            cache = {}
            _PARSER_CACHE[cache_key] = cache

    ts_parser = cache.get(ts_lang)
    if ts_parser is None:
        try:
            ts_parser = ts_module.Parser(language=parser_lang)
            with _PARSER_LOCK:
                cache[ts_lang] = ts_parser
        except Exception as e:
            logger.debug("Failed to create tree-sitter parser for %s: %s", ts_lang, e)
            return []

    try:
        tree = ts_parser.parse(bytes(content, "utf-8"))
    except Exception as e:
        logger.debug("Tree-sitter parse failed for %s: %s", rel_path, e)
        return []

    try:
        query = ts_module.Query(parser_lang, query_str)
        cursor = ts_module.QueryCursor(query)
        captures = cursor.captures(tree.root_node)
    except Exception as e:
        logger.debug("Tree-sitter query failed for %s: %s", rel_path, e)
        return []

    symbols: list[_SymbolInfo] = []
    for cap_name, nodes in captures.items():
        for node in nodes:
            if cap_name == "name":
                continue
            kind = cap_name
            # Find name child
            name_node = None
            for child in node.children:
                if child.type in ("identifier", "type_identifier", "property_identifier"):
                    name_node = child
                    break
            if name_node is None:
                continue
            name = name_node.text.decode("utf-8")
            # Build a brief signature
            sig = _build_signature_from_text(name, kind, content, node.start_point[0])
            symbols.append(_SymbolInfo(
                name=name,
                kind=kind,
                line=node.start_point[0] + 1,
                signature=sig,
            ))

    return symbols


def _extract_symbols_regex(
    lines: list[str], language: str, rel_path: str
) -> list[_SymbolInfo]:
    """Fallback: extract symbols using regex patterns."""
    if language in ("Python",):
        return _regex_python(lines, rel_path)
    elif language in ("Rust",):
        return _regex_rust(lines, rel_path)
    elif language in ("JavaScript", "TypeScript"):
        return _regex_javascript(lines, rel_path, language)
    elif language in ("Go",):
        return _regex_go(lines, rel_path)
    elif language in ("Ruby",):
        return _regex_ruby(lines, rel_path)
    elif language in ("Java", "Kotlin"):
        return _regex_java_kotlin(lines, rel_path, language)
    elif language in ("C", "C++"):
        return _regex_c(lines, rel_path, language)
    return []


def _build_signature_from_text(name: str, kind: str, text: str, line_num: int) -> str:
    """Build a brief signature from the first line of a definition."""
    lines = text.splitlines()
    if line_num < len(lines):
        raw = lines[line_num].strip()
        # Truncate for compactness
        if len(raw) > 100:
            raw = raw[:97] + "..."
        return raw
    return name


def _extract_dependencies(
    lines: list[str], language: str, rel_path: str, workspace: Path
) -> list[str]:
    """Extract import/dependency statements from source lines.

    Returns a list of raw import strings (module paths, file paths, etc.).
    """
    deps: list[str] = []

    if language == "Python":
        deps = _deps_python(lines)
    elif language == "Rust":
        deps = _deps_rust(lines)
    elif language in ("JavaScript", "TypeScript"):
        deps = _deps_javascript(lines)
    elif language == "Go":
        deps = _deps_go(lines)
    elif language == "Ruby":
        deps = _deps_ruby(lines)
    elif language in ("Java", "Kotlin"):
        deps = _deps_java(lines)
    elif language in ("C", "C++"):
        deps = _deps_c(lines)

    return deps


# ── Regex-based symbol extractors ──────────────────────────────────────


def _regex_python(lines: list[str], rel_path: str) -> list[_SymbolInfo]:
    symbols: list[_SymbolInfo] = []
    current_class: Optional[str] = None

    for i, line in enumerate(lines):
        stripped = line.strip()

        m = re.match(r'^class\s+(\w+)', stripped)
        if m:
            name = m.group(1)
            current_class = name
            # Extract signature (class bases)
            sig_match = re.match(r'^class\s+\w+\s*(\(.*?\))?\s*:', stripped)
            sig = sig_match.group(0).rstrip(":") if sig_match else f"class {name}"
            if len(sig) > 80:
                sig = sig[:77] + "..."
            symbols.append(_SymbolInfo(name=name, kind="class", line=i + 1, signature=sig))
            continue

        m = re.match(r'^(?:async\s+)?def\s+(\w+)\s*\(', stripped)
        if m:
            name = m.group(1)
            if name.startswith("_") and not (name.startswith("__") and name.endswith("__")):
                if not current_class:
                    continue
            kind = "method" if current_class else "function"
            # Build signature
            sig_match = re.match(r'^(\s*(?:async\s+)?def\s+\w+\s*\(.*?\)(?:\s*->.*?)?\s*:)', line)
            sig = sig_match.group(1).rstrip(":") if sig_match else f"def {name}(...)"
            if len(sig) > 100:
                sig = sig[:97] + "..."
            symbols.append(_SymbolInfo(
                name=name, kind=kind, line=i + 1, signature=sig,
                parent=current_class,
            ))
            continue

        # Class context tracking
        if current_class:
            if not line or line[0] not in (" ", "\t"):
                if not line.startswith(("def ", "class ", "async ", "@", "#", ")")):
                    current_class = None
                elif line.startswith(("def ", "async ")):
                    current_class = None

    return symbols


def _regex_rust(lines: list[str], rel_path: str) -> list[_SymbolInfo]:
    symbols: list[_SymbolInfo] = []
    current_impl: Optional[str] = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("#!") or stripped.startswith("/*"):
            continue

        m = re.match(r'^(?:pub\s+)?(?:unsafe\s+)?fn\s+(\w+)', stripped)
        if m:
            name = m.group(1)
            kind = "method" if current_impl else "function"
            sig_match = re.match(r'^((?:pub\s+)?(?:unsafe\s+)?fn\s+\w+\s*\([^)]*\)(?:\s*->.*?)?\s*\{?)', stripped)
            sig = sig_match.group(1).rstrip("{") if sig_match else f"fn {name}(...)"
            if len(sig) > 100:
                sig = sig[:97] + "..."
            symbols.append(_SymbolInfo(
                name=name, kind=kind, line=i + 1, signature=sig,
                parent=current_impl,
            ))
            continue

        m = re.match(r'^(?:pub\s+)?struct\s+(\w+)', stripped)
        if m:
            symbols.append(_SymbolInfo(name=m.group(1), kind="struct", line=i + 1, signature=f"struct {m.group(1)}"))
            continue

        m = re.match(r'^(?:pub\s+)?enum\s+(\w+)', stripped)
        if m:
            symbols.append(_SymbolInfo(name=m.group(1), kind="enum", line=i + 1, signature=f"enum {m.group(1)}"))
            continue

        m = re.match(r'^(?:pub\s+)?(?:unsafe\s+)?trait\s+(\w+)', stripped)
        if m:
            symbols.append(_SymbolInfo(name=m.group(1), kind="trait", line=i + 1, signature=f"trait {m.group(1)}"))
            continue

        m = re.match(r'^(?:pub\s+)?(?:unsafe\s+)?impl\s+(.+?)(?:\s+for\s+(.+?))?\s*\{?$', stripped)
        if m:
            impl_for = m.group(1).strip()
            impl_target = m.group(2).strip() if m.group(2) else None
            current_impl = impl_target or impl_for
            symbols.append(_SymbolInfo(
                name=f"impl {impl_for}" + (f" for {impl_target}" if impl_target else ""),
                kind="impl", line=i + 1,
                signature=stripped.rstrip("{"),
            ))
            continue

        m = re.match(r'^(?:pub\s+)?type\s+(\w+)', stripped)
        if m:
            symbols.append(_SymbolInfo(name=m.group(1), kind="type", line=i + 1, signature=f"type {m.group(1)}"))
            continue

        m = re.match(r'^(?:pub\s+)?const\s+(\w+)', stripped)
        if m:
            symbols.append(_SymbolInfo(name=m.group(1), kind="const", line=i + 1, signature=f"const {m.group(1)}"))
            continue

        if current_impl and (stripped == "}" or (stripped and not stripped.startswith((" ", "\t", "#", "//")))):
            current_impl = None

    return symbols


def _regex_javascript(lines: list[str], rel_path: str, language: str) -> list[_SymbolInfo]:
    symbols: list[_SymbolInfo] = []
    current_class: Optional[str] = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
            continue

        m = re.match(r'^(?:export\s+)?(?:default\s+)?class\s+(\w+)', stripped)
        if m:
            current_class = m.group(1)
            symbols.append(_SymbolInfo(name=current_class, kind="class", line=i + 1, signature=f"class {current_class}"))
            continue

        m = re.match(r'^(?:export\s+)?(?:async\s+)?function\s+(?:\*\s+)?(\w+)', stripped)
        if m:
            name = m.group(1)
            kind = "method" if current_class else "function"
            sig = stripped.split("{")[0].strip() if "{" in stripped else stripped
            symbols.append(_SymbolInfo(
                name=name, kind=kind, line=i + 1, signature=sig,
                parent=current_class,
            ))
            continue

        m = re.match(r'^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\(|\w+\s*=>)', stripped)
        if m:
            sig = stripped.split("=")[0].strip()
            symbols.append(_SymbolInfo(name=m.group(1), kind="function", line=i + 1, signature=sig))
            continue

        if current_class:
            m = re.match(r'^(?:async\s+)?(\w+)\s*\(', stripped)
            if m and not stripped.startswith(("if", "for", "while", "switch", "catch", "return")):
                sig = stripped.split("{")[0].strip() if "{" in stripped else stripped
                symbols.append(_SymbolInfo(
                    name=m.group(1), kind="method", line=i + 1,
                    signature=sig, parent=current_class,
                ))
                continue

        if language == "TypeScript":
            m = re.match(r'^(?:export\s+)?interface\s+(\w+)', stripped)
            if m:
                symbols.append(_SymbolInfo(name=m.group(1), kind="interface", line=i + 1, signature=f"interface {m.group(1)}"))
                continue
            m = re.match(r'^(?:export\s+)?type\s+(\w+)\s*=', stripped)
            if m:
                symbols.append(_SymbolInfo(name=m.group(1), kind="type", line=i + 1, signature=f"type {m.group(1)}"))
                continue
            m = re.match(r'^(?:export\s+)?enum\s+(\w+)', stripped)
            if m:
                symbols.append(_SymbolInfo(name=m.group(1), kind="enum", line=i + 1, signature=f"enum {m.group(1)}"))
                continue

        if current_class and line.strip() == "}" and not line.startswith((" ", "\t")):
            current_class = None

    return symbols


def _regex_go(lines: list[str], rel_path: str) -> list[_SymbolInfo]:
    symbols: list[_SymbolInfo] = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        m = re.match(r'^func\s+(?:\([^)]*\)\s+)?(\w+)', stripped)
        if m:
            name = m.group(1)
            has_receiver = re.match(r'^func\s+\(', stripped) is not None
            kind = "method" if has_receiver else "function"
            sig = stripped.split("{")[0].strip() if "{" in stripped else stripped
            symbols.append(_SymbolInfo(name=name, kind=kind, line=i + 1, signature=sig))
            continue

        m = re.match(r'^type\s+(\w+)\s+struct', stripped)
        if m:
            symbols.append(_SymbolInfo(name=m.group(1), kind="struct", line=i + 1, signature=f"type {m.group(1)} struct"))
            continue

        m = re.match(r'^type\s+(\w+)\s+interface', stripped)
        if m:
            symbols.append(_SymbolInfo(name=m.group(1), kind="interface", line=i + 1, signature=f"type {m.group(1)} interface"))
            continue

        m = re.match(r'^type\s+(\w+)\s+', stripped)
        if m and "struct" not in stripped and "interface" not in stripped:
            symbols.append(_SymbolInfo(name=m.group(1), kind="type", line=i + 1, signature=stripped.rstrip("{")))
            continue

    return symbols


def _regex_ruby(lines: list[str], rel_path: str) -> list[_SymbolInfo]:
    symbols: list[_SymbolInfo] = []
    current_class: Optional[str] = None
    current_module: Optional[str] = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        m = re.match(r'^(?:class\s+<<\s+)?module\s+(\w+)', stripped)
        if m:
            current_module = m.group(1)
            symbols.append(_SymbolInfo(name=current_module, kind="module", line=i + 1, signature=f"module {current_module}"))
            continue

        m = re.match(r'^class\s+(\w+)', stripped)
        if m:
            current_class = m.group(1)
            symbols.append(_SymbolInfo(name=current_class, kind="class", line=i + 1, signature=f"class {current_class}"))
            continue

        m = re.match(r'^def\s+(?:self\.)?(\w+(?:[?!])?)', stripped)
        if m and m.group(1):
            name = m.group(1)
            kind = "method"
            parent = current_class or current_module
            sig = stripped.split("def ")[1] if "def " in stripped else stripped
            symbols.append(_SymbolInfo(
                name=name, kind=kind, line=i + 1, signature=sig, parent=parent,
            ))
            continue

        if stripped == "end":
            current_class = None
            current_module = None

    return symbols


def _regex_java_kotlin(lines: list[str], rel_path: str, language: str) -> list[_SymbolInfo]:
    """Extract Java/Kotlin symbols."""
    symbols: list[_SymbolInfo] = []
    current_class: Optional[str] = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
            continue

        if language == "Kotlin":
            m = re.match(r'^(?:open\s+|abstract\s+|data\s+|sealed\s+)?class\s+(\w+)', stripped)
            if m:
                current_class = m.group(1)
                symbols.append(_SymbolInfo(name=current_class, kind="class", line=i + 1, signature=f"class {current_class}"))
                continue
            m = re.match(r'^(?:open\s+)?(?:suspend\s+)?fun\s+(\w+)', stripped)
            if m:
                name = m.group(1)
                kind = "method" if current_class else "function"
                sig = stripped.split("{")[0].strip() if "{" in stripped else stripped
                symbols.append(_SymbolInfo(name=name, kind=kind, line=i + 1, signature=sig, parent=current_class))
                continue
        else:
            # Java
            m = re.match(
                r'^(?:public\s+|private\s+|protected\s+|static\s+|final\s+|abstract\s+)*class\s+(\w+)',
                stripped,
            )
            if m:
                current_class = m.group(1)
                symbols.append(_SymbolInfo(name=current_class, kind="class", line=i + 1, signature=f"class {current_class}"))
                continue
            m = re.match(
                r'^(?:public\s+|private\s+|protected\s+|static\s+|final\s+|abstract\s+|synchronized\s+)*(?:void\s+|[<>\[\]\w]+\s+)(\w+)\s*\(',
                stripped,
            )
            if m and not stripped.startswith(("if", "for", "while", "try", "switch", "catch")):
                name = m.group(1)
                kind = "method" if current_class else "function"
                sig = stripped.split("{")[0].strip() if "{" in stripped else stripped
                symbols.append(_SymbolInfo(name=name, kind=kind, line=i + 1, signature=sig, parent=current_class))
                continue

        # Track brace depth loosely for class context
    return symbols


def _regex_c(lines: list[str], rel_path: str, language: str) -> list[_SymbolInfo]:
    """Extract C/C++ function definitions."""
    symbols: list[_SymbolInfo] = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
            continue

        # Function definition: return_type name(...) {
        m = re.match(
            r'^(?:static\s+|inline\s+|virtual\s+|extern\s+|const\s+)*(?:[\w:*&<>\s]+)\s+(\w+)\s*\([^)]*\)\s*(?:const\s*)?\{?',
            stripped,
        )
        if m and not stripped.startswith(("if", "for", "while", "switch", "return", "typedef", "using")):
            name = m.group(1)
            # Skip keywords that look like function names
            if name in ("if", "for", "while", "switch", "return", "sizeof", "typeof", "catch"):
                continue
            sig = stripped.split("{")[0].strip() if "{" in stripped else stripped
            symbols.append(_SymbolInfo(name=name, kind="function", line=i + 1, signature=sig))
            continue

        # Class/struct
        m = re.match(r'^(?:class|struct)\s+(\w+)', stripped)
        if m and not stripped.endswith(";"):
            symbols.append(_SymbolInfo(
                name=m.group(1), kind=m.group(0).split()[0],
                line=i + 1, signature=f"{m.group(0).split()[0]} {m.group(1)}",
            ))
            continue

    return symbols


# ── Regex-based dependency extractors ───────────────────────────────────


def _deps_python(lines: list[str]) -> list[str]:
    """Extract Python imports."""
    deps: list[str] = []
    for line in lines:
        stripped = line.strip()
        # import X, import X.Y, import X.Y as Z
        m = re.match(r'^import\s+([\w.]+)', stripped)
        if m:
            deps.append(m.group(1))
            continue
        # from X import Y
        m = re.match(r'^from\s+([\w.]+)\s+import', stripped)
        if m:
            deps.append(m.group(1))
            continue
    return deps


def _deps_rust(lines: list[str]) -> list[str]:
    """Extract Rust use / extern crate statements."""
    deps: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        # use crate::X, use X::Y, use super::X
        m = re.match(r'^use\s+([\w:]+)', stripped)
        if m:
            deps.append(m.group(1))
            continue
        # extern crate X
        m = re.match(r'^extern\s+crate\s+(\w+)', stripped)
        if m:
            deps.append(m.group(1))
            continue
    return deps


def _deps_javascript(lines: list[str]) -> list[str]:
    """Extract JS/TS imports and require statements."""
    deps: list[str] = []
    for line in lines:
        stripped = line.strip()
        # import X from 'Y'
        m = re.search(r"""from\s+['"]([^'"]+)['"]""", stripped)
        if m:
            dep = m.group(1)
            if not dep.startswith("."):
                deps.append(dep.split("/")[0])  # package name
            else:
                deps.append(dep)  # relative path
            continue
        # import 'Y'
        m = re.search(r"""import\s+['"]([^'"]+)['"]""", stripped)
        if m:
            dep = m.group(1)
            deps.append(dep)
            continue
        # require('Y')
        m = re.search(r"""require\s*\(\s*['"]([^'"]+)['"]""", stripped)
        if m:
            dep = m.group(1)
            deps.append(dep)
            continue
    return deps


def _deps_go(lines: list[str]) -> list[str]:
    """Extract Go import blocks."""
    deps: list[str] = []
    in_import = False
    for line in lines:
        stripped = line.strip()
        if stripped == "import (":
            in_import = True
            continue
        if in_import:
            if stripped == ")":
                in_import = False
                continue
            m = re.match(r'^(?:\w+\s+)?["\']([^"\']+)["\']', stripped)
            if m:
                deps.append(m.group(1))
            continue
        # Single-line import
        m = re.match(r'^import\s+(?:\w+\s+)?["\']([^"\']+)["\']', stripped)
        if m:
            deps.append(m.group(1))
    return deps


def _deps_ruby(lines: list[str]) -> list[str]:
    """Extract Ruby require / require_relative."""
    deps: list[str] = []
    for line in lines:
        stripped = line.strip()
        m = re.match(r'^(?:require|require_relative|load)\s+[\'"]([^\'"]+)[\'"]', stripped)
        if m:
            deps.append(m.group(1))
    return deps


def _deps_java(lines: list[str]) -> list[str]:
    """Extract Java/Kotlin import statements."""
    deps: list[str] = []
    for line in lines:
        stripped = line.strip()
        m = re.match(r'^import\s+([\w.]+(?:\*\s*)?)', stripped)
        if m:
            deps.append(m.group(1).rstrip("*").rstrip("."))
            continue
    return deps


def _deps_c(lines: list[str]) -> list[str]:
    """Extract C/C++ #include directives."""
    deps: list[str] = []
    for line in lines:
        stripped = line.strip()
        m = re.match(r'^#include\s+[<"]([^>"]+)[>"]', stripped)
        if m:
            deps.append(m.group(1))
    return deps


# ── Module name resolution ──────────────────────────────────────────────


def _generate_module_names(file_path: str) -> list[str]:
    """Generate possible module names that could map to a given file path.

    E.g., ``src/auth/login.py`` could be imported as ``src.auth.login``
    or ``auth.login``.
    """
    names: list[str] = []
    path = Path(file_path)
    ext = path.suffix.lower()

    # Python: strip .py and convert / to .
    if ext in (".py", ".pyi"):
        # Full path without extension
        name = file_path.replace("/", ".").replace("\\", ".")
        if ext:
            name = name[: -len(ext)]
        names.append(name)
        # Path without src/
        parts = name.split(".")
        for start in range(len(parts) - 1):
            names.append(".".join(parts[start:]))

    # Rust: crate::module
    elif ext == ".rs":
        name = file_path.replace("/", "::").replace("\\", "::")
        name = name[: -len(".rs")]
        names.append(name)
        names.append(f"crate::{name}")

    # Go: module/path
    elif ext == ".go":
        name = file_path.replace("/", "/").replace("\\", "/")
        name = name[: -len(".go")]
        names.append(name)

    # JS/TS: relative paths
    elif ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
        names.append(file_path)
        name_no_ext = file_path[: -len(ext)]
        names.append(name_no_ext)

    return names


def _resolve_dep_path(workspace: Path, from_file: str, dep_str: str) -> Optional[str]:
    """Try to resolve a dependency string to a relative file path in the workspace.

    Args:
        workspace: Workspace root.
        from_file: The file that has the dependency (relative path).
        dep_str: Raw dependency string (e.g., 'src.auth.login', './utils', '@scope/pkg').

    Returns:
        Resolved relative file path, or None if it's an external dependency.
    """
    # External package indicators
    if not dep_str:
        return None
    if dep_str.startswith("@") and "/" in dep_str:
        # Scoped npm package, skip
        return None

    # Python: dotted module path
    if "." in dep_str and not dep_str.startswith("."):
        parts = dep_str.split(".")
        candidate = str(Path(*parts) / "__init__.py")
        full = workspace / candidate
        if full.exists():
            return candidate
        candidate = str(Path(*parts)) + ".py"
        full = workspace / candidate
        if full.exists():
            return candidate

    # Rust: crate::module::path
    if "::" in dep_str:
        dep_str_parts = dep_str.replace("crate::", "").replace("self::", "").replace("super::", "")
        parts = dep_str_parts.split("::")
        candidate = str(Path(*parts)) + ".rs"
        full = workspace / candidate
        if full.exists():
            return candidate
        candidate = str(Path("src") / Path(*parts)) + ".rs"
        full = workspace / candidate
        if full.exists():
            return candidate

    # JS/TS relative import
    if dep_str.startswith("."):
        from_dir = Path(from_file).parent
        resolved = str((from_dir / dep_str).resolve())
        try:
            rel = Path(resolved).relative_to(workspace)
        except ValueError:
            return None
        # Try common extensions
        for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs"):
            candidate = str(rel) + ext
            if (workspace / candidate).exists():
                return candidate
        # Try index files
        for ext in (".ts", ".tsx", ".js", ".jsx"):
            candidate = str(rel / f"index{ext}")
            if (workspace / candidate).exists():
                return candidate
        return str(rel) + ".ts"  # best guess

    # Go import path: try to match against known files
    if "/" in dep_str:
        candidate = dep_str + ".go"
        full = workspace / candidate
        if full.exists():
            return candidate

    # Simple filename match (e.g., "utils" -> utils.py, utils.rs, utils.go)
    for ext in _EXT_TO_LANG:
        candidate = dep_str + ext
        full = workspace / candidate
        if full.exists():
            return candidate
        candidate = f"src/{dep_str}{ext}"
        full = workspace / candidate
        if full.exists():
            return candidate

    return None


# ── PageRank ─────────────────────────────────────────────────────────────


def _compute_pagerank(
    files: list[str],
    deps: dict[str, set[str]],
    rev_deps: dict[str, set[str]],
    damping: float = 0.85,
    iterations: int = 30,
) -> dict[str, float]:
    """Compute PageRank importance scores for files.

    Builds a directed graph where edge A->B means file A imports/references
    file B. Standard PageRank with damping factor.

    When the dependency graph is sparse (few resolved edges), falls back to
    a heuristic that assigns importance based on:
    - Entry-point files: high boost
    - Files in key directories (src/, lib/, app/): moderate boost
    - __init__.py files: structural boost
    - Test files: moderate importance floor
    - Leaf files in shallow paths: slightly lower

    Args:
        files: List of all file paths.
        deps: Forward dependency graph (file -> set of files it depends on).
        rev_deps: Reverse dependency graph (file -> set of files depending on it).
        damping: PageRank damping factor (default 0.85).
        iterations: Number of PageRank iterations.

    Returns:
        Mapping of file path to importance score (0.0-1.0 range).
    """
    n = len(files)
    if n == 0:
        return {}
    if n == 1:
        return {files[0]: 1.0}

    file_to_idx = {f: i for i, f in enumerate(files)}

    # Count total edges to decide PageRank vs heuristic
    total_edges = sum(len(d) for d in deps.values())
    edge_ratio = total_edges / n if n > 0 else 0

    # Precompute out-degree and adjacency
    # Edge: if file i depends on file j, then j receives importance from i
    out_edges: list[list[int]] = [[] for _ in range(n)]
    in_edges: list[list[int]] = [[] for _ in range(n)]
    for i, f in enumerate(files):
        for dep in deps.get(f, set()):
            if dep in file_to_idx:
                j = file_to_idx[dep]
                out_edges[i].append(j)
                in_edges[j].append(i)

    if edge_ratio >= 0.05:
        # Enough edges for meaningful PageRank; use personalized initial
        # scores that favor entry points and structural files.
        initial = [0.0] * n
        for i, f in enumerate(files):
            base = 1.0 / n
            if _is_entry_point(f):
                base *= 3.0
            elif Path(f).name == "__init__.py":
                base *= 1.5
            initial[i] = base
        total_init = sum(initial)
        scores = [v / total_init for v in initial]

        for _ in range(iterations):
            new_scores = [(1.0 - damping) / n] * n

            for i in range(n):
                out = out_edges[i]
                if out:
                    share = damping * scores[i] / len(out)
                    for j in out:
                        new_scores[j] += share
                else:
                    # Dangling node: distribute proportionally to initial
                    for j in range(n):
                        new_scores[j] += damping * scores[i] * initial[j] / total_init

            scores = new_scores

        # Normalize to 0.0-1.0
        max_score = max(scores) if max(scores) > 0 else 1.0
        score_map = {f: scores[i] / max_score for i, f in enumerate(files)}
    else:
        # Sparse graph: use heuristic scoring based on file role
        score_map = {}
        for f in files:
            name = Path(f).name
            parts = Path(f).parts

            # Base score
            score = 0.3

            # Entry points are most important
            if _is_entry_point(f):
                score = 0.9
            elif _is_test_file(f):
                score = 0.2
            elif name == "__init__.py":
                # __init__.py in deep packages are more important
                depth = len(parts) - 1
                score = 0.35 + min(depth * 0.05, 0.25)
            else:
                # Source directories get a boost
                if parts and parts[0] in ("src", "lib", "app"):
                    score += 0.1
                # Python/Rust/Go source files
                ext = Path(f).suffix.lower()
                if ext in (".py", ".pyi", ".rs", ".go"):
                    score += 0.05
                # Deep nesting = slightly lower visibility
                depth = len(parts)
                if depth > 3:
                    score -= min((depth - 3) * 0.04, 0.2)

            score_map[f] = score

        # Normalize to 0.0-1.0 range
        if score_map:
            max_val = max(score_map.values())
            if max_val > 0:
                for f in score_map:
                    score_map[f] = score_map[f] / max_val

    # Apply final boosts/corrections
    for f in files:
        if _is_entry_point(f):
            score_map[f] = min(1.0, max(score_map.get(f, 0.5), 0.85))
        elif _is_test_file(f):
            score_map[f] = max(0.12, min(0.3, score_map.get(f, 0.15)))

    return score_map


# ── Entry assembly ──────────────────────────────────────────────────────


def _assemble_entries(
    file_infos: dict[str, _FileInfo],
    importances: dict[str, float],
    deps: dict[str, set[str]],
    max_entries: int,
) -> list[RepoMapEntry]:
    """Assemble RepoMapEntry objects from file infos and importance scores."""
    entries: list[RepoMapEntry] = []

    for fpath, info in file_infos.items():
        imp = importances.get(fpath, 0.1)
        deps_list = sorted(deps.get(fpath, set()))

        # File-level entry
        entries.append(RepoMapEntry(
            path=fpath,
            name=Path(fpath).name,
            kind="file",
            line=1,
            signature="",
            importance=imp,
            dependencies=deps_list,
            summary="",
        ))

        # Symbol-level entries
        for sym in info.symbols:
            sym_imp = imp * 0.8  # Symbols slightly less important than their file
            entries.append(RepoMapEntry(
                path=fpath,
                name=sym.name,
                kind=sym.kind,
                line=sym.line,
                signature=sym.signature,
                importance=sym_imp,
                dependencies=deps_list,
                summary="",
            ))

    # Sort: highest importance first, then by path
    entries.sort(key=lambda e: (-e.importance, e.path, e.line))

    # Limit to max_entries
    if len(entries) > max_entries:
        logger.debug("Truncating entries from %d to %d", len(entries), max_entries)
        entries = entries[:max_entries]

    return entries


# ── Kind icon helper ──────────────────────────────────────────────────


def _kind_icon(kind: str) -> str:
    """Return a concise icon/prefix for the kind of symbol."""
    icons = {
        "class": "C",
        "function": "F",
        "method": "M",
        "struct": "S",
        "enum": "E",
        "trait": "T",
        "interface": "I",
        "type": "T",
        "impl": "imp",
        "module": "mod",
        "const": "K",
    }
    return icons.get(kind, kind[:1].upper())


# ── Public API ──────────────────────────────────────────────────────────

__all__ = ["RepoMap", "RepoMapEntry"]
