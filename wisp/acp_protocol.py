"""ACP (Agent Client Protocol) message types and serialization.

Implements the JSON-RPC 2.0 based protocol used by Zed to communicate
with external agents. All types are serializable to/from dicts for
newline-delimited JSON on stdin/stdout.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


# ── Core JSON-RPC ──────────────────────────────────────────────────────

def make_request(id: int | str, method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": params}


def make_response(id: int | str, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id, "result": result}


def make_error(id: int | str | None, code: int, message: str, data: dict | None = None) -> dict:
    err: dict = {"code": code, "message": message}
    if data:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id, "error": err}


def make_notification(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


# ── Capabilities ───────────────────────────────────────────────────────

@dataclass
class Implementation:
    name: str
    version: str

    def to_dict(self) -> dict:
        return {"name": self.name, "version": self.version}

    @classmethod
    def from_dict(cls, d: dict) -> Implementation:
        return cls(name=d.get("name", ""), version=d.get("version", ""))


@dataclass
class AgentCapabilities:
    """Capabilities that Wisp (the agent) supports."""

    tools: list[dict] = field(default_factory=list)
    prompts: bool = True
    terminals: bool = False  # Wisp doesn't manage terminals natively
    mcp: bool = True
    plans: bool = True
    permissions: bool = True
    checkpoints: bool = False  # Phase 2
    modes: list[str] = field(default_factory=lambda: ["default", "diagnose", "plan"])

    def to_dict(self) -> dict:
        return {
            "tools": self.tools,
            "prompts": self.prompts,
            "terminals": self.terminals,
            "mcp": self.mcp,
            "plans": self.plans,
            "permissions": self.permissions,
            "checkpoints": self.checkpoints,
            "modes": self.modes,
        }


@dataclass
class ClientCapabilities:
    """Capabilities that Zed (the client) supports."""

    file_system: bool = True
    terminals: bool = True
    mcp: bool = True
    permissions: bool = True
    checkpoints: bool = True

    def to_dict(self) -> dict:
        return {
            "file_system": self.file_system,
            "terminals": self.terminals,
            "mcp": self.mcp,
            "permissions": self.permissions,
            "checkpoints": self.checkpoints,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ClientCapabilities:
        return cls(
            file_system=d.get("file_system", True),
            terminals=d.get("terminals", True),
            mcp=d.get("mcp", True),
            permissions=d.get("permissions", True),
            checkpoints=d.get("checkpoints", True),
        )


# ── Initialize ─────────────────────────────────────────────────────────

@dataclass
class InitializeRequest:
    protocol_version: str
    capabilities: ClientCapabilities
    client_info: Implementation

    @classmethod
    def from_dict(cls, d: dict) -> InitializeRequest:
        return cls(
            protocol_version=d.get("protocol_version", ""),
            capabilities=ClientCapabilities.from_dict(d.get("capabilities", {})),
            client_info=Implementation.from_dict(d.get("client_info", {})),
        )


@dataclass
class InitializeResponse:
    protocol_version: str
    capabilities: AgentCapabilities
    agent_info: Implementation

    def to_dict(self) -> dict:
        return {
            "protocol_version": self.protocol_version,
            "capabilities": self.capabilities.to_dict(),
            "agent_info": self.agent_info.to_dict(),
        }


# ── Session ────────────────────────────────────────────────────────────

@dataclass
class SessionInfo:
    id: str
    title: str = ""
    created_at: str = ""
    updated_at: str = ""
    message_count: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": self.message_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SessionInfo:
        return cls(
            id=d.get("id", ""),
            title=d.get("title", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            message_count=d.get("message_count", 0),
        )


@dataclass
class NewSessionRequest:
    workspace: str = "."
    title: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> NewSessionRequest:
        return cls(workspace=d.get("workspace", "."), title=d.get("title", ""))


@dataclass
class NewSessionResponse:
    session: SessionInfo

    def to_dict(self) -> dict:
        return {"session": self.session.to_dict()}


@dataclass
class LoadSessionRequest:
    session_id: str

    @classmethod
    def from_dict(cls, d: dict) -> LoadSessionRequest:
        return cls(session_id=d.get("session_id", ""))


@dataclass
class ListSessionsResponse:
    sessions: list[SessionInfo]

    def to_dict(self) -> dict:
        return {"sessions": [s.to_dict() for s in self.sessions]}


# ── Prompt / Messages ──────────────────────────────────────────────────

@dataclass
class Message:
    role: str  # user, assistant, system
    content: str

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}

    @classmethod
    def from_dict(cls, d: dict) -> Message:
        return cls(role=d.get("role", ""), content=d.get("content", ""))


@dataclass
class PromptRequest:
    session_id: str
    messages: list[Message]
    tools: Optional[list[dict]] = None

    @classmethod
    def from_dict(cls, d: dict) -> PromptRequest:
        return cls(
            session_id=d.get("session_id", ""),
            messages=[Message.from_dict(m) for m in d.get("messages", [])],
            tools=d.get("tools"),
        )


# ── Content Blocks ─────────────────────────────────────────────────────

@dataclass
class TextContent:
    text: str

    def to_dict(self) -> dict:
        return {"type": "text", "text": self.text}


@dataclass
class ThinkingContent:
    text: str

    def to_dict(self) -> dict:
        return {"type": "thinking", "text": self.text}


@dataclass
class ToolCallContent:
    id: str
    name: str
    arguments: dict

    def to_dict(self) -> dict:
        return {
            "type": "tool_call",
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ToolCallContent:
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            arguments=d.get("arguments", {}),
        )


@dataclass
class ToolResultContent:
    id: str
    content: str
    is_error: bool = False

    def to_dict(self) -> dict:
        return {
            "type": "tool_result",
            "id": self.id,
            "content": self.content,
            "is_error": self.is_error,
        }


ContentBlock = TextContent | ThinkingContent | ToolCallContent | ToolResultContent


def content_block_to_dict(block: ContentBlock) -> dict:
    return block.to_dict()


def content_block_from_dict(d: dict) -> ContentBlock:
    t = d.get("type", "")
    if t == "text":
        return TextContent(text=d.get("text", ""))
    elif t == "thinking":
        return ThinkingContent(text=d.get("text", ""))
    elif t == "tool_call":
        return ToolCallContent.from_dict(d)
    elif t == "tool_result":
        return ToolResultContent(
            id=d.get("id", ""),
            content=d.get("content", ""),
            is_error=d.get("is_error", False),
        )
    else:
        return TextContent(text=d.get("text", ""))


# ── Prompt Response ────────────────────────────────────────────────────

@dataclass
class PromptResponse:
    content: list[ContentBlock]
    stop_reason: str = "end_turn"

    def to_dict(self) -> dict:
        return {
            "content": [c.to_dict() for c in self.content],
            "stop_reason": self.stop_reason,
        }


# ── Tool Result ────────────────────────────────────────────────────────

@dataclass
class ToolResultRequest:
    session_id: str
    tool_call_id: str
    content: list[ContentBlock]
    is_error: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> ToolResultRequest:
        return cls(
            session_id=d.get("session_id", ""),
            tool_call_id=d.get("tool_call_id", ""),
            content=[content_block_from_dict(c) for c in d.get("content", [])],
            is_error=d.get("is_error", False),
        )


# ── Permission ───────────────────────────────────────────────────────

@dataclass
class PermissionOption:
    id: str
    label: str

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label}


@dataclass
class PermissionRequest:
    session_id: str
    tool_call_id: str
    tool_name: str
    description: str
    options: list[PermissionOption]

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "description": self.description,
            "options": [o.to_dict() for o in self.options],
        }


@dataclass
class PermissionResponse:
    session_id: str
    tool_call_id: str
    selected_option: str

    @classmethod
    def from_dict(cls, d: dict) -> PermissionResponse:
        return cls(
            session_id=d.get("session_id", ""),
            tool_call_id=d.get("tool_call_id", ""),
            selected_option=d.get("selected_option", ""),
        )


# ── Config ─────────────────────────────────────────────────────────────

@dataclass
class ConfigSetRequest:
    session_id: str
    key: str
    value: str

    @classmethod
    def from_dict(cls, d: dict) -> ConfigSetRequest:
        return cls(
            session_id=d.get("session_id", ""),
            key=d.get("key", ""),
            value=d.get("value", ""),
        )


@dataclass
class ConfigUpdate:
    session_id: str
    key: str
    value: str

    def to_dict(self) -> dict:
        return {"session_id": self.session_id, "key": self.key, "value": self.value}


# ── Mode ───────────────────────────────────────────────────────────────

@dataclass
class ModeUpdate:
    session_id: str
    mode: str

    def to_dict(self) -> dict:
        return {"session_id": self.session_id, "mode": self.mode}


# ── Session Info Update ────────────────────────────────────────────────

@dataclass
class SessionInfoUpdate:
    session_id: str
    info: SessionInfo

    def to_dict(self) -> dict:
        return {"session_id": self.session_id, "info": self.info.to_dict()}


# ── Error Codes ────────────────────────────────────────────────────────

class ErrorCode:
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    SERVER_ERROR = -32000
