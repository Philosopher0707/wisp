"""The pre-push hook is advisory by default and blocking only when WISP_FLEET_ENFORCE=1."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / ".githooks" / "pre-push"


def _manifest_with_a_remoteless_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "orch"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    manifest = tmp_path / "wisp.fleet.toml"
    manifest.write_text(f'[[repo]]\nname = "orch"\npath = "{repo}"\nrole = "orchestrator"\n')
    return manifest


def _run(tmp_path: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "WISP_FLEET_ENFORCE"}
    env |= {
        "WISP_FLEET_MANIFEST": str(_manifest_with_a_remoteless_repo(tmp_path)),
        "WISP_FLEET_CMD": f"{sys.executable} -m wisp",
        **extra,
    }
    return subprocess.run([str(HOOK)], env=env, capture_output=True, text=True, check=False)


def test_advisory_by_default_reports_but_does_not_block(tmp_path):
    result = _run(tmp_path)
    assert result.returncode == 0
    assert "no-remote" in result.stderr


def test_enforce_blocks_when_a_repo_has_a_problem(tmp_path):
    result = _run(tmp_path, WISP_FLEET_ENFORCE="1")
    assert result.returncode == 1
    assert "push blocked" in result.stderr
