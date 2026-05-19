"""TDD for run_headless() in wisp.entry.

Tests the headless execution path using HeadlessTransport + CompositionRoot.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock


class TestRunHeadless:
    """run_headless() executes prompts without I/O."""

    @pytest.fixture(autouse=True)
    def _clear_headless_cache(self):
        """Clear the module-level headless root cache between tests."""
        from wisp import entry
        entry._headless_root = None
        yield
        entry._headless_root = None

    @pytest.mark.asyncio
    async def test_run_headless_returns_result(self):
        from wisp.entry import run_headless

        with patch("wisp.entry.CompositionRoot") as mock_root:
            with patch("wisp.entry.WispConfig") as mock_config:
                config_instance = MagicMock()
                config_instance.model = "test-model"
                config_instance.workspace = "/tmp"
                config_instance.permission_mode = "full"
                config_instance.auto_approve = True
                config_instance.show_thinking = True
                mock_config.return_value = config_instance

                runtime = MagicMock()
                session = {"id": "test-session", "messages": []}

                async def _mock_turn(session, prompt):
                    yield {"type": "content", "text": "Hello"}
                    yield {"type": "done", "turns": 1}

                runtime.run_turn = _mock_turn
                runtime.get_or_create_session = AsyncMock(return_value=session)

                root_instance = MagicMock()
                root_instance.runtime = runtime
                root_instance.config = config_instance
                mock_root.return_value = root_instance

                result = await run_headless("test prompt", model="test-model", workspace="/tmp")

                assert result["ok"] is True
                assert result["content"] == "Hello"
                assert result["iterations"] == 1
                assert result["prompt"] == "test prompt"
                assert result["model"] == "test-model"

    @pytest.mark.asyncio
    async def test_run_headless_handles_errors(self):
        from wisp.entry import run_headless

        with patch("wisp.entry.CompositionRoot") as mock_root:
            with patch("wisp.entry.WispConfig") as mock_config:
                config_instance = MagicMock()
                config_instance.model = "test-model"
                config_instance.workspace = "/tmp"
                config_instance.permission_mode = "full"
                config_instance.auto_approve = True
                config_instance.show_thinking = True
                mock_config.return_value = config_instance

                runtime = MagicMock()
                session = {"id": "test-session", "messages": []}

                async def _mock_turn(session, prompt):
                    yield {"type": "content", "text": "Working..."}
                    yield {"type": "error", "message": "Something failed", "recoverable": False}
                    yield {"type": "done"}

                runtime.run_turn = _mock_turn
                runtime.get_or_create_session = AsyncMock(return_value=session)

                root_instance = MagicMock()
                root_instance.runtime = runtime
                root_instance.config = config_instance
                mock_root.return_value = root_instance

                result = await run_headless("test prompt")

                assert result["ok"] is False
                assert result["content"] == "Working..."
                assert len(result["errors"]) == 1
                assert result["errors"][0]["message"] == "Something failed"

    @pytest.mark.asyncio
    async def test_run_headless_uses_session_id(self):
        from wisp.entry import run_headless

        with patch("wisp.entry.CompositionRoot") as mock_root:
            with patch("wisp.entry.WispConfig") as mock_config:
                config_instance = MagicMock()
                config_instance.model = "test-model"
                config_instance.workspace = "/tmp"
                config_instance.permission_mode = "full"
                config_instance.auto_approve = True
                config_instance.show_thinking = True
                mock_config.return_value = config_instance

                runtime = MagicMock()
                session = {"id": "existing-session", "messages": []}

                async def _mock_turn(session, prompt):
                    yield {"type": "done"}

                runtime.run_turn = _mock_turn
                runtime.get_or_create_session = AsyncMock(return_value=session)

                root_instance = MagicMock()
                root_instance.runtime = runtime
                root_instance.config = config_instance
                mock_root.return_value = root_instance

                result = await run_headless("test", session_id="existing-session")

                assert result["session_id"] == "existing-session"
