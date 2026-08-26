"""New server main using domain routers.

Replaces: monolithic 2954-line server.py.
Mounts all domain routers on a single FastAPI app.
"""

import asyncio

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from wisp.server.routes.sessions import router as sessions_router
from wisp.server.routes.files import router as files_router
from wisp.server.routes.health import router as health_router
from wisp.server.routes.models import router as models_router
from wisp.server.routes.arena import router as arena_router
from wisp.server.routes.swarm import router as swarm_router
from wisp.server.routes.runs import router as runs_router
from wisp.server.routes.codebase import router as codebase_router
from wisp.server.routes.diff import router as diff_router
from wisp.server.routes.complete import router as complete_router
from wisp.server.routes.workspace import router as workspace_router
from wisp.server.routes.git import router as git_router
from wisp.server.routes.context import router as context_router
from wisp.server.routes.bash import router as bash_router
from wisp.server.routes.review import router as review_router
from wisp.server.routes.suggestions import router as suggestions_router
from wisp.server.routes.prompt import router as prompt_router
from wisp.server.routes.jsonrpc import router as jsonrpc_router
from wisp.server.routes.plugins import router as plugins_router
from wisp.server.routes.hooks import router as hooks_router
from wisp.server.routes.mcp import router as mcp_router
from wisp.server.routes.agents import router as agents_router
from wisp.server.routes.background import router as background_agents_router
from wisp.server.routes.search import router as search_router
from wisp.server.routes.diagnostics import router as diagnostics_router

logger = logging.getLogger(__name__)

# CORS Configuration
_cors_raw = os.environ.get("WISP_CORS_ORIGINS", "")
if _cors_raw:
    CORS_ORIGINS = [o.strip() for o in _cors_raw.split(",") if o.strip()]
else:
    CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Creates CompositionRoot on startup and wires it into app.state
    so routes can access shared services.
    """
    from wisp.composition import CompositionRoot
    from wisp.config import WispConfig

    logger.info("Wisp API Server starting...")
    config = WispConfig()
    root = CompositionRoot(config)
    root.start()
    # Lifespan runs inside the serving loop — bind the shared pool now.
    root.bind_loop(asyncio.get_running_loop())
    app.state.root = root
    # Route CRUD must mutate the SAME manager tool_executor dispatches
    # through (L5) — not a private module-global twin.
    from wisp.server.routes import mcp as mcp_routes
    mcp_routes.set_mcp_manager(root._mcp_manager)
    yield
    logger.info("Wisp API Server shutting down...")
    root.shutdown()
    # root.shutdown() requested cancellation of detached agent work; reap it
    # here on the serving loop so nothing dies as destroyed-pending noise.
    from wisp.async_utils import drain_pending_tasks
    await drain_pending_tasks(asyncio.get_running_loop(), timeout=3.0)


app = FastAPI(title="Wisp API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Security Headers Middleware ──
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add recommended security headers to every HTTP response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # Block content MIME-type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Block clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        # Basic XSS protection for older browsers
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # Enforce HTTPS in production (set WISP_ENABLE_HSTS=true)
        if os.environ.get("WISP_ENABLE_HSTS", "").lower() == "true":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        # Generic referrer policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Restrictive CSP for API-only usage
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'none'; "
            "object-src 'none'; "
            "frame-ancestors 'none'"
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)

# Mount all routers
app.include_router(sessions_router)
app.include_router(files_router)
app.include_router(health_router)
app.include_router(models_router)
app.include_router(arena_router)
app.include_router(swarm_router)
app.include_router(runs_router)
app.include_router(codebase_router)
app.include_router(diff_router)
app.include_router(complete_router)
app.include_router(workspace_router)
app.include_router(git_router)
app.include_router(context_router)
app.include_router(bash_router)
app.include_router(review_router)
app.include_router(suggestions_router)
app.include_router(prompt_router)
app.include_router(jsonrpc_router)
app.include_router(plugins_router)
app.include_router(hooks_router)
app.include_router(mcp_router)
app.include_router(agents_router)
app.include_router(background_agents_router)
app.include_router(search_router)
app.include_router(diagnostics_router)


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "::"}


def main(host: str = "127.0.0.1", port: int = 8000, no_auth: bool = False):
    """Entry point to run the Wisp API server."""
    import os
    import sys
    import uvicorn
    from fastapi import Header

    # Non-loopback binds expose /api/bash (host command execution) to the network:
    # require an API key, and never allow --no-auth off-loopback.
    if host not in _LOOPBACK_HOSTS:
        has_key = bool(os.environ.get("WISP_API_KEY", "").strip())
        if no_auth or not has_key:
            print(
                "error: refusing to bind Wisp server to a non-loopback address "
                f"({host}) without authentication.\n"
                "Set WISP_API_KEY to require an API key, or bind to 127.0.0.1 "
                "for local-only access.",
                file=sys.stderr,
            )
            raise SystemExit(2)

    if no_auth:
        from wisp.server.deps import _auth, verify_api_key
        _auth.disable()

        async def _noop_auth(
            x_api_key_header: str | None = Header(None, alias="X-API-Key"),
            authorization: str | None = Header(None),
        ):
            return x_api_key_header or authorization or ""
        app.dependency_overrides[verify_api_key] = _noop_auth

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
