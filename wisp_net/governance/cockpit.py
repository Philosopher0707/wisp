"""Operator cockpit: the human side of the platform, over HTTP and WebSocket.

The approval queue, the kill switch, operator rollback, the ledger (with its integrity
check), an interactive what-if sandbox, and a live feed of ledger records. It binds to
127.0.0.1 and every request needs the bearer token from `token_path()` (created 0600).

This is the only channel that can grant an approval: the agent's MCP tools cannot.
Known limit of a single-host lab: any process running as the operator's user can read
the token file. A deployment puts the cockpit on another host behind real identity
(SSO/mTLS), which is what makes "the agent cannot approve its own change" hold there.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

DEFAULT_PORT = 8750


def token_path() -> Path:
    return Path(os.environ.get("WISP_NET_HOME", Path.home() / ".config" / "wisp-net")) / "cockpit.token"


def load_or_create_token(path: Path | None = None) -> str:
    path = path or token_path()
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    return token


class Decision(BaseModel):
    operator: str = Field(min_length=1)
    note: str = ""


class KillSwitchBody(BaseModel):
    engaged: bool
    operator: str = Field(min_length=1)
    reason: str = ""


class RollbackBody(BaseModel):
    operator: str = Field(min_length=1)
    reason: str = ""


class WhatIfBody(BaseModel):
    change: dict[str, Any]
    settle_s: float = 30.0


def create_app(service: Any, token: str) -> FastAPI:
    app = FastAPI(title="wisp-net cockpit", docs_url=None, redoc_url=None, openapi_url=None)

    def authorized(authorization: str = Header(default="")) -> None:
        scheme, _, presented = authorization.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(presented.strip(), token):
            raise HTTPException(status_code=401, detail="bearer token required")

    guard = [Depends(authorized)]

    def _call(fn: Any, *args: Any) -> Any:
        try:
            return fn(*args)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @app.get("/status", dependencies=guard)
    def status() -> dict[str, Any]:
        out: dict[str, Any] = service.status()
        ks = service.kill_switch
        out["kill_switch"] = {"engaged": ks.engaged, "by": ks.by, "reason": ks.reason}
        out["pending_approvals"] = len(service.approval_list("pending"))
        return out

    @app.get("/approvals", dependencies=guard)
    def approvals(status: str | None = None) -> list[dict[str, Any]]:
        return list(service.approval_list(status))

    @app.post("/approvals/{request_id}/grant", dependencies=guard)
    def grant(request_id: str, body: Decision) -> dict[str, Any]:
        return dict(_call(service.operator_decide, request_id, True, body.operator, body.note))

    @app.post("/approvals/{request_id}/deny", dependencies=guard)
    def deny(request_id: str, body: Decision) -> dict[str, Any]:
        return dict(_call(service.operator_decide, request_id, False, body.operator, body.note))

    @app.post("/kill-switch", dependencies=guard)
    def kill_switch(body: KillSwitchBody) -> dict[str, Any]:
        return dict(_call(service.operator_kill_switch, body.engaged, body.operator, body.reason))

    @app.post("/changes/{apply_id}/rollback", dependencies=guard)
    def rollback(apply_id: str, body: RollbackBody) -> dict[str, Any]:
        return dict(_call(service.operator_rollback, apply_id, body.operator, body.reason))

    @app.get("/ledger", dependencies=guard)
    def ledger(since: int = 0, fingerprint: str | None = None, limit: int = 100) -> dict[str, Any]:
        return dict(service.ledger_view(since, fingerprint, limit))

    @app.get("/changes/{fingerprint}/explain", dependencies=guard)
    def explain(fingerprint: str) -> dict[str, Any]:
        return dict(_call(service.explain, fingerprint))

    @app.post("/what-if", dependencies=guard)
    def what_if(body: WhatIfBody) -> dict[str, Any]:
        return dict(_call(service.what_if, body.change, body.settle_s))

    @app.websocket("/events")
    async def events(ws: WebSocket) -> None:
        if not hmac.compare_digest(ws.query_params.get("token", ""), token):
            await ws.close(code=4401)
            return
        await ws.accept()
        seq = int(ws.query_params.get("since", len(service.ledger)))
        try:
            while True:
                for rec in service.ledger.records(seq, limit=500):
                    await ws.send_json(rec.to_dict())
                    seq = rec.seq
                await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            return

    return app


class CockpitServer:
    """Runs the cockpit on a daemon thread, silently (the MCP server's stdio is sacred)."""

    def __init__(self, service: Any, port: int = DEFAULT_PORT, token: str | None = None) -> None:
        import uvicorn

        self.token = token or load_or_create_token()
        config = uvicorn.Config(create_app(service, self.token), host="127.0.0.1", port=port,
                                log_config=None, log_level="critical", access_log=False)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="wisp-net-cockpit", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)
