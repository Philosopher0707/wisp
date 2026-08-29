"""Core session commands: help, clear, session info, save, tokens, metrics,
drop, new, continue, exit. Split from wisp/commands.py (back-compat shim)."""

import logging

from wisp.colors import success, warning, info, dim, accent
from wisp.core.session_view import SessionView
from wisp.repl.commands import register

logger = logging.getLogger(__name__)


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


@register("drop", "Remove the last message from history", aliases=("pop", "undo"), usage="/drop")
def cmd_drop(agent, args: str):
    if not agent.messages:
        print(dim("History is empty."))
        return
    removed = agent.messages.pop()
    role = removed.get("role", "?")
    preview = (removed.get("content", "") or "")[:60].replace("\n", " ")
    print(success(f"✓ Dropped last message ({role}): {preview}..."))


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


from wisp.exceptions import ExitREPL  # noqa: E402


@register("exit", "Exit Wisp", aliases=("quit", "q", "bye"), usage="/exit")
def cmd_exit(agent, args: str):
    raise ExitREPL


# Imported after definitions to avoid a circular import at module scope
from wisp.repl.commands import all_commands  # noqa: E402
