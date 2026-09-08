"""TDD for server main entry point.

Tests that the new server main can be started.
"""

from unittest.mock import patch


class TestServerMain:
    """Server main entry point works."""

    def test_main_function_exists(self):
        from wisp.server.main import main
        assert callable(main)

    def test_app_is_fastapi(self):
        from wisp.server.main import app
        from fastapi import FastAPI
        assert isinstance(app, FastAPI)

    def test_app_has_all_routes(self):
        from wisp.server.main import app
        paths = [r.path for r in app.routes]
        assert "/api/health" in paths
        assert "/api/sessions" in paths
        assert "/api/files" in paths
        assert "/ws/agent" in paths

    def test_main_can_disable_auth(self):
        from wisp.server.main import main, app
        from wisp.server.deps import _auth
        # Save original state
        orig_no_auth = _auth._no_auth
        orig_key = _auth._key
        orig_overrides = dict(app.dependency_overrides)
        try:
            with patch("uvicorn.run"):
                with patch.object(_auth, "disable") as mock_disable:
                    _auth._no_auth = False
                    _auth._key = "secret"
                    main(no_auth=True)
                    mock_disable.assert_called_once()
        finally:
            # Restore original state
            _auth._no_auth = orig_no_auth
            _auth._key = orig_key
            app.dependency_overrides = orig_overrides


class TestBindAuth:
    """P0.2 gate: no silent unauthenticated boots (GH#14)."""

    def test_loopback_with_key_ok(self, capsys):
        from wisp.server.main import _check_bind_auth
        assert _check_bind_auth("127.0.0.1", False, True) is None
        assert capsys.readouterr().err == ""

    def test_loopback_no_key_no_flag_refuses(self, capsys):
        import pytest

        from wisp.server.main import _check_bind_auth
        with pytest.raises(SystemExit) as exc:
            _check_bind_auth("127.0.0.1", False, False)
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "WISP_API_KEY" in err and "--no-auth" in err

    def test_loopback_no_auth_warns(self, capsys):
        from wisp.server.main import _check_bind_auth
        warning = _check_bind_auth("127.0.0.1", True, False)
        assert warning is not None and "DISABLED" in warning

    def test_non_loopback_no_key_refuses(self):
        import pytest

        from wisp.server.main import _check_bind_auth
        with pytest.raises(SystemExit):
            _check_bind_auth("0.0.0.0", False, False)

    def test_non_loopback_no_auth_never_allowed(self):
        import pytest

        from wisp.server.main import _check_bind_auth
        with pytest.raises(SystemExit):
            _check_bind_auth("0.0.0.0", True, False)

    def test_non_loopback_with_key_ok(self):
        from wisp.server.main import _check_bind_auth
        assert _check_bind_auth("0.0.0.0", False, True) is None
