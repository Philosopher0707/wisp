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
    # Premature-surrender signal: did the turn execute any test runner?
    # A turn that changed code and finished without this is a surrender
    # candidate regardless of what it claims in prose.
    ran_tests: bool = False

    @property
    def tool_health(self) -> float:
        """Share of tool calls that did not end in an error result."""
        if self.tool_calls == 0:
            return 1.0
        return round(1.0 - self.tool_errors / self.tool_calls, 3)

    @property
    def surrendered(self) -> bool:
        """Finished work without ever running tests."""
        return self.tool_calls > 0 and not self.ran_tests


# Substrings matching a test-runner invocation inside run_bash commands.
TEST_RUNNER_TOKENS = (
    "pytest", "unittest", "npm test", "yarn test", "pnpm test",
    "go test", "cargo test", "make test", "ctest", "rspec",
    "jest", "vitest", "phpunit",
)


def score_events(events: list[dict[str, Any]]) -> TurnStats:
    """Fold a flat event stream into TurnStats."""
    stats = TurnStats()
    for ev in events:
        etype = ev.get("type", "")
        if etype == "tool_call":
            stats.tool_calls += 1
            if _is_test_execution(ev):
                stats.ran_tests = True
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


def _is_test_execution(ev: dict[str, Any]) -> bool:
    """True when a tool_call event executes a test runner.

    The dedicated run_tests tool always counts; run_bash counts when its
    command invokes a known runner (pytest, go test, npm test, ...).
    """
    name = str(ev.get("name", "") or "")
    if name == "run_tests":
        return True
    if name != "run_bash":
        return False
    args = ev.get("arguments") or {}
    command = args.get("command", "") if isinstance(args, dict) else ""
    lowered = str(command or "").lower()
    return any(tok in lowered for tok in TEST_RUNNER_TOKENS)


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
    surrendered: int = 0
    total_duration_s: float = 0.0
    task_rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.timed_out

    @property
    def pass_rate(self) -> float:
        return round(self.passed / self.total, 3) if self.total else 0.0
