"""CLI transport for Wisp v2.

Replaces: ad-hoc CLI handling in cli.py.
Clean separation: transport owns the wire protocol, runtime owns the logic.

Design:
  - Reads from stdin, writes to stdout
  - Routes input to runtime.run_turn()
  - Prints events in human-readable format
  - Handles EOF and exit commands gracefully
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from .base import Transport

logger = logging.getLogger(__name__)


def _box(content: str, title: str = "", width: int = 72) -> str:
    """Simple box-drawn panel for REPL banner."""
    inner = width - 4
    lines = content.split("\n")
    
    if title:
        title_text = f" {title} "
        top = "┌" + title_text + "─" * (width - 2 - len(title_text)) + "┐"
    else:
        top = "┌" + "─" * (width - 2) + "┐"
    
    bottom = "└" + "─" * (width - 2) + "┘"
    
    result = [top]
    for line in lines:
        if len(line) > inner:
            line = line[:inner]
        result.append("│ " + line.ljust(inner) + " │")
    result.append(bottom)
    return "\n".join(result)


class CLITransport(Transport):
    """CLI transport layer."""

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self._stdin: Any = None
        self._stdout: Any = None
        self._thinking_buffer: list[str] = []
        self._thinking_shown: bool = False

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

    # ── Banner ──────────────────────────────────────────────────────

    def print_banner(self, stdout: Any, session: dict, model: str, skill: str | None = None) -> None:
        """Print REPL startup banner with session info."""
        import shutil
        
        width = min(72, shutil.get_terminal_size().columns - 4)
        
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
        
        banner = _box("\n".join(lines), title="🔮 Wisp", width=width)
        stdout.write(banner + "\n\n")
        stdout.flush()

    def print_continuation_banner(self, stdout: Any, session: dict, model: str) -> None:
        """Print session continuation banner."""
        import shutil
        
        width = min(72, shutil.get_terminal_size().columns - 4)
        
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
        
        banner = _box("\n".join(lines), title="📋 Continuing Session", width=width)
        stdout.write(banner + "\n\n")
        stdout.flush()

    # ── Thinking buffer ─────────────────────────────────────────────

    def _buffer_thinking(self, text: str) -> None:
        """Accumulate thinking text instead of rendering immediately."""
        self._thinking_buffer.append(text)

    def _flush_thinking(self, stdout: Any) -> None:
        """Render accumulated thinking as a single block."""
        if not self._thinking_buffer or self._thinking_shown:
            return
        full = "".join(self._thinking_buffer)
        self._thinking_buffer = []
        if full.strip():
            # Single-line thinking display, truncated if very long
            display = full.replace("\n", " ")
            if len(display) > 120:
                display = display[:117] + "..."
            stdout.write(f"💭 {display}\n")
            stdout.flush()
        self._thinking_shown = True

    def _reset_thinking(self) -> None:
        """Reset thinking state for a new turn."""
        self._thinking_buffer = []
        self._thinking_shown = False

    # ── CLI-specific methods ──────────────────────────────────────

    async def run(self, stdin: Any, stdout: Any, session_id: str, model: str, workspace: str) -> None:
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

                self._reset_thinking()
                try:
                    async for event in self.runtime.run_turn(session, prompt):
                        self._render_event(stdout, event)
                    self._flush_thinking(stdout)
                except Exception as exc:
                    logger.exception("Error during turn")
                    stdout.write(f"Error: {exc}\n")
                    stdout.flush()
        finally:
            stop_event.set()

    def _render_event(self, stdout: Any, event: dict) -> None:
        """Render an event to stdout.

        Thinking events are buffered and rendered as a single block
        when content arrives or at turn end.
        """
        event_type = event.get("type")
        if event_type == "thinking":
            self._buffer_thinking(event.get("text", ""))
        elif event_type == "content":
            self._flush_thinking(stdout)
            stdout.write(event.get("text", ""))
        elif event_type == "complete":
            self._flush_thinking(stdout)
            stdout.write("\n")
        elif event_type == "done":
            self._flush_thinking(stdout)
            stdout.write("\n")
        elif event_type == "error":
            self._flush_thinking(stdout)
            stdout.write(f"Error: {event.get('message', '')}\n")
        elif event_type == "tool_call":
            self._flush_thinking(stdout)
            stdout.write(f"[Tool: {event.get('name', '')}]\n")
        elif event_type == "tool_result":
            self._flush_thinking(stdout)
            self._render_tool_result(stdout, event)
        # checkpoint events are internal — don't render
        stdout.flush()

    def _render_tool_result(self, stdout: Any, event: dict) -> None:
        """Render a tool_result event, with special handling for edits and writes."""
        name = event.get("name", "")
        result = event.get("result", "")

        # Try to parse structured result dict
        parsed = None
        if isinstance(result, dict):
            parsed = result
        elif isinstance(result, str):
            try:
                import json
                parsed = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                pass

        if name in ("edit_file", "edit_file_multi") and parsed:
            self._render_edit_result(stdout, parsed)
        elif name == "write_file" and parsed:
            self._render_write_result(stdout, parsed)
        else:
            # Default: show result summary
            text = result if isinstance(result, str) else str(result)
            # Truncate very long results
            if len(text) > 500:
                text = text[:497] + "..."
            stdout.write(f"[Result: {text}]\n")

    def _render_edit_result(self, stdout: Any, parsed: dict) -> None:
        """Render an edit_file result with diff."""
        data = parsed.get("data", "")
        meta = parsed.get("metadata", {})
        diff = meta.get("diff", "")
        path = meta.get("path", "unknown")

        stdout.write(f"✏️  {data}\n")
        if diff:
            stdout.write("---\n")
            for line in diff.split("\n")[:20]:  # Show first 20 lines
                if line.startswith("+"):
                    stdout.write(f"\033[32m{line}\033[0m\n")  # Green for additions
                elif line.startswith("-"):
                    stdout.write(f"\033[31m{line}\033[0m\n")  # Red for deletions
                elif line.startswith("@@"):
                    stdout.write(f"\033[36m{line}\033[0m\n")  # Cyan for hunk header
                else:
                    stdout.write(f"{line}\n")
            if len(diff.split("\n")) > 20:
                stdout.write("... (truncated)\n")
            stdout.write("---\n")

    def _render_write_result(self, stdout: Any, parsed: dict) -> None:
        """Render a write_file result with diff."""
        data = parsed.get("data", "")
        meta = parsed.get("metadata", {})
        diff = meta.get("diff", "")
        path = meta.get("path", "unknown")

        stdout.write(f"📝 {data}\n")
        if diff:
            stdout.write("---\n")
            for line in diff.split("\n")[:20]:
                if line.startswith("+"):
                    stdout.write(f"\033[32m{line}\033[0m\n")
                elif line.startswith("-"):
                    stdout.write(f"\033[31m{line}\033[0m\n")
                else:
                    stdout.write(f"{line}\n")
            if len(diff.split("\n")) > 20:
                stdout.write("... (truncated)\n")
            stdout.write("---\n")
