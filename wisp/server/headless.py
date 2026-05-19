"""Headless agent execution pool.

Extracted from legacy server.py.
Provides pooled WispAgentCore instances for headless/CI execution.

DEPRECATED: Use wisp.entry.run_headless() directly.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from wisp.config import WispConfig
from wisp.server.routes.workspace import WORKSPACE_ROOT

logger = logging.getLogger(__name__)


async def _run_agent_headless(
    prompt: str,
    model: str | None = None,
    session_id: str | None = None,
    skill: str | None = None,
    permission_mode: str = "full",
    images: list[str] | None = None,
) -> dict:
    """Run the agent headlessly and return a structured result.

    Delegates to wisp.entry.run_headless() for consistent
    CompositionRoot-based execution.
    """
    from wisp.entry import run_headless

    start = time.time()
    result = await run_headless(
        prompt=prompt,
        model=model,
        workspace=str(WORKSPACE_ROOT),
        session_id=session_id,
        permission_mode=permission_mode,
    )
    result["duration_ms"] = round((time.time() - start) * 1000, 1)
    result["model"] = result.get("model", model)
    result["permission_mode"] = permission_mode
    return result


# Legacy pool functions — kept for backward compatibility but no-op
def _headless_pool_key(config: WispConfig) -> str:
    return f"{config.model}:{config.permission_mode}:{config.workspace}"


def _get_headless_core(config: WispConfig) -> Any:
    """Deprecated. Returns None."""
    logger.warning("_get_headless_core is deprecated — use run_headless()")
    return None


def _shutdown_headless_pool() -> None:
    """Deprecated. No-op."""
    pass
