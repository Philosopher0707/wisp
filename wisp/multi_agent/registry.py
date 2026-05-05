"""Agent registry — tracks every spawned agent, its role, status, and metadata."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Optional


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


class AgentRegistry:
    """Thread-safe registry of all agents in a swarm.

    Used by the orchestrator to make scheduling decisions and by the
    CLI to show swarm status.
    """

    def __init__(self):
        self._agents: dict[str, AgentRecord] = {}
        self._lock = threading.RLock()

    def register(self, record: AgentRecord) -> None:
        with self._lock:
            self._agents[record.agent_id] = record

    def unregister(self, agent_id: str) -> None:
        with self._lock:
            self._agents.pop(agent_id, None)

    def get(self, agent_id: str) -> Optional[AgentRecord]:
        with self._lock:
            return self._agents.get(agent_id)

    def update_status(self, agent_id: str, status: AgentStatus, task: Optional[str] = None) -> None:
        with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id].status = status
                if task is not None:
                    self._agents[agent_id].current_task = task

    def heartbeat(self, agent_id: str) -> None:
        with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id].last_heartbeat = datetime.now(timezone.utc).isoformat()

    def claim_file(self, agent_id: str, path: str) -> bool:
        """Claim a file for editing. Returns False if another agent already holds it."""
        with self._lock:
            for other_id, rec in self._agents.items():
                if other_id != agent_id and path in rec.files_locked:
                    return False
            if agent_id in self._agents:
                if path not in self._agents[agent_id].files_locked:
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
