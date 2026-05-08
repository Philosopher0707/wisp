"""Wisp Cloud Server — FastAPI + WebSocket for remote clients.

Uses WispAgentCore + ServerTransport for event-driven agent execution.

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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from wisp.config import WispConfig
from wisp.core.agent import WispAgentCore
from wisp.transport.server import ServerTransport
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

class DiffRequest(BaseModel):
    path: str
    new_content: str

class ToolApproval(BaseModel):
    call_id: str
    approved: bool
    reason: Optional[str] = None

# ── Auth ─────────────────────────────────────────────────────────────

async def verify_api_key(x_api_key: str = Query(..., alias="api-key")):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key

# ── App lifecycle ────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Wisp Cloud Server starting...")
    yield
    logger.info("Wisp Cloud Server shutting down...")

app = FastAPI(title="Wisp Cloud", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Connection manager ───────────────────────────────────────────────

class Connection:
    """Represents a single WebSocket connection."""

    def __init__(self, websocket: WebSocket, client_id: str):
        self.websocket = websocket
        self.client_id = client_id
        self.agent_task: Optional[asyncio.Task] = None
        self.transport: Optional[ServerTransport] = None
        self._run_lock = asyncio.Lock()

    async def send(self, msg: dict):
        try:
            await self.websocket.send_text(json.dumps(msg))
        except Exception as e:
            logger.warning("Failed to send to %s: %s", self.client_id, e)


class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, Connection] = {}

    async def connect(self, websocket: WebSocket, client_id: str) -> Connection:
        await websocket.accept()
        conn = Connection(websocket, client_id)
        self._connections[client_id] = conn
        logger.info("Client %s connected", client_id)
        return conn

    async def disconnect(self, client_id: str):
        conn = self._connections.pop(client_id, None)
        if conn and conn.agent_task and not conn.agent_task.done():
            conn.agent_task.cancel()
            try:
                await asyncio.wait_for(conn.agent_task, timeout=2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        logger.info("Client %s disconnected", client_id)

    async def send(self, client_id: str, msg: dict):
        conn = self._connections.get(client_id)
        if conn:
            await conn.send(msg)


manager = ConnectionManager()

# ── Helpers ──────────────────────────────────────────────────────────

def _resolve_path(path: str) -> Path:
    target = WORKSPACE_ROOT / path
    try:
        target.relative_to(WORKSPACE_ROOT)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path traversal blocked")
    return target

# ── HTTP Routes ──────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/workspace")
async def workspace_info():
    return {"path": str(WORKSPACE_ROOT)}


class WorkspaceRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=1000)


@app.post("/api/workspace", dependencies=[Depends(verify_api_key)])
async def set_workspace(req: WorkspaceRequest):
    global WORKSPACE_ROOT
    new_root = Path(req.path).resolve()
    if not new_root.exists():
        raise HTTPException(status_code=400, detail="Directory does not exist")
    if not new_root.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")
    WORKSPACE_ROOT = new_root
    logger.info("Workspace changed to %s", WORKSPACE_ROOT)
    return {"path": str(WORKSPACE_ROOT)}


@app.get("/api/models", dependencies=[Depends(verify_api_key)])
async def list_models():
    """List available Ollama models."""
    import requests
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        models = [m.get("name", "") for m in data.get("models", [])]
        return {"models": models}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Ollama error: {e}")


@app.get("/api/sessions", dependencies=[Depends(verify_api_key)])
async def list_sessions():
    sm = SessionManager()
    sessions = sm.list_sessions()
    # list_sessions returns list[dict]; strip non-serializable keys
    for s in sessions:
        s.pop("file", None)
    return {"sessions": sessions}


@app.get("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
async def get_session(session_id: str):
    sm = SessionManager()
    session = sm.load(session_id)
    if session is None:
        resolved = sm.get_session_id_from_fragment(session_id)
        if resolved:
            session = sm.load(resolved)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session": session.to_dict()}


@app.delete("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
async def delete_session(session_id: str):
    sm = SessionManager()
    ok = sm.delete(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


class RenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


@app.patch("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
async def rename_session(session_id: str, req: RenameRequest):
    sm = SessionManager()
    session = sm.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    session.title = req.title
    sm.save(session)
    return {"ok": True, "title": req.title}


class ForkRequest(BaseModel):
    messages: list[dict]
    title: Optional[str] = None


@app.post("/api/sessions/fork", dependencies=[Depends(verify_api_key)])
async def fork_session(req: ForkRequest):
    from wisp.session import Session
    import copy, uuid

    sm = SessionManager()
    now = _now_iso()
    if req.title:
        slug = req.title[:60].strip()
    else:
        slug = "forked"
    sid = f"{_timestamp_id()}-{slug}"

    session = Session(
        id=sid,
        created_at=now,
        updated_at=now,
        model="",
        workspace=str(WORKSPACE_ROOT),
        messages=copy.deepcopy(req.messages),
        title=slug,
    )
    sm.save(session)
    return {"session_id": session.id, "title": session.title}


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _timestamp_id():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


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


@app.get("/api/files/tree", dependencies=[Depends(verify_api_key)])
async def file_tree():
    """Return a flat list of all files recursively (for quick-open)."""
    files = []
    for root, dirs, filenames in os.walk(WORKSPACE_ROOT):
        # Skip hidden directories and common ignore patterns
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__', '.git', 'dist', 'build', 'target', '.next', 'venv', '.venv', 'env')]
        for f in filenames:
            if f.startswith('.'):
                continue
            full = os.path.join(root, f)
            try:
                rel = os.path.relpath(full, WORKSPACE_ROOT)
                files.append({"name": f, "path": rel, "size": os.path.getsize(full)})
            except OSError:
                pass
    files.sort(key=lambda x: x["path"])
    return {"files": files}


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


@app.post("/api/diff", dependencies=[Depends(verify_api_key)])
async def diff_file(req: DiffRequest):
    target = _resolve_path(req.path)
    if target.is_file():
        try:
            old_content = target.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")
        from wisp.diff import generate_diff_string
        result = generate_diff_string(old_content, req.new_content)
        return {"diff": result.diff, "is_new": False, "path": req.path, "first_changed_line": result.first_changed_line}
    else:
        from wisp.diff import generate_diff_string
        result = generate_diff_string("", req.new_content)
        return {"diff": result.diff, "is_new": True, "path": req.path, "first_changed_line": result.first_changed_line}


@app.get("/api/git", dependencies=[Depends(verify_api_key)])
async def git_status():
    import subprocess
    git_dir = WORKSPACE_ROOT / ".git"
    if not git_dir.exists():
        return {"git": False}

    result: dict = {"git": True, "branch": "", "dirty": False, "ahead": 0, "behind": 0, "changed_files": []}

    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=5,
        )
        if branch.returncode == 0:
            result["branch"] = branch.stdout.strip()
    except Exception:
        pass

    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=5,
        )
        if status.returncode == 0:
            lines = [l for l in status.stdout.strip().split("\n") if l]
            result["dirty"] = len(lines) > 0
            result["changed_files"] = [l[3:].strip() for l in lines]
    except Exception:
        pass

    return result


class GitCommitRequest(BaseModel):
    message: Optional[str] = None


@app.post("/api/git/commit", dependencies=[Depends(verify_api_key)])
async def git_commit(req: GitCommitRequest):
    import subprocess
    git_dir = WORKSPACE_ROOT / ".git"
    if not git_dir.exists():
        raise HTTPException(status_code=400, detail="No git repository")

    # Stage all changes
    add = subprocess.run(
        ["git", "add", "-A"],
        cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=10,
    )
    if add.returncode != 0:
        raise HTTPException(status_code=500, detail=f"git add failed: {add.stderr}")

    # Build commit message if not provided
    msg = req.message
    if not msg:
        diff_stat = subprocess.run(
            ["git", "diff", "--staged", "--stat"],
            cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=10,
        )
        msg = f"chore: update files\n\n{diff_stat.stdout.strip()}" if diff_stat.stdout.strip() else "chore: update"

    commit = subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=10,
    )
    if commit.returncode != 0:
        raise HTTPException(status_code=500, detail=f"git commit failed: {commit.stderr}")

    return {"ok": True, "message": msg, "output": commit.stdout.strip()}


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
async def agent_websocket(websocket: WebSocket, api_key: str = Query(default="")):
    if API_KEY and api_key != API_KEY:
        await websocket.close(code=4001, reason="Invalid API key")
        return

    client_id = f"{websocket.client.host}:{websocket.client.port}"
    conn = await manager.connect(websocket, client_id)

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

                # Serialize prompt handling per connection to prevent concurrent agents
                async with conn._run_lock:
                    # Stop any existing agent run
                    if conn.agent_task and not conn.agent_task.done():
                        if conn.transport:
                            conn.transport.interrupt()
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
                    config.permission_mode = msg.get("permission_mode", "full")
                    config.plan_mode = msg.get("plan_mode", False)
                    config.plan_context = msg.get("plan_context")

                    session_id = msg.get("session_id")
                    session = None
                    if session_id:
                        sm = SessionManager()
                        session = sm.load(session_id)
                        if session is None:
                            resolved = sm.get_session_id_from_fragment(session_id)
                            if resolved:
                                session = sm.load(resolved)

                    core = WispAgentCore(config=config, session=session)
                    if session is not None and session.messages:
                        core.messages = list(session.messages)
                    conn.transport = ServerTransport(core, conn.send)
                    if session is not None:
                        session_id = session.id

                    # Run agent as async task
                    async def _run():
                        try:
                            await conn.transport.run(prompt)
                        except Exception as e:
                            logger.error("Agent error for %s: %s", client_id, e)
                            await conn.send({"type": "error", "message": str(e)})
                        finally:
                            sid = core.session.id if core.session else (session_id or "")
                            if config.plan_mode:
                                plan_content = ""
                                for m in reversed(core.messages):
                                    if m.get("role") == "assistant" and m.get("content"):
                                        plan_content = m["content"]
                                        break
                                await conn.send({"type": "plan_ready", "session_id": sid, "content": plan_content})
                            else:
                                await conn.send({"type": "complete", "session_id": sid})

                    conn.agent_task = asyncio.create_task(_run())

            elif msg_type == "tool_approval":
                call_id = msg.get("id")
                approved = msg.get("approved", False)
                reason = msg.get("reason")
                if conn.transport:
                    await conn.transport.approve_tool(call_id, approved, reason)
                else:
                    await conn.send({"type": "error", "message": "No active agent"})

            elif msg_type == "interrupt":
                if conn.transport:
                    conn.transport.interrupt()
                if conn.agent_task and not conn.agent_task.done():
                    conn.agent_task.cancel()
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

def main(host: str = "0.0.0.0", port: int = 8000, no_auth: bool = False):
    import uvicorn

    if no_auth:
        global API_KEY
        API_KEY = ""

        # HTTP: bypass api key check
        async def _noop_auth(x_api_key: str = Query(default="", alias="api-key")):
            return x_api_key
        app.dependency_overrides[verify_api_key] = _noop_auth

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
