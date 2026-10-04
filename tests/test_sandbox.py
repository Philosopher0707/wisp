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

    def test_the_default_image_matches_the_projects_python_minor(self):
        """The prompt advertises a Python version. A sandbox on a different minor makes that
        statement false for the environment the executing tools actually run in — the agent reads
        `Python: 3.12.8`, then `run_bash` gives it whatever the image has."""
        import sys

        from wisp.sandbox import _DEFAULT_SANDBOX_IMAGE

        minor = ".".join(sys.version.split()[0].split(".")[:2])
        assert minor in _DEFAULT_SANDBOX_IMAGE, (
            f"the sandbox default is {_DEFAULT_SANDBOX_IMAGE!r} but this project runs {minor}")

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


class TestSandboxNetwork:
    """Egress is fail-closed (`none`) unless the operator opens it explicitly."""

    def test_default_is_none(self, tmp_path, monkeypatch):
        from wisp.sandbox import DockerSandbox, docker_run_args, sandbox_network

        monkeypatch.delenv("WISP_SANDBOX_NETWORK", raising=False)
        assert sandbox_network() == "none"
        args = docker_run_args("c", str(tmp_path), "img", "2g", "2")
        assert args[args.index("--network") + 1] == "none"
        assert DockerSandbox(str(tmp_path)).network == "none"

    def test_operator_can_open_it(self, tmp_path, monkeypatch):
        from wisp.sandbox import DockerSandbox, docker_run_args, sandbox_network

        monkeypatch.setenv("WISP_SANDBOX_NETWORK", "bridge")
        assert sandbox_network() == "bridge"
        args = docker_run_args("c", str(tmp_path), "img", "2g", "2",
                               DockerSandbox(str(tmp_path)).network)
        assert args[args.index("--network") + 1] == "bridge"

    def test_blank_falls_back_to_none(self, monkeypatch):
        import wisp.sandbox as sandbox

        monkeypatch.setenv("WISP_SANDBOX_NETWORK", "   ")
        assert sandbox.sandbox_network() == "none"

    def test_an_explicit_network_still_wins(self, tmp_path, monkeypatch):
        from wisp.sandbox import DockerSandbox

        monkeypatch.setenv("WISP_SANDBOX_NETWORK", "bridge")
        assert DockerSandbox(str(tmp_path), network="mynet").network == "mynet"


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


class TestTheSandboxRecipeKeepsItsThreeConstraints:
    """The image recipe lives in the repo so it is reproducible, and it encodes three constraints.

    A test on a Dockerfile is unusual, but each constraint was learned by a real failure and none of
    them is visible from the Python side — the file is the only place they can be pinned without a
    Docker daemon in CI.
    """

    RECIPE = Path(__file__).resolve().parents[1] / "docker" / "wisp-sandbox.Dockerfile"

    def test_the_recipe_exists(self):
        assert self.RECIPE.is_file(), "the sandbox image recipe must be reproducible from the repo"

    def test_it_is_based_on_an_image_with_an_interpreter(self):
        first_from = next(line for line in self.RECIPE.read_text().splitlines()
                          if line.startswith("FROM "))
        assert "python" in first_from, (
            "a bare OS image has no interpreter, so the prompt's suggested command cannot run")

    def test_it_reads_the_dependencies_from_pyproject(self):
        text = self.RECIPE.read_text()
        assert "pyproject.toml" in text, (
            "retyping the dependency list lets it drift from the project's declared sets")

    def test_it_does_not_bake_a_copy_of_the_source(self):
        """`-v <workspace>:/workspace` wins on sys.path, so a baked copy is at best dead weight and at
        worst a stale shadow of the real source.

        Asserted over the **instructions**, not the file text — the header comment deliberately names
        the thing it forbids, and the first version of this test failed on that comment rather than on
        any instruction.
        """
        instructions = [line for line in self.RECIPE.read_text().splitlines()
                        if line.strip() and not line.lstrip().startswith("#")]
        assert not any("pip install -e ." in line for line in instructions)
