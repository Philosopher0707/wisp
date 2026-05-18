"""Agents router.

Handles WebSocket agent connections.
"""

from fastapi import APIRouter, WebSocket

router = APIRouter()


@router.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"Echo: {data}")
    except Exception:
        pass
    finally:
        await websocket.close()
