"""HookExtension — wraps HookManager for ExtensionHost.

Provides hook-based interception and lifecycle management.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class HookExtension:
    """Extension adapter for the hook system."""

    name = "hooks"

    def __init__(self, config_dir: str | None = None):
        self._config_dir = config_dir
        self._manager = None

    def start(self) -> None:
        """Start the hook extension — load project hooks."""
        from wisp.adapters import HookManager
        self._manager = HookManager(config_dir=self._config_dir)
        self._manager.load_hooks()
        logger.debug("HookExtension started")

    def stop(self) -> None:
        """Stop the hook extension."""
        self._manager = None
        logger.debug("HookExtension stopped")

    def tools(self) -> list[dict]:
        """Hooks don't expose tools directly."""
        return []

    def intercept(self, event: dict) -> dict:
        """Intercept events via hooks.

        Hooks can block tool calls based on configured rules.
        """
        if self._manager is None:
            return {"action": "allow"}

        event_type = event.get("type")
        if event_type != "tool_call":
            return {"action": "allow"}

        try:
            from wisp.adapters import HookEvent
            hook_event = HookEvent.PRE_TOOL_USE
            result = self._manager.run_hooks(hook_event)
            if result.decision == "block":
                return {"action": "block", "reason": getattr(result, "reason", "Hook blocked") or "Hook blocked"}
        except Exception as exc:
            logger.warning("HookExtension intercept() failed: %s", exc)

        return {"action": "allow"}
