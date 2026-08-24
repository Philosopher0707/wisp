"""Tests for robots.txt compliance in tool_web_fetch."""

import pytest
from unittest.mock import patch, MagicMock

from wisp.tools.web import (
    _check_robots_txt,
    _parse_robots_txt,
    _robots_cache,
    _ROBOTS_TTL,
    tool_web_fetch,
)
from wisp.tools.errors import ToolError


class TestParseRobotsTxt:
    """Unit tests for the lightweight manual robots.txt parser."""

    WISP_UA = "Wisp-Agent/0.1.0"

    def test_allow_all(self):
        """Empty / benign robots.txt → allow everything."""
        text = "# Nothing to see here\n"
        assert _parse_robots_txt(text, self.WISP_UA, "/") is True
        assert _parse_robots_txt(text, self.WISP_UA, "/secret") is True

    def test_disallow_root(self):
        """User-agent: * / Disallow: / → blocks everything."""
        text = "User-agent: *\nDisallow: /\n"
        assert _parse_robots_txt(text, self.WISP_UA, "/") is False
        assert _parse_robots_txt(text, self.WISP_UA, "/page") is False

    def test_disallow_specific_path(self):
        """Disallow only a subtree."""
        text = "User-agent: *\nDisallow: /private/\n"
        assert _parse_robots_txt(text, self.WISP_UA, "/private/data") is False
        assert _parse_robots_txt(text, self.WISP_UA, "/public") is True

    def test_allow_override(self):
        """Allow directive overrides an earlier Disallow."""
        text = (
            "User-agent: *\n"
            "Disallow: /\n"
            "Allow: /public\n"
        )
        assert _parse_robots_txt(text, self.WISP_UA, "/") is False
        assert _parse_robots_txt(text, self.WISP_UA, "/public") is True
        assert _parse_robots_txt(text, self.WISP_UA, "/secret") is False

    def test_comments_ignored(self):
        """Everything after # is a comment and ignored."""
        text = (
            "User-agent: *  # test comment\n"
            "Disallow: /admin  # no admin access\n"
        )
        assert _parse_robots_txt(text, self.WISP_UA, "/admin") is False
        assert _parse_robots_txt(text, self.WISP_UA, "/") is True

    def test_different_user_agent(self):
        """Rules for a different UA should not apply to us."""
        text = (
            "User-agent: BadBot\n"
            "Disallow: /\n"
        )
        assert _parse_robots_txt(text, self.WISP_UA, "/") is True

    def test_case_insensitive_ua(self):
        """User-agent matching is case-insensitive."""
        text = (
            "User-agent: wisp\n"
            "Disallow: /secret\n"
        )
        assert _parse_robots_txt(text, "Wisp-Agent", "/secret") is False


class TestCheckRobotsTxt:
    """Tests for _check_robots_txt helper (uses requests, not urllib.robotparser)."""

    def setup_method(self):
        _robots_cache.clear()

    def test_invalid_scheme_returns_true(self):
        """URLs with non-HTTP schemes should always be allowed."""
        assert _check_robots_txt("ftp://example.com/") is True
        assert _check_robots_txt("file:///etc/passwd") is True
        assert _check_robots_txt("data:text/html,hello") is True
        assert _check_robots_txt("not_a_url") is True
        assert _check_robots_txt("") is True

    def test_caches_result(self):
        """Same-domain robots.txt is cached for the TTL window."""
        import uuid
        domain = f"unique-{uuid.uuid4().hex[:8]}.example.com"
        robots_url = f"https://{domain}/robots.txt"
        assert robots_url not in _robots_cache

        # Build a mock response with a disallow rule
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "User-agent: *\nDisallow: /\n"

        with patch("wisp.tools.web._assert_public_url", return_value="93.184.216.34"), \
             patch("wisp.tools.web.requests.get", return_value=mock_resp):
            result1 = _check_robots_txt(f"https://{domain}/page1")
        assert robots_url in _robots_cache
        assert result1 is False

        # Second call must NOT make another HTTP request
        call_tracker = patch("wisp.tools.web.requests.get")
        with call_tracker as mock_get:
            result2 = _check_robots_txt(f"https://{domain}/page2")
        assert result2 is False
        mock_get.assert_not_called()

    def test_returns_true_on_fetch_failure(self):
        """If robots.txt cannot be fetched, allow the request."""
        import requests
        with patch(
            "wisp.tools.web.requests.get",
            side_effect=requests.exceptions.ConnectionError("network down"),
        ):
            assert _check_robots_txt("https://www.example.com/") is True

    def test_ttl_expires(self):
        """Stale cache entries are re-fetched after _ROBOTS_TTL."""
        import time
        url = "https://ttl-test.example.com/path"
        robots_url = "https://ttl-test.example.com/robots.txt"

        # Inject a stale cache entry
        _robots_cache[robots_url] = (False, time.time() - _ROBOTS_TTL - 1)

        # Re-read → mock to return True (allow)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "User-agent: *\nAllow: /\n"
        with patch("wisp.tools.web.requests.get", return_value=mock_resp):
            result = _check_robots_txt(url)
            assert result is True

    def test_404_means_allow(self):
        """Missing robots.txt (HTTP 404) means everything is allowed."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("wisp.tools.web.requests.get", return_value=mock_resp):
            assert _check_robots_txt("https://missing.example.com/page") is True


class TestToolWebFetchRobotsBlocking:
    """Integration tests that tool_web_fetch respects the robots check."""

    def test_blocked_by_robots_txt_raises(self, monkeypatch):
        """When robots.txt disallows, tool_web_fetch raises [WEB_FETCH_BLOCKED]."""
        monkeypatch.setenv("WISP_WEB_PROXY", "off")  # direct path only
        with patch("wisp.tools.web._check_robots_txt", return_value=False):
            with pytest.raises(ToolError) as exc_info:
                tool_web_fetch("https://blocked.com/secret", workspace=".")
            msg = str(exc_info.value)
            assert "[WEB_FETCH_BLOCKED]" in msg, \
                f"Expected [WEB_FETCH_BLOCKED], got: {msg}"
            assert "blocked.com" in msg, \
                f"Should mention the site, got: {msg}"

    def test_allowed_by_robots_txt_proceeds(self):
        """When robots.txt allows, fetch proceeds normally."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/plain"}
        mock_response.encoding = "utf-8"
        mock_response.iter_content = lambda chunk_size=65536: iter([b"hello world"])

        with patch("wisp.tools.web._check_robots_txt", return_value=True), \
             patch("wisp.tools.web._assert_public_url"), \
             patch("wisp.tools.web.requests.get", return_value=mock_response):
            result = tool_web_fetch("https://allowed.com/page", workspace=".")
            assert "✓ Fetched" in result
            assert "hello world" in result
