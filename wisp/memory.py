"""Cross-session memory — persistent key-value facts that persist across conversations.

Stores learned preferences, decisions, and project-specific knowledge in
~/.config/wisp/memory.json. Memories are injected into the system prompt
so the LLM remembers context across sessions.

Structure:
  {
    "global_facts": ["fact1", "fact2"],
    "workspace_facts": {
      "/path/to/project": ["project-specific fact"]
    },
    "last_updated": "2026-04-30T18:00:00Z"
  }
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from wisp.config import WISP_CONFIG_DIR

logger = logging.getLogger(__name__)

_MAX_FACTS = 50  # Max total facts to prevent bloat


def _get_memory_file() -> Path:
    """Return path to memory file, creating dir if needed."""
    WISP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return WISP_CONFIG_DIR / "memory.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_memory() -> dict:
    """Load memory from ~/.config/wisp/memory.json."""
    path = _get_memory_file()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load memory file: %s", e)
    return {"global_facts": [], "workspace_facts": {}, "last_updated": _now_iso()}


def save_memory(memory: dict):
    """Save memory to ~/.config/wisp/memory.json."""
    memory["last_updated"] = _now_iso()
    path = _get_memory_file()
    path.write_text(json.dumps(memory, indent=2, ensure_ascii=False) + "\n")


def add_fact(fact: str, workspace: Optional[str] = None) -> bool:
    """Add a fact to memory. Returns True if added, False if duplicate or at capacity."""
    memory = load_memory()

    if workspace:
        # Workspace-specific fact
        ws_facts = memory.setdefault("workspace_facts", {})
        ws_path = str(Path(workspace).resolve())
        facts = ws_facts.setdefault(ws_path, [])

        if fact in facts:
            return False  # duplicate

        # Count total facts
        total = _count_facts(memory)
        if total >= _MAX_FACTS:
            logger.warning("Memory at capacity (%d facts)", _MAX_FACTS)
            return False

        facts.append(fact)
        save_memory(memory)
        logger.info("Added workspace fact: %s", fact[:80])
        return True
    else:
        # Global fact
        facts = memory.setdefault("global_facts", [])

        if fact in facts:
            return False  # duplicate

        total = _count_facts(memory)
        if total >= _MAX_FACTS:
            logger.warning("Memory at capacity (%d facts)", _MAX_FACTS)
            return False

        facts.append(fact)
        save_memory(memory)
        logger.info("Added global fact: %s", fact[:80])
        return True


def remove_fact(fact: str, workspace: Optional[str] = None) -> bool:
    """Remove a fact from memory. Returns True if removed."""
    memory = load_memory()

    if workspace:
        ws_facts = memory.setdefault("workspace_facts", {})
        ws_path = str(Path(workspace).resolve())
        facts = ws_facts.get(ws_path, [])
        if fact in facts:
            facts.remove(fact)
            if not facts:
                del ws_facts[ws_path]
            save_memory(memory)
            return True
    else:
        facts = memory.setdefault("global_facts", [])
        if fact in facts:
            facts.remove(fact)
            save_memory(memory)
            return True

    return False


def list_facts(workspace: Optional[str] = None) -> list[str]:
    """List all facts, optionally filtered by workspace.

    Returns global facts + workspace-specific facts for the given workspace.
    """
    memory = load_memory()
    facts: list[str] = []

    # Global facts first
    facts.extend(memory.get("global_facts", []))

    # Workspace-specific facts
    if workspace:
        ws_facts = memory.get("workspace_facts", {})
        ws_path = str(Path(workspace).resolve())
        facts.extend(ws_facts.get(ws_path, []))

    return facts


def format_memory_block(workspace: Optional[str] = None) -> str:
    """Format memory facts as a block for the system prompt.

    Returns an empty string if no facts exist.
    """
    facts = list_facts(workspace)
    if not facts:
        return ""

    lines = ["## Learned Preferences"]
    for fact in facts:
        lines.append(f"- {fact}")
    lines.append("")
    lines.append("(Use `remember` tool to add new facts, or `wisp memory` CLI)")
    return "\n".join(lines)


def clear_memory(workspace: Optional[str] = None):
    """Clear all facts, optionally for a specific workspace."""
    memory = load_memory()

    if workspace:
        ws_facts = memory.setdefault("workspace_facts", {})
        ws_path = str(Path(workspace).resolve())
        ws_facts.pop(ws_path, None)
    else:
        memory["global_facts"] = []
        memory["workspace_facts"] = {}

    save_memory(memory)


def _count_facts(memory: dict) -> int:
    """Count total facts across global and all workspaces."""
    count = len(memory.get("global_facts", []))
    for ws_facts in memory.get("workspace_facts", {}).values():
        count += len(ws_facts)
    return count
