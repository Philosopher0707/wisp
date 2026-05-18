"""TDD for provider factory.

Manages provider registration, discovery, and instantiation.
"""

import pytest
from typing import Any


# ═══════════════════════════════════════════════════════════════════
# 1. Registration
# ═══════════════════════════════════════════════════════════════════

class TestProviderRegistration:
    """Providers can be registered and retrieved by name."""

    def test_register_provider(self):
        from wisp.providers.factory import ProviderFactory
        from wisp.providers.protocol import Provider

        class FakeProvider(Provider):
            def generate_stream_events(self, **kwargs): yield {"type": "done"}
            def health_check(self): return {"status": "healthy"}
            def list_models(self): return []
            def get_model_info(self, model): return {}

        factory = ProviderFactory()
        factory.register("fake", FakeProvider)
        assert "fake" in factory.list_providers()

    def test_create_registered_provider(self):
        from wisp.providers.factory import ProviderFactory
        from wisp.providers.protocol import Provider

        class FakeProvider(Provider):
            def __init__(self, url=""): self.url = url
            def generate_stream_events(self, **kwargs): yield {"type": "done"}
            def health_check(self): return {"status": "healthy"}
            def list_models(self): return []
            def get_model_info(self, model): return {}

        factory = ProviderFactory()
        factory.register("fake", FakeProvider)
        provider = factory.create("fake", url="http://test")
        assert isinstance(provider, FakeProvider)
        assert provider.url == "http://test"

    def test_unknown_provider_raises(self):
        from wisp.providers.factory import ProviderFactory

        factory = ProviderFactory()
        with pytest.raises(ValueError):
            factory.create("unknown")


# ═══════════════════════════════════════════════════════════════════
# 2. Built-in providers
# ═══════════════════════════════════════════════════════════════════

class TestBuiltInProviders:
    """Factory comes with built-in providers pre-registered."""

    def test_has_ollama_provider(self):
        from wisp.providers.factory import ProviderFactory

        factory = ProviderFactory()
        assert "ollama" in factory.list_providers()

    def test_can_create_ollama_provider(self):
        from wisp.providers.factory import ProviderFactory

        factory = ProviderFactory()
        provider = factory.create("ollama", base_url="http://localhost:11434", model="qwen")
        assert provider is not None


# ═══════════════════════════════════════════════════════════════════
# 3. Default provider
# ═══════════════════════════════════════════════════════════════════

class TestDefaultProvider:
    """Factory supports default provider selection."""

    def test_set_default_provider(self):
        from wisp.providers.factory import ProviderFactory
        from wisp.providers.protocol import Provider

        class FakeProvider(Provider):
            def generate_stream_events(self, **kwargs): yield {"type": "done"}
            def health_check(self): return {"status": "healthy"}
            def list_models(self): return []
            def get_model_info(self, model): return {}

        factory = ProviderFactory()
        factory.register("fake", FakeProvider)
        factory.set_default("fake")
        assert factory.get_default() == "fake"

    def test_create_default_provider(self):
        from wisp.providers.factory import ProviderFactory
        from wisp.providers.protocol import Provider

        class FakeProvider(Provider):
            def __init__(self, model_name=""): self.model_name = model_name
            def generate_stream_events(self, **kwargs): yield {"type": "done"}
            def health_check(self): return {"status": "healthy"}
            def list_models(self): return []
            def get_model_info(self, model): return {}

        factory = ProviderFactory()
        factory.register("fake", FakeProvider)
        factory.set_default("fake")
        provider = factory.create_default(model_name="test")
        assert isinstance(provider, FakeProvider)
        assert provider.model_name == "test"


# ═══════════════════════════════════════════════════════════════════
# 4. Provider discovery
# ═══════════════════════════════════════════════════════════════════

class TestProviderDiscovery:
    """Factory can discover providers from configuration."""

    def test_discover_from_config(self):
        from wisp.providers.factory import ProviderFactory

        class FakeConfig:
            provider = "ollama"
            ollama_url = "http://localhost:11434"
            model = "qwen"
            temperature = 0.7
            top_p = 0.9
            max_tokens = 4096
            system_prompt = ""
            show_thinking = False

        factory = ProviderFactory()
        provider = factory.from_config(FakeConfig())
        assert provider is not None

    def test_discover_unknown_provider_uses_default(self):
        from wisp.providers.factory import ProviderFactory

        class FakeConfig:
            provider = "unknown"

        factory = ProviderFactory()
        with pytest.raises(ValueError):
            factory.from_config(FakeConfig())
