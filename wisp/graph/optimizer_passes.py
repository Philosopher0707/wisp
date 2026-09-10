"""Optimizer pass registry: name -> pass function. Fixed order in PASS_ORDER."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wisp.graph.optimizer import PassFn

PASSES: dict[str, "PassFn"] = {}
