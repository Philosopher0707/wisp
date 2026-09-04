"""Phase 2.5 seam — bounded system-prompt cache (D1).

Replaces the unbounded module-global ``_SYSTEM_PROMPT_CACHE: dict`` in
``stateless.py``, whose key includes the agent-memory mtime that changes
every turn — one new entry per turn, never evicted (``invalidate_caches``
has no callers). In long server sessions this grows without bound.

Design: LRU via OrderedDict + optional per-entry TTL, guarded by a lock.
Dict-compatible surface (get/set/clear/len) so the single consumer diff
is minimal. Stats hook for telemetry without log spam.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Hashable


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0


class BoundedPromptCache:
    """LRU cache with optional TTL for rendered system prompts.

    Args:
        maxsize: maximum entries; oldest (least-recently-used) evicted first.
        ttl_s: per-entry time-to-live in seconds; 0/None disables expiry.
    """

    def __init__(self, maxsize: int = 64, ttl_s: float | None = None) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self.maxsize = maxsize
        self.ttl_s = ttl_s if ttl_s and ttl_s > 0 else None
        self._entries: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.stats = CacheStats()

    def _expired(self, stamped: float) -> bool:
        return self.ttl_s is not None and (time.monotonic() - stamped) > self.ttl_s

    def get(self, key: Hashable, default: Any = None) -> Any:
        with self._lock:
            ent = self._entries.get(key)
            if ent is None:
                self.stats.misses += 1
                return default
            stamped, value = ent
            if self._expired(stamped):
                del self._entries[key]
                self.stats.misses += 1
                return default
            self._entries.move_to_end(key)  # hit refreshes recency
            self.stats.hits += 1
            return value

    def __setitem__(self, key: Hashable, value: Any) -> None:
        with self._lock:
            if key in self._entries:
                del self._entries[key]
            self._entries[key] = (time.monotonic(), value)
            while len(self._entries) > self.maxsize:
                self._entries.popitem(last=False)
                self.stats.evictions += 1

    def __getitem__(self, key: Hashable) -> Any:
        value = self.get(key)
        if value is None and key not in self:
            raise KeyError(key)
        return value

    def __contains__(self, key: object) -> bool:
        with self._lock:
            ent = self._entries.get(key)  # type: ignore[arg-type]
            if ent is None:
                return False
            if self._expired(ent[0]):
                del self._entries[key]  # type: ignore[arg-type]
                return False
            return True

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
