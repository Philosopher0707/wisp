"""GH#18: interactive setup wizard pins.

All I/O injected — no tty, no network, no real HOME touched. Secrets
never round-trip through these tests; password_fn is a stub.
"""

from __future__ import annotations

import stat

import pytest

from wisp.cli.setup import (
    SETUP_PROVIDERS,
    _needs_setup,
    maybe_offer_setup,
    run_setup,
)


class Script:
    """Canned terminal inputs + password stub + captured output."""

    def __init__(self, inputs: list[str], passwords: list[str] | None = None) -> None:
        self._inputs = list(inputs)
        self._passwords = list(passwords or [])
        self.out: list[str] = []

    def input(self, prompt: str = "") -> str:
        self.out.append(prompt)
        assert self._inputs, f"out of inputs at prompt: {prompt!r}"
        return self._inputs.pop(0)

    def password(self, prompt: str = "") -> str:
        self.out.append(prompt)
        assert self._passwords, f"out of passwords at prompt: {prompt!r}"
        return self._passwords.pop(0)

    def write(self, text: str) -> None:
        self.out.append(text)

    @property
    def text(self) -> str:
        return "\n".join(self.out)


@pytest.fixture()
def no_persist(monkeypatch):
    """Capture persist/store_key calls instead of touching disk/env."""
    import wisp.provider_select as ps

    calls: dict = {"persist": [], "store_key": []}
    monkeypatch.setattr(ps, "persist",
                        lambda update: calls["persist"].append(update) or True)
    monkeypatch.setattr(ps, "store_key",
                        lambda p, k: calls["store_key"].append((p, k)))
    return calls


def _ok_probe(provider, model, key, base, timeout_s=8.0):
    return True, "handshake ok"


def _fail_probe(provider, model, key, base, timeout_s=8.0):
    return False, "unauthorized (bad key?)"


class TestWizardFlow:
    def test_ollama_happy_path(self, no_persist, monkeypatch) -> None:
        import wisp.cli.setup as setup_mod

        monkeypatch.setattr(setup_mod, "_ollama_models", lambda base: [])
        s = Script(inputs=["", "1", "qwen2.5-coder:latest", ""])
        result = run_setup(s.input, s.password, s.write, probe_fn=_ok_probe)
        assert result == {"provider": "ollama", "model": "qwen2.5-coder:latest",
                          "validated": True, "saved": True}
        for step in ("[1/4]", "[2/4]", "[3/4]", "[4/4]"):
            assert step in s.text
        assert no_persist["persist"][-1]["provider"] == "ollama"
        assert no_persist["persist"][-1]["ollama_url"] == "http://localhost:11434"
        assert no_persist["store_key"] == []  # ollama needs no key

    def test_openai_env_key_preferred(self, no_persist, monkeypatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-123")
        s = Script(inputs=["2", "", "y"])
        result = run_setup(s.input, s.password, s.write, probe_fn=_ok_probe)
        assert result is not None and result["provider"] == "openai"
        assert result["model"] == "gpt-4o"
        assert no_persist["store_key"] == []  # env-sourced: never written
        assert "OPENAI_API_KEY" in s.text or "environment" in s.text

    def test_typed_key_persisted_via_store_key(self, no_persist, monkeypatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("WISP_API_KEY", raising=False)
        s = Script(inputs=["2", "", ""], passwords=["sk-typed-456"])
        result = run_setup(s.input, s.password, s.write, probe_fn=_ok_probe)
        assert result is not None and result["validated"] is True
        assert no_persist["store_key"] == [("openai", "sk-typed-456")]

    def test_invalid_key_retry_then_save_anyway(self, no_persist, monkeypatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("WISP_API_KEY", raising=False)
        s = Script(inputs=["2", "", "s"], passwords=["sk-bad"])
        result = run_setup(s.input, s.password, s.write, probe_fn=_fail_probe)
        assert result is not None
        assert result["validated"] is False and result["saved"] is True
        assert "unauthorized" in s.text

    def test_abort_saves_nothing(self, no_persist, monkeypatch) -> None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("WISP_API_KEY", raising=False)
        s = Script(inputs=["4", "", ""], passwords=[""])
        # openrouter idx 4 -> needs key -> empty key aborts
        result = run_setup(s.input, s.password, s.write, probe_fn=_ok_probe)
        assert result is None
        assert no_persist["persist"] == [] and no_persist["store_key"] == []

    def test_no_anthropic_entry(self) -> None:
        # Factory cannot build anthropic — offering it repeats the
        # '/provider mock succeeded then turn crashed' bug class.
        assert "anthropic" not in SETUP_PROVIDERS
        assert set(SETUP_PROVIDERS) == {"ollama", "openai", "nvidia", "openrouter"}

    def test_non_tty_refuses(self, monkeypatch) -> None:
        import sys

        class DeadStdin:
            def isatty(self):
                return False

        monkeypatch.setattr(sys, "stdin", DeadStdin())
        out: list[str] = []
        assert run_setup(out=out.append) is None
        assert any("interactive terminal" in line for line in out)


class TestNeedsSetup:
    def test_mock_counts_as_usable(self) -> None:
        from wisp.config import WispConfig
        assert _needs_setup(WispConfig().replace(provider="mock")) is False

    def test_empty_or_unknown_needs_setup(self) -> None:
        from wisp.config import WispConfig
        assert _needs_setup(WispConfig().replace(provider="")) is True
        assert _needs_setup(WispConfig().replace(provider="wat")) is True

    def test_keyed_provider_without_key_needs_setup(self, monkeypatch) -> None:
        from wisp.config import WispConfig
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("WISP_API_KEY", raising=False)
        assert _needs_setup(WispConfig().replace(provider="openai")) is True

    def test_maybe_offer_no_tty_never_prompts(self, monkeypatch) -> None:
        import sys

        from wisp.config import WispConfig

        class DeadStdin:
            def isatty(self):
                return False

        monkeypatch.setattr(sys, "stdin", DeadStdin())
        assert maybe_offer_setup(WispConfig().replace(provider="")) is False


class TestSecurePersistence:
    def test_save_config_is_0600(self, tmp_path, monkeypatch) -> None:
        import wisp.config as cfg_mod

        # NOTE: WISP_CONFIG_DIR binds at import — patch the constant, not HOME.
        monkeypatch.setattr(cfg_mod, "WISP_CONFIG_DIR", tmp_path / ".config" / "wisp")
        cfg_mod.save_config({"provider": "ollama", "model": "m"})
        mode = stat.S_IMODE((tmp_path / ".config" / "wisp" / "config.json").stat().st_mode)
        assert mode == 0o600

    def test_env_upsert_is_0600(self, tmp_path) -> None:
        from wisp.provider_select import _upsert_env_file

        target = tmp_path / ".env"
        _upsert_env_file(target, {"WISP_API_KEY": "sk-x"})
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert "sk-x" in target.read_text()

    def test_probe_failure_shape_never_raises(self) -> None:
        from wisp.cli.setup import _probe

        ok, detail = _probe("nope-provider", "m", "", "", timeout_s=3.0)
        assert ok is False and isinstance(detail, str) and detail
