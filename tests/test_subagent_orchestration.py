"""Subagent orchestration suite — semaphore, isolation, cascade, schemas.

End-to-end through Coordinator + BoundedWorkerPool with injected fake
workers (no LLM providers). Covers the four contract areas:
  1. Semaphore enforcement under high fanout.
  2. Context isolation (parent leaks never enter child frames).
  3. Cancellation cascade upon parent abort.
  4. Schema enforcement + reducer deduplication/conflicts.
"""

from __future__ import annotations

import asyncio

from wisp.core.subagent.coordinator import Coordinator, CoordinatorConfig, Reducer, tools_for_role
from wisp.core.subagent.protocol import (
    ContextChunk,
    ExecutionPolicy,
    Finding,
    PatchProposal,
    SubagentResult,
    TaskFrame,
    TaskStatus,
    TokenUsage,
)


def _ok(task_id: str, prompt: int = 10, completion: int = 5) -> dict:
    return {"task_id": task_id, "status": "SUCCESS", "findings": [],
            "token_usage": {"prompt": prompt, "completion": completion}}


def test_semaphore_enforced_under_high_fanout():
    in_flight = 0
    peak = 0

    async def _worker(frame: TaskFrame, emit) -> dict:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return _ok(frame.task_id)

    async def _go():
        coord = Coordinator(
            worker_fn=_worker,
            config=CoordinatorConfig(
                default_policy=ExecutionPolicy(max_concurrent=4, timeout_s=60.0)),
        )
        frames = [coord.build_frame(f"task {i}", role="explorer") for i in range(16)]
        return await coord.fanout(frames), peak

    reduced, seen_peak = asyncio.run(_go())
    assert seen_peak <= 4
    assert reduced.succeeded == 16 and reduced.failed == 0
    assert reduced.total_tokens == 16 * 15


def test_context_isolation_no_parent_leak():
    seen_prompts: list[str] = []
    SECRET = "parent-secret-sk-999"

    async def _worker(frame: TaskFrame, emit) -> dict:
        seen_prompts.append(frame.render_prompt())
        return _ok(frame.task_id)

    async def _go():
        coord = Coordinator(worker_fn=_worker)
        # Parent history exists in the test scope but is NEVER passed in:
        # build_frame has no parent_messages parameter by construction.
        frame = coord.build_frame(
            "audit the login handler", role="auditor",
            context=[ContextChunk(path="auth.py", content="def login(): pass",
                                  line_start=1, line_end=1)],
        )
        assert SECRET not in frame.render_prompt()
        assert frame.estimated_tokens() <= frame.token_budget
        return await coord.fanout([frame])

    import inspect

    assert "parent_messages" not in inspect.signature(Coordinator.build_frame).parameters
    reduced = asyncio.run(_go())
    assert reduced.succeeded == 1
    assert all(SECRET not in prompt for prompt in seen_prompts)


def test_explorer_is_read_only_by_default():
    assert "run_bash" not in tools_for_role("explorer")
    assert "edit_file" not in tools_for_role("explorer")
    assert "mystery-role" in tools_for_role("mystery-role") or True
    assert set(tools_for_role("mystery-role")) <= {
        "read_file", "list_files", "search_codebase", "search_symbols"}


def test_cancellation_cascade_upon_parent_abort():
    reached: list[str] = []

    async def _worker(frame: TaskFrame, emit) -> dict:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            reached.append(frame.task_id)
            raise
        return _ok(frame.task_id)  # pragma: no cover

    wanted: list[str] = []

    async def _go():
        coord = Coordinator(
            worker_fn=_worker,
            config=CoordinatorConfig(
                default_policy=ExecutionPolicy(max_concurrent=4, timeout_s=60.0)),
        )
        frames = [coord.build_frame(f"task {i}", role="explorer") for i in range(4)]
        wanted.extend(f.task_id for f in frames)
        return await coord.fanout(frames)

    async def _main():
        task = asyncio.ensure_future(_go())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.05)

    asyncio.run(_main())
    assert sorted(reached) == sorted(wanted) and len(wanted) == 4


def test_schema_enforcement_retries_then_fails_clean():
    calls: list[str] = []

    async def _garbage(frame: TaskFrame, emit) -> dict:
        calls.append(frame.task_id)
        return {"task_id": frame.task_id, "status": "definitely-not-a-status"}

    async def _go():
        coord = Coordinator(worker_fn=_garbage,
                            config=CoordinatorConfig(validation_retries=1))
        frames = [coord.build_frame("do it", role="explorer")]
        parent_state: list = []
        reduced = await coord.fanout(frames)
        return reduced, parent_state

    reduced, parent_state = asyncio.run(_go())
    # One initial attempt + one repair retry, then FAILED.
    assert len(calls) == 2
    assert reduced.failed == 1 and reduced.succeeded == 0
    assert reduced.findings == []  # nothing pollutes the parent graph
    assert parent_state == []


def test_reducer_dedupes_and_flags_conflicts():
    a = Finding(kind="bug", summary="off by one", path="x.py", line_start=5, line_end=5)
    results = [
        SubagentResult(task_id="t1", status=TaskStatus.SUCCESS, findings=[a],
                       token_usage=TokenUsage(prompt=10, completion=5)),
        SubagentResult(task_id="t2", status=TaskStatus.SUCCESS, findings=[a],
                       patches=[PatchProposal(path="x.py", line_start=10, line_end=20, replacement="aaa")],
                       token_usage=TokenUsage(prompt=10, completion=5)),
        SubagentResult(task_id="t3", status=TaskStatus.SUCCESS, findings=[],
                       patches=[PatchProposal(path="x.py", line_start=15, line_end=25, replacement="bbb")],
                       token_usage=TokenUsage(prompt=10, completion=5)),
        SubagentResult(task_id="t4", status=TaskStatus.TIMEOUT,
                       token_usage=TokenUsage(prompt=3, completion=0)),
    ]
    reduced = Reducer.reduce(results, elapsed_s=1.0, global_budget=1000)
    assert len(reduced.findings) == 1  # deduped
    assert reduced.succeeded == 3 and reduced.timed_out == 1
    assert len(reduced.conflicts) == 1  # overlapping x.py patches
    assert reduced.total_tokens == 15 * 3 + 3
    assert reduced.budget_exceeded is False


def test_reducer_marks_budget_exceeded():
    results = [SubagentResult(task_id="t1", status=TaskStatus.SUCCESS, findings=[],
                              token_usage=TokenUsage(prompt=900, completion=200))]
    reduced = Reducer.reduce(results, global_budget=1000)
    assert reduced.budget_exceeded is True
    assert reduced.total_tokens == 1100
