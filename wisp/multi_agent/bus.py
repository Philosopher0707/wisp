""""In-memory message bus for agent-to-agent communication.

Supports:
- Broadcast (no target_agent)
- Direct messaging (target_agent set)
- Subscriptions by event type or source agent
- History replay for late-joining agents
- Optional disk persistence for crash recovery
"""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from .protocol import AgentEvent, EventType

logger = logging.getLogger(__name__)

MAX_HISTORY = 500  # Keep last N events for replay


class MessageBus:
    """Pub/sub bus for multi-agent events.

    Agents subscribe with callbacks; the bus delivers matching events.
    All operations are thread-safe.

    If ``persist_path`` is provided, events are appended to a JSONL file
    for crash recovery. On init, existing events are loaded from the file.
    """

    def __init__(self, max_history: int = MAX_HISTORY, persist_path: Optional[Path] = None):
        self._history: deque[AgentEvent] = deque(maxlen=max_history)
        self._subscribers: list[tuple[Optional[EventType], Optional[str], Callable[[AgentEvent], None]]] = []
        self._lock = threading.RLock()
        self._persist_path = persist_path
        self._persist_lock = threading.Lock()

        # Load persisted events on init
        if persist_path:
            self._load_persisted()

    def _load_persisted(self) -> None:
        """Load events from the persistence file on startup."""
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            with open(self._persist_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        event = AgentEvent.from_dict(data)
                        self._history.append(event)
                    except Exception as e:
                        logger.debug("Skipping corrupted persisted event: %s", e)
            logger.info("Loaded %d persisted events from %s", len(self._history), self._persist_path)
        except Exception as e:
            logger.warning("Failed to load persisted events: %s", e)

    def _persist_event(self, event: AgentEvent) -> None:
        """Append a single event to the persistence file."""
        if not self._persist_path:
            return
        try:
            with self._persist_lock:
                self._persist_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._persist_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event.to_dict()) + "\n")
        except Exception as e:
            logger.warning("Failed to persist event %s: %s", event.event_id, e)

    def emit(self, event: AgentEvent) -> None:
        """Publish an event to all matching subscribers."""
        with self._lock:
            self._history.append(event)
            subs = list(self._subscribers)

        # Persist before delivery so crash doesn't lose the event
        self._persist_event(event)

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
        """Clear all history and subscribers. Also clears persistence file."""
        with self._lock:
            self._history.clear()
            self._subscribers.clear()
        if self._persist_path and self._persist_path.exists():
            try:
                self._persist_path.unlink()
            except OSError as e:
                logger.warning("Failed to clear persistence file: %s", e)

    def compact_persistence(self, max_events: int = MAX_HISTORY) -> None:
        """Rewrite persistence file keeping only the last N events."""
        if not self._persist_path:
            return
        with self._lock:
            events = list(self._history)[-max_events:]
        try:
            with self._persist_lock:
                with open(self._persist_path, "w", encoding="utf-8") as f:
                    for event in events:
                        f.write(json.dumps(event.to_dict()) + "\n")
            logger.info("Compacted persistence to %d events", len(events))
        except Exception as e:
            logger.warning("Failed to compact persistence: %s", e)
