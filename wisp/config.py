"""Configuration for Wisp — reads settings from environment, CLI args, and config files.

Settings are resolved with priority: env vars > config file > defaults.
Config file is stored at ~/.config/wisp/config.json.
"""

import os
import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "deepseek-v4-pro:cloud"
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
    "show_thinking": {
        "type": bool,
        "default": True,
        "description": "Show model reasoning trace inline",
        "env_var": "WISP_SHOW_THINKING",
    },
    "max_iterations": {
        "type": int,
        "default": 30,
        "min": 1,
        "max": 100,
        "description": "Max agent loop iterations per user turn",
        "env_var": "WISP_MAX_ITERATIONS",
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
        # Auto-approve tool calls by default (coding agent should flow)
        self.auto_approve: bool = str(get_setting("auto_approve", "true")).lower() == "true"
        # Show reasoning trace inline (default: false — most users want the answer only)
        self.show_thinking: bool = str(get_setting("show_thinking", "true")).lower() == "true"
        # Max agent loop iterations per user turn
        self.max_iterations: int = int(get_setting("max_iterations", "30"))
        # Context window guard: trim oldest messages when estimated tokens exceed this
        raw_ctx = get_setting("max_context_tokens")
        self.max_context_tokens: int = int(raw_ctx) if raw_ctx is not None else DEFAULT_MAX_CONTEXT_TOKENS
        # Track whether user explicitly set context window (disables auto-detection)
        self._context_tokens_explicit: bool = raw_ctx is not None
        # Permissions: full | ask_all | auto_edit | read_only
        self.permission_mode: str = get_setting("permission_mode", "full")
        # Plan mode: agent plans only, no tool execution
        self.plan_mode: bool = str(get_setting("plan_mode", "false")).lower() == "true"
        # Plan context: approved plan injected into system prompt
        self.plan_context: Optional[str] = None
        # Tokens per character estimate for context budget (4 is conservative for code/text)
        self.chars_per_token: int = int(get_setting("chars_per_token", "4"))
        # Auto-compaction settings
        self.auto_compact: bool = str(get_setting("auto_compact", "true")).lower() == "true"
        self.compact_threshold_tokens: int = int(get_setting("compact_threshold_tokens", "75"))
        self.compact_keep_recent: int = int(get_setting("compact_keep_recent", "6"))
        # Context files
        raw_context_files = get_setting(
            "context_files",
            ["CLAUDE.md", "AGENTS.md", ".wisp/rules.md", "GEMINI.md"],
        )
        if isinstance(raw_context_files, str):
            self.context_files: list[str] = [f.strip() for f in raw_context_files.split(",") if f.strip()]
        elif isinstance(raw_context_files, list):
            self.context_files = raw_context_files
        else:
            self.context_files = ["CLAUDE.md", "AGENTS.md", ".wisp/rules.md", "GEMINI.md"]
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

    def __repr__(self):
        return (
            f"WispConfig(provider={self.provider}, ollama_url={self.ollama_url}, model={self.model}, "
            f"workspace={self.workspace})"
        )
