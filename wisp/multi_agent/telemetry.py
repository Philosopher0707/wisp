"""Per-agent telemetry ring buffers for background subagents (GH#9).

``SubagentTelemetryBuffer`` keeps an isolated, bounded event log per worker
so a monitor can attach (replay), follow (cursor poll), and detach without
pausing or killing the worker. Bounded in BOTH events and bytes — a chatty
tool loop cannot OOM the monitor. Oldest events evict first.

Kind vocabulary: started | thinking | tool_call | tool_result | progress | settled.
Only lifecycle + orchestrator TASK_* events are published today; per-tool
granularity is a runner change and stays a follow-up.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

__all__ = [
    "TeleEvent",
    "AgentSnapshot",
    "TeleKind",
    "TeleStatus",
    "mask_text",
    "SubagentTelemetryBuffer",
    "DEFAULT_MAX_EVENTS",
    "DEFAULT_MAX_BYTES",
    "MAX_EVENT_CHARS",
]

TeleKind = Literal["started", "thinking", "tool_call", "tool_result", "progress", "settled"]
TeleStatus = Literal["queued", "running", "settled-ok", "settled-fail", "cancelled"]


def mask_text(text: str) -> str:
    """Producer-boundary secret masking for telemetry payloads.

    Task text and result summaries can carry tokens/keys — mask before the
    event reaches any ring so no consumer can observe secrets. The auth
    layer must never break telemetry, hence the guarded import.
    """
    try:
        from wisp.auth.secrets import redact
    except Exception:
        return str(text)
    try:
        return redact(str(text))
    except Exception:
        return str(text)

DEFAULT_MAX_EVENTS = 500
DEFAULT_MAX_BYTES = 256_000
MAX_EVENT_CHARS = 4_000


@dataclass(frozen=True)
class TeleEvent:
    """One normalized worker event."""

    seq: int
    t: float  # monotonic seconds on the buffer-local clock
    agent_id: str
    kind: TeleKind
    text: str


@dataclass
class AgentSnapshot:
    """Live roll-up for one worker."""

    agent_id: str
    label: str = ""
    role: str = ""
    status: TeleStatus = "running"  # queued|running|settled-ok|settled-fail|cancelled
    started_at: float = field(default_factory=time.monotonic)
    ended_at: float | None = None
    tool_calls: int = 0
    events: int = 0
    bytes: int = 0

    @property
    def elapsed(self) -> float:
        """Seconds since registration (or until settlement)."""
        return (self.ended_at if self.ended_at is not None else time.monotonic()) - self.started_at


class SubagentTelemetryBuffer:
    """Thread-safe per-agent event store with attach/detach cursors."""

    def __init__(self, max_events: int = DEFAULT_MAX_EVENTS,
                 max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._max_events = max(1, max_events)
        self._max_bytes = max(1, max_bytes)
        self._seq = itertools.count()
        self._lock = threading.Lock()
        self._events: dict[str, deque[TeleEvent]] = {}
        self._agents: dict[str, AgentSnapshot] = {}

    def register(self, agent_id: str, label: str = "", role: str = "") -> AgentSnapshot:
        """Idempotently create the per-agent ring + snapshot.

        A repeated worker name (e.g. ``fanout-0`` across turns) re-registers
        only once the previous snapshot reached a terminal state; a live
        worker keeps its ring so concurrent same-name runs never clobber.
        """
        with self._lock:
            snap = self._agents.get(agent_id)
            if snap is None or snap.status in ("settled-ok", "settled-fail", "cancelled"):
                snap = AgentSnapshot(agent_id=agent_id, label=label or (snap.label if snap else ""),
                                     role=role or (snap.role if snap else ""))
                self._agents[agent_id] = snap
                self._events[agent_id] = deque()
            return snap

    def append(self, agent_id: str, kind: TeleKind, text: str) -> TeleEvent:
        """Append one event; evict oldest-first past either bound."""
        clipped = str(text)
        if len(clipped) > MAX_EVENT_CHARS:
            clipped = clipped[:MAX_EVENT_CHARS] + f"…[{len(text)} chars total]"
        event = TeleEvent(next(self._seq), time.monotonic(), agent_id, kind, clipped)
        with self._lock:
            buf = self._events.setdefault(agent_id, deque())
            buf.append(event)
            snap = self._agents.get(agent_id)
            if snap is None:
                snap = AgentSnapshot(agent_id=agent_id)
                self._agents[agent_id] = snap
            snap.events += 1
            snap.bytes += len(clipped.encode("utf-8", "replace"))
            if kind == "tool_call":
                snap.tool_calls += 1
            if kind == "settled":
                lowered = clipped.lower()
                if "cancel" in lowered:
                    snap.status = "cancelled"
                else:
                    snap.status = "settled-ok" if "ok" in lowered else "settled-fail"
                snap.ended_at = event.t
            while len(buf) > self._max_events or sum(
                len(e.text) for e in buf
            ) > self._max_bytes:
                buf.popleft()
        return event

    def snapshot(self, agent_id: str) -> AgentSnapshot | None:
        """Current roll-up, or None for an unknown worker."""
        with self._lock:
            return self._agents.get(agent_id)

    def drop(self, agent_id: str) -> None:
        """Forget a worker entirely (entry prune path)."""
        with self._lock:
            self._agents.pop(agent_id, None)
            self._events.pop(agent_id, None)

    def agents(self) -> list[str]:
        """Registered worker ids, in registration order."""
        with self._lock:
            return list(self._agents)

    def transcript(self, agent_id: str, last_n: int = 200) -> list[TeleEvent]:
        """Attach: replay recent history without pausing the worker."""
        with self._lock:
            events = list(self._events.get(agent_id, ()))
        if last_n <= 0:
            return []
        return events[-last_n:]

    def poll(self, agent_id: str, after_seq: int) -> list[TeleEvent]:
        """Live tail: every event after the caller's cursor."""
        with self._lock:
            return [e for e in self._events.get(agent_id, ()) if e.seq > after_seq]
