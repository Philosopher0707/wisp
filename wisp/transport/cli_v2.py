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


class CLITransport(Transport):
    """CLI transport layer."""

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self._stdin: Any = None
        self._stdout: Any = None

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

                try:
                    async for event in self.runtime.run_turn(session, prompt):
                        self._render_event(stdout, event)
                except Exception as exc:
                    logger.exception("Error during turn")
                    stdout.write(f"Error: {exc}\n")
                    stdout.flush()
        finally:
            stop_event.set()

    def _render_event(self, stdout: Any, event: dict) -> None:
        """Render an event to stdout."""
        event_type = event.get("type")
        if event_type == "content":
            stdout.write(event.get("text", ""))
        elif event_type == "done":
            stdout.write("\n")
        elif event_type == "error":
            stdout.write(f"Error: {event.get('message', '')}\n")
        elif event_type == "tool_call":
            stdout.write(f"[Tool: {event.get('name', '')}]\n")
        elif event_type == "tool_result":
            stdout.write(f"[Result: {event.get('result', '')}]\n")
        stdout.flush()
