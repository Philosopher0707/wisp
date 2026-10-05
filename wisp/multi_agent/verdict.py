"""The outcome of running several agents, and how to say what went wrong.

Shared by every surface that runs agents in parallel (the ``/swarm`` REPL command, the ``fanout`` tool): the verdict and the
grouping of failures are domain logic, so they live here and not in a presentation module. Duck-typed on ``success``,
``error`` and ``task_id`` so it needs no import of the result type.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from wisp.core.recovery import describe_failure

__all__ = ["distinct_failures", "verdict"]


def verdict(results: Sequence[Any]) -> str:
    """``complete`` when every agent succeeded, ``failed`` when none did (or none ran), else ``partial``.

    ``any(success)`` used to stand for all three, so four agents refused by the provider still ended in a green
    "Swarm complete" and a synthesized "answer" built from four error blocks.
    """
    ok = sum(1 for r in results if r.success)
    if not results or ok == 0:
        return "failed"
    return "complete" if ok == len(results) else "partial"


def distinct_failures(results: Sequence[Any], labels: Sequence[str] | None = None) -> list[tuple[str, list[str]]]:
    """``[(description, [label, ...])]``: one entry per distinct failure, in first-seen order.

    Four identical refusals are one finding naming the four labels that hit it, not four findings. ``labels`` parallels
    ``results`` (a role name, say); it defaults to each result's ``task_id``.
    """
    groups: dict[str, list[str]] = {}
    for i, r in enumerate(results):
        if r.success:
            continue
        label = labels[i] if labels is not None and i < len(labels) else str(getattr(r, "task_id", "") or i)
        groups.setdefault(describe_failure(r.error), []).append(label)
    return list(groups.items())
