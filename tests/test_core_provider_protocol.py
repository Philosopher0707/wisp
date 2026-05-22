"""TDD for WispAgentCore using Provider protocol.

Verifies that WispAgentCore works with any Provider, not just Ollama.
"""

import pytest
from typing import Any


# ── Mock provider for testing ──────────────────────────────────────

class _MockProvider:
    """A mock provider that conforms to the Provider protocol."""

    def __init__(self, responses: list[dict] | None = None):
        self.responses = responses or []
        self.calls = []
        self.stream_response = None

    def generate_stream_events(self, system_prompt: str, messages: list[dict], tools: list | None = None, checkpoint_every: int = 50):
        self.calls.append((system_prompt, messages, tools))
        for resp in self.responses:
            yield resp
        # Set stream_response to simulate successful completion
        self.stream_response = {
            "message": {"role": "assistant", "content": "hello", "thinking": ""},
        }

    def check_health(self):
        return {"status": "healthy"}

    def list_models(self):
        return []

    def get_model_info(self, model: str):
        return {"id": model, "context_length": 128000}


# ═══════════════════════════════════════════════════════════════════
# 1. Core accepts any provider
# ═══════════════════════════════════════════════════════════════════

class TestCoreProviderProtocol:
    """WispAgentCore can work with any Provider."""

    def test_core_accepts_mock_provider(self):
        from wisp.core.agent import WispAgentCore
        from wisp.config import WispConfig

        config = WispConfig()
        config.model = "mock"
        provider = _MockProvider([
            {"type": "content", "text": "hello"},
            {"type": "done"},
        ])

        core = WispAgentCore(config=config, provider=provider)
        assert core.provider is provider

    def test_core_runs_with_mock_provider(self, tmp_path):
        from wisp.core.agent import WispAgentCore
        from wisp.config import WispConfig

        config = WispConfig()
        config.model = "mock"
        provider = _MockProvider([
            {"type": "content", "text": "hello"},
            {"type": "done"},
        ])

        core = WispAgentCore(config=config, provider=provider)
        core.messages = [{"role": "user", "content": "hi"}]
        # Use temp store to avoid db lock
        from wisp.infra.store import get_store
        core.session_mgr = get_store(str(tmp_path / "test.db"))

        import asyncio
        events = asyncio.run(self._collect_events(core))

        assert len(events) == 2
        # AgentEvent objects have .data attribute
        assert events[0].data.get("text") == "hello" or events[0].data.get("content") == "hello"

    async def _collect_events(self, core):
        events = []
        async for event in core.run("hi"):
            events.append(event)
        return events


# ═══════════════════════════════════════════════════════════════════
# 2. Core handles provider errors generically
# ═══════════════════════════════════════════════════════════════════

class TestCoreProviderErrors:
    """WispAgentCore handles provider errors without Ollama-specific code."""

    def test_core_handles_provider_error(self, tmp_path):
        from wisp.core.agent import WispAgentCore
        from wisp.config import WispConfig

        config = WispConfig()
        config.model = "mock"

        class _BrokenProvider:
            def generate_stream_events(self, **kwargs):
                raise RuntimeError("provider boom")
                yield  # make it a generator

            def check_health(self): return {"status": "healthy"}
            def list_models(self): return []
            def get_model_info(self, model): return {}

        provider = _BrokenProvider()
        core = WispAgentCore(config=config, provider=provider)
        core.messages = [{"role": "user", "content": "hi"}]
        from wisp.infra.store import get_store
        core.session_mgr = get_store(str(tmp_path / "test.db"))

        import asyncio
        events = asyncio.run(self._collect_events(core))

        error_events = [e for e in events if e.type == "error" or e.data.get("_stream_error")]
        assert len(error_events) >= 1

    async def _collect_events(self, core):
        events = []
        async for event in core.run("hi"):
            events.append(event)
        return events
