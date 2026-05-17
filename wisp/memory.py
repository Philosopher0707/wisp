"""Cross-session memory — persistent facts that survive across conversations.

Stores learned preferences, decisions, and project-specific knowledge in
~/.config/wisp/memory.json. Facts are injected into the system prompt
so the LLM remembers context across sessions.

Each fact tracks: content, when added, last accessed, access count,
and importance (important facts resist eviction).

Structure:
  {
    "version": 2,
    "global_facts": [
      {
        "content": "user prefers tabs over spaces",
        "added": "2026-05-07T10:00:00Z",
        "last_accessed": "2026-05-07T12:00:00Z",
        "access_count": 5,
        "important": false
      }
    ],
    "workspace_facts": {
      "/path/to/project": [...]
    }
  }
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from wisp.config import WISP_CONFIG_DIR

logger = logging.getLogger(__name__)

_MAX_FACTS = 100  # Max total facts before LRU eviction kicks in


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(content: str) -> str:
    """Normalize fact text for dedup: strip, collapse whitespace."""
    return " ".join(content.strip().split())


def _resolve_workspace(workspace: str) -> str:
    """Canonical path with symlink resolution for stable workspace keys.

    Uses os.path.realpath so that /tmp/project and /private/tmp/project
    (macOS) resolve to the same key, preventing memory fragmentation.
    """
    return os.path.realpath(workspace)


# ── File I/O ────────────────────────────────────────────────────────────


def _get_memory_file() -> Path:
    WISP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return WISP_CONFIG_DIR / "memory.json"


def load_memory() -> dict:
    """Load memory from ~/.config/wisp/memory.json, migrating old formats."""
    path = _get_memory_file()
    if not path.exists():
        return {"version": 2, "global_facts": [], "workspace_facts": {}}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 2, "global_facts": [], "workspace_facts": {}}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load memory: %s", e)
        return {"version": 2, "global_facts": [], "workspace_facts": {}}

    # Migrate from version 1 (lists of strings) to version 2 (lists of dicts)
    if data.get("version", 1) < 2:
        data["version"] = 2
        data["global_facts"] = _migrate_facts(data.get("global_facts", []))
        ws_facts = {}
        for ws_path, facts in data.get("workspace_facts", {}).items():
            ws_facts[ws_path] = _migrate_facts(facts)
        data["workspace_facts"] = ws_facts
        try:
            _save(data)
        except OSError:
            pass

    data.setdefault("version", 2)
    data.setdefault("global_facts", [])
    data.setdefault("workspace_facts", {})
    return data


def _save(memory: dict):
    path = _get_memory_file()
    path.write_text(json.dumps(memory, indent=2, ensure_ascii=False) + "\n")


def _migrate_facts(old: list) -> list[dict]:
    """Migrate list of strings → list of fact dicts."""
    now = _now_iso()
    result = []
    for item in old:
        if isinstance(item, dict):
            item.setdefault("added", now)
            item.setdefault("last_accessed", now)
            item.setdefault("access_count", 0)
            item.setdefault("important", False)
            result.append(item)
        elif isinstance(item, str):
            result.append({
                "content": item,
                "added": now,
                "last_accessed": now,
                "access_count": 0,
                "important": False,
            })
    return result


# ── Fact dict helpers ───────────────────────────────────────────────────


def _make_fact(content: str, important: bool = False) -> dict:
    now = _now_iso()
    return {
        "content": _normalize(content),
        "added": now,
        "last_accessed": now,
        "access_count": 0,
        "important": important,
    }


def _fact_content(fact: dict) -> str:
    return fact.get("content", "")


def _match(fact: dict, normalized: str) -> bool:
    """Case-insensitive match against a normalized query."""
    return _normalize(_fact_content(fact)).lower() == normalized.lower()


def _touch(fact: dict):
    fact["last_accessed"] = _now_iso()
    fact["access_count"] = fact.get("access_count", 0) + 1


def _sort_key(fact: dict):
    """Sort: important first, then most recently accessed."""
    return (fact.get("important", False), fact.get("last_accessed", ""))


# ── Fact CRUD ───────────────────────────────────────────────────────────


def add_fact(content: str, workspace: Optional[str] = None,
             important: bool = False) -> bool:
    """Add a fact. Dedup is case-insensitive on normalized text.

    Returns True if added/updated, False if exact duplicate already exists.
    At capacity, evicts the least-recently-used non-important fact.
    """
    memory = load_memory()
    norm = _normalize(content)
    if not norm:
        return False

    if workspace:
        ws_path = _resolve_workspace(workspace)
        facts = memory.setdefault("workspace_facts", {}).setdefault(ws_path, [])
    else:
        facts = memory.setdefault("global_facts", [])

    # Check duplicate
    for f in facts:
        if _match(f, norm):
            # Update content if genuinely different (case-insensitive)
            if _normalize(_fact_content(f)).lower() != norm.lower():
                f["content"] = norm
            _touch(f)
            _save(memory)
            return False  # Not a new fact, but refreshed

    # At capacity — evict LRU non-important fact
    total = _count_facts(memory)
    while total >= _MAX_FACTS:
        if not _evict_one(memory):
            logger.warning("Memory at capacity (%d), all facts important", total)
            return False
        total = _count_facts(memory)

    f = _make_fact(content, important=important)
    facts.append(f)
    _save(memory)
    logger.info("Added fact: %s", content[:80])
    return True


def remove_fact(content: str, workspace: Optional[str] = None) -> bool:
    """Remove a fact by content (case-insensitive match)."""
    memory = load_memory()
    norm = _normalize(content)

    if workspace:
        ws_path = _resolve_workspace(workspace)
        ws_facts = memory.get("workspace_facts", {})
        if ws_path not in ws_facts:
            return False
        facts = ws_facts[ws_path]
    else:
        facts = memory.get("global_facts", [])

    for i, f in enumerate(facts):
        if _match(f, norm):
            facts.pop(i)
            if workspace and not facts:
                del memory["workspace_facts"][ws_path]
            _save(memory)
            return True
    return False


def list_facts(workspace: Optional[str] = None) -> list[dict]:
    """Return global + workspace facts, sorted by importance → recency.

    Updates last_accessed and access_count on read (touch on access).
    """
    memory = load_memory()
    results: list[dict] = []

    for f in memory.get("global_facts", []):
        _touch(f)
        results.append(f)

    if workspace:
        ws_path = _resolve_workspace(workspace)
        ws_facts = memory.get("workspace_facts", {})
        for f in ws_facts.get(ws_path, []):
            _touch(f)
            results.append(f)

    results.sort(key=_sort_key, reverse=True)
    _save(memory)
    return results


def list_all_facts() -> list[dict]:
    """Return ALL facts across global + every workspace, sorted by importance → recency.

    This makes memory work globally regardless of which directory the agent
    is currently running in. Updates last_accessed and access_count on read.
    """
    memory = load_memory()
    results: list[dict] = []

    for f in memory.get("global_facts", []):
        _touch(f)
        results.append(f)

    for ws_path, facts in memory.get("workspace_facts", {}).items():
        for f in facts:
            _touch(f)
            results.append(f)

    results.sort(key=_sort_key, reverse=True)
    _save(memory)
    return results


def set_importance(content: str, important: bool,
                   workspace: Optional[str] = None) -> bool:
    """Mark or unmark a fact as important. Returns True if found."""
    memory = load_memory()
    norm = _normalize(content)

    facts = _get_fact_list(memory, workspace)
    for f in facts:
        if _match(f, norm):
            f["important"] = important
            _touch(f)
            _save(memory)
            return True
    return False


def clear_memory(workspace: Optional[str] = None):
    """Clear all facts, optionally for a specific workspace."""
    memory = load_memory()

    if workspace:
        ws_path = _resolve_workspace(workspace)
        memory.get("workspace_facts", {}).pop(ws_path, None)
    else:
        memory["global_facts"] = []
        memory["workspace_facts"] = {}

    _save(memory)


# ── System prompt formatting ────────────────────────────────────────────


def format_memory_block(workspace: Optional[str] = None, include_all: bool = True) -> str:
    """Format memory facts as a system prompt block.

    By default (include_all=True) includes ALL facts across every workspace
    so memory works globally no matter which directory the agent is in.
    Set include_all=False to scope to just the current workspace + global.
    """
    if include_all:
        facts = list_all_facts()
    else:
        facts = list_facts(workspace)

    if not facts:
        return ""

    lines = ["## Learned Preferences (Global Memory)"]
    shown = 0
    for f in facts:
        content = _fact_content(f)
        marker = "⭐ " if f.get("important") else ""
        added = f.get("added", "")
        # Render YYYY-MM-DD prefix so model knows recency
        date_prefix = ""
        if added and len(added) >= 10:
            date_prefix = f"[{added[:10]}] "
        lines.append(f"- {marker}{date_prefix}{content}")
        shown += 1
        if shown >= 20:
            break

    lines.append("")
    lines.append("(Use `remember` to add facts, `forget` to remove)")
    return "\n".join(lines)


# ── Internal helpers ────────────────────────────────────────────────────


def _get_fact_list(memory: dict, workspace: Optional[str] = None) -> list[dict]:
    if workspace:
        ws_path = _resolve_workspace(workspace)
        return memory.setdefault("workspace_facts", {}).setdefault(ws_path, [])
    return memory.setdefault("global_facts", [])


def _count_facts(memory: dict) -> int:
    count = len(memory.get("global_facts", []))
    for facts in memory.get("workspace_facts", {}).values():
        count += len(facts)
    return count


def _evict_one(memory: dict) -> bool:
    """Evict the least-recently-used fact, with important facts getting a bonus.

    Important facts receive a 30-day recency bonus: they are treated as if
    they were accessed 30 days more recently than their actual last_accessed.
    This means important facts survive longer but are NOT immortal.
    """
    all_facts: list[tuple[dict, str | None]] = []
    for f in memory.get("global_facts", []):
        all_facts.append((f, None))
    for ws_path, facts in memory.get("workspace_facts", {}).items():
        for f in facts:
            all_facts.append((f, ws_path))

    if not all_facts:
        return False

    now = datetime.now(timezone.utc)
    bonus = timedelta(days=30)

    def _effective_age(item: tuple[dict, str | None]) -> timedelta:
        fact, _ = item
        last_str = fact.get("last_accessed", "")
        if not last_str:
            return timedelta.max
        try:
            last = datetime.fromisoformat(last_str)
        except ValueError:
            return timedelta.max
        age = now - last
        # Important facts appear younger by 30 days
        if fact.get("important"):
            age -= bonus
        return age

    # Evict the fact with the largest effective age (oldest)
    oldest = max(all_facts, key=_effective_age)
    fact, ws = oldest

    if ws:
        ws_list = memory["workspace_facts"][ws]
        ws_list.remove(fact)
        if not ws_list:
            del memory["workspace_facts"][ws]
    else:
        memory["global_facts"].remove(fact)

    logger.info("Evicted LRU fact: %s", _fact_content(fact)[:80])
    return True
