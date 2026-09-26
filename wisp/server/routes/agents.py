"""Agents router.

Handles WebSocket agent connections using WebSocketTransport + AgentRuntime.
"""

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from wisp.server.deps import _auth
from wisp.transport.websocket import WebSocketTransport

logger = logging.getLogger(__name__)

router = APIRouter()


MAX_WS_TEXT_SIZE = 256_000      # 256 KiB max for text/control messages
MAX_WS_IMAGE_SIZE = 10_000_000  # 10 MB max for image uploads


class _WsApprovalChannel:
    """One WebSocket connection, as an approval channel for REST (ADR-0057).

    The bridge owns correlation and timeouts; a channel only has to deliver a
    frame. That is what lets the round-trip be tested without a real WebSocket.
    """

    def __init__(self, ws: Any) -> None:
        self._ws = ws

    async def send_approval_frame(self, frame: dict) -> None:
        await self._ws.send_json(frame)


#: The decision keys that approve *this* call (`AgentRuntime.apply_approval_decision`'s own
#: once-verdict). A REST approval is one-shot, so `Y`/`a` approve it without folding memory.
_APPROVING_KEYS = frozenset({"y", "Y", "a"})


def _resolve_tool_approval(msg: dict, bridge: Any, transport: Any) -> tuple[Any, bool]:
    """Resolve one `tool_approval` frame; return `(call_id, approved)`.

    **The REST bridge is asked first, whichever form the client answers in.** The desktop
    and VS Code clients send `approved`; the TUI sends a `decision` key (`y`/`n`/`Y`/…). Until
    PR #30's review the `decision` form went straight to `resolve_decision`, whose unknown-id
    fallback resolves the *single pending agent approval* — so a "yes" to a REST hook
    registration approved an unrelated agent tool call, and the REST request timed out to 403.
    `bridge.resolve` only acts on an id the bridge issued, so asking it first cannot touch an
    agent approval.
    """
    call_id = msg.get("id")
    decision = msg.get("decision")
    if decision is not None:
        approved = str(decision).strip() in _APPROVING_KEYS
    else:
        approved = bool(msg.get("approved", False))
    # ADR-0057: a REST-originated approval is resolved by the bridge, which owns its own
    # correlation map. Checked FIRST: the transport has no entry for a `rest:` id, and its
    # fallback would resolve the wrong approval.
    if bridge is not None and call_id and bridge.resolve(call_id, approved):
        return call_id, approved
    if decision is not None and transport is not None:
        # Full y/Y/n/N/a/d/c contract: memory folds into the
        # session server-side; verdict comes back from there.
        return call_id, transport.resolve_decision(decision, approval_id=call_id)
    if transport is not None:
        resolved = transport.resolve_approval(approved, approval_id=call_id)
        approved = approved and resolved
    return call_id, approved


async def _turn_task_body(
    transport: WebSocketTransport,
    ws: Any,
    session: dict[str, Any],
    prompt: str,
) -> None:
    """Run one turn as its own task; translate outcomes into frames.

    The connection's reader loop must stay free while this runs — approval
    and interrupt frames are only readable there.
    """
    import contextlib
    try:
        await transport.stream_turn(ws, session, prompt)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await ws.send_json({"type": "status", "message": "Turn interrupted"})
        raise
    finally:
        pass


async def agent_settlement_pusher(background_agents: Any, websocket: Any) -> None:
    """Push agent_settled frames to one client as background agents finish.

    Subscribes to the manager's fan-out queue; runs as a side task next
    to the receive loop. Cancelled on disconnect (finally in the handler).
    """
    import asyncio
    queue = background_agents.subscribe()
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("Settlement pusher stopped for client", exc_info=True)
    finally:
        background_agents.unsubscribe(queue)


@router.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket):
    """WebSocket endpoint — auth via first-message AuthMessage frame only.

    Uses WebSocketTransport for event streaming and AgentRuntime for
    turn execution. Query-param auth removed: REST uses headers; WS
    uses a JSON frame so the API key never appears in URL.
    """
    await websocket.accept()
    _ws_authenticated = not _auth.required
    client_id = f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "unknown"

    # Get runtime from app state (created in lifespan)
    root = getattr(websocket.app.state, "root", None)
    if root is not None:
        transport = WebSocketTransport(root.runtime)
        transport.start()
    else:
        transport = None

    # ADR-0057: this connection is an approval channel, so a REST request for an
    # executable-config action can ask a human. Registered only once auth has
    # passed (below), so an unauthenticated socket never sees an action's name
    # or arguments.
    bridge = getattr(root, "approval_bridge", None) if root is not None else None
    channel = _WsApprovalChannel(websocket) if bridge is not None else None
    if channel is not None and not _auth.required:
        bridge.register(channel)

    session_id = None
    model = None
    turn_task: asyncio.Task[None] | None = None

    # Live settlement push: clients hear about finished background agents
    # without polling agents_list.
    pusher_task = None
    background_agents = getattr(root, "background_agents", None) if root is not None else None
    if background_agents is not None:
        pusher_task = asyncio.create_task(
            agent_settlement_pusher(background_agents, websocket))

    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                logger.info("Client %s disconnected", client_id)
                break

            # ── Security: enforce max message size (images get higher limit) ──
            msg_size = len(raw.encode("utf-8"))
            try:
                parsed_for_size = json.loads(raw)
                msg_type = parsed_for_size.get("type", "")
            except Exception:
                msg_type = ""
            max_size = MAX_WS_IMAGE_SIZE if msg_type == "image" else MAX_WS_TEXT_SIZE
            if msg_size > max_size:
                size_label = "image" if msg_type == "image" else "message"
                await websocket.send_json({
                    "type": "error",
                    "message": f"{size_label.capitalize()} too large (max {max_size // 1024} KiB)",
                })
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = msg.get("type")

            # First-message auth via `type: 'auth'`
            if msg_type == "auth":
                auth_key = msg.get("api_key", "")
                if _auth.required:
                    if auth_key == _auth.key:
                        _ws_authenticated = True
                        if bridge is not None and channel is not None:
                            bridge.register(channel)
                    else:
                        await websocket.send_json({"type": "error", "message": "Invalid API key"})
                        await websocket.close(code=4001)
                        return
                continue

            # Re-evaluate auth requirement every loop
            if _auth.required and not _ws_authenticated:
                await websocket.send_json({"type": "error", "message": "Authentication required"})
                await websocket.close(code=4001)
                return

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg_type == "prompt":
                prompt = msg.get("content", "").strip()
                if not prompt:
                    await websocket.send_json({"type": "error", "message": "Empty prompt"})
                    continue

                if transport is not None and root is not None:
                    # Initialize session on first prompt
                    if session_id is None:
                        session_id = msg.get("session_id") or f"ws-{client_id}"
                        model = msg.get("model") or root.config.model
                        await transport.handle(
                            ws=websocket,
                            session_id=session_id,
                            model=model,
                            workspace=root.config.workspace,
                        )

                    # Turns run as their OWN task so the reader loop keeps
                    # consuming control frames (approval/interrupt/ping).
                    if turn_task is not None and not turn_task.done():
                        await websocket.send_json({
                            "type": "error",
                            "message": "A turn is already running — send interrupt first",
                        })
                        continue

                    session = transport.session_for(websocket)
                    if session is None:
                        await websocket.send_json({
                            "type": "error",
                            "message": "Session not initialized",
                        })
                        continue
                    turn_task = asyncio.create_task(
                        _turn_task_body(transport, websocket, session, prompt)
                    )
                else:
                    # Fallback echo when no runtime available
                    await websocket.send_json({"type": "content", "text": f"Echo: {prompt}"})
                    await websocket.send_json({"type": "complete"})
                continue

            if msg_type == "tool_approval":
                call_id, approved = _resolve_tool_approval(msg, bridge, transport)
                await websocket.send_json({"type": "tool_approved", "id": call_id, "approved": approved})
                continue

            if msg_type == "interrupt":
                if turn_task is not None and not turn_task.done():
                    turn_task.cancel()
                    await websocket.send_json({
                        "type": "status", "message": "Interrupting turn",
                    })
                else:
                    await websocket.send_json({
                        "type": "status", "message": "No active turn",
                    })
                continue

            if msg_type == "pause":
                await websocket.send_json({"type": "steering_paused", "reason": "User paused"})
                continue

            if msg_type == "resume":
                await websocket.send_json({"type": "steering_resumed"})
                continue

            if msg_type == "swarm_run":
                goal = msg.get("goal", "").strip()
                if not goal:
                    await websocket.send_json({"type": "error", "message": "Empty swarm goal"})
                    continue
                await websocket.send_json({"type": "status", "message": f"Swarm started: {goal}"})
                continue

            if msg_type == "swarm_status":
                await websocket.send_json({"type": "swarm_status", "active": False, "message": "No active swarm"})
                continue

            if msg_type == "swarm_stop":
                await websocket.send_json({"type": "status", "message": "Swarm stopped", "level": "info"})
                continue

            # ── Background agents ─────────────────────────────────────
            if msg_type in ("agents_list", "agents_get", "agents_cancel", "agents_send"):
                bg = getattr(root, "background_agents", None) if root is not None else None
                if bg is None:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Background agents not available",
                    })
                    continue

                if msg_type == "agents_list":
                    entries = bg.list(include_finished=bool(msg.get("include_finished", True)))
                    await websocket.send_json({
                        "type": "agents_list",
                        "agents": entries,
                        "count": len(entries),
                    })
                elif msg_type == "agents_get":
                    agent_id = msg.get("agent_id", "")
                    entry = bg.get(agent_id)
                    if entry is None:
                        await websocket.send_json({"type": "error", "message": f"Unknown agent: {agent_id}"})
                    else:
                        snap = await bg.result(agent_id, wait_seconds=float(msg.get("wait_seconds", 0) or 0))
                        await websocket.send_json({"type": "agents_snapshot", **snap})
                elif msg_type == "agents_cancel":
                    outcome = bg.cancel(msg.get("agent_id", ""))
                    if not outcome.get("ok"):
                        await websocket.send_json({"type": "error", "message": outcome.get("error", "cancel failed")})
                    else:
                        await websocket.send_json({"type": "agents_cancelled", **outcome})
                else:  # agents_send
                    outcome = await bg.send(msg.get("agent_id", ""), msg.get("message", ""))
                    if not outcome.get("ok"):
                        await websocket.send_json({"type": "error", "message": outcome.get("error", "send failed")})
                    else:
                        await websocket.send_json({"type": "agents_continuation", **outcome})
                continue

            await websocket.send_json({"type": "error", "message": f"Unknown type: {msg_type}"})

    except Exception as e:
        logger.error("WebSocket error for %s: %s", client_id, e)
    finally:
        if bridge is not None and channel is not None:
            bridge.unregister(channel)
        if turn_task is not None and not turn_task.done():
            turn_task.cancel()
            try:
                await turn_task
            except (asyncio.CancelledError, Exception):
                pass
        if pusher_task is not None:
            pusher_task.cancel()
            try:
                await pusher_task
            except (asyncio.CancelledError, Exception):
                pass
        if transport is not None:
            transport.stop()
        try:
            await websocket.close()
        except Exception:
            pass
