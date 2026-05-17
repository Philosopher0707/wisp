"""Persistence — log subagent results to JSONL for audit trails."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .task import SubagentContract, SubagentResult

logger = logging.getLogger(__name__)


class Persistence:
    """Persist subagent results to a JSONL file."""

    def __init__(self, path: Path):
        self._path = path

    def save(self, contract: SubagentContract, result: SubagentResult) -> None:
        """Append a result record to the JSONL log."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "timestamp": time.time(),
                "task_id": result.task_id,
                "role": contract.role,
                "task": contract.task[:200],
                "success": result.success,
                "elapsed_seconds": result.elapsed_seconds,
                "tokens_used": result.tokens_used,
                "iterations_used": result.iterations_used,
                "timed_out": result.timed_out,
                "error": result.error,
                "output_preview": result.output[:500] if result.output else "",
            }
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as exc:
            logger.debug("Failed to persist subagent result: %s", exc)

    def load(self, limit: int = 100) -> list[dict]:
        """Read persisted results from the JSONL log."""
        results = []
        try:
            if not self._path.exists():
                return results
            with open(self._path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        results.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        logger.debug("Skipping corrupted JSONL line %d: %s", line_num, exc)
            return results[-limit:]
        except Exception as exc:
            logger.warning("Failed to read persisted results: %s", exc)
            return results

    def clear(self) -> None:
        """Clear the persisted results log."""
        try:
            if self._path.exists():
                self._path.unlink()
        except OSError as exc:
            logger.warning("Failed to clear persisted results: %s", exc)
