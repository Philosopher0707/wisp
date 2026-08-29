"""Tests for the guided provider/model/key setup flow (wisp/repl/commands/provider.py).

Exercises `/setup` and `/provider` end-to-end with all I/O + network seams
mocked: the picker, the key prompt, model listing, and persistence.
"""

import copy

import pytest

from wisp.commands import dispatch
from wisp.repl.commands.provider import (
    _apply_model_switch as _real_apply_model_switch,
    _ensure_api_key,
    _guided_provider_flow,
)


class FakeConfig:
    def __init__(self):
        self.provider = "ollama"
        self.model = "llama3"
        self.workspace = "/tmp"
        self._context_tokens_explicit = False

    def replace(self, **kwargs):
        inst = copy.copy(self)
        for k, v in kwargs.items():
            setattr(inst, k, v)
        return inst


class FakeAgent:
    def __init__(self):
        self.config = FakeConfig()
        self.messages = []
        self.session = {"id": "s1", "messages": self.messages}
        self.client = None
        self._system_prompt_cache = {}

    def _save_session(self):
        pass


@pytest.fixture
def agent():
    return FakeAgent()


@pytest.fixture(autouse=True)
def _patch_seams(monkeypatch):
    """Stub every external seam the wizard touches."""
    monkeypatch.setattr("wisp.repl.commands.provider._pick", _FakePicker.return_value)
    monkeypatch.setattr("wisp.repl.commands.provider._ensure_api_key",
                        lambda agent, name: True)
    monkeypatch.setattr("wisp.repl.commands.provider._list_models",
                        lambda prov, agent: ["qwen2.5-coder", "deepseek-v4", "llama3"])
    monkeypatch.setattr("wisp.repl.commands.provider._apply_model_switch",
                        _record_apply)
    monkeypatch.setattr("wisp.provider_select.apply_switch", _fake_apply_switch)
    monkeypatch.setattr("wisp.provider_select.persist", _record_persist)
    monkeypatch.setattr("wisp.provider_catalog.clear_models_cache", lambda name: None)


_APPLIED: list[tuple] = []
_PERSISTED: list[dict] = []


def _record_apply(agent, provider, new_model, persist_choice=True, switch_provider=False):
    _APPLIED.append((provider, new_model, switch_provider))


def _record_persist(update: dict) -> bool:
    _PERSISTED.append(update)
    return True


def _fake_reset():
    _APPLIED.clear()
    _PERSISTED.clear()


def _fake_apply_switch(runtime, session, config, provider=None, model="",
                       **kwargs):
    kw = {}
    if provider:
        kw["provider"] = provider
    if model is not None:
        kw["model"] = model
    return config.replace(**kw)


class _FakePicker:
    """Programmable picker — scripted return values consumed in order."""

    _returns = []

    @staticmethod
    def set_returns(values):
        _FakePicker._returns = list(values)

    @staticmethod
    def return_value(title, options, current=None, descriptions=None):
        if not _FakePicker._returns:
            return None  # cancel
        return _FakePicker._returns.pop(0)


# ── Guided wizard flow ───────────────────────────────────────────────


class TestGuidedFlow:
    def test_full_flow_switches_provider_and_model(self, agent, monkeypatch):
        _fake_reset()
        # Use the real _apply_model_switch so persist() runs too (its
        # missing_key check needs the key seam on for 'openai').
        monkeypatch.setattr("wisp.repl.commands.provider._apply_model_switch",
                            _real_apply_model_switch)
        monkeypatch.setattr("wisp.provider_select.missing_key", lambda name: None)
        # index 1 = 'openai'; model index 1 = 'deepseek-v4'
        _FakePicker.set_returns([1, 1])
        idx = _guided_provider_flow(agent, start_at="ollama")
        assert idx == "openai"
        assert agent.config.provider == "openai"
        assert agent.config.model == "deepseek-v4"
        assert _PERSISTED == [{"model": "deepseek-v4", "provider": "openai"}]

    def test_cancel_at_provider_returns_none(self, agent):
        _FakePicker.set_returns([None])
        assert _guided_provider_flow(agent) is None

    def test_cancel_at_model_aborts_without_change(self, agent):
        _FakePicker.set_returns([0, None])  # provider then cancel model
        assert _guided_provider_flow(agent) is None

    def test_missing_key_stops_flow(self, agent, monkeypatch):
        monkeypatch.setattr("wisp.repl.commands.provider._ensure_api_key",
                            lambda agent, name: False)
        _FakePicker.set_returns([0])
        assert _guided_provider_flow(agent) is None

    def test_no_live_models_still_switches_with_unset_model(self, agent, monkeypatch):
        monkeypatch.setattr("wisp.repl.commands.provider._list_models",
                            lambda prov, agent: [])
        _FakePicker.set_returns([2])  # index 2 = 'nvidia'
        idx = _guided_provider_flow(agent)
        assert idx == "nvidia"
        assert agent.config.provider == "nvidia"
        assert agent.config.model == ""  # unset → resolved live at turn time


# ── /setup and /provider commands ────────────────────────────────────


class TestSetupCommand:
    def test_setup_without_args_runs_flow(self, agent, capsys):
        _fake_reset()
        _FakePicker.set_returns([0, 1])  # provider 'ollama', model 'deepseek-v4'
        result = dispatch("/setup", agent)
        assert result is True  # consumed, no follow-up turn
        assert _APPLIED == [("ollama", "deepseek-v4", True)]

    def test_setup_cancelled_prints_listing(self, agent, capsys):
        _FakePicker.set_returns([None])
        dispatch("/setup", agent)
        out = capsys.readouterr().out
        assert "Current provider" in out


class TestProviderCommand:
    def test_provider_with_explicit_name_switches(self, agent, monkeypatch, capsys):
        _fake_reset()
        monkeypatch.setattr("wisp.provider_select.missing_key", lambda name: None)
        dispatch("/provider mock", agent)
        assert agent.config.provider == "mock"
        assert agent.config.model == ""
        assert _PERSISTED[-1] == {"provider": "mock", "model": ""}

    def test_provider_unknown_name_errors(self, agent, capsys):
        result = dispatch("/provider bogus", agent)
        out = capsys.readouterr().out
        assert result is True
        assert "Unknown provider" in out

    def test_provider_same_name_is_noop(self, agent, capsys):
        dispatch("/provider ollama", agent)
        out = capsys.readouterr().out
        assert "ollama" in out


# ── _ensure_api_key ──────────────────────────────────────────────────


class TestEnsureApiKey:
    def test_no_key_needed_for_mock(self):
        assert _ensure_api_key(FakeAgent(), "mock") is True

    def test_prompts_and_stores_when_missing(self, monkeypatch):
        agent = FakeAgent()

        def fake_missing(name):
            return "missing key" if name == "openai" else None

        monkeypatch.setattr("wisp.provider_select.missing_key", fake_missing)
        monkeypatch.setattr("getpass.getpass", lambda prompt: "sk-123")
        store_calls = []

        def fake_store(name, key):
            store_calls.append((name, key))

        monkeypatch.setattr("wisp.provider_select.store_key", fake_store)
        monkeypatch.setattr("wisp.provider_select.probe", lambda cand: (True, "ok"))
        monkeypatch.setattr("wisp.provider_select.build_provider", lambda **kw: None)

        assert _ensure_api_key(agent, "openai") is True
        assert store_calls == [("openai", "sk-123")]

    def test_empty_entry_cancels(self, monkeypatch):
        monkeypatch.setattr("wisp.provider_select.missing_key",
                            lambda name: "missing key")
        monkeypatch.setattr("getpass.getpass", lambda prompt: "")
        assert _ensure_api_key(FakeAgent(), "openai") is False