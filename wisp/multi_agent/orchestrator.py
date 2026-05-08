"""Swarm orchestrator — spawns agents, assigns tasks, collects results.

The orchestrator is the conductor of the multi-agent system:
1. Parses a high-level goal
2. Spawns a Planner to decompose it into subtasks
3. Assigns subtasks to specialized agents (Coder, Tester, Reviewer, etc.)
4. Manages file locking to prevent conflicts
5. Collects results and synthesizes a final answer
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed
from dataclasses import dataclass, field
from typing import Any, Optional

from wisp.agent import WispAgent
from wisp.config import WispConfig

from .protocol import AgentEvent, EventType, TaskAssignment, TaskResult
from .registry import AgentRegistry, AgentRecord, AgentStatus
from .bus import MessageBus
from .roles import AgentRole, ROLE_CONFIGS
from .agent_factory import AgentFactory
from .workspace_lock import WorkspaceLock

logger = logging.getLogger(__name__)


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
    """Manages a swarm of specialized agents working toward a shared goal."""

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

        # Subscribe to all result events
        self.bus.subscribe(self._on_task_result, event_type=EventType.TASK_RESULT)
        self.bus.subscribe(self._on_task_failed, event_type=EventType.TASK_FAILED)
        self.bus.subscribe(self._on_heartbeat, event_type=EventType.AGENT_HEARTBEAT)

    # ── Lifecycle ──────────────────────────────────────────────────────

    def spawn_agents(self, roles: list[str]) -> list[str]:
        """Spawn one agent per role and return their IDs."""
        ids: list[str] = []
        for role in roles:
            agent_id = f"{role}-{uuid.uuid4().hex[:6]}"
            agent = self.factory.create(role, agent_id, model=self.config.model)

            self._agents[agent_id] = agent
            self.registry.register(
                AgentRecord(agent_id=agent_id, role=role, status=AgentStatus.IDLE)
            )

            # Emit started event
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

    def _plan(self, goal: str, available_roles: Optional[list[str]] = None) -> tuple[str, list[dict]]:
        """Use the Planner role (or the parent agent) to break down a goal.

        Args:
            goal: High-level task description.
            available_roles: Which roles are available (only tasks for these roles).

        Returns:
            (plan_text, subtasks) where subtasks is a list of dicts with
            keys: role, description, expected_output, dependencies.
        """
        planner = self.factory.create(AgentRole.PLANNER, "planner-" + uuid.uuid4().hex[:6])

        if available_roles is None:
            available_roles = [AgentRole.CODER, AgentRole.REVIEWER, AgentRole.TESTER, AgentRole.RESEARCHER]

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
            content = raw.get("message", {}).get("content", "") if isinstance(raw.get("message"), dict) else ""
            # Try to extract JSON (strip markdown fences if present)
            data = self._extract_json(content)
            if data is not None:
                plan = data.get("plan", content)
                subtasks = data.get("subtasks", [])
            else:
                # Fallback: wrap whole response as a single task
                plan = content
                subtasks = [{"role": "coder", "description": goal, "expected_output": "working code", "dependencies": []}]
        except Exception as e:
            logger.warning("Planner failed: %s. Using fallback single-task plan.", e)
            plan = goal
            subtasks = [{"role": "coder", "description": goal, "expected_output": "working code", "dependencies": []}]

        return plan, subtasks

    def _extract_json(self, content: str) -> Optional[dict]:
        """Extract JSON from model output, handling markdown fences."""
        content = content.strip()
        # Try stripping markdown fences
        if content.startswith("```"):
            lines = content.splitlines()
            # Remove opening fence
            if lines[0].startswith("```"):
                lines = lines[1:]
            # Remove closing fence
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Try finding JSON object/array inside the text
            for match in re.finditer(r"(\{[\s\S]*\}|\[[\s\S]*\])", content):
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue
            return None

    # ── Execution ──────────────────────────────────────────────────────

    def run(self, goal: str, roles: Optional[list[str]] = None) -> SwarmResult:
        """Execute a goal using the swarm.

        Args:
            goal: High-level task description.
            roles: Which roles to spawn (default: coder, reviewer, tester, researcher).

        Returns:
            SwarmResult with plan, individual results, and synthesized output.
        """
        start = time.monotonic()
        if roles is None:
            roles = [AgentRole.CODER, AgentRole.REVIEWER, AgentRole.TESTER, AgentRole.RESEARCHER]

        # Spawn agents
        agent_ids = self.spawn_agents(roles)
        logger.info("Swarm spawned %d agents for goal: %s", len(agent_ids), goal)

        # Plan — only suggest tasks for roles we actually have
        plan_fn = self._plan
        positional_params = [
            param
            for param in inspect.signature(plan_fn).parameters.values()
            if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        if len(positional_params) >= 2:
            plan, subtasks = plan_fn(goal, roles)
        else:
            plan, subtasks = plan_fn(goal)
        logger.info("Plan generated with %d subtasks", len(subtasks))

        # Execute subtasks with dependency resolution
        results = self._execute_subtasks(subtasks, agent_ids)

        # Synthesize final answer
        final_output = self._synthesize(goal, plan, results)

        elapsed = time.monotonic() - start
        all_files = []
        for r in results:
            all_files.extend(r.files_changed)

        self.stop_all()

        return SwarmResult(
            success=all(r.success for r in results) or len(results) == 0,
            goal=goal,
            plan=plan,
            agent_results=results,
            final_output=final_output,
            elapsed_seconds=elapsed,
            files_changed=sorted(set(all_files)),
        )

    def _execute_subtasks(self, subtasks: list[dict], agent_ids: list[str]) -> list[TaskResult]:
        """Execute subtasks respecting dependencies, with parallelization."""
        results: dict[int, TaskResult] = {}
        completed = set()
        failed = set()

        # Map role to available agent IDs
        role_to_agents: dict[str, list[str]] = {}
        for aid in agent_ids:
            rec = self.registry.get(aid)
            if rec:
                role_to_agents.setdefault(rec.role, []).append(aid)

        pending = set(range(len(subtasks)))

        with ThreadPoolExecutor(max_workers=self.max_parallel) as executor:
            futures = {}

            while pending or futures:
                # Launch ready tasks
                for idx in list(pending):
                    task = subtasks[idx]
                    deps = task.get("dependencies", [])
                    if all(d in completed for d in deps) and any(d in failed for d in deps) is False:
                        # Check if any dependency failed
                        if any(d in failed for d in deps):
                            failed.add(idx)
                            pending.discard(idx)
                            continue

                        role = task.get("role", "coder")
                        available = role_to_agents.get(role, [])
                        if not available:
                            logger.warning("No agent available for role %s, skipping task %d", role, idx)
                            failed.add(idx)
                            pending.discard(idx)
                            continue

                        # Pick first idle agent
                        agent_id = available[0]
                        rec = self.registry.get(agent_id)
                        if rec and rec.status == AgentStatus.WORKING:
                            # Agent busy, try next or wait
                            continue

                        pending.discard(idx)
                        self.registry.update_status(agent_id, AgentStatus.WORKING, task=task.get("description", ""))

                        assignment = TaskAssignment(
                            task_id=f"task-{idx}",
                            description=task.get("description", ""),
                            expected_output=task.get("expected_output", ""),
                            max_iterations=ROLE_CONFIGS.get(role, ROLE_CONFIGS[AgentRole.CODER]).max_iterations,
                            timeout_seconds=ROLE_CONFIGS.get(role, ROLE_CONFIGS[AgentRole.CODER]).timeout_seconds,
                        )

                        future = executor.submit(self._run_agent_task, agent_id, assignment)
                        futures[future] = idx

                if not futures:
                    break

                # Wait for at least one future to complete, then launch more
                for future in as_completed(futures):
                    idx = futures.pop(future)
                    try:
                        result = future.result()
                        results[idx] = result
                        if result.success:
                            completed.add(idx)
                        else:
                            failed.add(idx)
                    except Exception as e:
                        logger.error("Task %d failed with exception: %s", idx, e)
                        results[idx] = TaskResult(
                            task_id=f"task-{idx}",
                            success=False,
                            output="",
                            error=str(e),
                        )
                        failed.add(idx)
                    break  # Process one at a time to allow launching new ready tasks

        return [results.get(i, TaskResult(task_id=f"task-{i}", success=False, output="")) for i in range(len(subtasks))]

    def _run_agent_task(self, agent_id: str, assignment: TaskAssignment) -> TaskResult:
        """Run a single task on a single agent."""
        agent = self._agents.get(agent_id)
        if not agent:
            return TaskResult(
                task_id=assignment.task_id,
                success=False,
                output="",
                error=f"Agent {agent_id} not found",
            )

        # Emit assignment
        self.bus.emit(assignment.to_event(source="orchestrator", target=agent_id))

        import asyncio
        start = time.monotonic()
        try:
            task_result = agent.run_task(
                task_description=assignment.description,
                workspace=agent.config.workspace or ".",
                max_iterations=assignment.max_iterations,
                timeout_seconds=assignment.timeout_seconds,
            )
            if asyncio.iscoroutine(task_result):
                loop = asyncio.new_event_loop()
                try:
                    task_result = loop.run_until_complete(task_result)
                finally:
                    loop.close()
            elapsed = time.monotonic() - start
            content = task_result.get("output", "")

            # Extract file changes from the agent's change tracker (accurate)
            files_changed = agent.change_tracker.get_changed_files()
            # Also do best-effort regex extraction from output as fallback
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
                success=False,
                output="",
                error=str(e),
                elapsed_seconds=elapsed,
            )

        # Update registry with files this agent touched
        for path in result.files_changed:
            self.registry.claim_file(agent_id, path)
            self.bus.emit(
                AgentEvent(
                    event_type=EventType.FILE_CLAIMED,
                    source_agent=agent_id,
                    payload={"path": path, "task_id": assignment.task_id},
                )
            )

        # Emit result
        self.bus.emit(result.to_event(source=agent_id, target="orchestrator"))
        self.registry.update_status(agent_id, AgentStatus.IDLE, task=None)

        # Release all file locks held by this agent
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
        import re
        paths = []
        # Match common patterns like `write_file(path=...)` or `Edited foo.py`
        for match in re.finditer(r'["\']([\w/\\.-]+\.(py|js|ts|java|rs|go|c|cpp|h|md|json|yaml|yml|toml))["\']', content):
            paths.append(match.group(1))
        return sorted(set(paths))

    def _synthesize(self, goal: str, plan: str, results: list[TaskResult]) -> str:
        """Combine all agent outputs into a coherent final response."""
        passed = sum(1 for r in results if r.success)
        failed = sum(1 for r in results if not r.success)
        all_files = sorted(set(
            f for r in results for f in r.files_changed
        ))

        lines = [
            f"## Swarm Result: {goal}",
            "",
            f"**{passed}/{len(results)} tasks passed**"
            + (f", {failed} failed" if failed else ""),
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
        result = TaskResult.from_event(event)
        rec = self.registry.get(event.source_agent)
        if rec:
            rec.total_tasks_completed += 1

    def _on_task_failed(self, event: AgentEvent) -> None:
        rec = self.registry.get(event.source_agent)
        if rec:
            rec.total_tasks_failed += 1

    def _on_heartbeat(self, event: AgentEvent) -> None:
        self.registry.heartbeat(event.source_agent)
