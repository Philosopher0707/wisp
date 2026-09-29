"""Git tools for Wisp — status, diff, branch, commit, push, and the GitHub workflow.

All operations delegate to wisp.git_context for actual git commands. The GitHub tools (PR view,
list, checks, comment, close, merge; failed-run logs; sync with the base branch) run on the host
as fixed argument lists, take no repository, and are how an agent whose bash is sandboxed still
follows the workflow.
"""

import logging


logger = logging.getLogger(__name__)


def tool_git_status(workspace: str = ".") -> str:
    """Show git status for the workspace."""
    from wisp.git_context import format_git_context
    result = format_git_context(workspace)
    if not result:
        return "Not a git repository (or git not available)."
    return result


def tool_git_diff(path: str = "", staged: bool = False, workspace: str = ".") -> str:
    """Show git diff for a file or the entire workspace."""
    from wisp.git_context import get_file_diff, get_workspace_diff
    if path:
        result = get_file_diff(path, workspace, staged=staged)
    else:
        result = get_workspace_diff(workspace, staged=staged)
    if not result:
        return "No diff available (not a git repo, file not tracked, or no changes)."
    return result


def tool_git_branch(action: str, name: str = "", workspace: str = ".") -> str:
    """List branches, create a new branch, or switch to an existing one."""
    from wisp.git_context import list_branches, create_branch, switch_branch
    if action == "list":
        code, out, err = list_branches(workspace)
    elif action == "create":
        if not name:
            return "Error: branch name required for 'create'"
        code, out, err = create_branch(name, workspace)
    elif action == "switch":
        if not name:
            return "Error: branch name required for 'switch'"
        code, out, err = switch_branch(name, workspace)
    else:
        return f"Error: unknown action '{action}'. Use: list, create, switch."
    if code != 0:
        return f"Error: {err or out}"
    return out or "OK"


def tool_git_commit(message: str, files: str = "", workspace: str = ".") -> str:
    """Stage files and commit with a message."""
    from wisp.git_context import commit
    file_list = [f.strip() for f in files.split(",") if f.strip()] if files else ["."]
    code, out, err = commit(file_list, message, workspace)
    if code != 0:
        return f"Error: {err or out}"
    return out or "✓ Committed"


def tool_git_push(set_upstream: bool = False, workspace: str = ".") -> str:
    """Push current branch to remote."""
    from wisp.git_context import push
    code, out, err = push(workspace, set_upstream=set_upstream)
    if code != 0:
        return f"Error: {err or out}"
    return out or "✓ Pushed"


def tool_gh_pr_create(title: str, body: str = "", workspace: str = ".") -> str:
    """Create a GitHub pull request using gh CLI."""
    from wisp.git_context import create_pr
    code, out, err = create_pr(title, body, workspace)
    if code != 0:
        return f"Error: {err or out}\n(Is 'gh' CLI installed and authenticated?)"
    return out or "✓ PR created"


# ── GitHub workflow ──────────────────────────────────────────────────────────

_OUTPUT_CAP = 20_000


def _cap(text: str) -> str:
    if len(text) <= _OUTPUT_CAP:
        return text
    return text[:_OUTPUT_CAP] + f"\n… [truncated {len(text) - _OUTPUT_CAP} chars]"


def _render(result: tuple[int, str, str], ok: str = "OK", informative: tuple[int, ...] = (0,)) -> str:
    """Output on success, `Refused:` when a policy said no, `Error:` when the command failed."""
    from wisp.git_context import REFUSED

    code, out, err = result
    text = (out or "").strip()
    if code in informative and text:
        return _cap(text)
    if code == 0:
        return ok
    if code == REFUSED:
        return f"Refused: {err or out}"
    return f"Error: {err or out}"


def tool_git_log(limit: int = 20, path: str = "", workspace: str = ".") -> str:
    """Recent commits on the current branch (optionally for one path)."""
    from wisp.git_context import log
    return _render(log(workspace, limit, path), ok="No commits.")


def tool_git_fetch(workspace: str = ".") -> str:
    """Fetch origin (updates remote-tracking refs only; the working tree is untouched)."""
    from wisp.git_context import fetch
    return _render(fetch(workspace), ok="✓ Fetched origin")


def tool_gh_pr_view(number: int, workspace: str = ".") -> str:
    """Show a pull request of this repository as JSON (state, checks, review decision, body)."""
    from wisp.git_context import pr_view
    return _render(pr_view(number, workspace))


def tool_gh_pr_list(state: str = "open", limit: int = 20, workspace: str = ".") -> str:
    """List this repository's pull requests (state: open, closed, merged, all)."""
    from wisp.git_context import pr_list
    return _render(pr_list(workspace, state, limit), ok="No pull requests.")


def tool_gh_pr_checks(number: int, workspace: str = ".") -> str:
    """CI checks of a pull request. Pending and failing checks are the answer, not an error."""
    from wisp.git_context import pr_checks
    return _render(pr_checks(number, workspace), ok="No checks reported.", informative=(0, 1, 8))


def tool_gh_run_failed_logs(run_id: int, workspace: str = ".") -> str:
    """Logs of the failed steps of a workflow run (capped)."""
    from wisp.git_context import run_failed_logs
    return _render(run_failed_logs(run_id, workspace), ok="No failed steps.")


def tool_gh_pr_comment(number: int, body: str, workspace: str = ".") -> str:
    """Comment on a pull request of this repository."""
    from wisp.git_context import pr_comment
    return _render(pr_comment(number, body, workspace), ok="✓ Commented")


def tool_gh_pr_close(number: int, comment: str = "", workspace: str = ".") -> str:
    """Close a pull request of this repository, optionally with a closing comment."""
    from wisp.git_context import pr_close
    return _render(pr_close(number, comment, workspace), ok="✓ Closed")


def tool_gh_pr_merge(number: int, workspace: str = ".") -> str:
    """Merge a pull request (merge commit) — only if it is open, not a draft, mergeable and every check is green."""
    from wisp.git_context import pr_merge
    return _render(pr_merge(number, workspace), ok="✓ Merged")


def tool_git_sync_base(base: str = "main", workspace: str = ".") -> str:
    """Merge origin/<base> into the current branch (never a rebase); conflicts are left for you to resolve."""
    from wisp.git_context import sync_base
    return _render(sync_base(base, workspace))
