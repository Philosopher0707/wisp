"""Shared exceptions for Wisp.

This module exists to prevent circular dependencies between
wisp.commands and wisp.transport.cli.

It also owns the approval *verdict* exceptions. They were originally defined
in wisp/cli/approval.py, which forced wisp/core/approval_gate.py — the layer
everything else depends on — to import upward into the CLI layer. A verdict
type is domain semantics, not presentation, so it belongs below both.
"""


class ExitREPL(Exception):
    """Raised by /exit to signal graceful REPL termination."""


class ApprovalCancelled(Exception):
    """The user chose 'cancel' at an approval prompt.

    Deliberately NOT a CancelledError/KeyboardInterrupt: it is a *verdict*,
    not an external interruption. Callers convert it to a recorded denial
    ("cancelled by user") so history stays protocol-consistent. Genuine
    task cancellation (SIGINT) still arrives as CancelledError and must
    propagate untouched — see the explicit re-raise guards at every
    approval catch site.
    """

    def __init__(self, tool_name: str = ""):
        self.tool_name = tool_name
        super().__init__(f"User cancelled at the approval prompt for {tool_name or 'tool'}")


class ApprovalTimeout(Exception):
    """The approval prompt lapsed without a verdict (input timeout).

    Also a verdict, not an interruption: callers fail closed with a
    distinct APPROVAL_TIMEOUT denial so a lapse never reads as an
    ordinary user deny. Caught alongside ApprovalCancelled at every
    approval catch site.
    """

    def __init__(self, tool_name: str = ""):
        self.tool_name = tool_name
        super().__init__(f"Approval timed out for {tool_name or 'tool'}")
