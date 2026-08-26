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


# ── Key vault: one resolver per provider ─────────────────────────────
# Every key lookup goes through resolve_key(); every key store through
# store_key(). Per-provider env vars win over the shared WISP_API_KEY so
# switching openai ↔ openrouter never clobbers the other key (the old
# single-slot behavior forced re-pasting keys on every switch).

KEY_ENV_VARS: "dict[str, list[str]]" = {
    "openai": ["OPENAI_API_KEY", "WISP_API_KEY"],
    "nvidia": ["NVIDIA_API_KEY", "WISP_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY", "WISP_API_KEY"],
}

_KEY_HINTS: "dict[str, str]" = {
    "openrouter": "Export OPENROUTER_API_KEY (or WISP_API_KEY) first.",
    "openai": "Export OPENAI_API_KEY (or WISP_API_KEY) first.",
    "nvidia": "Export NVIDIA_API_KEY (nvapi-… from build.nvidia.com, or WISP_API_KEY).",
}


def resolve_key(provider_name: str) -> str:
    """The API key for a provider — per-provider env var, else shared.

    Empty string = no key available anywhere. This is the ONE place that
    knows which env vars back which provider; callers must not re-derive it.
    """
    for env_var in KEY_ENV_VARS.get((provider_name or "").strip().lower(), ["WISP_API_KEY"]):
        val = os.environ.get(env_var, "").strip()
        if val:
            return val
    return ""


def store_key(provider_name: str, key: str) -> None:
    """Persist a key so this provider keeps its own slot permanently.

    Sets the per-provider env var AND the shared WISP_API_KEY in the
    process environment (so the very next turn works without restart),
    then persists both to config.json + .env files via persist().
    """
    name = (provider_name or "").strip().lower()
    primary = KEY_ENV_VARS.get(name, [None])[0]
    os.environ["WISP_API_KEY"] = key
    if primary and primary != "WISP_API_KEY":
        os.environ[primary] = key
    update: dict[str, str] = {"api_key": key}
    if primary:
        update[f"key_{name}"] = key  # per-provider slot in persisted config
    try:
        persist(update)
    except Exception:
        logger.debug("Could not persist key for %s", name, exc_info=True)
    # A new key can unlock a different live listing — don't serve a
    # listing fetched under the old (or empty) key.
    try:
        from wisp.provider_catalog import clear_models_cache

        clear_models_cache(name)
    except Exception:
        pass


def missing_key(provider_name: str) -> str | None:
    """Human message when a key-requiring provider has no key available."""
    if not KNOWN_PROVIDERS.get(provider_name, {}).get("requires_key"):
        return None
    if resolve_key(provider_name):
        return None
    hint = _KEY_HINTS.get(
        provider_name, f"Export WISP_API_KEY for '{provider_name}' first.")
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

    # `model=""` is a valid "unset" sentinel (means "pick first live"),
    # so check `is not None` instead of truthiness.
    new_cfg = config
    if provider is not None:
        new_cfg = _replace(new_cfg, provider=provider)
        # Reset api_base to the new provider's default — otherwise a switch
        # from openrouter (https://openrouter.ai/api/v1) to nvidia would
        # keep the old base and still hit openrouter's credit gate with
        # max_tokens 16384 → 402 "can only afford 5403".
        try:
            default_base = KNOWN_PROVIDERS.get(provider, {}).get("default_base", "")
            if default_base:
                new_cfg = _replace(new_cfg, api_base=default_base)
            else:
                new_cfg = _replace(new_cfg, api_base="")
        except Exception:
            pass
    if model is not None:
        new_cfg = _replace(new_cfg, model=model)

    if model is not None and hasattr(session, "__setitem__"):
        try:
            session["model"] = model
        except Exception:  # frozen/session-like objects
            logger.debug("Could not set session model", exc_info=True)

    # The runtime's own config is what core_factory reads when rebuilding
    # the cached core — updating only the adapter's copy would make the
    # next turn silently revert to the old provider/model.
    if provider is not None or model is not None:
        rt_cfg = getattr(runtime, "config", None)
        if rt_cfg is not None:
            try:
                kwargs: dict[str, Any] = {}
                if provider is not None:
                    kwargs["provider"] = provider
                    # Keep api_base in sync with provider — see above.
                    try:
                        default_base = KNOWN_PROVIDERS.get(provider, {}).get("default_base", "")
                        kwargs["api_base"] = default_base or ""
                    except Exception:
                        pass
                if model is not None:
                    kwargs["model"] = model
                runtime.config = _replace(rt_cfg, **kwargs)
            except Exception:
                logger.warning("Could not update runtime config", exc_info=True)

    invalidate = getattr(runtime, "invalidate_core_cache", None)
    if callable(invalidate):
        invalidate()
    else:
        logger.debug("Runtime lacks invalidate_core_cache; switch applies "
                     "to config only")
    # Cached model listings are keyed by provider+base+key fingerprint;
    # a switch may change all three, so drop stale entries eagerly.
    try:
        from wisp.provider_catalog import clear_models_cache

        if provider is not None:
            clear_models_cache(provider)
        elif model is not None:
            clear_models_cache()
    except Exception:
        pass
    return new_cfg


def persist(update: dict[str, str]) -> bool:
    """Merge keys into ~/.config/wisp/config.json and .env. Best-effort."""
    ok = True
    try:
        from wisp.config import load_config, save_config
        merged = load_config()
        merged.update(update)
        save_config(merged)
    except Exception as exc:
        logger.warning("Could not persist provider choice: %s", exc)
        ok = False
    # Also persist to .env in the workspace and home so `WISP_API_KEY`
    # survives restarts without re-entering via /provider. The .env file
    # is gitignored via .gitignore already.
    try:
        _persist_env(update)
    except Exception as exc:
        logger.debug("Could not persist .env: %s", exc)
    return ok


def _persist_env(update: dict[str, str]) -> None:
    """Write provider/model/api_key/api_base to .env files.

    Writes to both the workspace's .env and ~/.config/wisp/.env for
    durability. Only writes keys that are in `update` and non-empty.
    """
    import pathlib

    # Map config keys to env vars
    env_map = {
        "provider": "WISP_PROVIDER",
        "model": "WISP_MODEL",
        "api_key": "WISP_API_KEY",
        "api_base": "WISP_API_BASE",
        "ollama_url": "WISP_OLLAMA_URL",
        # Per-provider key slots (written by store_key) map to the
        # provider-specific env vars the providers themselves read.
        "key_openai": "OPENAI_API_KEY",
        "key_nvidia": "NVIDIA_API_KEY",
        "key_openrouter": "OPENROUTER_API_KEY",
    }
    # Filter to only env-mapped keys
    env_update: dict[str, str] = {}
    for k, v in update.items():
        ev = env_map.get(k)
        if ev and isinstance(v, str) and v:
            env_update[ev] = v
        elif ev and k == "api_key" and v:
            # api_key may be empty string to clear; still write if explicitly in update
            env_update[ev] = v
    if not env_update:
        return
    # Workspace .env (project-local, so `taki baar baar change na karna pade`)
    # Prefer the explicit workspace from the update (when /provider was called
    # from a REPL with a non-default workspace), then WISP_WORKSPACE env,
    # then the persisted config's workspace, then cwd.
    try:
        ws = update.get("workspace") or os.environ.get("WISP_WORKSPACE") or ""
        if not ws:
            try:
                from wisp.config import get_setting

                ws = get_setting("workspace", "") or ""
            except Exception:
                ws = ""
        if not ws:
            ws = os.getcwd()
        ws_env = pathlib.Path(ws).resolve() / ".env"
        _upsert_env_file(ws_env, env_update)
    except Exception:
        pass
    # Global fallback
    try:
        global_env = pathlib.Path.home() / ".config" / "wisp" / ".env"
        _upsert_env_file(global_env, env_update)
    except Exception:
        pass


def _upsert_env_file(path: "pathlib.Path", update: dict[str, str]) -> None:
    """Create or update a .env file with KEY=VALUE lines."""
    import pathlib

    path = pathlib.Path(path)
    existing: dict[str, str] = {}
    lines: list[str] = []
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    lines.append(line)
                    continue
                k, v = stripped.split("=", 1)
                existing[k.strip()] = v.strip()
                lines.append(line)
        except Exception:
            lines = []
    # Upsert
    for k, v in update.items():
        if k in existing:
            # Replace existing line
            for i, line in enumerate(lines):
                if line.strip().startswith(f"{k}="):
                    lines[i] = f"{k}={v}"
                    break
        else:
            lines.append(f"{k}={v}")
    # Ensure parent exists
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def current_key_status(provider_name: str) -> str:
    """'✓'/'✗' marker for /provider listings."""
    return "✓" if not missing_key(provider_name) else "✗"
