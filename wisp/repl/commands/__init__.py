"""Slash commands for Wisp REPL — local directives that bypass the LLM.

Registry lives here; implementations are split thematically across the
submodules imported at the bottom of this file (the import triggers each
module's @register decorators). ``wisp/commands.py`` is a back-compat
shim re-exporting everything from this package.

Commands are registered via the @register decorator and dispatched by name.
They receive the WispAgent instance and can mutate its state directly.
"""

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from wisp.exceptions import ExitREPL

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    handler: Callable
    aliases: tuple[str, ...] = ()
    usage: str = ""
    # Optional Tab-completion source: prefix -> full-word candidates
    completer: Optional[Callable[[str], list[str]]] = None


# Global registry: name/alias -> Command instance
_REGISTRY: dict[str, Command] = {}


def register(name: str, description: str, aliases: tuple[str, ...] = (),
             usage: str = "",
             completer: Optional[Callable[[str], list[str]]] = None):
    """Decorator to register a slash command.

    Raises ValueError on alias theft: a name/alias already owned by a
    different command fails at import time instead of silently rebinding.
    """
    def decorator(fn: Callable):
        cmd = Command(name, description, fn, aliases, usage, completer)
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
        from wisp.repl.commands.core import cmd_help
        cmd_help(agent, "")
        return True

    parts = body.split(maxsplit=1)
    name = parts[0]
    args = parts[1] if len(parts) > 1 else ""

    cmd = lookup(name)
    if not cmd:
        from wisp.colors import error
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
        from wisp.colors import error
        print(error(f"✗ Command failed: {e}"))
    return True


# ── Command implementations (import side effects register them) ──────

from wisp.repl.commands.core import (  # noqa: E402,F401
    cmd_help, cmd_clear, cmd_session, cmd_save, cmd_tokens, cmd_metrics,
    cmd_drop, cmd_new, cmd_continue, cmd_exit,
)
from wisp.repl.commands.provider import (  # noqa: E402,F401
    cmd_model, cmd_provider, cmd_setup,
)
from wisp.repl.commands.skills import cmd_skill  # noqa: E402,F401
from wisp.repl.commands.session_cmds import (  # noqa: E402,F401
    cmd_compact, cmd_sessions,
)
from wisp.repl.commands.files import (  # noqa: E402,F401
    cmd_bash, cmd_workspace, cmd_grep, cmd_ls, cmd_read, cmd_init,
)
from wisp.repl.commands.agents import (  # noqa: E402,F401
    cmd_approve, cmd_thinking, cmd_spawn, cmd_agents, cmd_swarm,
)
