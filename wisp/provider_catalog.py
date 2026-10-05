"""Provider/model selection contract — the single source of selection truth.

Everything that lists providers, lists models for a provider, or resolves
"which (provider, model) should serve the next turn" goes through here.
No other module may hardcode a model id as a fallback default: stale
defaults are how agents come online pointing at models that do not exist
(live evidence: DEFAULT_MODEL and the factory both shipped ids that were
absent from the daemon, producing mid-turn 404s instead of a clear
selection error).

Resolution order (highest wins): env > persisted config > explicit
argument. An EMPTY model means "unset" — resolve_model() then picks the
first model the provider actually serves, so what comes online is always
a REAL model, never a rotted constant.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from wisp.net_security import assert_ollama_host_safe, validate_api_base

logger = logging.getLogger(__name__)

# ── Model-listing TTL cache ──────────────────────────────────────────
# /model listings hit the network on every call; a short TTL keeps the
# repeated REPL interaction snappy while staying fresh enough to catch
# newly pulled/deprecated models. Cache key includes provider + endpoint
# base + a fingerprint of the auth key so switching providers or keys
# never serves a stale cross-provider listing.
MODELS_CACHE_TTL_S = 300.0

_MODELS_CACHE: "dict[tuple[str, str, str], tuple[float, list[str]]]" = {}


def _cache_fingerprint(cfg: Any) -> str:
    from wisp.provider_select import resolve_key

    return hash(str(getattr(cfg, "api_key", "") or "") + resolve_key(str(getattr(cfg, "provider", "")))).__str__()[:12]


def clear_models_cache(provider_name: str | None = None) -> None:
    """Drop cached listings (all, or one provider). Never raises."""
    if provider_name is None:
        _MODELS_CACHE.clear()
        return
    name = (provider_name or "").strip().lower()
    for k in [k for k in _MODELS_CACHE if k[0] == name]:
        _MODELS_CACHE.pop(k, None)


def list_models(provider_name: str, cfg: Any = None,
                force: bool = False) -> list[str]:
    """CANONICAL MODEL-LISTING CONTRACT. Models a provider can serve now.

    This function is the semantic authority for "which models does provider X
    offer". Its contract is deliberately narrow and is pinned by
    `tests/test_provider_listing_equivalence.py`:

    INPUT
        ``provider_name`` — a key of ``provider_select.KNOWN_PROVIDERS``.
        ``cfg``           — a ``WispConfig`` (endpoint + credentials).
        ``force``         — bypass the cache.

    OUTPUT
        ``list[str]`` of model ids. **Empty list means "cannot verify"**, never
        "no models exist" — callers must not treat ``[]`` as invalidity.

    GUARANTEES
        - returns ``[]`` (never raises) for an unreachable or unauthenticated
          provider — the transport helper absorbs connection/auth errors. A
          programming error in a provider-specific branch still propagates;
          this is not a blanket exception swallow.
        - results are cached per (provider, endpoint, key fingerprint)
        - the offline fallback uses a provider's **public** surface only

    PROVIDER-SPECIFIC TRANSPORT — LEGITIMATELY DISTINCT
        The *transport* beneath this contract is intentionally not unified with
        ``Provider.list_models()``. Phase 9 measured three real deltas, so
        delegating would be a behaviour change, not a refactor:

        1. **Auth** — this path sends only ``Authorization: Bearer <key>``;
           ``OpenRouterProvider._auth_headers()`` also sends
           ``HTTP-Referer``/``X-Title``, and the Ollama client path sends no
           bearer at all.
        2. **Timeout** — this path uses a flat 5.0 s; OpenRouter uses 15 s and
           OpenAI uses ``HARDENED_TIMEOUT``.
        3. **Degradation** — ``/api/models`` has its own per-provider guard, so
           its test patches *this* module's seam.

        A single HTTP implementation would silently change all three. The
        authority that matters is semantic, and it is singular. When the three
        deltas converge, delegate — the equivalence tests fail at that point
        and signal that the work can be done.
    """
    name = (provider_name or "").strip().lower()
    base_part = ""
    if name == "ollama":
        base_part = _ollama_base(cfg)
    elif name == "openai":
        base_part = str(getattr(cfg, "api_base", "") or "https://api.openai.com/v1")
    cache_key = (name, base_part, _cache_fingerprint(cfg))
    if not force:
        cached = _MODELS_CACHE.get(cache_key)
        if cached and (time.monotonic() - cached[0]) < MODELS_CACHE_TTL_S:
            return cached[1]

    models = _list_models_impl(name, cfg)
    if models:
        _MODELS_CACHE[cache_key] = (time.monotonic(), models)
    return models


def _list_models_impl(name: str, cfg: Any) -> list[str]:
    """Live/static listing fetch — no caching here."""
    if name == "mock":
        return ["mock-model"]
    if name == "ollama":
        base = _ollama_base(cfg)
        try:
            assert_ollama_host_safe(base)
        except ValueError as exc:
            logger.warning("Ollama model listing blocked: %s", exc)
            return []
        models = _authed_get(f"{base}/api/tags", cfg)
        # Cloud models route through the same daemon listing already.
        return sorted(models)
    if name == "openrouter":
        return sorted(_authed_get("https://openrouter.ai/api/v1/models", cfg))
    if name == "openai":
        base = str(getattr(cfg, "api_base", "") or "https://api.openai.com/v1")
        try:
            base = validate_api_base(base, "openai")
        except ValueError as exc:
            logger.warning("OpenAI model listing blocked: %s", exc)
            return []
        return sorted(_authed_get(f"{base.rstrip('/')}/models", cfg))
    if name == "nvidia":
        live = _authed_get("https://integrate.api.nvidia.com/v1/models", cfg)
        if live:
            return sorted(live)
        # API unreachable or unauthenticated (no key) — fall back to the
        # provider's OWN public model list so unknown_model can still be
        # detected instead of returning "ok, could not be verified" and then
        # 404ing at chat time.
        #
        # Duck-typed on the public `available_models` property, reached through
        # the factory. Importing NVIDIAProvider directly (as this did) inverted
        # the dependency: a shared module naming a concrete implementation, and
        # reaching into its PRIVATE _MODEL_CONTEXT.
        try:
            from wisp.provider_select import build_provider

            provider = build_provider("nvidia")
            static = getattr(provider, "available_models", None) or []
            return sorted(str(m) for m in static)
        except Exception:
            return []
    return []

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderInfo:
    """One selectable provider — label plus how to reach it."""

    name: str
    label: str
    requires_key: bool
    default_base: str


def list_providers() -> list[ProviderInfo]:
    """All selectable providers, in stable display order."""
    from wisp.provider_select import KNOWN_PROVIDERS

    return [
        ProviderInfo(
            name=name,
            label=str(spec.get("label", name)),
            requires_key=bool(spec.get("requires_key", False)),
            default_base=str(spec.get("default_base", "")),
        )
        for name, spec in KNOWN_PROVIDERS.items()
    ]


def _ollama_base(cfg: Any) -> str:
    return str(getattr(cfg, "ollama_url", "") or "http://localhost:11434")


def _is_base_reachable(provider: str, cfg: Any, timeout: float = 2.0) -> bool:
    """True when the provider endpoint answers TCP at all (any HTTP status).

    Only consulted when the model listing came back empty, to separate a
    dead daemon (fail fast) from a live daemon with an unlisted model
    (serve leniently). Never raises; a missing requests lib counts as
    reachable so offline-but-valid setups are not punished twice.
    """
    if (provider or "").strip().lower() != "ollama":
        return True
    try:
        import requests
    except Exception:
        return True
    base = _ollama_base(cfg)
    try:
        assert_ollama_host_safe(base)
    except ValueError:
        return False
    try:
        requests.get(base, timeout=timeout)
        return True
    except Exception:
        return False


def _authed_get(url: str, cfg: Any, timeout: float = 5.0) -> list[str]:
    """GET a JSON model catalog; returns [] on any failure. Never raises."""
    try:
        import requests

        headers = {}
        key = getattr(cfg, "api_key", "") or ""
        if key:
            headers["Authorization"] = f"Bearer {key}"
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug("Model catalog fetch failed for %s: %s", url, exc)
        return []
    items = data.get("data") or data.get("models") or []
    out = []
    for m in items:
        if isinstance(m, dict):
            mid = m.get("id") or m.get("name") or ""
        else:
            mid = str(m)
        if mid:
            out.append(mid)
    return out


@dataclass(frozen=True)
class Resolution:
    """Outcome of resolving the effective (provider, model).

    status:
      ok            — provider known, model set and verified present
      model_unset   — no model anywhere; suggested carries first listed
      unknown_model — model set but NOT in the live listing; alternatives
                      carries closest real names (listing may itself be
                      empty when unreachable — then this is unverifiable,
                      and detail says so rather than lying)
      unreachable   — provider listing unavailable AND no model configured
    """

    provider: str
    model: str
    status: str
    detail: str = ""
    suggested: str = ""
    alternatives: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _rank(target: str, candidate: str) -> int:
    """How close ``candidate`` is to ``target``: 0 equal, 1 prefix, 2 substring, 3-4 shared tokens, 9 unrelated."""
    t = target.lower()
    cl = candidate.lower()
    if cl == t:
        return 0
    if cl.startswith(t) or t.startswith(cl):
        return 1
    if t in cl or cl in t:
        return 2
    # Tokenize across the separators real ids use (org/name, family:tag, dashed variants) so 'org/typo-model'
    # still matches on 'org'+'model'.
    t_tokens = {tok for tok in t.replace("/", " ").replace(":", " ").replace("-", " ").split() if tok}
    c_tokens = {tok for tok in cl.replace("/", " ").replace(":", " ").replace("-", " ").split() if tok}
    overlap = len(t_tokens & c_tokens)
    if overlap >= 2:
        return 3
    if overlap == 1:
        return 4
    return 9


#: A candidate this close (equal, prefix or substring) is a spelling of the same model. Anything looser (one shared
#: token such as "coder") is a different model that merely resembles it, and must never be swapped in silently.
_STRONG_RANK = 2


def _strong_match(target: str, candidates: list[str]) -> str:
    """The candidate that is a spelling of ``target``, or "" when there is none."""
    ranked = sorted(candidates, key=lambda c: _rank(target, c))
    return ranked[0] if ranked and _rank(target, ranked[0]) <= _STRONG_RANK else ""


def _closest(target: str, candidates: list[str], limit: int = 5) -> list[str]:
    """Cheap prefix/substring/token ranking — good enough to suggest fixes."""
    def score(c: str) -> int:
        return _rank(target, c)

    ranked = sorted(candidates, key=score)
    return [c for c in ranked if score(c) < 9][:limit]


def resolve_selection(cfg: Any) -> Resolution:
    """Resolve + validate the effective (provider, model) from a config.

    This is the seam every entrypoint (REPL start, server lifespan, core
    build) should call before serving turns, so a stale or absent model
    surfaces ONCE, clearly, instead of as a mid-turn provider 404.
    """
    from wisp.provider_select import KNOWN_PROVIDERS

    provider = str(getattr(cfg, "provider", "") or "").strip().lower() or "ollama"
    model = str(getattr(cfg, "model", "") or "").strip()
    if provider not in KNOWN_PROVIDERS:
        return Resolution(
            provider=provider, model=model, status="unknown_model",
            detail=f"Unknown provider '{provider}'.",
            alternatives=sorted(KNOWN_PROVIDERS.keys()),
        )

    # Cloud providers that require a key: surface a clear error at selection
    # time instead of a cryptic 401 mid-turn. The check is here (not in the
    # provider) so the composition layer can decide to auto-correct or warn.
    spec = KNOWN_PROVIDERS.get(provider, {})
    if spec.get("requires_key"):
        key = str(getattr(cfg, "api_key", "") or "").strip()
        if not key:
            # Single source for env fallbacks: provider_select.resolve_key
            from wisp.provider_select import resolve_key

            key = resolve_key(provider)
        if not key:
            # No key — treat as unreachable so the caller can warn or
            # fallback, rather than letting the chat call 401 and then
            # yield 0 tools with no explanation.
            return Resolution(
                provider=provider, model=model, status="unreachable",
                detail=f"Provider '{provider}' requires an API key (WISP_API_KEY / {provider.upper()}_API_KEY) but none is set.",
                alternatives=[],
            )

    if not model:
        available = list_models(provider, cfg)
        # Prefer a locally-runnable model when the provider separates
        # them (:cloud ids proxy to remote inference that may need a
        # subscription — auto-picking one just trades a clear state for
        # a 403 on every turn).
        local = [m for m in available if not m.endswith(":cloud")]
        pool = local or available
        pick = pool[0] if pool else ""
        if pick:
            return Resolution(
                provider=provider, model=pick, status="model_unset",
                detail="No model configured — first locally-served "
                       "model picked.",
                suggested=pick,
                alternatives=pool[:8],
            )
        return Resolution(
            provider=provider, model="", status="unreachable",
            detail=(f"Provider '{provider}' is not reachable and no model "
                    "is configured."),
        )

    available = list_models(provider, cfg)
    if not available:
        # Lenient for non-keyed local providers: their models may not be
        # listed yet. Strict (key-required) providers with an empty listing
        # and a key present mean the catalog is hidden or down — surface as
        # unreachable so the caller can warn instead of 404ing mid-turn.
        from wisp.provider_select import is_strict_provider

        if not is_strict_provider(provider):
            if model and not _is_base_reachable(provider, cfg):
                return Resolution(
                    provider=provider, model=model, status="unreachable",
                    detail=f"Provider '{provider}' is not reachable — connection "
                           f"refused. Start the daemon (or fix the endpoint) "
                           f"before serving '{model}'.",
                    alternatives=[],
                )
            return Resolution(
                provider=provider, model=model, status="ok",
                detail="Model could not be verified against a live listing "
                       "(provider unreachable or catalog hidden).",
            )
        return Resolution(
            provider=provider, model=model, status="unreachable",
            detail=f"Provider '{provider}' is not reachable — could not list models to verify '{model}'.",
            alternatives=[],
        )
    if model in available:
        return Resolution(provider=provider, model=model, status="ok")
    close = _closest(model, available)
    return Resolution(
        provider=provider, model=model, status="unknown_model",
        detail=f"Model '{model}' is not in {provider}'s current listing.",
        # Never `available[0]` when the listing HAS look-alikes: `qwen2.5-coder` became `aion-labs/aion-2.0` (the
        # first model alphabetically: a different family, a different price) while `qwen/qwen3-coder` sat in the
        # alternatives. A spelling of the same model is safe to swap in; with look-alikes but no spelling, the
        # caller serves the configured model and shows the close names. With no look-alike at all the configured
        # name is a stale one from another provider and cannot work here, so the first listed model keeps the
        # first turn from being a 404 (test_nvidia_unknown_autocorrects_in_composition).
        suggested=_strong_match(model, available) or ("" if close else available[0]),
        alternatives=close,
    )
