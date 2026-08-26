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
    """Models a provider can actually serve right now. [] = unknown.

    Live listings where the provider offers one; curated static lists
    only where it does not. A provider that cannot be reached yields []
    — callers treat that as "cannot verify", never as "model invalid".

    Results are cached for MODELS_CACHE_TTL_S keyed by provider + base +
    key fingerprint; pass force=True for an explicit refresh.
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
        models = _authed_get(f"{base}/api/tags", cfg)
        # Cloud models route through the same daemon listing already.
        return sorted(models)
    if name == "openrouter":
        return sorted(_authed_get("https://openrouter.ai/api/v1/models", cfg))
    if name == "openai":
        base = str(getattr(cfg, "api_base", "") or "https://api.openai.com/v1")
        return sorted(_authed_get(f"{base.rstrip('/')}/models", cfg))
    if name == "nvidia":
        live = _authed_get("https://integrate.api.nvidia.com/v1/models", cfg)
        if live:
            return sorted(live)
        # API unreachable or unauthenticated (no key) — fall back to the
        # provider's known catalog so unknown_model can still be detected
        # instead of returning "ok, could not be verified" and then 404ing
        # at chat time. This is the same list NVIDIAProvider advertises.
        try:
            from wisp.providers.nvidia import NVIDIAProvider

            return sorted(NVIDIAProvider._MODEL_CONTEXT.keys())
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


def _closest(target: str, candidates: list[str], limit: int = 5) -> list[str]:
    """Cheap prefix/substring/token ranking — good enough to suggest fixes."""
    t = target.lower()
    # Tokenize across the separators real ids use (org/name, family:tag,
    # dashed variants) so 'org/typo-model' still matches on 'org'+'model'.
    t_tokens = {tok for tok in t.replace("/", " ").replace(":", " ").replace("-", " ").split() if tok}

    def score(c: str) -> int:
        cl = c.lower()
        if cl == t:
            return 0
        if cl.startswith(t) or t.startswith(cl):
            return 1
        if t in cl or cl in t:
            return 2
        c_tokens = {tok for tok in cl.replace("/", " ").replace(":", " ").replace("-", " ").split() if tok}
        overlap = len(t_tokens & c_tokens)
        if overlap >= 2:
            return 3
        if overlap == 1:
            return 4
        return 9

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
        # For ollama, local models may not be in the daemon yet — lenient.
        # For cloud, an empty listing with a key present means the catalog
        # is hidden or the gateway is down; treat as unverifiable but don't
        # silently serve a bad id — surface as unreachable so the caller can
        # warn instead of 404ing mid-turn with 0 tools.
        if provider == "ollama":
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
    return Resolution(
        provider=provider, model=model, status="unknown_model",
        detail=f"Model '{model}' is not in {provider}'s current listing.",
        suggested=available[0],
        alternatives=_closest(model, available),
    )
