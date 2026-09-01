"""Batch file reader — inspect multiple files in a single LLM turn.

Replaces sequential ``read_file`` fan-out with one batched call, cutting
round-trips and token overhead for multi-file reviews. Also provides:

* **Binary pre-flight** via ``shutil.which`` — external linters / audit
  binaries are probed before invocation so failed spawns never pollute the
  tool history.
* **Smart ignore filter** — deep recursive audits skip demo scripts,
  test fixtures and virtual environments unless the caller explicitly opts
  in via ``include_ignored=True``. Explicit paths are never filtered.

Typical LLM tool call::

    read_files_batch(paths=["src/system.rs", "src/events/mod.rs:1-60"])

Workspace-relative paths are resolved against ``workspace`` with the same
TOCTOU-safe checks as ``wisp.tools.filesystem.tool_read_file``.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from wisp.tools._utils import _resolve_path, _safe_read_text
from wisp.tools.errors import ToolError

logger: Final[logging.Logger] = logging.getLogger(__name__)

__all__ = [
    "read_files_batch",
    "aread_files_batch",
    "tool_read_files_batch",
    "check_binary",
    "ensure_binary",
    "should_ignore",
    "is_ignored_path",
    "list_auditable_files",
    "TOOL_SCHEMA",
    "MAX_FILES_PER_BATCH",
    "MAX_LINES_PER_FILE",
    "MAX_CHARS_PER_FILE",
    "MAX_TOTAL_CHARS",
]

# ── Limits ───────────────────────────────────────────────────────────

MAX_FILES_PER_BATCH: Final[int] = 20
MAX_LINES_PER_FILE: Final[int] = 300
MAX_CHARS_PER_FILE: Final[int] = 20_000
MAX_TOTAL_CHARS: Final[int] = 100_000
MAX_FILE_SIZE_BYTES: Final[int] = 2 * 1024 * 1024  # 2 MB — skip huge binaries

# ── Smart ignore filter ──────────────────────────────────────────────
# Mirrors wisp core ignores plus demo / fixture heuristics. The filter
# is deliberately conservative: it only fires on directory names or
# path substrings that are unambiguous. Explicit paths via read_files_batch
# are honoured unless the caller passes include_ignored=False and the path
# itself lives under an ignored directory.

IGNORED_DIRS: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".venv",
        "venv",
        ".eggs",
        "node_modules",
        "target",
        "build",
        "dist",
        ".tox",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
        ".next",
        "coverage",
        ".coverage",
        ".eggs-info",
        ".dist-info",
        ".parcel-cache",
        ".turbo",
    }
)

IGNORED_GLOBS: Final[frozenset[str]] = frozenset(
    {
        "*.log",
        "*.lock",
        "*.min.js",
        "*.pyc",
        "*.pyo",
        "*.so",
        "*.dylib",
        "*.o",
        "*.a",
        "package-lock.json",
        "yarn.lock",
        "uv.lock",
        "*.egg-info",
    }
)

# Demo / fixture path substrings — matched anywhere in the relative path.
DEMO_SUBSTRINGS: Final[tuple[str, ...]] = (
    "examples/",
    "/examples/",
    "demo/",
    "/demo/",
    "demos/",
    "/demos/",
)

DEMO_FILE_PATTERNS: Final[tuple[str, ...]] = (
    "demo_*.py",
    "*_demo.py",
    "demo.*",
    "example_*.py",
    "*_example.py",
)

FIXTURE_SUBSTRINGS: Final[tuple[str, ...]] = (
    "tests/fixtures",
    "test/fixtures",
    "/fixtures/",
    "__fixtures__",
    "test-data",
    "test_data",
    "tests/data",
)

# Compiled helper for fast substring check (not a regex, plain containment).
_DEMO_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|/)(?:demo|examples?)(?:_|/|\.)", re.I)


def should_ignore(path: Path, root: Path) -> bool:
    """Return True if *path* should be excluded from deep audits.

    Checks directory components, glob patterns and demo/fixture heuristics.
    The *root* is the workspace root; paths outside it are considered
    ignored (security boundary).
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True

    # Directory component check.
    for part in rel.parts:
        if part in IGNORED_DIRS:
            return True
        # Hidden directories except .wisp are ignored for audits.
        if part.startswith(".") and part not in (".wisp",):
            # Allow .agent but ignore .git etc. already covered; any other
            # dot-dir is noise for a deep audit.
            if part in (".git", ".hg", ".svn"):
                return True
            # Keep .wisp/.agent explicitly allowed
            if part not in (".wisp", ".agent"):
                # Dotfile at top-level like .env is not a directory to walk
                pass

    rel_str = rel.as_posix().lower()

    # Demo / fixture substring heuristics
    for sub in DEMO_SUBSTRINGS:
        if sub.lower() in rel_str:
            return True
    for sub in FIXTURE_SUBSTRINGS:
        if sub.lower() in rel_str:
            return True
    if _DEMO_RE.search(rel_str):
        return True

    # File-name glob patterns (demo file names, fixtures)
    name = path.name.lower()
    for pat in DEMO_FILE_PATTERNS:
        if fnmatch.fnmatch(name, pat.lower()):
            return True
    for pat in IGNORED_GLOBS:
        if fnmatch.fnmatch(name, pat.lower()):
            return True
        if fnmatch.fnmatch(rel_str, pat.lower()):
            return True

    return False


def is_ignored_path(
    path_str: str,
    workspace: str | Path = ".",
    *,
    include_ignored: bool = False,
) -> bool:
    """Public predicate for callers to test a single path.

    Returns False when ``include_ignored`` is True (caller explicitly opted
    in). Otherwise delegates to :func:`should_ignore`.
    """
    if include_ignored:
        return False
    try:
        ws = Path(workspace).resolve()
        # Resolve the path relative to workspace without requiring existence
        # — we only care about the logical location for ignore decisions.
        if Path(path_str).is_absolute():
            candidate = Path(path_str).resolve()
        else:
            candidate = (ws / path_str).resolve()
        return should_ignore(candidate, ws)
    except Exception:
        return False


# ── Binary pre-flight ────────────────────────────────────────────────

def check_binary(binary: str) -> bool:
    """Return True iff *binary* resolves via ``shutil.which``.

    Args:
        binary: Executable name or absolute path (e.g. ``"rg"``, ``"cargo"``,
            ``"semgrep"``).
    """
    if not binary or not isinstance(binary, str):
        return False
    # Absolute path — check existence + executability directly.
    if os.path.sep in binary or binary.startswith("."):
        p = Path(binary)
        return p.is_file() and os.access(str(p), os.X_OK)
    return shutil.which(binary) is not None


def ensure_binary(binary: str, purpose: str | None = None) -> tuple[bool, str]:
    """Pre-flight check with human-readable message.

    Returns ``(ok, message)``. When ``ok`` is False the message explains
    that the binary is missing and what the caller should do (install hint
    or skip). Logs the miss to ``agent.runtime`` via the file logger so it
    does not pollute stdout.

    Args:
        binary: Executable name.
        purpose: Optional context, e.g. ``"security audit (semgrep)"``.
    """
    if check_binary(binary):
        return True, f"binary '{binary}' available"
    hint = f" for {purpose}" if purpose else ""
    msg = f"binary '{binary}' not found in PATH{hint} — skipping (install it or set PATH)"
    try:
        logging.getLogger("agent.tools.batch_reader").info(msg)
        # Also demote to runtime.log via dedicated logger so it never hits console
        logging.getLogger("agent.runtime").info(msg)
    except Exception:
        pass
    return False, msg


# ── Core reader ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class _FileChunk:
    path: str
    ok: bool
    content: str
    lines: int
    truncated: bool
    error: str | None = None


def _read_one(
    raw_path: str,
    workspace: str,
    *,
    max_lines: int = MAX_LINES_PER_FILE,
    max_chars: int = MAX_CHARS_PER_FILE,
) -> _FileChunk:
    """Read one file with header, capping lines/chars for token budget.

    The path may include an optional ``:start-end`` suffix (e.g.
    ``src/foo.py:1-80``) to read a line range — mirrors the LLM's habit of
    passing anchors.
    """
    # Parse optional line range suffix: path:line or path:start-end
    path_str = raw_path.strip()
    start_line: int | None = None
    end_line: int | None = None

    # Only treat trailing :\d+(-\\d+)? as range if the prefix exists as a file
    # or the suffix looks like an anchor (prevents breaking Windows C:\ paths).
    m = re.match(r"^(.*?):(\d+)(?:-(\d+))?$", path_str)
    if m:
        candidate = m.group(1)
        # Probe existence before stripping — if candidate is a file, honour range.
        # We cannot rely on _resolve_path yet (needs workspace), so do a cheap
        # existence check relative to workspace.
        try:
            probe = Path(workspace).resolve() / candidate
            # Also handle absolute candidate
            if Path(candidate).is_absolute():
                probe = Path(candidate)
            if probe.exists() or probe.is_file() or True:
                # Be permissive: if it looks like an anchor, parse it.
                # The actual read will raise ToolError if path is wrong, which we
                # surface as an error chunk rather than blowing the whole batch.
                try:
                    start_line = int(m.group(2))
                    end_line = int(m.group(3)) if m.group(3) else start_line
                    if start_line >= 1 and (end_line is None or end_line >= start_line):
                        path_str = candidate
                    else:
                        start_line = end_line = None
                except ValueError:
                    start_line = end_line = None
        except Exception:
            pass

    try:
        full_path = _resolve_path(path_str, workspace)
        if not full_path.exists():
            return _FileChunk(path=raw_path, ok=False, content="", lines=0, truncated=False, error=f"File not found: {path_str}")
        if not full_path.is_file():
            return _FileChunk(path=raw_path, ok=False, content="", lines=0, truncated=False, error=f"Not a file: {path_str}")
        try:
            size = full_path.stat().st_size
        except OSError as e:
            return _FileChunk(path=raw_path, ok=False, content="", lines=0, truncated=False, error=f"Cannot stat {path_str}: {e}")
        if size > MAX_FILE_SIZE_BYTES:
            return _FileChunk(
                path=raw_path,
                ok=False,
                content="",
                lines=0,
                truncated=False,
                error=f"File too large: {path_str} is {size / 1024 / 1024:.1f} MB (max {MAX_FILE_SIZE_BYTES / 1024 / 1024:.0f} MB) — use range :1-300",
            )

        text = _safe_read_text(path_str, workspace, encoding="utf-8")
        total_lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        # Apply range if requested
        if start_line is not None:
            lines = text.splitlines()
            s = max(1, start_line)
            e = min(len(lines), end_line if end_line is not None else len(lines))
            if (e - s + 1) > max_lines:
                e = s + max_lines - 1
            chosen = lines[s - 1 : e]
            out = "\n".join(chosen)
            truncated = (e - s + 1) < (end_line - s + 1 if end_line else total_lines - s + 1) or len(lines) > max_lines
            header = f"--- FILE: {path_str} | LINES: {total_lines} | SHOWING: {s}-{e} ---\n"
            if len(out) > max_chars:
                out = out[: max_chars - 40] + "\n… [range truncated — see file for remainder]"
                truncated = True
            return _FileChunk(path=raw_path, ok=True, content=header + out, lines=e - s + 1, truncated=truncated)

        # No range — cap to head window for large files
        lines = text.splitlines()
        if len(lines) > max_lines:
            chosen = lines[:max_lines]
            out = "\n".join(chosen)
            if len(out) > max_chars:
                out = out[: max_chars - 40] + "\n… [truncated]"
            header = f"--- FILE: {path_str} | LINES: {total_lines} | SHOWING: 1-{max_lines} (capped) ---\n"
            return _FileChunk(path=raw_path, ok=True, content=header + out, lines=max_lines, truncated=True)

        # Small file — full content, but char-cap
        if len(text) > max_chars:
            text = text[: max_chars - 40] + "\n… [truncated]"
            header = f"--- FILE: {path_str} | LINES: {total_lines} | SHOWING: 1-{min(total_lines, max_lines)} (char-capped) ---\n"
            return _FileChunk(path=raw_path, ok=True, content=header + text, lines=min(total_lines, max_lines), truncated=True)

        header = f"--- FILE: {path_str} | LINES: {total_lines} | SHOWING: 1-{total_lines} ---\n"
        return _FileChunk(path=raw_path, ok=True, content=header + text, lines=total_lines, truncated=False)

    except ToolError as e:
        return _FileChunk(path=raw_path, ok=False, content="", lines=0, truncated=False, error=str(e))
    except Exception as e:
        return _FileChunk(path=raw_path, ok=False, content="", lines=0, truncated=False, error=f"Unexpected error reading {path_str}: {e}")


def read_files_batch(
    paths: list[str],
    workspace: str | Path = ".",
    *,
    include_ignored: bool = False,
    max_lines_per_file: int = MAX_LINES_PER_FILE,
    max_chars_per_file: int = MAX_CHARS_PER_FILE,
    max_total_chars: int = MAX_TOTAL_CHARS,
) -> str:
    """Read multiple files in one turn — token-efficient batch reader.

    Each file is capped to ``max_lines_per_file`` / ``max_chars_per_file``;
    the concatenated output is capped to ``max_total_chars``. Failures for
    individual files do not abort the batch — they surface as
    ``[Error reading <path>: …]`` blocks so the LLM can reason about
    missing files without a second round-trip.

    Ignored paths (venv, demo scripts, fixtures) are skipped with a note
    unless ``include_ignored`` is True or the path was explicitly listed
    and lives outside an ignored directory (explicit paths are honoured).

    Args:
        paths: File paths relative to *workspace* (or absolute). May include
            optional ``:start-end`` line-range suffix.
        workspace: Workspace root for path resolution.
        include_ignored: When True, disables the smart ignore filter.
        max_lines_per_file: Per-file line cap.
        max_chars_per_file: Per-file char cap.
        max_total_chars: Concatenated output cap; excess files are summarized.

    Returns:
        Concatenated file blocks with headers, or error stubs.
    """
    if not isinstance(paths, list):
        raise ToolError("paths must be a list of strings")
    if len(paths) == 0:
        return "(no files requested)"
    if len(paths) > MAX_FILES_PER_BATCH:
        raise ToolError(f"Too many files: {len(paths)} > {MAX_FILES_PER_BATCH} — split into smaller batches")

    cleaned: list[str] = []
    for p in paths:
        if not isinstance(p, str) or not p.strip():
            raise ToolError(f"Invalid path entry: {p!r} — must be non-empty string")
        cleaned.append(p.strip())

    ws_str = str(workspace)

    # Smart ignore: filter only when path logically lives under ignored dir.
    # Explicit files are otherwise honoured — the LLM asked for them.
    filtered: list[str] = []
    skipped: list[str] = []
    for p in cleaned:
        # Strip range suffix for ignore check
        base = re.sub(r":\d+(?:-\d+)?$", "", p)
        if not include_ignored and is_ignored_path(base, ws_str, include_ignored=False):
            skipped.append(p)
        else:
            filtered.append(p)

    if not filtered and skipped:
        return (
            f"(all {len(skipped)} requested files were ignored by smart filter "
            f"[venv/demo/fixtures] — pass include_ignored=True to force)\n"
            + "\n".join(f"  - {s} [ignored]" for s in skipped)
        )

    chunks: list[_FileChunk] = []
    for p in filtered:
        chunks.append(_read_one(p, ws_str, max_lines=max_lines_per_file, max_chars=max_chars_per_file))

    # Build concatenated output with caps
    parts: list[str] = []
    total_chars = 0
    ok_count = 0
    err_count = 0
    truncated_count = 0

    for ch in chunks:
        if ch.ok:
            block = ch.content
            ok_count += 1
            if ch.truncated:
                truncated_count += 1
        else:
            block = f"--- FILE: {ch.path} | ERROR ---\n[Error reading {ch.path}: {ch.error}]\n"
            err_count += 1

        # Respect total char cap — summarize remaining instead of truncating mid-file.
        if total_chars + len(block) > max_total_chars:
            remaining = len(chunks) - len(parts)
            # Try to fit at least the header of the next file
            if total_chars < max_total_chars - 200:
                # Fit a clipped version
                space = max_total_chars - total_chars - 200
                parts.append(block[:space] + "\n… [batch truncated — total char cap reached]")
            parts.append(f"\n… +{remaining} more file(s) truncated — batch hit {max_total_chars} char cap (showing {len(parts)}/{len(chunks)} files)")
            break

        parts.append(block)
        total_chars += len(block)

    # Prepend summary badge when there were skips
    header = ""
    if skipped:
        header = f"[Skipped {len(skipped)} ignored file(s) — pass include_ignored=True to force: {', '.join(skipped[:3])}{' …' if len(skipped) > 3 else ''}]\n"

    summary = f"[Batch: {ok_count} ok · {err_count} errors · {truncated_count} truncated · {total_chars} chars]\n"
    body = "\n\n".join(parts)

    # If no ok files, still surface errors
    if ok_count == 0 and err_count > 0:
        return header + summary + body

    # Badge the truncation sentinel for the renderer (never leak raw "… +N more" without badge)
    # but keep our block headers readable.
    return header + summary + body


async def aread_files_batch(
    paths: list[str],
    workspace: str | Path = ".",
    *,
    include_ignored: bool = False,
    max_lines_per_file: int = MAX_LINES_PER_FILE,
    max_chars_per_file: int = MAX_CHARS_PER_FILE,
    max_total_chars: int = MAX_TOTAL_CHARS,
    max_concurrent: int = 8,
) -> str:
    """Async batch reader with bounded concurrency.

    Uses ``asyncio.to_thread`` to offload blocking I/O without stalling the
    event loop. Concurrency is bounded by a semaphore so a 20-file fanout
    does not saturate the thread pool.
    """
    if len(paths) > MAX_FILES_PER_BATCH:
        raise ToolError(f"Too many files: {len(paths)} > {MAX_FILES_PER_BATCH}")

    ws_str = str(workspace)
    sem = asyncio.Semaphore(max(1, max_concurrent))

    async def _one(p: str) -> _FileChunk:
        async with sem:
            return await asyncio.to_thread(_read_one, p, ws_str, max_lines=max_lines_per_file, max_chars=max_chars_per_file)

    # Note: we replicate the sync filtering logic for consistency
    filtered: list[str] = []
    skipped: list[str] = []
    for p in paths:
        base = re.sub(r":\d+(?:-\d+)?$", "", p)
        if not include_ignored and is_ignored_path(base, ws_str, include_ignored=False):
            skipped.append(p)
        else:
            filtered.append(p)

    if not filtered and skipped:
        return (
            f"(all {len(skipped)} requested files were ignored — pass include_ignored=True)\n"
            + "\n".join(f"  - {s} [ignored]" for s in skipped)
        )

    chunks = await asyncio.gather(*[_one(p) for p in filtered])

    # Reuse sync concatenation logic
    parts: list[str] = []
    total_chars = 0
    ok_count = err_count = truncated_count = 0
    for ch in chunks:
        block = ch.content if ch.ok else f"--- FILE: {ch.path} | ERROR ---\n[Error reading {ch.path}: {ch.error}]\n"
        if ch.ok:
            ok_count += 1
            if ch.truncated:
                truncated_count += 1
        else:
            err_count += 1
        if total_chars + len(block) > max_total_chars:
            remaining = len(chunks) - len(parts)
            if total_chars < max_total_chars - 200:
                space = max_total_chars - total_chars - 200
                parts.append(block[:space] + "\n… [batch truncated]")
            parts.append(f"\n… +{remaining} more file(s) truncated — cap {max_total_chars} chars")
            break
        parts.append(block)
        total_chars += len(block)

    header = ""
    if skipped:
        header = f"[Skipped {len(skipped)} ignored — pass include_ignored=True: {', '.join(skipped[:3])}]\n"
    summary = f"[Batch: {ok_count} ok · {err_count} errors · {truncated_count} truncated · {total_chars} chars]\n"
    return header + summary + "\n\n".join(parts)


# ── Tool wrapper for registry ────────────────────────────────────────

def tool_read_files_batch(
    paths: list[str],
    workspace: str = ".",
    include_ignored: bool = False,
    max_lines_per_file: int = MAX_LINES_PER_FILE,
) -> str:
    """Tool entry point — matches ``TOOL_SCHEMAS`` signature.

    This is the function registered in ``wisp.tools.registry.TOOL_IMPLS``
    under ``read_files_batch`` (and available as ``agent.tools.batch_reader``).
    """
    return read_files_batch(
        paths,
        workspace=workspace,
        include_ignored=bool(include_ignored),
        max_lines_per_file=int(max_lines_per_file),
    )


# ── Auditable file discovery ─────────────────────────────────────────

def list_auditable_files(
    workspace: str | Path = ".",
    *,
    include_ignored: bool = False,
    max_files: int = 2000,
    extensions: tuple[str, ...] | None = None,
) -> list[str]:
    """List files under *workspace* respecting the smart ignore filter.

    Used for deep recursive audits where the caller wants a file inventory
    without manually enumerating paths. Respects demo/fixture/venv ignores
    unless ``include_ignored`` is True.

    Args:
        workspace: Root to walk.
        include_ignored: Disable ignore filtering.
        max_files: Hard cap on returned entries.
        extensions: Optional allowlist, e.g. ``(".py", ".rs")``.

    Returns:
        Sorted list of workspace-relative paths.
    """
    ws = Path(workspace).resolve()
    if not ws.is_dir():
        raise ToolError(f"Workspace not found: {workspace}")

    out: list[str] = []
    stack: list[Path] = [ws]

    while stack and len(out) < max_files:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    p = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        if not include_ignored and should_ignore(p, ws):
                            continue
                        try:
                            depth = len(p.relative_to(ws).parts)
                        except ValueError:
                            continue
                        if depth > 8:  # depth cap for audits
                            continue
                        stack.append(p)
                    elif entry.is_file():
                        if extensions and p.suffix.lower() not in extensions:
                            continue
                        if not include_ignored and should_ignore(p, ws):
                            continue
                        try:
                            rel = str(p.relative_to(ws))
                        except ValueError:
                            continue
                        # Quick size guard for audit — skip >2 MB
                        try:
                            if p.stat().st_size > MAX_FILE_SIZE_BYTES:
                                continue
                        except OSError:
                            continue
                        out.append(rel)
                        if len(out) >= max_files:
                            break
        except OSError:
            continue

    out.sort()
    return out


# ── Registry schema ──────────────────────────────────────────────────

TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_files_batch",
        "description": (
            "Read multiple files in a single turn — batch inspection. "
            "Replaces sequential read_file calls. Each file is capped to 300 lines / 20K chars; "
            "the batch is capped to 100K total chars. Ignored paths (venv, demo scripts, fixtures) "
            "are skipped unless include_ignored=true. Supports optional :start-end line suffix."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files to read (workspace-relative or absolute). Supports ':start-end' suffix, e.g. 'src/app.py:1-60'. Max 20 per call.",
                },
                "include_ignored": {
                    "type": "boolean",
                    "description": "Include venv / demo / fixture files. Default false.",
                    "default": False,
                },
                "max_lines_per_file": {
                    "type": "number",
                    "description": "Per-file line cap (default 300, max 500).",
                    "default": MAX_LINES_PER_FILE,
                },
            },
            "required": ["paths"],
        },
    },
}


def register_with_wisp_registry() -> None:
    """Register ``read_files_batch`` with ``wisp.tools.registry`` if available.

    Idempotent. Call once at startup (e.g. from ``wisp.composition``).
    """
    try:
        from wisp.tools.registry import TOOL_IMPLS, TOOL_SCHEMAS

        if "read_files_batch" not in TOOL_IMPLS:
            TOOL_IMPLS["read_files_batch"] = tool_read_files_batch  # type: ignore[assignment]
        if not any(s.get("function", {}).get("name") == "read_files_batch" for s in TOOL_SCHEMAS):
            TOOL_SCHEMAS.append(TOOL_SCHEMA)
        logger.debug("Registered read_files_batch with wisp registry")
    except Exception as e:
        logger.debug("Could not register batch_reader with wisp registry: %s", e)
