"""Tests for MockProvider — deterministic provider for unit testing.

MockProvider implements BaseProvider without network calls.
It yields predefined responses for reliable, fast tests.
"""

import pytest
from unittest.mock import MagicMock

from wisp.providers.base import BaseProvider
from wisp.stream_events import (
    TokenBatch,
    ToolCallBatch,
    StreamComplete,
    StreamError,
)


# ── Construction ─────────────────────────────────────────────────────


class TestMockProviderConstruction:

    def test_can_be_constructed(self):
        from wisp.providers.mock import MockProvider
        provider = MockProvider()
        assert provider is not None

    def test_is_base_provider(self):
        from wisp.providers.mock import MockProvider
        provider = MockProvider()
        assert isinstance(provider, BaseProvider)

    def test_default_model(self):
        from wisp.providers.mock import MockProvider
        provider = MockProvider()
        assert provider.model == "mock-model"

    def test_default_context_length(self):
        from wisp.providers.mock import MockProvider
        provider = MockProvider()
        assert provider.get_context_length() == 128000


# ── Health & metadata ────────────────────────────────────────────────


class TestMockProviderHealth:

    def test_check_health_always_true(self):
        from wisp.providers.mock import MockProvider
        provider = MockProvider()
        assert provider.check_health() is True

    def test_list_models_returns_mock(self):
        from wisp.providers.mock import MockProvider
        provider = MockProvider()
        models = provider.list_models()
        assert len(models) == 1
        assert models[0]["name"] == "mock-model"


# ── Non-streaming generation ───────────────────────────────────────


class TestMockProviderGenerate:

    def test_generate_returns_content(self):
        from wisp.providers.mock import MockProvider
        provider = MockProvider(responses=["hello world"])
        result = provider.generate("system", [{"role": "user", "content": "hi"}])
        assert result["message"]["content"] == "hello world"

    def test_generate_with_tool_calls(self):
        from wisp.providers.mock import MockProvider
        provider = MockProvider(
            responses=[],
            tool_calls=[[{"function": {"name": "read_file", "arguments": {"path": "x"}}}]]
        )
        result = provider.generate("system", [{"role": "user", "content": "hi"}])
        assert "tool_calls" in result["message"]
        assert result["message"]["tool_calls"][0]["function"]["name"] == "read_file"

    def test_generate_exhausts_responses(self):
        from wisp.providers.mock import MockProvider
        provider = MockProvider(responses=["first", "second"])
        r1 = provider.generate("system", [])
        r2 = provider.generate("system", [])
        r3 = provider.generate("system", [])
        assert r1["message"]["content"] == "first"
        assert r2["message"]["content"] == "second"
        assert r3["message"]["content"] == "[mock: no more responses]"


# ── Streaming generation ─────────────────────────────────────────────


class TestMockProviderStreamEvents:

    def test_stream_yields_content_tokens(self):
        from wisp.providers.mock import MockProvider
        provider = MockProvider(responses=["hello"])
        events = list(provider.generate_stream_events("system", [{"role": "user", "content": "hi"}]))

        # Should yield: TokenBatch(content, "he"), TokenBatch(content, "llo"), StreamComplete
        content_events = [e for e in events if isinstance(e, TokenBatch) and e.phase == "content"]
        complete_events = [e for e in events if isinstance(e, StreamComplete)]
        assert len(content_events) > 0
        assert len(complete_events) == 1
        assert complete_events[0].final_content == "hello"

    def test_stream_yields_thinking_then_content(self):
        from wisp.providers.mock import MockProvider
        provider = MockProvider(
            responses=["result"],
            thinking=["let me think"]
        )
        events = list(provider.generate_stream_events("system", []))

        thinking_events = [e for e in events if isinstance(e, TokenBatch) and e.phase == "thinking"]
        content_events = [e for e in events if isinstance(e, TokenBatch) and e.phase == "content"]
        assert len(thinking_events) > 0
        assert len(content_events) > 0

    def test_stream_yields_tool_calls(self):
        from wisp.providers.mock import MockProvider
        provider = MockProvider(
            responses=[],
            tool_calls=[[{"function": {"name": "read_file", "arguments": {"path": "x"}}}]]
        )
        events = list(provider.generate_stream_events("system", []))

        tool_call_events = [e for e in events if isinstance(e, ToolCallBatch)]
        assert len(tool_call_events) == 1
        assert tool_call_events[0].calls[0]["function"]["name"] == "read_file"

    def test_stream_sets_stream_response(self):
        from wisp.providers.mock import MockProvider
        provider = MockProvider(responses=["done"])
        list(provider.generate_stream_events("system", []))
        assert provider.stream_response is not None
        assert provider.stream_response["message"]["content"] == "done"

    def test_stream_exhausts_responses(self):
        from wisp.providers.mock import MockProvider
        provider = MockProvider(responses=["first", "second"])
        list(provider.generate_stream_events("system", []))
        list(provider.generate_stream_events("system", []))
        events = list(provider.generate_stream_events("system", []))
        complete = [e for e in events if isinstance(e, StreamComplete)][0]
        assert complete.final_content == "[mock: no more responses]"


# ── Integration with WispAgentCore ───────────────────────────────────


class TestMockProviderWithAgentCore:

    def test_agent_core_runs_turn_with_mock(self):
        from wisp.core.agent import WispAgentCore
        from wisp.config import WispConfig
        from wisp.providers.mock import MockProvider

        config = WispConfig()
        config.model = "mock-model"
        config.auto_approve = True
        config.max_iterations = 5

        provider = MockProvider(responses=["I am a mock model"])
        core = WispAgentCore(config=config)
        core.provider = provider
        core.client = provider

        events = []
        async def _collect():
            async for event in core._arun("hello", system="test"):
                events.append(event)

        import asyncio
        asyncio.run(_collect())

        content_events = [e for e in events if e.type == "content"]
        assert len(content_events) > 0
        full_text = "".join(e.data.get("text", "") for e in content_events)
        assert "mock model" in full_text

    def test_agent_core_runs_tool_call_with_mock(self):
        from wisp.core.agent import WispAgentCore
        from wisp.config import WispConfig
        from wisp.providers.mock import MockProvider

        config = WispConfig()
        config.model = "mock-model"
        config.auto_approve = True
        config.max_iterations = 5
        config.workspace = "/tmp"

        provider = MockProvider(
            responses=[""],
            tool_calls=[[{"function": {"name": "read_file", "arguments": {"path": "test.txt"}}}]]
        )
        core = WispAgentCore(config=config)
        core.provider = provider
        core.client = provider

        events = []
        async def _collect():
            async for event in core._arun("read a file", system="test"):
                events.append(event)

        import asyncio
        asyncio.run(_collect())

        tool_call_events = [e for e in events if e.type == "tool_call"]
        tool_result_events = [e for e in events if e.type == "tool_result"]
        assert len(tool_call_events) > 0
        assert len(tool_result_events) > 0
