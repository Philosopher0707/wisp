"""Tests for the OpenAI-compatible provider."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from wisp.providers.openai import OpenAIProvider


class TestOpenAIProviderInit:
    def test_init_with_config(self):
        config = MagicMock()
        config.api_key = "sk-test"
        config.api_base = "https://api.openai.com/v1"
        config.model = "gpt-4o"
        config.temperature = 0.1
        config.max_tokens = 4096
        provider = OpenAIProvider(config=config)
        assert provider.api_key == "sk-test"
        assert provider.api_base == "https://api.openai.com/v1"
        assert provider.model == "gpt-4o"
        assert provider.temperature == 0.1

    def test_init_with_explicit_args(self):
        provider = OpenAIProvider(
            base_url="https://api.groq.com/openai/v1",
            model="llama-3.3-70b-versatile",
            api_key="gsk_test",
        )
        assert provider.api_base == "https://api.groq.com/openai/v1"
        assert provider.model == "llama-3.3-70b-versatile"
        assert provider.api_key == "gsk_test"

    def test_init_strips_trailing_slash(self):
        provider = OpenAIProvider(base_url="https://api.openai.com/v1/")
        assert provider.api_base == "https://api.openai.com/v1"


class TestOpenAIProviderPayload:
    def test_build_payload_basic(self):
        provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
        payload = provider._build_payload(
            "You are a coding agent.",
            [{"role": "user", "content": "Hello"}],
            None,
            stream=True,
        )
        assert payload["model"] == "gpt-4o"
        assert payload["stream"] is True
        assert payload["temperature"] == 0.2
        assert payload["messages"][0] == {"role": "system", "content": "You are a coding agent."}
        assert payload["messages"][1] == {"role": "user", "content": "Hello"}

    def test_build_payload_with_tools(self):
        provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ]
        payload = provider._build_payload("sys", [{"role": "user", "content": "read foo.py"}], tools)
        assert "tools" in payload
        assert payload["tools"][0]["function"]["name"] == "read_file"

    def test_build_payload_normalizes_tool_messages(self):
        provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
        messages = [
            {"role": "user", "content": "test"},
            {"role": "tool", "tool_call_id": "call_123", "content": "result data"},
        ]
        payload = provider._build_payload("sys", messages, None)
        tool_msg = [m for m in payload["messages"] if m["role"] == "tool"]
        assert len(tool_msg) == 1
        assert tool_msg[0]["tool_call_id"] == "call_123"

    def test_convert_tools_already_openai_format(self):
        tools = [
            {"type": "function", "function": {"name": "test", "description": "test", "parameters": {}}},
        ]
        converted = OpenAIProvider._convert_tools(tools)
        assert converted == tools

    def test_convert_tools_wisp_format(self):
        tools = [{"name": "test", "description": "test", "parameters": {"type": "object"}}]
        converted = OpenAIProvider._convert_tools(tools)
        assert converted[0]["type"] == "function"
        assert converted[0]["function"]["name"] == "test"


class TestOpenAIProviderStreaming:
    """Test streaming event generation with mocked HTTP responses."""

    def _make_sse_lines(self, chunks: list[dict]) -> list[bytes]:
        lines = []
        for chunk in chunks:
            lines.append(f"data: {json.dumps(chunk)}".encode())
        lines.append(b"data: [DONE]")
        return lines

    def test_stream_content_events(self):
        provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
        chunks = [
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": " world"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = self._make_sse_lines(chunks)

        with patch("requests.post", return_value=mock_resp):
            events = list(provider.generate_stream_events("sys", [{"role": "user", "content": "hi"}]))

        content_events = [e for e in events if e["type"] == "content"]
        assert len(content_events) == 2
        assert content_events[0]["text"] == "Hello"
        assert content_events[1]["text"] == " world"
        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1
        assert done_events[0]["done_reason"] == "stop"

    def test_stream_tool_call_events(self):
        provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
        chunks = [
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "read_file"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"path":'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ' "foo.py"}'}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = self._make_sse_lines(chunks)

        with patch("requests.post", return_value=mock_resp):
            events = list(provider.generate_stream_events("sys", [{"role": "user", "content": "read foo.py"}]))

        tc_events = [e for e in events if e["type"] == "tool_call"]
        assert len(tc_events) == 1
        assert tc_events[0]["name"] == "read_file"
        assert tc_events[0]["arguments"] == {"path": "foo.py"}
        assert tc_events[0]["id"] == "call_1"

    def test_stream_multiple_tool_calls(self):
        provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
        chunks = [
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "read_file"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"path":"a.py"}'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 1, "id": "c2", "function": {"name": "list_files"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 1, "function": {"arguments": '{"dir":"."}'}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = self._make_sse_lines(chunks)

        with patch("requests.post", return_value=mock_resp):
            events = list(provider.generate_stream_events("sys", [{"role": "user", "content": "do both"}]))

        batch_events = [e for e in events if e["type"] == "tool_calls"]
        assert len(batch_events) == 1
        calls = batch_events[0]["calls"]
        assert len(calls) == 2
        assert calls[0]["function"]["name"] == "read_file"
        assert calls[1]["function"]["name"] == "list_files"

    def test_stream_error_on_non_200(self):
        provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        with patch("requests.post", return_value=mock_resp):
            events = list(provider.generate_stream_events("sys", [{"role": "user", "content": "hi"}]))

        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert "401" in error_events[0]["message"]

    def test_stream_connection_error(self):
        provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
        import requests as req_module

        with patch("requests.post", side_effect=req_module.exceptions.ConnectionError("refused")):
            events = list(provider.generate_stream_events("sys", [{"role": "user", "content": "hi"}]))

        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert "Connection error" in error_events[0]["message"]


class TestOpenAIProviderHealth:
    def test_health_check_healthy(self):
        provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]}

        with patch("requests.get", return_value=mock_resp):
            health = provider.health_check()

        assert health["status"] == "healthy"
        assert health["models"] == 2

    def test_health_check_unhealthy(self):
        provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
        with patch("requests.get", side_effect=Exception("network error")):
            health = provider.health_check()
        assert health["status"] == "unhealthy"

    def test_list_models(self):
        provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"id": "gpt-4o"}, {"id": "o1"}]}

        with patch("requests.get", return_value=mock_resp):
            models = provider.list_models()

        assert len(models) == 2
        assert models[0]["id"] == "gpt-4o"

    def test_get_model_info_known(self):
        provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
        info = provider.get_model_info("gpt-4o")
        assert info["context_length"] == 128000

    def test_get_model_info_unknown_defaults(self):
        provider = OpenAIProvider(model="custom-model", api_key="sk-test")
        info = provider.get_model_info("custom-model")
        assert info["context_length"] == 128000


class TestOpenAIProviderClose:
    def test_close_clears_stream_response(self):
        provider = OpenAIProvider(model="gpt-4o", api_key="sk-test")
        provider._stream_response = {"status_code": 200}
        provider.close()
        assert provider._stream_response is None
