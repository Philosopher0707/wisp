"""Phase 2.5 RED tests — bounded prompt cache (D1).

Target: the system-prompt cache is bounded (LRU + TTL) instead of an
unbounded module-global dict keyed on per-turn mtime. Behavior preserved:
get/set/clear semantics identical for cache hits.
"""

from __future__ import annotations

import time


def test_bounded_cache_module_exists():
    from wisp.core import prompt_cache as pc

    assert hasattr(pc, "BoundedPromptCache")


def test_cache_evicts_oldest_beyond_maxsize():
    from wisp.core.prompt_cache import BoundedPromptCache

    c = BoundedPromptCache(maxsize=3)
    c[("ws", 1.0, "v", "")] = "a"
    c[("ws", 2.0, "v", "")] = "b"
    c[("ws", 3.0, "v", "")] = "c"
    c[("ws", 4.0, "v", "")] = "d"  # evicts "a"
    assert len(c) == 3
    assert c.get(("ws", 1.0, "v", "")) is None
    assert c.get(("ws", 4.0, "v", "")) == "d"


def test_cache_hit_refreshes_recency():
    from wisp.core.prompt_cache import BoundedPromptCache

    c = BoundedPromptCache(maxsize=2)
    c["a"] = "1"
    c["b"] = "2"
    assert c.get("a") == "1"  # "a" now most-recent
    c["c"] = "3"  # evicts "b", not "a"
    assert c.get("a") == "1"
    assert c.get("b") is None


def test_cache_ttl_expiry_is_miss():
    from wisp.core.prompt_cache import BoundedPromptCache

    c = BoundedPromptCache(maxsize=8, ttl_s=0.05)
    c["k"] = "v"
    assert c.get("k") == "v"
    time.sleep(0.07)
    assert c.get("k") is None


def test_cache_clear_empties():
    from wisp.core.prompt_cache import BoundedPromptCache

    c = BoundedPromptCache(maxsize=8)
    c["a"] = "1"
    c.clear()
    assert len(c) == 0


def test_stateless_prompt_cache_is_bounded():
    import wisp.core.stateless as st

    cache = st._SYSTEM_PROMPT_CACHE
    # Must expose a bound (not a plain unbounded dict).
    assert hasattr(cache, "maxsize") or hasattr(cache, "__maxsize__"), (
        f"{type(cache).__name__} has no bound"
    )
    assert cache.maxsize <= 256
