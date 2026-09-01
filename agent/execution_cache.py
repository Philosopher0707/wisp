"""Mtime/Git-aware execution cache — prevents redundant builds/tests.

Goals from §3:
  * Build/Test State Caching — tracks codebase mutation via git hash or
    mtime walk. Identical full builds or test suites across consecutive
    turns are skipped unless source files actually changed.
  * Collapsible Log Artifact Sinking — when output exceeds display
    thresholds (>20 lines) the full raw output lands in
    ``.agent/logs/last_execution.log`` (plus a timestamped copy) and the
    terminal shows a single-line preview with an ``press 'e' / /expand``
    hint. No bytes are dropped.

Public API
----------
* :class:`ExecutionCache` — high-level cache + runner. Create one per
  workspace; it persists its index to ``.agent/cache/execution_cache.json``.
* :func:`compute_fingerprint` — workspace hash (git if available, else mtime).
* :func:`collapse_output` — single-line preview with artifact link.
* Convenience: :func:`run_cached` / :func:`arun_cached`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

logger: Final[logging.Logger] = logging.getLogger(__name__)

__all__ = [
    "ExecutionCache",
    "CachedResult",
    "CacheEntry",
    "compute_fingerprint",
    "git_fingerprint",
    "mtime_fingerprint",
    "collapse_output",
    "run_cached",
    "arun_cached",
]

# ── Constants ────────────────────────────────────────────────────────

CACHE_DIR: Final[Path] = Path(".agent/cache")
CACHE_FILE: Final[Path] = CACHE_DIR / "execution_cache.json"
LOG_DIR: Final[Path] = Path(".agent/logs")
LAST_EXECUTION_LOG: Final[Path] = LOG_DIR / "last_execution.log"
DEFAULT_THRESHOLD_LINES: Final[int] = 20
DEFAULT_MAX_CACHE_ENTRIES: Final[int] = 50

# Ignore sets for fingerprinting — mirrors agent.indexer / batch_reader
_IGNORED_DIRS: Final[frozenset[str]] = frozenset(
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
        ".parcel-cache",
        ".turbo",
    }
)

_SANITIZE_RE: Final[re.Pattern[str]] = re.compile(r"[^a-zA-Z0-9._-]+")

# Source extensions that count for fingerprinting — config/docs alone do not
# invalidate a build cache needlessly, but we include them for test-cache.
_SOURCE_EXTS: Final[frozenset[str]] = frozenset(
    {
        ".py",
        ".rs",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".go",
        ".java",
        ".kt",
        ".rb",
        ".php",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".swift",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
        ".md",
        ".sh",
        ".sql",
    }
)


# ── Helpers ──────────────────────────────────────────────────────────

def _sanitize(name: str, max_len: int = 32) -> str:
    s = _SANITIZE_RE.sub("_", name.strip())[:max_len].strip("_")
    return s or "cmd"


def _hash_command(cmd: str) -> str:
    return hashlib.sha256(cmd.encode("utf-8", errors="ignore")).hexdigest()[:16]


def git_fingerprint(workspace: str | Path) -> str | None:
    """Compute a git-aware fingerprint, or None if git unavailable.

    Hash combines:
      * ``git rev-parse HEAD`` (current commit)
      * ``git status --porcelain`` (staged/unstaged/untracked)
      * ``git diff --stat`` (working-tree vs index shape)

    Returns a hex digest (12 chars) or None when not a git repo / git missing.
    """
    if shutil.which("git") is None:
        return None
    ws = Path(workspace).resolve()
    if not (ws / ".git").exists():
        # Could be worktree — ask git directly
        try:
            probe = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=str(ws),
                capture_output=True,
                text=True,
                timeout=3,
            )
            if probe.returncode != 0:
                return None
        except Exception:
            return None

    def _run(args: list[str]) -> str:
        try:
            res = subprocess.run(
                ["git", *args],
                cwd=str(ws),
                capture_output=True,
                text=True,
                timeout=5,
            )
            return res.stdout if res.returncode == 0 else ""
        except Exception:
            return ""

    head = _run(["rev-parse", "HEAD"]).strip()
    status = _run(["status", "--porcelain"])
    # Limit status to first 500 lines to avoid hashing huge untracked dumps
    status = "\n".join(status.splitlines()[:500])
    diff_stat = _run(["diff", "--stat"])
    # Also include staged diff
    diff_cached = _run(["diff", "--cached", "--stat"])

    # Hash combined
    h = hashlib.sha256()
    h.update(head.encode(errors="ignore"))
    h.update(b"\x00")
    h.update(status.encode(errors="ignore"))
    h.update(b"\x00")
    h.update(diff_stat.encode(errors="ignore"))
    h.update(b"\x00")
    h.update(diff_cached.encode(errors="ignore"))
    return h.hexdigest()[:12]


def mtime_fingerprint(workspace: str | Path, *, max_files: int = 5000) -> str:
    """Mtime/size hash fallback when git is unavailable.

    Walks up to *max_files* source files, hashes (rel_path, mtime_ns, size).
    Ignores venv / build / hidden dirs. Deterministic across runs when
    nothing has changed.
    """
    ws = Path(workspace).resolve()
    entries: list[tuple[str, int, int]] = []

    # Depth-first walk with ignore
    stack: list[Path] = [ws]
    while stack and len(entries) < max_files:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    p = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        # Ignore check on directory component
                        if p.name in _IGNORED_DIRS:
                            continue
                        if p.name.startswith(".") and p.name not in (".wisp", ".agent"):
                            # Skip hidden dirs (except .wisp/.agent)
                            if p.name in (".git", ".hg", ".svn"):
                                continue
                            # Allow . but skip .opencode etc. for fingerprint? No, skip noise
                            if p.name.startswith("."):
                                continue
                        try:
                            depth = len(p.relative_to(ws).parts)
                        except ValueError:
                            continue
                        if depth > 8:
                            continue
                        stack.append(p)
                    elif entry.is_file():
                        if p.suffix.lower() not in _SOURCE_EXTS and p.name not in ("Cargo.lock", "pyproject.toml", "Makefile"):
                            # Still consider unrecognized source if not ignored dir
                            # but skip large non-source binaries quickly
                            if p.suffix.lower() in {".log", ".lock", ".min.js", ".pyc", ".png", ".jpg", ".gif", ".pdf", ".zip"}:
                                continue
                        try:
                            st = p.stat()
                        except OSError:
                            continue
                        if st.st_size > 5 * 1024 * 1024:
                            continue  # skip huge files
                        try:
                            rel = str(p.relative_to(ws))
                        except ValueError:
                            rel = str(p)
                        # Use mtime_ns for precision (some FS have 1s granularity)
                        try:
                            mtime_ns = int(st.st_mtime_ns)
                        except AttributeError:
                            mtime_ns = int(st.st_mtime * 1e9)
                        entries.append((rel, mtime_ns, int(st.st_size)))
                        if len(entries) >= max_files:
                            break
        except OSError:
            continue

    entries.sort(key=lambda x: x[0])
    h = hashlib.sha256()
    for rel, mtime_ns, size in entries:
        h.update(rel.encode(errors="ignore"))
        h.update(b"\x00")
        h.update(str(mtime_ns).encode())
        h.update(b"\x00")
        h.update(str(size).encode())
        h.update(b"\n")
    # Also fold in file count so empty vs missing workspace differs
    h.update(f"count={len(entries)}".encode())
    return h.hexdigest()[:12]


def compute_fingerprint(workspace: str | Path) -> str:
    """Compute workspace fingerprint: git if available, else mtime.

    Never raises — falls back to mtime and, in worst case, a constant
    sentinel so callers can still cache (per-process).
    """
    try:
        g = git_fingerprint(workspace)
        if g is not None:
            return f"git:{g}"
    except Exception as e:
        logger.debug("git fingerprint failed, falling back to mtime: %s", e)
    try:
        m = mtime_fingerprint(workspace)
        return f"mt:{m}"
    except Exception as e:
        logger.debug("mtime fingerprint failed: %s", e)
        return "mt:unknown"


def _write_artifacts(cmd: str, full_output: str, exit_code: int, duration_ms: int) -> tuple[Path, Path]:
    """Sink full output to disk. Returns (run_path, last_path)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    slug = _sanitize(cmd.split()[0] if cmd.strip() else "cmd", 24)
    slug_full = _sanitize(cmd, 20)
    run_path = LOG_DIR / f"run_{slug}_{slug_full}_{ts}.log"
    last_path = LAST_EXECUTION_LOG

    header = (
        f"# cmd: {cmd}\n"
        f"# exit: {exit_code}  duration_ms: {duration_ms}  ts: {ts}\n"
        f"# cwd: {Path.cwd()}\n"
        f"# ── output ──\n"
    )
    body = header + full_output
    if not body.endswith("\n"):
        body += "\n"
    body += f"# ── end (exit {exit_code}) ──\n"

    for p in (run_path, last_path):
        tmp = p.with_suffix(".tmp")
        try:
            tmp.write_text(body, encoding="utf-8", errors="replace")
            tmp.replace(p)
        except Exception:
            try:
                p.write_text(body, encoding="utf-8", errors="replace")
            except Exception as e:
                logger.debug("Failed to write artifact %s: %s", p, e)
    return run_path, last_path


def collapse_output(
    text: str,
    *,
    threshold_lines: int = DEFAULT_THRESHOLD_LINES,
    artifact_path: Path | None = None,
) -> tuple[str, bool]:
    """Collapse output to a single-line preview when over threshold.

    When *text* has more than *threshold_lines* non-empty lines, the full
    content is expected to already be sunk to *artifact_path*; the preview
    is a single concise line with an expansion hint.

    Returns ``(preview, truncated)``. ``truncated`` is True when the
    preview is collapsed.

    Example::

        preview, truncated = collapse_output(long_text, artifact_path=Path(".agent/logs/last_execution.log"))
        # "… +47 more [press 'e' or /expand — .agent/logs/last_execution.log]"
    """
    if artifact_path is None:
        artifact_path = LAST_EXECUTION_LOG
    lines = text.splitlines()
    # Count non-empty tail for threshold — mirrors runner's while pop logic
    non_empty = [ln for ln in lines if ln.strip() != ""]
    total = len(non_empty) if non_empty else len(lines)
    if total <= threshold_lines:
        return text, False

    # Single-line preview: first meaningful line, truncated to 100 chars
    first = ""
    for ln in lines:
        s = ln.strip()
        if s:
            first = s
            break
    if not first:
        first = lines[0].strip() if lines else ""

    if len(first) > 100:
        first = first[:97] + "…"

    remaining = total - threshold_lines
    hint = f"… +{remaining} more [press 'e' or /expand — {artifact_path}]"
    # Also include badge for the artifact file itself when present
    full_hint = f"{hint}  [✓ Full output → {artifact_path}]"
    preview = f"{first}  {full_hint}" if first else full_hint
    return preview, True


# ── Dataclasses ──────────────────────────────────────────────────────


@dataclass
class CachedResult:
    """Result of a (possibly cached) command execution."""

    command: str
    fingerprint: str
    exit_code: int
    stdout: str
    stderr: str
    full_output: str
    preview: str
    truncated: bool
    duration_ms: int
    timestamp: float
    cached: bool
    log_path: Path
    last_path: Path

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["log_path"] = str(self.log_path)
        d["last_path"] = str(self.last_path)
        return d


@dataclass
class CacheEntry:
    fingerprint: str
    exit_code: int
    preview: str
    truncated: bool
    duration_ms: int
    timestamp: float
    log_path: str
    command: str


# ── Core cache ───────────────────────────────────────────────────────


class ExecutionCache:
    """Mtime/Git-aware execution cache with collapsible log sinking.

    The cache is keyed by ``hash(command)`` and stores the fingerprint of
    the workspace at the time of execution. A subsequent ``run()`` with the
    same command and an unchanged fingerprint is served from cache without
    spawning a subprocess.

    Persistence: ``.agent/cache/execution_cache.json`` (best-effort, never
    required for correctness). Corrupt cache files are ignored.

    Example::

        cache = ExecutionCache(workspace=".", threshold_lines=20)
        result = await cache.arun("cargo test --release", timeout_s=120)
        if result.cached:
            print(f"Skipped — {result.preview}")
        else:
            print(result.preview)
    """

    def __init__(
        self,
        workspace: str | Path = ".",
        *,
        cache_dir: Path | str = CACHE_DIR,
        threshold_lines: int = DEFAULT_THRESHOLD_LINES,
        max_entries: int = DEFAULT_MAX_CACHE_ENTRIES,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.cache_dir = Path(cache_dir)
        self.cache_file = self.cache_dir / "execution_cache.json"
        self.threshold_lines = int(threshold_lines)
        self.max_entries = int(max_entries)
        self._index: dict[str, CacheEntry] = {}
        self._loaded = False

    # ── Persistence ──────────────────────────────────────────────

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            if self.cache_file.exists():
                data = json.loads(self.cache_file.read_text(encoding="utf-8"))
                raw = data.get("entries", {}) if isinstance(data, dict) else {}
                for k, v in raw.items():
                    try:
                        self._index[k] = CacheEntry(**v)
                    except Exception:
                        continue
        except Exception as e:
            logger.debug("Could not load execution cache %s: %s", self.cache_file, e)
            self._index.clear()

    def _save(self) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            # Prune to max_entries — keep most recent
            if len(self._index) > self.max_entries:
                # Sort by timestamp descending, keep newest
                sorted_items = sorted(self._index.items(), key=lambda kv: kv[1].timestamp, reverse=True)
                self._index = dict(sorted_items[: self.max_entries])
            payload = {"entries": {k: asdict(v) for k, v in self._index.items()}, "saved_at": time.time()}
            tmp = self.cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self.cache_file)
        except Exception as e:
            logger.debug("Could not save execution cache: %s", e)

    def _key(self, command: str) -> str:
        return _hash_command(command.strip())

    # ── Fingerprint ──────────────────────────────────────────────

    def fingerprint(self) -> str:
        """Compute current workspace fingerprint (git or mtime)."""
        return compute_fingerprint(self.workspace)

    def should_run(self, command: str, fingerprint: str | None = None) -> bool:
        """Return True if command should execute (cache miss or stale)."""
        self._load()
        fp = fingerprint if fingerprint is not None else self.fingerprint()
        key = self._key(command)
        entry = self._index.get(key)
        if entry is None:
            return True
        return entry.fingerprint != fp

    def get_cached(self, command: str, fingerprint: str | None = None) -> CachedResult | None:
        """Return cached result if fingerprint matches, else None."""
        self._load()
        fp = fingerprint if fingerprint is not None else self.fingerprint()
        key = self._key(command)
        entry = self._index.get(key)
        if entry is None or entry.fingerprint != fp:
            return None
        # Try to rehydrate full output from log file if still present.
        # The log file contains a header; strip it so cached.full_output matches
        # the original `full` (stdout + stderr) without re-introducing header bytes.
        full = ""
        try:
            lp = Path(entry.log_path)
            if lp.exists():
                raw = lp.read_text(encoding="utf-8", errors="replace")
                # Log format: header lines then "# ── output ──\n" then body.
                marker = "# ── output ──\n"
                end_marker = "\n# ── end"
                if marker in raw:
                    after = raw.split(marker, 1)[1]
                    if end_marker in after:
                        after = after.rsplit(end_marker, 1)[0]
                    full = after
                    # Remove trailing newline added by sink if it was not in original
                    if full.endswith("\n") and not full.endswith("\n\n"):
                        # Keep as-is; original may have ended with newline
                        pass
                else:
                    full = raw
        except Exception:
            pass
        return CachedResult(
            command=entry.command,
            fingerprint=entry.fingerprint,
            exit_code=entry.exit_code,
            stdout=full,
            stderr="",
            full_output=full,
            preview=entry.preview,
            truncated=entry.truncated,
            duration_ms=entry.duration_ms,
            timestamp=entry.timestamp,
            cached=True,
            log_path=Path(entry.log_path),
            last_path=LAST_EXECUTION_LOG,
        )

    def invalidate(self, command: str | None = None) -> None:
        """Invalidate one command or the entire cache."""
        self._load()
        if command is None:
            self._index.clear()
        else:
            self._index.pop(self._key(command), None)
        self._save()

    def clear(self) -> None:
        """Alias for invalidate(None)."""
        self.invalidate(None)

    # ── Execution ────────────────────────────────────────────────

    async def arun(
        self,
        command: str,
        *,
        timeout_s: float = 120.0,
        force: bool = False,
        fingerprint: str | None = None,
        cwd: str | Path | None = None,
    ) -> CachedResult:
        """Run command with caching (async).

        Args:
            command: Shell command to execute.
            timeout_s: Timeout in seconds.
            force: When True, bypass cache and always execute.
            fingerprint: Override fingerprint (for testing).
            cwd: Working directory (defaults to workspace).

        Returns:
            CachedResult — ``cached`` is True when served from cache.
        """
        fp = fingerprint if fingerprint is not None else self.fingerprint()
        key = self._key(command)

        if not force:
            cached = self.get_cached(command, fingerprint=fp)
            if cached is not None:
                logger.info("Cache hit for %r (fingerprint %s) — skipping execution", command[:60], fp)
                return cached

        # Execute
        exec_cwd = Path(cwd).resolve() if cwd is not None else self.workspace
        exec_cwd.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        stdout = ""
        stderr = ""
        exit_code = 0
        timed_out = False

        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(exec_cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
                stdout = out_b.decode(errors="replace") if out_b else ""
                stderr = err_b.decode(errors="replace") if err_b else ""
                exit_code = proc.returncode or 0
            except asyncio.TimeoutError:
                timed_out = True
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                try:
                    out_b, err_b = await proc.communicate()
                    stdout = (out_b or b"").decode(errors="replace")
                    stderr = (err_b or b"").decode(errors="replace") + f"\n[timeout after {timeout_s}s]"
                except Exception:
                    stderr = f"[timeout after {timeout_s}s — no output captured]"
                exit_code = 124
        except FileNotFoundError as e:
            stderr = str(e)
            exit_code = 127
        except Exception as e:
            stderr = f"runner error: {e}"
            exit_code = 1

        duration_ms = int((time.monotonic() - start) * 1000)
        full = stdout + (f"\n--- stderr ---\n{stderr}" if stderr else "")
        if timed_out:
            full = f"[timeout {timeout_s}s]\n" + full

        # Disk sink first — never lose bytes
        run_path, last_path = _write_artifacts(command, full, exit_code, duration_ms)

        # Collapsible preview
        preview, truncated = collapse_output(full, threshold_lines=self.threshold_lines, artifact_path=run_path)

        # Update index
        self._load()
        self._index[key] = CacheEntry(
            fingerprint=fp,
            exit_code=exit_code,
            preview=preview,
            truncated=truncated,
            duration_ms=duration_ms,
            timestamp=time.time(),
            log_path=str(run_path),
            command=command,
        )
        self._save()

        return CachedResult(
            command=command,
            fingerprint=fp,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            full_output=full,
            preview=preview,
            truncated=truncated,
            duration_ms=duration_ms,
            timestamp=time.time(),
            cached=False,
            log_path=run_path,
            last_path=last_path,
        )

    def run(
        self,
        command: str,
        *,
        timeout_s: float = 120.0,
        force: bool = False,
        fingerprint: str | None = None,
        cwd: str | Path | None = None,
    ) -> CachedResult:
        """Synchronous wrapper for :meth:`arun`."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.arun(command, timeout_s=timeout_s, force=force, fingerprint=fingerprint, cwd=cwd))

        # Already inside a loop — run in a dedicated thread to avoid deadlock.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(asyncio.run, self.arun(command, timeout_s=timeout_s, force=force, fingerprint=fingerprint, cwd=cwd))
            return fut.result()


# ── Convenience functional API ───────────────────────────────────────

_default_cache: ExecutionCache | None = None


def _get_default_cache(workspace: str | Path = ".") -> ExecutionCache:
    global _default_cache
    ws = Path(workspace).resolve()
    if _default_cache is None or _default_cache.workspace != ws:
        _default_cache = ExecutionCache(workspace=ws)
    return _default_cache


def run_cached(
    command: str,
    workspace: str | Path = ".",
    *,
    timeout_s: float = 120.0,
    force: bool = False,
    threshold_lines: int = DEFAULT_THRESHOLD_LINES,
) -> CachedResult:
    """Run *command* with default cache (sync)."""
    cache = _get_default_cache(workspace)
    cache.threshold_lines = threshold_lines
    return cache.run(command, timeout_s=timeout_s, force=force)


async def arun_cached(
    command: str,
    workspace: str | Path = ".",
    *,
    timeout_s: float = 120.0,
    force: bool = False,
    threshold_lines: int = DEFAULT_THRESHOLD_LINES,
) -> CachedResult:
    """Run *command* with default cache (async)."""
    cache = _get_default_cache(workspace)
    cache.threshold_lines = threshold_lines
    return await cache.arun(command, timeout_s=timeout_s, force=force)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="ExecutionCache demo")
    ap.add_argument("command", nargs="?", default="echo hello && seq 1 30")
    ap.add_argument("--force", action="store_true", help="Bypass cache")
    ap.add_argument("--workspace", default=".")
    args = ap.parse_args()

    c = ExecutionCache(workspace=args.workspace)
    print(f"Fingerprint: {c.fingerprint()}")
    res = c.run(args.command, force=args.force)
    print(f"{'[CACHED]' if res.cached else '[EXECUTED]'} exit={res.exit_code} cached={res.cached}")
    print(f"Preview: {res.preview[:200]}")
    print(f"Log: {res.log_path}  Last: {res.last_path}  Truncated: {res.truncated}")
