"""Backward-compatible re-exports for wisp.server package.

Replaces: monolithic wisp/server.py file.
Provides compatibility for code that imports from wisp.server.
"""

from wisp.server.deps import (
    _auth,
    API_KEY,
    API_KEY_STR,
    verify_api_key,
    RATE_LIMITER,
    SQLiteRateLimiter,
)

from wisp.server.main import (
    app,
    main,
    lifespan,
)

from wisp.server.routes.workspace import WORKSPACE_ROOT
from wisp.server.routes.files import _resolve_path

from wisp.server.headless import (
    _HEADLESS_POOL,
    _get_headless_core,
    _shutdown_headless_pool,
    _run_agent_headless,
    MemoryTransport,
)

from wisp.server.routes.prompt import PromptRequest

from wisp.server.connections import (
    Connection,
    ConnectionManager,
    manager,
)

from wisp.app_server import WispAppServer

__all__ = [
    "_auth",
    "API_KEY",
    "API_KEY_STR",
    "verify_api_key",
    "RATE_LIMITER",
    "SQLiteRateLimiter",
    "app",
    "main",
    "lifespan",
    "WORKSPACE_ROOT",
    "_resolve_path",
    "_HEADLESS_POOL",
    "_get_headless_core",
    "_shutdown_headless_pool",
    "_run_agent_headless",
    "MemoryTransport",
    "PromptRequest",
    "Connection",
    "ConnectionManager",
    "manager",
    "WispAppServer",
]
