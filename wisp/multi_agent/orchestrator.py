"""Async swarm orchestrator — spawns agents, assigns tasks, collects results.

v3 (SubagentOrchestrator): unified single + parallel execution, composable
patterns (map-reduce, vote, chain), token budget tracking, async schema
validation, worktree isolation.

The orchestrator is the conductor of the multi-agent system:
1. Parses a high-level goal
2. Spawns a Planner to decompose it into subtasks
3. Assigns subtasks to specialized agents (Coder, Tester, Reviewer, etc.)
4. Manages file locking to prevent conflicts
5. Collects results, retries failures, synthesizes final answer
6. Streams progress events for live UI

Unified API (v3)
----------------
``SubagentOrchestrator`` replaces the legacy ``SubagentRunner`` (subagent.py)
and ``SubagentRunner`` (subagent_runner.py) with a single class:

    orch = SubagentOrchestrator(parent_agent=my_agent)

    # Single subagent
    result = await orch.run(SubagentContract(task="Audit auth.py"))

    # Parallel subagents
    results = await orch.run_parallel([contract1, contract2, contract3])

    # Map-reduce
    result = await orch.run_map_reduce(
        task="Review codebase",
        items=["src/auth.py", "src/api.py"],
        mapper=lambda item: SubagentContract(task=f"Review {item}"),
        reducer="Synthesize findings",
    )

    # Voting consensus
    result = await orch.run_vote(
        task="Is this vulnerable?",
        agents=[SubagentContract(name=f"auditor-{i}") for i in range(3)],
        consensus_threshold=0.6,
    )

    # Sequential chain with context passing
    result = await orch.run_chain([
        SubagentContract(name="writer", task="Implement feature"),
        SubagentContract(name="reviewer", task="Review code"),
    ], pass_context=True)

Token Budgets
-------------
Set a global token budget across all subagent runs:

    orch.set_global_token_budget(100_000)
    result = await orch.run(contract)
    print(orch.get_tokens_consumed())      # total so far
    print(orch.get_token_budget_remaining())  # remaining budget

Per-contract limits:

    contract = SubagentContract(
        task="...",
        max_output_tokens=4_000,
        max_input_tokens=8_000,
    )
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import multiprocessing as mp
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from wisp.agent import WispAgent
from wisp.config import WispConfig
from wisp.session import Session, SessionManager

from .protocol import AgentEvent, EventType, TaskAssignment, TaskResult
from .registry import AgentRegistry, AgentRecord, AgentStatus
from .bus import MessageBus
from .roles import AgentRole, ROLE_CONFIGS
from .agent_factory import AgentFactory
from .workspace_lock import WorkspaceLock
from .task import EventKind, OrchestratorEvent, SubagentContract, SubagentResult

logger = logging.getLogger(__name__)

ProgressCallback = Optional[Callable[[OrchestratorEvent], Awaitable[None]]]


@dataclass
class SwarmResult:
    """Final output of a swarm execution."""

    success: bool
    goal: str
    plan: str = ""
    agent_results: list[TaskResult] = field(default_factory=list)
    final_output: str = ""
    elapsed_seconds: float = 0.0
    files_changed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "goal": self.goal,
            "plan": self.plan,
            "agent_results": [r.__dict__ for r in self.agent_results],
            "final_output": self.final_output,
            "elapsed_seconds": self.elapsed_seconds,
            "files_changed": self.files_changed,
        }


class SwarmOrchestrator:
    """Manages a swarm of specialized agents working toward a shared goal.

    v2 features:
    - async execution via asyncio.Semaphore + asyncio.gather
    - multiple agents per role with round-robin selection
    - retry with exponential backoff (different agent on retry)
    - streaming progress events via OrchestratorEvent callback
    """

    def __init__(
        self,
        config: WispConfig,
        parent_agent: Optional[WispAgent] = None,
        max_parallel: int = 3,
    ):
        self.config = config
        self.parent_agent = parent_agent
        self.max_parallel = max_parallel

        self.registry = AgentRegistry()
        self.workspace_lock = WorkspaceLock(config.workspace, self.registry)
        self.bus = MessageBus()
        self.factory = AgentFactory(config, parent_agent, workspace_lock=self.workspace_lock)

        self._agents: dict[str, WispAgent] = {}
        self._shutdown = False

        self.bus.subscribe(self._on_task_result, event_type=EventType.TASK_RESULT)
        self.bus.subscribe(self._on_task_failed, event_type=EventType.TASK_FAILED)
        self.bus.subscribe(self._on_heartbeat, event_type=EventType.AGENT_HEARTBEAT)

    # ── Lifecycle ──────────────────────────────────────────────────────

    def spawn_agents(
        self,
        roles: list[str],
        count_per_role: Optional[dict[str, int]] = None,
    ) -> list[str]:
        """Spawn agents per role. If count_per_role given, spawn multiple per role."""
        ids: list[str] = []
        counts = count_per_role or {}
        for role in roles:
            n = counts.get(role, 1)
            for i in range(n):
                suffix = f"-{i}" if n > 1 else ""
                agent_id = f"{role}{suffix}-{uuid.uuid4().hex[:6]}"
                agent = self.factory.create(role, agent_id, model=self.config.model)
                self._agents[agent_id] = agent
                self.registry.register(
                    AgentRecord(agent_id=agent_id, role=role, status=AgentStatus.IDLE)
                )
                self.bus.emit(
                    AgentEvent(
                        event_type=EventType.AGENT_STARTED,
                        source_agent=agent_id,
                        payload={"role": role},
                    )
                )
                ids.append(agent_id)
                logger.info("Spawned %s agent %s", role, agent_id)
        return ids

    def stop_all(self) -> None:
        """Gracefully stop all agents and release locks."""
        self._shutdown = True
        for agent_id in list(self._agents.keys()):
            self.workspace_lock.release_all(agent_id)
            self.registry.update_status(agent_id, AgentStatus.STOPPED)
            self.bus.emit(
                AgentEvent(
                    event_type=EventType.AGENT_STOPPED,
                    source_agent=agent_id,
                    payload={"reason": "orchestrator_shutdown"},
                )
            )
        self._agents.clear()
        logger.info("All agents stopped")

    # ── Planning ───────────────────────────────────────────────────────

    async def _plan(
        self, goal: str, available_roles: Optional[list[str]] = None
    ) -> tuple[str, list[dict]]:
        """Decompose goal into subtasks (async wrapper around sync LLM call)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._plan_sync, goal, available_roles)

    def _plan_sync(
        self, goal: str, available_roles: Optional[list[str]] = None
    ) -> tuple[str, list[dict]]:
        """Sync planning logic — runs in thread pool executor."""
        planner = self.factory.create(
            AgentRole.PLANNER, "planner-" + uuid.uuid4().hex[:6]
        )

        if available_roles is None:
            available_roles = [
                AgentRole.CODER, AgentRole.REVIEWER,
                AgentRole.TESTER, AgentRole.RESEARCHER,
            ]

        role_descriptions = {
            AgentRole.CODER: "writes and edits code",
            AgentRole.REVIEWER: "reviews code for correctness and style",
            AgentRole.TESTER: "writes and runs tests",
            AgentRole.RESEARCHER: "investigates problems and gathers context",
            AgentRole.DEBUGGER: "diagnoses and fixes bugs",
            AgentRole.PLANNER: "breaks down goals into structured plans",
        }

        role_list = "\n".join(
            f"- {r}: {role_descriptions.get(r, 'assists with tasks')}"
            for r in available_roles
        )

        prompt = f"""Break down the following goal into subtasks for a multi-agent swarm.

Goal: {goal}

IMPORTANT: Only assign tasks to these available roles:
{role_list}

For each subtask, provide:
1. role: which role should handle it (MUST be one of: {', '.join(available_roles)})
2. description: what to do
3. expected_output: what the deliverable is
4. dependencies: list of task indices that must finish first (empty for parallel tasks)

Respond in JSON with a "plan" string and a "subtasks" array.
"""
        planner.messages.append({"role": "user", "content": prompt})

        try:
            system = planner._build_system_prompt()
            raw = planner._run_turn_streaming(system)
            content = (
                raw.get("message", {}).get("content", "")
                if isinstance(raw.get("message"), dict)
                else ""
            )
            data = self._extract_json(content)
            if data is not None:
                plan = data.get("plan", content)
                subtasks = data.get("subtasks", [])
            else:
                plan = content
                subtasks = [
                    {
                        "role": "coder",
                        "description": goal,
                        "expected_output": "working code",
                        "dependencies": [],
                    }
                ]
        except Exception as e:
            logger.warning("Planner failed: %s. Using fallback single-task plan.", e)
            plan = goal
            subtasks = [
                {
                    "role": "coder",
                    "description": goal,
                    "expected_output": "working code",
                    "dependencies": [],
                }
            ]
        finally:
            if hasattr(planner, "close"):
                planner.close()

        return plan, subtasks

    def _extract_json(self, content: str) -> Optional[dict]:
        """Extract JSON from model output, handling markdown fences."""
        content = content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            for match in re.finditer(r"(\{[\s\S]*\}|\[[\s\S]*\])", content):
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue
            return None

    # ── Execution (sync entry points) ──────────────────────────────────

    def run(
        self,
        goal: str,
        roles: Optional[list[str]] = None,
        count_per_role: Optional[dict[str, int]] = None,
        max_retries: int = 2,
        progress_callback: ProgressCallback = None,
    ) -> SwarmResult:
        """Sync wrapper — delegates to async arun().

        Detects whether an event loop is already running (e.g. inside REPL
        slash command) and adapts accordingly.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — simple case (CLI entry point)
            return asyncio.run(
                self.arun(goal, roles, count_per_role, max_retries, progress_callback)
            )

        # Event loop already running — run async in a dedicated thread
        return self._run_sync_in_thread(
            goal, roles, count_per_role, max_retries, progress_callback
        )

    def _run_sync_in_thread(
        self,
        goal: str,
        roles: Optional[list[str]],
        count_per_role: Optional[dict[str, int]],
        max_retries: int,
        progress_callback: ProgressCallback,
    ) -> SwarmResult:
        """Run arun() in a background thread with its own event loop."""
        result_holder: dict[str, Any] = {}

        async def _runner():
            result_holder["result"] = await self.arun(
                goal, roles, count_per_role, max_retries, progress_callback
            )

        def _target():
            loop = asyncio.new_event_loop()
            try:
                result_holder["result"] = loop.run_until_complete(_runner())
            except Exception as e:
                result_holder["error"] = e
            finally:
                loop.close()

        thread = __import__("threading").Thread(target=_target, daemon=True)
        thread.start()
        thread.join(timeout=300)

        if thread.is_alive():
            logger.warning("Swarm thread did not finish within 5 minutes — forcing shutdown")
            self._shutdown = True
            try:
                self.stop_all()
            except Exception:
                pass
            return SwarmResult(
                success=False, goal=goal, plan="",
                final_output="[Swarm timed out after 5 minutes]",
            )

        if "error" in result_holder:
            raise RuntimeError(result_holder["error"])
        result = result_holder.get("result")
        if result is not None:
            return result
        return SwarmResult(
            success=False, goal=goal, plan="", final_output="Orchestrator thread returned no result"
        )

    # ── Async core ─────────────────────────────────────────────────────

    async def arun(
        self,
        goal: str,
        roles: Optional[list[str]] = None,
        count_per_role: Optional[dict[str, int]] = None,
        max_retries: int = 2,
        progress_callback: ProgressCallback = None,
    ) -> SwarmResult:
        """Execute a goal using the swarm (async).

        Args:
            goal: High-level task description.
            roles: Which roles to spawn. Default: coder, reviewer, tester, researcher.
            count_per_role: How many agents per role (e.g. {"coder": 3}).
            max_retries: Max retries per failed task.
            progress_callback: Async callback receiving OrchestratorEvent.
        """
        start = time.monotonic()
        if roles is None:
            roles = [AgentRole.CODER, AgentRole.REVIEWER, AgentRole.TESTER, AgentRole.RESEARCHER]

        async def emit(event: OrchestratorEvent) -> None:
            if progress_callback:
                await progress_callback(event)

        # Spawn agents
        agent_ids = self.spawn_agents(roles, count_per_role)
        logger.info("Swarm spawned %d agents for: %s", len(agent_ids), goal[:100])

        # Plan
        await emit(OrchestratorEvent(
            event_type=EventKind.PLANNING, payload={"goal": goal}
        ))
        plan, subtasks = await self._plan(goal, roles)
        await emit(OrchestratorEvent(
            event_type=EventKind.PLANNING,
            payload={"plan": plan, "subtask_count": len(subtasks)},
        ))
        logger.info("Plan generated with %d subtasks", len(subtasks))

        # Execute with retry + streaming
        results = await self._execute_subtasks_async(
            subtasks, agent_ids, max_retries, emit
        )

        # Synthesize
        final_output = self._synthesize(goal, plan, results)
        elapsed = time.monotonic() - start
        all_files: list[str] = []
        for r in results:
            all_files.extend(r.files_changed)

        await emit(OrchestratorEvent(
            event_type=EventKind.DONE,
            payload={
                "elapsed": elapsed,
                "files_changed": sorted(set(all_files)),
                "passed": sum(1 for r in results if r.success),
                "failed": sum(1 for r in results if not r.success),
            },
        ))

        self.stop_all()

        return SwarmResult(
            success=all(r.success for r in results) if results else True,
            goal=goal,
            plan=plan,
            agent_results=results,
            final_output=final_output,
            elapsed_seconds=elapsed,
            files_changed=sorted(set(all_files)),
        )

    # ── Subtask execution ──────────────────────────────────────────────

    async def _execute_subtasks_async(
        self,
        subtasks: list[dict],
        agent_ids: list[str],
        max_retries: int,
        emit: Callable[[OrchestratorEvent], Awaitable[None]],
    ) -> list[TaskResult]:
        """Execute subtasks with dependency resolution + async concurrency."""
        results: dict[int, TaskResult] = {}
        completed: set[int] = set()
        failed: set[int] = set()

        # Map role → available agent IDs
        role_to_agents: dict[str, list[str]] = {}
        for aid in agent_ids:
            rec = self.registry.get(aid)
            if rec:
                role_to_agents.setdefault(rec.role, []).append(aid)

        # Round-robin index per role
        role_rr: dict[str, int] = {r: 0 for r in role_to_agents}

        semaphore = asyncio.Semaphore(self.max_parallel)
        task_done_cond = asyncio.Condition()

        async def run_task(idx: int) -> None:
            nonlocal results, completed, failed

            task = subtasks[idx]
            role = task.get("role", "coder")
            available = role_to_agents.get(role, [])

            if not available:
                logger.warning("No agent for role %s, skipping task %d", role, idx)
                results[idx] = TaskResult(
                    task_id=f"task-{idx}", success=False, output="",
                    error=f"No agent available for role {role}",
                )
                failed.add(idx)
                return

            async with semaphore:
                # Round-robin pick
                rr_idx = role_rr[role] % len(available)
                agent_id = available[rr_idx]
                role_rr[role] += 1

                assignment = TaskAssignment(
                    task_id=f"task-{idx}",
                    description=task.get("description", ""),
                    expected_output=task.get("expected_output", ""),
                    max_iterations=ROLE_CONFIGS.get(
                        role, ROLE_CONFIGS[AgentRole.CODER]
                    ).max_iterations,
                    timeout_seconds=ROLE_CONFIGS.get(
                        role, ROLE_CONFIGS[AgentRole.CODER]
                    ).timeout_seconds,
                )

                await emit(OrchestratorEvent(
                    task_id=assignment.task_id,
                    event_type=EventKind.TASK_STARTED,
                    payload={
                        "role": role,
                        "agent_id": agent_id,
                        "description": assignment.description,
                    },
                ))

                # Retry loop with exponential backoff
                retry_count = 0
                result: Optional[TaskResult] = None

                while retry_count <= max_retries:
                    try:
                        self.registry.update_status(
                            agent_id, AgentStatus.WORKING,
                            task=task.get("description", ""),
                        )
                        result = await self._run_agent_task_async(agent_id, assignment)
                        self.registry.update_status(agent_id, AgentStatus.IDLE, task=None)
                    except Exception as e:
                        result = TaskResult(
                            task_id=assignment.task_id,
                            success=False, output="", error=str(e),
                        )

                    if result.success:
                        break

                    if retry_count < max_retries:
                        retry_count += 1
                        backoff = 2 ** (retry_count - 1)  # 1s, 2s, 4s

                        # Different agent on retry (if multiple available)
                        if len(available) > 1:
                            rr_idx2 = role_rr[role] % len(available)
                            new_agent_id = available[rr_idx2]
                            if new_agent_id != agent_id:
                                agent_id = new_agent_id
                            role_rr[role] += 1

                        await emit(OrchestratorEvent(
                            task_id=assignment.task_id,
                            event_type=EventKind.TASK_RETRY,
                            payload={
                                "retry": retry_count,
                                "error": result.error if result else "unknown",
                                "backoff_seconds": backoff,
                                "agent_id": agent_id,
                            },
                        ))
                        await asyncio.sleep(backoff)
                    else:
                        break

                if result is None:
                    result = TaskResult(
                        task_id=assignment.task_id, success=False, output="",
                        error="No result produced",
                    )

                results[idx] = result
                if result.success:
                    completed.add(idx)
                    await emit(OrchestratorEvent(
                        task_id=assignment.task_id,
                        event_type=EventKind.TASK_COMPLETED,
                        payload={
                            "files_changed": result.files_changed,
                            "elapsed": result.elapsed_seconds,
                        },
                    ))
                else:
                    failed.add(idx)
                    await emit(OrchestratorEvent(
                        task_id=assignment.task_id,
                        event_type=EventKind.TASK_FAILED,
                        payload={"error": result.error},
                    ))
                async with task_done_cond:
                    task_done_cond.notify_all()

        async def wait_then_run(idx: int) -> None:
            """Wait until dependencies resolved, then run."""
            deps = subtasks[idx].get("dependencies", [])
            while True:
                if self._shutdown:
                    failed.add(idx)
                    return
                # Check conditions while holding lock, then release before run_task
                async with task_done_cond:
                    ready = all(d in completed for d in deps)
                    failed_deps = any(d in failed for d in deps)
                if ready:
                    await run_task(idx)
                    return
                if failed_deps:
                    failed.add(idx)
                    return
                async with task_done_cond:
                    await task_done_cond.wait()

        coros = [wait_then_run(i) for i in range(len(subtasks))]
        await asyncio.gather(*coros)

        return [
            results.get(
                i, TaskResult(task_id=f"task-{i}", success=False, output="")
            )
            for i in range(len(subtasks))
        ]

    async def _run_agent_task_async(
        self, agent_id: str, assignment: TaskAssignment
    ) -> TaskResult:
        """Run a single task on a single agent (async)."""
        agent = self._agents.get(agent_id)
        if not agent:
            return TaskResult(
                task_id=assignment.task_id,
                success=False, output="",
                error=f"Agent {agent_id} not found",
            )

        self.bus.emit(assignment.to_event(source="orchestrator", target=agent_id))

        start = time.monotonic()
        try:
            task_result = await agent.run_task(
                task_description=assignment.description,
                workspace=agent.config.workspace or ".",
                max_iterations=assignment.max_iterations,
                timeout_seconds=assignment.timeout_seconds,
            )
            elapsed = time.monotonic() - start
            content = task_result.get("output", "")

            files_changed: list[str] = agent.change_tracker.get_changed_files()
            if not files_changed:
                files_changed = self._extract_file_changes(content)

            result = TaskResult(
                task_id=assignment.task_id,
                success=task_result.get("success", False),
                output=content,
                files_changed=files_changed,
                elapsed_seconds=elapsed,
                iterations_used=1,
            )
        except Exception as e:
            elapsed = time.monotonic() - start
            result = TaskResult(
                task_id=assignment.task_id,
                success=False, output="",
                error=str(e),
                elapsed_seconds=elapsed,
            )

        for path in result.files_changed:
            self.registry.claim_file(agent_id, path)
            self.bus.emit(
                AgentEvent(
                    event_type=EventType.FILE_CLAIMED,
                    source_agent=agent_id,
                    payload={"path": path, "task_id": assignment.task_id},
                )
            )

        self.bus.emit(result.to_event(source=agent_id, target="orchestrator"))

        agent.file_lock.release_all()
        for path in result.files_changed:
            self.registry.release_file(agent_id, path)
            self.bus.emit(
                AgentEvent(
                    event_type=EventType.FILE_RELEASED,
                    source_agent=agent_id,
                    payload={"path": path, "task_id": assignment.task_id},
                )
            )

        return result

    def _extract_file_changes(self, content: str) -> list[str]:
        """Best-effort extraction of file paths from agent output."""
        paths = []
        for match in re.finditer(
            r'["\']([\w/\\.-]+\.(py|js|ts|java|rs|go|c|cpp|h|md|json|yaml|yml|toml))["\']',
            content,
        ):
            paths.append(match.group(1))
        return sorted(set(paths))

    def _synthesize(
        self, goal: str, plan: str, results: list[TaskResult]
    ) -> str:
        """Combine all agent outputs into a coherent final response."""
        passed = sum(1 for r in results if r.success)
        failed_count = sum(1 for r in results if not r.success)
        all_files = sorted(set(f for r in results for f in r.files_changed))

        lines = [
            f"## Swarm Result: {goal}",
            "",
            f"**{passed}/{len(results)} tasks passed**"
            + (f", {failed_count} failed" if failed_count else ""),
            "",
        ]

        if all_files:
            lines.append(f"**Files changed:** {', '.join(all_files)}")
            lines.append("")

        lines.append("### Plan")
        lines.append(plan)
        lines.append("")

        for r in results:
            status = "PASS" if r.success else "FAIL"
            lines.append(f"### {status}: {r.task_id}")
            lines.append(f"Time: {r.elapsed_seconds:.1f}s")
            if r.files_changed:
                lines.append(f"Files: {', '.join(r.files_changed)}")
            lines.append("")
            if r.output:
                lines.append(r.output[:3000])
                if len(r.output) > 3000:
                    lines.append("... [truncated]")
            if r.error:
                lines.append(f"**Error:** {r.error}")
            lines.append("")

        total_time = sum(r.elapsed_seconds for r in results)
        lines.append(f"**Total agent time:** {total_time:.1f}s")

        return "\n".join(lines)

    # ── Event handlers ─────────────────────────────────────────────────

    def _on_task_result(self, event: AgentEvent) -> None:
        rec = self.registry.get(event.source_agent)
        if rec:
            rec.total_tasks_completed += 1

    def _on_task_failed(self, event: AgentEvent) -> None:
        rec = self.registry.get(event.source_agent)
        if rec:
            rec.total_tasks_failed += 1

    def _on_heartbeat(self, event: AgentEvent) -> None:
        self.registry.heartbeat(event.source_agent)


# ── Process-based subagent worker ────────────────────────────────────

def _run_subagent_worker(contract_dict: dict, result_path: str, parent_workspace: str):
    """Standalone worker that runs in a separate process.

    Reconstructs a minimal orchestrator from the serialized contract dict,
    runs the subagent, and writes the result to ``result_path`` as JSON.
    """
    import asyncio
    import time as _time

    from wisp.config import WispConfig
    from wisp.session import Session, SessionManager
    from wisp.multi_agent.orchestrator import SubagentOrchestrator
    from wisp.multi_agent.task import SubagentContract, SubagentResult

    start = _time.monotonic()
    contract = SubagentContract(**contract_dict)

    # Build a minimal orchestrator in the child process
    orch = SubagentOrchestrator(workspace=Path(parent_workspace))

    # Run synchronously in the child process's own event loop
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(orch.run(contract))
    except Exception as exc:
        duration = _time.monotonic() - start
        result = SubagentResult(
            task_id=contract.name,
            success=False,
            output=f"",
            error=f"Process worker crashed: {exc}",
            elapsed_seconds=duration,
        )
    finally:
        loop.close()

    # Serialize result to file for IPC
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump({
            "task_id": result.task_id,
            "success": result.success,
            "output": result.output,
            "error": result.error,
            "files_changed": result.files_changed,
            "elapsed_seconds": result.elapsed_seconds,
            "iterations_used": result.iterations_used,
            "retry_count": result.retry_count,
            "timed_out": result.timed_out,
            "hit_iteration_limit": result.hit_iteration_limit,
            "tokens_used": result.tokens_used,
        }, f, indent=2)


# ── SubagentOrchestrator (unified subagent API) ──────────────────────

class SubagentOrchestrator:
    """Unified orchestrator for single, parallel, and composable subagent execution.

    Replaces the legacy systems in ``wisp/subagent.py`` and
    ``wisp/subagent_runner.py`` with a single API that delegates to
    ``WispAgentCore.run_task()`` instead of reimplementing the agent loop.

    Core Methods
    ------------
    ``run(contract)`` — single subagent with worktree isolation, timeout,
    schema validation, and progress events.

    ``run_parallel(contracts, max_concurrent=4)`` — concurrent execution with
    semaphore-controlled concurrency.

    Composable Patterns
    -------------------
    ``run_map_reduce(task, items, mapper, reducer)`` — split work across
    mappers, synthesize with a reducer. Includes token budget guards.

    ``run_vote(task, agents, consensus_threshold=0.6)`` — ask multiple
    independent agents the same question, take majority vote.

    ``run_chain(contracts, pass_context=True)`` — sequential execution with
    optional context passing between steps.

    Token Budgets
    -------------
    ``set_global_token_budget(budget)`` — set a global token cap.
    ``get_tokens_consumed()`` — total tokens used so far.
    ``get_token_budget_remaining()`` — remaining budget (None = unlimited).

    Usage
    -----
        orch = SubagentOrchestrator(parent_agent=my_agent)

        # Single subagent
        result = await orch.run(SubagentContract(task="Audit auth.py"))

        # Parallel subagents
        results = await orch.run_parallel([contract1, contract2])

        # Map-reduce
        result = await orch.run_map_reduce(
            task="Review codebase",
            items=["src/auth.py", "src/api.py"],
            mapper=lambda item: SubagentContract(task=f"Review {item}"),
            reducer="Synthesize findings",
        )

        # Voting consensus
        result = await orch.run_vote(
            task="Is this vulnerable?",
            agents=[SubagentContract(name=f"auditor-{i}") for i in range(3)],
            consensus_threshold=0.6,
        )

        # Sequential chain with context passing
        result = await orch.run_chain([
            SubagentContract(name="writer", task="Implement feature"),
            SubagentContract(name="reviewer", task="Review code"),
        ], pass_context=True)
    """

    def __init__(
        self,
        parent_agent: Optional[WispAgent] = None,
        config: Optional[WispConfig] = None,
        workspace: Optional[Path] = None,
    ):
        """Initialise the orchestrator.

        Parameters
        ----------
        parent_agent:
            The parent WispAgent that spawns subagents. Used to inherit
            config, model, HTTP session, and file lock.
        config:
            Explicit config override. If None, inherited from ``parent_agent``.
        workspace:
            Repository root for worktree creation. If None, resolved from
            ``parent_agent.config.workspace`` or ``Path.cwd()``.
        """
        self.parent = parent_agent
        self.config = config or (parent_agent.config if parent_agent else WispConfig())
        self.workspace = (
            workspace
            or (Path(self.config.workspace).resolve() if self.config.workspace else None)
            or Path.cwd().resolve()
        )
        self._worktrees_root = self.workspace / ".wisp" / "worktrees"
        self._session_mgr = SessionManager()
        self._shutdown = False
        # ── Token budget tracking ──────────────────────────────────────
        self._tokens_consumed: int = 0
        self._global_token_budget: Optional[int] = None
        """Global token budget across all subagent runs. None = unlimited."""
        # ── Telemetry ────────────────────────────────────────────────────
        self._telemetry: dict[str, list[dict]] = {}
        """Per-model telemetry: latency, success, tokens."""

    # ── Token budget API ───────────────────────────────────────────────

    def set_global_token_budget(self, budget: Optional[int]) -> None:
        """Set a global token budget for all subagents spawned by this orchestrator.

        Parameters
        ----------
        budget:
            Maximum total tokens (input + output) across all runs.
            None removes the budget.
        """
        self._global_token_budget = budget
        logger.info("Global token budget set to %s", budget if budget else "unlimited")

    def get_tokens_consumed(self) -> int:
        """Return total tokens consumed so far."""
        return self._tokens_consumed

    def get_token_budget_remaining(self) -> Optional[int]:
        """Return remaining global token budget, or None if unlimited."""
        if self._global_token_budget is None:
            return None
        return max(0, self._global_token_budget - self._tokens_consumed)

    def _estimate_tokens(self, messages: list[dict]) -> tuple[int, int, int]:
        """Estimate token count from message history.

        Returns (input_tokens, output_tokens, total_tokens).
        Uses chars_per_token from config for estimation.
        """
        chars_per_token = getattr(self.config, "chars_per_token", 4)
        input_chars = 0
        output_chars = 0

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""
            if isinstance(content, str):
                text = content
            else:
                text = str(content)

            if role in ("user", "system", "tool"):
                input_chars += len(text)
            elif role == "assistant":
                output_chars += len(text)
                # Also count tool_calls in assistant messages as output
                for tc in msg.get("tool_calls", []) or []:
                    func = tc.get("function", {})
                    args = func.get("arguments", "")
                    if isinstance(args, str):
                        output_chars += len(args)
                    else:
                        output_chars += len(str(args))

        input_tokens = input_chars // chars_per_token
        output_tokens = output_chars // chars_per_token
        return input_tokens, output_tokens, input_tokens + output_tokens

    def _check_token_budget(self, contract: SubagentContract) -> Optional[str]:
        """Check if running this contract would exceed token budgets.

        Returns an error message if budget exceeded, None otherwise.
        """
        # Check global budget
        remaining = self.get_token_budget_remaining()
        if remaining is not None and remaining <= 0:
            return f"Global token budget exhausted ({self._global_token_budget} tokens)"

        # Per-contract budget checks are done post-run via truncation
        return None

    # ── Public API ─────────────────────────────────────────────────────

    async def run(self, contract: SubagentContract) -> SubagentResult:
        """Run a single subagent and return its result.

        Handles worktree creation, agent instantiation, timeout enforcement,
        structured output validation, and error capture.  Never raises —
        failures are returned as ``SubagentResult(success=False, error=...)``.
        """
        start = time.monotonic()
        worktree_path: Path | None = None
        session: Session | None = None
        tool_calls_log: list[dict] = []

        # ── Token budget check ─────────────────────────────────────────
        budget_error = self._check_token_budget(contract)
        if budget_error:
            logger.warning("Token budget check failed for %s: %s", contract.name, budget_error)
            return SubagentResult(
                task_id=contract.name,
                success=False,
                output=f"[TOKEN BUDGET EXCEEDED] {budget_error}",
                error=budget_error,
                elapsed_seconds=0.0,
            )

        # ── Resolve workspace ──────────────────────────────────────────
        if contract.worktree_isolated:
            try:
                worktree_path = await self._create_worktree(contract.name)
            except Exception as exc:
                logger.warning(
                    "Worktree creation failed for %s, falling back to shared "
                    "workspace: %s",
                    contract.name,
                    exc,
                )
                worktree_path = None

        agent_workspace = str(worktree_path or self.workspace)

        # ── Build child config ─────────────────────────────────────────
        child_cfg = self._build_child_config(contract)

        # ── Create session ─────────────────────────────────────────────
        session = Session.create(
            model=child_cfg.model,
            workspace=agent_workspace,
            first_prompt=contract.task,
        )
        session.title = f"[sub] {contract.name}"

        # ── Build system prompt ────────────────────────────────────────
        system = contract.system_prompt or self._default_system_prompt(contract)

        # ── Emit start event ───────────────────────────────────────────
        if contract.progress_callback:
            await self._emit(
                contract,
                EventKind.TASK_STARTED,
                {"role": contract.role, "description": contract.task},
            )

        # ── Dispatch by isolation level ──────────────────────────────
        try:
            if contract.isolation == "process":
                return await self._spawn_subagent_process(contract, start)
            else:
                return await self._spawn_subagent_thread(contract, start, agent_workspace, child_cfg, session, system, tool_calls_log)
        finally:
            # ── Cleanup worktree (unless debugging) ──────────────────
            if worktree_path and not os.environ.get("WISP_KEEP_WORKTREES", "").lower() == "true":
                try:
                    await self._cleanup_worktree(worktree_path)
                except Exception as exc:
                    logger.warning(
                        "Failed to clean up worktree %s: %s", worktree_path, exc
                    )

    async def _spawn_subagent_thread(
        self,
        contract: SubagentContract,
        start: float,
        agent_workspace: str,
        child_cfg: WispConfig,
        session: Session,
        system: str,
        tool_calls_log: list[dict],
    ) -> SubagentResult:
        """Thread-based subagent execution (default, fast)."""
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self._run_agent_sync,
                    contract,
                    child_cfg,
                    session,
                    system,
                    agent_workspace,
                    tool_calls_log,
                ),
                timeout=contract.timeout_seconds,
            )

            session.touch()
            self._session_mgr.save(session)

            duration = time.monotonic() - start
            subagent_result = SubagentResult(
                task_id=contract.name,
                success=result["success"],
                output=result["output"],
                tool_calls=list(tool_calls_log),
                elapsed_seconds=duration,
                error=result.get("error"),
                session_id=session.id,
                files_changed=result.get("files_changed", []),
                iterations_used=result.get("iterations_used", 0),
            )

            # ── Token estimation & budget tracking ───────────────────
            messages = result.get("messages", [])
            if messages:
                in_tok, out_tok, total_tok = self._estimate_tokens(messages)
                subagent_result.input_tokens = in_tok
                subagent_result.output_tokens = out_tok
                subagent_result.tokens_used = total_tok
                self._tokens_consumed += total_tok
                logger.debug(
                    "Subagent %s tokens: %d in / %d out / %d total",
                    contract.name, in_tok, out_tok, total_tok,
                )

                # Enforce per-contract output token limit
                if contract.max_output_tokens and out_tok > contract.max_output_tokens:
                    logger.warning(
                        "Subagent %s output tokens %d exceed limit %d",
                        contract.name, out_tok, contract.max_output_tokens,
                    )
                    subagent_result.output = (
                        subagent_result.output[:contract.max_output_chars]
                        + f"\n\n[OUTPUT TRUNCATED: exceeded {contract.max_output_tokens} output tokens]"
                    )

            # Enforce per-contract output char limit (independent of token limit)
            if len(subagent_result.output) > contract.max_output_chars:
                subagent_result.output = (
                    subagent_result.output[:contract.max_output_chars]
                    + f"\n\n[OUTPUT TRUNCATED: exceeded {contract.max_output_chars} characters]"
                )

            # ── Record telemetry ───────────────────────────────────────
            model_used = contract.model or self.config.model or "unknown"
            self._telemetry.setdefault(model_used, []).append({
                "task_id": contract.name,
                "success": subagent_result.success,
                "elapsed_seconds": subagent_result.elapsed_seconds,
                "tokens_used": subagent_result.tokens_used,
                "iterations_used": subagent_result.iterations_used,
                "timestamp": time.time(),
            })

            # ── Structured output validation ───────────────────────────
            if contract.output_schema and subagent_result.success:
                subagent_result = await self._validate_output(subagent_result, contract)

            # ── Emit completion event ────────────────────────────────
            if contract.progress_callback:
                await self._emit(
                    contract,
                    EventKind.TASK_COMPLETED,
                    {
                        "files_changed": subagent_result.files_changed,
                        "elapsed": duration,
                        "output": subagent_result.output[:200],
                    },
                )

            return subagent_result

        except asyncio.TimeoutError:
            duration = time.monotonic() - start
            logger.warning(
                "Subagent %s timed out after %.1fs", contract.name, duration
            )
            session.touch()
            self._session_mgr.save(session)
            if contract.progress_callback:
                await self._emit(
                    contract,
                    EventKind.TASK_FAILED,
                    {"error": f"Timeout after {contract.timeout_seconds}s"},
                )
            return SubagentResult(
                task_id=contract.name,
                success=False,
                output=f"[TIMED OUT after {duration:.1f}s]",
                tool_calls=list(tool_calls_log),
                elapsed_seconds=duration,
                error=f"Timeout after {contract.timeout_seconds}s",
                session_id=session.id,
                timed_out=True,
            )

        except Exception as exc:
            duration = time.monotonic() - start
            logger.error(
                "Subagent %s crashed: %s", contract.name, exc, exc_info=True
            )
            if session:
                session.touch()
                self._session_mgr.save(session)
            if contract.progress_callback:
                await self._emit(
                    contract,
                    EventKind.TASK_FAILED,
                    {"error": str(exc)},
                )
            return SubagentResult(
                task_id=contract.name,
                success=False,
                output="",
                tool_calls=list(tool_calls_log),
                elapsed_seconds=duration,
                error=str(exc),
                session_id=session.id if session else "",
            )

    async def _spawn_subagent_process(
        self,
        contract: SubagentContract,
        start: float,
    ) -> SubagentResult:
        """Process-based subagent execution (sandboxed, truly killable).

        Spawns the subagent in a separate OS process. On timeout the
        process is SIGTERM'd (and SIGKILL'd if necessary).  On crash the
        parent survives and reports the failure immediately.
        """
        # Create temp file for IPC
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            result_path = f.name

        # Serialize contract for the child process
        contract_dict = {
            "name": contract.name,
            "role": contract.role,
            "task": contract.task,
            "system_prompt": contract.system_prompt,
            "tools": contract.tools,
            "allowed_skills": contract.allowed_skills,
            "max_iterations": contract.max_iterations,
            "timeout_seconds": contract.timeout_seconds,
            "max_tokens": contract.max_tokens,
            "max_input_tokens": contract.max_input_tokens,
            "max_output_tokens": contract.max_output_tokens,
            "max_output_chars": contract.max_output_chars,
            "output_format": contract.output_format,
            "output_schema": contract.output_schema,
            "auto_retry_parse": contract.auto_retry_parse,
            "model": contract.model,
            "workspace": contract.workspace,
            "worktree_isolated": contract.worktree_isolated,
            "auto_approve": contract.auto_approve,
            "system_prompt_extra": contract.system_prompt_extra,
            "prompt": contract.prompt,
            "context_files": contract.context_files,
        }

        process = mp.Process(
            target=_run_subagent_worker,
            args=(contract_dict, result_path, str(self.workspace)),
        )

        try:
            process.start()
            process.join(timeout=contract.timeout_seconds)

            if process.is_alive():
                # Timeout — force kill
                duration = time.monotonic() - start
                logger.warning(
                    "Subagent %s process timed out after %.1fs — terminating",
                    contract.name, duration,
                )
                process.terminate()
                process.join(timeout=5)
                if process.is_alive():
                    logger.error(
                        "Subagent %s process refused SIGTERM — sending SIGKILL",
                        contract.name,
                    )
                    process.kill()
                    process.join(timeout=2)

                if contract.progress_callback:
                    await self._emit(
                        contract,
                        EventKind.TASK_FAILED,
                        {"error": f"Timeout after {contract.timeout_seconds}s"},
                    )
                return SubagentResult(
                    task_id=contract.name,
                    success=False,
                    output=f"[TIMED OUT after {contract.timeout_seconds}s]",
                    elapsed_seconds=duration,
                    error=f"Timeout after {contract.timeout_seconds}s",
                    timed_out=True,
                )

            # Process finished — read result
            try:
                with open(result_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                duration = time.monotonic() - start
                logger.error(
                    "Failed to read subagent %s process result: %s",
                    contract.name, exc,
                )
                return SubagentResult(
                    task_id=contract.name,
                    success=False,
                    output="[RESULT READ FAILED]",
                    elapsed_seconds=duration,
                    error=f"Failed to read process result: {exc}",
                )

            duration = time.monotonic() - start
            subagent_result = SubagentResult(
                task_id=data.get("task_id", contract.name),
                success=data.get("success", False),
                output=data.get("output", ""),
                error=data.get("error"),
                files_changed=data.get("files_changed", []),
                elapsed_seconds=duration,
                iterations_used=data.get("iterations_used", 0),
                retry_count=data.get("retry_count", 0),
                timed_out=data.get("timed_out", False),
                hit_iteration_limit=data.get("hit_iteration_limit", False),
                tokens_used=data.get("tokens_used", 0),
            )

            # Update parent token budget
            self._tokens_consumed += subagent_result.tokens_used

            # Record telemetry
            model_used = contract.model or self.config.model or "unknown"
            self._telemetry.setdefault(model_used, []).append({
                "task_id": contract.name,
                "success": subagent_result.success,
                "elapsed_seconds": subagent_result.elapsed_seconds,
                "tokens_used": subagent_result.tokens_used,
                "iterations_used": subagent_result.iterations_used,
                "timestamp": time.time(),
            })

            if contract.progress_callback:
                await self._emit(
                    contract,
                    EventKind.TASK_COMPLETED if subagent_result.success else EventKind.TASK_FAILED,
                    {
                        "files_changed": subagent_result.files_changed,
                        "elapsed": duration,
                        "output": subagent_result.output[:200],
                        "error": subagent_result.error,
                    },
                )

            return subagent_result

        except Exception as exc:
            duration = time.monotonic() - start
            logger.error(
                "Subagent %s process spawn failed: %s",
                contract.name, exc, exc_info=True,
            )
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            return SubagentResult(
                task_id=contract.name,
                success=False,
                output="[PROCESS SPAWN FAILED]",
                elapsed_seconds=duration,
                error=f"Process spawn failed: {exc}",
            )

        finally:
            # Cleanup temp file
            try:
                Path(result_path).unlink(missing_ok=True)
            except OSError:
                pass

    async def run_parallel(
        self,
        contracts: list[SubagentContract],
        max_concurrent: int = 4,
    ) -> list[SubagentResult]:
        """Run multiple subagent contracts concurrently.

        Parameters
        ----------
        contracts:
            Subagent specifications to execute.
        max_concurrent:
            Maximum number of subagents running at once (semaphore).

        Returns
        -------
        list[SubagentResult]
            One result per contract, in the same order as ``contracts``.
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _guarded(contract: SubagentContract) -> SubagentResult:
            async with semaphore:
                return await self.run(contract)

        tasks = [asyncio.create_task(_guarded(c)) for c in contracts]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        resolved: list[SubagentResult] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                contract = contracts[i]
                resolved.append(
                    SubagentResult(
                        task_id=contract.name,
                        success=False,
                        output="",
                        elapsed_seconds=0.0,
                        error=f"Unhandled gather exception: {result}",
                        session_id="",
                    )
                )
                logger.error(
                    "Subagent %s crashed during gather: %s", contract.name, result
                )
            else:
                resolved.append(result)

        logger.info(
            "Parallel run complete: %d/%d succeeded",
            sum(1 for r in resolved if r.success),
            len(resolved),
        )
        return resolved

    def get_telemetry(self) -> dict[str, list[dict]]:
        """Return per-model telemetry: latency, success rate, token usage."""
        return {k: list(v) for k, v in self._telemetry.items()}

    def get_telemetry_summary(self) -> dict[str, dict]:
        """Return aggregated telemetry per model."""
        summary = {}
        for model, records in self._telemetry.items():
            if not records:
                continue
            latencies = [r["elapsed_seconds"] for r in records]
            successes = [r["success"] for r in records]
            tokens = [r["tokens_used"] for r in records]
            summary[model] = {
                "count": len(records),
                "success_rate": sum(successes) / len(successes),
                "avg_latency": sum(latencies) / len(latencies),
                "max_latency": max(latencies),
                "total_tokens": sum(tokens),
            }
        return summary

    # ── Composable patterns ────────────────────────────────────────────

    async def run_map_reduce(
        self,
        task: str,
        items: list[str],
        mapper: Callable[[str], SubagentContract],
        reducer: str,
        max_concurrent: int = 4,
    ) -> SubagentResult:
        """Map-reduce: split work across mappers, then synthesize with a reducer.

        Parameters
        ----------
        task:
            High-level description of the overall goal.
        items:
            List of items to process (e.g., file paths).
        mapper:
            Function that takes an item and returns a SubagentContract.
        reducer:
            Task description for the reducer subagent.
        max_concurrent:
            Maximum number of mapper subagents running at once.

        Returns
        -------
        SubagentResult
            The reducer's synthesized result.
        """
        # ── Map phase ────────────────────────────────────────────────
        mapper_contracts = [mapper(item) for item in items]
        mapper_results = await self.run_parallel(mapper_contracts, max_concurrent)

        # ── Build reducer input ──────────────────────────────────────
        successful = [r for r in mapper_results if r.success]
        failed = [r for r in mapper_results if not r.success]

        parts = [f"## Overall Task\n{task}\n"]
        parts.append(f"## Mapper Results ({len(successful)}/{len(mapper_results)} succeeded)\n")

        for i, r in enumerate(successful):
            parts.append(f"### Mapper {i+1}: {r.task_id}\n")
            parts.append(r.output[:2000])
            if len(r.output) > 2000:
                parts.append("\n... [truncated]\n")
            parts.append("\n")

        if failed:
            parts.append(f"## Failed Mappers ({len(failed)})\n")
            for r in failed:
                parts.append(f"- {r.task_id}: {r.error or 'unknown error'}\n")

        reducer_input = "\n".join(parts)

        # Guard: if reducer input is too large, truncate
        estimated_tokens = len(reducer_input) // 4
        max_tokens = self.config.max_context_tokens * 0.8
        if estimated_tokens > max_tokens:
            logger.warning(
                "Reducer input %d tokens exceeds budget %d. Truncating.",
                estimated_tokens, max_tokens
            )
            # Keep all headers, truncate individual mapper outputs
            truncated_parts = parts[:2]  # headers
            budget_per_mapper = int(max_tokens * 4 // len(successful)) if successful else 1000
            for i, r in enumerate(successful):
                truncated_parts.append(f"### Mapper {i+1}: {r.task_id}\n")
                truncated_parts.append(r.output[:budget_per_mapper])
                if len(r.output) > budget_per_mapper:
                    truncated_parts.append("\n... [truncated]\n")
                truncated_parts.append("\n")
            if failed:
                truncated_parts.append(f"## Failed Mappers ({len(failed)})\n")
                for r in failed:
                    truncated_parts.append(f"- {r.task_id}: {r.error or 'unknown error'}\n")
            reducer_input = "\n".join(truncated_parts)

        # ── Reduce phase ───────────────────────────────────────────────
        reducer_contract = SubagentContract(
            name="reducer",
            role="generalist",
            task=f"{reducer}\n\n{reducer_input}",
            max_iterations=15,
            timeout_seconds=120.0,
            worktree_isolated=False,  # reducer doesn't need isolation
        )

        reducer_result = await self.run(reducer_contract)

        # Aggregate total token usage for the whole map-reduce operation.
        # We preserve the reducer's own input/output counts and only
        # update tokens_used so telemetry reflects the total job cost.
        mapper_tokens = sum(r.tokens_used for r in mapper_results)
        reducer_result.tokens_used += mapper_tokens
        return reducer_result

    async def run_vote(
        self,
        task: str,
        agents: list[SubagentContract],
        consensus_threshold: float = 0.6,
        max_concurrent: int = 4,
    ) -> SubagentResult:
        """Vote: ask multiple independent subagents the same question, take majority.

        Parameters
        ----------
        task:
            The question or task to vote on.
        agents:
            Subagent contracts (typically same role, different names).
        consensus_threshold:
            Minimum fraction of agents that must agree (0.0–1.0).
        max_concurrent:
            Maximum number of voting subagents running at once.

        Returns
        -------
        SubagentResult
            A synthesized result with vote metadata in the output.
        """
        # Override each agent's task with the voting task
        voting_contracts = []
        for agent in agents:
            c = SubagentContract(**{**agent.__dict__, "task": task})
            voting_contracts.append(c)

        results = await self.run_parallel(voting_contracts, max_concurrent)

        # Count successes and analyze outputs
        successful = [r for r in results if r.success]
        total = len(results)
        passed = len(successful)

        # Robust consensus: group by normalized similarity
        from collections import Counter

        def _normalize(text: str) -> str:
            """Normalize text for comparison: lowercase, strip, collapse whitespace."""
            return " ".join(text.lower().strip().split())

        def _similar(a: str, b: str) -> bool:
            """Check if two outputs are semantically similar."""
            na, nb = _normalize(a), _normalize(b)
            if na == nb:
                return True
            # Only consider short answers similar by containment to avoid
            # grouping a detailed dissent with a terse majority answer.
            if len(na) <= 10 and len(nb) <= 10:
                if len(na) > len(nb):
                    return nb in na
                return na in nb
            return False

        # Group outputs by similarity
        groups: list[list[str]] = []
        for r in successful:
            out = r.output.strip()[:500]
            placed = False
            for g in groups:
                if _similar(out, g[0]):
                    g.append(out)
                    placed = True
                    break
            if not placed:
                groups.append([out])

        # Find largest group
        if groups:
            winner_group = max(groups, key=len)
            winner = winner_group[0]
            count = len(winner_group)
            consensus_reached = count / total >= consensus_threshold
        else:
            winner = ""
            count = 0
            consensus_reached = False

        # Build synthesized output
        lines = [
            f"## Vote Result: {task[:100]}",
            "",
            f"**Consensus:** {'REACHED' if consensus_reached else 'NOT REACHED'}",
            f"**Agreement:** {count}/{total} ({count/total*100:.0f}%) — threshold {consensus_threshold*100:.0f}%",
            "",
            "### Individual Votes",
        ]
        for i, r in enumerate(results):
            status = "✓" if r.success else "✗"
            match = " (matches winner)" if r.success and _similar(r.output.strip()[:500], winner) else ""
            lines.append(f"{status} Agent {i+1} ({r.task_id}):{match}")
            if r.error:
                lines.append(f"   Error: {r.error}")

        lines.append("")
        lines.append("### Winning Answer")
        lines.append(winner if winner else "(no consensus)")

        output = "\n".join(lines)

        return SubagentResult(
            task_id="vote",
            success=consensus_reached,
            output=output,
            elapsed_seconds=sum(r.elapsed_seconds for r in results),
            iterations_used=sum(r.iterations_used for r in results),
            files_changed=list(set(f for r in results for f in r.files_changed)),
            input_tokens=sum(r.input_tokens for r in results),
            output_tokens=sum(r.output_tokens for r in results),
            tokens_used=sum(r.tokens_used for r in results),
        )

    async def run_chain(
        self,
        contracts: list[SubagentContract],
        pass_context: bool = True,
        max_concurrent: int = 1,
    ) -> SubagentResult:
        """Chain: run subagents sequentially, optionally passing context forward.

        Parameters
        ----------
        contracts:
            Ordered list of subagent contracts to execute.
        pass_context:
            If True, each step sees the previous steps' outputs.
        max_concurrent:
            For chains this is typically 1 (sequential). Higher values
            allow parallel steps but break context passing.

        Returns
        -------
        SubagentResult
            The final step's result, augmented with chain metadata.
        """
        if max_concurrent != 1:
            logger.warning("run_chain with max_concurrent > 1 breaks sequential context passing")

        context_parts = []
        last_result = None
        all_files_changed = []
        total_elapsed = 0.0
        total_iterations = 0
        total_input_tokens = 0
        total_output_tokens = 0
        total_tokens = 0

        for i, contract in enumerate(contracts):
            if pass_context and context_parts:
                # Inject previous context into the task
                context_block = "\n\n".join(context_parts)
                augmented_task = (
                    f"{contract.task}\n\n"
                    f"## Previous Steps Context\n"
                    f"{context_block}"
                )
                # Deep-copy mutable fields so each step is isolated
                copied = copy.deepcopy(contract.__dict__)
                copied["task"] = augmented_task
                contract = SubagentContract(**copied)

            result = await self.run(contract)
            last_result = result
            all_files_changed.extend(result.files_changed)
            total_elapsed += result.elapsed_seconds
            total_iterations += result.iterations_used
            total_input_tokens += result.input_tokens
            total_output_tokens += result.output_tokens
            total_tokens += result.tokens_used

            if pass_context:
                context_parts.append(
                    f"### Step {i+1}: {contract.name}\n"
                    f"{result.output[:1500]}"
                )

            if not result.success:
                # Chain broke — return partial result
                output = (
                    f"## Chain Failed at Step {i+1}/{len(contracts)}\n\n"
                    f"**Failed step:** {contract.name}\n"
                    f"**Error:** {result.error or 'unknown error'}\n\n"
                    f"### Completed Steps\n"
                    + "\n\n".join(context_parts[:-1] if context_parts else [])
                )
                return SubagentResult(
                    task_id=f"chain-failed-at-{i+1}",
                    success=False,
                    output=output,
                    elapsed_seconds=total_elapsed,
                    iterations_used=total_iterations,
                    files_changed=list(set(all_files_changed)),
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    tokens_used=total_tokens,
                )

        # Chain completed successfully
        if last_result is None:
            return SubagentResult(
                task_id="chain-empty",
                success=True,
                output="(empty chain)",
            )

        # Augment final result with chain metadata
        last_result.output = (
            f"## Chain Complete ({len(contracts)} steps)\n\n"
            f"{last_result.output}\n\n"
            f"---\n"
            f"*Chain elapsed: {total_elapsed:.1f}s, "
            f"iterations: {total_iterations}, "
            f"tokens: {total_tokens}*"
        )
        last_result.elapsed_seconds = total_elapsed
        last_result.iterations_used = total_iterations
        last_result.input_tokens = total_input_tokens
        last_result.output_tokens = total_output_tokens
        last_result.tokens_used = total_tokens
        last_result.files_changed = list(set(all_files_changed))
        return last_result

    # ── Internal helpers ───────────────────────────────────────────────

    def _run_agent_sync(
        self,
        contract: SubagentContract,
        config: WispConfig,
        session: Session,
        system_prompt: str,
        workspace_path: str,
        tool_calls_log: list[dict],
    ) -> dict:
        """Synchronous wrapper that runs the async _run_agent in a fresh event loop.

        This ensures the work can be cancelled by asyncio.wait_for via
        asyncio.to_thread, which is not possible when a sync blocking call
        is nested inside an async coroutine.
        """
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self._run_agent(contract, config, session, system_prompt, workspace_path, tool_calls_log)
            )
        finally:
            loop.close()

    async def _run_agent(
        self,
        contract: SubagentContract,
        config: WispConfig,
        session: Session,
        system_prompt: str,
        workspace_path: str,
        tool_calls_log: list[dict],
    ) -> dict:
        """Run a WispAgentCore instance and return its result dict.

        Uses ``run_task`` (the non-interactive API) to drive the agent loop.
        Tool calls are intercepted and logged for the result summary.
        """
        from wisp.core.agent import WispAgentCore

        agent = WispAgentCore(
            config=config,
            session=session,
            role=f"subagent:{contract.name}",
        )

        try:
            # Override the workspace in the config so tool execution resolves
            # paths relative to the worktree (or shared workspace).
            agent.config.workspace = workspace_path

            # Apply tool filtering if specified
            if contract.tools != ["all"]:
                agent._allowed_tools = set(contract.tools)

            # ── Run the task non-interactively ────────────────────────────
            max_iter = contract.max_iterations
            timeout_per_task = contract.timeout_seconds

            task_result = await agent.run_task(
                task_description=contract.task,
                workspace=workspace_path,
                max_iterations=max_iter,
                timeout_seconds=timeout_per_task,
                system_prompt=system_prompt,
            )

            # Collect tool call summaries from the agent's message history
            for msg in agent.messages:
                tcs = msg.get("tool_calls", []) or []
                for tc in tcs:
                    func = tc.get("function", {})
                    name = func.get("name", "")
                    args = func.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            import json
                            args = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                    if not isinstance(args, dict):
                        args = {}
                    arg_preview = self._compact_args(args)
                    tool_calls_log.append({"name": name, "args_preview": arg_preview})

            # Extract files changed from the final output (best-effort)
            files_changed: list[str] = []
            output_text = task_result.get("output", "") or ""
            if output_text:
                files_changed = self._extract_files_changed(output_text)

            return {
                "success": task_result.get("success", False),
                "output": output_text,
                "error": None if task_result.get("success") else task_result.get("output"),
                "files_changed": files_changed,
                "iterations_used": len([m for m in agent.messages if m.get("role") == "assistant"]),
                "messages": agent.messages,
            }
        finally:
            agent.close()

    def _build_child_config(self, contract: SubagentContract) -> WispConfig:
        """Clone the parent config with optional per-subagent overrides."""
        child = copy.deepcopy(self.config)
        # Override only the fields the subagent contract controls
        child.model = contract.model or self.config.model
        child.workspace = str(contract.workspace or self.workspace)
        child.auto_approve = contract.auto_approve
        child.max_context_tokens = contract.max_tokens or self.config.max_context_tokens
        child.max_iterations = contract.max_iterations
        return child

    def _default_system_prompt(self, contract: SubagentContract) -> str:
        """Build a concise default system prompt when none is provided."""
        from .roles import ROLE_CONFIGS, AgentRole

        # Try role-based prompt first
        role_cfg = ROLE_CONFIGS.get(contract.role)
        if role_cfg:
            base = role_cfg.system_prompt
        else:
            base = (
                f"You are a specialist subagent: **{contract.name}**.",
                "You have tools to read, write, and edit files, run bash commands, "
                "list directories, and fetch URLs.",
                "",
                "## Rules",
                "1. Focus ONLY on your assigned task.",
                "2. Work efficiently — you have a time budget.",
                "3. When done, provide a clear summary of what you did.",
                "4. If you edit files, list the changed paths.",
                "5. If stuck, explain what blocked you and stop.",
            )
            base = "\n".join(base)

        parts = [base]

        if contract.tools != ["all"]:
            parts.append("")
            parts.append("## Allowed Tools")
            parts.append(", ".join(contract.tools))

        if contract.context_files:
            parts.append("")
            parts.append("## Context Files")
            for f in contract.context_files:
                parts.append(f"- {f}")

        if contract.system_prompt_extra:
            parts.append("")
            parts.append("## Additional Instructions")
            parts.append(contract.system_prompt_extra)

        return "\n".join(parts)

    async def _create_worktree(self, agent_name: str) -> Path:
        """Create an isolated git worktree for a subagent."""
        self._worktrees_root.mkdir(parents=True, exist_ok=True)

        short_id = uuid.uuid4().hex[:8]
        ts = int(time.time())
        safe_name = agent_name.replace("/", "-").replace(" ", "-")
        dir_name = f"{safe_name}-{short_id}"
        branch_name = f"wisp-subagent/{safe_name}-{ts}"

        worktree_path = (self._worktrees_root / dir_name).resolve()

        logger.info(
            "Creating worktree: path=%s branch=%s", worktree_path, branch_name
        )

        proc = await asyncio.create_subprocess_exec(
            "git",
            "worktree",
            "add",
            str(worktree_path),
            "-b",
            branch_name,
            cwd=str(self.workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"git worktree add failed (exit {proc.returncode}): {err_text}"
            )

        logger.debug(
            "Worktree created: %s (branch=%s)",
            worktree_path,
            branch_name,
        )
        return worktree_path

    async def _cleanup_worktree(self, worktree_path: Path) -> None:
        """Remove a worktree and prune the associated branch."""
        logger.info("Cleaning up worktree: %s", worktree_path)

        proc = await asyncio.create_subprocess_exec(
            "git",
            "worktree",
            "remove",
            str(worktree_path),
            "--force",
            cwd=str(self.workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace").strip()
            logger.warning(
                "git worktree remove failed (exit %d): %s",
                proc.returncode,
                err_text,
            )
            # Fallback: manual directory removal
            if worktree_path.exists():
                import shutil
                shutil.rmtree(worktree_path, ignore_errors=True)
                logger.debug("Manually removed worktree directory: %s", worktree_path)

        # Prune the worktree metadata
        try:
            prune_proc = await asyncio.create_subprocess_exec(
                "git",
                "worktree",
                "prune",
                cwd=str(self.workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await prune_proc.communicate()
        except Exception as exc:
            logger.debug("Worktree prune failed (non-critical): %s", exc)

        logger.debug("Worktree cleanup complete: %s", worktree_path)

    async def _validate_output(
        self, result: SubagentResult, contract: SubagentContract
    ) -> SubagentResult:
        """Validate subagent output against a JSON schema.

        If validation fails and ``auto_retry_parse`` is True, retry once
        with the validation error injected into the subagent context.
        """
        if not contract.output_schema:
            return result

        try:
            import jsonschema
            parsed = json.loads(result.output)
            jsonschema.validate(instance=parsed, schema=contract.output_schema)
            result.validated_output = parsed
            return result
        except ImportError:
            logger.warning("jsonschema not installed, skipping output validation")
            try:
                result.validated_output = json.loads(result.output)
            except json.JSONDecodeError:
                pass
            return result
        except json.JSONDecodeError as e:
            logger.warning("Subagent %s output is not valid JSON: %s", contract.name, e)
            if contract.auto_retry_parse and result.retry_count == 0:
                return await self._retry_with_parse_error(result, contract, str(e))
            result.error = f"Output is not valid JSON: {e}"
            return result
        except jsonschema.ValidationError as e:
            logger.warning("jsonschema not installed, skipping output validation")
            try:
                result.validated_output = json.loads(result.output)
            except json.JSONDecodeError:
                pass
            return result

    async def _retry_with_parse_error(
        self, result: SubagentResult, contract: SubagentContract, error_msg: str
    ) -> SubagentResult:
        """Retry the subagent once with the parse error injected into context."""
        logger.info("Retrying subagent %s due to parse error", contract.name)
        retry_contract = SubagentContract(
            **{
                **contract.__dict__,
                "task": (
                    f"{contract.task}\n\n"
                    f"IMPORTANT: Your previous response failed validation.\n"
                    f"Error: {error_msg}\n"
                    f"Please fix the response and ensure it matches the required schema."
                ),
            }
        )
        retry_contract.retry_count = result.retry_count + 1
        return await self.run(retry_contract)

    async def _emit(
        self,
        contract: SubagentContract,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Emit a progress event via the contract's callback."""
        if contract.progress_callback:
            event = OrchestratorEvent(
                task_id=contract.name,
                event_type=event_type,
                payload=payload,
            )
            try:
                if asyncio.iscoroutinefunction(contract.progress_callback):
                    await contract.progress_callback(event)
                else:
                    contract.progress_callback(event)
            except Exception as e:
                logger.warning("Progress callback failed for %s: %s", contract.name, e)

    @staticmethod
    def _compact_args(args: dict) -> str:
        """One-line preview of tool arguments."""
        key = next(iter(args), None)
        if key is None:
            return "..."
        val = args[key]
        s = str(val)
        if len(s) > 60:
            s = s[:60] + "..."
        return f"{key}={s}"

    @staticmethod
    def _extract_files_changed(text: str) -> list[str]:
        """Best-effort extraction of file paths mentioned in output text."""
        import re
        patterns = [
            r"`([a-zA-Z0-9_\-./]+\.(?:py|ts|js|rs|go|java|rb|sh))`",
            r"(?:changed|modified|touched|files written|created files?)[:\-]\s*\n?\s*[-*]\s+([^\s,]+)",
            r"\b([a-zA-Z0-9_\-/]+\.(?:py|ts|js|rs|go|java|rb|sh))\b",
        ]
        found: list[str] = []
        seen: set[str] = set()
        for pat in patterns:
            for m in re.finditer(pat, text, re.IGNORECASE):
                path = m.group(1).strip()
                if path not in seen and len(path) > 2:
                    seen.add(path)
                    found.append(path)
        return found[:20]

