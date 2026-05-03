"""Configuration for Wisp — reads settings from environment, CLI args, and config files.

Settings are resolved with priority: env vars > config file > defaults.
Config file is stored at ~/.config/wisp/config.json.
"""

import os
import json
from pathlib import Path
from typing import Any, Optional

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "kimi-k2.6:cloud"
DEFAULT_MAX_CONTEXT_TOKENS = 256000
WISP_CONFIG_DIR = Path.home() / ".config" / "wisp"

# ── Schema definition ────────────────────────────────────────────────

SETTINGS_SCHEMA: dict[str, dict[str, Any]] = {
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
        "default": None,
        "description": "Max tokens per response (None = no limit)",
        "env_var": "WISP_MAX_TOKENS",
    },
    "skill_dirs": {
        "type": list,
        "default": [".agents/skills", ".warp/skills", ".claude/skills"],
        "description": "Directories to scan for skills",
        "env_var": "WISP_SKILL_DIRS",
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
        "default": False,
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
        self.ollama_url: str = get_setting("ollama_url", DEFAULT_OLLAMA_URL)
        self.model: str = get_setting("model", DEFAULT_MODEL)
        self.temperature: float = float(get_setting("temperature", "0.2"))
        raw_max_tokens = get_setting("max_tokens")
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
        self.show_thinking: bool = str(get_setting("show_thinking", "false")).lower() == "true"
        # Max agent loop iterations per user turn
        self.max_iterations: int = int(get_setting("max_iterations", "30"))
        # Context window guard: trim oldest messages when estimated tokens exceed this
        raw_ctx = get_setting("max_context_tokens")
        self.max_context_tokens: int = int(raw_ctx) if raw_ctx is not None else DEFAULT_MAX_CONTEXT_TOKENS
        # Track whether user explicitly set context window (disables auto-detection)
        self._context_tokens_explicit: bool = raw_ctx is not None
        # Tokens per character estimate for context budget (4 is conservative for code/text)
        self.chars_per_token: int = int(get_setting("chars_per_token", "4"))

    def __repr__(self):
        return (
            f"WispConfig(ollama_url={self.ollama_url}, model={self.model}, "
            f"workspace={self.workspace})"
        )
