"""Provider abstractions for model backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, Optional


class BaseProvider(ABC):
    """Abstract model provider contract used by the agent core."""

    stream_response: Optional[dict] = None

    @abstractmethod
    def check_health(self) -> bool:
        """Verify the provider is reachable and the configured model is usable."""

    @abstractmethod
    def list_models(self) -> list[dict]:
        """Return available models in provider-specific shape."""

    @abstractmethod
    def get_context_length(self) -> int:
        """Return the effective context length for the configured model."""

    @abstractmethod
    def generate(self, system_prompt: str, messages: list[dict], tools: Optional[list] = None) -> dict:
        """Run a non-streaming generation."""

    @abstractmethod
    def generate_stream_events(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: Optional[list] = None,
        checkpoint_every: int = 50,
    ) -> Iterator:
        """Run a streaming generation and yield stream events."""
