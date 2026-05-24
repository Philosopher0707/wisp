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
- subagent: spawn subagent stub
- tests: run test suite
- registry: TOOL_SCHEMAS, TOOL_IMPLS, execute_tool
"""

# ── Core exceptions and utilities ────────────────────────────────────
from wisp.tools.errors import ToolError
from wisp.tools._utils import (
    check_dangerous_command,
    set_collaboration_tools,
    set_lsp_manager,
    _get_dependents,
)

# ── Registry (schemas, implementations, execute_tool) ────────────────────
from wisp.tools.registry import (
    TOOL_SCHEMAS,
    TOOL_IMPLS,
    ToolRegistry,
    default_registry,
    execute_tool,
    _build_tool_metadata,
)

# ── Submodule implementations ────────────────────────────────────────
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
from wisp.tools.tests import tool_run_tests
