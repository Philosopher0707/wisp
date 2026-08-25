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


class OpenAIProvider(Provider):
    """OpenAI-compatible provider using the Chat Completions API."""

    def __init__(self, config=None, base_url: str = "", model: str = "", api_key: str = ""):
        if config is not None:
            self.api_key = getattr(config, "api_key", "") or os.environ.get("OPENAI_API_KEY", "")
            self.api_base = (
                getattr(config, "api_base", "")
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

        payload = self._build_payload(system_prompt, messages, tools, stream=True)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
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
                timeout=(10, 60),
            )
            if resp.status_code != 200:
                body = resp.text[:500]
                yield {"type": "error", "message": f"API error {resp.status_code}: {body}"}
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
                    if finish_reason == "tool_calls" and not tool_calls_yielded:
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
            yield {"type": "error", "message": f"Connection error: {exc}"}
        except requests.exceptions.Timeout as exc:
            yield {"type": "error", "message": f"Request timed out: {exc}"}
        except Exception as exc:
            logger.exception("OpenAI provider stream failed")
            yield {"type": "error", "message": str(exc)}

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
        try:
            resp = requests.get(
                f"{self.api_base}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10,
            )
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
            resp = requests.get(
                f"{self.api_base}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10,
            )
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
            payload["max_tokens"] = self.max_tokens

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
