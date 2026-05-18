"""Ollama-backed provider implementation."""

from __future__ import annotations

from typing import Iterator, Optional

from wisp.ollama_client import OllamaClient

from .base import BaseProvider


class OllamaProvider(BaseProvider):
    """Adapter that exposes the existing Ollama client via the provider API.

    Uses composition instead of multiple inheritance to avoid the
    provider/client identity crisis.
    """

    def __init__(self, config):
        self._client = OllamaClient(config)

    def check_health(self) -> bool:
        return self._client.check_health()

    def list_models(self) -> list[dict]:
        return self._client.list_models()

    def get_context_length(self) -> int:
        return self._client.get_context_length()

    def generate(self, system_prompt: str, messages: list[dict], tools: Optional[list] = None) -> dict:
        return self._client.generate(system_prompt, messages, tools)

    def generate_stream_events(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: Optional[list] = None,
        checkpoint_every: int = 50,
    ) -> Iterator:
        return self._client.generate_stream_events(system_prompt, messages, tools, checkpoint_every)

    @property
    def stream_response(self) -> Optional[dict]:
        return self._client.stream_response

    @stream_response.setter
    def stream_response(self, value: Optional[dict]) -> None:
        self._client.stream_response = value

    def __getattr__(self, name: str):
        """Delegate unknown attributes to the underlying OllamaClient.

        Preserves backward compatibility for methods like ``get_model_info()``
        that exist on the client but are not part of the BaseProvider contract.
        """
        return getattr(self._client, name)
