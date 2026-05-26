"""Unit tests for ResultCache."""



from wisp.multi_agent.subagent_orchestrator import ResultCache
from wisp.multi_agent.task import SubagentContract, SubagentResult


class TestResultCache:

    def test_miss_on_empty(self):
        cache = ResultCache()
        c = SubagentContract(task="test")
        assert cache.get(c) is None
        stats = cache.stats()
        assert stats["misses"] == 1
        assert stats["hits"] == 0

    def test_hit_after_set(self):
        cache = ResultCache()
        c = SubagentContract(task="test", output_format="text")
        r = SubagentResult(task_id="t", success=True, output="hello")
        cache.set(c, r)
        got = cache.get(c)
        assert got is not None
        assert got.output == "hello"
        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 0
        assert stats["hit_rate"] == 1.0

    def test_ttl_expires_text(self):
        cache = ResultCache()
        c = SubagentContract(task="test", output_format="text")
        r = SubagentResult(task_id="t", success=True, output="hello")
        cache.set(c, r)
        # Manually expire
        for key in list(cache._cache.keys()):
            result, _ = cache._cache[key]
            cache._cache[key] = (result, 0)
        got = cache.get(c)
        assert got is None
        stats = cache.stats()
        assert stats["misses"] == 1  # expired miss only

    def test_ttl_longer_for_json(self):
        cache = ResultCache()
        c = SubagentContract(task="test", output_format="json")
        r = SubagentResult(task_id="t", success=True, output='{}')
        cache.set(c, r)
        # Should still be valid (300s TTL)
        got = cache.get(c)
        assert got is not None
        assert got.output == '{}'

    def test_key_includes_task_role_tools(self):
        cache = ResultCache()
        c1 = SubagentContract(task="a", role="coder", tools=["read_file"])
        c2 = SubagentContract(task="a", role="coder", tools=["write_file"])
        r = SubagentResult(task_id="t", success=True, output="x")
        cache.set(c1, r)
        # Different tools = different key
        got = cache.get(c2)
        assert got is None

    def test_clear(self):
        cache = ResultCache()
        c = SubagentContract(task="test")
        r = SubagentResult(task_id="t", success=True, output="hello")
        cache.set(c, r)
        cache.clear()
        assert cache.get(c) is None
        stats = cache.stats()
        assert stats["size"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 1

    def test_stats_hit_rate(self):
        cache = ResultCache()
        c = SubagentContract(task="test")
        # 1 miss
        cache.get(c)
        # 1 hit
        r = SubagentResult(task_id="t", success=True, output="hello")
        cache.set(c, r)
        cache.get(c)
        stats = cache.stats()
        assert stats["total"] == 2
        assert stats["hit_rate"] == 0.5
