"""SubagentOrchestrator — thin coordinator for subagent execution.

Delegates all responsibilities to focused internal classes:
- ``SubagentRunner`` — direct async execution (no nested loops)
- ``BudgetTracker`` — token accounting
- ``ResultCache`` — result caching with TTL
- ``WorktreeManager`` — git worktree lifecycle
- ``Telemetry`` — metrics collection
- ``Persistence`` — JSONL audit logging
- ``_patterns`` — map-reduce, vote, chain workflows
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from wisp.agent import WispAgent
from wisp.config import WispConfig

from ._budget_tracker import BudgetTracker
from ._persistence import Persistence
from ._result_cache import ResultCache
from ._runner import SubagentRunner
from ._telemetry import Telemetry
from ._worktree_manager import WorktreeManager
from .roles import ROLE_CONFIGS
from .task import EventKind, OrchestratorEvent, SubagentContract, SubagentResult

logger = logging.getLogger(__name__)

# Maximum subagent nesting depth to prevent recursive explosion
MAX_SUBAGENT_DEPTH = 2
MAX_SUBAGENT_BRANCHING = 3


class SubagentOrchestrator:
    """Unified orchestrator for single, parallel, and composable subagent execution.

    Thin coordinator that composes focused subsystems. All heavy logic
    has been extracted to internal modules.
    """

    def __init__(
        self,
        parent_agent: Optional[WispAgent] = None,
        config: Optional[WispConfig] = None,
        workspace: Optional[Path] = None,
    ):
        self.parent = parent_agent
        self.config = config or (parent_agent.config if parent_agent else WispConfig())
        self.workspace = (
            workspace
            or (Path(self.config.workspace).resolve() if self.config.workspace else None)
            or Path.cwd().resolve()
        )

        # Composed subsystems
        self._budget = BudgetTracker()
        self._cache = ResultCache()
        self._telemetry = Telemetry()
        self._persistence = Persistence(self.workspace / ".wisp" / "subagent_results.jsonl")
        self._worktree_mgr = WorktreeManager(self.workspace)
        self._runner = SubagentRunner(self.config, self.workspace)

        # Concurrency
        self._pool_size = 4
        self._active = 0
        self._semaphore = asyncio.Semaphore(self._pool_size)

    # ── Token budget API ───────────────────────────────────────────────

    def set_global_token_budget(self, budget: Optional[int]) -> None:
        self._budget.set_budget(budget)

    def get_tokens_consumed(self) -> int:
        return self._budget.get_consumed()

    def get_token_budget_remaining(self) -> Optional[int]:
        return self._budget.get_remaining()

    # ── Cache API ──────────────────────────────────────────────────────

    def get_cache_stats(self) -> dict[str, int | float]:
        return self._cache.stats()

    def clear_cache(self) -> None:
        self._cache.clear()

    # ── Telemetry API ──────────────────────────────────────────────────

    def get_telemetry(self) -> dict[str, list[dict]]:
        return self._telemetry.get()

    def get_telemetry_summary(self) -> dict[str, dict]:
        return self._telemetry.summary()

    def aggregate_telemetry(self, results: list[SubagentResult]) -> dict[str, dict]:
        return self._telemetry.aggregate(results)

    # ── Pool API ───────────────────────────────────────────────────────

    def set_pool_size(self, size: int) -> None:
        if size < 1:
            raise ValueError("Pool size must be >= 1")
        self._pool_size = size
        self._semaphore = asyncio.Semaphore(size)
        logger.info("Subagent pool size set to %d", size)

    def get_pool_status(self) -> dict[str, int]:
        return {
            "pool_size": self._pool_size,
            "active_agents": self._active,
            "available_slots": max(0, self._pool_size - self._active),
        }

    # ── Persistence API ────────────────────────────────────────────────

    def get_persisted_results(self, limit: int = 100) -> list[dict]:
        return self._persistence.load(limit)

    def clear_persisted_results(self) -> None:
        self._persistence.clear()

    # ── Public API ─────────────────────────────────────────────────────

    async def run(
        self,
        contract: SubagentContract,
        _bypass_semaphore: bool = False,
    ) -> SubagentResult:
        """Run a single subagent and return its result.

        Never raises — failures are returned as ``SubagentResult(success=False)``.
        """
        # ── Depth guard ────────────────────────────────────────────────
        if contract._subagent_depth >= MAX_SUBAGENT_DEPTH:
            return SubagentResult(
                task_id=contract.name,
                success=False,
                output=f"[DEPTH LIMIT EXCEEDED] Max subagent depth is {MAX_SUBAGENT_DEPTH}",
                error=f"Subagent depth {contract._subagent_depth} exceeds max {MAX_SUBAGENT_DEPTH}",
                elapsed_seconds=0.0,
            )

        # ── Role validation ────────────────────────────────────────────
        role_error = self._validate_role(contract)
        if role_error:
            logger.warning("Role validation failed for %s: %s", contract.name, role_error)
            return SubagentResult(
                task_id=contract.name,
                success=False,
                output=f"[ROLE VALIDATION FAILED] {role_error}",
                error=role_error,
                elapsed_seconds=0.0,
            )

        # ── Contract validation ────────────────────────────────────────
        if contract.timeout_seconds <= 0:
            return SubagentResult(
                task_id=contract.name,
                success=False,
                output="[CONTRACT INVALID] timeout_seconds must be > 0",
                error="timeout_seconds must be > 0",
                elapsed_seconds=0.0,
            )
        if contract.max_iterations <= 0:
            return SubagentResult(
                task_id=contract.name,
                success=False,
                output="[CONTRACT INVALID] max_iterations must be > 0",
                error="max_iterations must be > 0",
                elapsed_seconds=0.0,
            )

        # ── Cache check ────────────────────────────────────────────────
        cached = self._cache.get(contract)
        if cached is not None:
            return cached

        # ── Token budget check ─────────────────────────────────────────
        budget_error = self._budget.check()
        if budget_error:
            logger.warning("Token budget check failed for %s: %s", contract.name, budget_error)
            return SubagentResult(
                task_id=contract.name,
                success=False,
                output=f"[TOKEN BUDGET EXCEEDED] {budget_error}",
                error=budget_error,
                elapsed_seconds=0.0,
            )

        # ── Worktree ───────────────────────────────────────────────────
        worktree_path: Path | None = None
        if contract.worktree_isolated:
            try:
                worktree_path = await self._worktree_mgr.create(contract.name)
            except Exception as exc:
                logger.warning(
                    "Worktree creation failed for %s, falling back to shared workspace: %s",
                    contract.name, exc,
                )
                worktree_path = None

        agent_workspace = str(worktree_path or self.workspace)

        # ── System prompt ──────────────────────────────────────────────
        system = contract.system_prompt or self._default_system_prompt(contract)

        # ── Run with concurrency control ─────────────────────────────
        if _bypass_semaphore:
            self._active += 1
            try:
                result = await self._runner.run(
                    contract=contract,
                    agent_workspace=agent_workspace,
                    system_prompt=system,
                    progress_callback=contract.progress_callback,
                )
            finally:
                self._active -= 1
        else:
            async with self._semaphore:
                self._active += 1
                try:
                    result = await self._runner.run(
                        contract=contract,
                        agent_workspace=agent_workspace,
                        system_prompt=system,
                        progress_callback=contract.progress_callback,
                    )
                finally:
                    self._active -= 1

        # ── Schema validation ────────────────────────────────────────
        if contract.output_schema and result.success:
            result = await self._validate_output(result, contract)

        # ── Post-run bookkeeping ─────────────────────────────────────
        self._cache.set(contract, result)
        self._persistence.save(contract, result)
        self._budget.record(result.tokens_used)
        self._telemetry.record(contract.model or self.config.model or "unknown", result)

        # ── Cleanup worktree ───────────────────────────────────────────
        if worktree_path and not os.environ.get("WISP_KEEP_WORKTREES", "").lower() == "true":
            try:
                await self._worktree_mgr.cleanup(worktree_path)
            except Exception as exc:
                logger.warning("Failed to clean up worktree %s: %s", worktree_path, exc)

        return result

    async def run_parallel(
        self,
        contracts: list[SubagentContract],
        max_concurrent: int = 4,
        adaptive: bool = True,
    ) -> list[SubagentResult]:
        """Run multiple subagent contracts concurrently."""
        effective_max = max_concurrent
        if adaptive:
            effective_max = self._adaptive_max_concurrent(max_concurrent, len(contracts))
            if effective_max != max_concurrent:
                logger.info(
                    "Adaptive load balancing: max_concurrent %d → %d",
                    max_concurrent, effective_max,
                )

        semaphore = asyncio.Semaphore(effective_max)

        async def _guarded(contract: SubagentContract) -> SubagentResult:
            async with semaphore:
                return await self.run(contract, _bypass_semaphore=True)

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
                logger.error("Subagent %s crashed during gather: %s", contract.name, result)
            else:
                resolved.append(result)

        logger.info(
            "Parallel run complete: %d/%d succeeded",
            sum(1 for r in resolved if r.success),
            len(resolved),
        )
        self.aggregate_telemetry(resolved)
        return resolved

    def _adaptive_max_concurrent(self, requested: int, queue_size: int) -> int:
        """Adjust max_concurrent based on system load and token budget."""
        effective = requested

        remaining = self._budget.get_remaining()
        if remaining is not None:
            budget_ratio = remaining / max(self._budget._global_budget or 1, 1)
            if budget_ratio < 0.1:
                effective = min(effective, 1)
            elif budget_ratio < 0.3:
                effective = min(effective, 2)

        try:
            import os
            cpu_count = os.cpu_count() or 4
            load_avg = os.getloadavg()[0]
            if load_avg > cpu_count * 1.5:
                effective = min(effective, 1)
            elif load_avg > cpu_count:
                effective = min(effective, max(1, effective // 2))
        except (AttributeError, OSError):
            pass

        effective = min(effective, queue_size)
        return max(1, effective)

    # ── Composable patterns ────────────────────────────────────────────

    async def run_map_reduce(
        self,
        task: str,
        items: list[str],
        mapper: Any,
        reducer: str,
        max_concurrent: int = 4,
        retry_failed: bool = True,
    ) -> SubagentResult:
        from ._patterns import run_map_reduce
        return await run_map_reduce(self, task, items, mapper, reducer, max_concurrent, retry_failed)

    async def run_vote(
        self,
        task: str,
        agents: list[SubagentContract],
        consensus_threshold: float = 0.6,
        max_concurrent: int = 4,
    ) -> SubagentResult:
        from ._patterns import run_vote
        return await run_vote(self, task, agents, consensus_threshold, max_concurrent)

    async def run_chain(
        self,
        contracts: list[SubagentContract],
        pass_context: bool = True,
        max_concurrent: int = 1,
        continue_on_error: bool = False,
    ) -> SubagentResult:
        from ._patterns import run_chain
        return await run_chain(self, contracts, pass_context, max_concurrent, continue_on_error)

    # ── Internal helpers ───────────────────────────────────────────────

    def _validate_role(self, contract: SubagentContract) -> Optional[str]:
        if not contract.role:
            return "Role is required"
        if contract.role not in ROLE_CONFIGS:
            valid_roles = ", ".join(sorted(ROLE_CONFIGS.keys()))
            return f"Unknown role '{contract.role}'. Valid roles: {valid_roles}"
        role_cfg = ROLE_CONFIGS[contract.role]
        if not role_cfg.system_prompt:
            return f"Role '{contract.role}' has no system prompt configured"
        return None

    def _default_system_prompt(self, contract: SubagentContract) -> str:
        """Build a concise default system prompt when none is provided."""
        from .roles import AgentRole

        role_cfg = ROLE_CONFIGS.get(contract.role)
        if role_cfg:
            base = role_cfg.system_prompt
        else:
            base = "\n".join([
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
            ])

        parts = [base]

        # Load skills
        workspace = str(contract.workspace or self.workspace)
        try:
            from wisp.skills import discover_skills
            skills = discover_skills(workspace)
            if skills:
                if contract.allowed_skills:
                    filtered = [s for s in skills if s.name in contract.allowed_skills]
                    if filtered:
                        parts.append("")
                        parts.append("## Skills")
                        parts.append("You may use these skills when relevant:")
                        for s in filtered:
                            parts.append(f"- **{s.name}**: {s.description}")
                        for s in filtered:
                            parts.append("")
                            parts.append(f"### {s.name}")
                            parts.append(s.instructions)
                else:
                    parts.append("")
                    parts.append("## Available Skills")
                    parts.append("You can invoke any of these skills when relevant:")
                    for s in skills:
                        parts.append(f"- {s.name}: {s.description}")
        except Exception as exc:
            logger.debug("Failed to load skills for subagent: %s", exc)

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

    async def _validate_output(
        self, result: SubagentResult, contract: SubagentContract
    ) -> SubagentResult:
        """Validate subagent output against a JSON schema.

        If validation fails and ``auto_retry_parse`` is True, retry once
        with the validation error injected into the subagent context.
        """
        if not contract.output_schema:
            return result

        from .schema_validator import validate_subagent_output, build_retry_prompt

        is_valid, validated_data, errors = validate_subagent_output(
            result.output, contract.output_schema, auto_retry=True
        )

        if is_valid and validated_data is not None:
            result.validated_output = validated_data
            logger.info("Subagent %s output validated against schema", contract.name)
            return result

        logger.warning(
            "Subagent %s output failed schema validation: %s",
            contract.name, "; ".join(errors)
        )

        if contract.auto_retry_parse and contract.retry_count == 0:
            logger.info("Retrying subagent %s with schema feedback", contract.name)
            retry_dict = {k: v for k, v in contract.__dict__.items()
                          if k in SubagentContract.__dataclass_fields__}
            retry_dict["task"] = build_retry_prompt(
                contract.task, contract.output_schema, result.output, errors
            )
            retry_dict["retry_count"] = contract.retry_count + 1
            retry_contract = SubagentContract(**retry_dict)
            return await self.run(retry_contract)

        result.error = f"Schema validation failed: {'; '.join(errors)}"
        return result

    # ── Spawn with guards (moved from WispAgentCore) ───────────────────

    async def spawn_with_guards(
        self,
        task: str,
        tools: Optional[list[str]] = None,
        max_iterations: int = 30,
        timeout_seconds: float = 300.0,
        output_format: str = "text",
        worktree_isolated: bool = False,
        max_tokens: Optional[int] = None,
        output_schema: Optional[dict] = None,
        auto_retry: bool = True,
        workspace: Optional[str] = None,
        auto_approve: bool = True,
        depth: int = 0,
        branch_count: int = 0,
    ) -> str:
        """Spawn a single subagent with full guard logic.

        Args:
            depth: Current subagent nesting depth (0 = top-level agent).
            branch_count: Current number of sibling subagents spawned.

        Returns the subagent output as a string, or an error message.
        """
        # ── Depth and branching limits ─────────────────────────────────
        if depth >= MAX_SUBAGENT_DEPTH:
            return f"[Error: subagent depth {depth} exceeds max {MAX_SUBAGENT_DEPTH}]"

        if branch_count >= MAX_SUBAGENT_BRANCHING:
            return f"[Error: subagent branching {branch_count} exceeds max {MAX_SUBAGENT_BRANCHING}]"

        # ── Build contract ─────────────────────────────────────────────
        contract = SubagentContract(
            task=task,
            tools=tools or ["all"],
            max_iterations=max_iterations,
            timeout_seconds=timeout_seconds,
            output_format=output_format,
            workspace=workspace or str(self.workspace),
            auto_approve=auto_approve,
            worktree_isolated=worktree_isolated,
            max_tokens=max_tokens,
            output_schema=output_schema,
            _subagent_depth=depth + 1,
            _subagent_branch_count=branch_count + 1,
        )

        # ── Local model fallback ───────────────────────────────────────
        local_model = self._pick_local_model_for_subagent(contract.task)
        if local_model:
            contract.model = local_model
            logger.info("Using local model %s for subagent %s", local_model, contract.name)

        # ── Adaptive timeout ─────────────────────────────────────────
        contract.timeout_seconds = self._adaptive_subagent_timeout(
            contract.task, contract.timeout_seconds
        )

        # ── Progress callback ──────────────────────────────────────────
        async def _progress(event: OrchestratorEvent) -> None:
            if event.event_type == EventKind.TASK_STARTED:
                logger.info("[sub] %s started", event.task_id)
            elif event.event_type == EventKind.TASK_COMPLETED:
                logger.info("[sub] %s completed", event.task_id)
            elif event.event_type == EventKind.TASK_FAILED:
                logger.warning("[sub] %s failed: %s", event.task_id, event.payload.get("error", ""))

        contract.progress_callback = _progress

        # ── Run with retry ───────────────────────────────────────────
        max_retries = int(auto_retry) * 2
        last_error = ""
        result: Optional[SubagentResult] = None
        for attempt in range(max_retries + 1):
            try:
                result = await self.run(contract)
                if result.success:
                    break
                last_error = result.error or "subagent failed"
                if result.timed_out or "timeout" in last_error.lower():
                    logger.warning("Subagent %s timed out — not retrying", contract.name)
                    break
                if attempt < max_retries:
                    backoff = 2 ** attempt
                    logger.warning("Subagent %s failed (attempt %d/%d), retrying in %ds: %s",
                                   contract.name, attempt + 1, max_retries + 1, backoff, last_error)
                    await asyncio.sleep(backoff)
            except Exception as exc:
                last_error = str(exc)
                if attempt < max_retries:
                    backoff = 2 ** attempt
                    logger.warning("Subagent %s crashed (attempt %d/%d), retrying in %ds: %s",
                                   contract.name, attempt + 1, max_retries + 1, backoff, last_error)
                    await asyncio.sleep(backoff)
                else:
                    logger.error("Subagent %s crashed: %s", contract.name, exc, exc_info=True)
                    return f"[Error: subagent crashed after {max_retries + 1} attempts: {exc}]"
        else:
            return f"[Error: subagent failed after {max_retries + 1} attempts: {last_error}]"

        if result is None:
            return f"[Error: subagent failed after {max_retries + 1} attempts: {last_error}]"

        # ── Return output (with size guard) ────────────────────────────
        output = result.output
        if len(output) > 12000:
            output = output[:12000] + f"\n... [truncated: {len(result.output)} total chars]"
        return output

    async def spawn_parallel_with_guards(
        self,
        specs: list[SubagentContract | dict],
        depth: int = 0,
        branch_count: int = 0,
    ) -> list[SubagentResult]:
        """Spawn parallel subagents with optimizations applied to each.

        Args:
            specs: A list of SubagentContract objects or dicts describing each subagent's task.
            depth: Current subagent nesting depth (0 = top-level agent).
            branch_count: Current number of sibling subagents spawned.

        Returns:
            A list of SubagentResult objects, one per spec.
        """
        # ── Depth and branching limits ─────────────────────────────────
        if depth >= MAX_SUBAGENT_DEPTH:
            return [
                SubagentResult(
                    task_id=getattr(spec, "name", "unknown"),
                    success=False,
                    output=f"[Error: subagent depth {depth} exceeds max {MAX_SUBAGENT_DEPTH}]",
                    elapsed_seconds=0.0,
                )
                for spec in specs
            ]

        if branch_count >= MAX_SUBAGENT_BRANCHING:
            return [
                SubagentResult(
                    task_id=getattr(spec, "name", "unknown"),
                    success=False,
                    output=f"[Error: subagent branching {branch_count} exceeds max {MAX_SUBAGENT_BRANCHING}]",
                    elapsed_seconds=0.0,
                )
                for spec in specs
            ]

        contracts: list[SubagentContract] = []
        for spec in specs:
            if isinstance(spec, dict):
                contract = SubagentContract(**spec)
            else:
                contract = spec
            # Apply production optimizations
            if contract.timeout_seconds < 30.0:
                contract.timeout_seconds = self._adaptive_subagent_timeout(
                    contract.task, contract.timeout_seconds
                )
            local_model = self._pick_local_model_for_subagent(contract.task)
            if local_model:
                contract.model = local_model
            contracts.append(contract)

        results = await self.run_parallel(contracts)
        for r in results:
            logger.info(
                "Subagent %s: success=%s, duration=%.1fs, files=%s, tokens=%d",
                r.task_id, r.success, r.elapsed_seconds, r.files_changed, r.tokens_used,
            )
        return results

    # ── Internal helpers (moved from WispAgentCore) ──────────────────

    def _pick_local_model_for_subagent(self, task: str) -> Optional[str]:
        """Return a fast local model name if the task is simple enough."""
        parent_model = getattr(self.config, "model", "")
        if not parent_model or ":cloud" not in parent_model:
            return None
        fast_locals = ["llama3.2", "llama3.1", "qwen2.5", "phi4", "gemma2"]
        available = self._list_local_models()
        for candidate in fast_locals:
            for name in available:
                if candidate in name.lower():
                    return name
        return None

    def _list_local_models(self) -> list[str]:
        """Query Ollama for locally available models. Cached for 60s."""
        now = time.monotonic()
        cache = getattr(self, "_local_model_cache", None)
        if cache and now - cache["ts"] < 60:
            return cache["models"]
        try:
            import requests
            url = getattr(self.config, "ollama_url", "http://localhost:11434")
            resp = requests.get(f"{url}/api/tags", timeout=5)
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                self._local_model_cache = {"ts": now, "models": models}
                return models
        except Exception:
            pass
        return []

    def _adaptive_subagent_timeout(self, task: str, requested: float) -> float:
        """Compute adaptive timeout based on task complexity and model latency."""
        parent_model = getattr(self.config, "model", "")
        is_cloud = ":cloud" in parent_model or "https://" in getattr(self.config, "ollama_url", "")
        base_per_turn = 25.0 if is_cloud else 8.0

        estimated_iterations = 3
        if len(task) > 200:
            estimated_iterations += 1
        if len(task) > 500:
            estimated_iterations += 1
        tool_keywords = ["read", "write", "edit", "list", "search", "run"]
        tool_mentions = sum(1 for kw in tool_keywords if kw in task.lower())
        estimated_iterations += min(tool_mentions, 3)

        estimated_seconds = estimated_iterations * base_per_turn + 10
        adaptive = max(30.0, min(estimated_seconds, 300.0))

        if requested >= 30.0:
            return min(requested, 300.0)
        return adaptive

    def _research_angles(self, prompt: str) -> list[str]:
        """Break a research prompt into parallel investigation angles."""
        prompt_lower = prompt.lower()
        config_angles = getattr(self.config, "research_angles", {})
        if isinstance(config_angles, dict):
            for keyword, angles in config_angles.items():
                if keyword.lower() in prompt_lower:
                    return [a.format(prompt=prompt) for a in angles]
        return [
            f"Research the core concepts and fundamentals: {prompt}",
            f"Research recent advances and state-of-the-art: {prompt}",
            f"Research practical implementations and tools: {prompt}",
            f"Research limitations, challenges, and future directions: {prompt}",
        ]

