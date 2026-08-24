"""TDD for stateless WispAgentCore — the turn engine.

Replaces: the stateful WispAgentCore in wisp/core/agent.py.
All state is injected or passed as parameters.
"""

import pytest


# ── Minimal mock provider for testing ──────────────────────────────

class _MockProvider:
    def __init__(self, responses: list[dict] | None = None):
        self.responses = responses or []
        self.calls = []

    def generate_stream_events(self, system_prompt: str, messages: list[dict], tools: list | None = None, checkpoint_every: int = 50):
        self.calls.append((system_prompt, messages, tools))
        for resp in self.responses:
            yield resp


# ── Test fixtures ──────────────────────────────────────────────────

@pytest.fixture
def core():
    from wisp.core.engine import WispAgentCore
    from wisp.infra.security import SecurityPolicy, PermissionMode
    from wisp.infra.extensions import ExtensionHost

    return WispAgentCore(
        provider=_MockProvider(),
        security=SecurityPolicy(permission_mode=PermissionMode.FULL),
        extensions=ExtensionHost(),
    )


# ═══════════════════════════════════════════════════════════════════
# 1. Construction and statelessness
# ═══════════════════════════════════════════════════════════════════

class TestCoreConstruction:
    """Core is stateless — all dependencies injected."""

    def test_core_has_no_internal_state(self, core):
        assert not hasattr(core, "_session")
        assert not hasattr(core, "_messages")


# ═══════════════════════════════════════════════════════════════════
# 2. Turn execution
# ═══════════════════════════════════════════════════════════════════

class TestTurnExecution:
    """Turn generates events from provider responses."""

    @pytest.mark.asyncio
    async def test_turn_yields_content_events(self, core):
        core.provider = _MockProvider([
            {"type": "token", "text": "Hello", "phase": "content"},
            {"type": "token", "text": " world", "phase": "content"},
            {"type": "done"},
        ])

        session = {"id": "s1", "messages": [], "model": "qwen"}
        events = []
        async for event in core.turn(session, "hi"):
            events.append(event)

        assert len(events) == 3
        assert events[0]["text"] == "Hello"
        assert events[1]["text"] == " world"
        assert events[2]["type"] == "done"

    @pytest.mark.asyncio
    async def test_turn_builds_system_prompt(self, core):
        core.provider = _MockProvider([
            {"type": "token", "text": "ok", "phase": "content"},
            {"type": "done"},
        ])

        session = {"id": "s1", "messages": [], "model": "qwen", "workspace": "/tmp"}
        async for _ in core.turn(session, "hi"):
            pass

        assert len(core.provider.calls) == 1
        system_prompt, messages, tools = core.provider.calls[0]
        assert "Wisp" in system_prompt or "wisp" in system_prompt.lower()
        assert len(messages) == 1
        assert messages[0]["role"] == "user"


# ═══════════════════════════════════════════════════════════════════
# 3. Tool call parsing
# ═══════════════════════════════════════════════════════════════════

class TestToolCallParsing:
    """Tool calls in the stream are parsed and executed."""

    @pytest.mark.asyncio
    async def test_tool_call_event(self, core):
        # Stateful provider: first call yields tool_call, second yields content
        calls = []
        def make_provider():
            class StatefulProvider:
                def __init__(self):
                    self.call_count = 0
                def generate_stream_events(self, system_prompt, messages, tools=None, checkpoint_every=50):
                    self.call_count += 1
                    calls.append((system_prompt, messages, tools))
                    if self.call_count == 1:
                        yield {"type": "tool_call", "name": "read_file", "arguments": {"path": "test.py"}}
                        yield {"type": "done"}
                    else:
                        yield {"type": "token", "text": "ok", "phase": "content"}
                        yield {"type": "done"}
            return StatefulProvider()

        core.provider = make_provider()
        session = {"id": "s1", "messages": [], "model": "qwen", "workspace": "/tmp"}
        events = []
        async for event in core.turn(session, "read test.py"):
            events.append(event)

        tool_events = [e for e in events if e.get("type") == "tool_call"]
        assert len(tool_events) == 1
        assert tool_events[0]["name"] == "read_file"

    @pytest.mark.asyncio
    async def test_tool_result_event(self, core):
        # Stateful provider: first call yields tool_call, second yields content
        def make_provider():
            class StatefulProvider:
                def __init__(self):
                    self.call_count = 0
                def generate_stream_events(self, system_prompt, messages, tools=None, checkpoint_every=50):
                    self.call_count += 1
                    if self.call_count == 1:
                        yield {"type": "tool_call", "name": "read_file", "arguments": {"path": "test.py"}}
                        yield {"type": "done"}
                    else:
                        yield {"type": "token", "text": "ok", "phase": "content"}
                        yield {"type": "done"}
            return StatefulProvider()

        core.provider = make_provider()
        session = {"id": "s1", "messages": [], "model": "qwen", "workspace": "/tmp"}
        events = []
        async for event in core.turn(session, "read test.py"):
            events.append(event)

        result_events = [e for e in events if e.get("type") == "tool_result"]
        assert len(result_events) == 1
        assert "test.py" in str(result_events[0].get("result", ""))


# ═══════════════════════════════════════════════════════════════════
# 4. Security integration
# ═══════════════════════════════════════════════════════════════════

class TestSecurityIntegration:
    """Security policy blocks dangerous tool calls."""

    @pytest.mark.asyncio
    async def test_blocked_tool_returns_error(self):
        from wisp.core.engine import WispAgentCore
        from wisp.infra.security import SecurityPolicy, PermissionMode
        from wisp.infra.extensions import ExtensionHost

        core = WispAgentCore(
            provider=_MockProvider([
                {"type": "tool_call", "name": "run_bash", "arguments": {"command": "rm -rf /"}},
                {"type": "done"},
            ]),
            security=SecurityPolicy(permission_mode=PermissionMode.READ_ONLY),
            extensions=ExtensionHost(),
        )

        session = {"id": "s1", "messages": [], "model": "qwen"}
        events = []
        async for event in core.turn(session, "delete everything"):
            events.append(event)

        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) >= 1
        assert "READ_ONLY" in error_events[0].get("message", "")


# ═══════════════════════════════════════════════════════════════════
# 5. Extension integration
# ═══════════════════════════════════════════════════════════════════

class TestExtensionIntegration:
    """Extensions can intercept events."""

    @pytest.mark.asyncio
    async def test_extension_blocks_tool(self):
        from wisp.core.engine import WispAgentCore
        from wisp.infra.security import SecurityPolicy, PermissionMode
        from wisp.infra.extensions import ExtensionHost

        host = ExtensionHost()
        host.register(_BlockingExtension())

        core = WispAgentCore(
            provider=_MockProvider([
                {"type": "tool_call", "name": "run_bash", "arguments": {"command": "ls"}},
                {"type": "done"},
            ]),
            security=SecurityPolicy(permission_mode=PermissionMode.FULL),
            extensions=host,
        )

        session = {"id": "s1", "messages": [], "model": "qwen"}
        events = []
        async for event in core.turn(session, "list files"):
            events.append(event)

        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) >= 1
        assert "blocked by extension" in error_events[0].get("message", "").lower()


# ── helpers ────────────────────────────────────────────────────────

class _BlockingExtension:
    name = "blocker"
    def start(self): pass
    def stop(self): pass
    def tools(self): return []
    def intercept(self, event):
        if event.get("type") == "tool_call" and event.get("name") == "run_bash":
            return {"action": "block", "reason": "blocked by extension"}
        return {"action": "allow"}


class TestModuleLocation:
    """WispAgentCore lives in wisp.core.stateless to break circular imports."""

    def test_can_import_from_stateless_module(self):
        from wisp.core.stateless import WispAgentCore
        assert WispAgentCore is not None

    def test_engine_reexports_for_backward_compat(self):
        from wisp.core.engine import WispAgentCore as EngineCore
        from wisp.core.stateless import WispAgentCore as StatelessCore
        assert EngineCore is StatelessCore


# ═══════════════════════════════════════════════════════════════════
# Tools must run off-loop: blocking I/O froze concurrent turns
# ═══════════════════════════════════════════════════════════════════


class TestToolsRunOffLoop:
    """A slow tool must not stall the event loop while it blocks."""

    def test_slow_tool_does_not_block_concurrent_stream(
        self, core, monkeypatch
    ):
        import asyncio
        import time as _time
        from unittest.mock import patch

        monkeypatch.delenv("WISP_WEB_PROXY", raising=False)

        def slow_tool(name, args, workspace="."):
            _time.sleep(1.0)
            return {"status": "ok", "data": "finally done"}

        async def scenario():
            ticks = {"count": 0}

            async def ticker():
                while True:
                    await asyncio.sleep(0.05)
                    ticks["count"] += 1

            tool_call = {
                "type": "tool_call",
                "name": "web_fetch",
                "arguments": {"url": "https://slow.example.com"},
                "id": "tc-1",
            }
            ticker_task = asyncio.create_task(ticker())
            try:
                start = _time.monotonic()
                async for _ in core._execute_tool(
                    tool_call, {"id": "s", "messages": []}
                ):
                    pass
                elapsed = _time.monotonic() - start
            finally:
                ticker_task.cancel()
            # The loop kept ticking during the 1s tool: proves to_thread.
            assert ticks["count"] >= 8, (
                f"loop starved during tool execution ({ticks['count']} ticks)"
            )
            assert elapsed >= 0.9, "tool must still run to completion"

        with patch("wisp.tools.registry.execute_tool", slow_tool):
            asyncio.run(scenario())
