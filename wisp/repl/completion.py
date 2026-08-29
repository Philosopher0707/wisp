"""Readline Tab completion for the Wisp REPL.

Completes, in order of context:
  - slash-command names (aliases included) right after "/"
  - per-command arguments via the Command.completer field or the
    built-in tables below (providers, models, subcommands, file paths)

``install_readline_completion()`` is called once at REPL start; it is a
no-op on non-tty stdin so piped sessions and tests are untouched.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_DELIMS = " \t\n"


# ── Completion sources (each returns full words for `prefix`) ────────


def command_completions(prefix: str) -> list[str]:
    """All registered command names/aliases beginning with prefix."""
    from wisp.repl.commands import all_commands

    out: list[str] = []
    for cmd in all_commands():
        for key in (cmd.name, *cmd.aliases):
            if key.startswith(prefix):
                out.append("/" + key)
    return sorted(out)


def provider_completions(prefix: str) -> list[str]:
    from wisp.provider_select import KNOWN_PROVIDERS

    return sorted(p for p in KNOWN_PROVIDERS if p.startswith(prefix))


def _current_provider() -> str:
    try:
        from wisp.config import get_setting
        return str(get_setting("provider", "ollama") or "ollama")
    except Exception:
        return "ollama"


def _safe_models(provider: str, prefix: str = "") -> list[str]:
    try:
        from wisp.provider_catalog import list_models
        models = list_models(provider)
        return [m for m in models if m.startswith(prefix)]
    except Exception:
        return []


def model_completions(prefix: str, provider: Optional[str] = None) -> list[str]:
    """Live models for `provider` (default: current), non-blocking best effort.

    Handles the `<provider>/<model>` compound form: completes the provider
    name until the slash, then that provider's models after it.
    """
    from wisp.provider_select import KNOWN_PROVIDERS

    if "/" in prefix:
        prov, _, model_part = prefix.partition("/")
        if prov not in KNOWN_PROVIDERS:
            return []
        return [f"{prov}/{m}" for m in _safe_models(prov) if m.startswith(model_part)]

    prov = provider or _current_provider()
    out = [p + "/" for p in KNOWN_PROVIDERS if p.startswith(prefix)]
    if prefix:
        out.extend(_safe_models(prov, prefix))
    else:
        out.extend(_safe_models(prov)[:25])
    return sorted(out)


def subcommand_completions(*subs: str) -> Callable[[str], list[str]]:
    """Static word list — for /agents, /skill, /multiline, etc."""
    def _complete(prefix: str) -> list[str]:
        return sorted(s for s in subs if s.startswith(prefix))
    return _complete


def path_completions(prefix: str) -> list[str]:
    """Filesystem path completion relative to cwd (workspace-agnostic)."""
    if not prefix or prefix.endswith("/"):
        dirname, partial = prefix, ""
    else:
        dirname, partial = os.path.split(prefix)
    base = dirname or "."
    try:
        names = os.listdir(base)
    except OSError:
        return []
    out = []
    for name in names:
        if not name.startswith(partial):
            continue
        full = os.path.join(dirname, name) if dirname else name
        out.append(full.rstrip("/") + "/" if os.path.isdir(full) else full)
    return sorted(out)


# ── Built-in per-command tables (used when Command.completer is unset) ──

COMMAND_COMPLETERS: dict[str, Callable[[str], list[str]]] = {
    "provider": provider_completions,
    "model": model_completions,
    "agents": subcommand_completions("cancel", "send"),
    "skill": subcommand_completions("suggest", "save"),
    "multiline": subcommand_completions("on", "off"),
    "ls": path_completions,
    "read": path_completions,
    "workspace": path_completions,
}


# ── Readline glue ────────────────────────────────────────────────────


class SlashCompleter:
    """readline completer: command names, then per-command arguments."""

    def __init__(self, command_completers: Optional[dict] = None):
        self._tables = dict(COMMAND_COMPLETERS)
        if command_completers:
            self._tables.update(command_completers)
        self._matches: list[str] = []

    def complete_matches(self, text: str) -> list[str]:
        """All completions for the word being typed (readline-independent)."""
        if not text.startswith("/"):
            return []
        parts = text.split(" ", 1)
        if len(parts) == 1:
            # Still typing the command itself — text includes the slash
            return command_completions(text[1:])
        name = parts[0][1:]
        arg_prefix = parts[1]
        completer = self._arg_completer(name)
        if completer is None:
            return []
        try:
            return [m for m in completer(arg_prefix) if m.startswith(arg_prefix)]
        except Exception:
            logger.debug("completion failed for /%s %r", name, arg_prefix, exc_info=True)
            return []

    def _arg_completer(self, name: str) -> Optional[Callable]:
        from wisp.repl.commands import lookup

        cmd = lookup(name)
        if cmd is not None and cmd.completer is not None:
            return cmd.completer
        return self._tables.get(name)

    # readline stateful protocol -------------------------------------
    def complete(self, text: str, state: int) -> Optional[str]:
        if state == 0:
            self._matches = self.complete_matches(text)
        return self._matches[state] if state < len(self._matches) else None


def install_readline_completion(completer: Optional[SlashCompleter] = None) -> bool:
    """Register Tab completion on the readline path. False when unsupported."""
    try:
        if not sys.stdin.isatty():
            return False
        import readline  # noqa: PLC0415
    except Exception:
        return False
    try:
        readline.set_completer(completer or SlashCompleter())
        readline.set_completer_delims(_DELIMS)
        readline.parse_and_bind("tab: complete")
        return True
    except Exception:
        logger.debug("readline completion install failed", exc_info=True)
        return False
