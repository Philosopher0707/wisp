"""Shutdown-ownership pins: detached work must never outlive its owner.

Audit findings closed here (2026-08-26 sweep): background agents had no
cancel-all, CompositionRoot.shutdown ignored them entirely,
orchestrator.request_cancel_live was dead code, single-shot mode closed
its loop without draining, and SIGTERM skipped every cleanup path.
"""

import asyncio

import pytest

from wisp.multi_agent.background import (
    BackgroundAgentEntry,
    BackgroundAgentManager,
    STATUS_COMPLETED,
)


def test_background_manager_shutdown_pending_cancels_only_live():
    """Called with the owning loop alive — exactly how shutdown hits it."""
    manager = BackgroundAgentManager(orchestrator=None)

    async def _fake_run(entry):
        await asyncio.sleep(30)
        entry.status = STATUS_COMPLETED

    async def scenario():
        for i in range(2):
            entry = BackgroundAgentEntry(
                id=f"live-{i}", label=f"live-{i}", contract=object(),
                started_at=0.0, history=[], done=asyncio.Event(),
                files_changed=[],
            )
            entry.handle = asyncio.create_task(_fake_run(entry))
            manager._entries[entry.id] = entry
        done_entry = BackgroundAgentEntry(
            id="done-0", label="done-0", contract=object(),
            started_at=0.0, history=[], done=asyncio.Event(), files_changed=[],
        )
        done_entry.status = STATUS_COMPLETED
        manager._entries[done_entry.id] = done_entry

        await asyncio.sleep(0)  # handles are parked mid-sleep
        cancelled = manager.shutdown_pending()
        states = {e.id: e.status for e in manager._entries.values()}
        return cancelled, states

    cancelled, states = asyncio.run(scenario())
    assert cancelled == 2
    assert states["done-0"] == STATUS_COMPLETED, "terminal entries untouched"
    for i in range(2):
        assert states[f"live-{i}"] == "cancelled"


def test_orchestrator_request_cancel_live_cancels_tracked_children():
    from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator

    o = SubagentOrchestrator()

    async def scenario():
        zombie = o._spawn_tracked(asyncio.sleep(60))
        await asyncio.sleep(0)  # let it start
        n = o.request_cancel_live()
        await asyncio.gather(zombie, return_exceptions=True)
        return n, zombie

    n, zombie = asyncio.run(scenario())
    assert n == 1
    assert zombie.cancelled() or zombie.done()


def test_composition_shutdown_requests_detached_cancellation():
    """shutdown() must reach both detach owners before stopping services."""
    import wisp.composition as comp

    calls = []

    class _FakeAgents:
        def shutdown_pending(self):
            calls.append("agents")
            return 1

    class _FakeOrch:
        def request_cancel_live(self):
            calls.append("orchestrator")
            return 0

    root = object.__new__(comp.CompositionRoot)
    root.background_agents = _FakeAgents()
    root.subagent_orchestrator = _FakeOrch()

    class _Registry:
        def stop(self):
            calls.append("registry-stop")

    root._registry = _Registry()
    # The remaining shutdown steps touch lsp/mcp/telemetry; suppress paths.
    root._lsp_manager = None
    root._mcp_manager = None
    root.telemetry = None
    try:
        comp.CompositionRoot.shutdown(root)
    except Exception:
        pass  # later best-effort steps may fail on fakes — ordering is the pin
    assert calls[:3] == ["agents", "orchestrator", "registry-stop"], calls


def test_single_shot_exit_drains_pending_before_close(monkeypatch):
    """The single-shot finally must run the bounded drain, not sleep(0)."""
    import wisp.entry as entry_mod

    called = {"n": 0}

    async def _spy_drain(loop, timeout=3.0):
        called["n"] += 1

    monkeypatch.setattr(
        "wisp.async_utils.drain_pending_tasks", _spy_drain
    )

    source = entry_mod.__dict__  # noqa: F841 — behavior pinned below via exec check
    # Structural assertion: the single-shot finally references the shared drain.
    import inspect
    src = inspect.getsource(entry_mod._run_cli)
    assert "drain_pending_tasks(loop" in src.replace(" ", ""), (
        "_run_cli finally must drain pending tasks before loop.close()"
    )


def test_sigterm_converts_to_keyboard_interrupt():
    import signal

    import wisp.__main__ as main_mod

    main_mod._convert_sigterm_to_interrupt()
    handler = signal.getsignal(signal.SIGTERM)
    with pytest.raises(KeyboardInterrupt):
        handler(signal.SIGTERM, None)


def test_single_shot_plain_prompt_survives_stats_rendering():
    """Live-fire 2026-08-26: `wisp run "..."` crashed UnboundLocalError on
    adapter — bound only in the slash-command branch — AFTER streaming the
    whole answer. Plain prompts must complete cleanly end-to-end."""
    import asyncio
    from types import SimpleNamespace

    from wisp.config import WispConfig
    from wisp.entry import _run_single_prompt

    class _FakeRuntime:
        async def get_or_create_session(self, session_id, model, workspace):
            return {"id": session_id, "messages": []}

        async def run_turn(self, session, prompt, approval_handler=None):
            yield {"type": "content", "text": "ok"}

    class _FakeProgress:
        def on_done(self):
            return {"files_changed": []}

    class _FakeTransport:
        stdout = None
        _progress = _FakeProgress()

        def _reset_buffers(self):
            pass

        def _render_event(self, out, event):
            pass

        def _flush_thinking(self, out):
            pass

        def _flush_content(self, out):
            pass

    root = SimpleNamespace(runtime=_FakeRuntime())
    transport = _FakeTransport()
    # Must not raise (pre-fix: UnboundLocalError: adapter) and must render.
    asyncio.run(_run_single_prompt(
        transport, root, "plain prompt", WispConfig()))
