"""Provider model-listing authority: one implementation, not two.

Context (see PHASE_CANONICAL_AUTHORITY_MAP.md, D-3):

Model enumeration exists twice:

  1. `Provider.list_models()` — the declared protocol method, implemented per
     provider (ollama, openai, openrouter; NVIDIA inherits OpenAI's).
  2. `provider_catalog._list_models_impl()` — an independent implementation
     that reimplements each provider's HTTP call.

Both are live, so they can disagree, and nothing detected it.

Full delegation of the catalog to the protocol is sequenced work (see the
report's Remaining Debt). It changes three things at once — the auth path, the
timeout, and the `/api/models` route's degradation contract — and the test
that pins that degradation (`test_server_blocking_routes.py:74`) patches the
catalog's private `_authed_get`, so it would silently become vacuous rather
than fail. Shipping that without an equivalence proof would be the "fix that
introduces a new violation" the brief warns about.

What these tests enforce today:

  - the catalog must not reach into a provider's PRIVATE surface;
  - the catalog's static fallback must equal the provider's own public list;
  - the catalog degrades to `[]` rather than raising, for every provider;
  - no module outside `wisp/providers/` may import a concrete provider
    (ratcheted, with the one known exception recorded).

Tests that construct providers request `isolated_wisp_env` so a machine-local
`~/.config/wisp/config.json` cannot change the outcome.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import wisp.provider_catalog as pc
from wisp.config import WispConfig
from wisp.provider_select import KNOWN_PROVIDERS, build_provider

REPO = pathlib.Path(__file__).resolve().parent.parent


# ── The catalog must not use a provider's private surface ────────────

def test_catalog_does_not_access_private_provider_attributes():
    """Regression pin: the NVIDIA fallback used `NVIDIAProvider._MODEL_CONTEXT`.

    Checked by AST attribute access, not by substring — the string legitimately
    appears in the comment explaining why the reach was removed.
    """
    tree = ast.parse((REPO / "wisp/provider_catalog.py").read_text(encoding="utf-8"))
    private_hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("_MODEL"):
            private_hits.append((node.attr, node.lineno))
    assert not private_hits, (
        "provider_catalog accesses a provider's private model table "
        f"{private_hits}; use the public `available_models` surface"
    )


def test_catalog_does_not_import_concrete_providers():
    tree = ast.parse((REPO / "wisp/provider_catalog.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if len(parts) >= 3 and parts[:2] == ["wisp", "providers"]:
                raise AssertionError(
                    f"provider_catalog imports {node.module}; it must obtain "
                    "providers from the factory instead"
                )


# ── The fallback must match the provider's own public list ───────────

def test_catalog_fallback_matches_the_providers_public_list(monkeypatch, isolated_wisp_env):
    monkeypatch.setattr(pc, "_authed_get", lambda *a, **k: [])
    got = pc._list_models_impl("nvidia", WispConfig())
    expected = sorted(build_provider("nvidia").available_models)
    assert got == expected, (
        "the catalog's offline fallback diverged from the provider's own list"
    )


def test_catalog_fallback_is_non_empty_for_nvidia(monkeypatch, isolated_wisp_env):
    """The fallback exists so `unknown_model` is still detectable offline."""
    monkeypatch.setattr(pc, "_authed_get", lambda *a, **k: [])
    assert pc._list_models_impl("nvidia", WispConfig())


# ── The catalog must degrade to [] rather than raise ─────────────────

@pytest.mark.parametrize("name", ["mock", "ollama", "openai", "openrouter", "nvidia"])
def test_catalog_degrades_to_empty_when_the_catalog_fetch_fails(
        name, monkeypatch, isolated_wisp_env):
    """`_authed_get`'s contract is 'never raises, [] on failure' — so an empty
    result is the realistic offline case, and the catalog must absorb it."""
    monkeypatch.setattr(pc, "_authed_get", lambda *a, **k: [])
    result = pc._list_models_impl(name, WispConfig())
    assert isinstance(result, list)
    assert all(isinstance(m, str) for m in result)


def test_authed_get_never_raises_on_an_unreachable_host():
    """Pin the documented contract the degradation above depends on."""
    out = pc._authed_get("http://127.0.0.1:9/definitely-not-listening", WispConfig())
    assert out == []


def test_catalog_unknown_provider_yields_empty(isolated_wisp_env):
    assert pc._list_models_impl("no-such-provider", WispConfig()) == []


# ── Every registered provider is constructible via the factory ───────

@pytest.mark.parametrize("name", sorted(KNOWN_PROVIDERS))
def test_every_registered_provider_exposes_the_listing_surface(name, isolated_wisp_env):
    """The protocol is the authority, so every registered provider must be
    constructible through the factory and expose `list_models()`."""
    provider = build_provider(name)
    assert hasattr(provider, "list_models"), (
        f"{name} is registered but exposes no list_models()"
    )


# ── Dependency direction: ratchet on concrete-provider imports ───────

# Known, tracked inversion not yet removed. Tighten to an empty set when
# cli/setup.py goes through the factory like every other consumer.
_KNOWN_CONCRETE_IMPORTERS = {"wisp/cli/setup.py"}

_CONCRETE = {"ollama", "openai", "nvidia", "openrouter", "mock"}


def test_no_new_concrete_provider_imports_outside_the_provider_layer():
    offenders = set()
    for py in (REPO / "wisp").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = str(py.relative_to(REPO))
        if rel.startswith("wisp/providers/"):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if (len(parts) == 3 and parts[:2] == ["wisp", "providers"]
                        and parts[2] in _CONCRETE):
                    offenders.add(rel)
    assert offenders <= _KNOWN_CONCRETE_IMPORTERS, (
        "a new concrete-provider import appeared outside wisp/providers/; "
        "consumers must obtain providers from the factory. "
        f"Found: {sorted(offenders)}"
    )
