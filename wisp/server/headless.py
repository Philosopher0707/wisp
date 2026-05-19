"""Headless agent execution pool.

Extracted from legacy server.py.
Provides pooled WispAgentCore instances for headless/CI execution.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any

from wisp.config import WispConfig
from wisp.core.agent import WispAgentCore
from wisp.adapters import get_store
from wisp.server.routes.workspace import WORKSPACE_ROOT
from wisp.transport.headless import HeadlessTransport

logger = logging.getLogger(__name__)

_HEADLESS_POOL: dict[str, WispAgentCore] = {}
_HEADLESS_POOL_LOCK = threading.Lock()


def _headless_pool_key(config: WispConfig) -> str:
    return f"{config.model}:{config.permission_mode}:{config.workspace}"


def _get_headless_core(config: WispConfig) -> WispAgentCore:
    """Return a cached WispAgentCore for headless execution."""
    key = _headless_pool_key(config)
    with _HEADLESS_POOL_LOCK:
        core = _HEADLESS_POOL.get(key)
        if core is None:
            core = WispAgentCore(config=config)
            core._pool_lock = asyncio.Lock()
            _HEADLESS_POOL[key] = core
            logger.info("Created headless core for key %s", key)
        return core


def _shutdown_headless_pool() -> None:
    """Close all pooled cores. Idempotent."""
    global _HEADLESS_POOL
    with _HEADLESS_POOL_LOCK:
        pool = dict(_HEADLESS_POOL)
        _HEADLESS_POOL.clear()
    for key, core in pool.items():
        try:
            core.close()
            logger.info("Closed headless core %s", key)
        except Exception as e:
            logger.warning("Error closing headless core %s: %s", key, e)


async def _run_agent_headless(
    prompt: str,
    model: str | None = None,
    session_id: str | None = None,
    skill: str | None = None,
    permission_mode: str = "full",
    images: list[str] | None = None,
) -> dict:
    """Run the agent synchronously in memory and return a structured result."""
    start = time.time()
    config = WispConfig()
    if model:
        config.model = model
    config.workspace = str(WORKSPACE_ROOT)
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

    core = _get_headless_core(config)
    transport = HeadlessTransport()
    transport.start()

    system = core._build_system_prompt(skill_name=skill, workspace=config.workspace) if skill else None

    async with core._pool_lock:
        core.session = session
        if session is not None:
            core.messages = list(session.messages)
        else:
            core.messages = []
        core._invalidate_token_cache()

        try:
            async for event in core.run(prompt, system=system, images=images):
                await transport.send(event)
        except Exception as e:
            logger.error("Headless agent error: %s", e)
            transport.events.append({"type": "error", "message": str(e), "recoverable": False})
        finally:
            try:
                core._save_session()
            except Exception:
                pass
            try:
                core._save_session_summary()
            except Exception:
                pass
            core.session = None

    duration_ms = round((time.time() - start) * 1000, 1)
    result = transport.collect_result()
    result["duration_ms"] = duration_ms
    result["model"] = config.model
    result["permission_mode"] = permission_mode
    result["events"] = transport.events
    return result
