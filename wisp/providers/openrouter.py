"""OpenRouter provider — OpenAI-compatible via openrouter.ai.

OpenRouter speaks the OpenAI chat-completions protocol but has its own
base URL, its own key convention (OPENROUTER_API_KEY), optional
attribution headers, and a /models payload that includes a human display
name next to the id.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .openai import OpenAIProvider

logger = logging.getLogger(__name__)


class OpenRouterProvider(OpenAIProvider):
    """OpenRouter (https://openrouter.ai) — one API, many upstream models.

    Configuration:
        WISP_PROVIDER=openrouter
        WISP_API_KEY=sk-or-v1-...      (or OPENROUTER_API_KEY)
        WISP_MODEL=anthropic/claude-3-haiku
        WISP_API_BASE=                 (optional; default openrouter.ai/api/v1)
    """

    _DEFAULT_API_BASE = "https://openrouter.ai/api/v1"
    _DEFAULT_MODEL = "openrouter/auto"

    def __init__(self, config: Any = None, base_url: str = "",
                 model: str = "", api_key: str = "") -> None:
        # Parent init sets EVERY operational attribute (temperature,
        # timeouts, ...). Skipping it left this object half-initialized and
        # streaming crashed on self.temperature. Call it first, then apply
        # OpenRouter-specific key/base precedence on top.
        super().__init__(config=config, base_url=base_url, model=model,
                         api_key=api_key)
        if config is not None:
            self.api_key = (
                getattr(config, "api_key", "")
                or os.environ.get("OPENROUTER_API_KEY", "")
                or os.environ.get("WISP_API_KEY", "")
            )
            if not (getattr(config, "api_base", "") or ""):
                self.api_base = self._DEFAULT_API_BASE
            if not model:
                self.model = getattr(config, "model", self._DEFAULT_MODEL)
        else:
            self.api_key = (
                api_key or os.environ.get("OPENROUTER_API_KEY", "")
                or os.environ.get("WISP_API_KEY", "")
            )
            self.api_base = (base_url or self._DEFAULT_API_BASE).rstrip("/")
            if not model:
                self.model = self._DEFAULT_MODEL

    def _auth_headers(self) -> dict[str, str]:
        headers = super()._auth_headers()
        # Optional attribution OpenRouter uses for app rankings; harmless
        # everywhere else because only their endpoint sees these.
        referer = os.environ.get("OPENROUTER_SITE_URL")
        title = os.environ.get("OPENROUTER_APP_TITLE")
        if referer:
            headers["HTTP-Referer"] = referer
        if title:
            headers["X-Title"] = title
        return headers

    def list_models(self) -> list[dict[str, Any]]:
        """All models on OpenRouter, id + human display name."""
        import requests

        try:
            resp = requests.get(
                f"{self.api_base}/models",
                headers=self._auth_headers(),
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning(
                    "OpenRouter list_models failed: HTTP %s", resp.status_code)
                return []
            data = resp.json()
            models: list[dict[str, Any]] = []
            for entry in data.get("data", []):
                mid = entry.get("id", "")
                if not mid:
                    continue
                models.append({
                    "id": mid,
                    "name": entry.get("name") or mid,
                    "context_length": entry.get("context_length"),
                })
            return models
        except Exception as exc:
            logger.warning("OpenRouter list_models error: %s", exc)
            return []
