"""In-memory message bus for agent-to-agent communication.

Supports:
- Broadcast (no target_agent)
- Direct messaging (target_agent set)
- Subscriptions by event type or source agent
- History replay for late-joining agents
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Callable, Optional

from .protocol import AgentEvent, EventType

logger = logging.getLogger(__name__)

MAX_HISTORY = 500  # Keep last N events for replay


class MessageBus:
    """Pub/sub bus for multi-agent events.

    Agents subscribe with callbacks; the bus delivers matching events.
    All operations are thread-safe.
    """

    def __init__(self, max_history: int = MAX_HISTORY):
        self._history: deque[AgentEvent] = deque(maxlen=max_history)
        self._subscribers: list[tuple[Optional[EventType], Optional[str], Callable[[AgentEvent], None]]] = []
        self._lock = threading.RLock()

    def emit(self, event: AgentEvent) -> None:
        """Publish an event to all matching subscribers."""
        with self._lock:
            self._history.append(event)
            subs = list(self._subscribers)

        delivered = 0
        for event_type_filter, agent_filter, callback in subs:
            # Filter by event type
            if event_type_filter is not None and event.event_type != event_type_filter:
                continue
            # Filter by target agent (direct message) or source agent
            if agent_filter is not None and event.target_agent != agent_filter and event.source_agent != agent_filter:
                continue
            # Direct messages: only deliver if target matches or it's a broadcast
            if event.target_agent is not None and event.target_agent != agent_filter and agent_filter is not None:
                continue

            try:
                callback(event)
                delivered += 1
            except Exception as e:
                logger.warning("Subscriber callback failed for event %s: %s", event.event_id, e)

        logger.debug(
            "Emitted %s from %s to %d subscribers",
            event.event_type.name,
            event.source_agent,
            delivered,
        )

    def subscribe(
        self,
        callback: Callable[[AgentEvent], None],
        event_type: Optional[EventType] = None,
        agent_id: Optional[str] = None,
    ) -> Callable[[], None]:
        """Subscribe to events. Returns an unsubscribe function.

        Args:
            callback: Function called with each matching AgentEvent.
            event_type: Only emit events of this type (None = all).
            agent_id: Only emit events targeting or from this agent (None = all).
        """
        with self._lock:
            entry = (event_type, agent_id, callback)
            self._subscribers.append(entry)

        def unsubscribe():
            with self._lock:
                if entry in self._subscribers:
                    self._subscribers.remove(entry)

        return unsubscribe

    def history(self, limit: int = 100) -> list[AgentEvent]:
        """Return the last N events (newest first)."""
        with self._lock:
            return list(self._history)[-limit:]

    def history_for_agent(self, agent_id: str, limit: int = 100) -> list[AgentEvent]:
        """Return events relevant to a specific agent (targeted at them or broadcasts)."""
        with self._lock:
            return [
                e for e in self._history
                if e.target_agent == agent_id or e.target_agent is None
            ][-limit:]

    def clear(self) -> None:
        with self._lock:
            self._history.clear()
            self._subscribers.clear()
