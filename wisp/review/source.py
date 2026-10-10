"""Where a review's diff and file contents come from: real `git` and, for a pull request, the read-only `gh`.

Everything here treats its inputs as untrusted: a ref is validated before it reaches a command line (no option-shaped or shell-shaped refs), git runs with the
repository's own program hooks switched off (`--no-ext-diff`, `--no-textconv`, `core.fsmonitor=false`), a path named in a diff is resolved against the workspace
and refused if it leaves it or is a symlink, and every read has a size bound. Nothing here writes to the repository or to GitHub.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from wisp.core.gates import secrets as secret_gate
from wisp.review.checks import is_test_path

MAX_DIFF_BYTES = 30 * 1024 * 1024
MAX_UNTRACKED_FILES = 200
MAX_UNTRACKED_BYTES = 1024 * 1024
MAX_POST_IMAGE_BYTES = 2 * 1024 * 1024
MAX_TEST_FILES = 5000
MAX_TEST_FILE_BYTES = 1024 * 1024
GIT_TIMEOUT_S = 60

_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@/~^\-]{0,127}$")
_SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "vendor", ".tox", ".mypy_cache", ".ruff_cache"})
_DIFF_FLAGS = ("--no-color", "--no-ext-diff", "--no-textconv", "-M")
#: Wisp's own state (the session store, run logs). It sits in the workspace while Wisp runs, so it is never the user's change.
STATE_DIRS = (".wisp", ".agent")
_STATE_PATHSPEC = tuple(f":(exclude){d}" for d in STATE_DIRS)
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class SourceError(Exception):
    """The diff could not be read. The message says why in words the user can act on."""


@dataclass(frozen=True)
class DiffSource:
    kind: str  # uncommitted | staged | range | commit | pr
    base: str = ""
    head: str = ""
    commit: str = ""
    pr: int = 0

    def label(self) -> str:
        return {
            "uncommitted": "uncommitted changes", "staged": "staged changes", "range": f"{self.base}...{self.head}", "commit": f"commit {self.commit}", "pr": f"pull request #{self.pr}",
        }.get(self.kind, self.kind)


def validate_ref(ref: str, field: str) -> str:
    if not isinstance(ref, str) or not _REF.match(ref) or ".." in ref:
        raise SourceError(f"invalid git ref for {field}: {ref[:40]!r} (letters, digits and . _ @ / ~ ^ - only, starting with a letter or digit)")
    return ref


def _env() -> dict[str, str]:
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C", "GIT_PAGER": "cat", "PAGER": "cat"}


def _git(workspace: str, *args: str, ok: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[bytes]:
    command = ["git", "-c", "core.quotepath=false", "-c", "core.fsmonitor=false", "-c", "core.pager=cat", *args]
    try:
        proc = subprocess.run(command, cwd=workspace, capture_output=True, timeout=GIT_TIMEOUT_S, env=_env(), check=False)
    except FileNotFoundError as exc:
        raise SourceError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise SourceError(f"git took longer than {GIT_TIMEOUT_S}s: {' '.join(args[:3])}") from exc
    if proc.returncode not in ok:
        message = secret_gate.scrub(proc.stderr.decode("utf-8", "replace").strip()).text[:300]
        raise SourceError(f"git {args[0] if args else ''} failed: {message}")
    return proc


def _require_repo(workspace: str) -> None:
    probe = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=workspace, capture_output=True, env=_env(), check=False) if shutil.which("git") else None
    if probe is None:
        raise SourceError("git is not installed or not on PATH")
    if probe.returncode != 0:
        raise SourceError(f"not a git repository: {workspace}")


def _ref_exists(workspace: str, ref: str) -> bool:
    return _git(workspace, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", ok=(0, 1)).returncode == 0


def _text(proc: subprocess.CompletedProcess[bytes]) -> str:
    if len(proc.stdout) > MAX_DIFF_BYTES:
        raise SourceError(f"the diff is over {MAX_DIFF_BYTES // (1024 * 1024)} MB; review a narrower range")
    return proc.stdout.decode("utf-8", "replace")


def read_diff(workspace: str, source: DiffSource) -> str:
    """The unified diff for `source`. Raises SourceError with a reason the user can act on."""
    if source.kind == "pr":
        return _gh_pr_diff(workspace, source.pr)
    _require_repo(workspace)
    if source.kind == "staged":
        return _text(_git(workspace, "diff", "--cached", *_DIFF_FLAGS, "--", ".", *_STATE_PATHSPEC))
    if source.kind == "uncommitted":
        has_head = _git(workspace, "rev-parse", "--verify", "--quiet", "HEAD", ok=(0, 1)).returncode == 0
        base = "HEAD" if has_head else _git(workspace, "hash-object", "-t", "tree", "/dev/null").stdout.decode().strip()
        return _text(_git(workspace, "diff", *_DIFF_FLAGS, base, "--", ".", *_STATE_PATHSPEC)) + _untracked_diffs(workspace)
    if source.kind == "range":
        base, head = validate_ref(source.base, "base"), validate_ref(source.head, "head")
        for ref in (base, head):
            if not _ref_exists(workspace, ref):
                raise SourceError(f"git ref not found: {ref}")
        return _text(_git(workspace, "diff", *_DIFF_FLAGS, f"{base}...{head}", "--"))
    if source.kind == "commit":
        commit = validate_ref(source.commit, "commit")
        if not _ref_exists(workspace, commit):
            raise SourceError(f"git ref not found: {commit}")
        return _text(_git(workspace, "show", "--format=", *_DIFF_FLAGS, commit, "--"))
    raise SourceError(f"unknown diff source: {source.kind}")


def _untracked_diffs(workspace: str) -> str:
    """New files git does not track yet, as added-file diffs: bounded in number and size, symlinks and non-regular files skipped."""
    listing = _git(workspace, "ls-files", "--others", "--exclude-standard", "-z").stdout.decode("utf-8", "replace")
    out: list[str] = []
    taken = 0
    for name in sorted(p for p in listing.split("\0") if p and p.split("/", 1)[0] not in STATE_DIRS):
        if taken >= MAX_UNTRACKED_FILES:
            break
        try:
            info = os.lstat(os.path.join(workspace, name))
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_UNTRACKED_BYTES:
            continue
        proc = _git(workspace, "diff", "--no-index", *_DIFF_FLAGS, "--", "/dev/null", name, ok=(0, 1))
        out.append(proc.stdout.decode("utf-8", "replace"))
        taken += 1
    return "".join(out)


def _gh(workspace: str, *args: str) -> str:
    if shutil.which("gh") is None:
        raise SourceError("the GitHub CLI (gh) is not installed or not on PATH; it is needed to read a pull request")
    try:
        proc = subprocess.run(["gh", *args], cwd=workspace, capture_output=True, timeout=GIT_TIMEOUT_S, env=_env(), check=False)
    except subprocess.TimeoutExpired as exc:
        raise SourceError(f"gh took longer than {GIT_TIMEOUT_S}s") from exc
    if proc.returncode != 0:
        raise SourceError("gh " + " ".join(args[:2]) + " failed: " + secret_gate.scrub(proc.stderr.decode("utf-8", "replace").strip()).text[:300])
    if len(proc.stdout) > MAX_DIFF_BYTES:
        raise SourceError(f"the pull request diff is over {MAX_DIFF_BYTES // (1024 * 1024)} MB")
    return proc.stdout.decode("utf-8", "replace")


def _gh_pr_diff(workspace: str, number: int) -> str:
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise SourceError("the pull request number must be a positive integer")
    return _gh(workspace, "pr", "diff", str(number), "--color=never")


# ── file contents after the change ───────────────────────────────────────────

def _inside(root: Path, path: str) -> Path | None:
    if not path or path.startswith(("/", "\\")) or ".." in Path(path).parts:
        return None
    candidate = root / path
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if candidate.is_symlink() or (resolved != root and root not in resolved.parents):
        return None
    return candidate


def _read_bounded(path: Path, limit: int) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > limit:
            return None
        return path.read_bytes().decode("utf-8", "replace")
    except OSError:
        return None


def post_image_reader(workspace: str, source: DiffSource) -> Callable[[str], str | None]:
    """A function from a changed file's path to its text after the change, or None when it cannot be read (never a guess)."""
    root = Path(workspace).resolve()

    def from_worktree(path: str) -> str | None:
        target = _inside(root, path)
        return _read_bounded(target, MAX_POST_IMAGE_BYTES) if target is not None else None

    def from_git(ref: str) -> Callable[[str], str | None]:
        def read(path: str) -> str | None:
            if not path or path.startswith(("/", "\\", "-")) or ".." in Path(path).parts or ":" in path:
                return None
            try:
                proc = _git(workspace, "show", f"{ref}:{path}", ok=(0, 128))
            except SourceError:
                return None
            if proc.returncode != 0 or len(proc.stdout) > MAX_POST_IMAGE_BYTES:
                return None
            return proc.stdout.decode("utf-8", "replace")

        return read

    if source.kind == "uncommitted":
        return from_worktree
    if source.kind == "staged":
        return from_git("")
    if source.kind == "range":
        return from_git(validate_ref(source.head, "head"))
    if source.kind == "commit":
        return from_git(validate_ref(source.commit, "commit"))
    if source.kind == "pr":
        return _pr_reader(workspace, source.pr, from_git)
    return lambda path: None


def _pr_reader(workspace: str, number: int, from_git: Callable[[str], Callable[[str], str | None]]) -> Callable[[str], str | None]:
    cache: dict[str, Callable[[str], str | None]] = {}

    def read(path: str) -> str | None:
        if "reader" not in cache:
            reader: Callable[[str], str | None] = lambda p: None
            try:
                import json

                sha = json.loads(_gh(workspace, "pr", "view", str(number), "--json", "headRefOid")).get("headRefOid", "")
                if re.fullmatch(r"[0-9a-f]{40,64}", sha) and _git(workspace, "cat-file", "-e", f"{sha}^{{commit}}", ok=(0, 1, 128)).returncode == 0:
                    reader = from_git(sha)
            except (SourceError, ValueError):
                pass
            cache["reader"] = reader
        return cache["reader"](path)

    return read


# ── do the tests already mention a name? ─────────────────────────────────────

class SymbolSearcher:
    """Whether any test file mentions a name as a whole word. Built lazily, bounded; once a bound is hit it assumes tested (unknown is not a claim) and says so."""

    def __init__(self, workspace: str, gaps: list[str] | None = None) -> None:
        self._root = workspace
        self._gaps = gaps
        self._tokens: set[str] | None = None
        self.truncated = False

    def _build(self) -> set[str]:
        tokens: set[str] = set()
        seen = 0
        for dirpath, dirnames, filenames in os.walk(self._root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, self._root).replace(os.sep, "/")
                if not is_test_path(rel):
                    continue
                seen += 1
                if seen > MAX_TEST_FILES:
                    self.truncated = True
                    return tokens
                text = _read_bounded(Path(full), MAX_TEST_FILE_BYTES)
                if text is None:
                    self.truncated = True
                    continue
                tokens.update(_TOKEN.findall(text))
        return tokens

    def __call__(self, name: str) -> bool:
        if self._tokens is None:
            self._tokens = self._build()
            if self.truncated and self._gaps is not None:
                self._gaps.append("missing-tests: the search of the test files hit its bound, so new public symbols were assumed tested")
        return True if self.truncated else name in self._tokens


def symbol_searcher(workspace: str, gaps: list[str] | None = None) -> SymbolSearcher:
    return SymbolSearcher(workspace, gaps)
