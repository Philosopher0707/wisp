import os
import pytest
import asyncio
import threading
from fastapi import HTTPException


class TestHeadlessAutoApprove:
    """Tests that /api/prompt (headless mode) does not auto-approve by default."""

    def test_headless_defaults_to_no_auto_approve(self, monkeypatch):
        from wisp.server import _run_agent_headless
        from wisp.config import WispConfig

        # Ensure env var is NOT set
        monkeypatch.delenv("WISP_HEADLESS_AUTO_APPROVE", raising=False)

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


class TestHeadlessPool:
    """Tests for the deprecated headless pool.

    The pool is now deprecated — _run_agent_headless delegates to
    wisp.entry.run_headless() which uses CompositionRoot.
    """

    def test_get_headless_core_returns_none(self):
        """_get_headless_core is deprecated and returns None."""
        from wisp.server import _get_headless_core
        from wisp.config import WispConfig

        config = WispConfig()
        result = _get_headless_core(config)
        assert result is None

    def test_shutdown_is_idempotent(self):
        """Multiple shutdown calls do not crash."""
        from wisp.server import _shutdown_headless_pool

        _shutdown_headless_pool()
        _shutdown_headless_pool()  # should not raise
