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

logger = logging.getLogger(__name__)

_HEADLESS_POOL: dict[str, WispAgentCore] = {}
_HEADLESS_POOL_LOCK = threading.Lock()


class MemoryTransport:
    """Minimal in-memory event collector for headless/CI agent execution."""

    def __init__(self, permission_mode: str = "auto_edit"):
        self.events: list[dict] = []
        self.errors: list[dict] = []
        self._approvals: dict[str, bool] = {}
        self.permission_mode = permission_mode

    def collect(self, event: Any) -> None:
        from wisp.core.events import AgentEvent
        if isinstance(event, AgentEvent):
            self.events.append(event.to_dict())
        elif isinstance(event, dict):
            self.events.append(event)

    async def approval_handler(self, tool_call: dict) -> dict:
        """Default approval handler for headless mode."""
        if self.permission_mode == "full":
            return {"approved": True}
        return {"approved": False, "reason": "Headless mode requires explicit approval"}


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
    transport = MemoryTransport(permission_mode=permission_mode)

    system = core._build_system_prompt(skill_name=skill, workspace=config.workspace) if skill else None

    async with core._pool_lock:
        core.session = session
        if session is not None:
            core.messages = list(session.messages)
        else:
            core.messages = []
        core._invalidate_token_cache()

        try:
            async for event in core.run(prompt, system=system, approval_handler=transport.approval_handler, images=images):
                transport.collect(event)
        except Exception as e:
            logger.error("Headless agent error: %s", e)
            transport.errors.append({"message": str(e), "recoverable": False})
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

    content_parts: list[str] = []
    for ev in transport.events:
        if ev.get("type") == "content":
            content_parts.append(ev.get("text", ""))

    return {
        "ok": len(transport.errors) == 0,
        "content": "".join(content_parts),
        "events": transport.events,
        "errors": transport.errors,
        "duration_ms": duration_ms,
        "model": config.model,
        "permission_mode": permission_mode,
    }
