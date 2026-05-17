"""Tests for robots.txt compliance in tool_web_fetch."""

import pytest
from unittest.mock import patch, MagicMock

from wisp.tools.web import _check_robots_txt, _robots_cache, _ROBOTS_TTL, tool_web_fetch
from wisp.tools.errors import ToolError


class TestCheckRobotsTxt:
    """Tests for _check_robots_txt helper."""

    def test_invalid_scheme_returns_true(self):
        """URLs with invalid schemes should always return True (allow)."""
        assert _check_robots_txt("ftp://example.com/") is True
        assert _check_robots_txt("file:///etc/passwd") is True
        assert _check_robots_txt("data:text/html,hello") is True
        assert _check_robots_txt("not_a_url") is True
        assert _check_robots_txt("") is True

    def test_caches_result(self):
        """Same domain robots.txt results should be cached and reused."""
        import uuid
        domain = f"unique-{uuid.uuid4().hex[:8]}.example.com"
        robots_url = f"https://{domain}/robots.txt"
        _robots_cache.clear()
        assert robots_url not in _robots_cache

        # First call populates cache
        result1 = _check_robots_txt(f"https://{domain}/page1") 
        assert robots_url in _robots_cache
        cached_value = _robots_cache[robots_url][0]
        assert result1 == cached_value

        # Second call should reuse cache (same entry, same result)
        result2 = _check_robots_txt(f"https://{domain}/page2")
        assert result2 == result1
        assert len(_robots_cache) == 1  # only one entry for this domain

    def test_returns_true_on_failure(self):
        """If robots.txt cannot be fetched/parsed, return True (allow)."""
        with patch("urllib.robotparser.RobotFileParser.read",
                   side_effect=OSError("network broken")):
            assert _check_robots_txt("https://www.example.com/") is True

    def test_ttl_expires(self):
        """Cached entries older than _ROBOTS_TTL should trigger a re-read."""
        import time
        url = "https://ttl-test.example.com/path"
        robots_url = "https://ttl-test.example.com/robots.txt"

        # Inject a stale cache entry
        _robots_cache[robots_url] = (False, time.time() - _ROBOTS_TTL - 1)

        # After TTL expiry should re-read → mock to return True
        with patch("urllib.robotparser.RobotFileParser.can_fetch", return_value=True):
            with patch("urllib.robotparser.RobotFileParser.read"):
                result = _check_robots_txt(url)
                # stale entry was False, but should have been refreshed to True
                assert result is True


class TestToolWebFetchRobotsBlocking:
    """Tests that tool_web_fetch blocks via robots.txt correctly."""

    def test_blocked_by_robots_txt_raises(self):
        """When robots.txt disallows, tool_web_fetch raises [WEB_FETCH_BLOCKED]."""
        with patch("wisp.tools.web._check_robots_txt", return_value=False):
            with pytest.raises(ToolError) as exc_info:
                tool_web_fetch("https://blocked.com/secret", workspace=".")
            msg = str(exc_info.value)
            assert "[WEB_FETCH_BLOCKED]" in msg, \
                f"Expected [WEB_FETCH_BLOCKED], got: {msg}"
            assert "robots.txt" in msg.lower(), \
                f"Should mention robots.txt, got: {msg}"
            assert "blocked.com" in msg, \
                f"Should mention the site, got: {msg}"

    def test_allowed_by_robots_txt_proceeds(self):
        """When robots.txt allows, fetch proceeds normally."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/plain"}
        mock_response.text = "hello world"

        with patch("wisp.tools.web._check_robots_txt", return_value=True):
            with patch("wisp.tools.web.requests.get", return_value=mock_response):
                result = tool_web_fetch("https://allowed.com/page", workspace=".")
                assert "✓ Fetched" in result
                assert "hello world" in result
