"""Agents router.

Handles WebSocket agent connections using WebSocketTransport + AgentRuntime.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from wisp.server.deps import _auth
from wisp.transport.websocket import WebSocketTransport

logger = logging.getLogger(__name__)

router = APIRouter()


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

    session_id = None
    model = None

    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                logger.info("Client %s disconnected", client_id)
                break

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

                    # Route through transport
                    await transport.receive_message(websocket, {"type": "user", "text": prompt})
                else:
                    # Fallback echo when no runtime available
                    await websocket.send_json({"type": "content", "text": f"Echo: {prompt}"})
                    await websocket.send_json({"type": "complete"})
                continue

            if msg_type == "tool_approval":
                call_id = msg.get("id")
                approved = msg.get("approved", False)
                await websocket.send_json({"type": "tool_approved", "id": call_id, "approved": approved})
                continue

            if msg_type == "interrupt":
                await websocket.send_json({"type": "status", "message": "Interrupted"})
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

            await websocket.send_json({"type": "error", "message": f"Unknown type: {msg_type}"})

    except Exception as e:
        logger.error("WebSocket error for %s: %s", client_id, e)
    finally:
        if transport is not None:
            transport.stop()
        try:
            await websocket.close()
        except Exception:
            pass
