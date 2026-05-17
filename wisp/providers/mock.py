"""MockProvider — deterministic model provider for unit testing.

Implements BaseProvider without network calls.
Yields predefined responses for reliable, fast tests.

Usage:
    from wisp.providers.mock import MockProvider

    # Simple text response
    provider = MockProvider(responses=["hello world"])

    # With tool calls
    provider = MockProvider(
        responses=[""],
        tool_calls=[[{"function": {"name": "read_file", "arguments": {"path": "x"}}}]]
    )

    # With thinking
    provider = MockProvider(
        responses=["result"],
        thinking=["let me think..."]
    )

    # Multiple turns
    provider = MockProvider(responses=["first", "second", "third"])
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Iterator, Optional

from wisp.providers.base import BaseProvider
from wisp.stream_events import (
    TokenBatch,
    ToolCallBatch,
    StreamComplete,
    StreamError,
    StreamEvent,
)

logger = logging.getLogger(__name__)


class MockProvider(BaseProvider):
    """Deterministic provider for unit testing.

    Cycles through predefined responses. Each call to generate() or
    generate_stream_events() consumes the next response.
    """

    def __init__(
        self,
        responses: list[str] | None = None,
        tool_calls: list[list[dict]] | None = None,
        thinking: list[str] | None = None,
        model: str = "mock-model",
        context_length: int = 128000,
    ):
        self.responses = list(responses) if responses else []
        self.tool_calls = list(tool_calls) if tool_calls else []
        self.thinking = list(thinking) if thinking else []
        self.model = model
        self._context_length = context_length
        self._index = 0
        self.stream_response: Optional[dict] = None

    # ── BaseProvider API ───────────────────────────────────────────────

    def check_health(self) -> bool:
        return True

    def list_models(self) -> list[dict]:
        return [{"name": self.model, "size": 0}]

    def get_context_length(self) -> int:
        return self._context_length

    def generate(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: Optional[list] = None,
    ) -> dict:
        """Non-streaming generation. Returns the next predefined response."""
        content, tool_calls = self._next_response()
        msg: dict = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return {"message": msg}

    def generate_stream_events(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: Optional[list] = None,
        checkpoint_every: int = 50,
    ) -> Iterator[StreamEvent]:
        """Streaming generation. Yields TokenBatch, ToolCallBatch, StreamComplete."""
        content, tool_calls = self._next_response()
        thinking_text = self._next_thinking()

        # Yield thinking tokens
        if thinking_text:
            for i, chunk in enumerate(self._chunk_text(thinking_text, size=10)):
                yield TokenBatch(phase="thinking", text=chunk, batch_index=i)

        # Yield content tokens
        for i, chunk in enumerate(self._chunk_text(content, size=10)):
            yield TokenBatch(phase="content", text=chunk, batch_index=i)

        # Yield tool calls
        if tool_calls:
            yield ToolCallBatch(
                phase="tool_calls",
                calls=tool_calls,
            )

        # Build response for message history
        response_msg = {
            "role": "assistant",
            "content": content,
        }
        if thinking_text:
            response_msg["thinking"] = thinking_text
        if tool_calls:
            response_msg["tool_calls"] = tool_calls
        self.stream_response = {"message": response_msg}

        yield StreamComplete(
            phase="complete",
            final_thinking=thinking_text,
            final_content=content,
            total_tokens=len(thinking_text) + len(content),
            tool_calls=tool_calls,
            validation_hash="mock-hash",
        )

    # ── Internal ───────────────────────────────────────────────────────

    def _next_response(self) -> tuple[str, Optional[list[dict]]]:
        """Get the next response and tool calls."""
        if self._index < len(self.responses):
            content = self.responses[self._index]
        else:
            content = "[mock: no more responses]"

        tool_calls = None
        if self._index < len(self.tool_calls):
            tool_calls = self.tool_calls[self._index]

        self._index += 1
        return content, tool_calls

    def _next_thinking(self) -> str:
        """Get the next thinking text."""
        idx = self._index - 1  # Align with current response
        if 0 <= idx < len(self.thinking):
            return self.thinking[idx]
        return ""

    @staticmethod
    def _chunk_text(text: str, size: int = 10) -> list[str]:
        """Split text into chunks for token batching."""
        if not text:
            return []
        return [text[i:i+size] for i in range(0, len(text), size)]
