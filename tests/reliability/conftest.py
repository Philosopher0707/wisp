"""Hermetic HOME for reliability tests.

13A/G0 baseline proved ambient ~/.config/wisp (placeholder ollama_url)
pollutes results. Reliability suites run under a temp HOME. User
site-packages stays on sys.path (interpreter startup already resolved it).
"""
import pytest


@pytest.fixture(autouse=True)
def _hermetic_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
