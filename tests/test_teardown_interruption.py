"""Regression pins for the Ctrl+C-during-subagent-swarm teardown crash.

Live incident (2026-08-26): interrupting a parallel subagent turn left
provider bridge threads blocking the event loop for 5s each, orphaned
orchestrator tasks running past turn cancellation ("Task exception was
never retrieved"), and a second Ctrl+C landing mid-teardown so cleanup
itself died with KeyboardInterrupt.
"""

import asyncio
import time


from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator
from wisp.providers.protocol import Provider


class _StuckProducer(Provider):
    """Sync stream whose producer keeps sleeping long after cancellation."""

    name = "stuck"

    def get_model_info(self, model: str) -> dict:
        return {}

    def health_check(self) -> dict:
        return {"status": "healthy"}

    def list_models(self) -> list[dict]:
        return []

    def generate_stream_events(self, system_prompt, messages, tools=None,
                               checkpoint_every=50):
        yield {"type": "content", "text": "first"}
        time.sleep(30.0)  # ignores cancelled flag far longer than any join
        yield {"type": "done", "done_reason": "never"}


def _events_iter(events):
    def gen(system_prompt, messages, tools=None, checkpoint_every=50):
        yield from events
    return gen


def test_bridge_cancel_does_not_block_event_loop():
    """Closing the bridge early must not stall the loop on thread.join."""
    provider = _StuckProducer()

    async def scenario():
        got_first = False
        async for _event in provider.generate_stream_events_async("s", []):
            got_first = True
            break
        assert got_first
        start = time.monotonic()
        await asyncio.sleep(0)  # let the generator's finally run via GC-free close
        return time.monotonic() - start

    # Explicit aclose is what unwinds the bridge finally-block.
    async def scenario_close():
        ait = provider.generate_stream_events_async("s", [])
        async for _event in ait:
            break
        start = time.monotonic()
        await ait.aclose()
        return time.monotonic() - start

    elapsed = asyncio.run(scenario_close())
    assert elapsed < 2.0, (
        f"bridge teardown blocked the loop for {elapsed:.2f}s — "
        "thread.join must never run on the loop thread"
    )


def test_run_parallel_cancel_reaps_children_and_exceptions():
    """Cancelling the caller must cancel every child and retrieve errors."""
    o = SubagentOrchestrator()

    async def fake_run(contract):
        await asyncio.sleep(30.0)
        return contract  # pragma: no cover - never reached

    o.run = fake_run  # type: ignore[method-assign]

    async def scenario():
        contracts = [
            type("C", (), {"name": f"c{i}", "_shared_context": None})()
            for i in range(3)
        ]
        outer = asyncio.create_task(o.run_parallel(contracts, max_concurrent=3))
        await asyncio.sleep(0.1)  # children are up and parked in sleep
        outer.cancel()
        try:
            await outer
        except asyncio.CancelledError:
            pass
        zombies = [t for t in asyncio.all_tasks()
                   if t is not asyncio.current_task() and not t.done()]
        return zombies

    zombies = asyncio.run(scenario())
    assert not zombies, (
        f"{len(zombies)} orchestrator child tasks survived caller "
        "cancellation — they leak into REPL teardown"
    )


def test_entry_cleanup_drains_pending_tasks_bounded():
    """REPL teardown helper must reap stragglers within its time budget."""
    from wisp.entry import _drain_pending_tasks

    async def scenario():
        loop = asyncio.get_running_loop()
        stuck = loop.create_task(asyncio.sleep(60))
        boom = loop.create_task(_raise_after_delay())
        await asyncio.sleep(0)
        start = time.monotonic()
        await _drain_pending_tasks(loop, timeout=1.0)
        elapsed = time.monotonic() - start
        assert stuck.done(), "zombie task not reaped by drain"
        assert boom.done(), "failing task not reaped by drain"
        assert elapsed < 3.0, f"drain took {elapsed:.2f}s"

    async def _raise_after_delay():
        await asyncio.sleep(0.01)
        raise ValueError("unretrieved-in-waiting")

    asyncio.run(scenario())


def test_full_incident_choreography_no_unretrieved_exceptions():
    """The exact 2026-08-26 crash, pinned end-to-end.

    Children stream through a REAL provider bridge; the caller is
    cancelled; teardown drains the loop. The asyncio exception handler
    must never see "Task exception was never retrieved" and no task may
    survive the drain.
    """
    retrieved_errors: list[BaseException] = []

    def _counting_handler(loop, context):
        if context.get("message", "").startswith("Task exception was never"):
            retrieved_errors.append(context.get("exception"))

    o = SubagentOrchestrator()
    provider = _StuckProducer()

    async def bridge_run(contract):
        # Consume a real bridge like _runner._run_via_runtime does.
        async for _event in provider.generate_stream_events_async("s", []):
            pass
        return contract  # pragma: no cover

    o.run = bridge_run  # type: ignore[method-assign]

    async def scenario(loop):
        loop.set_exception_handler(_counting_handler)
        contracts = [
            type("C", (), {"name": f"c{i}", "_shared_context": None})()
            for i in range(3)
        ]
        outer = loop.create_task(o.run_parallel(contracts, max_concurrent=3))
        await asyncio.sleep(0.15)  # children parked inside bridge reads
        outer.cancel()
        try:
            await outer
        except asyncio.CancelledError:
            pass
        from wisp.entry import _drain_pending_tasks
        await _drain_pending_tasks(loop, timeout=2.0)
        stragglers = [t for t in asyncio.all_tasks()
                      if t is not asyncio.current_task() and not t.done()]
        return stragglers

    loop = asyncio.new_event_loop()
    try:
        stragglers = loop.run_until_complete(scenario(loop))
    finally:
        loop.close()
    assert not stragglers, f"{len(stragglers)} tasks survived teardown"
    assert not retrieved_errors, (
        f"{len(retrieved_errors)} unretrieved task exceptions — "
        "orchestrator must hold strong refs and retrieve child results"
    )


def test_no_blocking_thread_join_inside_async_stream_finallys():
    """Structural pin: the bridge join must exist in exactly ONE form.

    The 2026-08-26 crash was fixed in providers/protocol.py but lived on
    as byte-identical copies in ollama.py and core/stateless.py — three
    implementations of one pattern, only one of which got the fix. This
    test fails if any blocking thread.join reappears in an async stream
    bridge, so copies can never silently diverge from the fixed default.
    """
    import re
    from pathlib import Path

    roots = [Path("wisp/providers"), Path("wisp/core")]
    offenders = []
    for root in roots:
        for py in root.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            # Comments may name the banned call; only real code counts.
            code_only = re.sub(r"^\s*#.*$", "", text, flags=re.M)
            if "thread.join(" not in code_only:
                continue
            # Flag any thread.join inside these bridge files; the only
            # sanctioned home for producer-thread joins is none — bridges
            # poll cooperatively instead.
            offenders.append(f"{py}:{text.count('thread.join(')}")
    assert not offenders, (
        "blocking thread.join() found in stream-bridge code "
        f"{offenders} — use the cooperative bounded wait from "
        "providers/protocol.py instead"
    )
    # And the canonical fixed implementation must still exist:
    protocol = Path("wisp/providers/protocol.py").read_text(encoding="utf-8")
    assert "while thread.is_alive()" in protocol, \
        "canonical cooperative bridge wait was removed"
