"""NVIDIA Nemotron provider implementation.

NVIDIA's API is OpenAI-compatible, so this provider extends the OpenAI provider
with NVIDIA-specific defaults and model support.

Configuration:
    WISP_PROVIDER=nvidia
    WISP_MODEL=nvidia/nemotron-3-ultra-550b-a55b
    WISP_API_KEY=nvapi-...  (from NVIDIA)
    WISP_API_BASE=https://integrate.api.nvidia.com/v1  (optional, default)

Available models (from NVIDIA API):
- nvidia/nemotron-3-ultra-550b-a55b (550B params, 128K context)
- nvidia/nemotron-3-ultra-253b-v1 (253B params, 128K context)
- nvidia/nemotron-3-super-120b-a12b (120B params, 128K context)
- nvidia/nemotron-3-nano-30b-a3b (30B params, 128K context)
- nvidia/nemotron-3-nano-omni-30b-a3b-reasoning (30B params, 128K context)
- nvidia/llama-3.1-nemotron-51b-instruct (51B params, 128K context)
- nvidia/llama-3.1-nemotron-70b-instruct (70B params, 128K context)
- nvidia/llama-3.1-nemotron-nano-8b-v1 (8B params, 128K context)
- nvidia/nemotron-4-340b-instruct (340B params, 128K context)
- nvidia/nemotron-4-340b-reward (340B params, 128K context)
"""

from __future__ import annotations

from typing import Any

from .openai import OpenAIProvider


class NVIDIAProvider(OpenAIProvider):
    """NVIDIA Nemotron provider using OpenAI-compatible API."""

    _DEFAULT_API_BASE = "https://integrate.api.nvidia.com/v1"
    _DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"

    # Known context lengths for NVIDIA models (from API)
    _MODEL_CONTEXT = {
        "nvidia/nemotron-3-ultra-550b-a55b": 128000,
        "nvidia/nemotron-3-ultra-253b-v1": 128000,
        "nvidia/nemotron-3-super-120b-a12b": 128000,
        "nvidia/nemotron-3-nano-30b-a3b": 128000,
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning": 128000,
        "nvidia/llama-3.1-nemotron-51b-instruct": 128000,
        "nvidia/llama-3.1-nemotron-70b-instruct": 128000,
        "nvidia/llama-3.1-nemotron-nano-8b-v1": 128000,
        "nvidia/nemotron-4-340b-instruct": 128000,
        "nvidia/nemotron-4-340b-reward": 128000,
    }

    def __init__(self, config=None, base_url: str = "", model: str = "", api_key: str = ""):
        # Call parent init but with NVIDIA defaults
        if config is not None:
            self.api_key = getattr(config, "api_key", "") or ""
            self.api_base = (
                getattr(config, "api_base", "")
                or ""
            ) or self._DEFAULT_API_BASE
            self.model = getattr(config, "model", self._DEFAULT_MODEL)
            self.temperature = getattr(config, "temperature", 0.2)
            self.max_tokens = getattr(config, "max_tokens", None)
        else:
            self.api_key = api_key or ""
            self.api_base = base_url or self._DEFAULT_API_BASE
            self.model = model or self._DEFAULT_MODEL
            self.temperature = 0.2
            self.max_tokens = None

        self.api_base = self.api_base.rstrip("/")
        self._stream_response = None

    def get_model_info(self, model: str) -> dict[str, Any]:
        """Get model info with NVIDIA-specific context lengths."""
        ctx = self._MODEL_CONTEXT.get(model, 128000)
        return {"id": model, "context_length": ctx}

    @property
    def default_model(self) -> str:
        """Default NVIDIA model."""
        return self._DEFAULT_MODEL

    @property
    def available_models(self) -> list[str]:
        """List of available NVIDIA models."""
        return list(self._MODEL_CONTEXT.keys())