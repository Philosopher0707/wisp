"""CLI entry point for Wisp — the local Ollama-powered coding agent."""

import json
import logging
import os
import sys
from wisp import __version__
from wisp.config import WispConfig, load_config, save_config
from wisp.agent import WispAgent
from wisp.providers import get_provider
from wisp.skills import discover_skills
from wisp.session import SessionManager, format_session_preview
from wisp.colors import success, error, warning, info, dim, accent


def _setup_logging(verbose: bool = False):
    """Configure Python logging."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_server(host="0.0.0.0", port=8000, no_auth=False):
    """Run Wisp Cloud Server."""
    from wisp.server import main as server_main
    server_main(host=host, port=port, no_auth=no_auth)


def cmd_run(prompt, model=None, skill=None, workspace=None, auto_approve=False, session_id=None, show_thinking=False, long_task=False):
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
    
    # Force long-task mode if requested
    if long_task:
        import asyncio
        async def _run_long():
            async for event in agent.core.run_long_task(goal=prompt, workspace=config.workspace or ".", background=True):
                if event.type == "task_started":
                    print(f"🎯 Task started: {event.data.get('task_id', '')}")
                    print(f"   Goal: {event.data.get('goal', '')[:60]}")
                elif event.type == "system":
                    print(f"   {event.data.get('message', '')}")
        asyncio.run(_run_long())
        return
    
    agent.run(prompt, skill_name=skill, session_id=session_id)


def cmd_repl(model=None, skill=None, workspace=None, session_id=None, show_thinking=False, auto_approve=False, long_task=False):
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
    agent.repl(skill_name=skill, session_id=session_id, force_long_task=long_task)


def cmd_tui(model=None, workspace=None, show_thinking=False, auto_approve=False):
    """Run the experimental full-screen terminal app."""
    config = WispConfig()
    if model:
        config.model = model
    if workspace:
        config.workspace = workspace
    if show_thinking:
        config.show_thinking = True
    if auto_approve:
        config.auto_approve = True

    from wisp.tui.app import WispTUIApp

    app = WispTUIApp(config=config)
    app.run()


def cmd_skills(workspace=None):
    """List all discovered skills."""
    ws = workspace or "."
    skills = discover_skills(ws)
    if not skills:
        print(error("No skills found."))
        print(dim(f"Searched in: {ws}/.agents/skills/, ~/.agents/skills/, etc."))
        return

    print(info(f"Found {len(skills)} skill(s):\n"))
    for s in skills:
        print(f"  {accent(s.name):30s}  {s.description}")
        print(f"  {'':30s}  {dim('📍 ' + str(s.file_path))}")
        print()


def cmd_config(set_kv=None, validate=False):
    """View or set configuration."""
    from wisp.config import load_config, save_config, validate_config, get_schema, _type_name

    if set_kv:
        key, value = set_kv.split("=", 1)
        config = load_config()
        config[key.strip()] = value.strip()
        try:
            save_config(config)
            print(success(f"✓ Set {key.strip()} = {value.strip()}"))
        except ValueError as e:
            print(error(f"✗ {e}"))
            return

    if validate:
        config = load_config()
        if not config:
            print(success("✓ No custom configuration to validate."))
            return
        errors = validate_config(config)
        if errors:
            print(warning(f"Found {len(errors)} issue(s):\n"))
            for err in errors:
                print(error(f"  ✗ {err}"))
        else:
            print(success("✓ Configuration is valid."))
        return

    config = load_config()
    if not config:
        print(dim("No custom configuration. Using defaults."))
        print()

    schema = get_schema()
    print(info("Current configuration:"))
    for key, value in sorted(config.items()):
        desc = schema.get(key, {}).get("description", "")
        print(f"  {accent(key)}: {value}")
        if desc:
            print(dim(f"     {desc}"))
    print()
    print(dim("Available settings:"))
    for key, info_item in sorted(schema.items()):
        default = info_item["default"]
        type_name = _type_name(info_item["type"])
        print(f"  {accent(key):20s}  ({type_name:8s})  default: {default!r}")
    print()
    print(dim("Set a value:  wisp config --set key=value"))
    print(dim("Validate:     wisp config --validate"))


def cmd_print(prompt, model=None, session_id=None, output_format="json", quiet=False):
    """Headless mode: run prompt, print JSON result to stdout, exit.

    First attempts to reach a local Wisp server at port 8000.
    If unavailable, runs the agent directly in-process.
    """
    import requests

    result = None
    exit_code = 0

    # Try local server first
    try:
        api_key = os.environ.get("WISP_API_KEY", "")
        params = {"api-key": api_key} if api_key else {}
        resp = requests.post(
            "http://127.0.0.1:8000/api/prompt",
            json={
                "prompt": prompt,
                "model": model,
                "session_id": session_id,
            },
            params=params,
            timeout=600,
        )
        if resp.status_code == 200:
            result = resp.json()
        elif resp.status_code == 401:
            # Auth required -- fall through to in-process
            pass
        else:
            # Other error from server
            try:
                result = resp.json()
            except Exception:
                result = {"ok": False, "error": f"Server returned {resp.status_code}: {resp.text[:500]}"}
            exit_code = 1
    except requests.ConnectionError:
        # No server running, fall through to in-process
        pass
    except Exception:
        pass

    # Run in-process if server not available
    if result is None:
        if not quiet:
            sys.stderr.write("No local server found — running agent in-process...\n")
        try:
            import asyncio
            from wisp.config import WispConfig as _WispConfig
            from wisp.core.agent import WispAgentCore as _WispAgentCore
            from wisp.session import SessionManager as _SessionManager
            import time as _time

            config = _WispConfig()
            if model:
                config.model = model
            config.workspace = os.getcwd()
            config.auto_approve = True
            config.show_thinking = True
            config.permission_mode = "full"

            s = None
            if session_id:
                sm = _SessionManager()
                s = sm.load(session_id)
                if s is None:
                    resolved = sm.get_session_id_from_fragment(session_id)
                    if resolved:
                        s = sm.load(resolved)

            core = _WispAgentCore(config=config, session=s)
            if s is not None and s.messages:
                core.messages = list(s.messages)

            # Collect events in memory
            content_parts: list[str] = []
            thinking_parts: list[str] = []
            tool_calls: list[dict] = []
            errors: list[dict] = []
            session_id_out = ""
            iterations = 0
            start = _time.time()

            async def _run_and_collect():
                nonlocal session_id_out, iterations
                async for event in core.run(prompt):
                    etype = event.type
                    if etype == "content":
                        content_parts.append(event.text)
                    elif etype == "thinking":
                        thinking_parts.append(event.text)
                    elif etype == "tool_call":
                        tool_calls.append({
                            "name": event.data.get("name", ""),
                            "args": event.data.get("arguments", {}),
                            "result": "",
                        })
                    elif etype == "tool_result":
                        for tc in reversed(tool_calls):
                            if tc["name"] == event.data.get("name", "") and "result" in tc and not tc["result"]:
                                tc["result"] = event.data.get("result", "")
                                tc["duration_ms"] = event.data.get("duration_ms")
                                break
                        else:
                            tool_calls.append({
                                "name": event.data.get("name", ""),
                                "result": event.data.get("result", ""),
                                "duration_ms": event.data.get("duration_ms"),
                            })
                    elif etype == "error":
                        errors.append({
                            "message": event.data.get("message", ""),
                            "recoverable": event.data.get("recoverable", True),
                        })
                    elif etype == "done":
                        session_id_out = event.data.get("session_id", "")
                        iterations = event.data.get("turns", 0)

            asyncio.run(_run_and_collect())
            core.close()

            duration = (_time.time() - start) * 1000
            result = {
                "ok": len(errors) == 0,
                "session_id": session_id_out or (core.session.id if core.session else ""),
                "content": "\n".join(content_parts),
                "thinking": "\n".join(thinking_parts) if thinking_parts else "",
                "tool_calls": tool_calls,
                "files_changed": [],
                "iterations": iterations,
                "duration_ms": round(duration),
            }
            if errors:
                result["errors"] = errors
        except Exception as e:
            result = {"ok": False, "error": str(e)}
            exit_code = 1

    # Output
    if output_format == "stream-json":
        # For streaming, we just output the full result as JSON
        if not quiet:
            sys.stderr.write(json.dumps({"status": "complete"}) + "\n")
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
    else:
        if quiet:
            sys.stdout.write(json.dumps(result) + "\n")
        else:
            sys.stdout.write(json.dumps(result, indent=2) + "\n")

    sys.exit(exit_code)


def cmd_check(model=None):
    """Check if Ollama is available and the model is usable."""
    config = WispConfig()
    if model:
        config.model = model
    client = get_provider(config)
    ok = client.check_health()
    if ok:
        print(success(f"✓ Provider '{config.provider}' is available"))
        if config.provider == "ollama":
            print(success(f"✓ Ollama is running at {config.ollama_url}"))
        print(success(f"✓ Model '{config.model}' is available"))
    else:
        sys.exit(1)


def cmd_models():
    """List all models available in Ollama."""
    config = WispConfig()
    client = get_provider(config)
    try:
        models = client.list_models()
        if not models:
            print(warning("No models found. Pull one with: ollama pull <model>"))
            return
        print(info(f"Available models ({len(models)}):\n"))
        for m in models:
            name = m["name"]
            size_raw = m.get("size", 0)
            size_gb = size_raw / 1e9 if size_raw and size_raw > 0 else 0
            modified = m.get("modified_at", "")[:10]
            size_str = f"{size_gb:6.1f}GB" if size_gb > 0.01 else "   cloud"
            print(f"  {accent(name):30s}  {size_str}  modified {modified}")
    except Exception as e:
        print(error(f"✗ Could not list models: {e}"))
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
            print(dim("No facts stored in memory."))
            print(dim("  Use: wisp memory add \"<fact>\""))
            print(dim("  Or the LLM can use the `remember` tool during conversations."))
            return

        if facts:
            print(info("Global facts:"))
            for f in facts:
                content = f["content"] if isinstance(f, dict) else f
                print(f"  • {content}")
            print()

        for ws_path, ws_fs in ws_facts.items():
            print(info(f"Workspace ({ws_path}):"))
            for f in ws_fs:
                content = f["content"] if isinstance(f, dict) else f
                print(f"  • {content}")
            print()

        print(dim(f"Total: {len(facts) + sum(len(v) for v in ws_facts.values())} fact(s)"))
        return

    sub = args[0]

    if sub == "add" and len(args) >= 2:
        fact = " ".join(args[1:])
        if add_fact(fact):
            print(success(f"✓ Added: {fact}"))
        else:
            print(warning("(Already exists or at capacity)"))

    elif sub == "remove" and len(args) >= 2:
        fact = " ".join(args[1:])
        if remove_fact(fact):
            print(success(f"✓ Removed: {fact}"))
        else:
            print(error(f"✗ Not found: {fact}"))

    elif sub == "clear":
        clear_memory()
        print(success("✓ Memory cleared."))

    elif sub == "summaries":
        _cmd_memory_summaries(args[1:])

    elif sub == "list":
        facts = list_facts()
        if facts:
            print(info("Facts:"))
            for f in facts:
                content = f["content"] if isinstance(f, dict) else f
                print(f"  • {content}")
        else:
            print(dim("No facts stored."))

    else:
        print(info("Usage:"))
        print(dim("  wisp memory                    List all facts"))
        print(dim("  wisp memory add \"<fact>\"       Add a fact"))
        print(dim("  wisp memory remove \"<fact>\"    Remove a fact"))
        print(dim("  wisp memory list               List global facts"))
        print(dim("  wisp memory clear              Clear all facts"))
        print(dim("  wisp memory summaries          List session summaries"))
        print(dim("  wisp memory summaries --show <id>  Show full summary"))
        print(dim("  wisp memory summaries --clear  Clear session summaries"))
        print(dim("  wisp memory summaries --stats  Show summary stats"))


def _cmd_memory_summaries(args: list[str]):
    """Handle agent memory (session summaries) subcommands."""
    from wisp.agent_memory import AgentMemory
    import json

    mem = AgentMemory()

    # Parse flags
    show_id = None
    do_clear = False
    do_stats = False
    i = 0
    while i < len(args):
        if args[i] in ("--show", "-s") and i + 1 < len(args):
            show_id = args[i + 1]
            i += 2
        elif args[i] in ("--clear", "-c"):
            do_clear = True
            i += 1
        elif args[i] in ("--stats", "-S"):
            do_stats = True
            i += 1
        else:
            i += 1

    if do_clear:
        mem.clear()
        print("✓ Agent memory (session summaries) cleared.")
        return

    if do_stats:
        summaries = mem.load_all()
        print(f"Total summaries: {len(summaries)}")
        if summaries:
            print(f"Oldest: {summaries[0].timestamp[:19]}")
            print(f"Newest: {summaries[-1].timestamp[:19]}")
        return

    if show_id:
        summaries = mem.load_all()
        for s in summaries:
            if s.session_id.startswith(show_id):
                print(json.dumps(s.to_dict(), indent=2, ensure_ascii=False))
                return
        print(f"✗ Session '{show_id}' not found.")
        return

    # Default: list
    summaries = mem.load_all()
    if not summaries:
        print("No session summaries stored.")
        print("  Summaries are generated automatically when a session ends.")
        return

    print(f"{'Session ID':<30} {'Date':<12} {'Workspace':<30} {'Summary'}")
    print("-" * 120)
    for s in summaries:
        ws = s.workspace[:28]
        sm = s.summary[:50] + "..." if len(s.summary) > 50 else s.summary
        print(f"{s.session_id:<30} {s.timestamp[:10]:<12} {ws:<30} {sm}")


# ── MCP server commands ──────────────────────────────────────────────

def cmd_mcp(args: list[str]):
    """Manage MCP server configurations."""
    if not args or args[0] == "list":
        from wisp.mcp import discover_mcp_configs
        configs = discover_mcp_configs(".")
        if configs:
            print(info("Configured MCP servers:"))
            for c in configs:
                status = success("✓") if not c.disabled else error("✗")
                source = c.command or c.url or "(unknown)"
                print(f"  {status} {accent(c.name):20s} {dim(source)}")
        else:
            print(dim("No MCP servers configured."))
            print(dim("  Add a server: wisp mcp add <name> <command> [args...]"))
            print(dim("  Add VS Code:  wisp mcp add-vscode"))
        return

    sub = args[0]

    if sub == "add-vscode":
        _setup_vscode_mcp()
    elif sub == "add" and len(args) >= 3:
        _add_mcp_server(args[1], args[2:])
    else:
        print("Usage:")
        print("  wisp mcp                      List MCP servers")
        print("  wisp mcp add-vscode           Add VS Code MCP server")
        print("  wisp mcp add <name> <cmd>     Add a custom MCP server")


def _setup_vscode_mcp():
    """Configure VS Code MCP server in .wisp/mcp.json."""
    import json
    from pathlib import Path

    ws_mcp_dir = Path(".wisp")
    ws_mcp_dir.mkdir(exist_ok=True)
    config_file = ws_mcp_dir / "mcp.json"

    vscode_config = {
        "name": "vscode",
        "command": sys.executable or "python",
        "args": ["-m", "wisp.mcp_servers.vscode_server"],
        "description": "VS Code editor integration - open files, run commands, show messages",
    }

    if config_file.exists():
        try:
            configs = json.loads(config_file.read_text())
            if isinstance(configs, dict):
                configs = configs.get("mcpServers", configs.get("servers", []))
        except (json.JSONDecodeError, OSError):
            configs = []
    else:
        configs = []

    # Check if vscode already exists
    for c in configs:
        if c.get("name") == "vscode":
            print(success("✓ VS Code MCP server already configured."))
            return

    configs.append(vscode_config)
    config_file.write_text(json.dumps(configs, indent=2) + "\n")
    print(success(f"✓ VS Code MCP server added to {config_file}"))
    print()
    print(info("  Tools available:"))
    print(dim("    vscode_open_file     - Open a file at a specific line"))
    print(dim("    vscode_run_command   - Run any VS Code command"))
    print(dim("    vscode_show_message  - Show a notification in VS Code"))
    print(dim("    vscode_get_editor_state - Get current editor state"))
    print()
    print(dim("  Next time you run wisp, these tools will be available to the LLM."))


def _add_mcp_server(name: str, cmd_args: list[str]):
    """Add a custom MCP server to .wisp/mcp.json."""
    import json
    from pathlib import Path

    ws_mcp_dir = Path(".wisp")
    ws_mcp_dir.mkdir(exist_ok=True)
    config_file = ws_mcp_dir / "mcp.json"

    config = {
        "name": name,
        "command": cmd_args[0],
        "args": cmd_args[1:],
    }

    if config_file.exists():
        try:
            configs = json.loads(config_file.read_text())
            if isinstance(configs, dict):
                configs = configs.get("mcpServers", configs.get("servers", []))
        except (json.JSONDecodeError, OSError):
            configs = []
    else:
        configs = []

    configs.append(config)
    config_file.write_text(json.dumps(configs, indent=2) + "\n")
    print(success(f"✓ MCP server '{name}' added to {config_file}"))


# ── Git commands ──────────────────────────────────────────────────────

def cmd_git(args: list[str]):
    """Show git context for the workspace."""
    from wisp.git_context import format_git_context, get_git_state

    if not args or args[0] in ("status", "st"):
        ctx = format_git_context(".")
        if ctx:
            print(ctx)
        else:
            print(dim("Not a git repository."))
        return

    if args[0] in ("diff", "d"):
        from wisp.git_context import get_workspace_diff
        diff = get_workspace_diff(".")
        if diff:
            print(diff)
        else:
            print(dim("No diff available."))
        return

    if args[0] == "log":
        state = get_git_state(".")
        if state and state.recent_commits:
            for commit in state.recent_commits:
                print(f"  {commit}")
        else:
            print(dim("No commits available."))
        return

    print(info("Usage:"))
    print(dim("  wisp git              Show git status"))
    print(dim("  wisp git status       Show git status"))
    print(dim("  wisp git diff         Show unstaged diff"))
    print(dim("  wisp git log          Show recent commits"))


# ── Plan commands ────────────────────────────────────────────────────

def cmd_plan(args: list[str]):
    """Manage structured plans."""
    from wisp.planner import PlanStore, parse_plan_from_text
    from wisp.progress import format_progress, list_plans

    if not args:
        # Show active plan
        store = PlanStore()
        plan = store.load_active(".")
        if plan:
            print(format_progress(plan))
        else:
            print(dim("No active plan."))
            print(dim("  Create one: wisp plan \"implement feature X\""))
        return

    if args[0] == "list":
        print(list_plans("."))
        return

    if args[0] == "abort":
        store = PlanStore()
        plan = store.load_active(".")
        if plan:
            plan.abort()
            store.save(plan)
            print(success(f"✓ Aborted plan: {plan.id}"))
        else:
            print(error("No active plan to abort."))
        return

    if args[0] == "clear":
        store = PlanStore()
        store.clear()
        print(success("✓ All plans cleared."))
        return

    # Create a plan from the goal string
    goal = " ".join(args)
    print(info(f"Creating plan for: {goal}"))
    print(dim("(Use the REPL with 'plan_task' tool to generate a structured plan)"))

def cmd_progress(args: list[str]):
    """Show current plan progress."""
    from wisp.planner import PlanStore
    from wisp.progress import format_progress

    store = PlanStore()
    plan = store.load_active(".")
    if plan:
        print(format_progress(plan))
    else:
        print(dim("No active plan. Run 'wisp plan' to see plans or create one in the REPL."))


# ── Diagnose commands ────────────────────────────────────────────────

def cmd_diagnose(args: list[str]):
    """Diagnose an error from file or stdin."""
    from wisp.error_diagnosis import diagnose

    if not args:
        print(info("Usage: wisp diagnose <error_message_or_file>"))
        print(dim("  wisp diagnose 'Traceback (most recent call last): ...'"))
        print(dim("  cat error.log | wisp diagnose -"))
        return

    error_text = " ".join(args)
    if error_text == "-":
        import sys
        error_text = sys.stdin.read()

    diag = diagnose(error_text, ".")
    print(diag.format())


# ── Collaborative editing commands ───────────────────────────────────

def cmd_locks(args: list[str]):
    """Show active file locks in the workspace."""
    from wisp.file_lock import FileLock
    fl = FileLock(".")
    locks = fl.list_active_locks()
    if not locks:
        print(dim("No active file locks."))
        return
    print(info(f"Active locks ({len(locks)}):"))
    for lock in locks:
        agent = lock.get("agent", "unknown")
        since = lock.get("since", "?")[:19]
        expires = lock.get("expires", "?")[:19]
        file = lock.get("_file", "?")
        print(f"  {accent(file):<40} {dim(agent):<20} expires {expires}")


def cmd_changes(args: list[str]):
    """Show changes made in this session."""
    from wisp.change_tracker import ChangeTracker
    from wisp.file_lock import FileLock
    fl = FileLock(".")
    ct = ChangeTracker(".", fl.agent_id)
    print(ct.summary())


# ── ACP adapter command ────────────────────────────────────────────────

def cmd_acp(args: list[str]):
    """Run Wisp as an ACP external agent for Zed."""
    from wisp.acp_adapter import main as acp_main
    acp_main(args)


# ── Task commands ─────────────────────────────────────────────────────

def cmd_task(args: list[str]):
    """Manage long-horizon tasks."""
    from wisp.long_horizon.storage import TaskStorage
    from wisp.long_horizon.state import TaskStatus

    storage = TaskStorage()

    if not args or args[0] == "list":
        tasks = storage.list_all()
        if not tasks:
            print(dim("No long-horizon tasks."))
            print(dim("  Create one: wisp task start 'migrate Flask to FastAPI'"))
            return

        print(info(f"Long-horizon tasks ({len(tasks)}):\n"))
        for t in tasks:
            tid = t["task_id"]
            status = t["status"]
            progress = f"{t.get('current_step', 0)}/{t.get('total_steps', 0)}"
            goal = t["goal"][:50]
            status_color = success if status == "completed" else error if status == "failed" else warning if status == "running" else info
            print(f"  {status_color(status):12} {progress:>6}  {goal}")
        return

    sub = args[0]
    rest = args[1:]

    if sub == "status":
        if not rest:
            print(error("✗ Usage: wisp task status <task-id>"))
            return
        task_id = rest[0]
        state = storage.load(task_id)
        if state is None:
            print(error(f"✗ Task not found: {task_id}"))
            return

        current = state.current_step
        status_color = success if state.is_complete else error if state.is_failed else warning if state.status == TaskStatus.RUNNING else info
        print(info(f"Task: {state.task_id}"))
        print(f"  Goal:     {state.goal}")
        print(f"  Status:   {status_color(state.status.value)}")
        print(f"  Progress: {state.current_step_index}/{state.total_steps} ({round(state.progress_pct, 1)}%)")
        print(f"  Completed: {state.completed_count}  Failed: {state.failed_count}")
        print(f"  Plan version: {state.plan_version}")
        if current:
            print(f"  Current step: {current.description}")
        if state.last_checkpoint:
            print(f"  Last checkpoint: {state.last_checkpoint}")
        return

    if sub == "start":
        if not rest:
            print(error("✗ Usage: wisp task start <goal>"))
            return
        goal = " ".join(rest)
        from wisp.tools.long_horizon import tool_run_long_task
        result = tool_run_long_task(goal=goal)
        parsed = json.loads(result)
        print(success(f"✓ {parsed['data']}"))
        return

    if sub == "pause":
        if not rest:
            print(error("✗ Usage: wisp task pause <task-id>"))
            return
        from wisp.tools.long_horizon import tool_pause_task
        result = tool_pause_task(task_id=rest[0])
        parsed = json.loads(result)
        print(info(parsed["data"]))
        return

    if sub == "resume":
        if not rest:
            print(error("✗ Usage: wisp task resume <task-id>"))
            return
        from wisp.tools.long_horizon import tool_resume_task
        result = tool_resume_task(task_id=rest[0])
        parsed = json.loads(result)
        print(info(parsed["data"]))
        return

    if sub == "cancel":
        if not rest:
            print(error("✗ Usage: wisp task cancel <task-id>"))
            return
        from wisp.tools.long_horizon import tool_cancel_task
        result = tool_cancel_task(task_id=rest[0])
        parsed = json.loads(result)
        print(info(parsed["data"]))
        return

    if sub == "cleanup":
        status_filter = rest[0] if rest else "completed"
        older_than = 7
        dry_run = True
        # Parse flags: --force, --days N
        i = 1 if rest else 0
        while i < len(rest):
            if rest[i] == "--force":
                dry_run = False
            elif rest[i] == "--days" and i + 1 < len(rest):
                try:
                    older_than = int(rest[i + 1])
                    i += 1
                except ValueError:
                    pass
            i += 1
        from wisp.tools.long_horizon import tool_cleanup_tasks
        result = tool_cleanup_tasks(
            status_filter=status_filter,
            older_than_days=older_than,
            dry_run=dry_run,
        )
        parsed = json.loads(result)
        print(info(parsed["data"]))
        if dry_run and parsed["metadata"]["removed"] > 0:
            print(dim(f"\n  Run with --force to actually delete."))
        return

    if sub == "output":
        if not rest:
            print(error("✗ Usage: wisp task output <task-id>"))
            return
        from wisp.tools.long_horizon import tool_task_output
        result = tool_task_output(task_id=rest[0])
        parsed = json.loads(result)
        if parsed["status"] == "ok":
            print(info(parsed["data"]))
        else:
            print(error(parsed["data"]))
        return

    print(error(f"✗ Unknown task subcommand: {sub}"))
    print(dim("  Try: list, status <id>, start <goal>, pause <id>, resume <id>, cancel <id>, output <id>, cleanup [status] [--force] [--days N]"))


# ── Session commands ─────────────────────────────────────────────────

def cmd_session_list():
    """List all saved sessions."""
    mgr = SessionManager()
    sessions = mgr.list_sessions()
    if not sessions:
        print(dim("No saved sessions."))
        print(dim("Run 'wisp \"your prompt\"' to start a new session."))
        return

    print(info(f"Saved sessions ({len(sessions)}):\n"))
    for s in sessions:
        # Show title or first prompt
        title = (s["title"] or "(untitled)")[:60]
        created = s["created_at"][:19] if s["created_at"] else "?"
        updated = s["updated_at"][:19] if s["updated_at"] else "?"
        print(f"  {accent(s['id'])}")
        print(f"    {dim('Title:')}    {title}")
        print(f"    {dim('Model:')}    {s['model']}")
        print(f"    {dim('Started:')}  {created}")
        print(f"    {dim('Updated:')}  {updated}")
        print(f"    {dim('Messages:')} {s['msg_count']}")
        if s.get("task_count"):
            print(f"    {dim('Tasks:')}    {s['task_count']}")
        print(f"    {dim('Continue:')} wisp -S {s['id']} \"your next question\"")
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
        print(error(f"✗ Session '{session_id}' not found."))
        print(dim("  Run 'wisp session list' to see available sessions."))
        return

    print(format_session_preview(session))
    print()

    # Show associated tasks
    if session.task_ids:
        from wisp.long_horizon.storage import TaskStorage
        storage = TaskStorage()
        print(info(f"Associated tasks ({len(session.task_ids)}):"))
        for tid in session.task_ids:
            t = storage.load(tid)
            if t:
                status_color = success if t.is_complete else error if t.is_failed else warning if t.status.value == "running" else info
                print(f"  {status_color(t.status.value):12} {t.progress_pct:5.1f}%  {t.goal[:50]}")
            else:
                print(f"  {dim('(missing)')}      {tid}")
        print()

    print(dim(f"  Continue: wisp -S {session.id} \"your next question\""))


def cmd_session_delete(session_id: str):
    """Delete a session."""
    mgr = SessionManager()
    session = _resolve_session_or_fragment(mgr, session_id)
    if session is None:
        print(error(f"✗ Session '{session_id}' not found."))
        print(dim("  Run 'wisp session list' to see available sessions."))
        return
    mgr.delete(session.id)
    print(success(f"✓ Deleted session {session.id}"))


def cmd_session_compact(session_id: str, keep: int = 6):
    """Compact a session by summarizing old messages and keeping recent ones."""
    mgr = SessionManager()
    session = _resolve_session_or_fragment(mgr, session_id)
    if session is None:
        print(error(f"✗ Session '{session_id}' not found."))
        return

    if len(session.messages) <= keep:
        print(dim(f"Session only has {len(session.messages)} message(s), nothing to compact."))
        return

    print(info(f"Compacting session {session.id} ({len(session.messages)} messages, keeping last {keep})..."))
    result = session.compact(keep_recent=keep)

    if result.get("compacted"):
        mgr.save(session)
        saved = result["before_count"] - result["after_count"]
        print(success(f"✓ Compacted: {result['before_count']} → {result['after_count']} messages ({saved} removed)"))
        if result.get("summary"):
            print(dim(f"  Summary: {result['summary'][:120]}..."))
    else:
        print(dim("Compaction skipped: not enough messages to summarize."))


def cmd_session_trim(session_id: str, keep: int = 10):
    """Trim a session to the last N exchanges (for context window management).

    Trims intelligently by counting complete user turns (user → assistant exchanges),
    preserving the last N turns regardless of how many tool messages they contain.
    """
    if keep < 1:
        print(error(f"✗ keep must be at least 1, got {keep}"))
        return

    mgr = SessionManager()
    session = _resolve_session_or_fragment(mgr, session_id)
    if session is None:
        print(error(f"✗ Session '{session_id}' not found."))
        return

    # Count complete user turns
    user_msg_indices = [i for i, m in enumerate(session.messages) if m.get("role") == "user"]
    if len(user_msg_indices) <= keep:
        print(dim(f"Session only has {len(user_msg_indices)} turn(s), nothing to trim."))
        return

    original = len(session.messages)
    # Find the start index of the (last N)th user turn
    # We keep everything from that user message onwards
    keep_from = user_msg_indices[-keep]
    session.messages = session.messages[keep_from:]
    mgr.save(session)
    print(success(f"✓ Trimmed session {session.id}: {original} → {len(session.messages)} messages "
          f"({keep} turn(s) preserved)"))


# ── Help ─────────────────────────────────────────────────────────────

_SHORT_HELP = """Usage: wisp [options] 'prompt'
   or:  wisp <subcommand> [args]

Options:
  --model, -m <name>       Ollama model to use (default: deepseek-v4-pro:cloud)
  --skill, -s <name>       Load a skill to guide the agent
  --session, -S <id>       Continue an existing session
  --workspace, -w <dir>    Working directory (default: current dir)
  --auto-approve, -y       Skip approval prompts for tool calls
  --show-thinking, -T      Show reasoning trace inline
  --print <prompt>         Headless mode: run prompt, print JSON result, exit
  --output-format <fmt>    Output format for --print: json | stream-json (default: json)
  --quiet                  Suppress all output except final result
  --long-task              Force long-horizon task mode for this prompt
  --version                Show version

Subcommands:
  run <prompt>             Single-shot agent with a prompt
  repl                     Interactive REPL mode (continuous chat)
  tui                      Full-screen terminal app (experimental)
  server                   Start cloud server for remote clients
  session list             List all saved sessions
  session show <id>        Show session details and recent messages
  session delete <id>      Delete a session
  session trim <id> [n]    Trim session to last N exchanges (default: 10)
  session compact <id> [n] Summarize old messages, keep last N (default: 6)
  task list                List all long-horizon tasks
  task status <id>         Show task progress and current step
  task start <goal>        Start a new long-horizon task
  task pause <id>          Pause a running task
  task resume <id>         Resume a paused/crashed task
  task cancel <id>         Cancel a task
  skills                   List discovered skills
  config [--set k=v]       View or set configuration
  check                    Verify Ollama connectivity
  models                   List available Ollama models
  swarm 'goal'             Spawn multi-agent swarm to accomplish a goal
  agents list              List available agent roles
  agents status            Show running swarm agent status

Examples:
  wisp 'add error handling to main.py'
  wisp repl                  # Start interactive session
  wisp repl -S mysession     # Continue session in REPL
  wisp -S mysession 'next'   # Continue session in single-shot
  wisp --model kimi-k2.5:cloud 'refactor the auth module'
  wisp --skill code-review 'review the latest changes'
  wisp --print "refactor the auth module" --output-format json > result.json
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

    _SUBCOMMANDS = {"run", "repl", "tui", "skills", "config", "check", "models", "session", "memory", "mcp", "git", "plan", "progress", "diagnose", "locks", "changes", "acp", "server", "compact", "swarm", "agents", "task"}
    first = argv[0]

    # Global flags
    flags_model = None
    flags_skill = None
    flags_session = None
    flags_workspace = None
    flags_auto = False
    flags_show_thinking = False
    flags_print = None
    flags_output_format = "json"
    flags_quiet = False
    flags_long_task = False

    def extract_global_flags(args):
        """Extract global flags from args list, return remaining args."""
        nonlocal flags_model, flags_skill, flags_session, flags_workspace, flags_auto, flags_show_thinking
        nonlocal flags_print, flags_output_format, flags_quiet, flags_long_task
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
            elif a == "--print" and i + 1 < len(args):
                flags_print = args[i + 1]
                i += 2
            elif a == "--output-format" and i + 1 < len(args):
                flags_output_format = args[i + 1].lower()
                i += 2
            elif a == "--quiet":
                flags_quiet = True
                i += 1
            elif a == "--long-task":
                flags_long_task = True
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
            cmd_run(" ".join(rest), flags_model, flags_skill, flags_workspace, flags_auto, flags_session, flags_show_thinking, flags_long_task)

        elif first == "repl":
            cmd_repl(flags_model, flags_skill, flags_workspace, flags_session, flags_show_thinking, flags_auto, flags_long_task)

        elif first == "tui":
            cmd_tui(flags_model, flags_workspace, flags_show_thinking, flags_auto)

        elif first == "session":
            if not rest:
                print(info("Usage: wisp session (list|show|delete|trim|compact) [args]"))
                print(dim("  wisp session list              List all sessions"))
                print(dim("  wisp session show <id>         Show session details"))
                print(dim("  wisp session delete <id>       Delete a session"))
                print(dim("  wisp session trim <id> [n]     Trim to last N exchanges"))
                print(dim("  wisp session compact <id> [n]  Compact old messages, keep last N"))
                return

            sub = rest[0]
            args = rest[1:]

            if sub == "list":
                cmd_session_list()
            elif sub == "show":
                if not args:
                    print(error("✗ Usage: wisp session show <id>"))
                    return
                cmd_session_show(args[0])
            elif sub == "delete":
                if not args:
                    print(error("✗ Usage: wisp session delete <id>"))
                    return
                cmd_session_delete(args[0])
            elif sub == "trim":
                if not args:
                    print(error("✗ Usage: wisp session trim <id> [n]"))
                    return
                keep = int(args[1]) if len(args) > 1 else 10
                cmd_session_trim(args[0], keep)
            elif sub == "compact":
                if not args:
                    print(error("✗ Usage: wisp session compact <id> [n]"))
                    return
                keep = int(args[1]) if len(args) > 1 else 6
                cmd_session_compact(args[0], keep)
            else:
                print(error(f"✗ Unknown session subcommand: {sub}"))
                print(dim("  Try: list, show <id>, delete <id>, trim <id> [n], compact <id> [n]"))

        elif first == "compact":
            if not rest:
                print(error("✗ Usage: wisp compact <session-id> [keep-n]"))
                print(dim("  wisp compact 20260430-123456-abcdef 6"))
                return
            keep = int(rest[1]) if len(rest) > 1 else 6
            cmd_session_compact(rest[0], keep)

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
            cmd_check(flags_model)
        elif first == "models":
            cmd_models()
        elif first == "memory":
            cmd_memory(rest)
        elif first == "mcp":
            cmd_mcp(rest)
        elif first == "git":
            cmd_git(rest)
        elif first == "plan":
            cmd_plan(rest)
        elif first == "progress":
            cmd_progress(rest)
        elif first == "diagnose":
            cmd_diagnose(rest)
        elif first == "locks":
            cmd_locks(rest)
        elif first == "changes":
            cmd_changes(rest)
        elif first == "acp":
            cmd_acp(rest)

        elif first == "task":
            cmd_task(rest)

        elif first == "server":
            host = "0.0.0.0"
            port = 8000
            no_auth = False
            i = 0
            while i < len(rest):
                if rest[i] == "--host" and i + 1 < len(rest):
                    host = rest[i + 1]
                    i += 2
                elif rest[i] == "--port" and i + 1 < len(rest):
                    port = int(rest[i + 1])
                    i += 2
                elif rest[i] == "--no-auth":
                    no_auth = True
                    i += 1
                else:
                    i += 1
            cmd_server(host=host, port=port, no_auth=no_auth)

        elif first == "swarm":
            if not rest:
                print(error("✗ Usage: wisp swarm 'goal' [--roles coder,reviewer,tester] [--max-parallel N]"))
                print(dim("  wisp swarm 'implement user auth' --roles coder,reviewer,tester"))
                return

            goal = rest[0]
            roles = None
            max_parallel = 3
            i = 1
            while i < len(rest):
                if rest[i] == "--roles" and i + 1 < len(rest):
                    roles = [r.strip() for r in rest[i + 1].split(",")]
                    i += 2
                elif rest[i] == "--max-parallel" and i + 1 < len(rest):
                    max_parallel = int(rest[i + 1])
                    i += 2
                else:
                    i += 1

            from wisp.multi_agent.cli import cmd_swarm
            cmd_swarm(goal, roles=roles, model=flags_model, workspace=flags_workspace, max_parallel=max_parallel)

        elif first == "agents":
            from wisp.multi_agent.cli import cmd_agents_list, cmd_agents_status
            if not rest:
                cmd_agents_list()
                return
            sub = rest[0]
            if sub == "list":
                cmd_agents_list()
            elif sub == "status":
                cmd_agents_status()
            else:
                print(error(f"✗ Unknown agents subcommand: {sub}"))
                print(dim("  Try: list, status"))

    else:
        # Implicit mode: wisp [flags] 'prompt'  OR  wisp --print "prompt"
        rest = extract_global_flags(argv)

        # If --print is set, use it as the prompt for headless mode
        if flags_print is not None:
            if flags_quiet:
                _setup_logging(verbose=False)
            cmd_print(
                prompt=flags_print,
                model=flags_model,
                session_id=flags_session,
                output_format=flags_output_format,
                quiet=flags_quiet,
            )
            return

        if not rest:
            print(error("✗ Please provide a prompt."))
            print_help()
            return
        cmd_run(" ".join(rest), flags_model, flags_skill, flags_workspace, flags_auto, flags_session, flags_show_thinking)


if __name__ == "__main__":
    main()
