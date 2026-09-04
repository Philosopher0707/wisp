"""Durable local scheduler (M3): bounded admission, leases, idempotency.

Pure logic over a RunStore — no threads, no loop. The manager calls
admit() at launch (durable counts replace the in-memory head-count);
workers call heartbeat() to renew owned leases; resume paths consult the
idempotency guard so effects never replay.
"""
from __future__ import annotations
from dataclasses import dataclass

from wisp.runs.record import TERMINAL_STATES


@dataclass(frozen=True)
class Admission:
    allowed: bool
    reason: str = ""


class Scheduler:
    def __init__(self, store, max_running: int = 8,
                 lease_ttl_s: float = 300.0, owner: str = ""):
        self._store = store
        self._max_running = max_running
        self._lease_ttl_s = lease_ttl_s
        self._owner = owner

    def active_runs(self) -> list:
        return [r for r in self._store.list()
                if r.status not in TERMINAL_STATES]

    def admit(self, run_id: str) -> Admission:
        """Admit a new run id: reject duplicates and over-bound launches."""
        if self._store.get(run_id) is not None:
            return Admission(False, f"duplicate run id {run_id}")
        active = self.active_runs()
        if len(active) >= self._max_running:
            return Admission(
                False,
                f"Background agent limit reached ({self._max_running} running). "
                "Collect or cancel existing agents first.")
        return Admission(True)

    def heartbeat(self) -> list[str]:
        """Renew leases on owned active rows; return renewed run ids."""
        renewed = []
        for rec in self.active_runs():
            if rec.lease_owner != self._owner:
                continue
            if self._store.claim_lease(rec.run_id, self._owner, self._lease_ttl_s):
                renewed.append(rec.run_id)
        return renewed

    def release(self, run_id: str) -> None:
        self._store.release_lease(run_id)

    def already_done(self, key: str) -> bool:
        return self._store.idempotent_get(key) is not None

    def memoize(self, key: str, result: str) -> None:
        self._store.idempotent_put(key, result)

    def recall(self, key: str) -> str | None:
        return self._store.idempotent_get(key)
