"""Session persistence for Wisp — save, load, list, and manage conversations.

Sessions are stored as JSON files in ~/.config/wisp/sessions/.
Each session preserves the full message history, model, workspace,
and timestamps so you can pick up conversations across invocations.
"""

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from wisp.config import WISP_CONFIG_DIR

logger = logging.getLogger(__name__)

SESSIONS_DIR = WISP_CONFIG_DIR / "sessions"

# ── Helpers ──────────────────────────────────────────────────────────

def _ensure_sessions_dir() -> Path:
    """Create the sessions directory if it doesn't exist."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return SESSIONS_DIR


def _slugify(text: str, max_len: int = 40) -> str:
    """Turn free text into a URL-safe slug for session filenames."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-")


def _timestamp_id() -> str:
    """Generate a sortable session ID: YYYYMMDD-HHMMSS-ffffff-<rand>."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%d-%H%M%S-%f")


def _session_path(session_id: str) -> Path:
    """Return the file path for a given session ID."""
    return SESSIONS_DIR / f"{session_id}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Session model ────────────────────────────────────────────────────

@dataclass
class Session:
    """A single Wisp conversation session."""
    id: str
    created_at: str
    updated_at: str
    model: str
    workspace: str
    messages: list[dict] = field(default_factory=list)
    title: str = ""

    @classmethod
    def create(cls, model: str, workspace: str, first_prompt: str) -> "Session":
        """Create a new session from a first user prompt."""
        now = _now_iso()
        slug = _slugify(first_prompt)
        sid = f"{_timestamp_id()}-{slug}" if slug else _timestamp_id()
        return cls(
            id=sid,
            created_at=now,
            updated_at=now,
            model=model,
            workspace=workspace,
            messages=[],
            title=first_prompt[:60].strip(),
        )

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON storage."""
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "model": self.model,
            "workspace": self.workspace,
            "messages": self.messages,
            "title": self.title,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        """Deserialize from a dict loaded from JSON."""
        return cls(
            id=data.get("id", "unknown"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            model=data.get("model", ""),
            workspace=data.get("workspace", ""),
            messages=data.get("messages", []),
            title=data.get("title", ""),
        )

    def touch(self):
        """Update the updated_at timestamp."""
        self.updated_at = _now_iso()

    def summarize(self):
        """Generate a summary of this session's conversation."""
        from wisp.summarizer import ExtractiveSummarizer
        if not self.messages:
            return None
        summarizer = ExtractiveSummarizer()
        return summarizer.summarize(
            messages=self.messages,
            session_id=self.id,
            workspace=self.workspace,
        )


# ── Session Manager ──────────────────────────────────────────────────

class SessionManager:
    """Persists and retrieves sessions on disk."""

    def __init__(self):
        _ensure_sessions_dir()

    def save(self, session: Session):
        """Save a session to disk, creating parent dirs if needed."""
        session.touch()
        path = _session_path(session.id)
        try:
            path.write_text(
                json.dumps(session.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.debug("Saved session %s (%d messages)", session.id, len(session.messages))
        except OSError as e:
            logger.error("Failed to save session %s: %s", session.id, e)
            raise

    def load(self, session_id: str) -> Optional[Session]:
        """Load a session by ID. Returns None if not found or corrupt."""
        path = _session_path(session_id)
        if not path.exists():
            logger.warning("Session not found: %s", session_id)
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Session.from_dict(data)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Corrupt session file %s: %s", session_id, e)
            return None

    def delete(self, session_id: str) -> bool:
        """Delete a session file. Returns True if deleted."""
        path = _session_path(session_id)
        if path.exists():
            path.unlink()
            logger.info("Deleted session %s", session_id)
            return True
        logger.warning("Session not found for deletion: %s", session_id)
        return False

    def list_sessions(self, limit: int = 50) -> list[dict]:
        """List all saved sessions, newest first, with metadata (no messages).

        Returns a list of dicts with keys: id, title, model, created_at, updated_at, msg_count.
        """
        sessions = []
        if not SESSIONS_DIR.exists():
            return sessions

        for path in sorted(SESSIONS_DIR.glob("*.json"), reverse=True):
            if len(sessions) >= limit:
                break
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                sessions.append({
                    "id": data.get("id", path.stem),
                    "title": data.get("title", "")[:80],
                    "model": data.get("model", "?"),
                    "created_at": data.get("created_at", ""),
                    "updated_at": data.get("updated_at", ""),
                    "msg_count": len(data.get("messages", [])),
                    "file": str(path),
                })
            except (json.JSONDecodeError, OSError):
                continue

        return sessions

    def get_session_id_from_fragment(self, fragment: str) -> Optional[str]:
        """Resolve a partial session ID (prefix) to a full session ID.

        Useful for tab-completion-style matching: '20260430-12' matches the
        first session whose ID starts with that string.
        """
        if not SESSIONS_DIR.exists():
            return None
        best = None
        for path in SESSIONS_DIR.glob(f"{fragment}*.json"):
            if best is not None:
                logger.warning("Ambiguous session prefix '%s' matches multiple", fragment)
                return None  # ambiguous
            best = path.stem
        return best


# ── Interactive session helpers ──────────────────────────────────────

def format_session_preview(session: Session, max_messages: int = 6) -> str:
    """Return a human-readable preview of a session."""
    lines = [
        f"  Session:  {session.id}",
        f"  Title:    {session.title or '(untitled)'}",
        f"  Model:    {session.model}",
        f"  Started:  {session.created_at[:19]}",
        f"  Updated:  {session.updated_at[:19]}",
        f"  Messages: {len(session.messages)}",
    ]
    # Show last few messages as context
    recent = [m for m in session.messages if m.get("role") in ("user", "assistant")]
    if recent:
        lines.append("")
        lines.append("  Last messages:")
        for m in recent[-max_messages:]:
            role = m.get("role", "?").ljust(9)
            text = (m.get("content", "") or "")[:80].replace("\n", " ")
            lines.append(f"    [{role}] {text}")
    return "\n".join(lines)
