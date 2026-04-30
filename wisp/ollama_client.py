"""Ollama API client for Wisp — handles model inference with tool-calling support.

Production-hardened with:
- Retry with exponential backoff for transient failures
- Proper request timeouts
- Connection pooling via requests.Session
- Structured logging
"""

import logging
import time
from typing import Optional

import requests
from wisp.config import WispConfig
from wisp.stream_parser import parse_stream, EventStreamError

logger = logging.getLogger(__name__)


class OllamaError(Exception):
    """Raised when Ollama API calls fail after all retries."""
    pass


class OllamaClient:
    """Minimal client for Ollama's API, optimized for tool-calling models."""

    def __init__(self, config: WispConfig):
        self.base_url = config.ollama_url.rstrip("/")
        self.model = config.model
        self.temperature = config.temperature
        self.max_tokens = config.max_tokens

        # Reusable session for connection pooling
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

        # Retry settings
        self._max_retries = 3
        self._base_delay = 1.0  # seconds
        self._max_delay = 10.0

    def _request(self, method: str, endpoint: str, timeout: int = 120, **kwargs) -> requests.Response:
        """Make an HTTP request with retry and exponential backoff."""
        url = f"{self.base_url}/api/{endpoint}"
        last_exc = None

        for attempt in range(1, self._max_retries + 1):
            try:
                resp = self._session.request(method, url, timeout=timeout, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.exceptions.ConnectionError as e:
                last_exc = OllamaError(
                    f"Cannot connect to Ollama at {self.base_url}. "
                    f"Is Ollama running? (ollama serve)"
                )
                logger.warning("Connection attempt %d/%d failed: %s", attempt, self._max_retries, e)
            except requests.exceptions.Timeout as e:
                last_exc = OllamaError(
                    f"Ollama request timed out after {timeout}s. "
                    f"The model may still be loading or the prompt is too large."
                )
                logger.warning("Timeout attempt %d/%d: %s", attempt, self._max_retries, e)
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else "?"
                body = e.response.text[:500] if e.response is not None else ""
                if status in (429, 502, 503, 504):
                    last_exc = OllamaError(f"Ollama server error (HTTP {status}): {body}")
                    logger.warning("Retryable HTTP %s on attempt %d/%d", status, attempt, self._max_retries)
                else:
                    raise OllamaError(f"Ollama API error (HTTP {status}): {body}")
            except requests.exceptions.RequestException as e:
                last_exc = OllamaError(f"Ollama request failed: {e}")
                logger.warning("Request error attempt %d/%d: %s", attempt, self._max_retries, e)

            # Backoff before retrying
            if attempt < self._max_retries:
                delay = min(self._base_delay * (2 ** (attempt - 1)), self._max_delay)
                logger.debug("Retrying in %.1fs...", delay)
                time.sleep(delay)

        raise last_exc or OllamaError("Ollama request failed after all retries")

    def _post(self, endpoint: str, payload: dict, timeout: int = 120) -> dict:
        """Make a POST request and return JSON."""
        resp = self._request("POST", endpoint, timeout=timeout, json=payload)
        return resp.json()

    def _post_stream(self, endpoint: str, payload: dict, timeout: int = 300):
        """Stream a response from Ollama using unified NDJSON/SSE parser.

        Auto-detects format (NDJSON vs text/event-stream) and yields
        parsed JSON dicts for each event.

        Retries once on connection/timeout errors before any data arrives.
        Errors mid-stream are raised immediately (cannot safely retry).
        """
        url = f"{self.base_url}/api/{endpoint}"
        first_attempt = True
        while True:
            try:
                with self._session.post(url, json=payload, timeout=timeout, stream=True) as resp:
                    resp.raise_for_status()
                    for event in parse_stream(resp):
                        yield event
                    return  # stream exhausted normally
            except requests.exceptions.ConnectionError:
                if first_attempt:
                    first_attempt = False
                    logger.warning("Stream connection failed, retrying once...")
                    time.sleep(1)
                    continue
                raise OllamaError(
                    f"Cannot connect to Ollama at {self.base_url}. Is Ollama running?"
                )
            except requests.exceptions.Timeout:
                if first_attempt:
                    first_attempt = False
                    logger.warning("Stream timed out, retrying once...")
                    time.sleep(1)
                    continue
                raise OllamaError(f"Ollama streaming request timed out after {timeout}s.")
            except requests.exceptions.RequestException as e:
                raise OllamaError(f"Ollama streaming error: {e}")

    def check_health(self) -> bool:
        """Check if Ollama is running and the model is available."""
        try:
            resp = self._session.get(f"{self.base_url}/api/tags", timeout=5)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            available = [m["name"] for m in models]
            model_available = any(
                self.model in m or m.startswith(self.model.split(":")[0])
                for m in available
            )
            if not model_available:
                logger.warning("Model '%s' not found among %d available", self.model, len(available))
                print(
                    f"⚠ Model '{self.model}' not found. "
                    f"Available: {', '.join(available[:10])}"
                )
                print(f"  Run: ollama pull {self.model}")
                return False
            logger.info("Health check OK — %s connected, model '%s' available", self.base_url, self.model)
            return True
        except requests.exceptions.ConnectionError:
            print(f"✗ Cannot reach Ollama at {self.base_url}")
            print(f"  Start with: ollama serve")
            return False
        except requests.exceptions.RequestException as e:
            logger.error("Health check failed: %s", e)
            print(f"✗ Ollama health check failed: {e}")
            return False

    def list_models(self) -> list[dict]:
        """List available models from Ollama."""
        resp = self._session.get(f"{self.base_url}/api/tags", timeout=10)
        resp.raise_for_status()
        return resp.json().get("models", [])

    def generate(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
    ) -> dict:
        """Send a chat request to Ollama (non-streaming). Returns the full response dict."""
        if not messages:
            raise ValueError("Cannot generate: messages list is empty")

        payload = {
            "model": self.model,
            "system": system_prompt,
            "messages": messages,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        logger.debug(
            "Generating (non-stream): model=%s, messages=%d, tools=%s",
            self.model, len(messages), bool(tools),
        )
        return self._post("chat", payload)

    # ── Streaming ─────────────────────────────────────────────────────

    def generate_stream(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
    ):
        """Send a chat request with streaming.

        Yields (text, kind) tuples where kind is one of:
        - "thinking" — reasoning/chain-of-thought text (DeepSeek/R1 style)
        - "content"  — final answer text

        After the generator is exhausted, the assembled full response is in
        ``self.stream_response`` with keys: message.content, message.thinking,
        message.tool_calls.

        Important: Ollama sends cumulative text in each chunk, so we compute
        deltas internally.  The consumer can simply stream every yielded item.
        """
        if not messages:
            raise ValueError("Cannot generate: messages list is empty")

        payload = {
            "model": self.model,
            "system": system_prompt,
            "messages": messages,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        logger.debug(
            "Generating (stream): model=%s, messages=%d, tools=%s",
            self.model, len(messages), bool(tools),
        )

        # ------------------------------------------------------------------
        # Ollama can return text in two modes:
        #
        # 1. Cumulative (local Ollama): each chunk contains the full text so
        #    far.  Delta = new[len(prev):].
        #
        # 2. Token-delta (Ollama cloud / provider proxy): each chunk contains
        #    only the new token.  The chunk IS the delta.
        #
        # We handle both transparently: if the new text starts with the previous
        # and is longer → cumulative (extract delta). Otherwise → token-delta
        # (yield directly, accumulate via +=).
        # ------------------------------------------------------------------
        prev_thinking = ""
        prev_content = ""
        tool_calls = None
        self.stream_response = None

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
                    if not prev_thinking:
                        prev_thinking = thinking
                        yield (thinking, "thinking")
                    elif thinking.startswith(prev_thinking):
                        if len(thinking) > len(prev_thinking):
                            delta = thinking[len(prev_thinking):]
                            prev_thinking = thinking
                            yield (delta, "thinking")
                    else:
                        yield (thinking, "thinking")
                        prev_thinking += thinking

                # ── Content / final answer ──────────────────────────
                content = msg.get("content", "") or ""
                if content:
                    if not prev_content:
                        prev_content = content
                        yield (content, "content")
                    elif content.startswith(prev_content):
                        if len(content) > len(prev_content):
                            delta = content[len(prev_content):]
                            prev_content = content
                            yield (delta, "content")
                    else:
                        yield (content, "content")
                        prev_content += content

                # ── Tool calls ──────────────────────────────────────
                tc = msg.get("tool_calls")
                if tc and isinstance(tc, list):
                    tool_calls = tc

                if chunk.get("done", False):
                    break
        finally:
            # Build and stash the full response for message history
            response_msg = {
                "role": "assistant",
                "content": prev_content,
                "thinking": prev_thinking,
            }
            if tool_calls:
                response_msg["tool_calls"] = tool_calls
            self.stream_response = {"message": response_msg}
