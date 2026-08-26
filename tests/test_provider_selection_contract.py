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


def test_resolve_nvidia_unreachable_when_list_empty():
    """Cloud with key but empty listing is unreachable, not 'ok'."""
    from wisp.provider_catalog import resolve_selection

    cfg = SimpleNamespace(provider="nvidia", model="some/model", api_key="k")
    with patch("wisp.provider_catalog.list_models", return_value=[]):
        r = resolve_selection(cfg)
    assert r.status == "unreachable"
    assert "not reachable" in r.detail


def test_resolve_nvidia_no_key_is_unreachable():
    """Cloud without key is unreachable at selection time, not 404 mid-turn."""
    from wisp.provider_catalog import resolve_selection

    cfg = SimpleNamespace(provider="nvidia", model="some/model", api_key="")
    r = resolve_selection(cfg)
    assert r.status == "unreachable"
    assert "requires an API key" in r.detail


def test_nvidia_unknown_autocorrects_in_composition(monkeypatch, tmp_path):
    """_create_core auto-corrects qwen on nvidia to a live nvidia model."""
    from wisp.composition import CompositionRoot

    root = CompositionRoot.__new__(CompositionRoot)
    root.config = _cfg(provider="nvidia", model="qwen2.5-coder", api_key="k")
    root.runtime = SimpleNamespace(config=root.config)
    monkeypatch.setattr("wisp.provider_catalog.list_models",
                        lambda p, c: ["nvidia/llama-3.1-nemotron-70b-instruct", "nvidia/nemotron-3-ultra-550b-a55b"] if p == "nvidia" else [])
    built = {}
    monkeypatch.setattr("wisp.providers.factory.ProviderFactory.from_config",
                        lambda self, c: built.setdefault("cfg", c))
    monkeypatch.setattr("wisp.composition.WispAgentCore", lambda **kw: built.setdefault("core_kw", kw))
    root.security = None
    root.extensions = None
    root.tool_executor = None
    root._create_core()
    assert built["cfg"].model == "nvidia/llama-3.1-nemotron-70b-instruct"


def test_openai_tool_call_on_stop():
    """Provider must yield tool_call even when finish_reason is stop."""
    from wisp.providers.openai import OpenAIProvider

    prov = OpenAIProvider.__new__(OpenAIProvider)
    prov.api_key = "k"
    prov.api_base = "https://example.com/v1"
    prov.model = "test-model"
    prov.temperature = 0.2
    prov.max_tokens = None
    sse = [
        b'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "write_file", "arguments": ""}}]}, "finish_reason": null}]}',
        b'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\\"path\\": \\"./output.md\\", \\"content\\": \\"hi\\"}"}}]}, "finish_reason": null}]}',
        b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}',
        b'data: [DONE]',
    ]

    class FakeResp:
        status_code = 200
        def iter_lines(self):
            for l in sse:
                yield l
        @property
        def text(self):
            return ""

    with patch("requests.post", return_value=FakeResp()):
        events = list(prov.generate_stream_events("sys", [{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "write_file"}}]))
    assert any(e.get("type") == "tool_call" and e.get("name") == "write_file" for e in events)


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


# ── One construction path: factory covers KNOWN_PROVIDERS ──────────────

def _ns(**kw):
    base = {"provider": "ollama", "model": "", "api_key": "",
            "ollama_url": "http://x", "api_base": ""}
    base.update(kw)
    return SimpleNamespace(**base)


def test_factory_builds_every_selectable_provider():
    """Every provider /provider offers must actually construct — `mock` was
    selectable once while the factory crashed 'Unknown provider: mock' on
    the first turn."""
    from wisp.provider_catalog import list_providers
    from wisp.providers.factory import ProviderFactory

    factory = ProviderFactory()
    for info in list_providers():
        assert info.name in factory.list_providers(), (
            f"provider '{info.name}' is selectable but not buildable")


def test_factory_builds_mock_end_to_end():
    from wisp.providers import MockProvider
    from wisp.providers.factory import ProviderFactory

    p = ProviderFactory().from_config(_ns(provider="mock"))
    assert isinstance(p, MockProvider)


def test_get_provider_delegates_to_factory():
    """/provider's probe path (get_provider) and the turn-time core builder
    (factory.from_config) must agree — drift here let selection succeed and
    the actual turn fail."""
    from wisp.providers import MockProvider, get_provider

    assert isinstance(get_provider(_ns(provider="mock")), MockProvider)


def test_factory_passes_empty_model_through_no_fallback():
    """No stale hardcoded model ids: empty model in → empty model out; the
    catalog resolves it to a real served model."""
    from wisp.providers.factory import ProviderFactory

    p = ProviderFactory().from_config(_ns(provider="openai", model=""))
    assert getattr(p, "model", "") == ""

    import inspect
    from wisp.providers import factory as fm

    src = inspect.getsource(fm)
    for rotten in ("gpt-4o", "nemotron-3-ultra", "openrouter/auto",
                   "qwen2.5-coder"):
        assert rotten not in src, f"factory hardcodes stale default {rotten}"


def test_factory_uses_key_vault(monkeypatch):
    """Factory must resolve keys via provider_select.resolve_key — its own
    inline env fallbacks were a second key-resolution implementation."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-vault")
    monkeypatch.delenv("WISP_API_KEY", raising=False)

    from wisp.providers.factory import ProviderFactory

    p = ProviderFactory().from_config(
        _ns(provider="nvidia", api_key="", model=""))
    assert getattr(p, "api_key", None) == "nv-vault"


def test_is_strict_provider_single_source():
    """/provider slash command, composition auto-correct, and catalog
    leniency all derive strictness from requires_key via is_strict_provider
    — no more per-module hardcoded ('nvidia','openai','openrouter')."""
    from wisp.provider_select import KNOWN_PROVIDERS, is_strict_provider

    for name, spec in KNOWN_PROVIDERS.items():
        assert is_strict_provider(name) == bool(spec.get("requires_key"))


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
