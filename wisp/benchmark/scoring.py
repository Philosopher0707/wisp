"""Event-stream scoring for benchmark turns.

Pure functions over flat event dicts — the same shape
``AgentRuntime.run_turn`` yields. No I/O, no clocks of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnStats:
    """What one benchmark turn actually did, read off the event stream."""

    tool_calls: int = 0
    tool_errors: int = 0
    content_chars: int = 0
    thinking_events: int = 0
    errored: bool = False
    error_message: str = ""

    @property
    def tool_health(self) -> float:
        """Share of tool calls that did not end in an error result."""
        if self.tool_calls == 0:
            return 1.0
        return round(1.0 - self.tool_errors / self.tool_calls, 3)


def score_events(events: list[dict[str, Any]]) -> TurnStats:
    """Fold a flat event stream into TurnStats."""
    stats = TurnStats()
    for ev in events:
        etype = ev.get("type", "")
        if etype == "tool_call":
            stats.tool_calls += 1
        elif etype == "tool_result":
            if _is_error_result(ev.get("result")):
                stats.tool_errors += 1
        elif etype == "content":
            stats.content_chars += len(ev.get("text", "") or "")
        elif etype == "thinking":
            stats.thinking_events += 1
        elif etype == "error":
            stats.errored = True
            stats.error_message = str(ev.get("message", ""))[:200]
    return stats


def _is_error_result(result: Any) -> bool:
    import json

    if isinstance(result, dict):
        return result.get("status") == "error"
    if isinstance(result, str):
        if result.startswith(("Error", "[Error", "[Blocked")):
            return True
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                return parsed.get("status") == "error"
        except (json.JSONDecodeError, TypeError):
            pass
    return False


@dataclass
class ModelScorecard:
    """Aggregated outcome for one model across tasks."""

    model: str
    passed: int = 0
    failed: int = 0
    timed_out: int = 0
    total_duration_s: float = 0.0
    task_rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.timed_out

    @property
    def pass_rate(self) -> float:
        return round(self.passed / self.total, 3) if self.total else 0.0
