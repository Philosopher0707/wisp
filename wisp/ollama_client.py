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
import re
import threading
import time
from typing import Any, Optional, Iterator

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


# ── Configuration incompatibility (ADR-0038 R5/R6) ─────────────────────
#
# Ollama rejects a request whose requested output budget exceeds the model's
# maximum output tokens. That is a CONFIGURATION incompatibility — permanent,
# and not a transient provider failure — and the rejection names the limit.
#
# ADR-0038 permits SURFACING that limit in the diagnostic and forbids deriving
# a capability from it. Nothing below caches the number, feeds it into a
# request, records it as model metadata, or persists it; it is parsed only to
# build the message, and the request is never retried, re-sent or modified.
#
# The pattern is anchored on the whole distinctive phrase rather than on a
# bare keyword: "maximum output tokens (N)" preceded by "exceeds". A generic
# HTTP 400 does not match, and neither does an unrelated mention of a token
# count.
_CAPACITY_REJECTION_RE = re.compile(
    r"exceeds\s+model['\u2019]s\s+maximum\s+output\s+tokens\s*\((\d+)\)",
    re.IGNORECASE,
)


class OllamaConfigurationError(OllamaError):
    """The request Wisp sent is incompatible with the provider's configuration.

    Permanent by construction: re-sending the identical request cannot succeed,
    so this is deliberately NOT retried and NOT transient (ADR-0038 R5/R7).
    It subclasses ``OllamaError``, so every existing handler keeps working; a
    caller that needs to tell "fix the configuration" apart from "try again"
    catches this type.
    """

    kind = "CONFIGURATION_INCOMPATIBILITY"


def _response_text(response: Any) -> str:
    """The response body, or ``""`` when it cannot be read.

    Defensive by design: a **closed** streamed response has no body at all, and
    a duck-typed test double may carry no ``.text``. Neither is an error worth
    raising over — the caller only ever uses this to build a message.
    """
    try:
        return response.text or ""
    except Exception:
        return ""


def _configuration_incompatibility(body: Any, configured: Any) -> Optional[str]:
    """Diagnostic when the provider rejected the output budget, else None.

    Returns a message naming the configured budget and the provider-reported
    limit, or ``None`` when this is not that specific rejection — every other
    HTTP error keeps its existing classification and message.

    *configured* being ``None`` returns ``None`` deliberately: with no budget
    sent there is no budget to exceed, so a claim that the user's configuration
    is at fault would be wrong.
    """
    if configured is None or not body:
        return None
    match = _CAPACITY_REJECTION_RE.search(body)
    if match is None:
        return None
    limit = match.group(1)
    return (
        f"Ollama rejected the request: the configured max_tokens ({configured}) "
        f"exceeds this model's maximum output tokens ({limit}). This is a "
        f"CONFIGURATION INCOMPATIBILITY, not a transient provider failure: the "
        f"request is not retried and was not modified. Lower `max_tokens` to at "
        f"most {limit} for this model, or set it to null to let the provider "
        f"choose the budget. The limit above is reported by the provider for "
        f"this message only — Wisp does not record it or use it on later requests."
    )



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
        if session is not None:
            self._session = session
            # Injected sessions are caller-owned (shared pools, test fakes):
            # close() must not close what it does not own (Phase 2.1, D4).
            self._owns_session = False
        else:
            try:
                from wisp.core.transport import get_hardened_session

                self._session = get_hardened_session()
            except ImportError:
                self._session = requests.Session()
            self._owns_session = True
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
        """Close the underlying requests session if owned.

        Injected (caller-owned) sessions are left open — the owner
        (CompositionRoot registry, test, or caller) closes them.
        """
        if self._session is not None and getattr(self, "_owns_session", True):
            self._session.close()
            self._session = None

    def check_health(self) -> bool:
        """Verify Ollama is running and the model is available."""
        try:
            # Hardened: explicit connect/read timeouts (write not needed for GET)
            try:
                from wisp.core.transport import HARDENED_TIMEOUT

                _timeout = (HARDENED_TIMEOUT.connect, HARDENED_TIMEOUT.read)
            except ImportError:
                _timeout = 5
            resp = self._session.get(f"{self.base_url}/api/tags", timeout=_timeout)
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
            try:
                from wisp.core.transport import HARDENED_TIMEOUT

                _timeout = (HARDENED_TIMEOUT.connect, HARDENED_TIMEOUT.read)
            except ImportError:
                _timeout = 5
            resp = self._session.get(f"{self.base_url}/api/tags", timeout=_timeout)
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

    def _post_with_retry(self, endpoint: str, payload: dict, timeout: Any = 600):
        """Make a POST request with exponential backoff retry.

        Retries on transient failures (5xx, connection errors, write timeouts,
        RemoteProtocolError) with hardened timeouts.
        """
        # Resolve timeout to hardened tuple if int passed
        try:
            from wisp.core.transport import HARDENED_TIMEOUT

            if isinstance(timeout, int):
                # For non-streaming POST, use (connect, read) where read is generous
                # Write is folded into read for requests
                timeout = (HARDENED_TIMEOUT.connect, HARDENED_TIMEOUT.read)
        except ImportError:
            # No fallback binding: the retry handler below re-imports as
            # _is_trans per attempt and degrades to requests-specific
            # checks when the import is unavailable.
            pass

        url = f"{self.base_url}/api/{endpoint}"
        max_retries = 3
        base_delay = 1  # seconds

        for attempt in range(max_retries):
            try:
                resp = self._session.post(url, json=payload, timeout=timeout)
                resp.raise_for_status()
                return resp
            except requests.exceptions.HTTPError as e:
                status = getattr(e.response, "status_code", None)
                try:
                    from wisp.core.transport import is_transient_status

                    if is_transient_status(status) and attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt) + __import__("random").uniform(0, 0.5)
                        logger.warning("Server error %d, retrying in %.2fs...", status, delay)
                        _async_sleep_if_in_loop(delay)
                        continue
                except ImportError:
                    pass
                if status is not None and status >= 500:
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning("Server error %d, retrying in %ds...", status, delay)
                        _async_sleep_if_in_loop(delay)
                        continue
                # ADR-0038 R5/R6 — placed after every retry branch so the retry
                # policy is untouched: a capacity rejection is a 400, which no
                # branch above retries, so this can only ever be reached for an
                # already-permanent status. This path is not streamed, so the
                # response body is still readable here.
                _config_err = _configuration_incompatibility(
                    _response_text(getattr(e, "response", None)), self.max_tokens)
                if _config_err is not None:
                    raise OllamaConfigurationError(_config_err)
                raise OllamaError(f"Ollama HTTP error: {e}")
            except BaseException as e:
                # Unified transient check: covers TimeoutError, ConnectionResetError,
                # httpcore.WriteTimeout, RemoteProtocolError, etc.
                try:
                    from wisp.core.transport import is_transient_error as _is_trans

                    if _is_trans(e) and attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt) + __import__("random").uniform(0, 0.5)
                        logger.warning("Transient %s, retrying in %.2fs: %s", type(e).__name__, delay, e)
                        _async_sleep_if_in_loop(delay)
                        continue
                except ImportError:
                    pass
                # Fallback for requests-specific
                if isinstance(e, requests.exceptions.ConnectionError):
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning("Connection error, retrying in %ds...", delay)
                        _async_sleep_if_in_loop(delay)
                        continue
                    raise OllamaError(f"Cannot connect to Ollama at {self.base_url}. Is Ollama running?")
                if isinstance(e, requests.exceptions.Timeout):
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning("Request timed out, retrying in %ds...", delay)
                        _async_sleep_if_in_loop(delay)
                        continue
                    raise OllamaError(f"Ollama request timed out after {timeout}s")
                raise

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
        # Prune historical tool results to prevent write timeout on large payloads
        try:
            from wisp.core.context_pruner import prune_messages
            from wisp.core.contracts import DEFAULT_PRUNE_POLICY as _OLLAMA_PRUNE_POLICY

            # Estimate payload size and prune if needed
            import json as _json

            _est = len(_json.dumps(ollama_messages).encode("utf-8", errors="ignore"))
            if _est > 150000:
                ollama_messages = prune_messages(ollama_messages, _OLLAMA_PRUNE_POLICY)  # type: ignore[arg-type]
                logger.debug("Pruned Ollama messages from %d to %d bytes", _est, len(_json.dumps(ollama_messages).encode("utf-8", errors="ignore")))
        except Exception:
            pass

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
        # Prune for write timeout budget (same as non-stream)
        try:
            from wisp.core.context_pruner import prune_messages
            from wisp.core.contracts import DEFAULT_PRUNE_POLICY as _OLLAMA_STREAM_PRUNE_POLICY

            import json as _json2

            _est2 = len(_json2.dumps(ollama_messages).encode("utf-8", errors="ignore"))
            if _est2 > 150000:
                ollama_messages = prune_messages(ollama_messages, _OLLAMA_STREAM_PRUNE_POLICY)  # type: ignore[arg-type]
                logger.debug("Pruned stream Ollama messages from %d to %d bytes", _est2, len(_json2.dumps(ollama_messages).encode("utf-8", errors="ignore")))
        except Exception:
            pass

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

    def _post_stream(self, endpoint: str, payload: dict, timeout: Any = 600):
        """Stream a response from Ollama using unified NDJSON/SSE parser.

        Auto-detects format (NDJSON vs text/event-stream) and yields
        parsed JSON dicts for each event.

        Retries with exponential backoff on transient failures (connection errors,
        timeouts, write timeouts, RemoteProtocolError, 5xx) before any data
        arrives. Errors mid-stream are raised immediately (cannot safely retry).
        Handles KeyboardInterrupt gracefully for clean Ctrl+C handling.

        Uses hardened timeouts: connect 15s, write 60s (large payloads),
        read 120s (between bytes), pool 30s, with TCP keepalive.
        """
        # Resolve timeout to hardened tuple
        try:
            from wisp.core.transport import HARDENED_TIMEOUT

            if isinstance(timeout, int):
                # Map int timeout to hardened read, keep write budget
                # For streaming, read is between bytes (120s), write is 60s for payload flush
                timeout = (HARDENED_TIMEOUT.connect, max(HARDENED_TIMEOUT.write, HARDENED_TIMEOUT.read))
            elif timeout is None:
                timeout = (HARDENED_TIMEOUT.connect, HARDENED_TIMEOUT.read)
        except ImportError:
            if timeout is None:
                timeout = 600

        url = f"{self.base_url}/api/{endpoint}"
        max_retries = 3
        base_delay = 1

        for attempt in range(max_retries):
            events_yielded = False
            # The error body is captured while the response is still OPEN.
            # `raise_for_status()` raises inside the `with`, so by the time the
            # handler runs the response has been closed and `response.text` is
            # empty — a streamed response's body is not buffered. ADR-0038 R6
            # needs the provider's message, so it is read here.
            error_body = ""
            try:
                if logger.isEnabledFor(logging.DEBUG):
                    payload_dump = json.dumps(payload, default=str)
                    logger.debug("Ollama POST %s payload (attempt %d): %s", url, attempt + 1, payload_dump[:3000])
                with self._session.post(url, json=payload, timeout=timeout, stream=True) as resp:
                    if resp.status_code >= 400:
                        error_body = _response_text(resp)
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
                # Log response body on 4xx errors for easier debugging.
                # Reads the body captured above: a closed streamed response has
                # none, so `e.response.text` here would always be empty.
                if e.response is not None and 400 <= e.response.status_code < 500:
                    if error_body:
                        logger.error("Ollama %d response: %s",
                                     e.response.status_code, error_body[:500])
                if e.response.status_code >= 500 and attempt < max_retries - 1 and not events_yielded:
                    delay = base_delay * (2 ** attempt)
                    logger.warning("Server error %d, retrying in %ds...", e.response.status_code, delay)
                    _async_sleep_if_in_loop(delay)
                    continue
                # ADR-0038 R5/R6 — after the 5xx retry branch, which a 400 cannot
                # enter, so the retry policy stays exactly as it was.
                _config_err = _configuration_incompatibility(error_body, self.max_tokens)
                if _config_err is not None:
                    raise OllamaConfigurationError(_config_err)
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
                # Check if this wrapped exception is actually transient (e.g., WriteTimeout wrapped)
                try:
                    from wisp.core.transport import is_transient_error

                    if is_transient_error(e) and attempt < max_retries - 1 and not events_yielded:
                        delay = base_delay * (2 ** attempt) + __import__("random").uniform(0, 0.5)
                        logger.warning("Transient %s, retrying in %.2fs: %s", type(e).__name__, delay, e)
                        _async_sleep_if_in_loop(delay)
                        continue
                except ImportError:
                    pass
                raise OllamaError(f"Ollama streaming error: {e}")
            except BaseException as e:
                # Catch-all for httpcore.WriteTimeout, RemoteProtocolError, etc.
                # These are not subclasses of requests.exceptions.RequestException
                try:
                    from wisp.core.transport import is_transient_error

                    if is_transient_error(e) and attempt < max_retries - 1 and not events_yielded:
                        delay = base_delay * (2 ** attempt) + __import__("random").uniform(0, 0.5)
                        logger.warning("Transient %s, retrying in %.2fs: %s", type(e).__name__, delay, e)
                        _async_sleep_if_in_loop(delay)
                        continue
                except ImportError:
                    pass
                raise
