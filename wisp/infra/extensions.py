"""ExtensionHost — unified extension system.

Replaces: plugin_registry.py, hooks.py, mcp.py, skills.py
with one lifecycle-managed host.

All extensions implement:
  - start() → None
  - stop() → None
  - tools() → list[dict]  (OpenAI-style function schemas)
  - intercept(event: dict) → {"action": "allow"|"block", "reason": str}
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ExtensionHost:
    """One system for all extensions."""

    def __init__(self):
        self._extensions: list[Any] = []

    def register(self, ext: Any) -> None:
        """Register an extension and start it."""
        try:
            ext.start()
            self._extensions.append(ext)
            logger.debug("Extension started: %s", getattr(ext, "name", type(ext).__name__))
        except Exception as exc:
            logger.warning("Extension %s failed to start: %s", getattr(ext, "name", "?"), exc)
            # Do NOT append broken extensions

    def tools(self) -> list[dict]:
        """Aggregate tools from all extensions."""
        tools: list[dict] = []
        for ext in self._extensions:
            try:
                ext_tools = ext.tools()
                if ext_tools:
                    tools.extend(ext_tools)
            except Exception as exc:
                logger.warning(
                    "Extension %s tools() failed: %s",
                    getattr(ext, "name", type(ext).__name__),
                    exc,
                )
        return tools

    def intercept(self, event: dict) -> dict:
        """Run event through all extensions. First block wins.

        If an extension raises an exception, the call is denied (fail-closed).
        """
        for ext in self._extensions:
            try:
                result = ext.intercept(event)
                if result.get("action") == "block":
                    return result
            except Exception as exc:
                logger.exception(
                    "Extension %s intercept() failed: %s — denying by default",
                    getattr(ext, "name", type(ext).__name__),
                    exc,
                )
                return {"action": "block", "reason": f"Extension error: {exc}"}
        return {"action": "allow"}

    def start(self) -> None:
        """Extensions already started during register() — kept for ServiceRegistry contract."""

    def stop(self) -> None:
        """Stop all extensions in reverse registration order."""
        for ext in reversed(self._extensions):
            try:
                ext.stop()
                logger.debug("Extension stopped: %s", getattr(ext, "name", type(ext).__name__))
            except Exception as exc:
                logger.warning(
                    "Extension %s stop() failed: %s",
                    getattr(ext, "name", "?"),
                    exc,
                )
        self._extensions.clear()
