"""Regression test for message_format tool_calls preservation."""

import pytest
from wisp.core.message_format import to_ollama_messages


def test_to_ollama_messages_preserves_tool_calls():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "list files"}]},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": ""}],
            "tool_calls": [{"function": {"name": "list_files", "arguments": {}}}],
        },
        {"role": "tool", "content": "foo.py\nbar.py", "name": "list_files"},
    ]
    converted = to_ollama_messages(messages)

    assert len(converted) == 3
    assert converted[1].get("tool_calls") is not None
    assert converted[1]["tool_calls"][0]["function"]["name"] == "list_files"


def test_to_ollama_messages_preserves_thinking():
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "ok"}],
            "thinking": "Let me think...",
        }
    ]
    converted = to_ollama_messages(messages)
    assert converted[0].get("thinking") == "Let me think..."


def test_tool_messages_pass_through():
    messages = [
        {"role": "tool", "content": "result", "name": "run_bash", "tool_call_id": "abc123"}
    ]
    converted = to_ollama_messages(messages)
    assert converted[0]["role"] == "tool"
    assert converted[0]["content"] == "result"
    assert converted[0]["tool_call_id"] == "abc123"
