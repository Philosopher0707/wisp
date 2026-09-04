"""Model/provider selection wired to the ANSI-safe fuzzy selector.

Handles:
  - Interactive TUI: `select_with_fuzzy` with Up/Down (wrap), Tab/Shift-Tab,
    Enter (commit), Esc (20 ms standalone cancel), type-to-filter.
  - Direct argument fallback: `/model 3`, `/model deepseek-r1`, provider/model
    prefix, `/provider openai` — all work without a tty (pipe/CI).

Reuses `wisp.provider_select` / `wisp.provider_catalog` for the single
source of truth; this file only owns the *selection UX*.
"""

from __future__ import annotations

import logging
from typing import Optional

from wisp.colors import accent, dim, error, info, success, warning

logger = logging.getLogger(__name__)


def _repl_is_tty() -> bool:
    import sys
    from wisp.terminal_width import is_accessible

    try:
        return (
            sys.stdin.isatty()
            and not is_accessible()
            and sys.platform != "win32"
            and hasattr(sys.stdin, "fileno")
        )
    except Exception:
        return False


def _pick_fuzzy(title: str, options: list[str], current: Optional[str] = None, descriptions: Optional[dict] = None) -> Optional[int]:
    """Fuzzy selector with numbered fallback. Returns original-list index or None."""
    # Prefer the new ANSI-safe selector; fall back to the legacy picker if
    # the new module is unavailable (e.g. partial checkout).
    try:
        from wisp.cli.ui.fuzzy_selector import select_with_fuzzy

        return select_with_fuzzy(title, options, current=current, descriptions=descriptions)
    except ImportError:
        from wisp.repl.picker import select_option

        return select_option(title, options, current=current, descriptions=descriptions)


def _list_models(provider: str, agent) -> list[str]:
    models: list[str] = []
    try:
        from wisp.provider_catalog import list_models as catalog_list_models

        models = catalog_list_models(provider, agent.config)
    except Exception as e:
        logger.warning("Failed to list models via catalog: %s", e)
    if not models and getattr(agent, "client", None):
        try:
            models = [m.get("name", "") for m in (agent.client.list_models() or []) if m.get("name")]
        except Exception as e2:
            logger.warning("Failed to list models via client: %s", e2)
    return models


def _print_model_listing(provider: str, agent, models: list[str]) -> None:
    cloud = dim("(cloud)") if provider == "ollama" else ""
    print(f"Provider: {accent(provider)}   Current model: {accent(agent.config.model)} {cloud}")
    print(info(f"\nAvailable models ({len(models)}):"))
    for i, name in enumerate(models, 1):
        display = name.removesuffix(":cloud")
        marker = accent("→") if name == agent.config.model else " "
        suffix = dim("(cloud)") if provider == "ollama" else ""
        print(f"  {marker} {i:2}. {display} {suffix}")


def _resolve_model_arg(arg: str, models: list[str]) -> Optional[str]:
    display_map = {m.removesuffix(":cloud"): m for m in models}
    if arg.isdigit():
        idx = int(arg) - 1
        if 0 <= idx < len(models):
            return models[idx]
        print(error(f"✗ Invalid model number: {arg}. Use /model to see the list."))
        return None
    if arg in models:
        return arg
    if arg in display_map:
        print(dim(f"  (resolved to {display_map[arg]})"))
        return display_map[arg]
    prefixes = [n for n in models if n.startswith(arg)]
    disp_prefixes = [n for n in models if n.removesuffix(":cloud").startswith(arg)]
    if len(prefixes) == 1:
        print(dim(f"  (resolved to {prefixes[0]})"))
        return prefixes[0]
    if len(prefixes) > 1:
        print(warning(f"⚠ Ambiguous prefix '{arg}'. Matches:"))
        for pfx in prefixes:
            print(f"    - {pfx}")
        return None
    if len(disp_prefixes) == 1:
        print(dim(f"  (resolved to {disp_prefixes[0]})"))
        return disp_prefixes[0]
    if len(disp_prefixes) > 1:
        print(warning(f"⚠ Ambiguous prefix '{arg}'. Matches:"))
        for pfx in disp_prefixes:
            print(f"    - {pfx}")
        return None
    print(warning(f"⚠ Model '{arg}' not found. Use /model to see the list."))
    return None


def _apply_model_switch(agent, provider: str, new_model: str, switch_provider: bool = False) -> None:
    from wisp.provider_select import apply_switch, missing_key, persist

    key_err = missing_key(provider)
    if key_err:
        print(error(f"✗ {key_err}"))
        return
    old_cfg = agent.config
    agent.config = apply_switch(
        getattr(agent, "runtime", None), agent.session, old_cfg,
        provider=provider if switch_provider else None, model=new_model,
    )
    client = getattr(agent, "client", None)
    if client is not None:
        client.model = new_model
        if provider == "ollama" and not getattr(agent.config, "_context_tokens_explicit", False):
            try:
                detected = client.get_context_length()
                if detected != agent.config.max_context_tokens:
                    agent.config = agent.config.replace(max_context_tokens=detected)
            except Exception:
                pass
    if getattr(agent, "_system_prompt_cache", None) is not None:
        agent._system_prompt_cache.clear()
    update = {"model": new_model}
    if switch_provider and provider:
        update["provider"] = provider
    persist(update)
    display = new_model.removesuffix(":cloud")
    cloud = dim("(cloud)") if provider == "ollama" else ""
    print(success(f"✓ Model set to: {display} {cloud}".rstrip()))


def cmd_model(agent, args: str):
    from wisp.provider_select import KNOWN_PROVIDERS, build_provider, missing_key, parse_target, probe

    provider = getattr(agent.config, "provider", None) or "ollama"
    target = parse_target(args)

    if target["provider"] is not None and args.strip():
        new_provider = target["provider"]
        if new_provider not in KNOWN_PROVIDERS:
            print(error(f"✗ Unknown provider: {new_provider}. Known: {', '.join(KNOWN_PROVIDERS)}"))
            return
        key_err = missing_key(new_provider)
        if key_err:
            print(error(f"✗ {key_err}"))
            return

    # No args: list then fuzzy picker on tty, numbered hint on pipe
    if not args.strip():
        models = _list_models(provider, agent)
        if not models:
            print(dim(f"  (Could not list models for '{provider}')"))
            print(dim(f"  Switch with /provider <name>. Known providers: {', '.join(KNOWN_PROVIDERS)}"))
            return
        _print_model_listing(provider, agent, models)
        if not _repl_is_tty():
            print(dim("\nType /model <number|name> to switch, /provider <name> to change provider."))
            return
        idx = _pick_fuzzy("Select a model", models, current=getattr(agent.config, "model", None))
        if idx is None:
            print(dim("  Cancelled — model unchanged."))
        else:
            _apply_model_switch(agent, provider, models[idx])
        return

    if target["provider"] is not None:
        new_provider = target["provider"]
        new_model = target["model"]
        if new_provider != provider:
            try:
                cand = build_provider(new_provider, model=new_model or agent.config.model)
                ok, detail = probe(cand)
            except Exception as exc:
                ok, detail = False, str(exc)[:160]
            if not ok:
                print(warning(f"⚠ Provider '{new_provider}' health check failed: {detail}"))
                print(dim("  Switching anyway — use /provider to come back."))

    _cmd_model_apply(agent, args, target, provider)


def _cmd_model_apply(agent, args: str, target: dict, provider: str) -> None:
    from wisp.provider_select import apply_switch, persist

    arg = (args or "").strip()
    if target["provider"] is not None:
        new_provider = target["provider"]
        if target["model"]:
            _apply_model_switch(agent, new_provider, target["model"], switch_provider=(new_provider != provider))
        else:
            agent.config = apply_switch(getattr(agent, "runtime", None), agent.session, agent.config, provider=new_provider, model="")
            if getattr(agent, "_system_prompt_cache", None) is not None:
                agent._system_prompt_cache.clear()
            persist({"provider": new_provider, "model": ""})
            print(success(f"✓ Provider set to: {new_provider} — pick a model with /model"))
        return

    if not arg:
        return

    models = _list_models(provider, agent)
    if not models:
        print(warning(f"⚠ Cannot list models for '{provider}' — pass an exact model id instead."))
        return
    new_model = _resolve_model_arg(arg, models)
    if new_model is not None:
        _apply_model_switch(agent, provider, new_model)


def cmd_provider(agent, args: str):
    from wisp.provider_select import KNOWN_PROVIDERS, apply_switch, persist

    current = getattr(agent.config, "provider", None) or "ollama"
    if not args.strip():
        target = _guided_provider_flow(agent, start_at=current)
        if target is None:
            _print_provider_listing(agent, current)
        return

    name = args.strip().split()[0].lstrip("@")
    if name not in KNOWN_PROVIDERS:
        print(error(f"✗ Unknown provider: {name}. Known: {', '.join(KNOWN_PROVIDERS)}"))
        return
    if name == current:
        print(dim(f"Already on {name}. Use /model to pick a model."))
        return
    if not _ensure_api_key(agent, name):
        return
    agent.config = apply_switch(getattr(agent, "runtime", None), agent.session, agent.config, provider=name, model="")
    if getattr(agent, "_system_prompt_cache", None) is not None:
        agent._system_prompt_cache.clear()
    persist({"provider": name, "model": ""})
    print(success(f"✓ Provider set to: {name} — pick a model with /model"))
    try:
        listing = _list_models(name, agent)
        if listing:
            print(info(f"Available models ({len(listing)}):"))
            for i, mname in enumerate(listing, 1):
                marker = accent("→") if mname == agent.config.model else " "
                print(f"  {marker} {i:2}. {mname}")
            print(dim("Pick with /model <number|name>."))
        else:
            print(dim(f"  (Could not list models for '{name}' — provider may be unreachable)"))
    except Exception as exc:
        logger.debug("Post-switch listing failed: %s", exc)


def _print_provider_listing(agent, current: str) -> None:
    from wisp.provider_select import KNOWN_PROVIDERS, current_key_status

    print(f"Current provider: {accent(current)}   Model: {accent(agent.config.model)}")
    print(info("\nAvailable providers:"))
    for name, meta in KNOWN_PROVIDERS.items():
        marker = accent("→") if name == current else " "
        key = current_key_status(name) if meta["requires_key"] else dim("-")
        key_req = dim("(needs WISP_API_KEY)") if meta["requires_key"] else ""
        print(f"  {marker} {name:<8} {meta['label']}  [{key}] {key_req}")
    print(dim("\nSwitch with /provider <name>; then /model <number|name>."))


def _ensure_api_key(agent, name: str) -> bool:
    from wisp.provider_select import missing_key

    key_err = missing_key(name)
    if not key_err:
        return True
    print(warning(f"⚠ {key_err}"))
    try:
        import getpass

        key_env = {
            "openai": "WISP_API_KEY (or OPENAI_API_KEY)",
            "openrouter": "WISP_API_KEY (or OPENROUTER_API_KEY)",
            "nvidia": "WISP_API_KEY (nvapi-… from https://build.nvidia.com)",
        }.get(name, "WISP_API_KEY")
        raw = getpass.getpass(f"Enter API key for '{name}' ({key_env}): ").strip()
        if not raw:
            print(error("✗ No key entered — provider not switched."))
            return False
        try:
            from wisp.provider_select import build_provider, probe

            cand = build_provider(name, api_key=raw)
            ok, detail = probe(cand)
            if ok:
                print(success(f"✓ API key verified for '{name}' — {detail or 'health check passed'}"))
            else:
                print(warning(f"⚠ Key entered but health check failed: {detail}"))
                print(dim("  Saving anyway — you can update via /provider or set WISP_API_KEY in .env"))
        except Exception as exc:
            print(warning(f"⚠ Could not verify key: {exc}"))
            print(dim("  Saving anyway — will verify on next turn."))
        from wisp.provider_select import store_key as _store_key

        _store_key(name, raw)
        return True
    except (KeyboardInterrupt, EOFError):
        print(dim("\n  Cancelled — provider not switched."))
        return False
    except Exception as exc:
        print(error(f"✗ Could not read API key: {exc}"))
        return False


def cmd_setup(agent, args: str):
    current = getattr(agent.config, "provider", None) or "ollama"
    result = _guided_provider_flow(agent, start_at=current)
    if result is None:
        _print_provider_listing(agent, current)


def _guided_provider_flow(agent, start_at: Optional[str] = None) -> Optional[str]:
    from wisp.provider_select import KNOWN_PROVIDERS, apply_switch, current_key_status, persist
    from wisp.provider_catalog import clear_models_cache

    providers = list(KNOWN_PROVIDERS.keys())
    descriptions = {}
    for name, meta in KNOWN_PROVIDERS.items():
        desc = meta["label"]
        if meta["requires_key"]:
            desc += f"  [{current_key_status(name)}]"
        descriptions[name] = desc

    print()
    idx = _pick_fuzzy("Select a provider", providers, current=start_at, descriptions=descriptions)
    if idx is None:
        return None
    name = providers[idx]
    print()
    if not _ensure_api_key(agent, name):
        return None
    clear_models_cache(name)
    models = _list_models(name, agent)
    if models:
        print()
        midx = _pick_fuzzy(f"Select a model for {name}", models)
        if midx is None:
            print(dim("  Cancelled — provider unchanged."))
            return None
        _apply_model_switch(agent, name, models[midx], switch_provider=True)
    else:
        print(warning(f"⚠ Could not list models for '{name}' — provider may be unreachable."))
        agent.config = apply_switch(getattr(agent, "runtime", None), agent.session, agent.config, provider=name, model="")
        if getattr(agent, "_system_prompt_cache", None) is not None:
            agent._system_prompt_cache.clear()
        persist({"provider": name, "model": ""})
        print(success(f"✓ Provider set to: {name}"))
    return name
