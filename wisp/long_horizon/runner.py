"""Long-horizon task runner — sequential step execution with checkpointing.

The LongHorizonRunner wraps WispAgentCore.run_task() to execute multi-step
tasks with automatic checkpointing, replanning on failure, and progress
reporting.

Usage:
    runner = LongHorizonRunner(agent=agent)
    async for event in runner.run("Migrate Flask to FastAPI"):
        print(event)

    # Resume after crash
    async for event in runner.run("", resume_from="task-20250115-143022"):
        print(event)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from wisp.core.agent import WispAgentCore

from wisp.core.events import (
    AgentEvent,
    task_started,
    task_step_started,
    task_step_completed,
    task_step_failed,
    task_replanning,
    task_paused,
    task_resumed,
    task_completed,
    task_failed,
    task_progress,
    task_escalation,
)
from wisp.long_horizon.state import TaskState, Step, Plan, TaskStatus, StepStatus
from wisp.long_horizon.storage import TaskStorage
from wisp.long_horizon.errors import (
    TaskError,
    StepTimeoutError,
    MaxIterationsError,
    MaxReplansError,
    EscalationError,
    ReplanError,
)

logger = logging.getLogger(__name__)

# Default escalation patterns that require human intervention
DEFAULT_ESCALATION_PATTERNS = [
    "git push failed",
    "permission denied",
    "merge conflict",
    "test suite broken",
    "requires manual review",
    "authentication failed",
    "unauthorized",
]


class LongHorizonRunner:
    """Execute long-horizon tasks with checkpointing and replanning.

    Attributes:
        agent: The WispAgentCore instance used to execute each step.
        storage: TaskStorage for checkpoint persistence.
        max_iterations: Maximum total steps before forced failure.
        step_timeout: Per-step timeout in seconds.
        replan_on_failure: Whether to replan when a step fails.
        max_replans: Maximum number of replanning cycles.
        progress_callback: Optional callback invoked after every step.
        escalation_patterns: Substrings that trigger human escalation.
    """

    def __init__(
        self,
        agent: WispAgentCore,
        storage: TaskStorage | None = None,
        max_iterations: int = 100,
        step_timeout: float = 300.0,
        replan_on_failure: bool = True,
        max_replans: int = 3,
        progress_callback: Callable[[TaskState], None] | None = None,
        escalation_patterns: list[str] | None = None,
    ):
        self.agent = agent
        self.storage = storage or TaskStorage()
        self.max_iterations = max_iterations
        self.step_timeout = step_timeout
        self.replan_on_failure = replan_on_failure
        self.max_replans = max_replans
        self.progress_callback = progress_callback
        self.escalation_patterns = escalation_patterns or DEFAULT_ESCALATION_PATTERNS.copy()

    # ── Public API ────────────────────────────────────────────────────

    async def run(
        self,
        goal: str = "",
        resume_from: str | None = None,
        workspace: str = ".",
    ) -> AsyncIterator[AgentEvent]:
        """Run a long-horizon task, yielding progress events.

        Args:
            goal: Task description. Ignored if resume_from is provided.
            resume_from: Task ID to resume from checkpoint.
            workspace: Working directory for tool execution.

        Yields:
            AgentEvent for each state change (step started, completed, failed, etc.)
        """
        # Resolve or create state
        if resume_from:
            state = self.storage.load(resume_from)
            if state is None:
                yield task_failed(resume_from, "", f"No checkpoint found for {resume_from}")
                return
            yield task_resumed(state.task_id, state.current_step_index)
        else:
            if not goal:
                yield task_failed("", "", "No goal provided and no task to resume")
                return
            state = await self._create_initial_state(goal, workspace)
            yield task_started(state.task_id, goal, state.total_steps)

        # Validate state
        if state.is_complete or state.is_failed:
            yield task_progress(state.task_id, state.current_step_index, state.total_steps, state.status.value)
            return

        state.set_status(TaskStatus.RUNNING)
        self.storage.save(state)

        try:
            async for event in self._run_loop(state, workspace):
                yield event
        except asyncio.CancelledError:
            logger.info("Task %s cancelled, saving checkpoint", state.task_id)
            state.set_status(TaskStatus.PAUSED)
            self.storage.save(state)
            yield task_paused(state.task_id, "Cancelled by user")
            raise

    async def pause(self, task_id: str) -> bool:
        """Pause a running task by updating its checkpoint.

        The task must be resumed with a new runner instance.
        Returns True if the task was found and paused.
        """
        state = self.storage.load(task_id)
        if state is None:
            return False
        state.set_status(TaskStatus.PAUSED)
        self.storage.save(state)
        return True

    # ── State creation ────────────────────────────────────────────────

    async def _create_initial_state(self, goal: str, workspace: str) -> TaskState:
        """Create a new TaskState with an initial plan from the agent."""
        state = TaskState.create(
            goal=goal,
            max_iterations=self.max_iterations,
            step_timeout=self.step_timeout,
            replan_on_failure=self.replan_on_failure,
            max_replans=self.max_replans,
        )

        # Generate initial plan via agent
        plan_result = await self._generate_plan(goal, workspace)
        steps = self._parse_plan(plan_result)
        state.plan.steps = steps
        state.plan.created_at = state.created_at
        return state

    # ── Main loop ─────────────────────────────────────────────────────

    async def _run_loop(
        self,
        state: TaskState,
        workspace: str,
    ) -> AsyncIterator[AgentEvent]:
        """Execute steps sequentially until completion or failure."""
        while state.current_step_index < len(state.plan.steps):
            step = state.plan.steps[state.current_step_index]

            # Check max iterations
            if state.current_step_index >= state.max_iterations:
                state.set_status(TaskStatus.FAILED)
                self.storage.save(state)
                yield task_failed(state.task_id, state.goal, f"Max iterations ({state.max_iterations}) exceeded")
                return

            # Emit step started
            step.status = StepStatus.RUNNING
            yield task_step_started(
                state.task_id, step.id, state.current_step_index, step.description
            )
            yield task_progress(
                state.task_id, state.current_step_index, state.total_steps, "running"
            )
            if self.progress_callback:
                self.progress_callback(state)

            # Execute step with timeout
            step_start = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    self._execute_step(step, state, workspace),
                    timeout=state.step_timeout,
                )
                duration_ms = int((time.monotonic() - step_start) * 1000)
                state.mark_step_completed(result, duration_ms)
                yield task_step_completed(
                    state.task_id, step.id, state.current_step_index - 1, result, duration_ms
                )
                logger.info("Step %s completed in %dms", step.id, duration_ms)

            except asyncio.TimeoutError:
                duration_ms = int((time.monotonic() - step_start) * 1000)
                error_msg = f"Timeout after {state.step_timeout}s"
                state.mark_step_failed(error_msg)
                yield task_step_failed(
                    state.task_id, step.id, state.current_step_index, error_msg, step.attempt_count
                )
                logger.warning("Step %s timed out after %ds", step.id, state.step_timeout)

                # Handle failure
                async for event in self._handle_step_failure(state, workspace):
                    yield event
                if state.is_failed:
                    return
                continue

            except Exception as e:
                duration_ms = int((time.monotonic() - step_start) * 1000)
                error_msg = f"{type(e).__name__}: {e}"
                state.mark_step_failed(error_msg)
                yield task_step_failed(
                    state.task_id, step.id, state.current_step_index, error_msg, step.attempt_count
                )
                logger.exception("Step %s failed: %s", step.id, error_msg)

                # Handle failure
                async for event in self._handle_step_failure(state, workspace):
                    yield event
                if state.is_failed:
                    return
                continue

            # Checkpoint after every step
            self.storage.save(state)

        # All steps completed
        state.set_status(TaskStatus.COMPLETED)
        self.storage.save(state)
        yield task_completed(
            state.task_id, state.goal, state.completed_count, state.total_steps
        )
        yield task_progress(state.task_id, state.total_steps, state.total_steps, "completed")

    # ── Step execution ────────────────────────────────────────────────

    async def _execute_step(self, step: Step, state: TaskState, workspace: str) -> str:
        """Execute a single step using the agent.

        Builds rich context from accumulated results and delegates to
        WispAgentCore.run_task().
        """
        context = self._build_step_context(step, state)
        result = await self.agent.run_task(
            context,
            workspace=workspace,
            max_iterations=10,
            timeout_seconds=state.step_timeout,
        )
        if not result.get("success", True):
            raise TaskError(result.get("output", "Step execution failed"))
        return result.get("output", "")

    def _build_step_context(self, step: Step, state: TaskState) -> str:
        """Build rich context for step execution."""
        lines = [
            f"Task: {state.goal}",
            f"Current step ({state.current_step_index + 1}/{state.total_steps}): {step.description}",
        ]

        # Add accumulated context if available
        if state.accumulated_context:
            lines.append(f"\nPrevious findings:\n{state.accumulated_context}")

        # Add recent completed steps
        recent = state.completed_steps[-3:] if state.completed_steps else []
        if recent:
            lines.append("\nRecent completed steps:")
            for r in recent:
                lines.append(f"- {r.step_id}: {r.result[:300]}")

        # Add current step's dependencies if any
        if step.dependencies:
            lines.append(f"\nThis step depends on: {', '.join(step.dependencies)}")
            for dep_id in step.dependencies:
                dep_result = next(
                    (r for r in state.completed_steps if r.step_id == dep_id), None
                )
                if dep_result:
                    lines.append(f"  {dep_id}: {dep_result.result[:200]}")

        return "\n".join(lines)

    # ── Failure handling ──────────────────────────────────────────────

    async def _handle_step_failure(
        self,
        state: TaskState,
        workspace: str,
    ) -> AsyncIterator[AgentEvent]:
        """Handle a failed step: retry, replan, or escalate."""
        step = state.current_step
        if step is None:
            return

        # Check escalation patterns
        if self._should_escalate(step.error):
            state.set_status(TaskStatus.FAILED)
            self.storage.save(state)
            yield task_escalation(
                state.task_id, step.id,
                f"Step '{step.description}' failed with escalation pattern: {step.error}",
                ["continue", "replan", "skip", "abort"],
            )
            yield task_failed(state.task_id, state.goal, f"Escalation required: {step.error}")
            return

        # Check if we can retry this step
        if step.attempt_count < step.max_attempts:
            logger.info("Retrying step %s (attempt %d/%d)", step.id, step.attempt_count, step.max_attempts)
            # Step will be retried on next loop iteration
            # (current_step_index not advanced, status reset to pending)
            step.status = StepStatus.PENDING
            state.touch()
            self.storage.save(state)
            return

        # Check if we can replan
        if state.replan_on_failure and len(state.replan_history) < state.max_replans:
            yield task_replanning(
                state.task_id,
                f"Step '{step.description}' failed after {step.attempt_count} attempts",
                state.plan_version,
                state.plan_version + 1,
            )
            try:
                new_plan = await self._replan(state, workspace)
                state.set_plan(new_plan)
                self.storage.save(state)
                logger.info("Replanning successful: version %d", new_plan.version)
                return
            except ReplanError as e:
                logger.error("Replanning failed: %s", e)
                # Fall through to failure

        # Max retries and replans exhausted
        state.set_status(TaskStatus.FAILED)
        self.storage.save(state)
        yield task_failed(
            state.task_id, state.goal,
            f"Step '{step.description}' failed after {step.attempt_count} attempts and {len(state.replan_history)} replans"
        )

    def _should_escalate(self, error: str) -> bool:
        """Check if an error message matches escalation patterns."""
        error_lower = error.lower()
        return any(pattern.lower() in error_lower for pattern in self.escalation_patterns)

    # ── Replanning ────────────────────────────────────────────────────

    async def _replan(self, state: TaskState, workspace: str) -> Plan:
        """Generate a new plan for remaining work."""
        # Build replanning prompt
        completed_summary = "\n".join(
            f"- {r.step_id}: {r.result[:300]}" for r in state.completed_steps
        ) or "None"

        remaining = state.plan.steps[state.current_step_index:]
        remaining_summary = "\n".join(
            f"- {s.id}: {s.description}" for s in remaining
        ) or "None"

        failed_step = state.current_step
        failed_info = ""
        if failed_step:
            failed_info = (
                f"\nCurrent step that failed:\n"
                f"  ID: {failed_step.id}\n"
                f"  Description: {failed_step.description}\n"
                f"  Failure reason: {failed_step.error}\n"
                f"  Attempts: {failed_step.attempt_count}/{failed_step.max_attempts}"
            )

        prompt = (
            f"You are replanning a long-horizon task. Here is the current state:\n\n"
            f"Original goal: {state.goal}\n"
            f"Completed steps ({state.completed_count}):\n{completed_summary}\n"
            f"{failed_info}\n"
            f"\nRemaining work (old plan):\n{remaining_summary}\n\n"
            f"Create a NEW plan for the remaining work. Requirements:\n"
            f"- Be specific and actionable\n"
            f"- Break complex steps into smaller ones\n"
            f"- Account for the failure (don't repeat the same approach)\n"
            f"- Include verification/validation steps\n"
            f"- Return as a numbered list, one step per line\n"
            f"- Each step should be independently verifiable"
        )

        # Compact context if needed
        if state.context_token_count > 4000:
            prompt = await self._compact_context(state) + "\n\n" + prompt

        try:
            result = await self.agent.run_task(
                prompt,
                workspace=workspace,
                max_iterations=5,
                timeout_seconds=60,
            )
        except Exception as e:
            raise ReplanError(f"Agent failed during replanning: {type(e).__name__}: {e}")

        if not result.get("success"):
            raise ReplanError(f"Agent failed to generate new plan: {result.get('output', 'unknown error')}")

        steps = self._parse_plan(result.get("output", ""))
        if not steps:
            raise ReplanError("Agent returned empty plan")

        return Plan(
            version=state.plan_version + 1,
            steps=steps,
            created_at=state.updated_at,
            reason=f"replan_after_{failed_step.id}_failed" if failed_step else "replan",
        )

    async def _compact_context(self, state: TaskState) -> str:
        """Summarize completed steps to free context window."""
        prompt = (
            f"Summarize these {len(state.completed_steps)} completed steps "
            f"into key findings and decisions for the remaining work:\n"
            + "\n".join(f"- {r.step_id}: {r.result[:300]}" for r in state.completed_steps)
        )
        result = await self.agent.run_task(prompt, max_iterations=2, timeout_seconds=30)
        summary = result.get("output", "")
        state.accumulated_context = summary
        # Rough token estimate: ~4 chars per token
        state.context_token_count = len(summary) // 4
        return f"Previous findings (summarized):\n{summary}"

    # ── Plan parsing ──────────────────────────────────────────────────

    def _parse_plan(self, text: str) -> list[Step]:
        """Parse a numbered list of steps from agent output.

        Handles formats like:
        1. First step
        2. Second step
        - Step three
        * Step four
        """
        steps: list[Step] = []
        for i, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            # Match numbered items: "1. Step" or "1) Step"
            # Match bullet items: "- Step" or "* Step"
            import re
            match = re.match(r"^(?:\d+[.):]\s+|[-*]\s+)(.+)$", line)
            if match:
                desc = match.group(1).strip()
                if desc:
                    steps.append(Step(
                        id=f"step-{len(steps) + 1}",
                        description=desc,
                    ))
        return steps

    async def _generate_plan(self, goal: str, workspace: str) -> str:
        """Ask the agent to generate an initial plan."""
        prompt = (
            f"Create a detailed, actionable plan for: {goal}\n\n"
            f"Requirements:\n"
            f"- Break into concrete, verifiable steps\n"
            f"- Number each step\n"
            f"- Each step should be independently executable\n"
            f"- Include verification steps where appropriate\n"
            f"- Return as a numbered list, one step per line"
        )
        result = await self.agent.run_task(
            prompt,
            workspace=workspace,
            max_iterations=3,
            timeout_seconds=60,
        )
        return result.get("output", "")
