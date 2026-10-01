"""Agent lifecycle commands: /approve, /thinking, /spawn, /agents, /swarm.
Split from wisp/commands.py (back-compat shim)."""

import logging

from wisp.colors import success, error, warning, info, dim, accent
from wisp.repl.commands import register

logger = logging.getLogger(__name__)


@register("approve", "Toggle auto-approve for tool calls", aliases=("y",), usage="/approve")
def cmd_approve(agent, args: str):
    agent.config = agent.config.replace(auto_approve=not agent.config.auto_approve)
    state = "ON" if agent.config.auto_approve else "OFF"
    print(success(f"✓ Auto-approve: {state}"))


@register("thinking", "Toggle reasoning trace display", aliases=("T",),
          usage="/thinking [on|off]")
def cmd_thinking(agent, args: str):
    """`/thinking` toggles; `/thinking on|off` sets it outright.

    The bare toggle always worked — the command was simply **undiscoverable**. `/help` listed only
    the built-in handlers, so `/thinking` was never mentioned, while the renderer's own hint
    ("use /thinking to expand") pointed at a name the user had no way to find. Reported as
    *"i cant toggle the tinking, there is no cmd to do this"* — there was, and `/help` hid it.

    Accepting an explicit value also makes it scriptable, and removes the "which way does it
    toggle?" question from the bare form.
    """
    want = (args or "").strip().lower()
    if want in ("on", "true", "1", "yes"):
        new = True
    elif want in ("off", "false", "0", "no"):
        new = False
    elif want in ("", "toggle"):
        new = not agent.config.show_thinking
    else:
        print(error("usage: /thinking [on|off]"))
        return
    agent.config = agent.config.replace(show_thinking=new)
    print(success(f"✓ Show thinking: {'ON' if new else 'OFF'}"))
    print(dim("  reasoning renders as a block before the answer, when the model emits any —"
              " not every provider returns reasoning tokens."))


@register("events", "Show engine diagnostics (retries, SSE, tool 404s) on the console",
          usage="/events [on|off|log [n]]")
def cmd_events(agent, args: str):
    """Surface the events the interceptor deliberately hides, or show the log.

    `agent/logger.py` routes the engine's diagnostics — provider retries, SSE reconnects, tool 404s,
    truncation badges — to `.agent/runtime.log` and **suppresses them on the console**. That is the
    right default; it is a bad one when something is misbehaving and you cannot see why, because
    the events exist and are simply invisible.

    `/events on` echoes them to **stderr** (never stdout, so a `--print` payload stays clean).
    `/events log [n]` shows the tail of the file the interceptor writes.
    """
    import agent.logger as engine_log

    arg = (args or "").strip()
    verb, _, rest = arg.partition(" ")
    verb = verb.lower()

    if verb in ("log", "tail"):
        path = engine_log.get_log_path()
        try:
            n = int(rest.strip() or 20)
        except ValueError:
            n = 20
        if not path.exists():
            print(dim(f"  no engine log yet at {path}"))
            return
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            print(error(f"  cannot read {path}: {exc}"))
            return
        print(dim(f"  {path} — last {min(n, len(lines))} of {len(lines)} lines:"))
        for line in lines[-n:]:
            print(dim("  " + line[:200]))
        return

    if verb in ("on", "true", "1", "yes"):
        engine_log.set_console_echo(True)
    elif verb in ("off", "false", "0", "no"):
        engine_log.set_console_echo(False)
    elif verb in ("", "status", "toggle"):
        if verb == "toggle":
            engine_log.set_console_echo(not engine_log.console_echo_enabled())
    else:
        print(error("usage: /events [on|off|log [n]]"))
        return

    on = engine_log.console_echo_enabled()
    print(success(f"✓ Engine events on console: {'ON' if on else 'OFF'}"))
    if on:
        print(dim("  retries / SSE / tool-404s now echo to stderr as they happen."))
    print(dim(f"  the full log is always at {engine_log.get_log_path()} — /events log"))


# ── Shared subagent helpers ──────────────────────────────────────────


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


def _get_background_manager(agent):
    """Resolve the composition-wired BackgroundAgentManager, or None."""
    orch = getattr(getattr(agent, "runtime", None), "orchestrator", None)
    return getattr(orch, "background_agents", None)


@register("spawn", "Spawn a subagent for a scoped task", aliases=("sub", "delegate"), usage="/spawn [role] <task description>")
def cmd_spawn(agent, args: str):
    if not args:
        print(info("Usage: /spawn [role] <task description>"))
        print(dim("Roles: coder, reviewer, tester, researcher, planner, debugger, generalist (default)"))
        print(dim("Example: /spawn researcher research the best Python HTTP client library"))
        print(dim("Example: /spawn to explore this dir  (defaults to generalist)"))
        return
    from wisp.multi_agent import SubagentContract
    from wisp.async_utils import run_sync_coro
    # Parse optional leading role: "/spawn researcher <task>" -> role=researcher
    # so the vague "/spawn to explore this dir" stays generalist but an
    # explicit "/spawn coder fix foo.py" gets the right toolset without
    # requiring the tool-based spawn.
    _valid_roles = {"coder", "reviewer", "tester", "researcher", "planner", "debugger", "generalist"}
    role = "generalist"
    task = args
    _first, _, _rest = args.partition(" ")
    if _first.lower() in _valid_roles and _rest.strip():
        role = _first.lower()
        task = _rest.strip()
    elif _first.lower() in _valid_roles and not _rest.strip():
        print(error(f"Role '{_first}' requires a task description"))
        print(dim("Usage: /spawn [role] <task description>"))
        return
    import sys
    if sys.stdin.isatty():
        from wisp.cli.ui.guard import run_spawn_gate
        if run_spawn_gate({"task": task, "role": role}) != "approve":
            print("Spawn cancelled.")
            return
    contract = SubagentContract(
        name="spawn",
        task=task,
        role=role,
        timeout_seconds=120,
        max_iterations=15,
        progress_callback=_print_subagent_progress,
    )
    orch = _get_orchestrator(agent)
    print(accent(f"🧬 Spawning subagent [{role}]: {task[:60]}..."))
    result = run_sync_coro(orch.run(contract))
    status = success("✓") if result.success else error("✗")
    if result.timed_out:
        status = warning("⏱")
    print(f"\n{status} Subagent done ({result.elapsed_seconds:.1f}s, {result.iterations_used} iterations)")
    print("─" * 40)
    print(result.output)


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


@register("swarm", "Launch a multi-agent swarm for a complex task",
          aliases=("multi",), usage="/swarm <task description>")
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