"""Backward-compatibility shim — lazily re-exports from _legacy_server.py.

The new server transports are wisp.transport.websocket.WebSocketTransport
and wisp.transport.sse.SSETransport (both implement Transport ABC).
This module preserves imports for code still using the old ServerTransport.
"""

from __future__ import annotations

import importlib


def __getattr__(name: str):
    """Lazy import from _legacy_server — only loads when accessed."""
    mod = importlib.import_module("wisp.transport._legacy_server")
    return getattr(mod, name)


__all__ = ["ServerTransport", "PendingApproval"]
