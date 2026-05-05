"""Wisp transport layer — bridges WispAgentCore to I/O backends.

Transports consume AgentEvent instances from WispAgentCore and handle
presentation (terminal, WebSocket, SSE, etc.) and user interaction.
"""

from wisp.transport.cli import CLITransport

__all__ = ["CLITransport"]
