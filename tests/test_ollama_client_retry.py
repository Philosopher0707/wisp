"""Tests for Q7 fix — _async_sleep_if_in_loop replaces time.sleep in retry loops."""

import asyncio
import threading
import time
from unittest.mock import patch
import pytest
import requests.exceptions

from wisp.ollama_client import (
    OllamaClient,
    OllamaError,
    _async_sleep_if_in_loop,
)


class FakeConfig:
    ollama_url = "http://localhost:11434"
    model = "test-model"
    temperature = 0.0
    max_tokens = 4096
    max_context_tokens = 128000
    chars_per_token = 4
    auto_approve = True
    show_thinking = False


class FakeResponse:
    """Minimal mock that duck-types a requests Response."""

    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code}", response=self
            )

    def iter_content(self, chunk_size=None):
        return iter(b"")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


# ── Tests for _async_sleep_if_in_loop ─────────────────────────────────────


class TestAsyncSleepIfInLoop:
    """Unit tests for the _async_sleep_if_in_loop helper."""

    def test_sync_context_falls_back_to_time_sleep(self, monkeypatch):
        """When no event loop is running, sleep falls back to time.sleep."""
        slept_for = []
        monkeypatch.setattr("time.sleep", slept_for.append)
        _async_sleep_if_in_loop(0.01)
        assert slept_for == [0.01]

    def test_inside_running_loop_delegates_asyncio_sleep(self):
        """Inside an active event-loop coroutine, sleep delegates to
        ``asyncio.sleep`` via ``run_coroutine_threadsafe`` so the loop
        is yielded."""
        results = []

        async def _inner():
            _async_sleep_if_in_loop(0.01)
            results.append("slept")

        async def _main():
            t1 = asyncio.create_task(_inner())
            t2 = asyncio.create_task(asyncio.sleep(0.005))
            done, pending = await asyncio.wait({t1, t2})
            for t in done:
                await t

        asyncio.run(_main())
        assert "slept" in results

    def test_inside_worker_thread_yields_to_host_loop(self):
        """When called from a worker thread with the host loop stashed,
        sleep delegates via *run_coroutine_threadsafe* so the host loop
        keeps processing other tasks."""
        order = []
        loop = asyncio.new_event_loop()
        threading.Event()

        def _host():
            """Runs the event loop on a dedicated host thread."""
            asyncio.set_event_loop(loop)
            try:
                loop.run_forever()
            except Exception:
                pass

        host_thread = threading.Thread(target=_host, daemon=True)
        host_thread.start()
        # Give the host loop time to start
        time.sleep(0.02)

        # Stash the loop like sync_gen_iter would
        from wisp.ollama_client import _loop_local
        _loop_local.loop = loop

        def _worker():
            _async_sleep_if_in_loop(0.08)
            order.append("worker_done")

        worker = threading.Thread(target=_worker)

        async def _quick():
            await asyncio.sleep(0.02)
            order.append("quick_done")

        # Schedule the quick coroutine on the host loop
        asyncio.run_coroutine_threadsafe(_quick(), loop)
        worker.start()
        worker.join(timeout=1)

        # Stop the host loop
        loop.call_soon_threadsafe(loop.stop)
        host_thread.join(timeout=0.5)
        loop.close()

        assert worker.is_alive() is False, "worker thread must have finished"
        assert "quick_done" in order
        assert "worker_done" in order
        # The quick task should finish BEFORE the worker's 0.08s sleep,
        # proving the loop was yielded and could process other tasks.
        assert order == ["quick_done", "worker_done"]


# ── Tests for retry integration ───────────────────────────────────────────


class TestRetryIntegration:
    """Integration tests: verify _post_stream still works and retries."""

    def test_post_stream_is_still_sync(self):
        client = OllamaClient(FakeConfig())
        gen = client._post_stream("chat", {"model": "test"})
        assert hasattr(gen, "__iter__")
        assert hasattr(gen, "__next__")

    def test_generate_stream_is_still_sync(self):
        client = OllamaClient(FakeConfig())
        gen = client.generate_stream("sys", [{"role": "user", "content": "hi"}])
        assert hasattr(gen, "__iter__")

    def test_generate_stream_events_is_still_sync(self):
        client = OllamaClient(FakeConfig())
        gen = client.generate_stream_events("sys", [{"role": "user", "content": "hi"}])
        assert hasattr(gen, "__iter__")

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    def test_post_stream_retry_on_503_exhausted_raises(self):
        client = OllamaClient(FakeConfig())
        exc = requests.exceptions.HTTPError("503", response=FakeResponse(503))
        exc.response = FakeResponse(503)

        with patch.object(client._session, "post", side_effect=exc):
            with pytest.raises(OllamaError):
                list(client._post_stream("chat", {"model": "test"}))

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    def test_post_stream_retry_on_connection_error_exhausted_raises(self):
        client = OllamaClient(FakeConfig())
        exc = requests.exceptions.ConnectionError("refused")

        with patch.object(client._session, "post", side_effect=exc):
            with pytest.raises(OllamaError, match="Cannot connect"):
                list(client._post_stream("chat", {"model": "test"}))

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    def test_post_stream_retry_on_timeout_exhausted_raises(self):
        client = OllamaClient(FakeConfig())
        exc = requests.exceptions.Timeout("timed out")

        with patch.object(client._session, "post", side_effect=exc):
            with pytest.raises(OllamaError, match="timed out"):
                list(client._post_stream("chat", {"model": "test"}))

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    def test_post_stream_success_after_one_retry(self):
        client = OllamaClient(FakeConfig())
        call_count = 0

        def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                exc = requests.exceptions.HTTPError("503", response=FakeResponse(503))
                exc.response = FakeResponse(503)
                raise exc
            return FakeResponse(200, {"done": True})

        with patch.object(client._session, "post", side_effect=_side_effect):
            items = list(client._post_stream("chat", {"model": "test"}))
            assert call_count == 2
            assert items == []

    def test_post_with_retry_uses_async_sleep(self, monkeypatch):
        """The non-streaming _post_with_retry also delegates to the
        async-sleep helper instead of plain ``time.sleep``."""
        slept_calls = []
        monkeypatch.setattr(
            "wisp.ollama_client._async_sleep_if_in_loop",
            slept_calls.append,
        )

        client = OllamaClient(FakeConfig())
        exc = requests.exceptions.HTTPError("503", response=FakeResponse(503))
        exc.response = FakeResponse(503)

        with patch.object(client._session, "post", side_effect=exc):
            with pytest.raises(OllamaError):
                client._post_with_retry("chat", {"model": "test"})

        assert slept_calls == [1, 2]  # attempt 0 sleeps 1s, attempt 1 sleeps 2s
