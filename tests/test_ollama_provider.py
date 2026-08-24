"""TDD for OllamaProvider.

Tests that OllamaProvider conforms to the Provider protocol
and correctly interacts with the Ollama HTTP API.
"""

from unittest.mock import Mock, patch


# ═══════════════════════════════════════════════════════════════════
# 1. Protocol conformance
# ═══════════════════════════════════════════════════════════════════

class TestProtocolConformance:
    """OllamaProvider must implement all Provider methods."""

    def test_is_provider_subclass(self):
        from wisp.providers.ollama import OllamaProvider
        from wisp.providers.protocol import Provider
        assert issubclass(OllamaProvider, Provider)

    def test_can_instantiate(self):
        from wisp.providers.ollama import OllamaProvider
        provider = OllamaProvider(base_url="http://localhost:11434", model="qwen2.5-coder")
        assert provider.base_url == "http://localhost:11434"
        assert provider.model == "qwen2.5-coder"


# ═══════════════════════════════════════════════════════════════════
# 2. generate_stream_events
# ═══════════════════════════════════════════════════════════════════

class TestGenerateStreamEvents:
    """generate_stream_events must parse Ollama streaming responses."""

    def test_yields_content_from_response(self):
        from wisp.providers.ollama import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434", model="qwen")

        # Mock the streaming response
        mock_response = Mock()
        mock_response.iter_lines.return_value = [
            b'{"message": {"content": "Hello"}}',
            b'{"message": {"content": " world"}}',
            b'{"done": true}',
        ]

        with patch.object(provider, "_stream_post", return_value=mock_response):
            events = list(provider.generate_stream_events("sys", [{"role": "user", "content": "hi"}]))

        content_events = [e for e in events if e.get("type") == "content"]
        assert len(content_events) == 2
        assert content_events[0]["text"] == "Hello"
        assert content_events[1]["text"] == " world"

    def test_yields_done_at_end(self):
        from wisp.providers.ollama import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434", model="qwen")
        mock_response = Mock()
        mock_response.iter_lines.return_value = [b'{"done": true}']

        with patch.object(provider, "_stream_post", return_value=mock_response):
            events = list(provider.generate_stream_events("sys", []))

        assert events[-1]["type"] == "done"

    def test_parses_tool_calls(self):
        from wisp.providers.ollama import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434", model="qwen")
        mock_response = Mock()
        mock_response.iter_lines.return_value = [
            b'{"message": {"tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "test.py"}}}]}}',
            b'{"done": true}',
        ]

        with patch.object(provider, "_stream_post", return_value=mock_response):
            events = list(provider.generate_stream_events("sys", []))

        tool_events = [e for e in events if e.get("type") == "tool_call"]
        assert len(tool_events) == 1
        assert tool_events[0]["name"] == "read_file"

    def test_assigns_id_to_tool_call_without_one(self):
        from wisp.providers.ollama import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434", model="qwen")
        mock_response = Mock()
        mock_response.iter_lines.return_value = [
            b'{"message": {"tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "test.py"}}}]}}',
            b'{"done": true}',
        ]

        with patch.object(provider, "_stream_post", return_value=mock_response):
            events = list(provider.generate_stream_events("sys", []))

        tool_events = [e for e in events if e.get("type") == "tool_call"]
        assert len(tool_events) == 1
        assert tool_events[0]["id"].startswith("call_")

    def test_preserves_provided_tool_call_id(self):
        from wisp.providers.ollama import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434", model="qwen")
        mock_response = Mock()
        mock_response.iter_lines.return_value = [
            b'{"message": {"tool_calls": [{"id": "call_abc123", "function": {"name": "read_file", "arguments": {}}}]}}',
            b'{"done": true}',
        ]

        with patch.object(provider, "_stream_post", return_value=mock_response):
            events = list(provider.generate_stream_events("sys", []))

        tool_events = [e for e in events if e.get("type") == "tool_call"]
        assert tool_events[0]["id"] == "call_abc123"

    def test_handles_http_error(self):
        from wisp.providers.ollama import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434", model="qwen")

        with patch.object(provider, "_stream_post", side_effect=Exception("Connection refused")):
            events = list(provider.generate_stream_events("sys", []))

        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) == 1
        assert "Connection refused" in error_events[0]["message"]


# ═══════════════════════════════════════════════════════════════════
# 3. health_check
# ═══════════════════════════════════════════════════════════════════

class TestHealthCheck:
    """health_check must verify Ollama availability."""

    def test_returns_healthy_when_ollama_responds(self):
        from wisp.providers.ollama import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434", model="qwen")
        provider._client = None

        with patch.object(provider, "_get", return_value={"models": []}):
            result = provider.health_check()

        assert result["status"] == "healthy"

    def test_returns_unhealthy_when_ollama_down(self):
        from wisp.providers.ollama import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434", model="qwen")
        provider._client = None

        with patch.object(provider, "_get", side_effect=Exception("Connection refused")):
            result = provider.health_check()

        assert result["status"] == "unhealthy"


# ═══════════════════════════════════════════════════════════════════
# 4. list_models
# ═══════════════════════════════════════════════════════════════════

class TestListModels:
    """list_models must return models from Ollama API."""

    def test_returns_models_from_api(self):
        from wisp.providers.ollama import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434", model="qwen")
        provider._client = None  # Ensure no real client

        with patch.object(provider, "_get", return_value={"models": [{"name": "qwen2.5-coder"}]}):
            models = provider.list_models()

        assert len(models) == 1
        assert models[0]["id"] == "qwen2.5-coder"


# ═══════════════════════════════════════════════════════════════════
# 5. get_model_info
# ═══════════════════════════════════════════════════════════════════

class TestGetModelInfo:
    """get_model_info must return model details from Ollama API."""

    def test_returns_model_info(self):
        from wisp.providers.ollama import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434", model="qwen")
        provider._client = None

        with patch.object(provider, "_post", return_value={"context_length": 128000}):
            info = provider.get_model_info("qwen2.5-coder")

        assert info["context_length"] == 128000
