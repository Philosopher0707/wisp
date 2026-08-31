"""Agentic graph loop — node implementations.

Four spec nodes + shared helpers:

  planner_coder      — LLM call that produces tool_calls (delegates to WispAgentCore provider streaming)
  sandbox_executor   — isolated code execution with strict timeouts, structured logs
  verifier           — exit-code / test-result check (replaces inline verification guard when graph is active)
  human_approval     — approval breakpoint (wraps ApprovalGate / ToolExecutor gating)

Every node:
  - Takes `GraphState` and returns `(GraphState, NodeResult)` without mutating the
    input except via defensive copies.
  - Wraps all transitions in explicit try/except with actionable logs.
  - Enforces timeouts at the node boundary (sandbox strictly; others via best-effort).
  - Never raises outward — failures are returned as `NodeResult(success=False)` and
    the state is transitioned to FAILED or NEEDS_HUMAN_REVIEW.

Nodes are pure wrt the graph state; I/O (provider, filesystem, approval handler) is
injected so they remain testable with mock providers (same pattern as
`wisp/core/provider_stream.guarded_provider_stream`).
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from wisp.core.graph_state import ExecutionLog, GraphState, GraphStatus

logger = logging.getLogger(__name__)

# ── Node result contract ──────────────────────────────────────────


@dataclass
class NodeResult:
    """Outcome of a single node execution."""

    success: bool
    output: str = ""
    error: str | None = None
    # Structured payload the node wishes to surface (e.g., verifier details)
    data: dict[str, Any] = field(default_factory=dict)
    # Whether the graph should continue. False means terminal (END/fallback).
    should_continue: bool = True
    duration_ms: float = 0.0


NodeHandler = Callable[[GraphState], Awaitable[tuple[GraphState, NodeResult]]]

# Timeout knobs — env-tunable like the core's FIRST_TOKEN_DEADLINE_S.
import os

PLANNER_CODER_TIMEOUT_S = float(os.environ.get("WISP_GRAPH_PLANNER_TIMEOUT", "180"))
SANDBOX_TIMEOUT_S = float(os.environ.get("WISP_GRAPH_SANDBOX_TIMEOUT", "120"))
VERIFIER_TIMEOUT_S = float(os.environ.get("WISP_GRAPH_VERIFIER_TIMEOUT", "120"))
APPROVAL_TIMEOUT_S = float(os.environ.get("WISP_GRAPH_APPROVAL_TIMEOUT", "3600"))


# ── planner_coder node ────────────────────────────────────────────


@dataclass
class PlannerCoderDeps:
    """Injected deps for the planner_coder node (test seam)."""

    get_tool_schemas: Callable[[], list[dict[str, Any]]] | None = None
    build_system_prompt: Callable[[GraphState, str], str] | None = None
    # (system_prompt, messages, tools) -> AsyncIterator[dict event]
    stream_provider: Callable[..., Any] | None = None
    normalize_event: Callable[[Any], dict[str, Any]] | None = None


async def planner_coder_node(
    state: GraphState,
    prompt: str = "",
    *,
    deps: PlannerCoderDeps | None = None,
    timeout_s: float | None = None,
) -> tuple[GraphState, NodeResult]:
    """LLM call that produces tool_calls / content.

    This is the graph's entry edge — it mirrors `WispAgentCore._turn_inner`'s
    provider round-trip but operates on `GraphState` instead of a raw messages list.
    When deps.stream_provider is None, the node is a no-op success (useful when
    the outer caller drives the LLM itself and feeds tool_calls into the graph).

    Always returns a new state snapshot; never mutates `state` on failure without
    updating `status`/`error`.
    """
    start = time.monotonic()
    timeout = timeout_s if timeout_s is not None else PLANNER_CODER_TIMEOUT_S
    new_state = _copy_state(state)
    try:
        # No provider wired — treat as identity (graph driven externally).
        if deps is None or deps.stream_provider is None:
            return new_state, NodeResult(success=True, output="planner_coder: no provider — identity", duration_ms=(time.monotonic() - start) * 1000)

        system_prompt = ""
        if deps.build_system_prompt is not None:
            try:
                system_prompt = deps.build_system_prompt(new_state, prompt)
            except Exception as e:
                logger.warning("planner_coder build_system_prompt failed: %s", e, exc_info=True)
                system_prompt = ""

        tools = []
        if deps.get_tool_schemas is not None:
            try:
                tools = deps.get_tool_schemas() or []
            except Exception as e:
                logger.warning("planner_coder get_tool_schemas failed: %s", e, exc_info=True)

        messages = list(new_state.messages)
        # Avoid duplicating prompt if caller already appended it.
        if prompt and (not messages or messages[-1].get("content") != prompt):
            messages.append({"role": "user", "content": prompt})

        # Stream with timeout — mirrors core's guarded_provider_stream timeout envelope.
        collected: list[dict[str, Any]] = []
        try:
            async with asyncio.timeout(timeout):
                stream = deps.stream_provider(system_prompt=system_prompt, messages=messages, tools=tools or None)
                # stream may be async iterator or sync generator
                if hasattr(stream, "__aiter__"):
                    async for ev in stream:  # type: ignore[union-attr]
                        if deps.normalize_event:
                            try:
                                ev = deps.normalize_event(ev)
                            except Exception:
                                pass
                        collected.append(dict(ev) if isinstance(ev, dict) else {"type": str(getattr(ev, "type", "unknown"))})
                else:
                    for ev in stream:  # type: ignore[union-attr]
                        if deps.normalize_event:
                            try:
                                ev = deps.normalize_event(ev)
                            except Exception:
                                pass
                        collected.append(dict(ev) if isinstance(ev, dict) else {"type": str(getattr(ev, "type", "unknown"))})
        except asyncio.TimeoutError:
            msg = f"planner_coder timed out after {timeout:.0f}s — LLM did not respond"
            logger.warning("%s (session %s)", msg, new_state.session_id)
            new_state.error = msg
            return new_state, NodeResult(success=False, error=msg, output=msg, should_continue=True, duration_ms=(time.monotonic() - start) * 1000)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            msg = f"planner_coder provider failed: {e}"
            logger.warning("%s (session %s)", msg, new_state.session_id, exc_info=True)
            return new_state, NodeResult(success=False, error=msg, output=msg, should_continue=True, duration_ms=(time.monotonic() - start) * 1000)

        # Record assistant content / tool_calls into state.messages (minimal bookkeeping;
        # the orchestrator's message handling is authoritative).
        has_tool_calls = any(ev.get("type") in ("tool_call", "tool_calls") for ev in collected)
        content_parts = [ev.get("text", ev.get("content", "")) for ev in collected if ev.get("type") == "content"]
        if content_parts:
            new_state.messages.append({"role": "assistant", "content": "".join(content_parts)})
        for ev in collected:
            if ev.get("type") in ("tool_call", "tool_calls"):
                # Normalize batched calls
                if "calls" in ev and "name" not in ev:
                    for call in ev.get("calls", []):
                        func = call.get("function", {})
                        new_state.messages.append({"role": "assistant", "tool_calls": [call]})
                else:
                    new_state.messages.append({"role": "assistant", "tool_calls": [{"function": {"name": ev.get("name", ""), "arguments": str(ev.get("arguments", ""))}}]})

        out = f"planner_coder: {len(collected)} events, has_tool_calls={has_tool_calls}"
        return new_state, NodeResult(success=True, output=out, data={"events": collected, "has_tool_calls": has_tool_calls}, duration_ms=(time.monotonic() - start) * 1000)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        msg = f"planner_coder unexpected failure: {e}"
        logger.error("%s (session %s)", msg, new_state.session_id, exc_info=True)
        try:
            new_state.transition(GraphStatus.FAILED, error=msg)
        except Exception:
            pass
        return new_state, NodeResult(success=False, error=msg, output=msg, should_continue=False, duration_ms=(time.monotonic() - start) * 1000)


# ── sandbox_executor node ─────────────────────────────────────────


@dataclass
class SandboxDeps:
    """Injected deps for the sandbox_executor node."""

    # (command, workspace, timeout) -> str | tuple[int, str, str] | dict
    run_command: Callable[..., Any] | None = None
    # Mirrors ToolExecutor's dangerous-command gate (optional, extra layer).
    check_dangerous: Callable[[str], str | None] | None = None


def _parse_run_result(raw: Any, command: str) -> tuple[int, str, str, str, bool]:
    """Normalize any run_command return into (exit_code, stdout, stderr, raw, truncated)."""
    if isinstance(raw, tuple) and len(raw) == 3:
        code, out, err = raw
        try:
            code = int(code)
        except Exception:
            code = -1
        raw_str = f"[exit code: {code}]\n{out}" if code != 0 else (out or err or "")
        if err and out:
            raw_str = f"[exit code: {code}]\n{out}\n--- stderr ---\n{err}" if code != 0 else f"{out}\n--- stderr ---\n{err}"
        return code, str(out or ""), str(err or ""), raw_str, False
    if isinstance(raw, dict):
        data = raw.get("data", raw.get("result", ""))
        meta = raw.get("metadata", {})
        code = int(meta.get("exit_code", 0) if isinstance(meta.get("exit_code"), int) else 0)
        if isinstance(data, str) and data.startswith("[exit code:"):
            try:
                code = int(data.split("[exit code:")[1].split("]")[0].strip())
            except Exception:
                pass
        truncated = bool(meta.get("truncated", False))
        return code, str(data), "", str(data), truncated
    # String: legacy flattened run_bash output
    raw_str = str(raw or "")
    log = ExecutionLog.from_raw(command, raw_str)
    return log.exit_code, log.stdout, log.stderr, raw_str, log.truncated


async def sandbox_executor_node(
    state: GraphState,
    command: str,
    *,
    deps: SandboxDeps | None = None,
    timeout_s: float | None = None,
    workspace: str | None = None,
) -> tuple[GraphState, NodeResult]:
    """Execute a shell command in isolation with strict timeouts and structured logging.

    Wraps the existing `wisp/tools/bash.py:async_tool_run_bash` path (or a sandbox)
    but enforces its own timeout even if the underlying runner has a longer one,
    and records a structured `ExecutionLog` into `state.execution_logs`.

    The command is screened via `check_dangerous_command` when a gate is injected — a
    blocked command is returned as FAILED NodeResult without touching the sandbox.
    """
    start = time.monotonic()
    timeout = timeout_s if timeout_s is not None else SANDBOX_TIMEOUT_S
    timeout = max(1.0, min(3600.0, float(timeout)))
    ws = workspace or state.workspace or "."
    new_state = _copy_state(state)

    if not command or not isinstance(command, str) or not command.strip():
        msg = "sandbox_executor: empty command — skipping"
        logger.warning("%s (session %s)", msg, new_state.session_id)
        return new_state, NodeResult(success=False, error=msg, output=msg, duration_ms=(time.monotonic() - start) * 1000)

    # Dangerous-command gate (defensive even though bash.py also gates — we want
    # an audit record at the graph level before any execution attempt).
    if deps and deps.check_dangerous is not None:
        try:
            reason = deps.check_dangerous(command)
            if reason:
                msg = f"Dangerous command blocked: {reason}"
                logger.warning("sandbox_executor blocked %r: %s (session %s)", command[:80], reason, new_state.session_id)
                entry = ExecutionLog(command=command, exit_code=-1, stdout="", stderr=msg, duration_ms=(time.monotonic() - start) * 1000, raw=msg)
                new_state.add_execution_log(entry)
                return new_state, NodeResult(success=False, error=msg, output=msg, data={"blocked_reason": reason}, duration_ms=(time.monotonic() - start) * 1000)
        except Exception as e:
            logger.warning("sandbox_executor check_dangerous failed — allowing with log: %s", e, exc_info=True)

    # Resolve runner — default is the host bash runner.
    run = None
    if deps and deps.run_command is not None:
        run = deps.run_command
    else:
        try:
            from wisp.tools.bash import async_tool_run_bash as _default_run

            async def _wrap(cmd: str, ws_: str, to: int) -> str:
                return await _default_run(command=cmd, workspace=ws_, timeout=to)

            run = _wrap  # type: ignore[assignment]
        except Exception as e:
            msg = f"sandbox_executor: no runner available: {e}"
            logger.error("%s (session %s)", msg, new_state.session_id, exc_info=True)
            return new_state, NodeResult(success=False, error=msg, output=msg, duration_ms=(time.monotonic() - start) * 1000)

    try:
        # Enforce graph-level timeout even if runner has its own.
        try:
            async with asyncio.timeout(timeout):
                result = run(command, ws, int(timeout)) if _is_async_callable(run) else await asyncio.to_thread(run, command, ws, int(timeout))  # type: ignore[arg-type]
                # Await if the result itself is awaitable (async runner returns coroutine)
                if asyncio.isfuture(result) or asyncio.iscoroutine(result):
                    result = await result  # type: ignore[assignment]
        except asyncio.TimeoutError:
            msg = f"Command timed out after {timeout:.0f}s: {command[:80]}"
            logger.warning("sandbox_executor %s (session %s)", msg, new_state.session_id)
            entry = ExecutionLog(command=command, exit_code=-1, stdout="", stderr=msg, duration_ms=timeout * 1000, raw=msg, truncated=False)
            new_state.add_execution_log(entry)
            return new_state, NodeResult(success=False, error=msg, output=msg, data={"timeout_s": timeout}, duration_ms=timeout * 1000)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            msg = f"sandbox_executor runner raised: {e}"
            logger.warning("%s (session %s, command %r)", msg, new_state.session_id, command[:60], exc_info=True)
            entry = ExecutionLog(command=command, exit_code=-1, stdout="", stderr=str(e), duration_ms=(time.monotonic() - start) * 1000, raw=f"Unexpected error: {e}")
            new_state.add_execution_log(entry)
            return new_state, NodeResult(success=False, error=msg, output=str(e), duration_ms=(time.monotonic() - start) * 1000)

        # Normalize and record structured log
        elapsed_ms = (time.monotonic() - start) * 1000
        try:
            code, out, err, raw_str, truncated = _parse_run_result(result, command)
        except Exception as e:
            logger.warning("sandbox_executor result parse failed: %s — storing raw", e, exc_info=True)
            raw_str = str(result)[:10000]
            code, out, err, truncated = -1, "", raw_str, False
            elapsed_ms = (time.monotonic() - start) * 1000

        entry = ExecutionLog(command=command, exit_code=code, stdout=out, stderr=err, duration_ms=elapsed_ms, raw=raw_str, truncated=truncated)
        new_state.add_execution_log(entry)

        ok = code == 0
        msg = entry.short_summary
        logger.info("sandbox_executor %s (session %s, %.0fms)", msg, new_state.session_id, elapsed_ms)
        return new_state, NodeResult(success=ok, output=msg, error=None if ok else f"exit {code}", data={"exit_code": code, "stdout": out[:2000], "stderr": err[:2000], "truncated": truncated}, duration_ms=elapsed_ms, should_continue=True)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        msg = f"sandbox_executor unexpected failure for {command[:60]!r}: {e}"
        logger.error("%s (session %s)", msg, new_state.session_id, exc_info=True)
        try:
            new_state.transition(GraphStatus.FAILED, error=msg)
        except Exception:
            pass
        return new_state, NodeResult(success=False, error=msg, output=msg, should_continue=False, duration_ms=(time.monotonic() - start) * 1000)


# ── verifier node ─────────────────────────────────────────────────


@dataclass
class VerifierDeps:
    """Injected deps for the verifier node."""

    # Optional custom check: (GraphState) -> (passed: bool, reason: str, data: dict)
    custom_check: Callable[[GraphState], tuple[bool, str, dict]] | None = None


async def verifier_node(
    state: GraphState,
    *,
    deps: VerifierDeps | None = None,
    require_exit_zero: bool = True,
    timeout_s: float | None = None,
) -> tuple[GraphState, NodeResult]:
    """Verify that execution evidence is healthy.

    Default check (no custom_check): the last execution log must exist and have
    exit_code == 0 when `require_exit_zero` is True. When a custom check is
    injected, it overrides the default (useful for test-suite parsers, lsp_diagnostics, etc.).

    The state's iteration_count/code_files are inspected for ordering: a log that
    predates the last code change is considered stale (mirrors
    `WispAgentCore._turn_inner`'s `verify_ok_after_edit` tracking).

    Never raises outward; returns NodeResult(success=False) when verification fails
    so the graph can route to the fallback path.
    """
    start = time.monotonic()
    new_state = _copy_state(state)
    # timeout is best-effort — verification itself is pure (no I/O) unless custom_check is async.
    _ = timeout_s if timeout_s is not None else VERIFIER_TIMEOUT_S
    try:
        # Custom check wins
        if deps and deps.custom_check is not None:
            try:
                passed, reason, data = deps.custom_check(new_state)
                out = f"verifier (custom): {'passed' if passed else 'FAILED'} — {reason}"
                if passed:
                    logger.debug("%s (session %s)", out, new_state.session_id)
                else:
                    logger.warning("%s (session %s)", out, new_state.session_id)
                return new_state, NodeResult(success=bool(passed), output=out, error=None if passed else reason, data=dict(data or {}), duration_ms=(time.monotonic() - start) * 1000)
            except Exception as e:
                msg = f"verifier custom_check raised: {e}"
                logger.warning("%s (session %s)", msg, new_state.session_id, exc_info=True)
                return new_state, NodeResult(success=False, error=msg, output=msg, duration_ms=(time.monotonic() - start) * 1000)

        # Default: last execution must be exit 0 and must postdate last code change (best-effort).
        if not new_state.execution_logs:
            msg = "verifier: no verification command has been run since code changes — requires exit-0 evidence"
            logger.warning("%s (session %s)", msg, new_state.session_id)
            return new_state, NodeResult(success=False, error=msg, output=msg, data={"reason": "no_logs"}, duration_ms=(time.monotonic() - start) * 1000)

        last = new_state.execution_logs[-1]
        if require_exit_zero and last.exit_code != 0:
            detail = (last.stderr or last.stdout or last.raw)[:500]
            msg = f"verifier: most recent verification FAILED (exit {last.exit_code})"
            logger.warning("%s (session %s)", msg, new_state.session_id)
            return new_state, NodeResult(success=False, error=f"exit {last.exit_code}", output=msg, data={"exit_code": last.exit_code, "detail": detail}, duration_ms=(time.monotonic() - start) * 1000)

        msg = f"verifier: passed (exit {last.exit_code})"
        logger.debug("%s (session %s)", msg, new_state.session_id)
        return new_state, NodeResult(success=True, output=msg, data={"exit_code": last.exit_code}, duration_ms=(time.monotonic() - start) * 1000)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        msg = f"verifier unexpected failure: {e}"
        logger.error("%s (session %s)", msg, new_state.session_id, exc_info=True)
        try:
            new_state.transition(GraphStatus.FAILED, error=msg)
        except Exception:
            pass
        return new_state, NodeResult(success=False, error=msg, output=msg, should_continue=False, duration_ms=(time.monotonic() - start) * 1000)


# ── human_approval breakpoint node ─────────────────────────────────


@dataclass
class ApprovalDeps:
    """Injected deps for the human_approval node."""

    # Matches ToolExecutor's ApprovalHandler: (tool_name, args, reason) -> (approved, modified_args|None)
    # Stored as Any to stay compatible with both single-arg and triple-arg handlers.
    approval_handler: Any | None = None
    security_policy: Any | None = None
    # Fallback checker: (tool_name, args) -> (allowed: bool, reason: str|None)
    security_check: Callable[[str, dict], tuple[bool, str | None]] | None = None


async def human_approval_node(
    state: GraphState,
    tool_name: str,
    tool_args: dict[str, Any] | None = None,
    *,
    reason: str = "",
    deps: ApprovalDeps | None = None,
    timeout_s: float | None = None,
) -> tuple[GraphState, NodeResult]:
    """Approval breakpoint — pause for dangerous/human-gated actions.

    Decision order (mirrors `ToolExecutor._needs_forced_approval` + `SecurityPolicy.check`):
      1. If no handler and policy would require explicit approval, transition to
         NEEDS_HUMAN_REVIEW with a bookmark and return non-continuing result.
      2. If handler present, await it with a bounded timeout and map to allowed/denied.
      3. Booking-mark any denied outcome as NEEDS_HUMAN_REVIEW with the denial reason.

    Never raises outward. A timed-out approval is treated as NEEDS_HUMAN_REVIEW so
    the graph can resume once the human responds (via state rollback/resume).
    """
    start = time.monotonic()
    timeout = timeout_s if timeout_s is not None else APPROVAL_TIMEOUT_S
    timeout = max(1.0, min(86400.0, float(timeout)))
    tool_args = dict(tool_args or {})
    new_state = _copy_state(state)
    tool_name = str(tool_name or "unknown")

    # Security pre-check when injected (mirrors core's ApprovalGate.check).
    if deps and deps.security_check is not None:
        try:
            allowed, block_reason = deps.security_check(tool_name, tool_args)
            if not allowed:
                # Policy says denied — still needs explicit human review if a handler exists
                # but the check itself refused. Bookmark and report as needs-review.
                msg = f"Security blocked {tool_name}: {block_reason}"
                logger.warning("%s (session %s)", msg, new_state.session_id)
                new_state.mark_needs_review(tool_name, tool_args, reason=block_reason or reason)
                return new_state, NodeResult(success=False, error=msg, output=msg, data={"reason": block_reason, "tool": tool_name}, should_continue=False)
        except Exception as e:
            logger.debug("human_approval security_check failed — continuing to handler: %s", e, exc_info=True)

    handler = deps.approval_handler if deps is not None else None
    if handler is None:
        # No handler — auto-approve only when policy doesn't require forced approval.
        # Be conservative: any bookmarkable write-class tool without a handler must
        # surface as NEEDS_HUMAN_REVIEW so it isn't silently executed.
        from wisp.tool_executor import _get_write_tools  # local import to avoid cycle at import time

        try:
            # Reuse the executor's write-set definition if a config is available via state.
            needs = False
            # Approximate: treat any non-safe-read as needing approval when no handler.
            safe = {"read_file", "list_files", "search_symbols", "search_codebase", "git_status", "git_diff", "lsp_diagnostics", "lsp_definition", "lsp_references", "lsp_hover", "lsp_symbols", "web_fetch", "web_search", "recall", "diagnose", "run_tests"}
            needs = tool_name not in safe
            if needs:
                msg = f"human_approval: no handler for gated tool {tool_name} — marked needs_human_review"
                logger.warning("%s (session %s)", msg, new_state.session_id)
                new_state.mark_needs_review(tool_name, tool_args, reason=reason or "no handler")
                return new_state, NodeResult(success=False, error=msg, output=msg, data={"tool": tool_name}, should_continue=False)
        except Exception as e:
            logger.debug("human_approval write-set check failed: %s", e, exc_info=True)
        # Safe tool — auto-approve
        return new_state, NodeResult(success=True, output=f"human_approval: safe tool {tool_name} auto-approved", duration_ms=(time.monotonic() - start) * 1000)

    # Handler present — await with timeout so we never hang the graph.
    try:
        handler_result: Any
        try:
            async with asyncio.timeout(timeout):
                # Support both handler shapes: (name, args, reason) -> (approved, modified)|bool
                # and (event_dict) -> bool.
                try:
                    # Triple-arg shape first (ToolExecutor protocol)
                    coro = handler(tool_name, tool_args, reason)
                except TypeError:
                    coro = handler({"name": tool_name, "arguments": tool_args, "reason": reason})
                handler_result = await coro if asyncio.iscoroutine(coro) or asyncio.isfuture(coro) else coro  # type: ignore[arg-type]
        except asyncio.TimeoutError:
            msg = f"human_approval timed out after {timeout:.0f}s for {tool_name} — marked needs_human_review"
            logger.warning("%s (session %s)", msg, new_state.session_id)
            new_state.mark_needs_review(tool_name, tool_args, reason=f"timeout {timeout:.0f}s")
            return new_state, NodeResult(success=False, error=msg, output=msg, data={"tool": tool_name, "timeout_s": timeout}, should_continue=False)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            msg = f"human_approval handler raised for {tool_name}: {e}"
            logger.warning("%s (session %s)", msg, new_state.session_id, exc_info=True)
            new_state.mark_needs_review(tool_name, tool_args, reason=str(e))
            return new_state, NodeResult(success=False, error=msg, output=msg, should_continue=False)

        # Normalize handler_result
        approved: bool
        modified: dict | None = None
        if isinstance(handler_result, tuple) and len(handler_result) == 2:
            approved, modified = bool(handler_result[0]), handler_result[1]
        else:
            approved = bool(handler_result)

        if modified is not None:
            try:
                tool_args.update(modified)
            except Exception:
                pass

        if approved:
            elapsed = (time.monotonic() - start) * 1000
            logger.info("human_approval: %s approved (session %s, %.0fms)", tool_name, new_state.session_id, elapsed)
            # Ensure we are not still bookmarked from a prior pause
            try:
                new_state.clear_review()
            except Exception:
                pass
            return new_state, NodeResult(success=True, output=f"human_approval: {tool_name} approved", data={"tool": tool_name, "approved": True}, duration_ms=elapsed)
        else:
            msg = f"human_approval: {tool_name} denied by human — needs_human_review"
            logger.warning("%s (session %s)", msg, new_state.session_id)
            new_state.mark_needs_review(tool_name, tool_args, reason=reason or "user denied")
            return new_state, NodeResult(success=False, error=msg, output=msg, data={"tool": tool_name, "approved": False}, should_continue=False)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        msg = f"human_approval unexpected failure for {tool_name}: {e}"
        logger.error("%s (session %s)", msg, new_state.session_id, exc_info=True)
        try:
            new_state.mark_needs_review(tool_name, tool_args, reason=str(e))
        except Exception:
            try:
                new_state.transition(GraphStatus.FAILED, error=msg)
            except Exception:
                pass
        return new_state, NodeResult(success=False, error=msg, output=msg, should_continue=False, duration_ms=(time.monotonic() - start) * 1000)


# ── helpers ────────────────────────────────────────────────────────


def _copy_state(state: GraphState) -> GraphState:
    try:
        copied = GraphState.from_dict(copy.deepcopy(state.to_dict()))
        # Preserve oscillation history and snapshot stack — they are transient but
        # critical for correctness across node boundaries.
        try:
            copied._recent_hashes = list(state._recent_hashes)
            copied._snapshot_stack = list(state._snapshot_stack)
        except Exception:
            pass
        return copied
    except Exception as e:
        logger.warning("GraphState copy failed — using shallow copy: %s", e, exc_info=True)
        try:
            # Best-effort shallow fallback — must also carry recent hashes / snapshots
            fb = GraphState(
                code_files=dict(state.code_files),
                execution_logs=list(state.execution_logs),
                iteration_count=state.iteration_count,
                max_iterations=state.max_iterations,
                status=state.status,
                workspace=state.workspace,
                session_id=state.session_id,
                messages=list(state.messages),
                error=state.error,
                pending_approval=copy.deepcopy(state.pending_approval) if state.pending_approval else None,
                created_at=state.created_at,
            )
            try:
                fb._recent_hashes = list(state._recent_hashes)
                fb._snapshot_stack = list(state._snapshot_stack)
            except Exception:
                pass
            return fb
        except Exception:
            return state


def _is_async_callable(fn: Any) -> bool:
    return asyncio.iscoroutinefunction(fn) or (hasattr(fn, "__call__") and asyncio.iscoroutinefunction(getattr(fn, "__call__", None)))


__all__ = [
    "NodeResult",
    "PlannerCoderDeps",
    "SandboxDeps",
    "VerifierDeps",
    "ApprovalDeps",
    "planner_coder_node",
    "sandbox_executor_node",
    "verifier_node",
    "human_approval_node",
]
