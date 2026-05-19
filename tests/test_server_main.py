"""TDD for server main entry point.

Tests that the new server main can be started.
"""

import pytest
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
        from wisp.server.main import main
        from wisp.server.deps import _auth
        with patch("uvicorn.run") as mock_run:
            with patch.object(_auth, "disable") as mock_disable:
                _auth._no_auth = False
                _auth._key = "secret"
                main(no_auth=True)
                mock_disable.assert_called_once()
