"""Tests for graceful server shutdown (Q21).

Validates that the lifespan context manager:
1. Interrupts in-flight agent tasks instead of killing them
2. Waits for agent tasks to complete (with a timeout)
3. Triggers session save via core.close() in the finally block
"""

import asyncio
import contextlib
import pytest
from unittest.mock import MagicMock


class FakeCore:
    def __init__(self, name="test-core"):
        self.name = name
        self.mcp = MagicMock()
        self.lsp = MagicMock()
        self.session = MagicMock()
        self.session.id = "sess-1"
        self.messages = []
        self._interrupted = False
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        self.mcp.shutdown()
        self.lsp.shutdown_all()


class FakeTransport:
    def __init__(self, core: FakeCore):
        self.core = core
        self.interrupt = MagicMock(side_effect=lambda: setattr(core, "_interrupted", True))


class FakeConnection:
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.agent_task: asyncio.Task | None = None
        self.transport: FakeTransport | None = None
        self.core: FakeCore | None = None
        self._run_lock = asyncio.Lock()

    async def send(self, msg: dict):
        pass

    async def stop_tasks(self, timeout: float = 2.0) -> None:
        if self.transport:
            self.transport.interrupt()
        if self.agent_task and not self.agent_task.done():
            try:
                await asyncio.wait_for(self.agent_task, timeout=timeout)
            except asyncio.TimeoutError:
                self.agent_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                    await asyncio.wait_for(self.agent_task, timeout=1.0)


@pytest.fixture
def mgr():
    from wisp.server import ConnectionManager
    return ConnectionManager()


@pytest.mark.asyncio
async def test_interrupt_flag_set_on_shutdown(mgr):
    """Shutdown must call transport.interrupt() instead of raw cancel()."""
    conn = FakeConnection("c1")
    core = FakeCore()
    conn.core = core
    conn.transport = FakeTransport(core)

    async def _run(fk=core):
        try:
            while True:
                if fk._interrupted:
                    break
                await asyncio.sleep(0.005)
        except asyncio.CancelledError:
            pass
        finally:
            fk.close()

    conn.agent_task = asyncio.create_task(_run())
    mgr._connections["c1"] = conn

    await mgr.shutdown_gracefully(timeout=0.2)

    conn.transport.interrupt.assert_called_once()
    assert core.close_calls == 1


@pytest.mark.asyncio
async def test_cancel_fallback_if_interrupt_doesnt_stop_task(mgr):
    """If the agent ignores the interrupt flag, cancel it after timeout."""
    conn = FakeConnection("c1")
    core = FakeCore()
    conn.core = core
    conn.transport = FakeTransport(core)

    async def _stubborn_run():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass
        finally:
            core.close()

    conn.agent_task = asyncio.create_task(_stubborn_run())
    mgr._connections["c1"] = conn

    start = asyncio.get_event_loop().time()
    await mgr.shutdown_gracefully(timeout=0.1)
    elapsed = asyncio.get_event_loop().time() - start

    assert elapsed < 0.5
    conn.transport.interrupt.assert_called_once()
    assert conn.agent_task.done()
    assert core.close_calls == 1


@pytest.mark.asyncio
async def test_multiple_connections_shut_down(mgr):
    """Five in-flight clients = all five interrupted + cleaned up."""
    conns = []
    for i in range(5):
        conn = FakeConnection(f"c{i}")
        core = FakeCore(name=f"core-{i}")
        conn.core = core
        conn.transport = FakeTransport(core)

        async def _run(idx=i, fk=core):
            try:
                while True:
                    if fk._interrupted:
                        break
                    await asyncio.sleep(0.005)
            except asyncio.CancelledError:
                pass
            finally:
                fk.close()

        conn.agent_task = asyncio.create_task(_run())
        mgr._connections[f"c{i}"] = conn
        conns.append(conn)

    await mgr.shutdown_gracefully(timeout=0.15)
    await asyncio.sleep(0.05)

    for conn in conns:
        conn.transport.interrupt.assert_called_once()
        assert conn.core.close_calls >= 1
        assert conn.agent_task.done()
