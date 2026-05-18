"""TDD for all server routers.

Tests that all domain routers are properly defined and mountable.
"""

import pytest
from fastapi import FastAPI


class TestAllRouters:
    """All routers can be imported and mounted."""

    def test_sessions_router(self):
        from wisp.server.routes.sessions import router
        assert router is not None

    def test_files_router(self):
        from wisp.server.routes.files import router
        assert router is not None

    def test_health_router(self):
        from wisp.server.routes.health import router
        assert router is not None

    def test_models_router(self):
        from wisp.server.routes.models import router
        assert router is not None

    def test_arena_router(self):
        from wisp.server.routes.arena import router
        assert router is not None

    def test_swarm_router(self):
        from wisp.server.routes.swarm import router
        assert router is not None

    def test_runs_router(self):
        from wisp.server.routes.runs import router
        assert router is not None

    def test_codebase_router(self):
        from wisp.server.routes.codebase import router
        assert router is not None

    def test_diff_router(self):
        from wisp.server.routes.diff import router
        assert router is not None

    def test_complete_router(self):
        from wisp.server.routes.complete import router
        assert router is not None

    def test_workspace_router(self):
        from wisp.server.routes.workspace import router
        assert router is not None

    def test_git_router(self):
        from wisp.server.routes.git import router
        assert router is not None

    def test_context_router(self):
        from wisp.server.routes.context import router
        assert router is not None

    def test_bash_router(self):
        from wisp.server.routes.bash import router
        assert router is not None

    def test_review_router(self):
        from wisp.server.routes.review import router
        assert router is not None

    def test_suggestions_router(self):
        from wisp.server.routes.suggestions import router
        assert router is not None

    def test_prompt_router(self):
        from wisp.server.routes.prompt import router
        assert router is not None

    def test_jsonrpc_router(self):
        from wisp.server.routes.jsonrpc import router
        assert router is not None

    def test_plugins_router(self):
        from wisp.server.routes.plugins import router
        assert router is not None

    def test_hooks_router(self):
        from wisp.server.routes.hooks import router
        assert router is not None

    def test_mcp_router(self):
        from wisp.server.routes.mcp import router
        assert router is not None

    def test_agents_router(self):
        from wisp.server.routes.agents import router
        assert router is not None

    def test_search_router(self):
        from wisp.server.routes.search import router
        assert router is not None

    def test_diagnostics_router(self):
        from wisp.server.routes.diagnostics import router
        assert router is not None


class TestServerMain:
    """Server main mounts all routers."""

    def test_app_created(self):
        from wisp.server.main import app
        assert app is not None
        assert isinstance(app, FastAPI)

    def test_all_routes_registered(self):
        from wisp.server.main import app
        paths = [r.path for r in app.routes]
        assert "/api/sessions" in paths
        assert "/api/files" in paths
        assert "/api/health" in paths
        assert "/api/models" in paths
        assert "/api/arena/entries" in paths
        assert "/api/swarm/run" in paths
        assert "/api/run/background" in paths
        assert "/api/codebase/search" in paths
        assert "/api/diff" in paths
        assert "/api/complete" in paths
        assert "/api/workspace" in paths
        assert "/api/git" in paths
        assert "/api/context" in paths
        assert "/api/bash" in paths
        assert "/api/review/best-of-n" in paths
        assert "/api/suggestions" in paths
        assert "/api/prompt" in paths
        assert "/api/jsonrpc" in paths
        assert "/api/plugins" in paths
        assert "/api/hooks" in paths
        assert "/api/mcp/servers" in paths
        assert "/ws/agent" in paths
        assert "/api/search" in paths
        assert "/api/diagnostics" in paths
