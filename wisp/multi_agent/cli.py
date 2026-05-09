"""CLI commands for multi-agent swarm mode.

Usage:
    wisp swarm "implement user auth" --roles coder,reviewer,tester
    wisp agents list
    wisp agents status
"""

from __future__ import annotations

import json
import logging

from wisp.config import WispConfig
from wisp.colors import success, error, warning, info, dim, accent

from .orchestrator import SwarmOrchestrator
from .roles import AgentRole

logger = logging.getLogger(__name__)

# Module-level reference to the most recent orchestrator (for status queries)
_last_orchestrator: SwarmOrchestrator | None = None


def cmd_swarm(goal: str, roles: list[str] | None = None, model: str | None = None, workspace: str | None = None, max_parallel: int = 3, count_per_role: dict[str, int] | None = None, max_retries: int = 2):
    """Run a multi-agent swarm to accomplish a goal."""
    global _last_orchestrator
    config = WispConfig()
    if model:
        config.model = model
    if workspace:
        config.workspace = workspace

    if roles is None:
        roles = [AgentRole.CODER, AgentRole.REVIEWER, AgentRole.TESTER, AgentRole.RESEARCHER]

    total_agents = sum((count_per_role or {}).get(r, 1) for r in roles)
    print(info(f"🐝 Starting swarm with {total_agents} agent(s) across {len(roles)} role(s)..."))
    print(dim(f"   Goal: {goal}"))
    print(dim(f"   Roles: {', '.join(roles)}"))
    print(dim(f"   Model: {config.model}"))
    print(dim(f"   Workspace: {config.workspace}"))
    if max_retries:
        print(dim(f"   Retries: up to {max_retries} per task"))
    print()

    orch = SwarmOrchestrator(config, max_parallel=max_parallel)
    _last_orchestrator = orch
    try:
        result = orch.run(goal, roles=roles, count_per_role=count_per_role, max_retries=max_retries)

        print()
        print(success("✓ Swarm execution complete"))
        print()
        print(result.final_output)
        print()
        print(dim(f"⏱  Total time: {result.elapsed_seconds:.1f}s"))
        print(dim(f"📁 Files changed: {', '.join(result.files_changed) if result.files_changed else 'none'}"))

        if not result.success:
            print(warning("⚠ Some tasks failed. Review the output above."))
    except KeyboardInterrupt:
        print(warning("\n⚠ Interrupted. Stopping all agents..."))
        orch.stop_all()
        raise


def cmd_agents_list():
    """List all available agent roles and their descriptions."""
    print(info("Available agent roles:\n"))
    for role, cfg in [
        (AgentRole.CODER, "Writes and edits code. Can read, write, edit files and run bash."),
        (AgentRole.REVIEWER, "Reviews code for correctness, style, and safety. Read-only on production code."),
        (AgentRole.TESTER, "Writes and runs tests. Can modify test files only."),
        (AgentRole.RESEARCHER, "Investigates problems and gathers context. Read-only, no file modifications."),
        (AgentRole.PLANNER, "Breaks down goals into subtasks. Read-only, produces structured plans."),
        (AgentRole.DEBUGGER, "Diagnoses and fixes bugs. Minimal changes with verification."),
    ]:
        print(f"  {accent(role):15s}  {cfg}")
    print()
    print(dim("Usage: wisp swarm 'goal' --roles coder,reviewer,tester"))


def cmd_agents_status(registry=None):
    """Show status of running agents (if any)."""
    global _last_orchestrator
    if registry is None:
        registry = _last_orchestrator.registry if _last_orchestrator else None
    if registry is None:
        print(info("No active swarm. Run `wisp swarm 'goal'` to start one."))
        return

    data = registry.to_dict()
    print(info(f"Swarm status ({data['active']} active / {data['total']} total):\n"))
    for rec in data["agents"]:
        status_icon = {
            "IDLE": "⏳",
            "WORKING": "🔨",
            "STOPPED": "🛑",
            "CRASHED": "💥",
            "SPAWNING": "🐣",
        }.get(rec["status"], "❓")
        print(f"  {status_icon} {rec['agent_id']:30s}  {rec['role']:12s}  {rec['status']}")
        if rec["current_task"]:
            print(f"     └─ {dim(rec['current_task'][:60])}")
        if rec["files_locked"]:
            print(f"     └─ 🔒 {', '.join(rec['files_locked'])}")
    print()
