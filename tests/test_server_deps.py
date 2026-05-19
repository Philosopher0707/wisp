"""TDD for server dependencies.

Tests auth and rate limiting extracted from server.py.
"""

import pytest
from unittest.mock import patch, MagicMock


class TestAuthConfig:
    """Auth configuration works correctly."""

    def test_auth_disabled_without_env(self):
        with patch.dict("os.environ", {}, clear=True):
            import importlib
            from wisp.server import deps
            importlib.reload(deps)
            auth = deps._AuthConfig()
            assert auth.required is False

    def test_auth_enabled_with_env(self):
        with patch.dict("os.environ", {"WISP_API_KEY": "secret123"}):
            import importlib
            from wisp.server import deps
            importlib.reload(deps)
            auth = deps._AuthConfig()
            assert auth.required is True
            assert auth.key == "secret123"

    def test_auth_disable(self):
        with patch.dict("os.environ", {"WISP_API_KEY": "secret123"}):
            import importlib
            from wisp.server import deps
            importlib.reload(deps)
            auth = deps._AuthConfig()
            auth.disable()
            assert auth.required is False

    def test_auth_set_key(self):
        with patch.dict("os.environ", {}, clear=True):
            import importlib
            from wisp.server import deps
            importlib.reload(deps)
            auth = deps._AuthConfig()
            auth.set_key("newkey")
            assert auth.required is True
            assert auth.key == "newkey"


class TestVerifyApiKey:
    """API key verification works correctly."""

    @pytest.mark.asyncio
    async def test_no_auth_required_allows_any(self):
        import importlib
        from wisp.server import deps
        importlib.reload(deps)
        from wisp.server.deps import verify_api_key
        result = await verify_api_key(None, None)
        assert result == ""

    @pytest.mark.asyncio
    async def test_valid_header_key(self):
        with patch.dict("os.environ", {"WISP_API_KEY": "secret123"}):
            import importlib
            from wisp.server import deps
            importlib.reload(deps)
            from wisp.server.deps import verify_api_key
            result = await verify_api_key("secret123", None)
            assert result == "secret123"

    @pytest.mark.asyncio
    async def test_valid_bearer_token(self):
        with patch.dict("os.environ", {"WISP_API_KEY": "secret123"}):
            import importlib
            from wisp.server import deps
            importlib.reload(deps)
            from wisp.server.deps import verify_api_key
            result = await verify_api_key(None, "Bearer secret123")
            assert result == "secret123"

    @pytest.mark.asyncio
    async def test_invalid_key_raises_401(self):
        with patch.dict("os.environ", {"WISP_API_KEY": "secret123"}):
            import importlib
            from wisp.server import deps
            importlib.reload(deps)
            from wisp.server.deps import verify_api_key
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                await verify_api_key("wrong", None)
            assert exc.value.status_code == 401


class TestRateLimiter:
    """Rate limiting works correctly."""

    def test_rate_limiter_allows_under_limit(self, tmp_path):
        from wisp.server.deps import SQLiteRateLimiter
        limiter = SQLiteRateLimiter(
            db_path=tmp_path / "rates.db",
            max_requests=5,
            window_seconds=60,
        )
        assert limiter.is_allowed("1.2.3.4") is True

    def test_rate_limiter_blocks_over_limit(self, tmp_path):
        from wisp.server.deps import SQLiteRateLimiter
        limiter = SQLiteRateLimiter(
            db_path=tmp_path / "rates.db",
            max_requests=2,
            window_seconds=60,
        )
        limiter.is_allowed("1.2.3.4")
        limiter.is_allowed("1.2.3.4")
        assert limiter.is_allowed("1.2.3.4") is False

    def test_rate_limiter_tracks_different_ips(self, tmp_path):
        from wisp.server.deps import SQLiteRateLimiter
        limiter = SQLiteRateLimiter(
            db_path=tmp_path / "rates.db",
            max_requests=1,
            window_seconds=60,
        )
        assert limiter.is_allowed("1.2.3.4") is True
        assert limiter.is_allowed("5.6.7.8") is True

    @pytest.mark.asyncio
    async def test_rate_limiter_callable(self, tmp_path):
        from wisp.server.deps import SQLiteRateLimiter
        limiter = SQLiteRateLimiter(
            db_path=tmp_path / "rates.db",
            max_requests=5,
            window_seconds=60,
        )
        request = MagicMock()
        request.client.host = "1.2.3.4"
        await limiter(request)
