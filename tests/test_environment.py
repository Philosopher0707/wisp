"""Tests for dynamic environment grounding (wisp/environment.py)."""

import os
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
