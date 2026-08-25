"""Provider/model selection — the logic behind /provider and /model.

Pure-ish glue between the REPL command layer and the provider factory:
parse targets, build providers, health-probe before committing, apply the
switch to (runtime, session, config), and persist the choice. No printing
happens here; commands own all presentation.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# requires_key: refuse switching without an API key in the environment —
# a provider that constructs but can never answer is worse than a refusal.
KNOWN_PROVIDERS: "dict[str, dict[str, Any]]" = {
    "ollama": {
        "label": "Ollama (local)",
        "requires_key": False,
        "default_base": "http://localhost:11434",
    },
    "openai": {
        "label": "OpenAI-compatible (WISP_API_BASE)",
        "requires_key": True,
        "default_base": "https://api.openai.com/v1",
    },
    "nvidia": {
        "label": "NVIDIA NIM (integrate.api.nvidia.com)",
        "requires_key": True,
        "default_base": "https://integrate.api.nvidia.com/v1",
    },
    "openrouter": {
        "label": "OpenRouter (one key, many upstream models)",
        "requires_key": True,
        "default_base": "https://openrouter.ai/api/v1",
    },
    "mock": {
        "label": "Mock (offline testing)",
        "requires_key": False,
        "default_base": "",
    },
}


def parse_target(arg: str) -> dict[str, str | None]:
    """Parse '/provider <arg>' or '/model <arg>' input into components.

    Precedence for 'a/b': if 'a' names a known provider it is the provider
    and 'b' the model; otherwise the whole string is a model id (model ids
    legitimately contain slashes, e.g. org/name).
    """
    arg = (arg or "").strip()
    if not arg:
        return {"provider": None, "model": None}

    parts = arg.split()
    if len(parts) >= 2 and parts[0] in KNOWN_PROVIDERS:
        return {"provider": parts[0], "model": " ".join(parts[1:])}

    if "/" in arg:
        left, _, right = arg.partition("/")
        if left in KNOWN_PROVIDERS:
            return {"provider": left, "model": right or None}

    if arg in KNOWN_PROVIDERS:
        return {"provider": arg, "model": None}

    return {"provider": None, "model": arg}


def _config_for(provider_name: str, model: str | None,
                base: str | None = None,
                api_key: str | None = None) -> Any:
    from wisp.config import WispConfig

    cfg = WispConfig().replace(provider=provider_name)
    if model:
        cfg = cfg.replace(model=model)
    if base:
        cfg = cfg.replace(api_base=base)
    if api_key:
        cfg = cfg.replace(api_key=api_key)
    return cfg


def build_provider(provider_name: str, model: str | None = None,
                   base: str | None = None,
                   api_key: str | None = None) -> Any:
    """Construct a provider instance directly — raises on unknown names."""
    from wisp.providers import get_provider

    cfg = _config_for(provider_name, model, base=base, api_key=api_key)
    if model:
        cfg = cfg.replace(model=model)
    return get_provider(cfg)


def probe(provider: Any) -> tuple[bool, str]:
    """Best-effort health check; (ok, detail). Never raises."""
    check = getattr(provider, "check_health", None)
    if check is None:
        return True, ""
    try:
        if check():
            return True, ""
        return False, "health check returned unhealthy"
    except Exception as exc:  # network down, bad key shape, ...
        return False, str(exc)[:200]


def missing_key(provider_name: str) -> str | None:
    """Human message when a key-requiring provider has no key available."""
    if not KNOWN_PROVIDERS.get(provider_name, {}).get("requires_key"):
        return None
    if os.environ.get("WISP_API_KEY"):
        return None
    # Provider-specific fallbacks mirror what each provider class reads.
    if provider_name == "openai" and os.environ.get("OPENAI_API_KEY"):
        return None
    if provider_name == "openrouter" and (
            os.environ.get("OPENROUTER_API_KEY") or os.environ.get("WISP_API_KEY")):
        return None
    hints = {
        "openrouter": "Export OPENROUTER_API_KEY (or WISP_API_KEY) first.",
        "openai": "Export WISP_API_KEY (or OPENAI_API_KEY) first.",
    }
    hint = hints.get(provider_name,
                     f"Export WISP_API_KEY for '{provider_name}' first.")
    return f"No API key for '{provider_name}'. {hint}"


def apply_switch(runtime: Any, session: "dict[str, Any]", config: Any,
                 provider: str | None = None, model: str | None = None) -> Any:
    """Commit the switch: new config + session model + core-cache invalidation.

    Returns the replaced config. The next turn lazily rebuilds the core with
    the new provider via runtime.core_factory.
    """
    def _replace(cfg: Any, **kw: Any) -> Any:
        if hasattr(cfg, "replace"):
            return cfg.replace(**kw)
        import dataclasses
        import typing
        if dataclasses.is_dataclass(cfg) and not isinstance(cfg, type):
            replacer = typing.cast(Any, dataclasses.replace)
            return replacer(cfg, **kw)
        import copy
        clone = copy.copy(cfg)
        for k, v in kw.items():
            setattr(clone, k, v)
        return clone

    new_cfg = config
    if provider:
        new_cfg = _replace(new_cfg, provider=provider)
    if model:
        new_cfg = _replace(new_cfg, model=model)

    if model and hasattr(session, "__setitem__"):
        try:
            session["model"] = model
        except Exception:  # frozen/session-like objects
            logger.debug("Could not set session model", exc_info=True)

    # The runtime's own config is what core_factory reads when rebuilding
    # the cached core — updating only the adapter's copy would make the
    # next turn silently revert to the old provider/model.
    if provider or model:
        rt_cfg = getattr(runtime, "config", None)
        if rt_cfg is not None and hasattr(rt_cfg, "replace"):
            try:
                runtime.config = _replace(rt_cfg,
                                          **({"provider": provider} if provider else {}),
                                          **({"model": model} if model else {}))
            except Exception:
                logger.warning("Could not update runtime config", exc_info=True)

    invalidate = getattr(runtime, "invalidate_core_cache", None)
    if callable(invalidate):
        invalidate()
    else:
        logger.debug("Runtime lacks invalidate_core_cache; switch applies "
                     "to config only")
    return new_cfg


def persist(update: dict[str, str]) -> bool:
    """Merge keys into ~/.config/wisp/config.json. Best-effort."""
    try:
        from wisp.config import load_config, save_config
        merged = load_config()
        merged.update(update)
        save_config(merged)
        return True
    except Exception as exc:
        logger.warning("Could not persist provider choice: %s", exc)
        return False


def current_key_status(provider_name: str) -> str:
    """'✓'/'✗' marker for /provider listings."""
    return "✓" if not missing_key(provider_name) else "✗"
