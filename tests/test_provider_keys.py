"""Per-provider API-key vault + model-listing TTL cache.

Pins the "switch providers without re-pasting keys" guarantee:
each provider resolves its key from its own env var first (shared
WISP_API_KEY as fallback), store_key persists per-provider slots to
env + .env + config, and cached listings never leak across keys/providers.
"""

from __future__ import annotations

import pytest

from wisp.provider_select import KEY_ENV_VARS, missing_key, resolve_key


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No ambient keys — tests set exactly what they assert.

    Snapshot/restore (not just delenv) because store_key() writes
    process env directly and would otherwise leak into later tests.
    """
    watched = ("WISP_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY",
               "OPENROUTER_API_KEY")
    import os

    saved = {v: os.environ.get(v) for v in watched}
    for v in watched:
        monkeypatch.delenv(v, raising=False)
    # Never write the real user's config/.env from tests.
    monkeypatch.setattr("wisp.provider_select.persist", lambda u: True)
    yield
    for v, old in saved.items():
        if old is None:
            os.environ.pop(v, None)
        else:
            os.environ[v] = old


# ── resolve_key ──────────────────────────────────────────────────────


class TestResolveKey:
    def test_per_provider_env_wins_over_shared(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
        monkeypatch.setenv("WISP_API_KEY", "shared")
        assert resolve_key("openrouter") == "or-key"

    def test_shared_fallback(self, monkeypatch):
        monkeypatch.setenv("WISP_API_KEY", "shared")
        assert resolve_key("openai") == "shared"
        assert resolve_key("nvidia") == "shared"

    def test_no_key_anywhere_is_empty(self):
        assert resolve_key("openai") == ""

    def test_unknown_provider_uses_shared_var(self, monkeypatch):
        monkeypatch.setenv("WISP_API_KEY", "shared")
        assert resolve_key("totally-new-provider") == "shared"

    def test_whitespace_only_key_counts_as_missing(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "   ")
        assert resolve_key("openai") == ""

    def test_every_keyed_provider_declared(self):
        from wisp.provider_select import KNOWN_PROVIDERS

        for name, spec in KNOWN_PROVIDERS.items():
            if spec.get("requires_key"):
                assert name in KEY_ENV_VARS, (
                    f"provider '{name}' requires a key but has no env mapping")


# ── missing_key via resolver ─────────────────────────────────────────


class TestMissingKeyVault:
    def test_openai_satisfied_by_dedicated_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k1")
        assert missing_key("openai") is None

    def test_nvidia_and_openrouter_coexist(self, monkeypatch):
        """The whole point: two keys, no clobbering."""
        monkeypatch.setenv("NVIDIA_API_KEY", "nv-1")
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-2")
        assert missing_key("nvidia") is None
        assert missing_key("openrouter") is None

    def test_switching_keeps_other_provider_key(self, monkeypatch):
        """After storing openrouter's key, openai's slot stays usable."""
        monkeypatch.setenv("OPENAI_API_KEY", "oa-1")
        from wisp.provider_select import store_key

        store_key("openrouter", "or-9")
        assert resolve_key("openai") == "oa-1"
        assert resolve_key("openrouter") == "or-9"


# ── store_key ────────────────────────────────────────────────────────


class TestStoreKey:
    def test_sets_process_env_immediately(self):
        import os

        from wisp.provider_select import store_key

        try:
            store_key("nvidia", "nv-live")
            assert os.environ["NVIDIA_API_KEY"] == "nv-live"
            assert os.environ["WISP_API_KEY"] == "nv-live"
        finally:
            os.environ.pop("NVIDIA_API_KEY", None)
            os.environ.pop("WISP_API_KEY", None)

    def test_persists_per_provider_slot(self, monkeypatch):
        import wisp.provider_select as ps

        updates = []
        monkeypatch.setattr(ps, "persist", lambda u: updates.append(dict(u)))
        ps.store_key("openrouter", "or-7")
        flat = {k: v for u in updates for k, v in u.items()}
        assert flat.get("api_key") == "or-7"
        assert flat.get("key_openrouter") == "or-7"


# ── TTL cache on list_models ─────────────────────────────────────────


@pytest.fixture()
def fresh_cache():
    from wisp.provider_catalog import clear_models_cache

    clear_models_cache()
    yield
    clear_models_cache()


def _cfg(**kw):
    from types import SimpleNamespace

    base = {"provider": "ollama", "ollama_url": "http://x:1", "api_key": "",
            "api_base": ""}
    base.update(kw)
    return SimpleNamespace(**base)


class TestModelsCache:
    def test_second_call_served_from_cache(self, monkeypatch, fresh_cache):
        import wisp.provider_catalog as pc

        calls = []
        monkeypatch.setattr(pc, "_list_models_impl",
                            lambda n, c: calls.append(n) or ["a", "b"])
        cfg = _cfg()
        assert pc.list_models("ollama", cfg) == ["a", "b"]
        assert pc.list_models("ollama", cfg) == ["a", "b"]
        assert len(calls) == 1  # impl hit once

    def test_force_bypasses_cache(self, monkeypatch, fresh_cache):
        import wisp.provider_catalog as pc

        calls = []
        monkeypatch.setattr(pc, "_list_models_impl",
                            lambda n, c: calls.append(n) or ["m"])
        pc.list_models("mock", None)
        pc.list_models("mock", None, force=True)
        assert len(calls) == 2

    def test_different_providers_cached_separately(self, monkeypatch,
                                                   fresh_cache):
        import wisp.provider_catalog as pc

        seen = []
        monkeypatch.setattr(pc, "_list_models_impl",
                            lambda n, c: seen.append(n) or [f"{n}-model"])
        pc.list_models("mock", None)
        pc.list_models("ollama", _cfg())
        assert seen == ["mock", "ollama"]  # no cache cross-contamination

    def test_expired_entry_refetches(self, monkeypatch, fresh_cache):
        import wisp.provider_catalog as pc

        calls = []
        monkeypatch.setattr(pc, "_list_models_impl",
                            lambda n, c: calls.append(n) or ["m"])
        cfg = _cfg()
        pc.list_models("ollama", cfg)
        # Age the single entry past the TTL.
        key = next(iter(pc._MODELS_CACHE))
        ts, models = pc._MODELS_CACHE[key]
        pc._MODELS_CACHE[key] = (ts - pc.MODELS_CACHE_TTL_S - 1, models)
        pc.list_models("ollama", cfg)
        assert len(calls) == 2

    def test_clear_models_cache_forces_refetch(self, monkeypatch,
                                               fresh_cache):
        import wisp.provider_catalog as pc

        calls = []
        monkeypatch.setattr(pc, "_list_models_impl",
                            lambda n, c: calls.append(n) or ["m"])
        cfg = _cfg()
        pc.list_models("ollama", cfg)
        pc.clear_models_cache("ollama")
        pc.list_models("ollama", cfg)
        assert len(calls) == 2

    def test_new_key_bypasses_stale_listing(self, monkeypatch, fresh_cache):
        """Listing cached under one key fingerprint must not be served
        after the key changes (store_key clears that provider)."""
        import os

        import wisp.provider_catalog as pc
        from wisp.provider_select import store_key

        calls = []
        monkeypatch.setattr(pc, "_list_models_impl",
                            lambda n, c: calls.append(n) or ["fresh"])
        cfg = _cfg(provider="openrouter")
        pc.list_models("openrouter", cfg)
        try:
            store_key("openrouter", "brand-new")  # must drop openrouter cache
            pc.list_models("openrouter", cfg)
            assert len(calls) == 2
        finally:
            os.environ.pop("OPENROUTER_API_KEY", None)
            os.environ.pop("WISP_API_KEY", None)

    def test_apply_switch_clears_cache(self, monkeypatch, fresh_cache):
        from wisp.provider_catalog import list_models
        from wisp.provider_select import apply_switch

        calls = []
        monkeypatch.setattr("wisp.provider_catalog._list_models_impl",
                            lambda n, c: calls.append(n) or ["m"])
        cfg = _cfg()
        list_models("ollama", cfg)
        apply_switch(None, {}, cfg, provider="mock")
        list_models("mock", _cfg(provider="mock"))
        assert sorted(calls) == ["mock", "ollama"]
