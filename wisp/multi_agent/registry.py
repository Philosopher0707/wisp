"""Agent registry — tracks every spawned agent, its role, status, and metadata."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class AgentStatus(Enum):
    """Lifecycle states for a swarm agent."""

    SPAWNING = auto()
    IDLE = auto()
    WORKING = auto()
    COMPACTING = auto()
    STOPPED = auto()
    CRASHED = auto()


@dataclass
class AgentRecord:
    """Snapshot of a single agent in the swarm."""

    agent_id: str
    role: str
    status: AgentStatus = AgentStatus.SPAWNING
    current_task: Optional[str] = None
    files_locked: list[str] = field(default_factory=list)
    spawned_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_heartbeat: Optional[str] = None
    total_tasks_completed: int = 0
    total_tasks_failed: int = 0

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "status": self.status.name,
            "current_task": self.current_task,
            "files_locked": self.files_locked,
            "spawned_at": self.spawned_at,
            "last_heartbeat": self.last_heartbeat,
            "total_tasks_completed": self.total_tasks_completed,
            "total_tasks_failed": self.total_tasks_failed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AgentRecord:
        """Reconstruct an AgentRecord from a dictionary."""
        return cls(
            agent_id=data["agent_id"],
            role=data["role"],
            status=AgentStatus[data["status"]],
            current_task=data.get("current_task"),
            files_locked=data.get("files_locked", []),
            spawned_at=data.get("spawned_at", datetime.now(timezone.utc).isoformat()),
            last_heartbeat=data.get("last_heartbeat"),
            total_tasks_completed=data.get("total_tasks_completed", 0),
            total_tasks_failed=data.get("total_tasks_failed", 0),
        )


class AgentRegistry:
    """Thread-safe registry of all agents in a swarm.

    Used by the orchestrator to make scheduling decisions and by the
    CLI to show swarm status.

    If ``persist_path`` is provided, the registry auto-saves on changes
    and auto-loads on init for crash recovery.
    """

    def __init__(self, persist_path: Optional[Path] = None):
        self._agents: dict[str, AgentRecord] = {}
        self._lock = threading.RLock()
        self._persist_path = persist_path

        # Auto-load on init if persistence file exists
        if persist_path:
            self.load(persist_path)

    def _auto_save(self) -> None:
        """Save registry state if persistence is enabled."""
        if self._persist_path:
            try:
                self.save(self._persist_path)
            except Exception as e:
                logger.warning("Auto-save failed: %s", e)

    def register(self, record: AgentRecord) -> None:
        with self._lock:
            self._agents[record.agent_id] = record
        self._auto_save()

    def unregister(self, agent_id: str) -> None:
        with self._lock:
            self._agents.pop(agent_id, None)
        self._auto_save()

    def get(self, agent_id: str) -> Optional[AgentRecord]:
        with self._lock:
            return self._agents.get(agent_id)

    def update_status(self, agent_id: str, status: AgentStatus, task: Optional[str] = None) -> None:
        with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id].status = status
                if task is not None:
                    self._agents[agent_id].current_task = task
        self._auto_save()

    def heartbeat(self, agent_id: str) -> None:
        with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id].last_heartbeat = datetime.now(timezone.utc).isoformat()
        self._auto_save()

    def detect_stale_agents(self, max_stale_seconds: float = 60.0) -> list[str]:
        """Mark agents as CRASHED if their heartbeat is older than threshold.

        Returns list of agent IDs that were marked stale.
        """
        now = datetime.now(timezone.utc)
        stale_ids: list[str] = []
        with self._lock:
            for agent_id, record in self._agents.items():
                if record.status in (AgentStatus.STOPPED, AgentStatus.CRASHED):
                    continue
                if record.last_heartbeat is None:
                    # Never heartbeated — check spawned_at instead
                    try:
                        spawned = datetime.fromisoformat(record.spawned_at)
                        if (now - spawned).total_seconds() > max_stale_seconds:
                            stale_ids.append(agent_id)
                    except ValueError:
                        stale_ids.append(agent_id)
                else:
                    try:
                        last_hb = datetime.fromisoformat(record.last_heartbeat)
                        if (now - last_hb).total_seconds() > max_stale_seconds:
                            stale_ids.append(agent_id)
                    except ValueError:
                        stale_ids.append(agent_id)

            for agent_id in stale_ids:
                self._agents[agent_id].status = AgentStatus.CRASHED
                self._agents[agent_id].files_locked = []

        if stale_ids:
            logger.warning("Marked %d agents as CRASHED due to stale heartbeat: %s", len(stale_ids), stale_ids)
            self._auto_save()

        return stale_ids

    def claim_file(self, agent_id: str, path: str) -> bool:
        """Claim a file for editing. Returns False if another agent already holds it."""
        with self._lock:
            for other_id, rec in self._agents.items():
                if other_id != agent_id and path in rec.files_locked:
                    return False
            if agent_id in self._agents:
                # Already claimed by this agent — idempotent success
                if path in self._agents[agent_id].files_locked:
                    return True
                self._agents[agent_id].files_locked.append(path)
                return True
            return False

    def release_file(self, agent_id: str, path: str) -> None:
        with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id].files_locked = [
                    p for p in self._agents[agent_id].files_locked if p != path
                ]

    def release_all_files(self, agent_id: str) -> None:
        with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id].files_locked = []

    def list_agents(self) -> list[AgentRecord]:
        with self._lock:
            return list(self._agents.values())

    def list_by_role(self, role: str) -> list[AgentRecord]:
        with self._lock:
            return [r for r in self._agents.values() if r.role == role]

    def count_active(self) -> int:
        with self._lock:
            return sum(
                1 for r in self._agents.values()
                if r.status not in (AgentStatus.STOPPED, AgentStatus.CRASHED)
            )

    def any_working(self) -> bool:
        with self._lock:
            return any(r.status == AgentStatus.WORKING for r in self._agents.values())

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "agents": [a.to_dict() for a in self._agents.values()],
                "total": len(self._agents),
                "active": self.count_active(),
            }

    def save(self, path: str | Path) -> None:
        """Persist registry state to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            data = {
                "agents": [a.to_dict() for a in self._agents.values()],
                "version": 1,
            }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load(self, path: str | Path) -> None:
        """Restore registry state from a JSON file."""
        path = Path(path)
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        with self._lock:
            self._agents.clear()
            for agent_data in data.get("agents", []):
                record = AgentRecord.from_dict(agent_data)
                self._agents[record.agent_id] = record

    @classmethod
    def from_dict(cls, data: dict) -> AgentRegistry:
        """Reconstruct an AgentRegistry from a dictionary."""
        registry = cls()
        for agent_data in data.get("agents", []):
            record = AgentRecord.from_dict(agent_data)
            registry._agents[record.agent_id] = record
        return registry
