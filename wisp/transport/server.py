"""Backward-compatibility shim — re-exports from _legacy_server.py.

The new server transports are wisp.transport.websocket.WebSocketTransport
and wisp.transport.sse.SSETransport (both implement Transport ABC).
This module preserves imports for code still using the old ServerTransport.
"""

from wisp.transport._legacy_server import (
    ServerTransport,
    PendingApproval,
)

__all__ = ["ServerTransport", "PendingApproval"]
