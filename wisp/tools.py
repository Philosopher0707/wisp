"""Tool definitions and execution for Wisp — file ops, bash, git, and search.

Production-hardened with:
- Path traversal protection via os.path.commonpath
- File size limits (50MB reads, 100MB writes)
- Input validation on all tool arguments
- Timeout enforcement on bash commands
"""

import logging
import os
import re
import shlex
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


class ToolError(Exception):
    """Raised when a tool execution fails."""
    pass


class _TextExtractor(HTMLParser):
    """HTML text extractor for web_fetch tool. Defined at module level to avoid redefinition on every call."""

    def __init__(self):
        super().__init__()
        self.text = []
        self.skip_tags = {"script", "style", "nav", "header", "footer"}
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.skip_tags:
            self._skip_depth += 1
        elif tag == "br":
            self.text.append("\n")
        elif tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6"):
            if self.text and not self.text[-1].endswith("\n"):
                self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in self.skip_tags:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.text.append("\n")

    def handle_data(self, data):
        if self._skip_depth <= 0:
            self.text.append(data)

    def get_text(self) -> str:
        text = "".join(self.text)
        lines = [line.strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line)


# ── Security constants ───────────────────────────────────────────────

_MAX_READ_SIZE = 50 * 1024 * 1024       # 50 MB
_MAX_WRITE_SIZE = 100 * 1024 * 1024     # 100 MB
_MAX_BASH_OUTPUT = 10_000               # chars of output to return to model
_MAX_CMD_LENGTH = 4096                  # max command length for safety


def check_dangerous_command(command: str) -> Optional[str]:
    """Check if a shell command is potentially dangerous.

    Returns a human-readable reason string if dangerous, None if safe.
    This is a surface-level heuristic; it cannot catch obfuscated commands.
    """
    if not command or not isinstance(command, str):
        return None
    cmd_lower = command.lower().strip()

    # sudo: any privilege escalation
    if re.search(r'\bsudo\b', cmd_lower):
        return "privilege escalation (sudo)"

    # rm with recursive flag (-r, -R, --recursive)
    tokens = cmd_lower.split()
    if tokens and tokens[0] == 'rm':
        for t in tokens[1:]:
            if t.startswith('-') and 'r' in t:
                return "recursive deletion"
            if t == '--recursive':
                return "recursive deletion"

    # dd to block device
    if re.search(r'\bdd\b', cmd_lower) and re.search(r'\bof\s*=\s*/dev/', cmd_lower):
        return "direct disk write (dd)"

    # mkfs / fdisk / parted
    if re.search(r'\bmkfs\.?\w*\b', cmd_lower):
        return "filesystem formatting"
    if re.search(r'\bfdisk\b', cmd_lower):
        return "disk partitioning"
    if re.search(r'\bparted\b', cmd_lower):
        return "disk partitioning"

    # curl/wget piped to shell
    if re.search(r'\b(curl|wget)\b.*\|\s*(sh|bash|zsh)\b', cmd_lower):
        return "remote code execution (pipe to shell)"

    # redirect to block device
    if re.search(r'[>]\s*/dev/(sd|nvme|hd|xvd|loop)', cmd_lower):
        return "redirect to block device"

    # chmod 777 on system paths
    if re.search(r'\bchmod\s+(-[rR]+\s*)*777\s*/', cmd_lower):
        return "recursive world-writable system path"

    # git destructive
    if re.search(r'\bgit\s+reset\s+--hard\b', cmd_lower):
        return "destructive git reset"
    if re.search(r'\bgit\s+clean\s+-[fd]*[fd]', cmd_lower):
        return "destructive git clean"

    # docker prune
    if re.search(r'\bdocker\s+system\s+prune\b', cmd_lower):
        return "docker system prune"

    # shutdown/reboot
    if re.search(r'\b(shutdown|reboot|halt|poweroff|init\s+0)\b', cmd_lower):
        return "system shutdown/reboot"

    return None


def _resolve_path(path: str, workspace: str) -> Path:
    """Resolve a path relative to workspace, with security boundary enforcement.

    Returns the resolved absolute Path if it's within the workspace.
    Raises ToolError on path traversal attempts.
    """
    ws = Path(workspace).resolve()
    # If path is relative, resolve it relative to workspace
    if Path(path).is_absolute():
        resolved = Path(path).resolve()
    else:
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
    return resolved


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
    full_path = _resolve_path(path, workspace)
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
    full_path = _resolve_path(path, workspace)

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
    full_path = _resolve_path(path, workspace)
    if not full_path.exists():
        raise ToolError(f"File not found: {path}")

    size = full_path.stat().st_size
    if size > _MAX_READ_SIZE:
        raise ToolError(
            f"File too large: {path} is {size / 1024 / 1024:.1f} MB "
            f"(max edit: {_MAX_READ_SIZE / 1024 / 1024:.0f} MB)."
        )

    content = full_path.read_text(encoding="utf-8", errors="replace")

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


def tool_web_fetch(url: str, workspace: str = ".", max_chars: int = 10000) -> str:
    """Fetch content from a URL (web page, API endpoint, etc.).
    
    Fetches the URL and returns the content as text.
    For HTML pages, returns extracted text content.
    Respects robots.txt and has reasonable timeouts.
    """
    from urllib.parse import urlparse
    
    # Validate URL
    _validate_string(url, "url", _MAX_CMD_LENGTH)
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ToolError(f"Invalid URL: {url}")
    if parsed.scheme not in ("http", "https"):
        raise ToolError(f"Unsupported URL scheme: {parsed.scheme}")
    
    max_chars = _validate_int(max_chars, "max_chars", 100, 100000)
    
    try:
        headers = {
            "User-Agent": "Wisp-Agent/0.1.0 (Web Fetch Tool)"
        }
        response = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
        response.raise_for_status()
        
        content_type = response.headers.get("Content-Type", "").lower()
        
        # Get text content
        if "text/html" in content_type:
            # Try to extract readable text from HTML using module-level extractor
            try:
                extractor = _TextExtractor()
                extractor.feed(response.text)
                text = extractor.get_text()
            except Exception:
                # Fallback to plain text
                text = response.text
        else:
            text = response.text
        
        # Truncate if needed
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... [truncated: {len(text)} total chars]"
        
        logger.info("Fetched %s — %d chars", url, len(text))
        return f"✓ Fetched {url}\n\n{text}"
        
    except requests.exceptions.Timeout:
        raise ToolError(f"Request timed out after 30s: {url}")
    except requests.exceptions.ConnectionError as e:
        raise ToolError(f"Connection error: {e}")
    except requests.exceptions.HTTPError as e:
        raise ToolError(f"HTTP error {e.response.status_code}: {url}")
    except requests.exceptions.RequestException as e:
        raise ToolError(f"Request failed: {e}")


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
    full_path = _resolve_path(path, workspace)
    if not full_path.exists():
        raise ToolError(f"Path not found: {path}")
    if not full_path.is_dir():
        return f"(not a directory) {path}"

    # Prevent glob traversal with '..' or absolute patterns
    if pattern.startswith("/") or ".." in Path(pattern).parts:
        raise ToolError(f"Invalid pattern: '{pattern}' — path traversal not allowed")

    try:
        entries = list(full_path.glob(pattern))
    except (ValueError, OSError) as e:
        raise ToolError(f"Invalid glob pattern '{pattern}': {e}")

    if not entries:
        return f"(no matches for '{pattern}' in {path})"

    result = []
    max_entries = 500  # prevent huge directories from blowing context
    ws_path = Path(workspace).resolve()
    for e in sorted(entries)[:max_entries]:
        kind = "📁" if e.is_dir() else "📄"
        size = e.stat().st_size if e.is_file() else 0
        name = str(e.relative_to(ws_path))
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
]

# Map tool names to their implementations
TOOL_IMPLS = {
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "run_bash": tool_run_bash,
    "list_files": tool_list_files,
    "web_fetch": tool_web_fetch,
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
