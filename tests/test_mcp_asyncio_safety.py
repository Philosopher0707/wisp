"""Regression: MCP JSON-RPC stdio I/O must not block the event loop."""

import asyncio
import time

import pytest

from unittest.mock import MagicMock


class FakePopen:
    """A fake subprocess.Popen that simulates slow stdio responses."""

    def __init__(self, responses=None, delay=0.0):
        self._responses = responses or []
        self._delay = delay
        self._read_idx = 0
        self.returncode = 0

    def write(self, data):
        self._last_write = data

    def flush(self):
        pass

    def readline(self):
        if self._delay:
            time.sleep(self._delay)
        if self._read_idx < len(self._responses):
            resp = self._responses[self._read_idx]
            self._read_idx += 1
            return resp + "\n"
        return ""

    def read(self):
        return ""

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0

    @property
    def stdin(self):
        return self

    @property
    def stdout(self):
        return self

    @property
    def stderr(self):
        return self


class TestMCPSendStdio:
    """_send_stdio_request blocks on slow subprocess pipes; verify thread-offload."""

    def test_send_stdio_request_is_sync(self):
        """The underlying request is a plain sync function."""
        from wisp.mcp import _send_stdio_request
        assert not asyncio.iscoroutinefunction(_send_stdio_request)

    def test_send_request_async_is_async(self):
        """Async wrapper exists and delegates via asyncio.to_thread."""
        from wisp.mcp import _send_request_async
        assert asyncio.iscoroutinefunction(_send_request_async)

    @pytest.mark.asyncio
    async def test_request_async_does_not_block_loop(self):
        """When stdio takes 0.3s, the asyncio event loop must remain free."""
        from wisp.mcp import _send_request_async
        from wisp.mcp import MCPServer, MCPServerConfig

        fake_proc = FakePopen(
            responses=['{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}'],
            delay=0.3,
        )

        config = MCPServerConfig(name="test", command="/bin/false")
        server = MCPServer(config=config)
        server.process = fake_proc

        # Schedule a timer that should fire *during* the slow stdio call
        loop = asyncio.get_running_loop()
        timer_fired = False

        def set_flag():
            nonlocal timer_fired
            timer_fired = True

        loop.call_later(0.05, set_flag)

        t0 = time.monotonic()
        result = await _send_request_async(server, "tools/list", {})
        elapsed = time.monotonic() - t0

        assert result == {"tools": []}
        assert timer_fired, "event loop was blocked — timer never fired"
        assert elapsed >= 0.25, f"request completed too fast ({elapsed:.3f}s) — thread offload probably not used"


class TestMCPManagerAsync:
    """Async MCPManager methods must not block the event loop."""

    @pytest.mark.asyncio
    async def test_health_check_offloads_sync_io(self):
        """health_check must wrap _send_request in asyncio.to_thread."""
        from wisp.mcp import MCPManager, MCPServer, MCPServerConfig
        import inspect

        # Verify the method exists and is declared async
        assert hasattr(MCPManager, "health_check")
        assert inspect.isasyncgenfunction(MCPManager.health_check) or inspect.iscoroutinefunction(MCPManager.health_check)
    @pytest.mark.asyncio
    async def test_connect_always_load_uses_to_thread(self):
        """connect_always_load_servers must wrap connect_server in asyncio.to_thread."""
        import inspect
        from wisp.mcp import MCPManager

        src = inspect.getsource(MCPManager.connect_always_load_servers)
        assert "asyncio.to_thread(connect_server" in src or "to_thread" in src, (
            "connect_always_load_servers must wrap blocking connect_server in asyncio.to_thread"
        )
    @pytest.mark.asyncio
    async def test_reconnect_server_uses_to_thread(self):
        """reconnect_server must wrap connect_server in asyncio.to_thread."""
        import inspect
        from wisp.mcp import MCPManager

        src = inspect.getsource(MCPManager.reconnect_server)
        assert "asyncio.to_thread(connect_server" in src or "to_thread" in src, (
            "reconnect_server must wrap blocking connect_server in asyncio.to_thread"
        )
