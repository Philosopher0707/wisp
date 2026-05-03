"""CLI entry point for Wisp — the local Ollama-powered coding agent."""

import logging
import sys
from wisp import __version__
from wisp.config import WispConfig, load_config, save_config
from wisp.agent import WispAgent
from wisp.skills import discover_skills
from wisp.session import SessionManager, format_session_preview


def _setup_logging(verbose: bool = False):
    """Configure Python logging."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_run(prompt, model=None, skill=None, workspace=None, auto_approve=False, session_id=None, show_thinking=False):
    """Run Wisp with a prompt."""
    config = WispConfig()
    if model:
        config.model = model
    if workspace:
        config.workspace = workspace
    if auto_approve:
        config.auto_approve = True
    if show_thinking:
        config.show_thinking = True

    agent = WispAgent(config)
    agent.run(prompt, skill_name=skill, session_id=session_id)


def cmd_repl(model=None, skill=None, workspace=None, session_id=None, show_thinking=False, auto_approve=False):
    """Run Wisp in interactive REPL mode."""
    config = WispConfig()
    if model:
        config.model = model
    if workspace:
        config.workspace = workspace
    if show_thinking:
        config.show_thinking = True
    if auto_approve:
        config.auto_approve = True

    agent = WispAgent(config)
    agent.repl(skill_name=skill, session_id=session_id)


def cmd_skills(workspace=None):
    """List all discovered skills."""
    ws = workspace or "."
    skills = discover_skills(ws)
    if not skills:
        print("No skills found.")
        print(f"Searched in: {ws}/.agents/skills/, ~/.agents/skills/, etc.")
        return

    print(f"Found {len(skills)} skill(s):\n")
    for s in skills:
        print(f"  {s.name:30s}  {s.description}")
        print(f"  {'':30s}  📍 {s.file_path}")
        print()


def cmd_config(set_kv=None, validate=False):
    """View or set configuration."""
    from wisp.config import load_config, save_config, validate_config, get_schema

    if set_kv:
        key, value = set_kv.split("=", 1)
        config = load_config()
        config[key.strip()] = value.strip()
        try:
            save_config(config)
            print(f"✓ Set {key.strip()} = {value.strip()}")
        except ValueError as e:
            print(f"✗ {e}")
            return

    if validate:
        config = load_config()
        if not config:
            print("✓ No custom configuration to validate.")
            return
        errors = validate_config(config)
        if errors:
            print(f"Found {len(errors)} issue(s):\n")
            for err in errors:
                print(f"  ✗ {err}")
        else:
            print("✓ Configuration is valid.")
        return

    config = load_config()
    if not config:
        print("No custom configuration. Using defaults.")
        print()

    schema = get_schema()
    print("Current configuration:")
    for key, value in sorted(config.items()):
        desc = schema.get(key, {}).get("description", "")
        print(f"  {key}: {value}")
        if desc:
            print(f"     {desc}")
    print()
    print("Available settings:")
    for key, info in sorted(schema.items()):
        default = info["default"]
        type_name = info["type"].__name__ if not isinstance(info["type"], tuple) else " or ".join(t.__name__ for t in info["type"] if t is not type(None)) + " or None"
        print(f"  {key:20s}  ({type_name:8s})  default: {default!r}")
    print()
    print("Set a value:  wisp config --set key=value")
    print("Validate:     wisp config --validate")


def cmd_check():
    """Check if Ollama is available and the model is usable."""
    config = WispConfig()
    from wisp.ollama_client import OllamaClient
    client = OllamaClient(config)
    ok = client.check_health()
    if ok:
        print(f"✓ Ollama is running at {config.ollama_url}")
        print(f"✓ Model '{config.model}' is available")
    else:
        sys.exit(1)


def cmd_models():
    """List all models available in Ollama."""
    config = WispConfig()
    from wisp.ollama_client import OllamaClient
    client = OllamaClient(config)
    try:
        models = client.list_models()
        if not models:
            print("No models found. Pull one with: ollama pull <model>")
            return
        print(f"Available models ({len(models)}):\n")
        for m in models:
            name = m["name"]
            size_raw = m.get("size", 0)
            size_gb = size_raw / 1e9 if size_raw and size_raw > 0 else 0
            modified = m.get("modified_at", "")[:10]
            size_str = f"{size_gb:6.1f}GB" if size_gb > 0.01 else "   cloud"
            print(f"  {name:30s}  {size_str}  modified {modified}")
    except Exception as e:
        print(f"✗ Could not list models: {e}")
        sys.exit(1)


# ── Memory commands ──────────────────────────────────────────────────

def cmd_memory(args: list[str]):
    """Manage cross-session memory (learned preferences, project facts)."""
    from wisp.memory import add_fact, remove_fact, list_facts, clear_memory, load_memory

    if not args:
        # Show all facts
        facts = list_facts()
        memory = load_memory()
        ws_facts = memory.get("workspace_facts", {})

        if not facts and not ws_facts:
            print("No facts stored in memory.")
            print("  Use: wisp memory add \"<fact>\"")
            print("  Or the LLM can use the `remember` tool during conversations.")
            return

        if facts:
            print("Global facts:")
            for f in facts:
                print(f"  • {f}")
            print()

        for ws_path, ws_fs in ws_facts.items():
            print(f"Workspace ({ws_path}):")
            for f in ws_fs:
                print(f"  • {f}")
            print()

        print(f"Total: {len(facts) + sum(len(v) for v in ws_facts.values())} fact(s)")
        return

    sub = args[0]

    if sub == "add" and len(args) >= 2:
        fact = " ".join(args[1:])
        if add_fact(fact):
            print(f"✓ Added: {fact}")
        else:
            print(f"(Already exists or at capacity)")

    elif sub == "remove" and len(args) >= 2:
        fact = " ".join(args[1:])
        if remove_fact(fact):
            print(f"✓ Removed: {fact}")
        else:
            print(f"✗ Not found: {fact}")

    elif sub == "clear":
        clear_memory()
        print("✓ Memory cleared.")

    elif sub == "list":
        facts = list_facts()
        if facts:
            print("Facts:")
            for f in facts:
                print(f"  • {f}")
        else:
            print("No facts stored.")

    else:
        print("Usage:")
        print("  wisp memory                    List all facts")
        print("  wisp memory add \"<fact>\"       Add a fact")
        print("  wisp memory remove \"<fact>\"    Remove a fact")
        print("  wisp memory list               List global facts")
        print("  wisp memory clear              Clear all facts")


# ── Session commands ─────────────────────────────────────────────────

def cmd_session_list():
    """List all saved sessions."""
    mgr = SessionManager()
    sessions = mgr.list_sessions()
    if not sessions:
        print("No saved sessions.")
        print("Run 'wisp \"your prompt\"' to start a new session.")
        return

    print(f"Saved sessions ({len(sessions)}):\n")
    for s in sessions:
        # Show title or first prompt
        title = (s["title"] or "(untitled)")[:60]
        created = s["created_at"][:19] if s["created_at"] else "?"
        updated = s["updated_at"][:19] if s["updated_at"] else "?"
        print(f"  {s['id']}")
        print(f"    Title:    {title}")
        print(f"    Model:    {s['model']}")
        print(f"    Started:  {created}")
        print(f"    Updated:  {updated}")
        print(f"    Messages: {s['msg_count']}")
        print(f"    Continue: wisp -S {s['id']} \"your next question\"")
        print()


def _resolve_session_or_fragment(mgr: SessionManager, session_id: str):
    """Resolve a session ID, trying exact match then prefix fragment match."""
    session = mgr.load(session_id)
    if session is None:
        resolved = mgr.get_session_id_from_fragment(session_id)
        if resolved:
            session = mgr.load(resolved)
    return session


def cmd_session_show(session_id: str):
    """Show details of a specific session."""
    mgr = SessionManager()
    session = _resolve_session_or_fragment(mgr, session_id)
    if session is None:
        print(f"✗ Session '{session_id}' not found.")
        print("  Run 'wisp session list' to see available sessions.")
        return

    print(format_session_preview(session))
    print()
    print(f"  Continue: wisp -S {session.id} \"your next question\"")


def cmd_session_delete(session_id: str):
    """Delete a session."""
    mgr = SessionManager()
    session = _resolve_session_or_fragment(mgr, session_id)
    if session is None:
        print(f"✗ Session '{session_id}' not found.")
        print("  Run 'wisp session list' to see available sessions.")
        return
    mgr.delete(session.id)
    print(f"✓ Deleted session {session.id}")


def cmd_session_trim(session_id: str, keep: int = 10):
    """Trim a session to the last N exchanges (for context window management).

    Trims intelligently by counting complete user turns (user → assistant exchanges),
    preserving the last N turns regardless of how many tool messages they contain.
    """
    if keep < 1:
        print(f"✗ keep must be at least 1, got {keep}")
        return

    mgr = SessionManager()
    session = _resolve_session_or_fragment(mgr, session_id)
    if session is None:
        print(f"✗ Session '{session_id}' not found.")
        return

    # Count complete user turns
    user_msg_indices = [i for i, m in enumerate(session.messages) if m.get("role") == "user"]
    if len(user_msg_indices) <= keep:
        print(f"Session only has {len(user_msg_indices)} turn(s), nothing to trim.")
        return

    original = len(session.messages)
    # Find the start index of the (last N)th user turn
    # We keep everything from that user message onwards
    keep_from = user_msg_indices[-keep]
    session.messages = session.messages[keep_from:]
    mgr.save(session)
    print(f"✓ Trimmed session {session.id}: {original} → {len(session.messages)} messages "
          f"({keep} turn(s) preserved)")


# ── Help ─────────────────────────────────────────────────────────────

_SHORT_HELP = """Usage: wisp [options] 'prompt'
   or:  wisp <subcommand> [args]

Options:
  --model, -m <name>       Ollama model to use (default: kimi-k2.6:cloud)
  --skill, -s <name>       Load a skill to guide the agent
  --session, -S <id>       Continue an existing session
  --workspace, -w <dir>    Working directory (default: current dir)
  --auto-approve, -y       Skip approval prompts for tool calls
  --show-thinking, -T      Show reasoning trace inline
  --version                Show version

Subcommands:
  run <prompt>             Single-shot agent with a prompt
  repl                     Interactive REPL mode (continuous chat)
  session list             List all saved sessions
  session show <id>        Show session details and recent messages
  session delete <id>      Delete a session
  session trim <id> [n]    Trim session to last N exchanges (default: 10)
  skills                   List discovered skills
  config [--set k=v]       View or set configuration
  check                    Verify Ollama connectivity
  models                   List available Ollama models

Examples:
  wisp 'add error handling to main.py'
  wisp repl                  # Start interactive session
  wisp repl -S mysession     # Continue session in REPL
  wisp -S mysession 'next'   # Continue session in single-shot
  wisp --model kimi-k2.5:cloud 'refactor the auth module'
  wisp --skill code-review 'review the latest changes'
"""


def print_help():
    print(_SHORT_HELP)


def main():
    argv = list(sys.argv[1:])
    _setup_logging(verbose="--verbose" in argv)

    # Handle --version
    if "--version" in argv:
        print(f"wisp {__version__}")
        return

    # Handle --help
    if not argv or "-h" in argv or "--help" in argv:
        print_help()
        return

    _SUBCOMMANDS = {"run", "repl", "skills", "config", "check", "models", "session"}
    first = argv[0]

    # Global flags
    flags_model = None
    flags_skill = None
    flags_session = None
    flags_workspace = None
    flags_auto = False
    flags_show_thinking = False

    def extract_global_flags(args):
        """Extract global flags from args list, return remaining args."""
        nonlocal flags_model, flags_skill, flags_session, flags_workspace, flags_auto, flags_show_thinking
        result = []
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("--model", "-m") and i + 1 < len(args):
                flags_model = args[i + 1]
                i += 2
            elif a in ("--skill", "-s") and i + 1 < len(args):
                flags_skill = args[i + 1]
                i += 2
            elif a in ("--session", "-S") and i + 1 < len(args):
                flags_session = args[i + 1]
                i += 2
            elif a in ("--workspace", "-w") and i + 1 < len(args):
                flags_workspace = args[i + 1]
                i += 2
            elif a in ("--auto-approve", "-y"):
                flags_auto = True
                i += 1
            elif a in ("--show-thinking", "-T"):
                flags_show_thinking = True
                i += 1
            else:
                result.append(a)
                i += 1
        return result

    if first in _SUBCOMMANDS:
        rest = extract_global_flags(argv[1:])

        if first == "run":
            if not rest:
                print("✗ Please provide a prompt.")
                return
            cmd_run(" ".join(rest), flags_model, flags_skill, flags_workspace, flags_auto, flags_session, flags_show_thinking)

        elif first == "repl":
            cmd_repl(flags_model, flags_skill, flags_workspace, flags_session, flags_show_thinking, flags_auto)

        elif first == "session":
            if not rest:
                print("Usage: wisp session (list|show|delete|trim) [args]")
                print("  wisp session list              List all sessions")
                print("  wisp session show <id>         Show session details")
                print("  wisp session delete <id>       Delete a session")
                print("  wisp session trim <id> [n]     Trim to last N exchanges")
                return

            sub = rest[0]
            args = rest[1:]

            if sub == "list":
                cmd_session_list()
            elif sub == "show":
                if not args:
                    print("✗ Usage: wisp session show <id>")
                    return
                cmd_session_show(args[0])
            elif sub == "delete":
                if not args:
                    print("✗ Usage: wisp session delete <id>")
                    return
                cmd_session_delete(args[0])
            elif sub == "trim":
                if not args:
                    print("✗ Usage: wisp session trim <id> [n]")
                    return
                keep = int(args[1]) if len(args) > 1 else 10
                cmd_session_trim(args[0], keep)
            else:
                print(f"✗ Unknown session subcommand: {sub}")
                print("  Try: list, show <id>, delete <id>, trim <id> [n]")

        elif first == "skills":
            cmd_skills(flags_workspace)
        elif first == "config":
            set_kv = None
            validate = False
            i = 0
            while i < len(rest):
                if rest[i] == "--set" and i + 1 < len(rest):
                    set_kv = rest[i + 1]
                    i += 2
                elif rest[i] == "--validate":
                    validate = True
                    i += 1
                else:
                    i += 1
            cmd_config(set_kv, validate=validate)
        elif first == "check":
            cmd_check()
        elif first == "models":
            cmd_models()
        elif first == "memory":
            cmd_memory(rest)

    else:
        # Implicit mode: wisp [flags] 'prompt'
        rest = extract_global_flags(argv)
        if not rest:
            print("✗ Please provide a prompt.")
            print_help()
            return
        cmd_run(" ".join(rest), flags_model, flags_skill, flags_workspace, flags_auto, flags_session, flags_show_thinking)


if __name__ == "__main__":
    main()
