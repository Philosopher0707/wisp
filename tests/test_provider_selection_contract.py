"""Provider/model selection contract — regression pins.

Covers the "latest selected model comes online" guarantee end to end:
no hardcoded model defaults, live listing resolution, session
latest-wins, and the GUI select route sharing the REPL switch seam.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest



def _cfg(**kw) -> SimpleNamespace:
    """Config stand-in with WispConfig-style .replace()."""
    base = {"provider": "openai", "model": "", "api_key": "k",
            "ollama_url": "http://x", "api_base": ""}

    class C(SimpleNamespace):
        def replace(self, **kw2):
            merged = {k: v for k, v in vars(self).items() if k != "replace"}
            merged.update(kw2)
            return _cfg(**merged)

    return C(**{**base, **kw})


# ── No stale model-id defaults anywhere in the selection path ──────────

def test_config_default_model_is_empty(tmp_path):
    from wisp.config import DEFAULT_MODEL, WispConfig

    assert DEFAULT_MODEL == ""
    # Fresh config (no file, no env): empty means unset, resolved later.
    with patch.dict("os.environ", {}, clear=True), \
            patch.object(__import__("wisp.config", fromlist=["WISP_CONFIG_DIR"]),
                         "WISP_CONFIG_DIR", tmp_path / "cfg"):
        cfg = WispConfig()
    assert cfg.model == ""


def test_factory_no_hardcoded_model_fallback():
    import inspect

    from wisp.providers import factory as factory_mod

    src = inspect.getsource(factory_mod)
    assert "qwen2.5-coder" not in src, (
        "factory must not hardcode a model id — stale defaults are how "
        "agents come online pointing at models that don't exist")


# ── Catalog listing + resolution ────────────────────────────────────────

def test_list_providers_covers_all_known():
    from wisp.provider_catalog import list_providers
    from wisp.provider_select import KNOWN_PROVIDERS

    names = {p.name for p in list_providers()}
    assert names == set(KNOWN_PROVIDERS.keys())


def test_list_models_ollama_reads_live_daemon():
    from wisp.provider_catalog import list_models

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": "alpha:1b"}, {"name": "beta:8b"}]}

    captured = {}

    def fake_get(url, **kw):
        captured["url"] = url
        return FakeResp()

    with patch("requests.get", side_effect=fake_get):
        models = list_models("ollama", SimpleNamespace(ollama_url="http://x:1"))
    assert captured["url"] == "http://x:1/api/tags"
    assert models == ["alpha:1b", "beta:8b"]  # sorted, real ids only


def test_resolve_unset_model_picks_first_served():
    from wisp.provider_catalog import resolve_selection

    cfg = SimpleNamespace(provider="ollama", model="", ollama_url="http://x")
    with patch("wisp.provider_catalog.list_models",
               return_value=["zeta", "alpha"]):
        r = resolve_selection(cfg)
    assert r.status == "model_unset"
    assert r.suggested == "zeta"


def test_resolve_unset_prefers_locally_runnable_models():
    """Auto-pick must not select :cloud ids that may need a paid plan —
    that trades a clear 'unset' state for a 403 on every turn."""
    from wisp.provider_catalog import resolve_selection

    cfg = SimpleNamespace(provider="ollama", model="", ollama_url="http://x")
    with patch("wisp.provider_catalog.list_models",
               return_value=["deepseek-v4-flash:cloud", "llama3.2:3b"]):
        r = resolve_selection(cfg)
    assert r.suggested == "llama3.2:3b"
    assert ":cloud" not in r.suggested


def test_resolve_unknown_model_offers_close_alternatives():
    from wisp.provider_catalog import resolve_selection

    cfg = SimpleNamespace(provider="openrouter", model="org/typo-model",
                          api_key="k")
    with patch("wisp.provider_catalog.list_models",
               return_value=["org/model-a", "org/model-b"]):
        r = resolve_selection(cfg)
    assert r.status == "unknown_model"
    assert r.alternatives == ["org/model-a", "org/model-b"]


def test_resolve_unreachable_listing_is_notice_not_lie():
    """Ollama (local) is lenient: model kept with 'could not be verified'."""
    from wisp.provider_catalog import resolve_selection

    cfg = SimpleNamespace(provider="ollama", model="some/model", ollama_url="http://x")
    with patch("wisp.provider_catalog.list_models", return_value=[]):
        r = resolve_selection(cfg)
    assert r.status == "ok"
    assert "not be verified" in r.detail


def test_core_factory_applies_catalog_suggestion(monkeypatch, tmp_path):
    """The composition seam resolves unset models BEFORE building cores."""
    from wisp.composition import CompositionRoot

    root = CompositionRoot.__new__(CompositionRoot)
    root.config = _cfg(model="")
    root.runtime = None

    sent = {}

    def fake_resolve(cfg):
        sent["cfg"] = cfg
        return SimpleNamespace(
            provider=cfg.provider, model=cfg.model,
            status="model_unset", detail="", suggested="first-served",
            alternatives=["first-served", "other"],
        )

    monkeypatch.setattr("wisp.provider_catalog.resolve_selection",
                        fake_resolve)

    built = {}
    monkeypatch.setattr(
        "wisp.providers.factory.ProviderFactory.from_config",
        lambda self, c: built.setdefault("cfg", c))
    monkeypatch.setattr(
        "wisp.composition.WispAgentCore",
        lambda **kw: built.setdefault("core_kw", kw))

    class _Sec:
        pass

    root.security = None
    root.extensions = None
    root.tool_executor = None
    root._create_core()
    assert built["cfg"].model == "first-served"


# ── Sessions serve the latest selected model ───────────────────────────

@pytest.mark.asyncio
async def test_resumed_session_honors_new_model(tmp_path):
    """A /model switch must reach RESUMED sessions — latest wins."""
    calls = []

    saved: dict[str, dict] = {}

    class Store:
        def load_session(self, sid):
            if sid not in saved:
                saved[sid] = {"id": sid, "model": "old-model",
                              "workspace": "/tmp",
                              "messages": [{"role": "user", "content": "hi"}],
                              "compaction_history": []}
            return dict(saved[sid])

        def save_session(self, sess):
            calls.append(dict(sess))
            saved[sess["id"]] = dict(sess)

    from wisp.core.runtime import AgentRuntime

    rt = AgentRuntime(store=Store(), security=MagicMock(),
                      extensions=MagicMock(), telemetry=MagicMock(),
                      core_factory=MagicMock())
    sess = await rt.get_or_create_session("sid-1", "new-hotness", "/tmp")
    assert sess["model"] == "new-hotness"
    assert calls and calls[-1]["model"] == "new-hotness"

    await rt.get_or_create_session("sid-1", "new-hotness", "/tmp")
    assert len(calls) == 1  # no redundant writes when already current


# ── GUI select route shares the REPL seam ───────────────────────────────

@pytest.mark.asyncio
async def test_models_route_lists_all_providers_and_active():
    from wisp.server.routes import models as models_route

    cfg = SimpleNamespace(provider="openrouter", model="org/m1", api_key="k")
    root = SimpleNamespace(config=cfg, runtime=None)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(root=root)))

    with patch("wisp.provider_catalog.list_models",
               side_effect=lambda name, c: [f"{name}/m1", f"{name}/m2"]):
        out = await models_route.list_models(request)

    assert {p["name"] for p in out["providers"]} >= {"ollama", "openai",
                                                     "nvidia", "openrouter"}
    assert out["active"] == {"provider": "openrouter", "model": "org/m1"}
    assert out["models"] == ["openrouter/m1", "openrouter/m2"]


@pytest.mark.asyncio
async def test_select_route_rejects_model_not_served():
    """Selecting a dead model fails VISIBLY here, not as a turn-time 404."""
    from fastapi import HTTPException

    from wisp.server.routes import models as models_route

    cfg = _cfg(provider="openrouter", model="org/m1")
    runtime = SimpleNamespace(config=cfg)
    root = SimpleNamespace(config=cfg, runtime=runtime)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(root=root)))

    with patch("wisp.provider_select.current_key_status", return_value="✓"), \
            patch("wisp.provider_catalog.list_models",
                  return_value=["real/a", "real/b"]):
        with pytest.raises(HTTPException) as ei:
            await models_route.select_model(
                models_route.SelectPayload(model="fake/dead"), request)
    assert ei.value.status_code == 400
    assert "not served" in ei.value.detail
