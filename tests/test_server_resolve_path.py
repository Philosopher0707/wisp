import pytest
import tempfile
import shutil
from pathlib import Path
from fastapi import HTTPException


@pytest.fixture
def server_workspace():
    tmp = Path(tempfile.mkdtemp())
    yield tmp
    shutil.rmtree(str(tmp), ignore_errors=True)


class TestServerResolvePath:
    """Tests for wisp.server._resolve_path symlink and traversal security."""

    def test_normal_relative_path(self, server_workspace, monkeypatch):
        from wisp.server.routes.files import _resolve_path
        import wisp.server.routes.files as files_module
        import os
        monkeypatch.setattr(files_module, "WORKSPACE_ROOT", server_workspace)
        result = _resolve_path("foo.txt")
        assert os.path.realpath(result) == os.path.realpath(server_workspace / "foo.txt")

    def test_path_traversal_blocked(self, server_workspace, monkeypatch):
        from wisp.server.routes.files import _resolve_path
        import wisp.server.routes.files as files_module
        monkeypatch.setattr(files_module, "WORKSPACE_ROOT", server_workspace)
        with pytest.raises(HTTPException) as exc_info:
            _resolve_path("../../etc/passwd")
        assert exc_info.value.status_code == 400

    def test_symlink_escape_blocked(self, server_workspace, monkeypatch):
        """A symlink inside workspace pointing to / should be blocked."""
        from wisp.server.routes.files import _resolve_path
        import wisp.server.routes.files as files_module
        monkeypatch.setattr(files_module, "WORKSPACE_ROOT", server_workspace)
        evil_link = server_workspace / "evil_link"
        evil_link.symlink_to("/etc")
        with pytest.raises(HTTPException) as exc_info:
            _resolve_path("evil_link/passwd")
        assert exc_info.value.status_code == 400

    def test_symlink_to_parent_blocked(self, server_workspace, monkeypatch):
        """A symlink pointing to the parent directory should be blocked."""
        from wisp.server.routes.files import _resolve_path
        import wisp.server.routes.files as files_module
        monkeypatch.setattr(files_module, "WORKSPACE_ROOT", server_workspace)
        parent_link = server_workspace / "parent_link"
        parent_link.symlink_to("..")
        with pytest.raises(HTTPException) as exc_info:
            _resolve_path("parent_link/secret.txt")
        assert exc_info.value.status_code == 400

    def test_workspace_itself_is_symlink(self, server_workspace, monkeypatch):
        """If workspace is a symlink, resolving paths inside it should still work."""
        from wisp.server.routes.files import _resolve_path
        import wisp.server.routes.files as files_module
        import os
        real_dir = server_workspace / "real_workspace"
        real_dir.mkdir()
        symlink_workspace = server_workspace / "link_workspace"
        symlink_workspace.symlink_to(real_dir)
        monkeypatch.setattr(files_module, "WORKSPACE_ROOT", symlink_workspace)
        result = _resolve_path("foo.txt")
        assert os.path.realpath(result) == os.path.realpath(real_dir / "foo.txt")

    def test_absolute_path_inside_workspace(self, server_workspace, monkeypatch):
        from wisp.server.routes.files import _resolve_path
        import wisp.server.routes.files as files_module
        import os
        monkeypatch.setattr(files_module, "WORKSPACE_ROOT", server_workspace)
        result = _resolve_path(str(server_workspace / "foo.txt"))
        assert os.path.realpath(result) == os.path.realpath(server_workspace / "foo.txt")

    def test_absolute_path_outside_workspace_blocked(self, server_workspace, monkeypatch):
        from wisp.server.routes.files import _resolve_path
        import wisp.server.routes.files as files_module
        monkeypatch.setattr(files_module, "WORKSPACE_ROOT", server_workspace)
        with pytest.raises(HTTPException) as exc_info:
            _resolve_path("/etc/passwd")
        assert exc_info.value.status_code == 400
