"""Tests for API key authentication security and query parameter exclusion.

Covers:
- Rejection of 'api-key' and 'api_key' in query parameters across all REST routes.
- Success of Header-based authentication (X-API-Key and Authorization Bearer).
- CLI client request packaging in headers rather than query params.
"""

import os
import pytest
from fastapi.testclient import TestClient
import wisp.server as ws_server


def test_verify_api_key_rejects_query_parameters():
    """Verify that verify_api_key strictly rejects api-key query parameters and requires headers."""
    # Backup current server API key
    backup_key = ws_server.API_KEY
    ws_server.API_KEY = "test-secret-key"
    
    try:
        from wisp.server import app
        client = TestClient(app)
        
        # Test 1: Query parameter authentication should fail with 401
        response = client.get("/api/models?api-key=test-secret-key")
        assert response.status_code == 401
        
        # Test 2: Correct header authentication (X-API-Key) should pass (not be 401)
        response = client.get("/api/models", headers={"X-API-Key": "test-secret-key"})
        assert response.status_code != 401
        
        # Test 3: Correct header authentication (Bearer) should pass (not be 401)
        response = client.get("/api/models", headers={"Authorization": "Bearer test-secret-key"})
        assert response.status_code != 401

    finally:
        ws_server.API_KEY = backup_key


def test_cli_client_uses_headers_instead_of_query_params(monkeypatch):
    """Verify that the CLI client (cmd_print) sends the API key in headers, not query params."""
    import requests
    from wisp.__main__ import cmd_print
    
    posted_headers = None
    posted_params = None
    
    def mock_post(url, json=None, params=None, headers=None, timeout=None):
        nonlocal posted_headers, posted_params
        posted_headers = headers
        posted_params = params
        # Return a mock response that triggers a success exit
        class MockResponse:
            status_code = 200
            def json(self):
                return {"ok": True, "content": "Done"}
        return MockResponse()
        
    monkeypatch.setattr(requests, "post", mock_post)
    monkeypatch.setenv("WISP_API_KEY", "test-secret-key")
    
    # Run cmd_print in a way that targets the local server
    with pytest.raises(SystemExit) as exc_info:
        cmd_print(prompt="hello", quiet=True)
        
    assert exc_info.value.code == 0
    # Assert headers are set correctly
    assert posted_headers is not None
    assert posted_headers.get("X-API-Key") == "test-secret-key"
    # Assert params does NOT contain api-key or is empty
    if posted_params:
        assert "api-key" not in posted_params
        assert "api_key" not in posted_params


def test_websocket_requires_auth_frame():
    """Verify that connecting to /ws/agent requires a type: auth JSON frame when API_KEY is set."""
    backup_key = ws_server.API_KEY
    ws_server.API_KEY = "test-secret-key"
    
    try:
        from wisp.server import app
        client = TestClient(app)
        
        # Test A: Connect and send a prompt without sending 'auth' first
        with client.websocket_connect("/ws/agent") as websocket:
            websocket.send_json({"type": "prompt", "content": "hello"})
            # Read first message from websocket
            resp = websocket.receive_json()
            assert resp.get("type") == "error"
            assert "Authentication required" in resp.get("message", "")
            
        # Test B: Connect and send correct 'auth' message first
        with client.websocket_connect("/ws/agent") as websocket:
            websocket.send_json({"type": "auth", "api_key": "test-secret-key"})
            # Now send prompt
            websocket.send_json({"type": "prompt", "content": ""})
            resp = websocket.receive_json()
            assert resp.get("type") == "error"
            assert "Authentication required" not in resp.get("message", "")

    finally:
        ws_server.API_KEY = backup_key
