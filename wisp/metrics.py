"""Lightweight in-agent metrics for observability.

Tracks token estimates, latency, tool success/failure, compaction,
and checkpoint counts.  No external dependency — pure in-memory counters
that survive across turns within a single session.

Usage::
    core = WispAgentCore(config)
    snapshot = core.metrics.snapshot()
    # snapshot = {"turns": 3, "total_tokens": 12000, "tool_calls": 7, ...}
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentMetrics:
    """In-memory counters for agent observability.

    Thread-safe by virtue of CPython GIL (all operations are single-dict/list
    ops).  Not designed for multi-process sharing.
    """

    # ── Turn-level ──
    turns: int = 0
    total_tokens: int = 0          # estimated chars / chars_per_token (runtime)
    prompt_tokens: int = 0         # input (sent to model) estimated tokens
    completion_tokens: int = 0     # output (received from model) estimated chars / 4
    latency_ms_total: float = 0.0

    # ── Tool-level ──
    tool_calls_total: int = 0
    tool_errors_total: int = 0
    tool_durations_ms: dict[str, list[float]] = field(default_factory=dict)

    # ── Lifecycle ──
    interruptions: int = 0         # SIGINT / Ctrl+C
    compactions: int = 0           # session.compact() calls
    checkpoints_created: int = 0   # git checkpoints
    tool_blocks: int = 0           # dangerous-command blocks
    tool_approvals: int = 0        # approval prompts answered "yes"

    def record_turn(self, latency_s: float, prompt_chars: int, completion_chars: int, chars_per_token: int = 4) -> None:
        """Record completion of one user turn."""
        self.turns += 1
        self.latency_ms_total += latency_s * 1000
        self.prompt_tokens += prompt_chars // chars_per_token
        self.completion_tokens += completion_chars // chars_per_token
        self.total_tokens = self.prompt_tokens + self.completion_tokens

    def record_tool(self, name: str, duration_ms: float, success: bool = True) -> None:
        """Record a tool execution outcome."""
        self.tool_calls_total += 1
        if not success:
            self.tool_errors_total += 1
        self.tool_durations_ms.setdefault(name, []).append(duration_ms)

    def record_tool_block(self) -> None:
        self.tool_blocks += 1

    def record_tool_approval(self, approved: bool) -> None:
        if approved:
            self.tool_approvals += 1

    def record_checkpoints(self, count: int = 1) -> None:
        self.checkpoints_created += count

    def record_compaction(self) -> None:
        self.compactions += 1

    def record_interruption(self) -> None:
        self.interruptions += 1

    def snapshot(self, chars_per_token: int = 4) -> dict[str, Any]:
        """Return a JSON-serializable dict of current counters."""
        avg_latency = self.latency_ms_total / self.turns if self.turns else 0.0
        tool_success_rate = (
            (1 - self.tool_errors_total / self.tool_calls_total) * 100
            if self.tool_calls_total else 100.0
        )
        avg_tool_dur: dict[str, float] = {}
        for name, durs in self.tool_durations_ms.items():
            avg_tool_dur[name] = sum(durs) / len(durs)

        return {
            "turns": self.turns,
            "total_tokens": self.total_tokens,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "avg_latency_ms": round(avg_latency, 1),
            "total_latency_ms": round(self.latency_ms_total, 1),
            "tool_calls": self.tool_calls_total,
            "tool_errors": self.tool_errors_total,
            "tool_success_rate": round(tool_success_rate, 1),
            "avg_tool_duration_ms": avg_tool_dur,
            "interruptions": self.interruptions,
            "compactions": self.compactions,
            "checkpoints": self.checkpoints_created,
            "tool_blocks": self.tool_blocks,
            "tool_approvals": self.tool_approvals,
        }

    def reset(self) -> None:
        """Reset all counters to zero."""
        self.turns = 0
        self.total_tokens = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.latency_ms_total = 0.0
        self.tool_calls_total = 0
        self.tool_errors_total = 0
        self.tool_durations_ms.clear()
        self.interruptions = 0
        self.compactions = 0
        self.checkpoints_created = 0
        self.tool_blocks = 0
        self.tool_approvals = 0

    def __repr__(self) -> str:
        return (
            f"AgentMetrics(turns={self.turns}, tokens={self.total_tokens}, "
            f"tools={self.tool_calls_total}, errors={self.tool_errors_total}, "
            f"latency={self.latency_ms_total:.0f}ms)"
        )
