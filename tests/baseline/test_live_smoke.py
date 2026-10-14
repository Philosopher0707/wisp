"""The baseline against a real model: one coding task through `wisp --print` (marked `live`).

Uses the owner's provider settings (`~/.config/wisp/config.json` and `.env`, copied into a private
temporary HOME, never printed) and skips without them. CI excludes `live`. Run it with:

    python -m pytest -q -m live tests/baseline/test_live_smoke.py

First run, 2026-10-14, nvidia/nemotron-3-ultra-550b-a55b: both bugs fixed in 148 s, verified by
`run_tests` (2/2) inside Wisp and independently. On that machine `python` in `run_bash` was an
Anaconda env whose pytest crashed on a plugin, so the model spent 21 shell calls before using
`run_tests`; the test therefore requires a counted green run, not a particular tool.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

REAL = Path.home() / ".config" / "wisp"
CALC = "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a + b\n"
TESTS = ("from calc import add, mul\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
         "def test_mul():\n    assert mul(2, 3) == 6\n")


@pytest.mark.skipif(not (REAL / "config.json").exists(), reason="no provider configured in ~/.config/wisp")
def test_a_real_model_fixes_two_bugs_and_the_fix_is_verified(tmp_path):
    home = tmp_path / "home" / ".config" / "wisp"
    home.mkdir(parents=True)
    for name in ("config.json", ".env"):
        if (REAL / name).exists():
            shutil.copyfile(REAL / name, home / name)
            os.chmod(home / name, 0o600)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "calc.py").write_text(CALC)
    (ws / "test_calc.py").write_text(TESTS)
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path / "home"), "LANG": "en_US.UTF-8",
           "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
    proc = subprocess.run([sys.executable, "-P", "-m", "wisp", "--print",
                           "The tests in test_calc.py fail. Fix calc.py so they pass, and run the tests to show it.",
                           "-w", str(ws)], cwd=ws, env=env, capture_output=True, text=True, timeout=900)
    result = json.loads(proc.stdout)
    assert result["ok"], result["errors"]
    assert "return a + b" in (ws / "calc.py").read_text() and "return a * b" in (ws / "calc.py").read_text()
    independent = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=ws,
                                 capture_output=True, text=True, timeout=120)
    assert independent.returncode == 0, independent.stdout
    assert "UNVERIFIED" not in result["content"] and "not backed by any verification" not in result["content"], (
        "the harness did not see a counted green run")
