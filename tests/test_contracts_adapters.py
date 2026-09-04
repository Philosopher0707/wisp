# tests/test_contracts_adapters.py
import json
from pathlib import Path
from wisp.contracts.adapters import (to_flat, from_flat, for_cli, for_tui,
                                      for_websocket, for_headless)
from wisp.contracts import CanonicalEvent

FIX = Path(__file__).resolve().parent / "fixtures" / "contracts"


def test_aliases_share_implementation():
    assert for_cli is to_flat and for_tui is to_flat
    assert for_websocket is to_flat and for_headless is to_flat


def test_flat_golden_byte_stable():
    flat = json.loads((FIX / "event_flat.json").read_text())
    ev = CanonicalEvent.from_dict(json.loads((FIX / "nested_event.json").read_text()))
    assert to_flat(ev) == flat  # byte-stability: flat consumers keep working


def test_from_flat_lenient_folds_unknowns():
    ev = from_flat({"type": "content", "text": "hi", "future": "x"})
    assert ev.data == {"text": "hi", "future": "x"}  # matches AgentEvent.from_dict


def test_from_flat_extracts_trace_fields():
    ev = from_flat({"type": "content", "text": "hi", "trace_id": "t1",
                    "span_id": "s1", "schema_version": 1, "timestamp": 2.0})
    assert (ev.trace_id, ev.span_id, ev.timestamp) == ("t1", "s1", 2.0)
    assert ev.data == {"text": "hi"}


def test_nested_round_trip_lossless():
    for p in sorted(FIX.glob("nested_*.json")):
        d = json.loads(p.read_text())
        assert CanonicalEvent.from_dict(d).to_dict() == d
