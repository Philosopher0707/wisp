"""Tests for wisp.acp_adapter — ACP adapter core."""

import json
import io
import sys
from unittest.mock import patch, MagicMock

import pytest

from wisp.acp_adapter import AcpAdapter
from wisp.acp_session import AcpSessionManager
from wisp.adapters import UnifiedSessionStore
from wisp.acp_adapter import ACP_PROTOCOL_VERSION
from wisp.acp_protocol import (
    ErrorCode,
    Implementation,
    SessionInfo,
    make_request,
    make_notification,
)


class TestAcpAdapterParsing:
    """Unit tests for message parsing."""

    def test_parse_valid_request(self):
        adapter = AcpAdapter()
        msg = adapter._parse('{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}')
        assert msg is not None
        assert msg["id"] == 1
        assert msg["method"] == "initialize"

    def test_parse_invalid_json(self):
        adapter = AcpAdapter()
        msg = adapter._parse('not json')
        assert msg is None

    def test_parse_wrong_version(self):
        adapter = AcpAdapter()
        msg = adapter._parse('{"jsonrpc": "1.0", "id": 1, "method": "test"}')
        assert msg is None


class TestAcpAdapterInitialize:
    """Unit tests for initialize handshake."""

    def test_initialize(self):
        adapter = AcpAdapter()

        with patch.object(adapter, "_send_response") as mock_send:
            adapter._handle_request({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocol_version": ACP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "client_info": {"name": "zed", "version": "1.0"},
                },
            })

            assert adapter.initialized is True
            mock_send.assert_called_once()
            req_id, result = mock_send.call_args[0]
            assert req_id == 1
            assert result["protocol_version"] == ACP_PROTOCOL_VERSION
            assert result["agent_info"]["name"] == "wisp"
            assert "capabilities" in result

    def test_initialize_sets_client_capabilities(self):
        adapter = AcpAdapter()
        adapter._handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocol_version": ACP_PROTOCOL_VERSION,
                "capabilities": {"file_system": False},
                "client_info": {"name": "zed", "version": "1.0"},
            },
        })
        assert adapter.client_capabilities is not None


class TestAcpAdapterSessionManagement:
    """Unit tests for session management."""

    def test_session_new(self):
        adapter = AcpAdapter()

        with patch.object(adapter, "_send_response") as mock_send:
            adapter._handle_request({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "session/new",
                "params": {"workspace": "/tmp", "title": "Test Session"},
            })

            mock_send.assert_called_once()
            req_id, result = mock_send.call_args[0]
            assert req_id == 2
            assert result["session"]["title"] == "Test Session"
            assert result["session"]["message_count"] == 0

    def test_session_list_empty(self, tmp_path):
        store = UnifiedSessionStore(sessions_dir=tmp_path / "sessions")
        adapter = AcpAdapter(session_mgr=AcpSessionManager(store=store))

        with patch.object(adapter, "_send_response") as mock_send:
            adapter._handle_request({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "session/list",
                "params": {},
            })

            mock_send.assert_called_once()
            req_id, result = mock_send.call_args[0]
            assert result["sessions"] == []

    def test_session_list_with_sessions(self, tmp_path):
        store = UnifiedSessionStore(sessions_dir=tmp_path / "sessions")
        adapter = AcpAdapter(session_mgr=AcpSessionManager(store=store))
        adapter.session_mgr.create("/tmp", MagicMock(model="llama3"), title="Session 1")

        with patch.object(adapter, "_send_response") as mock_send:
            adapter._handle_request({
                "jsonrpc": "2.0",
                "id": 4,
                "method": "session/list",
                "params": {},
            })

            req_id, result = mock_send.call_args[0]
            assert len(result["sessions"]) == 1
            assert result["sessions"][0]["title"] == "Session 1"

    def test_session_load_not_found(self):
        adapter = AcpAdapter()

        with patch.object(adapter, "_send_response") as mock_send:
            adapter._handle_request({
                "jsonrpc": "2.0",
                "id": 5,
                "method": "session/load",
                "params": {"session_id": "nonexistent"},
            })

            mock_send.assert_called_once()
            req_id, result = mock_send.call_args[0]
            assert "error" in result


class TestAcpAdapterPrompt:
    """Unit tests for prompt handling."""

    def test_prompt_session_not_found(self):
        adapter = AcpAdapter()

        with patch.object(adapter, "_send_response") as mock_send:
            adapter._handle_request({
                "jsonrpc": "2.0",
                "id": 6,
                "method": "prompt",
                "params": {
                    "session_id": "nonexistent",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            })

            mock_send.assert_called_once()
            req_id, result = mock_send.call_args[0]
            assert "error" in result


class TestAcpAdapterToolResult:
    """Unit tests for tool result handling."""

    def test_tool_result(self):
        adapter = AcpAdapter()
        session = adapter.session_mgr.create("/tmp", MagicMock(model="llama3"))

        with patch.object(adapter, "_send_response") as mock_send:
            adapter._handle_request({
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tool/result",
                "params": {
                    "session_id": session.session_id,
                    "tool_call_id": "tc1",
                    "content": [{"type": "text", "text": "result"}],
                    "is_error": False,
                },
            })

            mock_send.assert_called_once()
            req_id, result = mock_send.call_args[0]
            assert result["status"] == "ok"


class TestAcpAdapterConfig:
    """Unit tests for config handling."""

    def test_config_set_model(self):
        adapter = AcpAdapter()
        session = adapter.session_mgr.create("/tmp", MagicMock(model="llama3"))

        with patch.object(adapter, "_send_response"):
            adapter._handle_request({
                "jsonrpc": "2.0",
                "id": 8,
                "method": "config/set",
                "params": {
                    "session_id": session.session_id,
                    "key": "model",
                    "value": "gpt-4",
                },
            })

        assert session.config.model == "gpt-4"

    def test_config_set_auto_approve(self):
        adapter = AcpAdapter()
        session = adapter.session_mgr.create("/tmp", MagicMock(model="llama3"))

        with patch.object(adapter, "_send_response"):
            adapter._handle_request({
                "jsonrpc": "2.0",
                "id": 9,
                "method": "config/set",
                "params": {
                    "session_id": session.session_id,
                    "key": "auto_approve",
                    "value": "true",
                },
            })

        assert session.config.auto_approve is True


class TestAcpAdapterCancel:
    """Unit tests for cancel handling."""

    def test_cancel(self):
        adapter = AcpAdapter()
        session = adapter.session_mgr.create("/tmp", MagicMock(model="llama3"))
        agent = session._ensure_agent()
        agent._interrupted = False

        with patch.object(adapter, "_send_response") as mock_send:
            adapter._handle_request({
                "jsonrpc": "2.0",
                "id": 10,
                "method": "cancel",
                "params": {"session_id": session.session_id},
            })

            mock_send.assert_called_once()
            assert agent._interrupted is True


class TestAcpAdapterPermission:
    """Unit tests for permission handling."""

    def test_permission_response(self):
        adapter = AcpAdapter()
        event = MagicMock()
        adapter._pending_permissions["s1:tc1"] = event

        adapter._handle_request({
            "jsonrpc": "2.0",
            "id": 11,
            "method": "permission/response",
            "params": {
                "session_id": "s1",
                "tool_call_id": "tc1",
                "selected_option": "allow",
            },
        })

        assert adapter._permission_results["s1:tc1"] == "allow"
        event.set.assert_called_once()


class TestAcpAdapterNotifications:
    """Unit tests for notification handling."""

    def test_mode_update(self):
        adapter = AcpAdapter()
        session = adapter.session_mgr.create("/tmp", MagicMock(model="llama3"))

        adapter._handle_notification({
            "jsonrpc": "2.0",
            "method": "mode/update",
            "params": {"session_id": session.session_id, "mode": "diagnose"},
        })

        assert session.mode == "diagnose"


class TestAcpAdapterUnknownMethod:
    """Unit tests for unknown method handling."""

    def test_unknown_method(self):
        adapter = AcpAdapter()

        with patch.object(adapter, "_send_error") as mock_send:
            adapter._handle_request({
                "jsonrpc": "2.0",
                "id": 99,
                "method": "unknown/method",
                "params": {},
            })

            mock_send.assert_called_once()
            req_id, code, message = mock_send.call_args[0]
            assert code == ErrorCode.METHOD_NOT_FOUND
            assert "unknown/method" in message


class TestAcpAdapterIO:
    """Unit tests for I/O operations."""

    def test_write(self):
        adapter = AcpAdapter()
        with patch("sys.stdout") as mock_stdout:
            adapter._write({"test": "value"})
            mock_stdout.write.assert_called_once()
            written = mock_stdout.write.call_args[0][0]
            assert "test" in written
            assert "value" in written
