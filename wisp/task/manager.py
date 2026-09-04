"""Task manager (M6): user-facing lifecycle over RunStore.

task_id == run_id ("task-<hex>"). Plans ride a `task_plans` sidecar table
(the run row owns lifecycle; the plan owns intent). All mutating moves go
through legal RunState transitions — illegal moves raise, never coerce.
"""
from __future__ import annotations
import uuid
from typing import Any

from wisp.runs.record import RunRecord, RunState
from wisp.runs.store import RunStore


def _new_task_id() -> str:
    return f"task-{uuid.uuid4().hex[:8]}"


class TaskManager:
    def __init__(self, run_store: RunStore):
        self._runs = run_store

    def start(self, goal: str, workspace: str = ".",
              model: str = "", profile: str = "personal") -> str:
        tid = _new_task_id()
        self._runs.create(RunRecord(
            run_id=tid, prompt=goal, model=model or "unknown",
            workspace=workspace, status=RunState.QUEUED,
            idempotency_key=f"task:{tid}"))
        self._runs.transition(tid, RunState.QUEUED, RunState.RUNNING,
                              reason=f"task started (profile={profile})")
        return tid

    def list(self, *, include_terminal: bool = True) -> list[dict[str, Any]]:
        out = []
        for rec in self._runs.list():
            if not rec.run_id.startswith("task-"):
                continue
            if not include_terminal and rec.status in (
                    RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED):
                continue
            out.append({"task_id": rec.run_id, "goal": rec.prompt,
                        "status": rec.status.value,
                        "workspace": rec.workspace})
        return out

    def inspect(self, task_id: str) -> dict[str, Any]:
        rec = self._runs.get(task_id)
        if rec is None:
            raise KeyError(f"unknown task: {task_id}")
        transitions = self._runs.transitions(task_id)
        return {"task_id": rec.run_id, "goal": rec.prompt,
                "status": rec.status.value, "workspace": rec.workspace,
                "model": rec.model,
                "plan": self._runs.task_plan_get(task_id),
                "transitions": [
                    {"from": t.from_state, "to": t.to_state,
                     "reason": t.reason} for t in transitions]}

    def attach_plan(self, task_id: str, plan: dict[str, Any]) -> None:
        if self._runs.get(task_id) is None:
            raise KeyError(f"unknown task: {task_id}")
        self._runs.task_plan_put(task_id, plan)

    def pause(self, task_id: str) -> dict[str, Any]:
        rec = self._require(task_id)
        updated = self._runs.transition(
            task_id, rec.status, RunState.PAUSED, reason="task paused")
        return {"task_id": task_id, "status": updated.status.value}

    def resume(self, task_id: str) -> dict[str, Any]:
        rec = self._require(task_id)
        updated = self._runs.transition(
            task_id, rec.status, RunState.RUNNING, reason="task resumed")
        return {"task_id": task_id, "status": updated.status.value}

    def cancel(self, task_id: str) -> dict[str, Any]:
        rec = self._require(task_id)
        updated = self._runs.transition(
            task_id, rec.status, RunState.CANCELLED, reason="task cancelled")
        return {"task_id": task_id, "status": updated.status.value}

    def complete(self, task_id: str, output: str = "") -> dict[str, Any]:
        """Mark succeeded (execution layers report back here)."""
        rec = self._require(task_id)
        updated = self._runs.transition(
            task_id, rec.status, RunState.SUCCEEDED, reason="task completed")
        return {"task_id": task_id, "status": updated.status.value,
                "output": output}

    def _require(self, task_id: str) -> RunRecord:
        rec = self._runs.get(task_id)
        if rec is None:
            raise KeyError(f"unknown task: {task_id}")
        return rec
