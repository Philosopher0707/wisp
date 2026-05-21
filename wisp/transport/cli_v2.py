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
import shutil
import sys
import threading
from typing import Any

from .base import Transport
from .renderer import (
    format_duration as _format_duration,
    format_arg_value as _format_arg_value,
    wrap_text as _wrap_text,
    render_tool_call as _render_tool_call,
    render_thinking_block as _render_thinking_block,
    render_content_block as _render_content_block,
    render_done_reason as _render_done_reason,
    _box,
    _rule,
)
from wisp.colors import dim, error, warning, success, info, accent
from wisp.core.events import AgentEvent, EventType

logger = logging.getLogger(__name__)


def _is_interactive() -> bool:
    """Return True if stdin is a tty."""
    return sys.stdin.isatty()


def _input_line(prompt: str, allow_multiline: bool = True) -> str:
    """Read a line from stdin."""
    try:
        if sys.stdin.isatty():
            return input(prompt)
        # Non-tty: read from buffer to handle piped input
        data = sys.stdin.buffer.readline()
        if not data:
            return ""
        return data.decode("utf-8", errors="replace").rstrip("\n\r")
    except EOFError:
        return ""
    except UnicodeDecodeError:
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


class _SessionAdapter:
    """Wraps a session dict to look like the old Session object."""

    def __init__(self, session: dict):
        self._session = session

    def __getattr__(self, name: str):
        if name == "_session":
            raise AttributeError(name)
        if name in (
            "id",
            "created_at",
            "updated_at",
            "model",
            "workspace",
            "messages",
            "title",
            "compaction_history",
            "task_ids",
        ):
            return self._session.get(name)
        raise AttributeError(f"_SessionAdapter has no attribute '{name}'")

    def __setattr__(self, name: str, value):
        if name == "_session":
            super().__setattr__(name, value)
            return
        if name in ("messages", "title", "compaction_history", "task_ids"):
            self._session[name] = value
            return
        super().__setattr__(name, value)

    def touch(self) -> None:
        from datetime import datetime, timezone

        self._session["updated_at"] = datetime.now(timezone.utc).isoformat()

    def compact(self, keep_recent: int = 4, max_context_tokens: int = 4096) -> None:
        """Compact session messages, keeping recent ones."""
        msgs = self._session.get("messages", [])
        if len(msgs) <= keep_recent:
            return
        # Simple compaction: summarize older messages
        to_summarize = msgs[:-keep_recent]
        keep = msgs[-keep_recent:]
        summary = f"[Previous conversation: {len(to_summarize)} messages summarized]"
        self._session["messages"] = [{"role": "system", "content": summary}] + keep
        self._session.setdefault("compaction_history", []).append(
            {
                "before": len(msgs),
                "after": len(self._session["messages"]),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def to_dict(self) -> dict:
        return dict(self._session)


class AgentAdapter:
    """Adapts the new runtime+session+config to the old WispAgentCore API.

    This lets existing slash commands in wisp/commands.py work without
    modification.
    """

    def __init__(self, runtime: Any, config: Any, session: dict):
        self.runtime = runtime
        self.config = config
        self.session = _SessionAdapter(session)
        self.messages = session["messages"]
        self._system_prompt_cache: dict = {}
        self._active_skill: str | None = None
        self._pending_continue: bool = False
        self._interrupted: bool = False
        self._paused = asyncio.Event()
        self._paused.set()

    # ── Provider access ─────────────────────────────────────────────

    @property
    def client(self):
        """Return the provider from the cached core, if available."""
        try:
            core = self.runtime._get_core()
            return getattr(core, "provider", None)
        except Exception:
            return None

    # ── Metrics / circuit breaker ─────────────────────────────────────

    @property
    def metrics(self):
        return getattr(self.runtime, "telemetry", None)

    @property
    def circuit_breaker(self):
        return getattr(self.runtime, "security", None)

    # ── Session helpers ─────────────────────────────────────────────

    def _save_session(self) -> None:
        store = getattr(self.runtime, "store", None)
        if store is not None:
            store.save_session(self.session.to_dict())

    def _maybe_compact_session(self) -> None:
        asyncio.create_task(self.runtime.maybe_compact(self.session.to_dict()))

    def _build_system_prompt(
        self, skill_name: str | None = None, query: str = ""
    ) -> str:
        # Try to delegate to core
        try:
            core = self.runtime._get_core()
            if hasattr(core, "_build_system_prompt"):
                return core._build_system_prompt(skill_name, query)
        except Exception:
            pass
        # Fallback
        return "You are Wisp, a helpful coding assistant."

    def _estimate_tokens(self, messages: list[dict]) -> int:
        chars = sum(len(str(m.get("content", ""))) for m in messages)
        return chars // 4

    def _add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})

    def _expand_continuation(self, text: str) -> str:
        return text

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
        self._thinking_shown: bool = False
        self._content_buffer: list[str] = []
        self._in_thinking: bool = False
        self._in_content: bool = False
        self.show_thinking: bool = (
            getattr(config, "show_thinking", False) if config else False
        )
        self.show_tool_output: bool = True

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
        """CLI transport can prompt for approval.

        In a full implementation, this would ask the user.
        For now, auto-approve.
        """
        return True

    def start(self) -> None:
        """Start the transport."""
        logger.debug("CLITransport started")

    def stop(self) -> None:
        """Stop the transport."""
        logger.debug("CLITransport stopped")

    # ── Backward compatibility with old WispAgentCore ───────────────

    async def _execute_turn(self, system: str, workspace: str) -> None:
        """Backward-compat shim for old WispAgentCore._execute_loop.

        Delegates to the runtime's turn() if available, otherwise
        falls back to old-core _arun() streaming.
        """
        runtime = self.runtime
        # Pop the last user message (old behavior)
        if hasattr(runtime, "messages") and runtime.messages:
            if runtime.messages[-1].get("role") == "user":
                runtime.messages.pop()
        try:
            # If runtime has the old-core _arun method, use it
            if hasattr(runtime, "_arun"):
                async for _event in runtime._arun(system, workspace):
                    pass
                # Reset interrupt flags after successful turn
                self._interrupted = False
                if hasattr(runtime, "_interrupted"):
                    runtime._interrupted = False
                return
            # Otherwise assume new Runtime interface
            from wisp.runtime_protocol import Runtime

            if isinstance(runtime, Runtime):
                await runtime.run_turn(system, workspace)
                self._interrupted = False
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

    def print_banner(
        self, stdout: Any, session: dict, model: str, skill: str | None = None
    ) -> None:
        """Print REPL startup banner with session info."""
        width = min(72, _term_width() - 4)

        sid = session.get("id", "unknown")
        ws = session.get("workspace", ".")
        msg_count = len(session.get("messages", []))

        lines = [
            f"  Model:      {model}",
            f"  Session:    {sid}",
            f"  Workspace:  {ws}",
        ]
        if msg_count:
            lines.append(f"  History:    {msg_count} messages")
        if skill:
            lines.append(f"  Skill:      {skill}")
        lines.append("")
        lines.append("  /help for commands  ·  Ctrl+C/D to exit")

        banner = _box("\n".join(lines), title="🔮 Wisp", style="dim", width=width)
        stdout.write(banner + "\n\n")
        stdout.flush()

    def print_continuation_banner(self, stdout: Any, session: dict, model: str) -> None:
        """Print session continuation banner."""
        width = min(72, _term_width() - 4)

        sid = session.get("id", "unknown")
        ws = session.get("workspace", ".")
        msg_count = len(session.get("messages", []))
        title = session.get("title", "")

        lines = []
        if title:
            lines.append(f"  Title:      {title}")
        lines.append(f"  Model:      {model}")
        lines.append(f"  Session:    {sid}")
        lines.append(f"  Messages:   {msg_count}")
        lines.append(f"  Workspace:  {ws}")

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
            lines.append(f"  Last:       {preview}")

        lines.append("")
        lines.append("  /help for commands  ·  Ctrl+C/D to exit")

        banner = _box(
            "\n".join(lines), title="📋 Continuing Session", style="dim", width=width
        )
        stdout.write(banner + "\n\n")
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
        if not self._thinking_buffer or self._thinking_shown:
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
            stdout.write(
                dim(f"  🧠 Thinking... ({line_count} lines — /thinking to expand)\n")
            )
        stdout.flush()
        self._thinking_shown = True

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
        rendered = _render_content_block(full, box_mode=True, width=w)
        if rendered:
            stdout.write(rendered + "\n")
        stdout.flush()

    def _reset_buffers(self) -> None:
        """Reset all buffers for a new turn."""
        self._thinking_buffer = []
        self._content_buffer = []
        self._thinking_shown = False
        self._in_thinking = False
        self._in_content = False

    # ── CLI-specific methods ──────────────────────────────────────

    async def run(
        self, stdin: Any, stdout: Any, session_id: str, model: str, workspace: str
    ) -> None:
        """Run the CLI REPL loop.

        Uses a background thread for stdin reads to avoid blocking
        the asyncio event loop on TTY readline().
        """
        session = await self.runtime.get_or_create_session(
            session_id=session_id,
            model=model,
            workspace=workspace,
        )

        stdout.write("Wisp ready.\n")
        stdout.flush()

        # Dedicated thread + queue for stdin — avoids asyncio.to_thread
        # blocking issues on real TTYs where readline() can't be cancelled.
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        stop_event = threading.Event()
        loop = asyncio.get_running_loop()

        def _put(item: str | None) -> None:
            """Thread-safe put into asyncio queue via call_soon_threadsafe."""
            queue.put_nowait(item)

        def _reader() -> None:
            while not stop_event.is_set():
                try:
                    line = stdin.readline()
                except Exception:
                    loop.call_soon_threadsafe(_put, None)
                    return
                if not line:
                    loop.call_soon_threadsafe(_put, None)
                    return
                prompt = line.strip()
                if prompt.lower() in ("exit", "quit"):
                    loop.call_soon_threadsafe(_put, None)
                    return
                loop.call_soon_threadsafe(_put, prompt)

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        try:
            while True:
                prompt = await queue.get()
                if prompt is None:
                    break
                if not prompt:
                    continue

                # ── Slash commands ──────────────────────────────────────
                if prompt.startswith("/"):
                    from wisp.commands import dispatch
                    from wisp.exceptions import ExitREPL

                    adapter = AgentAdapter(self.runtime, self.config, session)
                    try:
                        if dispatch(prompt, adapter):
                            continue
                    except ExitREPL:
                        break
                    except Exception as exc:
                        logger.exception("Slash command failed")
                        stdout.write(f"Error: {exc}\n")
                        stdout.flush()
                        continue

                self._reset_buffers()
                try:
                    async for event in self.runtime.run_turn(session, prompt):
                        self._render_event(stdout, event)
                    self._flush_thinking(stdout)
                    self._flush_content(stdout)
                except Exception as exc:
                    logger.exception("Error during turn")
                    self._flush_thinking(stdout)
                    self._flush_content(stdout)
                    self._reset_buffers()
                    stdout.write(f"Error: {exc}\n")
                    stdout.flush()
        finally:
            stop_event.set()
            reader_thread.join(timeout=1.0)

    def _render_event(self, stdout: Any, event: AgentEvent | dict) -> None:
        """Render an event to stdout with structured formatting.

        Matches the legacy CLI's rich output with panels, rules,
        and color-coded tool results.
        """
        # Normalize to AgentEvent
        if isinstance(event, dict):
            # Handle both full serialization ({type, data}) and flat dicts ({type, text, ...})
            ev_data = event.get("data", {})
            if not ev_data:
                # Flat dict: wrap non-type keys into data
                ev_data = {k: v for k, v in event.items() if k != "type"}
            ev = AgentEvent(type=event.get("type", ""), data=ev_data)
        else:
            ev = event

        etype = ev.type
        width = _term_width()

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
            rendered = _render_tool_call(name, args, box_mode=True)
            if rendered:
                stdout.write(rendered + "\n")
                stdout.flush()

        elif etype == EventType.TOOL_RESULT:
            self._flush_thinking(stdout, width)
            self._flush_content(stdout, width)
            name = ev.data.get("name", "")
            result = ev.data.get("result", "")
            duration_ms = ev.data.get("duration_ms")
            rendered = self._render_tool_result(name, result, duration_ms, width)
            if rendered:
                stdout.write(rendered + "\n")
                stdout.flush()

        elif etype == EventType.DONE:
            self._flush_thinking(stdout, width)
            self._flush_content(stdout, width)
            turns = ev.data.get("turns", 0)
            msg = _render_done_reason(ev, turns)
            if msg:
                stdout.write(msg + "\n")
                stdout.flush()
            # Turn separator
            stdout.write("\n" + dim("─" * width) + "\n\n")
            stdout.flush()

        elif etype == EventType.ERROR:
            self._flush_thinking(stdout, width)
            self._flush_content(stdout, width)
            msg = ev.data.get("message", "")
            stdout.write(
                _box(f"✗ {msg}", title="Error", style="error", double=True, width=width)
                + "\n"
            )
            stdout.flush()

        elif etype == EventType.SYSTEM:
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
        self, name: str, result: Any, duration_ms: float | None, width: int
    ) -> str | None:
        """Render a tool result with structured output and diff support."""
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
            result_text = result.get("data", str(result))
        else:
            result_text = str(result)

        if isinstance(parsed, dict):
            is_error = parsed.get("status") == "error"
        elif isinstance(result, dict):
            is_error = result.get("status") == "error"
        else:
            is_error = result_text.startswith("[") or result_text.startswith("Error")

        diff_text = (meta or {}).get("diff", "")
        is_edit_tool = name in ("write_file", "edit_file", "edit_file_multi")

        # Edit tools with a diff: show diff regardless of success/failure
        if is_edit_tool and diff_text:
            icon = "✗" if is_error else "✓"
            header = dim(
                f"  {icon} {name} ({duration_str}) "
                + "·" * max(0, width - len(f"  {icon} {name} ({duration_str}) ") - 2)
            )
            summary = dim(f"     → {result_text[:200].replace(chr(10), ' ')}")
            try:
                from wisp.diff_renderer import render_diff_box

                lang = _detect_language(meta.get("path", ""))
                diff_box = render_diff_box(
                    diff_text,
                    title=f"Diff — {meta.get('path', '')}"[:60],
                    width=width,
                    box_mode=True,
                    language=lang,
                )
                return f"{header}\n{summary}\n{diff_box}"
            except ImportError:
                pass

        # Full-output tools (non-edit): preserve multi-line formatting
        if name in _FULL_OUTPUT_TOOLS and not is_edit_tool:
            icon = "✗" if is_error else "✓"
            output_str = result_text
            if not self.show_tool_output:
                line_count = output_str.count("\n") + 1
                return dim(
                    f"  {icon} {name} ({duration_str}) — {line_count} lines of output · · · · · · · · · · · · · · · · · · ·"
                )

            header = dim(
                f"  {icon} {name} ({duration_str}) "
                + "·" * max(0, width - len(f"  {icon} {name} ({duration_str}) ") - 2)
            )
            body = _box(output_str, width=width)
            return f"{header}\n{body}"

        # Regular / compact tool results
        icon = "✗" if is_error else "✓"
        if not self.show_tool_output:
            return dim(
                f"  {icon} {name} ({duration_str}) · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · · ·"
            )

        if is_error:
            preview = result_text[:200].replace("\n", " ")
            return dim(f"  ✗ {name} ({duration_str})") + "\n" + dim(f"     → {preview}")

        preview = result_text[:200].replace("\n", " ")
        if len(result_text) > 200:
            preview += "..."
        header = dim(
            f"  ✓ {name} ({duration_str}) "
            + "·" * max(0, width - len(f"  ✓ {name} ({duration_str}) ") - 2)
        )
        return f"{header}\n" + dim(f"     → {preview}")
