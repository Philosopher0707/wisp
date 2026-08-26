"""Models router.

The Web GUI's window into the provider/model selection contract:
GET lists every provider and the models each can actually serve right
now (plus what is active), POST applies a selection through the same
apply_switch seam the REPL /model command uses — so whichever surface
selects last is what serves the next turn. No hardcoded model ids and
no single-provider assumption survive here.
"""

import asyncio
import logging

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from wisp.server.deps import verify_api_key, RATE_LIMITER

logger = logging.getLogger(__name__)
router = APIRouter()


def _root(request: Request) -> Any:
    root = getattr(request.app.state, "root", None)
    if root is None:
        raise HTTPException(status_code=503, detail="Server not initialized")
    return root


@router.get("/api/models", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def list_models(request: Request) -> dict[str, Any]:
    """Provider-aware catalog: providers, their live models, active pick.

    The legacy shape {"models": [...]} is preserved under "models" for
    older clients — it now carries the ACTIVE provider's served models
    instead of unconditionally Ollama's.
    """
    from wisp.provider_catalog import list_models as catalog_models
    from wisp.provider_catalog import list_providers

    root = _root(request)
    cfg = getattr(getattr(root, "runtime", None), "config", None) or root.config
    active_provider = str(getattr(cfg, "provider", "") or "").strip().lower() or "ollama"
    active_model = str(getattr(cfg, "model", "") or "")

    providers_out: list[dict[str, Any]] = []
    for info in list_providers():
        try:
            models = await asyncio.to_thread(catalog_models, info.name, cfg)
        except Exception:
            models = []
        providers_out.append({
            "name": info.name,
            "label": info.label,
            "requires_key": info.requires_key,
            "models": models,
        })

    by_name = {p["name"]: p for p in providers_out}
    active_list = by_name.get(active_provider, {}).get("models", [])
    return {
        "active": {"provider": active_provider, "model": active_model},
        "providers": providers_out,
        # Backward-compatible key: active provider's live listing.
        "models": active_list,
    }


class SelectPayload(BaseModel):
    """Selection request; provider omitted = keep current provider."""

    provider: str | None = None
    model: str


@router.post("/api/models/select", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def select_model(payload: SelectPayload, request: Request) -> dict[str, Any]:
    """Apply a provider/model selection — the GUI twin of REPL /model.

    Goes through provider_select.apply_switch (runtime config + session +
    core-cache invalidation) and persists, so the latest selection is
    exactly what comes online on the next turn and after restart.
    """
    from wisp.provider_catalog import list_models as catalog_models
    from wisp.provider_catalog import resolve_selection
    from wisp.provider_select import (
        KNOWN_PROVIDERS,
        apply_switch,
        current_key_status,
    )

    root = _root(request)
    runtime = getattr(root, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime not available")

    cfg = getattr(runtime, "config", None) or root.config
    provider = (payload.provider or str(getattr(cfg, "provider", "") or "")
                ).strip().lower()
    if provider not in KNOWN_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider '{provider}'. "
                   f"Known: {', '.join(sorted(KNOWN_PROVIDERS))}",
        )
    if current_key_status(provider) != "✓":
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' needs an API key (WISP_API_KEY).",
        )

    model = payload.model.strip()
    if not model:
        raise HTTPException(status_code=400, detail="Model id required")

    # Verify against the LIVE listing before committing — selecting a
    # model the backend cannot serve must fail here, visibly, not at
    # turn time as a cryptic 404.
    probe_cfg = cfg.replace(provider=provider, model=model)
    available = await asyncio.to_thread(catalog_models, provider, probe_cfg)
    if available and model not in available:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model}' is not served by {provider}. "
                   f"Closest: {', '.join(available[:5])}",
        )

    session: dict[str, Any] = {"model": getattr(cfg, "model", "")}
    new_cfg = apply_switch(runtime, session, cfg,
                           provider=provider, model=model)
    from wisp.provider_select import persist
    persisted = persist({"provider": provider, "model": model})

    resolution = resolve_selection(new_cfg)
    return {
        "status": "ok",
        "selected": {"provider": provider, "model": model},
        "verified": resolution.status,
        "persisted": bool(persisted),
    }
