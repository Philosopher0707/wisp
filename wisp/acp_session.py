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
    Message,
    SessionInfo,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultContent,
)
from wisp.agent import WispAgent
from wisp.config import WispConfig
from wisp.tools import execute_tool, ToolError

logger = logging.getLogger(__name__)


class AcpSession:
    """Wraps a WispAgent for use inside an ACP session."""

    def __init__(self, session_id: str, workspace: str, config: WispConfig):
        self.session_id = session_id
        self.workspace = workspace
        self.config = config
        self.agent: Optional[WispAgent] = None  # Lazy init
        self.messages: list[dict] = []
        self.title: str = ""
        self.created_at = _now_iso()
        self.updated_at = self.created_at
        self.mode: str = "default"
        self._pending_tool_calls: dict[str, ToolCallContent] = {}
        self._waiting_for_tool_result: bool = False

    def _ensure_agent(self) -> WispAgent:
        """Lazy initialization of WispAgent."""
        if self.agent is None:
            logger.info("Lazy-init WispAgent for session %s", self.session_id)
            self.agent = WispAgent(self.config)
        return self.agent

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

        # Build system prompt
        system = self._ensure_agent()._build_system_prompt()

        # Get the last user message
        last_user_msg = self.messages[-1]
        if last_user_msg.get("role") != "user":
            return

        user_content = last_user_msg.get("content", "")

        # Add user message to agent
        self._ensure_agent()._add_message("user", user_content)

        # Run one turn with streaming — suppress stdout to avoid corrupting JSON-RPC
        captured_output = io.StringIO()
        try:
            with contextlib.redirect_stdout(captured_output):
                response = self._ensure_agent()._run_turn_streaming(system)

            if not response:
                yield TextContent(text="(No response)")
                return

            msg = response.get("message", {})
            content = msg.get("content", "") or ""
            thinking = msg.get("thinking", "") or ""

            # Yield thinking first
            if thinking:
                yield ThinkingContent(text=thinking)

            # Parse tool calls from response
            from wisp.agent import _parse_tool_call
            tool_calls = _parse_tool_call(response)

            if tool_calls:
                # Yield text content if any
                if content:
                    yield TextContent(text=content)

                # Yield tool calls
                for tc in tool_calls:
                    func = tc.get("function", {})
                    tool_call = ToolCallContent(
                        id=tc.get("id", str(uuid.uuid4())[:8]),
                        name=func.get("name", ""),
                        arguments=func.get("arguments", {}),
                    )
                    self._pending_tool_calls[tool_call.id] = tool_call
                    self._waiting_for_tool_result = True
                    yield tool_call
            else:
                # Just text response
                if content:
                    yield TextContent(text=content)
                self._ensure_agent()._add_message("assistant", content, thinking)

        except Exception as e:
            logger.exception("Error in agent turn")
            yield TextContent(text=f"Error: {e}")

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


class AcpSessionManager:
    """Manages multiple ACP sessions."""

    def __init__(self):
        self.sessions: dict[str, AcpSession] = {}

    def create(self, workspace: str, config: WispConfig, title: str = "") -> AcpSession:
        session_id = f"wisp-{uuid.uuid4().hex[:12]}"
        session = AcpSession(session_id, workspace, config)
        session.title = title or "Wisp Session"
        self.sessions[session_id] = session
        logger.info("Created ACP session %s", session_id)
        return session

    def get(self, session_id: str) -> Optional[AcpSession]:
        return self.sessions.get(session_id)

    def list(self) -> list[SessionInfo]:
        return [s.to_info() for s in self.sessions.values()]

    def load(self, session_id: str) -> Optional[AcpSession]:
        # For now, just return from memory
        # TODO: Load from disk via SessionManager
        return self.sessions.get(session_id)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
