import pytest
import tempfile
import shutil
from pathlib import Path


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
