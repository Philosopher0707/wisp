"""The test process runs in a private HOME, and a test run that touches the real operator files fails (tests/_home_guard.py, tests/conftest.py)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import _home_guard, conftest

OPTED_OUT = os.environ.get("WISP_TEST_REAL_HOME") == "1"
pytestmark = pytest.mark.skipif(OPTED_OUT, reason="WISP_TEST_REAL_HOME=1")


class TestThePrivateHome:
    def test_home_is_not_the_real_one(self):
        assert conftest._PRIVATE_HOME and str(Path.home()) == conftest._PRIVATE_HOME
        assert conftest._REAL_HOME != conftest._PRIVATE_HOME and "wisp-test-home-" in Path.home().name

    def test_the_config_dir_is_nowhere_near_the_real_home(self):
        """config.json was already redirected by an autouse fixture; this pins that it stays off the real home (the .env, which is under HOME, is the next test)."""
        import wisp.config as cfg

        assert not str(cfg.WISP_CONFIG_DIR).startswith(conftest._REAL_HOME + "/.config")

    def test_the_operator_env_path_is_private(self):
        from wisp.user_env import user_env_path

        assert str(user_env_path()).startswith(conftest._PRIVATE_HOME)

    def test_a_subprocess_inherits_it(self):
        out = subprocess.run([sys.executable, "-c", "from pathlib import Path; print(Path.home())"], capture_output=True, text=True, timeout=60).stdout.strip()
        assert out == conftest._PRIVATE_HOME

    def test_writing_a_choice_lands_in_the_private_home_and_leaves_the_real_files_alone(self):
        from wisp.provider_select import persist, store_key

        persist({"provider": "mock", "model": "m"})
        store_key("nvidia", "k-test-only")
        os.environ.pop("NVIDIA_API_KEY", None)
        os.environ.pop("WISP_API_KEY", None)
        assert (Path.home() / ".config" / "wisp" / ".env").read_text() == "NVIDIA_API_KEY=k-test-only\n"  # the key, in the PRIVATE home
        assert _home_guard.changed(conftest._REAL_FILES_BEFORE, _home_guard.snapshot(conftest._REAL_HOME)) == []


class TestTheTripwire:
    def test_it_reports_a_changed_a_created_and_a_removed_file(self, tmp_path):
        (tmp_path / ".config" / "wisp").mkdir(parents=True)
        env, cfg = tmp_path / ".config/wisp/.env", tmp_path / ".config/wisp/config.json"
        env.write_text("A=1\n")
        cfg.write_text("{}")
        before = _home_guard.snapshot(tmp_path)
        env.write_text("A=22\n")
        (tmp_path / ".config/wisp/mcp.json").write_text("{}")
        cfg.unlink()
        assert _home_guard.changed(before, _home_guard.snapshot(tmp_path)) == [".config/wisp/.env", ".config/wisp/config.json", ".config/wisp/mcp.json"]

    def test_an_untouched_home_reports_nothing(self, tmp_path):
        (tmp_path / ".config" / "wisp").mkdir(parents=True)
        (tmp_path / ".config/wisp/.env").write_text("A=1\n")
        assert _home_guard.changed(_home_guard.snapshot(tmp_path), _home_guard.snapshot(tmp_path)) == []

    def test_a_home_with_no_files_is_fine(self, tmp_path):
        assert set(_home_guard.snapshot(tmp_path).values()) == {None}
        assert _home_guard.snapshot(None) is not None

    def test_the_run_fails_when_the_real_files_change(self, tmp_path):
        """The hook through a real nested pytest run: a test that writes the (fake) real home's .env makes the whole run exit 1 and say so."""
        real = tmp_path / "real"
        (real / ".config" / "wisp").mkdir(parents=True)
        (real / ".config/wisp/.env").write_text("A=1\n")
        project = tmp_path / "proj"
        (project / "tests").mkdir(parents=True)
        repo = Path(__file__).resolve().parents[1]
        (project / "tests" / "conftest.py").write_text((repo / "tests" / "conftest.py").read_text())
        (project / "tests" / "_home_guard.py").write_text((repo / "tests" / "_home_guard.py").read_text())
        (project / "tests" / "__init__.py").write_text("")
        (project / "tests" / "test_writes_real_home.py").write_text(
            f"def test_it():\n    open({str(real / '.config/wisp/.env')!r}, 'a').write('B=2\\n')\n")
        env = {k: v for k, v in os.environ.items() if k != "WISP_TEST_REAL_HOME"}
        env.update({"HOME": str(real), "PYTHONPATH": str(repo)})
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_writes_real_home.py"], cwd=project, env=env,
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 1 and "CHANGED THE REAL OPERATOR FILES" in result.stdout + result.stderr, result.stdout[-800:]
