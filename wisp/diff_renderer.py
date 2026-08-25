"""Rich-powered diff rendering for terminal REPL output.

Uses Rich Panel + Text + pygments for styled diffs with:
  - Green/red background for added/deleted lines
  - Diff-match-patch for intra-line change highlighting  
  - pygments tokens for per-line syntax coloring
  - Proper theme support, width adaptation, and NO_COLOR respect
"""

from __future__ import annotations

import functools

from io import StringIO
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.style import Style
from rich.text import Text

# ── Diff styles ────────────────────────────────────────────────────────

_ADD_STYLE = Style(color="#80ff80", bgcolor="#1a3a1a")
_DEL_STYLE = Style(color="#ff8080", bgcolor="#3a1a1a")
_HUNK_STYLE = Style(color="cyan", bold=True, bgcolor="#1a2a3a")
_HEADER_STYLE = Style(color="bright_cyan", bold=True)
_CONTEXT_STYLE = Style(color="#aaaaaa")
_SKIP_STYLE = Style(color="#666666", italic=True)
_ADD_CHANGE = Style(color="#00ff44", bold=True, bgcolor="#0a2a0a")
_DEL_CHANGE = Style(color="#ff4444", bold=True, bgcolor="#2a0a0a")

# Pygments token type → Rich color (monokai-inspired)
_TOKEN_COLORS = {
    "Token.Keyword": "#f92672", "Token.Keyword.Namespace": "#f92672",
    "Token.Keyword.Type": "#66d9ef", "Token.Keyword.Declaration": "#f92672",
    "Token.Name.Function": "#a6e22e", "Token.Name.Class": "#a6e22e",
    "Token.Name.Decorator": "#a6e22e", "Token.Name.Builtin": "#a6e22e",
    "Token.Name.Tag": "#f92672", "Token.Name.Attribute": "#a6e22e",
    "Token.Name.Constant": "#ae81ff",
    "Token.Name.Variable": "#f8f8f2",
    "Token.Name": "#f8f8f2", "Token.Name.Other": "#f8f8f2",
    "Token.Literal.String": "#e6db74",
    "Token.Literal.String.Doc": "#e6db74",
    "Token.Literal.String.Double": "#e6db74",
    "Token.Literal.String.Single": "#e6db74",
    "Token.Literal.String.Backtick": "#e6db74",
    "Token.Literal.String.Char": "#e6db74",
    "Token.Literal.String.Escape": "#ae81ff",
    "Token.Literal.String.Interpol": "#e6db74",
    "Token.Literal.String.Other": "#e6db74",
    "Token.Literal.String.Regex": "#e6db74",
    "Token.Literal.String.Symbol": "#e6db74",
    "Token.Literal.Number": "#ae81ff",
    "Token.Literal.Number.Integer": "#ae81ff",
    "Token.Literal.Number.Float": "#ae81ff",
    "Token.Literal.Number.Hex": "#ae81ff",
    "Token.Literal.Number.Oct": "#ae81ff",
    "Token.Comment": "#75715e",
    "Token.Comment.Single": "#75715e",
    "Token.Comment.Multiline": "#75715e",
    "Token.Comment.Special": "#75715e",
    "Token.Operator": "#f92672", "Token.Operator.Word": "#f92672",
    "Token.Punctuation": "#f8f8f2",
    "Token.Text": "#f8f8f2",
}


@functools.lru_cache(maxsize=32)
def _get_lexer(language: str):
    """Cache lexers per language: a multi-hunk diff re-requests its lexer
    for every line, and get_lexer_by_name is not free."""
    try:
        from pygments.lexers import get_lexer_by_name

        return get_lexer_by_name(language, ensurenl=False)
    except Exception:
        return None


def _pygmentize(code: str, language: str, base_style: Style) -> Text:
    """Syntax-highlight a line of code using pygments, merged with base_style."""
    result = Text()
    try:
        from pygments import lex
        # ensurenl=False: pygments otherwise appends a phantom "\n"
        # token to every line, double-spacing the whole diff box.
        lexer = _get_lexer(language)
        if lexer is None:
            return result
        for ttype, text in lex(code, lexer):
            color = _TOKEN_COLORS.get(str(ttype), "#f8f8f2")
            merged = Style.combine([base_style, Style(color=color)])
            result.append(text, style=merged)
    except Exception:
        result.append(code, style=base_style)
    return result


_LINE_NUM_STYLE = Style(color="#555555")



def _parse_diff_line_parts(line: str) -> tuple[str, str, str]:
    """Extract (prefix, line_number, code) from a diff line.

    Diff lines from generate_diff_string look like:
      '+  42 def hello():'  → ('+', '42', 'def hello():')
      '-   1 import os'    → ('-', '1', 'import os')
      '     5     pass'    → (' ', '5', '    pass')
      '...'               → ('', '', '...')

    Returns (prefix, line_num, code).  line_num is "" when none detected.
    """
    if len(line) < 2:
        return ("", "", line)
    prefix = line[0]
    if prefix not in ("+", "-", " "):
        return ("", "", line)
    rest = line[1:]
    # Skip leading spaces after prefix
    rest = rest.lstrip(" ")
    # Read digits (line number)
    i = 0
    while i < len(rest) and rest[i].isdigit():
        i += 1
    # If we found digits and a following space, split the number
    if i > 0 and i < len(rest) and rest[i] == " ":
        return (prefix, rest[:i], rest[i + 1:])
    return (prefix, "", line[1:])


def _build_diff_line(line: str, language: Optional[str] = None, num_width: int = 3) -> Text:
    """Build a Rich Text for a single diff line, with line numbers.

    Format (tight, no extra gaps):
         11  
         12   from __future__ import annotations
         14  +import warnings
         17   import asyncio
    """
    stripped = line.rstrip("\n")

    prefix, line_num, code = _parse_diff_line_parts(stripped)

    # Right-aligned line number in a compact column
    num_str = f"{line_num:>{num_width}} " if line_num else " " * (num_width + 1)

    if prefix == "+":
        text = Text(f"{num_str}+", style=_ADD_STYLE)
        if language and code.strip():
            text.append_text(_pygmentize(code, language, _ADD_STYLE))
        else:
            text.append(code, style=_ADD_STYLE)
        return text
    elif prefix == "-":
        text = Text(f"{num_str}-", style=_DEL_STYLE)
        if language and code.strip():
            text.append_text(_pygmentize(code, language, _DEL_STYLE))
        else:
            text.append(code, style=_DEL_STYLE)
        return text
    elif stripped.startswith("@@"):
        return Text(stripped, style=_HUNK_STYLE)
    elif stripped.startswith("---") or stripped.startswith("+++"):
        return Text(stripped, style=_HEADER_STYLE)
    elif stripped.strip() in ("...",) or stripped.startswith("..."):
        return Text(stripped, style=_SKIP_STYLE)
    else:
        text = Text(num_str, style=_CONTEXT_STYLE)
        if language and code.strip():
            text.append_text(_pygmentize(code, language, _CONTEXT_STYLE))
        else:
            text.append(code, style=_CONTEXT_STYLE)
        return text


def colorize_diff(diff_text, language: Optional[str] = None, num_width: int = 3) -> str:
    """Apply Rich ANSI colors to a unified diff."""
    if hasattr(diff_text, 'diff'):
        diff_text = diff_text.diff
    if not diff_text:
        return ""
    lines = diff_text.strip().split("\n")
    text = Text()
    for i, line in enumerate(lines):
        if i > 0:
            text.append("\n")
        text.append(_build_diff_line(line, language=language, num_width=num_width))
    return str(text)


def shorten_diff_title(path: str, max_len: int = 60) -> str:
    """Build a 'Diff — <path>' title that never cuts the filename off.

    Long paths collapse to 'Diff — …<tail>' keeping the basename intact,
    instead of a mid-directory slice that hides which file changed.
    """
    title = f"Diff — {path}"
    if len(title) <= max_len:
        return title
    base = Path(path).name or path
    leader = "Diff — …"
    room = max_len - len(leader)
    if len(base) > room:
        base = base[-room:]
    return leader + base


def render_diff_box(
    diff_text, title: str = "Diff", max_lines: int = 50,
    width: Optional[int] = None, box_mode: bool = True,
    language: Optional[str] = None,
    plain: bool = False,
) -> str:
    """Colorize a diff and wrap it in a Rich Panel."""
    return render_diff_panel(diff_text, title=title, max_lines=max_lines,
                             width=width, box_mode=box_mode, language=language,
                             plain=plain)


def render_diff_panel(
    diff_text, title: str = "Diff", max_lines: int = 50,
    width: Optional[int] = None, box_mode: bool = True,
    language: Optional[str] = None,
    plain: bool = False,
) -> str:
    """Render a diff as a Rich Panel with syntax-colored backgrounds.

    Args:
        diff_text: Raw plain-text diff string.
        title: Panel title.
        max_lines: Max lines before truncation.
        width: Terminal width.
        box_mode: If False, return plain colored text.
        language: Pygments language name (e.g. "python", "rust").
        plain: No ANSI at all — for accessible/screen-reader output.

    Returns:
        ANSI-colored diff string (or bare text when plain=True).
    """
    if hasattr(diff_text, 'diff'):
        diff_text = diff_text.diff
    if not diff_text or not diff_text.strip():
        return ""

    lines = diff_text.strip().split("\n")
    if len(lines) > max_lines:
        total = len(lines)
        lines = lines[:max_lines]
        lines.append(f"... ({total - max_lines} more lines)")

    # Compute tight line-number width from actual content
    num_width = 2
    for ln in lines:
        _, num, _ = _parse_diff_line_parts(ln.rstrip("\n"))
        if num:
            num_width = max(num_width, len(num))

    text = _build_diff_text_with_dmp(lines, language=None if plain else language,
                                     num_width=num_width)

    buf = StringIO()
    console = Console(
        file=buf, width=width or 120,
        force_terminal=not plain, no_color=plain,
    )

    if box_mode and title:
        if plain:
            console.print(Text(f"[Diff] {title}"))
        else:
            console.print(Text(f"─── {title}", style=Style(color="#555555")))

    console.print(text)
    return buf.getvalue().rstrip("\n")


def _build_diff_text_with_dmp(lines: list[str],
                               language: Optional[str] = None,
                               num_width: int = 3) -> Text:
    """Build Rich Text with word-level diff and syntax coloring."""
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
            old_pref, old_num, old_code = _parse_diff_line_parts(line)
            new_pref, new_num, new_code = _parse_diff_line_parts(lines[i + 1])
            diffs = dmp.diff_main(old_code, new_code)
            dmp.diff_cleanupSemantic(diffs)

            old_num_str = f"{old_num:>{num_width}} " if old_num else " " * (num_width + 1)
            new_num_str = f"{new_num:>{num_width}} " if new_num else " " * (num_width + 1)

            if i > 0:
                text.append("\n")
            text.append(f"{old_num_str}-", style=_DEL_STYLE)
            for op, fragment in diffs:
                if op == 1:  # addition — skip on deletion line
                    continue
                style = _DEL_CHANGE if op == -1 else _DEL_STYLE
                text.append(fragment, style=style)
            text.append("\n")
            text.append(f"{new_num_str}+", style=_ADD_STYLE)
            for op, fragment in diffs:
                if op == -1:  # deletion — skip on addition line
                    continue
                style = _ADD_CHANGE if op == 1 else _ADD_STYLE
                text.append(fragment, style=style)
            i += 2
            continue

        if i > 0:
            text.append("\n")
        text.append(_build_diff_line(line, language=language, num_width=num_width))
        i += 1
    return text
