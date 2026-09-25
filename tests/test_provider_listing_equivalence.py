"""Provider model-listing delegation: the preconditions, made checkable.

Context (see PHASE_CANONICAL_AUTHORITY_MAP.md / D-3 and Round 2 of the
remediation report):

Model enumeration exists twice — `Provider.list_models()` (the declared
protocol) and `provider_catalog._list_models_impl()` (an independent
re-implementation). The dependency *inversion* is fixed (the catalog no longer
imports a concrete provider or reach into its private `_MODEL_CONTEXT`).

Full delegation of the catalog to the protocol is **not** shipped, because it
is not a pure refactor. Three concrete deltas were measured:

  1. **Auth headers differ.** The catalog sends only
     `Authorization: Bearer <cfg.api_key>`. `OpenRouterProvider._auth_headers()`
     also sends `HTTP-Referer` / `X-Title` (from `OPENROUTER_SITE_URL` /
     `OPENROUTER_APP_TITLE`); the Ollama client path sends no bearer at all.
     Delegating changes the bytes on the wire.
  2. **Timeouts differ.** The catalog uses a flat 5.0 s; OpenRouter uses 15 s
     and OpenAI uses `HARDENED_TIMEOUT` (falling back to 10 s). Delegating
     raises worst-case listing latency by 2–3x.
  3. **The `/api/models` degradation test would weaken.** The route has its own
     per-provider `try/except Exception: models = []`, so it still degrades —
     but `test_server_blocking_routes.py:74` patches the catalog's private
     `_authed_get`, which would stop intercepting. The test would keep passing
     while no longer *controlling* the failure.

These tests are **preconditions, not defects**. They assert the deltas still
exist so that the delegation is not attempted blind. When one starts failing,
the corresponding delta has converged and the delegation can proceed — that is
the signal, not a regression.
"""

from __future__ import annotations

import inspect
import pathlib

import pytest

import wisp.provider_catalog as pc
from wisp.config import WispConfig
from wisp.providers.openai import OpenAIProvider
from wisp.providers.openrouter import OpenRouterProvider

REPO = pathlib.Path(__file__).resolve().parent.parent


# ── Precondition 1: the auth paths differ ────────────────────────────

def test_catalog_sends_only_a_bearer_header():
    src = inspect.getsource(pc._authed_get)
    assert '"Authorization"' in src
    # The catalog knows nothing about provider-specific attribution headers.
    assert "HTTP-Referer" not in src
    assert "X-Title" not in src


def test_openrouter_provider_adds_attribution_headers(monkeypatch):
    """The delta that makes delegation a behaviour change, not a refactor."""
    monkeypatch.setenv("OPENROUTER_SITE_URL", "https://example.test")
    monkeypatch.setenv("OPENROUTER_APP_TITLE", "Wisp")
    provider = OpenRouterProvider(api_key="k", base_url="https://openrouter.ai/api/v1")
    headers = provider._auth_headers()
    assert "Authorization" in headers
    assert "HTTP-Referer" in headers, (
        "OpenRouter no longer adds attribution headers — the catalog's header "
        "set may now match the provider's; re-evaluate full delegation (D-3)"
    )
    assert "X-Title" in headers


def test_plain_openai_headers_do_match_the_catalog():
    """Documents that the delta is provider-specific, not universal."""
    provider = OpenAIProvider(api_key="k", base_url="https://api.openai.com/v1")
    assert provider._auth_headers() == {"Authorization": "Bearer k"}


# ── Precondition 2: the timeouts differ ──────────────────────────────

def test_catalog_timeout_is_five_seconds():
    sig = inspect.signature(pc._authed_get)
    assert sig.parameters["timeout"].default == 5.0


def test_openrouter_listing_timeout_exceeds_the_catalog_default():
    src = inspect.getsource(OpenRouterProvider.list_models)
    assert "timeout=15" in src, (
        "OpenRouter's listing timeout changed; re-check the latency delta "
        "before delegating (D-3)"
    )


def test_the_timeout_delta_is_real():
    """The numbers, asserted together so the gap is explicit."""
    catalog_default = inspect.signature(pc._authed_get).parameters["timeout"].default
    openrouter_timeout = 15
    assert openrouter_timeout > catalog_default


# ── Precondition 3: the degradation seam is the catalog's, not the route's

def test_the_route_has_its_own_degradation_guard():
    """Why delegating would weaken `test_server_blocking_routes` rather than
    break it: the route catches anything, so the test keeps passing while its
    monkeypatch stops controlling the failure."""
    src = (REPO / "wisp/server/routes/models.py").read_text(encoding="utf-8")
    assert "except Exception:" in src
    assert "models = []" in src


def test_the_existing_degradation_test_still_patches_the_catalog_seam():
    """If this seam moves, that test must be repointed — otherwise it silently
    stops exercising the failure path."""
    src = (REPO / "tests/test_server_blocking_routes.py").read_text(encoding="utf-8")
    assert "wisp.provider_catalog._authed_get" in src, (
        "the degradation test no longer patches the catalog seam; if the "
        "catalog now delegates, repoint it at the provider's list_models"
    )


# ── The one thing that IS required today ─────────────────────────────

@pytest.mark.parametrize("name", ["mock", "ollama", "openai", "openrouter", "nvidia"])
def test_catalog_output_contract_is_a_list_of_strings(name, monkeypatch, isolated_wisp_env):
    """Whatever the internals, the catalog's public contract is list[str]."""
    monkeypatch.setattr(pc, "_authed_get", lambda *a, **k: [])
    out = pc._list_models_impl(name, WispConfig())
    assert isinstance(out, list)
    assert all(isinstance(m, str) for m in out)


# ── The semantic contract itself (Target A) ──────────────────────────
# The authority is SEMANTIC, not transport. These tests pin the semantics so
# that the transport divergence stays a documented boundary rather than an
# unexamined duplicate.

def test_the_contract_is_declared_where_the_authority_lives():
    """An undeclared contract is an assumption. This one names its own
    boundary and the three measured deltas."""
    doc = pc.list_models.__doc__ or ""
    assert "CANONICAL MODEL-LISTING CONTRACT" in doc
    for delta in ("Auth", "Timeout", "Degradation"):
        assert delta in doc, f"the contract no longer documents the {delta} delta"


@pytest.mark.parametrize("name", ["mock", "ollama", "openai", "openrouter", "nvidia",
                                  "no-such-provider"])
def test_contract_yields_empty_when_the_fetch_fails(name, monkeypatch, isolated_wisp_env):
    """The realistic offline case: the transport helper absorbs the failure
    and returns []. This is what the route's degradation depends on.

    Note the contract is NOT "swallows every exception" — a programming error
    in a provider branch still propagates. What is guaranteed is that a
    *network/auth* failure yields [], because `_authed_get` never raises.
    """
    monkeypatch.setattr(pc, "_authed_get", lambda *a, **k: [])
    out = pc._list_models_impl(name, WispConfig())
    assert isinstance(out, list)
    # `mock` never fetches (hardcoded list) and `nvidia` has a static offline
    # fallback, so only the genuinely-live providers must yield [].
    if name not in ("mock", "nvidia"):
        assert out == [], f"{name} should yield [] when the fetch fails"


def test_the_transport_helper_is_the_failure_absorber():
    """The guarantee above rests on this one property of `_authed_get`."""
    import inspect
    src = inspect.getsource(pc._authed_get)
    assert "except Exception" in src, (
        "_authed_get no longer absorbs failures; the [] contract is broken"
    )
    assert "return []" in src


def test_empty_means_cannot_verify_not_no_models():
    """The contract's most load-bearing sentence: [] is 'unknown'.

    Callers must not read an empty listing as 'this model is invalid'. The
    docstring states it; this pins that the statement survives.
    """
    doc = pc.list_models.__doc__ or ""
    assert "cannot verify" in doc
    assert "never" in doc


def test_caching_is_part_of_the_contract(monkeypatch, isolated_wisp_env):
    """Results are cached; force=True bypasses. Pinned because callers rely
    on not re-hitting the network per keystroke in the REPL."""
    calls = {"n": 0}

    def _counting(*a, **k):
        calls["n"] += 1
        return ["m1"]

    monkeypatch.setattr(pc, "_authed_get", _counting)
    pc.clear_models_cache()
    first = pc.list_models("openai", WispConfig())
    second = pc.list_models("openai", WispConfig())
    assert first == second
    assert calls["n"] == 1, "the second call was not served from cache"
    pc.list_models("openai", WispConfig(), force=True)
    assert calls["n"] == 2, "force=True did not bypass the cache"


def test_provider_specific_transport_is_confined_to_the_catalog():
    """The distinct transport must live in ONE module.

    If a second module starts doing provider HTTP for listings, the boundary
    has leaked and the duplication is real again.
    """
    src = (REPO / "wisp/provider_catalog.py").read_text(encoding="utf-8")
    assert "_authed_get" in src, "the catalog's transport helper disappeared"
    # The catalog must not import a concrete provider (Phase 9 fixed this).
    for concrete in ("providers.ollama", "providers.openai",
                     "providers.nvidia", "providers.openrouter"):
        assert f"from wisp.{concrete} import" not in src, (
            f"the catalog imports {concrete} directly again"
        )
