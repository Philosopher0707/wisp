"""Shared tool exceptions for Wisp.

Placed in a dedicated module to avoid circular imports between
_tools._utils and _tools._legacy.
"""


class ToolError(Exception):
    """Raised when a tool execution fails."""
    pass
