"""Git-aware context extraction for Wisp.

Extracts git state (branch, uncommitted changes, recent commits) via the git CLI
and formats it for injection into the system prompt. Also provides guard checks
for files with pending changes.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

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


def _run_git(args: list[str], cwd: str, timeout: int = 10) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        logger.debug("git not found in PATH")
        return 1, "", "git not found"
    except subprocess.TimeoutExpired:
        logger.warning("git command timed out: %s", " ".join(args))
        return 1, "", "timeout"
    except Exception as e:
        logger.warning("git command failed: %s", e)
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
