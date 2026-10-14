"""One durable record per subagent, refusals included (design S1, docs/harness/subagent-orchestration-design.md).

`SubagentOrchestrator.run` is the only place a child is made, so it is the only writer: a row when
the child starts (or is refused), updated once when it ends. Rows link to the parent agent and the
top-level session, so "what did this turn spawn, what did each child cost and change, and what was
refused and why" is one query. Background runs keep their own `background_runs` lifecycle (leases,
recovery); this record links to it rather than replacing it.

Writes are best-effort, like the run store's (ADR-0004): recording must never fail a child.
"""

from __future__ import annotations

import builtins
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

logger = logging.getLogger(__name__)

RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
REFUSED = "refused"
TERMINAL = frozenset({SUCCEEDED, FAILED, CANCELLED, REFUSED})

TASK_PREVIEW_CHARS = 200
ERROR_CHARS = 500


def new_agent_id() -> str:
    return "ag-" + uuid.uuid4().hex[:12]


@dataclass(frozen=True)
class AgentRecord:
    agent_id: str
    status: str
    parent_agent_id: str = ""
    root_session_id: str = ""
    origin: str = "spawn"
    name: str = ""
    role: str = ""
    depth: int = 0
    attempt: int = 0
    tools: tuple[str, ...] = ()
    model: str = ""
    task_preview: str = ""
    refusal_reason: str = ""
    error: str = ""
    started_at: float = 0.0
    ended_at: float = 0.0
    tokens_used: int = 0
    iterations: int = 0
    files_changed: tuple[str, ...] = ()
    worktree: bool = False
    links: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "AgentRecord":
        return cls(
            agent_id=row["agent_id"], status=row["status"], parent_agent_id=row["parent_agent_id"],
            root_session_id=row["root_session_id"], origin=row["origin"], name=row["name"], role=row["role"],
            depth=int(row["depth"]), attempt=int(row["attempt"]), tools=tuple(json.loads(row["tools"] or "[]")),
            model=row["model"], task_preview=row["task_preview"], refusal_reason=row["refusal_reason"],
            error=row["error"], started_at=float(row["started_at"]), ended_at=float(row["ended_at"]),
            tokens_used=int(row["tokens_used"]), iterations=int(row["iterations"]),
            files_changed=tuple(json.loads(row["files_changed"] or "[]")), worktree=bool(row["worktree"]),
            links=dict(json.loads(row["links"] or "{}")))

    def summary(self) -> dict[str, Any]:
        """What `subagent_list` shows for one node of the tree."""
        out: dict[str, Any] = {
            "agent_id": self.agent_id, "name": self.name, "role": self.role, "origin": self.origin,
            "depth": self.depth, "status": self.status,
            "elapsed_seconds": round(max(0.0, (self.ended_at or time.time()) - self.started_at), 1),
            "tokens_used": self.tokens_used,
        }
        if self.refusal_reason:
            out["refusal_reason"] = self.refusal_reason
        if self.error:
            out["error"] = self.error[:200]
        if self.files_changed:
            out["files_changed"] = list(self.files_changed)
        return out


class AgentRegistry:
    """The `agent_records` table of the unified store."""

    def __init__(self, store: Any):
        self._store = store

    def _write(self, op: str, *args: Any, **kwargs: Any) -> None:
        try:
            getattr(self._store, op)(*args, **kwargs)
        except Exception:
            logger.warning("agent record %s failed", op, exc_info=True)

    def _row(self, record: AgentRecord) -> dict[str, Any]:
        return {
            "agent_id": record.agent_id, "parent_agent_id": record.parent_agent_id,
            "root_session_id": record.root_session_id, "origin": record.origin, "name": record.name,
            "role": record.role, "depth": record.depth, "attempt": record.attempt,
            "tools": json.dumps(list(record.tools)), "model": record.model,
            "task_preview": record.task_preview[:TASK_PREVIEW_CHARS], "status": record.status,
            "refusal_reason": record.refusal_reason, "error": record.error[:ERROR_CHARS],
            "started_at": record.started_at, "ended_at": record.ended_at, "tokens_used": record.tokens_used,
            "iterations": record.iterations, "files_changed": json.dumps(list(record.files_changed)),
            "worktree": int(record.worktree), "links": json.dumps(record.links),
        }

    def start(self, record: AgentRecord) -> None:
        self._write("agent_insert", self._row(record))

    def refuse(self, record: AgentRecord, reason: str, error: str) -> None:
        now = time.time()
        self._write("agent_insert", self._row(replace(
            record, status=REFUSED, refusal_reason=reason, error=error,
            started_at=record.started_at or now, ended_at=now)))

    def finish(self, agent_id: str, status: str, *, error: str = "", tokens_used: int = 0, iterations: int = 0,
               files_changed: tuple[str, ...] | list[str] = (), worktree: bool = False) -> None:
        if status not in TERMINAL:
            raise ValueError(f"not a terminal status: {status}")
        self._write("agent_update", agent_id, status=status, error=(error or "")[:ERROR_CHARS],
                    ended_at=time.time(), tokens_used=int(tokens_used or 0), iterations=int(iterations or 0),
                    files_changed=json.dumps(list(files_changed or ())), worktree=int(worktree))

    def list(self, *, root_session_id: str | None = None, parent_agent_id: str | None = None) -> list[AgentRecord]:
        try:
            rows = self._store.agent_rows(root_session_id=root_session_id, parent_agent_id=parent_agent_id)
        except Exception:
            logger.warning("agent records unreadable", exc_info=True)
            return []
        return [AgentRecord.from_row(r) for r in rows]

    def tree(self, root_session_id: str) -> builtins.list[dict[str, Any]]:
        """This session's children as nested summaries, oldest first; a child whose parent is not in the
        session (it should not happen) is shown at the top rather than dropped."""
        records = self.list(root_session_id=root_session_id)
        nodes = {r.agent_id: {**r.summary(), "children": []} for r in records}
        roots: builtins.list[dict[str, Any]] = []
        for r in records:
            parent = nodes.get(r.parent_agent_id)
            (parent["children"] if parent is not None else roots).append(nodes[r.agent_id])
        return roots
