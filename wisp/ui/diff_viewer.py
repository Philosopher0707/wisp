"""Structured diff formatter and Rich viewer for file modifications in Wisp.

Provides:
- Line delta calculations (+N / -M lines) and code context detection
- Unified diff computation using Python's standard difflib
- Rich syntax-highlighted diff rendering via rich.syntax.Syntax and rich.panel.Panel
- Plain-text / accessible fallback rendering
"""

from __future__ import annotations

import difflib
from io import StringIO
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text


def extract_change_summary(old_text: str, new_text: str) -> str:
    """Extract a concise description of the modified code block (function, class, or snippet)."""
    if not old_text and new_text:
        first_line = next((l.strip() for l in new_text.splitlines() if l.strip()), "")
        if first_line.startswith(("def ", "async def ", "class ")):
            return f"added {first_line.split('(')[0].split(':')[0].strip()}()"
        return f"new file ({len(new_text.splitlines())} lines)"

    if old_text and not new_text:
        return f"deleted file ({len(old_text.splitlines())} lines)"

    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()

    # Search for enclosing or modified function/class definitions
    for line in new_lines + old_lines:
        stripped = line.strip()
        if stripped.startswith("def ") or stripped.startswith("async def "):
            fn_name = stripped.split("(")[0].replace("async def ", "def ").strip()
            return f"in {fn_name}()"
        if stripped.startswith("class "):
            cls_name = stripped.split("(")[0].split(":")[0].strip()
            return f"in {cls_name}"

    # Fallback to examining unified diff hunks
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
    for line in diff:
        if line.startswith("@@"):
            parts = line.split("@@", 2)
            if len(parts) >= 3 and parts[2].strip():
                return f"near {parts[2].strip()}"
        elif line.startswith("+") and not line.startswith("+++"):
            stripped = line[1:].strip()
            if stripped:
                if len(stripped) > 40:
                    stripped = stripped[:37] + "..."
                return f"modifying: {stripped}"
        elif line.startswith("-") and not line.startswith("---"):
            stripped = line[1:].strip()
            if stripped:
                if len(stripped) > 40:
                    stripped = stripped[:37] + "..."
                return f"modifying: {stripped}"

    return "modified block"


def compute_diff_stats(old_text: str, new_text: str) -> tuple[int, int, str]:
    """Compute (added_lines, removed_lines, summary) from old_text and new_text."""
    if old_text == new_text:
        return 0, 0, "no changes"

    old_lines = (old_text + "\n" if old_text and not old_text.endswith("\n") else old_text).splitlines(keepends=True)
    new_lines = (new_text + "\n" if new_text and not new_text.endswith("\n") else new_text).splitlines(keepends=True)

    diff = list(difflib.unified_diff(old_lines, new_lines, fromfile="before", tofile="after"))
    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    summary = extract_change_summary(old_text, new_text)

    return added, removed, summary


def generate_unified_diff(
    old_text: str,
    new_text: str,
    file_path: str = "file",
    n_context: int = 3,
) -> str:
    """Generate a clean unified diff string using Python's difflib.unified_diff."""
    old_lines = (old_text + "\n" if old_text and not old_text.endswith("\n") else old_text).splitlines(keepends=True)
    new_lines = (new_text + "\n" if new_text and not new_text.endswith("\n") else new_text).splitlines(keepends=True)

    diff_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            n=n_context,
        )
    )

    if not diff_lines:
        return f"# No changes detected in {file_path}\n"

    return "".join(diff_lines)


def create_diff_panel(
    old_text: str,
    new_text: str,
    file_path: str = "file",
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    theme: str = "monokai",
    line_numbers: bool = True,
    max_height: Optional[int] = None,
) -> Panel:
    """Render a unified diff inside a rich.panel.Panel using rich.syntax.Syntax with the 'diff' lexer."""
    added, removed, summary = compute_diff_stats(old_text, new_text)
    diff_text = generate_unified_diff(old_text, new_text, file_path=file_path)

    syntax = Syntax(
        diff_text,
        lexer="diff",
        theme=theme,
        line_numbers=line_numbers,
        word_wrap=True,
        background_color="default",
    )

    panel_title = title or f"[bold cyan]Diff:[/bold cyan] [white]{file_path}[/white]"
    panel_subtitle = subtitle or f"[green]+{added}[/green] / [red]-{removed}[/red] lines · {summary}"

    return Panel(
        syntax,
        title=panel_title,
        subtitle=panel_subtitle,
        border_style="cyan",
        padding=(0, 1),
        height=max_height,
    )


def render_diff_string(
    old_text: str,
    new_text: str,
    file_path: str = "file",
    width: int = 100,
    plain: bool = False,
) -> str:
    """Render the diff panel or unified diff to a string."""
    if plain:
        diff_str = generate_unified_diff(old_text, new_text, file_path=file_path)
        added, removed, summary = compute_diff_stats(old_text, new_text)
        header = f"--- Diff: {file_path} (+{added} / -{removed} lines | {summary}) ---\n"
        return header + diff_str

    panel = create_diff_panel(old_text, new_text, file_path=file_path)
    buf = StringIO()
    console = Console(file=buf, width=width, force_terminal=True)
    console.print(panel)
    return buf.getvalue().rstrip("\n")
