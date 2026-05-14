"""Regression tests for stream error recovery paths."""

import pytest
from unittest.mock import MagicMock

from wisp.stream_events import StreamError, TokenBatch


class TestStreamErrorRecovery:
    """Verify partial content is preserved when streams fail mid-generation."""

    def test_stream_error_preserves_partial_content(self):
        """When generate_stream_events yields StreamError, partial content
        must be preserved in client.stream_response so _arun can add it
        to the conversation history.
        """
        from wisp.core.agent import WispAgentCore
        from wisp.config import WispConfig

        config = WispConfig()
        config.model = "test"
        config.workspace = "/tmp"
        agent = WispAgentCore(config)
        agent.messages = [{"role": "user", "content": "hello"}]

        mock_client = MagicMock()
        mock_client.generate_stream_events.return_value = iter([
            TokenBatch(phase="content", text="Partial ", batch_index=0),
            TokenBatch(phase="content", text="output", batch_index=1),
            StreamError(
                phase="error",
                error_type="OllamaError",
                message="Connection dropped during streaming",
                partial_thinking="",
                partial_content="Partial output",
            ),
        ])
        agent.client = mock_client

        events = list(agent._run_turn_streaming_events("system"))

        # Must yield the partial content tokens
        content_events = [e for e in events if e.type == "content"]
        texts = "".join(e.text for e in content_events)
        assert texts == "Partial output"

        # Must have stored partial content in stream_response
        resp = agent.client.stream_response
        assert resp.get("_stream_error") is True
        assert resp["_error_type"] == "OllamaError"
        assert resp["message"]["content"] == "Partial output"

    def test_stream_error_adds_assistant_message_to_history(self):
        """_arun should add the assistant's partial content to messages
        before breaking out, keeping the conversation valid.
        """
        from wisp.core.agent import WispAgentCore
        from wisp.config import WispConfig

        config = WispConfig()
        config.model = "test"
        config.workspace = "/tmp"
        agent = WispAgentCore(config)
        agent.messages = [{"role": "user", "content": "hi"}]

        mock_client = MagicMock()
        mock_client.generate_stream_events.return_value = iter([
            StreamError(
                phase="error",
                error_type="TimeoutError",
                message="timed out",
                partial_thinking="",
                partial_content="I was about to say that",
            ),
        ])
        agent.client = mock_client

        import asyncio

        async def collect():
            return [e async for e in agent._arun("hi", system="s")]

        events = asyncio.run(collect())

        error_events = [e for e in events if e.type == "error"]
        assert len(error_events) == 1
        msg = error_events[0].data.get("message", "")
        assert "Stream error (TimeoutError)" in msg
        assert "Partial output" in msg

        # Key assertion: assistant message added so conversation is valid
        assert len(agent.messages) == 3  # user(orig) + user(new) + assistant(partial)
        assert agent.messages[2]["role"] == "assistant"
        assert "I was about to say that" in str(agent.messages[2].get("content", ""))

    def test_midstream_drop_error_message(self):
        """When a ConnectionError happens mid-stream (after events yielded), the
        error should NOT say 'Cannot connect -- Is Ollama running?'.
        """
        from wisp.ollama_client import OllamaClient
        from wisp.stream_events import StreamError
        from wisp.config import WispConfig
        import json

        config = WispConfig()
        config.model = "test"
        config.ollama_url = "http://localhost:9999"
        client = OllamaClient(config)
        mock_msg = [{"role": "user", "content": "hi"}]

        attempt = [0]

        def raising_post(*args, **kwargs):
            attempt[0] += 1
            if attempt[0] == 1:

                class R1:
                    def raise_for_status(self):
                        pass

                    def iter_content(self, chunk_size=None):
                        # Real Ollama NDJSON ends each line with newline
                        yield json.dumps({"message": {"content": "x"}}).encode() + b"\n"
                        # Mid-stream drop
                        import requests

                        raise requests.exceptions.ConnectionError(
                            "remote end closed"
                        )

                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        return False

                return R1()
            # Second attempt -- simulate a real connection error
            raise ConnectionError("cannot connect")

        client._session.post = raising_post

        events = list(client.generate_stream_events("sys", mock_msg))

        # Should get one content token, then a StreamError -- NOT an OllamaError raise
        assert len(events) == 2
        assert events[0].phase == "content"
        assert events[1].phase == "error"
        assert isinstance(events[1], StreamError)
        msg = events[1].message
        assert (
            "interrupted" in msg or "dropped" in msg.lower()
        ), f"Expected 'interrupted/dropped' but got: {msg}"
        assert "Is Ollama running" not in msg
        assert events[1].partial_content == "x"

    def test_nonstream_retry_on_timeout(self):
        """_post_with_retry should retry on timeout and eventually raise."""
        from wisp.ollama_client import OllamaClient, OllamaError
        from wisp.config import WispConfig
        import requests

        config = WispConfig()
        config.model = "test"
        config.ollama_url = "http://localhost:9999"
        client = OllamaClient(config=config)
        call_count = [0]

        def slow_post(*args, **kwargs):
            call_count[0] += 1
            raise requests.exceptions.Timeout("Request timed out")

        client._session.post = slow_post

        with pytest.raises(OllamaError) as exc_info:
            client._post_with_retry("chat", {}, timeout=1)

        assert "timed out after 1s" in str(exc_info.value)
        assert call_count[0] >= 1
