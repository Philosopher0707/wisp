"""Tests for ollama_client.py — delta computation, cumulative and token-delta modes."""

from unittest.mock import patch, MagicMock
import pytest
from wisp.ollama_client import OllamaClient, OllamaError


class FakeConfig:
    """Minimal config object for testing."""
    ollama_url = "http://localhost:11434"
    model = "test-model"
    temperature = 0.0
    max_tokens = 4096
    max_context_tokens = 128000
    chars_per_token = 4
    auto_approve = True
    show_thinking = False


@pytest.fixture
def client():
    return OllamaClient(FakeConfig())


def _make_chunk(content="", thinking="", tool_calls=None, done=False):
    """Helper to build a mock Ollama streaming chunk."""
    msg = {}
    if content:
        msg["content"] = content
    if thinking:
        msg["thinking"] = thinking
    if tool_calls:
        msg["tool_calls"] = tool_calls
    chunk = {"message": msg}
    if done:
        chunk["done"] = True
    return chunk


class TestGenerateStreamDeltas:

    def test_cumulative_content_mode(self, client):
        """Local Ollama sends cumulative content: 'hel', 'hello', 'hello world'."""
        chunks = [
            _make_chunk(content="hel"),
            _make_chunk(content="hello"),
            _make_chunk(content="hello world"),
            _make_chunk(done=True),
        ]

        with patch.object(client, '_post_stream', return_value=chunks):
            results = list(client.generate_stream("sys", [{"role": "user", "content": "hi"}]))
            texts = [t for t, k in results]
            assert texts == ["hel", "lo", " world"]
            assert client.stream_response["message"]["content"] == "hello world"

    def test_cumulative_thinking_mode(self, client):
        chunks = [
            _make_chunk(thinking="Let"),
            _make_chunk(thinking="Let me"),
            _make_chunk(thinking="Let me think"),
            _make_chunk(content="Here's the answer."),
            _make_chunk(done=True),
        ]

        with patch.object(client, '_post_stream', return_value=chunks):
            results = list(client.generate_stream("sys", [{"role": "user", "content": "q"}]))
            thinking_texts = [t for t, k in results if k == "thinking"]
            content_texts = [t for t, k in results if k == "content"]
            assert thinking_texts == ["Let", " me", " think"]
            assert content_texts == ["Here's the answer."]
            assert client.stream_response["message"]["thinking"] == "Let me think"
            assert client.stream_response["message"]["content"] == "Here's the answer."

    def test_token_delta_content_mode(self, client):
        """Cloud models send individual tokens: 'Hel', 'lo', ' world'."""
        chunks = [
            _make_chunk(content="Hel"),
            _make_chunk(content="lo"),
            _make_chunk(content=" world"),
            _make_chunk(done=True),
        ]

        with patch.object(client, '_post_stream', return_value=chunks):
            results = list(client.generate_stream("sys", [{"role": "user", "content": "hi"}]))
            texts = [t for t, k in results]
            assert texts == ["Hel", "lo", " world"]
            assert client.stream_response["message"]["content"] == "Hello world"

    def test_token_delta_thinking_mode(self, client):
        chunks = [
            _make_chunk(thinking="Let"),
            _make_chunk(thinking=" me"),
            _make_chunk(thinking=" think"),
            _make_chunk(content="Answer"),
            _make_chunk(done=True),
        ]

        with patch.object(client, '_post_stream', return_value=chunks):
            results = list(client.generate_stream("sys", [{"role": "user", "content": "q"}]))
            thinking_texts = [t for t, k in results if k == "thinking"]
            content_texts = [t for t, k in results if k == "content"]
            assert thinking_texts == ["Let", " me", " think"]
            assert content_texts == ["Answer"]
            assert client.stream_response["message"]["thinking"] == "Let me think"
            assert client.stream_response["message"]["content"] == "Answer"

    def test_mixed_cumulative_then_token_delta(self, client):
        """Edge: server switches mode mid-stream."""
        chunks = [
            _make_chunk(content="Hel"),           # starts cumulative
            _make_chunk(content="Hello"),          # cumulative (startswith match)
            _make_chunk(content=" world"),         # token-delta (doesn't start with "Hello")
            _make_chunk(done=True),
        ]

        with patch.object(client, '_post_stream', return_value=chunks):
            results = list(client.generate_stream("sys", [{"role": "user", "content": "hi"}]))
            texts = [t for t, k in results]
            assert texts == ["Hel", "lo", " world"]
            assert client.stream_response["message"]["content"] == "Hello world"

    def test_no_text_chunks(self, client):
        chunks = [_make_chunk(tool_calls=[{"function": {"name": "read_file"}}]), _make_chunk(done=True)]

        with patch.object(client, '_post_stream', return_value=chunks):
            results = list(client.generate_stream("sys", [{"role": "user", "content": "hi"}]))
            assert results == []
            assert client.stream_response["message"]["tool_calls"] is not None

    def test_empty_messages_raises(self, client):
        with pytest.raises(ValueError, match="messages list is empty"):
            list(client.generate_stream("sys", []))

    def test_tool_calls_captured(self, client):
        tc = [{"function": {"name": "list_files", "arguments": {"path": "."}}}]
        chunks = [
            _make_chunk(content="Let me check"),
            _make_chunk(tool_calls=tc),
            _make_chunk(done=True),
        ]

        with patch.object(client, '_post_stream', return_value=chunks):
            list(client.generate_stream("sys", [{"role": "user", "content": "list"}]))
            assert client.stream_response["message"]["tool_calls"] == tc

    def test_non_dict_chunk_skipped(self, client):
        chunks = [{"not": "a message"}, _make_chunk(content="hi"), _make_chunk(done=True)]

        with patch.object(client, '_post_stream', return_value=chunks):
            results = list(client.generate_stream("sys", [{"role": "user", "content": "hi"}]))
            texts = [t for t, k in results]
            assert texts == ["hi"]


class TestCheckHealth:

    def test_health_ok(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"models": [{"name": "test-model"}]}

        with patch.object(client._session, 'get', return_value=mock_resp):
            assert client.check_health() is True

    def test_model_not_found(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"models": [{"name": "other-model"}]}

        with patch.object(client._session, 'get', return_value=mock_resp):
            assert client.check_health() is False

    def test_connection_error(self, client):
        from requests.exceptions import ConnectionError

        with patch.object(client._session, 'get', side_effect=ConnectionError()):
            assert client.check_health() is False
