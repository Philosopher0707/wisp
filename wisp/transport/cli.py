"""CLI transport for Wisp — renders AgentEvent to terminal, handles user input.

This module contains all I/O-specific code: printing, colors, readline,
signal handling, and approval prompts. It wraps WispAgentCore and drives
the REPL loop.

Output is rendered with enterprise-grade structure:
  - Buffered thinking/content for clean phase transitions (no typewriter flicker)
  - Box-drawn panels for multi-line tool output and responses
  - Width-adaptive layout respecting terminal size
  - Hierarchical indentation: primary > meta > debug
  - Graceful degradation: flat mode for pipes, narrow terminals, and compact_mode
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import shutil
import signal
import sys
import textwrap
import threading
import time
import weakref
from pathlib import Path as PathLib
from typing import Optional, Generator

# Enable readline for line-editing and history in REPL
try:
    import readline
except ImportError:
    readline = None

from wisp.core.agent import WispAgentCore, _coerce_tool_data
from wisp.core.message_format import extract_text
from wisp.core.events import (
    AgentEvent,
    TYPE_CONTENT,
    TYPE_THINKING,
    TYPE_TOOL_CALL,
    TYPE_TOOL_RESULT,
    TYPE_ERROR,
    TYPE_DONE,
    TYPE_SYSTEM,
    TYPE_APPROVAL_REQUEST,
    TYPE_STEERING_PAUSED,
    TYPE_STEERING_RESUMED,
    TYPE_STEERING_INJECT,
)
from wisp.colors import success, error, warning, info, dim, accent, muted, border, highlight
from wisp.session import Session, SessionManager, format_session_preview
from wisp.skills import find_skill

logger = logging.getLogger(__name__)

# Tools whose output is always human-readable multi-line text (plans, search
# results, git output, diagnostics, etc.).  These bypass the normal truncation
# and preserve newlines / table formatting.
_FULL_OUTPUT_TOOLS: set[str] = {
    "plan_task",
    "mark_step_done",
    "update_plan",
    "web_search",
    "git_status",
    "git_diff",
    "git_branch",
    "git_commit",
    "git_push",
    "gh_pr_create",
    "list_files",
    "search_symbols",
    "search_codebase",
    "lsp_diagnostics",
    "lsp_definition",
    "lsp_references",
    "lsp_hover",
    "lsp_symbols",
    "diagnose",
    "recall",
    "run_bash",
}

# Minimum terminal width for box-drawing mode. Below this, use flat mode.
_MIN_BOX_WIDTH = 50

# ── Signal handling ──────────────────────────────────────────────────

_transport_instances: weakref.WeakSet = weakref.WeakSet()
_old_sigint_handler = None


def _handle_sigint(signum, frame):
    """Mark interruption on all live transport instances AND their cores so loops exit gracefully."""
    for inst in _transport_instances:
        inst._interrupted = True
        if hasattr(inst, "core"):
            inst.core._interrupted = True
    print(error("\n\n⏹  Interrupted. Finishing current step... (Ctrl+C again to force quit)"))
    signal.signal(signal.SIGINT, signal.default_int_handler)


def _install_signal_handler():
    """Register interrupt handler and reset interrupt state on all instances."""
    global _old_sigint_handler
    for inst in _transport_instances:
        inst._interrupted = False
    _old_sigint_handler = signal.signal(signal.SIGINT, _handle_sigint)


def _restore_signal_handler():
    """Restore the previous SIGINT handler."""
    global _old_sigint_handler
    if _old_sigint_handler is not None:
        signal.signal(signal.SIGINT, _old_sigint_handler)
        _old_sigint_handler = None


# ── Terminal helpers ─────────────────────────────────────────────────

# Paste tracking for input area indicator
_paste_counter: int = 0
_last_paste_lines: int = 0


def _get_git_branch() -> Optional[str]:
    """Get current git branch and dirty status, e.g. 'main [± ↑2]'."""
    try:
        import subprocess
        r = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=1,
        )
        if r.returncode != 0:
            return None
        branch = r.stdout.strip()
        if not branch:
            return None
        # Check for dirty
        r2 = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=1,
        )
        dirty = r2.stdout.strip() != ""
        # Check ahead
        r3 = subprocess.run(
            ["git", "rev-list", "--count", f"{branch}..origin/{branch}", "--"],
            capture_output=True, text=True, timeout=1,
        )
        ahead = r3.stdout.strip()
        suffix = ""
        if dirty:
            suffix += " \u00b1"
        if ahead and ahead != "0":
            suffix += f" \u2191{ahead}"
        if suffix:
            branch += f" [{suffix.strip()}]"
        return branch
    except Exception:
        return None


def _shorten_path(path: str, max_len: int = 30) -> str:
    """Shorten a path for display, e.g. ~/Documents/wisp."""
    try:
        home = os.path.expanduser("~")
        if path.startswith(home):
            display = "~" + path[len(home):]
        else:
            display = path
        if len(display) <= max_len:
            return display
        # Truncate with ellipsis from start
        return "..." + display[-(max_len - 3):]
    except Exception:
        return path


def _get_context_info(core) -> str:
    """Get context token usage as percentage string.
    Returns something like 'context: 39.6% (103.8k/262.1k)'."""
    try:
        tokens = core.estimate_messages_tokens(core.messages)
        max_tokens = core.config.max_context_tokens
        if max_tokens and max_tokens > 0:
            pct = tokens / max_tokens * 100
            used_k = tokens / 1000
            max_k = max_tokens / 1000
            return f"context: {pct:.1f}% ({used_k:.1f}k/{max_k:.1f}k)"
        return f"context: {tokens:,} tokens"
    except Exception:
        return ""


def _render_status_bar(core) -> str:
    """Render the status bar line."""
    lines = []
    # Left side: agent (model)  ~/Documents/wisp  main [±]
    left_parts = []
    model = getattr(core.config, "model", "") or ""
    if model:
        left_parts.append(f"agent ({model})")
    ws = getattr(core.config, "workspace", ".") or "."
    left_parts.append(_shorten_path(ws, 40))
    branch = _get_git_branch()
    if branch:
        left_parts.append(branch)
    left = "  ".join(left_parts)
    lines.append(dim(left))
    # Right side: context info (aligned right)
    ctx = _get_context_info(core)
    if ctx:
        w = _term_width()
        padding = max(0, w - len(left) - len(ctx))
        lines[-1] = dim(left + " " * padding + ctx)
    return "\n".join(lines)


def _input_box_top(label: str = "input") -> str:
    """Draw the top border of the input area."""
    w = _term_width()
    label_text = f" {label} "
    top = "─" * (w - 2 - len(label_text))
    return dim(f"┌{label_text}{top}┐")


def _input_box_bottom() -> str:
    """Draw the bottom border of the input area."""
    w = _term_width()
    return dim(f"└" + "─" * (w - 2) + "┘")


def _paste_indicator(nth: int, nlines: int) -> str:
    """Show paste indicator line, e.g. ' [Pasted text #3 +10 lines]'"""
    return f" {accent('[Pasted text #' + str(nth) + ' +' + str(nlines) + ' lines]')}"

def _is_interactive() -> bool:
    """Detect if stdin is a real terminal (vs pipe/redirect)."""
    return sys.stdin.isatty()


def _term_width() -> int:
    """Get terminal width, clamping to reasonable bounds."""
    try:
        w = shutil.get_terminal_size().columns
    except OSError:
        w = 80
    return max(40, w)


def _use_box_mode(config: object) -> bool:
    """Determine whether to use box-drawing mode."""
    if not sys.stdout.isatty():
        return False
    if getattr(config, "compact_mode", False):
        return False
    if _term_width() < _MIN_BOX_WIDTH:
        return False
    return True


def _wrap_text(text: str, width: int, indent: str = "") -> list[str]:
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


def _box(content: str, title: str = "", style: str = "dim",
         double: bool = False, width: Optional[int] = None) -> str:
    """Wrap content in a box-drawn panel.

    Args:
        content: The text to box.
        title: Optional title shown in the top border.
        style: 'dim' for regular, 'error' for red, 'success' for green.
        double: Use double-line chars (for errors).
        width: Explicit width; auto-detected from terminal if None.
    """
    if width is None:
        width = _term_width()
    inner_width = width - 4  # borders + padding

    style_fn = {"dim": dim, "error": error, "success": success, "muted": muted}.get(style, dim)

    if double:
        tl, tr, bl, br, hz, vt = "╔", "╗", "╚", "╝", "═", "║"
    else:
        tl, tr, bl, br, hz, vt = "┌", "┐", "└", "┘", "─", "│"

    # Title in top border
    if title:
        title_text = f" {title} "
        available = width - 2  # corners
        if len(title_text) > available:
            title_text = title_text[:available]
        top = tl + title_text + hz * (width - 2 - len(title_text)) + tr
    else:
        top = tl + hz * (width - 2) + tr

    bottom = bl + hz * (width - 2) + br

    lines = content.split("\n")
    result_lines = [style_fn(top)]

    # Padding line at top for breathing room in response panels
    if title:
        result_lines.append(style_fn(f"{vt} {' ' * inner_width} {vt}"))

    for line in lines:
        if not line.strip():
            result_lines.append(style_fn(f"{vt} {' ' * inner_width} {vt}"))
            continue
        # Wrap long lines
        wrapped = textwrap.wrap(line, width=inner_width)
        for w in wrapped:
            padded = w.ljust(inner_width)
            result_lines.append(style_fn(f"{vt} {padded} {vt}"))

    # Padding line at bottom for breathing room
    if title:
        result_lines.append(style_fn(f"{vt} {' ' * inner_width} {vt}"))

    result_lines.append(style_fn(bottom))
    return "\n".join(result_lines)


def _rule(char: str = "─", label: str = "", style_fn=None,
          width: Optional[int] = None) -> str:
    """Draw a horizontal rule, optionally with a label.

    Args:
        char: Rule character.
        label: Optional label placed left of center.
        style_fn: Color function (e.g. dim).
        width: Explicit width.
    """
    if width is None:
        width = _term_width()
    style_fn = style_fn or dim

    if label:
        label_str = f" {label} "
        remaining = width - len(label_str)
        left = char * (remaining // 2)
        right = char * (remaining - len(left))
        return style_fn(f"{left}{label_str}{right}")
    return style_fn(char * width)


def _spinner_gen() -> Generator[str, None, None]:
    """Yield spinner frames infinitely."""
    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    for i in itertools.cycle(range(len(frames))):
        yield dim(frames[i])


# ── Readline setup ─────────────────────────────────────────────────────

def _setup_readline_history():
    """Load readline history from disk for REPL arrow-key recall."""
    if readline is None:
        return
    histfile = os.path.expanduser("~/.config/wisp/history")
    try:
        os.makedirs(os.path.dirname(histfile), exist_ok=True)
        readline.read_history_file(histfile)
    except (OSError, FileNotFoundError):
        pass
    import atexit
    atexit.register(lambda: readline.write_history_file(histfile))

    _doc = (readline.__doc__ or "").lower()
    _is_libedit = "libedit" in _doc

    try:
        readline.set_completer_delims(" \t\n`~!@#$%^&*()-=+[{]}\\|;'\",<>")
    except Exception:
        pass

    def _completer(text, state):
        if not text.startswith("/"):
            return None
        from wisp.commands import all_commands
        names = sorted(
            {f"/{c.name}" for c in all_commands()}
            | {f"/{a}" for c in all_commands() for a in c.aliases}
        )
        matches = [n for n in names if n.startswith(text)]
        return matches[state] if state < len(matches) else None

    readline.set_completer(_completer)

    if _is_libedit:
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")

    if _is_libedit:
        readline.parse_and_bind("bind ^[[A ed-search-prev-history")
        readline.parse_and_bind("bind ^[[B ed-search-next-history")
    else:
        readline.parse_and_bind(r'"\e[A": history-search-backward')
        readline.parse_and_bind(r'"\e[B": history-search-forward')

    from wisp.commands import all_commands
    for cmd in all_commands():
        readline.add_history(f"/{cmd.name}")
    readline.add_history("/")

    try:
        readline.write_history_file(histfile)
    except OSError:
        pass


def _args_preview(args: dict) -> str:
    """Short one-line preview of tool arguments (backward-compatible)."""
    parts = []
    path = args.get("path", args.get("command", ""))
    if path:
        s = str(path)
        parts.append(s[:60])
    content = args.get("content", "")
    if content:
        parts.append(f"({len(content)} chars)")
    return ", ".join(parts) if parts else "..."


# ── Language detection for syntax highlighting ────────────────────────

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python", ".pyi": "python", ".pyx": "python",
    ".rs": "rust",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".go": "go",
    ".rb": "ruby",
    ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin",
    ".swift": "swift",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hxx": "cpp",
    ".html": "html", ".htm": "html",
    ".css": "css", ".scss": "scss",
    ".json": "json", ".jsonc": "json",
    ".toml": "toml",
    ".yaml": "yaml", ".yml": "yaml",
    ".xml": "xml", ".svg": "xml",
    ".sql": "sql",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".md": "markdown", ".mdx": "markdown",
    ".lua": "lua",
    ".r": "r",
    ".scala": "scala",
    ".dart": "dart",
    ".php": "php",
    ".ex": "elixir", ".exs": "elixir",
    ".erl": "erlang", ".hrl": "erlang",
    ".hs": "haskell",
    ".clj": "clojure", ".cljs": "clojure", ".edn": "clojure",
    ".zig": "zig",
    ".nim": "nim",
    ".proto": "protobuf",
}


def _detect_language(path: str) -> Optional[str]:
    """Detect pygments lexer name from file extension."""
    ext = os.path.splitext(path)[1].lower()
    return _EXT_TO_LANG.get(ext)


# ── Input handling ───────────────────────────────────────────────────

_MAX_INPUT_CHARS = 100_000  # guard against accidental huge pastes


def _has_unclosed_brackets(text: str) -> bool:
    """Return True if text has unclosed brackets, quotes, or triple-quotes."""
    stack: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        # Triple-quote detection
        if ch in ('"', "'"):
            if i + 2 < len(text) and text[i:i + 3] in ('"""', "'''"):
                quote = text[i:i + 3]
                # Find closing triple quote
                close = text.find(quote, i + 3)
                if close == -1:
                    return True
                i = close + 3
                continue
            else:
                # Single quote — toggle if not inside the other kind
                if stack and stack[-1] == ch:
                    stack.pop()
                else:
                    stack.append(ch)
                i += 1
                continue
        if ch in ('(', '[', '{'):
            stack.append(ch)
        elif ch in (')', ']', '}'):
            if not stack:
                return False  # mismatched, but not "unclosed"
            opener = stack.pop()
            if (ch == ')' and opener != '(') or \
               (ch == ']' and opener != '[') or \
               (ch == '}' and opener != '{'):
                return False  # mismatched
        i += 1
    return bool(stack)


def _continuation_prompt(depth: int) -> str:
    """Build a continuation prompt showing bracket nesting depth."""
    # Use ╎ (box drawings light vertical) for visual grouping
    if depth <= 0:
        return "╎ "
    indent = "  " * min(depth, 4)
    return f"{indent}╎ "


def _input_line(prompt: str, allow_multiline: bool = True) -> str:
    """Read input from the user with a prompt.

    Interactive mode:
      - Uses readline for arrow-key editing and history.
      - Supports multi-line input via backslash continuation OR
        auto-detection of unclosed brackets/quotes.
      - Auto-detects paste: if multiple lines arrive rapidly,
        they are combined into a single multi-line input.
      - Guards against accidental huge pastes (>100KB).

    Non-interactive mode:
      - Reads raw bytes to survive invalid UTF-8 in piped input.
      - Returns empty string on EOF so the caller can exit.
    """
    global _paste_counter, _last_paste_lines
    if sys.stdin.isatty():
        lines: list[str] = []
        total_chars = 0

        # ── Pre-input paste detection using FIONREAD ──
        # FIONREAD ioctl queries the kernel tty buffer directly — no select()
        # buffering issues. On macOS, select() on PTY doesn't return True
        # until readline's first input() consumes the data. FIONREAD fixes
        # this by bypassing the buffering layer entirely.
        import array, fcntl, termios, os as _paste_os
        try:
            _pbuf = array.array('i', [0])
            fcntl.ioctl(sys.stdin, termios.FIONREAD, _pbuf, True)
            _nbytes = _pbuf[0]
        except (OSError, TypeError, termios.error, ImportError):
            _nbytes = 0
        if _nbytes > 0:
            try:
                _rawdata = _paste_os.read(sys.stdin.fileno(), min(_nbytes, _MAX_INPUT_CHARS + 1))
                _fulltext = _rawdata.decode("utf-8", errors="replace")
                # Split into lines, handle trailing newline
                _parts = _fulltext.split("\n")
                if _parts[-1] == "":
                    _parts.pop()
                if _parts:
                    _paste_counter += 1
                    _last_paste_lines = len(_parts)
                    # Print clean prompt + one-line preview
                    print(f"\001\033[1m\002{prompt}\001\033[0m\002", end="")
                    w = _term_width()
                    s = _parts[0][:w - 14].replace("\n", " ")
                    if len(_parts[0]) > w - 14:
                        s += "..."
                    print(f"  {dim(s)}")
                    result = "\n".join(_parts)
                    if readline is not None and result.strip():
                        readline.add_history(result)
                    return result
            except (OSError, TypeError, ValueError):
                pass

        while True:
            try:
                if not lines:
                    rl_prompt = f"\001\033[1m\002{prompt}\001\033[0m\002"
                else:
                    depth = sum(1 for c in "\n".join(lines) if c in "([{")
                    rl_prompt = _continuation_prompt(depth)
                line = input(rl_prompt)
            except KeyboardInterrupt:
                print()
                raise
            except EOFError:
                if not lines:
                    raise
                break
            except (OSError, UnicodeDecodeError):
                return ""

            total_chars += len(line)
            if total_chars > _MAX_INPUT_CHARS:
                print(warning(f"\n  \u26a0\ufe0f  Input truncated at {_MAX_INPUT_CHARS} chars."))
                line = line[:max(0, _MAX_INPUT_CHARS - total_chars + len(line))]
                lines.append(line)
                break

            stripped = line.rstrip()
            if allow_multiline and stripped.endswith("\\"):
                lines.append(stripped[:-1])
                continue

            lines.append(line)
            combined = "\n".join(lines)

            # Bracket continuation — switch to raw stdin for remaining lines
            # so we control what's echoed (no ugly readline echo).
            if allow_multiline and _has_unclosed_brackets(combined):
                # More data queued? This is a paste.
                try:
                    import select as _sel
                    r, _, _ = _sel.select([sys.stdin], [], [], 0)
                    is_paste = bool(r)
                except ImportError:
                    is_paste = False
                if is_paste:
                    # Drain remaining lines from raw buffer
                    while True:
                        try:
                            r2, _, _ = _sel.select([sys.stdin], [], [], 0.02)
                        except (ImportError, OSError, ValueError):
                            break
                        if not r2:
                            break
                        try:
                            raw = sys.stdin.buffer.readline()
                        except (EOFError, OSError):
                            break
                        if not raw:
                            break
                        extra = raw.decode("utf-8", errors="replace").rstrip("\n")
                        # Don't print this line (we'll show a clean summary)
                        total_chars += len(extra)
                        if total_chars > _MAX_INPUT_CHARS:
                            extra = extra[:max(0, _MAX_INPUT_CHARS - total_chars + len(extra))]
                            lines.append(extra)
                            break
                        lines.append(extra)
                    _paste_counter += 1
                    _last_paste_lines = len(lines)
                    return "\n".join(lines)
                else:
                    # Keep typing in readline
                    continue

            if allow_multiline and lines:
                try:
                    import select
                    ready, _, _ = select.select([sys.stdin], [], [], 0.02)
                    if ready:
                        _drained_something = False
                        attempts = 0
                        while attempts < 200:
                            ready2, _, _ = select.select([sys.stdin], [], [], 0.01)
                            if not ready2:
                                if _drained_something:
                                    break
                                attempts += 1
                                continue
                            _drained_something = True
                            attempts = 0
                            try:
                                raw = sys.stdin.buffer.readline()
                                if not raw:
                                    break
                                extra_line = raw.decode("utf-8", errors="replace").rstrip("\n")
                            except (EOFError, OSError):
                                break
                            total_chars += len(extra_line)
                            if total_chars > _MAX_INPUT_CHARS:
                                print(warning(f"\n  \u26a0\ufe0f  Input truncated at {_MAX_INPUT_CHARS} chars."))
                                extra_line = extra_line[:max(0, _MAX_INPUT_CHARS - total_chars + len(extra_line))]
                                lines.append(extra_line)
                                break
                            lines.append(extra_line)
                        _paste_counter += 1
                        _last_paste_lines = len(lines)
                        return "\n".join(lines)
                except (ImportError, OSError):
                    pass

            break

        result = "\n".join(lines)
        if readline is not None and result.strip():
            readline.add_history(result)
        return result
    try:
        data = sys.stdin.buffer.readline()
    except (EOFError, OSError):
        return ""
    if not data:
        return ""
    return data.decode("utf-8", errors="replace").rstrip("\n")

def _prompt_approve(func_name: str) -> bool:
    """Prompt user to approve a tool call. Returns True if approved."""
    if not _is_interactive():
        return True
    try:
        choice = input(f"     Enter to approve, 's' to skip: ").strip().lower()
        return choice != "s"
    except KeyboardInterrupt:
        print()
        return False
    except (EOFError, OSError):
        logger.warning("Stdin unavailable, auto-approving")
        return True


def _prompt_dangerous(func_name: str, reason: str) -> bool:
    """Prompt user to approve a dangerous tool call. Requires typing 'yes'."""
    if not _is_interactive():
        return False
    try:
        print(warning(f"     ⚠️  DANGEROUS: {reason}"))
        choice = input(f"     Type 'yes' to approve {func_name}: ").strip().lower()
        return choice == "yes"
    except KeyboardInterrupt:
        print()
        return False
    except (EOFError, OSError):
        logger.warning("Stdin unavailable, auto-declining dangerous command")
        return False


def _prompt_edit_approval(func_name: str, reason: str) -> bool:
    """Prompt user to approve a file edit. Diff already shown above."""
    if not _is_interactive():
        return True
    try:
        choice = input(f"     Apply this change? [Enter=yes / s=skip / n=reject]: ").strip().lower()
        if choice == 'n':
            return False
        return choice != 's'  # Enter or 'y' = approve; 's' = skip
    except KeyboardInterrupt:
        print()
        return False
    except (EOFError, OSError):
        return True




# ── Event rendering ──────────────────────────────────────────────────

def _render_event(event: AgentEvent, show_thinking: bool = False,
                  show_tool_output: bool = True, box_mode: bool = True) -> Optional[str]:
    """Render an AgentEvent to a terminal string. Returns None for silent events.

    Args:
        event: The AgentEvent to render.
        show_thinking: If True, show thinking content inline.
        show_tool_output: If False, collapse tool results to one-liners.
        box_mode: If True, use box-drawing characters for panels.
    """
    etype = event.type
    w = _term_width()

    if etype == TYPE_CONTENT:
        raw = event.text
        if not box_mode:
            return raw
        # Wrap content into a response panel
        inner_w = w - 4
        wrapped = _wrap_text(raw, inner_w)
        return _box("\n".join(wrapped), title="Response", style="muted", width=w)

    if etype == TYPE_THINKING:
        text = event.text
        if not show_thinking:
            line_count = text.count("\n") + 1
            return dim(f"  🧠 Thinking... ({line_count} lines — /thinking to expand)")
        if not box_mode:
            header = dim("🧠 Reasoning · · · · · · · · · · · · · · · · · · · · · · · · · · · · · ·")
            wrapped = _wrap_text(text, w - 2, indent="")
            body = "\n".join(dim(f"  {line}") for line in wrapped)
            return f"{header}\n{body}"
        header = _rule("·", "🧠 Reasoning", style_fn=dim, width=w)
        wrapped = _wrap_text(text, w - 2)
        body = "\n".join(dim(f"  {line}") for line in wrapped)
        return f"{header}\n{body}"

    if etype == TYPE_TOOL_CALL:
        name = event.data.get("name", "")
        args = event.data.get("arguments", {})
        return _render_tool_call(name, args, box_mode)

    if etype == TYPE_TOOL_RESULT:
        name = event.data.get("name", "")
        duration_ms = event.data.get("duration_ms")
        result = event.data.get("result", "")
        if isinstance(result, str) and result.startswith("{"):
            try:
                parsed = json.loads(result)
                # Pass the full parsed dict so downstream renderers
                # can access metadata (e.g. diffs for write/edit tools),
                # but provide a cleaned 'data' field for text display.
                parsed["data"] = _coerce_tool_data(parsed.get("data", result))
                result = parsed
            except (json.JSONDecodeError, KeyError):
                pass
        return _render_tool_result(name, result, duration_ms, show_tool_output, box_mode, w)

    if etype == TYPE_ERROR:
        msg = event.data.get("message", "")
        if box_mode:
            return _box(f"✗ {msg}", title="Error", style="error", double=True, width=w)
        return error(f"✗ {msg}")

    if etype == TYPE_SYSTEM:
        level = event.data.get("level", "info")
        if level == "debug":
            return None
        msg = event.data.get("message", "")
        if level == "warning":
            return warning(f"  ⚠ {msg}")
        return info(f"  ℹ {msg}")

    if etype == TYPE_APPROVAL_REQUEST:
        return warning(f"  ⚠️  Approval required: {event.data.get('reason', '')}")

    if etype == TYPE_STEERING_PAUSED:
        return warning(f"  ⏸  Steering paused: {event.data.get('reason', '')}")

    if etype == TYPE_STEERING_RESUMED:
        return success("  ▶  Steering resumed")

    if etype == TYPE_STEERING_INJECT:
        return dim(f"  💉 Steering feedback: {event.data.get('text', '')[:80]}")

    if etype == TYPE_DONE:
        reason = event.data.get("reason", "")
        turns = event.data.get("turns", 0)
        if reason == "max_iterations":
            msg = f"⚠️  Hit max iterations after {turns} turns. Type 'continue' or increase --max-iterations."
            return warning(f"\n{msg}")
        elif reason == "max_reflections":
            msg = f"🔄  Detected reflective loop after {turns} turns."
            return warning(f"\n{msg}")
        elif reason == "interrupted":
            return dim("\n⏹  Interrupted.")
        elif reason == "error":
            return error("\n✗ Stream error — turn aborted.")
        return None

    return None


def _render_tool_call(name: str, args: dict, box_mode: bool) -> str:
    """Render a tool call with structured argument display."""
    lines = [dim(f"  🔧 {name}")]
    if args:
        for key, value in args.items():
            val_str = _format_arg_value(key, value)
            lines.append(dim(f"  │  {key}: {val_str}"))
    return "\n".join(lines)


def _format_arg_value(key: str, value) -> str:
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


def _render_tool_result(name: str, result, duration_ms, 
                        show_tool_output: bool, box_mode: bool, width: int) -> str:
    """Render a tool result. Extracts and renders diffs for write/edit tools."""
    duration_str = _format_duration(duration_ms)

    # Parse JSON result if it's a string (execute_tool returns JSON string)
    meta = None
    result_text: str
    if isinstance(result, str) and result.startswith("{"):
        try:
            parsed = json.loads(result)
            meta = parsed.get("metadata", {})
            result_text = _coerce_tool_data(parsed.get("data", result))
        except (json.JSONDecodeError, KeyError):
            result_text = str(result)
    elif isinstance(result, dict):
        meta = result.get("metadata", {})
        result_text = result.get("data", str(result))
    else:
        result_text = str(result)

    diff_text = (meta or {}).get("diff", "")
    is_edit_tool = name in ("write_file", "edit_file", "edit_file_multi")

    # Full-output tools (non-edit): preserve multi-line formatting
    if name in _FULL_OUTPUT_TOOLS and not is_edit_tool:
        output_str = result_text
        if not show_tool_output:
            line_count = output_str.count("\n") + 1
            return dim(f"  ✓ {name} ({duration_str}) — {line_count} lines of output · · · · · · · · · · · · · · · · · · ·")

        if box_mode:
            header = dim(f"  ✓ {name} ({duration_str}) " + "·" * max(0, width - len(f"  ✓ {name} ({duration_str}) ") - 2))
            body = _box(output_str, width=width)
            return f"{header}\n{body}"

        header = dim(f"  ✓ {name} ({duration_str})")
        body = dim(f"     → {name} result:\n{output_str}")
        return f"{header}\n{body}"

    # Edit tools: show summary + diff if available
    if is_edit_tool and diff_text:
        header = dim(f"  ✓ {name} ({duration_str}) " + "·" * max(0, width - len(f"  ✓ {name} ({duration_str}) ") - 2))
        summary = dim(f"     → {result_text[:200].replace(chr(10), ' ')}")
        try:
            from wisp.diff_renderer import render_diff_box
            lang = _detect_language(meta.get('path', ''))
            diff_box = render_diff_box(diff_text, title=f"Diff — {meta.get('path', '')}"[:60],
                                       width=width, box_mode=box_mode, language=lang)
            return f"{header}\n{summary}\n{diff_box}"
        except ImportError:
            pass

    # Regular / compact tool results
    if not show_tool_output:
        return dim(f"  ✓ {name} ({duration_str}) · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · ·")

    status_icon = "✓" if not result_text.startswith("Error") else "✗"
    if result_text.startswith("Error"):
        preview = result_text[:200].replace("\n", " ")
        return dim(f"  ✗ {name} ({duration_str})") + "\n" + dim(f"     → {preview}")

    preview = result_text[:200].replace("\n", " ")
    if len(result_text) > 200:
        preview += "..."
    header = dim(f"  {status_icon} {name} ({duration_str}) " + "·" * max(0, width - len(f"  {status_icon} {name} ({duration_str}) ") - 2))
    return f"{header}\n" + dim(f"     → {preview}")


def _format_duration(duration_ms) -> str:
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


# ── CLITransport ─────────────────────────────────────────────────────

class CLITransport:
    """Terminal transport for WispAgentCore.

    Drives the REPL loop, renders events with colors, handles user input,
    and manages signal interrupts.
    """

    def __init__(self, core: WispAgentCore):
        self.core = core
        self.show_thinking = core.config.show_thinking
        self.show_tool_output = getattr(core.config, "show_tool_output", True)
        self.auto_approve = core.config.auto_approve
        self._interrupted = False
        self._pending_approval = None
        self._spinner = _spinner_gen()
        _transport_instances.add(self)

    # ── Input area helpers ────────────────────────────────────────

    def _use_input_box(self) -> bool:
        """Whether to render the Kimi-style input box (borders + status bar)."""
        return _use_box_mode(self.core.config)

    def _status_bar(self) -> str:
        """Render the status bar below the input area."""
        return _render_status_bar(self.core)

    # ── Public API ─────────────────────────────────────────────────

    def run(self, prompt: str, skill_name: Optional[str] = None, session_id: Optional[str] = None) -> None:
        """Single-shot mode — setup, slash commands, skill, execute, cleanup."""
        _install_signal_handler()

        if not self.core.client.check_health():
            _restore_signal_handler()
            return

        # Slash commands
        if prompt.strip().startswith("/"):
            from wisp.commands import dispatch, ExitREPL
            try:
                if dispatch(prompt.strip(), self.core):
                    _restore_signal_handler()
                    return
            except ExitREPL:
                _restore_signal_handler()
                return

        # Skill lookup
        if skill_name:
            skill = find_skill(skill_name, self.core.config.workspace or ".")
            if skill:
                print(accent(f"🧠 Loaded skill: {skill.name} — {skill.description}"))
            else:
                print(warning(f"⚠ Skill '{skill_name}' not found. Running without it."))

        # Session setup
        if session_id:
            loaded = self.core._resolve_session(session_id)
            if loaded is None:
                print(error(f"✗ Session '{session_id}' not found."))
                print(dim("  Run 'wisp session list' to see available sessions."))
                _restore_signal_handler()
                return
            self.core.session = loaded
            self.core.messages = list(loaded.messages)
            self._print_session_banner(loaded)
        else:
            self.core.session = Session.create(
                model=self.core.config.model,
                workspace=self.core.config.workspace or ".",
                first_prompt=prompt,
            )
            self.core.messages = []

        print(info(f"🔮 Wisp (model: {self.core.config.model})"))
        print()

        try:
            self.core._add_message("user", self.core._expand_continuation(prompt))
            asyncio.run(self._execute_turn(
                self.core._build_system_prompt(skill_name, workspace=self.core.config.workspace, query=prompt),
                self.core.config.workspace or ".",
            ))
        finally:
            self.core._save_session_summary()
            self.core.mcp.shutdown()
            self.core.lsp.shutdown_all()
            _restore_signal_handler()

    def run_once(self, prompt: str, skill_name: Optional[str] = None) -> None:
        """Run a single prompt and print results (minimal wrapper)."""
        self.run(prompt, skill_name=skill_name)

    def _print_session_banner(self, loaded) -> None:
        """Print session continuation info — shared by run() and repl()."""
        lines = []
        if loaded.title:
            lines.append(f"  Title:      {loaded.title}")
        lines.append(f"  Model:      {self.core.config.model}")
        if loaded.model and loaded.model != self.core.config.model:
            lines.append(f"  ⚠ Session created with model '{loaded.model}'. Now using '{self.core.config.model}'.")
        lines.append(f"  Session:    {self.core.session.id}")
        lines.append(f"  Messages:   {len(self.core.messages)}")
        ws_display = self.core.config.workspace or "."
        lines.append(f"  Workspace:  {ws_display}")
        last_user = None
        for m in reversed(self.core.messages):
            if m.get("role") == "user":
                text = extract_text(m.get("content", ""))
                if text.strip():
                    last_user = text
                    break
        if last_user:
            preview = last_user[:100].replace("\n", " ")
            if len(last_user) > 100:
                preview += "..."
            lines.append(f"  Last:       {preview}")
        lines.append("")
        lines.append("  /help for commands  ·  Ctrl+C/D to exit")
        print(_box("\n".join(lines), title="📋 Continuing Session"))
        print()

    def repl(self, skill_name: Optional[str] = None, session_id: Optional[str] = None) -> None:
        """Interactive REPL — continuous conversation until the user exits."""
        _install_signal_handler()

        if not self.core.client.check_health():
            _restore_signal_handler()
            return

        # Session setup
        if session_id:
            loaded = self.core._resolve_session(session_id)
            if loaded is None:
                print(error(f"✗ Session '{session_id}' not found."))
                return
            self.core.session = loaded
            self.core.messages = list(loaded.messages)
            self._print_session_banner(loaded)
        else:
            self.core.session = Session.create(
                model=self.core.config.model,
                workspace=self.core.config.workspace or ".",
                first_prompt="REPL session",
            )
            self.core.messages = []

        ws = self.core.config.workspace or "."

        _setup_readline_history()

        msg_count = len(self.core.messages)
        ws_display = ws or "."

        # ── Startup banner box ──
        banner_lines = [
            f"  Model:      {self.core.config.model}",
            f"  Session:    {self.core.session.id}",
            f"  Workspace:  {ws_display}",
        ]
        if msg_count:
            banner_lines.append(f"  History:    {msg_count} messages")
        if skill_name:
            banner_lines.append(f"  Skill:      {skill_name}")
        banner_lines.append("")
        banner_lines.append("  /help for commands  ·  Ctrl+C/D to exit")
        print(_box("\n".join(banner_lines), title="🔮 Wisp"))
        print()

        self._interrupted = False
        try:
            while not self._interrupted:
                # ── Input area: top border ──
                use_box = self._use_input_box()
                if use_box:
                    print(_input_box_top("input"))

                # Reset paste tracking before each input
                global _last_paste_lines
                _last_paste_lines = 0

                try:
                    user_input = _input_line("➜ ")
                except EOFError:
                    print(success("\n  👋 Goodbye."))
                    break
                except KeyboardInterrupt:
                    print(error("\n⏹  Exiting."))
                    break

                cmd = user_input.strip()
                if not cmd:
                    if not _is_interactive():
                        break
                    if use_box:
                        print(_input_box_bottom())
                        print(self._status_bar())
                    continue

                # ── Input area: paste indicator + bottom border + status bar ──
                global _paste_counter
                was_paste = _paste_counter > 0 and _last_paste_lines > 1
                if use_box:
                    if was_paste:
                        print(_paste_indicator(_paste_counter, _last_paste_lines))
                    print(_input_box_bottom())
                    print(self._status_bar())
                elif was_paste:
                    n = _last_paste_lines
                    print(dim(f"  📝 {n} lines"))
                print()

                # Slash commands
                from wisp.commands import dispatch, ExitREPL
                try:
                    if dispatch(cmd, self.core):
                        continue
                except ExitREPL:
                    print(success("  👋 Goodbye."))
                    break

                # Legacy non-slash commands
                if cmd in ("exit", "quit"):
                    print(success("  👋 Goodbye."))
                    break
                if cmd in ("help", "?"):
                    dispatch("/help", self.core)
                    continue

                # Update session title
                if self.core.session and (
                    not self.core.session.title
                    or self.core.session.title in ("REPL session", "(untitled)")
                ):
                    self.core.session.title = cmd[:60].strip()

                try:
                    system = self.core._build_system_prompt(skill_name, query=cmd)
                    self.core._add_message("user", self.core._expand_continuation(cmd))
                    asyncio.run(self._execute_turn(system, ws))
                except KeyboardInterrupt:
                    print(error("\n⏹  Turn interrupted."))
                    self._interrupted = False
                    continue
                except Exception as e:
                    print(error(f"\n✗ Unexpected error: {e}"))
                    logger.error("REPL turn crashed", exc_info=True)
                    self._interrupted = False
                    continue

                if not self._interrupted:
                    self._print_turn_done()

            print()
            if self.core.session:
                print(success(f"📋 Session {self.core.session.id} saved."))
                print(dim(f"   Continue with: wisp repl -S {self.core.session.id}"))
        finally:
            self.core._save_session_summary()
            self.core.mcp.shutdown()
            self.core.lsp.shutdown_all()
            _restore_signal_handler()

    # ── Internal ─────────────────────────────────────────────────────

    async def _run_once_async(self, prompt: str, skill_name: Optional[str] = None) -> None:
        """Async helper for run_once."""
        system = self.core._build_system_prompt(skill_name, query=prompt)
        self.core._add_message("user", self.core._expand_continuation(prompt))
        await self._execute_turn(system, self.core.config.workspace or ".")

    async def _execute_turn(self, system: str, workspace: str) -> None:
        """Execute one user turn — buffered phase rendering with spinner.

        Thinking and content tokens are accumulated silently while a spinner
        provides feedback. When a phase transition occurs (thinking→content,
        content→tool_call, etc.), the buffered text is flushed as a structured
        block. Tool calls and results are rendered immediately.
        """
        self._interrupted = False
        if hasattr(self.core, "_interrupted"):
            self.core._interrupted = False

        prompt = ""
        if self.core.messages and self.core.messages[-1].get("role") == "user":
            raw = self.core.messages[-1].get("content", "")
            prompt = extract_text(raw)
            self.core.messages.pop()

        box_mode = _use_box_mode(self.core.config)
        width = _term_width()  # snapshot once per turn — prevents jagged boxes on resize

        async def _cli_approval(name: str, args: dict, reason: str) -> tuple[bool, Optional[dict]]:
            if name in ("write_file", "edit_file", "edit_file_multi"):
                approved = _prompt_edit_approval(name, reason)
            else:
                approved = _prompt_dangerous(name, reason)
            return (approved, None)

        thinking_buf: list[str] = []
        content_buf: list[str] = []
        in_thinking = False
        in_content = False
        spinner_active = False
        spinner = _spinner_gen()
        total_iterations = 0
        turn_start = time.monotonic()

        def _stop_spinner():
            nonlocal spinner_active
            if spinner_active:
                sys.stdout.write("\r\033[K")  # clear spinner line
                sys.stdout.flush()
                spinner_active = False

        def _flush_thinking():
            nonlocal in_thinking
            if thinking_buf:
                _stop_spinner()
                text = "".join(thinking_buf)
                rendered = _render_thinking_block(text, box_mode, width)
                if rendered:
                    print(rendered)
                thinking_buf.clear()
            in_thinking = False

        def _flush_content():
            nonlocal in_content
            if content_buf:
                _stop_spinner()
                text = "".join(content_buf)
                rendered = _render_content_block(text, box_mode, width)
                if rendered:
                    print(rendered)
                content_buf.clear()
            in_content = False

        def _show_spinner(label: str):
            nonlocal spinner_active
            if box_mode and sys.stdout.isatty():
                frame = next(spinner)
                sys.stdout.write(f"\r{frame} {label}")
                sys.stdout.flush()
                spinner_active = True

        try:
            async for event in self.core._arun(prompt, system=system, approval_handler=_cli_approval):
                if self._interrupted:
                    break

                if event.type == TYPE_THINKING:
                    if in_content:
                        continue
                    if not in_thinking:
                        _flush_content()
                        in_thinking = True
                        _show_spinner("Thinking...")
                    thinking_buf.append(event.text)

                elif event.type == TYPE_CONTENT:
                    if in_thinking:
                        _flush_thinking()
                    if not in_content:
                        in_content = True
                        _show_spinner("Generating response...")
                    content_buf.append(event.text)

                elif event.type == TYPE_TOOL_CALL:
                    _flush_thinking()
                    _flush_content()
                    _stop_spinner()
                    name = event.data.get("name", "")
                    args = event.data.get("arguments", {})
                    rendered = _render_tool_call(name, args, box_mode)
                    print(rendered)

                elif event.type == TYPE_TOOL_RESULT:
                    _stop_spinner()
                    rendered = _render_tool_result(
                        event.data.get("name", ""),
                        event.data.get("result", ""),
                        event.data.get("duration_ms"),
                        self.show_tool_output,
                        box_mode,
                        width,
                    )
                    print(rendered)

                elif event.type == TYPE_DONE:
                    total_iterations = event.data.get("turns", 0)
                    # Render completion reason if non-natural
                    msg = _render_done_reason(event, total_iterations)
                    if msg:
                        _stop_spinner()
                        print(msg)

                else:
                    _flush_thinking()
                    _flush_content()
                    _stop_spinner()
                    rendered = _render_event(event, self.show_thinking,
                                            self.show_tool_output, box_mode)
                    if rendered is not None:
                        print(rendered)

            # Flush any remaining buffered content
            _flush_thinking()
            _flush_content()
            _stop_spinner()

        except KeyboardInterrupt:
            self.core._interrupted = True
            self._interrupted = True
            _stop_spinner()
            raise
        except asyncio.CancelledError:
            self.core._interrupted = True
            self._interrupted = True
            _stop_spinner()
            raise
        except Exception:
            _stop_spinner()
            raise

    def _print_turn_done(self):
        _print_separator()


def _print_separator():
    """Print a turn-completion separator."""
    w = _term_width()
    print()
    print(dim("─" * w))
    print()


# ── Block renderers (used by _execute_turn for buffered output) ──────

def _render_thinking_block(text: str, box_mode: bool, width: int) -> Optional[str]:
    """Render buffered thinking text as a block."""
    if not text.strip():
        return None
    inner_w = width - 4
    wrapped = _wrap_text(text.strip(), inner_w)
    if box_mode:
        header = _rule("·", "🧠 Reasoning", style_fn=dim, width=width)
        body = "\n".join(dim(f"  {line}") for line in wrapped)
        return f"{header}\n{body}"
    else:
        header = _rule("─", "🧠 Reasoning", style_fn=dim, width=width)
        body = "\n".join(dim(f"  {line}") for line in wrapped)
        return f"{header}\n{body}"


def _render_content_block(text: str, box_mode: bool, width: int) -> Optional[str]:
    """Render buffered content text as a block."""
    if not text.strip():
        return None
    inner_w = width - 4
    wrapped = _wrap_text(text.strip(), inner_w)
    if box_mode:
        return _box("\n".join(wrapped), title="Response", style="muted", width=width)
    else:
        return "\n".join(wrapped)


def _render_done_reason(event: AgentEvent, iterations: int) -> Optional[str]:
    """Render the turn completion reason."""
    reason = event.data.get("reason", "")
    if reason == "max_iterations":
        return warning(f"\n  ⚠️  Max iterations ({iterations}) reached. Type 'continue' or increase --max-iterations.")
    elif reason == "max_reflections":
        return warning(f"\n  🔄  Reflective loop detected after {iterations} iterations.")
    elif reason == "interrupted":
        return dim("\n  ⏹  Interrupted.")
    elif reason == "error":
        return error("\n  ✗ Stream error — turn aborted.")
    return None
