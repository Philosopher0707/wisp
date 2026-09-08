"""Interactive `wisp setup` wizard (GH#18).

Numbered pickers (no prompt_toolkit in this repo), getpass-secured key
entry, live validation handshake before anything hits disk. All I/O is
injected (input_fn/password_fn/out) so the flow is unit-testable; the
CLI passes the real stdin/getpass/print.

Deliberate deviations from the request spec:
- No `wisp/core/config_store.py`: persistence reuses the canonical paths
  (provider_select.persist/store_key + config.save_config) — a parallel
  store would split the source of truth.
- No `anthropic` picker entry: ProviderFactory cannot build it
  (KNOWN_PROVIDERS has no anthropic); offering it repeats the exact
  '/provider mock succeeded then turn crashed' bug the factory warns about.
- No max_tokens probe knob: BaseProvider.generate() takes no max_tokens;
  the handshake is a tiny completion bounded by a worker-thread timeout.
"""

from __future__ import annotations

import getpass
import logging
import sys
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_S = 8.0

# Order matters: default (index 0) is the local-first recommendation.
SETUP_PROVIDERS = ["ollama", "openai", "nvidia", "openrouter"]

RECOMMENDED_MODELS: dict[str, list[str]] = {
    "openrouter": [
        "anthropic/claude-3.7-sonnet",
        "deepseek/deepseek-r1",
        "openai/gpt-4o",
        "meta-llama/llama-3.3-70b-instruct",
    ],
    "openai": ["gpt-4o", "gpt-4o-mini", "o3-mini", "o1"],
    "nvidia": [
        "nvidia/llama-3.1-nemotron-70b-instruct",
        "meta/llama-3.3-70b-instruct",
        "deepseek-ai/deepseek-r1",
    ],
    "ollama": [],  # filled live from /api/tags; manual fallback
}

KEY_URLS = {
    "openai": "https://platform.openai.com/api-keys",
    "openrouter": "https://openrouter.ai/keys",
    "nvidia": "https://build.nvidia.com (API section)",
}


def _pick(prompt: str, options: list[str], input_fn: Callable[[str], str],
          out: Callable[[str], None], default: int = 0) -> int:
    """Numbered single-choice picker; returns the chosen index."""
    for i, opt in enumerate(options):
        marker = " (default)" if i == default else ""
        out(f"  {i + 1}) {opt}{marker}")
    while True:
        raw = (input_fn(f"{prompt} [1-{len(options)}, default {default + 1}]: ") or "").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        out(f"  Enter a number 1-{len(options)} (or empty for default).")


def _yes_no(prompt: str, input_fn: Callable[[str], str],
            out: Callable[[str], None], default_yes: bool = True) -> bool:
    suffix = "[Y/n]" if default_yes else "[y/N]"
    raw = (input_fn(f"{prompt} {suffix}: ") or "").strip().lower()
    if not raw:
        return default_yes
    return raw in ("y", "yes")


def _provider_label(name: str) -> str:
    try:
        from wisp.provider_select import KNOWN_PROVIDERS
        return str(KNOWN_PROVIDERS.get(name, {}).get("label", name))
    except Exception:
        return name


def _ollama_models(base_url: str) -> list[str]:
    """Live model tags from the daemon; [] when unreachable."""
    try:
        from wisp.providers.ollama import OllamaProvider
        client = OllamaProvider(base_url=base_url)
        return [str(m.get("name", "")) for m in client.list_models()
                if m.get("name")]
    except Exception:
        return []


def _probe(provider_name: str, model: str, api_key: str, api_base: str,
           timeout_s: float = PROBE_TIMEOUT_S) -> tuple[bool, str]:
    """Tiny completion handshake in a worker thread (bounded by timeout).

    Returns (ok, detail). Never raises — every failure mode maps to a
    human message so the wizard can offer retry/model/save-anyway.
    """
    result: dict[str, Any] = {}

    def _run() -> None:
        try:
            from wisp.config import WispConfig
            from wisp.providers.factory import ProviderFactory

            cfg = WispConfig()
            cfg = cfg.replace(provider=provider_name, model=model)
            if api_key:
                try:
                    object.__setattr__(cfg, "api_key", api_key)
                    if hasattr(cfg, "__dict__"):
                        cfg.__dict__["api_key"] = api_key
                except Exception:
                    pass
            if api_base:
                try:
                    object.__setattr__(cfg, "api_base", api_base)
                    if hasattr(cfg, "__dict__"):
                        cfg.__dict__["api_base"] = api_base
                except Exception:
                    pass
            provider = ProviderFactory().from_config(cfg)
            provider.generate(
                "You are a connectivity probe.",
                [{"role": "user", "content": "Reply with exactly: ok"}],
            )
            result["ok"] = True
            result["detail"] = "handshake ok"
        except Exception as exc:  # noqa: BLE001 — mapped below, never raised
            result["ok"] = False
            result["detail"] = _classify_probe_error(exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s)
    if thread.is_alive():
        return False, f"timed out after {timeout_s:g}s (endpoint too slow)"
    if result.get("ok"):
        return True, str(result.get("detail", "ok"))
    return False, str(result.get("detail", "probe failed"))


def _classify_probe_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()
    if "401" in lowered or "unauthorized" in lowered or "invalid api key" in lowered:
        return f"unauthorized (bad key?) — {text[:160]}"
    if "403" in lowered or "forbidden" in lowered:
        return f"forbidden (key lacks access?) — {text[:160]}"
    if "404" in lowered or "not found" in lowered:
        return f"model or endpoint not found — {text[:160]}"
    if "connect" in lowered or "refused" in lowered or "max retries" in lowered:
        return f"unreachable — {text[:160]}"
    return text[:200]


def _persist_choice(provider: str, model: str, api_base: str,
                    api_key: str, key_from_env: bool) -> None:
    """Save provider/model/base via canonical persist; key via store_key
    only when the user typed it (env-sourced keys are never written)."""
    from wisp.provider_select import persist, store_key

    update: dict[str, str] = {"provider": provider, "model": model}
    if api_base:
        update["ollama_url" if provider == "ollama" else "api_base"] = api_base
    persist(update)
    if api_key and not key_from_env:
        store_key(provider, api_key)


def run_setup(input_fn: Callable[[str], str] | None = None,
              password_fn: Callable[[str], str] | None = None,
              out: Callable[[str], None] | None = None,
              probe_timeout: float = PROBE_TIMEOUT_S,
              probe_fn: Callable[..., tuple[bool, str]] | None = None,
              ) -> dict[str, Any] | None:
    """Run the interactive wizard. Returns the saved config summary, or
    None when aborted. Injected I/O keeps this unit-testable."""
    import builtins

    _in = input_fn or builtins.input
    _getpass = password_fn or getpass.getpass
    _out = out or print
    _probe_fn = probe_fn or _probe

    if input_fn is None and not sys.stdin.isatty():
        _out("error: wisp setup needs an interactive terminal.")
        return None

    from wisp.provider_select import KEY_ENV_VARS, KNOWN_PROVIDERS

    _out("Wisp setup — configure your model provider.")
    # ── [1/4] provider ──────────────────────────────────────────
    _out("[1/4] Provider")
    options = [f"{name} — {_provider_label(name)}" for name in SETUP_PROVIDERS]
    provider = SETUP_PROVIDERS[_pick("Provider", options, _in, _out, default=0)]
    spec = KNOWN_PROVIDERS.get(provider, {})
    needs_key = bool(spec.get("requires_key", False))

    model = ""
    api_key = ""
    api_base = ""
    key_from_env = False

    while True:
        # ── [2/4] model ─────────────────────────────────────────
        _out("[2/4] Model")
        candidates = list(RECOMMENDED_MODELS.get(provider, []))
        if provider == "ollama":
            live = _ollama_models(api_base or "http://localhost:11434")
            if live:
                _out(f"  (live daemon models: {len(live)} found)")
                candidates = live
            else:
                _out("  (daemon unreachable — enter a tag manually)")
        choices = candidates + ["[Custom model name]"]
        idx = _pick("Model", choices, _in, _out, default=0)
        if idx < len(candidates) and candidates:
            model = candidates[idx]
        else:
            model = (_in("  Custom model name: ") or "").strip()
            if not model:
                _out("  A model name is required.")
                continue

        # ── [3/4] endpoint + credentials ────────────────────────
        _out("[3/4] Endpoint & credentials")
        if provider == "ollama":
            default_base = "http://localhost:11434"
            raw = (_in(f"  Ollama base URL [{default_base}]: ") or "").strip()
            api_base = raw or default_base
        elif needs_key:
            url = KEY_URLS.get(provider, "")
            if url:
                _out(f"  Create a key: {url}")
            env_hit = ""
            for var in KEY_ENV_VARS.get(provider, []):
                import os
                if os.environ.get(var, "").strip():
                    env_hit = var
                    break
            if env_hit:
                use_env = _yes_no(f"  Found ${env_hit} in the environment — use it?", _in, _out)
                if use_env:
                    import os
                    api_key = os.environ[env_hit].strip()
                    key_from_env = True
            if not api_key:
                api_key = (_getpass(f"  API key for {provider} (hidden): ") or "").strip()
                if not api_key:
                    _out("  No key entered — aborting. Re-run `wisp setup` any time.")
                    return None

        # ── [4/4] validation handshake ──────────────────────────
        _out("[4/4] Validating…")
        ok, detail = _probe_fn(provider, model, api_key, api_base,
                               timeout_s=probe_timeout)
        if ok:
            _out(f"  ✓ {detail}")
            _persist_choice(provider, model, api_base, api_key, key_from_env)
            _out("Configuration saved.")
            return {"provider": provider, "model": model,
                    "validated": True, "saved": True}
        _out(f"  ✗ {detail}")
        _out("  [r]etry credentials  [m]odel  [s]ave anyway  [a]bort")
        choice = (_in("  Choice [r/m/s/a]: ") or "").strip().lower()
        if choice == "m":
            api_key = ""  # re-pick model (and credentials) from the top
            key_from_env = False
            continue
        if choice == "s":
            _persist_choice(provider, model, api_base, api_key, key_from_env)
            _out("Configuration saved WITHOUT validation.")
            return {"provider": provider, "model": model,
                    "validated": False, "saved": True}
        if choice == "a":
            _out("Aborted — nothing saved.")
            return None
        # default (r / empty): re-enter credentials (keyed) or re-probe
        if needs_key and provider != "ollama":
            api_key = (_getpass(f"  API key for {provider} (hidden): ") or "").strip()
            key_from_env = False
            if not api_key:
                _out("  No key entered — aborting.")
                return None


def _needs_setup(config: Any) -> bool:
    """True when no usable provider is configured (offline-safe: no probes)."""
    from wisp.provider_select import KNOWN_PROVIDERS, missing_key

    name = str(getattr(config, "provider", "") or "").strip().lower()
    if not name or name not in KNOWN_PROVIDERS or name == "mock":
        return name != "mock"
    return missing_key(name) is not None


def maybe_offer_setup(config: Any) -> bool:
    """Offer the wizard at boot. True when setup ran and saved (caller
    should stop and ask for a restart — the live graph holds stale config).

    Fires only on interactive terminals with no usable provider; scripts,
    pipes, and mock-configured sessions never block.
    """
    if not sys.stdin.isatty():
        return False
    try:
        if not _needs_setup(config):
            return False
    except Exception:
        return False
    sys.stdout.write(
        "\n  No usable model provider configured.\n")
    try:
        answer = (input("  Run interactive setup now? [Y/n]: ") or "").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if answer not in ("", "y", "yes"):
        return False
    saved = run_setup()
    return saved is not None and bool(saved.get("saved", False))
