"""Provider factory — manages provider registration, discovery, and instantiation.

Usage:
    factory = ProviderFactory()
    factory.register("ollama", OllamaProvider)
    provider = factory.create("ollama", base_url="...", model="...")
"""

from __future__ import annotations

import os

import logging
from typing import Any, Type

from .protocol import Provider
from .ollama import OllamaProvider
from .openai import OpenAIProvider
from .nvidia import NVIDIAProvider
from .openrouter import OpenRouterProvider

logger = logging.getLogger(__name__)


class ProviderFactory:
    """Factory for creating provider instances."""

    def __init__(self):
        self._providers: dict[str, Type[Provider]] = {}
        self._default: str | None = None
        self._register_builtins()

    def _register_builtins(self) -> None:
        """Register built-in providers.

        MUST cover every entry in provider_select.KNOWN_PROVIDERS — the
        selection contract (/provider, catalog, server routes) only offers
        providers this factory can actually build. `mock` was missing here
        once and /provider mock succeeded while the first turn crashed with
        'Unknown provider: mock'.
        """
        from .mock import MockProvider

        self.register("ollama", OllamaProvider)
        self.register("openai", OpenAIProvider)
        self.register("nvidia", NVIDIAProvider)
        self.register("openrouter", OpenRouterProvider)
        self.register("mock", MockProvider)

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

        Table-driven, no hardcoded model-id fallbacks anywhere — an empty
        model is legal and provider_catalog resolves it to a real served
        model. The API key comes from the provider_select vault
        (per-provider env var first, shared WISP_API_KEY fallback) — it is
        never re-derived here.
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
                model=getattr(config, "model", "") or "",
            )

        if name == "mock":
            return self.create("mock")

        # OpenAI-compatible providers share one construction shape; the
        # per-provider endpoint default lives in KNOWN_PROVIDERS — the same
        # table /provider and apply_switch already read — so there is ONE
        # source for "how to reach provider X".
        from wisp.provider_select import KNOWN_PROVIDERS, resolve_key

        spec = KNOWN_PROVIDERS.get(name, {})
        resolved_key = getattr(config, "api_key", "") or resolve_key(name)
        # Providers read keys from the CONFIG object when one is passed and
        # ignore the api_key kwarg (nvidia/openai init order), so hand them
        # a shallow copy carrying the vault-resolved key. Never mutate the
        # caller's runtime config.
        import copy

        local = copy.copy(config)
        try:
            object.__setattr__(local, "api_key", resolved_key)
            if hasattr(config, "__dict__"):
                local.__dict__["api_key"] = resolved_key
        except Exception:
            pass
        return self.create(
            name,
            config=local,
            base_url=getattr(local, "api_base", "") or spec.get("default_base", ""),
            model=getattr(local, "model", "") or "",
            api_key=resolved_key,
        )

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
