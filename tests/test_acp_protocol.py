"""Tests for wisp.acp_protocol — ACP message types and serialization."""

import pytest

from wisp.acp_protocol import (
    AgentCapabilities,
    ClientCapabilities,
    ConfigSetRequest,
    ConfigUpdate,
    ErrorCode,
    Implementation,
    InitializeRequest,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionRequest,
    Message,
    ModeUpdate,
    NewSessionRequest,
    NewSessionResponse,
    PermissionOption,
    PermissionRequest,
    PermissionResponse,
    PromptRequest,
    SessionInfo,
    SessionInfoUpdate,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultContent,
    ToolResultRequest,
    content_block_from_dict,
    content_block_to_dict,
    make_error,
    make_notification,
    make_request,
    make_response,
)


class TestJsonRpcFactory:
    """Unit tests for JSON-RPC message factories."""

    def test_make_request(self):
        msg = make_request(1, "initialize", {"version": "1.0"})
        assert msg["jsonrpc"] == "2.0"
        assert msg["id"] == 1
        assert msg["method"] == "initialize"
        assert msg["params"]["version"] == "1.0"

    def test_make_response(self):
        msg = make_response(1, {"status": "ok"})
        assert msg["jsonrpc"] == "2.0"
        assert msg["id"] == 1
        assert msg["result"]["status"] == "ok"

    def test_make_error(self):
        msg = make_error(1, -32600, "Invalid request")
        assert msg["jsonrpc"] == "2.0"
        assert msg["id"] == 1
        assert msg["error"]["code"] == -32600
        assert msg["error"]["message"] == "Invalid request"

    def test_make_notification(self):
        msg = make_notification("session/info", {"id": "abc"})
        assert msg["jsonrpc"] == "2.0"
        assert "id" not in msg
        assert msg["method"] == "session/info"


class TestImplementation:
    """Unit tests for Implementation."""

    def test_roundtrip(self):
        impl = Implementation(name="wisp", version="1.0.0")
        d = impl.to_dict()
        restored = Implementation.from_dict(d)
        assert restored.name == "wisp"
        assert restored.version == "1.0.0"


class TestCapabilities:
    """Unit tests for capabilities."""

    def test_agent_capabilities(self):
        caps = AgentCapabilities(tools=[{"name": "read_file"}])
        d = caps.to_dict()
        assert d["tools"][0]["name"] == "read_file"
        assert d["prompts"] is True

    def test_client_capabilities(self):
        caps = ClientCapabilities.from_dict({"file_system": False, "mcp": True})
        assert caps.file_system is False
        assert caps.mcp is True


class TestSessionInfo:
    """Unit tests for SessionInfo."""

    def test_roundtrip(self):
        info = SessionInfo(id="s1", title="Test", message_count=5)
        d = info.to_dict()
        restored = SessionInfo.from_dict(d)
        assert restored.id == "s1"
        assert restored.title == "Test"
        assert restored.message_count == 5


class TestMessages:
    """Unit tests for Message types."""

    def test_message_roundtrip(self):
        msg = Message(role="user", content="hello")
        d = msg.to_dict()
        restored = Message.from_dict(d)
        assert restored.role == "user"
        assert restored.content == "hello"

    def test_prompt_request(self):
        req = PromptRequest(
            session_id="s1",
            messages=[Message(role="user", content="hi")],
        )
        d = {"session_id": "s1", "messages": [{"role": "user", "content": "hi"}]}
        restored = PromptRequest.from_dict(d)
        assert restored.session_id == "s1"
        assert len(restored.messages) == 1


class TestContentBlocks:
    """Unit tests for content blocks."""

    def test_text_content(self):
        block = TextContent(text="hello")
        d = content_block_to_dict(block)
        assert d["type"] == "text"
        assert d["text"] == "hello"

    def test_thinking_content(self):
        block = ThinkingContent(text="reasoning...")
        d = content_block_to_dict(block)
        assert d["type"] == "thinking"

    def test_tool_call_content(self):
        block = ToolCallContent(id="tc1", name="read_file", arguments={"path": "a.py"})
        d = content_block_to_dict(block)
        assert d["type"] == "tool_call"
        assert d["name"] == "read_file"

    def test_tool_result_content(self):
        block = ToolResultContent(id="tc1", content="file contents", is_error=False)
        d = content_block_to_dict(block)
        assert d["type"] == "tool_result"
        assert d["is_error"] is False

    def test_content_block_from_dict_text(self):
        block = content_block_from_dict({"type": "text", "text": "hello"})
        assert isinstance(block, TextContent)
        assert block.text == "hello"

    def test_content_block_from_dict_tool_call(self):
        block = content_block_from_dict({
            "type": "tool_call",
            "id": "tc1",
            "name": "read_file",
            "arguments": {"path": "a.py"},
        })
        assert isinstance(block, ToolCallContent)
        assert block.name == "read_file"

    def test_content_block_from_dict_unknown(self):
        block = content_block_from_dict({"type": "unknown", "text": "fallback"})
        assert isinstance(block, TextContent)


class TestRequests:
    """Unit tests for request types."""

    def test_initialize_request(self):
        d = {
            "protocol_version": "2025-03-26",
            "capabilities": {},
            "client_info": {"name": "zed", "version": "1.0"},
        }
        req = InitializeRequest.from_dict(d)
        assert req.protocol_version == "2025-03-26"
        assert req.client_info.name == "zed"

    def test_initialize_response(self):
        resp = InitializeResponse(
            protocol_version="2025-03-26",
            capabilities=AgentCapabilities(),
            agent_info=Implementation(name="wisp", version="1.0"),
        )
        d = resp.to_dict()
        assert d["protocol_version"] == "2025-03-26"
        assert d["agent_info"]["name"] == "wisp"

    def test_new_session_request(self):
        req = NewSessionRequest.from_dict({"workspace": "/tmp", "title": "Test"})
        assert req.workspace == "/tmp"
        assert req.title == "Test"

    def test_load_session_request(self):
        req = LoadSessionRequest.from_dict({"session_id": "s1"})
        assert req.session_id == "s1"

    def test_list_sessions_response(self):
        resp = ListSessionsResponse(sessions=[SessionInfo(id="s1")])
        d = resp.to_dict()
        assert len(d["sessions"]) == 1

    def test_tool_result_request(self):
        req = ToolResultRequest.from_dict({
            "session_id": "s1",
            "tool_call_id": "tc1",
            "content": [{"type": "text", "text": "result"}],
            "is_error": False,
        })
        assert req.session_id == "s1"
        assert req.is_error is False

    def test_config_set_request(self):
        req = ConfigSetRequest.from_dict({"session_id": "s1", "key": "model", "value": "gpt-4"})
        assert req.key == "model"
        assert req.value == "gpt-4"

    def test_permission_request(self):
        req = PermissionRequest(
            session_id="s1",
            tool_call_id="tc1",
            tool_name="run_bash",
            description="Run rm -rf",
            options=[PermissionOption(id="allow", label="Allow")],
        )
        d = req.to_dict()
        assert d["tool_name"] == "run_bash"
        assert len(d["options"]) == 1

    def test_permission_response(self):
        resp = PermissionResponse.from_dict({
            "session_id": "s1",
            "tool_call_id": "tc1",
            "selected_option": "allow",
        })
        assert resp.selected_option == "allow"

    def test_mode_update(self):
        update = ModeUpdate(session_id="s1", mode="diagnose")
        d = update.to_dict()
        assert d["mode"] == "diagnose"

    def test_session_info_update(self):
        update = SessionInfoUpdate(session_id="s1", info=SessionInfo(id="s1", title="T"))
        d = update.to_dict()
        assert d["info"]["title"] == "T"


class TestErrorCodes:
    """Unit tests for error codes."""

    def test_error_codes(self):
        assert ErrorCode.PARSE_ERROR == -32700
        assert ErrorCode.INVALID_REQUEST == -32600
        assert ErrorCode.METHOD_NOT_FOUND == -32601
        assert ErrorCode.INVALID_PARAMS == -32602
        assert ErrorCode.INTERNAL_ERROR == -32603
