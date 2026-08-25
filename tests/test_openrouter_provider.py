"""OpenRouter as a first-class provider.

OpenRouter is OpenAI-compatible but has its own base URL, its own key env
(OPENROUTER_API_KEY), and a richer /models payload ('name' alongside 'id').
These tests pin registration across BOTH factories, selection-layer
membership, key fallbacks, and model-list parsing.
"""

from unittest.mock import MagicMock, patch



class TestRegistration:
    def test_get_provider_builds_openrouter(self):
        from wisp.config import WispConfig
        from wisp.providers import get_provider
        from wisp.providers.openrouter import OpenRouterProvider

        p = get_provider(WispConfig().replace(provider="openrouter",
                                              model="x/y"))
        assert isinstance(p, OpenRouterProvider)
        assert p.api_base == "https://openrouter.ai/api/v1"
        assert p.model == "x/y"

    def test_provider_factory_builds_openrouter(self):
        from wisp.config import WispConfig
        from wisp.providers.factory import ProviderFactory

        p = ProviderFactory().from_config(
            WispConfig().replace(provider="openrouter", model="a/b"))
        assert type(p).__name__ == "OpenRouterProvider"

    def test_base_url_override_respected(self):
        from wisp.config import WispConfig
        from wisp.providers import get_provider

        p = get_provider(WispConfig().replace(
            provider="openrouter", api_base="https://proxy.example/v1"))
        assert p.api_base == "https://proxy.example/v1"


class TestKeyResolution:
    def test_missing_key_honors_openrouter_env(self, monkeypatch):
        from wisp.provider_select import missing_key

        monkeypatch.delenv("WISP_API_KEY", raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-x")
        assert missing_key("openrouter") is None

    def test_missing_key_blocks_without_any(self, monkeypatch):
        from wisp.provider_select import missing_key

        for var in ("WISP_API_KEY", "OPENROUTER_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        msg = missing_key("openrouter")
        assert msg and "OPENROUTER_API_KEY" in msg

    def test_known_providers_declares_openrouter(self):
        from wisp.provider_select import KNOWN_PROVIDERS

        meta = KNOWN_PROVIDERS["openrouter"]
        assert meta["requires_key"] is True
        assert "openrouter.ai" in meta["default_base"]

    def test_parse_target_provider_qualified(self):
        from wisp.provider_select import parse_target

        assert parse_target("openrouter/anthropic/claude-3-haiku") == {
            "provider": "openrouter",
            "model": "anthropic/claude-3-haiku",
        }


class TestListModels:
    def _provider(self):
        from wisp.providers.openrouter import OpenRouterProvider

        return OpenRouterProvider(
            base_url="https://openrouter.ai/api/v1", model="x/y",
            api_key="sk-or-test")

    def test_parses_id_and_display_name(self):
        payload = {"data": [
            {"id": "anthropic/claude-3-haiku", "name": "Claude: Haiku"},
            {"id": "stealth/ox-alpha", "name": "Ox Alpha"},
        ]}
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = payload
        with patch("requests.get", return_value=resp) as mocked:
            models = self._provider().list_models()
        assert [(m["id"], m["name"]) for m in models] == [
            ("anthropic/claude-3-haiku", "Claude: Haiku"),
            ("stealth/ox-alpha", "Ox Alpha"),
        ]
        args, kwargs = mocked.call_args
        assert args[0].endswith("/models")
        assert kwargs["headers"]["Authorization"] == "Bearer sk-or-test"

    def test_non_200_returns_empty(self):
        resp = MagicMock()
        resp.status_code = 429
        with patch("requests.get", return_value=resp):
            assert self._provider().list_models() == []

    def test_auth_header_uses_key(self):
        """Streaming requests must carry the OpenRouter key."""
        provider = self._provider()
        sse = [b'data: {"choices":[{"delta":{"content":"hi"}}]}', b"data: [DONE]"]
        resp = MagicMock()
        resp.status_code = 200
        resp.iter_lines.return_value = sse
        with patch("requests.post", return_value=resp) as mocked:
            list(provider.generate_stream_events(
                system_prompt="s", messages=[{"role": "user", "content": "q"}]))
        headers = mocked.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer sk-or-test"


class TestPersistence:
    def test_config_file_accepts_openrouter(self, tmp_path, monkeypatch):
        import wisp.config as cfgmod

        monkeypatch.setattr(cfgmod, "get_config_path",
                            lambda: tmp_path / "config.json")
        cfgmod.save_config({"provider": "openrouter"})
        loaded = cfgmod.load_config()
        assert loaded["provider"] == "openrouter"
