"""Warp OSC 777 transport — enables native Warp CLI agent integration.

Usage:
    wisp --warp-mode "refactor to async"

This module provides:
  - OSC 777 event emission to stdout (Warp interprets these)
  - Approval request/response via stdin (Warp sends "approve" / "reject")
  - Rich status, banners, notifications — all without Oz

No installation needed — just emit OSC 777 sequences to stdout
and Warp will pick them up automatically, as long as your patch
is applied to Warp's source and built.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from typing import Any

from .base import Transport

logger = logging.getLogger(__name__)


def _osc_777_emit(agent: str, event_type: str, **fields: Any) -> None:
    """Emit a JSON OSC 777 event to stdout.

    Format: ESC ] 777 ; payload BEL
    Warp's PTY listener intercepts these and renders native UI:
      - session_start   → Footer chip "Wisp · In Progress"
      - permission_request → Inline banner with [Approve] [Reject]
      - stop → Footer "Wisp · Done" + desktop notification

    Args:
        agent: CLIAgent::command_prefix() value, must be "wisp"
        event_type: Event name matching Warp's CLIAgentEventType variants
        **fields: Additional event payload fields
    """
    body = {"agent": agent, "event": _map_event_name(event_type), **fields}
    # Strip None values — Warp deserializes missing fields as Option::None
    body = {k: v for k, v in body.items() if v is not None}
    payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    # OSC 777: ESC ] 777 ; payload BEL
    seq = f"\033]777;{payload}\007"
    sys.stdout.write(seq)
    sys.stdout.flush()


def _map_event_name(name: str) -> str:
    """Map internal event names to OSC 777 wire format.

    Warp expects snake_case event names:
      session_start, permission_request, permission_replied,
      prompt_submit, tool_complete, stop, idle_prompt, question_asked
    """
    return name  # Already snake_case, but centralize mapping if needed


class WarpTransport(Transport):
    """Warp-native transport via OSC 777 protocol.

    All stdout goes through OSC 777 structured events.
    All user input (approvals, answers) comes from stdin.

    Architecture:
        ┌─────────┐    OSC 777 events    ┌──────────────┐
        │  Wisp   │ ────────────────────▶  │   Warp App   │
        │ process │                      │ (PTY listener)│
        │ stdout  │                      │  renders UI   │
        └────┬────┘                      └──────────────┘
             ▲                                 │
             │    "approve" / "reject"        │
    stdin ───┘  (from Warp banner buttons)     │
                                              │
                                        [Approve][Reject]
                                        (Warp renders)

    Event flow for a typical turn:
        1. Wisp starts → session_start (footer shows "In Progress")
        2. User types prompt → prompt_submit (captures query)
        3. Wisp wants to edit file → permission_request
        4. Warp shows banner with Approve/Reject
        5. User clicks Approve → Warp sends "approve" to our stdin
        6. Wisp emits permission_replied (unblocks)
        7. Wisp finishes → stop (footer "Done", notification if unfocused)
    """

    def __init__(self, runtime: Any, agent: str = "wisp"):
        self.runtime = runtime
        self.agent = agent
        self.session_id: str | None = None
        self.cwd: str | None = None
        self.project: str | None = None

    # ── Transport ABC implementation ─────────────────────────────────

    async def send(self, event: dict) -> None:
        """Send a Wisp runtime event as an OSC 777 notification."""
        self._route_to_osc_777(event)

    async def recv(self) -> str | None:
        """Read user input from stdin.

        Returns None on EOF, "exit", or "quit".
        This is called when Wisp needs a prompt from the user
        (REPL mode) or when approval responses arrive.
        """
        try:
            line = await asyncio.to_thread(sys.stdin.readline)
        except (EOFError, OSError):
            return None
        if not line:
            return None
        prompt = line.strip()
        if prompt.lower() in ("exit", "quit"):
            return None
        return prompt

    async def approve(self, tool_call: dict) -> bool:
        """Request approval from the user via Warp's inline banner.

        Emits permission_request → Warp shows banner →
        user clicks Approve/Reject → Warp sends text to stdin →
        we read "approve" or "reject" and send permission_replied.

        Args:
            tool_call: Dict with keys like "name", "file_path", "command", "summary"

        Returns:
            True if user clicked Approve, False otherwise
        """
        file_path = tool_call.get("file_path", "")
        command = tool_call.get("command", "")
        tool_name = tool_call.get("name", "tool")
        summary = tool_call.get("summary", "")
        agent_name = tool_call.get("agent_name", "")

        # Build tool_input object that Warp renders in the banner
        tool_input: dict[str, Any] = {}
        if file_path:
            tool_input["file_path"] = file_path
        if command:
            tool_input["command"] = command

        # Build readable summary
        if not summary:
            if tool_name == "edit_file" and file_path:
                summary = f"Edit {file_path}"
            elif tool_name == "run_command" and command:
                summary = f"Run: {command[:80]}"
            elif agent_name:
                summary = f"{agent_name} wants to {tool_name}"
            else:
                summary = f"Run {tool_name}"

        # Emit request — Warp shows banner with Approve/Reject buttons
        _osc_777_emit(
            self.agent,
            "permission_request",
            session_id=self.session_id,
            cwd=self.cwd,
            project=self.project,
            tool_name=tool_name,
            tool_input=tool_input if tool_input else None,
            summary=summary,
        )

        logger.info("Waiting for user approval via Warp banner...")

        # Block until user clicks Approve or Reject (or types in terminal)
        response = await self.recv()
        if response is None:
            logger.warning("Approval channel closed — denying tool call")
            return False

        is_approved = response.lower() == "approve"

        # Inform Warp the permission is handled — banner dismisses
        _osc_777_emit(
            self.agent,
            "permission_replied",
            session_id=self.session_id,
        )

        logger.info(f"Approval result: {'approved' if is_approved else 'rejected'}")
        return is_approved

    def start(self) -> None:
        """Start the session — emit session_start to Warp.

        Called before the first turn. This tells Warp to:
          - Show footer: "Wisp · In Progress"
          - Open Ctrl-G rich input (if auto-open setting enabled)
          - Create CLIAgentSessionListener for this terminal
        """
        self.session_id = uuid.uuid4().hex[:12]
        self.cwd = os.getcwd()
        self.project = self._detect_project()

        # Tell Warp the agent session is starting
        _osc_777_emit(
            self.agent,
            "session_start",
            session_id=self.session_id,
            cwd=self.cwd,
            project=self.project,
        )

    def stop(self) -> None:
        """Called when transport shuts down.

        NOTE: We do NOT emit a stop event here because the runtime
        emits one after the last turn succeeds. If we emitted here,
        the user would see a premature "Done" before output finished.
        """
        pass

    # ── Internal: route Wisp events to OSC 777 ───────────────────────

    def _route_to_osc_777(self, event: dict) -> None:
        """Map Wisp runtime events to OSC 777 events.

        Wisp event types from runtime.run_turn():
          - content: Streaming text output
          - thinking: Internal reasoning (collapsible if we had native blocks)
          - file_edit: File modification request
          - command: Shell command to run
          - complete: Turn finished with response
          - tool_result: Tool execution result
          - error: Runtime error
          - done: End of stream
        """
        etype = event.get("type")

        if etype == "content":
            # Text content — no structural OSC event
            # Warp shows this in the terminal block as-is
            pass

        elif etype == "thinking":
            # Thinking traces — not in OSC spec, but we could emit as summary later
            pass

        elif etype == "file_edit":
            # Wisp wants to modify a file — requires approval
            _osc_777_emit(
                self.agent,
                "permission_request",
                session_id=self.session_id,
                tool_name="edit_file",
                tool_input={
                    "file_path": event.get("path", ""),
                    "command": event.get("diff_summary", "edit"),
                },
                summary=event.get("summary", "Edit file"),
            )
            # Runtime handles actual approval through approve() method

        elif etype == "command":
            # Wisp wants to run a shell command
            _osc_777_emit(
                self.agent,
                "permission_request",
                session_id=self.session_id,
                tool_name="run_command",
                tool_input={"command": event.get("command", "")},
                summary=event.get("summary", "Run shell command"),
            )

        elif etype == "complete":
            # Agent finished — show success
            _osc_777_emit(
                self.agent,
                "stop",
                query=event.get("query"),
                response=event.get("response", ""),
                summary=event.get("summary", ""),
                session_id=self.session_id,
            )

        elif etype == "tool_result":
            # Tool finished executing — show completion
            _osc_777_emit(
                self.agent,
                "tool_complete",
                session_id=self.session_id,
                tool_name=event.get("name", "tool"),
            )

        elif etype == "question":
            # Agent needs clarification from user
            _osc_777_emit(
                self.agent,
                "question_asked",
                summary=event.get("text", "Question"),
                session_id=self.session_id,
            )
            # Runtime should await recv() after this for the answer

        elif etype == "error":
            # Something went wrong
            _osc_777_emit(
                self.agent,
                "stop",
                response=f"Error: {event.get('text', 'unknown')}",
                session_id=self.session_id,
            )

        elif etype == "done":
            # End of stream — emit idle prompt for follow-up
            _osc_777_emit(
                self.agent,
                "idle_prompt",
                session_id=self.session_id,
            )

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _detect_project() -> str | None:
        """Detect project name from git repo root.

        Used for the "project" field in OSC events — shown in Warp UI.
        """
        import subprocess
        try:
            root = subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                text=True,
                capture_output=True,
                timeout=0.5,
            ).strip()
            return os.path.basename(root)
        except Exception:
            return None


# ════════════════════════════════════════════════════════════════════
# Standalone test — run in Warp to see OSC 777 events in action
# ════════════════════════════════════════════════════════════════════

def main():
    """Manual test: emit OSC 777 events so you can see them in Warp.

    Usage: cd to project root with Warp patched  & built,
    then run: python -m wisp.transport.warp
    """
    import time

    print("=== WarpTransport Manual Test ===")
    print("Run this in Warp terminal to see OSC 777 events")
    print("(Make sure your Wisp patches are applied and Warp is built)")
    print()

    transport = WarpTransport(None)

    # Step 1: Session start
    transport.start()
    print(f"1. Emitted session_start (id={transport.session_id})")
    print("   → Warp should show footer: ⚡ Wisp · In Progress")
    time.sleep(0.3)

    # Step 2: Idle prompt
    _osc_777_emit("wisp", "idle_prompt", session_id=transport.session_id)
    print("2. Emitted idle_prompt → Ctrl-G rich input should open")
    print("   Type a prompt and press Enter:")

    user_input = sys.stdin.readline().strip()

    # Step 3: Prompt submit
    _osc_777_emit(
        "wisp",
        "prompt_submit",
        query=user_input,
        session_id=transport.session_id,
    )
    print(f"3. Emitted prompt_submit: {user_input!r}")
    time.sleep(0.5)

    # Step 4: Permission request (file edit)
    _osc_777_emit(
        "wisp",
        "permission_request",
        tool_name="edit_file",
        tool_input={"file_path": "src/main.rs"},
        summary="Refactor to async/await",
        session_id=transport.session_id,
    )
    print("4. Emitted permission_request")
    print("   → Warp should show banner with [Approve] [Reject]")
    print("   Type 'approve' or 'reject' and press Enter:")

    resp = sys.stdin.readline().strip()

    _osc_777_emit(
        "wisp",
        "permission_replied",
        session_id=transport.session_id,
    )
    print(f"5. Emitted permission_replied ({resp})")
    time.sleep(0.3)

    # Step 5b: Tool complete
    _osc_777_emit(
        "wisp",
        "tool_complete",
        tool_name="edit_file",
        session_id=transport.session_id,
    )
    print("6. Emitted tool_complete (back to In Progress)")
    time.sleep(0.5)

    # Step 6: Stop (success)
    _osc_777_emit(
        "wisp",
        "stop",
        query=user_input,
        response="Done! Refactored to async/await. All 42 tests passing.",
        summary="Refactoring complete",
        session_id=transport.session_id,
    )
    print("7. Emitted stop (Warp: ✅ Wisp · Done)")
    print("   If window unfocused: 🔔 Desktop notification")
    print("=== Test complete ===")


if __name__ == "__main__":
    main()
