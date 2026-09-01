"""Wisp CLI package — spec compatibility shim for `wisp/cli/commands` and approval."""

from __future__ import annotations

from .approval import (
    ToolApprovalInfo,
    parse_tool_approval,
    render_approval_badge,
    render_diff_view,
    render_approval_options,
    sanitize_arg_value,
)

__all__ = [
    "ToolApprovalInfo",
    "parse_tool_approval",
    "render_approval_badge",
    "render_diff_view",
    "render_approval_options",
    "sanitize_arg_value",
]
