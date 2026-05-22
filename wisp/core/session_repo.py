"""SessionRepository — persists and replays session events.

Append-only event storage in SQLite. Session state is reconstructed
by replaying events in sequence order.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from wisp.core.session import Session, SessionEvent, SessionEventType

logger = logging.getLogger(__name__)


class SessionRepository:
    """Persist session events and replay to reconstruct sessions."""

    def __init__(self, store):
        self._store = store

    # ── Write ───────────────────────────────────────────────────────

    def append_event(self, session_id: str, event: SessionEvent) -> None:
        """Persist a single session event immediately (not batched)."""
        conn = self._store._get_conn()
        conn.execute(
            """INSERT INTO session_events (session_id, sequence_num, event_type, payload, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                session_id,
                event.sequence_num,
                str(event.event_type),
                json.dumps(event.payload, default=str),
                event.timestamp,
            ),
        )

    def append_events(self, session_id: str, events: list[SessionEvent]) -> None:
        """Persist multiple events in a single transaction."""
        conn = self._store._get_conn()
        with self._store.transaction() as conn:
            for ev in events:
                conn.execute(
                    """INSERT INTO session_events (session_id, sequence_num, event_type, payload, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        ev.sequence_num,
                        str(ev.event_type),
                        json.dumps(ev.payload, default=str),
                        ev.timestamp,
                    ),
                )

    # ── Read ────────────────────────────────────────────────────────

    def load_events(self, session_id: str, after_seq: int = -1) -> list[SessionEvent]:
        """Load all events for a session, optionally after a sequence number."""
        conn = self._store._get_conn()
        rows = conn.execute(
            """SELECT sequence_num, event_type, payload, created_at
               FROM session_events
               WHERE session_id = ? AND sequence_num > ?
               ORDER BY sequence_num ASC""",
            (session_id, after_seq),
        ).fetchall()

        events: list[SessionEvent] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                payload = {}
            events.append(SessionEvent(
                event_type=SessionEventType(row["event_type"]),
                sequence_num=row["sequence_num"],
                payload=payload,
                timestamp=row["created_at"],
            ))
        return events

    def load_session(self, session_id: str) -> Optional[Session]:
        """Replay events to reconstruct a Session."""
        events = self.load_events(session_id)
        if not events:
            return None

        session = Session(session_id=session_id)
        session.replay(events)
        return session

    def get_last_sequence(self, session_id: str) -> int:
        """Return the highest sequence_num for a session, or -1 if empty."""
        conn = self._store._get_conn()
        row = conn.execute(
            "SELECT MAX(sequence_num) AS max_seq FROM session_events WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row and row["max_seq"] is not None:
            return row["max_seq"]
        return -1

    def was_last_turn_complete(self, session_id: str) -> bool:
        """True if the last event is a DONE event (turn completed normally)."""
        conn = self._store._get_conn()
        row = conn.execute(
            """SELECT event_type FROM session_events
               WHERE session_id = ?
               ORDER BY sequence_num DESC LIMIT 1""",
            (session_id,),
        ).fetchone()
        if row is None:
            return True  # no events = clean state
        return row["event_type"] == str(SessionEventType.DONE)
