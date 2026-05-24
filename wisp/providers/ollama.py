"""Ollama-backed provider implementation."""

from __future__ import annotations

from typing import Iterator, Optional

from wisp.ollama_client import OllamaClient

from .protocol import Provider


class OllamaProvider(Provider):
    """Adapter that exposes the existing Ollama client via the Provider protocol."""

    def __init__(self, config=None, base_url: str = "", model: str = ""):
        if config is not None:
            self._client = OllamaClient(config)
            self.base_url = getattr(config, "ollama_url", "http://localhost:11434")
            self.model = getattr(config, "model", "")
        else:
            # Direct instantiation for testing/new code
            self._client = None
            self.base_url = base_url
            self.model = model

    def check_health(self) -> bool:
        if self._client is None:
            return False
        return self._client.check_health()

    def health_check(self) -> dict:
        """Check provider health per Provider protocol."""
        try:
            if self._client is not None:
                healthy = self._client.check_health()
                return {"status": "healthy" if healthy else "unhealthy"}
            # Try direct HTTP check via _get
            self._get("/api/tags")
            return {"status": "healthy"}
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc)}

    def list_models(self) -> list[dict]:
        if self._client is not None:
            models = self._client.list_models()
            return [{"id": m.get("name", ""), "name": m.get("name", "")} for m in models]
        # Direct HTTP fallback
        try:
            data = self._get("/api/tags")
            return [{"id": m.get("name", ""), "name": m.get("name", "")} for m in data.get("models", [])]
        except Exception:
            return []

    def get_context_length(self) -> int:
        if self._client is not None:
            return self._client.get_context_length()
        return 128000  # default

    def get_model_info(self, model: str) -> dict:
        """Get model info per Provider protocol."""
        try:
            if self._client is not None:
                ctx = self._client.get_context_length()
                return {"id": model, "context_length": ctx}
            import requests
            resp = requests.post(
                f"{self.base_url}/api/show",
                json={"name": model},
                timeout=5,
            )
            data = resp.json()
            return {
                "id": model,
                "context_length": data.get("context_length", 128000),
            }
        except Exception:
            return {"id": model, "context_length": 128000}

    def generate(self, system_prompt: str, messages: list[dict], tools: Optional[list] = None) -> dict:
        if self._client is not None:
            return self._client.generate(system_prompt, messages, tools)
        raise NotImplementedError("Direct generate not implemented without client")

    def generate_stream_events(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: Optional[list] = None,
        checkpoint_every: int = 50,
    ) -> Iterator:
        if self._client is not None:
            return self._client.generate_stream_events(system_prompt, messages, tools, checkpoint_every)
        # Direct HTTP fallback for testing
        return self._generate_stream_events_direct(system_prompt, messages, tools)

    def _generate_stream_events_direct(self, system_prompt: str, messages: list[dict], tools: Optional[list] = None):
        """Direct HTTP implementation for when client is not available."""
        try:
            payload = {
                "model": self.model,
                "messages": [{"role": "system", "content": system_prompt}] + messages,
                "stream": True,
            }
            if tools:
                payload["tools"] = tools

            resp = self._stream_post("/api/chat", payload)
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    import json
                    data = json.loads(line)
                    if data.get("done"):
                        yield {"type": "done", "done_reason": data.get("done_reason", "")}
                        break
                    msg = data.get("message", {})
                    if msg.get("tool_calls"):
                        for tc in msg["tool_calls"]:
                            func = tc.get("function", {})
                            yield {
                                "type": "tool_call",
                                "name": func.get("name", ""),
                                "arguments": func.get("arguments", {}),
                            }
                    elif msg.get("content"):
                        yield {"type": "content", "text": msg["content"]}
                except json.JSONDecodeError:
                    continue
        except Exception as exc:
            yield {"type": "error", "message": str(exc)}

    def _stream_post(self, endpoint: str, json_payload: dict):
        import requests
        return requests.post(f"{self.base_url}{endpoint}", json=json_payload, stream=True, timeout=60)

    @property
    def stream_response(self) -> Optional[dict]:
        if self._client is not None:
            return self._client.stream_response
        return None

    @stream_response.setter
    def stream_response(self, value: Optional[dict]) -> None:
        if self._client is not None:
            self._client.stream_response = value

    # Internal helpers for testing
    def _post(self, endpoint: str, json: dict | None = None) -> dict:
        import requests
        resp = requests.post(f"{self.base_url}{endpoint}", json=json, timeout=5)
        return resp.json()

    def _get(self, endpoint: str) -> dict:
        import requests
        resp = requests.get(f"{self.base_url}{endpoint}", timeout=5)
        return resp.json()

    def __getattr__(self, name: str):
        """Delegate unknown attributes to the underlying OllamaClient.

        Preserves backward compatibility for methods like ``get_model_info()``
        that exist on the client but are not part of the BaseProvider contract.
        """
        return getattr(self._client, name)
