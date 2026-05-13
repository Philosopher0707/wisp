"""Colored diff rendering for terminal REPL output.

Zero new dependencies — uses the existing wisp.colors module and the
box-drawing primitives from wisp.transport.cli.

Usage::
    from wisp.diff_renderer import colorize_diff, render_diff_box

    colored = colorize_diff(raw_diff_string)
    print(render_diff_box(raw_diff_string, title="Diff"))
"""

from __future__ import annotations

from typing import Optional

from wisp.colors import success, error, info, dim


def colorize_diff(diff_text) -> str:
    """Apply ANSI colors to a plain unified diff.

    Color rules:
      - Lines starting with ``+`` (additions)   → green
      - Lines starting with ``-`` (deletions)   → red
      - ``@@`` hunk headers & ``---``/``+++``   → cyan
      - ``...`` skip markers                     → dim
      - All other lines (context)                → dim

    Args:
        diff_text: Raw plain-text diff string, or a DiffResult object
                   (as produced by wisp/diff.py).

    Returns:
        ANSI-colored diff string.
    """
    # Handle DiffResult objects
    if hasattr(diff_text, 'diff'):
        diff_text = diff_text.diff
    if not diff_text:
        return ""

    lines = diff_text.split("\n")
    colored_lines: list[str] = []

    for line in lines:
        if line.startswith("+"):
            colored_lines.append(success(line))
        elif line.startswith("-"):
            colored_lines.append(error(line))
        elif line.startswith("@@"):
            colored_lines.append(info(line))
        elif line.startswith("---") or line.startswith("+++"):
            colored_lines.append(info(line))
        elif line.strip() == "..." or line.startswith("..."):
            colored_lines.append(dim(line))
        else:
            colored_lines.append(dim(line))

    return "\n".join(colored_lines)


def render_diff_box(
    diff_text,
    title: str = "Diff",
    max_lines: int = 50,
    width: Optional[int] = None,
    box_mode: bool = True,
) -> str:
    """Colorize a diff and wrap it in a box-drawn panel.

    Long diffs (>max_lines) are truncated with a footer showing the
    remaining line count.

    Args:
        diff_text: Raw plain-text diff.
        title: Panel title shown in the top border.
        max_lines: Maximum lines to show before truncation.
        width: Terminal width; auto-detected if None.

    Returns:
        ANSI-colored diff wrapped in a box-drawn panel.
    """
    if not diff_text.strip():
        return ""

    lines = diff_text.strip().split("\n")

    if len(lines) > max_lines:
        shown = lines[:max_lines]
        more = len(lines) - max_lines
        footer = f"... ({more} more lines)"
        diff_text = "\n".join(shown) + "\n" + footer
    else:
        diff_text = "\n".join(lines)

    colored = colorize_diff(diff_text)

    if not box_mode:
        return colored

    # Box the colored diff
    if width is None:
        import shutil
        try:
            width = shutil.get_terminal_size().columns
        except OSError:
            width = 80
        width = max(40, min(width, 160))

    return _box_panel(colored, title=title, width=width)


def _box_panel(content: str, title: str = "", width: int = 80) -> str:
    """Wrap content in a box-drawn panel.  (Lightweight duplicate of
    transport/cli._box so diff_renderer stays self-contained.)
    """
    tl, tr, bl, br, hz, vt = "\u250c", "\u2510", "\u2514", "\u2518", "\u2500", "\u2502"  # ┌┐└┘─│
    inner_width = width - 4  # borders + padding

    import textwrap
    from wisp.colors import dim as _dim

    # Title bar
    if title:
        title_text = f" {title} "
        available = width - 2
        if len(title_text) > available:
            title_text = title_text[:available]
        top = tl + title_text + hz * (width - 2 - len(title_text)) + tr
    else:
        top = tl + hz * (width - 2) + tr

    bottom = bl + hz * (width - 2) + br

    result_lines = [_dim(top)]
    for line in content.split("\n"):
        # Already colored lines — just pad to inner_width with spaces
        # We can't use ANSI-aware width measurement so we use a rough
        # visual padding approach: pad the inner content visually
        raw = _strip_for_width(line)
        padding = max(0, inner_width - len(raw))
        result_lines.append(_dim(f"{vt} {line}{' ' * padding} {vt}"))
    result_lines.append(_dim(bottom))

    return "\n".join(result_lines)


def _strip_for_width(text: str) -> str:
    """Strip ANSI codes to measure visible width."""
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", text)
