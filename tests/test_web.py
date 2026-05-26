"""Tests for web_fetch error messages — verifies LLM-actionable error output."""

import pytest
import requests
from unittest.mock import patch, Mock

from wisp.tools.web import tool_web_fetch
from wisp.tools.errors import ToolError


class TestWebFetchErrors:
    """Tests that web_fetch returns actionable error messages with [WEB_FETCH_FAILED] prefix."""

    def test_dns_error_returns_actionable_message(self):
        """DNS resolution failures should say 'domain does not exist' and suggest alternatives."""
        with patch("wisp.tools.web.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.ConnectionError(
                "Failed to resolve 'docs.memgpt.ai' ([Errno 8] nodename nor servname provided, or not known)"
            )
            with pytest.raises(ToolError) as exc_info:
                tool_web_fetch("https://docs.memgpt.ai", workspace=".")
            msg = str(exc_info.value)
            assert "[WEB_FETCH_FAILED]" in msg, \
                f"Error should start with [WEB_FETCH_FAILED], got: {msg}"
            assert "DNS resolution failed" in msg, \
                f"Should mention DNS resolution failure, got: {msg}"
            assert "Try a different URL" in msg, \
                f"Should suggest alternative action, got: {msg}"

    def test_404_error_returns_actionable_message(self):
        """HTTP 404 errors should say 'does not exist' and tell the agent NOT to retry."""
        mock_response = Mock()
        mock_response.status_code = 404
        with patch("wisp.tools.web.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.HTTPError(
                "404 Client Error", response=mock_response
            )
            with pytest.raises(ToolError) as exc_info:
                tool_web_fetch("https://example.com/missing", workspace=".")
            msg = str(exc_info.value)
            assert "[WEB_FETCH_FAILED]" in msg
            assert "404" in msg
            assert "does not exist" in msg
            assert "Do NOT retry" in msg, \
                f"Should tell the agent not to retry, got: {msg}"

    def test_403_error_returns_actionable_message(self):
        """HTTP 403 errors should mention access denied and suggest alternatives."""
        mock_response = Mock()
        mock_response.status_code = 403
        with patch("wisp.tools.web.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.HTTPError(
                "403 Forbidden", response=mock_response
            )
            with pytest.raises(ToolError) as exc_info:
                tool_web_fetch("https://example.com/restricted", workspace=".")
            msg = str(exc_info.value)
            assert "[WEB_FETCH_FAILED]" in msg
            assert "403" in msg
            assert "Access denied" in msg or "blocking" in msg

    def test_429_error_returns_actionable_message(self):
        """HTTP 429 rate limit errors should mention rate limiting."""
        mock_response = Mock()
        mock_response.status_code = 429
        with patch("wisp.tools.web.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.HTTPError(
                "429 Too Many Requests", response=mock_response
            )
            with pytest.raises(ToolError) as exc_info:
                tool_web_fetch("https://example.com/limited", workspace=".")
            msg = str(exc_info.value)
            assert "[WEB_FETCH_FAILED]" in msg
            assert "429" in msg
            assert "Rate limited" in msg

    def test_connection_refused_returns_actionable_message(self):
        """Connection refused should mention the server is down."""
        with patch("wisp.tools.web.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.ConnectionError(
                "Connection refused by example.com:443"
            )
            with pytest.raises(ToolError) as exc_info:
                tool_web_fetch("https://example.com:443", workspace=".")
            msg = str(exc_info.value)
            assert "[WEB_FETCH_FAILED]" in msg
            assert "Connection refused" in msg

    def test_timeout_returns_actionable_message(self):
        """Timeouts should mention the server is too slow."""
        with patch("wisp.tools.web.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.Timeout("timed out")
            with pytest.raises(ToolError) as exc_info:
                tool_web_fetch("https://example.com/slow", workspace=".")
            msg = str(exc_info.value)
            assert "[WEB_FETCH_FAILED]" in msg
            assert "Timeout" in msg
            assert "too slow" in msg

    def test_successful_fetch_returns_data_without_prefix(self):
        """Successful fetches should NOT have [WEB_FETCH_FAILED] prefix."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.text = "<html><body><p>Hello World</p></body></html>"
        with patch("wisp.tools.web.requests.get") as mock_get:
            mock_get.return_value = mock_response
            result = tool_web_fetch("https://example.com", workspace=".")
        assert "[WEB_FETCH_FAILED]" not in result
        assert "✓ Fetched" in result
