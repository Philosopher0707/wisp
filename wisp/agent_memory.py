"""Agent memory store — persist and retrieve session summaries.

Stores structured session summaries in ~/.config/wisp/agent_memory/sessions.jsonl
and injects relevant past context into the system prompt.
"""

from __future__ import annotations

import json
import logging
import os
from collections import deque
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
    """Store and retrieve session summaries."""

    def __init__(self):
        AGENT_MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    # ── Persistence ──

    def save(self, summary: SessionSummary) -> None:
        """Append a summary to sessions.jsonl. Skips if session_id already saved."""
        if self._has_session(summary.session_id):
            logger.debug("Session %s already summarized — skipping", summary.session_id)
            return
        try:
            with SESSIONS_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(summary.to_dict(), ensure_ascii=False) + "\n")
            logger.info("Saved session summary for %s", summary.session_id)
            self._rotate()
        except OSError as e:
            logger.error("Failed to save session summary: %s", e)

    def _has_session(self, session_id: str) -> bool:
        """Check if a session_id already exists in the store."""
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
                            return True
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return False

    def load_all(self) -> list[SessionSummary]:
        """Load all summaries from disk, oldest first."""
        summaries: list[SessionSummary] = []
        if not SESSIONS_FILE.exists():
            return summaries

        try:
            with SESSIONS_FILE.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        summaries.append(SessionSummary.from_dict(data))
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.warning("Skipping corrupt summary line: %s", e)
                        continue
        except OSError as e:
            logger.error("Failed to load session summaries: %s", e)

        return summaries

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
        if SESSIONS_FILE.exists():
            SESSIONS_FILE.unlink()
            logger.info("Cleared agent memory")

    # ── Rotation ──

    def _rotate(self) -> None:
        """If file exceeds _MAX_SUMMARIES, keep only the most recent."""
        if not SESSIONS_FILE.exists():
            return

        try:
            with SESSIONS_FILE.open("r", encoding="utf-8") as f:
                lines = [l for l in f if l.strip()]
        except OSError:
            return

        if len(lines) <= _MAX_SUMMARIES:
            return

        # Keep the most recent _MAX_SUMMARIES lines
        keep = lines[-_MAX_SUMMARIES:]
        try:
            with SESSIONS_FILE.open("w", encoding="utf-8") as f:
                f.writelines(keep)
            logger.info("Rotated agent memory to %d summaries", len(keep))
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
