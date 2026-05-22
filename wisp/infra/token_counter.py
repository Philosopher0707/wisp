"""TokenCounter — accurate token counting with tiktoken, fallback to ratio.

Replaces ad-hoc ``len(text) // chars_per_token`` estimates across the codebase.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Map model identifiers to tiktoken encoding names.
# Covers Ollama models (llama3, qwen, phi, gemma, mistral, codellama, deepseek)
# and cloud models (claude, gpt).
_MODEL_TO_ENCODING: dict[str, str] = {
    # OpenAI models
    "gpt-4": "cl100k_base",
    "gpt-4o": "o200k_base",
    "gpt-4-turbo": "cl100k_base",
    "gpt-3.5": "cl100k_base",
    "gpt-3.5-turbo": "cl100k_base",
    # Claude models (approximate — Anthropic uses a different tokenizer)
    "claude": "cl100k_base",
    # Llama 3+ uses a BPE tokenizer similar to cl100k_base
    "llama3": "cl100k_base",
    "llama3.1": "cl100k_base",
    "llama3.2": "cl100k_base",
    "llama3.3": "cl100k_base",
    "llama4": "cl100k_base",
    # Mistral
    "mistral": "cl100k_base",
    "mixtral": "cl100k_base",
    # Qwen
    "qwen": "cl100k_base",
    "qwen2.5": "cl100k_base",
    # Phi
    "phi3": "cl100k_base",
    "phi4": "cl100k_base",
    # Gemma
    "gemma2": "cl100k_base",
    # DeepSeek
    "deepseek": "cl100k_base",
    # Codellama
    "codellama": "cl100k_base",
}


class TokenCounter:
    """Count tokens in text, preferring tiktoken when available.

    Usage::

        counter = TokenCounter(chars_per_token=4)
        tokens = counter.count("hello world")           # uses ratio fallback
        tokens = counter.count("hello", model="llama3") # tries tiktoken first
    """

    def __init__(self, chars_per_token: int = 4):
        self._chars_per_token = max(1, chars_per_token)
        self._encoders: dict[str, object] = {}

    def count(self, text: str, model: str | None = None) -> int:
        """Return token count for *text*.

        If *model* is given and maps to a known tiktoken encoding, uses
        tiktoken for accuracy. Otherwise falls back to the char ratio.
        """
        if not text:
            return 0

        if model:
            encoder = self._get_encoder(model)
            if encoder is not None:
                try:
                    return len(encoder.encode(text))
                except Exception:
                    logger.debug("tiktoken encode failed for %s", model, exc_info=True)

        return max(1, len(text) // self._chars_per_token)

    def estimate(self, text: str) -> int:
        """Fast estimate using char ratio only (no tiktoken)."""
        return self.count(text, model=None)

    def estimate_chars(self, num_chars: int) -> int:
        """Convert a raw character count to a token estimate using the ratio."""
        if num_chars <= 0:
            return 0
        return max(1, num_chars // self._chars_per_token)

    # ── Internal ──────────────────────────────────────────────────────

    def _get_encoder(self, model: str) -> object | None:
        """Return a tiktoken Encoding for *model*, or None."""
        model_lower = model.lower()

        # Check cache
        if model_lower in self._encoders:
            return self._encoders[model_lower]

        # Find matching encoding name
        encoding_name: str | None = None
        for prefix, enc in _MODEL_TO_ENCODING.items():
            if model_lower.startswith(prefix):
                encoding_name = enc
                break

        if encoding_name is None:
            self._encoders[model_lower] = None
            return None

        try:
            import tiktoken
            encoder = tiktoken.get_encoding(encoding_name)
            self._encoders[model_lower] = encoder
            return encoder
        except ImportError:
            logger.debug("tiktoken not installed — using char ratio fallback")
        except Exception:
            logger.debug("Failed to get tiktoken encoding %s", encoding_name, exc_info=True)

        self._encoders[model_lower] = None
        return None
