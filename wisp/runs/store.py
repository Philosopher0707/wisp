"""RunStore: durable run registry (M3).

`RunStore` ABC with a SQLite implementation over `UnifiedStore`
(`background_runs` + `run_transitions` + `idempotency` tables). No
process-memory registry is the source of truth — managers are caches.
"""
from __future__ import annotations
import abc
import time
from typing import Any

from wisp.contracts.run import Transition
from wisp.runs.record import RunRecord, RunState, is_legal

# Legacy produced-vocabulary values (pre-M3 rows) mapped into the 8-state
# machine on read. Unknown values surface as ValueError — fail loud, not
# silent, so drift is visible.
_LEGACY_STATUS_IN = {
    "pending": RunState.QUEUED,
    "running": RunState.RUNNING,
    "completed": RunState.SUCCEEDED,
    "failed": RunState.FAILED,
    "cancelled": RunState.CANCELLED,
}


def _coerce_status(value: str) -> RunState:
    try:
        return RunState(value)
    except ValueError:
        pass
    if value in _LEGACY_STATUS_IN:
        return _LEGACY_STATUS_IN[value]
    raise ValueError(f"unknown run status: {value!r}")


class RunStore(abc.ABC):
    """Durable run registry. Postgres-compatible implementations may follow;
    the interface is deliberately narrow."""

    @abc.abstractmethod
    def create(self, record: RunRecord) -> None: ...
    @abc.abstractmethod
    def get(self, run_id: str) -> RunRecord | None: ...
    @abc.abstractmethod
    def list(self, *, status: RunState | None = None) -> list[RunRecord]: ...
    @abc.abstractmethod
    def transition(self, run_id: str, from_state: RunState,
                   to_state: RunState, reason: str = "") -> RunRecord: ...
    @abc.abstractmethod
    def transitions(self, run_id: str) -> list[Transition]: ...
    @abc.abstractmethod
    def claim_lease(self, run_id: str, owner: str, ttl_s: float) -> bool: ...
    @abc.abstractmethod
    def release_lease(self, run_id: str) -> None: ...
    @abc.abstractmethod
    def idempotent_get(self, key: str) -> str | None: ...
    @abc.abstractmethod
    def idempotent_put(self, key: str, result: str) -> None: ...
    @abc.abstractmethod
    def task_plan_put(self, run_id: str, plan: dict) -> None: ...
    @abc.abstractmethod
    def task_plan_get(self, run_id: str) -> dict | None: ...


class SQLiteRunStore(RunStore):
    """RunStore over UnifiedStore. Terminal states immutable; transitions
    append-only; leases time-bounded; idempotent writes first-win."""

    def __init__(self, store: Any):
        self._store = store

    def create(self, record: RunRecord) -> None:
        self._store.bg_create({
            "id": record.run_id, "prompt": record.prompt, "model": record.model,
            "workspace": record.workspace, "status": record.status.value,
            "created_at": record.created_at,
        })
        if record.idempotency_key or record.lease_owner:
            self._store.bg_update(
                record.run_id, idempotency_key=record.idempotency_key,
                lease_owner=record.lease_owner, lease_expires=record.lease_expires)

    def get(self, run_id: str) -> RunRecord | None:
        row = self._store.bg_get(run_id)
        if row is None:
            return None
        return RunRecord.from_dict({**row, "status": _coerce_status(row["status"]).value,
                                    "lease_owner": row.get("lease_owner", "") or "",
                                    "lease_expires": row.get("lease_expires", 0.0) or 0.0,
                                    "idempotency_key": row.get("idempotency_key", "") or ""})

    def list(self, *, status: RunState | None = None) -> list[RunRecord]:
        out = []
        for row in self._store.bg_list():
            try:
                rec = self.get(row["run_id"])
            except ValueError:
                continue  # unknown status — visible via direct query, not here
            if rec is not None and (status is None or rec.status == status):
                out.append(rec)
        return out

    def transition(self, run_id: str, from_state: RunState,
                   to_state: RunState, reason: str = "") -> RunRecord:
        current = self.get(run_id)
        if current is None:
            raise KeyError(f"unknown run: {run_id}")
        if current.status != from_state:
            raise ValueError(
                f"stale transition: {run_id} is {current.status.value}, "
                f"not {from_state.value}")
        if not is_legal(from_state, to_state):
            raise ValueError(
                f"illegal transition: {from_state.value} -> {to_state.value}")
        seq = len(self._store.bg_list_transitions(run_id))
        now = time.time()
        self._store.bg_update(run_id, status=to_state.value,
                              **({"finished_at": now} if to_state in
                                  (RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED) else {}))
        self._store.bg_append_transition(run_id, seq, from_state.value,
                                         to_state.value, reason, now)
        updated = self.get(run_id)
        assert updated is not None
        return updated

    def transitions(self, run_id: str) -> list[Transition]:
        return [Transition(run_id=t["run_id"], seq=t["seq"],
                           from_state=t["from_state"], to_state=t["to_state"],
                           reason=t["reason"], timestamp=t["timestamp"])
                for t in self._store.bg_list_transitions(run_id)]

    def claim_lease(self, run_id: str, owner: str, ttl_s: float) -> bool:
        return self._store.bg_claim_lease(run_id, owner, time.time() + ttl_s)

    def release_lease(self, run_id: str) -> None:
        self._store.bg_update(run_id, lease_owner="", lease_expires=0.0)

    def idempotent_get(self, key: str) -> str | None:
        return self._store.idem_get(key)

    def idempotent_put(self, key: str, result: str) -> None:
        self._store.idem_put(key, result)

    def task_plan_put(self, run_id: str, plan: dict) -> None:
        self._store.task_plan_put(run_id, plan)

    def task_plan_get(self, run_id: str) -> dict | None:
        return self._store.task_plan_get(run_id)
