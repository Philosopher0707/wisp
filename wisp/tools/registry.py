"""Tool registry for Wisp.

Contains TOOL_SCHEMAS (OpenAI-compatible function schemas) and
TOOL_IMPLS (name → function mapping) for all built-in tools.
"""

import json
import logging
from pathlib import Path
from typing import Any, Optional

from wisp.tools.errors import ToolError

# Submodule implementations
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
from wisp.tools.subagent import tool_spawn_subagent
from wisp.tools.tests import tool_run_tests

logger = logging.getLogger(__name__)

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Returns the ENTIRE file by default. Use for reviewing code, configs, or logs. Max file size: 50 MB. For huge files, use offset/limit to read portions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file (relative to workspace or absolute)"},
                    "offset": {"type": "number", "description": "Starting line number (0-indexed). Default 0.", "default": 0},
                    "limit": {"type": "number", "description": "Max lines to read (default: 1,000,000 lines — effectively unlimited)."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file. Creates parent directories if needed. WARNING: Overwrites existing files. Max content size: 100 MB.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to write to"},
                    "content": {"type": "string", "description": "Full content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exact text in a file. The old_text must match exactly and be unique. Use for targeted edits instead of rewriting entire files. Supports Unicode fuzzy matching for smart quotes, dashes, and special spaces.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file"},
                    "old_text": {"type": "string", "description": "Exact text to replace (must be unique)"},
                    "new_text": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file_multi",
            "description": "Make multiple precise edits to a single file in one call. All edits[].old_text values are matched against the ORIGINAL file (not incrementally). Edits must not overlap. Use when changing multiple separate locations in one file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to edit"},
                    "edits": {
                        "type": "array",
                        "description": "One or more targeted replacements. Each edit is matched against the original file, not incrementally.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_text": {"type": "string", "description": "Exact text to replace (must be unique in the file and not overlap with other edits)"},
                                "new_text": {"type": "string", "description": "Replacement text"},
                            },
                            "required": ["old_text", "new_text"],
                        },
                    },
                },
                "required": ["path", "edits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "Run a bash command. Use for building, testing, git operations, or running scripts. Max command length: 4096 chars. Timeout: configurable (default 60s). Output truncated to 50K chars.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "timeout": {"type": "number", "description": "Timeout in seconds", "default": 60},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch content from a URL (web page, API endpoint, etc.). Returns extracted text content. Respects robots.txt and has 30s timeout. Max 100K chars returned.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch (http:// or https://)"},
                    "max_chars": {"type": "number", "description": "Maximum characters to return", "default": 10000},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a directory. Use to explore the workspace structure. Max 500 entries. No path traversal allowed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path", "default": "."},
                    "pattern": {"type": "string", "description": "Glob pattern (e.g., '*.py', '**/*.rs')", "default": "*"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_symbols",
            "description": "Search the code index for symbols (functions, classes, structs, traits, etc.) matching a query. Use to find where things are defined without reading every file. Returns file path, line number, and symbol kind.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term — matches against symbol names, kinds, and file paths (case-insensitive)"},
                    "max_results": {"type": "number", "description": "Maximum results to return", "default": 20},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Store a fact in cross-session memory so you remember it across conversations. Use for user preferences, project conventions, decisions made, or anything worth remembering long-term. IMPORTANT: DO NOT use this for information that is already in the current conversation context — the full conversation history is always available to you. Only use this for facts that should persist across multiple separate chat sessions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "The fact to remember. Be specific and concise."},
                },
                "required": ["fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "Search cross-session memory and past session summaries for relevant facts. Use when you need to actively recall something learned in PREVIOUS conversations — user preferences, past decisions, files touched, open tasks, etc. IMPORTANT: DO NOT use this to recall something the user just said in the current conversation — the full current conversation history is always available in context. Only use this for information from earlier sessions that may have been forgotten due to context window limits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for. Be specific — e.g. 'user preference for indentation', 'auth module decisions', 'files touched in API refactor'"},
                    "limit": {"type": "number", "description": "Max results to return (1-50)", "default": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_subagent",
            "description": "Spawn a specialist subagent to handle a scoped task (research, coding, testing) with its own iteration budget and timeout. The subagent runs in parallel and returns a structured result. Use when a task can be decomposed into an independent work unit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Specific instruction for the subagent. Be precise about what to produce."},
                    "tools": {"type": "array", "items": {"type": "string"}, "description": "Tool names the subagent may use. Omit or use ['all'] for full toolset.", "default": ["all"]},
                    "max_iterations": {"type": "number", "description": "Max agent loop iterations", "default": 15},
                    "timeout_seconds": {"type": "number", "description": "Hard timeout in seconds. If omitted, adaptive timeout is used based on task complexity.", "default": 120},
                    "output_format": {"type": "string", "description": "text | json | markdown | report", "default": "text"},
                    "worktree_isolated": {"type": "boolean", "description": "Run in isolated git worktree. Default false for speed.", "default": False},
                    "max_tokens": {"type": "number", "description": "Max tokens for subagent output. Prevents context overflow.", "default": 4000},
                    "output_schema": {"type": "object", "description": "JSON schema for structured output validation. Only used when output_format=json.", "default": None},
                    "auto_retry": {"type": "boolean", "description": "Automatically retry on failure with exponential backoff.", "default": True},
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show git status for the workspace: current branch, uncommitted files (staged, modified, untracked, deleted, conflicted), and recent commits. Returns empty string if not a git repository.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show git diff for a file or the entire workspace. Use to review uncommitted changes before editing. Returns empty string if not a git repository or no changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File to diff (omit for entire workspace)", "default": ""},
                    "staged": {"type": "boolean", "description": "Show staged changes instead of unstaged", "default": False},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose",
            "description": "Diagnose an error from test output, traceback, or command output. Returns error type, location, root cause, and fix suggestion. Use when tests fail, code crashes, or tools return errors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "error_output": {"type": "string", "description": "The error output, traceback, or test failure message to analyze"},
                },
                "required": ["error_output"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_task",
            "description": "Create a structured plan with subtasks. Break down a complex goal into numbered steps with complexity estimates, file targets, and dependencies. Use when the user asks to implement, refactor, or build something multi-step.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "High-level goal for the plan"},
                    "tasks": {"type": "string", "description": "Newline-separated task list. Format: '1. [low|medium|high] Description — files: a.py, b.py — deps: 1, 2'"},
                },
                "required": ["goal", "tasks"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_step_done",
            "description": "Mark a plan task as completed. Use after finishing a subtask to update progress.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID to mark done (e.g., task-1)"},
                    "notes": {"type": "string", "description": "Optional completion notes", "default": ""},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": "Update a plan task's status (pending, in_progress, done, skipped, blocked) or add notes. Use to start, skip, or block a task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID to update"},
                    "status": {"type": "string", "description": "New status: pending, in_progress, done, skipped, blocked"},
                    "notes": {"type": "string", "description": "Optional notes", "default": ""},
                },
                "required": ["task_id", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_branch",
            "description": "List branches, create a new branch and switch to it, or switch to an existing branch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "list, create, or switch"},
                    "name": {"type": "string", "description": "Branch name (required for create/switch)"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Stage files and commit with a message. Follow conventional commits format (feat:, fix:, refactor:, etc). Always check git_status first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Commit message"},
                    "files": {"type": "string", "description": "Comma-separated file paths to stage (default: all)"},
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_push",
            "description": "Push current branch to remote. Always commit before pushing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "set_upstream": {"type": "boolean", "description": "Set upstream tracking (-u flag)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gh_pr_create",
            "description": "Create a GitHub pull request using gh CLI. Requires gh to be installed and authenticated. Use after committing and pushing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "PR title (short, descriptive)"},
                    "body": {"type": "string", "description": "PR description (changes, reason, test plan)"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lsp_diagnostics",
            "description": "Run language server diagnostics on a file to find errors and warnings. Supports .py (py_compile), .ts/.tsx (tsc), .js/.jsx (eslint), .rs (cargo check), .go (go vet). Use after writing code to catch errors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to check"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lsp_definition",
            "description": "Go to definition of a symbol at the given line and character (1-based). Returns file:line:char with context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to query"},
                    "line": {"type": "integer", "description": "Line number (1-based)", "default": 1},
                    "character": {"type": "integer", "description": "Character column (1-based)", "default": 1},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lsp_references",
            "description": "Find all references to a symbol at the given line and character (1-based).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to query"},
                    "line": {"type": "integer", "description": "Line number (1-based)", "default": 1},
                    "character": {"type": "integer", "description": "Character column (1-based)", "default": 1},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lsp_hover",
            "description": "Get hover info (type signature, docstring) for the symbol at the given line and character (1-based).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to query"},
                    "line": {"type": "integer", "description": "Line number (1-based)", "default": 1},
                    "character": {"type": "integer", "description": "Character column (1-based)", "default": 1},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lsp_symbols",
            "description": "List all symbols (functions, classes, methods, etc.) in a file as a hierarchical outline.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to analyze"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for information, docs, error messages, or latest news. Returns top results with titles, URLs, and snippets. Use for finding up-to-date information beyond your knowledge cutoff.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "num_results": {"type": "number", "description": "Number of results (default: 5, max: 10)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_codebase",
            "description": "Semantic search over the codebase using embeddings. Finds code related to a natural language query. Use for: 'where is error handling for X?', 'find the authentication logic', 'show me tests for Y'. Returns file paths, line ranges, and relevance scores.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language query about the codebase"},
                    "top_k": {"type": "number", "description": "Number of results (default: 5, max: 10)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run tests for the given files, or all tests if no files specified. If files are provided, only tests affected by those files are run using import-graph analysis. Returns a formatted summary with pass/fail counts and failure details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "files": {"type": "array", "items": {"type": "string"}, "description": "List of changed source files to find affected tests for. If empty/omitted, runs the full test suite."},
                    "workspace": {"type": "string", "description": "Workspace directory containing the tests", "default": "."},
                    "timeout": {"type": "integer", "description": "Maximum seconds to wait for tests", "default": 120},
                },
                "required": [],
            },
        },
    },
]

def _tool_spawn_subagent_stub(**kwargs) -> str:
    """Stub: spawn_subagent is handled by the agent core, not the tool executor.

    When the agent core processes tool calls, it intercepts spawn_subagent
    and routes it to WispAgentCore._spawn_subagent() which creates a
    SubagentContract and delegates to SubagentOrchestrator.

    If you see this message, spawn_subagent was called outside the agent loop
    (e.g. via execute_tool() directly). Use agent.spawn_subagents() instead.
    """
    return json.dumps({
        "status": "error",
        "tool": "spawn_subagent",
        "data": (
            "spawn_subagent must be called through the agent loop. "
            "Use agent.spawn_subagents() or include it in a tool_calls "
            "block from the model response."
        ),
        "metadata": {},
    })


# Map tool names to their implementations

TOOL_IMPLS = {
    "spawn_subagent": _tool_spawn_subagent_stub,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "edit_file_multi": tool_edit_file_multi,
    "run_bash": tool_run_bash,
    "list_files": tool_list_files,
    "web_fetch": tool_web_fetch,
    "search_symbols": tool_search_symbols,
    "remember": tool_remember,
    "recall": tool_recall,
    "git_status": tool_git_status,
    "git_diff": tool_git_diff,
    "diagnose": tool_diagnose,
    "plan_task": tool_plan_task,
    "mark_step_done": tool_mark_step_done,
    "update_plan": tool_update_plan,
    "git_branch": tool_git_branch,
    "git_commit": tool_git_commit,
    "git_push": tool_git_push,
    "gh_pr_create": tool_gh_pr_create,
    "lsp_diagnostics": tool_lsp_diagnostics,
    "lsp_definition": tool_lsp_definition,
    "lsp_references": tool_lsp_references,
    "lsp_hover": tool_lsp_hover,
    "lsp_symbols": tool_lsp_symbols,
    "web_search": tool_web_search,
    "search_codebase": tool_search_codebase,
    "run_tests": tool_run_tests,
}


def _build_tool_metadata(name: str, args: dict, result: str) -> dict:
    """Build metadata dict for structured tool results."""
    meta: dict[str, Any] = {"tool": name, "args": dict(args), "result_length": len(result)}

    # Flatten common args into metadata for easy consumption
    for key in ("path", "command", "url", "query", "fact", "pattern", "timeout", "max_chars", "max_results", "limit", "offset"):
        if key in args:
            meta[key] = args[key]

    if name == "read_file":
        # Parse the metadata header that read_file always includes:
        # --- FILE: path | LINES: total | SHOWING: lo-hi ---
        for line in result.splitlines()[:3]:
            if line.startswith("--- FILE:"):
                try:
                    # Format: "--- FILE: path | LINES: 120 | SHOWING: 1-120 ---"
                    parts = line.split(" | ")
                    for part in parts[1:]:
                        k, v = part.split(":", 1)
                        if k.strip() == "LINES":
                            meta["total_lines"] = int(v.strip())
                        elif k.strip() == "SHOWING":
                            meta["lines_shown"] = v.strip().rstrip(" -")
                except (ValueError, IndexError):
                    pass
                break

    elif name == "write_file" and "path" in args:
        meta["bytes_written"] = len(args.get("content", ""))

    elif name == "edit_file":
        old_text = args.get("old_text", "")
        new_text = args.get("new_text", "")
        meta["old_text_preview"] = old_text[:100]
        meta["new_text_preview"] = new_text[:100]

    elif name == "run_bash":
        if "[exit code:" in result:
            try:
                exit_str = result.split("[exit code:")[1].split("]")[0].strip()
                meta["exit_code"] = int(exit_str)
            except (ValueError, IndexError):
                pass
        if "\n... [output truncated]" in result or result.endswith("... [output truncated]"):
            meta["truncated"] = True

    elif name == "list_files":
        meta["entry_count"] = result.count("📄") + result.count("📁")

    elif name == "web_fetch":
        if "... [truncated" in result or result.endswith("... [truncated]"):
            meta["truncated"] = True

    elif name == "search_symbols":
        # "Found N symbols" or similar
        import re as _re
        m = _re.search(r"[Ff]ound\s+(\d+)\s+(?:symbol|result)", result)
        if m:
            meta["results_count"] = int(m.group(1))

    return meta


def execute_tool(name: str, args: dict, workspace: str, max_data_chars: int = 0, file_lock=None, lsp_manager=None, security_policy=None) -> str:
    """Execute a tool by name with given arguments.

    Returns a structured JSON string with status, data, and metadata
    so the LLM can parse results programmatically.

    If security_policy is provided, it is checked before execution
    (defense-in-depth — callers should also check before calling).
    """
    impl = TOOL_IMPLS.get(name)
    # ── Defense-in-depth: security check before execution ──
    if security_policy is not None and impl is not None:
        from wisp.infra.security import Action, Context
        decision = security_policy.check(Action(name=name, args=args), Context(workspace=Path(workspace)))
        if not decision.allowed:
            structured = {
                "status": "error",
                "tool": name,
                "data": f"Security blocked: {decision.reason}",
                "metadata": _build_tool_metadata(name, args, ""),
            }
            return json.dumps(structured, ensure_ascii=False)
    # ── Plugin tools are fallback (built-ins take absolute precedence) ──
    # Built-ins checked first; plugins only run if no built-in tool exists.
    # Security: plugin tools run in-process with no sandbox.
    from wisp.adapters import has_plugin_tool, execute_plugin_tool
    if not impl and has_plugin_tool(name):
        try:
            result = execute_plugin_tool(name, **args, workspace=workspace)
            logger.debug("Plugin tool %s returned %d chars", name, len(str(result)))
            metadata = _build_tool_metadata(name, args, str(result))
            data = str(result)
            if max_data_chars > 0 and len(data) > max_data_chars:
                data = data[:max_data_chars] + f"\n... [truncated {len(str(result))} total chars]"
                metadata["truncated"] = True
            structured = {
                "status": "ok",
                "tool": name,
                "data": data,
                "metadata": metadata,
            }
            return json.dumps(structured, ensure_ascii=False)
        except Exception as e:
            logger.error("Plugin tool %s failed: %s", name, e, exc_info=True)
            structured = {
                "status": "error",
                "tool": name,
                "data": f"Plugin tool error: {e}",
                "metadata": _build_tool_metadata(name, args, ""),
            }
            return json.dumps(structured, ensure_ascii=False)

    if not impl:
        raise ToolError(f"Unknown tool: {name}")

    # Filter args to only what the function accepts
    import inspect
    sig = inspect.signature(impl)
    filtered = {k: v for k, v in args.items() if k in sig.parameters}
    if "workspace" in sig.parameters:
        filtered["workspace"] = workspace
    if "file_lock" in sig.parameters:
        filtered["file_lock"] = file_lock
    if "lsp_manager" in sig.parameters:
        filtered["lsp_manager"] = lsp_manager

    try:
        result = impl(**filtered)
        if inspect.iscoroutine(result):
            from wisp.async_utils import run_sync_coro
            result = run_sync_coro(result)

        # Tools can return a dict with 'data' and 'metadata' keys for structured output
        if isinstance(result, dict) and "data" in result:
            metadata = result.get("metadata", _build_tool_metadata(name, args, ""))
            data = result["data"]
            if max_data_chars > 0 and len(str(data)) > max_data_chars:
                data = str(data)[:max_data_chars] + f"\n... [truncated]"
                metadata["truncated"] = True
            structured = {
                "status": result.get("status", "ok"),
                "tool": name,
                "data": data,
                "metadata": metadata,
            }
            return json.dumps(structured, ensure_ascii=False)

        logger.debug("Tool %s returned %d chars", name, len(str(result)))

        # Build structured result with optional truncation
        metadata = _build_tool_metadata(name, args, str(result))
        data = str(result)
        if max_data_chars > 0 and len(data) > max_data_chars:
            data = data[:max_data_chars] + f"\n... [truncated {len(str(result))} total chars]"
            metadata["truncated"] = True

        structured = {
            "status": "ok",
            "tool": name,
            "data": data,
            "metadata": metadata,
        }
        return json.dumps(structured, ensure_ascii=False)

    except ToolError as e:
        logger.warning("Tool %s failed: %s", name, e)
        structured = {
            "status": "error",
            "tool": name,
            "data": str(e),
            "metadata": _build_tool_metadata(name, args, ""),
        }
        return json.dumps(structured, ensure_ascii=False)

    except KeyboardInterrupt:
        raise  # Let user interruption propagate; do NOT bury it in JSON

    except Exception as e:
        """Catch actual tool bugs (ValueError, TypeError, etc.) and JSON-serialize them."""
        logger.error("Unexpected error in tool %s: %s", name, e, exc_info=True)
        structured = {
            "status": "error",
            "tool": name,
            "data": f"Unexpected error: {e}",
            "metadata": _build_tool_metadata(name, args, ""),
        }
        return json.dumps(structured, ensure_ascii=False)

    except BaseException:
        raise  # Propagate SystemExit, GeneratorExit (and KeyboardInterrupt) uncaught