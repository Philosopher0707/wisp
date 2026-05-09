"""Arena Mode — blind A/B comparison of models on real tasks.

Creates two isolated git worktrees, runs the same prompt with two different
models, returns side-by-side diffs with hidden identities. User votes before
seeing which model is which. Tracks per-project leaderboard.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from wisp.config import WispConfig
from wisp.core.agent import WispAgentCore
from wisp.core.events import (
    AgentEvent, TYPE_CONTENT, TYPE_DONE, TYPE_ERROR,
    TYPE_TOOL_CALL, TYPE_TOOL_RESULT,
)

logger = logging.getLogger(__name__)

LEADERBOARD_FILE = ".wisp/arena_leaderboard.json"


@dataclass
class ArenaEntry:
    id: str
    prompt: str
    task: str
    model_a: str
    model_b: str
    a_summary: str
    b_summary: str
    a_diff: str
    b_diff: str
    a_files_changed: list[str]
    b_files_changed: list[str]
    a_duration_ms: int
    b_duration_ms: int
    vote: Optional[str] = None  # 'a' | 'b' | 'tie' (set after user votes)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "prompt": self.prompt[:200],
            "task": self.task[:100],
            "model_a": self.model_a,
            "model_b": self.model_b,
            "a_summary": self.a_summary[:500],
            "b_summary": self.b_summary[:500],
            "a_files_changed": self.a_files_changed,
            "b_files_changed": self.b_files_changed,
            "a_duration_ms": self.a_duration_ms,
            "b_duration_ms": self.b_duration_ms,
            "vote": self.vote,
            "created_at": self.created_at,
        }

    def to_blind_dict(self, side: str) -> dict:
        """Returns data for ONE side, hiding the model name."""
        if side == "a":
            return {
                "id": self.id,
                "side": "A",
                "summary": self.a_summary[:500],
                "diff": self.a_diff,
                "files_changed": self.a_files_changed,
                "duration_ms": self.a_duration_ms,
            }
        return {
            "id": self.id,
            "side": "B",
            "summary": self.b_summary[:500],
            "diff": self.b_diff,
            "files_changed": self.b_files_changed,
            "duration_ms": self.b_duration_ms,
        }


@dataclass
class ArenaCompareRequest:
    prompt: str = ""
    task: str = ""
    model_a: str = "claude-sonnet-4-6"
    model_b: str = "claude-opus-4-7"
    workspace: str = "."


class ArenaRunner:
    """Orchestrates blind A/B model comparisons."""

    def __init__(self):
        self._entries: dict[str, ArenaEntry] = {}

    async def run_comparison(self, request: ArenaCompareRequest) -> ArenaEntry:
        """Run prompt with two models in parallel, compare results."""
        entry_id = f"arena-{uuid.uuid4().hex[:10]}"
        entry = ArenaEntry(
            id=entry_id,
            prompt=request.prompt,
            task=request.task or request.prompt[:100],
            model_a=request.model_a,
            model_b=request.model_b,
            a_summary="",
            b_summary="",
            a_diff="",
            b_diff="",
            a_files_changed=[],
            b_files_changed=[],
            a_duration_ms=0,
            b_duration_ms=0,
        )

        ws = Path(request.workspace).resolve()

        # Run both models in parallel
        results = await asyncio.gather(
            self._run_side(ws, request.prompt, request.model_a),
            self._run_side(ws, request.prompt, request.model_b),
        )

        (entry.a_summary, entry.a_diff, entry.a_files_changed, entry.a_duration_ms) = results[0]
        (entry.b_summary, entry.b_diff, entry.b_files_changed, entry.b_duration_ms) = results[1]

        self._entries[entry_id] = entry
        self._save_leaderboard(ws)

        return entry

    async def _run_side(self, workspace: Path, prompt: str,
                        model: str) -> tuple[str, str, list[str], int]:
        """Run the prompt with a single model. Returns (summary, diff, files, duration_ms)."""
        start = time.time()

        try:
            config = WispConfig()
            config.model = model
            config.workspace = str(workspace)
            config.permission_mode = "auto_edit"
            config.auto_approve = True

            core = WispAgentCore(config=config)
            content_parts: list[str] = []

            async for event in core.run(prompt):
                if event.type == TYPE_CONTENT:
                    content_parts.append(event.text)
                elif event.type == TYPE_ERROR:
                    content_parts.append(f"\n[Error: {event.data.get('message', 'unknown')}]")

            summary = "\n".join(content_parts)

            # Collect changed files and diff
            files_changed: list[str] = []
            diff = ""
            try:
                if hasattr(core.change_tracker, 'files_changed'):
                    files_changed = core.change_tracker.files_changed()
                if hasattr(core.change_tracker, 'cumulative_diff'):
                    diff = core.change_tracker.cumulative_diff()
            except Exception:
                pass

            # Fallback: get diff from git
            if not diff and (workspace / ".git").exists():
                diff = self._git_diff(workspace)

            duration_ms = round((time.time() - start) * 1000)
            return summary, diff, files_changed, duration_ms

        except Exception as e:
            duration_ms = round((time.time() - start) * 1000)
            return f"Error: {e}", "", [], duration_ms

    @staticmethod
    def _git_diff(workspace: Path) -> str:
        try:
            proc = subprocess.run(
                ["git", "diff"],
                cwd=str(workspace), capture_output=True, text=True, timeout=15,
            )
            return proc.stdout[:10000] if proc.stdout else ""
        except Exception:
            return ""

    def vote(self, entry_id: str, vote: str) -> Optional[ArenaEntry]:
        """Record a vote for an arena comparison."""
        entry = self._entries.get(entry_id)
        if not entry:
            return None
        entry.vote = vote
        return entry

    def get_entry(self, entry_id: str) -> Optional[ArenaEntry]:
        return self._entries.get(entry_id)

    def list_entries(self) -> list[ArenaEntry]:
        return sorted(self._entries.values(), key=lambda e: e.created_at, reverse=True)

    def get_leaderboard(self, workspace: str) -> list[dict]:
        """Get per-project leaderboard from stored votes."""
        lb_path = Path(workspace) / LEADERBOARD_FILE
        if not lb_path.exists():
            return []
        try:
            data = json.loads(lb_path.read_text())
            return data.get("entries", [])
        except Exception:
            return []

    def _save_leaderboard(self, workspace: Path):
        """Update the leaderboard with new entry."""
        lb_path = workspace / LEADERBOARD_FILE
        lb_path.parent.mkdir(parents=True, exist_ok=True)

        data: dict = {"entries": [], "updated_at": time.time()}
        if lb_path.exists():
            try:
                data = json.loads(lb_path.read_text())
            except Exception:
                pass

        # Count wins per model from voted entries
        model_wins: dict[str, int] = {}
        model_appearances: dict[str, int] = {}
        for entry in self._entries.values():
            if entry.vote:
                model_appearances[entry.model_a] = model_appearances.get(entry.model_a, 0) + 1
                model_appearances[entry.model_b] = model_appearances.get(entry.model_b, 0) + 1
                if entry.vote == "a":
                    model_wins[entry.model_a] = model_wins.get(entry.model_a, 0) + 1
                elif entry.vote == "b":
                    model_wins[entry.model_b] = model_wins.get(entry.model_b, 0) + 1

        data["model_stats"] = {
            m: {"wins": model_wins.get(m, 0), "appearances": model_appearances.get(m, 0),
                "win_rate": round(model_wins.get(m, 0) / max(1, model_appearances.get(m, 0)), 3)}
            for m in set(list(model_wins.keys()) + list(model_appearances.keys()))
        }

        lb_path.write_text(json.dumps(data, indent=2))


# Module-level singleton
_arena: Optional[ArenaRunner] = None


def get_arena() -> ArenaRunner:
    global _arena
    if _arena is None:
        _arena = ArenaRunner()
    return _arena
