"""TDD for server routes refactoring.

Tests that extracted routers work correctly.
"""

import pytest
from typing import Any


# ═══════════════════════════════════════════════════════════════════
# 1. Sessions router
# ═══════════════════════════════════════════════════════════════════

class TestSessionsRouter:
    """Sessions router handles session CRUD."""

    def test_router_has_list_endpoint(self):
        from wisp.server.routes.sessions import router
        routes = [r.path for r in router.routes]
        assert "/api/sessions" in routes

    def test_router_has_get_endpoint(self):
        from wisp.server.routes.sessions import router
        routes = [r.path for r in router.routes]
        assert "/api/sessions/{session_id}" in routes

    def test_router_has_delete_endpoint(self):
        from wisp.server.routes.sessions import router
        methods = {}
        for r in router.routes:
            methods.setdefault(r.path, []).extend(r.methods)
        assert "DELETE" in methods.get("/api/sessions/{session_id}", [])

    def test_router_has_patch_endpoint(self):
        from wisp.server.routes.sessions import router
        methods = {}
        for r in router.routes:
            methods.setdefault(r.path, []).extend(r.methods)
        assert "PATCH" in methods.get("/api/sessions/{session_id}", [])

    def test_router_has_fork_endpoint(self):
        from wisp.server.routes.sessions import router
        routes = [r.path for r in router.routes]
        assert "/api/sessions/fork" in routes


# ═══════════════════════════════════════════════════════════════════
# 2. Files router
# ═══════════════════════════════════════════════════════════════════

class TestFilesRouter:
    """Files router handles file operations."""

    def test_router_has_list_endpoint(self):
        from wisp.server.routes.files import router
        routes = [r.path for r in router.routes]
        assert "/api/files" in routes

    def test_router_has_tree_endpoint(self):
        from wisp.server.routes.files import router
        routes = [r.path for r in router.routes]
        assert "/api/files/tree" in routes


# ═══════════════════════════════════════════════════════════════════
# 3. Health router
# ═══════════════════════════════════════════════════════════════════

class TestHealthRouter:
    """Health router handles health checks."""

    def test_router_has_health_endpoint(self):
        from wisp.server.routes.health import router
        routes = [r.path for r in router.routes]
        assert "/api/health" in routes


# ═══════════════════════════════════════════════════════════════════
# 4. Models router
# ═══════════════════════════════════════════════════════════════════

class TestModelsRouter:
    """Models router handles model listing."""

    def test_router_has_list_endpoint(self):
        from wisp.server.routes.models import router
        routes = [r.path for r in router.routes]
        assert "/api/models" in routes


# ═══════════════════════════════════════════════════════════════════
# 5. Router integration
# ═══════════════════════════════════════════════════════════════════

class TestRouterIntegration:
    """Routers can be mounted on a FastAPI app."""

    def test_can_create_app_with_routers(self):
        from fastapi import FastAPI
        from wisp.server.routes.sessions import router as sessions_router
        from wisp.server.routes.files import router as files_router
        from wisp.server.routes.health import router as health_router
        from wisp.server.routes.models import router as models_router

        app = FastAPI()
        app.include_router(sessions_router)
        app.include_router(files_router)
        app.include_router(health_router)
        app.include_router(models_router)

        paths = [r.path for r in app.routes]
        assert "/api/sessions" in paths
        assert "/api/files" in paths
        assert "/api/health" in paths
        assert "/api/models" in paths
