"""CLI transport for Wisp v2.

Replaces: ad-hoc CLI handling in cli.py.
Clean separation: transport owns the wire protocol, runtime owns the logic.

Design:
  - Reads from stdin, writes to stdout
  - Routes input to runtime.run_turn()
  - Prints events in human-readable format with structured panels
  - Handles EOF and exit commands gracefully
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import select
import shutil
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

# Import readline to enable arrow-key/editing support in input().
# Silently skip on platforms where it's unavailable (e.g. some Docker images).
try:
    import readline  # noqa: F401
except ImportError:
    pass

from .base import Transport
from .renderer import (
    format_duration as _format_duration,
    render_tool_call as _render_tool_call,
    render_thinking_block as _render_thinking_block,
    render_content_block as _render_content_block,
    _box,
    _rule,
)
from wisp.colors import bold, dim, error, warning, success, info
from wisp.approval_state import ApprovalSessionState, SessionPolicy
from wisp.infra.security import redact_sensitive_tool_args
from wisp.core.events import AgentEvent, EventType
from wisp.terminal_width import (
    is_accessible, get_output_mode, wrap_text_wide,
    status_symbols,
    OutputMode,
)
from wisp.transport.progress import ProgressTracker
from wisp.transport.spinner import Spinner
from wisp.transport.renderer import render_phase_bar, render_provider_status, render_subagent_status

logger = logging.getLogger(__name__)

# Global transport registry for signal handling
_transport_instances: list = []
_old_sigint_handler = None


def _is_interactive() -> bool:
    """Return True if stdin is a tty."""
    return sys.stdin.isatty()


_ANSI_ESCAPE_RE = re.compile(
    r"\x1b\[[0-9;]*[A-Za-z]"           # CSI sequences (colors, cursor, etc.)
    r"|\x1b\].*?\x07"                   # OSC sequences (title, etc.)
    r"|\x1b[()][AB0DEHM]"               # Character set selection
    r"|\x1b\[[0-9;]*~"                  # Bracketed paste (200~, 201~) and function keys
    r"|[^\x20-\x7e\s]"                  # Non-printable control chars (keep newlines/tabs)
)


def _strip_ansi(s: str) -> str:
    """Remove ANSI escape sequences and non-printable control chars from a string."""
    return _ANSI_ESCAPE_RE.sub("", s)


def _input_line(prompt: str, allow_multiline: bool = True) -> str | None:
    r"""Read a line from stdin. Returns None on EOF.

    Strips any ANSI escape sequences that leak through when readline
    is unavailable (e.g. arrow keys emitted as raw ESC sequences).

    Supports:
      - Multiline with trailing backslash (\)
      - Readline history/editing when the tty provides it
    """
    try:
        if sys.stdin.isatty():
            # Interactive mode - use input() with readline support
            line = input(prompt)
            line = _strip_ansi(line)
            
            # Handle multiline with trailing backslash
            if allow_multiline and line.rstrip().endswith("\\"):
                lines = [line.rstrip()[:-1]]  # Remove trailing backslash
                while True:
                    try:
                        cont = input("... ")
                        cont = _strip_ansi(cont)
                        if cont.rstrip().endswith("\\"):
                            lines.append(cont.rstrip()[:-1])
                        else:
                            lines.append(cont)
                            break
                    except EOFError:
                        break
                return "\n".join(lines)
            return line
        
        # Non-tty: read from buffer to handle piped input
        # StringIO (used in tests) doesn't have .buffer — fall back to readline()
        if hasattr(sys.stdin, "buffer"):
            data = sys.stdin.buffer.readline()
            if not data:
                return None
            return data.decode("utf-8", errors="replace").rstrip("\n\r")
        line = sys.stdin.readline()
        if not line:
            return None
        return line.rstrip("\n\r")
    except EOFError:
        return None
    except UnicodeDecodeError:
        return ""


def _input_multiline(prompt: str = "➜ ", continuation_prompt: str = "... ") -> str | None:
    """Read multiline input with explicit continuation.

    Uses blank line (double Enter) to terminate, or Ctrl+D.
    More intuitive than backslash for pasted code blocks.
    """
    if not sys.stdin.isatty():
        # Non-interactive: read what remains of stdin. Exhausted stdin is
        # EOF (None), not an empty submission — otherwise piped sessions
        # would spin forever on empty prompts.
        if hasattr(sys.stdin, "buffer"):
            data = sys.stdin.buffer.read()
            if not data:
                return None
            return data.decode("utf-8", errors="replace").strip()
        data = sys.stdin.read()
        if not data:
            return None
        return data.strip()
    
    print(prompt, end="", flush=True)
    lines = []
    empty_count = 0
    
    try:
        while True:
            try:
                line = input()
                line = _strip_ansi(line)
            except EOFError:
                break
            
            if line == "":
                empty_count += 1
                if empty_count >= 2:
                    # Double empty line = end of input
                    if lines and lines[-1] == "":
                        lines.pop()  # Remove the last empty line
                    break
            else:
                empty_count = 0
            
            lines.append(line)
            print(continuation_prompt, end="", flush=True)

        if not lines:
            return None
        return "\n".join(lines)
    except KeyboardInterrupt:
        # Multiline mode's documented contract: Ctrl+C clears the current
        # input, it does not exit the REPL. Empty string re-prompts.
        print(f"\n{status_symbols()['fail']} Input cleared")
        return ""

def _args_preview(args: dict) -> str:
    """Compact preview of tool arguments."""
    if not args:
        return "..."
    if "path" in args:
        return str(args["path"])
    if "command" in args:
        return str(args["command"])
    if "content" in args:
        content = str(args["content"])
        if len(content) > 40:
            content = content[:37] + "..."
        return f"{content} ({len(str(args['content']))} chars)"
    # Fallback: show first key=value
    k, v = next(iter(args.items()))
    sv = str(v)
    if len(sv) > 40:
        sv = sv[:37] + "..."
    return f"{k}={sv}"


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
}


def _term_width() -> int:
    """Get terminal width, with a sensible minimum."""
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


def _coerce_tool_data(value: Any) -> str:
    """Coerce a tool result value to a display string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _preview_lines(text: str, max_lines: int = 3, max_line_width: int = 200) -> str:
    """Format multi-line tool output as a clean indented preview.

    Each shown line is dimmed and indented two spaces. Long single lines are
    truncated to ``max_line_width``. When the source has more than
    ``max_lines`` lines, a trailing ``… +N more`` marker is appended instead
    of dumping the whole output.
    """
    if not text:
        return ""
    if not isinstance(text, str):
        text = _coerce_tool_data(text)
    lines = text.split("\n")
    while lines and lines[-1].strip() == "":
        lines.pop()
    if not lines:
        return ""
    shown: list[str] = []
    for line in lines[:max_lines]:
        if len(line) > max_line_width:
            line = line[: max_line_width - 1] + "…"
        shown.append(dim(f"  {line}"))
    if len(lines) > max_lines:
        shown.append(dim(f"  … +{len(lines) - max_lines} more"))
    return "\n".join(shown)


def _detect_language(path: str) -> str:
    """Detect programming language from file extension for diff highlighting."""
    ext = path.split(".")[-1].lower() if "." in path else ""
    lang_map = {
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "jsx": "jsx",
        "tsx": "tsx",
        "rs": "rust",
        "go": "go",
        "java": "java",
        "kt": "kotlin",
        "rb": "ruby",
        "php": "php",
        "c": "c",
        "cpp": "cpp",
        "h": "c",
        "hpp": "cpp",
        "cs": "csharp",
        "swift": "swift",
        "scala": "scala",
        "sh": "bash",
        "bash": "bash",
        "zsh": "zsh",
        "fish": "fish",
        "ps1": "powershell",
        "sql": "sql",
        "yaml": "yaml",
        "yml": "yaml",
        "json": "json",
        "toml": "toml",
        "xml": "xml",
        "html": "html",
        "css": "css",
        "scss": "scss",
        "sass": "sass",
        "less": "less",
        "md": "markdown",
        "markdown": "markdown",
        "dockerfile": "dockerfile",
        "makefile": "makefile",
        "cmake": "cmake",
        "lua": "lua",
        "r": "r",
        "pl": "perl",
        "pm": "perl",
        "t": "perl",
        "dart": "dart",
        "flutter": "dart",
        "ex": "elixir",
        "exs": "elixir",
        "erl": "erlang",
        "hrl": "erlang",
        "clj": "clojure",
        "cljs": "clojure",
        "hs": "haskell",
        "lhs": "haskell",
        "ml": "ocaml",
        "mli": "ocaml",
        "fs": "fsharp",
        "fsx": "fsharp",
        "fsi": "fsharp",
        "nim": "nim",
        "zig": "zig",
        "v": "v",
        "vlang": "v",
        "cr": "crystal",
        "jl": "julia",
        "pas": "pascal",
        "pp": "pascal",
        "d": "d",
        "groovy": "groovy",
        "gradle": "groovy",
        "tf": "terraform",
        "hcl": "hcl",
        "nix": "nix",
        "elm": "elm",
        "purescript": "purescript",
        "purs": "purescript",
        "coffee": "coffeescript",
        "litcoffee": "coffeescript",
        "vue": "vue",
        "svelte": "svelte",
        "astro": "astro",
        "solidity": "solidity",
        "vy": "vyper",
        "cairo": "cairo",
        "move": "move",
        "noir": "noir",
        "circom": "circom",
        "lean": "lean",
        "agda": "agda",
        "idris": "idris",
        "coq": "coq",
        "isabelle": "isabelle",
        "twig": "twig",
        "smarty": "smarty",
        "liquid": "liquid",
        "jinja": "jinja",
        "jinja2": "jinja",
        "mustache": "mustache",
        "handlebars": "handlebars",
        "ejs": "ejs",
        "pug": "pug",
        "haml": "haml",
        "slim": "slim",
        "erb": "erb",
        "rhtml": "erb",
    }
    return lang_map.get(ext, "")


class AgentAdapter:
    """Adapts the new runtime+session+config to the old WispAgentCore API.

    This lets existing slash commands in wisp/commands.py work without
    modification. Uses session dict directly instead of _SessionAdapter.
    """

    def __init__(self, runtime: Any, config: Any, session: dict, loop: asyncio.AbstractEventLoop | None = None):
        self.runtime = runtime
        self.config = config
        self.session = session  # Use session dict directly
        self.messages = session["messages"]
        self._system_prompt_cache: dict = {}
        self._active_skill: str | None = None
        self._pending_continue: bool = False
        self._interrupted: bool = False
        self._paused = asyncio.Event()
        self._paused.set()
        self._loop = loop

    # ── Provider access ─────────────────────────────────────────────

    @property
    def client(self):
        """Return the provider from the cached core, if available."""
        try:
            return self.runtime.get_core_provider()
        except Exception:
            return None

    # ── Metrics / circuit breaker ─────────────────────────────────────

    @property
    def metrics(self):
        from wisp.metrics import AgentMetrics
        if not hasattr(self, '_agent_metrics'):
            self._agent_metrics = AgentMetrics()
        return self._agent_metrics

    # ── Session helpers ─────────────────────────────────────────────

    def _save_session(self) -> None:
        store = getattr(self.runtime, "store", None)
        if store is not None:
            store.save_session(self.session)

    def _maybe_compact_session(self) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.run_until_complete(
                self.runtime.maybe_compact(self.session)
            )
        else:
            asyncio.run(self.runtime.maybe_compact(self.session))

    def _build_system_prompt(
        self, skill_name: str | None = None, query: str = ""
    ) -> str:
        # Try to delegate to core
        try:
            core = self.runtime._get_core()
            if hasattr(core, "_build_system_prompt"):
                return core._build_system_prompt(self.session, query=query or skill_name or "")
        except Exception:
            pass
        # Fallback
        return "You are Wisp, a helpful coding assistant."

    def _estimate_tokens(self, messages: list[dict]) -> int:
        chars = sum(len(str(m.get("content", ""))) for m in messages)
        return chars // 4

    def _add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})

    _CONTINUATION_TRIGGERS = frozenset({
        "continue", "go on", "more", "and?", "keep going", "next", "proceed",
        "finish", "tell me more", "expand on that", "elaborate", "what else",
    })

    def _expand_continuation(self, text: str) -> str:
        """Rewrite bare continuation words into explicit, anaphora-free prompts.

        After compaction the model may have lost the exact last assistant message.
        This hook disambiguates 'continue' by injecting the topic from the
        compacted summary or the last verbatim assistant message.
        """
        lowered = text.strip().lower().rstrip("?.!")
        if lowered not in self._CONTINUATION_TRIGGERS:
            return text

        parts: list[str] = [text]

        # Try to grab the last verbatim assistant message in hot context
        last_assistant = ""
        for m in reversed(self.messages):
            if m.get("role") == "assistant":
                last_assistant = m.get("content", "") or ""
                break

        if last_assistant:
            tail = last_assistant[-200:].replace("\n", " ")
            parts.append(
                f"\n[Context: Continue your previous response. "
                f"Do NOT repeat anything already said. "
                f"Pick up exactly after: {tail}]"
            )

        return "\n".join(parts)

    def _run_turn_streaming(self, system: str) -> dict:
        """Backward-compat for /continue."""
        return {}

    # ── Steering ──────────────────────────────────────────────────────

    def pause(self) -> None:
        self._paused.clear()

    def resume(self, injected_text: str | None = None) -> None:
        self._paused.set()


class CLITransport(Transport):
    """CLI transport layer with structured output matching legacy CLI."""

    def __init__(self, runtime: Any, config: Any | None = None):
        self.runtime = runtime
        self.config = config
        self._stdin: Any = None
        self._stdout: Any = None
        self._thinking_buffer: list[str] = []

        self._content_buffer: list[str] = []
        self._in_thinking: bool = False
        self._in_content: bool = False
        self.show_thinking: bool = (
            getattr(config, "show_thinking", False) if config else False
        )
        self._last_block_was_tool = False
        self.show_tool_output: bool = True
        # Session-level per-tool approval memory
        self._approval_state = ApprovalSessionState()
        self._force_approval_mode = not _is_interactive()
        # Progress tracking
        self._progress = ProgressTracker()
        self._spinner: Spinner | None = None
        self._turn_number: int = 0
        self._phase: str = "understand"
        self._interrupted: bool = False
        _transport_instances.append(self)

    def is_interrupted(self) -> bool:
        """Return True if this transport has been interrupted (Ctrl+C)."""
        return self._interrupted

    # ── Transport ABC implementation ────────────────────────────────

    async def send(self, event: dict) -> None:
        """Send an event to stdout."""
        if self._stdout is not None:
            self._render_event(self._stdout, event)

    async def recv(self) -> str | None:
        """Receive a prompt from stdin.

        Uses asyncio.to_thread() to avoid blocking the event loop.
        """
        if self._stdin is None:
            return None
        try:
            line = await asyncio.to_thread(self._stdin.readline)
        except Exception:
            return None
        if not line:
            return None
        prompt = line.strip()
        if prompt.lower() in ("exit", "quit"):
            return None
        return prompt

    async def approve(self, tool_call: dict) -> bool:
        """Interactive approval with session-level per-tool memory.

        Prompts the user with choices:
          y = yes (once)  Y = yes (always this tool this session)
          a = approve ALL tools this session
          n = no (once)   N = no (always deny this tool)
          d = deny ALL tools this session
          c = cancel turn
        """
        tool_name = tool_call.get("name", "unknown")
        args_text = ""
        args_map = redact_sensitive_tool_args(tool_call.get("arguments", {}))
        if args_map:
            args_text = ", ".join(f"{k}={v!r}" for k, v in list(args_map.items())[:3])
            if len(args_map) > 3:
                args_text += ", ..."

        # Non-interactive / piped input = auto-deny unless session policy is auto
        if self._force_approval_mode or self._approval_state.session_policy is SessionPolicy.AUTO:
            if self._approval_state.session_policy == SessionPolicy.AUTO:
                return True
            # Blocked in non-interactive mode
            return False

        # Check session state first
        if self._approval_state.session_policy == SessionPolicy.BLOCK:
            return False
        if tool_name in self._approval_state.allowed_tools:
            return True
        if tool_name in self._approval_state.denied_tools:
            return False

        # Stop spinner so its background thread doesn't overwrite the
        # approval prompt with \r frames while waiting for input().
        if self._spinner is not None:
            self._spinner.stop()

        # Show interactive prompt
        print(file=sys.stdout)  # blank line before prompt
        print(
            warning(
                f"{status_symbols()['warn']}  {tool_name}({args_text})"
            ),
            file=sys.stdout,
        )
        print(
            dim("     [y] yes  [Y] always this  [a] all on  [n] no  [N] always no  [d] all off  [c] cancel"),
            file=sys.stdout,
        )
        sys.stdout.flush()

        try:
            raw = await self._read_approval_answer()
        except (EOFError, OSError):
            return False
        choice = raw.strip()

        if choice in ("y", "Y"):
            if choice == "Y":
                self._approval_state.allow_tool(tool_name)
            self._get_spinner().start(f"{tool_name} {_args_preview(args_map)}")
            return True
        if choice == "a":
            self._approval_state.set_auto()
            self._get_spinner().start(f"{tool_name} {_args_preview(args_map)}")
            return True
        if choice in ("n", "N"):
            if choice == "N":
                self._approval_state.deny_tool(tool_name)
            return False
        if choice == "d":
            self._approval_state.set_block()
            return False
        if choice == "c":
            # Honest cancel: unwind the turn (the REPL renders it like an
            # interrupt), not a silent deny that lets the agent continue.
            print(dim(f"{status_symbols()['cancel']}  Turn cancelled."), file=sys.stdout)
            raise asyncio.CancelledError(
                f"User cancelled the turn at the approval prompt for {tool_name}"
            )
        # Any other key = deny
        return False

    @staticmethod
    def _read_approval_line(prompt_text: str, stop: threading.Event) -> str:
        """Blocking stdin read that notices cancellation within ~0.2s.

        select() keeps the worker thread polling instead of parking inside
        input(), so a cancelled approval can't leave an orphaned reader
        swallowing the user's next typed line.
        """
        sys.stdout.write(prompt_text)
        sys.stdout.flush()
        use_select = hasattr(sys.stdin, "isatty") and sys.stdin.isatty() and hasattr(select, "select")
        if not use_select:
            return input(prompt_text)
        while not stop.is_set():
            try:
                ready, _, _ = select.select([sys.stdin], [], [], 0.2)
            except (OSError, ValueError):
                return input(prompt_text)
            if ready:
                return sys.stdin.readline()
        return ""

    async def _read_approval_answer(self) -> str:
        """Await one approval line; cancellation releases the reader thread."""
        stop = threading.Event()
        try:
            return await asyncio.to_thread(self._read_approval_line, dim("Approve? "), stop)
        finally:
            stop.set()

    async def _send(self, event: dict) -> None:
        """Send compatibility shim.
        (some call-sites expect _send instead of send)."""
        await self.send(event)


    def start(self) -> None:
        """Start the transport."""
        logger.debug("CLITransport started")

    def stop(self) -> None:
        """Stop the transport."""
        logger.debug("CLITransport stopped")

    # ── Backward compatibility with old WispAgentCore ───────────────

    async def _execute_turn(self, system: str, workspace: str) -> None:
        """Backward-compat shim for old WispAgentCore._execute_loop.

        Note: new code path uses entry.py → runtime.run_turn → engine.turn.
        This shim exists only for callers that still go through old agent.py.
        """
        runtime = self.runtime
        # Pop the last user message (old behavior)
        if hasattr(runtime, "messages") and runtime.messages:
            if runtime.messages[-1].get("role") == "user":
                runtime.messages.pop()
        try:
            if hasattr(runtime, "_arun"):
                async for _event in runtime._arun(system, workspace):
                    pass
                self._interrupted = False
                if hasattr(runtime, "_interrupted"):
                    runtime._interrupted = False
        except KeyboardInterrupt:
            self._interrupted = True
            if hasattr(runtime, "_interrupted"):
                runtime._interrupted = True
            raise
        except asyncio.CancelledError:
            self._interrupted = True
            if hasattr(runtime, "_interrupted"):
                runtime._interrupted = True
            raise

    # ── Banner ──────────────────────────────────────────────────────

    @staticmethod
    def _tidy_session_info(session: dict) -> tuple[str, str]:
        """Short session id and home-collapsed workspace for display.

        The full UUID stays available via /resume; the banner only needs
        enough to recognize a session at a glance.
        """
        sid = str(session.get("id", "unknown"))
        short_id = sid.split("-")[0] if "-" in sid else sid[:8]
        ws = str(session.get("workspace", "."))
        home = str(Path.home())
        if ws == home:
            ws_display = "~"
        elif ws.startswith(home + "/"):
            ws_display = "~" + ws[len(home):]
        else:
            ws_display = ws
        return short_id, ws_display

    def print_banner(
        self, stdout: Any, session: dict, model: str, skill: str | None = None
    ) -> None:
        """Print REPL startup banner with session info.

        Compact two-line header — no box panel. The REPL is interactive; a
        heavy frame on every launch is noise.
        """
        short_id, ws_display = self._tidy_session_info(session)
        msg_count = len(session.get("messages", []))

        title = bold("🔮 Wisp")
        meta_bits = [model]
        if skill:
            meta_bits.append(skill)
        stdout.write(f"\n  {title} {dim('· ' + ' · '.join(meta_bits))}\n")
        info_bits = [short_id, ws_display]
        if msg_count:
            info_bits.append(f"{msg_count} messages")
        stdout.write(f"  {dim(' · '.join(info_bits))}\n")
        stdout.write(f"  {dim('/help for commands · Ctrl+C/D to exit')}\n\n")
        stdout.flush()

    def print_continuation_banner(self, stdout: Any, session: dict, model: str) -> None:
        """Print session continuation banner — compact, un-boxed."""
        short_id, ws_display = self._tidy_session_info(session)
        msg_count = len(session.get("messages", []))
        title = session.get("title", "")

        header = "📋 Continuing Session"
        if title:
            header = f"{header} · {title}"
        stdout.write(f"\n  {bold(header)} {dim('· ' + model)}\n")
        info_bits = [short_id, f"{msg_count} messages", ws_display]
        stdout.write(f"  {dim(' · '.join(info_bits))}\n")

        # Find last user message
        last_user = None
        for m in reversed(session.get("messages", [])):
            if m.get("role") == "user":
                text = m.get("content", "")
                if isinstance(text, str) and text.strip():
                    last_user = text.strip()
                    break
        if last_user:
            preview = last_user[:80].replace("\n", " ")
            if len(last_user) > 80:
                preview += "..."
            stdout.write(f"  {dim('Last: ' + preview)}\n")

        stdout.write(f"  {dim('/help for commands · Ctrl+C/D to exit')}\n\n")
        stdout.flush()

    # ── Thinking / content buffering ────────────────────────────────

    def _buffer_thinking(self, text: str) -> None:
        """Accumulate thinking text instead of rendering immediately."""
        self._thinking_buffer.append(text)
        self._in_thinking = True

    def _buffer_content(self, text: str) -> None:
        """Accumulate content text instead of rendering immediately."""
        self._content_buffer.append(text)
        self._in_content = True

    def _flush_thinking(self, stdout: Any, width: int | None = None) -> None:
        """Render accumulated thinking as a structured block."""
        if not self._thinking_buffer:
            return
        full = "".join(self._thinking_buffer)
        self._thinking_buffer = []
        self._in_thinking = False
        if not full.strip():
            return
        w = width or _term_width()
        show_thinking = (
            getattr(self.config, "show_thinking", False) if self.config else False
        )
        if show_thinking:
            rendered = _render_thinking_block(full, box_mode=True, width=w)
            if rendered:
                stdout.write(rendered + "\n")
        else:
            line_count = full.count("\n") + 1
            # Extract first meaningful line as preview
            preview = ""
            for line in full.strip().splitlines():
                stripped = line.strip()
                if stripped:
                    preview = stripped[:60]
                    if len(stripped) > 60:
                        preview += "..."
                    break
            if preview:
                plural = "line" if line_count == 1 else "lines"
                if is_accessible():
                    stdout.write(
                        dim(f'  [Thinking] "{preview}" — {line_count} {plural}, /thinking to expand\n')
                    )
                else:
                    stdout.write(
                        dim(f'  {status_symbols()["thinking"]} Thinking: "{preview}" ({line_count} {plural} — /thinking to expand)\n')
                    )
            else:
                plural = "line" if line_count == 1 else "lines"
                if is_accessible():
                    stdout.write(
                        dim(f"  [Thinking] {line_count} {plural} — use /thinking to expand\n")
                    )
                else:
                    stdout.write(
                        dim(f"  {status_symbols()['thinking']} Thinking... ({line_count} {plural} — /thinking to expand)\n")
                    )
        stdout.flush()

    def _flush_content(self, stdout: Any, width: int | None = None) -> None:
        """Render accumulated content as a structured block."""
        if not self._content_buffer:
            return
        full = "".join(self._content_buffer)
        self._content_buffer = []
        self._in_content = False
        if not full.strip():
            return
        w = width or _term_width()
        # The response is the payload of the turn; when it follows heavy
        # tool output, one blank line keeps it from blurring into noise.
        prefix = "\n" if getattr(self, "_last_block_was_tool", False) else ""
        self._last_block_was_tool = False
        rendered = _render_content_block(full, box_mode=True, width=w)
        if rendered:
            stdout.write(prefix + rendered + "\n")
        stdout.flush()

    # ── Wait clock (latency contract) ──────────────────────────────

    def start_wait_clock(self, stdout: Any | None = None) -> None:
        """Render growing elapsed time while nothing else has rendered.

        The dead-air rule: between Enter and the first provider event the
        terminal must show that time is passing. Tty-only — piped output
        stays clean for scripting.
        """
        out = stdout or self._stdout or sys.stdout
        if not hasattr(out, "isatty") or not out.isatty():
            return
        self._wait_started = time.monotonic()
        self._wait_stop = threading.Event()

        def _tick() -> None:
            while not self._wait_stop.wait(0.25):
                elapsed = time.monotonic() - self._wait_started
                try:
                    out.write(f"\r  {dim(f'… waiting · {elapsed:.1f}s')}")
                    out.flush()
                except Exception:
                    break

        self._wait_thread = threading.Thread(target=_tick, daemon=True)
        self._wait_thread.start()

    def stop_wait_clock(self, stdout: Any | None = None) -> None:
        stop = getattr(self, "_wait_stop", None)
        if stop is None:
            return
        stop.set()
        thread = getattr(self, "_wait_thread", None)
        if thread is not None:
            thread.join(timeout=1.0)
        self._wait_stop = None
        self._wait_thread = None
        out = stdout or self._stdout or sys.stdout
        if hasattr(out, "isatty") and out.isatty():
            # Erase the ticker line so real output starts clean.
            out.write("\r\033[K")
            out.flush()

    def _reset_buffers(self) -> None:
        """Reset all buffers for a new turn."""
        self._thinking_buffer = []
        self._content_buffer = []

        self._in_thinking = False
        self._in_content = False
        if self._spinner is not None:
            self._spinner.stop()
        self._turn_number += 1
        self._progress.start_turn(self._turn_number)
        self._phase = "understand"

    def _get_spinner(self) -> Spinner:
        """Lazily create spinner bound to current stdout."""
        if self._spinner is None:
            stdout = self._stdout if self._stdout is not None else sys.stdout
            self._spinner = Spinner(stdout, mode=get_output_mode())
        return self._spinner

    @staticmethod
    def _is_error_result(result: Any) -> bool:
        """Check if a tool result indicates an error."""
        if isinstance(result, dict):
            return result.get("status") == "error"
        if isinstance(result, str):
            return result.startswith("Error") or result.startswith("[Error")
        return False

    # ── CLI-specific methods ──────────────────────────────────────

    def _render_event(self, stdout: Any, event: AgentEvent | dict) -> None:
        """Render an event to stdout with structured formatting.

        Uses ProgressTracker for phase detection, Spinner for live
        tool execution feedback, and shows file change ticker.
        """
        self.stop_wait_clock(stdout)
        # Normalize to AgentEvent
        if isinstance(event, dict):
            ev_data = event.get("data", {})
            if not ev_data:
                ev_data = {k: v for k, v in event.items() if k != "type"}
            ev = AgentEvent(type=event.get("type", ""), data=ev_data)
        else:
            ev = event

        etype = ev.type
        width = _term_width()

        # Feed to progress tracker for phase detection
        new_phase = self._progress.on_event(ev)
        if new_phase and new_phase != self._phase:
            self._phase = new_phase
            bar = render_phase_bar(new_phase, {"tools_run": self._progress.progress.tools_run}, width)
            if bar:
                stdout.write(bar + "\n")
                stdout.flush()

        if etype == EventType.THINKING:
            if self._in_content:
                return
            if not self._in_thinking:
                self._flush_content(stdout, width)
                self._in_thinking = True
            self._buffer_thinking(ev.text)

        elif etype == EventType.CONTENT:
            if self._in_thinking:
                self._flush_thinking(stdout, width)
            if not self._in_content:
                self._in_content = True
            self._buffer_content(ev.text)

        elif etype == EventType.TOOL_CALL:
            self._flush_thinking(stdout, width)
            self._flush_content(stdout, width)
            name = ev.data.get("name", "")
            args = ev.data.get("arguments", {})
            label = f"{name} {_args_preview(args)}"
            spinner = self._get_spinner()
            spinner.start(label)

        elif etype == EventType.TOOL_RESULT:
            self._flush_thinking(stdout, width)
            self._flush_content(stdout, width)
            name = ev.data.get("name", "")
            result = ev.data.get("result", "")
            duration_ms = ev.data.get("duration_ms")
            if duration_ms and duration_ms < 50:
                # Fast tools: stop spinner, show result inline
                if self._spinner is not None:
                    self._spinner.stop()
                rendered = self._render_tool_result(name, result, duration_ms, width)
                if rendered:
                    self._last_block_was_tool = True
                    stdout.write(rendered + "\n")
                    stdout.flush()
            else:
                spinner = self._get_spinner()
                duration_str = _format_duration(duration_ms) if duration_ms else ""
                label = f"{name} {duration_str}".strip()
                if self._is_error_result(result):
                    spinner.fail(label)
                else:
                    spinner.succeed(label)
                # Show result content below spinner line
                rendered = self._render_tool_result(name, result, duration_ms, width, skip_header=True)
                if rendered:
                    self._last_block_was_tool = True
                    stdout.write(rendered + "\n")
                    stdout.flush()

        elif etype == EventType.DONE:
            self._flush_thinking(stdout, width)
            self._flush_content(stdout, width)
            # Ensure spinner is stopped on turn completion
            if self._spinner is not None:
                self._spinner.stop()

        elif etype == EventType.ERROR:
            self._flush_thinking(stdout, width)
            self._flush_content(stdout, width)
            # Stop spinner on errors so it doesn't keep spinning
            if self._spinner is not None:
                self._spinner.stop()
            msg = ev.data.get("message", "")
            error_prefix = "[ERROR] " if is_accessible() else f"{status_symbols()['fail']} "
            stdout.write(
                _box(f"{error_prefix}{msg}", title="Error", style="error", double=True, width=width)
                + "\n"
            )
            stdout.flush()

        elif etype == EventType.PROVIDER_STATUS:
            self._flush_thinking(stdout, width)
            rendered = render_provider_status(ev, width)
            if rendered:
                stdout.write(rendered + "\n")
                stdout.flush()

        elif etype == EventType.SUBAGENT:
            self._flush_thinking(stdout, width)
            rendered = render_subagent_status(ev, width)
            if rendered:
                stdout.write(rendered + "\n")
                stdout.flush()

        elif etype == EventType.SYSTEM:
            self._flush_thinking(stdout, width)
            self._flush_content(stdout, width)
            level = ev.data.get("level", "info")
            if level == "debug":
                return
            msg = ev.data.get("message", "")
            if level == "warning":
                stdout.write(warning(f"  ⚠ {msg}\n"))
            else:
                stdout.write(info(f"  ℹ {msg}\n"))
            stdout.flush()

        elif etype == EventType.APPROVAL_REQUEST:
            # Silently absorbed — handler resolves on next step
            pass

        elif etype == EventType.STEERING_PAUSED:
            stdout.write(
                warning(f"  ⏸  Steering paused: {ev.data.get('reason', '')}\n")
            )
            stdout.flush()

        elif etype == EventType.STEERING_RESUMED:
            stdout.write(success("  ▶  Steering resumed\n"))
            stdout.flush()

        elif etype == EventType.STEERING_INJECT:
            stdout.write(
                dim(f"  💉 Steering feedback: {ev.data.get('text', '')[:80]}\n")
            )
            stdout.flush()

        else:
            # Default: silently ignore unknown event types
            # (checkpoint, stream_complete, custom extension events, etc.)
            pass

    def _render_tool_result(
        self, name: str, result: Any, duration_ms: float | None, width: int,
        skip_header: bool = False,
    ) -> str | None:
        """Render a tool result with structured output and diff support.
        
        Supports all output modes: unicode, ascii, accessible, minimal.
        """
        duration_str = _format_duration(duration_ms)

        # Parse JSON result and determine true success/error status
        meta: dict | None = None
        parsed: dict | None = None
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
            # Structured payloads (spawn/fanout/MCP) carry non-string data;
            # coercing here keeps preview/wrap code on strings downstream.
            result_text = _coerce_tool_data(result.get("data", result))
        else:
            result_text = str(result)

        if isinstance(parsed, dict):
            is_error = parsed.get("status") == "error"
        elif isinstance(result, dict):
            is_error = result.get("status") == "error"
        else:
            is_error = result_text.startswith("[") or result_text.startswith("Error")

        # Mode-aware icon selection
        sym = status_symbols()
        icon = sym["fail"] if is_error else sym["ok"]

        diff_text = (meta or {}).get("diff", "")
        is_edit_tool = name in ("write_file", "edit_file", "edit_file_multi")

        # Helper: clean header \u2014 icon colored, name plain, duration dim. No fill dots.
        def _build_header(icon_str: str, tool_name: str, dur_str: str) -> str:
            icon_color = error if is_error else success
            head = f"  {icon_color(icon_str)} {tool_name}"
            if dur_str:
                dur = "\u00B7 " + dur_str
                head += f" {dim(dur)}"
            return head

        # Edit tools with a diff: show diff regardless of success/failure.
        # Minimal mode skips the box entirely (summary line is enough);
        # accessible mode strips ANSI so screen readers get clean text.
        if is_edit_tool and diff_text:
            mode = get_output_mode()
            if mode == OutputMode.MINIMAL:
                summary = _preview_lines(result_text, max_lines=1)
                header = _build_header(icon, name, duration_str) \
                    if not skip_header else ""
                parts = [p for p in (header, summary) if p]
                return "\n".join(parts)
            summary = _preview_lines(result_text, max_lines=1)
            try:
                from wisp.diff_renderer import render_diff_box, shorten_diff_title
                lang = _detect_language(meta.get("path", ""))
                plain_diff = mode == OutputMode.ACCESSIBLE
                diff_box = render_diff_box(
                    diff_text,
                    title=shorten_diff_title(meta.get("path", "")),
                    width=width,
                    box_mode=True,
                    language=None if plain_diff else lang,
                    plain=plain_diff,
                )
                if skip_header:
                    return f"{summary}\n{diff_box}"
                header = _build_header(icon, name, duration_str)
                return f"{header}\n{summary}\n{diff_box}"
            except ImportError:
                pass

        # Full-output tools (non-edit): preserve multi-line formatting
        if name in _FULL_OUTPUT_TOOLS and not is_edit_tool:
            output_str = result_text
            if not self.show_tool_output:
                line_count = output_str.count("\n") + 1
                return f"{_build_header(icon, name, duration_str)}{dim(f' — {line_count} lines')}"

            # Light framing: thin dim rule + indented output (no heavy box, no emoji)
            label_str = f"{name} output"
            rule = _rule("-", label_str, style_fn=dim, width=width)
            inner_w = width - 4
            wrapped = wrap_text_wide(output_str.strip(), inner_w)
            # Cap floods: show first N wrapped lines, summarize the rest.
            _MAX_SHOW = 30
            if len(wrapped) > _MAX_SHOW:
                indented = "\n".join(dim(f"  {line}") for line in wrapped[:_MAX_SHOW])
                indented += "\n" + dim(f"  … +{len(wrapped) - _MAX_SHOW} more lines")
            else:
                indented = "\n".join(dim(f"  {line}") for line in wrapped)
            if skip_header:
                return indented
            header = _build_header(icon, name, duration_str)
            return f"{header}\n{rule}\n{indented}"

        # Regular / compact tool results
        if not self.show_tool_output:
            if skip_header:
                return None
            return _build_header(icon, name, duration_str)

        preview = _preview_lines(result_text)
        if skip_header:
            return preview
        header = _build_header(icon, name, duration_str)
        return f"{header}\n{preview}" if preview else header


# ── Signal handling ────────────────────────────────────────────────


def _handle_sigint(signum, frame):
    """Mark interruption on all live transport instances AND their cores."""
    for inst in _transport_instances:
        inst._interrupted = True
        if hasattr(inst, "runtime") and hasattr(inst.runtime, "_interrupted"):
            inst.runtime._interrupted = True
        if hasattr(inst, "core") and hasattr(inst.core, "_interrupted"):
            inst.core._interrupted = True
        # Stop spinner immediately so it doesn't keep writing frames
        if hasattr(inst, "_spinner") and inst._spinner is not None:
            inst._spinner.stop()
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


# ── Standalone event renderer (for programmatic use) ───────────────


def _render_event(
    event: AgentEvent,
    show_thinking: bool = False,
    show_tool_output: bool = True,
    box_mode: bool = True,
) -> Optional[str]:
    """Render an AgentEvent to a terminal string. Returns None for silent events."""
    etype = event.type

    if etype == EventType.CONTENT:
        raw = event.text
        if not box_mode:
            return raw
        return _render_content_block(raw, box_mode=True, width=80)

    if etype == EventType.THINKING:
        text = event.text
        if not show_thinking:
            line_count = text.count("\n") + 1
            return dim(f"  {status_symbols()['thinking']} Thinking... ({line_count} lines — /thinking to expand)")
        return _render_thinking_block(text, box_mode=True, width=80)

    if etype == EventType.TOOL_CALL:
        name = event.data.get("name", "")
        args = event.data.get("arguments", {})
        return _render_tool_call(name, args, box_mode=True)

    if etype == EventType.TOOL_RESULT:
        name = event.data.get("name", "")
        result = event.data.get("result", "")
        return f"  {status_symbols()['ok']} {name}: {result}"[:200]

    if etype == EventType.ERROR:
        return f"  {status_symbols()['fail']} Error: {event.text}"

    if etype == EventType.SYSTEM:
        return f"  ℹ {event.data.get('message', '')}"

    if etype == EventType.PROVIDER_STATUS:
        return render_provider_status(event, width=80)

    if etype == EventType.SUBAGENT:
        return render_subagent_status(event, width=80)

    if etype == EventType.DONE:
        return None

    return None
