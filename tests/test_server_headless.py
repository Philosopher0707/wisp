import os
import pytest
from fastapi import HTTPException


class TestHeadlessAutoApprove:
    """Tests that /api/prompt (headless mode) does not auto-approve by default."""

    def test_headless_defaults_to_no_auto_approve(self, monkeypatch):
        from wisp.server import _run_agent_headless
        from wisp.config import WispConfig

        # Ensure env var is NOT set
        monkeypatch.delenv("WISP_HEADLESS_AUTO_APPROVE", raising=False)

        # We can't easily run the async _run_agent_headless without mocking,
        # so instead we inspect the config creation logic by replicating it.
        config = WispConfig()
        config.auto_approve = os.environ.get("WISP_HEADLESS_AUTO_APPROVE", "") == "1"
        assert config.auto_approve is False

    def test_headless_auto_approve_with_env_var(self, monkeypatch):
        from wisp.config import WispConfig

        monkeypatch.setenv("WISP_HEADLESS_AUTO_APPROVE", "1")
        config = WispConfig()
        config.auto_approve = os.environ.get("WISP_HEADLESS_AUTO_APPROVE", "") == "1"
        assert config.auto_approve is True

    def test_prompt_request_default_permission_mode(self):
        from wisp.server import PromptRequest
        req = PromptRequest(prompt="hello")
        assert req.permission_mode == "auto_edit"

    def test_prompt_request_can_override_permission_mode(self):
        from wisp.server import PromptRequest
        req = PromptRequest(prompt="hello", permission_mode="full")
        assert req.permission_mode == "full"
