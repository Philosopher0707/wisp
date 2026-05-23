"""CLI rendering utilities — pure functions for formatting terminal output.

Extracted from wisp/transport/cli.py to make rendering logic testable
and reusable across transports.

Uses width-aware rendering (CJK, emoji, combining chars) with fallback
modes: unicode, ascii, accessible, minimal.
"""

from __future__ import annotations

import textwrap
from typing import Optional

from wisp.colors import dim, error, warning, success
from wisp.core.events import AgentEvent
from wisp.terminal_width import (
    display_width,
    wrap_text_wide,
    pad_right as _pad_right,
    BoxChars,
    OutputMode,
    get_output_mode,
    is_accessible,
)


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
    """Wrap text to display width, accounting for wide characters.

    Uses display-width-aware wrapping instead of naive character count.
    """
    return wrap_text_wide(text, width, indent)


def render_tool_call(name: str, args: dict, box_mode: bool = True) -> str:
    """Render a tool call with structured argument display."""
    box = BoxChars()
    if box.mode == OutputMode.ACCESSIBLE:
        lines = [dim(f"  [TOOL] {name}")]
    elif box.mode == OutputMode.MINIMAL:
        lines = [f"  tool: {name}"]
    else:
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

    if is_accessible():
        # Accessible mode: semantic label, no emoji
        header = _rule("─", "Reasoning:", style_fn=dim, width=width)
        if box_mode:
            body = "\n".join(dim(f"  {line}") for line in wrapped)
        else:
            body = "\n".join(dim(f"  {line}") for line in wrapped)
        return f"{header}\n{body}"

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
        if is_accessible():
            return f"[Response]\n" + "\n".join(wrapped)
        header = _rule("─", "Response", style_fn=dim, width=width)
        return f"{header}\n" + "\n".join(wrapped)
    else:
        return "\n".join(wrapped)


def render_done_reason(event: AgentEvent, iterations: int) -> Optional[str]:
    """Render the turn completion reason."""
    reason = event.data.get("reason", "")
    if is_accessible():
        # Accessible mode: text descriptions instead of emoji
        if reason == "max_iterations":
            return warning(
                f"\n  [WARNING] Max iterations ({iterations}) reached. "
                "Type 'continue' or increase --max-iterations."
            )
        elif reason == "max_reflections":
            return warning(f"\n  [REFLECT] Reflective loop detected after {iterations} iterations.")
        elif reason == "interrupted":
            return dim("\n  [INTERRUPTED]")
        elif reason == "error":
            return error("\n  [ERROR] Stream error — turn aborted.")
        return None

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

    box = BoxChars()
    mode = box.mode

    if width is None:
        width = 80

    # Pick style function
    style_fn = {"dim": dim, "error": error, "success": success, "muted": muted}.get(style, dim)

    if mode == OutputMode.MINIMAL:
        # Minimal mode: no boxes
        if title:
            return f"[{title}]\n{content}"
        return content

    inner_width = width - 4

    # Build top border
    if title:
        if mode == OutputMode.ACCESSIBLE:
            title_text = f"[ {title} ]"
            top = title_text + "-" * max(0, width - display_width(title_text))
        else:
            title_text = f" {title} "
            available = width - 2
            title_width = display_width(title_text)
            if title_width > available:
                title_text = title_text[:available]
                title_width = display_width(title_text)
            left = (available - title_width) // 2
            right = available - title_width - left
            if double:
                hz = "═"
                top = "╔" + hz * left + title_text + hz * right + "╗"
            else:
                top = box.tl + box.hz * left + title_text + box.hz * right + box.tr
    else:
        if mode != OutputMode.ACCESSIBLE and double:
            # Only use unicode double borders in unicode mode
            hz = "═"
            top = "╔" + hz * (width - 2) + "╗"
        else:
            top = box.top(width)

    # Build bottom border
    if double:
        if mode == OutputMode.ACCESSIBLE:
            bottom = "-" * width
        elif mode == OutputMode.MINIMAL:
            bottom = ""
        else:
            hz_b = "═" * (width - 2)
            bottom = "╚" + hz_b + "╝"
    else:
        bottom = box.bottom(width)

    lines = content.split("\n")
    result_lines = [style_fn(top)]

    if title:
        result_lines.append(style_fn(f"{box.vt} {' ' * inner_width} {box.vt}"))

    for line in lines:
        if not line.strip():
            if mode != OutputMode.ACCESSIBLE:
                result_lines.append(style_fn(f"{box.vt} {' ' * inner_width} {box.vt}"))
            continue

        # Use display_width-aware wrapping
        wrapped = wrap_text(line, inner_width)
        for w in wrapped:
            if mode == OutputMode.ACCESSIBLE:
                padded = w.ljust(inner_width)
                result_lines.append(style_fn(f"  {padded}"))
            else:
                padded = w.ljust(inner_width)
                result_lines.append(style_fn(f"{box.vt} {padded} {box.vt}"))

    if title:
        result_lines.append(style_fn(f"{box.vt} {' ' * inner_width} {box.vt}"))

    result_lines.append(style_fn(bottom))
    return "\n".join(result_lines)


def _rule(char: str = "─", label: str = "", style_fn=None,
          width: Optional[int] = None) -> str:
    """Draw a horizontal rule, optionally with a label."""
    if width is None:
        width = 80
    style_fn = style_fn or dim

    box = BoxChars()

    if box.mode == OutputMode.MINIMAL:
        if label:
            return f"[{label}]"
        return ""

    if box.mode == OutputMode.ACCESSIBLE:
        # Accessible mode: simpler separators
        if label:
            return style_fn(f"-- {label} --")
        return style_fn("-" * width)

    if label:
        label_str = f" {label} "
        label_width = display_width(label_str)
        remaining = width - label_width
        left = char * (remaining // 2)
        right = char * (remaining - len(left))
        return style_fn(f"{left}{label_str}{right}")
    return style_fn(char * width)


# ── Phase bar ────────────────────────────────────────────────────

_PHASES = ("understand", "plan", "execute", "verify")
_PHASE_ICONS = {"understand": "🔍", "plan": "📋", "execute": "⚡", "verify": "✅"}


def render_phase_bar(phase: str, stats: dict, width: int = 80) -> str:
    """Render a phase progress indicator.

    Shows all four phases with the current one highlighted.
    Returns empty string in minimal mode.
    """
    mode = get_output_mode()
    if mode == OutputMode.MINIMAL:
        return ""

    if mode == OutputMode.ACCESSIBLE:
        segments = []
        for p in _PHASES:
            if p == phase:
                segments.append(f"[{p.upper()}]")
            else:
                segments.append(p)
        return dim("  " + " > ".join(segments))

    # Unicode / ASCII: progress bar style
    segments = []
    current_idx = _PHASES.index(phase) if phase in _PHASES else 0
    for i, p in enumerate(_PHASES):
        icon = _PHASE_ICONS.get(p, "")
        if i < current_idx:
            segments.append(dim(f"{icon} {p}"))
        elif i == current_idx:
            segments.append(f"{icon} {p}")
        else:
            segments.append(dim(f"{icon} {p}"))
    return "  " + "  →  ".join(segments)


def render_turn_stats(stats: dict, width: int = 80) -> str:
    """Render a one-line turn summary: turn number, tools, files, elapsed."""
    turn = stats.get("turn_number", 0)
    tools_run = stats.get("tools_run", 0)
    succeeded = stats.get("tools_succeeded", 0)
    failed = stats.get("tools_failed", 0)
    files = stats.get("files_changed", [])
    elapsed = stats.get("elapsed", 0.0)

    parts = [f"Turn {turn}"]

    tool_str = f"{tools_run} tools"
    if failed > 0:
        tool_str += f" ({succeeded} ok, {failed} failed)"
    parts.append(tool_str)

    n_files = len(files)
    parts.append(f"{n_files} files")

    if elapsed > 0:
        if elapsed < 60:
            parts.append(f"{elapsed:.1f}s")
        else:
            mins = int(elapsed / 60)
            secs = int(elapsed % 60)
            parts.append(f"{mins}m {secs}s")

    return dim("  " + " · ".join(parts))


def render_file_ticker(files: list[str], width: int = 80) -> str:
    """Render changed files as a compact inline list."""
    if not files:
        return ""

    mode = get_output_mode()
    if mode == OutputMode.ACCESSIBLE:
        prefix = "  Files changed: "
    else:
        prefix = "  Files: "

    shown = files[:4]
    more = f" +{len(files) - 4}" if len(files) > 4 else ""
    file_list = ", ".join(shown) + more

    return dim(f"{prefix}{file_list}")
