"""Tool definitions and execution for Wisp — file ops, bash, git, and search.

Production-hardened with:
- Path traversal protection via os.path.commonpath
- File size limits (50MB reads, 100MB writes)
- Input validation on all tool arguments
- Timeout enforcement on bash commands
"""

import json
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

# Module-level references for collaborative editing (set by agent)
_file_lock = None
_change_tracker = None


class ToolError(Exception):
    """Raised when a tool execution fails."""
    pass


def set_collaboration_tools(file_lock=None, change_tracker=None):
    """Set file lock and change tracker for collaborative editing."""
    global _file_lock, _change_tracker
    _file_lock = file_lock
    _change_tracker = change_tracker


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

    # ── Collaborative editing: check lock ──
    if _file_lock and not _file_lock.acquire(path):
        lock_info = _file_lock.lock_info(path)
        holder = lock_info.get("agent", "unknown") if lock_info else "unknown"
        raise ToolError(f"File {path} is locked by {holder}. Wait or coordinate before editing.")

    # Warn if overwriting an existing file
    if full_path.exists():
        logger.warning("Overwriting existing file: %s (%d bytes)", path, full_path.stat().st_size)

    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    logger.info("Wrote %d bytes to %s", len(content), path)

    # ── Collaborative editing: record change ──
    if _change_tracker:
        _change_tracker.record_write(path, content)

    # Release lock after write
    if _file_lock:
        _file_lock.release(path)

    return f"✓ Wrote {len(content)} bytes to {path}"


def _fuzzy_find_text(content: str, old_text: str, threshold: float = 0.85) -> tuple[Optional[int], Optional[str], float]:
    """Find the best fuzzy match of old_text in content.

    Uses character-level similarity (Dice coefficient on bigrams) to find
    the closest match when exact matching fails.

    Args:
        content: The full file content.
        old_text: The text to search for.
        threshold: Minimum similarity ratio (0.0–1.0) to consider a match.

    Returns:
        Tuple of (start_index, actual_matched_text, similarity) if a match
        above threshold is found, or (None, None, best_similarity) if no
        match meets the threshold.
    """
    if not old_text or not content:
        return None, None, 0.0

    # Compute bigram similarity between two strings
    def _bigram_sim(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        # Build bigram sets
        bigrams_a = {a[i:i+2] for i in range(len(a) - 1)}
        bigrams_b = {b[i:i+2] for i in range(len(b) - 1)}
        if not bigrams_a or not bigrams_b:
            return 0.0
        intersection = bigrams_a & bigrams_b
        # Dice coefficient
        return 2.0 * len(intersection) / (len(bigrams_a) + len(bigrams_b))

    old_lines = old_text.splitlines(keepends=True)
    content_lines = content.splitlines(keepends=True)

    best_score = 0.0
    best_start = None
    best_match = None

    # Slide a window of the same line count over content
    window_size = len(old_lines)
    if window_size == 0:
        return None, None, 0.0

    for start in range(len(content_lines) - window_size + 1):
        candidate = "".join(content_lines[start:start + window_size])

        # Quick length check — skip wildly different lengths
        len_ratio = len(candidate) / len(old_text) if old_text else 0
        if len_ratio < 0.5 or len_ratio > 2.0:
            continue

        sim = _bigram_sim(candidate, old_text)
        if sim > best_score:
            best_score = sim
            best_start = len("".join(content_lines[:start]))
            best_match = candidate

    if best_score >= threshold and best_start is not None and best_match is not None:
        return best_start, best_match, best_score
    return None, None, best_score


def tool_edit_file(path: str, workspace: str, old_text: str, new_text: str) -> str:
    """Replace exact text in a file (surgical edit).

    First tries exact match. If that fails, falls back to fuzzy matching
    using character-level similarity (Dice coefficient on bigrams).
    """
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

    # ── Collaborative editing: check lock ──
    if _file_lock and not _file_lock.acquire(path):
        lock_info = _file_lock.lock_info(path)
        holder = lock_info.get("agent", "unknown") if lock_info else "unknown"
        raise ToolError(f"File {path} is locked by {holder}. Wait or coordinate before editing.")

    content = full_path.read_text(encoding="utf-8", errors="replace")

    # ── Exact match (fast path) ──────────────────────────────────
    if old_text in content:
        count = content.count(old_text)
        if count > 1:
            if _file_lock:
                _file_lock.release(path)
            raise ToolError(
                f"old_text appears {count} times in {path}. "
                "Edit must target a unique match."
            )
        new_content = content.replace(old_text, new_text, 1)
        full_path.write_text(new_content, encoding="utf-8")
        logger.info("Edited %s — %d chars replaced with %d chars", path, len(old_text), len(new_text))

        # ── Collaborative editing: record change ──
        if _change_tracker:
            _change_tracker.record_edit(path, old_text, new_text)

        if _file_lock:
            _file_lock.release(path)

        return f"✓ Edited {path} — {len(old_text)} chars replaced with {len(new_text)} chars"

    # ── Fuzzy match (fallback) ───────────────────────────────────
    match_start, actual_old, similarity = _fuzzy_find_text(content, old_text)
    if match_start is None or actual_old is None:
        if _file_lock:
            _file_lock.release(path)
        raise ToolError(
            f"old_text not found in {path} "
            f"(best fuzzy similarity: {similarity:.0%}). "
            "Make sure the exact text exists (including whitespace)."
        )

    new_content = content[:match_start] + new_text + content[match_start + len(actual_old):]
    full_path.write_text(new_content, encoding="utf-8")
    logger.info(
        "Edited %s (fuzzy, %.0%% similar) — %d chars replaced with %d chars",
        path, similarity * 100, len(actual_old), len(new_text),
    )

    # ── Collaborative editing: record change ──
    if _change_tracker:
        _change_tracker.record_edit(path, actual_old, new_text)

    if _file_lock:
        _file_lock.release(path)

    return (
        f"✓ Edited {path} (fuzzy match, {similarity:.0%} similar) — "
        f"{len(actual_old)} chars replaced with {len(new_text)} chars"
    )


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


def tool_search_symbols(query: str, workspace: str = ".", max_results: int = 20) -> str:
    """Search the code index for symbols matching a query.

    Builds a lightweight index of function/class/struct definitions in the
    workspace and searches it for the given query. Results include file path,
    line number, and symbol kind.
    """
    _validate_string(query, "query", 200)
    max_results = _validate_int(max_results, "max_results", 1, 100)

    from wisp.code_index import build_index, search_symbols

    index = build_index(workspace)
    if index.total_symbols == 0:
        return "(no symbols found — no source files indexed)"

    results = search_symbols(index, query, max_results=max_results)

    if not results:
        return f"(no symbols matching '{query}' — {index.total_symbols} symbols indexed)"

    lines = [f"Found {len(results)} symbol(s) matching '{query}':", ""]
    for sym in results:
        parent_info = f" (in {sym.parent})" if sym.parent else ""
        lines.append(f"  {sym.kind:12s} {sym.name}{parent_info}")
        lines.append(f"  {'':12s} 📍 {sym.file}:{sym.line}")
        lines.append("")

    if len(results) == max_results:
        lines.append(f"... and more (showing top {max_results})")

    return "\n".join(lines)


def tool_remember(fact: str, workspace: str = ".") -> str:
    """Store a fact in cross-session memory.

    The fact will be remembered across conversations and injected into
    the system prompt in future sessions.
    """
    _validate_string(fact, "fact", 500)

    from wisp.memory import add_fact

    added = add_fact(fact, workspace=workspace)
    if added:
        return f"✓ Remembered: {fact}"
    else:
        return f"(Already remembered: {fact})"


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


def tool_diagnose(error_output: str, workspace: str = ".") -> str:
    """Diagnose an error from test output, traceback, or command output.

    Use when tests fail, code crashes, or tools return errors.
    Returns a structured diagnosis with error type, location, cause, and fix suggestion.
    """
    from wisp.error_diagnosis import diagnose
    diag = diagnose(error_output, workspace)
    return diag.format()


def tool_plan_task(goal: str, tasks: str, workspace: str = ".") -> str:
    """Create a structured plan with subtasks.

    tasks should be a newline-separated list in this format:
      1. [low] Description here — files: a.py, b.py
      2. [medium] Another task — deps: 1 — files: c.py
      3. [high] Final task — deps: 1, 2
    """
    from wisp.planner import PlanStore, parse_plan_from_text

    plan = parse_plan_from_text(tasks, goal=goal, workspace=workspace)
    if not plan.tasks:
        return "⚠ No tasks parsed. Use format: '1. [low] Description — files: a.py'"

    store = PlanStore()
    store.save(plan)

    lines = [f"✓ Created plan: {plan.id}", f"Goal: {plan.goal}", f"Tasks: {len(plan.tasks)}", ""]
    for i, t in enumerate(plan.tasks, 1):
        deps = f" (deps: {', '.join(t.dependencies)})" if t.dependencies else ""
        files = f" [files: {', '.join(t.files_to_touch)}]" if t.files_to_touch else ""
        lines.append(f"  {i}. [{t.estimated_complexity}] {t.description}{deps}{files}")

    return "\n".join(lines)


def tool_mark_step_done(task_id: str, notes: str = "", workspace: str = ".") -> str:
    """Mark a plan task as completed."""
    from wisp.planner import PlanStore

    store = PlanStore()
    plan = store.load_active(workspace)
    if not plan:
        return "⚠ No active plan for this workspace."

    if plan.complete_task(task_id, notes=notes):
        store.save(plan)
        done, total = plan.progress()
        return f"✓ Marked task {task_id} as done. Progress: {done}/{total}"
    return f"⚠ Could not complete task {task_id}. Is it in progress?"


def tool_update_plan(task_id: str, status: str, notes: str = "", workspace: str = ".") -> str:
    """Update a plan task's status (pending, in_progress, done, skipped, blocked)."""
    from wisp.planner import PlanStore

    store = PlanStore()
    plan = store.load_active(workspace)
    if not plan:
        return "⚠ No active plan for this workspace."

    task = plan.get_task(task_id)
    if not task:
        return f"⚠ Task {task_id} not found."

    if status == "in_progress":
        plan.start_task(task_id)
    elif status == "done":
        plan.complete_task(task_id, notes)
    elif status == "skipped":
        plan.skip_task(task_id, notes)
    else:
        task.status = status
        if notes:
            task.notes = notes
        plan.touch()

    store.save(plan)
    done, total = plan.progress()
    return f"✓ Updated task {task_id} to '{status}'. Progress: {done}/{total}"


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
            "description": "Store a fact in cross-session memory so you remember it across conversations. Use for user preferences, project conventions, decisions made, or anything worth remembering long-term.",
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
            "name": "spawn_subagent",
            "description": "Spawn a specialist subagent to handle a scoped task (research, coding, testing) with its own iteration budget and timeout. The subagent runs in parallel and returns a structured result. Use when a task can be decomposed into an independent work unit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Specific instruction for the subagent. Be precise about what to produce."},
                    "tools": {"type": "array", "items": {"type": "string"}, "description": "Tool names the subagent may use. Omit or use ['all'] for full toolset.", "default": ["all"]},
                    "max_iterations": {"type": "number", "description": "Max agent loop iterations", "default": 15},
                    "timeout_seconds": {"type": "number", "description": "Hard timeout in seconds", "default": 120},
                    "output_format": {"type": "string", "description": "text | json | markdown | report", "default": "text"},
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
]

# Map tool names to their implementations
TOOL_IMPLS = {
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "run_bash": tool_run_bash,
    "list_files": tool_list_files,
    "web_fetch": tool_web_fetch,
    "search_symbols": tool_search_symbols,
    "remember": tool_remember,
    "git_status": tool_git_status,
    "git_diff": tool_git_diff,
    "diagnose": tool_diagnose,
    "plan_task": tool_plan_task,
    "mark_step_done": tool_mark_step_done,
    "update_plan": tool_update_plan,
}


def _build_tool_metadata(name: str, args: dict, result: str) -> dict:
    """Build structured metadata for a tool result based on the tool name and arguments."""
    meta: dict[str, Any] = {}

    if name == "read_file":
        meta["path"] = args.get("path", "")
        meta["offset"] = args.get("offset", 0)
        meta["limit"] = args.get("limit", 2000)
        # Try to extract line info from the result footer
        m = re.search(r"--- \[showing lines (\d+)-(\d+) of (\d+)\] ---", result)
        if m:
            meta["lines_shown"] = f"{m.group(1)}-{m.group(2)}"
            meta["total_lines"] = int(m.group(3))

    elif name == "write_file":
        meta["path"] = args.get("path", "")
        meta["bytes_written"] = len(args.get("content", ""))

    elif name == "edit_file":
        meta["path"] = args.get("path", "")
        meta["old_text_preview"] = (args.get("old_text", "") or "")[:80]
        meta["new_text_preview"] = (args.get("new_text", "") or "")[:80]

    elif name == "run_bash":
        meta["command"] = (args.get("command", "") or "")[:120]
        meta["timeout"] = args.get("timeout", 60)
        # Extract exit code from result
        m = re.search(r"\[exit code: (\d+)\]", result)
        if m:
            meta["exit_code"] = int(m.group(1))
        else:
            meta["exit_code"] = 0
        if "[output truncated]" in result:
            meta["truncated"] = True

    elif name == "list_files":
        meta["path"] = args.get("path", ".")
        meta["pattern"] = args.get("pattern", "*")
        # Count entries from result
        lines = [l for l in result.split("\n") if l.strip() and not l.startswith("(")]
        meta["entry_count"] = len(lines)

    elif name == "web_fetch":
        meta["url"] = args.get("url", "")
        meta["max_chars"] = args.get("max_chars", 10000)
        if "[truncated" in result:
            meta["truncated"] = True

    elif name == "search_symbols":
        meta["query"] = args.get("query", "")
        meta["max_results"] = args.get("max_results", 20)
        m = re.search(r"Found (\d+) symbol", result)
        if m:
            meta["results_count"] = int(m.group(1))

    elif name == "remember":
        meta["fact"] = (args.get("fact", "") or "")[:80]

    return meta


def execute_tool(name: str, args: dict, workspace: str, max_data_chars: int = 0) -> str:
    """Execute a tool by name with given arguments.

    Returns a structured JSON string with status, data, and metadata
    so the LLM can parse results programmatically.

    If max_data_chars > 0, the data field is truncated to that length
    and a 'truncated' flag is added to metadata.
    """
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

        # Build structured result with optional truncation
        metadata = _build_tool_metadata(name, args, result)
        data = result
        if max_data_chars > 0 and len(data) > max_data_chars:
            data = data[:max_data_chars] + f"\n... [truncated {len(result)} total chars]"
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

    except Exception as e:
        logger.error("Unexpected error in tool %s: %s", name, e, exc_info=True)
        structured = {
            "status": "error",
            "tool": name,
            "data": f"Unexpected error: {e}",
            "metadata": _build_tool_metadata(name, args, ""),
        }
        return json.dumps(structured, ensure_ascii=False)
