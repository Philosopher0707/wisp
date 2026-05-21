"""Adapter layer — bridges old entry points to the new CompositionRoot system.

Allows gradual migration without breaking existing interfaces:
  - Old config objects -> new CompositionRoot
  - Old session API -> new UnifiedStore
  - Old tool API -> new ExtensionHost
  - Old security API -> new SecurityPolicy

Usage:
    from wisp.adapters import create_runtime
    runtime = create_runtime(old_config)
    # Use runtime just like the old session manager
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from wisp.infra.security import PermissionMode
from wisp.infra.store import UnifiedStore

# Re-export legacy types for compatibility during migration
# If old modules are deleted, provide stubs to prevent import errors
try:
    from wisp.session_store import Run, UnifiedSessionStore
except ImportError:
    @dataclass
    class Run:  # type: ignore
        id: str = ""
        session_id: str = ""
        prompt: str = ""
        status: str = "pending"
        created_at: str = ""

    class UnifiedSessionStore:  # type: ignore
        """Backward-compatible wrapper around UnifiedStore."""

        def __init__(self, sessions_dir: str | Path | None = None, db_path: str | Path | None = None):
            if db_path is not None:
                self._store = UnifiedStore(db_path)
            elif sessions_dir is not None:
                self._store = UnifiedStore(Path(sessions_dir) / "wisp.db")
            else:
                self._store = UnifiedStore()

        def create_session(self, session_id: str | None = None, model: str = "", workspace: str = "", title: str = "") -> Session:
            if session_id is None:
                import uuid
                session_id = f"sess-{uuid.uuid4().hex[:12]}"
            data = self._store.create_session(session_id, model, workspace, title)
            return Session.from_dict(data)

        def save_session(self, session: dict) -> None:
            """Backward-compatible save that accepts dict."""
            self._store.save_session(session)

        def save(self, session: Session) -> None:
            """Backward-compatible save that accepts Session object."""
            self._store.save_session(session.to_dict())

        def load_session(self, session_id: str) -> Session | None:
            data = self._store.load_session(session_id)
            if data is None:
                return None
            return Session.from_dict(data)

        def get_session_id_from_fragment(self, fragment: str) -> str | None:
            """Find session ID matching a fragment."""
            sessions = self._store.list_sessions()
            for s in sessions:
                if fragment.lower() in s.get("id", "").lower():
                    return s["id"]
            return None

        def list_sessions(self, limit: int = 50) -> list[dict]:
            sessions = self._store.list_sessions(limit)
            for s in sessions:
                if "title" not in s:
                    s["title"] = ""
            return sessions

        def delete_session(self, session_id: str) -> bool:
            return self._store.delete_session(session_id)

        def create_run(self, session_id: str, prompt: str, model: str = "unknown") -> Run:
            import uuid
            run_id = f"run-{uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc).isoformat()
            run = {
                "id": run_id,
                "session_id": session_id,
                "prompt": prompt,
                "model": model,
                "status": "pending",
                "events": [],
                "created_at": now,
            }
            self._store.save_run(run)
            return Run(
                id=run_id,
                session_id=session_id,
                prompt=prompt,
                status="pending",
                created_at=now,
            )

        def save_run(self, run: dict) -> None:
            self._store.save_run(run)

        def load_run(self, run_id: str) -> dict | None:
            return self._store.load_run(run_id)

        def list_runs(self, session_id: str | None = None) -> list[dict]:
            return self._store.list_runs(session_id)

        def add_event(self, run_id: str, event_type: str, data: dict) -> None:
            self._store.add_event(run_id, event_type, data)

        def get_events(self, run_id: str) -> list[dict]:
            return self._store.get_events(run_id)

        def update_run_status(self, run_id: str, status: str) -> None:
            run = self._store.load_run(run_id)
            if run is not None:
                run["status"] = status
                self._store.save_run(run)

        def get_run(self, run_id: str) -> Run | None:
            data = self._store.load_run(run_id)
            if data is None:
                return None
            return Run(
                id=data.get("id", ""),
                session_id=data.get("session_id", ""),
                prompt=data.get("prompt", ""),
                status=data.get("status", "pending"),
                created_at=data.get("created_at", ""),
            )


# ── Hooks stubs ────────────────────────────────────────────────────

class HookEvent:  # type: ignore
    """Stub for backward compatibility."""
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    BASH_COMMAND = "bash_command"
    FILE_WRITE = "file_write"
    SESSION_START = "session_start"
    SESSION_END = "session_end"

    def __init__(self, event_type: str, **kwargs):
        self.event_type = event_type
        self.__dict__.update(kwargs)


@dataclass
class HookConfig:  # type: ignore
    """Stub for backward compatibility."""
    event: str = ""
    script: str = ""
    timeout: float = 5.0


class HookResult:  # type: ignore
    """Stub for backward compatibility."""
    ALLOW = "allow"
    BLOCK = "block"
    WARN = "warn"

    def __init__(self, decision: str = "allow", reason: str = "", modified_args: dict | None = None):
        self.decision = decision
        self.reason = reason
        self.modified_args = modified_args or {}


class HookManager:  # type: ignore
    """Stub for backward compatibility."""

    def __init__(self, config_dir: str | None = None):
        self.config_dir = config_dir
        self.hooks: list[HookConfig] = []

    def load_hooks(self) -> None:
        pass

    def run_hooks(self, event: HookEvent) -> HookResult:
        return HookResult(decision="allow")

    def register(self, hook: HookConfig) -> None:
        self.hooks.append(hook)


def build_hook_context(**kwargs) -> dict:
    """Stub for backward compatibility."""
    return kwargs


# ── Plugin registry stubs ────────────────────────────────────────

_plugin_tools: dict[str, Any] = {}
_plugin_schemas: list[dict] = []


def register_tool(name: str, impl: Any, schema: dict | None = None, description: str = "") -> None:
    """Stub for backward compatibility."""
    _plugin_tools[name] = {"impl": impl, "schema": schema, "description": description}
    if schema:
        _plugin_schemas.append(schema)


def list_plugin_tools() -> list[str]:
    """Stub for backward compatibility."""
    return list(_plugin_tools.keys())


def unregister_tool(name: str) -> None:
    """Stub for backward compatibility."""
    _plugin_tools.pop(name, None)


def get_plugin_schemas() -> list[dict]:
    """Stub for backward compatibility."""
    return list(_plugin_schemas)


def has_plugin_tool(name: str) -> bool:
    """Stub for backward compatibility."""
    return name in _plugin_tools


def execute_plugin_tool(name: str, **kwargs) -> Any:
    """Stub for backward compatibility."""
    tool = _plugin_tools.get(name)
    if tool:
        return tool["impl"](**kwargs)
    raise ValueError(f"Plugin tool '{name}' not found")

logger = logging.getLogger(__name__)

# Singleton store cache for get_store() compatibility
_store_cache: dict[str, UnifiedStore] = {}


def get_store(db_path: str | None = None) -> UnifiedSessionStore:
    """Get or create a UnifiedSessionStore instance.

    Backward-compatible replacement for wisp.session_store.get_store().
    """
    if db_path is None:
        db_path = str(Path.home() / ".config" / "wisp" / "wisp.db")

    if db_path not in _store_cache:
        _store_cache[db_path] = UnifiedSessionStore(db_path=db_path)
    return _store_cache[db_path]


def format_session_preview(session) -> str:
    """Format a session for display in a list.

    Backward-compatible replacement for wisp.session.format_session_preview().
    Accepts both dict and Session objects.
    """
    if hasattr(session, "to_dict"):
        session = session.to_dict()
    sid = session.get("id", "unknown")
    title = session.get("title", "")
    updated = session.get("updated_at", "")
    model = session.get("model", "")
    msg_count = len(session.get("messages", []))

    parts = [sid]
    if title:
        parts.append(f"'{title}'")
    if updated:
        parts.append(f"updated {updated[:19]}")
    if model:
        parts.append(f"model={model}")
    parts.append(f"{msg_count} messages")

    return " | ".join(parts)


@dataclass
class Session:
    """Backward-compatible Session dataclass.

    Replaces wisp.session.Session with a UnifiedStore-compatible version.
    """

    id: str
    created_at: str
    updated_at: str
    model: str
    workspace: str
    messages: list[dict] = field(default_factory=list)
    title: str = ""
    compaction_history: list[dict] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)

    @classmethod
    def create(cls, model: str, workspace: str, first_prompt: str) -> Session:
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
        """Serialize to dictionary for UnifiedStore."""
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "model": self.model,
            "workspace": self.workspace,
            "messages": self.messages,
            "title": self.title,
            "compaction_history": self.compaction_history,
            "task_ids": self.task_ids,
        }

    def touch(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = _now_iso()

    def compact(self, keep_recent: int = 4, max_context_tokens: int = 4096) -> None:
        """Compact session messages, keeping recent ones."""
        if len(self.messages) <= keep_recent:
            return
        # Try semantic compressor if available
        try:
            from wisp.semantic_compressor import SemanticCompressor
            compressor = SemanticCompressor()
            result = compressor.compress(
                messages=self.messages,
                keep_recent=keep_recent,
                max_context_tokens=max_context_tokens,
            )
            self.messages = result.messages
            self.compaction_history.append({
                "before_count": result.compression_stats.get("before_messages", len(self.messages)),
                "after_count": len(self.messages),
                "timestamp": _now_iso(),
                "method": "semantic",
            })
            return
        except Exception:
            pass  # Fall back to simple compaction

        old_count = len(self.messages)
        to_summarize = self.messages[:-keep_recent]
        kept = self.messages[-keep_recent:]
        summary = f"[Compacted {len(to_summarize)} messages]"
        self.messages = [{"role": "system", "content": summary}] + kept
        self.compaction_history.append({
            "before_count": old_count,
            "after_count": len(self.messages),
            "timestamp": _now_iso(),
        })

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        """Deserialize from dictionary."""
        return cls(
            id=data.get("id", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            model=data.get("model", ""),
            workspace=data.get("workspace", ""),
            messages=data.get("messages", []),
            title=data.get("title", ""),
            compaction_history=data.get("compaction_history", []),
            task_ids=data.get("task_ids", []),
        )


def _slugify(text: str, max_len: int = 40) -> str:
    """Turn free text into a URL-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-")


def _timestamp_id() -> str:
    """Generate a sortable session ID."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%d-%H%M%S-%f")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_runtime(config: Any) -> Any:
    """Create an AgentRuntime from an old-style config object.

    Accepts any object with attributes:
      - model: str
      - workspace: str
      - permission_mode: str ("full", "read_only", "ask")
      - db_path: str
    """
    # Normalize config
    db_path = getattr(config, "db_path", "")
    if not db_path:
        db_path = Path.home() / ".config" / "wisp" / "wisp.db"
    else:
        db_path = Path(db_path)

    permission_mode_str = getattr(config, "permission_mode", "full").lower()
    try:
        permission_mode = PermissionMode(permission_mode_str)
    except ValueError:
        permission_mode = PermissionMode.FULL
        logger.warning(
            "Unknown permission mode '%s', defaulting to FULL",
            permission_mode_str,
        )

    # Create a new-style config dataclass
    @dataclass
    class _NewConfig:
        db_path: Path
        permission_mode: PermissionMode
        model: str

    new_config = _NewConfig(
        db_path=db_path,
        permission_mode=permission_mode,
        model=getattr(config, "model", "qwen2.5-coder"),
    )

    from wisp.composition import CompositionRoot
    root = CompositionRoot(new_config)
    return root.runtime
