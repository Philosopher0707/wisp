"""Tests for wisp.acp_session — ACP session management."""

from unittest.mock import MagicMock, patch

import pytest

from wisp.acp_session import AcpSession, AcpSessionManager
from wisp.infra.store import UnifiedStore
from wisp.acp_protocol import TextContent, ToolCallContent, ToolResultContent


class TestAcpSession:
    """Unit tests for AcpSession."""

    def test_add_user_message(self):
        session = AcpSession("s1", "/tmp", MagicMock())
        session.add_user_message("hello")
        assert len(session.messages) == 1
        assert session.messages[0]["role"] == "user"
        assert session.messages[0]["content"] == "hello"

    def test_add_assistant_message(self):
        session = AcpSession("s1", "/tmp", MagicMock())
        session.add_assistant_message("hi there")
        assert len(session.messages) == 1
        assert session.messages[0]["role"] == "assistant"

    def test_add_tool_result(self):
        session = AcpSession("s1", "/tmp", MagicMock())
        session.add_tool_result("tc1", "result", is_error=False)
        assert len(session.messages) == 1
        assert session.messages[0]["role"] == "tool"
        assert session.messages[0]["tool_call_id"] == "tc1"
        assert session.messages[0]["is_error"] is False

    def test_to_info(self):
        session = AcpSession("s1", "/tmp", MagicMock())
        session.title = "Test"
        info = session.to_info()
        assert info.id == "s1"
        assert info.title == "Test"
        assert info.message_count == 0

    def test_run_turn_no_messages(self):
        session = AcpSession("s1", "/tmp", MagicMock())
        blocks = list(session.run_turn())
        assert len(blocks) == 0

    def test_run_turn_with_user_message(self):
        session = AcpSession("s1", "/tmp", MagicMock())
        session.add_user_message("hello")

        # Mock the agent's _run_turn_streaming to return a simple response
        mock_response = {
            "message": {
                "content": "Hello!",
                "thinking": "",
            }
        }
        agent = session._ensure_agent()
        agent._run_turn_streaming = MagicMock(return_value=mock_response)
        agent._build_system_prompt = MagicMock(return_value="")

        blocks = list(session.run_turn())
        assert len(blocks) == 1
        assert isinstance(blocks[0], TextContent)
        assert blocks[0].text == "Hello!"

    def test_run_turn_with_thinking(self):
        session = AcpSession("s1", "/tmp", MagicMock())
        session.add_user_message("hello")

        mock_response = {
            "message": {
                "content": "Hello!",
                "thinking": "Let me think...",
            }
        }
        agent = session._ensure_agent()
        agent._run_turn_streaming = MagicMock(return_value=mock_response)
        agent._build_system_prompt = MagicMock(return_value="")

        blocks = list(session.run_turn())
        assert len(blocks) == 2
        from wisp.acp_protocol import ThinkingContent
        assert isinstance(blocks[0], ThinkingContent)
        assert blocks[0].text == "Let me think..."

    def test_execute_tool_not_found(self):
        session = AcpSession("s1", "/tmp", MagicMock())
        result = session.execute_tool("nonexistent")
        assert isinstance(result, ToolResultContent)
        assert result.is_error is True
        assert "not found" in result.content

    def test_execute_tool_success(self):
        session = AcpSession("s1", "/tmp", MagicMock())
        session._pending_tool_calls["tc1"] = ToolCallContent(
            id="tc1", name="read_file", arguments={"path": "test.py"}
        )

        with patch("wisp.acp_session.execute_tool") as mock_exec:
            mock_exec.return_value = "file contents"
            result = session.execute_tool("tc1")

        assert isinstance(result, ToolResultContent)
        assert result.content == "file contents"
        assert result.is_error is False
        assert "tc1" not in session._pending_tool_calls

    def test_execute_tool_error(self):
        session = AcpSession("s1", "/tmp", MagicMock())
        session._pending_tool_calls["tc1"] = ToolCallContent(
            id="tc1", name="read_file", arguments={"path": "test.py"}
        )

        with patch("wisp.acp_session.execute_tool") as mock_exec:
            from wisp.tools import ToolError
            mock_exec.side_effect = ToolError("File not found")
            result = session.execute_tool("tc1")

        assert isinstance(result, ToolResultContent)
        assert result.is_error is True
        assert "File not found" in result.content


class TestAcpSessionManager:
    """Unit tests for AcpSessionManager."""

    @pytest.fixture
    def store(self, tmp_path):
        return UnifiedStore(tmp_path / "sessions" / "wisp.db")

    def test_create(self, store):
        mgr = AcpSessionManager(store=store)
        cfg = MagicMock(); cfg.model = "llama3"; session = mgr.create("/tmp", cfg, title="Test")
        assert session.session_id.startswith("wisp-")
        assert session.title == "Test"
        assert session.workspace == "/tmp"

    def test_get(self, store):
        mgr = AcpSessionManager(store=store)
        cfg = MagicMock(); cfg.model = "llama3"; session = mgr.create("/tmp", cfg)
        found = mgr.get(session.session_id)
        assert found is session

    def test_get_not_found(self, store):
        mgr = AcpSessionManager(store=store)
        assert mgr.get("nonexistent") is None

    def test_list(self, store):
        mgr = AcpSessionManager(store=store)
        cfg = MagicMock(); cfg.model = "llama3"; mgr.create("/tmp", cfg, title="S1")
        cfg = MagicMock(); cfg.model = "llama3"; mgr.create("/tmp", cfg, title="S2")
        sessions = mgr.list()
        assert len(sessions) == 2

    def test_load(self, store):
        mgr = AcpSessionManager(store=store)
        cfg = MagicMock(); cfg.model = "llama3"; session = mgr.create("/tmp", cfg)
        loaded = mgr.load(session.session_id)
        assert loaded is session
