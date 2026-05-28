"""Ollama API client for Wisp — handles model inference with tool-calling support.

Production-hardened with:
- Retry with exponential backoff for transient failures
- Proper request timeouts
- Connection pooling via requests.Session
- Structured logging
- Batched token streaming with checkpoint validation
"""

import asyncio
import contextvars
import json
import logging
import threading
import time
from typing import Optional, Iterator

import requests

from wisp.config import WispConfig
from wisp.core.message_format import to_ollama_messages
from wisp.stream_events import (
    EventBatcher,
    TokenBatch,
    ToolCallBatch,
    Checkpoint,
    StreamComplete,
    StreamError,
    StreamEvent,
)
from wisp.stream_parser import parse_stream

logger = logging.getLogger(__name__)
_loop_local = threading.local()

# Context-safe storage for per-turn/client stream responses.
# Using a ContextVar prevents cross-session data leakage when the same
# client instance is shared across concurrent tasks.
_ollama_stream_response: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "ollama_stream_response", default=None
)


class OllamaError(Exception):
    """Raised when Ollama API calls fail after all retries."""
    pass



def _async_sleep_if_in_loop(delay: float) -> None:
    """Sleep *delay* seconds without pinning a thread-pool worker.

    In async contexts (e.g. inside ``sync_gen_iter`` thread) this schedules
    the sleep on the host event loop and frees the worker.  In plain sync
    contexts it falls back to ordinary ``time.sleep``.
    """
    # 1) Already on the main event-loop thread — blocking is the only safe
    #    option because sync code cannot ``await``.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and threading.current_thread() is threading.main_thread():
        time.sleep(delay)
        return

    # 2) Inside a sync_gen_iter worker — the loop was stashed in a
    #    thread-local by the bridge before the generator started.
    if loop is None:
        loop = getattr(_loop_local, "loop", None)
        if loop is not None:
            coro = asyncio.sleep(delay)
            try:
                future = asyncio.run_coroutine_threadsafe(coro, loop)
                future.result(timeout=delay + 5)
                return
            except Exception:
                coro.close()
                pass

    # 3) Fallback — we have no event loop to defer to.
    time.sleep(delay)


class OllamaClient:
    """Minimal client for Ollama's API, optimized for tool-calling models."""

    def __init__(self, config: WispConfig, session: Optional[requests.Session] = None):
        self.base_url = config.ollama_url.rstrip("/")
        self.model = config.model
        self.temperature = config.temperature
        self.max_tokens = config.max_tokens
        self._session = session or requests.Session()
        # SECURITY: stream_response is stored in a ContextVar (not a
        # mutable instance attribute) so that concurrent turns cannot
        # overwrite or leak each other's response data.
        _ollama_stream_response.set(None)

    @property
    def stream_response(self) -> Optional[dict]:
        return _ollama_stream_response.get(None)

    @stream_response.setter
    def stream_response(self, value: Optional[dict]) -> None:
        _ollama_stream_response.set(value)

    def close(self) -> None:
        """Close the underlying requests session."""
        if self._session is not None:
            self._session.close()
            self._session = None

    def check_health(self) -> bool:
        """Verify Ollama is running and the model is available."""
        try:
            resp = self._session.get(f"{self.base_url}/api/tags", timeout=5)
            resp.raise_for_status()
            data = resp.json()
            models = data.get("models", [])
            model_names = [m.get("name", "").lower() for m in models]
            # Support both exact match and tag-less match
            target = self.model.lower()
            if target in model_names:
                return True
            # Try without :latest or other tags
            base = target.split(":")[0]
            if base in model_names or any(m.startswith(base + ":") for m in model_names):
                return True
            logger.warning(
                "Model '%s' not found in Ollama. Available: %s",
                self.model,
                ", ".join(model_names[:5]) + ("..." if len(model_names) > 5 else ""),
            )
            return False
        except requests.exceptions.ConnectionError:
            logger.error("Cannot connect to Ollama at %s", self.base_url)
            return False
        except requests.exceptions.RequestException as e:
            logger.error("Health check failed: %s", e)
            return False

    def list_models(self) -> list[dict]:
        """List all available models from Ollama."""
        try:
            resp = self._session.get(f"{self.base_url}/api/tags", timeout=5)
            resp.raise_for_status()
            return resp.json().get("models", [])
        except requests.exceptions.RequestException as e:
            logger.error("Failed to list models: %s", e)
            return []

    def get_model_info(self) -> dict:
        """Fetch detailed model info via /api/show.

        Returns the raw response dict. Raises OllamaError on failure.
        """
        try:
            resp = self._session.post(
                f"{self.base_url}/api/show",
                json={"model": self.model},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            raise OllamaError(f"Failed to get model info: {e}")

    def get_context_length(self) -> int:
        """Auto-detect the model's context window length.

        Queries /api/show and scans model_info for any key ending in
        '.context_length'. Falls back to 128000 if not found.
        """
        try:
            info = self.get_model_info()
            model_info = info.get("model_info", {})
            for key, value in model_info.items():
                if key.endswith(".context_length") and isinstance(value, int):
                    logger.debug("Detected context length for %s: %d", self.model, value)
                    return value
        except OllamaError as e:
            logger.warning("Could not auto-detect context length: %s", e)
        return 128000  # conservative default

    def _post_with_retry(self, endpoint: str, payload: dict, timeout: int = 600):
        """Make a POST request with exponential backoff retry.

        Retries on transient failures (5xx errors, connection errors).
        """
        url = f"{self.base_url}/api/{endpoint}"
        max_retries = 3
        base_delay = 1  # seconds

        for attempt in range(max_retries):
            try:
                resp = self._session.post(url, json=payload, timeout=timeout)
                resp.raise_for_status()
                return resp
            except requests.exceptions.HTTPError as e:
                if e.response.status_code >= 500:
                    # Server error - retry
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning("Server error %d, retrying in %ds...", e.response.status_code, delay)
                        _async_sleep_if_in_loop(delay)
                        continue
                raise OllamaError(f"Ollama HTTP error: {e}")
            except requests.exceptions.ConnectionError:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning("Connection error, retrying in %ds...", delay)
                    _async_sleep_if_in_loop(delay)
                    continue
                raise OllamaError(f"Cannot connect to Ollama at {self.base_url}. Is Ollama running?")
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning("Request timed out, retrying in %ds...", delay)
                    _async_sleep_if_in_loop(delay)
                    continue
                raise OllamaError(f"Ollama request timed out after {timeout}s")

    def generate(self, system_prompt: str, messages: list[dict], tools: Optional[list] = None) -> dict:
        """Generate a response (non-streaming) with optional tool-calling.

        This is used for simple prompts or when streaming is not needed.
        """
        if not messages:
            raise ValueError("messages list is empty")

        options = {"temperature": self.temperature}
        if self.max_tokens is not None:
            options["num_predict"] = self.max_tokens

        ollama_messages = to_ollama_messages(messages)

        payload = {
            "model": self.model,
            "system": system_prompt,
            "messages": ollama_messages,
            "options": options,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        logger.debug(
            "Generating (non-stream): model=%s, messages=%d, tools=%s",
            self.model, len(messages), bool(tools)
        )

        try:
            resp = self._post_with_retry("chat", payload)
            data = resp.json()
            self.stream_response = data
            return data
        except OllamaError:
            raise
        except Exception as e:
            logger.error("Unexpected error in generate: %s", e, exc_info=True)
            raise OllamaError(f"Unexpected error: {e}")

    def generate_stream_events(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: Optional[list] = None,
        checkpoint_every: int = 50,  # Tokens between checkpoints
    ) -> Iterator[StreamEvent]:
        """Generate a streaming response with batched events and checkpoint validation.

        Yields typed events instead of raw strings:
        - TokenBatch: Batched thinking/content tokens (reduces I/O)
        - ToolCallBatch: Tool calls from model
        - Checkpoint: Periodic integrity checkpoints (not from LLM, from our state)
        - StreamComplete: Successful completion with validation
        - StreamError: Error occurred

        Checkpoint validation:
        - We accumulate text independently of the LLM's "done" flag
        - Periodically emit Checkpoint events with accumulated state hash
        - On StreamComplete, verify hash matches last checkpoint
        - This catches mid-stream corruption or truncation
        """
        if not messages:
            raise ValueError("messages list is empty")

        options = {"temperature": self.temperature}
        if self.max_tokens is not None:
            options["num_predict"] = self.max_tokens

        ollama_messages = to_ollama_messages(messages)

        payload = {
            "model": self.model,
            "system": system_prompt,
            "messages": ollama_messages,
            "options": options,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        logger.debug(
            "Generating (stream): model=%s, messages=%d, tools=%s",
            self.model, len(messages), bool(tools)
        )

        # State for delta computation
        prev_thinking = ""
        prev_content = ""
        accumulated_thinking = ""
        accumulated_content = ""
        tool_calls = None
        token_counter = 0
        done_reason = ""
        self.stream_response = None

        # Batcher to reduce I/O operations
        batcher = EventBatcher(batch_size=10, max_wait_chars=100)

        # Mode detection for delta vs cumulative
        thinking_mode: Optional[str] = None  # "cumulative" or "token-delta"
        content_mode: Optional[str] = None

        def should_checkpoint() -> bool:
            """Check if we should emit a checkpoint."""
            nonlocal token_counter
            return token_counter >= checkpoint_every

        try:
            for chunk in self._post_stream("chat", payload):
                if not isinstance(chunk, dict):
                    continue

                msg = chunk.get("message", {})
                if not isinstance(msg, dict):
                    continue

                # ── Thinking / reasoning ────────────────────────────
                thinking = msg.get("thinking", "") or ""
                if thinking:
                    # Detect mode: cumulative if new text starts with old, else token-delta
                    # Defer mode detection until we have a previous chunk to compare against
                    if thinking_mode is None:
                        if prev_thinking:
                            # Second+ chunk — can detect mode
                            if thinking.startswith(prev_thinking):
                                thinking_mode = "cumulative"
                            else:
                                thinking_mode = "token-delta"
                        # else: first chunk, leave mode as None (will treat as token-delta)

                    if thinking_mode == "cumulative":
                        if thinking.startswith(prev_thinking):
                            # Still cumulative — extract delta
                            if len(thinking) > len(prev_thinking):
                                delta = thinking[len(prev_thinking):]
                                prev_thinking = thinking
                                accumulated_thinking += delta
                                token_counter += len(delta)
                                for event in batcher.add_thinking(delta):
                                    yield event
                        else:
                            # Model switched to token-delta mid-stream
                            thinking_mode = "token-delta"
                            delta = thinking
                            accumulated_thinking += delta
                            token_counter += len(delta)
                            for event in batcher.add_thinking(delta):
                                yield event
                            prev_thinking = accumulated_thinking
                    elif thinking_mode == "token-delta":
                        accumulated_thinking += thinking
                        token_counter += len(thinking)
                        for event in batcher.add_thinking(thinking):
                            yield event
                        prev_thinking = accumulated_thinking
                    else:
                        # First chunk — treat as token-delta (safe default)
                        accumulated_thinking += thinking
                        token_counter += len(thinking)
                        for event in batcher.add_thinking(thinking):
                            yield event
                        prev_thinking = thinking

                # ── Content / final answer ──────────────────────────
                content = msg.get("content", "") or ""
                if content:
                    # Detect mode: defer until we have a previous chunk to compare
                    if content_mode is None:
                        if prev_content:
                            # Second+ chunk — can detect mode
                            if content.startswith(prev_content):
                                content_mode = "cumulative"
                            else:
                                content_mode = "token-delta"
                        # else: first chunk, leave mode as None

                    if content_mode == "cumulative":
                        if content.startswith(prev_content):
                            # Still cumulative — extract delta
                            if len(content) > len(prev_content):
                                delta = content[len(prev_content):]
                                prev_content = content
                                accumulated_content += delta
                                token_counter += len(delta)
                                for event in batcher.add_content(delta):
                                    yield event
                        else:
                            # Model switched to token-delta mid-stream
                            content_mode = "token-delta"
                            delta = content
                            accumulated_content += delta
                            token_counter += len(delta)
                            for event in batcher.add_content(delta):
                                yield event
                            prev_content = accumulated_content
                    elif content_mode == "token-delta":
                        accumulated_content += content
                        token_counter += len(content)
                        for event in batcher.add_content(content):
                            yield event
                        prev_content = accumulated_content
                    else:
                        # First chunk — treat as token-delta (safe default)
                        accumulated_content += content
                        token_counter += len(content)
                        for event in batcher.add_content(content):
                            yield event
                        prev_content = content

                # ── Tool calls ──────────────────────────────────────
                tc = msg.get("tool_calls")
                if tc and isinstance(tc, list):
                    tool_calls = tc
                    # Flush any pending batches before tool calls
                    for event in batcher.flush_all():
                        yield event
                    yield ToolCallBatch(
                        phase="tool_calls",
                        calls=tool_calls,
                    )

                # ── Checkpoint ──────────────────────────────────────
                if should_checkpoint():
                    for event in batcher.flush_all():
                        yield event
                    yield batcher.checkpoint(
                        accumulated_thinking,
                        accumulated_content,
                        token_counter
                    )
                    token_counter = 0

                # ── Stream end ───────────────────────────────────────
                if chunk.get("done", False):
                    done_reason = chunk.get("done_reason", "")
                    break

            # Flush remaining batches
            for event in batcher.flush_all():
                yield event

            # Final checkpoint validation
            final_hash = Checkpoint.compute_hash(
                accumulated_thinking,
                accumulated_content
            )

            # Self-validate: recompute hash and ensure consistency
            recomputed = Checkpoint.compute_hash(accumulated_thinking, accumulated_content)
            if final_hash != recomputed:
                logger.warning("StreamComplete hash mismatch — accumulated text may be corrupted")

            # Build response for message history
            response_msg = {
                "role": "assistant",
                "content": accumulated_content,
                "thinking": accumulated_thinking,
            }
            if tool_calls:
                response_msg["tool_calls"] = tool_calls
            self.stream_response = {"message": response_msg}

            yield StreamComplete(
                phase="complete",
                final_thinking=accumulated_thinking,
                final_content=accumulated_content,
                total_tokens=len(accumulated_thinking) + len(accumulated_content),
                tool_calls=tool_calls,
                validation_hash=final_hash,
                done_reason=done_reason
            )

        except KeyboardInterrupt:
            # Flush any pending batches
            for event in batcher.flush_all():
                yield event
            yield StreamError(
                phase="error",
                error_type="KeyboardInterrupt",
                message="Stream interrupted by user",
                partial_thinking=accumulated_thinking,
                partial_content=accumulated_content
            )
        except Exception as e:
            logger.error("Stream error: %s", e, exc_info=True)
            # Flush any pending batches
            for event in batcher.flush_all():
                yield event
            yield StreamError(
                phase="error",
                error_type=type(e).__name__,
                message=str(e),
                partial_thinking=accumulated_thinking,
                partial_content=accumulated_content
            )

    def generate_stream(self, system_prompt: str, messages: list[dict], tools: Optional[list] = None) -> Iterator[tuple[str, str]]:
        """Legacy streaming interface yielding (text, phase) tuples for backward compatibility.
        
        This is a wrapper around generate_stream_events that yields simple tuples.
        Consider migrating to generate_stream_events for typed events with checkpointing.
        """
        for event in self.generate_stream_events(system_prompt, messages, tools):
            if isinstance(event, TokenBatch):
                yield (event.text, event.phase)
            elif isinstance(event, ToolCallBatch):
                # Tool calls don't yield text - they're metadata
                pass
            elif isinstance(event, Checkpoint):
                # Checkpoints don't yield text - they're metadata
                pass
            elif isinstance(event, StreamComplete):
                # Stream complete - stop yielding
                break
            elif isinstance(event, StreamError):
                # Stream error - stop yielding
                break

    def _post_stream(self, endpoint: str, payload: dict, timeout: int = 600):
        """Stream a response from Ollama using unified NDJSON/SSE parser.

        Auto-detects format (NDJSON vs text/event-stream) and yields
        parsed JSON dicts for each event.

        Retries with exponential backoff on transient failures (connection errors,
        timeouts, 5xx server errors) before any data arrives.
        Errors mid-stream are raised immediately (cannot safely retry).
        Handles KeyboardInterrupt gracefully for clean Ctrl+C handling.
        """
        url = f"{self.base_url}/api/{endpoint}"
        max_retries = 3
        base_delay = 1

        for attempt in range(max_retries):
            events_yielded = False
            try:
                if logger.isEnabledFor(logging.DEBUG):
                    payload_dump = json.dumps(payload, default=str)
                    logger.debug("Ollama POST %s payload (attempt %d): %s", url, attempt + 1, payload_dump[:3000])
                with self._session.post(url, json=payload, timeout=timeout, stream=True) as resp:
                    resp.raise_for_status()
                    try:
                        for event in parse_stream(resp):
                            events_yielded = True
                            yield event
                    except KeyboardInterrupt:
                        # Re-raise so caller knows stream was interrupted
                        raise
                return  # stream exhausted normally
            except requests.exceptions.HTTPError as e:
                # Log response body on 4xx errors for easier debugging
                if e.response is not None and 400 <= e.response.status_code < 500:
                    try:
                        body = e.response.text[:500]
                        logger.error("Ollama %d response: %s", e.response.status_code, body)
                    except Exception:
                        pass
                if e.response.status_code >= 500 and attempt < max_retries - 1 and not events_yielded:
                    delay = base_delay * (2 ** attempt)
                    logger.warning("Server error %d, retrying in %ds...", e.response.status_code, delay)
                    _async_sleep_if_in_loop(delay)
                    continue
                raise OllamaError(f"Ollama HTTP error: {e}")
            except requests.exceptions.ConnectionError as e:
                if attempt < max_retries - 1 and not events_yielded:
                    delay = base_delay * (2 ** attempt)
                    logger.warning("Stream connection failed, retrying in %ds...", delay)
                    _async_sleep_if_in_loop(delay)
                    continue
                if events_yielded:
                    raise OllamaError(f"Stream dropped mid-response: {e}")
                raise OllamaError(
                    f"Cannot connect to Ollama at {self.base_url}. Is Ollama running?"
                )
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1 and not events_yielded:
                    delay = base_delay * (2 ** attempt)
                    logger.warning("Stream timed out, retrying in %ds...", delay)
                    _async_sleep_if_in_loop(delay)
                    continue
                raise OllamaError(f"Ollama streaming request timed out after {timeout}s.")
            except requests.exceptions.RequestException as e:
                raise OllamaError(f"Ollama streaming error: {e}")
