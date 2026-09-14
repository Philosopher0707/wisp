"""C1a: api_base is fail-closed at provider construction.

An api_base reaching OpenAI-compatible providers used to flow straight into
requests with the Bearer key — any config writer could exfiltrate. These pins
prove malformed / insecure bases die in from_config before any key or byte
moves, while loopback dev and https custom endpoints keep working.
"""

from types import SimpleNamespace

import pytest


def _cfg(base: str):
    return SimpleNamespace(provider="openai", api_base=base, api_key="DUMMY", model="m")


def test_garbage_base_rejected():
    from wisp.providers.factory import ProviderFactory

    for bad in ("---", "not a url", "ftp://x/y"):
        with pytest.raises(ValueError):
            ProviderFactory().from_config(_cfg(bad))


def test_remote_plaintext_http_rejected():
    from wisp.providers.factory import ProviderFactory

    with pytest.raises(ValueError, match="https"):
        ProviderFactory().from_config(_cfg("http://evil.example:8000"))


def test_loopback_http_and_https_allowed():
    from wisp.providers.factory import ProviderFactory

    p = ProviderFactory().from_config(_cfg("http://127.0.0.1:11434"))
    assert "127.0.0.1" in p.api_base
    p = ProviderFactory().from_config(_cfg("https://gateway.example/v1"))
    assert p.api_base.startswith("https://")


def test_empty_base_falls_back_to_default():
    from wisp.providers.factory import ProviderFactory

    p = ProviderFactory().from_config(_cfg(""))
    assert p.api_base.startswith("https://")


def test_insecure_opt_in_env(monkeypatch):
    from wisp.providers.factory import ProviderFactory

    monkeypatch.setenv("WISP_ALLOW_INSECURE_BASE", "true")
    p = ProviderFactory().from_config(_cfg("http://evil.example:8000"))
    assert p.api_base.startswith("http://")
