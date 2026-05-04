"""Wisp Cloud Server — FastAPI + WebSocket for remote Android clients.

Run:
    wisp server --host 0.0.0.0 --port 8000

Environment:
    WISP_API_KEY      Pre-shared key for client auth (required)
    WISP_WORKSPACE    Root workspace directory (default: ./workspace)
    OLLAMA_HOST       Ollama URL (default: http://localhost:11434)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from wisp.config import WispConfig
from wisp.server_agent import ServerAgent
from wisp.session import SessionManager

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────

API_KEY = os.environ.get("WISP_API_KEY", "")
WORKSPACE_ROOT = Path(os.environ.get("WISP_WORKSPACE", "./workspace")).resolve()
MAX_BASH_OUTPUT = 50_000  # chars

if not API_KEY:
    API_KEY = secrets.token_urlsafe(32)
    logger.warning("WISP_API_KEY not set — generated temporary key: %s", API_KEY)

WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

# ── Pydantic models ──────────────────────────────────────────────────

class PromptRequest(BaseModel):
    prompt: str
    model: Optional[str] = None
    session_id: Optional[str] = None
    skill: Optional[str] = None

class BashRequest(BaseModel):
    command: str = Field(..., max_length=2000)
    cwd: Optional[str] = None

class FileWriteRequest(BaseModel):
    content: str

class FileEditRequest(BaseModel):
    old_text: str
    new_text: str

class ToolApproval(BaseModel):
    call_id: str
    approved: bool
    reason: Optional[str] = None

# ── Auth ─────────────────────────────────────────────────────────────

async def verify_api_key(x_api_key: str = Query(..., alias="api-key")):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key

# ── App lifecycle ────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Wisp Cloud Server starting")
    logger.info("Workspace: %s", WORKSPACE_ROOT)
    logger.info("API key: %s...%s", API_KEY[:4], API_KEY[-4:])
    yield
    logger.info("Wisp Cloud Server shutting down")

app = FastAPI(
    title="Wisp Cloud",
    description="Remote coding agent server for Wisp",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS: restrict to known origins. Override via env var for development.
CORS_ORIGINS = os.environ.get("WISP_CORS_ORIGINS", "")
if CORS_ORIGINS:
    _origins = [o.strip() for o in CORS_ORIGINS.split(",") if o.strip()]
else:
    # Default: same-origin only (no web browser access from other domains)
    _origins = []

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# ── Connection manager ───────────────────────────────────────────────

class Connection:
    """Represents one WebSocket client connection."""

    def __init__(self, websocket: WebSocket, client_id: str):
        self.ws = websocket
        self.client_id = client_id
        self.agent: Optional[ServerAgent] = None
        self.agent_task: Optional[asyncio.Task] = None
        self._closed = False

    async def send(self, msg: dict):
        if not self._closed:
            try:
                await self.ws.send_json(msg)
            except Exception as e:
                logger.debug("Send failed to %s: %s", self.client_id, e)

    async def close(self):
        self._closed = True
        if self.agent:
            self.agent._interrupted = True
        if self.agent_task and not self.agent_task.done():
            self.agent_task.cancel()
            try:
                await asyncio.wait_for(self.agent_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        try:
            await self.ws.close()
        except Exception:
            pass


class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, Connection] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, client_id: str) -> Connection:
        await websocket.accept()
        conn = Connection(websocket, client_id)
        async with self._lock:
            self._connections[client_id] = conn
        return conn

    async def disconnect(self, client_id: str):
        async with self._lock:
            conn = self._connections.pop(client_id, None)
        if conn:
            await conn.close()

    async def broadcast(self, msg: dict):
        async with self._lock:
            conns = list(self._connections.values())
        for conn in conns:
            await conn.send(msg)


manager = ConnectionManager()

# ── Helpers ────────────────────────────────────────────────────────

def _resolve_path(path: str) -> Path:
    """Resolve a user-provided path safely inside WORKSPACE_ROOT."""
    target = (WORKSPACE_ROOT / path).resolve()
    # Prevent path traversal
    try:
        target.relative_to(WORKSPACE_ROOT)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    return target


# ── REST API ─────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"service": "wisp-cloud", "version": "0.1.0"}


@app.get("/api/models", dependencies=[Depends(verify_api_key)])
async def list_models():
    config = WispConfig()
    from wisp.ollama_client import OllamaClient
    client = OllamaClient(config)
    try:
        models = client.list_models()
        return {"models": models}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Ollama error: {e}")


@app.get("/api/sessions", dependencies=[Depends(verify_api_key)])
async def list_sessions():
    sm = SessionManager()
    sessions = sm.list_sessions()
    return {"sessions": [s.to_dict() for s in sessions]}


@app.get("/api/files", dependencies=[Depends(verify_api_key)])
async def list_or_read_file(path: str = ""):
    target = _resolve_path(path)
    if target.is_dir():
        items = []
        for item in target.iterdir():
            rel = item.relative_to(WORKSPACE_ROOT).as_posix()
            items.append({
                "name": item.name,
                "path": rel,
                "type": "directory" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None,
            })
        return {"type": "directory", "path": path, "items": items}
    elif target.is_file():
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            return {"type": "file", "path": path, "content": content}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        raise HTTPException(status_code=404, detail="Not found")


@app.post("/api/files", dependencies=[Depends(verify_api_key)])
async def write_file(path: str, req: FileWriteRequest):
    target = _resolve_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(req.content, encoding="utf-8")
        return {"ok": True, "path": path, "bytes": len(req.content.encode("utf-8"))}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/files/edit", dependencies=[Depends(verify_api_key)])
async def edit_file(path: str, req: FileEditRequest):
    target = _resolve_path(path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content = target.read_text(encoding="utf-8")
        if req.old_text not in content:
            raise HTTPException(status_code=400, detail="old_text not found in file")
        new_content = content.replace(req.old_text, req.new_text, 1)
        target.write_text(new_content, encoding="utf-8")
        return {"ok": True, "path": path}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/files", dependencies=[Depends(verify_api_key)])
async def delete_file(path: str):
    target = _resolve_path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/bash", dependencies=[Depends(verify_api_key)])
async def run_bash(req: BashRequest):
    """Run a bash command inside the workspace. Restricted for safety."""
    import subprocess

    # Block dangerous commands at the API layer too
    from wisp.tools import check_dangerous_command
    danger = check_dangerous_command(req.command)
    if danger:
        raise HTTPException(status_code=400, detail=f"Dangerous command blocked: {danger}")

    cwd = _resolve_path(req.cwd or ".")
    if not cwd.is_dir():
        raise HTTPException(status_code=400, detail="Invalid cwd")

    try:
        proc = subprocess.run(
            req.command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        stdout = proc.stdout[:MAX_BASH_OUTPUT]
        stderr = proc.stderr[:MAX_BASH_OUTPUT]
        return {
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": len(proc.stdout) > MAX_BASH_OUTPUT or len(proc.stderr) > MAX_BASH_OUTPUT,
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="Command timed out after 60s")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── WebSocket Agent ──────────────────────────────────────────────────

@app.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket, api_key: str = Query(...)):
    if api_key != API_KEY:
        await websocket.close(code=4001, reason="Invalid API key")
        return

    client_id = f"{websocket.client.host}:{websocket.client.port}"
    conn = await manager.connect(websocket, client_id)
    loop = asyncio.get_running_loop()

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await conn.send({"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = msg.get("type")

            if msg_type == "prompt":
                prompt = msg.get("content", "").strip()
                if not prompt:
                    await conn.send({"type": "error", "message": "Empty prompt"})
                    continue

                # Stop any existing agent run
                if conn.agent_task and not conn.agent_task.done():
                    if conn.agent:
                        conn.agent._interrupted = True
                    conn.agent_task.cancel()
                    try:
                        await asyncio.wait_for(conn.agent_task, timeout=2)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass

                config = WispConfig()
                if msg.get("model"):
                    config.model = msg["model"]
                config.workspace = str(WORKSPACE_ROOT)
                config.auto_approve = False
                config.show_thinking = msg.get("show_thinking", True)

                conn.agent = ServerAgent(config, conn.send, loop)
                session_id = msg.get("session_id")

                # Run agent in a background thread to avoid blocking the event loop
                conn.agent_task = asyncio.create_task(
                    asyncio.to_thread(conn.agent.run_server, prompt, session_id=session_id)
                )

            elif msg_type == "tool_approval":
                call_id = msg.get("id")
                approved = msg.get("approved", False)
                reason = msg.get("reason")
                if conn.agent:
                    conn.agent.approve_tool(call_id, approved, reason)
                else:
                    await conn.send({"type": "error", "message": "No active agent"})

            elif msg_type == "interrupt":
                if conn.agent:
                    conn.agent._interrupted = True
                await conn.send({"type": "status", "message": "Interrupted"})

            elif msg_type == "ping":
                await conn.send({"type": "pong"})

            else:
                await conn.send({"type": "error", "message": f"Unknown type: {msg_type}"})

    except WebSocketDisconnect:
        logger.info("Client %s disconnected", client_id)
    except Exception as e:
        logger.error("WebSocket error for %s: %s", client_id, e)
    finally:
        await manager.disconnect(client_id)


# ── Entry point ──────────────────────────────────────────────────────

def main(host: str = "0.0.0.0", port: int = 8000):
    import uvicorn
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
