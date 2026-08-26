"""Integration pins: the WS connection must read control frames DURING turns.

Pre-fix, agent_websocket executed turns inline in the receive loop — so
tool_approval frames sat unread until the 60s approval timeout auto-denied
them, interrupt was a stub echo, and pings went unanswered mid-turn.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

from wisp.approval_state import SessionPolicy
from wisp.server.routes.agents import agent_websocket
from wisp.transport.websocket import WebSocketTransport

_SENTINEL = object()


class _FakeWebSocket:
    """Queue-fed inbound frames + recorded outbound frames."""

    def __init__(self, runtime):
        self._queue: asyncio.Queue = asyncio.Queue()
        self.outbox: list[dict] = []
        self.closed = False
        self.client = None
        self._root = SimpleNamespace(
            state=SimpleNamespace(),
            config=SimpleNamespace(model="fake", workspace="/tmp"),
            runtime=runtime,
        )
        self.app = SimpleNamespace(state=SimpleNamespace(root=self._root))

    def send_client(self, msg) -> None:
        self._queue.put_nowait(msg)

    async def accept(self):
        pass

    async def receive_text(self) -> str:
        item = await self._queue.get()
        if item is _SENTINEL:
            raise WebSocketDisconnect(code=1000)
        return json.dumps(item)

    async def send_json(self, payload: dict):
        self.outbox.append(payload)

    async def close(self, code: int = 1000):
        self.closed = True

    # ── assertion helpers ─────────────────────────────────────────
    def sent_types(self):
        return [m.get("type") for m in self.outbox]

    async def wait_for_type(self, msg_type: str, timeout: float = 5.0) -> dict:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if msg_type in self.sent_types():
                return next(m for m in self.outbox
                            if m.get("type") == msg_type)
            await asyncio.sleep(0.02)
        raise AssertionError(
            f"{msg_type} never arrived; got {self.sent_types()}")


def _runtime(turn_impl):
    class _FakeRuntime:
        def __init__(self):
            self.transport = WebSocketTransport(self)

        def approval_state(self, session_id):
            return SimpleNamespace(
                session_policy=SessionPolicy.PROMPT,
                allowed_tools=set(), denied_tools=set(),
            )

        def apply_approval_decision(self, sid, tool, key):
            return key.lower() == "y"

        async def get_or_create_session(self, session_id, model, workspace):
            return {"id": session_id, "model": model,
                    "workspace": workspace, "messages": []}

        run_turn = staticmethod(turn_impl)

    return _FakeRuntime()


async def _finish(task: asyncio.Task, ws: _FakeWebSocket) -> None:
    ws.send_client(_SENTINEL)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=5)
    except asyncio.TimeoutError:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_approval_frame_resolves_mid_turn():
    """approval_request → client approves → gate opens → turn completes."""
    seen_approved = []

    async def turn(session, prompt, approval_handler=None):
        yield {"type": "content", "text": "working"}
        ok = await approval_handler({"name": "run_bash",
                                     "arguments": {"cmd": "ls"}})
        seen_approved.append(ok)
        yield {"type": "tool_result", "approved": ok}
        yield {"type": "complete"}

    ws = _FakeWebSocket(_runtime(turn))
    ws.send_client({"type": "prompt", "content": "do it"})
    task = asyncio.create_task(agent_websocket(ws))

    await ws.wait_for_type("approval_request")
    ws.send_client({"type": "tool_approval", "id": "x", "approved": True})
    await ws.wait_for_type("complete")

    await _finish(task, ws)
    assert seen_approved == [True], "gate never received the client verdict"
    assert "tool_approved" in ws.sent_types()


@pytest.mark.asyncio
async def test_interrupt_cancels_running_turn():
    started = asyncio.Event()

    async def turn(session, prompt, approval_handler=None):
        yield {"type": "content", "text": "start"}
        started.set()
        await asyncio.sleep(30)  # would wedge forever pre-fix
        yield {"type": "complete"}  # pragma: no cover

    ws = _FakeWebSocket(_runtime(turn))
    ws.send_client({"type": "prompt", "content": "long job"})
    task = asyncio.create_task(agent_websocket(ws))

    await started.wait()
    await asyncio.sleep(0.05)  # let the turn park inside its sleep
    ws.send_client({"type": "interrupt"})

    statuses = []
    deadline = asyncio.get_running_loop().time() + 5.0
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.02)
        statuses = [m.get("message", "") for m in ws.outbox
                    if m.get("type") == "status"]
        if any("interrupt" in s.lower() for s in statuses):
            break
    assert any("interrupt" in s.lower() for s in statuses), statuses
    assert not any(t == "complete" for t in ws.sent_types()), \
        "interrupted turn still completed"

    await _finish(task, ws)


@pytest.mark.asyncio
async def test_ping_answered_while_turn_runs():
    async def turn(session, prompt, approval_handler=None):
        yield {"type": "content", "text": "start"}
        await asyncio.sleep(1.0)
        yield {"type": "complete"}

    ws = _FakeWebSocket(_runtime(turn))
    ws.send_client({"type": "prompt", "content": "job"})
    ws.send_client({"type": "ping"})  # arrives while the turn runs
    task = asyncio.create_task(agent_websocket(ws))

    await ws.wait_for_type("pong")
    await ws.wait_for_type("complete")
    # pong must precede completion — proof the reader stayed free mid-turn
    assert ws.sent_types().index("pong") < ws.sent_types().index("complete")
    await _finish(task, ws)


@pytest.mark.asyncio
async def test_second_prompt_rejected_while_turn_active():
    release = asyncio.Event()

    async def turn(session, prompt, approval_handler=None):
        yield {"type": "content", "text": "start"}
        await release.wait()
        yield {"type": "complete"}

    ws = _FakeWebSocket(_runtime(turn))
    ws.send_client({"type": "prompt", "content": "first"})
    ws.send_client({"type": "prompt", "content": "second"})
    task = asyncio.create_task(agent_websocket(ws))

    err = await ws.wait_for_type("error")
    assert "already running" in err.get("message", "")

    release.set()
    await ws.wait_for_type("complete")
    await _finish(task, ws)
