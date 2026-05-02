"""Slash commands for Wisp REPL — local directives that bypass the LLM.

Commands are registered via the @register decorator and dispatched by name.
They receive the WispAgent instance and can mutate its state directly.
"""

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class ExitREPL(Exception):
    """Raised by /exit to signal graceful REPL termination."""
    pass


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
    """Decorator to register a slash command."""
    def decorator(fn: Callable):
        cmd = Command(name, description, fn, aliases, usage)
        _REGISTRY[name] = cmd
        for alias in aliases:
            _REGISTRY[alias] = cmd
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


def dispatch(text: str, agent) -> bool:
    """Parse text as a slash command and execute it.

    Returns True if the input was consumed (known or unknown /command).
    Returns False if text does not start with '/'.
    """
    text = text.strip()
    if not text.startswith("/"):
        return False

    body = text[1:].strip()
    if not body:
        return False

    parts = body.split(maxsplit=1)
    name = parts[0]
    args = parts[1] if len(parts) > 1 else ""

    cmd = lookup(name)
    if not cmd:
        print(f"Unknown command: /{name}. Type /help for available commands.")
        return True

    try:
        cmd.handler(agent, args.strip())
    except ExitREPL:
        raise
    except Exception as e:
        logger.exception("Command /%s failed", name)
        print(f"✗ Command failed: {e}")
    return True


# ── Command implementations ──────────────────────────────────────────


@register("help", "Show available slash commands", aliases=("h", "?"), usage="/help")
def cmd_help(agent, args: str):
    print("Available commands:")
    for cmd in all_commands():
        alias_str = f" (aliases: {', '.join(cmd.aliases)})" if cmd.aliases else ""
        print(f"  /{cmd.name:<12}  {cmd.description}{alias_str}")
    print()
    print("Commands run locally and do not send anything to the LLM.")


@register("clear", "Clear conversation history", aliases=("cls",), usage="/clear")
def cmd_clear(agent, args: str):
    count = len(agent.messages)
    agent.messages.clear()
    print(f"✓ Cleared {count} messages.")


@register("model", "Switch Ollama model", aliases=("m",), usage="/model <name>")
def cmd_model(agent, args: str):
    if not args:
        print(f"Current model: {agent.config.model}")
        return
    new_model = args.strip()
    agent.config.model = new_model
    agent.client.model = new_model
    # Invalidate system prompt cache in case model-specific behavior matters
    if hasattr(agent, "_system_prompt_cache"):
        agent._system_prompt_cache.clear()
    print(f"✓ Model set to: {new_model}")


@register("skill", "Load or list skills", aliases=("s",), usage="/skill [name]")
def cmd_skill(agent, args: str):
    from wisp.skills import discover_skills, find_skill

    ws = agent.config.workspace or "."
    if not args:
        skills = discover_skills(ws)
        if not skills:
            print("No skills found.")
            return
        active = getattr(agent, "_active_skill", None)
        for sk in skills:
            marker = " → " if active == sk.name else "   "
            print(f"{marker}{sk.name}: {sk.description}")
        return

    name = args.strip()
    skill = find_skill(name, ws)
    if skill is None:
        print(f"⚠ Skill '{name}' not found.")
        return

    agent._active_skill = name
    if hasattr(agent, "_system_prompt_cache"):
        agent._system_prompt_cache.clear()
    print(f"✓ Skill loaded: {skill.name} — {skill.description}")


@register("session", "Show session info", usage="/session")
def cmd_session(agent, args: str):
    if agent.session is None:
        print("No active session.")
        return
    active_skill = getattr(agent, "_active_skill", None)
    print(f"Session ID:    {agent.session.id}")
    print(f"Title:         {agent.session.title or '(untitled)'}")
    print(f"Model:         {agent.config.model}")
    print(f"Workspace:     {agent.config.workspace or '.'}")
    print(f"Active skill:  {active_skill or '(none)'}")
    print(f"Messages:      {len(agent.messages)}")
    print(f"Auto-approve:  {agent.config.auto_approve}")
    print(f"Show thinking: {agent.config.show_thinking}")


@register("save", "Force-save the current session", usage="/save")
def cmd_save(agent, args: str):
    agent._save_session()
    if agent.session:
        print(f"✓ Session saved: {agent.session.id}")
    else:
        print("✓ Nothing to save (no session).")


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
    print(f"Context: [{bar}] {used:,} / {budget:,} ({pct:.1f}%)")
    print(f"  System overhead: ~{overhead:,} tokens")
    print(f"  Messages:        ~{msg_tokens:,} tokens")


@register("approve", "Toggle auto-approve for tool calls", aliases=("y",), usage="/approve")
def cmd_approve(agent, args: str):
    agent.config.auto_approve = not agent.config.auto_approve
    state = "ON" if agent.config.auto_approve else "OFF"
    print(f"✓ Auto-approve: {state}")


@register("thinking", "Toggle reasoning trace display", aliases=("T",), usage="/thinking")
def cmd_thinking(agent, args: str):
    agent.config.show_thinking = not agent.config.show_thinking
    state = "ON" if agent.config.show_thinking else "OFF"
    print(f"✓ Show thinking: {state}")


@register("bash", "Run a bash command directly", aliases=("!", "sh"), usage="/bash <command>")
def cmd_bash(agent, args: str):
    if not args:
        print("Usage: /bash <command>")
        return
    from wisp.tools import tool_run_bash, check_dangerous_command

    reason = check_dangerous_command(args)
    if reason:
        import sys
        if not sys.stdin.isatty():
            print(f"⚠️  Blocked dangerous command ({reason})")
            return
        try:
            print(f"     ⚠️  DANGEROUS: {reason}")
            choice = input("     Type 'yes' to approve bash: ").strip().lower()
            if choice != "yes":
                print("  ⏭  Skipped")
                return
        except (KeyboardInterrupt, EOFError, OSError):
            print()
            return

    ws = agent.config.workspace or "."
    try:
        result = tool_run_bash(args, ws)
        print(result)
    except Exception as e:
        print(f"✗ {e}")


@register("workspace", "Change working directory", aliases=("cd", "w"), usage="/workspace <dir>")
def cmd_workspace(agent, args: str):
    if not args:
        print(f"Current workspace: {agent.config.workspace or '.'}")
        return
    new_ws = args.strip()
    path = Path(new_ws).expanduser()
    if not path.exists():
        print(f"✗ Path does not exist: {path}")
        return
    if not path.is_dir():
        print(f"✗ Not a directory: {path}")
        return
    agent.config.workspace = str(path.resolve())
    # Invalidate system prompt cache because skill discovery is workspace-relative
    if hasattr(agent, "_system_prompt_cache"):
        agent._system_prompt_cache.clear()
    print(f"✓ Workspace: {agent.config.workspace}")


@register("grep", "Search files with grep", aliases=("g", "search"), usage="/grep <pattern> [path]")
def cmd_grep(agent, args: str):
    if not args:
        print("Usage: /grep <pattern> [path]")
        return
    parts = args.split(maxsplit=1)
    pattern = parts[0]
    target = parts[1] if len(parts) > 1 else "."
    ws = agent.config.workspace or "."
    target_path = Path(ws) / target
    if not target_path.exists():
        print(f"✗ Path not found: {target_path}")
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
            print("(no matches)")
            return
        for line in lines[:200]:
            print(line)
        if len(lines) > 200:
            print(f"... and {len(lines) - 200} more matches")
    except subprocess.TimeoutExpired:
        print("✗ grep timed out after 30s")
    except Exception as e:
        print(f"✗ grep failed: {e}")


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
        print(f"✗ {e}")


@register("read", "Read a file", aliases=("cat",), usage="/read <file> [offset] [limit]")
def cmd_read(agent, args: str):
    from wisp.tools import tool_read_file
    ws = agent.config.workspace or "."
    parts = args.split()
    if not parts:
        print("Usage: /read <file> [offset] [limit]")
        return
    path = parts[0]
    offset = int(parts[1]) if len(parts) > 1 else 0
    limit = int(parts[2]) if len(parts) > 2 else 2000
    try:
        result = tool_read_file(path, ws, offset, limit)
        print(result)
    except Exception as e:
        print(f"✗ {e}")


@register("drop", "Remove the last message from history", aliases=("pop", "undo"), usage="/drop")
def cmd_drop(agent, args: str):
    if not agent.messages:
        print("History is empty.")
        return
    removed = agent.messages.pop()
    role = removed.get("role", "?")
    preview = (removed.get("content", "") or "")[:60].replace("\n", " ")
    print(f"✓ Dropped last message ({role}): {preview}...")


@register("exit", "Exit Wisp", aliases=("quit", "q", "bye"), usage="/exit")
def cmd_exit(agent, args: str):
    raise ExitREPL
