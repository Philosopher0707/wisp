"""Provider protocol — the interface all LLM providers must implement.

Decouples WispAgentCore from any specific provider implementation.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Generator


class Provider(ABC):
    """Abstract base class for LLM providers.

    Providers must implement these methods:
      - generate_stream_events: stream events from the model (sync)
      - health_check: check if provider is available
      - list_models: list available models
      - get_model_info: get capabilities for a specific model

    generate_stream_events_async has a default implementation that bridges the
    sync generator onto a worker thread. Providers with native async I/O
    should override it.
    """

    @abstractmethod
    def generate_stream_events(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        checkpoint_every: int = 50,
    ) -> Generator[dict[str, Any], None, None]:
        """Generate a stream of events from the model (synchronous).

        Yields standardized event dictionaries:
          - {"type": "content", "text": "..."}
          - {"type": "tool_call", "name": "...", "arguments": {...}}
          - {"type": "tool_calls", "calls": [...]}  # Batched tool calls (preferred)
          - {"type": "thinking", "text": "..."}
          - {"type": "done", "done_reason": "..."}
          - {"type": "error", "message": "..."}
        """
        ...

    def generate_stream_events_async(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        checkpoint_every: int = 50,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream events from the model (asynchronous).

        Default implementation: bridges the sync generator onto a daemon
        worker thread and shuttles events to the running event loop through
        an asyncio.Queue, so a blocking HTTP client never stalls the loop.
        Providers with native async I/O should override this.

        Yields the same event format as generate_stream_events.
        """
        sync_gen = self.generate_stream_events(
            system_prompt, messages, tools, checkpoint_every
        )

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        done: object = object()
        producer_error: list[BaseException] = []
        cancelled = threading.Event()

        def _sync_producer() -> None:
            try:
                for event in sync_gen:
                    if cancelled.is_set():
                        break
                    # put_nowait's return type (None) trips mypy's callback
                    # signature; same suppression as core/stateless.py
                    loop.call_soon_threadsafe(queue.put_nowait, event)
                loop.call_soon_threadsafe(queue.put_nowait, done)  # type: ignore[arg-type]
            except Exception as exc:
                # Deliver the failure to the consumer instead of letting it
                # die in the thread excepthook as a clean-looking end.
                producer_error.append(exc)
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(queue.put_nowait, done)  # type: ignore[arg-type]

        thread = threading.Thread(target=_sync_producer, daemon=True)
        thread.start()

        async def _bridge() -> AsyncIterator[dict[str, Any]]:
            try:
                while True:
                    event = await queue.get()
                    if event is done:
                        if producer_error:
                            raise producer_error[0]
                        break
                    yield event
            finally:
                cancelled.set()
                while not queue.empty():
                    try:
                        queue.get_nowait()
                    except Exception:
                        break
                # Never join the producer thread here: a blocking join runs
                # on the event-loop thread, so every cancelled stream froze
                # the whole REPL for up to 5s — and a Ctrl+C landing inside
                # that join corrupted task teardown. Poll cooperatively for
                # a short grace window (well-behaved producers check the
                # cancel flag between chunks and exit immediately); a
                # producer stuck mid-read stays daemon-backed and dies with
                # the process instead of stalling teardown.
                deadline = loop.time() + 1.0
                try:
                    while thread.is_alive() and loop.time() < deadline:
                        await asyncio.sleep(0.02)
                except RuntimeError:
                    pass  # loop closed mid-poll during interpreter teardown

        return _bridge()

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

    def generate_structured(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        output_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate structured output matching a JSON schema.

        Optional method - providers that support structured output
        (e.g., Ollama with `format=json`, OpenAI with `response_format`)
        should override this.

        Args:
            system_prompt: System prompt
            messages: Conversation messages
            output_schema: JSON Schema to validate output against

        Returns:
            Parsed JSON object matching the schema

        Raises:
            NotImplementedError: If provider doesn't support structured output
        """
        raise NotImplementedError("Structured output not supported by this provider")
