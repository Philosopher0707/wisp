"""TDD for Transport ABC.

All transports (CLI, TUI, WebSocket, SSE) must implement this interface.
This decouples the core from any specific transport.
"""

import pytest
from typing import Any


# ═══════════════════════════════════════════════════════════════════
# 1. Transport ABC definition
# ═══════════════════════════════════════════════════════════════════

class TestTransportABC:
    """Transport defines the interface all UIs implement."""

    def test_has_send_method(self):
        from wisp.transport.base import Transport
        assert hasattr(Transport, "send")

    def test_has_recv_method(self):
        from wisp.transport.base import Transport
        assert hasattr(Transport, "recv")

    def test_has_approve_method(self):
        from wisp.transport.base import Transport
        assert hasattr(Transport, "approve")

    def test_has_start_method(self):
        from wisp.transport.base import Transport
        assert hasattr(Transport, "start")

    def test_has_stop_method(self):
        from wisp.transport.base import Transport
        assert hasattr(Transport, "stop")


# ═══════════════════════════════════════════════════════════════════
# 2. send() contract
# ═══════════════════════════════════════════════════════════════════

class TestSendContract:
    """send() must accept standardized events."""

    @pytest.mark.asyncio
    async def test_send_accepts_content_event(self):
        from wisp.transport.base import Transport

        class FakeTransport(Transport):
            def __init__(self):
                self.sent = []
            async def send(self, event):
                self.sent.append(event)
            async def recv(self):
                return ""
            async def approve(self, tool_call):
                return True
            def start(self): pass
            def stop(self): pass

        transport = FakeTransport()
        await transport.send({"type": "content", "text": "hello"})
        assert len(transport.sent) == 1
        assert transport.sent[0]["type"] == "content"

    @pytest.mark.asyncio
    async def test_send_accepts_tool_call_event(self):
        from wisp.transport.base import Transport

        class FakeTransport(Transport):
            def __init__(self):
                self.sent = []
            async def send(self, event):
                self.sent.append(event)
            async def recv(self):
                return ""
            async def approve(self, tool_call):
                return True
            def start(self): pass
            def stop(self): pass

        transport = FakeTransport()
        await transport.send({"type": "tool_call", "name": "read_file"})
        assert transport.sent[0]["name"] == "read_file"


# ═══════════════════════════════════════════════════════════════════
# 3. recv() contract
# ═══════════════════════════════════════════════════════════════════

class TestRecvContract:
    """recv() must yield user prompts."""

    @pytest.mark.asyncio
    async def test_recv_yields_prompts(self):
        from wisp.transport.base import Transport

        class FakeTransport(Transport):
            def __init__(self):
                self.prompts = ["hello", "world"]
                self.idx = 0
            async def send(self, event): pass
            async def recv(self):
                if self.idx < len(self.prompts):
                    prompt = self.prompts[self.idx]
                    self.idx += 1
                    return prompt
                return None
            async def approve(self, tool_call):
                return True
            def start(self): pass
            def stop(self): pass

        transport = FakeTransport()
        prompts = []
        for _ in range(2):
            prompt = await transport.recv()
            if prompt:
                prompts.append(prompt)

        assert prompts == ["hello", "world"]


# ═══════════════════════════════════════════════════════════════════
# 4. approve() contract
# ═══════════════════════════════════════════════════════════════════

class TestApproveContract:
    """approve() must return approval decisions."""

    @pytest.mark.asyncio
    async def test_approve_returns_bool(self):
        from wisp.transport.base import Transport

        class FakeTransport(Transport):
            async def send(self, event): pass
            async def recv(self):
                return ""
            async def approve(self, tool_call):
                return True
            def start(self): pass
            def stop(self): pass

        transport = FakeTransport()
        result = await transport.approve({"name": "run_bash"})
        assert result is True

    @pytest.mark.asyncio
    async def test_approve_can_reject(self):
        from wisp.transport.base import Transport

        class FakeTransport(Transport):
            async def send(self, event): pass
            async def recv(self):
                return ""
            async def approve(self, tool_call):
                return False
            def start(self): pass
            def stop(self): pass

        transport = FakeTransport()
        result = await transport.approve({"name": "run_bash"})
        assert result is False


# ═══════════════════════════════════════════════════════════════════
# 5. Lifecycle
# ═══════════════════════════════════════════════════════════════════

class TestLifecycle:
    """start() and stop() manage transport lifecycle."""

    def test_start_initializes_transport(self):
        from wisp.transport.base import Transport

        class FakeTransport(Transport):
            def __init__(self):
                self.started = False
            async def send(self, event): pass
            async def recv(self):
                return ""
            async def approve(self, tool_call):
                return True
            def start(self):
                self.started = True
            def stop(self): pass

        transport = FakeTransport()
        transport.start()
        assert transport.started is True

    def test_stop_cleans_up(self):
        from wisp.transport.base import Transport

        class FakeTransport(Transport):
            def __init__(self):
                self.stopped = False
            async def send(self, event): pass
            async def recv(self):
                return ""
            async def approve(self, tool_call):
                return True
            def start(self): pass
            def stop(self):
                self.stopped = True

        transport = FakeTransport()
        transport.stop()
        assert transport.stopped is True
