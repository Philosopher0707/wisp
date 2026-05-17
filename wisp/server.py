"""Wisp Cloud Server — FastAPI + WebSocket for remote clients.

Uses WispAgentCore + ServerTransport for event-driven agent execution.

Run:
    wisp server --host 0.0.0.0 --port 8000

Environment:
    WISP_API_KEY                Pre-shared key for client auth (required)
    WISP_WORKSPACE              Root workspace directory (default: ./workspace)
    OLLAMA_HOST                 Ollama URL (default: http://localhost:11434)
    WISP_CORS_ORIGINS           Comma-separated allowed CORS origins (default: localhost dev)
    WISP_RATE_LIMIT_RPS         Requests per second cap for expensive endpoints (default: 10)
    WISP_HEADLESS_AUTO_APPROVE  Set to "1" to allow /api/prompt to auto-approve write tools (DANGEROUS)

Security note:
    The /api/prompt endpoint defaults to permission_mode="auto_edit" and does NOT
    auto-approve destructive tools. If you set WISP_HEADLESS_AUTO_APPROVE=1, any
    client with the API key can execute arbitrary bash commands without confirmation.
    Only enable this in isolated CI environments with short-lived API keys.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import sys
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# ── Structured logging ────────────────────────────────────────────
if os.environ.get("WISP_JSON_LOGS") == "1":
    class _JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            payload = {
                "time": self.formatTime(record),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
                "module": record.module,
            }
            if record.exc_info:
                payload["exception"] = self.formatException(record.exc_info)
            return json.dumps(payload)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logging.root.handlers.clear()
    logging.root.addHandler(handler)
    logging.root.setLevel(logging.INFO)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Query, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from wisp.config import WispConfig
# SwarmOrchestrator is imported lazily — the swarm subsystem may be unavailable
# from wisp.multi_agent.orchestrator import SwarmOrchestrator
from wisp.multi_agent.roles import AgentRole
from wisp.transport.server import create_swarm_progress_callback
from wisp.app_server import WispAppServer
from wisp.runtime_protocol import JsonRpcRequest

DEFAULT_SWARM_ROLES = [AgentRole.CODER, AgentRole.REVIEWER, AgentRole.TESTER, AgentRole.RESEARCHER]

from wisp.config import WispConfig
from wisp.core.agent import WispAgentCore
from wisp.core.events import (
    AgentEvent,
    TYPE_CONTENT,
    TYPE_THINKING,
    TYPE_TOOL_CALL,
    TYPE_TOOL_RESULT,
    TYPE_ERROR,
    TYPE_DONE,
    TYPE_APPROVAL_REQUEST,
)
from wisp.transport.server import ServerTransport
from wisp.session_store import get_store
from wisp.persistence.swarm_store import SwarmStateStore

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────

API_KEY = os.environ.get("WISP_API_KEY", "")
WORKSPACE_ROOT = Path(os.environ.get("WISP_WORKSPACE", "./workspace")).resolve()
MAX_BASH_OUTPUT = 50_000  # chars

if not API_KEY:
    API_KEY = secrets.token_urlsafe(32)
    logger.warning("WISP_API_KEY not set — generated temporary key: %s", API_KEY)

WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

# ── CORS Configuration ──────────────────────────────────────────────────
_cors_raw = os.environ.get("WISP_CORS_ORIGINS", "")
if _cors_raw:
    CORS_ORIGINS = [o.strip() for o in _cors_raw.split(",") if o.strip()]
else:
    CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]

# ── Pydantic models ──────────────────────────────────────────────────

class PromptRequest(BaseModel):
    prompt: str
    model: Optional[str] = None
    session_id: Optional[str] = None
    skill: Optional[str] = None
    permission_mode: str = Field(default="auto_edit", description="full | ask_all | auto_edit | read_only")
    images: list[str] = Field(default_factory=list, description="Base64 data URLs of images")

class BashRequest(BaseModel):
    command: str = Field(..., max_length=4096)
    cwd: Optional[str] = None

class FileWriteRequest(BaseModel):
    content: str

class FileEditRequest(BaseModel):
    old_text: str
    new_text: str

class DiffRequest(BaseModel):
    path: str
    new_content: str


class InlineEditRequest(BaseModel):
    path: str = Field(..., description="File path relative to workspace")
    selection: str = Field(..., min_length=1, description="Selected code to replace")
    instruction: str = Field(..., min_length=1, description="Natural language edit instruction")
    model: Optional[str] = None


class CompletionRequest(BaseModel):
    path: str = Field(default="", description="File path relative to workspace")
    file_content: str = Field(..., min_length=1, description="Full file content")
    cursor_line: int = Field(..., ge=0, description="0-based cursor line")
    cursor_char: int = Field(..., ge=0, description="0-based cursor character")
    language: str = Field(default="", description="Programming language")


class SemanticSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)


class BackgroundRunRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: Optional[str] = None
    permission_mode: str = Field(default="auto_edit")


class ArenaCompareRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    prompt: str = Field(..., min_length=1)
    task: str = Field(default="", max_length=200)
    model_a: str = Field(default="claude-sonnet-4-6")
    model_b: str = Field(default="claude-opus-4-7")


class ArenaVoteRequest(BaseModel):
    entry_id: str
    vote: str = Field(..., pattern="^(a|b|tie)$")

class WebSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=200)
    num_results: int = Field(default=5, ge=1, le=10)

class ToolApproval(BaseModel):
    call_id: str
    approved: bool
    reason: Optional[str] = None


class ContextUpdateRequest(BaseModel):
    content: str = Field(..., min_length=1, description="Content for .wisp/rules.md")


class SwarmRunRequest(BaseModel):
    goal: str = Field(..., min_length=1, description="High-level task description")
    roles: list[str] = Field(default=[], description="Agent roles to spawn")
    count_per_role: Optional[dict[str, int]] = None
    model: Optional[str] = None
    max_retries: int = Field(default=2, ge=0, le=5)
    max_parallel: int = Field(default=3, ge=1, le=10)


class PRReviewRequest(BaseModel):
    pr_number: Optional[int] = None
    base_branch: str = Field(default="main", description="Base branch for diffing")
    head_branch: Optional[str] = None
    repo: Optional[str] = None
    model: Optional[str] = None


class DiffReviewRequest(BaseModel):
    target: str = Field(default="uncommitted", description="uncommitted | staged | commit SHA")


class BestOfNRequest(BaseModel):
    models: list[str] = Field(..., min_items=2, max_items=4, description="Models to run in parallel")
    n: int = Field(default=2, ge=2, le=4, description="Number of parallel reviews")
    prompt: Optional[str] = None


class PluginInstallRequest(BaseModel):
    path: str = Field(..., min_length=1, description="Local path to plugin directory")


class PluginToggleRequest(BaseModel):
    enable: bool = Field(..., description="True to enable, False to disable")


class MCPServerAddRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Unique server name")
    command: Optional[str] = Field(default=None, description="Command for stdio transport")
    args: list[str] = Field(default_factory=list, description="Arguments for stdio transport")
    url: Optional[str] = Field(default=None, description="URL for HTTP/SSE transport")
    transport: str = Field(default="stdio", description="stdio | sse | streamable-http")
    always_load: bool = Field(default=False, description="Auto-connect on agent start")
    auth: str = Field(default="none", description="none | bearer_token | oauth_client_credentials | x509_certificate")
    auth_config: Optional[dict[str, Any]] = Field(default=None, description="Auth-specific configuration")
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    headers: Optional[dict[str, str]] = Field(default=None, description="Extra HTTP headers")
    disabled_tools: Optional[list[str]] = Field(default=None, description="Tools to exclude")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables")


class HookCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Hook name")
    event: str = Field(..., description="PRE_TOOL_USE | POST_TOOL_USE | PRE_BASH | POST_BASH | PRE_FILE_WRITE | SESSION_START | SESSION_END")
    command: str = Field(..., min_length=1, description="Shell command or script path")
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    enabled: bool = Field(default=True)
    matcher: Optional[str] = Field(default=None, description="Regex pattern for tool name matching")
    working_dir: Optional[str] = Field(default=None, description="Working directory for the hook subprocess")


# ── Auth ─────────────────────────────────────────────────────────────

async def verify_api_key(
    x_api_key: str | None = Query(None, alias="api-key"),
    x_api_key_header: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
):
    if authorization and authorization.lower().startswith("bearer "):
        auth_key = authorization[7:]
        if auth_key == API_KEY:
            return auth_key
    if x_api_key and x_api_key == API_KEY:
        return x_api_key
    if x_api_key_header and x_api_key_header == API_KEY:
        return x_api_key_header
    raise HTTPException(status_code=401, detail="Invalid or missing API key")

class RateLimiter:
    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    async def __call__(self, request: Request):
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        async with self._lock:
            requests = self._requests.get(client_ip, [])
            requests = [t for t in requests if now - t < self.window_seconds]
            if len(requests) >= self.max_requests:
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            requests.append(now)
            self._requests[client_ip] = requests


_rate_limit_rps = int(os.environ.get("WISP_RATE_LIMIT_RPS", "10"))
RATE_LIMITER = RateLimiter(max_requests=_rate_limit_rps * 60, window_seconds=60)

# ── App lifecycle ────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Wisp Cloud Server starting...")
    yield
    logger.info("Wisp Cloud Server shutting down...")

app = FastAPI(title="Wisp Cloud", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
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
        self.swarm_task: Optional[asyncio.Task] = None
        self.swarm_orchestrator: Any = None

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
    """Resolve a path relative to WORKSPACE_ROOT, with security boundary enforcement.

    Uses os.path.realpath to follow symlinks and verify the resolved path
    is physically within the workspace directory. This prevents symlink
    escapes where a link inside the workspace points outside it.

    Returns the resolved absolute Path if it's within the workspace.
    Raises HTTPException on path traversal or symlink escape attempts.
    """
    real_ws = os.path.realpath(str(WORKSPACE_ROOT))
    target = WORKSPACE_ROOT / path
    real_target = os.path.realpath(str(target))

    # Exact match (e.g., path is "." or the workspace itself)
    if real_target == real_ws:
        return Path(real_target)

    # Prefix check: target must be inside workspace, not a sibling
    prefix = real_ws if real_ws.endswith(os.sep) else real_ws + os.sep
    if not real_target.startswith(prefix):
        raise HTTPException(status_code=400, detail="Path traversal blocked")
    return Path(real_target)

# ── MemoryTransport ────────────────────────────────────────────────────


class MemoryTransport:
    """Minimal in-memory event collector for headless/CI agent execution.

    Collects all :class:`AgentEvent` instances yielded by the agent core
    into structured result dicts -- no WebSocket, no stdout, no blocking.
    """

    def __init__(self, permission_mode: str = "full"):
        self.events: list[AgentEvent] = []
        self.content_parts: list[str] = []
        self.thinking_parts: list[str] = []
        self.tool_calls: list[dict] = []
        self.errors: list[dict] = []
        self.permission_mode = permission_mode
        self.session_id: str = ""
        self.iterations: int = 0

    async def approval_handler(self, name: str, args: dict, reason: str) -> tuple[bool, Optional[dict]]:
        """Headless approval handler.

        In headless mode there is no user to ask, so all tools that reach
        this handler are denied unless permission_mode is ``full``.

        The real enforcement matrix lives in ``ToolExecutor``:
        - ``_check_permission_mode()`` — hard blocks (read_only blocks all writes)
        - ``_needs_forced_approval()`` — forces certain tools through this
          handler even when ``auto_approve=True`` (auto_edit→bash/git,
          ask_all→all writes)
        """
        if self.permission_mode == "full":
            return (True, None)
        return (False, None)

    def collect(self, event: AgentEvent) -> None:
        """Ingest a single event."""
        self.events.append(event)

        if event.type == TYPE_CONTENT:
            self.content_parts.append(event.text)
        elif event.type == TYPE_THINKING:
            self.thinking_parts.append(event.text)
        elif event.type == TYPE_TOOL_CALL:
            self.tool_calls.append({
                "name": event.data.get("name", ""),
                "args": event.data.get("arguments", {}),
            })
        elif event.type == TYPE_TOOL_RESULT:
            # Attach result to the last tool call (matched by name)
            name = event.data.get("name", "")
            duration = event.data.get("duration_ms")
            for tc in reversed(self.tool_calls):
                if tc["name"] == name and "result" not in tc:
                    tc["result"] = event.data.get("result", "")
                    if duration is not None:
                        tc["duration_ms"] = duration
                    break
        elif event.type == TYPE_ERROR:
            self.errors.append({
                "message": event.data.get("message", ""),
                "recoverable": event.data.get("recoverable", True),
            })
        elif event.type == TYPE_DONE:
            self.session_id = event.data.get("session_id", "")
            self.iterations = event.data.get("turns", 0)

    def to_result(self, extra_files_changed: Optional[list[str]] = None) -> dict:
        """Build the final structured response dict."""
        content = "\n".join(self.content_parts)
        thinking = "\n".join(self.thinking_parts) if self.thinking_parts else ""
        # Clean tool_calls: remove pending entries that never got a result
        resolved_calls = [tc for tc in self.tool_calls if "result" in tc]

        result: dict = {
            "ok": len(self.errors) == 0,
            "session_id": self.session_id,
            "content": content,
            "thinking": thinking,
            "tool_calls": resolved_calls,
            "files_changed": extra_files_changed or [],
            "iterations": self.iterations,
            "errors": self.errors if self.errors else None,
        }
        return result


async def _run_agent_headless(
    prompt: str,
    model: Optional[str] = None,
    session_id: Optional[str] = None,
    skill: Optional[str] = None,
    permission_mode: str = "full",
    images: Optional[list[str]] = None,
) -> dict:
    """Run the agent synchronously in memory and return a structured result."""
    start = time.time()
    config = WispConfig()
    if model:
        config.model = model
    config.workspace = str(WORKSPACE_ROOT)
    # Headless mode defaults to NOT auto-approving destructive tools.
    # CI pipelines can opt in via WISP_HEADLESS_AUTO_APPROVE=1.
    config.auto_approve = os.environ.get("WISP_HEADLESS_AUTO_APPROVE", "") == "1"
    config.show_thinking = True
    config.permission_mode = permission_mode

    session = None
    if session_id:
        sm = get_store()
        session = sm.load(session_id)
        if session is None:
            resolved = sm.resolve_session_id(session_id)
            if resolved:
                session = sm.load(resolved)

    core = WispAgentCore(config=config, session=session)
    if session is not None and session.messages:
        core.messages = list(session.messages)

    transport = MemoryTransport(permission_mode=permission_mode)

    # Pre-build system prompt if skill is specified
    system = core._build_system_prompt(skill_name=skill, workspace=config.workspace) if skill else None

    try:
        async for event in core.run(prompt, system=system, approval_handler=transport.approval_handler, images=images):
            transport.collect(event)
    except Exception as e:
        logger.error("Headless agent error: %s", e)
        transport.errors.append({"message": str(e), "recoverable": False})
    finally:
        core.close()

    result = transport.to_result()

    # Collect changed file paths from the change tracker
    try:
        changed = core.change_tracker.files_changed() if hasattr(core.change_tracker, 'files_changed') else []
        result["files_changed"] = changed
    except Exception:
        pass

    duration = (time.time() - start) * 1000
    result["duration_ms"] = round(duration)
    return result


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
    """List available Ollama models (local + cloud)."""
    import subprocess
    try:
        result = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if len(lines) > 1:
                models = [line.split()[0] for line in lines[1:] if line.strip()]
                return {"models": models}
    except Exception:
        pass

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
    sm = get_store()
    sessions = sm.list_sessions()
    # list_sessions returns list[dict]; strip non-serializable keys
    for s in sessions:
        s.pop("file", None)
    return {"sessions": sessions}


@app.get("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
async def get_session(session_id: str):
    sm = get_store()
    session = sm.load(session_id)
    if session is None:
        resolved = sm.resolve_session_id(session_id)
        if resolved:
            session = sm.load(resolved)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session": session.to_dict()}


@app.delete("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
async def delete_session(session_id: str):
    sm = get_store()
    ok = sm.delete(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


class RenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


@app.patch("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
async def rename_session(session_id: str, req: RenameRequest):
    sm = get_store()
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

    sm = get_store()
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


@app.post("/api/edit/inline", dependencies=[Depends(verify_api_key)])
async def inline_edit(req: InlineEditRequest):
    """Inline edit — replace a selection based on natural language instruction.

    Accepts a file path, the selected code, and an instruction. Runs a
    single-turn agent call to generate the replacement, then returns the
    new text and a diff.
    """
    target = _resolve_path(req.path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    try:
        file_content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")

    if req.selection not in file_content:
        raise HTTPException(status_code=400, detail="Selection not found in file")

    edit_prompt = f"""Rewrite the selected code according to the instruction.

## File: {req.path}
```{file_content[:8000]}
```

## Selected code:
```{req.selection}
```

## Instruction:
{req.instruction}

Return ONLY the replacement code for the selection. No explanation, no markdown fences.
The replacement must be valid code that can directly substitute the selection."""

    result = await _run_agent_headless(
        prompt=edit_prompt,
        model=req.model,
        permission_mode="read_only",
    )

    new_text = result.get("content", "").strip()
    # Strip markdown fences if model included them
    if new_text.startswith("```") and new_text.endswith("```"):
        new_text = "\n".join(new_text.split("\n")[1:-1])
        new_text = new_text.strip()

    new_file_content = file_content.replace(req.selection, new_text, 1)
    from wisp.diff import generate_diff_string
    diff_result = generate_diff_string(file_content, new_file_content)

    return {
        "ok": True,
        "path": req.path,
        "new_text": new_text,
        "diff": diff_result.diff,
        "first_changed_line": diff_result.first_changed_line,
        "new_file_content": new_file_content,
    }


# ── Autocomplete ───────────────────────────────────────────────────────

@app.post("/api/complete", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def autocomplete(req: CompletionRequest):
    """Generate a code completion using the configured LLM provider.

    Accepts file content + cursor position, returns completion text.
    Uses low-temperature, fill-in-the-middle prompting.
    """
    from wisp.completion import generate_completion, CompletionRequest as CR

    config = WispConfig()
    config.workspace = str(WORKSPACE_ROOT)

    result = await generate_completion(
        CR(
            file_content=req.file_content,
            cursor_line=req.cursor_line,
            cursor_char=req.cursor_char,
            path=req.path,
            language=req.language,
        ),
        config,
    )
    return {"completion": result.text, "finish_reason": result.finish_reason}


# ── Semantic Codebase Search ───────────────────────────────────────────

# Module-level index instance (lazy init)
_semantic_index: Optional[object] = None


def _get_semantic_index():
    """Get or create the semantic index singleton."""
    global _semantic_index
    if _semantic_index is None:
        from wisp.semantic_index import SemanticIndex
        _semantic_index = SemanticIndex(str(WORKSPACE_ROOT))
    return _semantic_index


@app.get("/api/codebase/search", dependencies=[Depends(verify_api_key)])
async def search_codebase(q: str = Query(..., min_length=1, max_length=500),
                          n: int = Query(default=5, ge=1, le=20)):
    """Semantic search over the codebase. Returns top-N relevant code chunks."""
    index = _get_semantic_index()
    results = index.search(q, top_k=n)
    return {
        "query": q,
        "results": [
            {
                "file": r.file_path,
                "start_line": r.start_line,
                "end_line": r.end_line,
                "content": r.content,
                "symbol": r.symbol_name,
                "score": r.score,
            }
            for r in results
        ],
    }


@app.post("/api/codebase/index", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def reindex_codebase():
    """Trigger a full re-index of the workspace."""
    index = _get_semantic_index()
    stats = index.index_all()
    return {"ok": True, **stats}


@app.get("/api/codebase/stats", dependencies=[Depends(verify_api_key)])
async def codebase_stats():
    """Get semantic index statistics."""
    index = _get_semantic_index()
    return index.get_stats()


@app.post("/api/search", dependencies=[Depends(verify_api_key)])
async def web_search(req: WebSearchRequest):
    """Standalone web search — returns structured results with title/url/snippet."""
    from wisp.tools import tool_web_search
    import json as _json
    try:
        result = tool_web_search(req.query, req.num_results)
        return _json.loads(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Background Agents ──────────────────────────────────────────────────

@app.post("/api/run/background", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def start_background_run(req: BackgroundRunRequest):
    """Start an agent run in the background. Returns run ID for polling."""
    from wisp.background_agent import get_runner
    runner = get_runner(str(WORKSPACE_ROOT))

    model = req.model or os.environ.get("WISP_DEFAULT_MODEL", "claude-sonnet-4-6")
    run = runner.create(
        prompt=req.prompt,
        model=model,
        workspace=str(WORKSPACE_ROOT),
        permission_mode=req.permission_mode,
    )
    runner.start(run.id)
    return {"ok": True, "run_id": run.id, "status": "running"}


@app.get("/api/run/{run_id}", dependencies=[Depends(verify_api_key)])
async def get_background_run(run_id: str):
    """Get the status and results of a background agent run."""
    from wisp.background_agent import get_runner
    runner = get_runner(str(WORKSPACE_ROOT))
    run = runner.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.to_dict()


@app.get("/api/runs", dependencies=[Depends(verify_api_key)])
async def list_background_runs():
    """List all background agent runs."""
    from wisp.background_agent import get_runner
    runner = get_runner(str(WORKSPACE_ROOT))
    return {"runs": [r.to_dict() for r in runner.list_runs()[:20]]}


@app.delete("/api/run/{run_id}", dependencies=[Depends(verify_api_key)])
async def cancel_background_run(run_id: str):
    """Cancel a running background agent."""
    from wisp.background_agent import get_runner
    runner = get_runner(str(WORKSPACE_ROOT))
    ok = runner.cancel(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Run not found or not running")
    return {"ok": True}


# ── Swarm Helpers ─────────────────────────────────────────────────────

async def _launch_swarm_ws(
    conn: Connection,
    goal: str,
    roles: list[str],
    *,
    count_per_role: Optional[dict[str, int]] = None,
    max_retries: int = 2,
    max_parallel: int = 3,
    model: Optional[str] = None,
) -> None:
    """Create orchestrator, cancel prior swarm, and launch new one on conn."""
    config = WispConfig()
    if model:
        config.model = model
    config.workspace = str(WORKSPACE_ROOT)
    config.auto_approve = True

    try:
        from wisp.multi_agent.orchestrator import SwarmOrchestrator
    except ImportError:
        logger.error("Swarm subsystem unavailable: wisp.multi_agent.orchestrator module missing")
        await conn.send({"type": "error", "message": "Swarm feature not available"})
        return

    orch = SwarmOrchestrator(config, max_parallel=max_parallel)
    old_orch = conn.swarm_orchestrator
    conn.swarm_orchestrator = orch
    progress_cb = create_swarm_progress_callback(conn.send)

    # Cancel any existing swarm before starting new one
    if conn.swarm_task and not conn.swarm_task.done():
        if old_orch:
            old_orch.stop_all()
        conn.swarm_task.cancel()
        try:
            await asyncio.wait_for(conn.swarm_task, timeout=2)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    async def _run():
        try:
            result = await orch.arun(
                goal, roles=roles,
                count_per_role=count_per_role,
                max_retries=max_retries,
                progress_callback=progress_cb,
            )
            await conn.send({
                "type": "status",
                "message": f"Swarm done: {sum(1 for r in result.agent_results if r.success)}/{len(result.agent_results)} tasks passed",
                "level": "info" if result.success else "warn",
            })
        except Exception as e:
            logger.error("Swarm error for %s: %s", conn.client_id, e)
            await conn.send({"type": "error", "message": f"Swarm failed: {e}"})
        finally:
            conn.swarm_orchestrator = None
            conn.swarm_task = None

    conn.swarm_task = asyncio.create_task(_run())
    await conn.send({"type": "status", "message": f"Swarm starting: {goal[:100]}", "level": "info"})


# ── Swarm HTTP API ────────────────────────────────────────────────────

_SWARM_TTL_SECONDS = 600  # auto-evict finished runs after 10 minutes
# SwarmStateStore is a dict-like SQLite-backed store for multi-process safety
_swarm_store = SwarmStateStore(str(WORKSPACE_ROOT))
# Orchestrator objects are process-local (not serializable). Only the worker
# that starts a swarm keeps a handle for cancellation.
_swarm_orchestrators: dict[str, Any] = {}


@app.post("/api/swarm/run", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def swarm_run_api(req: SwarmRunRequest):
    """Run a multi-agent swarm asynchronously. Returns run_id for polling."""
    from wisp.multi_agent.task import OrchestratorEvent as SwarmEvent

    roles = req.roles or DEFAULT_SWARM_ROLES

    config = WispConfig()
    if req.model:
        config.model = req.model
    config.workspace = str(WORKSPACE_ROOT)
    config.auto_approve = True

    try:
        from wisp.multi_agent.orchestrator import SwarmOrchestrator
    except ImportError:
        return JSONResponse(
            status_code=503,
            content={"error": "Swarm subsystem unavailable"},
        )

    orch = SwarmOrchestrator(config, max_parallel=req.max_parallel)
    run_id = f"swarm-{secrets.token_hex(6)}"
    event_log: list[dict] = []

    async def collect_events(evt: SwarmEvent) -> None:
        ws_msg = evt.to_ws_message()
        entry = {
            "event_type": evt.event_type,
            "task_id": evt.task_id,
            "payload": evt.payload,
        }
        if ws_msg:
            entry["ws_message"] = ws_msg
        event_log.append(entry)

    # Store initial metadata (orchestrator is process-local, not serializable to SQLite)
    _swarm_store[run_id] = {
        "orchestrator": orch,
        "event_log": event_log,
        "goal": req.goal,
        "roles": roles,
        "start_time": time.monotonic(),
    }

    async def _run():
        try:
            await orch.arun(
                req.goal,
                roles=roles,
                count_per_role=req.count_per_role,
                max_retries=req.max_retries,
                progress_callback=collect_events,
            )
        except Exception as e:
            logger.error("Swarm run %s error: %s", run_id, e)
        finally:
            entry = _swarm_store.get(run_id)
            if entry:
                entry["finished"] = True
                entry["end_time"] = time.monotonic()

    asyncio.create_task(_run())
    return {"run_id": run_id, "status": "running", "roles": roles}


def _evict_stale_swarms() -> None:
    """Remove finished swarm runs older than TTL."""
    now = time.monotonic()
    stale = [
        rid for rid, e in _swarm_store.items()
        if e.get("finished") and (now - e.get("end_time", 0)) > _SWARM_TTL_SECONDS
    ]
    for rid in stale:
        del _swarm_store[rid]


@app.get("/api/swarm/status/{run_id}", dependencies=[Depends(verify_api_key)])
async def swarm_status_api(run_id: str):
    """Get status of a swarm run: agent list, counts, elapsed."""
    _evict_stale_swarms()
    entry = _swarm_store.get(run_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Swarm run not found")

    orch = entry["orchestrator"]
    registry_data = orch.registry.to_dict()
    elapsed = time.monotonic() - entry["start_time"]

    return {
        "run_id": run_id,
        "goal": entry["goal"],
        "roles": entry["roles"],
        "elapsed_seconds": round(elapsed, 1),
        "finished": entry.get("finished", False),
        "agents": registry_data["agents"],
        "total_agents": registry_data["total"],
        "active_agents": registry_data["active"],
    }


@app.get("/api/swarm/events/{run_id}", dependencies=[Depends(verify_api_key)])
async def swarm_events_api(run_id: str):
    """Get accumulated event log for a swarm run (for polling clients)."""
    entry = _swarm_store.get(run_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Swarm run not found")

    return {
        "run_id": run_id,
        "goal": entry["goal"],
        "finished": entry.get("finished", False),
        "events": list(entry["event_log"]),
    }


# ── Arena Mode ─────────────────────────────────────────────────────────

@app.post("/api/arena/compare", dependencies=[Depends(verify_api_key)])
async def arena_compare(req: ArenaCompareRequest):
    """Run a blind A/B comparison between two models.

    Runs the same prompt with both models, returns blind side-by-side
    results. Model identities are hidden until after voting.
    """
    from wisp.arena import get_arena, ArenaCompareRequest as AR

    arena = get_arena()
    entry = await arena.run_comparison(AR(
        prompt=req.prompt,
        task=req.task,
        model_a=req.model_a,
        model_b=req.model_b,
        workspace=str(WORKSPACE_ROOT),
    ))

    return {
        "entry_id": entry.id,
        "task": entry.task,
        "side_a": entry.to_blind_dict("a"),
        "side_b": entry.to_blind_dict("b"),
        "voted": False,
    }


@app.post("/api/arena/vote", dependencies=[Depends(verify_api_key)])
async def arena_vote(req: ArenaVoteRequest):
    """Vote on an arena comparison. Reveals model identities after voting."""
    from wisp.arena import get_arena

    arena = get_arena()
    entry = arena.vote(req.entry_id, req.vote)
    if not entry:
        raise HTTPException(status_code=404, detail="Arena entry not found")

    return {
        "entry_id": entry.id,
        "model_a": entry.model_a,
        "model_b": entry.model_b,
        "vote": entry.vote,
        "revealed": True,
    }


@app.get("/api/arena/leaderboard", dependencies=[Depends(verify_api_key)])
async def arena_leaderboard():
    """Get the per-project arena leaderboard."""
    from wisp.arena import get_arena

    arena = get_arena()
    lb = arena.get_leaderboard(str(WORKSPACE_ROOT))
    entries = [
        {
            "id": e.id,
            "task": e.task,
            "model_a": e.model_a,
            "model_b": e.model_b,
            "a_duration_ms": e.a_duration_ms,
            "b_duration_ms": e.b_duration_ms,
            "vote": e.vote,
            "created_at": e.created_at,
        }
        for e in arena.list_entries()[:10]
    ]
    return {"leaderboard": lb, "entries": entries}


@app.get("/api/arena/entries", dependencies=[Depends(verify_api_key)])
async def arena_entries():
    """List all arena comparison entries."""
    from wisp.arena import get_arena

    arena = get_arena()
    return {
        "entries": [
            {
                "id": e.id,
                "task": e.task,
                "model_a": e.model_a,
                "model_b": e.model_b,
                "vote": e.vote,
                "created_at": e.created_at,
            }
            for e in arena.list_entries()[:20]
        ],
    }


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



# ── Suggestion Routes ────────────────────────────────────────────────────

_app_suggestion_watcher = None


def _get_suggestion_watcher():
    global _app_suggestion_watcher
    if _app_suggestion_watcher is None:
        from wisp.suggestion_watcher import SuggestionWatcher
        _app_suggestion_watcher = SuggestionWatcher(str(WORKSPACE_ROOT))
    return _app_suggestion_watcher


@app.get("/api/diagnostics", dependencies=[Depends(verify_api_key)])
async def get_diagnostics(path: str):
    """Return LSP diagnostics for a specific file."""
    target = _resolve_path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        from wisp.lsp.manager import get_lsp_manager
        lsp = get_lsp_manager(str(WORKSPACE_ROOT))
        diags = lsp.get_diagnostics(str(target))
        return {"path": path, "diagnostics": diags, "count": len(diags)}
    except Exception as e:
        return {"path": path, "diagnostics": [], "count": 0, "error": str(e)}


@app.get("/api/suggestions", dependencies=[Depends(verify_api_key)])
async def get_suggestions():
    """Return files changed since last poll with diagnostic counts."""
    try:
        from wisp.lsp.manager import get_lsp_manager
        lsp = get_lsp_manager(str(WORKSPACE_ROOT))
        watcher = _get_suggestion_watcher()
        suggestions = watcher.get_suggestions(lsp)
        return {
            "suggestions": [
                {
                    "path": s.path,
                    "mtime": s.mtime,
                    "diagnostic_count": s.diagnostic_count,
                    "severities": s.severities,
                }
                for s in suggestions
            ]
        }
    except Exception as e:
        logger.warning("Failed to get suggestions: %s", e)
        return {"suggestions": []}


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


@app.post("/api/bash", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def run_bash(req: BashRequest):
    """Run a bash command inside the workspace. Restricted for safety."""
    from wisp.tools import check_dangerous_command

    # ── Input validation ──────────────────────────────────────
    if not req.command or not isinstance(req.command, str):
        raise HTTPException(status_code=400, detail="Command must be a non-empty string")
    if "\x00" in req.command:
        raise HTTPException(status_code=400, detail="Null bytes not allowed in command")
    if len(req.command) > 4096:
        raise HTTPException(status_code=400, detail="Command too long (max 4096 chars)")

    danger = check_dangerous_command(req.command)
    if danger:
        logger.warning("Dangerous bash blocked: %s", danger[:200])
        raise HTTPException(status_code=400, detail=f"Dangerous command blocked: {danger}")

    cwd = req.cwd or "."
    # Resolve cwd relative to workspace
    target_cwd = _resolve_path(cwd)
    if not target_cwd.is_dir():
        raise HTTPException(status_code=400, detail="Invalid cwd")

    from wisp.sandbox import get_sandbox
    sandbox = get_sandbox(str(WORKSPACE_ROOT))

    # ── Execution ──────────────────────────────────────
    start = time.time()
    try:
        timeout = int(os.environ.get("WISP_BASH_TIMEOUT", "60"))
        exit_code, stdout, stderr = await sandbox.run(
            req.command,
            cwd=cwd,
            timeout=timeout,
        )
        duration = round(time.time() - start, 3)

        # ANSI escape code stripping
        def _strip_ansi(text: str) -> str:
            import re
            return re.sub(r"\x1b\[[0-9;]*m", "", text)

        stdout = _strip_ansi(stdout[:MAX_BASH_OUTPUT])
        stderr = _strip_ansi(stderr[:MAX_BASH_OUTPUT])

        # Structured log entry
        logger.info(
            "bash_exec sandbox=%s exit=%d duration=%.3fs cmd_prefix=%s",
            sandbox.name,
            exit_code,
            duration,
            req.command[:100].replace("\n", "\\n"),
        )
        return {
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": False,
            "sandbox": sandbox.name,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("bash_exec failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sandbox/status", dependencies=[Depends(verify_api_key)])
async def sandbox_status():
    """Return current sandbox provider info."""
    from wisp.sandbox import get_sandbox
    sandbox = get_sandbox(str(WORKSPACE_ROOT))
    return {
        "type": sandbox.name,
        "available": sandbox.is_available(),
    }


# ── Headless / CI Prompt ──────────────────────────────────────────────

@app.post("/api/prompt", dependencies=[Depends(verify_api_key)])
async def prompt_sync(req: PromptRequest):
    """Non-interactive/headless prompt execution with JSON response.

    Runs the agent synchronously and returns the final result.
    No WebSocket needed. For CI/CD pipelines, scripting, and automation.

    Security:
        Defaults to permission_mode="auto_edit" and does NOT auto-approve
        destructive tools. Set WISP_HEADLESS_AUTO_APPROVE=1 to opt in to
        full auto-approval (dangerous — equivalent to remote shell access).
    """
    result = await _run_agent_headless(
        prompt=req.prompt,
        model=req.model,
        session_id=req.session_id,
        skill=req.skill,
        permission_mode=req.permission_mode,
        images=req.images if req.images else None,
    )
    if not result.get("ok"):
        return JSONResponse(status_code=500, content=result)
    return result


# ── Context File Endpoints ────────────────────────────────────────────

@app.get("/api/context", dependencies=[Depends(verify_api_key)])
async def get_context():
    """Get loaded project context for display in UI."""
    config = WispConfig()
    config.workspace = str(WORKSPACE_ROOT)
    content = config.load_context_files()
    files_found: list[str] = list(config._context_mtimes.keys()) if content else []
    return {
        "content": content,
        "files_found": files_found,
        "context_files_setting": config.context_files,
    }


@app.post("/api/context", dependencies=[Depends(verify_api_key)])
async def update_context(req: ContextUpdateRequest):
    """Update or create .wisp/rules.md with the provided content."""
    wisp_dir = WORKSPACE_ROOT / ".wisp"
    wisp_dir.mkdir(parents=True, exist_ok=True)
    rules_path = wisp_dir / "rules.md"
    try:
        rules_path.write_text(req.content, encoding="utf-8")
        logger.info("Updated %s (%d chars)", rules_path, len(req.content))
        return {
            "ok": True,
            "path": str(rules_path.relative_to(WORKSPACE_ROOT)),
            "bytes": len(req.content.encode("utf-8")),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── JSON-RPC Endpoint ──────────────────────────────────────────────────

_app_server = WispAppServer()


@app.post("/api/jsonrpc", dependencies=[Depends(verify_api_key)])
async def jsonrpc_handler(request: dict):
    """JSON-RPC 2.0 endpoint for app-style clients."""
    rpc_request = JsonRpcRequest.from_dict(request)
    config = WispConfig()
    config.workspace = str(WORKSPACE_ROOT)
    response = await _app_server.handle_request(rpc_request, config=config)
    return response.to_dict()


# ── Plugin Management Endpoints ────────────────────────────────────────

_plugin_registry = None


def _get_plugin_registry():
    global _plugin_registry
    if _plugin_registry is None:
        from wisp.plugins.registry import PluginRegistry
        _plugin_registry = PluginRegistry()
    return _plugin_registry


@app.get("/api/plugins", dependencies=[Depends(verify_api_key)])
async def list_plugins():
    registry = _get_plugin_registry()
    installed = registry.list_installed()
    state = registry._read_state()
    return {
        "plugins": [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "author": p.author,
                "license": p.license,
                "namespace": p.namespace,
                "enabled": state.get(p.name, {}).get("enabled", True),
                "installed_at": state.get(p.name, {}).get("installed_at"),
            }
            for p in installed
        ]
    }


@app.post("/api/plugins/install", dependencies=[Depends(verify_api_key)])
async def install_plugin(req: PluginInstallRequest):
    from pathlib import Path
    registry = _get_plugin_registry()
    plugin_path = Path(req.path).expanduser().resolve()
    if not plugin_path.exists():
        raise HTTPException(status_code=404, detail=f"Plugin path not found: {req.path}")
    if not plugin_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Plugin path is not a directory: {req.path}")
    try:
        manifest = registry.install(plugin_path)
        return {
            "ok": True,
            "plugin": {
                "name": manifest.name,
                "version": manifest.version,
                "description": manifest.description,
                "namespace": manifest.namespace,
            },
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Plugin install failed")
        raise HTTPException(status_code=500, detail=f"Install failed: {e}")


@app.delete("/api/plugins/{name}", dependencies=[Depends(verify_api_key)])
async def delete_plugin(name: str):
    registry = _get_plugin_registry()
    if not registry.get(name):
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not installed")
    ok = registry.uninstall(name)
    if not ok:
        raise HTTPException(status_code=500, detail=f"Failed to uninstall plugin '{name}'")
    return {"ok": True, "message": f"Plugin '{name}' uninstalled"}


@app.post("/api/plugins/{name}/toggle", dependencies=[Depends(verify_api_key)])
async def toggle_plugin(name: str, req: PluginToggleRequest):
    registry = _get_plugin_registry()
    if not registry.get(name):
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not installed")
    if req.enable:
        registry.enable(name)
    else:
        registry.disable(name)
    return {"ok": True, "plugin": name, "enabled": req.enable}


@app.get("/api/plugins/marketplace", dependencies=[Depends(verify_api_key)])
async def plugin_marketplace():
    return {
        "plugins": [],
        "message": "Marketplace not yet available. Install plugins locally via POST /api/plugins/install",
    }


# ── MCP Management Endpoints ───────────────────────────────────────────

_mcp_manager = None


def _get_mcp_manager():
    global _mcp_manager
    if _mcp_manager is None:
        from wisp.mcp import MCPManager
        _mcp_manager = MCPManager(str(WORKSPACE_ROOT))
    return _mcp_manager


@app.get("/api/mcp/servers", dependencies=[Depends(verify_api_key)])
async def list_mcp_servers():
    manager = _get_mcp_manager()
    configs = manager.load_server_configs()
    return {
        "servers": [
            {
                "name": c.name,
                "command": c.command,
                "args": c.args,
                "url": c.url,
                "transport": c.transport,
                "always_load": c.always_load,
                "auth": c.auth.value,
                "timeout_seconds": c.timeout_seconds,
                "headers": c.headers,
                "disabled_tools": c.disabled_tools,
                "env": c.env,
            }
            for c in configs
        ]
    }


@app.post("/api/mcp/servers", dependencies=[Depends(verify_api_key)])
async def add_mcp_server(req: MCPServerAddRequest):
    from wisp.mcp import MCPAuthMethod, MCPServerConfig
    manager = _get_mcp_manager()
    configs = manager.load_server_configs()
    existing = [c for c in configs if c.name == req.name]
    if existing:
        raise HTTPException(status_code=409, detail=f"MCP server '{req.name}' already exists")

    try:
        auth_method = MCPAuthMethod(req.auth)
    except ValueError:
        auth_method = MCPAuthMethod.NONE

    config = MCPServerConfig(
        name=req.name,
        command=req.command,
        args=req.args,
        url=req.url,
        env=req.env,
        transport=req.transport,
        always_load=req.always_load,
        auth=auth_method,
        auth_config=req.auth_config,
        timeout_seconds=req.timeout_seconds,
        headers=req.headers,
        disabled_tools=req.disabled_tools,
    )

    manager._server_configs[req.name] = config
    manager.save_server_configs()

    # Optionally connect if always_load is set
    if req.always_load:
        try:
            from wisp.mcp import connect_server
            server = connect_server(config)
            manager.servers.append(server)
        except Exception as e:
            logger.warning("Failed to connect MCP server '%s' during add: %s", req.name, e)

    return {"ok": True, "server": {"name": req.name, "transport": req.transport}}


@app.delete("/api/mcp/servers/{name}", dependencies=[Depends(verify_api_key)])
async def delete_mcp_server(name: str):
    manager = _get_mcp_manager()
    configs = manager.load_server_configs()
    if not any(c.name == name for c in configs):
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")

    # Disconnect if connected
    for server in list(manager.servers):
        if server.config.name == name:
            try:
                from wisp.mcp import disconnect_server
                disconnect_server(server)
            except Exception as e:
                logger.warning("Error disconnecting MCP server '%s': %s", name, e)
            manager.servers.remove(server)

    manager._server_configs.pop(name, None)
    manager.save_server_configs()
    return {"ok": True, "message": f"MCP server '{name}' deleted"}


@app.post("/api/mcp/servers/{name}/test", dependencies=[Depends(verify_api_key)])
async def test_mcp_server(name: str):
    manager = _get_mcp_manager()
    configs = manager.load_server_configs()
    if not any(c.name == name for c in configs):
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")

    result = await manager.health_check(name)
    return {"ok": result["status"] == "ok", "health": result}


# ── Hook Management Endpoints ──────────────────────────────────────────


@app.get("/api/hooks", dependencies=[Depends(verify_api_key)])
async def list_hooks():
    from wisp.hooks import HookManager
    manager = HookManager(workspace=WORKSPACE_ROOT)
    manager.load_project_hooks()
    hooks = manager.list_hooks()
    return {
        "hooks": [
            {
                "name": h.name,
                "event": h.event.value if hasattr(h.event, "value") else str(h.event),
                "command": h.command,
                "timeout_seconds": h.timeout_seconds,
                "enabled": h.enabled,
                "matcher": h.matcher,
                "working_dir": h.working_dir,
            }
            for h in hooks
        ]
    }


@app.post("/api/hooks", dependencies=[Depends(verify_api_key)])
async def create_hook(req: HookCreateRequest):
    from wisp.hooks import HookConfig, HookEvent
    import json as _json

    hooks_dir = WORKSPACE_ROOT / ".wisp" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    try:
        event = HookEvent[req.event.upper()]
    except KeyError:
        valid = [e.name for e in HookEvent]
        raise HTTPException(status_code=400, detail=f"Invalid event '{req.event}'. Must be one of: {', '.join(valid)}")

    hook = HookConfig(
        name=req.name,
        event=event,
        command=req.command,
        timeout_seconds=req.timeout_seconds,
        enabled=req.enabled,
        matcher=req.matcher,
        working_dir=req.working_dir,
    )

    hook_file = hooks_dir / f"{req.name}.json"
    hook_file.write_text(_json.dumps(hook.to_dict(), indent=2) + "\n", encoding="utf-8")

    return {"ok": True, "hook": hook.to_dict()}


@app.delete("/api/hooks/{name}", dependencies=[Depends(verify_api_key)])
async def delete_hook(name: str):
    hooks_dir = WORKSPACE_ROOT / ".wisp" / "hooks"
    hook_file = hooks_dir / f"{name}.json"
    if not hook_file.exists():
        raise HTTPException(status_code=404, detail=f"Hook '{name}' not found")
    hook_file.unlink()
    return {"ok": True, "message": f"Hook '{name}' deleted"}


@app.post("/api/hooks/{name}/test", dependencies=[Depends(verify_api_key)])
async def test_hook(name: str, request: dict):
    from wisp.hooks import HookManager, HookEvent, build_hook_context
    import asyncio

    manager = HookManager(workspace=WORKSPACE_ROOT)
    manager.load_project_hooks()
    hook = manager.get_hook(name)
    if hook is None:
        raise HTTPException(status_code=404, detail=f"Hook '{name}' not found")

    # Build a synthetic context for testing
    event_type = hook.event
    tool_name = request.get("tool_name", "run_bash")
    tool_args = request.get("tool_args", {"command": "echo 'hook test'"})

    context = build_hook_context(
        event=event_type,
        tool_name=tool_name,
        tool_args=tool_args,
        workspace=str(WORKSPACE_ROOT),
        session_id="test-session",
    )

    try:
        results = await manager.run_hooks(event_type, context)
        return {
            "ok": True,
            "hook": name,
            "results": [r.to_dict() for r in results],
        }
    except Exception as e:
        logger.exception("Hook test failed")
        raise HTTPException(status_code=500, detail=f"Hook test failed: {e}")


@app.get("/api/hooks/logs", dependencies=[Depends(verify_api_key)])
async def hook_logs():
    return {"logs": [], "message": "Hook execution logging not yet implemented"}


# ── PR Review Endpoints ───────────────────────────────────────────────

@app.post("/api/review/pr", dependencies=[Depends(verify_api_key)])
async def review_pr(req: PRReviewRequest):
    """Review a PR by diffing base vs head and running the agent.

    Returns a structured review with summary, issues, and an approval recommendation.
    """
    git_dir = WORKSPACE_ROOT / ".git"
    if not git_dir.exists():
        raise HTTPException(status_code=400, detail="No git repository in workspace")

    import subprocess

    # Determine the diff target
    base = req.base_branch
    head = req.head_branch

    if req.pr_number is not None and not head:
        head = f"pull/{req.pr_number}/head"
    elif not head:
        raise HTTPException(status_code=400, detail="Either pr_number or head_branch is required")

    try:
        proc = subprocess.run(
            ["git", "diff", f"{base}...{head}"],
            cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"git diff failed: {proc.stderr}")
        diff_text = proc.stdout
        if not diff_text.strip():
            return {"summary": "No changes to review.", "issues": [], "approval": "approve", "files_reviewed": []}
    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="git diff timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Collect changed files from diff
    files_reviewed = _extract_files_from_diff(diff_text)

    review_prompt = f"""Review the following code diff. Find bugs, security issues, style problems,
and suggest improvements. Be concise and actionable.

## Git diff ({base}...{head})
```diff
{diff_text[:15000]}
```

## Instructions
1. Identify critical bugs (logic errors, data corruption, security vulnerabilities)
2. Identify warnings (code smells, performance issues, missing error handling)
3. Identify informational notes (style improvements, better patterns)
4. Provide a final verdict: approve, request_changes, or comment

## Response format
Return your review as JSON:
```json
{{
  "summary": "1-2 sentence overview",
  "issues": [
    {{"severity": "critical|warning|info", "file": "path", "line": int, "message": "...", "suggestion": "..."}}
  ],
  "approval": "approve|request_changes|comment",
  "files_reviewed": ["path1", "path2"]
}}
```
"""

    result = await _run_agent_headless(
        prompt=review_prompt,
        model=req.model if hasattr(req, 'model') else None,
        permission_mode="read_only",
    )

    # Try to parse JSON from the agent response
    try:
        content = result.get("content", "")
        json_match = _extract_json(content)
        if json_match:
            parsed = json.loads(json_match)
            result["review"] = parsed
    except Exception:
        logger.warning("Could not parse structured review from agent output")

    result["files_reviewed"] = files_reviewed
    return result


@app.post("/api/review/diff", dependencies=[Depends(verify_api_key)])
async def review_diff(req: DiffReviewRequest):
    """Review uncommitted changes, staged changes, or a specific commit diff."""
    git_dir = WORKSPACE_ROOT / ".git"
    if not git_dir.exists():
        raise HTTPException(status_code=400, detail="No git repository in workspace")

    import subprocess

    diff_text = ""
    target_desc = req.target

    try:
        if req.target == "uncommitted":
            proc = subprocess.run(
                ["git", "diff"],
                cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=30,
            )
            diff_text = proc.stdout
        elif req.target == "staged":
            proc = subprocess.run(
                ["git", "diff", "--staged"],
                cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=30,
            )
            diff_text = proc.stdout
        elif req.target:
            # Treat as commit SHA
            proc = subprocess.run(
                ["git", "diff", f"{req.target}^!"],
                cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=30,
            )
            diff_text = proc.stdout
            if not diff_text.strip():
                # Try diff against parent
                proc = subprocess.run(
                    ["git", "show", req.target],
                    cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=30,
                )
                diff_text = proc.stdout

        if not diff_text.strip():
            return {"summary": "No changes to review.", "issues": [], "files_reviewed": []}

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="git command timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    files_reviewed = _extract_files_from_diff(diff_text)

    review_prompt = f"""Review the following code diff. Find bugs, security issues, style problems,
and suggest improvements. Be concise and actionable.

## Diff ({target_desc})
```diff
{diff_text[:15000]}
```

## Response format
Return your review as JSON:
```json
{{
  "summary": "1-2 sentence overview",
  "issues": [
    {{"severity": "critical|warning|info", "file": "path", "line": int, "message": "...", "suggestion": "..."}}
  ],
  "approval": "approve|request_changes|comment"
}}
```
"""

    result = await _run_agent_headless(
        prompt=review_prompt,
        permission_mode="read_only",
    )

    try:
        content = result.get("content", "")
        json_match = _extract_json(content)
        if json_match:
            parsed = json.loads(json_match)
            result["review"] = parsed
    except Exception:
        logger.warning("Could not parse structured review from agent output")

    result["files_reviewed"] = files_reviewed
    return result


@app.post("/api/review/best-of-n", dependencies=[Depends(verify_api_key)])
async def best_of_n(req: BestOfNRequest):
    """Run N parallel reviews with different models and compare results.

    Each model reviews the same diff independently. Results are returned
    side-by-side for comparison.
    """
    git_dir = WORKSPACE_ROOT / ".git"
    if not git_dir.exists():
        raise HTTPException(status_code=400, detail="No git repository in workspace")

    import subprocess

    try:
        proc = subprocess.run(
            ["git", "diff"],
            cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=30,
        )
        diff_text = proc.stdout
        if not diff_text.strip():
            return {"diff": "", "reviews": [], "message": "No changes to review."}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="git diff timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    files_reviewed = _extract_files_from_diff(diff_text)

    review_prompt = req.prompt or f"""Review the following code diff. Find bugs, security issues, style problems,
and suggest improvements.

```diff
{diff_text[:10000]}
```

Be concise and return a JSON object with: summary, issues (list of {{severity, file, line, message, suggestion}}), and approval.
"""

    async def _review_with_model(model: str) -> dict:
        try:
            result = await _run_agent_headless(
                prompt=review_prompt,
                model=model,
                permission_mode="read_only",
            )
            content = result.get("content", "")
            json_match = _extract_json(content)
            if json_match:
                try:
                    result["review"] = json.loads(json_match)
                except json.JSONDecodeError:
                    result["review"] = {"raw_content": content[:2000]}
            else:
                result["review"] = {"raw_content": content[:2000]}
            return result
        except Exception as e:
            return {"model": model, "ok": False, "error": str(e)}

    # Run up to N reviews in parallel
    models_to_use = req.models[:req.n]
    tasks = [_review_with_model(m) for m in models_to_use]
    reviews = await asyncio.gather(*tasks)

    return {
        "diff": diff_text[:5000],
        "files_reviewed": files_reviewed,
        "models_used": models_to_use,
        "reviews": reviews,
    }


def _extract_files_from_diff(diff_text: str) -> list[str]:
    """Extract unique file paths from a git diff output."""
    files: list[str] = []
    for line in diff_text.split("\n"):
        if line.startswith("+++ ") and line != "+++ /dev/null":
            # Strip the b/ prefix
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            if path and path not in files:
                files.append(path)
    return files


def _extract_json(text: str) -> Optional[str]:
    """Extract a JSON object from text that may contain surrounding content."""
    import re
    # Find content between ```json ... ``` or just { ... }
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        return fenced.group(1).strip()
    # Find outermost braces
    brace_start = text.find("{")
    if brace_start == -1:
        return None
    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[brace_start:i + 1]
    return None


# ── WebSocket Agent ──────────────────────────────────────────────────

@app.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket, api_key: str = Query(default="")):
    authenticated = False
    # Immediate auth via query param (backward compat)
    if api_key and API_KEY and api_key == API_KEY:
        authenticated = True
    elif not API_KEY:
        authenticated = True  # open mode

    if API_KEY and not api_key:
        # Allow first-message auth — don't reject yet
        authenticated = False
    elif API_KEY and api_key and api_key != API_KEY:
        await websocket.close(code=4001, reason="Invalid API key")
        return

    client_id = f"{websocket.client.host}:{websocket.client.port}"
    conn = await manager.connect(websocket, client_id)
    _first_message = True

    # WebSocket idle timeout: close after 10 minutes of no messages
    _WS_IDLE_TIMEOUT = 600.0

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=_WS_IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                logger.info("WebSocket idle timeout — closing %s", client_id)
                await conn.send({"type": "error", "message": "Connection closed after 10 minutes of inactivity"})
                await websocket.close(code=4001, reason="Idle timeout")
                return
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await conn.send({"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = msg.get("type")

            # First-message auth via `type: 'auth'` (desktop sends this after onopen)
            if msg_type == "auth" and not authenticated:
                auth_key = msg.get("api_key", "")
                if API_KEY and auth_key == API_KEY:
                    authenticated = True
                elif API_KEY:
                    await conn.send({"type": "error", "message": "Invalid API key"})
                    await websocket.close(code=4001)
                    return
                _first_message = False
                continue
            elif msg_type == "auth" and authenticated:
                # Already authenticated — just acknowledge
                continue

            _first_message = False

            if not authenticated:
                await conn.send({"type": "error", "message": "Authentication required"})
                await websocket.close(code=4001)
                return

            if msg_type == "ping":
                await conn.send({"type": "pong"})
                continue

            if msg_type == "prompt":
                prompt = msg.get("content", "").strip()
                raw_images: list[str] = msg.get("images", []) or []
                if not prompt and not raw_images:
                    await conn.send({"type": "error", "message": "Empty prompt"})
                    continue

                # Intercept /swarm slash command — run with WS progress callback
                if prompt.startswith("/swarm") or prompt.startswith("/multi"):
                    swarm_goal = prompt.split(maxsplit=1)[1] if " " in prompt else ""
                    if not swarm_goal:
                        await conn.send({"type": "error", "message": "Usage: /swarm <task description>"})
                        continue
                    model = msg.get("model")
                    await _launch_swarm_ws(conn, swarm_goal, DEFAULT_SWARM_ROLES, model=model)
                    continue

                # Validate and filter images
                images: list[str] | None = None
                if raw_images:
                    from wisp.core.message_format import validate_images
                    valid, errors = validate_images(raw_images)
                    for err in errors:
                        await conn.send({"type": "error", "message": err})
                    if valid:
                        images = valid

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
                    config.permission_mode = msg.get("permission_mode", "auto_edit")
                    config.plan_mode = msg.get("plan_mode", False)
                    config.plan_context = msg.get("plan_context")

                    session_id = msg.get("session_id")
                    session = None
                    if session_id:
                        sm = get_store()
                        session = sm.load(session_id)
                        if session is None:
                            resolved = sm.resolve_session_id(session_id)
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
                            await conn.transport.run(prompt, images=images)
                        except KeyboardInterrupt:
                            # KeyboardInterrupt is a BaseException, not an Exception.
                            # Log and notify the client before re-raising so the
                            # event loop is not silently killed.
                            logger.warning("Agent task interrupted for %s", client_id)
                            try:
                                await conn.send({"type": "error", "message": "Server interrupted: KeyboardInterrupt"})
                            except Exception:
                                pass  # WebSocket may already be closed
                            raise
                        except Exception as e:
                            logger.error("Agent error for %s: %s", client_id, e)
                            try:
                                await conn.send({"type": "error", "message": str(e)})
                            except Exception:
                                pass  # WebSocket may already be closed
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
                            # Save session state and summary for cross-session memory
                            try:
                                core.close()
                            except Exception as save_err:
                                logger.error("Session save failed: %s", save_err)

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

            elif msg_type == "pause":
                if conn.transport:
                    conn.transport.pause()
                await conn.send({"type": "steering_paused", "reason": "User paused"})

            elif msg_type == "resume":
                if conn.transport:
                    conn.transport.resume(msg.get("injected_text"))
                await conn.send({"type": "steering_resumed"})

            elif msg_type == "swarm_run":
                goal = msg.get("goal", "").strip()
                if not goal:
                    await conn.send({"type": "error", "message": "Empty swarm goal"})
                    continue
                roles: list[str] = msg.get("roles", DEFAULT_SWARM_ROLES)
                count_per_role: dict[str, int] | None = msg.get("count_per_role")
                max_retries: int = msg.get("max_retries", 2)
                max_parallel: int = msg.get("max_parallel", 3)
                model = msg.get("model")
                await _launch_swarm_ws(
                    conn, goal, roles,
                    count_per_role=count_per_role,
                    max_retries=max_retries,
                    max_parallel=max_parallel,
                    model=model,
                )

            elif msg_type == "swarm_status":
                orch = conn.swarm_orchestrator
                if orch is None:
                    await conn.send({"type": "swarm_status", "active": False, "message": "No active swarm"})
                else:
                    registry_data = orch.registry.to_dict()
                    await conn.send({"type": "swarm_status", "active": True, "agents": registry_data.get("agents", [])})

            elif msg_type == "swarm_stop":
                if conn.swarm_orchestrator:
                    conn.swarm_orchestrator.stop_all()
                if conn.swarm_task and not conn.swarm_task.done():
                    conn.swarm_task.cancel()
                conn.swarm_orchestrator = None
                conn.swarm_task = None
                await conn.send({"type": "status", "message": "Swarm stopped", "level": "info"})

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
        async def _noop_auth(
            x_api_key: str | None = Query(None, alias="api-key"),
            authorization: str | None = Header(None),
        ):
            return x_api_key or authorization or ""
        app.dependency_overrides[verify_api_key] = _noop_auth

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
