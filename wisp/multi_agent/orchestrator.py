"""Async swarm orchestrator — spawns agents, assigns tasks, collects results.

v2: async execution, multi-agent per role, streaming progress, retry with backoff.

The orchestrator is the conductor of the multi-agent system:
1. Parses a high-level goal
2. Spawns a Planner to decompose it into subtasks
3. Assigns subtasks to specialized agents (Coder, Tester, Reviewer, etc.)
4. Manages file locking to prevent conflicts
5. Collects results, retries failures, synthesizes final answer
6. Streams progress events for live UI
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from wisp.agent import WispAgent
from wisp.config import WispConfig

from .protocol import AgentEvent, EventType, TaskAssignment, TaskResult
from .registry import AgentRegistry, AgentRecord, AgentStatus
from .bus import MessageBus
from .roles import AgentRole, ROLE_CONFIGS
from .agent_factory import AgentFactory
from .workspace_lock import WorkspaceLock
from .task import EventKind, OrchestratorEvent

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

        thread = __import__("threading").Thread(target=_target)
        thread.start()
        thread.join(timeout=300)

        if thread.is_alive():
            logger.warning("Swarm thread did not finish within 5 minutes — returning timeout result")
            self._shutdown = True
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

        async def wait_then_run(idx: int) -> None:
            """Poll until dependencies resolved, then run."""
            deps = subtasks[idx].get("dependencies", [])
            while True:
                if self._shutdown:
                    failed.add(idx)
                    return
                if all(d in completed for d in deps):
                    await run_task(idx)
                    return
                if any(d in failed for d in deps):
                    failed.add(idx)
                    return
                await asyncio.sleep(0.1)

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
