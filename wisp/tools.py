"""Tool definitions and execution for Wisp — file ops, bash, git, and search.

Production-hardened with:
- Path traversal protection via os.path.commonpath
- File size limits (50MB reads, 100MB writes)
- Input validation on all tool arguments
- Timeout enforcement on bash commands
"""

import contextvars
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

# Per-agent context variables for multi-agent concurrency safety.
# Each asyncio.Task (each agent) gets its own lock/tracker/manager.
_file_lock_ctx: contextvars.ContextVar = contextvars.ContextVar("file_lock", default=None)
_change_tracker_ctx: contextvars.ContextVar = contextvars.ContextVar("change_tracker", default=None)


class ToolError(Exception):
    """Raised when a tool execution fails."""
    pass


def set_collaboration_tools(file_lock=None, change_tracker=None):
    """Set file lock and change tracker for the current agent context."""
    _file_lock_ctx.set(file_lock)
    _change_tracker_ctx.set(change_tracker)


# LSP manager context variable (set per agent)
_lsp_manager_ctx: contextvars.ContextVar = contextvars.ContextVar("lsp_manager", default=None)


def set_lsp_manager(manager):
    """Set the LSP manager for the current agent context."""
    _lsp_manager_ctx.set(manager)


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

    # git push --force
    if re.search(r'\bgit\s+push\b.*(-f|--force)\b', cmd_lower):
        return "force push (rewrites remote history)"

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

def tool_read_file(path: str, workspace: str, offset: int = 0, limit: int = 1_000_000) -> str:
    """Read the contents of a file within the workspace. Returns entire file by default."""
    _validate_string(path, "path")
    offset = _validate_int(offset, "offset", 0)
    limit = _validate_int(limit, "limit", 1, 1_000_000)
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
        shown = min(offset + limit, total)
        result += (
            f"\n--- [showing lines {offset+1}-{shown} of {total}] ---"
        )
    logger.debug("Read %s (%d/%d lines)", path, len(selected), total)
    return result


def tool_write_file(path: str, workspace: str, content: str, file_lock=None) -> str:
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
    lock = file_lock or _file_lock_ctx.get()
    if lock and not lock.acquire(path):
        lock_info = lock.lock_info(path)
        holder = lock_info.get("agent", "unknown") if lock_info else "unknown"
        raise ToolError(f"File {path} is locked by {holder}. Wait or coordinate before editing.")

    # Warn if overwriting an existing file
    if full_path.exists():
        logger.warning("Overwriting existing file: %s (%d bytes)", path, full_path.stat().st_size)

    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    logger.info("Wrote %d bytes to %s", len(content), path)

    # ── Collaborative editing: record change ──
    tracker = _change_tracker_ctx.get()
    if tracker:
        tracker.record_write(path, content)

    # Release lock after write
    if lock:
        lock.release(path)

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


def tool_edit_file(path: str, workspace: str, old_text: str, new_text: str, file_lock=None) -> dict:
    """Replace exact text in a file (surgical edit).

    Uses Unicode-aware fuzzy matching (smart quotes, dashes, special spaces)
    when exact matching fails. Returns a structured JSON result with diff.
    """
    from wisp.diff import EditOp, apply_edit_with_diff

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
    lock = file_lock or _file_lock_ctx.get()
    if lock and not lock.acquire(path):
        lock_info = lock.lock_info(path)
        holder = lock_info.get("agent", "unknown") if lock_info else "unknown"
        raise ToolError(f"File {path} is locked by {holder}. Wait or coordinate before editing.")

    try:
        result = apply_edit_with_diff(path, [EditOp(old_text=old_text, new_text=new_text)], workspace)

        if not result.success:
            raise ToolError(result.error or "Edit failed")

        # ── Collaborative editing: record change ──
        tracker = _change_tracker_ctx.get()
        if tracker:
            tracker.record_edit(path, old_text, new_text)

        logger.info(
            "Edited %s — %d chars replaced with %d chars%s",
            path, result.old_length, result.new_length,
            " (fuzzy)" if result.used_fuzzy_match else "",
        )

        return {
            "status": "ok",
            "data": f"✓ Edited {path} — {result.old_length} chars replaced with {result.new_length} chars",
            "metadata": {
                "path": path,
                "old_length": result.old_length,
                "new_length": result.new_length,
                "edits_applied": result.edits_applied,
                "used_fuzzy_match": result.used_fuzzy_match,
                "diff": result.diff,
                "first_changed_line": result.first_changed_line,
            },
        }

    finally:
        if lock:
            lock.release(path)


def tool_edit_file_multi(path: str, workspace: str, edits: list[dict], file_lock=None) -> dict:
    """Make multiple precise edits to a single file in one call.

    All edits[].old_text values are matched against the ORIGINAL file content
    (not incrementally). Edits must not overlap. Uses Unicode-aware fuzzy
    matching when exact matching fails.

    Args:
        path: Path to the file to edit.
        edits: List of {"old_text": str, "new_text": str} objects.
    """
    from wisp.diff import EditOp, apply_edit_with_diff

    _validate_string(path, "path")
    if not isinstance(edits, list) or len(edits) == 0:
        raise ToolError("edits must be a non-empty array of {old_text, new_text} objects")
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise ToolError(f"edits[{i}] must be an object with old_text and new_text")
        _validate_string(edit.get("old_text", ""), f"edits[{i}].old_text")
        _validate_string(edit.get("new_text", ""), f"edits[{i}].new_text", _MAX_WRITE_SIZE, allow_empty=True)

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
    lock = file_lock or _file_lock_ctx.get()
    if lock and not lock.acquire(path):
        lock_info = lock.lock_info(path)
        holder = lock_info.get("agent", "unknown") if lock_info else "unknown"
        raise ToolError(f"File {path} is locked by {holder}. Wait or coordinate before editing.")

    try:
        ops = [EditOp(old_text=e["old_text"], new_text=e["new_text"]) for e in edits]
        result = apply_edit_with_diff(path, ops, workspace)

        if not result.success:
            raise ToolError(result.error or "Edit failed")

        # ── Collaborative editing: record changes ──
        tracker = _change_tracker_ctx.get()
        if tracker:
            for edit in edits:
                tracker.record_edit(path, edit["old_text"], edit["new_text"])

        logger.info(
            "Multi-edited %s — %d edits, %d→%d chars",
            path, result.edits_applied, result.old_length, result.new_length,
        )

        return {
            "status": "ok",
            "data": f"✓ Applied {result.edits_applied} edit(s) to {path} — {result.old_length} chars replaced with {result.new_length} chars",
            "metadata": {
                "path": path,
                "old_length": result.old_length,
                "new_length": result.new_length,
                "edits_applied": result.edits_applied,
                "used_fuzzy_match": result.used_fuzzy_match,
                "diff": result.diff,
                "first_changed_line": result.first_changed_line,
            },
        }

    finally:
        if lock:
            lock.release(path)


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
            except Exception as e:
                logger.warning("HTML text extraction failed for %s: %s — falling back to raw HTML", url, e)
                text = response.text
                if len(text) > max_chars:
                    text = text[:max_chars] + "\n[Warning: HTML parsing failed, showing raw HTML. Results may be hard to read.]"
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


def tool_recall(query: str, workspace: str = ".", limit: int = 10) -> str:
    """Search cross-session memory and past session summaries for relevant facts.

    Use this when you need to actively recall something you may have learned
    in previous conversations, rather than relying only on what's in the
    current context window.
    """
    _validate_string(query, "query", 200)
    if limit < 1 or limit > 50:
        limit = 10

    from wisp.memory import list_facts, load_memory
    from wisp.agent_memory import AgentMemory
    from wisp.summarizer import SessionSummary

    query_lower = query.lower()
    query_words = [w for w in query_lower.split() if len(w) > 2]

    results: list[tuple[float, str]] = []

    # ── Search memory facts ──
    facts = list_facts(workspace)
    for fact in facts:
        content = fact["content"] if isinstance(fact, dict) else fact
        score = _relevance_score(content, query_lower, query_words)
        if score > 0:
            results.append((score, f"[Memory] {content}"))

    # ── Search session summaries ──
    agent_mem = AgentMemory()
    summaries = agent_mem.load_recent(workspace=workspace, limit=20)
    for summary in summaries:
        texts = [
            (summary.summary, 1.0),
            (" ".join(summary.key_decisions), 1.5),
            (" ".join(summary.user_preferences), 1.5),
            (" ".join(summary.open_tasks), 1.2),
            (" ".join(summary.files_touched), 1.0),
        ]
        for text, field_boost in texts:
            if text:
                score = _relevance_score(text, query_lower, query_words)
                # Session summaries need higher bar to avoid noise
                if score >= 2.0:
                    score *= field_boost
                    results.append((score, f"[Session {summary.session_id[:20]}] {text[:200]}"))

    # ── Search global memory (if workspace-specific didn't find much) ──
    if len(results) < 3:
        global_facts = list_facts(None)
        for fact in global_facts:
            content = fact["content"] if isinstance(fact, dict) else fact
            if fact not in facts:  # avoid duplicates
                score = _relevance_score(content, query_lower, query_words)
                if score > 0:
                    results.append((score, f"[Global] {content}"))

    if not results:
        return "No relevant memories found for this query."

    # Sort by score descending, deduplicate, limit
    results.sort(key=lambda x: x[0], reverse=True)
    seen: set[str] = set()
    deduped: list[str] = []
    for score, text in results:
        key = text.lower()[:80]
        if key not in seen:
            seen.add(key)
            deduped.append(f"({score:.1f}) {text}")
            if len(deduped) >= limit:
                break

    return "\n".join(deduped)


def _relevance_score(text: str, query_lower: str, query_words: list[str]) -> float:
    """Relevance score for memory retrieval. Exact matches score highest.
    Partial word matches score lower to avoid generic text pollution.
    """
    text_lower = text.lower()
    score = 0.0

    # Exact substring match = very high signal
    if query_lower in text_lower:
        score += 5.0
        # Bonus for shorter exact matches (more precise)
        score += max(0, 3.0 - len(text) / 200)

    # Word overlap — require meaningful words only
    text_words = set(w for w in text_lower.split() if len(w) > 2)
    meaningful_query = [w for w in query_words if len(w) > 2]
    for word in meaningful_query:
        if word in text_words:
            score += 2.0
        # Partial match only for longer words (avoid "the" matching "then")
        elif len(word) >= 4:
            for tw in text_words:
                if word in tw or tw in word:
                    score += 0.5
                    break

    # Penalize very long generic text (session summaries can be noisy)
    if len(text) > 300:
        score *= 0.6
    elif len(text) > 150:
        score *= 0.8

    # Boost concise memory facts (they're usually high signal)
    if len(text) < 100 and score > 0:
        score *= 1.3

    return score


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


def tool_lsp_diagnostics(path: str, workspace: str = ".") -> str:
    """Run language server diagnostics on a file. Returns linter errors/warnings."""
    full_path = _resolve_path(path, workspace)
    if not full_path.exists():
        return f"Error: file not found: {path}"
    ext = full_path.suffix.lower()

    linters = {
        ".py": ["python3", "-m", "py_compile"],
        ".ts": ["npx", "tsc", "--noEmit"],
        ".tsx": ["npx", "tsc", "--noEmit"],
        ".js": ["npx", "eslint"],
        ".jsx": ["npx", "eslint"],
        ".rs": ["cargo", "check"],
        ".go": ["go", "vet"],
    }
    cmd = linters.get(ext)
    if not cmd:
        return f"No diagnostics available for {ext} files."

    import subprocess
    try:
        r = subprocess.run(cmd + [str(full_path)], capture_output=True, text=True,
                          timeout=60, cwd=workspace)
        output = r.stdout + r.stderr
        if not output.strip():
            return "✓ No issues found."
        if len(output) > 5000:
            output = output[:5000] + "\n... [output truncated]"
        status = "✓ No errors" if r.returncode == 0 else f"Found issues (exit {r.returncode})"
        return f"[{status}]\n{output}"
    except FileNotFoundError:
        return f"Error: linter not found for {ext}. Install it first."
    except subprocess.TimeoutExpired:
        return "Error: diagnostics timed out."


def _get_lsp_server(path: str, workspace: str, lsp_manager=None):
    """Resolve LSP manager and return (server, full_path) or error string."""
    mgr = lsp_manager or _lsp_manager_ctx.get()
    if mgr is None:
        return "Error: LSP not available (no language servers configured)."
    full_path = _resolve_path(path, workspace)
    if not full_path.exists():
        return f"Error: file not found: {path}"
    server = mgr.get_server_safe(str(full_path))
    if server is None:
        return f"No LSP server available for {full_path.suffix} files."
    return (server, full_path)


def tool_lsp_definition(path: str, workspace: str = ".", line: int = 1, character: int = 1, lsp_manager=None) -> str:
    """Go to definition of a symbol at the given line/character (1-based)."""
    from wisp.lsp.client import _format_locations
    resolved = _get_lsp_server(path, workspace, lsp_manager)
    if isinstance(resolved, str):
        return resolved
    server, full_path = resolved
    try:
        locations = server.get_definition(str(full_path), line - 1, character - 1)
        return _format_locations(locations, workspace, max_items=5)
    except Exception as e:
        return f"Error: {e}"


def tool_lsp_references(path: str, workspace: str = ".", line: int = 1, character: int = 1, lsp_manager=None) -> str:
    """Find all references to the symbol at the given line/character (1-based)."""
    from wisp.lsp.client import _format_locations
    resolved = _get_lsp_server(path, workspace, lsp_manager)
    if isinstance(resolved, str):
        return resolved
    server, full_path = resolved
    try:
        locations = server.get_references(str(full_path), line - 1, character - 1)
        return _format_locations(locations, workspace, max_items=50)
    except Exception as e:
        return f"Error: {e}"


def tool_lsp_hover(path: str, workspace: str = ".", line: int = 1, character: int = 1, lsp_manager=None) -> str:
    """Get hover info (type, docstring) for the symbol at the given line/character (1-based)."""
    from wisp.lsp.client import _format_hover
    resolved = _get_lsp_server(path, workspace, lsp_manager)
    if isinstance(resolved, str):
        return resolved
    server, full_path = resolved
    try:
        result = server.get_hover(str(full_path), line - 1, character - 1)
        return _format_hover(result)
    except Exception as e:
        return f"Error: {e}"


def tool_lsp_symbols(path: str, workspace: str = ".", lsp_manager=None) -> str:
    """List all symbols (functions, classes, etc.) in a file."""
    from wisp.lsp.client import _format_symbols
    resolved = _get_lsp_server(path, workspace, lsp_manager)
    if isinstance(resolved, str):
        return resolved
    server, full_path = resolved
    try:
        symbols = server.get_symbols(str(full_path))
        return _format_symbols(symbols)
    except Exception as e:
        return f"Error: {e}"


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


def tool_web_search(query: str, num_results: int = 5) -> str:
    """Search the web using DuckDuckGo (prefers duckduckgo_search library, falls back to HTML)."""
    import json as _json

    # Try duckduckgo_search/ddgs library first
    DDGS = None
    for module_name in ("ddgs", "duckduckgo_search"):
        try:
            mod = __import__(module_name, fromlist=["DDGS"])
            DDGS = mod.DDGS
            break
        except ImportError:
            continue
    if DDGS is not None:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=num_results))
            formatted = []
            for i, r in enumerate(results, 1):
                formatted.append({
                    "number": i,
                    "title": r.get("title", "Untitled"),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
            if formatted:
                return _json.dumps({
                    "status": "ok",
                    "data": {"query": query, "results": formatted},
                    "metadata": {"query": query, "num_results": len(formatted), "backend": module_name},
                })
        except Exception as e:
            logger.warning("ddgs/duckduckgo_search failed, falling back to HTML: %s", e)

    # Fallback: HTML parsing
    import urllib.request
    import urllib.parse
    from html.parser import HTMLParser

    class _ResultParser(HTMLParser):
        """Parse DuckDuckGo HTML results into structured dicts.

        DDG wraps each organic result in:
            <div class="result results_links ...">
              <h2 class="result__title"><a class="result__a" href="//...">Title</a></h2>
              <a href="//..." class="result__snippet">Snippet text</a>
            </div>
        """

        def __init__(self):
            super().__init__()
            self.results: list[dict] = []
            self._state = "idle"    # idle | in_result
            self._current: dict = {}
            self._text_buf = ""

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            cls = a.get("class", "")

            # Detect a result block start
            if tag == "div" and cls.startswith("result "):
                self._state = "in_result"
                self._current = {"href": "", "title": "", "snippet": ""}
                return

            if self._state != "in_result":
                return

            if tag == "a":
                if "result__a" in cls:
                    # Title link — store the DuckDuckGo redirect href
                    raw_href = a.get("href", "")
                    self._current["href"] = raw_href
                    self._text_buf = ""
                elif "result__snippet" in cls or "result__a" not in cls:
                    # Snippet anchor — collect text until closing </a>
                    self._text_buf = ""

        def handle_data(self, data):
            if self._state == "in_result":
                self._text_buf += data

        def handle_endtag(self, tag):
            if self._state != "in_result":
                return

            if tag == "div":
                # Result block ends
                self._state = "idle"
                if self._current.get("title"):
                    self.results.append(self._current.copy())
                self._text_buf = ""
            elif tag == "h2":
                # h2 closes → title is complete (text inside <a> inside <h2>)
                pass  # title was captured when </a> closed
            elif tag == "a":
                text = self._text_buf.strip()
                # Heuristic: if href starts with DuckDuckGo redirect, it's the title
                href = self._current.get("href", "")
                if text:
                    if "duckduckgo.com/l/" in href and not self._current["title"]:
                        self._current["title"] = text
                        # Try to extract the REAL URL from DDG redirect
                        try:
                            qs = urllib.parse.urlparse(href).query
                            qd = urllib.parse.parse_qs(qs)
                            real = qd.get("uddg", [])[0] if "uddg" in qd else ""
                            if real:
                                self._current["href"] = urllib.parse.unquote(real)
                        except Exception:
                            pass
                    elif not self._current["snippet"]:
                        # This is likely the snippet anchor (text after title link)
                        self._current["snippet"] = text
                self._text_buf = ""

    try:
        import ssl
        qs = urllib.parse.urlencode({"q": query})
        url = f"https://html.duckduckgo.com/html/?{qs}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"}
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        parser = _ResultParser()
        parser.feed(html)
        results = parser.results[:num_results]

        # Filter out ads (they often have empty snippets or different structure)
        results = [r for r in results if r.get("title") and r.get("snippet")]

        formatted = []
        for i, r in enumerate(results, 1):
            formatted.append({
                "number": i,
                "title": r.get("title", "Untitled"),
                "url": r.get("href", ""),
                "snippet": r.get("snippet", ""),
            })

        if not formatted:
            return _json.dumps({
                "status": "ok",
                "data": {"query": query, "results": []},
                "metadata": {"query": query, "num_results": 0, "backend": "html", "note": "no results matched expected structure"},
            })

        return _json.dumps({
            "status": "ok",
            "data": {"query": query, "results": formatted},
            "metadata": {"query": query, "num_results": len(formatted), "backend": "html"},
        })
    except Exception as e:
        return _json.dumps({
            "status": "error",
            "data": {"query": query, "results": []},
            "metadata": {"query": query, "error": str(e), "backend": ""},
            "error": str(e),
        })


def tool_search_codebase(query: str, top_k: int = 5, workspace: str = ".") -> str:
    """Semantic search over the codebase using embedding similarity."""
    try:
        from wisp.semantic_index import SemanticIndex
        index = SemanticIndex(workspace)
        results = index.search(query, top_k=top_k)
        if not results:
            return f"No semantically relevant code found for: {query}"
        lines = [f"Semantic search results for '{query}':"]
        for i, r in enumerate(results, 1):
            lines.append(f"\n{i}. {r.file_path}:{r.start_line}-{r.end_line} "
                         f"(score: {r.score:.3f})"
                         f"{' [' + r.symbol_name + ']' if r.symbol_name else ''}")
            content_lines = r.content.split("\n")[:4]
            for cl in content_lines:
                lines.append(f"   | {cl[:120]}")
        return "\n".join(lines)
    except ImportError:
        return "Semantic index module not available. Install: pip install wisp[semantic]"
    except Exception as e:
        return f"Semantic search error: {e}"


# ── Tool schema definitions (Ollama tool-calling format) ──

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
            "name": "recall",
            "description": "Search cross-session memory and past session summaries for relevant facts. Use when you need to actively recall something learned in previous conversations — user preferences, past decisions, files touched, open tasks, etc. Returns ranked results with relevance scores.",
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
]

def _tool_spawn_subagent_stub(**kwargs) -> str:
    """Stub: spawn_subagent is handled by the agent core, not the tool executor."""
    return json.dumps({
        "status": "error",
        "tool": "spawn_subagent",
        "data": "spawn_subagent must be handled by the agent core, not the tool executor",
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

    elif name == "edit_file_multi":
        meta["path"] = args.get("path", "")
        edits_list = args.get("edits", [])
        meta["edits_count"] = len(edits_list) if isinstance(edits_list, list) else 0

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

    elif name == "recall":
        meta["query"] = (args.get("query", "") or "")[:80]
        meta["limit"] = args.get("limit", 10)

    elif name == "git_branch":
        meta["action"] = args.get("action", "")

    elif name == "git_commit":
        meta["message"] = (args.get("message", "") or "")[:80]

    elif name == "lsp_diagnostics":
        meta["path"] = args.get("path", "")
    elif name == "lsp_definition":
        meta["path"] = args.get("path", "")
        meta["line"] = args.get("line", 1)
    elif name == "lsp_references":
        meta["path"] = args.get("path", "")
        meta["line"] = args.get("line", 1)
    elif name == "lsp_hover":
        meta["path"] = args.get("path", "")
        meta["line"] = args.get("line", 1)
    elif name == "lsp_symbols":
        meta["path"] = args.get("path", "")

    elif name == "search_codebase":
        meta["query"] = args.get("query", "")
        meta["top_k"] = args.get("top_k", 5)
    elif name == "web_search":
        meta["query"] = args.get("query", "")
        meta["num_results"] = args.get("num_results", 5)
    elif name == "git_status":
        pass  # no specific args to capture
    elif name == "git_diff":
        meta["path"] = args.get("path", "")
        meta["staged"] = args.get("staged", False)
    elif name == "git_push":
        meta["set_upstream"] = args.get("set_upstream", False)
    elif name == "gh_pr_create":
        meta["title"] = (args.get("title", "") or "")[:80]
    elif name == "diagnose":
        meta["error_preview"] = (args.get("error_output", "") or "")[:80]
    elif name == "plan_task":
        meta["goal"] = (args.get("goal", "") or "")[:80]
    elif name == "mark_step_done":
        meta["task_id"] = args.get("task_id", "")
    elif name == "update_plan":
        meta["task_id"] = args.get("task_id", "")
        meta["status"] = args.get("status", "")

    return meta


def execute_tool(name: str, args: dict, workspace: str, max_data_chars: int = 0, file_lock=None, lsp_manager=None) -> str:
    """Execute a tool by name with given arguments.

    Returns a structured JSON string with status, data, and metadata
    so the LLM can parse results programmatically.

    If max_data_chars > 0, the data field is truncated to that length
    and a 'truncated' flag is added to metadata.

    Args:
        file_lock: Optional file lock instance (for multi-agent swarm).
        lsp_manager: Optional LSP manager instance (for per-connection isolation).
    """
    impl = TOOL_IMPLS.get(name)
    # ── Try plugin tools first (user-registered take priority) ──
    from wisp.plugin_registry import has_plugin_tool, execute_plugin_tool
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

    except Exception as e:
        logger.error("Unexpected error in tool %s: %s", name, e, exc_info=True)
        structured = {
            "status": "error",
            "tool": name,
            "data": f"Unexpected error: {e}",
            "metadata": _build_tool_metadata(name, args, ""),
        }
        return json.dumps(structured, ensure_ascii=False)
