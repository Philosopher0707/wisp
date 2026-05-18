"""TDD for Provider protocol.

Defines the interface that all LLM providers must implement.
This decouples WispAgentCore from any specific provider.
"""

import pytest
from typing import Any


# ═══════════════════════════════════════════════════════════════════
# 1. Protocol definition
# ═══════════════════════════════════════════════════════════════════

class TestProviderProtocol:
    """All providers must implement these methods."""

    def test_has_generate_stream_events(self):
        from wisp.providers.protocol import Provider
        assert hasattr(Provider, "generate_stream_events")

    def test_has_health_check(self):
        from wisp.providers.protocol import Provider
        assert hasattr(Provider, "health_check")

    def test_has_list_models(self):
        from wisp.providers.protocol import Provider
        assert hasattr(Provider, "list_models")

    def test_has_get_model_info(self):
        from wisp.providers.protocol import Provider
        assert hasattr(Provider, "get_model_info")


# ═══════════════════════════════════════════════════════════════════
# 2. generate_stream_events contract
# ═══════════════════════════════════════════════════════════════════

class TestGenerateStreamEvents:
    """generate_stream_events must yield standardized events."""

    def test_yields_content_events(self):
        from wisp.providers.protocol import Provider

        class FakeProvider(Provider):
            def generate_stream_events(self, system_prompt, messages, tools=None, checkpoint_every=50):
                yield {"type": "content", "text": "hello"}
                yield {"type": "done"}

            def health_check(self): pass
            def list_models(self): return []
            def get_model_info(self, model): return {}

        provider = FakeProvider()
        events = list(provider.generate_stream_events("sys", [{"role": "user", "content": "hi"}]))
        assert len(events) == 2
        assert events[0]["type"] == "content"
        assert events[1]["type"] == "done"

    def test_yields_tool_call_events(self):
        from wisp.providers.protocol import Provider

        class FakeProvider(Provider):
            def generate_stream_events(self, system_prompt, messages, tools=None, checkpoint_every=50):
                yield {"type": "tool_call", "name": "read_file", "arguments": {"path": "test.py"}}
                yield {"type": "done"}

            def health_check(self): pass
            def list_models(self): return []
            def get_model_info(self, model): return {}

        provider = FakeProvider()
        events = list(provider.generate_stream_events("sys", [{"role": "user", "content": "read"}]))
        assert events[0]["type"] == "tool_call"
        assert events[0]["name"] == "read_file"

    def test_accepts_tools_parameter(self):
        from wisp.providers.protocol import Provider

        class FakeProvider(Provider):
            def generate_stream_events(self, system_prompt, messages, tools=None, checkpoint_every=50):
                self.last_tools = tools
                yield {"type": "done"}

            def health_check(self): pass
            def list_models(self): return []
            def get_model_info(self, model): return {}

        provider = FakeProvider()
        tools = [{"name": "read_file", "description": "Read a file"}]
        list(provider.generate_stream_events("sys", [], tools=tools))
        assert provider.last_tools == tools


# ═══════════════════════════════════════════════════════════════════
# 3. health_check contract
# ═══════════════════════════════════════════════════════════════════

class TestHealthCheck:
    """health_check must return standardized status."""

    def test_returns_healthy_when_available(self):
        from wisp.providers.protocol import Provider

        class HealthyProvider(Provider):
            def generate_stream_events(self, **kwargs): yield {"type": "done"}
            def health_check(self):
                return {"status": "healthy", "latency_ms": 10}
            def list_models(self): return []
            def get_model_info(self, model): return {}

        provider = HealthyProvider()
        result = provider.health_check()
        assert result["status"] == "healthy"

    def test_returns_unhealthy_when_down(self):
        from wisp.providers.protocol import Provider

        class UnhealthyProvider(Provider):
            def generate_stream_events(self, **kwargs): yield {"type": "done"}
            def health_check(self):
                return {"status": "unhealthy", "error": "connection refused"}
            def list_models(self): return []
            def get_model_info(self, model): return {}

        provider = UnhealthyProvider()
        result = provider.health_check()
        assert result["status"] == "unhealthy"


# ═══════════════════════════════════════════════════════════════════
# 4. list_models contract
# ═══════════════════════════════════════════════════════════════════

class TestListModels:
    """list_models must return standardized model info."""

    def test_returns_list_of_models(self):
        from wisp.providers.protocol import Provider

        class ModelProvider(Provider):
            def generate_stream_events(self, **kwargs): yield {"type": "done"}
            def health_check(self): return {"status": "healthy"}
            def list_models(self):
                return [{"id": "qwen2.5-coder", "name": "Qwen 2.5 Coder"}]
            def get_model_info(self, model): return {}

        provider = ModelProvider()
        models = provider.list_models()
        assert len(models) == 1
        assert models[0]["id"] == "qwen2.5-coder"


# ═══════════════════════════════════════════════════════════════════
# 5. get_model_info contract
# ═══════════════════════════════════════════════════════════════════

class TestGetModelInfo:
    """get_model_info must return model capabilities."""

    def test_returns_model_details(self):
        from wisp.providers.protocol import Provider

        class InfoProvider(Provider):
            def generate_stream_events(self, **kwargs): yield {"type": "done"}
            def health_check(self): return {"status": "healthy"}
            def list_models(self): return []
            def get_model_info(self, model):
                return {"id": model, "context_length": 128000}

        provider = InfoProvider()
        info = provider.get_model_info("qwen2.5-coder")
        assert info["context_length"] == 128000
