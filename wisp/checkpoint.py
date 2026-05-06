"""File content snapshots for undo/rollback support.

Before each file write or edit, the current file content is saved
as a FileSnapshot. The /undo slash command restores the last snapshot
and removes the corresponding messages from the conversation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class FileSnapshot:
    """A single file's state before a tool action."""

    filepath: str  # relative to workspace
    content_before: str  # full file content (empty string if file did not exist)
    existed_before: bool  # False means file was created by the tool action
    tool_name: str  # "write_file" or "edit_file"
    timestamp: str

    def to_dict(self) -> dict:
        return {
            "filepath": self.filepath,
            "content_before": self.content_before,
            "existed_before": self.existed_before,
            "tool_name": self.tool_name,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FileSnapshot":
        return cls(
            filepath=data["filepath"],
            content_before=data["content_before"],
            existed_before=data["existed_before"],
            tool_name=data["tool_name"],
            timestamp=data["timestamp"],
        )


class CheckpointStore:
    """Stack of file snapshots, one per successful file-write tool action."""

    def __init__(self, session_id: str = ""):
        self.session_id = session_id
        self._snapshots: list[FileSnapshot] = []

    def push(self, snapshot: FileSnapshot):
        self._snapshots.append(snapshot)
        logger.debug(
            "Checkpoint saved: %s (%d chars)", snapshot.filepath, len(snapshot.content_before)
        )

    def pop(self) -> Optional[FileSnapshot]:
        if not self._snapshots:
            return None
        return self._snapshots.pop()

    def peek(self) -> Optional[FileSnapshot]:
        if not self._snapshots:
            return None
        return self._snapshots[-1]

    def clear(self):
        count = len(self._snapshots)
        self._snapshots.clear()
        logger.debug("Cleared %d checkpoints", count)

    def __len__(self) -> int:
        return len(self._snapshots)

    def __bool__(self) -> bool:
        return True
