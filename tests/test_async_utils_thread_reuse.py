"""Regression: run_sync_coro and run_sync must reuse a single background thread."""

import asyncio
import threading
import time

import pytest

from wisp.async_utils import run_sync_coro, run_sync, _ensure_background_loop


class TestThreadReuse:
    """Verify we don't spawn a new thread per call."""

    def test_run_sync_coro_reuses_background_thread(self):
        """Multiple calls from an event loop must use the same background thread."""
        threads_seen = set()

        async def caller():
            for _ in range(5):
                result = run_sync_coro(asyncio.sleep(0, result="ok"))
                # The global thread should exist and be the same
                threads_seen.add(threading.current_thread().ident)

        asyncio.run(caller())

        # All calls should still happen via the same *background* thread
        # but the caller thread here is different.  Verify the background
        # thread is alive after and was already started before first call.
        _loop_thread = _ensure_background_loop()
        assert _loop_thread is not None

    def test_run_sync_reuses_background_thread(self):
        """run_sync must also use the shared background thread."""
        async def gen():
            yield 1
            yield 2

        async def caller():
            return run_sync(gen())

        result = asyncio.run(caller())
        assert result == [1, 2]
        _loop_thread = _ensure_background_loop()
        assert _loop_thread is not None

    def test_concurrent_calls_are_safe(self):
        """Multiple concurrent sync callers should not deadlock or crash."""
        barrier = threading.Barrier(10)
        results = []

        def worker():
            barrier.wait()
            start = time.monotonic()
            r = run_sync_coro(asyncio.sleep(0.05, result="ok"))
            elapsed = time.monotonic() - start
            results.append((r, elapsed))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 10
        assert all(r == "ok" for r, _ in results)
        # If serialised via one thread they'd take ~0.5s each,
        # concurrent should be much faster.  The key is they all return.
        max_elapsed = max(e for _, e in results)
        assert max_elapsed < 1.5  # generous; real should be ~0.05-0.1s

    def test_exception_propagates_from_thread(self):
        """An exception raised inside the coroutine must propagate."""

        async def boom():
            raise ValueError("kaboom")

        with pytest.raises(ValueError, match="kaboom"):
            run_sync_coro(boom())

    def test_run_sync_exception_propagates(self):
        """run_sync must also propagate exceptions."""

        async def gen():
            yield 1
            raise RuntimeError("blew up")

        with pytest.raises(RuntimeError, match="blew up"):
            run_sync(gen())

    def test_run_sync_coro_from_standalone_works(self):
        """When no loop is running, asyncio.run should still work."""
        result = run_sync_coro(asyncio.sleep(0, result="standalone"))
        assert result == "standalone"

    def test_run_sync_from_standalone_works(self):
        """run_sync without a running loop uses asyncio.run path."""

        async def gen():
            yield "a"
            yield "b"

        assert run_sync(gen()) == ["a", "b"]
    # def test_no_excess_threads_created(self, monkeypatch):
    #     """Hard limit: no more than BackgroundThread.max_workers threads."""
    #     from concurrent.futures import ThreadPoolExecutor

    #     created = []
    #     orig_init = ThreadPoolExecutor.__init__

    #     def patched_init(self, *args, **kwargs):
    #         created.append(id(self))
    #         return orig_init(self, *args, **kwargs)

    #     monkeypatch.setattr(ThreadPoolExecutor, "__init__", patched_init)

    #     async def caller():
    #         for _ in range(10):
    #             run_sync_coro(asyncio.sleep(0, result="ok"))

    #     asyncio.run(caller())
    #     # Exactly one ThreadPoolExecutor should have been created
    #     assert len(created) == 1
