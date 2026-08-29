import pytest
import tempfile
import shutil
from pathlib import Path


@pytest.fixture
def isolated_wisp_env(monkeypatch, tmp_path):
    """Strip WISP_* env vars and hide the user config file.

    Machine setups (e.g. WISP_PROVIDER=nvidia plus
    ~/.config/wisp/config.json) leak into tests that assert *default*
    behavior, making them fail on one machine and pass on another.
    Opt in where hermeticity matters:

        def test_defaults_are_sane(self, isolated_wisp_env):
            cfg = WispConfig()   # pure defaults, no env/config influence
    """
    import os

    import wisp.config as cfg_mod

    for var in [k for k in os.environ if k.startswith("WISP_")]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        cfg_mod, "get_config_path", lambda: tmp_path / "missing-config.json"
    )
    return monkeypatch


@pytest.fixture
def temp_workspace():
    """Provide a temporary workspace directory for file operations."""
    tmp = Path(tempfile.mkdtemp())
    yield tmp
    shutil.rmtree(str(tmp), ignore_errors=True)


@pytest.fixture
def sample_file(temp_workspace):
    """Create a sample file in the workspace."""
    path = temp_workspace / "sample.txt"
    path.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n")
    return path


@pytest.fixture
def nested_dir(temp_workspace):
    """Create nested directories inside workspace."""
    d = temp_workspace / "a" / "b"
    d.mkdir(parents=True)
    (d / "deep.txt").write_text("deep")
    return d
