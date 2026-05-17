"""Provider factory for Wisp model backends."""

from __future__ import annotations

from wisp.config import WispConfig

from .base import BaseProvider
from .ollama import OllamaProvider
from .mock import MockProvider

__all__ = ["BaseProvider", "OllamaProvider", "MockProvider", "get_provider"]


def get_provider(config: WispConfig) -> BaseProvider:
    """Build the configured model provider."""
    provider_name = getattr(config, "provider", "ollama")
    if not isinstance(provider_name, str) or not provider_name:
        provider_name = "ollama"
    if provider_name == "ollama":
        return OllamaProvider(config)
    if provider_name == "mock":
        return MockProvider()
    raise ValueError(f"Unsupported provider: {provider_name}")
