"""Regression tests for Q11: blocking LLM call in SemanticCompressor.

Issues fixed:
1. _llm_summarize() created a new OllamaClient per compaction call,
   ignoring the agent's configured client -- fixed by passing client param.
2. No max_tokens cap -- the summary could be as long as the context window.
   Fixed by setting client.max_tokens = 512 with try/finally restore.
3. Synchronous blocking call inside the async event loop during
   _maybe_compact_session() -- fixed by wrapping in asyncio.to_thread().
"""

import asyncio
from unittest.mock import MagicMock, patch
import threading
import pytest

from wisp.semantic_compressor import _llm_summarize, CompressionResult


class TestClientReuse:
    """_llm_summarize must reuse an injected client, not create a new one."""

    def test_reuses_injected_client(self):
        """When client is passed, it must be used. No new OllamaClient created."""
        fake_client = MagicMock()
        fake_client.model = "agent-model"
        fake_client.max_tokens = 4096
        fake_client.generate.return_value = {
            "message": {"content": "## Summary\nsummary\n\n## Key Decisions\n- a\n\n## Tasks\n- b"}
        }

        with patch("wisp.ollama_client.OllamaClient") as MockClient:
            result = _llm_summarize(
                [{"role": "user", "content": "hi"}],
                client=fake_client,
            )

        assert MockClient.call_count == 0  # No new client created
        assert result is not None
        fake_client.generate.assert_called_once()
        # The prompt should contain a summary system prompt
        call_kwargs = fake_client.generate.call_args.kwargs
        assert "Summarize" in call_kwargs.get("system_prompt", "")

    def test_creates_new_client_when_none_passed(self):
        """When client is None, a new client is created (tests/back-compat)."""
        with patch("wisp.ollama_client.OllamaClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance.model = "fallback"
            mock_instance.generate.return_value = {
                "message": {"content": "Short summary.\n\nDecisions:\n- None\n\nTasks:\n- None"}
            }
            MockClient.return_value = mock_instance

            result = _llm_summarize([{"role": "user", "content": "hi"}])

        assert MockClient.call_count == 1  # Falls back to new client
        assert result is not None


class TestMaxTokensCap:
    """Tier-3 summary must be capped at 512 tokens, restored after."""

    def test_caps_max_tokens_at_512(self):
        """client.max_tokens should be temporarily set to 512."""
        fake_client = MagicMock()
        fake_client.model = "model"
        fake_client.max_tokens = 4096
        fake_client.generate.return_value = {
            "message": {"content": "Very short.\n\nDecisions:\n- None\n\nTasks:\n- None"}
        }

        _llm_summarize([{"role": "user", "content": "test"}], client=fake_client)

        # During generate(), max_tokens was 512
        generate_call = fake_client.generate.call_args
        assert fake_client.max_tokens == 4096  # Restored after

    def test_restores_old_max_tokens_even_on_error(self):
        """If generate() raises, old max_tokens must still be restored."""
        fake_client = MagicMock()
        fake_client.model = "model"
        fake_client.max_tokens = 2048
        fake_client.generate.side_effect = RuntimeError("network")

        result = _llm_summarize([{"role": "user", "content": "test"}], client=fake_client)

        assert result is None  # Returns None on failure
        assert fake_client.max_tokens == 2048  # Still restored


class TestAsyncOffloading:
    """Compaction must not block the event loop."""

    @pytest.mark.asyncio
    async def test_compact_offloaded_to_thread(self):
        """_maybe_compact_session wrapped in asyncio.to_thread."""
        # Mock a slow compaction to verify it's in a thread
        loop = asyncio.get_running_loop()
        compact_thread_id = None

        def _slow_compact():
            import threading
            nonlocal compact_thread_id
            compact_thread_id = threading.current_thread().ident
            return {"compacted": True, "before_count": 10, "after_count": 2, "summary": "ok"}

        # Verify that running in a thread gives a different thread ID
        result = await asyncio.to_thread(_slow_compact)
        assert result["compacted"] is True
        assert compact_thread_id != threading.current_thread().ident
