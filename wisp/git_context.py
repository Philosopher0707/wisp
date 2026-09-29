"""Git-aware context extraction for Wisp.

Extracts git state (branch, uncommitted changes, recent commits) via the git CLI
and formats it for injection into the system prompt. Also provides guard checks
for files with pending changes.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class GitState:
    """Structured git state for a workspace."""

    branch: str = ""
    is_dirty: bool = False
    is_git_repo: bool = False
    untracked_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    staged_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    recent_commits: list[str] = field(default_factory=list)
    ahead_behind: str = ""  # e.g. "+2 -1" or ""
    merge_conflict_files: list[str] = field(default_factory=list)


def _run_git(args: list[str], cwd: str, timeout: int = 10, command: str = "git") -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            [command] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        logger.debug("%s not found in PATH", command)
        return 1, "", f"{command} not found"
    except subprocess.TimeoutExpired:
        logger.warning("command timed out: %s", " ".join(args))
        return 1, "", "timeout"
    except Exception as e:
        logger.warning("command failed: %s", e)
        return 1, "", str(e)


def _is_git_repo(cwd: str) -> bool:
    """Check if cwd is inside a git repository."""
    rc, _, _ = _run_git(["rev-parse", "--git-dir"], cwd)
    return rc == 0


def get_git_state(workspace: str) -> Optional[GitState]:
    """Extract full git state for a workspace. Returns None if not a git repo."""
    ws = str(Path(workspace).resolve())

    if not _is_git_repo(ws):
        return None

    state = GitState(is_git_repo=True)

    # ── Branch ──
    rc, out, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], ws)
    if rc == 0:
        branch = out.strip()
        state.branch = branch if branch else "(no commits yet)"
    else:
        # No commits yet — try to get the default branch name
        rc2, out2, _ = _run_git(["symbolic-ref", "--short", "HEAD"], ws)
        if rc2 == 0:
            state.branch = out2.strip()
        else:
            state.branch = "(no commits yet)"

    # ── Status (porcelain) ──
    rc, out, _ = _run_git(["status", "--porcelain", "-u"], ws)
    if rc == 0:
        lines = out.rstrip("\n").split("\n")
        for line in lines:
            if not line or len(line) < 3:
                continue
            status = line[0:2]
            # Path starts after the status codes; strip leading whitespace
            filepath = line[2:].strip().split(" -> ")[0]

            # XY codes: https://git-scm.com/docs/git-status#_short_format
            if status == "??":
                state.untracked_files.append(filepath)
            elif status == "UU" or status.startswith("U") or status.endswith("U"):
                state.merge_conflict_files.append(filepath)
            elif status[0] != " " and status[0] != "?":
                state.staged_files.append(filepath)
            elif status[1] == "M":
                state.modified_files.append(filepath)
            elif status[1] == "D":
                state.deleted_files.append(filepath)

    # ── Ahead/behind ──
    rc, out, _ = _run_git(["rev-list", "--left-right", "--count", "HEAD...@{u}"], ws)
    if rc == 0:
        parts = out.strip().split("\t")
        if len(parts) == 2:
            ahead, behind = parts
            state.ahead_behind = f"+{ahead} -{behind}"

    # ── Recent commits ──
    rc, out, _ = _run_git(["log", "--oneline", "-5"], ws)
    if rc == 0:
        state.recent_commits = [line.strip() for line in out.strip().split("\n") if line.strip()]

    state.is_dirty = bool(
        state.untracked_files
        or state.modified_files
        or state.staged_files
        or state.deleted_files
        or state.merge_conflict_files
    )

    return state


def has_uncommitted_changes(filepath: str, workspace: str) -> bool:
    """Check if a specific file has uncommitted changes."""
    if not _is_git_repo(workspace):
        return False
    rc, out, _ = _run_git(["status", "--porcelain", filepath], workspace)
    if rc != 0:
        return False
    return bool(out.strip())


def get_file_diff(filepath: str, workspace: str, staged: bool = False) -> str:
    """Get git diff for a specific file."""
    if not _is_git_repo(workspace):
        return ""
    args = ["diff", "--no-color"]
    if staged:
        args.append("--staged")
    args.append(filepath)
    rc, out, _ = _run_git(args, workspace)
    if rc != 0:
        return ""
    return out


def get_workspace_diff(workspace: str, staged: bool = False) -> str:
    """Get git diff for the entire workspace."""
    if not _is_git_repo(workspace):
        return ""
    args = ["diff", "--no-color"]
    if staged:
        args.append("--staged")
    rc, out, _ = _run_git(args, workspace)
    if rc != 0:
        return ""
    return out


def format_git_context(workspace: str) -> str:
    """Format git state as a system prompt block. Returns empty string if not a git repo."""
    state = get_git_state(workspace)
    if state is None:
        return ""

    lines = ["## Git Context"]
    lines.append(f"- Branch: {state.branch or '(detached HEAD)'}")

    if state.ahead_behind:
        lines.append(f"- Remote: {state.ahead_behind} (ahead/behind)")

    # Change summary
    changes: list[str] = []
    if state.staged_files:
        changes.append(f"{len(state.staged_files)} staged")
    if state.modified_files:
        changes.append(f"{len(state.modified_files)} modified")
    if state.untracked_files:
        changes.append(f"{len(state.untracked_files)} untracked")
    if state.deleted_files:
        changes.append(f"{len(state.deleted_files)} deleted")
    if state.merge_conflict_files:
        changes.append(f"{len(state.merge_conflict_files)} conflicted")

    if changes:
        lines.append(f"- Uncommitted: {', '.join(changes)}")
    else:
        lines.append("- Working tree clean")

    # Recent commits
    if state.recent_commits:
        lines.append("- Recent commits:")
        for commit in state.recent_commits:
            lines.append(f"  - {commit}")

    # Warnings for files with pending changes
    warned_files = state.modified_files[:3] + state.merge_conflict_files[:3]
    if warned_files:
        lines.append("- ⚠️ Files with pending changes:")
        for f in warned_files:
            lines.append(f"  - {f}")

    return "\n".join(lines)


def format_git_status_short(workspace: str) -> str:
    """One-line git status for REPL header."""
    state = get_git_state(workspace)
    if state is None:
        return ""

    parts = [f"git:{state.branch}"]
    if state.is_dirty:
        counts = []
        if state.staged_files:
            counts.append(f"+{len(state.staged_files)}")
        if state.modified_files:
            counts.append(f"~{len(state.modified_files)}")
        if state.untracked_files:
            counts.append(f"?{len(state.untracked_files)}")
        if state.merge_conflict_files:
            counts.append(f"!{len(state.merge_conflict_files)}")
        parts.append(" ".join(counts))
    else:
        parts.append("clean")

    return " ".join(parts)


# ── Git workflow operations ──────────────────────────────────────────


def list_branches(workspace: str) -> tuple[int, str, str]:
    """List all branches (local + remote)."""
    return _run_git(["branch", "-a"], workspace)


def create_branch(name: str, workspace: str) -> tuple[int, str, str]:
    """Create and switch to a new branch."""
    if not _is_git_repo(workspace):
        return (1, "", "not a git repository")
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9._/-]*$', name):
        return (1, "", f"invalid branch name: {name}")
    return _run_git(["checkout", "-b", name], workspace)


def switch_branch(name: str, workspace: str) -> tuple[int, str, str]:
    """Switch to an existing branch."""
    if not _is_git_repo(workspace):
        return (1, "", "not a git repository")
    return _run_git(["checkout", name], workspace)


def commit(files: list[str], message: str, workspace: str) -> tuple[int, str, str]:
    """Stage given files and commit with message."""
    if not _is_git_repo(workspace):
        return (1, "", "not a git repository")
    if not message.strip():
        return (1, "", "commit message cannot be empty")
    code, out, err = _run_git(["add"] + files, workspace)
    if code != 0:
        return (code, out, err)
    return _run_git(["commit", "-m", message], workspace)


def push(workspace: str, set_upstream: bool = False, force: bool = False) -> tuple[int, str, str]:
    """Push current branch to remote. force=True blocked by tools.py guard."""
    if not _is_git_repo(workspace):
        return (1, "", "not a git repository")
    code, out, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], workspace)
    branch = out.strip() if code == 0 else ""
    if branch in PROTECTED_BRANCHES:
        return (1, "", f"refusing to push {branch} directly — push a feature branch and open a PR")
    args = ["push"]
    if set_upstream:
        args.extend(["-u", "origin", "HEAD"])
    if force:
        args.append("--force-with-lease")
    return _run_git(args, workspace)


def create_pr(title: str, body: str, workspace: str) -> tuple[int, str, str]:
    """Create a GitHub PR using gh CLI."""
    if not _is_git_repo(workspace):
        return (1, "", "not a git repository")
    args = ["pr", "create", "--title", title, "--body", body]
    return _run_git(args, workspace, command="gh")


# ── GitHub workflow: reads, and writes that a policy stands in front of ──────
#
# Every call is a fixed argument list run in the workspace, never a shell string, and none takes a
# repository: `gh` resolves it from the workspace's own remote, so a caller cannot aim a write at
# another repository. A policy refusal returns REFUSED (a failed command returns 1), so the tool
# layer can tell "I will not" from "it did not work".

REFUSED = 2
PROTECTED_BRANCHES = frozenset({"main", "master"})
_BASE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_PR_STATES = frozenset({"open", "closed", "merged", "all"})
_PR_FIELDS = ("number,title,state,isDraft,mergeable,reviewDecision,baseRefName,headRefName,"
              "url,additions,deletions,changedFiles,body,statusCheckRollup")
_MERGE_FIELDS = "state,isDraft,mergeable,reviewDecision,statusCheckRollup"
_GREEN = frozenset({"SUCCESS", "SKIPPED", "NEUTRAL"})
_NOT_A_NUMBER = (1, "", "number must be a positive integer")


def positive_int(value: object) -> int | None:
    """A positive int, or a string of ASCII digits for one; anything else is None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        n = value
    elif isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        n = int(value.strip())
    else:
        return None
    return n if n > 0 else None


def _clamped(value: object, default: int, ceiling: int = 100) -> int:
    n = positive_int(value)
    return min(n, ceiling) if n is not None else default


def _gh(args: list[str], workspace: str, timeout: int = 30) -> tuple[int, str, str]:
    return _run_git(args, workspace, timeout=timeout, command="gh")


def log(workspace: str, limit: object = 20, path: str = "") -> tuple[int, str, str]:
    args = ["log", f"-{_clamped(limit, 20)}", "--oneline", "--decorate"]
    if path:
        args += ["--", path]
    return _run_git(args, workspace)


def fetch(workspace: str) -> tuple[int, str, str]:
    return _run_git(["fetch", "origin"], workspace, timeout=60)


def pr_view(number: object, workspace: str) -> tuple[int, str, str]:
    n = positive_int(number)
    if n is None:
        return _NOT_A_NUMBER
    return _gh(["pr", "view", str(n), "--json", _PR_FIELDS], workspace)


def pr_list(workspace: str, state: str = "open", limit: object = 20) -> tuple[int, str, str]:
    if state not in _PR_STATES:
        return (1, "", f"state must be one of: {', '.join(sorted(_PR_STATES))}")
    return _gh(["pr", "list", "--state", state, "--limit", str(_clamped(limit, 20))], workspace)


def pr_checks(number: object, workspace: str) -> tuple[int, str, str]:
    n = positive_int(number)
    if n is None:
        return _NOT_A_NUMBER
    return _gh(["pr", "checks", str(n)], workspace)


def run_failed_logs(run_id: object, workspace: str) -> tuple[int, str, str]:
    n = positive_int(run_id)
    if n is None:
        return (1, "", "run_id must be a positive integer")
    return _gh(["run", "view", str(n), "--log-failed"], workspace, timeout=60)


def pr_comment(number: object, body: str, workspace: str) -> tuple[int, str, str]:
    n = positive_int(number)
    if n is None:
        return _NOT_A_NUMBER
    if not isinstance(body, str) or not body.strip():
        return (1, "", "comment body cannot be empty")
    return _gh(["pr", "comment", str(n), "--body", body], workspace)


def pr_close(number: object, comment: str, workspace: str) -> tuple[int, str, str]:
    n = positive_int(number)
    if n is None:
        return _NOT_A_NUMBER
    args = ["pr", "close", str(n)]
    if isinstance(comment, str) and comment.strip():
        args += ["--comment", comment]
    return _gh(args, workspace)


def merge_blockers(pr: dict[str, Any]) -> list[str]:
    """Why this PR may not be merged; empty means every proof is present."""
    blockers: list[str] = []
    if pr.get("state") != "OPEN":
        blockers.append(f"the PR is {pr.get('state') or 'in an unknown state'}, not OPEN")
    if pr.get("isDraft"):
        blockers.append("the PR is a draft")
    if pr.get("mergeable") != "MERGEABLE":
        blockers.append(f"mergeable is {pr.get('mergeable') or 'unknown'}, not MERGEABLE")
    if pr.get("reviewDecision") == "CHANGES_REQUESTED":
        blockers.append("changes were requested")
    checks = pr.get("statusCheckRollup") or []
    if not checks:
        blockers.append("no checks are reported, so nothing vouches for it")
    for check in checks:
        name = check.get("name") or check.get("context") or "a check"
        status = str(check.get("status") or "").upper()
        verdict = str(check.get("conclusion") or check.get("state") or "").upper()
        if status and status != "COMPLETED":
            blockers.append(f"{name} is still {status.lower()}")
        elif verdict not in _GREEN:
            blockers.append(f"{name}: {verdict.lower() or 'no result'}")
    return blockers


def pr_merge(number: object, workspace: str) -> tuple[int, str, str]:
    """Merge (a plain merge commit) only when every proof is present. Never --admin/--auto."""
    n = positive_int(number)
    if n is None:
        return _NOT_A_NUMBER
    code, out, err = _gh(["pr", "view", str(n), "--json", _MERGE_FIELDS], workspace)
    if code != 0:
        return (1, out, err or "could not read the PR")
    try:
        pr = json.loads(out)
    except ValueError:
        return (1, "", "could not parse the PR from gh")
    if not isinstance(pr, dict):
        return (1, "", "could not parse the PR from gh")
    blockers = merge_blockers(pr)
    if blockers:
        return (REFUSED, "", f"PR #{n} is not mergeable: " + "; ".join(blockers))
    return _gh(["pr", "merge", str(n), "--merge"], workspace, timeout=60)


def sync_base(base: str, workspace: str) -> tuple[int, str, str]:
    """Merge origin/<base> into the current branch. Never rebases; conflicts are left to resolve."""
    if (not isinstance(base, str) or not _BASE_RE.fullmatch(base)
            or ".." in base or base.endswith(("/", ".lock"))):
        return (1, "", f"invalid base branch: {base!r}")
    code, out, err = _run_git(["status", "--porcelain", "--untracked-files=no"], workspace)
    if code != 0:
        return (code, out, err)
    if out.strip():
        return (REFUSED, "", "uncommitted changes to tracked files — commit or stash them first")
    code, out, err = _run_git(["fetch", "origin", base], workspace, timeout=60)
    if code != 0:
        return (code, out, err)
    code, out, err = _run_git(["merge", "--no-edit", f"origin/{base}"], workspace, timeout=60)
    if code == 0:
        return (0, out or f"already up to date with origin/{base}", "")
    _, conflicted, _ = _run_git(["diff", "--name-only", "--diff-filter=U"], workspace)
    files = [f for f in conflicted.splitlines() if f.strip()]
    if files:
        return (1, out, "merge stopped on conflicts (nothing was aborted); resolve, then git_commit: "
                        + ", ".join(files))
    return (code, out, err)
