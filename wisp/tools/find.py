"""`grep` and `glob`: purpose-built, read-only search over the workspace.

Both are contained to the workspace (the same `_resolve_path` boundary as `read_file`), never follow symlinks (a
symlinked directory is not entered, a symlinked file is not read), skip VCS and build-cache directories, and are bounded
in files, lines, output and time, so neither can be turned into a way to escape the workspace or to stall the agent.

`grep` is pure Python. A regex engine that backtracks can be made to run for minutes by a pattern the model writes
itself, and Python's cannot be interrupted mid-match, so the defence is layered:

* patterns are length-capped, and the shapes that backtrack exponentially (an unbounded repeat over another
  unbounded repeat, or over an alternation with overlapping or empty branches) are refused before any matching;
* only the first 500 characters of a line are matched. This matters more than it looks: ordinary, non-nested
  patterns such as ``.*a.*b.*c`` are polynomial, and measured at 2,000 characters they take 0.85 to over 3 seconds
  per line, which a shape check cannot catch; at 500 characters the same work is about 64 times smaller;
* the wall-clock budget is checked on every line, so a slow pattern ends the search with partial results and a note
  instead of hanging.

This is a mitigation, not a proof: one line can still cost a fraction of a second. No new dependency is added
(ripgrep is not one this project can rely on).
"""

from __future__ import annotations

import importlib
import os
import re
import time
from pathlib import Path

from wisp.tools._utils import _resolve_path, _validate_int, _validate_string
from wisp.tools.errors import ToolError

#: Directories never entered. Version control and caches: never what a code search is after, and large.
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", ".tox", "dist", "build", ".next", ".cache",
})
_MAX_PATTERN = 500
_MAX_FILE_BYTES = 1_000_000
_BINARY_SNIFF = 8192
_MAX_LINE = 500
_MAX_OUTPUT_CHARS = 100_000
_MAX_ENTRIES = 200_000
_SEARCH_BUDGET_S = 10.0
_OUTPUT_MODES = ("content", "files_with_matches", "count")


# ── glob translation ───────────────────────────────────────────────────

def _expand_braces(pattern: str, limit: int = 20) -> list[str]:
    """``a.{py,js}`` -> ``['a.py', 'a.js']``. One level, no nesting, bounded."""
    m = re.search(r"\{([^{}]*)\}", pattern)
    if not m:
        return [pattern]
    out: list[str] = []
    for alt in m.group(1).split(","):
        out.extend(_expand_braces(pattern[: m.start()] + alt + pattern[m.end():], limit))
        if len(out) > limit:
            raise ToolError(f"Pattern expands to more than {limit} alternatives")
    return out


def _segment_regex(seg: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(seg):
        c = seg[i]
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = seg.find("]", i + 2)
            if j == -1:
                out.append(re.escape(c))
            else:
                body = seg[i + 1: j]
                body = "^" + body[1:] if body[:1] == "!" else body
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """A glob as a regex over '/'-separated relative paths. ``**`` spans directories; ``*`` and ``?`` do not."""
    alts = []
    for pat in _expand_braces(pattern):
        parts = pat.split("/")
        rx = ""
        for idx, part in enumerate(parts):
            last = idx == len(parts) - 1
            if part == "**":
                rx += ".*" if last else "(?:[^/]+/)*"
            else:
                rx += _segment_regex(part) + ("" if last else "/")
        alts.append(rx)
    return re.compile("(?:" + "|".join(alts) + ")$")


def _check_glob(pattern: str, name: str) -> None:
    _validate_string(pattern, name, 300)
    if pattern.startswith("/") or ".." in Path(pattern).parts:
        raise ToolError(f"Invalid pattern: '{pattern}': path traversal not allowed")


# ── walking ────────────────────────────────────────────────────────────

class _Budget:
    def __init__(self) -> None:
        self.deadline = time.monotonic() + _SEARCH_BUDGET_S
        self.expired = False

    def over(self) -> bool:
        if not self.expired and time.monotonic() >= self.deadline:
            self.expired = True
        return self.expired


def _base_dir(path: str, workspace: str, *, must_be_dir: bool) -> Path:
    _validate_string(path, "path", 4096)
    base = _resolve_path(path, workspace)
    if not base.exists():
        raise ToolError(f"Path not found: {path}")
    if must_be_dir and not base.is_dir():
        raise ToolError(f"Not a directory: {path}")
    return base


def _walk(base: Path, budget: _Budget):
    """Yield ``(absolute_path, path_relative_to_base)`` for every regular, non-symlink file, in a stable order."""
    seen = 0
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not os.path.islink(os.path.join(dirpath, d)))
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            seen += 1
            if seen > _MAX_ENTRIES or budget.over():
                return
            yield full, Path(os.path.relpath(full, base)).as_posix()


def _display(full: str, workspace: str) -> str:
    """A workspace-relative path for output. Containment is `_resolve_path`'s job; this only formats."""
    return Path(os.path.relpath(full, Path(workspace).resolve())).as_posix()


# ── glob ───────────────────────────────────────────────────────────────

def tool_glob(pattern: str, workspace: str = ".", path: str = ".", max_results: int = 200) -> str:
    """Find files by glob pattern, newest first."""
    _check_glob(pattern, "pattern")
    max_results = _validate_int(max_results, "max_results", 1, 1000)
    base = _base_dir(path, workspace, must_be_dir=True)
    rx = _glob_regex(pattern)
    budget = _Budget()

    hits: list[tuple[float, str]] = []
    for full, rel in _walk(base, budget):
        if rx.match(rel):
            try:
                mtime = os.stat(full).st_mtime
            except OSError:
                continue
            hits.append((mtime, _display(full, workspace)))

    if not hits:
        note = " (stopped at the time budget)" if budget.expired else ""
        return f"No files matched '{pattern}' in {path}{note}"
    hits.sort(key=lambda h: (-h[0], h[1]))
    shown = hits[:max_results]
    lines = [p for _, p in shown]
    if len(hits) > max_results:
        lines.append(f"(showing {max_results} of {len(hits)} matches, newest first; narrow the pattern)")
    if budget.expired:
        lines.append("(stopped at the time budget; results may be incomplete)")
    return "\n".join(lines)


# ── grep ───────────────────────────────────────────────────────────────

def _unbounded(max_repeat: int) -> bool:
    return max_repeat > 100


def _catastrophic(pattern: str) -> str | None:
    """A reason when the pattern has a shape that backtracks catastrophically, else None."""
    try:
        sre = importlib.import_module("re._parser")
    except ImportError:  # pragma: no cover - the parser module moved
        sre = None
    if sre is None:
        return "nested quantifier" if re.search(r"\([^()]*[+*][^()]*\)\s*[+*{]", pattern) else None
    try:
        tree = sre.parse(pattern)
    except Exception:
        return None  # re.compile reports it with a better message

    def walk(items, inside_repeat: bool) -> str | None:
        for op, av in items:
            name = str(op)
            if name in ("MAX_REPEAT", "MIN_REPEAT"):
                lo, hi, sub = av
                if _unbounded(hi):
                    if inside_repeat:
                        return "nested quantifier"
                    if _contains_overlapping_branch(sub):
                        return "alternation inside a repeat with overlapping branches"
                found = walk(sub, inside_repeat or _unbounded(hi))
                if found:
                    return found
            elif name == "SUBPATTERN":
                found = walk(av[-1], inside_repeat)
                if found:
                    return found
            elif name == "BRANCH":
                for branch in av[1]:
                    found = walk(branch, inside_repeat)
                    if found:
                        return found
        return None

    def starts(sub) -> set | None:
        out: set = set()
        for op, av in sub:
            name = str(op)
            if name == "LITERAL":
                out.add(av)
            elif name == "SUBPATTERN":
                inner = starts(av[-1])
                if inner is None:
                    return None
                out |= inner
            elif name == "BRANCH":
                for branch in av[1]:
                    inner = starts(branch)
                    if inner is None:
                        return None
                    out |= inner
            elif name in ("MAX_REPEAT", "MIN_REPEAT"):
                if av[0] == 0:
                    return None
                inner = starts(av[2])
                if inner is None:
                    return None
                out |= inner
            else:
                return None
            return out
        return out

    def _contains_overlapping_branch(sub) -> bool:
        for op, av in sub:
            name = str(op)
            if name == "BRANCH":
                firsts = [starts(b) for b in av[1]]
                if any(f is None for f in firsts):
                    return True
                if any(not f for f in firsts):
                    return True  # a branch that can match nothing (Python factors `a|aa` into `a(?:|a)`) is ambiguous
                seen: set = set()
                for f in firsts:
                    if seen & f:
                        return True
                    seen |= f
            elif name == "SUBPATTERN" and _contains_overlapping_branch(av[-1]):
                return True
            elif name in ("MAX_REPEAT", "MIN_REPEAT") and _contains_overlapping_branch(av[2]):
                return True
        return False

    return walk(tree, False)


def _read_text(full: str) -> str | None:
    """The file's text, or None when it is binary or too large. Opened with O_NOFOLLOW so a swap to a symlink fails."""
    try:
        fd = os.open(full, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        if os.fstat(fd).st_size > _MAX_FILE_BYTES:
            return None
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
    except OSError:
        return None
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if b"\x00" in data[:_BINARY_SNIFF]:
        return None
    return data.decode("utf-8", errors="replace")


def _clip(line: str) -> str:
    return line if len(line) <= _MAX_LINE else line[:_MAX_LINE] + "…"


def tool_grep(
    pattern: str,
    workspace: str = ".",
    path: str = ".",
    glob: str | None = None,
    ignore_case: bool = False,
    context: int = 0,
    output_mode: str = "content",
    max_results: int = 100,
) -> str:
    """Search file contents with a regular expression."""
    _validate_string(pattern, "pattern", _MAX_PATTERN)
    if output_mode not in _OUTPUT_MODES:
        raise ToolError(f"output_mode must be one of {', '.join(_OUTPUT_MODES)}, got {output_mode!r}")
    context = _validate_int(context, "context", 0, 5)
    max_results = _validate_int(max_results, "max_results", 1, 1000)
    reason = _catastrophic(pattern)
    if reason:
        raise ToolError(
            f"Pattern refused: {reason} can backtrack catastrophically. "
            f"Rewrite it without nesting one repeat inside another."
        )
    try:
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        raise ToolError(f"Invalid regular expression: {exc}")
    file_rx = None
    if glob:
        _check_glob(glob, "glob")
        file_rx = _glob_regex(glob)

    base = _base_dir(path, workspace, must_be_dir=False)
    budget = _Budget()

    if base.is_file():
        files = iter([(str(base), base.name)])
    else:
        files = _walk(base, budget)

    out: list[str] = []
    per_file: dict[str, int] = {}
    total = 0
    skipped = 0
    searched = 0
    truncated = False
    size = 0
    printed_any = False

    for full, rel in files:
        if budget.over():
            break
        if file_rx is not None and not (file_rx.match(rel) or file_rx.match(os.path.basename(rel))):
            continue
        text = _read_text(full)
        if text is None:
            skipped += 1
            continue
        searched += 1
        disp = _display(full, workspace)
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        match_nos: list[int] = []
        for n, line in enumerate(lines, 1):
            if budget.over():  # every line: one slow line must not be followed by a thousand more
                break
            if rx.search(line[:_MAX_LINE]):
                match_nos.append(n)
                if output_mode == "files_with_matches":
                    break  # one hit is enough to list the file
                if output_mode == "content" and total + len(match_nos) >= max_results:
                    break
        if not match_nos:
            continue
        per_file[disp] = len(match_nos)
        total += len(match_nos)
        if output_mode == "content":
            hit = set(match_nos)
            wanted: set[int] = set()
            for n in match_nos:
                wanted.update(range(max(1, n - context), min(len(lines), n + context) + 1))
            prev = None
            for n in sorted(wanted):
                if (prev is not None and n != prev + 1) or (prev is None and printed_any and context):
                    out.append("--")
                sep = ":" if n in hit else "-"
                row = f"{disp}{sep}{n}{sep}{_clip(lines[n - 1])}"
                size += len(row) + 1
                out.append(row)
                prev = n
            printed_any = True
        limit_hit = total >= max_results if output_mode == "content" else len(per_file) >= max_results
        if limit_hit or size > _MAX_OUTPUT_CHARS:
            truncated = True
            break

    if not per_file:
        note = " (stopped at the time budget)" if budget.expired else ""
        return f"No matches for '{pattern}' in {path}{note}" + _footer(searched, skipped, False, budget.expired, max_results)

    if output_mode == "files_with_matches":
        body = sorted(per_file)
    elif output_mode == "count":
        body = [f"{p}:{c}" for p, c in sorted(per_file.items())]
    else:
        body = out
    return "\n".join(body) + _footer(searched, skipped, truncated, budget.expired, max_results)


def _footer(searched: int, skipped: int, truncated: bool, expired: bool, max_results: int) -> str:
    notes: list[str] = []
    if truncated:
        notes.append(f"output truncated at {max_results} results or {_MAX_OUTPUT_CHARS:,} characters; narrow the pattern or path")
    if expired:
        notes.append("stopped at the time budget; results may be incomplete")
    notes.append(f"searched {searched} files" + (f", skipped {skipped} binary or oversized" if skipped else ""))
    return "\n(" + "; ".join(notes) + ")"
