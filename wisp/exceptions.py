"""Shared exceptions for Wisp.

This module exists to prevent circular dependencies between
wisp.commands and wisp.transport.cli.
"""


class ExitREPL(Exception):
    """Raised by /exit to signal graceful REPL termination."""
