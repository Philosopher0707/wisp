"""Wisp transport layer — bridges WispAgentCore to I/O backends.

Transports consume AgentEvent instances from WispAgentCore and handle
presentation (terminal, WebSocket, SSE, etc.) and user interaction.
"""

from wisp.transport.cli import CLITransport
from wisp.transport.server import ServerTransport

__all__ = ["CLITransport", "ServerTransport"]
