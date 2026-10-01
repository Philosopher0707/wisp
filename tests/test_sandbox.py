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


class TestTheSandboxCanRunTheProjectsTests:
    """The container IS the agent's working environment, and `--network none` means it can never
    install anything at run time — so whatever the prompt's suggested verification command needs must
    already be in the image.

    It was not. The default was `ubuntu:22.04`, which ships no interpreter at all: inside the sandbox
    `python3` was *command not found*, while the prompt advertised `Python: 3.12.8` and suggested
    `python -m pytest tests/ -x -q`. **The suggested command could not run in the environment the
    agent actually ran it in** — the verification loop could never close, and the model read
    "command not found" as a failure of its own change rather than of the environment it was handed.
    """

    def test_the_default_image_is_not_a_bare_os(self):
        from wisp.sandbox import _DEFAULT_SANDBOX_IMAGE

        assert _DEFAULT_SANDBOX_IMAGE != "ubuntu:22.04", (
            "a bare OS image has no interpreter, so the suggested verification command cannot run")
        assert "python" in _DEFAULT_SANDBOX_IMAGE

    def test_the_image_is_configurable(self, monkeypatch):
        import wisp.sandbox as sandbox

        monkeypatch.setenv("WISP_SANDBOX_IMAGE", "my/image:tag")
        assert sandbox.sandbox_image() == "my/image:tag"

    def test_an_empty_override_falls_back_to_the_default(self, monkeypatch):
        import wisp.sandbox as sandbox

        monkeypatch.setenv("WISP_SANDBOX_IMAGE", "   ")
        assert sandbox.sandbox_image() == sandbox._DEFAULT_SANDBOX_IMAGE

    def test_the_sandbox_uses_it(self, tmp_path):
        from wisp.sandbox import DockerSandbox, sandbox_image

        assert DockerSandbox(str(tmp_path)).image == sandbox_image()

    def test_an_explicit_image_still_wins(self, tmp_path):
        from wisp.sandbox import DockerSandbox

        assert DockerSandbox(str(tmp_path), image="x:y").image == "x:y"


class TestTheAgentBashGetsTheProjectsInterpreter:
    """Without the workspace's virtualenv on PATH, the scrubbed PATH resolves `python3` to whatever
    the host happens to have — on this machine the managed 3.13.12, **which has no pytest** — so the
    command the prompt suggests cannot run in the environment the agent runs it in.
    """

    def test_the_venv_goes_first(self, tmp_path, monkeypatch):
        import os

        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        from wisp.tools._utils_env import credential_free_env

        env, _ = credential_free_env(workspace=str(tmp_path))
        assert env["PATH"].split(os.pathsep)[0] == str(tmp_path / ".venv" / "bin")

    def test_no_venv_is_a_no_op(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        from wisp.tools._utils_env import credential_free_env

        env, _ = credential_free_env(workspace=str(tmp_path))
        assert env["PATH"] == "/usr/bin:/bin"

    def test_no_workspace_is_a_no_op(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        from wisp.tools._utils_env import credential_free_env

        env, _ = credential_free_env()
        assert env["PATH"] == "/usr/bin:/bin"

    def test_idempotent(self, tmp_path, monkeypatch):
        import os

        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        from wisp.tools._utils_env import credential_free_env

        once, _ = credential_free_env(workspace=str(tmp_path))
        twice, _ = credential_free_env(env=once, workspace=str(tmp_path))
        assert twice["PATH"] == once["PATH"]
        assert once["PATH"].split(os.pathsep).count(str(tmp_path / ".venv" / "bin")) == 1

    def test_credentials_are_still_stripped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("WISP_API_KEY", "secret")
        from wisp.tools._utils_env import credential_free_env

        env, stripped = credential_free_env(workspace=str(tmp_path))
        assert "WISP_API_KEY" not in env and stripped >= 1
