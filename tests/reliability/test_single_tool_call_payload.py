"""A lone tool call is a response, not an empty attempt.

The OpenAI-compatible provider emits one tool call as a flat `tool_call` event
(`name`/`arguments`/`id`) and several as `tool_calls` with a `calls` list. Only
the list form was recognized as a payload, so a turn whose whole answer was one
tool call — no text — was retried three times and then reported as "Provider
returned no usable response". Observed live on OpenRouter
(stealth/space-bunny-alpha): stream stats `usable=3 finish=tool_calls`.
"""

from wisp.core.provider_stream import _event_has_payload
from wisp.core.stateless import WispAgentCore


def _normalized(event: dict) -> dict:
    return WispAgentCore._normalize_event(None, event)


def test_single_tool_call_is_a_payload():
    event = {"type": "tool_call", "name": "read_file",
             "arguments": {"path": "/testbed/src/app.py"}, "id": "call_1"}
    assert _event_has_payload(_normalized(event))


def test_multiple_tool_calls_remain_a_payload():
    event = {"type": "tool_calls",
             "calls": [{"id": "a", "function": {"name": "read_file", "arguments": {}}}]}
    assert _event_has_payload(_normalized(event))


def test_nameless_tool_call_is_not_a_payload():
    event = {"type": "tool_call", "name": "", "arguments": {}, "id": ""}
    assert not _event_has_payload(_normalized(event))
