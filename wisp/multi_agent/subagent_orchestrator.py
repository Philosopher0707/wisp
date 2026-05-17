"""Subagent orchestrator — unified single + parallel subagent execution.

v3 features:
- composable patterns (map-reduce, vote, chain)
- token budget tracking
- async schema validation
- worktree isolation
- caching and persistence
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
from wisp.session import Session
from wisp.session_store import get_store

from .protocol import AgentEvent, EventType, TaskAssignment, TaskResult
from .registry import AgentRegistry, AgentRecord, AgentStatus
from .bus import MessageBus
from .roles import AgentRole, ROLE_CONFIGS
from .agent_factory import AgentFactory
from .workspace_lock import WorkspaceLock
from .task import EventKind, OrchestratorEvent, SubagentContract, SubagentResult

logger = logging.getLogger(__name__)

# Maximum subagent nesting depth to prevent recursive explosion
MAX_SUBAGENT_DEPTH = 2

def _run_subagent_worker(contract_dict: dict, conn, parent_workspace: str):
    """Standalone worker that runs in a separate process.

    Reconstructs a minimal orchestrator from the serialized contract dict,
    runs the subagent, and sends the result through the pipe ``conn``.
    """
    import asyncio
    import time as _time

    from wisp.config import WispConfig
    from wisp.session import Session
    from wisp.session_store import get_store
    from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator
    from wisp.multi_agent.task import SubagentContract, SubagentResult

    start = _time.monotonic()
    contract = SubagentContract(**contract_dict)

    # ── Resource limits (Unix only) ────────────────────────────────────
    try:
        import resource
        # Memory limit: 2GB default
        mem_limit = getattr(contract, "max_memory_mb", 2048) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem_limit, mem_limit))
        # CPU limit: timeout + 30s buffer
        cpu_limit = int(contract.timeout_seconds + 30)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
    except (ImportError, OSError, ValueError):
        pass  # Not on Unix or limits not supported

    # ── Depth guard ──────────────────────────────────────────────────
    if contract._subagent_depth >= MAX_SUBAGENT_DEPTH:
        duration = _time.monotonic() - start
        result = SubagentResult(
            task_id=contract.name,
            success=False,
            output=f"[DEPTH LIMIT EXCEEDED] Max subagent depth is {MAX_SUBAGENT_DEPTH}",
            error=f"Subagent depth {contract._subagent_depth} exceeds max {MAX_SUBAGENT_DEPTH}",
            elapsed_seconds=duration,
        )
        conn.send({
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
        })
        conn.close()
        return

    # The worker already runs inside a dedicated process, so nested
    # process isolation would cause recursion/hang. Force thread isolation.
    contract.isolation = "thread"

    # ── Inject context files into task ─────────────────────────────────
    if contract.context_files:
        context_block = "\n".join(
            f"- {f}" for f in contract.context_files
        )
        contract.task = (
            f"{contract.task}\n\n"
            f"## Relevant Context\n"
            f"{context_block}"
        )

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

    # Serialize result and send through pipe
    data = {
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
    }
    # Compress large outputs to avoid pipe overflow
    output_bytes = data["output"].encode("utf-8")
    if len(output_bytes) > 10000:
        import gzip
        compressed = gzip.compress(output_bytes)
        data["output"] = compressed.hex()
        data["__compressed"] = True

    try:
        conn.send(data)
    except Exception:
        # Pipe overflow — fall back to temp file with compression
        import gzip
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".json.gz", delete=False) as f:
            f.write(gzip.compress(json.dumps(data).encode("utf-8")))
            conn.send({"__fallback_path": f.name, "__compressed": True})
    conn.close()


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
        self._session_mgr = get_store()
        self._shutdown = False
        # ── Result persistence ───────────────────────────────────────────
        self._persist_path = self.workspace / ".wisp" / "subagent_results.jsonl"
        """Path to JSONL file for persisting subagent results."""
        # ── Token budget tracking ──────────────────────────────────────
        self._tokens_consumed: int = 0
        self._global_token_budget: Optional[int] = None
        """Global token budget across all subagent runs. None = unlimited."""
        # ── Telemetry ────────────────────────────────────────────────────
        self._telemetry: dict[str, list[dict]] = {}
        """Per-model telemetry: latency, success, tokens."""
        # ── Result cache ───────────────────────────────────────────────────
        self._result_cache: dict[str, tuple[SubagentResult, float]] = {}
        """Cache: key → (result, timestamp). TTL = 60s text, 300s structured."""
        self._cache_hits = 0
        self._cache_misses = 0
        # ── Shared context for inter-subagent communication ────────────────
        self._shared_context: dict[str, Any] = {}
        """Shared key-value store for parallel subagents to coordinate."""
        self._shared_context_lock = asyncio.Lock()
        # ── Agent pool ───────────────────────────────────────────────────
        self._agent_pool_size: int = 4
        """Maximum number of concurrent subagents in the pool."""
        self._active_agents: int = 0
        """Currently running subagents."""
        self._pool_semaphore = asyncio.Semaphore(self._agent_pool_size)

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

    # ── Cost estimation ────────────────────────────────────────────────

    # Approximate pricing per 1K tokens (input + output averaged)
    # Updated periodically; used for estimation only.
    _MODEL_PRICING: dict[str, float] = {
        "gpt-4o": 0.005,
        "gpt-4o-mini": 0.00015,
        "gpt-4-turbo": 0.01,
        "gpt-4": 0.03,
        "gpt-3.5-turbo": 0.0015,
        "claude-3-5-sonnet": 0.003,
        "claude-3-opus": 0.015,
        "claude-3-haiku": 0.00025,
        "llama3.1": 0.0,
        "llama3.2": 0.0,
        "mistral": 0.0,
        "codellama": 0.0,
        "default": 0.0,
    }

    def estimate_cost(self, tokens: int, model: str = "") -> float:
        """Estimate USD cost for a given token count and model.

        Returns 0.0 for local/unknown models.
        """
        model = model or self.config.model or "default"
        # Normalize model name for lookup
        model_key = model.lower().replace("-", "").replace(".", "")
        # Build normalized pricing table
        normalized_pricing = {
            k.lower().replace("-", "").replace(".", ""): v
            for k, v in self._MODEL_PRICING.items()
        }
        price_per_1k = normalized_pricing.get(model_key)
        if price_per_1k is None:
            # Try fuzzy match on original keys
            for key, price in self._MODEL_PRICING.items():
                norm_key = key.lower().replace("-", "").replace(".", "")
                if norm_key in model_key or model_key in norm_key:
                    price_per_1k = price
                    break
            else:
                price_per_1k = self._MODEL_PRICING["default"]
        return (tokens / 1000) * price_per_1k

    def get_cost_summary(self) -> dict[str, float]:
        """Return total estimated cost across all telemetry records."""
        total = 0.0
        per_model: dict[str, float] = {}
        for model, records in self._telemetry.items():
            model_total = sum(
                self.estimate_cost(r["tokens_used"], model) for r in records
            )
            per_model[model] = model_total
            total += model_total
        return {"total_usd": total, "per_model": per_model}

    # ── Cache API ──────────────────────────────────────────────────────

    def _cache_key(self, contract: SubagentContract) -> str:
        """Build a cache key from contract fields that affect output."""
        import hashlib
        parts = [
            contract.task,
            contract.role,
            ",".join(sorted(contract.tools)),
            str(contract.model or ""),
            str(contract.workspace or ""),
            contract.output_format,
            str(contract.output_schema or ""),
            str(contract.system_prompt or ""),
        ]
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _check_cache(self, contract: SubagentContract) -> Optional[SubagentResult]:
        """Return cached result if valid, else None."""
        key = self._cache_key(contract)
        entry = self._result_cache.get(key)
        if entry is None:
            self._cache_misses += 1
            return None
        result, ts = entry
        ttl = 300 if contract.output_format == "json" else 60
        age = time.monotonic() - ts
        if age > ttl:
            del self._result_cache[key]
            self._cache_misses += 1
            return None
        self._cache_hits += 1
        logger.info("Cache hit for %s (age=%.0fs)", contract.name, age)
        return result

    def _store_cache(self, contract: SubagentContract, result: SubagentResult) -> None:
        """Store result in cache with current timestamp."""
        key = self._cache_key(contract)
        self._result_cache[key] = (result, time.monotonic())

    def get_cache_stats(self) -> dict[str, int]:
        """Return cache hit/miss statistics."""
        total = self._cache_hits + self._cache_misses
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "total": total,
            "hit_rate": self._cache_hits / total if total else 0.0,
            "size": len(self._result_cache),
        }

    def clear_cache(self) -> None:
        """Clear all cached results."""
        self._result_cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0

    # ── Shared context API ─────────────────────────────────────────────

    async def get_shared(self, key: str, default: Any = None) -> Any:
        """Get a value from the shared context store."""
        async with self._shared_context_lock:
            return self._shared_context.get(key, default)

    async def set_shared(self, key: str, value: Any) -> None:
        """Set a value in the shared context store."""
        async with self._shared_context_lock:
            self._shared_context[key] = value

    async def update_shared(self, key: str, value: Any) -> None:
        """Update a value in the shared context store (append to list or set)."""
        async with self._shared_context_lock:
            if key not in self._shared_context:
                self._shared_context[key] = []
            if isinstance(self._shared_context[key], list):
                self._shared_context[key].append(value)
            else:
                self._shared_context[key] = value

    def get_shared_context(self) -> dict[str, Any]:
        """Return a snapshot of the shared context."""
        return dict(self._shared_context)

    def clear_shared_context(self) -> None:
        """Clear all shared context."""
        self._shared_context.clear()

    def set_pool_size(self, size: int) -> None:
        """Set the maximum number of concurrent subagents.

        Parameters
        ----------
        size:
            New pool size (must be >= 1).
        """
        if size < 1:
            raise ValueError("Pool size must be >= 1")
        self._agent_pool_size = size
        self._pool_semaphore = asyncio.Semaphore(size)
        logger.info("Subagent pool size set to %d", size)

    def get_pool_status(self) -> dict[str, int]:
        """Return current pool status."""
        return {
            "pool_size": self._agent_pool_size,
            "active_agents": self._active_agents,
            "available_slots": max(0, self._agent_pool_size - self._active_agents),
        }

    def _persist_result(self, contract: SubagentContract, result: SubagentResult) -> None:
        """Persist a subagent result to the JSONL log."""
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "timestamp": time.time(),
                "task_id": result.task_id,
                "role": contract.role,
                "task": contract.task[:200],
                "success": result.success,
                "elapsed_seconds": result.elapsed_seconds,
                "tokens_used": result.tokens_used,
                "iterations_used": result.iterations_used,
                "timed_out": result.timed_out,
                "error": result.error,
                "output_preview": result.output[:500] if result.output else "",
            }
            with open(self._persist_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as exc:
            logger.debug("Failed to persist subagent result: %s", exc)

    def get_persisted_results(self, limit: int = 100) -> list[dict]:
        """Read persisted results from the JSONL log."""
        results = []
        try:
            if not self._persist_path.exists():
                return results
            with open(self._persist_path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        results.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        logger.debug(
                            "Skipping corrupted JSONL line %d: %s", line_num, exc
                        )
            return results[-limit:]
        except Exception as exc:
            logger.warning("Failed to read persisted results: %s", exc)
            return results

    def clear_persisted_results(self) -> None:
        """Clear the persisted results log."""
        try:
            if self._persist_path.exists():
                self._persist_path.unlink()
        except OSError as exc:
            logger.warning("Failed to clear persisted results: %s", exc)

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

        # ── Contract parameter validation ──────────────────────────────
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
        cached = self._check_cache(contract)
        if cached is not None:
            return cached

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
        result: SubagentResult | None = None
        async with self._pool_semaphore:
            self._active_agents += 1
            try:
                if contract.isolation == "process":
                    result = await self._spawn_subagent_process(contract, start)
                else:
                    result = await self._spawn_subagent_thread(contract, start, agent_workspace, child_cfg, session, system, tool_calls_log)
                # Store successful/failed results in cache
                self._store_cache(contract, result)
                # Persist result for audit trail
                self._persist_result(contract, result)
                return result
            finally:
                self._active_agents -= 1
                # ── Cleanup worktree (unless debugging) ──────────────────
                if worktree_path and not os.environ.get("WISP_KEEP_WORKTREES", "").lower() == "true":
                    # Small delay to let process finish flushing files
                    await asyncio.sleep(0.5)
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
        # Create heartbeat file for hung-thread detection
        heartbeat_path = Path(tempfile.gettempdir()) / f"wisp_heartbeat_{contract.name}_{uuid.uuid4().hex[:8]}.txt"

        # Start heartbeat task for progress streaming
        heartbeat_task: asyncio.Task | None = None
        _done = [False]

        async def _heartbeat():
            """Emit periodic progress events and touch heartbeat file."""
            while not _done[0]:
                await asyncio.sleep(5)
                if _done[0]:
                    break
                # Touch heartbeat file to show subagent parent is alive
                try:
                    heartbeat_path.write_text(str(time.time()), encoding="utf-8")
                except OSError:
                    pass
                elapsed = time.monotonic() - start
                if contract.progress_callback:
                    await self._emit(
                        contract,
                        EventKind.TASK_PROGRESS,
                        {"elapsed": elapsed, "status": "running"},
                    )

        async def _health_monitor():
            """Monitor heartbeat file for hung thread detection."""
            while not _done[0]:
                await asyncio.sleep(10)
                if _done[0]:
                    break
                try:
                    if heartbeat_path.exists():
                        mtime = heartbeat_path.stat().st_mtime
                        age = time.time() - mtime
                        if age > min(60.0, max(15.0, contract.timeout_seconds * 0.8)):
                            logger.warning(
                                "Subagent %s heartbeat stale \u2014 may be hung (%.0fs stale, threshold %.0fs)",
                                contract.name, age, min(60.0, max(15.0, contract.timeout_seconds * 0.8)),
                            )
                except OSError:
                    pass

        if contract.progress_callback:
            heartbeat_task = asyncio.create_task(_heartbeat())
        health_task = asyncio.create_task(_health_monitor())

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
                    str(heartbeat_path),
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
        finally:
            _done[0] = True
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except (asyncio.CancelledError, RuntimeError):
                    pass
            health_task.cancel()
            try:
                await health_task
            except (asyncio.CancelledError, RuntimeError):
                pass
            # Cleanup heartbeat file
            try:
                heartbeat_path.unlink(missing_ok=True)
            except OSError:
                pass

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
        # Create multiprocessing pipe for IPC
        parent_conn, child_conn = mp.Pipe()

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
            "_subagent_depth": contract._subagent_depth + 1,
            "_subagent_branch_count": getattr(contract, "_subagent_branch_count", 0) + 1,
        }

        process = mp.Process(
            target=_run_subagent_worker,
            args=(contract_dict, child_conn, str(self.workspace)),
        )

        # Start heartbeat for progress streaming
        heartbeat_task: asyncio.Task | None = None
        _done = [False]

        async def _heartbeat():
            while not _done[0]:
                await asyncio.sleep(5)
                if _done[0]:
                    break
                elapsed = time.monotonic() - start
                if contract.progress_callback:
                    await self._emit(
                        contract,
                        EventKind.TASK_PROGRESS,
                        {"elapsed": elapsed, "status": "running"},
                    )

        if contract.progress_callback:
            heartbeat_task = asyncio.create_task(_heartbeat())

        try:
            process.start()
            # Run join in thread pool to avoid blocking the event loop
            await asyncio.to_thread(process.join, contract.timeout_seconds)

            if process.is_alive():
                # Timeout — force kill
                duration = time.monotonic() - start
                logger.warning(
                    "Subagent %s process timed out after %.1fs — terminating",
                    contract.name, duration,
                )
                process.terminate()
                await asyncio.to_thread(process.join, 5)
                if process.is_alive():
                    logger.error(
                        "Subagent %s process refused SIGTERM — sending SIGKILL",
                        contract.name,
                    )
                    process.kill()
                    await asyncio.to_thread(process.join, 2)

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

            # Process finished — read result from pipe
            exit_code = process.exitcode
            if exit_code is not None and exit_code != 0:
                duration = time.monotonic() - start
                logger.error(
                    "Subagent %s process exited with code %d",
                    contract.name, exit_code,
                )
                parent_conn.close()
                return SubagentResult(
                    task_id=contract.name,
                    success=False,
                    output=f"[PROCESS EXITED {exit_code}]",
                    elapsed_seconds=duration,
                    error=f"Process exited with code {exit_code}",
                )

            try:
                if parent_conn.poll(5):
                    data = parent_conn.recv()
                    parent_conn.close()
                    # Handle fallback to temp file for large outputs
                    if isinstance(data, dict) and "__fallback_path" in data:
                        fallback_path = data["__fallback_path"]
                        if data.get("__compressed"):
                            import gzip
                            with open(fallback_path, "rb") as f:
                                data = json.loads(gzip.decompress(f.read()).decode("utf-8"))
                        else:
                            with open(fallback_path, "r", encoding="utf-8") as f:
                                data = json.load(f)
                        try:
                            os.unlink(fallback_path)
                        except OSError:
                            pass
                    # Decompress inline compressed output
                    if data.get("__compressed"):
                        import gzip
                        data["output"] = gzip.decompress(bytes.fromhex(data["output"])).decode("utf-8")
                        del data["__compressed"]
                else:
                    parent_conn.close()
                    raise TimeoutError("No data received from subagent process")
            except Exception as exc:
                parent_conn.close()
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
            _done[0] = True
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
            # Close pipe connection
            try:
                parent_conn.close()
            except OSError:
                pass

    async def run_parallel(
        self,
        contracts: list[SubagentContract],
        max_concurrent: int = 4,
        adaptive: bool = True,
    ) -> list[SubagentResult]:
        """Run multiple subagent contracts concurrently.

        Parameters
        ----------
        contracts:
            Subagent specifications to execute.
        max_concurrent:
            Maximum number of subagents running at once (semaphore).
        adaptive:
            If True, adjust max_concurrent based on system load and token budget.

        Returns
        -------
        list[SubagentResult]
            One result per contract, in the same order as ``contracts``.
        """
        # ── Adaptive load balancing ──────────────────────────────────
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
        # Auto-aggregate telemetry from this batch
        self.aggregate_telemetry(resolved)
        return resolved

    def _adaptive_max_concurrent(self, requested: int, queue_size: int) -> int:
        """Adjust max_concurrent based on system load and token budget.

        Returns a value between 1 and requested.
        """
        import os

        # Start with requested value
        effective = requested

        # Check token budget
        remaining = self.get_token_budget_remaining()
        if remaining is not None:
            # If budget is tight, reduce concurrency
            budget_ratio = remaining / max(self._global_token_budget, 1)
            if budget_ratio < 0.1:
                effective = min(effective, 1)
            elif budget_ratio < 0.3:
                effective = min(effective, 2)

        # Check CPU load (Unix only)
        try:
            cpu_count = os.cpu_count() or 4
            load_avg = os.getloadavg()[0]  # 1-minute load
            # If load > CPU count, reduce concurrency
            if load_avg > cpu_count * 1.5:
                effective = min(effective, 1)
            elif load_avg > cpu_count:
                effective = min(effective, max(1, effective // 2))
        except (AttributeError, OSError):
            pass  # Not on Unix

        # Don't exceed queue size
        effective = min(effective, queue_size)

        return max(1, effective)

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

    def aggregate_telemetry(self, results: list[SubagentResult]) -> dict[str, dict]:
        """Auto-aggregate telemetry from a batch of subagent results.

        Updates internal telemetry store and returns a summary.
        """
        for result in results:
            if result.model_used:
                self._telemetry.setdefault(result.model_used, []).append({
                    "elapsed_seconds": result.elapsed_seconds,
                    "success": result.success,
                    "tokens_used": result.tokens_used,
                    "timestamp": time.time(),
                })
        return self.get_telemetry_summary()

    # ── Composable patterns ────────────────────────────────────────────

    async def run_map_reduce(
        self,
        task: str,
        items: list[str],
        mapper: Callable[[str], SubagentContract],
        reducer: str,
        max_concurrent: int = 4,
        retry_failed: bool = True,
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
        retry_failed:
            If True, retry failed mappers once before reducing.

        Returns
        -------
        SubagentResult
            The reducer's synthesized result.
        """
        if not items:
            return SubagentResult(
                task_id="map-reduce",
                success=False,
                output="[MAP-REDUCE FAILED] No items provided to process.",
                error="No items provided",
                elapsed_seconds=0.0,
            )

        # ── Map phase ────────────────────────────────────────────────
        mapper_contracts = [mapper(item) for item in items]
        mapper_results = await self.run_parallel(mapper_contracts, max_concurrent)

        # ── Retry failed mappers ─────────────────────────────────────
        if retry_failed:
            retry_contracts = []
            retry_indices = []
            for i, r in enumerate(mapper_results):
                if not r.success and not r.timed_out:
                    contract = mapper_contracts[i]
                    retry_contract = SubagentContract(
                        **{
                            **{k: v for k, v in contract.__dict__.items()
                               if k in SubagentContract.__dataclass_fields__},
                            "task": (
                                f"{contract.task}\n\n"
                                f"IMPORTANT: Previous attempt failed: {r.error or 'unknown'}. "
                                f"Please try again with a different approach."
                            ),
                        }
                    )
                    retry_contracts.append(retry_contract)
                    retry_indices.append(i)

            if retry_contracts:
                logger.info("Retrying %d failed mapper(s)", len(retry_contracts))
                retry_results = await self.run_parallel(retry_contracts, max_concurrent)
                for idx, r_result in zip(retry_indices, retry_results):
                    if r_result.success:
                        mapper_results[idx] = r_result

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
        if not agents:
            return SubagentResult(
                task_id="vote",
                success=False,
                output="[VOTE FAILED] No agents provided for voting.",
                error="No agents provided",
                elapsed_seconds=0.0,
            )

        voting_contracts = []
        for agent in agents:
            c = SubagentContract(**{**agent.__dict__, "task": task})
            voting_contracts.append(c)

        results = await self.run_parallel(voting_contracts, max_concurrent)

        # Count successes and analyze outputs
        successful = [r for r in results if r.success]
        total = len(results)
        passed = len(successful)

        if total == 0:
            return SubagentResult(
                task_id="vote",
                success=False,
                output="[VOTE FAILED] No voting agents executed.",
                error="No results from voting agents",
                elapsed_seconds=0.0,
            )

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

            # Tie-breaker: if two groups are equal size, run a decider
            if len(groups) >= 2:
                sorted_groups = sorted(groups, key=len, reverse=True)
                if len(sorted_groups[0]) == len(sorted_groups[1]):
                    logger.info("Vote tie detected (%d-%d), running tie-breaker",
                                len(sorted_groups[0]), len(sorted_groups[1]))
                    tie_contract = SubagentContract(
                        name="tie-breaker",
                        role="generalist",
                        task=(
                            f"Break this tie vote.\n\n"
                            f"Question: {task}\n\n"
                            f"Option A ({len(sorted_groups[0])} votes):\n"
                            f"{sorted_groups[0][0][:500]}\n\n"
                            f"Option B ({len(sorted_groups[1])} votes):\n"
                            f"{sorted_groups[1][0][:500]}\n\n"
                            f"Which option is better? Respond with 'A' or 'B' and a brief reason."
                        ),
                        timeout_seconds=30,
                        max_iterations=5,
                    )
                    tie_result = await self.run(tie_contract)
                    if tie_result.success and "A" in tie_result.output.upper():
                        winner = sorted_groups[0][0]
                        count = len(sorted_groups[0])
                    elif tie_result.success and "B" in tie_result.output.upper():
                        winner = sorted_groups[1][0]
                        count = len(sorted_groups[1])
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
        continue_on_error: bool = False,
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
        continue_on_error:
            If True, continue the chain even if a step fails.

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
        failed_steps = []

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
                failed_steps.append((i + 1, contract.name, result.error))
                if not continue_on_error:
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

        # Chain completed (possibly with failures if continue_on_error=True)
        if last_result is None:
            return SubagentResult(
                task_id="chain-empty",
                success=True,
                output="(empty chain)",
            )

        # Augment final result with chain metadata
        success = len(failed_steps) == 0
        output_lines = [f"## Chain Complete ({len(contracts)} steps)"]
        if failed_steps:
            output_lines.append(f"\n**Failed steps:** {len(failed_steps)}")
            for step_num, name, error in failed_steps:
                output_lines.append(f"- Step {step_num} ({name}): {error or 'unknown'}")
        output_lines.append(f"\n{last_result.output}")
        output_lines.append(
            f"\n---\n"
            f"*Chain elapsed: {total_elapsed:.1f}s, "
            f"iterations: {total_iterations}, "
            f"tokens: {total_tokens}*"
        )
        last_result.output = "\n".join(output_lines)
        last_result.elapsed_seconds = total_elapsed
        last_result.iterations_used = total_iterations
        last_result.input_tokens = total_input_tokens
        last_result.output_tokens = total_output_tokens
        last_result.tokens_used = total_tokens
        last_result.files_changed = list(set(all_files_changed))
        last_result.success = success
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
        heartbeat_path: str = "",
    ) -> dict:
        """Synchronous wrapper that runs the async _run_agent in a fresh event loop.

        This ensures the work can be cancelled by asyncio.wait_for via
        asyncio.to_thread, which is not possible when a sync blocking call
        is nested inside an async coroutine.

        WARNING: This creates a nested event loop (parent loop → to_thread →
        new loop). This is an anti-pattern but necessary because _run_agent
        calls async code (run_task) that may spawn subagents. Always cancel
        pending tasks before closing to avoid "Task was destroyed but it is
        pending!" warnings.
        """
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self._run_agent(contract, config, session, system_prompt, workspace_path, tool_calls_log, heartbeat_path)
            )
        finally:
            # Cancel all pending tasks before closing the loop to avoid
            # "Task was destroyed but it is pending!" warnings
            try:
                pending = asyncio.all_tasks(loop)
                if pending:
                    for task in pending:
                        task.cancel()
                    # Give tasks a moment to cancel gracefully
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            loop.close()

    async def _run_agent(
        self,
        contract: SubagentContract,
        config: WispConfig,
        session: Session,
        system_prompt: str,
        workspace_path: str,
        tool_calls_log: list[dict],
        heartbeat_path: str = "",
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

            # ── Touch heartbeat file before running ─────────────────────
            if heartbeat_path:
                try:
                    Path(heartbeat_path).write_text(str(time.time()))
                except OSError:
                    pass

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

            # ── Touch heartbeat file after completion ───────────────────
            if heartbeat_path:
                try:
                    Path(heartbeat_path).write_text(str(time.time()))
                except OSError:
                    pass

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

    def _validate_role(self, contract: SubagentContract) -> Optional[str]:
        """Validate role configuration for a contract.

        Returns an error message if invalid, None if valid.
        """
        from .roles import ROLE_CONFIGS

        if not contract.role:
            return "Role is required"

        if contract.role not in ROLE_CONFIGS:
            valid_roles = ", ".join(sorted(ROLE_CONFIGS.keys()))
            return f"Unknown role '{contract.role}'. Valid roles: {valid_roles}"

        role_cfg = ROLE_CONFIGS[contract.role]
        if not role_cfg.system_prompt:
            return f"Role '{contract.role}' has no system prompt configured"

        return None

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
        """Build a concise default system prompt when none is provided.

        Loads skills from the workspace if allowed_skills is set or if
        skills are discovered automatically.
        """
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

        # ── Load skills ──────────────────────────────────────────────
        workspace = str(contract.workspace or self.workspace)
        try:
            from wisp.skills import discover_skills
            skills = discover_skills(workspace)
            if skills:
                if contract.allowed_skills:
                    # Filter to only allowed skills
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
                    # No restriction — list all available skills
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

    async def _create_worktree(self, agent_name: str) -> Path:
        """Create an isolated git worktree for a subagent."""
        self._worktrees_root.mkdir(parents=True, exist_ok=True)

        short_id = uuid.uuid4().hex[:8]
        ts = int(time.time())
        # Sanitize name for filesystem and git branch safety
        import re
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '-', agent_name)[:32]
        safe_name = safe_name.strip('-')
        if not safe_name:
            safe_name = "subagent"
        dir_name = f"{safe_name}-{short_id}"
        branch_name = f"wisp-subagent/{safe_name}-{short_id}-{ts}"

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
        """Remove a worktree and delete the associated branch."""
        logger.info("Cleaning up worktree: %s", worktree_path)

        # Derive branch name from worktree path metadata (stored in .git/worktrees/)
        git_dir = self.workspace / ".git" / "worktrees"
        branch_name: str | None = None
        try:
            for entry in git_dir.iterdir():
                if entry.is_dir() and worktree_path.name in str(entry.name):
                    head_file = entry / "HEAD"
                    if head_file.exists():
                        head_text = head_file.read_text().strip()
                        if head_text.startswith("ref: "):
                            branch_name = head_text.replace("ref: refs/heads/", "")
                            break
        except Exception as exc:
            logger.debug("Could not determine branch for %s: %s", worktree_path, exc)

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

        # Delete the orphan branch (best effort)
        if branch_name and branch_name.startswith("wisp-subagent/"):
            try:
                branch_proc = await asyncio.create_subprocess_exec(
                    "git",
                    "branch",
                    "-D",
                    branch_name,
                    cwd=str(self.workspace),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await branch_proc.communicate()
                if branch_proc.returncode == 0:
                    logger.debug("Deleted branch %s", branch_name)
                else:
                    logger.debug("Branch delete %s may already be gone", branch_name)
            except Exception as exc:
                logger.debug("Branch delete failed (non-critical): %s", exc)

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

        Uses the built-in schema_validator (no external deps).
        If validation fails and ``auto_retry_parse`` is True, retry once
        with the validation error injected into the subagent context.
        """
        if not contract.output_schema:
            return result

        from wisp.multi_agent.schema_validator import validate_subagent_output, build_retry_prompt

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

