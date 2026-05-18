"""Provider protocol — the interface all LLM providers must implement.

Decouples WispAgentCore from any specific provider implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generator


class Provider(ABC):
    """Abstract base class for LLM providers.

    All providers must implement these four methods:
      - generate_stream_events: stream events from the model
      - health_check: check if provider is available
      - list_models: list available models
      - get_model_info: get capabilities for a specific model
    """

    @abstractmethod
    def generate_stream_events(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        checkpoint_every: int = 50,
    ) -> Generator[dict, None, None]:
        """Generate a stream of events from the model.

        Yields standardized event dictionaries:
          - {"type": "content", "text": "..."}
          - {"type": "tool_call", "name": "...", "arguments": {...}}
          - {"type": "done"}
          - {"type": "error", "message": "..."}
        """
        ...

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        """Check provider health.

        Returns {"status": "healthy"|"unhealthy", ...}
        """
        ...

    @abstractmethod
    def list_models(self) -> list[dict[str, Any]]:
        """List available models.

        Returns [{"id": "...", "name": "...", ...}]
        """
        ...

    @abstractmethod
    def get_model_info(self, model: str) -> dict[str, Any]:
        """Get info about a specific model.

        Returns {"id": "...", "context_length": ..., ...}
        """
        ...
