import pytest
import tempfile
import shutil
from pathlib import Path


@pytest.fixture
def sandbox_workspace():
    tmp = Path(tempfile.mkdtemp())
    yield tmp
    shutil.rmtree(str(tmp), ignore_errors=True)


class TestNoopSandboxDangerousCommand:
    """Tests that NoopSandbox blocks dangerous commands even when called directly."""

    @pytest.mark.asyncio
    async def test_noop_blocks_rm_rf(self, sandbox_workspace):
        from wisp.sandbox import NoopSandbox
        sandbox = NoopSandbox(str(sandbox_workspace))
        exit_code, stdout, stderr = await sandbox.run("rm -rf /")
        assert exit_code == -1
        assert "Dangerous command blocked" in stderr
        assert "recursive deletion" in stderr

    @pytest.mark.asyncio
    async def test_noop_blocks_sudo(self, sandbox_workspace):
        from wisp.sandbox import NoopSandbox
        sandbox = NoopSandbox(str(sandbox_workspace))
        exit_code, stdout, stderr = await sandbox.run("sudo apt update")
        assert exit_code == -1
        assert "Dangerous command blocked" in stderr
        assert "privilege escalation" in stderr

    @pytest.mark.asyncio
    async def test_noop_blocks_eval(self, sandbox_workspace):
        from wisp.sandbox import NoopSandbox
        sandbox = NoopSandbox(str(sandbox_workspace))
        exit_code, stdout, stderr = await sandbox.run("eval $(echo 'rm -rf /')")
        assert exit_code == -1
        assert "Dangerous command blocked" in stderr
        assert "dynamic code execution" in stderr

    @pytest.mark.asyncio
    async def test_noop_allows_safe_command(self, sandbox_workspace):
        from wisp.sandbox import NoopSandbox
        sandbox = NoopSandbox(str(sandbox_workspace))
        exit_code, stdout, stderr = await sandbox.run("echo hello")
        assert exit_code == 0
        assert "hello" in stdout
        assert stderr == ""
