"""New server main using domain routers.

Replaces: monolithic 2954-line server.py.
Mounts all domain routers on a single FastAPI app.
"""

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
    """Application lifespan handler."""
    logger.info("Wisp API Server starting...")
    yield
    logger.info("Wisp API Server shutting down...")


app = FastAPI(title="Wisp API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
app.include_router(search_router)
app.include_router(diagnostics_router)
