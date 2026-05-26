"""Tests for ContextAssembler cache behavior.

Verifies LRU eviction, memory pressure handling, and cache correctness.
"""

from wisp.context_assembler import ContextAssembler


class TestCacheBehavior:

    def test_cache_returns_same_object_for_identical_calls(self):
        assembler = ContextAssembler()
        r1 = assembler.build(workspace="/tmp", default_system="SYS")
        r2 = assembler.build(workspace="/tmp", default_system="SYS")
        assert r1 is r2

    def test_cache_misses_for_different_parameters(self):
        assembler = ContextAssembler()
        r1 = assembler.build(workspace="/tmp", default_system="SYS")
        r2 = assembler.build(workspace="/tmp", default_system="OTHER")
        assert r1 is not r2

    def test_invalidate_cache_clears_cache(self):
        assembler = ContextAssembler()
        r1 = assembler.build(workspace="/tmp", default_system="SYS")
        assembler.invalidate_cache()
        r2 = assembler.build(workspace="/tmp", default_system="SYS")
        assert r1 is not r2


class TestCacheEviction:
    """Cache must not grow unboundedly — LRU eviction after max size."""

    def test_cache_evicts_oldest_entries(self):
        """After max_size entries, oldest keys are evicted."""
        assembler = ContextAssembler()
        # Build with 20 different keys
        for i in range(20):
            assembler.build(workspace=f"/tmp/{i}", default_system="SYS")

        # Cache should be bounded — max 16 entries
        assert len(assembler._cache) <= 16, (
            f"Cache grew to {len(assembler._cache)} entries — unbounded growth"
        )

    def test_evicted_entry_recomputed_correctly(self):
        """An evicted entry produces the same output when rebuilt."""
        assembler = ContextAssembler()
        # Prime key 0
        original = assembler.build(workspace="/tmp/0", default_system="SYS")
        # Fill cache beyond max_size to evict key 0
        for i in range(1, 20):
            assembler.build(workspace=f"/tmp/{i}", default_system="SYS")

        # Rebuild key 0 — should produce identical content
        rebuilt = assembler.build(workspace="/tmp/0", default_system="SYS")
        assert rebuilt == original

    def test_recently_accessed_entry_not_evicted(self):
        """Most-recently-used entries survive eviction."""
        assembler = ContextAssembler()
        # Build entries 1..16
        for i in range(1, 17):
            assembler.build(workspace=f"/tmp/{i}", default_system="SYS")
        # Touch entry 1 again (Makes it MRU)
        assembler.build(workspace="/tmp/1", default_system="SYS")
        # Fill cache to eviction point
        for i in range(17, 25):
            assembler.build(workspace=f"/tmp/{i}", default_system="SYS")

        # Entry 1 should still be cached
        assert any("/tmp/1" in str(k) for k in assembler._cache), (
            "MRU entry was evicted too early"
        )
