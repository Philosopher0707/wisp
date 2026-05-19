"""Wisp transport layer — bridges WispAgentCore to I/O backends.

Transports consume AgentEvent instances from WispAgentCore and handle
presentation (terminal, WebSocket, SSE, etc.) and user interaction.

New architecture (Transport ABC):
  - Transport: abstract base class
  - CLITransport (v2): stdin/stdout via Transport ABC
  - WebSocketTransport: WebSocket via Transport ABC
  - SSETransport: Server-Sent Events via Transport ABC

Legacy transports (still supported):
  - CLITransport (cli.py): original REPL transport
  - ServerTransport (server.py): original WebSocket transport
"""

from wisp.transport.base import Transport
from wisp.transport.cli_v2 import CLITransport as CLITransportV2
from wisp.transport.websocket import WebSocketTransport
from wisp.transport.sse import SSETransport
from wisp.transport.tui import TUITransport
from wisp.transport.adapters import CLITransportAdapter, ServerTransportAdapter

# Legacy exports for backward compatibility
from wisp.transport.cli import CLITransport
from wisp.transport.server import ServerTransport

__all__ = [
    "Transport",
    "CLITransportV2",
    "WebSocketTransport",
    "SSETransport",
    "TUITransport",
    "CLITransportAdapter",
    "ServerTransportAdapter",
    # Legacy
    "CLITransport",
    "ServerTransport",
]
