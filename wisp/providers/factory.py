"""Provider factory — manages provider registration, discovery, and instantiation.

Usage:
    factory = ProviderFactory()
    factory.register("ollama", OllamaProvider)
    provider = factory.create("ollama", base_url="...", model="...")
"""

from __future__ import annotations

import logging
from typing import Any, Type

from .protocol import Provider
from .ollama import OllamaProvider

logger = logging.getLogger(__name__)


class ProviderFactory:
    """Factory for creating provider instances."""

    def __init__(self):
        self._providers: dict[str, Type[Provider]] = {}
        self._default: str | None = None
        self._register_builtins()

    def _register_builtins(self) -> None:
        """Register built-in providers."""
        self.register("ollama", OllamaProvider)

    def register(self, name: str, provider_class: Type[Provider]) -> None:
        """Register a provider class under a name."""
        self._providers[name] = provider_class
        logger.debug("Registered provider: %s", name)

    def create(self, name: str, **kwargs) -> Provider:
        """Create a provider instance by name."""
        if name not in self._providers:
            raise ValueError(f"Unknown provider: {name}. Available: {list(self._providers.keys())}")
        return self._providers[name](**kwargs)

    def list_providers(self) -> list[str]:
        """List registered provider names."""
        return list(self._providers.keys())

    def set_default(self, name: str) -> None:
        """Set the default provider name."""
        if name not in self._providers:
            raise ValueError(f"Unknown provider: {name}")
        self._default = name

    def get_default(self) -> str | None:
        """Get the default provider name."""
        return self._default

    def create_default(self, **kwargs) -> Provider:
        """Create the default provider instance."""
        if self._default is None:
            raise ValueError("No default provider set")
        return self.create(self._default, **kwargs)

    def from_config(self, config: Any) -> Provider:
        """Create a provider from a config object.

        Config must have attributes:
          - provider: str (provider name)
          - ollama_url: str (for ollama provider)
          - model: str
        """
        name = getattr(config, "provider", "ollama")
        if not isinstance(name, str):
            name = "ollama"
        if name not in self._providers:
            raise ValueError(f"Unknown provider: {name}. Available: {list(self._providers.keys())}")

        if name == "ollama":
            raw_url = getattr(config, "ollama_url", "http://localhost:11434")
            base_url = self._validate_ollama_url(raw_url)
            return self.create(
                "ollama",
                config=config,
                base_url=base_url,
                model=getattr(config, "model", "qwen2.5-coder"),
            )

        return self.create(name, config=config)

    def _validate_ollama_url(self, url: str) -> str:
        """Validate Ollama URL to prevent SSRF.

        In production, rejects private IP ranges, metadata endpoints,
        and any URL that does not point to an explicitly allowed host.
        """
        import os
        import urllib.parse

        allowed_hosts = os.environ.get("WISP_ALLOWED_OLLAMA_HOSTS", "localhost,127.0.0.1").split(",")
        allowed_hosts = [h.strip().lower() for h in allowed_hosts]

        parsed = urllib.parse.urlparse(url)
        hostname = (parsed.hostname or "").lower()

        if not hostname:
            raise ValueError(f"Invalid Ollama URL (missing hostname): {url}")

        # Production guard: if WISP_PRODUCTION_MODE is set, restrict more
        if os.environ.get("WISP_PRODUCTION_MODE", "").lower() == "true":
            if hostname not in allowed_hosts:
                raise ValueError(
                    f"Ollama host '{hostname}' not in WISP_ALLOWED_OLLAMA_HOSTS. "
                    f"Allowed: {', '.join(allowed_hosts)}"
                )

            # Block private IP ranges / metadata endpoints
            if hostname in ("169.254.169.254", "metadata.google.internal"):
                raise ValueError(f"Ollama URL points to a cloud metadata endpoint: {url}")

        return url
