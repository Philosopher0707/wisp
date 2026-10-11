"""`--provider X` must not inherit another provider's endpoint or key.

Repro (2026-10-06): config says provider=openrouter with WISP_API_BASE and WISP_API_KEY set for
OpenRouter. `wisp --print ... --provider nvidia` rebuilt the config with only `provider` replaced,
so the nvidia provider was built against openrouter.ai with the OpenRouter key (402, then 401 once
the base was fixed). The `/provider` command already reset the base (apply_switch); the flag paths
did not.
"""
import pytest

OPENROUTER = "https://openrouter.ai/api/v1"
NVIDIA = "https://integrate.api.nvidia.com/v1"


@pytest.fixture
def configured_for_openrouter(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    for k in ("WISP_PROVIDER", "WISP_API_BASE", "WISP_API_KEY", "OPENAI_API_KEY",
              "NVIDIA_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_BASE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("WISP_PROVIDER", "openrouter")
    monkeypatch.setenv("WISP_API_BASE", OPENROUTER)
    monkeypatch.setenv("WISP_API_KEY", "or-shared-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-own-key")


def _build(config):
    from wisp.providers.factory import ProviderFactory
    return ProviderFactory().from_config(config)


def test_the_precondition_holds(configured_for_openrouter):
    from wisp.config import WispConfig
    cfg = WispConfig()
    assert (cfg.provider, cfg.api_base, cfg.api_key) == ("openrouter", OPENROUTER, "or-shared-key")


def test_switching_provider_uses_that_providers_endpoint_and_key(configured_for_openrouter):
    from wisp.config import WispConfig
    from wisp.provider_select import with_provider
    p = _build(with_provider(WispConfig(), "nvidia"))
    assert p.api_base == NVIDIA
    assert p.api_key == "nv-own-key"


def test_same_provider_keeps_the_users_custom_endpoint_and_key(configured_for_openrouter, monkeypatch):
    from wisp.config import WispConfig
    from wisp.provider_select import with_provider
    monkeypatch.setenv("WISP_API_BASE", "https://openrouter.ai/api/v1/")
    p = _build(with_provider(WispConfig(), "openrouter"))
    assert p.api_key == "or-shared-key"
    assert p.api_base.startswith(OPENROUTER)


def test_switching_back_to_openrouter_does_not_use_the_nvidia_key(configured_for_openrouter):
    from wisp.config import WispConfig
    from wisp.provider_select import with_provider
    nv = with_provider(WispConfig(), "nvidia")
    p = _build(with_provider(nv, "openrouter"))
    assert p.api_base == OPENROUTER
    assert p.api_key != "nv-own-key"


def test_every_flag_path_goes_through_the_helper():
    """Guard against a sixth `config.replace(provider=...)` that skips the endpoint reset."""
    import pathlib, re
    root = pathlib.Path(__file__).resolve().parents[1] / "wisp"
    allowed = {"provider_select.py"}
    offenders = []
    for p in root.rglob("*.py"):
        if p.name in allowed:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\.replace\(\s*provider\s*=", line):
                offenders.append(f"{p.relative_to(root.parent)}:{n}")
    assert not offenders, f"use provider_select.with_provider: {offenders}"
