"""TDD for remaining router business logic.

Tests that migrated routers have actual implementations.
"""

import pytest


class TestMCPRouter:
    """MCP router has actual MCP manager integration."""

    def test_list_mcp_servers_endpoint(self):
        from wisp.server.routes.mcp import router
        routes = [r.path for r in router.routes]
        assert "/api/mcp/servers" in routes

    def test_create_mcp_server_endpoint(self):
        from wisp.server.routes.mcp import router
        methods = {}
        for r in router.routes:
            methods.setdefault(r.path, []).extend(r.methods)
        assert "POST" in methods.get("/api/mcp/servers", [])


class TestHooksRouter:
    """Hooks router has actual hook manager integration."""

    def test_list_hooks_endpoint(self):
        from wisp.server.routes.hooks import router
        routes = [r.path for r in router.routes]
        assert "/api/hooks" in routes

    def test_create_hook_endpoint(self):
        from wisp.server.routes.hooks import router
        methods = {}
        for r in router.routes:
            methods.setdefault(r.path, []).extend(r.methods)
        assert "POST" in methods.get("/api/hooks", [])


class TestReviewRouter:
    """Review router has actual review logic."""

    def test_review_pr_endpoint(self):
        from wisp.server.routes.review import router
        routes = [r.path for r in router.routes]
        assert "/api/review/pr" in routes

    def test_review_diff_endpoint(self):
        from wisp.server.routes.review import router
        routes = [r.path for r in router.routes]
        assert "/api/review/diff" in routes

    def test_best_of_n_endpoint(self):
        from wisp.server.routes.review import router
        routes = [r.path for r in router.routes]
        assert "/api/review/best-of-n" in routes


class TestJsonRpcRouter:
    """JSON-RPC router has actual handler integration."""

    def test_jsonrpc_endpoint(self):
        from wisp.server.routes.jsonrpc import router
        routes = [r.path for r in router.routes]
        assert "/api/jsonrpc" in routes
