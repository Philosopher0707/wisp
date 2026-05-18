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

import logging
from typing import Any

logger = logging.getLogger(__name__)


class CLITransport:
    """CLI transport layer."""

    def __init__(self, runtime: Any):
        self.runtime = runtime

    async def run(self, stdin: Any, stdout: Any, session_id: str, model: str, workspace: str) -> None:
        """Run the CLI REPL loop."""
        session = await self.runtime.get_or_create_session(
            session_id=session_id,
            model=model,
            workspace=workspace,
        )

        stdout.write("Wisp ready.\n")
        stdout.flush()

        while True:
            try:
                line = stdin.readline()
            except Exception:
                break

            if not line:
                break  # EOF

            prompt = line.strip()
            if not prompt:
                continue
            if prompt.lower() in ("exit", "quit"):
                break

            try:
                async for event in self.runtime.run_turn(session, prompt):
                    self._render_event(stdout, event)
            except Exception as exc:
                logger.exception("Error during turn")
                stdout.write(f"Error: {exc}\n")
                stdout.flush()

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
