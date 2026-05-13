"""Rich-powered diff rendering for terminal REPL output.

Uses Rich Panel + Text for styled diffs with:
  - Green/red background for added/deleted lines
  - Diff-match-patch for intra-line change highlighting
  - Proper theme support, width adaptation, and NO_COLOR respect

Usage::
    from wisp.diff_renderer import colorize_diff, render_diff_box, render_diff_panel

    colored = colorize_diff(raw_diff_string)
    print(render_diff_box(raw_diff_string, title="Diff"))
"""

from __future__ import annotations

from io import StringIO
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.style import Style
from rich.text import Text

# ── Diff styles ────────────────────────────────────────────────────────

_ADD_STYLE = Style(color="green", bgcolor="#1a3a1a")
_DEL_STYLE = Style(color="red", bgcolor="#3a1a1a")
_HUNK_STYLE = Style(color="cyan", bold=True, bgcolor="#1a2a3a")
_HEADER_STYLE = Style(color="bright_cyan", bold=True)
_CONTEXT_STYLE = Style(color="#888888")
_SKIP_STYLE = Style(color="#666666", italic=True)

# Intra-line change styles (for diff-match-patch word-level highlighting)
_ADD_CHANGE_STYLE = Style(color="#00ff00", bold=True, bgcolor="#0a2a0a")
_DEL_CHANGE_STYLE = Style(color="#ff4444", bold=True, bgcolor="#2a0a0a")


def _build_diff_line(line: str) -> Text:
    """Build a Rich Text object for a single diff line with styling."""
    stripped = line.rstrip("\n")

    if stripped.startswith("+"):
        return _build_changed_line(stripped, is_add=True)
    elif stripped.startswith("-"):
        return _build_changed_line(stripped, is_add=False)
    elif stripped.startswith("@@"):
        return Text(stripped, style=_HUNK_STYLE)
    elif stripped.startswith("---") or stripped.startswith("+++"):
        return Text(stripped, style=_HEADER_STYLE)
    elif stripped.strip() in ("...",) or stripped.startswith("..."):
        return Text(stripped, style=_SKIP_STYLE)
    else:
        return Text(stripped, style=_CONTEXT_STYLE)


def _build_changed_line(line: str, is_add: bool) -> Text:
    """Build a changed line with intra-line word-level highlighting."""
    prefix = line[0]   # '+' or '-'
    content = line[1:]  # the actual code

    # Try diff-match-patch for word-level precision
    try:
        from diff_match_patch import diff_match_patch
        # For intra-line highlighting, we compare the content portion
        # against itself — but we need to know what changed.
        # We apply dmp within pairs of added/deleted lines at the caller level.
        # For individual line rendering, apply base style.
        pass
    except ImportError:
        pass

    base_style = _ADD_STYLE if is_add else _DEL_STYLE
    return Text(f"{prefix}{content}", style=base_style)


def colorize_diff(diff_text) -> str:
    """Apply Rich ANSI colors to a unified diff.

    Args:
        diff_text: Raw plain-text diff string, or a DiffResult object.

    Returns:
        ANSI-colored diff string.
    """
    if hasattr(diff_text, 'diff'):
        diff_text = diff_text.diff
    if not diff_text:
        return ""

    lines = diff_text.strip().split("\n")
    text = Text()
    for i, line in enumerate(lines):
        if i > 0:
            text.append("\n")
        text.append(_build_diff_line(line))
    return str(text)


def render_diff_box(
    diff_text,
    title: str = "Diff",
    max_lines: int = 50,
    width: Optional[int] = None,
    box_mode: bool = True,
) -> str:
    """Colorize a diff and wrap it in a Rich Panel.

    Args:
        diff_text: Raw plain-text diff.
        title: Panel title.
        max_lines: Max lines before truncation.
        width: Terminal width; auto-detected if None.
        box_mode: If False, return plain colored text (no box).

    Returns:
        ANSI-colored diff wrapped in a Rich Panel.
    """
    return render_diff_panel(diff_text, title=title, max_lines=max_lines,
                             width=width, box_mode=box_mode)


def render_diff_panel(
    diff_text,
    title: str = "Diff",
    max_lines: int = 50,
    width: Optional[int] = None,
    box_mode: bool = True,
    language: Optional[str] = None,
) -> str:
    """Render a diff as a Rich Panel with colored backgrounds.

    With diff-match-patch installed, adds word-level change highlighting
    within added/deleted lines.

    Args:
        diff_text: Raw plain-text diff string.
        title: Panel title shown in the top border.
        max_lines: Maximum lines to show before truncation.
        width: Terminal width; auto-detected from terminal if None.
        box_mode: If False, return plain colored text (no Panel).
        language: Optional language for syntax highlighting (stretch goal).

    Returns:
        ANSI-colored diff string.
    """
    if hasattr(diff_text, 'diff'):
        diff_text = diff_text.diff
    if not diff_text or not diff_text.strip():
        return ""

    lines = diff_text.strip().split("\n")

    # Truncate long diffs
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        footer = f"... ({len(diff_text.strip().split(chr(10))) - max_lines} more lines)"
        lines.append(footer)

    # Build Rich Text with intra-line highlighting
    text = _build_diff_text_with_highlighting(lines)

    if not box_mode:
        return str(text)

    # Wrap in Rich Panel and render to ANSI string
    panel = Panel(
        text,
        title=title,
        border_style=Style(color="#555555"),
        width=width,
        padding=(0, 1),
    )
    buf = StringIO()
    console = Console(file=buf, width=width or 120, force_terminal=True)
    console.print(panel)
    return buf.getvalue().rstrip("\n")


def _build_diff_text_with_highlighting(lines: list[str]) -> Text:
    """Build a Rich Text from diff lines, with word-level highlighting
    when diff-match-patch is available."""
    text = Text()

    dmp = None
    try:
        from diff_match_patch import diff_match_patch
        dmp = diff_match_patch()
    except ImportError:
        pass

    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\n")

        if dmp and line.startswith("-") and i + 1 < len(lines) and lines[i + 1].startswith("+"):
            old_content = line[1:]
            new_content = lines[i + 1][1:]
            diffs = dmp.diff_main(old_content, new_content)
            dmp.diff_cleanupSemantic(diffs)

            if i > 0:
                text.append("\n")
            text.append("-", style=_DEL_STYLE)
            for op, fragment in diffs:
                if op == 0:    # equal
                    text.append(fragment, style=_DEL_STYLE)
                elif op == -1: # deleted from old
                    text.append(fragment, style=_DEL_CHANGE_STYLE)

            text.append("\n")
            text.append("+", style=_ADD_STYLE)
            for op, fragment in diffs:
                if op == 0:    # equal
                    text.append(fragment, style=_ADD_STYLE)
                elif op == 1:  # added in new
                    text.append(fragment, style=_ADD_CHANGE_STYLE)
            i += 2
            continue

        if i > 0:
            text.append("\n")
        text.append(_build_diff_line(line))
        i += 1

    return text


def _build_word_level_line(content: str, is_add: bool) -> Text:
    """Build a single line with word-level change highlighting."""
    prefix = "+" if is_add else "-"
    base_style = _ADD_STYLE if is_add else _DEL_STYLE
    change_style = _ADD_CHANGE_STYLE if is_add else _DEL_CHANGE_STYLE

    text = Text(prefix, style=base_style)

    try:
        from diff_match_patch import diff_match_patch
        dmp = diff_match_patch()
        diffs = dmp.diff_main(content, content)  # identity diff — always same
    except ImportError:
        text.append(content, style=base_style)
        return text

    # For word-level diff between old and new, we need the old_content at this point.
    # Since _build_word_level_line doesn't have the old line, we fall back to
    # just highlighting the entire line. The real intra-line diff happens in
    # _build_diff_text_with_highlighting where we have both old and new.
    text.append(content, style=base_style)
    return text
