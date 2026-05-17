"""Agent memory store — persist and retrieve session summaries.

Stores structured session summaries in ~/.config/wisp/agent_memory/sessions.jsonl
and injects relevant past context into the system prompt.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from wisp.config import WISP_CONFIG_DIR
from wisp.summarizer import SessionSummary

logger = logging.getLogger(__name__)

AGENT_MEMORY_DIR = WISP_CONFIG_DIR / "agent_memory"
SESSIONS_FILE = AGENT_MEMORY_DIR / "sessions.jsonl"
_MAX_SUMMARIES = 100  # Keep last N summaries to prevent bloat


def _resolve_workspace(workspace: str) -> str:
    """Absolute path without symlink resolution. Avoids macOS /tmp→/private/tmp issues."""
    return os.path.normpath(os.path.abspath(workspace))


class AgentMemory:
    """Store and retrieve session summaries.

    Holds an in-memory cache of all session IDs and summaries to avoid
    re-reading the JSONL file on every save() call.
    """

    def __init__(self):
        AGENT_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self._seen_ids: set[str] = set()
        self._summaries: list[SessionSummary] = []
        self._loaded = False  # True once cache is warm

    def _ensure_loaded(self) -> None:
        """Populate in-memory cache from disk on first use."""
        if self._loaded:
            return
        self._load_into_cache()

    def _load_into_cache(self) -> None:
        """Read the JSONL file and populate _summaries / _seen_ids."""
        self._summaries = []
        self._seen_ids = set()
        if not SESSIONS_FILE.exists():
            self._loaded = True
            return

        try:
            with SESSIONS_FILE.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        summary = SessionSummary.from_dict(data)
                        self._summaries.append(summary)
                        self._seen_ids.add(summary.session_id)
                    except (json.JSONDecodeError, TypeError):
                        continue
        except OSError:
            pass
        self._loaded = True

    # ── Persistence ──

    def save(self, summary: SessionSummary) -> None:
        """Append a summary to sessions.jsonl. Skips if session_id already saved."""
        self._ensure_loaded()
        if summary.session_id in self._seen_ids:
            logger.debug("Session %s already summarized — skipping", summary.session_id)
            return
        try:
            with SESSIONS_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(summary.to_dict(), ensure_ascii=False) + "\n")
            self._summaries.append(summary)
            self._seen_ids.add(summary.session_id)
            logger.info("Saved session summary for %s", summary.session_id)
            self._rotate()
        except OSError as e:
            logger.error("Failed to save session summary: %s", e)

    def _has_session(self, session_id: str) -> bool:
        """Check if a session_id already exists in the store."""
        self._ensure_loaded()
        if session_id in self._seen_ids:
            return True
        # Fallback: another process may have written it.
        if not SESSIONS_FILE.exists():
            return False
        try:
            with SESSIONS_FILE.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("session_id") == session_id:
                            self._seen_ids.add(session_id)
                            return True
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return False

    def load_all(self) -> list[SessionSummary]:
        """Load all summaries from disk, oldest first."""
        self._ensure_loaded()
        return list(self._summaries)

    def load_recent(
        self,
        workspace: Optional[str] = None,
        limit: int = 5,
    ) -> list[SessionSummary]:
        """Load recent summaries, optionally filtered by workspace.

        Returns newest first (reverse chronological).
        """
        summaries = self.load_all()

        # Filter by workspace if provided
        if workspace:
            ws_path = _resolve_workspace(workspace)
            summaries = [s for s in summaries if s.workspace == ws_path]

        # Sort by timestamp descending (newest first)
        summaries.sort(key=lambda s: s.timestamp, reverse=True)

        return summaries[:limit]

    def load_recent_global(self, limit: int = 7) -> list[SessionSummary]:
        """Load recent summaries across ALL workspaces.

        Returns newest first (reverse chronological) regardless of directory.
        """
        summaries = self.load_all()
        summaries.sort(key=lambda s: s.timestamp, reverse=True)
        return summaries[:limit]

    def clear(self) -> None:
        """Delete all summaries."""
        self._summaries = []
        self._seen_ids = set()
        self._loaded = True
        if SESSIONS_FILE.exists():
            SESSIONS_FILE.unlink()
            logger.info("Cleared agent memory")

    # ── Rotation ──

    def _rotate(self) -> None:
        """If file exceeds _MAX_SUMMARIES, keep only the most recent."""
        if len(self._summaries) <= _MAX_SUMMARIES:
            return

        # Truncate in-memory cache
        self._summaries = self._summaries[-_MAX_SUMMARIES:]
        self._seen_ids = {s.session_id for s in self._summaries}

        try:
            with SESSIONS_FILE.open("w", encoding="utf-8") as f:
                for summary in self._summaries:
                    f.write(json.dumps(summary.to_dict(), ensure_ascii=False) + "\n")
            logger.info("Rotated agent memory to %d summaries", len(self._summaries))
        except OSError as e:
            logger.error("Failed to rotate agent memory: %s", e)

    # ── Prompt formatting ──

    def format_for_prompt(
        self,
        summaries: list[SessionSummary],
        last_messages: Optional[list[dict]] = None,
    ) -> str:
        """Format summaries and optional last messages into a system prompt block."""
        if not summaries and not last_messages:
            return ""

        lines: list[str] = []

        if summaries:
            lines.append("## Previous Session Context")
            for s in summaries:
                lines.append(f"\n### Session {s.session_id[:24]} ({s.timestamp[:10]})")
                if s.summary:
                    lines.append(f"**Summary:** {s.summary}")
                if s.key_decisions:
                    lines.append("**Decisions:**")
                    for d in s.key_decisions:
                        lines.append(f"  - {d}")
                if s.open_tasks:
                    lines.append("**Open tasks:**")
                    for t in s.open_tasks:
                        lines.append(f"  - {t}")
                if s.user_preferences:
                    lines.append("**User preferences:**")
                    for p in s.user_preferences:
                        lines.append(f"  - {p}")
                if s.files_touched:
                    files_str = ", ".join(s.files_touched[:5])
                    if len(s.files_touched) > 5:
                        files_str += f" (+{len(s.files_touched) - 5} more)"
                    lines.append(f"**Files touched:** {files_str}")

        if last_messages:
            lines.append("\n## Recent Conversation (last few messages from current session)")
            for msg in last_messages:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                if isinstance(content, list):
                    # Multimodal content — extract text parts
                    text_parts = [
                        p.get("text", "") for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    ]
                    content = "\n".join(text_parts)
                if role == "user":
                    lines.append(f"\n**User:** {content[:500]}")
                elif role == "assistant":
                    thinking = msg.get("thinking", "")
                    if thinking:
                        lines.append(f"\n**Assistant** *(thinking)*: {thinking[:300]}")
                    lines.append(f"**Assistant:** {content[:500]}")
                elif role == "tool":
                    lines.append(f"\n**Tool result** ({msg.get('name', 'unknown')}): {content[:300]}")

        return "\n".join(lines)


# Module-level singleton for production use (avoids re-parsing JSONL
# every time a new instance is created).  Tests should instantiate
# AgentMemory directly so that they can monkey-patch paths.
_agent_memory_singleton: AgentMemory | None = None


def get_agent_memory() -> AgentMemory:
    """Return the module-level singleton AgentMemory instance."""
    global _agent_memory_singleton
    if _agent_memory_singleton is None:
        _agent_memory_singleton = AgentMemory()
    return _agent_memory_singleton


# ── Singleton ───────────────────────────────────────────────────────

_agent_memory_singleton: AgentMemory | None = None


def get_agent_memory() -> AgentMemory:
    """Return the module-level singleton AgentMemory instance.

    Production code should call this instead of instantiating AgentMemory
    directly so that the in-memory cache persists across calls.
    """
    global _agent_memory_singleton
    if _agent_memory_singleton is None:
        _agent_memory_singleton = AgentMemory()
    return _agent_memory_singleton
