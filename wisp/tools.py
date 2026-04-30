"""Tool definitions and execution for Wisp — file ops, bash, git, and search.

Production-hardened with:
- Path traversal protection via os.path.commonpath
- File size limits (50MB reads, 100MB writes)
- Input validation on all tool arguments
- Timeout enforcement on bash commands
"""

import logging
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ToolError(Exception):
    """Raised when a tool execution fails."""
    pass


# ── Security constants ───────────────────────────────────────────────

_MAX_READ_SIZE = 50 * 1024 * 1024       # 50 MB
_MAX_WRITE_SIZE = 100 * 1024 * 1024     # 100 MB
_MAX_BASH_OUTPUT = 10_000               # chars of output to return to model
_MAX_CMD_LENGTH = 4096                  # max command length for safety


def _check_workspace(path: str, workspace: str):
    """Ensure the given path is within the workspace (security boundary).

    Uses os.path.commonpath to prevent path traversal attacks like
    '../etc/passwd' or '/Users/foo/../../bar'.
    """
    resolved = Path(path).resolve()
    ws = Path(workspace).resolve()
    # If the path is relative, it resolves to CWD — but the user
    # intended it relative to workspace.  Join with workspace first.
    if not Path(path).is_absolute():
        resolved = (ws / path).resolve()
    try:
        common = os.path.commonpath([str(resolved), str(ws)])
    except ValueError:
        raise ToolError(f"Access denied: cannot resolve path {path}")
    if common != str(ws):
        raise ToolError(
            f"Access denied: {path} resolves to {resolved}, "
            f"which is outside workspace {ws}"
        )


def _validate_string(value: Any, name: str, max_len: int = 4096, allow_empty: bool = False) -> str:
    """Validate that a value is a string within length limits."""
    if not isinstance(value, str):
        raise ToolError(f"{name} must be a string, got {type(value).__name__}")
    if not allow_empty and not value.strip():
        raise ToolError(f"{name} cannot be empty")
    if len(value) > max_len:
        raise ToolError(f"{name} too long ({len(value)} > {max_len} chars)")
    return value


def _validate_int(value: Any, name: str, min_val: int = 0, max_val: int = 10**6) -> int:
    """Validate that a value is an integer within range."""
    if not isinstance(value, (int, float)):
        raise ToolError(f"{name} must be a number, got {type(value).__name__}")
    val = int(value)
    if val < min_val:
        raise ToolError(f"{name} too small ({val} < {min_val})")
    if val > max_val:
        raise ToolError(f"{name} too large ({val} > {max_val})")
    return val


# ── Tools ────────────────────────────────────────────────────────────

def tool_read_file(path: str, workspace: str, offset: int = 0, limit: int = 2000) -> str:
    """Read the contents of a file within the workspace."""
    _validate_string(path, "path")
    offset = _validate_int(offset, "offset", 0)
    limit = _validate_int(limit, "limit", 1, 100_000)
    _check_workspace(path, workspace)

    full_path = Path(workspace) / path if not Path(path).is_absolute() else Path(path)
    if not full_path.exists():
        raise ToolError(f"File not found: {path}")
    if not full_path.is_file():
        raise ToolError(f"Not a file: {path}")

    # Check file size before reading
    size = full_path.stat().st_size
    if size > _MAX_READ_SIZE:
        raise ToolError(
            f"File too large: {path} is {size / 1024 / 1024:.1f} MB "
            f"(max read: {_MAX_READ_SIZE / 1024 / 1024:.0f} MB). "
            f"Use offset/limit to read portions."
        )

    content = full_path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines(keepends=True)
    total = len(lines)

    selected = lines[offset:offset + limit]
    result = "".join(selected)
    if offset > 0 or limit < total:
        result += (
            f"\n--- [showing lines {offset+1}-{min(offset+limit, total)} of {total}] ---"
        )
    logger.debug("Read %s (%d/%d lines)", path, len(selected), total)
    return result


def tool_write_file(path: str, workspace: str, content: str) -> str:
    """Write content to a file (creates or overwrites)."""
    _validate_string(path, "path")
    _validate_string(content, "content", _MAX_WRITE_SIZE, allow_empty=True)
    _check_workspace(path, workspace)

    full_path = Path(workspace) / path if not Path(path).is_absolute() else Path(path)

    # Size check
    if len(content) > _MAX_WRITE_SIZE:
        raise ToolError(
            f"Content too large: {len(content)} bytes "
            f"(max write: {_MAX_WRITE_SIZE / 1024 / 1024:.0f} MB)"
        )

    # Warn if overwriting an existing file
    if full_path.exists():
        logger.warning("Overwriting existing file: %s (%d bytes)", path, full_path.stat().st_size)

    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    logger.info("Wrote %d bytes to %s", len(content), path)
    return f"✓ Wrote {len(content)} bytes to {path}"


def tool_edit_file(path: str, workspace: str, old_text: str, new_text: str) -> str:
    """Replace exact text in a file (surgical edit)."""
    _validate_string(path, "path")
    _validate_string(old_text, "old_text")
    _validate_string(new_text, "new_text", _MAX_WRITE_SIZE, allow_empty=True)
    _check_workspace(path, workspace)

    full_path = Path(workspace) / path if not Path(path).is_absolute() else Path(path)
    if not full_path.exists():
        raise ToolError(f"File not found: {path}")

    size = full_path.stat().st_size
    if size > _MAX_READ_SIZE:
        raise ToolError(
            f"File too large: {path} is {size / 1024 / 1024:.1f} MB "
            f"(max edit: {_MAX_READ_SIZE / 1024 / 1024:.0f} MB)."
        )

    content = full_path.read_text(encoding="utf-8")

    if old_text not in content:
        raise ToolError(
            f"old_text not found in {path}. "
            "Make sure the exact text exists (including whitespace)."
        )
    count = content.count(old_text)
    if count > 1:
        raise ToolError(
            f"old_text appears {count} times in {path}. "
            "Edit must target a unique match."
        )

    new_content = content.replace(old_text, new_text, 1)
    full_path.write_text(new_content, encoding="utf-8")
    logger.info("Edited %s — %d chars replaced with %d chars", path, len(old_text), len(new_text))
    return f"✓ Edited {path} — {len(old_text)} chars replaced with {len(new_text)} chars"


def tool_run_bash(command: str, workspace: str, timeout: int = 60) -> str:
    """Run a bash command in the workspace directory.

    Security: validates command length, enforces timeout, captures output.
    Uses shell=True for pipeline support but validates the command.
    """
    _validate_string(command, "command", _MAX_CMD_LENGTH)
    timeout_val = _validate_int(timeout, "timeout", 1, 3600)

    cwd = Path(workspace).resolve()
    logger.info("Running bash (timeout=%ds): %.100s", timeout_val, command)

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_val,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            if result.stdout:
                output += "\n--- stderr ---\n"
            output += result.stderr
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        # Truncate for the model (but log the full output)
        if len(output) > _MAX_BASH_OUTPUT:
            logger.debug("Bash output truncated (%d chars)", len(output))
            output = output[:_MAX_BASH_OUTPUT] + "\n... [output truncated]"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        logger.warning("Command timed out after %ds: %.100s", timeout_val, command)
        raise ToolError(f"Command timed out after {timeout_val}s: {command[:100]}...")
    except OSError as e:
        logger.error("Command failed with OSError: %s", e)
        raise ToolError(f"Command failed: {e}")
    except Exception as e:
        logger.error("Unexpected error in run_bash: %s", e)
        raise ToolError(f"Command failed: {e}")


def tool_list_files(path: str, workspace: str, pattern: str = "*") -> str:
    """List files and directories, optionally matching a pattern."""
    _validate_string(path, "path")
    _validate_string(pattern, "pattern", 200)
    _check_workspace(path, workspace)

    full_path = Path(workspace) / path if not Path(path).is_absolute() else Path(path)
    if not full_path.exists():
        raise ToolError(f"Path not found: {path}")
    if not full_path.is_dir():
        return f"(not a directory) {path}"

    # Prevent glob traversal with '..' or absolute patterns
    if pattern.startswith("/") or ".." in pattern:
        raise ToolError(f"Invalid pattern: '{pattern}' — path traversal not allowed")

    try:
        entries = list(full_path.glob(pattern))
    except (ValueError, OSError) as e:
        raise ToolError(f"Invalid glob pattern '{pattern}': {e}")

    if not entries:
        return f"(no matches for '{pattern}' in {path})"

    result = []
    max_entries = 500  # prevent huge directories from blowing context
    for e in sorted(entries)[:max_entries]:
        kind = "📁" if e.is_dir() else "📄"
        size = e.stat().st_size if e.is_file() else 0
        name = str(e.relative_to(Path(workspace)))
        if e.is_dir():
            result.append(f"{kind} {name}/")
        else:
            result.append(f"{kind} {name} ({size:,} bytes)")

    if len(entries) > max_entries:
        result.append(f"... and {len(entries) - max_entries} more entries")

    logger.debug("Listed %s with pattern '%s' — %d entries", path, pattern, len(entries))
    return "\n".join(result)


# ── Tool schema definitions (Ollama tool-calling format) ──

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Use for reviewing code, configs, or logs. Max file size: 50 MB. Supports offset/limit for large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file (relative to workspace or absolute)"},
                    "offset": {"type": "number", "description": "Starting line number (0-indexed)", "default": 0},
                    "limit": {"type": "number", "description": "Max lines to read", "default": 2000},
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
            "description": "Replace exact text in a file. The old_text must match exactly and be unique. Use for targeted edits instead of rewriting entire files.",
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
            "name": "run_bash",
            "description": "Run a bash command. Use for building, testing, git operations, or running scripts. Max command length: 4096 chars. Timeout: configurable (default 60s). Output truncated to 10K chars.",
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
]

# Map tool names to their implementations
TOOL_IMPLS = {
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "run_bash": tool_run_bash,
    "list_files": tool_list_files,
}


def execute_tool(name: str, args: dict, workspace: str) -> str:
    """Execute a tool by name with given arguments."""
    impl = TOOL_IMPLS.get(name)
    if not impl:
        raise ToolError(f"Unknown tool: {name}")

    # Filter args to only what the function accepts
    import inspect
    sig = inspect.signature(impl)
    filtered = {k: v for k, v in args.items() if k in sig.parameters}
    filtered["workspace"] = workspace

    try:
        result = impl(**filtered)
        logger.debug("Tool %s returned %d chars", name, len(result))
        return result
    except ToolError:
        raise
    except Exception as e:
        logger.error("Unexpected error in tool %s: %s", name, e, exc_info=True)
        raise ToolError(f"{name} failed: {e}")
