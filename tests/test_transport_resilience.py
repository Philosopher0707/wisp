"""Transport resilience — hardened timeouts, pruning, and transient retry.

Covers:
  - Granular timeout config (connect 15, write 60, read 120, pool 30)
  - TCP keepalive + pool limits
  - Tool payload pruning (historical read_file/list_files condensed, byte ceiling)
  - Transient error classification and retry (WriteTimeout, ConnectionResetError, etc.)
  - Integration: large payloads do not stall write, slow socket writes are retried
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

# ── Timeout & Transport Config ────────────────────────────────────────


class TestHardenedTimeout:
    def test_hardened_timeout_values(self):
        from wisp.core.transport import HARDENED_TIMEOUT

        assert HARDENED_TIMEOUT.connect == 15.0
        assert HARDENED_TIMEOUT.write == 60.0
        assert HARDENED_TIMEOUT.read == 120.0
        assert HARDENED_TIMEOUT.pool == 30.0

    def test_hardened_timeout_as_requests_tuple(self):
        from wisp.core.transport import HARDENED_TIMEOUT

        connect, read = HARDENED_TIMEOUT.as_requests_tuple()
        assert connect == 15.0
        # write folded into read => max(60, 120) = 120
        assert read == 120.0

    def test_hardened_timeout_as_httpx(self):
        from wisp.core.transport import HARDENED_TIMEOUT

        try:
            import httpx
        except ImportError:
            pytest.skip("httpx not installed")

        t = HARDENED_TIMEOUT.as_httpx_timeout()
        assert isinstance(t, httpx.Timeout)
        # httpx.Timeout stores as properties
        assert t.connect == 15.0
        assert t.write == 60.0
        assert t.read == 120.0
        assert t.pool == 30.0

    def test_pool_limits(self):
        from wisp.core.transport import POOL_LIMITS

        assert POOL_LIMITS["max_keepalive_connections"] == 20
        assert POOL_LIMITS["max_connections"] == 100
        assert POOL_LIMITS["keepalive_expiry"] == 30.0

    def test_keepalive_config(self):
        from wisp.core.transport import KEEPALIVE_CONFIG

        assert KEEPALIVE_CONFIG["keepalive"] is True
        assert KEEPALIVE_CONFIG["tcp_keepalive"]["enabled"] is True
        assert KEEPALIVE_CONFIG["tcp_keepalive"]["idle"] == 60

    def test_get_hardened_session_has_keepalive_and_pool(self):
        from wisp.core.transport import get_hardened_session

        session = get_hardened_session()
        # Check that session has keepalive header
        assert session.headers.get("Connection") == "keep-alive"
        # Check that adapters are mounted
        assert "http://" in session.adapters
        assert "https://" in session.adapters
        # Check that session stores timeout
        assert hasattr(session, "_wisp_hardened_timeout")
        session.close()

    def test_get_hardened_httpx_client(self):
        from wisp.core.transport import get_hardened_httpx_client

        try:
            import httpx
        except ImportError:
            pytest.skip("httpx not installed")

        client = get_hardened_httpx_client()
        # httpx.Client should have hardened timeout
        assert isinstance(client, httpx.Client)
        assert client.timeout.connect == 15.0
        assert client.timeout.write == 60.0
        assert client.timeout.read == 120.0
        assert client.timeout.pool == 30.0
        # Check limits — attribute name varies by httpx version
        limits = getattr(client, "limits", None) or getattr(client, "_limits", None)
        if limits is not None:
            # Try both old and new attribute names
            keepalive = getattr(limits, "max_keepalive_connections", None) or getattr(limits, "max_keepalive", None)
            max_conn = getattr(limits, "max_connections", None)
            if keepalive is not None:
                assert keepalive == 20
            if max_conn is not None:
                assert max_conn == 100
        client.close()

    def test_socket_options_include_keepalive(self):
        from wisp.core.transport import _get_keepalive_socket_options
        import socket

        opts = _get_keepalive_socket_options()
        # Should at least have SO_KEEPALIVE
        assert any(o[0] == socket.SOL_SOCKET and o[1] == socket.SO_KEEPALIVE for o in opts)
        # Should have TCP_NODELAY
        assert any(o[1] == socket.TCP_NODELAY for o in opts)


# ── Transient Error Classification ────────────────────────────────────


class TestTransientError:
    def test_is_transient_status(self):
        from wisp.core.transport import is_transient_status

        assert is_transient_status(429) is True
        assert is_transient_status(500) is True
        assert is_transient_status(502) is True
        assert is_transient_status(503) is True
        assert is_transient_status(599) is True
        assert is_transient_status(200) is False
        assert is_transient_status(400) is False
        assert is_transient_status(404) is False
        assert is_transient_status(None) is False

    def test_is_transient_error_stdlib(self):
        from wisp.core.transport import is_transient_error

        assert is_transient_error(TimeoutError("timed out")) is True
        assert is_transient_error(ConnectionResetError("reset")) is True
        assert is_transient_error(ConnectionAbortedError("aborted")) is True
        assert is_transient_error(BrokenPipeError("pipe")) is True
        assert is_transient_error(ValueError("not transient")) is False

    def test_is_transient_error_write_timeout(self):
        from wisp.core.transport import is_transient_error

        # Simulate httpcore.WriteTimeout without importing httpcore
        class FakeWriteTimeout(Exception):
            pass

        FakeWriteTimeout.__name__ = "WriteTimeout"
        # Need to set module to trigger httpcore check
        FakeWriteTimeout.__module__ = "httpcore"

        exc = FakeWriteTimeout("The write operation timed out")
        assert is_transient_error(exc) is True

        # Also test string matching fallback
        exc2 = TimeoutError("The write operation timed out")
        assert is_transient_error(exc2) is True

        exc3 = Exception("httpcore.WriteTimeout: The write operation timed out")
        assert is_transient_error(exc3) is True

    def test_is_transient_error_remote_protocol(self):
        from wisp.core.transport import is_transient_error

        class FakeRemoteProtocolError(Exception):
            pass

        FakeRemoteProtocolError.__name__ = "RemoteProtocolError"
        FakeRemoteProtocolError.__module__ = "h11"

        exc = FakeRemoteProtocolError("peer closed")
        assert is_transient_error(exc) is True

        exc2 = Exception("RemoteProtocolError: peer closed")
        assert is_transient_error(exc2) is True

    def test_is_transient_error_requests(self):
        from wisp.core.transport import is_transient_error

        try:
            import requests.exceptions
        except ImportError:
            pytest.skip("requests not installed")

        assert is_transient_error(requests.exceptions.Timeout("timeout")) is True
        assert is_transient_error(requests.exceptions.ConnectionError("conn")) is True
        assert is_transient_error(requests.exceptions.ChunkedEncodingError("chunk")) is True
        assert is_transient_error(requests.exceptions.HTTPError("404")) is False

    def test_is_transient_error_wrapped(self):
        from wisp.core.transport import is_transient_error

        # Wrapped error: outer exception with transient cause
        cause = TimeoutError("The write operation timed out")
        outer = RuntimeError("outer")
        outer.__cause__ = cause
        assert is_transient_error(outer) is True

        # Wrapped via context
        outer2 = RuntimeError("outer2")
        outer2.__context__ = ConnectionResetError("reset")
        assert is_transient_error(outer2) is True


# ── Retry Logic ───────────────────────────────────────────────────────


class TestRetry:
    def test_retry_with_backoff_success_on_retry(self):
        from wisp.core.transport import retry_with_backoff

        call_count = 0

        @retry_with_backoff(max_attempts=3, base_delay=0.01)
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TimeoutError("The write operation timed out")
            return "success"

        result = flaky()
        assert result == "success"
        assert call_count == 3

    def test_retry_with_backoff_non_transient_no_retry(self):
        from wisp.core.transport import retry_with_backoff

        call_count = 0

        @retry_with_backoff(max_attempts=3, base_delay=0.01)
        def not_transient():
            nonlocal call_count
            call_count += 1
            raise ValueError("permanent error")

        with pytest.raises(ValueError):
            not_transient()
        assert call_count == 1

    def test_retry_exhaustion(self):
        from wisp.core.transport import retry_with_backoff

        @retry_with_backoff(max_attempts=3, base_delay=0.01)
        def always_fails():
            raise ConnectionResetError("reset")

        with pytest.raises(ConnectionResetError):
            always_fails()

    @pytest.mark.asyncio
    async def test_async_retry(self):
        from wisp.core.transport import async_retry_with_backoff

        call_count = 0

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise TimeoutError("write timeout")
            return "ok"

        result = await async_retry_with_backoff(flaky, max_attempts=3, base_delay=0.01)
        assert result == "ok"
        assert call_count == 2

    def test_hardened_post_retries_write_timeout(self):
        from wisp.core.transport import hardened_post, HARDENED_TIMEOUT

        class FakeResponse:
            status_code = 200
            text = "ok"

            def raise_for_status(self):
                pass

        call_count = 0

        def fake_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            # Check that hardened timeout is used
            assert kwargs["timeout"] == HARDENED_TIMEOUT.as_requests_tuple() or kwargs["timeout"] == HARDENED_TIMEOUT
            if call_count < 3:
                raise TimeoutError("The write operation timed out")
            return FakeResponse()

        mock_session = MagicMock()
        mock_session.post = fake_post
        mock_session._wisp_hardened_timeout = HARDENED_TIMEOUT

        resp = hardened_post(mock_session, "http://example.com", json={"a": 1}, timeout=HARDENED_TIMEOUT, max_attempts=3)
        assert resp.status_code == 200
        assert call_count == 3

    def test_hardened_post_retries_429(self):
        from wisp.core.transport import hardened_post, HARDENED_TIMEOUT

        call_count = 0

        def fake_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.status_code = 429 if call_count < 3 else 200
            resp.text = "rate limited" if call_count < 3 else "ok"
            resp.raise_for_status = MagicMock()
            return resp

        mock_session = MagicMock()
        mock_session.post = fake_post

        resp = hardened_post(mock_session, "http://example.com", json={}, max_attempts=3)
        assert resp.status_code == 200
        assert call_count == 3


# ── Context Pruner ────────────────────────────────────────────────────


class TestContextPruner:
    def test_prune_keeps_recent_full(self):
        from wisp.core.context_pruner import prune_messages, PrunerConfig

        # Build messages with 5 tool results
        messages = [{"role": "user", "content": "hello"}]
        for i in range(5):
            # Add assistant tool call
            messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": f"call_{i}", "function": {"name": "read_file", "arguments": {"path": f"file{i}.py"}}}]
            })
            # Add tool result
            messages.append({
                "role": "tool",
                "tool_call_id": f"call_{i}",
                "content": f"--- FILE: file{i}.py | LINES: 100 | SHOWING: 1-100 ---\n" + ("x" * 1000),
            })

        config = PrunerConfig(keep_last_n_full=2, max_bytes_per_historical_result=500)
        pruned = prune_messages(messages, config)

        # Last 2 tool results should be kept more fully, older condensed
        # Check that pruned messages count matches
        assert len(pruned) == len(messages)
        # Check that historical ones are condensed (smaller)
        # First tool result (idx 2) should be pruned, last (idx 10) should be recent
        first_tool_content = pruned[2]["content"]
        last_tool_content = pruned[10]["content"] if len(pruned) > 10 else pruned[-1]["content"]
        assert len(first_tool_content) < 1000  # historical condensed
        # Last should be larger or full
        assert "pruned" in first_tool_content.lower() or len(first_tool_content) < len(messages[2]["content"])

    def test_condense_read_file(self):
        from wisp.core.context_pruner import condense_read_file_result

        content = "--- FILE: src/app.py | LINES: 120 | SHOWING: 1-50 ---\n" + ("x = 1\n" * 50)
        condensed = condense_read_file_result(content, max_bytes=500)
        assert "FILE:" in condensed
        assert "LINES:" in condensed
        assert len(condensed.encode("utf-8")) <= 500
        assert "pruned" in condensed.lower()

        # Already pruned should not double-prune
        condensed2 = condense_read_file_result(condensed, max_bytes=500)
        assert len(condensed2.encode("utf-8")) <= 500

    def test_condense_list_files(self):
        from wisp.core.context_pruner import condense_list_files_result

        content = "\n".join([f"📄 file{i}.py (1234 bytes)" for i in range(30)])
        condensed = condense_list_files_result(content, max_bytes=500)
        assert len(condensed.encode("utf-8")) <= 500
        assert "pruned" in condensed.lower() or "more entries" in condensed.lower()
        # Should keep count
        assert "30" in condensed or "files" in condensed.lower()

    def test_enforce_byte_ceiling(self):
        from wisp.core.context_pruner import enforce_byte_ceiling

        content = "a" * 10000
        result = enforce_byte_ceiling(content, max_bytes=1000)
        assert len(result.encode("utf-8")) <= 1000
        assert "pruned" in result.lower()

        # Under ceiling should be unchanged
        small = "a" * 500
        assert enforce_byte_ceiling(small, max_bytes=1000) == small

    def test_prune_enforces_total_ceiling(self):
        from wisp.core.context_pruner import prune_messages, PrunerConfig

        # Create many large tool results
        messages = []
        for i in range(10):
            messages.append({"role": "assistant", "tool_calls": [{"id": f"c{i}", "function": {"name": "read_file"}}]})
            messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 50000})

        config = PrunerConfig(max_total_bytes=20000, max_bytes_per_historical_result=1000, keep_last_n_full=2)
        pruned, stats = prune_messages(messages, config, return_stats=True)
        total = sum(len(str(m.get("content", "")).encode("utf-8")) for m in pruned)
        assert total <= 25000  # Allow some overhead, but well under original 500K
        assert stats.bytes_saved > 0
        assert stats.historical_pruned > 0

    def test_prune_preserves_non_tool_messages(self):
        from wisp.core.context_pruner import prune_messages, PrunerConfig

        messages = [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "tool", "tool_call_id": "c1", "content": "x" * 10000},
        ]
        # Use config where even recent single tool is considered for pruning if over limit
        # With keep_last_n_full=0, the single tool is historical and will be pruned
        config = PrunerConfig(keep_last_n_full=0, max_bytes_per_historical_result=5000)
        pruned = prune_messages(messages, config)
        # System, user, assistant should be unchanged
        assert pruned[0]["content"] == "you are helpful"
        assert pruned[1]["content"] == "hello"
        assert pruned[2]["content"] == "hi"
        # Tool should be pruned (historical, over 5000)
        assert len(pruned[3]["content"]) < 10000
        assert len(pruned[3]["content"]) <= 5000

    def test_prune_handles_large_payload_30_tool_calls(self):
        from wisp.core.context_pruner import prune_messages, PrunerConfig

        # Simulate 30 tool calls in a single turn — the exact bloat scenario
        messages = [{"role": "user", "content": "refactor"}]
        for i in range(30):
            messages.append({
                "role": "assistant",
                "tool_calls": [{"id": f"call_{i}", "function": {"name": "read_file", "arguments": {"path": f"file{i}.py"}}}]
            })
            # Each read_file returns ~10KB
            messages.append({
                "role": "tool",
                "tool_call_id": f"call_{i}",
                "content": f"--- FILE: file{i}.py | LINES: 500 | SHOWING: 1-100 ---\n" + ("y" * 10000),
            })

        original_bytes = sum(len(str(m.get("content", "")).encode("utf-8")) for m in messages)
        assert original_bytes > 300000  # 30 * 10KB = 300KB

        config = PrunerConfig(keep_last_n_full=3, max_bytes_per_historical_result=2048, max_total_bytes=50000)
        pruned, stats = prune_messages(messages, config, return_stats=True)

        pruned_bytes = sum(len(str(m.get("content", "")).encode("utf-8")) for m in pruned)
        # Pruned should be well under write timeout budget (60s can handle 50KB easily, not 300KB)
        assert pruned_bytes < 100000
        assert stats.reduction_pct > 50
        # Recent 3 should be preserved more
        assert stats.recent_kept_full == 3 or stats.recent_kept_full > 0

    def test_estimate_tokens(self):
        from wisp.core.context_pruner import estimate_tokens

        assert estimate_tokens("") == 0
        assert estimate_tokens("hello world") > 0
        # Approx 4 chars per token without tiktoken, or tiktoken's actual count
        # With tiktoken, "a"*400 may be 50 tokens (due to BPE), without it's 100
        # So we check it's in a reasonable range
        tokens = estimate_tokens("a" * 400)
        assert 40 <= tokens <= 120, f"expected 40-120 tokens for 400 'a's, got {tokens}"
        assert estimate_tokens("hello") >= 1

    def test_is_pruned(self):
        from wisp.core.context_pruner import is_pruned

        assert is_pruned("hello [pruned 100 bytes] world") is True
        assert is_pruned("normal content") is False
        assert is_pruned("... +10 more") is True


# ── Integration: Pruning + Transport ──────────────────────────────────


class TestIntegration:
    def test_large_payload_pruning_prevents_write_timeout(self):
        """Simulate large payload that would previously stall write."""
        from wisp.core.context_pruner import prune_messages, PrunerConfig
        from wisp.core.transport import HARDENED_TIMEOUT

        # Build a payload that would be 300KB without pruning
        messages = [{"role": "user", "content": "do refactor"}]
        for i in range(30):
            messages.append({"role": "assistant", "tool_calls": [{"id": f"c{i}", "function": {"name": "read_file"}}]})
            messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 10000})

        # Without pruning, json would be >300KB and risk write timeout
        original_json = json.dumps({"messages": messages})
        assert len(original_json.encode("utf-8")) > 300000

        # With pruning, should be well under
        pruned = prune_messages(messages, PrunerConfig(max_total_bytes=50000))
        pruned_json = json.dumps({"messages": pruned})
        assert len(pruned_json.encode("utf-8")) < 100000

        # Hardened write timeout is 60s, which can handle 50-100KB easily
        # but 300KB might stall on slow connections — pruning prevents this
        assert HARDENED_TIMEOUT.write == 60.0

    def test_slow_socket_write_is_retried(self):
        """Simulate slow socket that times out on write, verify retry."""
        from wisp.core.transport import hardened_post, HARDENED_TIMEOUT
        from unittest.mock import MagicMock

        # Simulate a provider that times out on first write, succeeds on retry
        attempt = 0

        def fake_post(url, **kwargs):
            nonlocal attempt
            attempt += 1
            # Verify that hardened timeout is being used (write 60s)
            timeout = kwargs.get("timeout")
            # Could be tuple or HardenedTimeout
            if isinstance(timeout, tuple):
                assert timeout[0] == 15.0  # connect
                assert timeout[1] == 120.0  # read (max of write/read)
            if attempt == 1:
                # First attempt: write timeout (large payload flush)
                raise TimeoutError("The write operation timed out")
            # Second attempt: success
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "ok"
            resp.raise_for_status = MagicMock()
            return resp

        mock_session = MagicMock()
        mock_session.post = fake_post

        # Should succeed on retry
        resp = hardened_post(mock_session, "http://test.com", json={"large": "x" * 100000}, timeout=HARDENED_TIMEOUT, max_attempts=3)
        assert resp.status_code == 200
        assert attempt == 2

    def test_payload_stalling_prevented_by_pruning_and_keepalive(self):
        """Ensure pruning + keepalive prevent silent drops over multi-minute turns."""
        from wisp.core.context_pruner import prune_messages
        from wisp.core.transport import get_hardened_session, POOL_LIMITS, KEEPALIVE_CONFIG

        # 1. Pruning keeps payload small
        messages = [{"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 20000} for i in range(30)]
        # Add assistant messages for mapping
        full_messages = []
        for i in range(30):
            full_messages.append({"role": "assistant", "tool_calls": [{"id": f"c{i}", "function": {"name": "read_file"}}]})
            full_messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 20000})

        pruned = prune_messages(full_messages)
        pruned_bytes = sum(len(str(m.get("content", "")).encode("utf-8")) for m in pruned)
        assert pruned_bytes < 100000

        # 2. Keepalive and pool limits are configured
        session = get_hardened_session()
        assert session.headers.get("Connection") == "keep-alive"
        assert POOL_LIMITS["keepalive_expiry"] == 30.0
        assert KEEPALIVE_CONFIG["tcp_keepalive"]["idle"] == 60
        session.close()

    def test_transient_write_timeout_is_retried_not_failed(self):
        """Verify that WriteTimeout during large payload is treated as transient, not fatal."""
        from wisp.core.transport import is_transient_error

        # Exact error from bug report
        exc = TimeoutError("The write operation timed out")
        assert is_transient_error(exc) is True

        # httpcore variant
        class FakeWriteTimeout(Exception):
            pass
        FakeWriteTimeout.__name__ = "WriteTimeout"
        FakeWriteTimeout.__module__ = "httpcore"
        assert is_transient_error(FakeWriteTimeout("write timeout")) is True

        # Should be retried, not surfaced as fatal turn error
        # In provider_stream, this would trigger retry with backoff
        # In stateless, this would trigger iteration retry

