"""Wisp transport layer — bridges WispAgentCore to I/O backends.

Transports consume AgentEvent instances from WispAgentCore and handle
presentation (terminal, WebSocket, files, etc.) and user interaction.

All transports implement the Transport ABC:
  - CLITransport: interactive REPL (stdin/stdout)
  - HeadlessTransport: collects events into a result dict, no I/O
  - FileTransport / MultiTransport / MetricsTransport: composable wrappers
"""

from wisp.transport.base import Transport
from wisp.transport.cli import CLITransport
from wisp.transport.headless import HeadlessTransport
from wisp.transport.file import FileTransport
from wisp.transport.multi import MultiTransport
from wisp.transport.metrics import MetricsTransport

__all__ = [
    "Transport",
    "CLITransport",
    "HeadlessTransport",
    "FileTransport",
    "MultiTransport",
    "MetricsTransport",
]

