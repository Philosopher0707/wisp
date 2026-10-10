"""`wisp review` and `wisp triage` through the real entry point: registered like every subcommand, and run as a real process in a real repository."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.review.helpers import fake_token

REPO = str(Path(__file__).resolve().parents[2])


def test_both_commands_are_registered_with_help():
    from wisp.__main__ import _SUBCOMMAND_HELP, _SUBCOMMAND_NAMES, print_subcommand_help

    for name in ("review", "triage"):
        assert name in _SUBCOMMAND_NAMES and name in _SUBCOMMAND_HELP and print_subcommand_help(name)


def run_wisp(cwd: Path, *argv: str, home: Path, extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", ""), "HOME": str(home), "PYTHONPATH": REPO, "TERM": "dumb", "WISP_PROVIDER": "mock", "WISP_MODEL": "mock/m", **(extra or {})}
    return subprocess.run([sys.executable, "-m", "wisp", *argv], cwd=cwd, env=env, capture_output=True, text=True, timeout=180)


@pytest.fixture
def repo(tmp_path):
    work = tmp_path / "work"
    work.mkdir()

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=work, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    (work / "app.py").write_text("def load(path):\n    return open(path).read()\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    (tmp_path / "home").mkdir()
    return work


def test_help_exits_zero(repo):
    proc = run_wisp(repo, "review", "--help", home=repo.parent / "home")
    assert proc.returncode == 0 and "--staged" in proc.stdout and "--no-model" in proc.stdout


def test_a_secret_blocks_with_exit_one_and_is_not_printed(repo):
    token = fake_token("openrouter")
    (repo / "cfg.py").write_text(f"KEY = '{token}'\n")
    proc = run_wisp(repo, "review", "--no-model", home=repo.parent / "home")
    assert proc.returncode == 1 and "BLOCKED" in proc.stdout and token not in proc.stdout + proc.stderr


def test_a_clean_change_exits_zero_as_json(repo):
    (repo / "app.py").write_text("def load(path):\n    return open(path).read().strip()\n")
    proc = run_wisp(repo, "review", "--no-model", "--json", home=repo.parent / "home")
    import json

    assert proc.returncode == 0 and json.loads(proc.stdout)["verdict"] == "clean"


def test_the_global_workspace_flag_is_honoured(repo, tmp_path):
    (repo / "cfg.py").write_text(f"KEY = '{fake_token('github')}'\n")
    proc = run_wisp(tmp_path, "review", "-w", str(repo), "--no-model", home=repo.parent / "home")
    assert proc.returncode == 1 and "BLOCKED" in proc.stdout


def test_asking_for_the_model_when_it_cannot_answer_is_incomplete_never_clean(repo):
    """The mock provider answers in prose, not the JSON a lens needs: the review must say it could not use the answer."""
    (repo / "app.py").write_text("def load(path):\n    return open(path).read().strip()\n")
    proc = run_wisp(repo, "review", "--lens", "tests", home=repo.parent / "home")
    assert proc.returncode == 0 and "INCOMPLETE" in proc.stdout and "tests:" in proc.stdout
    strict = run_wisp(repo, "review", "--lens", "tests", "--fail-on", "incomplete", home=repo.parent / "home")
    assert strict.returncode == 1


def test_a_usage_error_exits_two_without_a_traceback(repo):
    proc = run_wisp(repo, "review", "--staged", "--base", "main", home=repo.parent / "home")
    assert proc.returncode == 2 and "cannot be combined" in proc.stderr and "Traceback" not in proc.stderr


def test_triage_without_gh_says_so_and_exits_two(repo):
    proc = run_wisp(repo, "triage", home=repo.parent / "home", extra={"PATH": os.path.dirname(sys.executable)})
    assert proc.returncode == 2 and "gh" in proc.stderr and "Traceback" not in proc.stderr
