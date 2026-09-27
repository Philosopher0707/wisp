"""Tests for wisp.net_security — the SSRF/endpoint guards shared by
wisp.providers.factory and wisp.provider_catalog (the latter used to skip
validation entirely and fire an unauthenticated request straight at
ollama_url/api_base — see tests/test_security_integration.py's
TestProviderCatalogSSRF for the integration-level version of this)."""

import pytest

from wisp.net_security import assert_ollama_host_safe, validate_api_base


class TestAssertOllamaHostSafe:
    """Loopback and ordinary private/LAN addresses must stay allowed — a
    local or LAN-hosted Ollama daemon is the normal case. Only the
    link-local/reserved/metadata class is universally wrong, and that
    block is unconditional (no WISP_PRODUCTION_MODE opt-in required)."""

    def test_localhost_allowed(self):
        assert_ollama_host_safe("http://localhost:11434")  # must not raise

    def test_loopback_ip_allowed(self):
        assert_ollama_host_safe("http://127.0.0.1:11434")  # must not raise

    def test_private_lan_allowed(self):
        assert_ollama_host_safe("http://192.168.1.50:11434")  # must not raise

    def test_cloud_metadata_ip_blocked_unconditionally(self):
        with pytest.raises(ValueError, match="link-local"):
            assert_ollama_host_safe("http://169.254.169.254/latest/meta-data/")

    def test_link_local_blocked_unconditionally(self):
        with pytest.raises(ValueError, match="link-local"):
            assert_ollama_host_safe("http://169.254.1.1:11434")

    def test_no_host_does_not_raise(self):
        assert_ollama_host_safe("not-a-url")  # no hostname to judge; not this function's job

    def test_unresolvable_host_does_not_raise(self):
        # DNS failure is a connectivity concern, not an SSRF one — the
        # caller's own request fails with its own clear connection error.
        assert_ollama_host_safe("http://this-host-does-not-exist.invalid:11434")


class TestValidateApiBase:
    """Moved verbatim from wisp.providers.factory.ProviderFactory.
    _validate_api_base so provider_catalog.py enforces the identical
    policy instead of one of the two callers skipping it."""

    def test_https_allowed(self):
        assert validate_api_base(
            "https://openrouter.ai/api/v1", "openrouter"
        ) == "https://openrouter.ai/api/v1"

    def test_empty_passes_through(self):
        assert validate_api_base("", "openai") == ""

    def test_plaintext_http_remote_rejected(self):
        with pytest.raises(ValueError, match="https"):
            validate_api_base("http://example.com/v1", "openai")

    def test_plaintext_http_loopback_allowed(self):
        assert validate_api_base("http://localhost:4000", "openai") == "http://localhost:4000"

    def test_insecure_opt_in_env(self, monkeypatch):
        monkeypatch.setenv("WISP_ALLOW_INSECURE_BASE", "true")
        assert validate_api_base("http://example.com/v1", "openai") == "http://example.com/v1"

    def test_invalid_url_rejected(self):
        with pytest.raises(ValueError):
            validate_api_base("not-a-url", "openai")
