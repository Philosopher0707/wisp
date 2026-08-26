"""Shared ToolExecutor — per-execution state must be task-local.

The composition root wires ONE ToolExecutor to the root core AND to every
subagent child core (wisp/composition.py _create_core, wisp/multi_agent/
_runner.py). ToolExecutor.execute() therefore runs concurrently for
different agents — parent fanout while a background child executes, nested
spawn inside a fanout, two TUI sessions. Anything per-call must be
task-local, not an instance field:

  C1  stream queue      exec_ctx.sub_event_queue — a sibling's spawn must not
                        clobber this call's lifecycle-event channel.
  C2  pending repeat    exec_ctx.repeat_key — concurrent web_fetch calls must
                        not swap cache identities mid-flight.
  C3  agent identity    exec_ctx.agent_depth/branch — the engine publishes the
                        EXECUTING agent's depth at turn start, because the
                        shared executor's config is the root's (always 0) and
                        would flatten every descendant's depth (disarming the
                        unbounded-recursion guard).
  C4  skill capture     records only the top-level agent's tools; subagent
                        internals are mechanism, not the user's workflow.

Full contract: docs/tool-calling-contracts.md.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest

from wisp.config import WispConfig
from wisp.skill_capture import get_capture, reset_capture
from wisp.tools import context as exec_ctx
from wisp.tool_executor import ToolExecutor, _exec_branch, _exec_depth


class _FakeOrchParallel:
    """Blocking fanout fake: parks so concurrent calls overlap, and
    captures each invocation's contracts (with their bound callbacks)."""

    def __init__(self) -> None:
        self.calls: list[list[Any]] = []

    async def run_parallel(self, contracts: list[Any], max_concurrent: int = 4) -> list:
        self.calls.append(contracts)
        await asyncio.sleep(0.05)  # hold both concurrent calls in the window
        return []


class _MarkerEvent:
    """Stand-in OrchestratorEvent for probing a bound progress callback."""

    def __init__(self, tag: str) -> None:
        self.event_type = "task_started"
        self.task_id = f"probe-{tag}"
        self.payload = {"role": "coder", "description": f"MARKER-{tag}"}


# ═══════════════════════════════════════════════════════════════════
# C1: stream queue is task-local
# ═══════════════════════════════════════════════════════════════════


class TestStreamQueueTaskLocal:
    @pytest.mark.asyncio
    async def test_concurrent_fanouts_keep_separate_event_channels(self):
        """Two concurrent fanout calls: each child's lifecycle events must
        surface in the PARENT call's own event stream, never the sibling's.
        (Old design: a shared instance slot let the later call clobber the
        earlier one's channel.)
        """
        orch = _FakeOrchParallel()
        ex = ToolExecutor(WispConfig(), subagent_orchestrator=orch)
        results: dict[str, list[Any]] = {}

        async def drive(tag: str) -> None:
            events: list[Any] = []
            async for ev in ex.execute(
                "fanout",
                {"tasks": [{"task": tag, "role": "coder"}], "mode": "blocking"},
                "/tmp",
            ):
                events.append(ev)
            results[tag] = events

        ta = asyncio.create_task(drive("A"))
        tb = asyncio.create_task(drive("B"))
        await asyncio.sleep(0.02)  # both parked inside run_parallel
        assert len(orch.calls) == 2, "concurrent fanouts must overlap"

        for tag in ("A", "B"):
            for c in orch.calls[0] if tag == "A" else orch.calls[1]:
                if c.progress_callback is not None:
                    c.progress_callback(_MarkerEvent(tag))

        await asyncio.wait([ta, tb])

        a_text = json.dumps([str(e.data) for e in results["A"]], default=str)
        b_text = json.dumps([str(e.data) for e in results["B"]], default=str)
        assert "MARKER-A" in a_text, "call A must receive its own lifecycle events"
        assert "MARKER-B" not in a_text, "call A received sibling B's events — channel clobbered"
        assert "MARKER-B" in b_text, "call B must receive its own lifecycle events"
        assert "MARKER-A" not in b_text, "call B received sibling A's events — channel clobbered"

    def test_queue_is_cleared_after_call(self):
        """A finished fanout must not leave its queue in the ContextVar —
        a later plain call must not inherit a dead channel."""
        orch = _FakeOrchParallel()
        ex = ToolExecutor(WispConfig(), subagent_orchestrator=orch)

        async def run() -> None:
            async for _ev in ex.execute(
                "fanout", {"tasks": [{"task": "x", "role": "coder"}], "mode": "blocking"}, "/tmp"
            ):
                pass

        asyncio.run(run())
        assert exec_ctx.sub_event_queue.get() is None


# ═══════════════════════════════════════════════════════════════════
# C2: pending repeat key is task-local
# ═══════════════════════════════════════════════════════════════════


class TestRepeatKeyTaskLocal:
    @pytest.mark.asyncio
    async def test_concurrent_guarded_calls_cache_under_own_identity(self, monkeypatch):
        """Two concurrent web_fetch calls with different URLs: each result
        must be cached under ITS OWN (tool, args) key. (Old design: one
        instance slot held a single pending key; the second call overwrote
        the first's, and both results landed under the same identity.)
        """
        import wisp.tool_executor as te

        url_a = f"https://a-{uuid.uuid4().hex}/"
        url_b = f"https://b-{uuid.uuid4().hex}/"
        settled = asyncio.Event()

        def fake_execute(tool_name, args, workspace=None, **kw):
            settled.set()  # both calls have reached execution
            import time
            time.sleep(0.03)
            return json.dumps(
                {"status": "ok", "tool": tool_name, "data": f"body-{args['url']}"}
            )

        monkeypatch.setattr(te, "execute_tool", fake_execute)
        ex = ToolExecutor(WispConfig())

        async def fetch(url: str) -> None:
            async for _ev in ex.execute("web_fetch", {"url": url}, "/tmp"):
                pass

        ta = asyncio.create_task(fetch(url_a))
        tb = asyncio.create_task(fetch(url_b))
        await settled.wait()
        await asyncio.wait([ta, tb])

        keys = {k: v[1] for k, v in ex._repeat_cache.items()}
        key_a = f"web_fetch:{json.dumps({'url': url_a}, sort_keys=True)}"
        key_b = f"web_fetch:{json.dumps({'url': url_b}, sort_keys=True)}"
        assert key_a in keys, f"call A's identity missing from cache: {sorted(keys)}"
        assert key_b in keys, f"call B's identity missing from cache: {sorted(keys)}"
        assert f"body-{url_a}" in keys[key_a], "A's result cached under B's identity"
        assert f"body-{url_b}" in keys[key_b], "B's result cached under A's identity"


# ═══════════════════════════════════════════════════════════════════
# C3: executing-agent identity
# ═══════════════════════════════════════════════════════════════════


class TestAgentIdentity:
    def test_exec_depth_prefers_context_over_root_config(self):
        """The engine-published identity wins; the executor's own config
        (the root's, always 0) is only the fallback for direct users."""
        cfg = WispConfig()  # _subagent_depth = 0 (root)
        assert _exec_depth(cfg) == 0
        tok = exec_ctx.agent_depth.set(5)
        try:
            assert _exec_depth(cfg) == 5
        finally:
            exec_ctx.agent_depth.reset(tok)
        assert _exec_depth(cfg) == 0

    def test_exec_branch_prefers_context_over_root_config(self):
        cfg = WispConfig()
        assert _exec_branch(cfg) == 0
        tok = exec_ctx.agent_branch.set(7)
        try:
            assert _exec_branch(cfg) == 7
        finally:
            exec_ctx.agent_branch.reset(tok)

    @pytest.mark.asyncio
    async def test_child_spawn_stamps_depth_from_executing_agent(self):
        """A depth-3 child's own spawn must produce a depth-4 contract.
        Old design read the shared executor's ROOT config (always 0) and
        stamped depth 1 — grandchildren of grandchildren became
        grandchildren, and the depth guard never tripped.
        """
        from wisp.multi_agent.task import SubagentResult

        captured: list[Any] = []

        class FakeOrch:
            async def _run_with_retry(self, contract):
                captured.append(contract)
                return SubagentResult(
                    task_id=contract.name, success=True, output="done",
                    elapsed_seconds=0.1,
                )

        ex = ToolExecutor.__new__(ToolExecutor)
        ex.config = WispConfig()  # root config: _subagent_depth = 0
        ex.subagent_orchestrator = FakeOrch()

        tok_d = exec_ctx.agent_depth.set(3)
        tok_b = exec_ctx.agent_branch.set(2)
        try:
            await ex._spawn({"task": "nested work", "role": "coder"}, "/tmp")
        finally:
            exec_ctx.agent_depth.reset(tok_d)
            exec_ctx.agent_branch.reset(tok_b)

        assert len(captured) == 1
        assert captured[0]._subagent_depth == 4, (
            f"expected depth 4 (child depth 3 + 1), got {captured[0]._subagent_depth}"
        )
        assert captured[0]._subagent_branch_count == 3

    @pytest.mark.asyncio
    async def test_fanout_contracts_stamp_depth_from_executing_agent(self):
        from wisp.multi_agent.task import SubagentResult

        captured: list[Any] = []

        class FakeOrch:
            async def run_parallel(self, contracts, max_concurrent=4):
                captured.extend(contracts)
                return [
                    SubagentResult(task_id=c.name, success=True, output="ok",
                                   elapsed_seconds=0.1)
                    for c in contracts
                ]

        ex = ToolExecutor.__new__(ToolExecutor)
        ex.config = WispConfig()
        ex.subagent_orchestrator = FakeOrch()
        # Sentinel: _fanout resolves the manager before the mode check; a
        # real (non-None) value prevents the lazy creation of a real
        # BackgroundAgentManager around the fake orchestrator.
        ex.background_agents = object()

        tok = exec_ctx.agent_depth.set(2)
        try:
            await ex._fanout(
                {"tasks": [
                    {"task": "one", "role": "coder"},
                    {"task": "two", "role": "researcher"},
                ], "mode": "blocking"},
                "/tmp",
            )
        finally:
            exec_ctx.agent_depth.reset(tok)

        assert len(captured) == 2
        assert [c._subagent_depth for c in captured] == [3, 3]


# ═══════════════════════════════════════════════════════════════════
# C3+C4 through the real engine: turn() publishes identity, and the
# capture gate uses it.
# ═══════════════════════════════════════════════════════════════════


class _StatefulProvider:
    def __init__(self) -> None:
        self.calls = 0

    def generate_stream_events(self, system_prompt, messages, tools=None,
                               checkpoint_every=50):
        self.calls += 1
        if self.calls == 1:
            yield {"type": "tool_call", "name": "list_files",
                   "arguments": {"path": "."}}
        else:
            yield {"type": "content", "text": "done"}


def _core_with_depth(depth: int, provider, executor):
    from wisp.core.engine import WispAgentCore
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.security import PermissionMode, SecurityPolicy

    cfg = WispConfig()
    object.__setattr__(cfg, "_subagent_depth", depth)
    object.__setattr__(cfg, "_subagent_branch_count", 0)
    return WispAgentCore(
        provider=provider,
        security=SecurityPolicy(permission_mode=PermissionMode.FULL),
        extensions=ExtensionHost(),
        config=cfg,
        tool_executor=executor,
    )


class TestEnginePublishesIdentity:
    @pytest.mark.asyncio
    async def test_top_level_turn_records_skill_capture(self, tmp_path):
        """A depth-0 (top-level) turn's tool calls feed the workflow
        recorder — the /skill capture path keeps working."""
        reset_capture()
        try:
            core = _core_with_depth(0, _StatefulProvider(), ToolExecutor(WispConfig()))
            session = {"id": "top", "messages": [], "workspace": str(tmp_path)}
            async for _ev in core.turn(session, "list it"):
                pass
            assert any(s.tool == "list_files" for s in get_capture().recent()), (
                "top-level tool calls must be recorded"
            )
        finally:
            reset_capture()

    @pytest.mark.asyncio
    async def test_child_turn_does_not_pollute_skill_capture(self, tmp_path):
        """A depth-1 child's tool calls are mechanism, not the user's
        workflow — they must NOT reach the workflow recorder."""
        reset_capture()
        try:
            core = _core_with_depth(1, _StatefulProvider(), ToolExecutor(WispConfig()))
            session = {"id": "child", "messages": [], "workspace": str(tmp_path)}
            async for _ev in core.turn(session, "list it"):
                pass
            assert not any(s.tool == "list_files" for s in get_capture().recent()), (
                "subagent tool calls must not pollute the user's workflow capture"
            )
        finally:
            reset_capture()

    @pytest.mark.asyncio
    async def test_child_turn_publishes_its_own_depth(self, tmp_path):
        """The engine publishes the executing agent's identity for the whole
        turn — visible to any consumer (here: a spawn built inside the turn).
        A child at depth 2 must stamp its spawn at depth 3.
        """
        from wisp.multi_agent.task import SubagentResult

        # A provider whose tool call is a spawn (blocking, fake orchestrator).
        class SpawnProvider:
            def __init__(self) -> None:
                self.calls = 0

            def generate_stream_events(self, system_prompt, messages, tools=None,
                                       checkpoint_every=50):
                self.calls += 1
                if self.calls == 1:
                    yield {"type": "tool_call", "name": "spawn",
                           "arguments": {"task": "nested", "role": "coder"}}
                else:
                    yield {"type": "content", "text": "done"}

        captured: list[Any] = []

        class FakeOrch:
            async def _run_with_retry(self, contract):
                captured.append(contract)
                return SubagentResult(task_id=contract.name, success=True,
                                      output="ok", elapsed_seconds=0.1)

        ex = ToolExecutor(WispConfig(), subagent_orchestrator=FakeOrch())
        core = _core_with_depth(2, SpawnProvider(), ex)
        session = {"id": "grandchild", "messages": [], "workspace": str(tmp_path)}
        async for _ev in core.turn(session, "spawn one"):
            pass

        assert len(captured) == 1
        assert captured[0]._subagent_depth == 3, (
            f"a depth-2 child's spawn must be depth 3, got {captured[0]._subagent_depth} "
            "(the shared executor's root config leaked in)"
        )
