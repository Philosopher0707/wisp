"""Tool approval interceptor and structured confirmation renderer.

Provides:
- Sanitization of large/multiline payloads in approval prompts
- Compact 2-line approval badge for file mutations (path, delta metrics, scope)
- Expandable diff viewer ([v] view diff) integration via rich.panel.Panel and rich.syntax.Syntax
- Support for edit_file, edit_file_multi, write_file, patch, and general tools
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass, field
from io import StringIO
from typing import Any, Optional

from rich.console import Console
from rich.panel import Panel

from wisp.colors import bold, dim, error, success, warning, info
from wisp.terminal_width import status_symbols, is_accessible
from wisp.ui.diff_viewer import compute_diff_stats, create_diff_panel, render_diff_string

logger = logging.getLogger(__name__)


@dataclass
class ToolApprovalInfo:
    """Structured information extracted from a pending tool call."""

    tool_name: str
    is_file_edit: bool = False
    target_path: Optional[str] = None
    added_lines: int = 0
    removed_lines: int = 0
    summary: str = ""
    old_text: Optional[str] = None
    new_text: Optional[str] = None
    sanitized_args_str: str = ""
    raw_arguments: dict = field(default_factory=dict)


def sanitize_arg_value(key: str, value: Any, max_len: int = 50) -> str:
    """Sanitize argument values for single-line header display.
    
    Prevents raw newlines, escape sequences, or multi-kilobyte string dumps.
    """
    if key in ("old_text", "new_text", "patch", "content", "edits"):
        if isinstance(value, str):
            lines = len(value.splitlines())
            return f"<{lines} lines, {len(value)} chars>"
        elif isinstance(value, list):
            return f"<{len(value)} edits>"
        return f"<{len(str(value))} chars>"

    if isinstance(value, str):
        # Collapse newlines/whitespace to single spaces
        collapsed = " ".join(value.split())
        if len(collapsed) > max_len:
            collapsed = collapsed[: max_len - 3] + "..."
        return repr(collapsed) if not collapsed.startswith(("'", '"')) else collapsed

    if isinstance(value, (int, float, bool)) or value is None:
        return repr(value)

    if isinstance(value, dict):
        return f"<{len(value)} keys>"

    if isinstance(value, list):
        return f"<{len(value)} items>"

    s = " ".join(str(value).split())
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return repr(s)


def parse_tool_approval(tool_call: dict) -> ToolApprovalInfo:
    """Parse and sanitize a tool call event for approval rendering."""
    tool_name = tool_call.get("name", "unknown")
    raw_args = tool_call.get("arguments", {})
    if not isinstance(raw_args, dict):
        raw_args = {}

    try:
        from wisp.infra.security import redact_sensitive_tool_args
        args = redact_sensitive_tool_args(raw_args)
    except Exception:
        args = dict(raw_args)

    info = ToolApprovalInfo(
        tool_name=tool_name,
        raw_arguments=args,
    )

    # Detect file editing / mutation tools
    is_edit = tool_name in ("edit_file", "edit_file_multi", "write_file", "patch_file", "apply_patch")
    has_diff_keys = any(k in args for k in ("old_text", "new_text", "patch", "edits"))
    
    if is_edit or has_diff_keys:
        info.is_file_edit = True
        info.target_path = str(args.get("path") or args.get("filepath") or args.get("file") or "unknown")

        if "old_text" in args or "new_text" in args:
            info.old_text = str(args.get("old_text") or "")
            info.new_text = str(args.get("new_text") or "")
            added, removed, summary = compute_diff_stats(info.old_text, info.new_text)
            info.added_lines = added
            info.removed_lines = removed
            info.summary = summary
        elif "edits" in args and isinstance(args["edits"], list):
            # edit_file_multi
            total_added = 0
            total_removed = 0
            summaries = []
            old_snippets = []
            new_snippets = []
            for edit in args["edits"]:
                if isinstance(edit, dict):
                    ot = str(edit.get("old_text") or "")
                    nt = str(edit.get("new_text") or "")
                    old_snippets.append(ot)
                    new_snippets.append(nt)
                    a, r, s = compute_diff_stats(ot, nt)
                    total_added += a
                    total_removed += r
                    if s and s != "no changes" and len(summaries) < 2:
                        summaries.append(s)
            info.added_lines = total_added
            info.removed_lines = total_removed
            info.old_text = "\n---\n".join(old_snippets)
            info.new_text = "\n---\n".join(new_snippets)
            info.summary = ", ".join(summaries) if summaries else f"{len(args['edits'])} edit blocks"
        elif "content" in args:
            # write_file
            content = str(args.get("content") or "")
            info.old_text = ""
            info.new_text = content
            info.added_lines = len(content.splitlines())
            info.removed_lines = 0
            info.summary = f"write {info.added_lines} lines"

    # Build sanitized args string for header
    sanitized_pairs = []
    for k, v in list(args.items())[:3]:
        # For file edits, skip the giant old_text/new_text in header
        if info.is_file_edit and k in ("old_text", "new_text", "content", "edits", "patch"):
            continue
        val_str = sanitize_arg_value(k, v)
        sanitized_pairs.append(f"{k}={val_str}")

    if len(args) > 3 or (info.is_file_edit and any(k in args for k in ("old_text", "new_text", "content"))):
        if not sanitized_pairs and info.is_file_edit and info.target_path:
            sanitized_pairs.append(f"path={info.target_path!r}")

    info.sanitized_args_str = ", ".join(sanitized_pairs)
    return info


def render_approval_badge(info: ToolApprovalInfo, plain: bool = False) -> str:
    """Render a compact 2-line approval badge.
    
    Line 1: Target path / Tool name with (+N / -M lines)
    Line 2: Scope summary / sanitized arguments
    """
    warn_sym = "[!]" if plain or is_accessible() else status_symbols().get("warn", "⚠")
    
    if info.is_file_edit and info.target_path:
        # Line 1: ⚠  edit_file: path/to/file (+N / -M lines)
        delta_part = f"(+{info.added_lines} / -{info.removed_lines} lines)"
        if not plain:
            line1 = warning(f"{warn_sym}  {info.tool_name}: ") + bold(info.target_path) + " " + info_colored_delta(info.added_lines, info.removed_lines)
            line2 = dim(f"   Scope: {info.summary}") if info.summary else ""
        else:
            line1 = f"{warn_sym}  {info.tool_name}: {info.target_path} {delta_part}"
            line2 = f"   Scope: {info.summary}" if info.summary else ""

        if line2:
            return f"{line1}\n{line2}"
        return line1

    # Non-file tools: single/double line badge
    if not plain:
        line1 = warning(f"{warn_sym}  {info.tool_name}({info.sanitized_args_str})")
    else:
        line1 = f"{warn_sym}  {info.tool_name}({info.sanitized_args_str})"
    return line1


def info_colored_delta(added: int, removed: int) -> str:
    """Format (+N / -M lines) with green/red ANSI styling."""
    return f"({success(f'+{added}')} / {error(f'-{removed}')} lines)"


def render_approval_options(is_file_edit: bool) -> str:
    """Render interactive key choice hint line."""
    if is_file_edit:
        return dim("     [y] yes  [v] view diff  [Y] always this  [a] all on  [n] no  [N] always no  [d] all off  [c] cancel")
    return dim("     [y] yes  [Y] always this  [a] all on  [n] no  [N] always no  [d] all off  [c] cancel")


def render_diff_view(info: ToolApprovalInfo, width: int = 100, plain: bool = False) -> str:
    """Render formatted diff panel for the pending tool approval."""
    if not info.is_file_edit or (info.old_text is None and info.new_text is None):
        return dim("No diff available for this tool call.")

    old_text = info.old_text or ""
    new_text = info.new_text or ""
    target_path = info.target_path or "file"

    return render_diff_string(old_text, new_text, file_path=target_path, width=width, plain=plain)
