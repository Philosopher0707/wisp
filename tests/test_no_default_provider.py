"""Wisp has no default model provider, and a blank selection setting is "unset", not a choice.

Before: `provider` defaulted to "ollama" in the settings registry and in a dozen `or "ollama"` fallbacks, so an install nobody had configured silently tried
`http://localhost:11434` and failed with a connection error (or, on a machine that does run Ollama, quietly used it). And a blank `WISP_PROVIDER=` / `WISP_MODEL=` /
`WISP_API_BASE=` line in `~/.config/wisp/.env` beat the value saved in `config.json`: the loader treats an empty environment variable as the operator's explicit choice.

Now:

* `provider` and `ollama_url` default to "" (unset). Ollama stays a supported provider; it is just never assumed.
* A blank (or whitespace) environment value for a SELECTION setting (provider, model, api_base, ollama_url, api_key) falls through to `config.json`. Other settings keep
  "empty means empty" (a flag set to nothing is a choice).
* With no provider the factory, the selection resolver and the boot path say so, in words that name the fix (`wisp setup`), and make no network call.
* The setup wizard has no preselected provider.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import wisp.config as cfg_mod
from wisp.config import SETTINGS_SCHEMA, WispConfig

REPO = Path(__file__).resolve().parents[1]
SELECTION_ENV = {"provider": "WISP_PROVIDER", "model": "WISP_MODEL", "api_base": "WISP_API_BASE", "ollama_url": "WISP_OLLAMA_URL", "api_key": "WISP_API_KEY"}


@pytest.fixture
def saved(isolated_wisp_env, tmp_path):
    """A config.json the test controls, and a clean environment around it."""
    path = tmp_path / "config.json"

    def write(**values):
        path.write_text(json.dumps(values))
        isolated_wisp_env.setattr(cfg_mod, "get_config_path", lambda: path)

    return write


class TestTheRegistryAssumesNoProvider:
    def test_the_defaults_are_unset(self):
        assert SETTINGS_SCHEMA["provider"]["default"] == ""
        assert SETTINGS_SCHEMA["ollama_url"]["default"] == ""

    def test_a_config_built_from_nothing_has_no_provider_and_no_endpoint(self, isolated_wisp_env):
        config = WispConfig()
        assert config.provider == "" and config.ollama_url == "" and config.model == ""

    def test_the_description_does_not_recommend_one(self):
        assert "ollama" in SETTINGS_SCHEMA["provider"]["description"] and "wisp setup" in SETTINGS_SCHEMA["provider"]["description"]

    def test_a_saved_provider_is_used(self, saved):
        saved(provider="nvidia", model="m1")
        config = WispConfig()
        assert (config.provider, config.model) == ("nvidia", "m1")


class TestABlankSelectionIsUnset:
    @pytest.mark.parametrize("key", sorted(SELECTION_ENV))
    @pytest.mark.parametrize("blank", ["", " ", "   ", "\t"])
    def test_a_blank_env_value_does_not_hide_the_saved_one(self, saved, monkeypatch, key, blank):
        saved(**{key: "from-config"})
        monkeypatch.setenv(SELECTION_ENV[key], blank)
        assert cfg_mod.get_setting(key, "") == "from-config"

    @pytest.mark.parametrize("key", sorted(SELECTION_ENV))
    def test_a_real_env_value_still_wins(self, saved, monkeypatch, key):
        saved(**{key: "from-config"})
        monkeypatch.setenv(SELECTION_ENV[key], "from-env")
        assert cfg_mod.get_setting(key, "") == "from-env"

    @pytest.mark.parametrize("key", sorted(SELECTION_ENV))
    def test_blank_with_nothing_saved_is_the_default(self, saved, monkeypatch, key):
        saved()
        monkeypatch.setenv(SELECTION_ENV[key], "")
        assert cfg_mod.get_setting(key, "the-default") == "the-default"

    def test_a_blank_provider_reaches_the_config_as_the_saved_provider(self, saved, monkeypatch):
        saved(provider="nvidia", model="m1")
        monkeypatch.setenv("WISP_PROVIDER", "")
        monkeypatch.setenv("WISP_MODEL", "")
        config = WispConfig()
        assert (config.provider, config.model) == ("nvidia", "m1")

    def test_other_settings_keep_empty_as_an_explicit_choice(self, saved, monkeypatch):
        saved(gate_write_roots="/saved")
        monkeypatch.setenv("WISP_GATE_WRITE_ROOTS", "")
        assert cfg_mod.get_setting("gate_write_roots", "x") == ""

    def test_only_selection_settings_are_marked(self):
        marked = {k for k, v in SETTINGS_SCHEMA.items() if v.get("blank_is_unset")}
        assert marked == set(SELECTION_ENV)


class TestTheFactoryDoesNotGuess:
    @pytest.mark.parametrize("provider", ["", "  ", None, 5, [], "\n"])
    def test_no_provider_is_an_error_that_names_the_fix(self, provider):
        from wisp.providers.factory import ProviderFactory

        with pytest.raises(ValueError) as caught:
            ProviderFactory().from_config(SimpleNamespace(provider=provider, model="m", ollama_url="", api_key="", api_base=""))
        assert "wisp setup" in str(caught.value) and "11434" not in str(caught.value)

    def test_a_config_without_the_attribute_is_the_same_error(self):
        from wisp.providers.factory import ProviderFactory

        with pytest.raises(ValueError, match="wisp setup"):
            ProviderFactory().from_config(SimpleNamespace(model="m"))

    def test_ollama_chosen_with_no_url_uses_the_providers_own_endpoint(self, isolated_wisp_env):
        from wisp.config import DEFAULT_OLLAMA_URL
        from wisp.providers.factory import ProviderFactory

        provider = ProviderFactory().from_config(WispConfig().replace(provider="ollama", model="m", ollama_url=""))
        assert provider.base_url == DEFAULT_OLLAMA_URL

    def test_ollama_chosen_with_its_own_url_uses_it(self, isolated_wisp_env):
        from wisp.providers.factory import ProviderFactory

        provider = ProviderFactory().from_config(WispConfig().replace(provider="ollama", model="m", ollama_url="http://127.0.0.1:9"))
        assert provider.base_url == "http://127.0.0.1:9"

    def test_a_placeholder_url_is_refused_not_dialled(self):
        from wisp.providers.factory import ProviderFactory

        with pytest.raises(ValueError):
            ProviderFactory().from_config(SimpleNamespace(provider="ollama", model="m", ollama_url="---", api_key="", api_base=""))


class TestTheSelectionResolverSaysSo:
    @pytest.mark.parametrize("provider", ["", "  ", None])
    def test_no_provider_is_its_own_status(self, provider):
        from wisp.provider_catalog import resolve_selection
        from wisp.provider_select import KNOWN_PROVIDERS

        out = resolve_selection(SimpleNamespace(provider=provider, model="", api_key="", api_base="", ollama_url=""))
        assert out.status == "no_provider" and "wisp setup" in out.detail
        assert out.suggested in ("", None) and set(KNOWN_PROVIDERS) <= set(out.alternatives)

    def test_the_resolver_makes_no_network_call_without_a_provider(self, monkeypatch):
        import wisp.provider_catalog as pc

        def boom(*args, **kwargs):
            raise AssertionError("listed models without a provider")

        monkeypatch.setattr(pc, "list_models", boom)
        assert pc.resolve_selection(SimpleNamespace(provider="", model="x")).status == "no_provider"

    def test_the_composition_root_builds_a_core_that_says_what_is_missing(self, isolated_wisp_env, monkeypatch):
        """The REPL, the server and the desktop app build a core per turn; raising here would kill them before `/provider` or `/setup` could fix the choice.
        The core answers every turn with the fix and constructs no provider (so dials nothing)."""
        import wisp.providers.factory as factory_mod
        from wisp.composition import CompositionRoot

        def boom(*args, **kwargs):
            raise AssertionError("built a provider with none configured")

        monkeypatch.setattr(factory_mod.ProviderFactory, "from_config", boom)
        root = CompositionRoot(WispConfig())
        try:
            core = root._create_core()
            events = list(core.provider.generate_stream_events(messages=[]))
        finally:
            root.shutdown()
        assert [e["type"] for e in events] == ["error"] and "wisp setup" in events[0]["text"]


class TestNothingElseAssumesOllama:
    def test_the_repl_completion_has_no_current_provider(self, isolated_wisp_env):
        from wisp.repl.completion import _current_provider

        assert _current_provider() == ""

    def test_the_model_listing_route_marks_no_provider_active(self, isolated_wisp_env):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from wisp.server.routes.models import router

        app = FastAPI()
        app.include_router(router)
        config = WispConfig()
        app.state.root = SimpleNamespace(config=config, runtime=SimpleNamespace(config=config))
        body = TestClient(app).get("/api/models").json()
        assert body["active"]["provider"] == "" and body["models"] == [], body

    def test_the_wizard_has_no_preselected_provider(self, monkeypatch):
        import wisp.cli.setup as setup_mod

        setup_mod_calls: list[str] = []
        answers = iter(["", "", "2"])  # two empty answers (must NOT pick anything), then "2"

        def ask(prompt):
            setup_mod_calls.append(prompt)
            try:
                return next(answers)
            except StopIteration:
                raise KeyboardInterrupt

        lines: list[str] = []
        setup_mod.run_setup(ask, lambda p: "", lines.append, probe_fn=lambda *a, **k: (True, "ok"))
        provider_step = "\n".join(lines).split("[2/4]")[0]  # the model step later may offer a default model; the PROVIDER step must not
        assert "(default)" not in provider_step and "default 1" not in " ".join(setup_mod_calls[:3])
        assert sum(1 for p in setup_mod_calls if p.startswith("Provider")) >= 3  # the empty answers re-asked

    def test_no_source_file_falls_back_to_ollama_by_name(self):
        """The fallback is gone from every module that used to carry it. A provider named in a LIST (the factory table, the wizard's options, KNOWN_PROVIDERS) is fine;
        `or "ollama"` and a default argument of "ollama" are not."""
        import re

        offenders = []
        for path in (REPO / "wisp").rglob("*.py"):
            if "providers/ollama" in str(path):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"""(or\s+["']ollama["'])|(get_setting\(\s*["']provider["']\s*,\s*["']ollama["'])|(getattr\([^)]*["']provider["']\s*,\s*["']ollama["']\))""", line):
                    offenders.append(f"{path.relative_to(REPO)}:{number}")
        assert offenders == []


class TestAFreshInstallThroughTheRealEntryPoint:
    """The real console entry in a subprocess, with an empty HOME (no config.json, no .env) and no WISP_* variables."""

    def _run(self, tmp_path, *args):
        home = tmp_path / "home"
        home.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("WISP_") and not k.endswith("_API_KEY")}
        env.update({"HOME": str(home), "PYTHONPATH": str(REPO), "TERM": "dumb", "NO_COLOR": "1"})
        return subprocess.run([sys.executable, "-P", "-m", "wisp", *args], cwd=project, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)

    def test_check_says_what_to_do_and_does_not_dial_ollama(self, tmp_path):
        result = self._run(tmp_path, "check")
        output = result.stdout + result.stderr
        assert result.returncode != 0
        assert "wisp setup" in output
        assert "11434" not in output and "refused" not in output.lower()

    def test_a_one_shot_prompt_says_the_same_and_does_not_dial_ollama(self, tmp_path):
        result = self._run(tmp_path, "say hi")
        output = result.stdout + result.stderr
        assert result.returncode != 0
        assert "wisp setup" in output
        assert "11434" not in output and "refused" not in output.lower()
