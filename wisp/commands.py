"""Slash commands for Wisp REPL — local directives that bypass the LLM.

Commands are registered via the @register decorator and dispatched by name.
They receive the WispAgent instance and can mutate its state directly.
"""

import logging
import os
import subprocess
import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from wisp.colors import success, error, warning, info, dim, accent
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
        cmd.handler(agent, args.strip())
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


@register("model", "Switch Ollama model", aliases=("m",), usage="/model [name|number]")
def cmd_model(agent, args: str):
    # Fetch available models from Ollama
    models: list[dict] = []
    if hasattr(agent, "client") and agent.client:
        try:
            models = agent.client.list_models()
        except Exception as e:
            logger.warning("Failed to list models: %s", e)

    model_names = [m.get("name", "") for m in models if m.get("name")]

    # Helper: strip :cloud suffix for display (all are cloud models)
    def _display_name(name: str) -> str:
        return name.removesuffix(":cloud")

    # Build display name -> full name map for resolution
    display_map = {_display_name(n): n for n in model_names}

    if not args:
        # Show current model + numbered list
        print(f"Current model: {accent(_display_name(agent.config.model))} {dim('(cloud)')}")
        if not model_names:
            print(dim("  (Could not fetch model list from Ollama)"))
            return
        print(info(f"\nAvailable models ({len(model_names)}):"))
        for i, name in enumerate(model_names, 1):
            display = _display_name(name)
            marker = accent("→") if name == agent.config.model else " "
            print(f"  {marker} {i:2}. {display} {dim('(cloud)')}")
        print(dim("\nType /model <number> or /model <name> to switch."))
        return

    arg = args.strip()

    # Try numeric selection first
    if arg.isdigit():
        idx = int(arg) - 1
        if 0 <= idx < len(model_names):
            new_model = model_names[idx]
        else:
            print(error(f"✗ Invalid model number: {arg}. Use /model to see the list."))
            return
    else:
        # Name-based selection
        # 1. Exact match on full name
        exact = [n for n in model_names if n == arg]
        if exact:
            new_model = exact[0]
        # 2. Exact match on display name (without :cloud)
        elif arg in display_map:
            new_model = display_map[arg]
            print(dim(f"  (resolved to {new_model})"))
        # 3. Prefix match on full name
        else:
            prefixes = [n for n in model_names if n.startswith(arg)]
            if len(prefixes) == 1:
                new_model = prefixes[0]
                print(dim(f"  (resolved to {new_model})"))
            elif len(prefixes) > 1:
                print(warning(f"⚠ Ambiguous prefix '{arg}'. Matches:"))
                for p in prefixes:
                    print(f"    - {_display_name(p)} {dim('(cloud)')}")
                return
            else:
                # 4. Prefix match on display name
                disp_prefixes = [n for n in model_names if _display_name(n).startswith(arg)]
                if len(disp_prefixes) == 1:
                    new_model = disp_prefixes[0]
                    print(dim(f"  (resolved to {new_model})"))
                elif len(disp_prefixes) > 1:
                    print(warning(f"⚠ Ambiguous prefix '{arg}'. Matches:"))
                    for p in disp_prefixes:
                        print(f"    - {_display_name(p)} {dim('(cloud)')}")
                    return
                else:
                    print(warning(f"⚠ Model '{arg}' not found in Ollama. It may need to be pulled."))
                    return

    # Apply the switch
    agent.config.model = new_model
    if hasattr(agent, "client") and agent.client:
        agent.client.model = new_model
        # Re-detect context window for the new model
        if not agent.config._context_tokens_explicit:
            try:
                detected = agent.client.get_context_length()
                if detected != agent.config.max_context_tokens:
                    logger.info(
                        "Auto-detected context window for %s: %d tokens",
                        new_model, detected,
                    )
                    agent.config.max_context_tokens = detected
            except Exception:
                pass
    if hasattr(agent, "_system_prompt_cache"):
        agent._system_prompt_cache.clear()
    print(success(f"✓ Model set to: {_display_name(new_model)} {dim('(cloud)')}"))


@register("skill", "Load or list skills", aliases=("s",), usage="/skill [name]")
def cmd_skill(agent, args: str):
    from wisp.skills import discover_skills, find_skill

    ws = agent.config.workspace or "."
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
    if agent.session is None:
        print(dim("No active session."))
        return
    active_skill = getattr(agent, "_active_skill", None)
    print(info("Session info:"))
    print(f"  {dim('Session ID:')}    {agent.session.id}")
    print(f"  {dim('Title:')}         {agent.session.title or '(untitled)'}")
    print(f"  {dim('Model:')}         {agent.config.model}")
    print(f"  {dim('Workspace:')}     {agent.config.workspace or '.'}")
    print(f"  {dim('Active skill:')}  {active_skill or '(none)'}")
    print(f"  {dim('Messages:')}      {len(agent.messages)}")
    print(f"  {dim('Auto-approve:')}  {agent.config.auto_approve}")
    print(f"  {dim('Show thinking:')} {agent.config.show_thinking}")


@register("save", "Force-save the current session", usage="/save")
def cmd_save(agent, args: str):
    agent._save_session()
    if agent.session:
        print(success(f"✓ Session saved: {agent.session.id}"))
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
    snap = agent.metrics.snapshot(chars_per_token=agent.config.chars_per_token)
    print(info("📊 Agent Metrics"))
    print(f"  {dim('Turns:')}           {snap['turns']}")
    print(f"  {dim('Tokens:')}          {snap['total_tokens']:,} "
          f"(prompt {snap['prompt_tokens']:,} + completion {snap['completion_tokens']:,})")
    print(f"  {dim('Avg latency:')}     {snap['avg_latency_ms']:.0f} ms")
    print(f"  {dim('Tool calls:')}      {snap['tool_calls']} "
          f"({snap['tool_errors']} errors, {snap['tool_success_rate']:.0f}% success)")
    if snap['avg_tool_duration_ms']:
        print(f"  {dim('Tool latencies:')}")
        for name, dur in sorted(snap['avg_tool_duration_ms'].items()):
            print(f"    {dim(name + ':')} {dur:.0f} ms")
    if snap['compactions']:
        print(f"  {dim('Compactions:')}     {snap['compactions']}")
    if snap['interruptions']:
        print(f"  {dim('Interruptions:')}   {snap['interruptions']}")
    if snap['tool_blocks']:
        print(f"  {dim('Blocks:')}          {snap['tool_blocks']}")


@register("circuit", "Show circuit breaker status", usage="/circuit [tool_name|reset]")
def cmd_circuit(agent, args: str):
    cb = agent.circuit_breaker
    if not cb._states:
        print(dim("No circuit breaker state (no tools have been called yet)."))
        return

    parts = args.split()
    if parts and parts[0] == "reset":
        reset_name = parts[1] if len(parts) > 1 else ""
        cb.reset(reset_name)
        if reset_name:
            print(success(f"✓ Circuit breaker for '{reset_name}' reset."))
        else:
            print(success("✓ All circuit breakers reset."))
        return

    open_tools = []
    half_tools = []
    closed_tools = []
    for name, state in cb._states.items():
        if state.state == "OPEN":
            open_tools.append(name)
        elif state.state == "HALF_OPEN":
            half_tools.append(name)
        else:
            closed_tools.append(name)

    if open_tools:
        print(error(f"⚠ OPEN: {', '.join(open_tools)} — blocked until recovery timeout"))
    if half_tools:
        print(warning(f"⚡ HALF_OPEN: {', '.join(half_tools)} — one probe allowed"))
    if closed_tools:
        print(success(f"✓ CLOSED: {', '.join(closed_tools)}"))
    if not (open_tools or half_tools or closed_tools):
        print(dim("All circuits healthy."))


@register("compact", "Compact session history to save context", aliases=("c",), usage="/compact")
def cmd_compact(agent, args: str):
    if agent.session is None:
        print(warning("⚠ No active session to compact."))
        return
    if len(agent.messages) <= agent.config.compact_keep_recent:
        print(dim(f"Session has only {len(agent.messages)} messages — not enough to compact."))
        return

    print(info(f"Compacting session ({len(agent.messages)} messages, keeping last {agent.config.compact_keep_recent})..."))
    result = agent.session.compact(
        keep_recent=agent.config.compact_keep_recent,
        chars_per_token=agent.config.chars_per_token,
    )

    if result.get("compacted"):
        agent.messages = list(agent.session.messages)
        saved = result["before_count"] - result["after_count"]
        print(success(f"✓ Compacted: {result['before_count']} → {result['after_count']} messages ({saved} removed)"))
        if result.get("summary"):
            print(dim(f"  Summary: {result['summary'][:120]}..."))
    else:
        print(dim("Compaction skipped: not enough messages to summarize."))


@register("approve", "Toggle auto-approve for tool calls", aliases=("y",), usage="/approve")
def cmd_approve(agent, args: str):
    agent.config.auto_approve = not agent.config.auto_approve
    state = "ON" if agent.config.auto_approve else "OFF"
    print(success(f"✓ Auto-approve: {state}"))


@register("thinking", "Toggle reasoning trace display", aliases=("T",), usage="/thinking")
def cmd_thinking(agent, args: str):
    agent.config.show_thinking = not agent.config.show_thinking
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
    agent.config.workspace = str(path.resolve())
    # Invalidate system prompt cache because skill discovery is workspace-relative
    if hasattr(agent, "_system_prompt_cache"):
        agent._system_prompt_cache.clear()
    print(success(f"✓ Workspace: {agent.config.workspace}"))


@register("grep", "Search files with grep", aliases=("g", "search"), usage="/grep <pattern> [path]")
def cmd_grep(agent, args: str):
    if not args:
        print(info("Usage: /grep <pattern> [path]"))
        return
    parts = args.split(maxsplit=1)
    pattern = parts[0]
    target = parts[1] if len(parts) > 1 else "."
    ws = agent.config.workspace or "."
    target_path = Path(ws) / target
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


@register("spawn", "Spawn a subagent for a scoped task", aliases=("sub", "delegate"), usage="/spawn <task description>")
def cmd_spawn(agent, args: str):
    if not args:
        print(info("Usage: /spawn <task description>"))
        print(dim("Example: /spawn research the best Python HTTP client library"))
        return
    from wisp.multi_agent import SubagentOrchestrator, SubagentContract
    contract = SubagentContract(
        task=args,
        timeout_seconds=120,
        max_iterations=15,
    )
    orch = SubagentOrchestrator(parent_agent=agent)
    print(accent(f"🧬 Spawning subagent: {args[:60]}..."))
    result = asyncio.run(orch.run(contract))
    status = success("✓") if result.success else error("✗")
    if result.timed_out:
        status = warning("⏱")
    print(f"\n{status} Subagent done ({result.elapsed_seconds:.1f}s, {result.iterations_used} iterations)")
    print("─" * 40)
    print(result.output)


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

    # Lazy import to avoid circular dependencies
    from wisp.multi_agent.orchestrator import SwarmOrchestrator
    from wisp.multi_agent.roles import AgentRole

    roles = [AgentRole.CODER, AgentRole.REVIEWER, AgentRole.TESTER, AgentRole.RESEARCHER]
    config = agent.config

    print(info(f"🐝 Starting swarm with {len(roles)} agent(s)..."))
    print(dim(f"   Goal: {args}"))
    print(dim(f"   Roles: {', '.join(roles)}"))
    print()

    orch = SwarmOrchestrator(config, parent_agent=agent)
    try:
        result = orch.run(args, roles=roles, progress_callback=_swarm_progress)
    except KeyboardInterrupt:
        print(warning("\n⚠ Interrupted. Stopping all agents..."))
        orch.stop_all()
        raise

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
        system = agent._build_system_prompt()
        # Synthesize on a temporary copy of messages to avoid mutating live state
        saved_messages = agent.messages
        try:
            agent.messages = list(saved_messages)
            agent.messages.append({"role": "user", "content": prompt})
            response = agent._run_turn_streaming(system)
        finally:
            agent.messages = saved_messages
        content = response.get("message", {}).get("content", "") if isinstance(response.get("message"), dict) else ""
        return content.strip() or result.final_output
    except Exception:
        return result.final_output


@register("new", "Start a new session", aliases=(), usage="/new")
def cmd_new(agent, args: str):
    from wisp.session import Session
    agent._save_session()
    agent.session = Session.create(
        model=agent.config.model,
        workspace=agent.config.workspace or ".",
        first_prompt="New session",
    )
    agent.messages = []
    print(success(f"✓ New session started: {agent.session.id}"))


@register("continue", "Continue the assistant's previous response", aliases=("c", "go", "on"), usage="/continue")
def cmd_continue(agent, args: str):
    """Explicitly continue from the last assistant message.

    Builds an expanded continuation prompt using the same logic as the
    automatic _expand_continuation hook, but shows the user what context
    is being resumed before sending it.
    """
    if not agent.messages:
        print(warning("⚠ No conversation history to continue from."))
        return

    expanded = agent._expand_continuation("continue")

    # If expansion did nothing useful, warn and bail
    if expanded == "continue":
        print(warning("⚠ No previous assistant message found to continue from."))
        return

    # Show the user what we're continuing from (first line only for brevity)
    context_preview = expanded.split("\n")[-1] if "\n" in expanded else expanded
    if context_preview.startswith("[Context:"):
        print(info(f"⏩ Continuing… {context_preview[:100]}"))
    else:
        print(info("⏩ Continuing previous response…"))

    agent._add_message("user", expanded)
    system = agent._build_system_prompt()
    ws = agent.config.workspace or "."
    agent._execute_loop(system, ws, agent.config.auto_approve)


@register("exit", "Exit Wisp", aliases=("quit", "q", "bye"), usage="/exit")
def cmd_exit(agent, args: str):
    raise ExitREPL


# ── /task: Long-horizon task management ──────────────────────────────

@register("task", "Create or manage a long-horizon task", aliases=(), usage="/task <goal> | /task status <id>")
def cmd_task(agent, args: str):
    """Create a long-horizon task or check task status."""
    if not args:
        print(error("✗ Usage: /task <goal>  or  /task status <task-id>"))
        return

    if args.startswith("status "):
        task_id = args[7:].strip()
        if task_id:
            from wisp.tools.long_horizon import tool_task_status
            import json
            result = tool_task_status(task_id=task_id)
            parsed = json.loads(result)
            if parsed["status"] == "ok":
                print(info(parsed["data"]))
            else:
                print(error(parsed["data"]))
        else:
            print(error("✗ Usage: /task status <task-id>"))
        return

    # Default: create and start a background task
    goal = args
    ws = agent.config.workspace or "."

    import asyncio
    async def _start():
        async for event in agent.run_long_task(goal=goal, workspace=ws, background=True):
            if event.type == "task_started":
                print(success(f"🎯 Task started: {event.data.get('task_id', '')}"))
                print(info(f"   Goal: {event.data.get('goal', '')[:60]}"))
                print(dim(f"   Use `/task status <id>` to check progress."))
            elif event.type == "system":
                print(info(f"   {event.data.get('message', '')}"))
    asyncio.run(_start())


@register("tasks", "List all long-horizon tasks", aliases=(), usage="/tasks")
def cmd_tasks(agent, args: str):
    """List all long-horizon tasks with their statuses."""
    from wisp.tools.long_horizon import tool_list_tasks
    import json
    result = tool_list_tasks()
    parsed = json.loads(result)
    if parsed["status"] == "ok":
        print(info(parsed["data"]))
    else:
        print(error(parsed["data"]))


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
        print(dim(f"   Run '/init overwrite' to regenerate."))
        return

    print(info(f"🔍 Analyzing {ws.name}…"))

    # ── Gather project metadata ──
    from wisp.project_context import detect_project_context, format_context
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
    lang_list = sorted(index.languages) if index.languages else [ctx.language or "Unknown"]

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
