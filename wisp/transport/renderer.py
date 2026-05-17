"""CLI rendering utilities — pure functions for formatting terminal output.

Extracted from wisp/transport/cli.py to make rendering logic testable
and reusable across transports.
"""

from __future__ import annotations

import textwrap
from typing import Optional

from wisp.colors import dim, error, warning, success
from wisp.core.events import AgentEvent


def format_duration(duration_ms: float | None) -> str:
    """Format a duration in milliseconds to a human-readable string."""
    if duration_ms is None:
        return ""
    if duration_ms < 1:
        return f"{duration_ms * 1000:.0f}μs"
    if duration_ms < 1000:
        return f"{duration_ms:.0f}ms"
    if duration_ms < 60000:
        return f"{duration_ms / 1000:.1f}s"
    mins = int(duration_ms / 60000)
    secs = (duration_ms % 60000) / 1000
    return f"{mins}m {secs:.0f}s"


def format_arg_value(key: str, value) -> str:
    """Format a single argument value for display."""
    if key in ("path", "command", "pattern", "filepath"):
        s = str(value)
        if len(s) > 60:
            s = s[:57] + "..."
        return s
    if key in ("content", "text", "old", "new"):
        if isinstance(value, str):
            return f"({len(value)} chars)"
        return str(value)[:60]
    if key in ("arguments", "args"):
        if isinstance(value, dict):
            return f"({len(value)} keys)"
        return str(value)[:40]
    s = str(value)
    if len(s) > 80:
        s = s[:77] + "..."
    return s


def wrap_text(text: str, width: int, indent: str = "") -> list[str]:
    """Wrap text to a given width, with an optional indent on each line after the first."""
    if not text:
        return [""]
    lines = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        wrapped = textwrap.wrap(paragraph, width=width)
        if indent and len(lines) > 0:
            wrapped = [wrapped[0]] + [indent + w for w in wrapped[1:]]
        lines.extend(wrapped)
    return lines


def render_tool_call(name: str, args: dict, box_mode: bool = True) -> str:
    """Render a tool call with structured argument display."""
    lines = [dim(f"  🔧 {name}")]
    if args:
        for key, value in args.items():
            val_str = format_arg_value(key, value)
            lines.append(dim(f"  │  {key}: {val_str}"))
    return "\n".join(lines)


def render_thinking_block(text: str, box_mode: bool, width: int) -> Optional[str]:
    """Render buffered thinking text as a block."""
    if not text.strip():
        return None
    inner_w = width - 4
    wrapped = wrap_text(text.strip(), inner_w)
    if box_mode:
        header = _rule("·", "🧠 Reasoning", style_fn=dim, width=width)
        body = "\n".join(dim(f"  {line}") for line in wrapped)
        return f"{header}\n{body}"
    else:
        header = _rule("─", "🧠 Reasoning", style_fn=dim, width=width)
        body = "\n".join(dim(f"  {line}") for line in wrapped)
        return f"{header}\n{body}"


def render_content_block(text: str, box_mode: bool, width: int) -> Optional[str]:
    """Render buffered content text as a block."""
    if not text.strip():
        return None
    inner_w = width - 4
    wrapped = wrap_text(text.strip(), inner_w)
    if box_mode:
        return _box("\n".join(wrapped), title="Response", style="muted", width=width)
    else:
        return "\n".join(wrapped)


def render_done_reason(event: AgentEvent, iterations: int) -> Optional[str]:
    """Render the turn completion reason."""
    reason = event.data.get("reason", "")
    if reason == "max_iterations":
        return warning(
            f"\n  ⚠️  Max iterations ({iterations}) reached. "
            "Type 'continue' or increase --max-iterations."
        )
    elif reason == "max_reflections":
        return warning(f"\n  🔄  Reflective loop detected after {iterations} iterations.")
    elif reason == "interrupted":
        return dim("\n  ⏹  Interrupted.")
    elif reason == "error":
        return error("\n  ✗ Stream error — turn aborted.")
    return None


# ── Internal helpers (also used by cli.py) ─────────────────────────

def _box(content: str, title: str = "", style: str = "dim",
         double: bool = False, width: Optional[int] = None) -> str:
    """Wrap content in a box-drawn panel."""
    from wisp.colors import muted

    if width is None:
        width = 80  # default for testing
    inner_width = width - 4

    style_fn = {"dim": dim, "error": error, "success": success, "muted": muted}.get(style, dim)

    if double:
        tl, tr, bl, br, hz, vt = "╔", "╗", "╚", "╝", "═", "║"
    else:
        tl, tr, bl, br, hz, vt = "┌", "┐", "└", "┘", "─", "│"

    if title:
        title_text = f" {title} "
        available = width - 2
        if len(title_text) > available:
            title_text = title_text[:available]
        top = tl + title_text + hz * (width - 2 - len(title_text)) + tr
    else:
        top = tl + hz * (width - 2) + tr

    bottom = bl + hz * (width - 2) + br

    lines = content.split("\n")
    result_lines = [style_fn(top)]

    if title:
        result_lines.append(style_fn(f"{vt} {' ' * inner_width} {vt}"))

    for line in lines:
        if not line.strip():
            result_lines.append(style_fn(f"{vt} {' ' * inner_width} {vt}"))
            continue
        wrapped = textwrap.wrap(line, width=inner_width)
        for w in wrapped:
            padded = w.ljust(inner_width)
            result_lines.append(style_fn(f"{vt} {padded} {vt}"))

    if title:
        result_lines.append(style_fn(f"{vt} {' ' * inner_width} {vt}"))

    result_lines.append(style_fn(bottom))
    return "\n".join(result_lines)


def _rule(char: str = "─", label: str = "", style_fn=None,
          width: Optional[int] = None) -> str:
    """Draw a horizontal rule, optionally with a label."""
    if width is None:
        width = 80
    style_fn = style_fn or dim

    if label:
        label_str = f" {label} "
        remaining = width - len(label_str)
        left = char * (remaining // 2)
        right = char * (remaining - len(left))
        return style_fn(f"{left}{label_str}{right}")
    return style_fn(char * width)
