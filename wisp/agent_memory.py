"""Agent memory store — persist and retrieve session summaries.

Stores structured session summaries in ~/.config/wisp/agent_memory/sessions.jsonl
and injects relevant past context into the system prompt.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from wisp.config import WISP_CONFIG_DIR
from wisp.summarizer import SessionSummary

logger = logging.getLogger(__name__)

AGENT_MEMORY_DIR = WISP_CONFIG_DIR / "agent_memory"
SESSIONS_FILE = AGENT_MEMORY_DIR / "sessions.jsonl"
_MAX_SUMMARIES = 50  # Keep last N summaries to prevent bloat


class AgentMemory:
    """Store and retrieve session summaries."""

    def __init__(self):
        AGENT_MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    # ── Persistence ──

    def save(self, summary: SessionSummary) -> None:
        """Append a summary to sessions.jsonl."""
        try:
            with SESSIONS_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(summary.to_dict(), ensure_ascii=False) + "\n")
            logger.info("Saved session summary for %s", summary.session_id)
            self._rotate()
        except OSError as e:
            logger.error("Failed to save session summary: %s", e)

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
        limit: int = 3,
    ) -> list[SessionSummary]:
        """Load recent summaries, optionally filtered by workspace.

        Returns newest first (reverse chronological).
        """
        summaries = self.load_all()

        # Filter by workspace if provided
        if workspace:
            ws_path = str(Path(workspace).resolve())
            summaries = [s for s in summaries if s.workspace == ws_path]

        # Sort by timestamp descending (newest first)
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

    def format_for_prompt(self, summaries: list[SessionSummary]) -> str:
        """Format summaries into a system prompt block."""
        if not summaries:
            return ""

        lines = ["## Previous Session Context"]
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

        return "\n".join(lines)
