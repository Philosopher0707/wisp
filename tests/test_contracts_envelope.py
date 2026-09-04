# tests/test_contracts_envelope.py
from wisp.contracts import CONTRACT_VERSION, CanonicalEvent
from wisp.core.events import AgentEvent


def test_version_constant():
    assert CONTRACT_VERSION == 1


def test_from_agent_event_round_trip():
    ev = AgentEvent(type="content", data={"text": "hi"},
                    trace_id="t1", span_id="s1")
    c = CanonicalEvent.from_agent_event(ev)
    assert c.schema_version == 1
    back = c.to_agent_event()
    assert back.type == "content" and back.trace_id == "t1"


def test_unknown_field_rejected():
    import pytest
    with pytest.raises(TypeError):
        CanonicalEvent(type="content", data={}, bogus=1)


def test_from_dict_unknown_field_rejected():
    import pytest
    with pytest.raises(ValueError, match="unknown envelope fields"):
        CanonicalEvent.from_dict({"type": "content", "data": {}, "bogus": 1})
