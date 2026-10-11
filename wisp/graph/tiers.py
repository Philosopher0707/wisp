"""The operator's map from a graph node's tier to a real model.

A graph node says how much model it deserves (`ModelPolicy.model_class`: `cheap | standard | strong`). Only this map says which model that is, and the map
belongs to the operator (the `graph_model_tiers` setting, `WISP_GRAPH_MODEL_TIERS`), never to a graph, a planner or a node input: those are untrusted and
can pick a tier, not a model. A model is always one of the session's provider; a tier left out means the session model, so an empty map changes nothing.

The format is `cheap=MODEL,standard=MODEL,strong=MODEL`. A malformed entry is dropped, never repaired: a typo makes that tier the session model, it cannot
invent a tier or smuggle a character into a model name. Dropped entries are counted in the log and never quoted, because whatever was typed there might
have been pasted by mistake.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

TIERS = ("cheap", "standard", "strong")
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,127}")


def _valid_model(name: object) -> bool:
    return isinstance(name, str) and _MODEL_NAME.fullmatch(name) is not None and ".." not in name


def _pairs(raw: object) -> tuple[list[tuple[Any, Any]], int]:
    """The (tier, model) pairs in `raw` and how many pieces were not pairs at all."""
    if isinstance(raw, Mapping):
        return list(raw.items()), 0
    if not isinstance(raw, str) or not raw.strip():
        return [], 0
    text = raw.strip()
    if text.startswith("{"):
        try:
            loaded = json.loads(text)  # a text that starts with "{" and parses is an object
        except ValueError:
            return [], 1
        return list(loaded.items()), 0
    pairs: list[tuple[Any, Any]] = []
    not_pairs = 0
    for piece in text.split(","):
        if not piece.strip():
            continue
        tier, sep, model = piece.partition("=")
        if sep:
            pairs.append((tier, model))
        else:
            not_pairs += 1
    return pairs, not_pairs


def parse_model_tiers(raw: object) -> dict[str, str]:
    """`{tier: model}` from `cheap=A,strong=B` (or a mapping, or a JSON object). Anything malformed is dropped and counted, never repaired."""
    pairs, dropped = _pairs(raw)
    tiers: dict[str, str] = {}
    for tier, model in pairs:
        key = tier.strip().lower() if isinstance(tier, str) else ""
        name = model.strip() if isinstance(model, str) else model
        if key in TIERS and _valid_model(name):
            tiers[key] = name
        else:
            dropped += 1
    if dropped:
        logger.warning("graph_model_tiers: ignored %d entr%s that are not 'cheap|standard|strong=<model name>'; those tiers use the session model",
                       dropped, "y" if dropped == 1 else "ies")
    return tiers


def render_model_tiers(mapping: Mapping[str, str]) -> str:
    """The canonical form, in tier order: `cheap=A,standard=B,strong=C`; empty for an empty map."""
    return ",".join(f"{tier}={mapping[tier]}" for tier in TIERS if mapping.get(tier))
