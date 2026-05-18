"""Tests for Q8: thread-safe _PARSER_CACHE in wisp/repo_map.py.

_PARSER_CACHE is a module-level dict shared by all async tasks.
Without a lock, concurrent cache misses race to create parsers,
leaking unclosed Parser objects (C extensions → potential crash).

Fix: threading.Lock around cache read and parser write.
"""

import threading

import pytest


class TestParserCacheThreadSafety:
    """Q8: _PARSER_CACHE must be guarded by a threading.Lock."""

    def test_parser_lock_exists(self):
        """_PARSER_LOCK must exist and be a lock-like object."""
        from wisp.repo_map import _PARSER_LOCK

        assert _PARSER_LOCK is not None
        # C-level thread locks are named '_thread.lock' and support
        # acquire()/release() but aren't instances of threading.Lock.
        assert hasattr(_PARSER_LOCK, "acquire")
        assert hasattr(_PARSER_LOCK, "release")
        assert callable(_PARSER_LOCK.acquire)
        assert callable(_PARSER_LOCK.release)

    def test_parser_lock_holds_across_threads(self):
        """The lock actually blocks concurrent access."""
        from wisp.repo_map import _PARSER_LOCK

        barrier = threading.Barrier(2)
        order: list[str] = []
        errors: list[Exception] = []

        def worker(name: str):
            try:
                barrier.wait(timeout=2)
                with _PARSER_LOCK:
                    order.append(f"{name}-in")
                    # hold briefly so the other thread would overtake if lockless
                    import time

                    time.sleep(0.02)
                    order.append(f"{name}-out")
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=worker, args=("A",))
        t2 = threading.Thread(target=worker, args=("B",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert not errors, f"Worker raised: {errors}"
        # Interleaving must be strictly A-in A-out B-in B-out or B-in B-out A-in A-out
        assert len(order) == 4
        first, second = (order[:2], order[2:]) if order[0].startswith("A") else (
            order[:2], order[2:]
        )
        # Both entries for the winner come first (no interleaving)
        assert first[0].split("-")[0] == first[1].split("-")[0]

    def test_concurrent_cache_access_no_race(self):
        """Many threads reading/writing the same cache key don't corrupt dict."""
        from wisp.repo_map import _PARSER_CACHE, _PARSER_LOCK

        fake_parser_cls = type("FakeParser", (), {})
        cache_key = id(fake_parser_cls)
        ts_lang = "fake_test_lang"
        errors: list[Exception] = []

        def writer(val: str):
            try:
                with _PARSER_LOCK:
                    cache = _PARSER_CACHE.get(cache_key)
                    if cache is None:
                        cache = {}
                        _PARSER_CACHE[cache_key] = cache
                    if cache.get(ts_lang) is None:
                        cache[ts_lang] = val
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(str(i),)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Concurrent access raised: {errors}"
        assert len(_PARSER_CACHE.get(cache_key, {})) == 1  # only one entry

    def test_parser_lock_prevents_double_initialisation(self):
        """On cache miss for a new ts_lang, only one parser is created."""
        from wisp.repo_map import _PARSER_CACHE, _PARSER_LOCK

        fake_parser_cls = type("FakeParser2", (), {})
        cache_key = id(fake_parser_cls)
        ts_lang = "double_init_lang"
        call_count = [0]

        def slow_create():
            call_count[0] += 1

        def slow_writer():
            with _PARSER_LOCK:
                cache = _PARSER_CACHE.get(cache_key)
                if cache is None:
                    cache = {}
                    _PARSER_CACHE[cache_key] = cache
                if cache.get(ts_lang) is None:
                    # Simulate the ~20-50ms parser creation
                    slow_create()
                    cache[ts_lang] = {"parser": True}

        threads = [threading.Thread(target=slow_writer) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # With proper locking, slow_create should only be called once
        assert call_count[0] == 1, (
            f"slow_create called {call_count[0]} times — no double-init guard"
        )

    @pytest.fixture(autouse=True)
    def _cleanup_parsers(self):
        """Yield, then remove any test keys from the real module cache."""
        from wisp.repo_map import _PARSER_CACHE

        before = set(_PARSER_CACHE.keys())
        yield
        after = set(_PARSER_CACHE.keys())
        for k in after - before:
            _PARSER_CACHE.pop(k, None)
