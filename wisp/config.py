"""Configuration for Wisp — reads settings from environment, CLI args, and config files."""

import os
import json
from pathlib import Path
from typing import Optional

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "deepseek-v4-flash:cloud"
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
    """Resolve a setting: config file > env var > default."""
    config = load_config()
    env_key = f"WISP_{key.upper()}"
    env_val = os.environ.get(env_key)
    return env_val or config.get(key, default)


class WispConfig:
    """Resolved Wisp configuration."""

    def __init__(self):
        self.ollama_url: str = get_setting("ollama_url", DEFAULT_OLLAMA_URL)
        self.model: str = get_setting("model", DEFAULT_MODEL)
        self.temperature: float = float(get_setting("temperature", "0.2"))
        self.max_tokens: int = int(get_setting("max_tokens", "16384"))
        self.skill_dirs: list[str] = get_setting(
            "skill_dirs",
            [
                ".agents/skills",
                ".warp/skills",
                ".claude/skills",
            ],
        )
        self.workspace: Optional[str] = get_setting("workspace", os.getcwd())
        # Auto-approve tool calls by default (coding agent should flow)
        self.auto_approve: bool = get_setting("auto_approve", "true").lower() == "true"
        # Show reasoning trace inline (default: false — most users want the answer only)
        self.show_thinking: bool = get_setting("show_thinking", "false").lower() == "true"

    def __repr__(self):
        return (
            f"WispConfig(ollama_url={self.ollama_url}, model={self.model}, "
            f"workspace={self.workspace})"
        )
