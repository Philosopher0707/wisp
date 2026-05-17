"""Wisp tools package — domain-specific tool modules.

This package splits the monolithic wisp/tools.py into focused modules:
- filesystem: read, write, edit, list files
- bash: run shell commands
- web: fetch URLs, search the web
- git: status, diff, branch, commit, push, PR
- lsp: diagnostics, definition, references, hover, symbols
- memory: remember, recall
- search: symbol search, semantic codebase search
- plan: create plans, mark steps, update status
- diagnose: error diagnosis

Submodule implementations override legacy ones where available.
"""

# ── Import legacy base FIRST to avoid circular imports ────────────────
# Submodules import ToolError, check_dangerous_command, etc. from wisp.tools.
# We must pull these from _legacy and _utils before importing submodules.
from wisp.tools._legacy import (
    TOOL_SCHEMAS,
    TOOL_IMPLS,
    execute_tool,
    ToolError,
    _build_tool_metadata,
    check_dangerous_command,
    _tool_spawn_subagent_stub,
    tool_run_tests,
    set_collaboration_tools,
    set_lsp_manager,
)
from wisp.tools._utils import ToolError as _ToolError  # noqa: F811 — same class

# ── Import submodule implementations (preferred) ───────────────────────
from wisp.tools.filesystem import (
    tool_read_file,
    tool_write_file,
    tool_edit_file,
    tool_edit_file_multi,
    tool_list_files,
)
from wisp.tools.bash import tool_run_bash
from wisp.tools.web import tool_web_fetch, tool_web_search
from wisp.tools.git import (
    tool_git_status,
    tool_git_diff,
    tool_git_branch,
    tool_git_commit,
    tool_git_push,
    tool_gh_pr_create,
)
from wisp.tools.lsp import (
    tool_lsp_diagnostics,
    tool_lsp_definition,
    tool_lsp_references,
    tool_lsp_hover,
    tool_lsp_symbols,
)
from wisp.tools.memory import tool_remember, tool_recall
from wisp.tools.search import tool_search_symbols, tool_search_codebase
from wisp.tools.plan import tool_plan_task, tool_mark_step_done, tool_update_plan
from wisp.tools.diagnose import tool_diagnose
from wisp.tools.long_horizon import (
    tool_run_long_task,
    tool_resume_task,
    tool_task_status,
    tool_list_tasks,
    tool_pause_task,
    tool_cancel_task,
    tool_cleanup_tasks,
    tool_task_output,
)

# Override legacy implementations with submodule versions where available
_SUBMODULE_OVERRIDES = {
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "edit_file_multi": tool_edit_file_multi,
    "list_files": tool_list_files,
    "run_bash": tool_run_bash,
    "web_fetch": tool_web_fetch,
    "web_search": tool_web_search,
    "git_status": tool_git_status,
    "git_diff": tool_git_diff,
    "git_branch": tool_git_branch,
    "git_commit": tool_git_commit,
    "git_push": tool_git_push,
    "gh_pr_create": tool_gh_pr_create,
    "lsp_diagnostics": tool_lsp_diagnostics,
    "lsp_definition": tool_lsp_definition,
    "lsp_references": tool_lsp_references,
    "lsp_hover": tool_lsp_hover,
    "lsp_symbols": tool_lsp_symbols,
    "remember": tool_remember,
    "recall": tool_recall,
    "search_symbols": tool_search_symbols,
    "search_codebase": tool_search_codebase,
    "plan_task": tool_plan_task,
    "mark_step_done": tool_mark_step_done,
    "update_plan": tool_update_plan,
    "diagnose": tool_diagnose,
    "run_long_task": tool_run_long_task,
    "resume_task": tool_resume_task,
    "task_status": tool_task_status,
    "list_tasks": tool_list_tasks,
    "pause_task": tool_pause_task,
    "cancel_task": tool_cancel_task,
    "cleanup_tasks": tool_cleanup_tasks,
    "task_output": tool_task_output,
}

# Apply overrides to the legacy registry
for _name, _impl in _SUBMODULE_OVERRIDES.items():
    if _name in TOOL_IMPLS:
        TOOL_IMPLS[_name] = _impl

# Also make submodules available for direct import
from wisp.tools import _utils
from wisp.tools import filesystem
from wisp.tools import bash
from wisp.tools import web
from wisp.tools import git
from wisp.tools import lsp
from wisp.tools import memory
from wisp.tools import search
from wisp.tools import plan
from wisp.tools import diagnose
from wisp.tools import long_horizon
