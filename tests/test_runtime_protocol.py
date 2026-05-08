"""Tests for the app runtime protocol models."""

from wisp.runtime_protocol import AppEvent, JsonRpcRequest


def test_jsonrpc_request_round_trip():
    request = JsonRpcRequest(id="1", method="threads.list", params={"workspace": "/tmp/repo"})
    clone = JsonRpcRequest.from_dict(request.to_dict())
    assert clone == request


def test_app_event_round_trip():
    event = AppEvent(
        event="thread.updated",
        thread_id="thread-1",
        payload={"status": "active"},
    )
    clone = AppEvent.from_dict(event.to_dict())
    assert clone == event
