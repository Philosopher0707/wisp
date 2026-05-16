"""Git tools for Wisp — status, diff, branch, commit, push, PR creation.

All operations delegate to wisp.git_context for actual git commands.
"""

import logging

from wisp.tools._utils import _validate_string

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
