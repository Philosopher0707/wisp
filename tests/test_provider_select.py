"""Provider/model selection: parsing, switching, probing, REPL commands.

The REPL could previously only list Ollama models. These tests pin the
provider-aware selection layer: explicit provider targeting (`/provider`),
provider-qualified model targets, health probing before commit, runtime
core-cache invalidation, and config persistence.
"""

from types import SimpleNamespace

import pytest

from wisp.provider_select import (
    KNOWN_PROVIDERS,
    apply_switch,
    build_provider,
    parse_target,
    probe,
)


# ── parse_target ─────────────────────────────────────────────────────


class TestParseTarget:
    def test_bare_provider_name(self):
        assert parse_target("nvidia") == {"provider": "nvidia", "model": None}

    def test_bare_model_name(self):
        assert parse_target("gpt-4o") == {"provider": None, "model": "gpt-4o"}

    def test_space_form(self):
        assert parse_target("openai gpt-4o") == {
            "provider": "openai", "model": "gpt-4o"}

    def test_slash_form_prefers_known_provider(self):
        # 'nvidia' is a known provider → left side wins.
        assert parse_target("nvidia/nemotron-3-ultra") == {
            "provider": "nvidia", "model": "nemotron-3-ultra"}

    def test_slash_with_unknown_left_is_model(self):
        # Model ids legitimately contain slashes ('org/name'); without a
        # known provider on the left it is just a model id.
        assert parse_target("meta/llama-3") == {
            "provider": None, "model": "meta/llama-3"}

    def test_empty(self):
        assert parse_target("") == {"provider": None, "model": None}
        assert parse_target("   ") == {"provider": None, "model": None}


# ── build_provider / probe ───────────────────────────────────────────


class TestBuildProvider:
    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unsupported provider"):
            build_provider("warpdrive", model="x")

    @pytest.mark.parametrize("name,cls", [
        ("ollama", "OllamaProvider"),
        ("openai", "OpenAIProvider"),
        ("nvidia", "NVIDIAProvider"),
    ])
    def test_known_names_honor_model(self, name, cls):
        p = build_provider(name, model="m-1")
        assert type(p).__name__ == cls
        assert getattr(p, "model", None) == "m-1"

    def test_mock_constructs(self):
        from wisp.providers import MockProvider
        assert isinstance(build_provider("mock", model="ignored"), MockProvider)

    def test_all_known_providers_declared(self):
        for required in ("ollama", "openai", "nvidia", "mock"):
            assert required in KNOWN_PROVIDERS
            assert "requires_key" in KNOWN_PROVIDERS[required]


class TestProbe:
    def test_healthy(self):
        ok = SimpleNamespace(check_health=lambda: True)
        good, detail = probe(ok)
        assert good and detail == ""

    def test_unhealthy(self):
        bad = SimpleNamespace(check_health=lambda: False)
        good, detail = probe(bad)
        assert not good

    def test_exception_is_caught(self):
        def boom():
            raise RuntimeError("connection refused")
        good, detail = probe(SimpleNamespace(check_health=boom))
        assert not good
        assert "connection refused" in detail


# ── apply_switch ─────────────────────────────────────────────────────


class RecordingRuntime:
    def __init__(self):
        self.invalidations = 0

    def invalidate_core_cache(self):
        self.invalidations += 1


class TestApplySwitch:
    def test_updates_session_and_invalidates(self):
        rt = RecordingRuntime()
        session = {"id": "s1", "model": "old"}
        cfg = SimpleNamespace(provider="ollama", model="old")
        new_cfg = apply_switch(rt, session, cfg, provider="nvidia", model="m2")
        assert new_cfg.provider == "nvidia"
        assert new_cfg.model == "m2"
        assert session["model"] == "m2"
        assert rt.invalidations == 1

    def test_runtime_config_is_source_of_truth(self):
        """Next-turn core rebuild reads runtime.config — switching only the
        adapter copy made turns silently revert (caught in live smoke)."""
        from wisp.config import WispConfig

        class Rt:
            def __init__(self):
                self.config = WispConfig().replace(
                    provider="mock", model="mock-1")
                self.invalidations = 0

            def invalidate_core_cache(self):
                self.invalidations += 1

        rt = Rt()
        session = {"model": "mock-1"}
        adapter_view = rt.config  # adapter starts sharing runtime config
        apply_switch(rt, session, adapter_view,
                     provider="nvidia", model="nvidia/nemotron-3-ultra")
        assert rt.config.provider == "nvidia"
        assert rt.config.model == "nvidia/nemotron-3-ultra"
        assert rt.invalidations == 1

    def test_model_only_keeps_provider(self):
        rt = RecordingRuntime()
        session = {"model": "a"}
        cfg = SimpleNamespace(provider="openai", model="a")
        new_cfg = apply_switch(rt, session, cfg, provider=None, model="b")
        assert new_cfg.provider == "openai" and new_cfg.model == "b"

    def test_provider_only_keeps_model(self):
        cfg = SimpleNamespace(provider="openai", model="gpt-x")
        new_cfg = apply_switch(RecordingRuntime(), {"model": "gpt-x"}, cfg,
                               provider="nvidia", model=None)
        assert new_cfg.model == "gpt-x" and new_cfg.provider == "nvidia"


# ── REPL commands ────────────────────────────────────────────────────


class FakeClient:
    def __init__(self, models):
        self.model = "test-model"
        self._models = models

    def list_models(self):
        return [{"name": n} for n in self._models]


class CmdAgent:
    """Minimal adapter stand-in matching AgentAdapter's used surface."""

    def __init__(self, provider="ollama", models=("test-model", "qwen2.5-coder")):
        from wisp.config import WispConfig
        self.config = WispConfig().replace(
            workspace="/tmp/wisp-psel", provider=provider, model="test-model")
        self.session = {"id": "s", "messages": [], "model": "test-model"}
        self.messages = self.session["messages"]
        self.client = FakeClient(list(models))
        self.runtime = RecordingRuntime()
        self._system_prompt_cache = {}

    def _save_session(self):
        pass


@pytest.fixture()
def no_persist(monkeypatch):
    """Keep switches from touching the real user config file."""
    calls = []
    monkeypatch.setattr("wisp.provider_select.persist",
                        lambda update: calls.append(update))
    return calls


class TestProviderCommand:
    def test_list_providers_marks_current(self, capsys, no_persist):
        from wisp import commands as C
        C.cmd_provider(CmdAgent(provider="ollama"), "")
        out = capsys.readouterr().out
        for name in ("ollama", "openai", "nvidia"):
            assert name in out
        assert "→ ollama" in out or "→" in out and "ollama" in out

    def test_switch_requires_key_when_missing(self, capsys, no_persist, monkeypatch):
        from wisp import commands as C
        monkeypatch.delenv("WISP_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        agent = CmdAgent(provider="ollama")
        C.cmd_provider(agent, "openai")
        out = capsys.readouterr().out
        assert "API key" in out
        assert agent.config.provider == "ollama"  # unchanged

    def test_switch_to_mock_succeeds(self, capsys, no_persist):
        from wisp import commands as C
        agent = CmdAgent(provider="ollama")
        C.cmd_provider(agent, "mock")
        out = capsys.readouterr().out
        assert "mock" in out
        assert agent.config.provider == "mock"
        assert agent.runtime.invalidations >= 1
        assert no_persist and no_persist[0].get("provider") == "mock"

    def test_unknown_provider_rejected(self, capsys, no_persist):
        from wisp import commands as C
        agent = CmdAgent()
        C.cmd_provider(agent, "warpdrive")
        assert "Unknown provider" in capsys.readouterr().out
        assert agent.config.provider == "ollama"


class TestModelCommandProviderAware:
    def test_show_uses_current_provider_models(self, capsys, no_persist, monkeypatch):
        from wisp import commands as C
        monkeypatch.setattr("wisp.provider_catalog.list_models",
                            lambda p, c: ["test-model", "qwen2.5-coder"])
        C.cmd_model(CmdAgent(provider="ollama"), "")
        out = capsys.readouterr().out
        assert "qwen2.5-coder" in out
        assert "ollama" in out.lower()

    def test_provider_qualified_switch(self, no_persist, monkeypatch):
        from wisp import commands as C
        monkeypatch.setattr("wisp.provider_catalog.list_models",
                            lambda p, c: ["mock-xl", "other"] if p == "mock" else ["test-model", "qwen2.5-coder"])
        agent = CmdAgent(provider="ollama")
        C.cmd_model(agent, "mock mock-xl")
        assert agent.config.provider == "mock"
        assert agent.config.model == "mock-xl"
        assert agent.session["model"] == "mock-xl"

    def test_number_selection_within_provider(self, no_persist, monkeypatch):
        from wisp import commands as C
        monkeypatch.setattr("wisp.provider_catalog.list_models",
                            lambda p, c: ["test-model", "qwen2.5-coder"])
        agent = CmdAgent(provider="ollama",
                         models=("test-model", "qwen2.5-coder"))
        C.cmd_model(agent, "2")
        assert agent.config.model == "qwen2.5-coder"

    def test_legacy_client_model_still_updated(self, no_persist, monkeypatch):
        from wisp import commands as C
        monkeypatch.setattr("wisp.provider_catalog.list_models",
                            lambda p, c: ["test-model", "qwen2.5-coder"])
        agent = CmdAgent()
        C.cmd_model(agent, "qwen2.5-coder")
        assert agent.client.model == "qwen2.5-coder"

    def test_switch_persists_choice(self, no_persist, monkeypatch):
        from wisp import commands as C
        monkeypatch.setattr("wisp.provider_catalog.list_models",
                            lambda p, c: ["test-model", "qwen2.5-coder"])
        agent = CmdAgent()
        C.cmd_model(agent, "qwen2.5-coder")
        assert any(u.get("model") == "qwen2.5-coder" for u in no_persist)

    def test_runtime_cache_invalidated(self, no_persist, monkeypatch):
        from wisp import commands as C
        monkeypatch.setattr("wisp.provider_catalog.list_models",
                            lambda p, c: ["test-model", "qwen2.5-coder"])
        agent = CmdAgent()
        C.cmd_model(agent, "qwen2.5-coder")
        assert agent.runtime.invalidations == 1
