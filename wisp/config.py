"""Configuration for Wisp — reads settings from environment, CLI args, and config files."""

import os
import json
from pathlib import Path
from typing import Optional

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "kimi-k2.6:cloud"
DEFAULT_MAX_CONTEXT_TOKENS = 256000
WISP_CONFIG_DIR = Path.home() / ".config" / "wisp"


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
    """Save config to ~/.config/wisp/config.json."""
    cfg_path = get_config_path()
    cfg_path.write_text(json.dumps(config, indent=2))


def get_setting(key: str, default=None):
    """Resolve a setting: env var > config file > default."""
    config = load_config()
    env_key = f"WISP_{key.upper()}"
    env_val = os.environ.get(env_key)
    if env_val is not None:
        return env_val
    return config.get(key, default)


class WispConfig:
    """Resolved Wisp configuration."""

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
