"""SubagentRunner — execute a single subagent in the parent's event loop.

No nested event loops. No threads. Direct async execution with
``asyncio.timeout`` for cancellation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

from wisp.config import WispConfig
from wisp.core.session import Session
from wisp.infra.store import UnifiedStore

from .task import EventKind, OrchestratorEvent, SubagentContract, SubagentResult


def _effective_child_tools(
    contract_tools: list[str] | None, permission_mode: str
) -> list[str]:
    """Advertise only tools the child's inherited mode can execute.

    Children run under the parent's permission mode with no approval
    handler; a blocked tool in their schema just buys a wasted turn on a
    guaranteed '[Blocked: ...]' result (live fanout evidence, 2026-08-25).
    """
    from wisp.infra.policy_engine import filter_allowed_for_mode

    if contract_tools and "all" not in [t.lower() for t in contract_tools]:
        requested: list[str] = list(contract_tools)
    else:
        from wisp.tools.registry import TOOL_SCHEMAS

        requested = [
            s.get("function", {}).get("name", "") for s in TOOL_SCHEMAS
        ]
    return filter_allowed_for_mode(permission_mode, requested)


def _stricter(a: int | float | None, b: int | float | None):
    """The tighter of two optional limits; None means unlimited."""
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _budget_from_contract(contract: Any, deadline: float):
    """Contract-derived resource budget, narrowed by a DAG node's declaration.

    The contract's own fields are the floor. A DAG node's `budget` metadata —
    built by the orchestrator and carried on the contract as
    `metadata["_budget"]` — may only **narrow** it: a graph author can bound a
    node, never widen it past the contract's limits.

    Until Phase 10 this function did not exist and both call sites built a
    bare `ResourceBudget()` from contract fields, so a node's declared budget
    was constructed, attached, and silently dropped. That is audit-2026-08-24
    item 11, *"honor metadata budget"* — the third of its three prescribed
    fixes (blocked descendants and dependency injection are already in; see
    `dag.py::_block_descendants` and `metadata["_dep_results"]`).
    """
    from .resource_budget import ResourceBudget

    budget = ResourceBudget()
    if contract.max_tokens:
        budget.max_tokens = contract.max_tokens
    if contract.max_input_tokens:
        budget.max_tokens = (budget.max_tokens or contract.max_input_tokens)
    budget.max_wall_time = deadline - time.monotonic()

    declared = (getattr(contract, "metadata", None) or {}).get("_budget")
    if declared is not None:
        budget = ResourceBudget(
            max_tokens=_stricter(budget.max_tokens, declared.max_tokens),
            max_wall_time=_stricter(budget.max_wall_time, declared.max_wall_time),
            max_tool_calls=_stricter(budget.max_tool_calls,
                                     declared.max_tool_calls),
        )

    budget.start()
    return budget


def _event_field(event: dict, key: str, default: Any = None) -> Any:
    """Read an event field across flat and canonical ({type, data}) shapes."""
    if key in event:
        return event[key]
    data = event.get("data")
    if isinstance(data, dict) and key in data:
        return data[key]
    return default


def _child_verdict(*, saw_done: bool, saw_fatal_error: bool, error_message: str,
                   budget_error: str, output_text: str,
                   last_nonempty_round: str) -> tuple[bool, str, str | None]:
    """``(success, output, error)`` for a child's turn, from the turn's own terminal evidence.

    The runner used to report success whenever the stream ended without an ``error`` event, so a
    stream that stopped without ``done`` returned the narration written before its last tool call
    ("Let me check the …") as a finished report, with ``ok: true``. ADR-0044's
    ``terminal_outcome_from_evidence`` is the one authority for whether a turn succeeded;
    exhausting the child's resource budget is a failure in its own right.
    """
    from wisp.core.goal import TerminalOutcome, terminal_outcome_from_evidence

    if budget_error:
        return False, f"[BUDGET EXHAUSTED] {budget_error}", budget_error
    outcome = terminal_outcome_from_evidence(saw_done=saw_done, saw_fatal_error=saw_fatal_error)
    if outcome is TerminalOutcome.SUCCEEDED:
        # A child whose LAST action was a tool call (remember/save) never
        # narrates afterwards — fall back to its last words.
        return True, output_text if output_text.strip() else last_nonempty_round, None
    if outcome is TerminalOutcome.FAILED:
        error = error_message or "turn failed"
        return False, error, error
    error = error_message or "the turn ended without completing"
    return False, f"[INCOMPLETE] {error}", error


logger = logging.getLogger(__name__)


class FirstTokenTimeout(asyncio.TimeoutError):
    """Provider accepted the request but streamed nothing within budget.

    Distinct from a wall-clock timeout so callers can message it honestly
    while still treating it as retryable (timed_out=True).
    """

    def __init__(self, deadline_s: float):
        super().__init__(f"no stream events within {deadline_s:.0f}s")
        self.deadline_s = deadline_s


class SubagentRunner:
    """Execute a single subagent contract and return a ``SubagentResult``.

    Responsibilities:
    - Build child config from parent config + contract overrides
    - Create a Session for the subagent
    - Build system prompt (role-based or default)
    - Run ``WispAgentCore.run_task()`` directly in the same event loop
    - Collect tool calls, files changed, token estimates
    - Return a fully populated ``SubagentResult``

    This class does **not** handle:
    - Caching (see ``ResultCache``)
    - Token budgets (see ``BudgetTracker``)
    - Worktrees (see ``WorktreeManager``)
    - Persistence (see ``Persistence``)
    - Patterns (map-reduce, vote, chain)
    """

    # Silent-stall guard: a healthy provider streams reasoning deltas
    # within seconds. No first event inside this window means the request
    # is dead (observed: NVIDIA early-session holds with zero bytes) —
    # abort so the orchestrator's retry lands on a live socket instead of
    # burning the whole budget. Env: WISP_FIRST_TOKEN_DEADLINE.
    FIRST_TOKEN_DEADLINE_S = float(os.environ.get("WISP_FIRST_TOKEN_DEADLINE", "90"))

    def __init__(
        self,
        parent_config: WispConfig,
        workspace: Path,
        store: UnifiedStore | None = None,
        tool_executor: Any | None = None,
        agent_runtime: Any | None = None,
    ):
        self.parent_config = parent_config
        self.workspace = workspace
        default_db = Path(workspace) / ".wisp" / "wisp.db"
        self._store = store or UnifiedStore(default_db)
        self._tool_executor = tool_executor
        self._agent_runtime = agent_runtime
        # Warm cache: reuse provider/security/extensions across subagent runs
        # to avoid re-creating HTTP connections and re-loading config on every spawn.
        self._provider_cache: dict[str, Any] = {}
        self._security_cache: Any | None = None
        self._extensions_cache: Any | None = None

    async def run(
        self,
        contract: SubagentContract,
        agent_workspace: str,
        system_prompt: str,
        progress_callback: Any = None,
    ) -> SubagentResult:
        """Run a single subagent and return its result.

        Uses a single wall-clock deadline — the contract timeout is the hard
        upper bound regardless of iteration count. Per-iteration timeouts are
        derived from remaining budget so a 300s contract with 30 iterations
        cannot run 9000s.
        """
        start = time.monotonic()
        deadline = start + contract.timeout_seconds
        tool_calls_log: list[dict] = []

        # Build child config
        child_cfg = self._build_child_config(contract, agent_workspace)

        # Soft provider health hint — informational only. We do NOT hard-fail
        # here: ollama reports "model not found" the same as "server down", and
        # a missing model may auto-pull on first request, while tests and
        # mocked cores don't need a live provider at all. The actual run +
        # contract timeout bound any genuinely unreachable provider.
        provider_name = getattr(child_cfg, "provider", "ollama")
        cache_key = f"{provider_name}:{child_cfg.model}"
        cached_provider = self._provider_cache.get(cache_key)
        if cached_provider is not None:
            try:
                health = cached_provider.health_check()
                if hasattr(health, "get") and health.get("status") == "unhealthy":
                    logger.debug(
                        "Subagent %s provider reported unhealthy (%s); proceeding — run will time out if truly unreachable.",
                        contract.name, health.get("error", "unknown"),
                    )
            except Exception as exc:
                logger.debug("Provider health check error for %s: %s", contract.name, exc)

        # Create session dict — fresh, or resumed from a prior stored session
        # when this contract continues an earlier conversation (background
        # agent follow-ups). A resume keeps the same session id and history.
        import uuid
        from datetime import datetime, timezone
        resume_id = str(getattr(contract, "_resume_session_id", "") or "")
        if resume_id:
            loaded = self._store.load_session(resume_id)
            if not isinstance(loaded, dict) or "messages" not in loaded:
                logger.warning(
                    "Subagent %s: cannot resume missing session %s",
                    contract.name, resume_id,
                )
                return SubagentResult(
                    task_id=contract.name,
                    success=False,
                    output=f"[RESUME FAILED] Stored session '{resume_id}' not found.",
                    error=f"session {resume_id} not found",
                    elapsed_seconds=0.0,
                )
            session = loaded
            session.setdefault("title", f"[sub] {contract.name}")
        else:
            session_id = f"sess-{uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc).isoformat()
            if self._agent_runtime is not None:
                session = {
                    "id": session_id,
                    "model": child_cfg.model,
                    "workspace": agent_workspace,
                    "messages": [],
                    "compaction_history": [],
                    "created_at": now,
                    "updated_at": now,
                    "title": f"[sub] {contract.name}",
                }
            else:
                session = {
                    "id": session_id,
                    "model": child_cfg.model,
                    "workspace": agent_workspace,
                    "messages": [{"role": "user", "content": contract.task}],
                    "compaction_history": [],
                    "created_at": now,
                    "updated_at": now,
                    "title": f"[sub] {contract.name}",
                }
            self._store.create_session(session_id, child_cfg.model, agent_workspace, title=f"[sub] {contract.name}")

        # Emit start event
        if progress_callback:
            await self._emit(
                progress_callback,
                contract.name,
                EventKind.TASK_STARTED,
                {"role": contract.role, "description": contract.task},
            )

        try:
            async with asyncio.timeout(contract.timeout_seconds):
                result_dict = await self._run_agent(
                    contract,
                    child_cfg,
                    session,
                    system_prompt,
                    agent_workspace,
                    tool_calls_log,
                    deadline,
                )

            from datetime import datetime, timezone
            session["updated_at"] = datetime.now(timezone.utc).isoformat()
            # Sync messages back from the working copy that core.turn() modified
            session["messages"] = result_dict.get("messages", session.get("messages", []))
            self._store.save_session(session)

            duration = time.monotonic() - start
            subagent_result = SubagentResult(
                task_id=contract.name,
                success=result_dict["success"],
                output=result_dict["output"],
                tool_calls=list(tool_calls_log),
                elapsed_seconds=duration,
                error=result_dict.get("error"),
                session_id=session["id"],
                files_changed=result_dict.get("files_changed", []),
                iterations_used=result_dict.get("iterations_used", 0),
            )

            # Token estimation
            messages = result_dict.get("messages", [])
            if messages:
                in_tok, out_tok, total_tok = self._estimate_tokens(messages)
                subagent_result.input_tokens = in_tok
                subagent_result.output_tokens = out_tok
                subagent_result.tokens_used = total_tok

                # Enforce per-contract output token limit
                if contract.max_output_tokens and out_tok > contract.max_output_tokens:
                    logger.warning(
                        "Subagent %s output tokens %d exceed limit %d",
                        contract.name, out_tok, contract.max_output_tokens,
                    )
                    subagent_result.output = self._compress_output(
                        subagent_result.output, contract.max_output_chars,
                        reason=f"exceeded {contract.max_output_tokens} output tokens",
                    )

            # Enforce per-contract output char limit
            if len(subagent_result.output) > contract.max_output_chars:
                subagent_result.output = self._compress_output(
                    subagent_result.output, contract.max_output_chars,
                    reason=f"exceeded {contract.max_output_chars} characters",
                )

            # Emit completion event. A child whose turn ended ok:false is
            # a FAILED child — announcing ✓ here rendered dishonest
            # lifecycle lines in the live fanout run (2026-08-25).
            if progress_callback:
                payload: dict[str, Any] = {
                    "role": contract.role,
                    "elapsed": duration,
                }
                if subagent_result.success:
                    payload["files_changed"] = subagent_result.files_changed
                    payload["output"] = subagent_result.output[:200]
                    kind = EventKind.TASK_COMPLETED
                else:
                    payload["error"] = (
                        subagent_result.error or "subagent reported failure"
                    )[:120]
                    kind = EventKind.TASK_FAILED
                await self._emit(progress_callback, contract.name, kind, payload)

            return subagent_result

        except FirstTokenTimeout as e:
            duration = time.monotonic() - start
            logger.warning(
                "Subagent %s: %s — failing fast for retry",
                contract.name, e,
            )
            session["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._store.save_session(session)
            return SubagentResult(
                task_id=contract.name,
                success=False,
                output=(
                    f"[FIRST TOKEN TIMEOUT] No stream events within "
                    f"{e.deadline_s:.0f}s. The provider accepted the request "
                    f"but streamed nothing."
                ),
                tool_calls=list(tool_calls_log),
                elapsed_seconds=duration,
                error=str(e),
                session_id=session["id"],
                timed_out=True,  # retryable via the orchestrator's ×1.5 path
            )

        except asyncio.TimeoutError:
            duration = time.monotonic() - start
            logger.warning("Subagent %s timed out after %.1fs", contract.name, duration)
            session["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._store.save_session(session)
            if progress_callback:
                await self._emit(
                    progress_callback,
                    contract.name,
                    EventKind.TASK_FAILED,
                    {"error": f"Timeout after {contract.timeout_seconds}s"},
                )
            # Build a helpful diagnostic message
            diag_parts = [f"Timed out after {duration:.1f}s"]
            if tool_calls_log:
                diag_parts.append(f"made {len(tool_calls_log)} tool calls")
                last_tool = tool_calls_log[-1]["name"] if tool_calls_log else "none"
                diag_parts.append(f"last tool: {last_tool}")
            else:
                diag_parts.append("no tool calls were made")
                diag_parts.append("(model may be unreachable or too slow — check `ollama ps`)")
            return SubagentResult(
                task_id=contract.name,
                success=False,
                output=f"[TIMED OUT] {' — '.join(diag_parts)}",
                tool_calls=list(tool_calls_log),
                elapsed_seconds=duration,
                error=f"Timeout after {contract.timeout_seconds}s",
                session_id=session["id"],
                timed_out=True,
            )

        except Exception as exc:
            duration = time.monotonic() - start
            logger.error("Subagent %s crashed: %s", contract.name, exc, exc_info=True)
            if progress_callback:
                await self._emit(
                    progress_callback,
                    contract.name,
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
                session_id=session["id"],
            )

    async def _run_agent(
        self,
        contract: SubagentContract,
        config: WispConfig,
        session: Session,
        system_prompt: str,
        workspace_path: str,
        tool_calls_log: list[dict],
        deadline: float,
    ) -> dict:
        """Run a stateless WispAgentCore instance and return its result dict.

        Uses the new engine (wisp.core.engine) instead of the deprecated
        stateful core (wisp.core.agent).
        """
        # Route through AgentRuntime when available (Issue 2)
        if self._agent_runtime is not None:
            return await self._run_via_runtime(
                contract, config, session, system_prompt, workspace_path, tool_calls_log, deadline
            )
        from wisp.core.engine import WispAgentCore as StatelessCore
        from wisp.providers.factory import ProviderFactory
        from wisp.infra.security import SecurityPolicy
        from wisp.infra.extensions import ExtensionHost

        # Propagate subagent depth/branch from contract to config so the core
        # can access them (and tests can verify propagation).
        config = config.replace(
            _subagent_depth=getattr(contract, "_subagent_depth", 0),
            _subagent_branch_count=getattr(contract, "_subagent_branch_count", 0),
        )

        provider_name = getattr(config, "provider", None)
        if not isinstance(provider_name, str):
            provider_name = "ollama"

        # Reuse cached provider when model + provider match (warm pool optimization).
        # Provider creation may establish HTTP connections; caching avoids this overhead.
        cache_key = f"{provider_name}:{config.model}"
        provider = self._provider_cache.get(cache_key)
        if provider is None:
            factory = ProviderFactory()
            provider = factory.from_config(config)
            self._provider_cache[cache_key] = provider

        # Reuse security policy and extensions (stateless, safe to share)
        if self._security_cache is None:
            self._security_cache = SecurityPolicy(
                permission_mode=getattr(config, "permission_mode", "full"),
            )
        if self._extensions_cache is None:
            self._extensions_cache = ExtensionHost()

        security = self._security_cache
        extensions = self._extensions_cache

        try:
            # Register share_finding tool when shared context is active
            if contract._shared_context is not None:
                from .shared_context import build_shared_context_tool_schema, build_shared_context_tool_impl
                share_schema = build_shared_context_tool_schema()
                share_impl = build_shared_context_tool_impl(contract.name, contract._shared_context)
                # Add as a temporary extension tool
                if not hasattr(extensions, '_shared_context_tools'):
                    extensions._shared_context_tools = {}
                extensions._shared_context_tools[share_schema["function"]["name"]] = (share_schema, share_impl)

            core = StatelessCore(
                provider=provider,
                security=security,
                extensions=extensions,
                config=config,
                tool_executor=self._tool_executor,
            )

            session_dict = dict(session)
            # Don't inject system prompt into messages — core.turn() builds its own
            # via _build_system_prompt(). Passing it as a message would create
            # duplicate system prompts and confuse the LLM.
            # Instead, store it in session metadata for the context assembler.
            if system_prompt:
                session_dict["subagent_system_prompt"] = system_prompt
            # Role tool restrictions become enforced (not just prompt text):
            # core filters the tool schemas AND rejects disallowed calls.
            _effective = _effective_child_tools(
                contract.tools,
                str(getattr(config, "permission_mode", "auto_edit") or "auto_edit"),
            )
            session_dict["allowed_tools"] = _effective
            # The child's authorization identity (migration M15). `allowed_tools`
            # is enforced by the core as a schema filter; this is the identity the
            # authorization layer consults, so a child is denied at L1 rather
            # than only being offered fewer schemas.
            session_dict["principal"] = self._child_principal(contract, _effective)

            # Partition context — only pass relevant history to subagent
            raw_messages = list(session_dict.get("messages", []))
            if len(raw_messages) > 10:
                from .context_partition import ContextPartitioner
                partitioner = ContextPartitioner(max_messages=10, max_tokens=4000)
                filtered = partitioner.partition(raw_messages, contract.task, include_system=True)
                # Always ensure the task message is present
                task_msg = {"role": "user", "content": contract.task}
                has_task = any(
                    m.get("role") == "user" and m.get("content") == contract.task
                    for m in filtered
                )
                if not has_task:
                    filtered.append(task_msg)
                session_dict["messages"] = filtered
                logger.debug(
                    "Context partitioned for %s: %d → %d messages",
                    contract.name, len(raw_messages), len(filtered),
                )

            output_text = ""
            engine_iterations = 0
            last_nonempty_round = ""
            saw_done = saw_fatal_error = False
            error_message = budget_error = ""

            budget = _budget_from_contract(contract, deadline)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError("contract deadline reached")

            async with asyncio.timeout(remaining):
                child_start = time.monotonic()
                last_event_at = child_start
                first_event_seen = False
                # The confirmation gate is structural now: a mutating tool with no
                # approver is DENIED rather than executed. A subagent's
                # authorisation is its contract's `auto_approve`, which
                # `_build_child_config` already sets — so the child states its
                # authorisation explicitly instead of inheriting a fall-through.
                # A contract with `auto_approve=False` now gets DENIED for a
                # write, which is the correct reading: it was never authorised.
                stream = core.turn(session_dict, contract.task)
                try:
                    # First-token deadline: wait_for cancels the pending
                    # __anext__ on stall; the generator is closed below so
                    # the provider bridge thread stops with it.
                    first_event = await asyncio.wait_for(
                        stream.__anext__(), timeout=self.FIRST_TOKEN_DEADLINE_S
                    )
                except asyncio.TimeoutError as exc:
                    await stream.aclose()
                    raise FirstTokenTimeout(self.FIRST_TOKEN_DEADLINE_S) from exc

                async def _with_first(first, rest):
                    yield first
                    async for ev in rest:
                        yield ev

                async for event in _with_first(first_event, stream):
                    now = time.monotonic()
                    etype = event.get("type")
                    if not first_event_seen:
                        first_event_seen = True
                        # Where a dying child's budget goes: latency to
                        # first token vs time lost mid-turn.
                        logger.info(
                            "Subagent %s first event (%s) after %.1fs — task=%d chars, msgs=%d",
                            contract.name, etype, now - child_start,
                            len(contract.task or ""),
                            len(session_dict.get("messages", [])),
                        )
                    elif etype == "tool_call":
                        logger.info(
                            "Subagent %s tool %s at %.1fs (+%.1fs since prev)",
                            contract.name, event.get("name", "?"),
                            now - child_start, now - last_event_at,
                        )
                    last_event_at = now
                    if etype == "content":
                        # Streaming providers emit many small deltas; the
                        # report is their concatenation, not the last chunk.
                        output_text += event.get("text", "")
                        if output_text.strip():
                            last_nonempty_round = output_text
                    elif etype == "tool_call":
                        engine_iterations += 1
                        budget.record_tool_call()
                        name = event.get("name", "")
                        args = event.get("arguments", {})
                        arg_preview = self._compact_args(args)
                        tool_calls_log.append({"name": name, "args_preview": arg_preview})
                        budget_error = budget.check() or ""
                        if budget_error:
                            logger.warning(
                                "Subagent %s budget exhausted: %s",
                                contract.name, budget_error,
                            )
                            break
                        # Content after a tool call belongs to the next
                        # round — pre-action narration is not the answer.
                        output_text = ""
                    elif etype == "tool_result":
                        result_data = event.get("result", "")
                        if isinstance(result_data, str):
                            budget.record_tokens(len(result_data) // 4)
                        budget_error = budget.check() or ""
                        if budget_error:
                            logger.warning(
                                "Subagent %s budget exhausted: %s",
                                contract.name, budget_error,
                            )
                            break
                    elif etype == "done":
                        saw_done = True
                    elif etype == "error":
                        # Only a fatal error fails the turn (ADR-0044); a
                        # recoverable one must not abandon the child mid-turn.
                        error_message = str(_event_field(event, "message") or "turn failed")
                        if not _event_field(event, "recoverable", True):
                            saw_fatal_error = True

            success, output_text, error = _child_verdict(
                saw_done=saw_done, saw_fatal_error=saw_fatal_error,
                error_message=error_message, budget_error=budget_error,
                output_text=output_text, last_nonempty_round=last_nonempty_round)
            return {
                "success": success,
                "output": output_text,
                "error": error,
                "files_changed": self._extract_files_changed(output_text) if success else [],
                "iterations_used": engine_iterations,
                "messages": session_dict.get("messages", []),
            }
        finally:
            pass  # Provider is cached for reuse — do not close


    async def _run_via_runtime(
        self,
        contract: SubagentContract,
        config: WispConfig,
        session: Session,
        system_prompt: str,
        workspace_path: str,
        tool_calls_log: list[dict],
        deadline: float,
    ) -> dict:
        """Route subagent execution through AgentRuntime instead of bypassing.

        The runtime's run_turn() will:
        - Add the user message (contract.task) itself
        - Call core.turn() which builds its own system prompt
        - Handle compaction, session persistence, etc.

        We pass the contract's system prompt via session metadata so
        core._build_system_prompt() can use it as role_extra, avoiding
        double system prompts.
        """
        session_dict = dict(session)
        # Don't inject system prompt into messages — core.turn() builds its own.
        # Instead, pass it via session metadata for the context assembler.
        # A resumed conversation keeps its stored history; a fresh run starts
        # empty (get_or_create_session would otherwise replay the old thread).
        if not getattr(contract, "_resume_session_id", None):
            session_dict["messages"] = []
        if system_prompt:
            session_dict["subagent_system_prompt"] = system_prompt
        _effective = _effective_child_tools(
            contract.tools,
            str(getattr(config, "permission_mode", "auto_edit") or "auto_edit"),
        )
        session_dict["allowed_tools"] = _effective
        session_dict["principal"] = self._child_principal(contract, _effective)

        # Ensure session exists in runtime store
        sid = session_dict.get("id", "")
        model = session_dict.get("model", "")
        ws = session_dict.get("workspace", "")
        runtime_session = await self._agent_runtime.get_or_create_session(sid, model, ws)
        runtime_session["messages"] = list(session_dict["messages"])
        if system_prompt:
            runtime_session["subagent_system_prompt"] = system_prompt
            runtime_session["workspace"] = ws
        if "allowed_tools" in session_dict:
            runtime_session["allowed_tools"] = session_dict["allowed_tools"]
        # The runtime path builds a SECOND session dict; the principal has to be
        # stamped into it too, or this path authorizes as the local human while
        # the other one does not — the half-fix this migration keeps finding.
        if "principal" in session_dict:
            runtime_session["principal"] = session_dict["principal"]
        # `contract.task` is written by the parent MODEL, so it may not drive a host-run
        # criteria probe (ADR-0050 R6's premise is a caller-written objective).
        from wisp.core.turn_criteria import MODEL_AUTHORED_PROMPT_KEY
        runtime_session[MODEL_AUTHORED_PROMPT_KEY] = True

        output_text = ""
        engine_iterations = 0
        last_nonempty_round = ""
        saw_done = saw_fatal_error = False
        error_message = budget_error = ""

        budget = _budget_from_contract(contract, deadline)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError("contract deadline reached")

        async with asyncio.timeout(remaining):
            async for event in self._agent_runtime.run_turn(runtime_session, contract.task):
                etype = event.get("type")
                if etype == "content":
                    # Streaming providers emit many small deltas; the
                    # report is their concatenation, not the last chunk.
                    output_text += event.get("text", "")
                    if output_text.strip():
                        last_nonempty_round = output_text
                elif etype == "tool_call":
                    engine_iterations += 1
                    budget.record_tool_call()
                    name = event.get("name", "")
                    args = event.get("arguments", {})
                    arg_preview = self._compact_args(args)
                    tool_calls_log.append({"name": name, "args_preview": arg_preview})
                    budget_error = budget.check() or ""
                    if budget_error:
                        logger.warning(
                            "Subagent %s budget exhausted: %s",
                            contract.name, budget_error,
                        )
                        break
                    # Content after a tool call belongs to the next
                    # round — pre-action narration is not the answer.
                    output_text = ""
                elif etype == "tool_result":
                    result_data = event.get("result", "")
                    if isinstance(result_data, str):
                        budget.record_tokens(len(result_data) // 4)
                    budget_error = budget.check() or ""
                    if budget_error:
                        logger.warning(
                            "Subagent %s budget exhausted: %s",
                            contract.name, budget_error,
                        )
                        break
                elif etype == "done":
                    saw_done = True
                elif etype == "error":
                    # Only a fatal error fails the turn (ADR-0044); a
                    # recoverable one must not abandon the child mid-turn.
                    error_message = str(_event_field(event, "message") or "turn failed")
                    if not _event_field(event, "recoverable", True):
                        saw_fatal_error = True

        success, output_text, error = _child_verdict(
            saw_done=saw_done, saw_fatal_error=saw_fatal_error,
            error_message=error_message, budget_error=budget_error,
            output_text=output_text, last_nonempty_round=last_nonempty_round)
        return {
            "success": success,
            "output": output_text,
            "error": error,
            "files_changed": self._extract_files_changed(output_text) if success else [],
            "iterations_used": engine_iterations,
            "messages": runtime_session.get("messages", []),
        }

    def close(self) -> None:
        """Close cached providers and release resources."""
        for provider in self._provider_cache.values():
            if hasattr(provider, "close"):
                try:
                    provider.close()
                except Exception:
                    pass
        self._provider_cache.clear()

    def _child_principal(self, contract: SubagentContract,
                         effective_tools: list[str]):
        """The narrowed principal this child authorizes as (migration M15).

        P9 built `child_principal` and `ToolExecutor.principal`; this is the
        spawn site they were waiting for. Until now the child core was handed
        the **parent's** executor, whose `principal` is `None`, so every child
        call was authorized as the unbounded local human.

        The parent is resolved by `auth.principal.executor_principal` — the same
        rule the executor uses for its own calls — so a child can never be
        derived from a different principal than its parent's own calls
        authorize as.

        `effective_tools` is passed explicitly rather than left to
        `child_principal` reading `contract.tools`, because by this point
        `_effective_child_tools` has resolved `"all"` (and the permission mode)
        into a concrete list. `child_principal` correctly refuses to *guess* at
        `"all"`; here there is nothing to guess.

        Returns `None` when there is no executor to derive from — a runner
        without one has no authorization layer at all, and the core's own
        no-executor fallback already denies anything but safe reads.
        """
        if self._tool_executor is None:
            return None
        from wisp.auth.principal import child_principal, executor_principal

        workspace = str(getattr(self, "workspace", "") or "")
        parent = executor_principal(
            self._tool_executor, workspace=workspace,
            # `parent_config`, not `_parent_config`. The attribute is set as `self.parent_config`
            # at :140 and read that way at :814, :815, :818, :836 and :837 — this one line was the
            # only underscore, so EVERY subagent run via the runtime raised AttributeError and was
            # retried three times before failing. One character, on the one path no test covers.
            profile=str(getattr(self.parent_config, "profile", None)
                        or "default"))
        return child_principal(parent, contract, capabilities=effective_tools)

    def _build_child_config(self, contract: SubagentContract, workspace: str) -> WispConfig:
        """Clone the parent config with optional per-subagent overrides."""
        child = self.parent_config.replace(
            model=contract.model or self.parent_config.model,
            workspace=workspace,
            auto_approve=contract.auto_approve,
            max_context_tokens=contract.max_tokens or self.parent_config.max_context_tokens,
            max_iterations=contract.max_iterations,
        )
        # Stamp nesting position so a subagent's own spawn/fanout calls inherit
        # its depth instead of resetting to 0 (unbounded recursion guard).
        object.__setattr__(child, "_subagent_depth", int(getattr(contract, "_subagent_depth", 0) or 0))
        object.__setattr__(child, "_subagent_branch_count", int(getattr(contract, "_subagent_branch_count", 0) or 0))
        return child

    def _estimate_tokens(self, messages: list[dict]) -> tuple[int, int, int]:
        """Estimate token count from message history.

        Returns (input_tokens, output_tokens, total_tokens).
        Uses tiktoken for accurate counting when available, falls back to
        model-specific char ratios (claude=3.5, gpt=4, gemini=3, default=4).
        """
        from wisp.infra.token_counter import TokenCounter

        model = getattr(self.parent_config, "model", "") or ""
        chars_per_token = getattr(self.parent_config, "chars_per_token", 4)
        counter = TokenCounter(chars_per_token=chars_per_token)

        input_chars = 0
        output_chars = 0

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""
            text = content if isinstance(content, str) else str(content)

            if role in ("user", "system", "tool"):
                input_chars += len(text)
            elif role == "assistant":
                output_chars += len(text)
                for tc in msg.get("tool_calls", []) or []:
                    func = tc.get("function", {})
                    args = func.get("arguments", "")
                    output_chars += len(args) if isinstance(args, str) else len(str(args))

        # Use tiktoken if model is known for total count, char ratio for split.
        # Tool call args already accounted in char computation above.
        input_tokens = counter.estimate_chars(input_chars)
        output_tokens = counter.estimate_chars(output_chars)
        if model:
            parts: list[str] = []
            for m in messages:
                content = m.get("content", "") or ""
                if isinstance(content, str):
                    parts.append(content)
                for tc in m.get("tool_calls", []) or []:
                    func = tc.get("function", {})
                    args = func.get("arguments", "")
                    if isinstance(args, str):
                        parts.append(args)
            full_text = "".join(parts)
            tiktoken_total = counter.count(full_text, model=model)
            if tiktoken_total > 0:
                # Sanity check: tiktoken total should be in same ballpark
                char_total = input_tokens + output_tokens
                if char_total > 0 and abs(tiktoken_total - char_total) / char_total > 0.5:
                    # Large discrepancy — tiktoken disagrees with char ratio.
                    # Use tiktoken total, split proportionally.
                    total_chars = input_chars + output_chars
                    if total_chars > 0:
                        input_tokens = max(0, int(tiktoken_total * input_chars / total_chars))
                        output_tokens = max(0, tiktoken_total - input_tokens)

        return input_tokens, output_tokens, input_tokens + output_tokens

    @staticmethod
    async def _emit(
        callback: Any,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Emit a progress event via the callback."""
        event = OrchestratorEvent(
            task_id=task_id,
            event_type=event_type,
            payload=payload,
        )
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(event)
            else:
                callback(event)
        except Exception as e:
            logger.warning("Progress callback failed for %s: %s", task_id, e)

    @staticmethod
    def _compress_output(text: str, max_chars: int, reason: str) -> str:
        """Compress output to fit within max_chars while preserving key content.

        Strategy (preserves the most useful information for the parent agent):
        1. Keep the first section (problem statement / summary)
        2. Keep all code blocks (truncated if individually too long)
        3. Keep headings and bullet points
        4. Keep the last section (final conclusions / file list)
        5. Fill remaining budget with prose from the middle
        """
        if len(text) <= max_chars:
            return text

        import re

        # Reserve space for the truncation notice
        notice = f"\n\n[OUTPUT COMPRESSED: {reason}. Original: {len(text)} chars.]"
        budget = max_chars - len(notice)
        if budget < 200:
            # Small budget (can be negative when the notice itself exceeds
            # max_chars): keep as much original text as fits alongside the
            # notice, then hard-cap so output never exceeds max_chars.
            keep = max(0, budget)
            return (text[:keep] + notice)[:max_chars]

        # Split into sections by markdown headers
        sections = re.split(r'(\n#{1,4}\s+.+)', text)
        # Reassemble sections with their headers
        chunks: list[tuple[str, str]] = []  # (header, body)
        current_header = ""
        current_body = ""
        for part in sections:
            if re.match(r'\n#{1,4}\s+', part):
                if current_body or current_header:
                    chunks.append((current_header, current_body))
                current_header = part.strip()
                current_body = ""
            else:
                current_body += part
        if current_body or current_header:
            chunks.append((current_header, current_body))

        # If no sections found, fall back to beginning + end
        if len(chunks) <= 1:
            keep_start = int(budget * 0.6)
            keep_end = int(budget * 0.3)
            return text[:keep_start] + "\n...\n" + text[-keep_end:] + notice

        # Prioritize: first section, last section, code blocks, headings
        result_parts: list[str] = []
        used = 0

        # Always keep first section (context/summary)
        first_header, first_body = chunks[0]
        first_text = (first_header + "\n" + first_body).strip()
        if len(first_text) > budget * 0.4:
            first_text = first_text[:int(budget * 0.4)] + "..."
        result_parts.append(first_text)
        used += len(first_text)

        # Always keep last section (conclusions/files)
        if len(chunks) > 1:
            last_header, last_body = chunks[-1]
            last_text = (last_header + "\n" + last_body).strip()
            if len(last_text) > budget * 0.3:
                last_text = last_text[:int(budget * 0.3)] + "..."
            if used + len(last_text) < budget:
                result_parts.append("...\n" + last_text)
                used += len(last_text) + 4

        # Fill remaining budget with middle sections, prioritizing code blocks
        remaining = budget - used - 10  # 10 for separators
        if remaining > 100 and len(chunks) > 2:
            middle_chunks = chunks[1:-1]
            # Sort by code block presence (code-heavy sections first)
            def _has_code(chunk):
                return "```" in chunk[1]
            middle_chunks.sort(key=_has_code, reverse=True)

            middle_parts: list[str] = []
            for header, body in middle_chunks:
                chunk_text = (header + "\n" + body).strip()
                if used + len(chunk_text) < budget:
                    middle_parts.append(chunk_text)
                    used += len(chunk_text) + 4
                else:
                    # Truncate this chunk
                    fits = budget - used - 10
                    if fits > 50:
                        middle_parts.append(chunk_text[:fits] + "...")
                        used += fits + 4
                    break

            if middle_parts:
                # Re-sort middle parts by original order
                result_parts.insert(1, "\n...\n".join(middle_parts))

        return "\n...\n".join(result_parts) + notice

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
        """Best-effort extraction of file paths mentioned in output text.

        Three-pass strategy:
        1. Backtick-quoted tokens — highest confidence.
        2. Structured multi-line list items after change-verb keywords.
        3. Bare word tokens that look like plausible file paths.

        Markdown decoration (**bold**, *italic*, _italic_) is stripped in a
        pre-processing step so that surrounded paths are still found.
        Paths are accepted if they contain a dot or a slash (covers
        extensionless files like Makefile / Dockerfile).
        """
        import re

        # ── Known-good extensions ────────────────────────────────────────
        _EXT = (
            r"py|ts|js|jsx|tsx|mjs|cjs|"
            r"rs|go|java|rb|sh|bash|zsh|fish|"
            r"c|cpp|cc|h|hpp|"
            r"json|yaml|yml|toml|ini|cfg|conf|env|"
            r"md|rst|txt|csv|"
            r"html|css|scss|sass|"
            r"sql|proto|"
            r"dockerfile|makefile|gemfile|rakefile"
        )
        _EXT_RE = re.compile(rf"\.(?:{_EXT})$", re.IGNORECASE)

        # ── Pre-strip markdown decoration (keep backticks for Pass 1) ───
        clean = text
        clean = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", clean)   # **bold**
        clean = re.sub(r"\*([^*\n]+)\*",     r"\1", clean)   # *italic*
        clean = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", clean)  # _italic_

        # ── Helpers ──────────────────────────────────────────────────────
        def _clean_token(raw: str) -> str:
            s = raw.strip()
            s = re.sub(r"^[`*_\[(<\"']+|[`*_\])>\"']+$", "", s)
            return s.strip()

        def _is_plausible(s: str) -> bool:
            if not s or len(s) < 2 or len(s) > 260:
                return False
            if "." not in s and "/" not in s:
                return False
            if re.search(r'[<>:"|?*\x00-\x1f]', s):
                return False
            if _EXT_RE.search(s):
                return True
            if "/" in s:
                return True
            basename = s.rsplit("/", 1)[-1].lower()
            return basename in {
                "makefile", "dockerfile", "gemfile", "rakefile",
                "procfile", "vagrantfile", "jenkinsfile", "brewfile",
            }

        found: list[str] = []
        seen: set[str] = set()

        def _add(raw: str) -> None:
            path = _clean_token(raw)
            if path and path not in seen and _is_plausible(path):
                seen.add(path)
                found.append(path)

        # ── Pass 1: backtick-quoted tokens (run on original text) ────────
        for m in re.finditer(r"`([^`\n]{2,260})`", text):
            _add(m.group(1))

        # ── Pass 2: multi-line list blocks after change-verb keywords ────
        # Capture everything after the colon/dash up to the next blank line
        # or non-list line, then iterate over every bullet item inside.
        verb_block_re = re.compile(
            r"(?:changed|modified|touched|wrote|created|updated|deleted|removed)"
            r"(?:\s+files?)?"
            r"[:\-]"
            r"((?:\s*\n\s*[-*•]\s+[^\n]+)+)",
            re.IGNORECASE,
        )
        item_re = re.compile(r"[-*•]\s+([^\n]+)")
        for block_m in verb_block_re.finditer(clean):
            for item_m in item_re.finditer(block_m.group(1)):
                _add(item_m.group(1))

        # ── Pass 3: bare tokens with a known extension ───────────────────
        bare_re = re.compile(
            r"(?<![a-zA-Z0-9])"
            r"((?:[a-zA-Z0-9_\-./]+/)?"
            r"[a-zA-Z0-9_\-]+"
            r"\.(?:" + _EXT + r"))"
            r"(?![a-zA-Z0-9])",
            re.IGNORECASE,
        )
        for m in bare_re.finditer(clean):
            _add(m.group(1))

        return found[:20]
