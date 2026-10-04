"""Background subagents: a continuation must run, and a child's verdict must be the turn's.

Two defects, both seen live in one session:

    [WARNING] wisp.multi_agent._runner: Subagent spawn-background timed out after 120.2s
    ✓ subagent_result 3m 39s   {"ok": true, "agent_id": "bg-985ed870", …}
      — the final message was a truncated mid-sentence status, not findings
    ✗ subagent_send  duplicate run id bg-985ed870

**1. `subagent_send` could never continue an agent in production.** `send()` re-admitted the
*same* agent id through `Scheduler.admit`, which refuses any id already in the durable store.
The store has been wired at the composition root since the durable layer was made reachable, so
every continuation failed with *"duplicate run id"*. The `send()` tests built the manager
without a store and never met the refusal. A durable row cannot leave a terminal state
(`LEGAL_TRANSITIONS`), so a continuation is **a new run of the same agent**: it gets its own run
id (`<agent>-t<turn>`) and clears the same admission bar, bound included.

**2. The runner's `success` was "no error event", not the turn's outcome.** `_run_via_runtime`
and `_run_agent` returned `success: True` whenever the stream ended without an `error` event:
on budget exhaustion, and on a stream that ended **without `done`**. After a tool call resets
the output, that path reported the pre-action narration ("Let me check the …") as the finished
report. ADR-0044's `terminal_outcome_from_evidence` is the one authority for "did the turn
succeed" (`done` and no fatal error); the runner now consumes it. It also stops abandoning the
child's turn at the first *recoverable* error event, which the authority does not count as
failure.

**Observation points are production** (F96):
* the real `BackgroundAgentManager` over a real `SQLiteRunStore`, the shape
  `composition.py` builds;
* the real `SubagentRunner.run()` → `_run_via_runtime`, with only the agent runtime's event
  stream scripted.

**Floors** (F81): every continuation test asserts the first turn completed before asserting on
the second. Every verdict test asserts the scripted stream was consumed.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from wisp.config import WispConfig
from wisp.infra.store import UnifiedStore
from wisp.multi_agent._runner import SubagentRunner
from wisp.multi_agent.background import (
    STATUS_COMPLETED,
    STATUS_RUNNING,
    BackgroundAgentManager,
)
from wisp.multi_agent.task import SubagentContract, SubagentResult
from wisp.runs.record import RunState
from wisp.runs.store import SQLiteRunStore


class _Orchestrator:
    def __init__(self, delays: list[float] | None = None):
        self.delays = list(delays or [])
        self.contracts: list[SubagentContract] = []

    async def _run_with_retry(self, contract: SubagentContract) -> SubagentResult:
        self.contracts.append(contract)
        await asyncio.sleep(self.delays.pop(0) if self.delays else 0.0)
        return SubagentResult(task_id=contract.name, success=True, output="findings",
                              session_id="sess-1", elapsed_seconds=0.0)


def _contract(task: str = "review auth.py") -> SubagentContract:
    return SubagentContract(name="reviewer", role="reviewer", task=task)


def _manager(tmp_path: Path, orch: _Orchestrator, max_running: int = 8):
    store = SQLiteRunStore(UnifiedStore(tmp_path / "runs.db"))
    return BackgroundAgentManager(orch, max_running=max_running, run_store=store), store


class TestAContinuationRuns:
    @pytest.mark.asyncio
    async def test_send_continues_a_finished_agent(self, tmp_path) -> None:
        orch = _Orchestrator()
        mgr, _store = _manager(tmp_path, orch)
        agent = (await mgr.launch(_contract()))["agent_id"]
        first = await mgr.result(agent, wait_seconds=2.0)
        assert first["status"] == STATUS_COMPLETED, "floor: the first turn must finish"

        sent = await mgr.send(agent, "now give the verdict")
        assert sent["ok"] is True, sent
        second = await mgr.result(agent, wait_seconds=2.0)
        assert second["status"] == STATUS_COMPLETED and second["turns"] == 2
        assert orch.contracts[1].task == "now give the verdict"
        assert getattr(orch.contracts[1], "_resume_session_id", "") == "sess-1"

    @pytest.mark.asyncio
    async def test_each_turn_is_its_own_durable_run(self, tmp_path) -> None:
        mgr, store = _manager(tmp_path, _Orchestrator())
        agent = (await mgr.launch(_contract()))["agent_id"]
        await mgr.result(agent, wait_seconds=2.0)
        assert (await mgr.send(agent, "continue"))["ok"] is True
        await mgr.result(agent, wait_seconds=2.0)

        rows = {r.run_id: r.status for r in store.list()}
        assert rows == {agent: RunState.SUCCEEDED, f"{agent}-t2": RunState.SUCCEEDED}
        assert mgr.persist_skipped_total == 0, "no transition may be swallowed as illegal"

    @pytest.mark.asyncio
    async def test_a_third_turn_gets_a_third_run(self, tmp_path) -> None:
        mgr, store = _manager(tmp_path, _Orchestrator())
        agent = (await mgr.launch(_contract()))["agent_id"]
        await mgr.result(agent, wait_seconds=2.0)
        for message in ("second", "third"):
            assert (await mgr.send(agent, message))["ok"] is True
            await mgr.result(agent, wait_seconds=2.0)
        assert sorted(r.run_id for r in store.list()) == sorted(
            [agent, f"{agent}-t2", f"{agent}-t3"])


class TestTheBoundHoldsForContinuations:
    @pytest.mark.asyncio
    async def test_a_continuation_is_refused_at_the_bound_for_the_bound(self, tmp_path) -> None:
        orch = _Orchestrator(delays=[0.0, 1.0])
        mgr, _store = _manager(tmp_path, orch, max_running=1)
        a = (await mgr.launch(_contract("a")))["agent_id"]
        assert (await mgr.result(a, wait_seconds=2.0))["status"] == STATUS_COMPLETED
        b = (await mgr.launch(_contract("b")))["agent_id"]
        assert mgr.get(b).status == STATUS_RUNNING, "floor: b must hold the one slot"

        refused = await mgr.send(a, "continue")
        assert refused["ok"] is False
        assert "limit reached" in refused["error"], refused["error"]

        await mgr.result(b, wait_seconds=3.0)
        assert (await mgr.send(a, "continue"))["ok"] is True

    @pytest.mark.asyncio
    async def test_a_running_continuation_holds_a_slot(self, tmp_path) -> None:
        orch = _Orchestrator(delays=[0.0, 1.0])
        mgr, _store = _manager(tmp_path, orch, max_running=1)
        a = (await mgr.launch(_contract("a")))["agent_id"]
        await mgr.result(a, wait_seconds=2.0)
        assert (await mgr.send(a, "continue"))["ok"] is True
        refused = await mgr.launch(_contract("c"))
        assert refused["ok"] is False and "limit reached" in refused["error"]
        await mgr.result(a, wait_seconds=3.0)


# ── The child's verdict ──────────────────────────────────────────────────


class _ScriptedRuntime:
    """An AgentRuntime whose run_turn yields a fixed event script."""

    def __init__(self, events: list[dict]):
        self.events = events
        self.consumed = 0

    async def get_or_create_session(self, sid, model, ws):
        return {"id": sid, "model": model, "workspace": ws, "messages": []}

    async def run_turn(self, session, task):
        for ev in self.events:
            self.consumed += 1
            yield ev


def _run(tmp_path, events, **contract_fields) -> tuple[SubagentResult, _ScriptedRuntime]:
    runtime = _ScriptedRuntime(events)
    cfg = WispConfig().replace(model="test-model", provider="ollama", workspace=str(tmp_path))
    runner = SubagentRunner(cfg, Path(tmp_path), store=UnifiedStore(tmp_path / "s.db"),
                            agent_runtime=runtime)
    contract = SubagentContract(name="reviewer", role="reviewer", task="review",
                                timeout_seconds=30, **contract_fields)
    result = asyncio.run(runner.run(contract, str(tmp_path), system_prompt="sys"))
    return result, runtime


_TOOL = [{"type": "tool_call", "name": "read_file", "arguments": {"path": "a.py"}},
         {"type": "tool_result", "name": "read_file", "result": "x" * 400}]


class TestTheVerdictIsTheTurns:
    def test_a_turn_that_ends_with_done_succeeds(self, tmp_path) -> None:
        result, rt = _run(tmp_path, [{"type": "content", "text": "Let me look."}, *_TOOL,
                                     {"type": "content", "text": "Verdict: two bugs."},
                                     {"type": "done"}])
        assert rt.consumed == 5
        assert result.success is True and result.output == "Verdict: two bugs."

    def test_a_stream_that_ends_without_done_is_not_a_success(self, tmp_path) -> None:
        result, rt = _run(tmp_path, [{"type": "content", "text": "Let me check the "}, *_TOOL,
                                     {"type": "error", "message": "Turn aborted: boom",
                                      "recoverable": True}])
        assert rt.consumed == 4
        assert result.success is False
        assert "Turn aborted: boom" in (result.error or "")
        assert not result.output.startswith("Let me check"), (
            "pre-action narration must not be reported as the child's findings")

    def test_a_stream_that_simply_stops_is_not_a_success(self, tmp_path) -> None:
        """No `done`, no error: the turn never completed, whatever text preceded the stop."""
        result, rt = _run(tmp_path, [{"type": "content", "text": "Let me check the "}, *_TOOL])
        assert rt.consumed == 3
        assert result.success is False
        assert not result.output.startswith("Let me check")

    def test_budget_exhaustion_is_not_a_success(self, tmp_path) -> None:
        result, _rt = _run(tmp_path, [{"type": "content", "text": "Let me check the "}, *_TOOL,
                                      {"type": "content", "text": "more"}, {"type": "done"}],
                           max_tokens=10)
        assert result.success is False
        assert "budget exhausted" in (result.error or "").lower()
        assert not result.output.startswith("Let me check")

    def test_a_recoverable_error_does_not_abandon_the_turn(self, tmp_path) -> None:
        result, rt = _run(tmp_path, [{"type": "error", "message": "transient", "recoverable": True},
                                     {"type": "content", "text": "Verdict: fine."},
                                     {"type": "done"}])
        assert rt.consumed == 3, "the runner must keep consuming after a recoverable error"
        assert result.success is True and result.output == "Verdict: fine."

    def test_a_fatal_error_fails_even_with_done(self, tmp_path) -> None:
        result, _rt = _run(tmp_path, [{"type": "content", "text": "partial"},
                                      {"type": "error", "message": "Max iterations reached",
                                       "recoverable": False},
                                      {"type": "done"}])
        assert result.success is False
        assert "Max iterations reached" in (result.error or "")
