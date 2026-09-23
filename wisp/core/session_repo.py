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
        """Persist multiple events in a single transaction.

        Atomic on purpose: a partially-written turn body is worse than an
        absent one, because a reader cannot tell truncation from a short
        turn. Either every event in the batch lands or none does.

        NOTE on the transaction contract: `UnifiedStore.transaction()` yields
        the STORE, not the connection (see `infra/store.py:317-327`). The
        connection must therefore be taken separately; executing on the store
        itself raises AttributeError. This method previously did the latter
        and had never been called, so the defect was invisible until P0
        started using it.
        """
        if not events:
            return
        conn = self._store._get_conn()
        with self._store.transaction():
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

    # ── Journal-first reconstruction with blob fallback (migration M2) ──
    #
    # P1's remainder. `UnifiedStore.load_session` (the blob) is read by five
    # production consumers — `__main__.py`, `supervisor.py`, `sdk.py`,
    # `acp_session.py`, `server/routes/sessions.py` — while the journal was a
    # different function with a different shape. Switching them naively would
    # make old sessions reconstruct WORSE than the blob does, because
    # **pre-P0 sessions have no turn body in the log**.
    #
    # So: journal first, blob as the fallback, and a check that says which one
    # answered. The returned dict is SHAPE-COMPATIBLE with
    # `UnifiedStore.load_session`, so adoption is a one-line change per consumer
    # rather than a rewrite.

    def reconstruct(self, session_id: str) -> Optional[dict]:
        """Rebuild a session from the journal, falling back to the blob.

        Journal-first because the journal is the append-only record and carries
        everything the blob does — plus the audit records (proposals, outcomes,
        verdicts, the task graph, recovery) the blob never had.

        Falls back to the blob when the journal yields no **turn body**, which
        is the case for **every session written before P0**: the turn body was
        not journaled then, so the log holds only a user message and a terminal
        marker. Reconstructing from that alone would silently truncate a
        session's history to one message.

        Returns a dict shaped like `UnifiedStore.load_session`, or `None` when
        neither source has the session.
        """
        source = self.reconstruction_source(session_id)
        if source == "none":
            return None

        blob = self._store.load_session(session_id) or {}

        if source == "journal":
            replayed = self.load_session(session_id)
            if replayed is not None:
                return {
                    "id": replayed.session_id,
                    "model": replayed.model or blob.get("model", ""),
                    "workspace": replayed.workspace or blob.get("workspace", ""),
                    # `title` is a blob-only field; the journal never carried it.
                    "title": blob.get("title", ""),
                    "messages": list(replayed.messages),
                    "compaction_history": list(replayed.compaction_history),
                    "created_at": replayed.created_at or blob.get("created_at", 0.0),
                    "updated_at": replayed.updated_at or blob.get("updated_at", 0.0),
                    # Provenance, so a caller can tell which path answered.
                    "_source": "journal",
                }

        if not blob:
            return None
        return {**blob, "_source": "blob"}

    def reconstruction_source(self, session_id: str) -> str:
        """`journal` | `blob` | `none` — the migration check.

        A session is reconstructible from the journal only when the log holds a
        **turn body** — an assistant or tool message — not merely the user
        message and DONE marker a pre-P0 session has.

        The predicate is `any(role != "user")`, not `messages` being non-empty.
        A pre-P0 session replays to `[user]`, which IS non-empty, so the obvious
        test picks the journal and returns a session truncated to one message —
        the exact hazard this method exists to avoid. Verified the hard way: the
        first implementation used `if replayed.messages:` and failed its own
        pre-P0 test.
        """
        replayed = self.load_session(session_id)
        if replayed is not None and any(
                str(m.get("role")) != "user" for m in replayed.messages):
            return "journal"
        if self._store.load_session(session_id) is not None:
            return "blob"
        return "none"

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
