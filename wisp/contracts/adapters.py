"""Edge adapters: canonical nested <-> transport flat shapes (M1a).

`to_flat` mirrors the three existing flatten sites
(core/stateless.py:105, core/provider_stream.py, transport/headless.py)
without touching them; `from_flat` is lenient like AgentEvent.from_dict."""
from __future__ import annotations
from typing import Any

from wisp.contracts.envelope import CanonicalEvent


def to_flat(ev: CanonicalEvent) -> dict[str, Any]:
    # Named exclusion (spec §3): mirrors the three existing flatten sites by
    # dropping trace context, so flat consumers see byte-identical shapes.
    # Lineage preservation lives in the nested form (to_dict/from_dict).
    flat: dict[str, Any] = dict(ev.data)
    flat["type"] = ev.type
    flat["timestamp"] = ev.timestamp
    return flat


def from_flat(d: dict[str, Any]) -> CanonicalEvent:
    # Lenient like AgentEvent.from_dict: known envelope keys become fields,
    # everything else folds into data.
    data = {k: v for k, v in d.items()
            if k not in ("type", "timestamp", "trace_id", "span_id", "schema_version")}
    return CanonicalEvent(type=d.get("type", ""), data=data,
                          timestamp=d.get("timestamp", 0.0),
                          trace_id=d.get("trace_id", ""),
                          span_id=d.get("span_id", ""),
                          schema_version=d.get("schema_version", 1))


for_cli = to_flat
for_tui = to_flat
for_websocket = to_flat
for_headless = to_flat
