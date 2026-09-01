"""OpenAI-compatible provider implementation.

Supports any service that implements the OpenAI Chat Completions API:
- OpenAI (api.openai.com)
- Azure OpenAI (openai.azure.com)
- Groq (api.groq.com/openai)
- Together (api.together.xyz/v1)
- OpenRouter (openrouter.ai/api/v1)
- LiteLLM proxy (localhost:4000)
- Any OpenAI-compatible endpoint

Configuration:
    WISP_PROVIDER=openai
    WISP_MODEL=gpt-4o
    WISP_API_KEY=sk-...
    WISP_API_BASE=https://api.openai.com/v1  (optional, defaults to OpenAI)

Streaming tool calls are handled via the OpenAI streaming format where
tool_calls arrive as deltas that must be accumulated across chunks.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Iterator, Optional

from .protocol import Provider

logger = logging.getLogger(__name__)

_DEFAULT_API_BASE = "https://api.openai.com/v1"


def _translate_nvidia_404(body: str, requested_model: str) -> str:
    """Turn NVIDIA NIM's misleading "Function X not found" into a clear message.

    NVIDIA's gateway returns 404 with body shape
    ``{"status":404,"title":"Not Found","detail":"Function '<uuid>':
    Not found for account '<acct>'"}`` when the requested model is not
    entitled for the calling account. The literal string "Function" makes
    users think a tool wiring or function-calling is broken. Translate
    the detail into a sentence that names the model and the cause.

    Returns the translated body string, or "" if the body is not the
    expected shape (caller falls back to the raw body).
    """
    import json as _json
    import re as _re

    try:
        parsed = _json.loads(body)
    except (ValueError, TypeError):
        return ""
    detail = str(parsed.get("detail", ""))
    m = _re.match(r"^Function '([^']+)':\s*Not found for account '([^']+)'", detail)
    if not m:
        return ""
    model_id = requested_model or m.group(1)
    return (
        f"{parsed.get('title', 'Not Found')}: model '{model_id}' is not "
        f"entitled for this NVIDIA account (account id {m.group(2)}). "
        f"Run /provider nvidia to pick a model your account has access to, "
        f"or check https://build.nvidia.com for the entitled list."
    )


class OpenAIProvider(Provider):
    """OpenAI-compatible provider using the Chat Completions API."""

    def __init__(self, config=None, base_url: str = "", model: str = "", api_key: str = ""):
        if config is not None:
            self.api_key = getattr(config, "api_key", "") or os.environ.get("OPENAI_API_KEY", "")
            # Provider-specific default base when config.api_base is empty
            cfg_base = getattr(config, "api_base", "") or ""
            if not cfg_base:
                prov = str(getattr(config, "provider", "") or "").lower()
                if prov == "openrouter":
                    cfg_base = "https://openrouter.ai/api/v1"
                elif prov == "nvidia":
                    cfg_base = "https://integrate.api.nvidia.com/v1"
            self.api_base = (
                cfg_base
                or os.environ.get("WISP_API_BASE", "")
                or os.environ.get("OPENAI_API_BASE", "")
                or _DEFAULT_API_BASE
            )
            self.model = getattr(config, "model", "gpt-4o")
            self.temperature = getattr(config, "temperature", 0.2)
            self.max_tokens = getattr(config, "max_tokens", None)
        else:
            self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
            self.api_base = base_url or _DEFAULT_API_BASE
            self.model = model
            self.temperature = 0.2
            self.max_tokens = None

        self.api_base = self.api_base.rstrip("/")
        self._stream_response: Optional[dict] = None

    # ── Provider protocol ────────────────────────────────────────────

    def generate_stream_events(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        checkpoint_every: int = 50,
    ) -> Iterator[dict, None, None]:
        """Stream events from the OpenAI Chat Completions API.

        Yields standardized event dicts:
          - {"type": "content", "text": "..."}
          - {"type": "tool_call", "name": "...", "arguments": {...}}
          - {"type": "tool_calls", "calls": [...]}  (batch, for multi-tool turns)
          - {"type": "thinking", "text": "..."}     (reasoning models, if available)
          - {"type": "done", "done_reason": "..."}
          - {"type": "error", "message": "..."}
        """
        import requests

        # ── Build payload with pruning guard
        # Large historical tool results (30+ calls) would otherwise stall
        # the socket write (60s timeout) — prune before serialization.
        # Primary pruning happens in stateless.py, but this is a safety net
        # for direct provider calls.
        _pruned_messages = messages
        try:
            from wisp.core.context_pruner import prune_messages as _prune

            # Quick size estimate — only prune if over 150KB (well below 200KB ceiling)
            # to avoid overhead for small turns
            _est = len(json.dumps(messages).encode("utf-8", errors="ignore"))
            if _est > 150000:
                _pruned_messages = _prune(messages)
                logger.debug("Pre-pruned messages from %d to %d bytes", _est, len(json.dumps(_pruned_messages).encode("utf-8", errors="ignore")))
        except Exception:
            _pruned_messages = messages

        payload = self._build_payload(system_prompt, _pruned_messages, tools, stream=True)

        # Defensive re-check: if payload still >200KB, prune again and rebuild
        try:
            from wisp.core.context_pruner import prune_messages as _prune2

            _payload_str = json.dumps(payload)
            if len(_payload_str.encode("utf-8", errors="ignore")) > 200000:
                _pruned2 = _prune2(messages)
                payload = self._build_payload(system_prompt, _pruned2, tools, stream=True)
                logger.debug(
                    "Pruned payload from %d to %d bytes before dispatch",
                    len(_payload_str),
                    len(json.dumps(payload).encode("utf-8", errors="ignore")),
                )
        except Exception:
            pass

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            # Hardened transport: granular timeouts (connect 15, write 60, read 120, pool 30)
            # with TCP keepalive and retry for write timeouts / RemoteProtocolError.
            # Use `requests` module directly (not a Session) so tests that patch
            # `requests.post` continue to work; hardened_post will handle pooling
            # and retry internally and delegate to the patched function.
            try:
                from wisp.core.transport import hardened_post, HARDENED_TIMEOUT

                resp = hardened_post(
                    requests,
                    f"{self.api_base}/chat/completions",
                    json=payload,
                    headers=headers,
                    stream=True,
                    timeout=HARDENED_TIMEOUT,
                    max_attempts=3,
                )
            except ImportError:
                # Fallback to raw requests with hardened timeout tuple
                resp = requests.post(
                    f"{self.api_base}/chat/completions",
                    json=payload,
                    headers=headers,
                    stream=True,
                    # (connect, read): read is BETWEEN BYTES, not total — a
                    # stalled endpoint must raise fast so the caller's retry
                    # gets a live attempt instead of ticking to its own
                    # timeout on a dead socket. Reasoning models stream
                    # reasoning deltas continuously, so 60s of byte-silence
                    # means the stream is dead, not thinking.
                    # Write timeout is folded into read for requests (60s)
                    timeout=(15, 120),
                )
            if resp.status_code != 200:
                body = resp.text[:500]
                # Make 401 actionable: tell the user how to fix it via /provider
                # and that the key is saved to .env so they don't have to re-enter
                # it every REPL restart (taki baar baar change na karna pade).
                hint = ""
                if resp.status_code == 401:
                    hint = " — check WISP_API_KEY / provider API key. Run /provider <name> and enter the key when prompted; it will be verified and saved to ~/.config/wisp/config.json and ./.env"
                # NVIDIA NIM gateway returns 404 with body
                #   {"status":404,"title":"Not Found",
                #    "detail":"Function '<uuid>': Not found for account '<id>'"}
                # where "Function" is actually the model id, and the cause is
                # that the model is not entitled for this account. Without
                # this translation the user sees "Function '...': Not found"
                # and misdiagnoses it as a tool/wiring bug.
                if resp.status_code == 404 and "integrate.api.nvidia.com" in self.api_base:
                    translated = _translate_nvidia_404(body, self.model)
                    if translated:
                        body = translated
                yield {
                    "type": "error",
                    "message": f"API error {resp.status_code}: {body}{hint}",
                    # Machine-readable so the guarded stream can retry
                    # transient statuses (429/5xx) instead of surfacing
                    # them as fatal turn errors.
                    "status": resp.status_code,
                }
                return

            self._stream_response = {"status_code": resp.status_code}

            # Accumulate tool call deltas across chunks. Counters ride a
            # terminal stream_stats event so callers can distinguish
            # "server sent nothing" (throttle) from "sent unusable chunks"
            # without shared mutable provider state (subagents run parallel
            # turns on ONE provider instance).
            sse_lines = 0
            usable_deltas = 0
            empty_choice_chunks = 0
            tool_call_accum: dict[int, dict] = {}
            tool_calls_yielded = False
            done_reason = "stop"

            for line in resp.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
                if not line_str.startswith("data: "):
                    continue
                sse_lines += 1
                data_str = line_str[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choices = chunk.get("choices", [])
                if not choices:
                    empty_choice_chunks += 1
                    continue
                choice = choices[0]
                delta = choice.get("delta", {})
                finish_reason = choice.get("finish_reason")

                # Content delta
                content = delta.get("content")
                if content:
                    usable_deltas += 1
                    yield {"type": "content", "text": content}

                # Reasoning/thinking delta (for o1/o3-style models)
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning:
                    usable_deltas += 1
                    yield {"type": "thinking", "text": reasoning}

                # Tool call deltas — accumulate by index
                tc_deltas = delta.get("tool_calls")
                if tc_deltas:
                    usable_deltas += 1
                    for tc_delta in tc_deltas:
                        idx = tc_delta.get("index", 0)
                        if idx not in tool_call_accum:
                            tool_call_accum[idx] = {
                                "id": tc_delta.get("id", ""),
                                "name": "",
                                "arguments": "",
                            }
                        acc = tool_call_accum[idx]
                        if tc_delta.get("id"):
                            acc["id"] = tc_delta["id"]
                        func = tc_delta.get("function", {})
                        if func.get("name"):
                            acc["name"] = func["name"]
                        if func.get("arguments"):
                            acc["arguments"] += func["arguments"]

                if finish_reason:
                    done_reason = finish_reason
                    # Some providers (NVIDIA NIM, some OpenRouter gateways)
                    # return `stop` even when they streamed tool_calls deltas;
                    # the canonical `tool_calls` finish is ideal but not
                    # reliable. If we accumulated any tool call, emit it
                    # regardless of the finish string — otherwise a valid
                    # write_file is silently dropped and the turn ends with
                    # 0 tools (live repro: 18k T-cell write via nvidia).
                    should_emit = (finish_reason == "tool_calls" or bool(tool_call_accum)) and not tool_calls_yielded
                    if should_emit:
                        tool_calls_yielded = True
                        calls = []
                        for idx in sorted(tool_call_accum.keys()):
                            acc = tool_call_accum[idx]
                            raw_args = acc["arguments"]
                            try:
                                parsed_args = json.loads(raw_args) if raw_args else {}
                            except json.JSONDecodeError:
                                parsed_args = {"_raw": raw_args}
                            calls.append({
                                "id": acc["id"],
                                "type": "function",
                                "function": {
                                    "name": acc["name"],
                                    "arguments": parsed_args,
                                },
                            })
                        if len(calls) == 1:
                            yield {
                                "type": "tool_call",
                                "name": calls[0]["function"]["name"],
                                "arguments": calls[0]["function"]["arguments"],
                                "id": calls[0]["id"],
                            }
                        elif calls:
                            yield {"type": "tool_calls", "calls": calls}

            yield {
                "type": "stream_stats",
                "sse_lines": sse_lines,
                "usable_deltas": usable_deltas,
                "empty_choice_chunks": empty_choice_chunks,
                "finish_reason": done_reason,
            }
            yield {"type": "done", "done_reason": done_reason}

        except requests.exceptions.ConnectionError as exc:
            yield {"type": "error", "message": f"Connection error: {exc}", "status": 500}
        except requests.exceptions.Timeout as exc:
            yield {"type": "error", "message": f"Request timed out: {exc}", "status": 500}
        except BaseException as exc:
            # Check for httpcore.WriteTimeout, RemoteProtocolError, etc.
            try:
                from wisp.core.transport import is_transient_error

                if is_transient_error(exc):
                    yield {"type": "error", "message": f"Transient {type(exc).__name__}: {exc}", "status": 500}
                    return
            except ImportError:
                pass
            logger.exception("OpenAI provider stream failed")
            yield {"type": "error", "message": str(exc)}

    def _auth_headers(self) -> dict[str, str]:
        """Authorization header(s) for API calls; subclass hook."""
        return {"Authorization": f"Bearer {self.api_key}"}

    def check_health(self) -> bool:
        """Base-contract health gate: reachable + configured model usable."""
        info = self.health_check()
        if info.get("status") != "healthy":
            return False
        # Reachable is necessary but not sufficient: the configured model
        # must actually exist on this endpoint.
        try:
            models = {m.get("id") or m.get("name") for m in self.list_models()}
            if models and self.model and self.model not in models:
                return False
        except Exception:
            pass  # model listing optional; reachability already proven
        return True

    def health_check(self) -> dict[str, Any]:
        """Check provider health by attempting to list models."""
        import requests

        # Use hardened timeouts for health check as well (connect 15, read 120)
        try:
            try:
                from wisp.core.transport import HARDENED_TIMEOUT, hardened_get

                resp = hardened_get(
                    requests,
                    f"{self.api_base}/models",
                    headers=self._auth_headers(),
                    timeout=HARDENED_TIMEOUT,
                    max_attempts=3,
                )
            except ImportError:
                resp = requests.get(
                    f"{self.api_base}/models",
                    headers=self._auth_headers(),
                    timeout=10,
                )
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc)}
        try:
            if resp.status_code != 200:
                return {"status": "unhealthy", "error": f"HTTP {resp.status_code}"}
            data = resp.json()
            return {"status": "healthy", "models": len(data.get("data", []))}
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc)}

    def list_models(self) -> list[dict[str, Any]]:
        """List available models from the API."""
        import requests

        try:
            try:
                from wisp.core.transport import HARDENED_TIMEOUT, hardened_get

                resp = hardened_get(
                    requests,
                    f"{self.api_base}/models",
                    headers=self._auth_headers(),
                    timeout=HARDENED_TIMEOUT,
                    max_attempts=3,
                )
            except ImportError:
                resp = requests.get(
                    f"{self.api_base}/models",
                    headers=self._auth_headers(),
                    timeout=10,
                )
        except Exception:
            return []
        try:
            if resp.status_code != 200:
                return []
            data = resp.json()
            return [
                {"id": m.get("id", ""), "name": m.get("id", "")}
                for m in data.get("data", [])
            ]
        except Exception:
            return []

    def get_model_info(self, model: str) -> dict[str, Any]:
        """Get model info. OpenAI doesn't expose context length via API, use known values."""
        known_context = {
            "gpt-4o": 128000,
            "gpt-4o-mini": 128000,
            "gpt-4-turbo": 128000,
            "gpt-4": 8192,
            "gpt-3.5-turbo": 16385,
            "o1": 200000,
            "o1-mini": 128000,
            "o3": 200000,
            "o3-mini": 200000,
            "o4-mini": 200000,
        }
        ctx = known_context.get(model, 128000)
        return {"id": model, "context_length": ctx}

    def close(self) -> None:
        """No persistent resources to close."""
        self._stream_response = None

    @property
    def stream_response(self) -> Optional[dict]:
        return self._stream_response

    @stream_response.setter
    def stream_response(self, value: Optional[dict]) -> None:
        self._stream_response = value

    # ── Internal helpers ─────────────────────────────────────────────

    def _build_payload(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict] | None,
        stream: bool = False,
    ) -> dict:
        """Build the OpenAI Chat Completions request payload."""
        # Prepend system message
        api_messages = [{"role": "system", "content": system_prompt}] + list(messages)

        # Normalize tool messages: OpenAI expects role "tool" with tool_call_id
        normalized = []
        for msg in api_messages:
            role = msg.get("role", "")
            if role == "tool":
                normalized.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "content": str(msg.get("content", "")),
                })
            elif role == "assistant" and msg.get("tool_calls"):
                # OpenAI rejects dict arguments; persisted sessions must
                # never leak them through even if upstream normalization missed.
                calls = []
                for tc in msg["tool_calls"]:
                    func = dict(tc.get("function", {}))
                    if not isinstance(func.get("arguments"), str):
                        func["arguments"] = json.dumps(func.get("arguments", {}))
                    calls.append({**tc, "function": func})
                normalized.append({**msg, "tool_calls": calls})
            else:
                normalized.append(msg)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": normalized,
            "temperature": self.temperature,
            "stream": stream,
        }

        if self.max_tokens and isinstance(self.max_tokens, int) and self.max_tokens > 0:
            # Cloud gateways (OpenRouter, NVIDIA) reject huge max_tokens with
            # 402 "can only afford N" — cap to a generous but credit-safe
            # value. Local Ollama ignores this field anyway.
            # OpenRouter's remaining-credit check is dynamic (e.g. 5403), so
            # use a conservative 4096 for openrouter to stay under the
            # typical free-tier balance; nvidia can use 16384.
            max_tok = int(self.max_tokens)
            if self.api_base == "https://openrouter.ai/api/v1" and max_tok > 4096:
                max_tok = 4096
            elif max_tok > 16384 and self.api_base in (
                "https://integrate.api.nvidia.com/v1",
            ):
                max_tok = 16384
            payload["max_tokens"] = max_tok

        if tools:
            payload["tools"] = self._convert_tools(tools)

        return payload

    @staticmethod
    def _convert_tools(wisp_tools: list[dict]) -> list[dict]:
        """Convert Wisp tool schemas to OpenAI function tool format.

        Wisp tools are already in OpenAI format:
        {"type": "function", "function": {"name", "description", "parameters"}}
        but we normalize to be safe.
        """
        converted = []
        for tool in wisp_tools:
            if tool.get("type") == "function" and "function" in tool:
                converted.append(tool)
            elif "function" in tool:
                converted.append({"type": "function", "function": tool["function"]})
            elif "name" in tool:
                converted.append({
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {}),
                    },
                })
        return converted
