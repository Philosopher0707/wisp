"""agent/tools/runner.py sink patch — contract and lifecycle (F14).

install_sink() globally replaces wisp.tools.bash.async_tool_run_bash.
The replacement must honor the original contract (executor-style keyword
calls, input validation, danger checks) and must be reversible, or every
test running after any CompositionRoot construction observes different
tool behavior with no way back.
"""

import asyncio

import pytest


def _unpatched():
    import wisp.tools.bash as bashmod
    from agent.tools.runner import uninstall_sink

    uninstall_sink()
    return bashmod.async_tool_run_bash


@pytest.fixture
def clean_sink():
    """Guarantee the global patch is removed after each test."""
    yield
    _unpatched()


class TestWrappedContract:
    def test_accepts_executor_style_kwargs(self, tmp_path, clean_sink):
        """F14b: ToolExecutor calls with command=/workspace=/timeout= keywords."""
        from agent.tools.runner import install_sink
        import wisp.tools.bash as bashmod

        install_sink()
        result = asyncio.run(bashmod.async_tool_run_bash(
            command="echo sink-kw-ok", workspace=str(tmp_path), timeout=10))
        assert isinstance(result, str)
        assert "sink-kw-ok" in result

    def test_enforces_danger_check(self, tmp_path, clean_sink):
        """F14c: the sink path must not drop the tool-level danger gate."""
        from unittest.mock import AsyncMock, patch

        from agent.tools.runner import install_sink
        import wisp.tools.bash as bashmod
        from wisp.tools.errors import ToolError

        install_sink()
        with patch("agent.tools.runner.run_bash_with_sink",
                   new=AsyncMock()) as sink_mock:
            with pytest.raises(ToolError, match="Dangerous command blocked"):
                asyncio.run(bashmod.async_tool_run_bash(
                    command="sudo rm -rf /tmp/whatever", workspace=str(tmp_path)))
        sink_mock.assert_not_called()

    def test_rejects_null_bytes(self, tmp_path, clean_sink):
        from agent.tools.runner import install_sink
        import wisp.tools.bash as bashmod
        from wisp.tools.errors import ToolError

        install_sink()
        with pytest.raises(ToolError, match="Null bytes"):
            asyncio.run(bashmod.async_tool_run_bash(
                command="echo hi\x00bye", workspace=str(tmp_path)))


class TestSinkLifecycle:
    def test_uninstall_restores_original(self, clean_sink):
        import wisp.tools.bash as bashmod
        from agent.tools.runner import install_sink, uninstall_sink

        orig = _unpatched()
        install_sink()
        assert getattr(bashmod.async_tool_run_bash, "_sink_patched", False) is True
        uninstall_sink()
        assert bashmod.async_tool_run_bash is orig

    def test_uninstall_without_install_is_safe(self, clean_sink):
        from agent.tools.runner import uninstall_sink

        _unpatched()
        uninstall_sink()  # must not raise

    def test_reinstall_after_uninstall_rewraps_original(self, tmp_path, clean_sink):
        """A second composition lifecycle must patch the true original."""
        import asyncio

        import wisp.tools.bash as bashmod
        from agent.tools.runner import install_sink, uninstall_sink

        orig = _unpatched()
        install_sink()
        uninstall_sink()
        install_sink()
        try:
            assert getattr(bashmod.async_tool_run_bash, "_sink_patched", False) is True
            assert bashmod.async_tool_run_bash._orig is orig
            result = asyncio.run(bashmod.async_tool_run_bash(
                command="echo rewrap-ok", workspace=str(tmp_path), timeout=10))
            assert "rewrap-ok" in result
        finally:
            uninstall_sink()

    def test_composition_shutdown_uninstalls(self, tmp_path, clean_sink):
        """Building + shutting down a root must leave tool globals pristine."""
        import wisp.tools.bash as bashmod
        from wisp.config import WispConfig
        from wisp.composition import CompositionRoot

        orig = _unpatched()
        cfg = WispConfig().replace(workspace=str(tmp_path))
        root = CompositionRoot(config=cfg)
        try:
            assert getattr(bashmod.async_tool_run_bash, "_sink_patched", False) is True
        finally:
            root.shutdown()
        assert bashmod.async_tool_run_bash is orig
