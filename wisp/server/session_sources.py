"""Read-only access to the other session databases on this machine.

Wisp keeps one database per workspace, so a session made from the terminal in one directory is invisible to an app whose
workspace is another. This module lists and loads sessions from the known other stores WITHOUT writing to them: each is
opened `mode=ro`, so no migration, no WAL checkpoint and no lock is taken from a Wisp process that is using it. Getting a
foreign session into the app's own store is an explicit copy (`import_session`); the source is never modified.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# An untitled session is labelled with the start of its first message, so the list shows something a person can recognise.
# json_type guards the multimodal case (content is a list), which json_extract would return as raw JSON text.
LIST_COLUMNS = (
    "id, model, workspace, "
    "COALESCE(NULLIF(title, ''), "
    "CASE WHEN json_type(messages, '$[0].content') = 'text' "
    "THEN substr(replace(json_extract(messages, '$[0].content'), char(10), ' '), 1, 80) END, '') AS title, "
    "created_at, updated_at, COALESCE(msg_count, 0) AS msg_count"
)


def candidate_sources(own_db: Path) -> list[tuple[str, Path]]:
    """Other stores that exist and are not the app's own file, in priority order."""
    home = Path.home()
    wanted = [("global", home / ".config" / "wisp" / "wisp.db"), ("home", home / ".wisp" / "wisp.db")]
    own = _resolve(own_db)
    seen = {own}
    out: list[tuple[str, Path]] = []
    for label, path in wanted:
        real = _resolve(path)
        if real in seen or not real.is_file():
            continue
        seen.add(real)
        out.append((label, real))
    return out


def _resolve(path: Path) -> Path:
    try:
        return Path(path).resolve()
    except OSError:
        return Path(path)


@contextmanager
def _open_ro(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def list_foreign(own_db: Path, limit: int = 200) -> list[dict[str, Any]]:
    """Newest sessions from every other store, each tagged with its `source`. A store that cannot be read is skipped."""
    out: list[dict[str, Any]] = []
    for label, path in candidate_sources(own_db):
        try:
            with _open_ro(path) as conn:
                rows = conn.execute(
                    f"SELECT {LIST_COLUMNS} FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
                ).fetchall()
        except sqlite3.Error as exc:
            logger.warning("session source %s unreadable: %s", label, exc)
            continue
        out.extend({**dict(r), "source": label} for r in rows)
    return out


def load_foreign(own_db: Path, session_id: str) -> dict[str, Any] | None:
    """The full session from the first other store that has it, tagged with its `source`."""
    for label, path in candidate_sources(own_db):
        try:
            with _open_ro(path) as conn:
                row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        except sqlite3.Error as exc:
            logger.warning("session source %s unreadable: %s", label, exc)
            continue
        if row is None:
            continue
        try:
            messages = json.loads(row["messages"])
            compaction = json.loads(row["compaction_history"])
        except (json.JSONDecodeError, TypeError, KeyError, IndexError):
            logger.warning("session %s in %s has unreadable content", session_id, label)
            return None
        return {
            "id": row["id"],
            "model": row["model"],
            "workspace": row["workspace"],
            "title": row["title"],
            "messages": messages,
            "compaction_history": compaction,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "source": label,
        }
    return None


def import_session(store: Any, session_id: str) -> str | None:
    """Copy a session into `store` if it is not there yet. Returns the source label it came from, 'app' if it was already
    in the store, or None if no store has it. Never writes to the source."""
    if store.load_session(session_id) is not None:
        return "app"
    found = load_foreign(Path(store.db_path), session_id)
    if found is None:
        return None
    label = found.pop("source")
    store.save_session(found)
    return label
