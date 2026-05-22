"""SessionDTO — backward-compatible session dataclass.

Extracted from wisp/adapters.py during Phase 7.1 migration.
Provides SessionDTO.create() / .to_dict() / .from_dict() / .compact() / .touch().
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class SessionDTO:
    """Backward-compatible Session dataclass. Replaces old wisp.session.Session."""

    id: str
    created_at: str
    updated_at: str
    model: str
    workspace: str
    messages: list[dict] = field(default_factory=list)
    title: str = ""
    compaction_history: list[dict] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)

    @classmethod
    def create(cls, model: str, workspace: str, first_prompt: str) -> "SessionDTO":
        """Create a new session from a first user prompt."""
        now = _now_iso()
        slug = _slugify(first_prompt)
        sid = f"{_timestamp_id()}-{slug}" if slug else _timestamp_id()
        return cls(
            id=sid,
            created_at=now,
            updated_at=now,
            model=model,
            workspace=workspace,
            messages=[],
            title=first_prompt[:60].strip(),
        )

    def to_dict(self) -> dict:
        """Serialize to dictionary for UnifiedStore."""
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "model": self.model,
            "workspace": self.workspace,
            "messages": self.messages,
            "title": self.title,
            "compaction_history": self.compaction_history,
            "task_ids": self.task_ids,
        }

    def touch(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = _now_iso()

    def compact(self, keep_recent: int = 4, max_context_tokens: int = 4096) -> None:
        """Compact session messages, keeping recent ones."""
        if len(self.messages) <= keep_recent:
            return
        try:
            from wisp.semantic_compressor import SemanticCompressor
            compressor = SemanticCompressor()
            result = compressor.compress(
                messages=self.messages,
                keep_recent=keep_recent,
                max_context_tokens=max_context_tokens,
            )
            self.messages = result.messages
            self.compaction_history.append({
                "before_count": result.compression_stats.get("before_messages", len(self.messages)),
                "after_count": len(self.messages),
                "timestamp": _now_iso(),
                "method": "semantic",
            })
            return
        except Exception:
            pass

        old_count = len(self.messages)
        to_summarize = self.messages[:-keep_recent]
        kept = self.messages[-keep_recent:]
        summary = f"[Compacted {len(to_summarize)} messages]"
        self.messages = [{"role": "system", "content": summary}] + kept
        self.compaction_history.append({
            "before_count": old_count,
            "after_count": len(self.messages),
            "timestamp": _now_iso(),
        })

    @classmethod
    def from_dict(cls, data: dict) -> "SessionDTO":
        """Deserialize from dictionary."""
        return cls(
            id=data.get("id", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            model=data.get("model", ""),
            workspace=data.get("workspace", ""),
            messages=data.get("messages", []),
            title=data.get("title", ""),
            compaction_history=data.get("compaction_history", []),
            task_ids=data.get("task_ids", []),
        )


def _slugify(text: str, max_len: int = 40) -> str:
    """Turn free text into a URL-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-")


def _timestamp_id() -> str:
    """Generate a sortable session ID."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%d-%H%M%S-%f")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
