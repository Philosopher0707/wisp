"""Shared utilities for Wisp tools.

This module contains common helpers used by all tool domains:
- Path resolution and security
- Input validation
- Dangerous command detection
- HTML text extraction
- Collaborative editing context variables
"""

import contextvars
import json
import logging
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

from wisp.tools.errors import ToolError

logger = logging.getLogger(__name__)

# Per-agent context variables for multi-agent concurrency safety.
_file_lock_ctx: contextvars.ContextVar = contextvars.ContextVar("file_lock", default=None)
_change_tracker_ctx: contextvars.ContextVar = contextvars.ContextVar("change_tracker", default=None)
_lsp_manager_ctx: contextvars.ContextVar = contextvars.ContextVar("lsp_manager", default=None)


def set_collaboration_tools(file_lock=None, change_tracker=None):
    """Set file lock and change tracker for the current agent context."""
    _file_lock_ctx.set(file_lock)
    _change_tracker_ctx.set(change_tracker)


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
_MAX_BASH_OUTPUT = 50_000               # chars of output to return to model
_MAX_CMD_LENGTH = 16384                 # max command length for safety
_MAX_OLD_TEXT_LENGTH = 5_000_000          # max length for old_text in edit operations
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


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

    # curl/wget piped to any interpreter
    if re.search(r'\b(curl|wget)\b.*\|\s*(sh|bash|zsh|python3?|perl|ruby|node)\b', cmd_lower):
        return "remote code execution (pipe to interpreter)"

    # rm of root filesystem
    if re.search(r'\brm\s+.*--no-preserve-root', cmd_lower):
        return "recursive deletion of root filesystem"
    if re.search(r'\brm\s+(-[a-zA-Z]*r|--recursive).*?\s*/\s*$', cmd_lower):
        return "recursive deletion of root"

    # fork bomb heuristic
    if re.search(r'\(\)\s*\{[^}]*\|[^}]*&[^}]*\}', cmd_lower):
        return "fork bomb detected"

    # -- Obfuscation / indirect execution vectors --

    # eval of any kind (eval "$(...)", eval `...`, eval 'string')
    if re.search(r'\beval\b', cmd_lower):
        return "dynamic code execution (eval)"

    # source / dot command with process substitution or remote fetch
    if re.search(r'\b(source|\.)\s+.*\b(curl|wget)\b', cmd_lower):
        return "remote code execution (source from curl/wget)"
    if re.search(r'\b(source|\.)\s*\s*<\s*\(', cmd_lower):
        return "dynamic code execution (source with process substitution)"

    # bash -c with dangerous subcommands
    if re.search(r'\bbash\s+-c\b', cmd_lower):
        return "dynamic code execution (bash -c)"

    # python/perl/ruby/node with -c or -e containing system/exec/os.system/child_process
    if re.search(r'\bpython3?\s+-[ce]\b', cmd_lower) and re.search(r'\b(os\.system|subprocess\.|exec\(|system\()', cmd_lower):
        return "dynamic code execution (python interpreter)"
    if re.search(r'\bperl\s+-[ce]\b', cmd_lower) and re.search(r'\b(system|exec|qx\()', cmd_lower):
        return "dynamic code execution (perl interpreter)"
    if re.search(r'\bruby\s+-[ce]\b', cmd_lower) and re.search(r'\b(system|exec|eval|backtick|`[^`]*`)', cmd_lower):
        return "dynamic code execution (ruby interpreter)"
    if re.search(r'\bnode\s+-[ce]\b', cmd_lower) and re.search(r'\b(child_process|exec|spawn|eval)', cmd_lower):
        return "dynamic code execution (node interpreter)"

    # find with -exec rm / -ok rm / -execdir rm
    if re.search(r'\bfind\b', cmd_lower) and re.search(r'-exec(dir)?\s+\b(rm|mv|cp|chmod|chown|dd)\b', cmd_lower):
        return "dangerous find -exec"

    # awk with system() or exec
    if re.search(r'\bawk\b', cmd_lower) and re.search(r'\b(system|exec)\s*\(', cmd_lower):
        return "dynamic code execution (awk system/exec)"

    # xargs with dangerous commands
    if re.search(r'\bxargs\b', cmd_lower) and re.search(r'\b(rm|mv|cp|chmod|chown|dd|sh|bash)\b', cmd_lower):
        return "dangerous xargs command"

    # command substitution $(...) or backticks in combination with bash/sh/eval
    if re.search(r'\b(bash|sh|zsh|eval)\b', cmd_lower) and re.search(r'[\$`]\s*\(|`[^`]*`', cmd_lower):
        return "dynamic code execution (command substitution)"

    # process substitution <(...) -- often used to hide curl | bash
    if re.search(r'\b(bash|sh|zsh|source|\.)\b.*<\s*\(', cmd_lower):
        return "dynamic code execution (process substitution)"

    # base64 / hex / rot13 decoded and executed
    if re.search(r'\b(base64|xxd|openssl)\b.*\|\s*(bash|sh|zsh|eval)', cmd_lower):
        return "encoded payload execution"

    return None


# Path fragments that are hook-controlled and should never be written
# to by agent tools, because hook scripts execute with the full process
# environment and can be self-installed by the agent, creating a privilege
# escalation path (write_file -> hook -> arbitrary code execution).
_SENSITIVE_HOOK_DIR_FRAGMENTS: frozenset[str] = frozenset({
    ".wisp/hooks",
    ".wisp\\hooks",  # Windows
})


_SENSITIVE_ENV_KEYS: frozenset[str] = frozenset({
    "WISP_API_KEY",
    "OLLAMA_HOST",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AZURE_OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "HF_TOKEN",
    "GITHUB_TOKEN",
    "GITLAB_TOKEN",
    "DOCKER_CONFIG",
    "KUBECONFIG",
    "HOME",  # prevents scripts that resolve via ~
    "USER",
    "SSH_AGENT_LAUNCHER",
    "SSH_AUTH_SOCK",
})


def _is_hook_controlled_path(path: str) -> bool:
    """Return True if the path resolves inside a hook-controlled directory."""
    # Normalize separators (collapse double backslashes, then unify)
    # Use os.path.normpath which handles both / and \\ on both platforms.
    norm = os.path.normpath(path).replace("\\", "/")
    # Must be inside the hooks/ directory, not just contain "hooks" as fragment
    for frag in _SENSITIVE_HOOK_DIR_FRAGMENTS:
        if frag in norm:
            idx = norm.index(frag)
            after = norm[idx + len(frag) :]
            if after == "" or after.startswith("/"):
                return True
    return False


def _resolve_path(path: str, workspace: str) -> Path:
    """Resolve a path relative to workspace, with security boundary enforcement.

    Uses os.path.realpath to follow symlinks and verify the resolved path
    is physically within the workspace directory. This prevents symlink
    escapes where a link inside the workspace points outside it.

    Returns the resolved absolute Path if it's within the workspace.
    Raises ToolError on path traversal or symlink escape attempts.
    """
    real_ws = os.path.realpath(workspace)
    if Path(path).is_absolute():
        real_target = os.path.realpath(path)
    else:
        real_target = os.path.realpath(os.path.join(real_ws, path))

    # Exact match (e.g., path is "." or the workspace itself)
    if real_target == real_ws:
        return Path(real_target)

    # Prefix check: target must be inside workspace, not a sibling
    # Use os.sep to avoid matching /workspace2 when workspace is /workspace
    prefix = real_ws if real_ws.endswith(os.sep) else real_ws + os.sep
    if not real_target.startswith(prefix):
        raise ToolError(
            f"Access denied: {path} resolves to {real_target}, "
            f"which is outside workspace {real_ws}"
        )
    return Path(real_target)


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


def _get_dependents(path: str, workspace: str) -> list[str]:
    """Find files that depend on the given file using the repo map."""
    try:
        from wisp.repo_map import RepoMap
        rm = RepoMap(Path(workspace).resolve())
        cached = rm.build(use_cache=True, fast_mode=True)
        if cached:
            return rm.get_dependents(path)[:10]
    except Exception:
        pass
    return []


def _fuzzy_find_text(content: str, old_text: str, threshold: float = 0.85) -> tuple[Optional[int], Optional[str], float]:
    """Find the best fuzzy match of old_text in content.

    Uses character-level similarity (Dice coefficient on bigrams) to find
    the closest match when exact matching fails.

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
