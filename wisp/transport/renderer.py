"""CLI rendering utilities — pure functions for formatting terminal output.

Extracted from wisp/transport/cli.py to make rendering logic testable
and reusable across transports.

Uses width-aware rendering (CJK, emoji, combining chars) with fallback
modes: unicode, ascii, accessible, minimal.
"""

from __future__ import annotations

from typing import Optional

from wisp.colors import bold, dim, error, warning, success, accent
from wisp.core.events import AgentEvent
from wisp.terminal_width import (
    display_width,
    pad_right,
    wrap_text_wide,
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
    """Render buffered content text as a block.

    The assistant's answer is left to breathe — no framing rule. Accessible
    mode keeps a ``[Response]`` label so screen readers can announce it.
    """
    if not text.strip():
        return None
    inner_w = width - 4
    wrapped = wrap_text(text.strip(), inner_w)
    if is_accessible():
        return "[Response]\n" + "\n".join(wrapped)
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
            padded = pad_right(w, inner_width)
            if mode == OutputMode.ACCESSIBLE:
                result_lines.append(style_fn(f"  {padded}"))
            else:
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


def render_phase_bar(phase: str, stats: dict, width: int = 80) -> str:
    """Render a phase progress indicator.

    Done phases get a check, the current phase gets a pointer and bold name,
    future phases get an empty circle. Returns empty string in minimal mode.
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

    current_idx = _PHASES.index(phase) if phase in _PHASES else 0
    segments = []
    for i, p in enumerate(_PHASES):
        if i < current_idx:
            mark = "✓" if mode == OutputMode.UNICODE else "x"
            segments.append(dim(f"{mark} {p}"))
        elif i == current_idx:
            ptr = "❯" if mode == OutputMode.UNICODE else ">"
            segments.append(f"{bold(ptr)} {bold(p)}")
        else:
            mark = "○" if mode == OutputMode.UNICODE else "o"
            segments.append(dim(f"{mark} {p}"))
    return "  " + "  ".join(segments)


def render_turn_stats(stats: dict, width: int = 80) -> str:
    """Render a one-line turn summary: turn number, tools, files, elapsed."""
    mode = get_output_mode()
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

    # Context meter: estimate only — labeled as such by position, never
    # presented as billing truth. Hidden without a limit to compare against.
    ctx_tokens = stats.get("ctx_tokens")
    ctx_limit = stats.get("ctx_limit")
    if ctx_tokens and ctx_limit and mode != OutputMode.MINIMAL:
        pct = round(100.0 * ctx_tokens / max(1, ctx_limit))
        k = ctx_tokens / 1024.0
        ctx_str = f"{k:.0f}k" if k >= 1 else f"{ctx_tokens}"
        parts.append(f"ctx {ctx_str} ({pct}%)")

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


def render_provider_status(event: AgentEvent, width: int = 80) -> Optional[str]:
    """Render a provider availability change (circuit breaker lifecycle).

    circuit_open warns with the retry horizon; circuit_closed confirms
    recovery. Minimal mode returns empty — the error event accompanying an
    open circuit carries the turn-relevant information.
    """
    status = str(event.data.get("status", ""))
    detail = str(event.data.get("detail", "")).rstrip()
    retry_after = event.data.get("retry_after")
    mode = get_output_mode()

    if mode == OutputMode.MINIMAL:
        return ""

    retry_part = ""
    if isinstance(retry_after, (int, float)) and retry_after > 0:
        retry_part = f" — retry in ~{retry_after:.0f}s"

    if status == "circuit_open":
        if mode == OutputMode.ACCESSIBLE:
            return warning(f"\n  [PROVIDER] Circuit open. {detail}{retry_part}")
        marker = "◌" if mode == OutputMode.UNICODE else "-"
        return warning(f"\n  {marker} Provider paused{retry_part}")

    if status == "circuit_closed":
        if mode == OutputMode.ACCESSIBLE:
            return success(f"\n  [PROVIDER] Recovered. {detail}".rstrip())
        check = "✓" if mode == OutputMode.UNICODE else "+"
        return success(f"\n  {check} Provider recovered")

    return None


def render_subagent_status(event: AgentEvent, width: int = 80) -> Optional[str]:
    """Render a subagent lifecycle update from the orchestrator.

    task_started announces the child, task_completed confirms with elapsed
    time, task_failed/task_retry warn. Minimal mode returns empty — the
    final tool_result already carries the full outcome for the transcript.
    """
    kind = str(event.data.get("kind", ""))
    role = str(event.data.get("role", "")).strip()
    name = str(event.data.get("name", "")).strip()
    detail = str(event.data.get("detail", "")).rstrip()
    mode = get_output_mode()

    if mode == OutputMode.MINIMAL:
        return ""

    who = f"[{role}]" if role else (f"[{name}]" if name else "[subagent]")
    body = f"{who} {detail}".rstrip()

    if kind == "task_started":
        if mode == OutputMode.ACCESSIBLE:
            return dim(f"\n  [SUBAGENT] Started. {body}")
        marker = "🧬" if mode == OutputMode.UNICODE else ">"
        return accent(f"\n  {marker} {body}")
    if kind == "task_progress":
        return dim(f"\n  · {body}")
    if kind == "task_completed":
        if mode == OutputMode.ACCESSIBLE:
            return success(f"\n  [SUBAGENT] Done. {body}")
        check = "✓" if mode == OutputMode.UNICODE else "+"
        return success(f"\n  {check} {body}")
    if kind == "task_retry":
        if mode == OutputMode.ACCESSIBLE:
            return warning(f"\n  [SUBAGENT] Retrying. {body}")
        marker = "↻" if mode == OutputMode.UNICODE else "~"
        return warning(f"\n  {marker} {body}")
    if kind == "task_failed":
        if mode == OutputMode.ACCESSIBLE:
            return error(f"\n  [SUBAGENT] Failed. {body}")
        cross = "✗" if mode == OutputMode.UNICODE else "x"
        return error(f"\n  {cross} {body}")

    return None


# ── Background agents ────────────────────────────────────────────

_AGENT_STATUS_ORDER = {"running": 0, "completed": 1, "failed": 2, "cancelled": 3}


def _agent_status_mark(status: str) -> tuple[str, str]:
    """(display mark, accessible word) for a background-agent status."""
    marks = {
        "running": ("●", "o", "RUNNING"),
        "completed": ("✓", "+", "DONE"),
        "failed": ("✗", "x", "FAILED"),
        "cancelled": ("⏹", "[]", "CANCELLED"),
    }
    uni, ascii_, word = marks.get(status, ("·", "-", status.upper()))
    mode = get_output_mode()
    if mode == OutputMode.ACCESSIBLE:
        return f"[{word}]", word
    return (uni if mode == OutputMode.UNICODE else ascii_), word


def _truncate(text: str, width: int) -> str:
    if width <= 0 or display_width(text) <= width:
        return text
    out = ""
    for ch in text:
        if display_width(out) + display_width(ch) > max(0, width - 3):
            return out + "..."
        out += ch
    return out


def render_background_agents(entries: list[dict], width: int = 80) -> str:
    """Render the background-agent registry table.

    Pure function over snapshot dicts (BackgroundAgentManager.snapshot()).
    Running agents sort first. All four output modes handled.
    """
    if not entries:
        mode = get_output_mode()
        hint = "Launch one with spawn_background."
        if mode == OutputMode.MINIMAL:
            return f"No background agents. {hint}"
        return dim(f"No background agents. {hint}")

    mode = get_output_mode()
    ordered = sorted(entries, key=lambda e: (
        _AGENT_STATUS_ORDER.get(e.get("status", ""), 9),
        -(e.get("elapsed_seconds", 0.0) or 0.0),
    ))

    lines: list[str] = []
    for e in ordered:
        mark, _word = _agent_status_mark(e.get("status", ""))
        agent_id = e.get("agent_id", "?")
        label = e.get("label", "")
        role = e.get("role", "generalist")
        turns = e.get("turns", 0)
        elapsed = e.get("elapsed_seconds", 0.0) or 0.0
        elapsed_str = (
            f"{elapsed:.0f}s" if elapsed < 60
            else f"{int(elapsed // 60)}m{int(elapsed % 60):02d}s"
        )
        task = str(e.get("task", "")).replace("\n", " ")

        if mode == OutputMode.MINIMAL:
            lines.append(f"{e.get('status', '?')} {agent_id} {elapsed_str} t{turns}")
            continue

        head = f"{mark} {agent_id} {label} ({role}) {elapsed_str}"
        if turns > 1:
            head += f" turn {turns}"

        if mode == OutputMode.ACCESSIBLE:
            lines.append(f"  [{_accessible_status(e.get('status', ''))}] {agent_id} {label} "
                         f"(role: {role}, elapsed: {elapsed_str}, turns: {turns})")
            if task:
                lines.append(f"    task: {_truncate(task, max(20, width - 10))}")
            result = e.get("result")
            if isinstance(result, dict):
                if result.get("error"):
                    lines.append(f"    error: {_truncate(str(result['error']), max(20, width - 12))}")
                elif result.get("summary"):
                    lines.append(f"    summary: {_truncate(str(result['summary']), max(20, width - 14))}")
            continue

        # unicode / ascii: one line, task snippet fills remaining width
        meta_w = display_width(head) + 6
        snippet = _truncate(task, max(10, width - meta_w))
        line = f"{head}  -  {snippet}" if snippet else head

        status = e.get("status", "")
        if status == "completed":
            lines.append(success(line))
        elif status == "failed":
            lines.append(error(line))
        elif status == "cancelled":
            lines.append(warning(line))
        else:
            lines.append(accent(line))

    header = f"{len(ordered)} background agent(s)"
    return dim(header) + "\n" + "\n".join(lines)


def _accessible_status(status: str) -> str:
    return {
        "running": "RUNNING",
        "completed": "DONE",
        "failed": "FAILED",
        "cancelled": "CANCELLED",
    }.get(status, status.upper())


def render_agent_detail(snapshot: dict, width: int = 80) -> str:
    """Render one background agent's full snapshot (fields + latest result)."""
    mode = get_output_mode()
    if not snapshot:
        return error("No such agent.")

    mark, _ = _agent_status_mark(snapshot.get("status", ""))
    task_line = str(snapshot.get("task", "")).replace("\n", " ")
    content = "\n".join([
        f"id:     {snapshot.get('agent_id', '?')}",
        f"label:  {snapshot.get('label', '')}",
        f"role:   {snapshot.get('role', 'generalist')}",
        f"status: {snapshot.get('status', '?')} {mark}".rstrip() if mode != OutputMode.MINIMAL
        else f"status: {snapshot.get('status', '?')}",
        f"turns:  {snapshot.get('turns', 0)}",
        f"task:   {_truncate(task_line, max(20, width - 10))}",
    ])

    result = snapshot.get("result")
    if isinstance(result, dict):
        if result.get("error"):
            content += f"\nerror:  {_truncate(str(result['error']), max(20, width - 10))}"
        if result.get("files"):
            content += f"\nfiles:  {', '.join(result['files'])}"
        summary = (result.get("summary") or "").strip()
        if summary:
            wrapped = wrap_text(summary, max(24, width - 8), indent="  ")
            content += "\n\n" + "\n".join(wrapped)

    if mode == OutputMode.MINIMAL:
        return content
    return _box(content, title=f"Agent {snapshot.get('agent_id', '?')}")
