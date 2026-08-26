"""Provider factory for Wisp model backends."""

from __future__ import annotations

from wisp.config import WispConfig

from .base import BaseProvider
from .ollama import OllamaProvider
from .openai import OpenAIProvider
from .nvidia import NVIDIAProvider
from .openrouter import OpenRouterProvider
from .mock import MockProvider

__all__ = ["BaseProvider", "OllamaProvider", "OpenAIProvider", "NVIDIAProvider", "OpenRouterProvider", "MockProvider", "get_provider"]


def get_provider(config: WispConfig) -> BaseProvider:
    """Build the configured model provider.

    Single construction path: delegates to ProviderFactory.from_config so
    this function and the composition core NEVER diverge on which providers
    exist or how they are built (drift here once meant /provider mock
    succeeded via this path while the turn-time factory crashed).
    """
    from .factory import ProviderFactory

    return ProviderFactory().from_config(config)
