"""Change tracking for collaborative editing.

Records which files were modified by this agent session, when, and how.
Used for conflict detection and change notifications.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ChangeRecord:
    """A single file change made by the agent."""

    filepath: str
    action: str  # write, edit, delete
    timestamp: str
    agent_id: str
    size_before: int = 0
    size_after: int = 0
    lines_changed: int = 0
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ChangeRecord:
        return cls(**data)


class ChangeTracker:
    """Track file changes made by this agent session."""

    def __init__(self, workspace: str, agent_id: str):
        self.workspace = Path(workspace).resolve()
        self.agent_id = agent_id
        self.changes: list[ChangeRecord] = []
        self._seen_files: set[str] = set()

    def record_write(self, filepath: str, content: str, description: str = "") -> None:
        """Record a file write operation."""
        path = self._resolve(filepath)
        size_before = self._get_size(path)
        size_after = len(content.encode("utf-8"))
        rel_path = self._rel_path(path)

        record = ChangeRecord(
            filepath=rel_path,
            action="write",
            timestamp=_now_iso(),
            agent_id=self.agent_id,
            size_before=size_before,
            size_after=size_after,
            lines_changed=content.count("\n"),
            description=description,
        )
        self.changes.append(record)
        self._seen_files.add(rel_path)
        logger.debug("Recorded write: %s (%d bytes)", record.filepath, size_after)

    def record_edit(self, filepath: str, old_text: str, new_text: str, description: str = "") -> None:
        """Record a file edit operation."""
        path = self._resolve(filepath)
        size_before = len(old_text.encode("utf-8"))
        size_after = len(new_text.encode("utf-8"))
        lines_before = old_text.count("\n")
        lines_after = new_text.count("\n")
        rel_path = self._rel_path(path)

        record = ChangeRecord(
            filepath=rel_path,
            action="edit",
            timestamp=_now_iso(),
            agent_id=self.agent_id,
            size_before=size_before,
            size_after=size_after,
            lines_changed=abs(lines_after - lines_before),
            description=description,
        )
        self.changes.append(record)
        self._seen_files.add(rel_path)
        logger.debug("Recorded edit: %s (%d lines changed)", record.filepath, record.lines_changed)

    def record_delete(self, filepath: str, description: str = "") -> None:
        """Record a file deletion."""
        path = self._resolve(filepath)
        rel_path = self._rel_path(path)
        record = ChangeRecord(
            filepath=rel_path,
            action="delete",
            timestamp=_now_iso(),
            agent_id=self.agent_id,
            description=description,
        )
        self.changes.append(record)
        self._seen_files.discard(rel_path)
        logger.debug("Recorded delete: %s", record.filepath)

    def get_changes(self, action: Optional[str] = None) -> list[ChangeRecord]:
        """Get all changes, optionally filtered by action."""
        if action:
            return [c for c in self.changes if c.action == action]
        return list(self.changes)

    def get_changed_files(self) -> list[str]:
        """Return list of unique files that were modified."""
        return sorted(self._seen_files)

    def summary(self) -> str:
        """Return a human-readable summary of changes."""
        if not self.changes:
            return "No changes made."

        writes = len([c for c in self.changes if c.action == "write"])
        edits = len([c for c in self.changes if c.action == "edit"])
        deletes = len([c for c in self.changes if c.action == "delete"])
        files = len(self._seen_files)

        lines = [
            f"Changes this session: {writes} writes, {edits} edits, {deletes} deletes ({files} files)",
        ]
        for c in self.changes:
            size_info = f" ({c.size_before} → {c.size_after} bytes)" if c.action in ("write", "edit") else ""
            lines.append(f"  [{c.action}] {c.filepath}{size_info}")
            if c.description:
                lines.append(f"      {c.description}")

        return "\n".join(lines)

    def to_json(self) -> str:
        """Serialize changes to JSON."""
        return json.dumps(
            {
                "agent_id": self.agent_id,
                "workspace": str(self.workspace),
                "timestamp": _now_iso(),
                "changes": [c.to_dict() for c in self.changes],
            },
            indent=2,
            ensure_ascii=False,
        )

    def _rel_path(self, filepath: str) -> str:
        """Return filepath relative to workspace, falling back to basename."""
        path = self._resolve(filepath)
        try:
            return str(path.relative_to(self.workspace))
        except ValueError:
            return str(path)

    def _resolve(self, filepath: str) -> Path:
        """Resolve a filepath relative to workspace."""
        path = Path(filepath)
        if not path.is_absolute():
            path = self.workspace / path
        return path.resolve()

    def _get_size(self, path: Path) -> int:
        """Get file size in bytes, or 0 if not exists."""
        try:
            return path.stat().st_size
        except OSError:
            return 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
