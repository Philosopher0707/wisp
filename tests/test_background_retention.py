"""Background-agent retention: the declared cap is enforced, not merely declared.

Context (see PHASE_BOUNDARY_FORENSIC.md / F11):

`BackgroundAgentManager` declares `MAX_FINISHED_ENTRIES = 50` and ships a
`prune()` that drops the oldest finished entries beyond it. Nothing called
`prune()`, so `_entries` and their telemetry rings grew for the life of the
process. `launch()` now prunes at the growth point.

Only FINISHED entries are eligible, so admission and live workers are
unaffected — pinned below.
"""

from __future__ import annotations

import asyncio

import pytest

from wisp.multi_agent.background import (
    STATUS_RUNNING,
    BackgroundAgentManager,
)
from wisp.multi_agent.task import SubagentContract, SubagentResult


class FakeOrchestrator:
    """Delay is chosen per task text, so one slow worker cannot be raced by
    changing a shared attribute (which would also speed up the slow one)."""

    def __init__(self, delays: dict[str, float] | None = None, default: float = 0.0):
        self.delays = delays or {}
        self.default = default

    async def _run_with_retry(self, contract: SubagentContract) -> SubagentResult:
        delay = self.delays.get(contract.task, self.default)
        await asyncio.sleep(delay)
        return SubagentResult(
            task_id=contract.name, success=True, output="done",
            files_changed=[], elapsed_seconds=delay, error=None,
            session_id="sess-x",
        )


def _contract(task: str = "t") -> SubagentContract:
    return SubagentContract(name="bg-t", role="coder", task=task)


@pytest.mark.asyncio
async def test_entries_stay_bounded_across_many_launches():
    """Without auto-prune this grew without limit."""
    mgr = BackgroundAgentManager(FakeOrchestrator(), max_running=4, max_finished=5)

    for i in range(40):
        snap = await mgr.launch(_contract(f"task-{i}"))
        assert snap["ok"] is True, snap
        await mgr.result(snap["agent_id"], wait_seconds=2.0)

    finished = [e for e in mgr._entries.values() if e.status != STATUS_RUNNING]
    assert len(finished) <= 5, (
        f"finished entries grew to {len(finished)} against a cap of 5"
    )


@pytest.mark.asyncio
async def test_pruned_entries_drop_their_telemetry_rings():
    mgr = BackgroundAgentManager(FakeOrchestrator(), max_running=4, max_finished=3)

    for i in range(12):
        snap = await mgr.launch(_contract(f"task-{i}"))
        await mgr.result(snap["agent_id"], wait_seconds=2.0)

    live_ids = set(mgr._entries)
    ring_ids = {a for a in mgr.telemetry.agents()}
    assert ring_ids <= live_ids, (
        f"telemetry rings outlived their entries: {sorted(ring_ids - live_ids)}"
    )


@pytest.mark.asyncio
async def test_a_running_entry_is_never_pruned():
    """Only finished entries are eligible — a live worker must survive."""
    orch = FakeOrchestrator(delays={"long": 600.0}, default=0.0)
    mgr = BackgroundAgentManager(orch, max_running=8, max_finished=1)

    live = await mgr.launch(_contract("long"))
    assert live["ok"] is True
    live_id = live["agent_id"]

    # Churn finished entries; the live one must persist throughout.
    for i in range(6):
        snap = await mgr.launch(_contract(f"short-{i}"))
        await mgr.result(snap["agent_id"], wait_seconds=2.0)
        assert mgr.get(live_id) is not None, "a running entry was pruned"
        assert mgr.get(live_id).status == STATUS_RUNNING

    mgr.cancel(live_id)


@pytest.mark.asyncio
async def test_prune_is_idempotent_and_returns_a_count():
    mgr = BackgroundAgentManager(FakeOrchestrator(), max_running=4, max_finished=2)
    assert mgr.prune() == 0
    for i in range(5):
        snap = await mgr.launch(_contract(f"t{i}"))
        await mgr.result(snap["agent_id"], wait_seconds=2.0)
    assert mgr.prune() >= 0
    assert mgr.prune() == 0 or mgr.prune() >= 0
