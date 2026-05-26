"""ACP session management — maps ACP sessions to Wisp agents."""

from __future__ import annotations

import contextlib
import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Iterator, Optional

from wisp.acp_protocol import (
    ContentBlock,
    SessionInfo,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultContent,
)
from wisp.config import WispConfig
from wisp.infra.store import UnifiedStore
from wisp.tools import execute_tool, ToolError

logger = logging.getLogger(__name__)


class AcpSession:
    """Wraps a WispAgentCore for use inside an ACP session.

    Uses CompositionRoot to get a fully-wired core with provider,
    security, extensions, and tool_executor injected.
    """

    def __init__(
        self,
        session_id: str,
        workspace: str,
        config: WispConfig,
        composition_root: Optional[object] = None,
    ):
        self.session_id = session_id
        self.workspace = workspace
        self.config = config
        self._composition_root = composition_root
        self._core: Optional[object] = None  # Lazy WispAgentCore from CompositionRoot
        self.messages: list[dict] = []
        self.title: str = ""
        self.created_at = _now_iso()
        self.updated_at = self.created_at
        self.mode: str = "default"
        self._pending_tool_calls: dict[str, ToolCallContent] = {}
        self._waiting_for_tool_result: bool = False
        self._interrupted: bool = False
        self._active_skill: Optional[str] = None

    def _ensure_core(self):
        """Lazy initialization of WispAgentCore via CompositionRoot."""
        if self._core is not None:
            return self._core

        if self._composition_root is not None:
            self._core = self._composition_root._create_core()
        else:
            # Fallback: bare core with config only (limited — no provider/tools)
            from wisp.core.engine import WispAgentCore
            self._core = WispAgentCore(config=self.config)
            logger.warning(
                "AcpSession created without CompositionRoot — "
                "agent will have no provider or tool_executor"
            )
        return self._core

    @property
    def agent(self):
        """Backward-compat: returns the underlying core.

        Code that used session.agent to access _interrupted, _active_skill,
        or client should migrate to session._ensure_core().
        """
        return self._ensure_core()

    def add_user_message(self, content: str) -> None:
        """Add a user message to the session."""
        self.messages.append({"role": "user", "content": content})
        self.updated_at = _now_iso()

    def add_assistant_message(self, content: str) -> None:
        """Add an assistant message to the session."""
        self.messages.append({"role": "assistant", "content": content})
        self.updated_at = _now_iso()

    def add_tool_result(self, tool_call_id: str, result: str, is_error: bool = False) -> None:
        """Add a tool result to the session."""
        self.messages.append({
            "role": "tool",
            "content": result,
            "tool_call_id": tool_call_id,
            "is_error": is_error,
        })
        self._waiting_for_tool_result = False
        self.updated_at = _now_iso()

    def run_turn(self) -> Iterator[ContentBlock]:
        """Run one agent turn and yield content blocks as they are produced.

        This streams:
        1. Thinking blocks (reasoning)
        2. Text blocks (assistant response)
        3. Tool call blocks (when agent wants to use a tool)
        """
        if not self.messages:
            return

        # Build session dict
        session_dict = {
            "id": self.session_id,
            "model": getattr(self.config, "model", "unknown"),
            "workspace": self.workspace,
            "messages": list(self.messages),
        }

        # Get the last user message as prompt
        last_user_msg = self.messages[-1]
        if last_user_msg.get("role") != "user":
            return

        prompt = last_user_msg.get("content", "")

        # Run one turn via the stateless core
        captured_output = io.StringIO()
        try:
            from wisp.async_utils import run_sync_coro

            with contextlib.redirect_stdout(captured_output):
                core = self._ensure_core()
                events = run_sync_coro(self._collect_events(core, session_dict, prompt))

            # Process events into content blocks
            content_parts: list[str] = []
            thinking_parts: list[str] = []
            tool_calls: list[dict] = []

            for event in events:
                etype = event.get("type", "")
                if etype == "content":
                    content_parts.append(event.get("text", ""))
                elif etype == "thinking":
                    thinking_parts.append(event.get("text", ""))
                elif etype == "tool_call":
                    tool_calls.append(event)

            if not events and not content_parts and not thinking_parts and not tool_calls:
                yield TextContent(text="(No response)")
                return

            # Yield thinking first
            if thinking_parts:
                yield ThinkingContent(text="".join(thinking_parts))

            # Yield tool calls
            if tool_calls:
                if content_parts:
                    yield TextContent(text="".join(content_parts))

                for tc in tool_calls:
                    func = tc.get("function", {})
                    if not func and "name" in tc:
                        # Already in flat format from core
                        func = {"name": tc.get("name", ""), "arguments": tc.get("arguments", {})}
                    tool_call = ToolCallContent(
                        id=tc.get("id", str(uuid.uuid4())[:8]),
                        name=func.get("name", tc.get("name", "")),
                        arguments=func.get("arguments", tc.get("arguments", {})),
                    )
                    self._pending_tool_calls[tool_call.id] = tool_call
                    self._waiting_for_tool_result = True
                    yield tool_call
            else:
                # Just text response
                text = "".join(content_parts)
                if text:
                    yield TextContent(text=text)
                self.add_assistant_message(text)

        except Exception as e:
            logger.exception("Error in agent turn")
            yield TextContent(text=f"Error: {e}")

    async def _collect_events(self, core, session_dict: dict, prompt: str) -> list[dict]:
        """Collect all events from one core.turn() call."""
        events: list[dict] = []
        async for event in core.turn(session_dict, prompt):
            events.append(event)
        return events

    def execute_tool(self, tool_call_id: str) -> ToolResultContent:
        """Execute a pending tool call and return the result."""
        tool_call = self._pending_tool_calls.pop(tool_call_id, None)
        if not tool_call:
            return ToolResultContent(
                id=tool_call_id,
                content=f"Tool call {tool_call_id} not found",
                is_error=True,
            )

        try:
            result = execute_tool(
                tool_call.name,
                tool_call.arguments,
                self.workspace,
                max_data_chars=8000,
                file_lock=getattr(self, 'file_lock', None),
            )
            return ToolResultContent(id=tool_call_id, content=result)
        except ToolError as e:
            return ToolResultContent(id=tool_call_id, content=str(e), is_error=True)
        except Exception as e:
            logger.exception("Tool execution failed")
            return ToolResultContent(
                id=tool_call_id,
                content=f"Unexpected error: {e}",
                is_error=True,
            )

    def to_info(self) -> SessionInfo:
        return SessionInfo(
            id=self.session_id,
            title=self.title or "Wisp Session",
            created_at=self.created_at,
            updated_at=self.updated_at,
            message_count=len(self.messages),
        )

    def to_session(self) -> dict:
        """Convert this ACP session to a persistent session dict."""
        return {
            "id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "model": getattr(self.config, "model", "unknown"),
            "workspace": self.workspace,
            "messages": self.messages.copy(),
            "title": self.title or "Wisp Session",
            "compaction_history": [],
        }

    @classmethod
    def from_session(cls, session: dict, config: WispConfig, composition_root=None) -> "AcpSession":
        """Restore an ACP session from a persistent session dict."""
        acp = cls(session["id"], session["workspace"], config, composition_root=composition_root)
        acp.messages = list(session.get("messages", []))
        acp.title = session.get("title", "Wisp Session")
        acp.created_at = session.get("created_at", _now_iso())
        acp.updated_at = session.get("updated_at", _now_iso())
        return acp


class AcpSessionManager:
    """Manages multiple ACP sessions with optional disk persistence."""

    def __init__(self, store: UnifiedStore | None = None, composition_root=None):
        self._store = store
        self._composition_root = composition_root
        self._active: dict[str, AcpSession] = {}

    def _get_store(self) -> UnifiedStore:
        if self._store is None:
            from pathlib import Path
            self._store = UnifiedStore(Path.home() / ".config" / "wisp" / "wisp.db")
        return self._store

    def create(self, workspace: str, config: WispConfig, title: str = "") -> AcpSession:
        session_id = f"wisp-{uuid.uuid4().hex[:12]}"
        session = AcpSession(session_id, workspace, config, composition_root=self._composition_root)
        session.title = title or "Wisp Session"
        self._active[session_id] = session

        # Persist to disk
        store = self._get_store()
        store.create_session(
            session_id=session_id,
            model=getattr(config, "model", "unknown"),
            workspace=workspace,
            title=session.title,
        )
        logger.info("Created ACP session %s", session_id)
        return session

    def get(self, session_id: str) -> Optional[AcpSession]:
        # Check active sessions first
        if session_id in self._active:
            return self._active[session_id]
        # Try loading from disk
        return self.load(session_id)

    def list(self) -> list[SessionInfo]:
        # Merge active sessions with persisted sessions
        store = self._get_store()
        persisted = {s["id"]: s for s in store.list_sessions()}

        # Add active sessions
        for s in self._active.values():
            persisted[s.session_id] = {
                "id": s.session_id,
                "title": s.title,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "msg_count": len(s.messages),
            }

        # Convert to SessionInfo
        infos = []
        for data in persisted.values():
            infos.append(SessionInfo(
                id=data["id"],
                title=data.get("title", "Wisp Session"),
                created_at=data.get("created_at", ""),
                updated_at=data.get("updated_at", ""),
                message_count=data.get("msg_count", 0),
            ))
        return sorted(infos, key=lambda i: i.updated_at, reverse=True)

    def load(self, session_id: str) -> Optional[AcpSession]:
        # Check active first
        if session_id in self._active:
            return self._active[session_id]

        # Load from disk
        store = self._get_store()
        session = store.load_session(session_id)
        if session is None:
            return None

        # Need config to restore — use default
        from wisp.config import WispConfig
        config = WispConfig()
        config = config.replace(workspace=session.workspace)
        acp = AcpSession.from_session(session, config, composition_root=self._composition_root)
        self._active[session_id] = acp
        return acp

    def save(self, session_id: str) -> bool:
        """Persist an active session to disk."""
        acp = self._active.get(session_id)
        if acp is None:
            return False
        store = self._get_store()
        store.save_session(acp.to_session())
        return True

    def delete(self, session_id: str) -> bool:
        """Delete a session from memory and disk."""
        self._active.pop(session_id, None)
        store = self._get_store()
        return store.delete_session(session_id)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()