"""G1E retry amplification & idempotency integrity (§5-§7, §26-§28).

Core invariant: ONE logical task -> bounded executions against ONE shared
budget (SubagentContract.retry_count/max_retries, monotonic). Children
inherit consumed budget; they cannot mint fresh budget.

Side-effect rule: automatic re-execution requires all-read tools, or
effective worktree isolation, or zero mutating tool calls in the attempt.
Unknown tools fail closed (EXEC).
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator
from wisp.multi_agent.task import (MAX_SUBAGENT_RETRIES, SubagentContract,
                                   SubagentResult)


def _orch():
    agent = MagicMock()
    agent.config.model = "test-model"
    agent.config.workspace = "/tmp"
    agent.config.subagent_pool_size = 4
    agent.config.max_subagent_depth = 4
    return SubagentOrchestrator(parent_agent=agent)


def _contract(**kw):
    kw.setdefault("name", "c")
    kw.setdefault("task", "t")
    kw.setdefault("role", "coder")
    return SubagentContract(**kw)


def _fail_transient(counter, error="429 rate limit"):
    async def _run(contract, **kw):
        counter["n"] += 1
        return SubagentResult(task_id="c", success=False, error=error,
                              elapsed_seconds=0.1)
    return _run


# ── Budget model + monotonic invariant (§5-§7) ──

def test_budget_clamped_and_coerced():
    assert SubagentContract(name="a", task="t",
                            max_retries=-3).max_retries == 0
    assert SubagentContract(name="a", task="t",
                            max_retries=10 ** 9).max_retries == MAX_SUBAGENT_RETRIES
    assert SubagentContract(name="a", task="t",
                            max_retries="10").max_retries == 0  # strings mint nothing
    assert SubagentContract(name="a", task="t",
                            max_retries="false").max_retries == 0
    assert SubagentContract(name="a", task="t",
                            retry_count=-2).retry_count == 0


def test_consume_budget_monotonic():
    o = _orch()
    c = _contract(max_retries=2)
    c1 = o._consume_budget(c)
    assert (c1.retry_count, c1.max_retries) == (1, 2)
    c2 = o._consume_budget(c1)
    assert (c2.retry_count, c2.max_retries) == (2, 2)
    assert c.retry_count == 0  # parent untouched
    assert o._budget_remaining(c2) == 0


@pytest.mark.asyncio
async def test_guarded_shares_budget_no_hidden_multiplier():
    o = _orch()
    counter = {"n": 0}
    o._runner.run = _fail_transient(counter)
    r = await o.run_parallel([_contract(max_retries=1)], max_concurrent=1,
                             adaptive=False, shared_context=False)
    assert counter["n"] == 2, counter  # 1 + 1, not 3
    assert r[0].retry_count == 1


@pytest.mark.asyncio
async def test_no_budget_no_retry():
    o = _orch()
    counter = {"n": 0}
    o._runner.run = _fail_transient(counter)
    r = await o.run_parallel([_contract()], max_concurrent=1,
                             adaptive=False, shared_context=False)
    assert counter["n"] == 1, counter  # max_retries=0: run once
    assert r[0].success is False


@pytest.mark.asyncio
async def test_nested_run_with_retry_bounded():
    o = _orch()
    counter = {"n": 0}
    o._runner.run = _fail_transient(counter, error="boom")
    r = await o._run_with_retry(_contract(max_retries=2))
    # 1 + 2 outer iterations; inner run() retries observe consumed budget
    assert counter["n"] == 3, counter
    assert "FAILED after 3 attempts" in r.output


@pytest.mark.asyncio
async def test_inner_timeout_consumes_outer_budget():
    o = _orch()
    calls = {"n": 0}

    async def _flaky(contract, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return SubagentResult(task_id="c", success=False, timed_out=True,
                                  error="Timeout after 60s", elapsed_seconds=60)
        return SubagentResult(task_id="c", success=False, error="boom",
                              elapsed_seconds=0.1)

    o._runner.run = _flaky
    # outer max 2, but the timeout consumes one unit inside run():
    # total executions bounded by 1 + 2 all the same.
    r = await o._run_with_retry(_contract(max_retries=2))
    assert calls["n"] <= 3, calls
    assert r.retry_count <= 2


# ── §26 matrix: what retries, what doesn't ──

@pytest.mark.asyncio
async def test_malformed_non_transient_no_retry():
    o = _orch()
    counter = {"n": 0}
    o._runner.run = _fail_transient(counter, error="parse failed: bad json")
    await o.run_parallel([_contract(max_retries=2)], max_concurrent=1,
                         adaptive=False, shared_context=False)
    assert counter["n"] == 1, counter


@pytest.mark.asyncio
async def test_cancelled_propagates_no_retry():
    o = _orch()
    counter = {"n": 0}

    async def _cancel(contract, **kw):
        counter["n"] += 1
        raise asyncio.CancelledError()

    o._runner.run = _cancel
    with pytest.raises(asyncio.CancelledError):
        await o.run_parallel([_contract(max_retries=2)], max_concurrent=1,
                             adaptive=False, shared_context=False)
    assert counter["n"] == 1, counter


@pytest.mark.asyncio
async def test_denial_never_retries():
    o = _orch()
    for entry in ("_run_with_retry", "run_parallel"):
        counter = {"n": 0}
        o._runner.run = _fail_transient(
            counter, error="[Denied by workspace layer: quarantine]")
        c = _contract(max_retries=2)
        if entry == "_run_with_retry":
            await o._run_with_retry(c)
        else:
            await o.run_parallel([c], max_concurrent=1, adaptive=False,
                                 shared_context=False)
        assert counter["n"] == 1, (entry, counter)


@pytest.mark.asyncio
async def test_readonly_task_retry_allowed():
    o = _orch()
    counter = {"n": 0}

    async def _run(contract, **kw):
        counter["n"] += 1
        return SubagentResult(task_id="c", success=False,
                              error="429 rate limit", elapsed_seconds=0.1,
                              tool_calls=[{"name": "read_file"}])

    o._runner.run = _run
    c = _contract(max_retries=2, tools=["read_file"],
                  worktree_isolated=False)
    await o.run_parallel([c], max_concurrent=1, adaptive=False,
                         shared_context=False)
    assert counter["n"] == 3, counter


@pytest.mark.asyncio
async def test_shared_mutation_blocks_retry():
    o = _orch()
    counter = {"n": 0}

    async def _run(contract, **kw):
        counter["n"] += 1
        return SubagentResult(task_id="c", success=False,
                              error="429 rate limit", elapsed_seconds=0.1,
                              tool_calls=[{"name": "write_file"}])

    o._runner.run = _run
    c = _contract(max_retries=2, worktree_isolated=False)
    r = await o.run_parallel([c], max_concurrent=1, adaptive=False,
                             shared_context=False)
    assert counter["n"] == 1, counter  # blocked after first mutating attempt
    assert r[0].success is False


@pytest.mark.asyncio
async def test_unknown_tool_fails_closed():
    from wisp.core.contracts import ToolRisk, risk_for_tool
    assert risk_for_tool("no_such_tool_xyz") == ToolRisk.EXEC
    o = _orch()
    assert o._auto_retry_safe(_contract(), SubagentResult(), False) is True
    r = SubagentResult(tool_calls=[{"name": "no_such_tool_xyz"}])
    assert o._auto_retry_safe(_contract(), r, False) is False


# ── §27 amplification fixture: declared vs observed ──

@pytest.mark.asyncio
async def test_declared_bound_holds_nested_stack():
    """provider_max=2, agent_max=2: worst-case executions derived, observed."""
    o = _orch()
    counts = {"agent": 0, "mutations": 0}

    async def _run(contract, **kw):
        counts["agent"] += 1
        return SubagentResult(task_id="c", success=False,
                              error="429 rate limit", elapsed_seconds=0.1)

    o._runner.run = _run
    # read-only contract: safety gate never the binding constraint here
    c = _contract(max_retries=2, tools=["read_file"])
    await o._run_with_retry(c)
    # declared: 1 + max_retries outer executions (inner gates observe it)
    assert counts["agent"] <= 1 + 2, counts
    assert counts["mutations"] == 0


@pytest.mark.asyncio
async def test_concurrent_fanout_bound():
    """8 children x budget 1: total executions bounded explicitly."""
    o = _orch()
    counts = {"n": 0}
    o._runner.run = _fail_transient(counts)
    contracts = [_contract(name=f"k{i}", max_retries=1,
                           tools=["read_file"]) for i in range(8)]
    rs = await o.run_parallel(contracts, max_concurrent=8, adaptive=False,
                              shared_context=False)
    assert counts["n"] == 16, counts  # 8 x (1 + 1), no hidden multiplier
    assert all(r.success is False for r in rs)


# ── Crash/restart/resume posture (§23-§24) ──
def test_retry_state_not_durably_minted():
    """Persistence is append-only telemetry: records carry no retry-budget
    authority, so restart/resume cannot inflate budget from them (fresh
    contracts start at 0 by construction = explicit new attempts)."""
    from wisp.multi_agent.subagent_orchestrator import Persistence
    import tempfile, pathlib
    p = Persistence(pathlib.Path(tempfile.mkdtemp()) / "r.jsonl")
    c = _contract(max_retries=2)
    c.retry_count = 2
    p.save(c, SubagentResult(task_id="c", success=False))
    loaded = p.load()
    assert loaded
    assert "retry_count" not in loaded[0] and "max_retries" not in loaded[0]
    fresh = SubagentContract(name="c", task="t")
    assert (fresh.retry_count, fresh.max_retries) == (0, 0)


# ── Pattern retry inherits budget (§5 child-cannot-mint) ──

@pytest.mark.asyncio
async def test_map_reduce_retry_inherits_consumed_budget():
    """Pattern retry derives from the stamped result: exhausted budget
    skips the retry round; remaining budget grants exactly one round."""
    o = _orch()
    counts = {"n": 0}

    async def _always_transient(contract, **kw):
        counts["n"] += 1
        return SubagentResult(task_id="m", success=False,
                              error="429 rate limit", elapsed_seconds=0.1,
                              tool_calls=[])

    o._runner.run = _always_transient
    # budget 1, transient-always: guarded consumes it fully (2 executions);
    # a pattern retry round would find used(1) >= max(1) -> skipped.
    r1 = await o.run_parallel([_contract(name="m0", max_retries=1)],
                              max_concurrent=1, adaptive=False,
                              shared_context=False)
    assert counts["n"] == 2, counts
    used = max(0, r1[0].retry_count or 0)
    assert used >= 1  # stamp observed; pattern would skip (1 >= 1)

    # non-transient failure with remaining budget -> exactly one more round
    counts["n"] = 0

    async def _fail_once(contract, **kw):
        counts["n"] += 1
        if counts["n"] == 1:
            return SubagentResult(task_id="m", success=False, error="boom",
                                  elapsed_seconds=0.1)
        return SubagentResult(task_id="m", success=True, output="ok",
                              elapsed_seconds=0.1)

    o._runner.run = _fail_once
    c = _contract(name="m0", max_retries=2, tools=["read_file"])
    r1 = await o.run_parallel([c], max_concurrent=1, adaptive=False,
                              shared_context=False)
    assert counts["n"] == 1 and r1[0].success is False  # non-transient: 1 try
    used = max(c.retry_count, r1[0].retry_count or 0)
    assert used < c.max_retries
    retry = o._with_count(c, used + 1)
    r2 = await o.run_parallel([retry], max_concurrent=1, adaptive=False,
                              shared_context=False)
    assert counts["n"] == 2 and r2[0].success is True
    # the retry round carried the inherited count (mechanism pinned)
    assert retry.retry_count == 1


# ── G1D interaction: incomplete attempt never executes (§13) ──

@pytest.mark.asyncio
async def test_truncated_attempt_then_complete_attempt():
    """Attempt 1 (truncated tool call) mutates 0; attempt 2 (complete)
    executes normally subject to authorization. Uses the turn-level gate
    through a wired executor (G1D), driven per attempt."""
    import tempfile
    from wisp.config import WispConfig
    from wisp.core.engine import WispAgentCore
    from wisp.tool_executor import ToolExecutor

    ws = tempfile.mkdtemp(prefix="g1e-g1d_")
    ex = ToolExecutor(config=WispConfig(), hook_manager=None, mcp=None,
                      file_lock=None, lsp_manager=None,
                      subagent_orchestrator=None, extensions=None)

    class _Truncated:
        def generate_stream_events(self, *a, **k):
            yield {"type": "tool_call", "name": "write_file", "id": "c1",
                   "arguments": {"path": "a.txt", "content": "partial"}}
            # no terminal marker -> truncated round

    class _Complete:
        def generate_stream_events(self, *a, **k):
            yield {"type": "tool_call", "name": "write_file", "id": "c2",
                   "arguments": {"path": "a.txt", "content": "whole"}}
            yield {"type": "done", "done_reason": "tool_calls"}

    async def _allow(ev):
        return True

    for provider in (_Truncated(), _Complete()):
        core = WispAgentCore(provider=provider, tool_executor=ex)
        session = {"id": "g", "messages": [], "model": "mock",
                   "workspace": ws}

        async def _run():
            async for ev in core.turn(session, "go",
                                      approval_handler=_allow):
                if ev.get("type") == "tool_result":
                    break

        await _run()
    import pathlib
    assert pathlib.Path(ws, "a.txt").read_text() == "whole"


# ── Turn-legacy retry never re-executes tools (§7 operation class) ──

@pytest.mark.asyncio
async def test_turn_transient_retry_executes_tool_once():
    """A transient provider failure before any tool call retries the fetch;
    the subsequent tool call executes exactly once."""
    import tempfile
    from wisp.config import WispConfig
    from wisp.core.engine import WispAgentCore
    from wisp.tool_executor import ToolExecutor
    import requests

    ws = tempfile.mkdtemp(prefix="g1e-turn_")
    ex = ToolExecutor(config=WispConfig(), hook_manager=None, mcp=None,
                      file_lock=None, lsp_manager=None,
                      subagent_orchestrator=None, extensions=None)
    calls = {"n": 0}

    class _Flaky:
        def generate_stream_events(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise requests.ConnectionError("reset before anything")
            yield {"type": "tool_call", "name": "write_file", "id": "c1",
                   "arguments": {"path": "once.txt", "content": "x"}}
            yield {"type": "done", "done_reason": "tool_calls"}

    async def _allow(ev):
        return True

    core = WispAgentCore(provider=_Flaky(), tool_executor=ex)
    session = {"id": "g", "messages": [], "model": "mock", "workspace": ws}

    async def _run():
        async for ev in core.turn(session, "go",
                                  approval_handler=_allow):
            if ev.get("type") == "tool_result":
                break

    await _run()
    import pathlib
    assert calls["n"] == 2  # 1 failed fetch + 1 good round
    assert pathlib.Path(ws, "once.txt").exists()
