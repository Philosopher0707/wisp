"""CLI transport for Wisp — renders AgentEvent to terminal, handles user input.

This module contains all I/O-specific code: printing, colors, readline,
signal handling, and approval prompts. It wraps WispAgentCore and drives
the REPL loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import sys
import threading
import weakref
from typing import Optional

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
    TYPE_CHECKPOINT_CREATED,
    TYPE_STEERING_PAUSED,
    TYPE_STEERING_RESUMED,
    TYPE_STEERING_INJECT,
)
from wisp.colors import success, error, warning, info, dim, accent
from wisp.session import Session, SessionManager, format_session_preview
from wisp.skills import find_skill

logger = logging.getLogger(__name__)

# ── Signal handling ──────────────────────────────────────────────────

_transport_instances: weakref.WeakSet = weakref.WeakSet()
_old_sigint_handler = None


def _handle_sigint(signum, frame):
    """Mark interruption on all live transport instances so loops exit gracefully."""
    for inst in _transport_instances:
        inst._interrupted = True
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

def _is_interactive() -> bool:
    """Detect if stdin is a real terminal (vs pipe/redirect)."""
    return sys.stdin.isatty()


def _print_separator():
    """Print a visual separator between turns."""
    try:
        width = shutil.get_terminal_size().columns
    except OSError:
        width = 50
    print("─" * max(20, min(width, 80)))


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


# ── Input handling ───────────────────────────────────────────────────

def _input_line(prompt: str, allow_multiline: bool = True) -> str:
    """Read input from the user with a prompt.

    Interactive mode:
      - Uses readline for arrow-key editing and history.
      - Supports multi-line input: if a line ends with '\\',
        the next line is appended.
      - Auto-detects paste: if multiple lines arrive rapidly,
        they are combined into a single multi-line input.

    Non-interactive mode:
      - Reads raw bytes to survive invalid UTF-8 in piped input.
    """
    if sys.stdin.isatty():
        lines = []
        while True:
            try:
                if not lines:
                    rl_prompt = f"\001\033[1m\002{prompt}\001\033[0m\002"
                else:
                    rl_prompt = "... "
                line = input(rl_prompt)
            except KeyboardInterrupt:
                print()
                raise
            except (EOFError, OSError, UnicodeDecodeError):
                return ""

            stripped = line.rstrip()
            if allow_multiline and stripped.endswith("\\"):
                lines.append(stripped[:-1])
                continue
            lines.append(line)

            if allow_multiline and lines:
                try:
                    import select
                    ready, _, _ = select.select([sys.stdin], [], [], 0.1)
                    if ready:
                        while True:
                            try:
                                ready2, _, _ = select.select([sys.stdin], [], [], 0)
                                if not ready2:
                                    break
                                extra_line = input()
                                lines.append(extra_line)
                            except (EOFError, OSError):
                                break
                        return "\n".join(lines)
                except (ImportError, OSError):
                    pass

            break
        return "\n".join(lines)

    try:
        data = sys.stdin.buffer.readline()
    except (EOFError, OSError):
        return ""
    if not data:
        return ""
    return data.decode("utf-8", errors="replace").rstrip("\n")


# ── Approval prompts ─────────────────────────────────────────────────

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


# ── Event rendering ──────────────────────────────────────────────────

def _render_event(event: AgentEvent, show_thinking: bool = False) -> Optional[str]:
    """Render an AgentEvent to a terminal string. Returns None for silent events."""
    etype = event.type

    if etype == TYPE_CONTENT:
        return event.text

    if etype == TYPE_THINKING:
        if show_thinking:
            return dim(f"⏳ Thinking: {event.text}")
        return None

    if etype == TYPE_TOOL_CALL:
        name = event.data.get("name", "")
        args = event.data.get("arguments", {})
        preview = _args_preview(args)
        return dim(f"  🛠  {name}({preview})")

    if etype == TYPE_TOOL_RESULT:
        name = event.data.get("name", "")
        result = event.data.get("result", "")
        if isinstance(result, str) and result.startswith("{"):
            try:
                parsed = json.loads(result)
                data = _coerce_tool_data(parsed.get("data", result))
                result = data
            except (json.JSONDecodeError, KeyError):
                pass
        preview = str(result)[:200].replace("\n", " ")
        if len(preview) > 200:
            preview += "..."
        return dim(f"     → {preview}")

    if etype == TYPE_ERROR:
        return error(f"✗ {event.data.get('message', '')}")

    if etype == TYPE_SYSTEM:
        level = event.data.get("level", "info")
        if level == "debug":
            return None  # Suppress debug in CLI
        msg = event.data.get("message", "")
        if level == "warning":
            return warning(f"⚠ {msg}")
        return info(f"ℹ {msg}")

    if etype == TYPE_APPROVAL_REQUEST:
        return warning(f"  ⚠️  Approval required: {event.data.get('reason', '')}")

    if etype == TYPE_CHECKPOINT_CREATED:
        cid = event.data.get("checkpoint_id", "")[:12]
        desc = event.data.get("description", "")
        return dim(f"  📸 checkpoint {cid}: {desc}")

    if etype == TYPE_STEERING_PAUSED:
        return warning(f"  ⏸  Steering paused: {event.data.get('reason', '')}")

    if etype == TYPE_STEERING_RESUMED:
        return success("  ▶  Steering resumed")

    if etype == TYPE_STEERING_INJECT:
        return dim(f"  💉 Steering feedback: {event.data.get('text', '')[:80]}")

    if etype == TYPE_DONE:
        reason = event.data.get("reason", "")
        turns = event.data.get("turns", 0)
        msg = ""
        if reason == "max_iterations":
            msg = f"\n⚠️  Hit max iterations ({turns}/{turns}). Type 'continue' or increase --max-iterations."
        elif reason == "interrupted":
            msg = "\n⏹  Interrupted."
        elif reason == "error":
            msg = "\n✗ Stream error — turn aborted."
        return dim(msg) if msg else None

    return None


def _args_preview(args: dict) -> str:
    """Short one-line preview of tool arguments."""
    parts = []
    path = args.get("path", args.get("command", ""))
    if path:
        s = str(path)
        parts.append(s[:60])
    content = args.get("content", "")
    if content:
        parts.append(f"({len(content)} chars)")
    return ", ".join(parts) if parts else "..."


# ── CLITransport ─────────────────────────────────────────────────────

class CLITransport:
    """Terminal transport for WispAgentCore.

    Drives the REPL loop, renders events with colors, handles user input,
    and manages signal interrupts.
    """

    def __init__(self, core: WispAgentCore):
        self.core = core
        self.show_thinking = core.config.show_thinking
        self.auto_approve = core.config.auto_approve
        self._interrupted = False
        self._pending_approval = None
        _transport_instances.add(self)

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
                self.core._build_system_prompt(skill_name, workspace=self.core.config.workspace),
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
        print(info(f"📋 Continuing session: {self.core.session.id}"))
        if loaded.title:
            print(f"   {dim('Title:')} {loaded.title}")
        print(f"   {dim('Model:')} {self.core.config.model}")
        if loaded.model and loaded.model != self.core.config.model:
            print(warning(f"   ⚠️  Session was created with model '{loaded.model}'. Now using '{self.core.config.model}'."))
        print(f"   {dim('Messages so far:')} {len(self.core.messages)}")
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
            print(f"   {dim('Last prompt:')} {preview}")
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
        print(info(f"🔮 Wisp (model: {self.core.config.model})"))
        print(f"   {dim('Session:')} {self.core.session.id}")
        if msg_count:
            print(f"   {dim('History:')} {msg_count} messages so far")
        if skill_name:
            print(f"   {dim('Skill:')} {skill_name}")
        print()
        print(dim("Type /help for commands, 'exit', or press Ctrl+C to end."))
        print(dim("Tip: end a line with \\ to continue on the next line."))
        print()

        self._interrupted = False
        try:
            while not self._interrupted:
                try:
                    user_input = _input_line("➜ ")
                except KeyboardInterrupt:
                    print(error("\n⏹  Exiting."))
                    break

                cmd = user_input.strip()
                if not cmd:
                    if not _is_interactive():
                        break
                    continue

                # Slash commands
                from wisp.commands import dispatch, ExitREPL
                try:
                    if dispatch(cmd, self.core):
                        continue
                except ExitREPL:
                    print(success("👋 Goodbye."))
                    break

                # Legacy non-slash commands
                if cmd in ("exit", "quit"):
                    print(success("👋 Goodbye."))
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

                print()

                try:
                    system = self.core._build_system_prompt(skill_name)
                    self.core._add_message("user", self.core._expand_continuation(cmd))
                    asyncio.run(self._execute_turn(system, ws))
                except KeyboardInterrupt:
                    print(error("\n⏹  Turn interrupted. Type 'exit' to quit or continue chatting."))
                    self._interrupted = False
                    continue
                except Exception as e:
                    print(error(f"\n✗ Unexpected error in REPL: {e}"))
                    logger.error("REPL turn crashed", exc_info=True)
                    self._interrupted = False
                    continue

                if not self._interrupted:
                    _print_separator()

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
        system = self.core._build_system_prompt(skill_name)
        self.core._add_message("user", self.core._expand_continuation(prompt))
        await self._execute_turn(system, self.core.config.workspace or ".")

    async def _execute_turn(self, system: str, workspace: str) -> None:
        """Execute one user turn by consuming events from core._arun()."""
        self._interrupted = False

        # _arun() adds the user message internally, so we pass the raw prompt.
        # Extract the last user message content before _arun() clears/re-adds it.
        prompt = ""
        if self.core.messages and self.core.messages[-1].get("role") == "user":
            raw = self.core.messages[-1].get("content", "")
            from wisp.core.message_format import extract_text
            prompt = extract_text(raw)
            self.core.messages.pop()

        # Build approval handler that prompts the user in the terminal
        async def _cli_approval(name: str, args: dict, reason: str) -> tuple[bool, Optional[dict]]:
            approved = _prompt_dangerous(name, reason)
            return (approved, None)

        _in_thinking = False
        _content_started = False
        try:
            async for event in self.core._arun(prompt, system=system, approval_handler=_cli_approval):
                if self._interrupted:
                    break

                show_thinking = self.core.config.show_thinking

                # Real-time streaming for thinking/content tokens
                if event.type == TYPE_THINKING:
                    if _content_started:
                        continue
                    if not _in_thinking:
                        _in_thinking = True
                        if show_thinking:
                            print(dim("⏳ Thinking: "), end="", flush=True)
                        else:
                            print(dim("⏳ Thinking..."), end="", flush=True)
                    if show_thinking:
                        print(event.text, end="", flush=True)
                elif event.type == TYPE_CONTENT:
                    _content_started = True
                    if _in_thinking:
                        _in_thinking = False
                        if show_thinking:
                            print()
                        print()
                    print(event.text, end="", flush=True)
                elif event.type == TYPE_TOOL_CALL:
                    _content_started = False
                    _in_thinking = False
                    rendered = _render_event(event, show_thinking)
                    if rendered is not None:
                        print(rendered, end="\n", flush=True)
                else:
                    _in_thinking = False
                    rendered = _render_event(event, show_thinking)
                    if rendered is not None:
                        print(rendered, end="\n", flush=True)
        except Exception:
            if self.core.messages and self.core.messages[-1].get("role") != "user":
                self.core.messages.append({"role": "user", "content": [{"type": "text", "text": prompt}]})
            raise
