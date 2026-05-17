"""ResultCache — subagent result caching with TTL."""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Optional

from .task import SubagentContract, SubagentResult

logger = logging.getLogger(__name__)


class ResultCache:
    """Cache subagent results with time-based expiration."""

    def __init__(self):
        self._cache: dict[str, tuple[SubagentResult, float]] = {}
        self._hits = 0
        self._misses = 0

    def _key(self, contract: SubagentContract) -> str:
        """Build cache key from contract fields that affect output."""
        parts = [
            contract.task,
            contract.role,
            ",".join(sorted(contract.tools)),
            str(contract.model or ""),
            str(contract.workspace or ""),
            contract.output_format,
            str(contract.output_schema or ""),
            str(contract.system_prompt or ""),
        ]
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def get(self, contract: SubagentContract) -> Optional[SubagentResult]:
        """Return cached result if valid, else None."""
        key = self._key(contract)
        entry = self._cache.get(key)
        if entry is None:
            self._misses += 1
            return None
        result, ts = entry
        ttl = 300 if contract.output_format == "json" else 60
        age = time.monotonic() - ts
        if age > ttl:
            del self._cache[key]
            self._misses += 1
            return None
        self._hits += 1
        logger.info("Cache hit for %s (age=%.0fs)", contract.name, age)
        return result

    def set(self, contract: SubagentContract, result: SubagentResult) -> None:
        """Store result in cache."""
        key = self._key(contract)
        self._cache[key] = (result, time.monotonic())

    def stats(self) -> dict[str, int | float]:
        """Return cache statistics."""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "total": total,
            "hit_rate": self._hits / total if total else 0.0,
            "size": len(self._cache),
        }

    def clear(self) -> None:
        """Clear all cached results."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0
