"""Resource budget — per-subagent resource limits for DAG execution.

Enforces token count, wall-clock time, and tool-call limits on individual
subagents. Budget exhaustion terminates the agent early with partial results.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ResourceBudget:
    """Resource limits for a single subagent execution.

    All fields are optional — None means unlimited.
    """

    max_tokens: Optional[int] = None
    max_wall_time: Optional[float] = None  # seconds
    max_tool_calls: Optional[int] = None

    _tokens_used: int = field(default=0, repr=False)
    _tool_calls: int = field(default=0, repr=False)
    _started_at: float = field(default=0.0, repr=False)
    _exhausted: bool = field(default=False, repr=False)
    _exhausted_reason: str = field(default="", repr=False)

    @classmethod
    def unlimited(cls) -> "ResourceBudget":
        """No limits."""
        return cls()

    @classmethod
    def from_config(cls, config: dict) -> "ResourceBudget":
        """Create from a config dict."""
        return cls(
            max_tokens=config.get("max_tokens"),
            max_wall_time=config.get("max_wall_time"),
            max_tool_calls=config.get("max_tool_calls"),
        )

    def start(self) -> None:
        """Begin tracking resource usage."""
        self._started_at = time.monotonic()
        self._tokens_used = 0
        self._tool_calls = 0
        self._exhausted = False
        self._exhausted_reason = ""

    def record_tokens(self, count: int) -> None:
        """Record token consumption. Returns True if budget remains."""
        self._tokens_used += count

    def record_tool_call(self) -> None:
        """Record a tool call. Returns True if budget remains."""
        self._tool_calls += 1

    def check(self) -> Optional[str]:
        """Check if budget is exhausted.

        Returns None if OK, or an error message if exhausted.
        """
        if self._exhausted:
            return self._exhausted_reason

        if self.max_tokens is not None and self._tokens_used >= self.max_tokens:
            self._exhausted = True
            self._exhausted_reason = (
                f"Token budget exhausted: {self._tokens_used}/{self.max_tokens}"
            )
            return self._exhausted_reason

        if self.max_wall_time is not None:
            elapsed = time.monotonic() - self._started_at
            if elapsed >= self.max_wall_time:
                self._exhausted = True
                self._exhausted_reason = (
                    f"Time budget exhausted: {elapsed:.1f}s/{self.max_wall_time}s"
                )
                return self._exhausted_reason

        if self.max_tool_calls is not None and self._tool_calls >= self.max_tool_calls:
            self._exhausted = True
            self._exhausted_reason = (
                f"Tool call budget exhausted: {self._tool_calls}/{self.max_tool_calls}"
            )
            return self._exhausted_reason

        return None

    @property
    def exhausted(self) -> bool:
        return self._exhausted

    @property
    def tokens_remaining(self) -> Optional[int]:
        if self.max_tokens is None:
            return None
        return max(0, self.max_tokens - self._tokens_used)

    @property
    def tool_calls_remaining(self) -> Optional[int]:
        if self.max_tool_calls is None:
            return None
        return max(0, self.max_tool_calls - self._tool_calls)

    @property
    def elapsed(self) -> float:
        if self._started_at == 0.0:
            return 0.0
        return time.monotonic() - self._started_at

    def to_dict(self) -> dict:
        return {
            "max_tokens": self.max_tokens,
            "max_wall_time": self.max_wall_time,
            "max_tool_calls": self.max_tool_calls,
            "tokens_used": self._tokens_used,
            "tool_calls": self._tool_calls,
            "elapsed": self.elapsed,
            "exhausted": self._exhausted,
            "exhausted_reason": self._exhausted_reason,
        }
