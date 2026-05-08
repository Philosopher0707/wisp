"""Ollama-backed provider implementation."""

from __future__ import annotations

from wisp.ollama_client import OllamaClient

from .base import BaseProvider


class OllamaProvider(OllamaClient, BaseProvider):
    """Adapter that exposes the existing Ollama client via the provider API."""

    pass
