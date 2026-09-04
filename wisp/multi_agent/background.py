"""Background subagents — durable agents that outlive the launching turn.

Sync ``spawn``/``fanout`` block the parent turn until every child finishes.
This module adds the async counterpart: launch a subagent, get an id back
immediately, keep working, and collect results later — plus conversation
continuation (send a follow-up message to a finished agent's stored session).

The manager is intentionally dumb about *how* subagents run; execution goes
through the orchestrator's single-contract path so depth guards, budgets,
caching, and telemetry all apply unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field, replace as dc_replace
from datetime import datetime, timezone
from typing import Any, List

logger = logging.getLogger(__name__)

# Bounded registries: runaway launches must hit a ceiling, and finished
# entries are pruned so a long-lived process cannot leak them forever.
MAX_RUNNING_AGENTS = 8
MAX_FINISHED_ENTRIES = 50
DEFAULT_WAIT_SECONDS = 0.0
MAX_WAIT_SECONDS = 300.0

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
_TERMINAL = {STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED}
# Lease time-to-live for durable run rows (renewed by scheduler heartbeat).
_LEASE_TTL_S = 300.0


@dataclass
class BackgroundAgentEntry:
    """One background agent: identity, lifecycle state, and its latest run."""

    id: str
    label: str
    contract: Any
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    status: str = STATUS_RUNNING
    result: Any = None
    error: str | None = None
    turns: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    handle: asyncio.Task[None] | None = None
    last_session_id: str = ""
    files_changed: list[str] = field(default_factory=list)
    notified: bool = False
    """Cleared when a terminal state is reached; set when the parent's next
    turn surfaces it via drain_notifications()."""

    def elapsed(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return max(0.0, end - self.started_at)


class BackgroundAgentManager:
    """Registry of background subagents launched via the ``spawn_background`` tool.

    One manager instance per composition root. Entries persist for the
    lifetime of the process; each agent id identifies a continuing
    conversation thread (see :meth:`send`).
    """

    # Manager status vocabulary mapped into the M3 RunState machine.
    _STATUS_TO_RUN_STATE = {
        STATUS_RUNNING: "running",
        STATUS_COMPLETED: "succeeded",
        STATUS_FAILED: "failed",
        STATUS_CANCELLED: "cancelled",
    }

    def __init__(
        self,
        orchestrator: Any,
        max_running: int = MAX_RUNNING_AGENTS,
        max_finished: int = MAX_FINISHED_ENTRIES,
        run_store: Any = None,
    ):
        self._orchestrator = orchestrator
        self._max_running = max_running
        self._max_finished = max_finished
        # Durable registry (M3 J1): source of truth for run status. None
        # preserves the legacy pure-in-memory behavior (tests, embeddings).
        self._run_store = run_store
        self._owner_id = f"mgr-{uuid.uuid4().hex[:8]}"
        self._scheduler = None
        if run_store is not None:
            from wisp.runs.scheduler import Scheduler
            self._scheduler = Scheduler(
                run_store, max_running=max_running,
                lease_ttl_s=_LEASE_TTL_S, owner=self._owner_id)
        self._entries: dict[str, BackgroundAgentEntry] = {}
        self._counter = 0
        # Settlement fan-out: each subscriber gets one event per agent
        # reaching a terminal state (used by WebSocket push).
        self._subscribers: set["asyncio.Queue[dict[str, Any]]"] = set()

    def subscribe(self) -> "asyncio.Queue[dict[str, Any]]":
        """Register a settlement-event queue (WebSocket push, dashboards)."""
        queue: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[dict[str, Any]]") -> None:
        self._subscribers.discard(queue)

    def _publish(self, event: dict[str, Any]) -> None:
        """Fan one lifecycle event out to all subscribers."""
        if not self._subscribers:
            return
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except Exception:
                logger.debug("Event subscriber dropped its queue", exc_info=True)
                self._subscribers.discard(queue)

    def _publish_settlement(self, entry: BackgroundAgentEntry) -> None:
        """Fan one terminal transition out to all subscribers."""
        if not self._subscribers:
            return
        result = entry.result
        summary = ""
        ok = False
        if result is not None:
            ok = bool(getattr(result, "success", False))
            summary = (getattr(result, "output", "") or "").strip()[:200]
        event = {
            "type": "agent_settled",
            "agent_id": entry.id,
            "label": entry.label,
            "status": entry.status,
            "ok": ok,
            "turns": entry.turns,
            "elapsed_seconds": round(entry.elapsed(), 1),
            "task": (getattr(entry.contract, "task", "") or "")[:120],
        }
        if entry.error:
            event["error"] = str(entry.error)[:200]
        elif summary:
            event["summary"] = summary
        self._publish(event)

    # ── Durable registry (M3 J1) ────────────────────────────────────

    def _persist_create(self, entry: BackgroundAgentEntry, contract: Any) -> None:
        """Insert the run row (queued) and move it to running."""
        if self._run_store is None:
            return
        try:
            from wisp.runs.record import RunRecord, RunState
            ws = getattr(contract, "workspace", ".") or "."
            self._run_store.create(RunRecord(
                run_id=entry.id,
                prompt=getattr(contract, "task", "") or "",
                workspace=str(ws),
                status=RunState.QUEUED,
            ))
            self._run_store.transition(
                entry.id, RunState.QUEUED, RunState.RUNNING, reason="launched")
            self._run_store.claim_lease(entry.id, self._owner_id, _LEASE_TTL_S)
        except Exception:
            logger.warning("run persistence failed on create %s",
                           entry.id, exc_info=True)

    def _persist_status(self, entry: BackgroundAgentEntry) -> None:
        """Best-effort status sync. Persistence must never break execution:
        stale/illegal transitions are logged, not raised. Continuation
        relaunches (send()) that revisit a terminal state are skipped."""
        if self._run_store is None:
            return
        try:
            from wisp.runs.record import RunRecord, RunState
            target = RunState(self._STATUS_TO_RUN_STATE[entry.status])
            rec = self._run_store.get(entry.id)
            if rec is None:
                self._run_store.create(RunRecord(
                    run_id=entry.id,
                    prompt=getattr(entry.contract, "task", "") or "",
                    status=RunState.QUEUED))
                rec = self._run_store.get(entry.id)
                assert rec is not None
            if rec.status == target:
                return
            self._run_store.transition(
                entry.id, rec.status, target,
                reason=f"settled:{entry.status}")
            if target in (RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED):
                self._run_store.release_lease(entry.id)
        except Exception:
            logger.warning("run persistence failed on status %s (%s)",
                           entry.id, entry.status, exc_info=True)

    def recover(self, lease_owner: str = "") -> dict[str, int]:
        """Park rows abandoned by dead processes. Stale RUNNING → PAUSED
        (explicit resume only); other non-terminal states → CANCELLED.
        Live leases owned by others are left alone. Never resumes effects."""
        report = {"paused": 0, "cancelled": 0, "left": 0}
        if self._run_store is None:
            return report
        from wisp.runs.record import TERMINAL_STATES, RunState
        now = time.time()
        for rec in self._run_store.list():
            if rec.status in TERMINAL_STATES:
                continue
            live = bool(rec.lease_owner) and rec.lease_expires >= now \
                and rec.lease_owner != lease_owner
            if live:
                report["left"] += 1
                continue
            target = RunState.PAUSED if rec.status == RunState.RUNNING \
                else RunState.CANCELLED
            try:
                self._run_store.transition(
                    rec.run_id, rec.status, target,
                    reason="abandoned at restart")
                report["paused" if target == RunState.PAUSED else "cancelled"] += 1
            except Exception:
                logger.warning("recover failed for %s", rec.run_id, exc_info=True)
                report["left"] += 1
        return report

    # ── Launch ────────────────────────────────────────────────────────

    async def launch(self, contract: Any, label: str = "") -> dict[str, Any]:
        """Start a contract in the background. Returns a launch snapshot."""
        self._counter += 1
        agent_id = f"bg-{uuid.uuid4().hex[:8]}"
        if self._scheduler is not None:
            # Durable admission (M3 J2): store counts replace the
            # in-memory head-count so limits survive restarts.
            admitted = self._scheduler.admit(agent_id)
            if not admitted.allowed:
                return {"ok": False, "error": admitted.reason}
        else:
            running = [e for e in self._entries.values() if e.status == STATUS_RUNNING]
            if len(running) >= self._max_running:
                return {
                    "ok": False,
                    "error": (
                        f"Background agent limit reached ({self._max_running} running). "
                        "Collect or cancel existing agents first."
                    ),
                }
        entry = BackgroundAgentEntry(
            id=agent_id,
            label=label or f"{getattr(contract, 'role', 'generalist')}-{self._counter}",
            contract=contract,
        )
        self._entries[agent_id] = entry
        self._persist_create(entry, contract)
        entry.handle = asyncio.create_task(self._run_entry(entry))
        self._publish({
            "type": "agent_started",
            "agent_id": agent_id,
            "label": entry.label,
            "role": getattr(contract, "role", "generalist"),
            "task": (getattr(contract, "task", "") or "")[:120],
        })
        return {"ok": True, "agent_id": agent_id, "label": entry.label, "status": entry.status}

    async def _run_entry(self, entry: BackgroundAgentEntry) -> None:
        entry.turns += 1
        message = entry.contract.task
        try:
            result = await self._orchestrator._run_with_retry(entry.contract)
            entry.result = result
            entry.error = getattr(result, "error", None)
            entry.status = STATUS_COMPLETED if result.success else STATUS_FAILED
            if entry.status == STATUS_FAILED and not entry.error:
                entry.error = "subagent reported failure"
        except asyncio.CancelledError:
            entry.status = STATUS_CANCELLED
            entry.error = entry.error or "cancelled by caller"
            entry.finished_at = time.monotonic()
            entry.history.append({
                "task": message[:200],
                "status": entry.status,
                "summary": "",
            })
            entry.done.set()
            self._publish_settlement(entry)
            raise
        except Exception as e:
            logger.error("Background agent %s crashed: %s", entry.id, e, exc_info=True)
            entry.result = None
            entry.error = str(e)
            entry.status = STATUS_FAILED
        finally:
            if entry.finished_at is None:
                entry.finished_at = time.monotonic()
            self._persist_status(entry)

        summary = ""
        files: list[str] = []
        session_id = ""
        if entry.result is not None:
            summary = (getattr(entry.result, "output", "") or "")[:2000]
            files = list(getattr(entry.result, "files_changed", []) or [])
            session_id = getattr(entry.result, "session_id", "") or ""
        entry.history.append({
            "task": message[:200],
            "status": entry.status,
            "summary": summary[:200],
        })
        # Remember where this conversation lives so send() can resume it.
        if session_id:
            entry.last_session_id = session_id
        entry.files_changed = files
        entry.done.set()
        self._publish_settlement(entry)

    # ── Inspection ────────────────────────────────────────────────────

    def get(self, agent_id: str) -> BackgroundAgentEntry | None:
        return self._entries.get(agent_id)

    def list(self, include_finished: bool = True) -> list[dict[str, Any]]:
        entries = sorted(self._entries.values(), key=lambda e: e.started_at)
        return [
            self.snapshot(e)
            for e in entries
            if include_finished or e.status not in _TERMINAL
        ]

    def snapshot(self, entry: BackgroundAgentEntry) -> dict[str, Any]:
        snap: dict[str, Any] = {
            "agent_id": entry.id,
            "label": entry.label,
            "role": getattr(entry.contract, "role", "generalist"),
            "task": (getattr(entry.contract, "task", "") or "")[:200],
            "status": entry.status,
            "turns": entry.turns,
            "elapsed_seconds": round(entry.elapsed(), 1),
        }
        if entry.status in _TERMINAL:
            snap["result"] = self._result_payload(entry)
        return snap

    def _result_payload(self, entry: BackgroundAgentEntry) -> dict[str, Any] | None:
        if entry.result is None:
            return {
                "ok": False,
                "summary": "",
                "files": [],
                "error": entry.error,
                "session_id": "",
            }
        r = entry.result
        return {
            "ok": bool(getattr(r, "success", False)),
            "summary": (getattr(r, "output", "") or "")[:2000],
            "files": list(getattr(r, "files_changed", []) or []),
            "error": getattr(r, "error", None),
            "session_id": getattr(r, "session_id", "") or "",
            "elapsed_seconds": round(getattr(r, "elapsed_seconds", 0.0), 1),
        }

    async def result(self, agent_id: str, wait_seconds: float = DEFAULT_WAIT_SECONDS) -> dict[str, Any]:
        """Snapshot an agent, optionally blocking until it finishes."""
        entry = self._entries.get(agent_id)
        if entry is None:
            return {"ok": False, "error": f"Unknown agent_id '{agent_id}'"}
        wait_seconds = min(max(0.0, float(wait_seconds)), MAX_WAIT_SECONDS)
        if wait_seconds > 0 and entry.status not in _TERMINAL:
            try:
                await asyncio.wait_for(entry.done.wait(), timeout=wait_seconds)
            except asyncio.TimeoutError:
                pass
        return {"ok": True, **self.snapshot(entry)}

    # ── Continuation ──────────────────────────────────────────────────

    async def send(self, agent_id: str, message: str) -> dict[str, Any]:
        """Continue a finished agent's conversation with a follow-up message.

        Reuses the agent id: the entry resets to running with a contract
        whose ``_resume_session_id`` points at the stored session, so the
        subagent sees its full prior history plus the new instruction.
        """
        entry = self._entries.get(agent_id)
        if entry is None:
            return {"ok": False, "error": f"Unknown agent_id '{agent_id}'"}
        if entry.status == STATUS_RUNNING:
            return {
                "ok": False,
                "error": f"Agent {agent_id} is still running — collect it with subagent_result first.",
            }
        if not message or not message.strip():
            return {"ok": False, "error": "send requires a non-empty message"}

        resume_id = getattr(entry, "last_session_id", "") or ""
        if not resume_id:
            return {
                "ok": False,
                "error": "No storable session from the previous run — cannot continue this agent.",
            }

        original = entry.contract
        self._counter += 1
        contract = dc_replace(
            original,
            task=message,
            name=f"{original.name}-t{entry.turns + 1}",
        )
        # depth/branch/cache fields are dataclass fields, so replace() carries
        # them; only the resume pointer needs explicit stamping.
        setattr(contract, "_resume_session_id", resume_id)

        entry.label = entry.label or f"{getattr(original, 'role', 'generalist')}-{self._counter}"
        entry.contract = contract
        entry.status = STATUS_RUNNING
        entry.result = None
        entry.error = None
        entry.started_at = time.monotonic()
        entry.finished_at = None
        entry.notified = False
        entry.done.clear()
        entry.handle = asyncio.create_task(self._run_entry(entry))
        self._publish({
            "type": "agent_progress",
            "agent_id": agent_id,
            "label": entry.label,
            "turn": entry.turns + 1,
            "note": "continuation started",
            "task": message[:120],
        })
        return {"ok": True, "agent_id": agent_id, "status": entry.status}

    # ── Cancellation & pruning ────────────────────────────────────────

    def cancel(self, agent_id: str) -> dict[str, Any]:
        entry = self._entries.get(agent_id)
        if entry is None:
            return {"ok": False, "error": f"Unknown agent_id '{agent_id}'"}
        if entry.status != STATUS_RUNNING:
            return {"ok": False, "error": f"Agent {agent_id} already {entry.status}"}
        handle = getattr(entry, "handle", None)
        if handle is not None:
            handle.cancel()
        entry.status = STATUS_CANCELLED
        entry.error = "cancelled by caller"
        self._persist_status(entry)
        return {"ok": True, "agent_id": agent_id, "status": STATUS_CANCELLED}

    def shutdown_pending(self) -> int:
        """Cancel every live handle; returns how many were running.

        Sync on purpose: CompositionRoot.shutdown() is sync and may run from
        a lifespan or an interpreter-exit path. Reaping happens wherever the
        owning loop is being drained — this only *requests* cancellation so
        no background agent can outlive the process that spawned it.
        """
        live = 0
        for entry in self._entries.values():
            if entry.status != STATUS_RUNNING:
                continue
            handle = getattr(entry, "handle", None)
            if handle is not None and not handle.done():
                handle.cancel()
                live += 1
            entry.status = STATUS_CANCELLED
            entry.error = entry.error or "shutdown"
            self._persist_status(entry)
        return live

    def prune(self) -> int:
        """Drop oldest finished entries beyond the retention cap."""
        finished = sorted(
            (e for e in self._entries.values() if e.status in _TERMINAL),
            key=lambda e: e.finished_at or 0.0,
        )
        excess = len(finished) - self._max_finished
        for entry in finished[:max(0, excess)]:
            del self._entries[entry.id]
        return max(0, excess)

    # ── Parent-facing context ─────────────────────────────────────────

    def counts(self) -> dict[str, int]:
        """Entry counts by status — for the operating-context block."""
        out = {"running": 0, "completed": 0, "failed": 0, "cancelled": 0}
        for entry in self._entries.values():
            out[entry.status] = out.get(entry.status, 0) + 1
        return out

    def drain_notifications(self) -> List[str]:
        """One line per agent that reached a terminal state since last drain.

        Called once per parent turn so the model learns about settled
        background work without polling. Draining marks entries notified;
        a send() relaunch clears the flag again.
        """
        lines: list[str] = []
        for entry in sorted(self._entries.values(), key=lambda e: e.started_at):
            if entry.status not in _TERMINAL or entry.notified:
                continue
            entry.notified = True
            mark = {
                STATUS_COMPLETED: "completed",
                STATUS_FAILED: "FAILED",
                STATUS_CANCELLED: "cancelled",
            }.get(entry.status, entry.status)
            task = (getattr(entry.contract, "task", "") or "")[:80]
            line = f"- {entry.id} ({entry.label}) {mark}"
            if task:
                line += f" — task: {task}"
            result = entry.result
            if result is not None:
                summary = (getattr(result, "output", "") or "").strip()
                err = getattr(result, "error", None)
                files = list(getattr(result, "files_changed", []) or [])
                if summary:
                    line += f"; result: {summary[:120]}"
                elif err:
                    line += f"; error: {str(err)[:120]}"
                if files:
                    line += f"; changed {len(files)} file(s)"
            line += f" → collect with subagent_result('{entry.id}')"
            lines.append(line)
        return lines

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
