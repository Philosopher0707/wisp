"""Configuration for Wisp — reads settings from environment, CLI args, and config files.

Settings are resolved with priority: env vars > config file > defaults.
Config file is stored at ~/.config/wisp/config.json.
"""

import os
import json
import logging
from enum import StrEnum
from pathlib import Path
from typing import Any, Optional


class PermissionMode(StrEnum):
    """Permission levels for tool execution — enforced by ToolExecutor."""
    FULL = "full"
    """All tools allowed, no restrictions."""
    ASK_ALL = "ask_all"
    """Safe reads auto-approved; writes, edits, and bash require user approval."""
    AUTO_EDIT = "auto_edit"
    """File edits and writes auto-approved; bash and git writes require approval."""
    READ_ONLY = "read_only"
    """Only safe reads allowed; all write/edit/bash operations are blocked."""

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "kimi-k2.6:cloud"
DEFAULT_MAX_CONTEXT_TOKENS = 256000
WISP_CONFIG_DIR = Path.home() / ".config" / "wisp"

# ── Schema definition ────────────────────────────────────────────────

SETTINGS_SCHEMA: dict[str, dict[str, Any]] = {
    "provider": {
        "type": str,
        "default": "ollama",
        "description": "Model provider backend",
        "env_var": "WISP_PROVIDER",
    },
    "ollama_url": {
        "type": str,
        "default": DEFAULT_OLLAMA_URL,
        "description": "Ollama API endpoint URL",
        "env_var": "WISP_OLLAMA_URL",
    },
    "model": {
        "type": str,
        "default": DEFAULT_MODEL,
        "description": "Default Ollama model",
        "env_var": "WISP_MODEL",
    },
    "temperature": {
        "type": float,
        "default": 0.2,
        "min": 0.0,
        "max": 2.0,
        "description": "Model temperature (0.0–2.0)",
        "env_var": "WISP_TEMPERATURE",
    },
    "max_tokens": {
        "type": (int, type(None)),
        "default": 8192,
        "description": "Max tokens per response (set to null/None for no limit)",
        "env_var": "WISP_MAX_TOKENS",
    },
    "skill_dirs": {
        "type": list,
        "default": [".agents/skills", ".warp/skills", ".claude/skills"],
        "description": "Directories to scan for skills",
        "env_var": "WISP_SKILL_DIRS",
    },
    "context_files": {
        "type": list,
        "default": ["CLAUDE.md", "AGENTS.md", ".wisp/rules.md", "GEMINI.md"],
        "description": "Project context files to load and inject into system prompt",
        "env_var": "WISP_CONTEXT_FILES",
    },
    "workspace": {
        "type": (str, type(None)),
        "default": None,
        "description": "Working directory (None = current dir)",
        "env_var": "WISP_WORKSPACE",
    },
    "auto_approve": {
        "type": bool,
        "default": True,
        "description": "Auto-approve tool calls without prompting",
        "env_var": "WISP_AUTO_APPROVE",
    },
    "permission_mode": {
        "type": str,
        "default": PermissionMode.AUTO_EDIT,
        "description": "Permission level: full | ask_all | auto_edit | read_only",
        "env_var": "WISP_PERMISSION_MODE",
    },
    "show_thinking": {
        "type": bool,
        "default": True,
        "description": "Show model reasoning trace inline",
        "env_var": "WISP_SHOW_THINKING",
    },
    "show_tool_output": {
        "type": bool,
        "default": True,
        "description": "Show full tool output (when false, collapse to one-liners)",
        "env_var": "WISP_SHOW_TOOL_OUTPUT",
    },
    "compact_mode": {
        "type": bool,
        "default": False,
        "description": "Minimal rendering mode — no boxes, flat output",
        "env_var": "WISP_COMPACT_MODE",
    },
    "log_format": {
        "type": str,
        "default": "text",
        "description": "Log output format: text (human-readable) or json (structured)",
        "env_var": "WISP_LOG_FORMAT",
    },
    "max_iterations": {
        "type": int,
        "default": 30,
        "min": 1,
        "max": 100,
        "description": "Max agent loop iterations per user turn",
        "env_var": "WISP_MAX_ITERATIONS",
    },
    "max_reflections": {
        "type": int,
        "default": 3,
        "min": 0,
        "max": 10,
        "description": "Max repeated identical tool calls before stopping (0 = disabled)",
        "env_var": "WISP_MAX_REFLECTIONS",
    },
    "max_context_tokens": {
        "type": int,
        "default": DEFAULT_MAX_CONTEXT_TOKENS,
        "min": 1024,
        "description": "Context window size in tokens (auto-detected if not set)",
        "env_var": "WISP_MAX_CONTEXT_TOKENS",
    },
    "chars_per_token": {
        "type": int,
        "default": 4,
        "min": 1,
        "max": 10,
        "description": "Estimated chars per token for context budgeting",
        "env_var": "WISP_CHARS_PER_TOKEN",
    },
    "auto_compact": {
        "type": bool,
        "default": True,
        "description": "Automatically compact sessions when they grow too long",
        "env_var": "WISP_AUTO_COMPACT",
    },
    "compact_threshold_tokens": {
        "type": int,
        "default": 75,
        "min": 10,
        "max": 95,
        "description": "Token usage percentage (0-100) to trigger auto-compaction",
        "env_var": "WISP_COMPACT_THRESHOLD_TOKENS",
    },
    "compact_keep_recent": {
        "type": int,
        "default": 10,
        "min": 4,
        "max": 50,
        "description": "Number of recent messages to preserve during compaction (must be even to preserve turn symmetry)",
        "env_var": "WISP_COMPACT_KEEP_RECENT",
    },
}


def get_schema() -> dict[str, dict[str, Any]]:
    """Return a copy of the settings schema."""
    return dict(SETTINGS_SCHEMA)


def validate_config(config: dict) -> list[str]:
    """Validate config values against the schema.

    Returns a list of error messages (empty if valid).
    Unknown keys are reported as warnings, not errors.
    """
    errors: list[str] = []
    for key, value in config.items():
        if key not in SETTINGS_SCHEMA:
            errors.append(f"Unknown setting: '{key}'")
            continue

        schema = SETTINGS_SCHEMA[key]
        expected_type = schema["type"]

        # Check type
        if not isinstance(value, expected_type):
            type_name = _type_name(expected_type)
            errors.append(
                f"'{key}': expected {type_name}, got {type(value).__name__} ({value!r})"
            )
            continue

        # Check numeric range
        if isinstance(value, (int, float)):
            if "min" in schema and value < schema["min"]:
                errors.append(
                    f"'{key}': {value} is below minimum {schema['min']}"
                )
            if "max" in schema and value > schema["max"]:
                errors.append(
                    f"'{key}': {value} is above maximum {schema['max']}"
                )

    return errors


def _type_name(tp: type | tuple) -> str:
    """Get a human-readable type name, handling unions."""
    if isinstance(tp, tuple):
        return " or ".join(t.__name__ for t in tp if t is not type(None)) + " or None"
    return tp.__name__


# ── File I/O ─────────────────────────────────────────────────────────


def get_config_path() -> Path:
    """Return path to wisp config file, creating dir if needed."""
    WISP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return WISP_CONFIG_DIR / "config.json"


def load_config() -> dict:
    """Load config from ~/.config/wisp/config.json."""
    cfg_path = get_config_path()
    if cfg_path.exists():
        try:
            return json.loads(cfg_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(config: dict):
    """Save config to ~/.config/wisp/config.json.

    Validates the config against the schema before saving.
    Raises ValueError if validation fails.
    """
    errors = validate_config(config)
    if errors:
        raise ValueError(
            "Cannot save config with invalid values:\n" + "\n".join(f"  - {e}" for e in errors)
        )
    cfg_path = get_config_path()
    cfg_path.write_text(json.dumps(config, indent=2) + "\n")


def get_setting(key: str, default=None):
    """Resolve a setting: env var > config file > default."""
    config = load_config()
    env_key = f"WISP_{key.upper()}"
    env_val = os.environ.get(env_key)
    if env_val is not None:
        return env_val
    return config.get(key, default)


# ── Resolved config ──────────────────────────────────────────────────


def _parse_bool(value: Any, default: bool) -> bool:
    """Parse a boolean setting, falling back to default on error."""
    if isinstance(value, bool):
        return value
    try:
        return str(value).lower() == "true"
    except Exception:
        return default


def _parse_int(value: Any, default: int, min_val: int | None = None, max_val: int | None = None) -> int:
    """Parse an integer setting, falling back to default on error."""
    if isinstance(value, int):
        result = value
    else:
        try:
            result = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default
    if min_val is not None and result < min_val:
        return min_val
    if max_val is not None and result > max_val:
        return max_val
    return result


def _parse_float(value: Any, default: float, min_val: float | None = None, max_val: float | None = None) -> float:
    """Parse a float setting, falling back to default on error."""
    if isinstance(value, float):
        result = value
    elif isinstance(value, int):
        result = float(value)
    else:
        try:
            result = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default
    if min_val is not None and result < min_val:
        return min_val
    if max_val is not None and result > max_val:
        return max_val
    return result


class WispConfig:
    """Resolved Wisp configuration.

    Reads from env vars, config file, and defaults (in priority order).
    """

    def __init__(self):
        self.provider: str = get_setting("provider", "ollama")
        self.ollama_url: str = get_setting("ollama_url", DEFAULT_OLLAMA_URL)
        self.model: str = get_setting("model", DEFAULT_MODEL)
        self.temperature: float = float(get_setting("temperature", "0.2"))
        raw_max_tokens = get_setting("max_tokens", 8192)
        self.max_tokens: Optional[int] = (
            int(raw_max_tokens) if raw_max_tokens is not None else None
        )
        raw_skill_dirs = get_setting(
            "skill_dirs",
            [
                ".agents/skills",
                ".warp/skills",
                ".claude/skills",
            ],
        )
        # Ensure it's a list (env vars come as strings, config file may have list)
        if isinstance(raw_skill_dirs, str):
            self.skill_dirs = [d.strip() for d in raw_skill_dirs.split(",") if d.strip()]
        elif isinstance(raw_skill_dirs, list):
            self.skill_dirs = raw_skill_dirs
        else:
            self.skill_dirs = [".agents/skills", ".warp/skills", ".claude/skills"]
        self.workspace: Optional[str] = get_setting("workspace", os.getcwd())
        # Auto-approve tool calls (default FALSE — require explicit opt-in).
        # When false, every mutating tool call triggers an approval_request
        # event and the transport layer must confirm before execution.
        self.auto_approve: bool = _parse_bool(get_setting("auto_approve", "false"), False)
        # Show reasoning trace inline
        self.show_thinking: bool = _parse_bool(get_setting("show_thinking", "true"), True)
        # Show full tool output (when false, collapse to one-liners)
        self.show_tool_output: bool = _parse_bool(get_setting("show_tool_output", "true"), True)
        # Minimal rendering mode — no boxes, flat output, good for pipes/narrow terminals
        self.compact_mode: bool = _parse_bool(get_setting("compact_mode", "false"), False)
        # Max agent loop iterations per user turn
        self.max_iterations: int = _parse_int(get_setting("max_iterations", "30"), 30, 1, 100)
        # Max repeated identical tool calls before stopping (0 = disabled)
        self.max_reflections: int = _parse_int(get_setting("max_reflections", "3"), 3, 0, 10)
        # Context window guard: trim oldest messages when estimated tokens exceed this
        raw_ctx = get_setting("max_context_tokens")
        self.max_context_tokens: int = _parse_int(raw_ctx, DEFAULT_MAX_CONTEXT_TOKENS, 1024)
        # Track whether user explicitly set context window (disables auto-detection)
        self._context_tokens_explicit: bool = raw_ctx is not None
        # Permissions: full (all allowed) | ask_all (ask for writes) | auto_edit (ask for bash only) | read_only (no writes)
        self.permission_mode: PermissionMode = PermissionMode(get_setting("permission_mode", PermissionMode.FULL))
        # Plan mode: agent plans only, no tool execution
        self.plan_mode: bool = _parse_bool(get_setting("plan_mode", "false"), False)
        # Plan context: approved plan injected into system prompt
        self.plan_context: Optional[str] = None
        # Tokens per character estimate for context budget (4 is conservative for code/text)
        self.chars_per_token: int = _parse_int(get_setting("chars_per_token", "4"), 4, 1, 10)
        # Auto-compaction settings
        self.auto_compact: bool = _parse_bool(get_setting("auto_compact", "true"), True)
        self.compact_threshold_tokens: int = _parse_int(get_setting("compact_threshold_tokens", "75"), 75, 10, 95)
        self.compact_keep_recent: int = _parse_int(get_setting("compact_keep_recent", "6"), 6, 4, 50)
        self.compaction_model: str = get_setting("compaction_model", "") or ""
        # Concurrency limits
        self.thread_pool_size: int = _parse_int(
            get_setting("thread_pool_size", "8"), 8, 1, 64
        )
        self.subagent_pool_size: int = _parse_int(
            get_setting("subagent_pool_size", "4"), 4, 1, 32
        )
        self.max_subagent_timeout: int = _parse_int(
            get_setting("max_subagent_timeout", "600"), 600, 30, 3600
        )
        self.max_subagent_depth: int = _parse_int(
            get_setting("max_subagent_depth", "2"), 2, 0, 10
        )
        self.max_subagent_branching: int = _parse_int(
            get_setting("max_subagent_branching", "3"), 3, 1, 20
        )
        # Auto-delegation: detect multi-faceted prompts and split into subagents
        self.auto_delegate: bool = _parse_bool(
            get_setting("auto_delegate", "true"), True
        )
        self.delegation_threshold: float = _parse_float(
            get_setting("delegation_threshold", "0.18"), 0.18, 0.05, 0.95
        )
        # Write-classification tools requiring approval in restricted modes
        raw_write_tools = get_setting(
            "write_tools",
            ["write_file", "edit_file", "run_bash", "git_commit", "git_push",
             "gh_pr_create", "spawn_subagent"],
        )
        if isinstance(raw_write_tools, str):
            self.write_tools: list[str] = [t.strip() for t in raw_write_tools.split(",") if t.strip()]
        elif isinstance(raw_write_tools, list):
            self.write_tools = raw_write_tools
        else:
            self.write_tools = ["write_file", "edit_file", "run_bash", "git_commit",
                                "git_push", "gh_pr_create", "spawn_subagent"]
        # Subagent model fallback priority (tried in order for local subagent execution)
        raw_subagent_models = get_setting(
            "subagent_models",
            ["llama3.2", "llama3.1", "qwen2.5", "phi4", "gemma2"],
        )
        if isinstance(raw_subagent_models, str):
            self.subagent_models: list[str] = [m.strip() for m in raw_subagent_models.split(",") if m.strip()]
        elif isinstance(raw_subagent_models, list):
            self.subagent_models = raw_subagent_models
        else:
            self.subagent_models = ["llama3.2", "llama3.1", "qwen2.5", "phi4", "gemma2"]
        # Context files
        raw_context_files = get_setting(
            "context_files",
            ["wisp.md", "CLAUDE.md", "AGENTS.md", ".wisp/rules.md", "GEMINI.md"],
        )
        if isinstance(raw_context_files, str):
            self.context_files: list[str] = [f.strip() for f in raw_context_files.split(",") if f.strip()]
        elif isinstance(raw_context_files, list):
            self.context_files = raw_context_files
        else:
            self.context_files = ["wisp.md", "CLAUDE.md", "AGENTS.md", ".wisp/rules.md", "GEMINI.md"]
        self.loaded_context: str = ""
        self._context_mtimes: dict[str, float] = {}
        self._last_workspace_for_context: Optional[str] = None

    def load_context_files(self) -> str:
        """Load and concatenate context files from workspace root.

        Searches for files listed in ``context_files``, plus additional
        locations under ``.wisp/`` (rules.md, conventions.md) and user-home
        config (``~/.config/wisp/CLAUDE.md``).

        Returns concatenated content for injection into system prompt.
        Caches result -- re-reads if workspace changes or file mtimes change.
        """
        workspace = self.workspace or os.getcwd()
        ws_path = Path(workspace).resolve()

        # Check if cache is valid
        if self._last_workspace_for_context == str(ws_path) and self.loaded_context:
            # Verify no files changed
            stale = False
            for fpath, cached_mtime in list(self._context_mtimes.items()):
                try:
                    current_mtime = Path(fpath).stat().st_mtime
                    if current_mtime != cached_mtime:
                        stale = True
                        break
                except OSError:
                    stale = True
                    break
            if not stale:
                return self.loaded_context

        found_files: list[Path] = []
        mtimes: dict[str, float] = {}

        # 1. Search workspace root for each file in context_files list
        for fname in self.context_files:
            candidate = ws_path / fname
            if candidate.is_file():
                found_files.append(candidate)

        # 2. Also check .wisp/ directory for convention files
        wisp_dir = ws_path / ".wisp"
        for extra in ("rules.md", "conventions.md"):
            candidate = wisp_dir / extra
            if candidate.is_file() and candidate not in found_files:
                found_files.append(candidate)

        # 3. User home config CLAUDE.md
        user_claude = Path.home() / ".config" / "wisp" / "CLAUDE.md"
        if user_claude.is_file():
            found_files.append(user_claude)

        # 4. Read and concatenate
        blocks: list[str] = []
        for fpath in found_files:
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
                rel = fpath.relative_to(ws_path) if str(fpath).startswith(str(ws_path)) else fpath
                blocks.append(f"## Project Context: {rel}\n{content}\n---\n")
                mtimes[str(fpath)] = fpath.stat().st_mtime
            except Exception as e:
                logger.warning("Failed to read context file %s: %s", fpath, e)

        if blocks:
            self.loaded_context = (
                "## Project Context\n\n" + "\n".join(blocks)
            )
        else:
            self.loaded_context = ""

        self._context_mtimes = mtimes
        self._last_workspace_for_context = str(ws_path)
        logger.debug("Loaded %d context file(s) for workspace %s", len(found_files), ws_path)
        return self.loaded_context

    def validate(self) -> list[str]:
        """Validate this config instance against the schema.

        Returns a list of error messages (empty if valid).
        """
        errors: list[str] = []

        # Temperature
        if not (0.0 <= self.temperature <= 2.0):
            errors.append(
                f"temperature: {self.temperature} is out of range [0.0, 2.0]"
            )

        # Max iterations
        if not (1 <= self.max_iterations <= 100):
            errors.append(
                f"max_iterations: {self.max_iterations} is out of range [1, 100]"
            )

        # Max reflections
        if not (0 <= self.max_reflections <= 10):
            errors.append(
                f"max_reflections: {self.max_reflections} is out of range [0, 10]"
            )

        # Max context tokens
        if self.max_context_tokens < 1024:
            errors.append(
                f"max_context_tokens: {self.max_context_tokens} is below minimum 1024"
            )

        # Chars per token
        if not (1 <= self.chars_per_token <= 10):
            errors.append(
                f"chars_per_token: {self.chars_per_token} is out of range [1, 10]"
            )

        # Compact threshold
        if not (10 <= self.compact_threshold_tokens <= 95):
            errors.append(
                f"compact_threshold_tokens: {self.compact_threshold_tokens} is out of range [10, 95]"
            )

        # Compact keep recent
        if self.compact_keep_recent < 4:
            errors.append(
                f"compact_keep_recent: {self.compact_keep_recent} is below minimum 4"
            )

        # Permission mode
        valid_modes = {m.value for m in PermissionMode}
        if self.permission_mode not in valid_modes:
            errors.append(
                f"permission_mode: '{self.permission_mode}' is not one of {[m.value for m in PermissionMode]}"
            )

        # Provider
        if not self.provider:
            errors.append("provider: cannot be empty")

        # Model
        if not self.model:
            errors.append("model: cannot be empty")

        return errors

    def __repr__(self):
        return (
            f"WispConfig(provider={self.provider}, ollama_url={self.ollama_url}, model={self.model}, "
            f"workspace={self.workspace})"
        )
