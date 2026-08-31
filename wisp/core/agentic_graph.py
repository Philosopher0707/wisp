"""Agentic graph loop — orchestrator (END vs fallback routing, circuit breaker, oscillation guards).

This is the *additive* graph layer promised by the audit. It does not replace
`WispAgentCore.turn()` or `AgentRuntime.run_turn()` — those remain the backwards-
compatible hot paths. A graph loop is one object:

    runner = GraphRunner(state=GraphState.initial(workspace="/ws"), config=cfg)
    final_state = await runner.run(prompt="fix bug", ...)

Execution model — linear agentic loop with branching terminals, mirrors the spec nodes:

    [START] → planner_coder ─→ sandbox_executor ─→ verifier ─┬─► END (COMPLETED)
                              ▲                                └─► fallback ─┘
                              │                                     │
                     human_approval (breakpoint: NEEDS_HUMAN_REVIEW) │
                              │                                     │
                              └──────────── next iteration ←─────────┘

On each iteration we:

  1. planner_coder: produce the next action (or note that the model emitted none)
  2. gates: optional human_approval before any write/sandbox step
  3. sandbox_executor: run the commanded shell (or no-op when there is no command)
  4. verifier: demand exit-0 evidence when code changed (or custom check)
  5. route: verifier passed → END; failed & retries remain → fallback (loop); budget exhausted → FAILED; approval needed → NEEDS_HUMAN_REVIEW

Defensive guarantees (all with actionable logs, never bare exceptions):

  - Circuit breaker: `iteration_count > max_iterations` → FAILED, not exception.
  - Oscillation guard: repeated state hashes → FAILED with hint to increase budget.
  - Per-node timeouts: every node enforces its own wall-clock deadline.
  - Explicit error handlers around every transition/edge and snapshot/rollback for fatal payloads.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from wisp.core.graph_nodes import (
    ApprovalDeps,
    NodeResult,
    PlannerCoderDeps,
    SandboxDeps,
    VerifierDeps,
    human_approval_node,
    planner_coder_node,
    sandbox_executor_node,
    verifier_node,
)
from wisp.core.graph_state import GraphState, GraphStatus

logger = logging.getLogger(__name__)


# ── Done event shape — canonical terminal payload ─────────────────


@dataclass
class DonePayload:
    """Structured terminal result — mirrors `wisp/core/events.done` but for the graph layer.

    Carries everything `done` does (session_id, turns, summary, reason) plus
    graph-specific fields (status, error, execution summary).
    """

    session_id: str
    reason: str  # natural | max_iterations | failed | needs_human_review | oscillation | error
    status: GraphStatus
    turns: int = 0  # == iteration_count at termination
    summary: str = ""
    error: str | None = None
    # Advisory: whether fallback synthesis should be attempted by the caller.
    should_fallback: bool = False


@dataclass
class GraphConfig:
    """Graph runner tuning — all optional, all bounded, all env-tunable via nodes."""

    # Reconciled default: spec says 5 (graph outer loop), inner turn stays at 50.
    max_iterations: int = 5
    max_sandbox_iterations: int = 2  # verification nudges inside the verifier branch
    enable_oscillation_guard: bool = True
    oscillation_window: int = 3
    enable_verifier_gate: bool = True
    sandbox_timeout_s: float = 120.0
    # Graph-level wall clock (independent of turn_timeout). 0 = no limit.
    graph_timeout_s: float = 0.0


@dataclass
class GraphRunner:
    """Stateless graph orchestrator — one instance per turn, constructed by the caller.

    The runner owns no session state; it threads a `GraphState` through the nodes.
    Use `from wisp.core.graph_state import GraphState` to seed `state`.
    """

    state: GraphState
    config: GraphConfig = field(default_factory=GraphConfig)
    # Node dep bundles — inject mocks in tests exactly as the audit recommends
    # (all deps injected, so the guard is testable without a core).
    planner_deps: PlannerCoderDeps | None = None
    sandbox_deps: SandboxDeps | None = None
    verifier_deps: VerifierDeps | None = None
    approval_deps: ApprovalDeps | None = None
    # Optional hooks (e.g., emit events to transport, capture tool calls)
    on_state_change: Callable[[GraphState], None] | None = None
    on_node_result: Callable[[str, NodeResult], None] | None = None

    def _emit_state(self) -> None:
        if self.on_state_change is None:
            return
        try:
            self.on_state_change(self.state)
        except Exception as e:
            logger.debug("GraphRunner.on_state_change failed: %s", e, exc_info=True)

    def _emit_node(self, name: str, result: NodeResult) -> None:
        if self.on_node_result is None:
            return
        try:
            self.on_node_result(name, result)
        except Exception as e:
            logger.debug("GraphRunner.on_node_result %s failed: %s", name, e, exc_info=True)

    async def run(
        self,
        prompt: str = "",
        *,
        # Optional per-turn overrides
        extract_command: Callable[[GraphState, NodeResult], str | None] | None = None,
        requires_approval: Callable[[str, dict], tuple[bool, str]] | None = None,
        timeout_s: float | None = None,
    ) -> GraphState:
        """Drive the agentic loop until terminal (COMPLETED / FAILED / NEEDS_HUMAN_REVIEW).

        Returns the final `GraphState` (always — never raises on circuit/oscillation failures).
        Wraps every edge in an explicit handler so a misbehaving node produces a FAILED
        state with an actionable error, not an unhandled exception.

        `extract_command` maps the planner_coder's NodeResult (which carries raw provider
        events in `data["events"]`) to a shell command string for the sandbox node.
        When None, the sandbox step is skipped unless `verifier` needs evidence.

        `requires_approval(tool_name, args) -> (needs, reason)` gates sandbox_executor
        behind the human_approval breakpoint.
        """
        overall_start = time.monotonic()
        graph_deadline: float | None = None
        if self.config.graph_timeout_s and self.config.graph_timeout_s > 0:
            try:
                graph_deadline = overall_start + float(self.config.graph_timeout_s)
            except Exception:
                graph_deadline = None

        try:
            return await self._run_inner(prompt, extract_command=extract_command, requires_approval=requires_approval, graph_deadline=graph_deadline, timeout_s=timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            msg = f"GraphRunner unexpected terminal failure: {e}"
            logger.error("%s (session %s)", msg, self.state.session_id, exc_info=True)
            try:
                self.state.transition(GraphStatus.FAILED, error=msg)
            except Exception:
                pass
            return self.state

    async def _run_inner(
        self,
        prompt: str,
        *,
        extract_command: Callable[[GraphState, NodeResult], str | None] | None,
        requires_approval: Callable[[str, dict], tuple[bool, str]] | None,
        graph_deadline: float | None,
        timeout_s: float | None,
    ) -> GraphState:
        # Resolve max_iterations from state (already reconciled at construction) vs config
        if self.state.max_iterations != self.config.max_iterations:
            # Config wins when runner was constructed with an explicit config budget
            try:
                self.state.max_iterations = max(1, min(200, int(self.config.max_iterations)))
            except Exception:
                pass

        iteration = 0
        while True:
            # ── Global wall-clock guard for the whole graph run ───────
            if graph_deadline is not None and time.monotonic() >= graph_deadline:
                msg = f"Graph wall-clock budget ({self.config.graph_timeout_s:.0f}s) exhausted at iteration {iteration}"
                logger.warning("%s (session %s)", msg, self.state.session_id)
                self._transition_failed(msg, reason="timeout")
                break

            # ── Circuit breaker: iteration budget ─────────────────────
            if self.state.iteration_count >= self.state.max_iterations and not self.state.is_terminal():
                msg = f"Max graph iterations ({self.state.max_iterations}) reached — circuit breaker tripped"
                logger.warning("%s (session %s, iter=%d)", msg, self.state.session_id, self.state.iteration_count)
                self._transition_failed(msg, reason="max_iterations")
                break

            if self.state.is_terminal():
                break

            # Snapshot before this iteration so a hard node failure can rollback
            # to a consistent predecessor without leaking partial executor logs.
            try:
                self.state.snapshot()
            except Exception as e:
                logger.debug("GraphRunner snapshot failed — continuing without rollback: %s", e, exc_info=True)

            # ── planner_coder node ────────────────────────────────────
            try:
                self.state.increment_iteration()
                if self.state.is_terminal():
                    break
                new_state, planner_result = await self._run_planner_coder(prompt if iteration == 0 else "")
                self.state = new_state
                self._emit_node("planner_coder", planner_result)
                if not planner_result.success and planner_result.should_continue is False:
                    # Planner signalled terminal failure (e.g., unrecoverable provider error)
                    self._transition_failed(planner_result.error or planner_result.output or "planner_coder failed", reason="error")
                    break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                msg = f"planner_coder edge failed: {e}"
                logger.error("%s (session %s, iter=%d)", msg, self.state.session_id, self.state.iteration_count, exc_info=True)
                # Restore pre-iteration snapshot so partial mutations don't leak, then mark FAILED.
                self._try_rollback()
                self._transition_failed(msg, reason="error")
                break

            # ── Determine whether there is a command to execute ───────
            command: str | None = None
            try:
                if extract_command is not None:
                    command = extract_command(self.state, planner_result)  # type: ignore[arg-type]
                else:
                    # Default heuristic: if the planner emitted a run_bash tool_call, extract its command.
                    command = _default_extract_command(planner_result)
            except Exception as e:
                logger.warning("extract_command failed — skipping sandbox step: %s", e, exc_info=True)
                command = None

            # ── human_approval breakpoint (before sandbox) ────────────
            if command and requires_approval is not None:
                needs = False
                reason = ""
                try:
                    needs, reason = requires_approval("run_bash", {"command": command})
                except Exception as e:
                    logger.warning("requires_approval check failed — treating as not required: %s", e, exc_info=True)
                if needs:
                    try:
                        new_state, approval_result = await human_approval_node(
                            self.state, "run_bash", {"command": command}, reason=reason, deps=self.approval_deps
                        )
                        self.state = new_state
                        self._emit_node("human_approval", approval_result)
                        if self.state.status == GraphStatus.NEEDS_HUMAN_REVIEW or not approval_result.success:
                            # Paused — caller should resume after human responds via state.clear_review()
                            self._emit_state()
                            break
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        msg = f"human_approval edge failed for {command[:60]!r}: {e}"
                        logger.error("%s (session %s)", msg, self.state.session_id, exc_info=True)
                        self._try_rollback()
                        self._transition_failed(msg, reason="error")
                        break

            # ── sandbox_executor node ─────────────────────────────────
            sandbox_result: NodeResult | None = None
            if command:
                try:
                    new_state, sandbox_result = await sandbox_executor_node(
                        self.state, command, deps=self.sandbox_deps, workspace=self.state.workspace,
                        timeout_s=self.config.sandbox_timeout_s,
                    )
                    self.state = new_state
                    self._emit_node("sandbox_executor", sandbox_result)
                    # Structured log already appended inside the node.
                    # A sandbox timeout/deny is not terminal — control flows to verifier/fallback.
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    msg = f"sandbox_executor edge failed for {command[:60]!r}: {e}"
                    logger.error("%s (session %s)", msg, self.state.session_id, exc_info=True)
                    self._try_rollback()
                    self._transition_failed(msg, reason="error")
                    break
            else:
                # No command this iteration — still run verifier to decide END vs fallback
                logger.debug("GraphRunner iteration %d: no sandbox command — skipping to verifier (session %s)", self.state.iteration_count, self.state.session_id)

            # ── verifier node ─────────────────────────────────────────
            if not self.config.enable_verifier_gate:
                # Verifier disabled — route to END immediately after sandbox (or after planner if no sandbox)
                logger.debug("GraphRunner verifier disabled — routing to COMPLETED (session %s)", self.state.session_id)
                self._transition_completed("verifier disabled — completed")
                break

            try:
                new_state, verify_result = await verifier_node(self.state, deps=self.verifier_deps)
                self.state = new_state
                self._emit_node("verifier", verify_result)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                msg = f"verifier edge failed: {e}"
                logger.error("%s (session %s)", msg, self.state.session_id, exc_info=True)
                self._try_rollback()
                self._transition_failed(msg, reason="error")
                break

            # ── END vs fallback routing ───────────────────────────────
            try:
                should_end = bool(verify_result.success)  # type: ignore[possibly-undefined]
            except Exception:
                should_end = False

            if should_end:
                # All evidence healthy → END
                self._transition_completed(verify_result.output if verify_result else "verified")  # type: ignore[possibly-undefined]
                break

            # Fallback path — check if retries remain. The verifier's failure is
            # actionable (exit non-zero, no logs, etc.) and we loop for another attempt
            # unless the budget or oscillation guard says stop.
            if self.state.iteration_count >= self.state.max_iterations:
                msg = f"Max graph iterations ({self.state.max_iterations}) reached — verification still failing after {self.state.iteration_count} iterations (last: {(verify_result.error or verify_result.output)[:160] if verify_result else 'unknown'})"  # type: ignore[possibly-undefined]
                logger.warning("%s (session %s)", msg, self.state.session_id)
                self._transition_failed(msg, reason="max_iterations")
                break

            # Oscillation guard — detect loops where the same command keeps failing identically
            if self.config.enable_oscillation_guard:
                try:
                    if self.state.check_oscillation(window=self.config.oscillation_window):
                        msg = f"Oscillation detected at iteration {self.state.iteration_count} — loop guard tripped. Hint: increase max_iterations, fix the command/args, or clear the recurring error. Last verifier: {(verify_result.error or '')[:120] if verify_result else ''}"  # type: ignore[possibly-undefined]
                        logger.warning("%s (session %s)", msg, self.state.session_id)
                        self._transition_failed(msg, reason="oscillation")
                        break
                except Exception as e:
                    logger.debug("oscillation check failed — ignoring: %s", e, exc_info=True)

            # Loop continues — revert the snapshot so fallback retries from a clean predecessor
            # but keep the execution log so history is not lost? We keep the log (already
            # appended) and only collapse redundant state for the next iteration.
            try:
                # Consume the snapshot taken at iteration start; do not rollback — the
                # execution log is evidence we want to preserve. Just drop the marker.
                if self.state._snapshot_stack:
                    self.state._snapshot_stack.pop()
            except Exception:
                pass

            # Yield control briefly so concurrent graphs / transports stay responsive
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                raise

            iteration += 1
            self._emit_state()

        return self.state

    # ── Terminal transitions (with explicit handlers) ────────────────

    def _transition_completed(self, summary: str) -> None:
        try:
            ok = self.state.transition(GraphStatus.COMPLETED)
            if not ok:
                logger.warning("GraphRunner COMPLETED transition blocked (session %s, status=%s)", self.state.session_id, self.state.status)
            else:
                logger.info("GraphRunner → COMPLETED at iteration %d (session %s): %s", self.state.iteration_count, self.state.session_id, summary[:120])
        except Exception as e:
            logger.error("GraphRunner COMPLETED transition failed: %s", e, exc_info=True)
            try:
                self.state.status = GraphStatus.COMPLETED
            except Exception:
                pass

    def _transition_failed(self, error: str, reason: str = "failed") -> None:
        # reason is advisory for DonePayload; the state status is always FAILED
        _ = reason
        try:
            ok = self.state.transition(GraphStatus.FAILED, error=error)
            if not ok:
                logger.warning("GraphRunner FAILED transition blocked — forcing FAILED (session %s)", self.state.session_id)
                self.state.status = GraphStatus.FAILED
                self.state.error = str(error)[:2000]
            else:
                logger.warning("GraphRunner → FAILED at iteration %d (session %s): %s", self.state.iteration_count, self.state.session_id, error[:200])
        except Exception as e:
            logger.error("GraphRunner FAILED transition handler itself failed: %s", e, exc_info=True)
            try:
                self.state.status = GraphStatus.FAILED
                self.state.error = str(error)[:2000]
            except Exception:
                pass

    async def _run_planner_coder(self, prompt: str = "") -> tuple[GraphState, NodeResult]:
        """Hookable planner_coder entry — delegates to `planner_coder_node` (test seam)."""
        new_state, result = await planner_coder_node(self.state, prompt, deps=self.planner_deps)
        return new_state, result

    def _try_rollback(self) -> bool:
        """Attempt rollback; returns True if restored, False otherwise."""
        try:
            return self.state.rollback()
        except Exception as e:
            logger.error("GraphRunner rollback failed: %s", e, exc_info=True)
            return False

    # ── Done payload synthesis ───────────────────────────────────────

    def done_payload(self, *, summary: str = "") -> DonePayload:
        """Build the canonical terminal payload for this run (call after `run()`)."""
        try:
            if self.state.status == GraphStatus.COMPLETED:
                reason = "natural"
            elif self.state.status == GraphStatus.NEEDS_HUMAN_REVIEW:
                reason = "needs_human_review"
            elif self.state.status == GraphStatus.FAILED:
                err = (self.state.error or "").lower()
                if "oscillation detected" in err:
                    reason = "oscillation"
                elif "max graph iterations" in err or "max_iterations" in err:
                    # Oscillation hint contains "max_iterations" as advice — check oscillation first.
                    reason = "max_iterations"
                elif "timeout" in err or "wall-clock" in err:
                    reason = "timeout"
                else:
                    reason = "failed"
            else:
                reason = "unknown"
            return DonePayload(
                session_id=self.state.session_id,
                reason=reason,
                status=self.state.status,
                turns=self.state.iteration_count,
                summary=summary or (self.state.error or ""),
                error=self.state.error,
                should_fallback=(self.state.status == GraphStatus.FAILED and reason == "max_iterations"),
            )
        except Exception as e:
            logger.error("GraphRunner done_payload synthesis failed: %s", e, exc_info=True)
            return DonePayload(session_id=self.state.session_id, reason="error", status=GraphStatus.FAILED, turns=self.state.iteration_count, error=str(e))


# ── Default command extractor ─────────────────────────────────────


def _default_extract_command(planner_result: NodeResult) -> str | None:
    """Extract a run_bash command from the planner's raw provider events.

    Looks for the latest `tool_call` with `name == 'run_bash'` in `data["events"]`.
    Returns None when no such call exists.
    """
    try:
        events = planner_result.data.get("events") if isinstance(planner_result.data, dict) else None
        if not events:
            return None
        for ev in reversed(events):
            if not isinstance(ev, dict):
                continue
            if ev.get("type") in ("tool_call", "tool_calls") and ev.get("name") == "run_bash":
                args = ev.get("arguments", {})
                if isinstance(args, dict):
                    cmd = args.get("command")
                    if isinstance(cmd, str) and cmd.strip():
                        return cmd
                elif isinstance(args, str):
                    try:
                        import json
                        parsed = json.loads(args)
                        if isinstance(parsed, dict) and parsed.get("command"):
                            return str(parsed["command"])
                    except Exception:
                        pass
            # Batched ToolCallBatch shape: {"type":"tool_calls","calls":[...]}
            if ev.get("type") == "tool_calls" and isinstance(ev.get("calls"), list):
                for call in reversed(ev["calls"]):
                    func = call.get("function", {}) if isinstance(call, dict) else {}
                    if func.get("name") == "run_bash":
                        args = func.get("arguments", {})
                        if isinstance(args, dict) and args.get("command"):
                            return str(args["command"])
                        if isinstance(args, str):
                            try:
                                import json
                                parsed = json.loads(args)
                                if isinstance(parsed, dict) and parsed.get("command"):
                                    return str(parsed["command"])
                            except Exception:
                                pass
        return None
    except Exception as e:
        logger.debug("_default_extract_command failed: %s", e, exc_info=True)
        return None


__all__ = ["GraphRunner", "GraphConfig", "DonePayload"]
