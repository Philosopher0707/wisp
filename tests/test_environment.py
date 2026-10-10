"""Tests for dynamic environment grounding (wisp/environment.py)."""

import subprocess
from pathlib import Path

from wisp.environment import (
    EnvironmentSnapshot,
    collect_environment,
    format_environment_block,
)


class TestCollectEnvironment:
    def test_cwd_is_resolved_workspace(self, tmp_path: Path):
        snap = collect_environment(str(tmp_path))
        assert snap.cwd == str(tmp_path.resolve())

    def test_os_and_python_detected(self, tmp_path: Path):
        snap = collect_environment(str(tmp_path))
        assert snap.os_name  # non-empty on any platform
        assert snap.python_version

    def test_git_state_in_repo(self, tmp_path: Path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "--allow-empty", "-qm", "init"],
                       cwd=tmp_path, check=True)
        snap = collect_environment(str(tmp_path))
        assert snap.git_branch
        assert len(snap.git_commit) == 7  # short hash

    def test_no_git_outside_repo(self, tmp_path: Path):
        snap = collect_environment(str(tmp_path))
        assert snap.git_branch == ""
        assert snap.git_commit == ""

    def test_package_managers_from_markers(self, tmp_path: Path):
        (tmp_path / "uv.lock").write_text("")
        (tmp_path / "package-lock.json").write_text("{}")
        snap = collect_environment(str(tmp_path))
        assert "uv" in snap.package_managers
        assert "npm" in snap.package_managers
        assert "cargo" not in snap.package_managers

    def test_pip_not_duplicated_by_two_markers(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("")
        (tmp_path / "pyproject.toml").write_text("")
        snap = collect_environment(str(tmp_path))
        assert snap.package_managers.count("pip") == 1

    def test_verification_commands_for_pytest_project(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("")
        snap = collect_environment(str(tmp_path))
        assert "python -m pytest tests/ -x -q" in snap.verification_commands

    def test_verification_commands_from_package_json_scripts(self, tmp_path: Path):
        import json
        (tmp_path / "package.json").write_text(json.dumps(
            {"scripts": {"test": "vitest", "lint": "eslint ."}}))
        snap = collect_environment(str(tmp_path))
        assert "npm test" in snap.verification_commands
        assert "npm run lint" in snap.verification_commands

    def test_no_verification_commands_when_nothing_detected(self, tmp_path: Path):
        snap = collect_environment(str(tmp_path))
        assert snap.verification_commands == ()


class TestFormatEnvironmentBlock:
    def test_contains_header_and_cwd(self):
        snap = EnvironmentSnapshot(cwd="/tmp/proj")
        block = format_environment_block(snap)
        assert block.startswith("## Environment")
        assert "/tmp/proj" in block

    def test_git_line_with_commit(self):
        snap = EnvironmentSnapshot(cwd="/p", git_branch="main", git_commit="abc1234")
        block = format_environment_block(snap)
        assert "- git: main @ abc1234" in block

    def test_package_managers_line(self):
        snap = EnvironmentSnapshot(cwd="/p", package_managers=("uv", "npm"))
        block = format_environment_block(snap)
        assert "- package managers: uv, npm" in block

    def test_verification_commands_listed(self):
        snap = EnvironmentSnapshot(
            cwd="/p", verification_commands=("python -m pytest", "ruff check ."))
        block = format_environment_block(snap)
        assert "- suggested verification commands:" in block
        assert "`python -m pytest`" in block
        assert "`ruff check .`" in block

    def test_minimal_snapshot_omits_empty_fields(self):
        snap = EnvironmentSnapshot(cwd="/p")
        block = format_environment_block(snap)
        assert "git:" not in block
        assert "package managers:" not in block
        assert "suggested verification" not in block


class TestTheEnvironmentBlockSaysWhereItApplies:
    """The block describes the **host**. `run_bash` and `run_tests` do not run there.

    An unqualified `Python: 3.12.8` reads as "the Python you will get", and it is not — the sandbox
    has its own interpreter. That mismatch is what let an agent run `python -m pytest` against a
    container with no interpreter at all and read the failure as its own.
    """

    def _block(self, tmp_path):
        from wisp.environment import collect_environment, format_environment_block

        return format_environment_block(collect_environment(str(tmp_path)))

    def test_the_python_line_is_labelled_host(self, tmp_path):
        block = self._block(tmp_path)
        assert "- Python (host):" in block, "an unqualified Python line reads as the sandbox's"
        assert "- Python: " not in block

    def test_it_says_the_executing_tools_are_sandboxed(self, tmp_path):
        block = self._block(tmp_path)
        assert "run_bash" in block and "sandbox" in block.lower()


class TestVerificationCommandsFollowWhereTheTestsAre:
    """The detected pytest command named `tests/` whenever any pytest marker existed, so a project with its tests at the root got a command that exits 4 (no such directory)."""

    def test_tests_at_the_root_get_a_command_that_finds_them(self, tmp_path: Path):
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        (tmp_path / "test_totals.py").write_text("def test_a():\n    assert True\n")
        snap = collect_environment(str(tmp_path))
        assert "python -m pytest -x -q" in snap.verification_commands
        assert not any("tests/" in c for c in snap.verification_commands)

    def test_a_tests_directory_is_still_named(self, tmp_path: Path):
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("")
        snap = collect_environment(str(tmp_path))
        assert "python -m pytest tests/ -x -q" in snap.verification_commands
        assert "python -m pytest -x -q" not in snap.verification_commands

    def test_the_detected_command_really_runs_the_root_level_tests(self, tmp_path: Path):
        import subprocess
        import sys

        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        (tmp_path / "test_totals.py").write_text("def test_a():\n    assert True\n")
        command = next(c for c in collect_environment(str(tmp_path)).verification_commands if "pytest" in c)
        proc = subprocess.run([sys.executable, *command.split()[1:]], cwd=tmp_path, capture_output=True, text=True)
        assert proc.returncode == 0 and "1 passed" in proc.stdout

    def test_a_lone_test_file_at_the_root_is_enough_to_suggest_pytest(self, tmp_path: Path):
        (tmp_path / "test_totals.py").write_text("def test_a():\n    assert True\n")
        assert "python -m pytest -x -q" in collect_environment(str(tmp_path)).verification_commands

    def test_a_lone_underscore_test_file_at_the_root_is_enough_too(self, tmp_path: Path):
        (tmp_path / "totals_test.py").write_text("def test_a():\n    assert True\n")
        assert "python -m pytest -x -q" in collect_environment(str(tmp_path)).verification_commands
