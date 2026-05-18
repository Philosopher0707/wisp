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
    """Tests for the WispAgentCore pooling in headless mode.

    The pool avoids recreating WispAgentCore on every /api/prompt call,
    saving the cost of health checks, hook loading, and manager spin-up.
    """

    @pytest.fixture(autouse=True)
    def _clear_pool(self):
        """Drain the pool between tests so state doesn't leak."""
        yield
        from wisp.server import _shutdown_headless_pool
        _shutdown_headless_pool()

    def test_pool_caches_core_for_same_config(self):
        """Two calls with identical config return the same WispAgentCore."""
        # We can't run _run_agent_headless without a real model, so we test
        # the pool primitives directly.
        from wisp.server import _get_headless_core, _HEADLESS_POOL, _shutdown_headless_pool
        from wisp.config import WispConfig

        _shutdown_headless_pool()
        assert len(_HEADLESS_POOL) == 0

        config = WispConfig()
        config.model = "qwen2.5-coder"
        config.permission_mode = "auto_edit"
        config.workspace = "/tmp"

        core1 = _get_headless_core(config)
        assert len(_HEADLESS_POOL) == 1
        core2 = _get_headless_core(config)
        assert core1 is core2, "Pool should return the same instance"
        assert len(_HEADLESS_POOL) == 1, "Pool should contain exactly one entry"

    def test_pool_creates_separate_core_for_different_model(self):
        """Different model = different pool entry."""
        from wisp.server import _get_headless_core, _HEADLESS_POOL
        from wisp.config import WispConfig

        config_a = WispConfig()
        config_a.model = "model-a"
        config_a.permission_mode = "auto_edit"
        config_a.workspace = "/tmp"

        config_b = WispConfig()
        config_b.model = "model-b"
        config_b.permission_mode = "auto_edit"
        config_b.workspace = "/tmp"

        core_a = _get_headless_core(config_a)
        core_b = _get_headless_core(config_b)
        assert core_a is not core_b
        assert len(_HEADLESS_POOL) == 2

    def test_pool_creates_separate_core_for_different_permission_mode(self):
        """Different permission_mode = different pool entry."""
        from wisp.server import _get_headless_core, _HEADLESS_POOL
        from wisp.config import WispConfig

        config = WispConfig()
        config.model = "qwen2.5-coder"
        config.permission_mode = "auto_edit"
        config.workspace = "/tmp"

        pem = "full"
        config.permission_mode = pem
        core = _get_headless_core(config)
        assert len(_HEADLESS_POOL) == 1

        config.permission_mode = "sandbox"
        core2 = _get_headless_core(config)
        assert len(_HEADLESS_POOL) == 2
        assert core is not core2

    def test_pool_creates_separate_core_for_different_workspace(self):
        """Different workspace = different pool entry."""
        from wisp.server import _get_headless_core, _HEADLESS_POOL
        from wisp.config import WispConfig

        config_a = WispConfig()
        config_a.model = "qwen2.5-coder"
        config_a.permission_mode = "auto_edit"
        config_a.workspace = "/tmp/a"

        config_b = WispConfig()
        config_b.model = "qwen2.5-coder"
        config_b.permission_mode = "auto_edit"
        config_b.workspace = "/tmp/b"

        core_a = _get_headless_core(config_a)
        core_b = _get_headless_core(config_b)
        assert core_a is not core_b
        assert len(_HEADLESS_POOL) == 2

    def test_shutdown_drains_all_entries(self):
        """_shutdown_headless_pool clears and closes all pooled cores."""
        from wisp.server import _get_headless_core, _HEADLESS_POOL, _shutdown_headless_pool
        from wisp.config import WispConfig

        config1 = WispConfig()
        config1.model = "m1"
        config1.permission_mode = "auto_edit"
        config1.workspace = "/tmp/x"

        config2 = WispConfig()
        config2.model = "m2"
        config2.permission_mode = "sandbox"
        config2.workspace = "/tmp/x"

        _get_headless_core(config1)
        _get_headless_core(config2)
        assert len(_HEADLESS_POOL) == 2

        _shutdown_headless_pool()
        assert len(_HEADLESS_POOL) == 0

    def test_pool_is_thread_safe(self):
        """Concurrent calls with the same key produce a single core."""
        from wisp.server import _get_headless_core, _HEADLESS_POOL, _shutdown_headless_pool
        from wisp.config import WispConfig

        _shutdown_headless_pool()

        config = WispConfig()
        config.model = "qwen2.5-coder"
        config.permission_mode = "auto_edit"
        config.workspace = "/tmp"

        cores: list = []

        def _worker():
            cores.append(_get_headless_core(config))

        threads = [threading.Thread(target=_worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(_HEADLESS_POOL) == 1, "Only one core should exist"
        assert all(c is cores[0] for c in cores), "All threads should get the same instance"

    def test_shutdown_is_idempotent(self):
        """Multiple shutdown calls do not crash."""
        from wisp.server import _shutdown_headless_pool

        _shutdown_headless_pool()
        _shutdown_headless_pool()  # should not raise
