"""Progress tracking for agent turns — pure data, no I/O.

Tracks phase transitions, tool execution counts, file changes,
and turn-level statistics for CLI rendering.
"""

from __future__ import annotations

from typing import Any

import json
import time
from dataclasses import dataclass, field

from wisp.core.events import AgentEvent, EventType

# ── Phase constants ─────────────────────────────────────────────

UNDERSTAND = "understand"
PLAN = "plan"
EXECUTE = "execute"
VERIFY = "verify"

_PHASE_ORDER = {UNDERSTAND: 0, PLAN: 1, EXECUTE: 2, VERIFY: 3}

_WRITE_TOOLS = frozenset({"write_file", "edit_file", "edit_file_multi"})
_EXECUTE_TOOLS = _WRITE_TOOLS | {"run_bash"}
_VERIFY_TOOLS = frozenset({"run_tests", "lsp_diagnostics", "diagnose"})
_PLAN_TOOLS = frozenset({"plan_task"})


@dataclass
class TurnProgress:
    """Snapshot of progress within a single turn."""

    turn_number: int = 0
    phase: str = UNDERSTAND
    tools_run: int = 0
    tools_succeeded: int = 0
    tools_failed: int = 0
    files_changed: list[str] = field(default_factory=list)
    start_time: float = 0.0
    current_tool: str | None = None
    current_tool_args: dict = field(default_factory=dict)
    current_tool_start: float = 0.0


class ProgressTracker:
    """Tracks agent progress across a turn from event stream.

    Pure data — no I/O, no side effects. Callers read ``progress``
    and ``on_event`` return values to drive rendering.
    """

    def __init__(self) -> None:
        self.progress = TurnProgress()
        self._has_written: bool = False
        self._seen_files: set[str] = set()

    # ── Lifecycle ───────────────────────────────────────────────

    def start_turn(self, turn_number: int) -> None:
        """Reset state for a new turn."""
        self.progress = TurnProgress(
            turn_number=turn_number,
            start_time=time.monotonic(),
        )
        self._has_written = False
        self._seen_files.clear()

    @property
    def elapsed(self) -> float:
        """Seconds since turn started."""
        if self.progress.start_time == 0:
            return 0.0
        return time.monotonic() - self.progress.start_time

    def on_done(self) -> dict:
        """Return final stats dict for the turn."""
        p = self.progress
        return {
            "turn_number": p.turn_number,
            "phase": p.phase,
            "tools_run": p.tools_run,
            "tools_succeeded": p.tools_succeeded,
            "tools_failed": p.tools_failed,
            "files_changed": list(p.files_changed),
            "elapsed": self.elapsed,
        }

    # ── Event dispatch ──────────────────────────────────────────

    def on_event(self, event: AgentEvent) -> str | None:
        """Process an event and return new phase if it changed."""
        etype = event.type if isinstance(event.type, str) else str(event.type)

        if etype == "tool_call":
            name = event.data.get("name", "")
            args = event.data.get("arguments", {})
            self.on_tool_call(name, args)
            return self._maybe_advance_phase(name)

        if etype == "tool_result":
            name = event.data.get("name", "")
            result = event.data.get("result", "")
            duration_ms = event.data.get("duration_ms")
            self.on_tool_result(name, result, duration_ms)
            return None

        if etype == "thinking":
            text = event.data.get("text", "")
            if len(text) > 500 and self.progress.phase == UNDERSTAND:
                old = self.progress.phase
                self.progress.phase = PLAN
                if old != PLAN:
                    return PLAN
            return None

        return None

    def on_tool_call(self, name: str, args: dict | None = None) -> None:
        """Record a tool invocation with its arguments."""
        self.progress.tools_run += 1
        self.progress.current_tool = name
        self.progress.current_tool_args = args or {}
        self.progress.current_tool_start = time.monotonic()

    def on_tool_result(
        self, name: str, result: Any, duration_ms: float | None = None
    ) -> None:
        """Record tool completion and track file changes from saved args."""
        self.progress.current_tool = None
        self._classify_result(result)
        self._track_files(name, self.progress.current_tool_args)
        self.progress.current_tool_args = {}

    # ── Internal ────────────────────────────────────────────────

    def _maybe_advance_phase(self, tool_name: str) -> str | None:
        """Check if tool call triggers a phase transition."""
        current = _PHASE_ORDER.get(self.progress.phase, 0)

        if tool_name in _PLAN_TOOLS and current < _PHASE_ORDER[PLAN]:
            self.progress.phase = PLAN
            return PLAN

        if tool_name in _EXECUTE_TOOLS:
            self._has_written = True
            if current < _PHASE_ORDER[EXECUTE]:
                self.progress.phase = EXECUTE
                return EXECUTE

        if tool_name in _VERIFY_TOOLS:
            target = VERIFY if self._has_written else current
            target_idx = _PHASE_ORDER.get(target, 0)
            if current < target_idx:
                self.progress.phase = target
                return target

        return None

    def _classify_result(self, result: Any) -> None:
        """Classify tool result as success or failure."""
        is_error = False
        if isinstance(result, dict):
            is_error = result.get("status") == "error"
        elif isinstance(result, str):
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict):
                    is_error = parsed.get("status") == "error"
            except (json.JSONDecodeError, TypeError):
                is_error = result.startswith("Error") or result.startswith("[")
        # Non-string, non-dict results (bytes, int, etc.) = success

        if is_error:
            self.progress.tools_failed += 1
        else:
            self.progress.tools_succeeded += 1

    def _track_files(self, name: str, args: dict) -> None:
        """Track file paths from write/edit tools."""
        if name not in _WRITE_TOOLS:
            return
        path = args.get("path", "")
        if path and path not in self._seen_files:
            self._seen_files.add(path)
            self.progress.files_changed.append(path)
