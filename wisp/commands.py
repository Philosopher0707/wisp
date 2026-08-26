"""Slash commands for Wisp REPL — local directives that bypass the LLM.

Commands are registered via the @register decorator and dispatched by name.
They receive the WispAgent instance and can mutate its state directly.
"""

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from wisp.colors import success, error, warning, info, dim, accent
from wisp.core.session_view import SessionView
from wisp.exceptions import ExitREPL

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    handler: Callable
    aliases: tuple[str, ...] = ()
    usage: str = ""


# Global registry: name/alias -> Command instance
_REGISTRY: dict[str, Command] = {}


def register(name: str, description: str, aliases: tuple[str, ...] = (), usage: str = ""):
    """Decorator to register a slash command.

    Raises ValueError on alias theft: a name/alias already owned by a
    different command fails at import time instead of silently rebinding.
    """
    def decorator(fn: Callable):
        cmd = Command(name, description, fn, aliases, usage)
        for key in (name, *aliases):
            existing = _REGISTRY.get(key)
            if existing is not None and existing.name != name:
                raise ValueError(
                    f"Command '{name}' cannot claim /{key}: already owned "
                    f"by '{existing.name}'. Aliases must be unique."
                )
            _REGISTRY[key] = cmd
        return fn
    return decorator


def lookup(name: str) -> Optional[Command]:
    """Find a command by exact name or alias."""
    return _REGISTRY.get(name)


def all_commands() -> list[Command]:
    """Return unique commands sorted by name."""
    seen: set[str] = set()
    result: list[Command] = []
    for cmd in sorted(_REGISTRY.values(), key=lambda c: c.name):
        if cmd.name not in seen:
            seen.add(cmd.name)
            result.append(cmd)
    return result


def dispatch(text: str, agent) -> str | None | bool:
    """Parse text as a slash command and execute it.

    Returns:
        True  — input was consumed (no follow-up turn needed)
        False — input was not a slash command
        str   — prompt to run as a follow-up turn (e.g. /continue)
    """
    text = text.strip()
    if not text.startswith("/"):
        return False

    body = text[1:].strip()
    if not body:
        # Bare "/" typed — show help menu
        cmd_help(agent, "")
        return True

    parts = body.split(maxsplit=1)
    name = parts[0]
    args = parts[1] if len(parts) > 1 else ""

    cmd = lookup(name)
    if not cmd:
        print(error(f"Unknown command: /{name}. Type /help for available commands."))
        return True

    try:
        result = cmd.handler(agent, args.strip())
        # If handler returns a string, it's a prompt to run as a follow-up turn
        if isinstance(result, str) and result:
            return result
    except ExitREPL:
        raise
    except Exception as e:
        logger.exception("Command /%s failed", name)
        print(error(f"✗ Command failed: {e}"))
    return True


# ── Command implementations ──────────────────────────────────────────


@register("help", "Show available slash commands", aliases=("h", "?"), usage="/help")
def cmd_help(agent, args: str):
    print(info("Available commands:"))
    for cmd in all_commands():
        alias_str = f" (aliases: {', '.join(cmd.aliases)})" if cmd.aliases else ""
        print(f"  {accent('/' + cmd.name):<14}  {cmd.description}{dim(alias_str)}")
    print()
    print(dim("Commands run locally and do not send anything to the LLM."))


@register("clear", "Clear conversation history", aliases=("cls",), usage="/clear")
def cmd_clear(agent, args: str):
    count = len(agent.messages)
    agent.messages.clear()
    print(success(f"✓ Cleared {count} messages."))


@register("model", "List/switch models for the active provider",
          aliases=("m", "models"), usage="/model [<provider> <model>|<provider>/<model>|<num>|<name>]")
def cmd_model(agent, args: str):
    from wisp.provider_select import (
        KNOWN_PROVIDERS, apply_switch, build_provider, missing_key,
        parse_target, persist, probe,
    )

    provider = getattr(agent.config, "provider", None) or "ollama"
    target = parse_target(args)

    # ── Explicit provider component ────────────────────────────────
    if target["provider"] is not None and args.strip():
        new_provider = target["provider"]
        if new_provider not in KNOWN_PROVIDERS:
            print(error(f"✗ Unknown provider: {new_provider}. "
                        f"Known: {', '.join(KNOWN_PROVIDERS)}"))
            return
        key_err = missing_key(new_provider)
        if key_err:
            print(error(f"✗ {key_err}"))
            return

    # ── No args: show current + this provider's models ─────────────
    # Single source: provider_catalog.list_models (live + nvidia static fallback)
    if not args.strip():
        models = []
        try:
            from wisp.provider_catalog import list_models as catalog_list_models

            models = catalog_list_models(provider, agent.config)
        except Exception as e:
            logger.warning("Failed to list models via catalog: %s", e)
            if getattr(agent, "client", None):
                try:
                    models = [m.get("name", "") for m in (agent.client.list_models() or [])
                              if m.get("name")]
                except Exception as e2:
                    logger.warning("Failed to list models via client: %s", e2)

        cloud = dim("(cloud)") if provider == "ollama" else ""
        print(f"Provider: {accent(provider)}   Current model: "
              f"{accent(agent.config.model)} {cloud}")
        if not models:
            print(dim(f"  (Could not list models for '{provider}')"))
            others = ", ".join(KNOWN_PROVIDERS)
            print(dim(f"  Switch with /provider <name>. Known providers: {others}"))
            return
        print(info(f"\nAvailable models ({len(models)}):"))
        for i, name in enumerate(models, 1):
            display = name.removesuffix(":cloud")
            marker = accent("→") if name == agent.config.model else " "
            suffix = dim("(cloud)") if provider == "ollama" else ""
            print(f"  {marker} {i:2}. {display} {suffix}")
        print(dim("\nType /model <number|name> to switch, /provider <name> "
                  "to change provider."))
        return

    # ── Provider switch with optional model ─────────────────────────
    if target["provider"] is not None:
        new_provider = target["provider"]
        new_model = target["model"]
        if new_provider != provider:
            probe_ok, detail = True, ""
            try:
                cand = build_provider(new_provider, model=new_model
                                      or agent.config.model)
                probe_ok, detail = probe(cand)
            except Exception as exc:
                probe_ok, detail = False, str(exc)[:160]
            if not probe_ok:
                print(warning(f"⚠ Provider '{new_provider}' health check "
                              f"failed: {detail}"))
                print(dim("  Switching anyway — use /provider to come back."))
        else:
            new_model = new_model  # same provider, maybe model only

        if new_model:
            _apply_model_switch(agent, new_provider, new_model,
                                persist_choice=True, switch_provider=True)
            return
        # Provider only: unset model so next turn picks first live for new provider
        agent.config = apply_switch(
            getattr(agent, "runtime", None), agent.session,
            agent.config, provider=new_provider, model="")
        if getattr(agent, "_system_prompt_cache", None) is not None:
            agent._system_prompt_cache.clear()
        persist({"provider": new_provider, "model": ""})
        print(success(f"✓ Provider set to: {new_provider} — pick a model with /model"))
        try:
            from wisp.provider_catalog import list_models as catalog_list_models

            listing = catalog_list_models(new_provider, agent.config)
            if not listing and getattr(agent, "client", None):
                # Fallback to client only if catalog is empty (should not happen for nvidia due to static fallback)
                try:
                    listing = [m.get("name", "") for m in (agent.client.list_models() or [])
                               if m.get("name")]
                except Exception:
                    pass
            if listing:
                print(info(f"Available models ({len(listing)}):"))
                for i, name in enumerate(listing, 1):
                    print(f"   {i:2}. {name}")
                print(dim("Pick with /model <number|name>."))
        except Exception as exc:
            logger.debug("Model listing for %s failed: %s", new_provider, exc)
        return

    # ── Model-only switch within the active provider ────────────────
    # Strict for cloud: must be in the live catalog; lenient for ollama.
    arg = target["model"]
    models = []
    try:
        from wisp.provider_catalog import list_models as catalog_list_models
        from wisp.provider_catalog import resolve_selection as catalog_resolve

        models = catalog_list_models(provider, agent.config)
        # For cloud, enforce strict unknown_model check before any apply
        if provider in ("nvidia", "openai", "openrouter") and arg and not arg.isdigit():
            # Re-use catalog's closest logic for the error message
            if models and arg not in models and arg.removesuffix(":cloud") not in {m.removesuffix(":cloud") for m in models}:
                # Check if it's an ambiguous prefix or truly unknown
                prefixes = [m for m in models if m.startswith(arg)] + [m for m in models if m.removesuffix(":cloud").startswith(arg)]
                if not prefixes:
                    # Use catalog's resolve to get suggested/alternatives
                    tmp_cfg = agent.config.replace(model=arg) if hasattr(agent.config, "replace") else agent.config
                    # Temporarily set model to arg for resolution (don't mutate real config)
                    import copy
                    tmp = copy.copy(agent.config)
                    try:
                        object.__setattr__(tmp, "model", arg)
                    except Exception:
                        tmp.model = arg
                    res = catalog_resolve(tmp)
                    if res.status == "unknown_model":
                        print(error(f"✗ Unknown model '{arg}' for '{provider}'. Did you mean: {', '.join(res.alternatives[:3]) or res.suggested or '(none)'}"))
                        print(dim(f"  Available ({len(models)}): {', '.join(models[:8])}{' …' if len(models) > 8 else ''}"))
                        return
    except Exception as e:
        logger.debug("Catalog pre-check failed: %s", e)
    if not models and getattr(agent, "client", None):
        try:
            models = [m.get("name", "") for m in (agent.client.list_models() or [])
                      if m.get("name")]
        except Exception as e:
            logger.warning("Failed to list models: %s", e)

    display_map = {n.removesuffix(":cloud"): n for n in models}
    new_model: str | None = None

    if arg.isdigit():
        idx = int(arg) - 1
        if 0 <= idx < len(models):
            new_model = models[idx]
        else:
            print(error(f"✗ Invalid model number: {arg}. Use /model to see the list."))
            return
    else:
        if arg in models:
            new_model = arg
        elif arg in display_map:
            new_model = display_map[arg]
            print(dim(f"  (resolved to {new_model})"))
        else:
            prefixes = [n for n in models if n.startswith(arg)]
            disp_prefixes = [n for n in models
                             if n.removesuffix(":cloud").startswith(arg)]
            if len(prefixes) == 1:
                new_model = prefixes[0]
                print(dim(f"  (resolved to {new_model})"))
            elif len(prefixes) > 1:
                print(warning(f"⚠ Ambiguous prefix '{arg}'. Matches:"))
                for pfx in prefixes:
                    print(f"    - {pfx}")
                return
            elif len(disp_prefixes) == 1:
                new_model = disp_prefixes[0]
                print(dim(f"  (resolved to {new_model})"))
            elif len(disp_prefixes) > 1:
                print(warning(f"⚠ Ambiguous prefix '{arg}'. Matches:"))
                for pfx in disp_prefixes:
                    print(f"    - {pfx}")
                return
            else:
                print(warning(f"⚠ Model '{arg}' not found for provider "
                              f"'{provider}'. Use /model to see the list."))
                return

    _apply_model_switch(agent, provider, new_model, persist_choice=True)


def _apply_model_switch(agent, provider: str, new_model: str,
                        persist_choice: bool = True,
                        switch_provider: bool = False) -> None:
    """Commit a model switch within `provider`; shared by all paths."""
    from wisp.provider_select import apply_switch, missing_key, persist

    key_err = missing_key(provider)
    if key_err:
        print(error(f"✗ {key_err}"))
        return

    old_cfg = agent.config
    agent.config = apply_switch(
        getattr(agent, "runtime", None), agent.session,
        old_cfg,
        provider=provider if switch_provider else None,
        model=new_model)

    client = getattr(agent, "client", None)
    if client is not None:
        client.model = new_model
        if provider == "ollama" and not getattr(
                agent.config, "_context_tokens_explicit", False):
            try:
                detected = client.get_context_length()
                if detected != agent.config.max_context_tokens:
                    logger.info(
                        "Auto-detected context window for %s: %d tokens",
                        new_model, detected,
                    )
                    agent.config = agent.config.replace(
                        max_context_tokens=detected)
            except Exception:
                pass
    if getattr(agent, "_system_prompt_cache", None) is not None:
        agent._system_prompt_cache.clear()

    if persist_choice:
        update = {"model": new_model}
        if switch_provider and provider:
            update["provider"] = provider
        persist(update)

    display = new_model.removesuffix(":cloud")
    cloud = dim("(cloud)") if provider == "ollama" else ""
    print(success(f"✓ Model set to: {display} {cloud}".rstrip()))


@register("provider", "Switch LLM provider (openai/nvidia/ollama/mock)",
          aliases=("prov",), usage="/provider [name]")
def cmd_provider(agent, args: str):
    from wisp.provider_select import (
        KNOWN_PROVIDERS, apply_switch, build_provider, current_key_status,
        missing_key, persist, probe,
    )

    current = getattr(agent.config, "provider", None) or "ollama"

    if not args.strip():
        print(f"Current provider: {accent(current)}   "
              f"Model: {accent(agent.config.model)}")
        print(info("\nAvailable providers:"))
        for name, meta in KNOWN_PROVIDERS.items():
            marker = accent("→") if name == current else " "
            key = current_key_status(name) if meta["requires_key"] else dim("-")
            key_req = dim("(needs WISP_API_KEY)") if meta["requires_key"] else ""
            print(f"  {marker} {name:<8} {meta['label']}  [{key}] {key_req}")
        print(dim("\nSwitch with /provider <name>; then /model <number|name>."))
        return

    name = args.strip().split()[0].lstrip("@")
    if name not in KNOWN_PROVIDERS:
        print(error(f"✗ Unknown provider: {name}. "
                    f"Known: {', '.join(KNOWN_PROVIDERS)}"))
        return
    if name == current:
        print(dim(f"Already on {name}. Use /model to pick a model."))
        return
    key_err = missing_key(name)
    if key_err:
        print(warning(f"⚠ {key_err}"))
        # Prompt for the key securely, verify it, and persist to .env + config
        # so the user doesn't have to re-enter it via REPL every time.
        try:
            import getpass

            # Ask for the key appropriate to the provider
            key_env = {
                "openai": "WISP_API_KEY (or OPENAI_API_KEY)",
                "openrouter": "WISP_API_KEY (or OPENROUTER_API_KEY)",
                "nvidia": "WISP_API_KEY (nvapi-… from https://build.nvidia.com)",
            }.get(name, "WISP_API_KEY")
            raw = getpass.getpass(f"Enter API key for '{name}' ({key_env}): ").strip()
            if not raw:
                print(error("✗ No key entered — provider not switched."))
                return
            # Verify the key by building a provider and probing
            try:
                from wisp.provider_select import build_provider, probe

                cand = build_provider(name, api_key=raw)
                ok, detail = probe(cand)
                # For nvidia/openrouter, also try a live model listing with the new key
                if ok:
                    print(success(f"✓ API key verified for '{name}' — {detail or 'health check passed'}"))
                else:
                    print(warning(f"⚠ Key entered but health check failed: {detail}"))
                    print(dim("  Saving anyway — you can update via /provider or set WISP_API_KEY in .env"))
            except Exception as exc:
                print(warning(f"⚠ Could not verify key: {exc}"))
                print(dim("  Saving anyway — will verify on next turn."))
            # Persist to config and .env (so `taki baar baar change na karna pade`)
            from wisp.provider_select import persist as _persist

            # Also set it in the process env so the next turn in this REPL uses it without restart
            import os

            os.environ["WISP_API_KEY"] = raw
            if name == "openai":
                os.environ["OPENAI_API_KEY"] = raw
            elif name == "openrouter":
                os.environ["OPENROUTER_API_KEY"] = raw
            elif name == "nvidia":
                os.environ["NVIDIA_API_KEY"] = raw
            _persist({"api_key": raw})
            # Continue to provider switch — now missing_key will pass
        except (KeyboardInterrupt, EOFError):
            print(dim("\n  Cancelled — provider not switched."))
            return
        except Exception as exc:
            print(error(f"✗ Could not read API key: {exc}"))
            return

    # Provider switch without an explicit model → unset the model so
    # the next turn's resolve_selection picks the first live model for the
    # new provider (local-first). Passing model="" is the "unset" sentinel,
    # not None (which would mean "keep old model" and then 404 on nvidia
    # with qwen2.5-coder).
    agent.config = apply_switch(
        getattr(agent, "runtime", None), agent.session,
        agent.config, provider=name, model="")
    if getattr(agent, "_system_prompt_cache", None) is not None:
        agent._system_prompt_cache.clear()
    persist({"provider": name, "model": ""})
    print(success(f"✓ Provider set to: {name} — pick a model with /model"))

    # Best-effort: show what this provider can serve right now (single source).
    try:
        from wisp.provider_catalog import list_models as catalog_list_models
        from wisp.provider_select import build_provider, probe

        cand = build_provider(name, model=agent.config.model)
        ok, detail = probe(cand)
        if not ok:
            print(warning(f"⚠ Health check failed: {detail}"))
        listing = catalog_list_models(name, agent.config)
        if not listing:
            # Fallback to client only if catalog is empty (should not happen for nvidia due to static fallback)
            try:
                listing = [m.get("name", "") for m in (cand.list_models() or []) if m.get("name")]
            except Exception:
                pass
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


@register("skill", "Load or list skills (suggest/save capture workflows)",
          aliases=("s",), usage="/skill [name | suggest | save <name>]")
def cmd_skill(agent, args: str):
    from wisp.skills import discover_skills, find_skill

    ws = agent.config.workspace or "."

    # ── Capture subcommands ─────────────────────────────────────────
    parts = args.split(maxsplit=2)
    sub = parts[0] if parts else ""
    if sub == "suggest":
        from wisp.skill_capture import get_capture
        capture = get_capture()
        suggestion = capture.suggest()
        if suggestion is None:
            print(dim("No repeated workflow detected yet. Run a procedure "
                      "twice, then check again."))
            return
        print(accent(f"Repeated workflow detected ({suggestion.occurrences}x):"))
        for i, step in enumerate(suggestion.steps, 1):
            print(f"  {i}. {step.describe()}")
        print(dim("Save it: /skill save <name>"))
        return

    if sub == "save":
        if len(parts) < 2:
            print(info("Usage: /skill save <name> [description]"))
            return
        from wisp.skill_capture import get_capture
        capture = get_capture()
        name = parts[1].strip()
        description = parts[2].strip() if len(parts) > 2 else f"Captured {name} workflow"
        try:
            path, merged = capture.render_skill(name, description, ws)
        except ValueError as e:
            print(error(str(e)))
            return
        except OSError as e:
            print(error(f"Could not write skill file: {e}"))
            return
        if merged:
            print(success(f"✓ Skill merged: {path}"))
        else:
            print(success(f"✓ Skill saved: {path}"))
        print(dim(f"Load it with /skill {path.parent.name}"))
        return

    if not args or not args.strip():
        skills = discover_skills(ws)
        if not skills:
            print(dim("No skills found."))
            return
        active = getattr(agent, "_active_skill", None)
        for sk in skills:
            marker = accent(" → ") if active == sk.name else "   "
            print(f"{marker}{accent(sk.name)}: {sk.description}")
        return

    name = args.strip()
    skill = find_skill(name, ws)
    if skill is None:
        print(warning(f"⚠ Skill '{name}' not found."))
        return

    agent._active_skill = name
    if hasattr(agent, "_system_prompt_cache"):
        agent._system_prompt_cache.clear()

    print(success(f"✓ Skill loaded: {skill.name}"))


@register("session", "Show session info", usage="/session")
def cmd_session(agent, args: str):
    view = SessionView.coerce(agent.session)
    if view is None:
        print(dim("No active session."))
        return
    active_skill = getattr(agent, "_active_skill", None)
    print(info("Session info:"))
    print(f"  {dim('Session ID:')}    {view.id or '(none)'}")
    print(f"  {dim('Title:')}         {view.display_title()}")
    print(f"  {dim('Model:')}         {agent.config.model}")
    print(f"  {dim('Workspace:')}     {agent.config.workspace or '.'}")
    print(f"  {dim('Active skill:')}  {active_skill or '(none)'}")
    print(f"  {dim('Messages:')}      {len(view.messages)}")
    print(f"  {dim('Auto-approve:')}  {agent.config.auto_approve}")
    print(f"  {dim('Show thinking:')} {agent.config.show_thinking}")


@register("save", "Force-save the current session", usage="/save")
def cmd_save(agent, args: str):
    agent._save_session()
    view = SessionView.coerce(agent.session)
    if view is not None:
        print(success(f"✓ Session saved: {view.id or '(unknown)'}"))
    else:
        print(dim("✓ Nothing to save (no session)."))


@register("tokens", "Show estimated token usage", aliases=("context",), usage="/tokens")
def cmd_tokens(agent, args: str):
    system = agent._build_system_prompt()
    overhead = agent._estimate_tokens([{"content": system}])
    msg_tokens = agent._estimate_tokens(agent.messages)
    budget = agent.config.max_context_tokens
    used = msg_tokens + overhead
    pct = used / budget * 100 if budget else 0
    filled = int(pct / 5)
    bar = "█" * filled + "░" * (20 - filled)
    print(info(f"Context: [{bar}] {used:,} / {budget:,} ({pct:.1f}%)"))
    print(f"  {dim('System overhead:')} ~{overhead:,} tokens")
    print(f"  {dim('Messages:')}        ~{msg_tokens:,} tokens")


@register("metrics", "Show agent metrics (turns, tokens, tools, latency)", usage="/metrics")
def cmd_metrics(agent, args: str):
    # Try new Telemetry first, fall back to old AgentMetrics
    metrics = getattr(agent, "telemetry", None) or getattr(agent, "metrics", None)
    if metrics is None:
        print(dim("No metrics available."))
        return

    try:
        snap = metrics.snapshot()
    except TypeError:
        snap = metrics.snapshot(chars_per_token=getattr(agent.config, "chars_per_token", 4))

    print(info("Agent Metrics"))
    turns = snap.get("turns", snap.get("turns_total", 0))
    tools = snap.get("tools", {})
    latency = snap.get("turn_latency", {})

    print(f"  {dim('Turns:')}           {turns}")
    if isinstance(tools, dict):
        print(f"  {dim('Tool calls:')}      {tools.get('total', 0)} "
              f"({tools.get('errors', 0)} errors, {tools.get('success_rate', 0)}% success)")
    else:
        print(f"  {dim('Tool calls:')}      {snap.get('tool_calls', 0)} "
              f"({snap.get('tool_errors', 0)} errors)")
    print(f"  {dim('Avg latency:')}     {snap.get('avg_latency_ms', snap.get('turn_latency_ms_avg', 0)):.0f} ms")
    if latency:
        print(f"  {dim('Latency p50:')}      {latency.get('p50_ms', '-')} ms")
        print(f"  {dim('Latency p95:')}      {latency.get('p95_ms', '-')} ms")
        print(f"  {dim('Latency p99:')}      {latency.get('p99_ms', '-')} ms")

    # Per-tool breakdown
    per_tool = snap.get("per_tool", {})
    if per_tool:
        print(f"  {dim('Per-tool:')}")
        for name, stats in sorted(per_tool.items()):
            print(f"    {dim(name + ':')} {stats['calls']} calls, {stats['avg_duration_ms']:.0f} ms avg"
                  f"{', ' + str(stats['errors']) + ' errors' if stats.get('errors') else ''}")


@register("compact", "Compact session history to save context", usage="/compact")
def cmd_compact(agent, args: str):
    if agent.session is None:
        print(warning("⚠ No active session to compact."))
        return

    msg_count = len(agent.messages)
    if msg_count <= 10:
        print(dim(f"Session has only {msg_count} messages — not enough to compact."))
        return

    print(info(f"Compacting session ({msg_count} messages)..."))

    # Use the runtime's Compactor (LLM summarization) if available.
    # AgentAdapter carries the REPL's event loop for synchronous compaction.
    loop = getattr(agent, '_loop', None)

    if hasattr(agent, 'runtime') and hasattr(agent.runtime, 'maybe_compact') and loop is not None:
        try:
            session_dict = dict(agent.session) if isinstance(agent.session, dict) else (
                agent.session.to_dict() if hasattr(agent.session, 'to_dict') else agent.session._data
            )
            before = len(session_dict.get("messages", []))
            result = loop.run_until_complete(
                agent.runtime.maybe_compact(session_dict, force=True),
            )
            if result and result.get("compacted"):
                agent.messages = list(session_dict.get("messages", agent.messages))
                after = len(agent.messages)
                print(success(f"✓ Compacted: {before} → {after} messages ({before - after} removed)"))
                if result.get("summary"):
                    print(dim(f"  Summary: {result['summary'][:120]}..."))
            else:
                print(dim("Compaction skipped: not enough messages to summarize."))
        except Exception as exc:
            logger.warning("LLM compaction failed, falling back to truncation: %s", exc)
            _compact_truncate(agent)
    else:
        _compact_truncate(agent)


def _compact_truncate(agent):
    """Fallback compaction: simple truncation keeping recent messages."""
    keep_recent = getattr(agent.config, 'compact_keep_recent', 10)
    msg_count = len(agent.messages)
    if msg_count <= keep_recent:
        print(dim(f"Session has only {msg_count} messages — not enough to compact."))
        return
    removed = msg_count - keep_recent
    agent.messages[:] = agent.messages[-keep_recent:]
    print(success(f"✓ Truncated: {msg_count} → {keep_recent} messages ({removed} removed)"))


@register("approve", "Toggle auto-approve for tool calls", aliases=("y",), usage="/approve")
def cmd_approve(agent, args: str):
    agent.config = agent.config.replace(auto_approve=not agent.config.auto_approve)
    state = "ON" if agent.config.auto_approve else "OFF"
    print(success(f"✓ Auto-approve: {state}"))


@register("thinking", "Toggle reasoning trace display", aliases=("T",), usage="/thinking")
def cmd_thinking(agent, args: str):
    agent.config = agent.config.replace(show_thinking=not agent.config.show_thinking)
    state = "ON" if agent.config.show_thinking else "OFF"
    print(success(f"✓ Show thinking: {state}"))


@register("bash", "Run a bash command directly", aliases=("!", "sh"), usage="/bash <command>")
def cmd_bash(agent, args: str):
    if not args:
        print(info("Usage: /bash <command>"))
        return
    from wisp.tools import tool_run_bash, check_dangerous_command

    reason = check_dangerous_command(args)
    if reason:
        import sys
        if not sys.stdin.isatty():
            print(warning(f"⚠️  Blocked dangerous command ({reason})"))
            return
        try:
            print(warning(f"     ⚠️  DANGEROUS: {reason}"))
            choice = input("     Type 'yes' to approve bash: ").strip().lower()
            if choice != "yes":
                print(dim("  ⏭  Skipped"))
                return
        except (KeyboardInterrupt, EOFError, OSError):
            print()
            return

    ws = agent.config.workspace or "."
    try:
        result = tool_run_bash(args, ws)
        print(result)
    except Exception as e:
        print(error(f"✗ {e}"))


@register("workspace", "Change working directory", aliases=("cd", "w"), usage="/workspace <dir>")
def cmd_workspace(agent, args: str):
    if not args:
        print(f"Current workspace: {accent(agent.config.workspace or '.')}")
        return
    new_ws = args.strip()
    path = Path(new_ws).expanduser()
    if not path.exists():
        print(error(f"✗ Path does not exist: {path}"))
        return
    if not path.is_dir():
        print(error(f"✗ Not a directory: {path}"))
        return
    agent.config = agent.config.replace(workspace=str(path.resolve()))
    # Invalidate system prompt cache because skill discovery is workspace-relative
    if hasattr(agent, "_system_prompt_cache"):
        agent._system_prompt_cache.clear()
    print(success(f"✓ Workspace: {agent.config.workspace}"))


@register("grep", "Search files with grep", aliases=("g", "search"), usage="/grep <pattern> [path]")
def cmd_grep(agent, args: str):
    if not args:
        print(info("Usage: /grep <pattern> [path]"))
        return
    # Last whitespace-separated token is the target path, everything before is the pattern
    parts = args.rsplit(maxsplit=1)
    if len(parts) == 1:
        pattern = parts[0]
        target = "."
    else:
        # If the last token looks like a file path (contains . / or exists), treat it as path
        candidate_path = parts[1]
        ws = agent.config.workspace or "."
        full_path = Path(ws) / candidate_path
        if full_path.exists() or "/" in candidate_path or "." in candidate_path:
            pattern = parts[0]
            target = candidate_path
        else:
            # Last token is part of the pattern
            pattern = args
            target = "."
    ws = agent.config.workspace or "."
    try:
        from wisp.tools._utils import _resolve_path
        target_path = _resolve_path(target, ws)
    except Exception as e:
        # Same containment policy as /read — absolute or traversal paths
        # must not let /grep search outside the workspace.
        print(error(f"✗ {e}"))
        return
    if not target_path.exists():
        print(error(f"✗ Path not found: {target_path}"))
        return
    try:
        result = subprocess.run(
            ["grep", "-r", "-n", "--color=never", pattern, str(target_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        lines = result.stdout.splitlines()
        if not lines:
            print(dim("(no matches)"))
            return
        for line in lines[:200]:
            print(line)
        if len(lines) > 200:
            print(dim(f"... and {len(lines) - 200} more matches"))
    except subprocess.TimeoutExpired:
        print(error("✗ grep timed out after 30s"))
    except Exception as e:
        print(error(f"✗ grep failed: {e}"))


@register("ls", "List files in a directory", aliases=("files", "dir"), usage="/ls [path] [pattern]")
def cmd_ls(agent, args: str):
    from wisp.tools import tool_list_files
    ws = agent.config.workspace or "."
    parts = args.split(maxsplit=1) if args else []
    path = parts[0] if parts else "."
    pattern = parts[1] if len(parts) > 1 else "*"
    try:
        result = tool_list_files(path, ws, pattern)
        print(result)
    except Exception as e:
        print(error(f"✗ {e}"))


@register("read", "Read a file", aliases=("cat",), usage="/read <file> [offset] [limit]")
def cmd_read(agent, args: str):
    from wisp.tools import tool_read_file
    ws = agent.config.workspace or "."
    parts = args.split()
    if not parts:
        print(info("Usage: /read <file> [offset] [limit]"))
        return
    path = parts[0]
    offset = int(parts[1]) if len(parts) > 1 else 0
    limit = int(parts[2]) if len(parts) > 2 else 2000
    try:
        result = tool_read_file(path, ws, offset, limit)
        print(result)
    except Exception as e:
        print(error(f"✗ {e}"))


@register("drop", "Remove the last message from history", aliases=("pop", "undo"), usage="/drop")
def cmd_drop(agent, args: str):
    if not agent.messages:
        print(dim("History is empty."))
        return
    removed = agent.messages.pop()
    role = removed.get("role", "?")
    preview = (removed.get("content", "") or "")[:60].replace("\n", " ")
    print(success(f"✓ Dropped last message ({role}): {preview}..."))


def _get_orchestrator(agent):
    """Prefer the composition-wired orchestrator over a degraded bare one.

    The runtime's orchestrator carries tool_executor, agent_runtime, and
    store wiring; a freshly built one only inherits config/workspace.
    """
    from wisp.multi_agent import SubagentOrchestrator

    wired = getattr(getattr(agent, "runtime", None), "orchestrator", None)
    if wired is not None:
        return wired
    return SubagentOrchestrator(parent_agent=agent)


def _print_subagent_progress(event) -> None:
    """Render an OrchestratorEvent through the shared subagent renderer."""
    import sys

    from wisp.tool_executor import orchestrator_event_to_agent_event
    from wisp.transport.renderer import render_subagent_status

    line = render_subagent_status(orchestrator_event_to_agent_event(event))
    if line:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


@register("spawn", "Spawn a subagent for a scoped task", aliases=("sub", "delegate"), usage="/spawn <task description>")
def cmd_spawn(agent, args: str):
    if not args:
        print(info("Usage: /spawn <task description>"))
        print(dim("Example: /spawn research the best Python HTTP client library"))
        return
    from wisp.multi_agent import SubagentContract
    from wisp.async_utils import run_sync_coro
    contract = SubagentContract(
        name="spawn",
        task=args,
        timeout_seconds=120,
        max_iterations=15,
        progress_callback=_print_subagent_progress,
    )
    orch = _get_orchestrator(agent)
    print(accent(f"🧬 Spawning subagent: {args[:60]}..."))
    result = run_sync_coro(orch.run(contract))
    status = success("✓") if result.success else error("✗")
    if result.timed_out:
        status = warning("⏱")
    print(f"\n{status} Subagent done ({result.elapsed_seconds:.1f}s, {result.iterations_used} iterations)")
    print("─" * 40)
    print(result.output)


# ── Background agents ─────────────────────────────────────────────────

def _get_background_manager(agent):
    """Resolve the composition-wired BackgroundAgentManager, or None."""
    orch = getattr(getattr(agent, "runtime", None), "orchestrator", None)
    return getattr(orch, "background_agents", None)


@register("agents", "Show background subagents (list, detail, cancel, send)",
          aliases=("ba",), usage="/agents [id | cancel <id> | send <id> <msg>]")
def cmd_agents(agent, args: str):
    from wisp.transport.renderer import render_agent_detail, render_background_agents
    from wisp.async_utils import run_sync_coro

    mgr = _get_background_manager(agent)
    if mgr is None:
        print(warning("Background agents not available (no composition root)."))
        return

    parts = args.split(maxsplit=2)
    sub = parts[0] if parts else ""

    if sub in ("cancel", "stop") and len(parts) >= 2:
        out = mgr.cancel(parts[1])
        if out.get("ok"):
            print(warning(f"⏹ Cancelled {parts[1]}"))
        else:
            print(error(out.get("error", "cancel failed")))
        return

    if sub == "send" and len(parts) >= 3:
        agent_id, message = parts[1], parts[2]
        out = run_sync_coro(mgr.send(agent_id, message))
        if out.get("ok"):
            print(accent(f"🧬 Continuation running on {agent_id} — poll with /agents {agent_id}"))
        else:
            print(error(out.get("error", "send failed")))
        return

    if not sub:
        entries = mgr.list(include_finished=True)
        print(render_background_agents([e for e in entries]))
        return

    # `/agents <id>` — detail view for one agent.
    entry = mgr.get(sub)
    if entry is None:
        print(error(f"No such agent: {sub}"))
        return
    snapshot = mgr.snapshot(entry)
    if snapshot["status"] == "running":
        # Brief settle window so a just-finished agent shows its result.
        snapshot = run_sync_coro(mgr.result(sub, wait_seconds=0.5))
    print(render_agent_detail(snapshot))


def _swarm_progress(event) -> None:
    """Print swarm progress updates to the terminal."""
    from wisp.multi_agent.task import EventKind
    kind = event.event_type
    p = event.payload
    if kind == EventKind.PLANNING:
        if "plan" in p:
            print(dim(f"   📋 Plan: {p['subtask_count']} subtasks"))
    elif kind == EventKind.TASK_STARTED:
        print(dim(f"   🔨 {p.get('role', 'agent')} started: {p.get('description', '')[:50]}"))
    elif kind == EventKind.TASK_COMPLETED:
        print(success(f"   ✓ {event.task_id} done ({p.get('elapsed', 0):.1f}s)"))
    elif kind == EventKind.TASK_FAILED:
        print(error(f"   ✗ {event.task_id} failed: {p.get('error', '')[:60]}"))
    elif kind == EventKind.TASK_RETRY:
        print(warning(f"   🔄 {event.task_id} retry #{p.get('retry', 0)} (backoff {p.get('backoff_seconds', 0)}s)"))


@register("swarm", "Launch a multi-agent swarm for a complex task", aliases=("multi",), usage="/swarm <task description>")
def cmd_swarm(agent, args: str):
    if not args:
        print(info("Usage: /swarm <task description>"))
        print(dim("Example: /swarm add user authentication with JWT tokens"))
        return

    from wisp.multi_agent import SubagentContract
    from wisp.async_utils import run_sync_coro

    roles = ["coder", "reviewer", "tester", "researcher"]

    contracts = []
    for role in roles:
        contracts.append(
            SubagentContract(
                name=role,
                task=args,
                role=role,
                timeout_seconds=120,
                max_iterations=15,
                progress_callback=_swarm_progress,
            )
        )

    print(info(f"🐝 Starting swarm with {len(roles)} agent(s)..."))
    print(dim(f"   Goal: {args}"))
    print(dim(f"   Roles: {', '.join(roles)}"))
    print()

    orch = _get_orchestrator(agent)
    try:
        results = run_sync_coro(orch.run_parallel(contracts, max_concurrent=4))
    except KeyboardInterrupt:
        print(warning("\n⚠ Interrupted. Stopping all agents..."))
        raise

    # Build a synthetic result object for the synthesizer
    class _SwarmResult:
        def __init__(self, goal, results):
            self.goal = goal
            self.agent_results = results
            self.elapsed_seconds = sum(r.elapsed_seconds for r in results)
            self.files_changed = []
            self.success = any(r.success for r in results)
            self.final_output = "\n\n".join(r.output for r in results if r.output)
            self.plan = f"Parallel execution with {len(roles)} agents: {', '.join(roles)}"

    result = _SwarmResult(args, results)

    # Synthesize a proper final answer using the agent's LLM
    print()
    print(info("🐝 Synthesizing final answer..."))
    final = _swarm_synthesize(agent, result)
    print()
    print(success("✓ Swarm complete"))
    print("─" * 60)
    print(final)
    print("─" * 60)
    print()
    print(dim(f"⏱  Total time: {result.elapsed_seconds:.1f}s"))
    print(dim(f"📁 Files changed: {', '.join(result.files_changed) if result.files_changed else 'none'}"))
    if not result.success:
        print(warning("⚠ Some tasks failed. Review the output above."))


def _swarm_synthesize(agent, result) -> str:
    """Use the LLM to produce a coherent final answer from swarm results."""
    agent_results_text = ""
    for r in result.agent_results:
        icon = "PASS" if r.success else "FAIL"
        agent_results_text += f"\n### {icon}: {r.task_id}\n{r.output[:3000]}\n"
        if r.error:
            agent_results_text += f"\n**Error:** {r.error}\n"

    prompt = f"""A multi-agent swarm just completed a task on my behalf. You are the conductor giving me the final briefing.

## Goal
{result.goal}

## Plan
{result.plan}

## Agent Results
{agent_results_text}

## Files Changed
{', '.join(result.files_changed) if result.files_changed else 'none'}

---

Please give me a clear, concise final answer that:
1. Summarizes what was accomplished
2. Highlights key decisions or changes made
3. Mentions any files that were modified
4. Flags any issues or failures
5. Suggests next steps if applicable

Write this as a direct report to me, the user. No preamble — just the synthesis.
"""
    try:
        # Inject the synthesis prompt as a temporary user message
        saved_messages = agent.messages
        try:
            agent.messages = list(saved_messages)
            agent.messages.append({"role": "user", "content": prompt})
            response = agent._run_turn_streaming()
        finally:
            agent.messages = saved_messages
        content = response.get("message", {}).get("content", "") if isinstance(response.get("message"), dict) else ""
        return content.strip() or result.final_output
    except Exception:
        return result.final_output


@register("new", "Start a new session", aliases=(), usage="/new")
def cmd_new(agent, args: str):
    from wisp.infra.session_dto import SessionDTO
    agent._save_session()
    # AgentAdapter.session is a plain dict everywhere else — stay in that
    # contract so /session, /save and the REPL keep working after /new.
    agent.session = SessionDTO.create(
        model=agent.config.model,
        workspace=agent.config.workspace or ".",
        first_prompt="New session",
    ).to_dict()
    view = SessionView(agent.session)
    agent.messages = view.messages
    print(success(f"✓ New session started: {view.id}"))


@register("continue", "Continue the assistant's previous response", aliases=("c", "go", "on"), usage="/continue")
def cmd_continue(agent, args: str):
    """Explicitly continue from the last assistant message.

    Builds an expanded continuation prompt and returns it so the REPL
    can run a follow-up turn immediately.
    """
    if not agent.messages:
        print(warning("⚠ No conversation history to continue from."))
        return True

    expanded = agent._expand_continuation("continue")

    # If expansion did nothing useful, warn and bail
    if expanded == "continue":
        print(warning("⚠ No previous assistant message found to continue from."))
        return True

    # Show the user what we're continuing from (first line only for brevity)
    context_preview = expanded.split("\n")[-1] if "\n" in expanded else expanded
    if context_preview.startswith("[Context:"):
        print(info(f"⏩ Continuing… {context_preview[:100]}"))
    else:
        print(info("⏩ Continuing previous response…"))

    # Return the prompt so the REPL loop runs a follow-up turn.
    # The REPL's run_turn will add the user message to the session.
    return expanded


@register("exit", "Exit Wisp", aliases=("quit", "q", "bye"), usage="/exit")
def cmd_exit(agent, args: str):
    raise ExitREPL



# ── /init: Generate wisp.md ──────────────────────────────────────────

@register("init", "Generate wisp.md for this codebase", aliases=(), usage="/init [overwrite]")
def cmd_init(agent, args: str):
    """Analyze the current workspace and generate a wisp.md file.

    The generated file includes project overview, architecture, key files,
    conventions, and dependencies — giving Wisp instant context whenever
    it enters this project.
    """
    ws = Path(agent.config.workspace or ".").resolve()
    wisp_md = ws / "wisp.md"

    if wisp_md.exists() and "overwrite" not in args.lower():
        print(warning(f"⚠ {wisp_md.name} already exists."))
        print(dim("   Run '/init overwrite' to regenerate."))
        return

    print(info(f"🔍 Analyzing {ws.name}…"))

    # ── Gather project metadata ──
    from wisp.project_context import detect_project_context
    ctx = detect_project_context(str(ws))

    # ── Gather file structure ──
    top_files = []
    top_dirs = []
    for item in sorted(ws.iterdir()):
        if item.name.startswith(".") and item.name not in (".github", ".vscode"):
            continue
        if item.is_file():
            top_files.append(item.name)
        elif item.is_dir():
            top_dirs.append(item.name + "/")

    # ── Gather source file stats ──
    from wisp.code_index import build_index
    index = build_index(str(ws))

    # ── Find key source files (entry points, main modules) ──
    key_files = []
    for fname in top_files:
        if fname.lower() in ("readme.md", "readme.rst", "readme.txt"):
            key_files.append((fname, "Project documentation"))
        elif fname.lower() in ("main.py", "app.py", "index.js", "main.rs", "main.go"):
            key_files.append((fname, "Application entry point"))
        elif fname in ("pyproject.toml", "package.json", "cargo.toml", "go.mod", "setup.py"):
            key_files.append((fname, "Project configuration"))
        elif fname in ("dockerfile", "docker-compose.yml", "compose.yaml"):
            key_files.append((fname, "Docker configuration"))
        elif fname in ("makefile", "justfile"):
            key_files.append((fname, "Build automation"))
        elif fname in ("requirements.txt", "poetry.lock", "yarn.lock", "cargo.lock"):
            key_files.append((fname, "Dependency lock file"))

    # ── Find test directories ──
    test_dirs = [d for d in top_dirs if "test" in d.lower() or "spec" in d.lower()]

    # ── Find CI/config directories ──
    ci_dirs = [d for d in top_dirs if d in (".github/", ".gitlab/", ".circleci/")]

    # ── Extract top-level symbols ──
    top_symbols = []
    for file_symbols in index.symbols.values():
        for sym in file_symbols:
            if sym.kind in ("class", "function", "struct", "trait", "interface"):
                top_symbols.append(sym)
    # Sort by file, then line; cap at 30
    top_symbols.sort(key=lambda s: (s.file, s.line))
    top_symbols = top_symbols[:30]

    # ── Build wisp.md content ──
    lines: list[str] = []
    lines.append(f"# {ctx.project_name or ws.name}")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    if ctx.project_name:
        lines.append(f"**Project:** {ctx.project_name}")
    if ctx.language:
        ver = f" {ctx.language_version}" if ctx.language_version else ""
        lines.append(f"**Language:** {ctx.language}{ver}")
    if ctx.framework:
        lines.append(f"**Framework:** {ctx.framework}")
    if ctx.build_system:
        lines.append(f"**Build System:** {ctx.build_system}")
    if ctx.project_type:
        lines.append(f"**Type:** {ctx.project_type}")
    lines.append("")

    # File structure
    lines.append("## File Structure")
    lines.append("")
    lines.append("```")
    for d in top_dirs[:20]:
        lines.append(d)
    for f in top_files[:20]:
        lines.append(f)
    if len(top_dirs) > 20 or len(top_files) > 20:
        lines.append("...")
    lines.append("```")
    lines.append("")

    # Key files
    if key_files:
        lines.append("## Key Files")
        lines.append("")
        for fname, desc in key_files:
            lines.append(f"- `{fname}` — {desc}")
        lines.append("")

    # Dependencies
    if ctx.dependencies:
        lines.append("## Dependencies")
        lines.append("")
        for dep in ctx.dependencies[:15]:
            lines.append(f"- {dep}")
        if len(ctx.dependencies) > 15:
            lines.append(f"- …and {len(ctx.dependencies) - 15} more")
        lines.append("")

    # Dev dependencies
    if ctx.dev_dependencies:
        lines.append("## Dev Dependencies")
        lines.append("")
        for dep in ctx.dev_dependencies[:10]:
            lines.append(f"- {dep}")
        if len(ctx.dev_dependencies) > 10:
            lines.append(f"- …and {len(ctx.dev_dependencies) - 10} more")
        lines.append("")

    # Architecture / Key Symbols
    if top_symbols:
        lines.append("## Architecture")
        lines.append("")
        lines.append("Key symbols defined in the codebase:")
        lines.append("")
        current_file = None
        for sym in top_symbols:
            if sym.file != current_file:
                current_file = sym.file
                lines.append(f"\n**{sym.file}**")
            parent = f" (in {sym.parent})" if sym.parent else ""
            lines.append(f"- `{sym.name}` — {sym.kind}{parent}")
        lines.append("")

    # Testing
    if ctx.has_tests or test_dirs:
        lines.append("## Testing")
        lines.append("")
        if ctx.test_framework:
            lines.append(f"**Framework:** {ctx.test_framework}")
        if test_dirs:
            lines.append(f"**Directories:** {', '.join(test_dirs)}")
        lines.append("")

    # CI/CD
    if ci_dirs:
        lines.append("## CI / CD")
        lines.append("")
        lines.append(f"**Config directories:** {', '.join(ci_dirs)}")
        lines.append("")

    # Docker
    if ctx.has_docker:
        lines.append("## Docker")
        lines.append("")
        lines.append("This project includes Docker configuration.")
        lines.append("")

    # Conventions
    lines.append("## Conventions")
    lines.append("")
    if ctx.language == "Python":
        lines.append("- Follow PEP 8 style guidelines")
        if "pytest" in str(ctx.test_framework).lower():
            lines.append("- Use pytest for testing")
    elif ctx.language == "JavaScript" or ctx.language == "TypeScript":
        lines.append("- Follow the project's ESLint / Prettier configuration")
    elif ctx.language == "Rust":
        lines.append("- Follow Rust naming conventions and `cargo fmt`")
    elif ctx.language == "Go":
        lines.append("- Follow Go conventions: `gofmt`, `golint`")
    lines.append("- Prefer targeted edits over full file rewrites")
    lines.append("- Run tests after making changes")
    lines.append("")

    # Wisp-specific guidance
    lines.append("## Wisp Agent Notes")
    lines.append("")
    lines.append("This file was auto-generated by `/init`. Update it as the project evolves.")
    lines.append("- Use `search_symbols` to find functions/classes quickly")
    lines.append("- Use `read_file` with offset/limit for large files")
    lines.append("- Use `run_bash` for build/test commands")
    if ctx.build_system:
        lines.append(f"- Build/test via: {ctx.build_system}")
    lines.append("")

    content = "\n".join(lines)

    # Write file
    try:
        wisp_md.write_text(content, encoding="utf-8")
        print(success(f"✓ Created {wisp_md.name} ({len(content)} chars)"))
        print(dim(f"   {len(top_dirs)} dirs, {len(top_files)} files, {index.total_symbols} symbols analyzed."))
    except Exception as e:
        print(error(f"✗ Failed to write {wisp_md.name}: {e}"))


# ── Session control surface (REPL design R5) ─────────────────────────


def _fmt_k(n: int) -> str:
    if n >= 1024:
        return f"{n / 1024:.0f}k"
    return str(n)


@register("sessions", "List saved sessions", aliases=("ss",), usage="/sessions")
def cmd_sessions(agent, args: str):
    store = getattr(getattr(agent, "runtime", None), "store", None)
    if store is None:
        print(warning("No session store available."))
        return
    try:
        rows = store.list_sessions(limit=10)
    except Exception as e:
        print(error(f"✗ Could not list sessions: {e}"))
        return
    if not rows:
        print(info("No saved sessions yet."))
        return
    print(accent("Saved sessions (newest first):"))
    for r in rows:
        sid = str(r.get("id", "?"))
        short = sid.split("-")[0] if "-" in sid else sid[:8]
        title = r.get("title") or "(untitled)"
        model = r.get("model", "?")
        msgs = r.get("msg_count", 0)
        updated = str(r.get("updated_at", ""))[:16].replace("T", " ")
        print(f"  {short} · {model} · {msgs} msgs · {title}{dim(f' · {updated}')}")
    print(dim("Resume with: wisp repl --session <full-id>"))


