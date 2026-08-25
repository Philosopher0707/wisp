"""WispAgentCore — stateless turn engine.

Replaces: the stateful WispAgentCore in wisp/core/agent.py.
All state is injected or passed as parameters.

Design:
  - Receives session dict, prompt, and dependencies
  - Builds system prompt from context (rules.md, skills, repo map, etc.)
  - Streams events from provider
  - Parses tool calls, checks security, executes via extensions
  - Yields flat dict events for backward compatibility
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from wisp.core.events import (
    AgentEvent,
    content as content_event,
    tool_result as tool_result_event,
    error as error_event,
    done as done_event,
    system,
    provider_status as provider_status_event,
    steering_feedback,
)
from wisp.core.approval_gate import ApprovalGate
from wisp.infra.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)

from contextvars import ContextVar

# Absolute monotonic deadline of the innermost running turn, published so
# subagent retries can bound themselves by the parent's remaining clock
# instead of guessing. None outside a turn.
_turn_deadline: ContextVar[float | None] = ContextVar("wisp_turn_deadline", default=None)


def get_turn_deadline() -> float | None:
    """Deadline (time.monotonic) of the running turn, or None."""
    return _turn_deadline.get()


if TYPE_CHECKING:
    from wisp.providers.protocol import Provider
    from wisp.infra.security import SecurityPolicy
    from wisp.infra.extensions import ExtensionHost
    from wisp.tool_executor import ToolExecutor
    from wisp.config import WispConfig
    from wisp.context_assembler import ContextAssembler

logger = logging.getLogger(__name__)

# Module-level caches shared across all core instances (parent + subagents).
# Per-instance caches were useless because subagents create fresh cores,
# discarding the parent's cached system prompt on every spawn.
_ASSEMBLER: ContextAssembler | None = None
_SYSTEM_PROMPT_CACHE: dict[tuple[str, float, str], str] = {}


def _flatten_event(ev: AgentEvent | dict[str, Any]) -> dict[str, Any]:
    """Convert canonical AgentEvent to flat dict for backward compatibility."""
    if isinstance(ev, dict):
        return dict(ev)
    flat = dict(ev.data)
    flat["type"] = str(ev.type)
    flat["timestamp"] = ev.timestamp
    return flat


@dataclass
class WispAgentCore:
    """Stateless turn engine."""

    config: WispConfig | None = None
    provider: Provider | None = None
    security: SecurityPolicy | None = None
    extensions: ExtensionHost | None = None
    tool_executor: ToolExecutor | None = None

    _approval_gate: ApprovalGate | None = field(default=None, repr=False)
    _circuit_breaker: CircuitBreaker | None = field(default=None, repr=False, init=False)

    def __post_init__(self) -> None:
        if self.config is not None:
            cb_config = self.config.get("circuit_breaker") if hasattr(self.config, "get") else None
            if cb_config is None:
                # Check for circuit breaker settings on config object
                failure_threshold = getattr(self.config, "circuit_breaker_failure_threshold", 5)
                success_threshold = getattr(self.config, "circuit_breaker_success_threshold", 2)
                recovery_timeout = getattr(self.config, "circuit_breaker_recovery_timeout", 30.0)
                from wisp.infra.circuit_breaker import CircuitBreakerConfig
                cb_config = CircuitBreakerConfig(
                    failure_threshold=failure_threshold,
                    success_threshold=success_threshold,
                    recovery_timeout=recovery_timeout,
                )
            if cb_config:
                self._circuit_breaker = CircuitBreaker(cb_config)

    async def turn(self, session: dict[str, Any], prompt: str, approval_handler: Any = None, steering_drain: Any = None) -> AsyncIterator[dict[str, Any]]:
        """Run one turn, yielding events.

        Loops internally: provider → tool_calls → execute → append → provider
        until the model returns content (no tool calls) or max iterations.

        *steering_drain*, when given, is called at each tool boundary; its
        strings are mid-course corrections appended as user messages so the
        next provider round-trip adapts (M3 of docs/repl-design.md).

        Has a wall-clock timeout (config turn_timeout, default 30 min) to
        prevent infinite hangs.
        """
        import asyncio as _asyncio
        turn_timeout = getattr(self.config, "turn_timeout", 1800) if self.config else 1800
        # Publish the absolute deadline so nested consumers (subagent
        # orchestrator retries) can budget themselves against the same clock.
        # Overwritten by every turn; only read while a turn is live.
        _turn_deadline.set(time.monotonic() + turn_timeout)
        # Build messages list
        messages = list(session.get("messages", []))
        # Avoid duplicating the user message if runtime already added it
        if (
            not messages
            or messages[-1].get("role") != "user"
            or messages[-1].get("content") != prompt
        ):
            messages.append({"role": "user", "content": prompt})

        # Build system prompt with full context awareness
        system_prompt = self._build_system_prompt(session, query=prompt)

        # Get tools — built-in + extensions. Role-restricted subagents only
        # get their allowed subset (contract.tools), not the full toolset.
        tools = self._get_tool_schemas()
        allowed = session.get("allowed_tools")
        if isinstance(allowed, (list, tuple, set)) and "all" not in {str(a).lower() for a in allowed}:
            allowed_set = {str(a) for a in allowed}

            def _schema_name(t: Any) -> str:
                if isinstance(t, dict):
                    fn = t.get("function")
                    if isinstance(fn, dict) and fn.get("name"):
                        return str(fn["name"])
                return str(t.get("name", "")) if isinstance(t, dict) else ""

            tools = [t for t in tools if _schema_name(t) in allowed_set]

        max_iterations = getattr(self.config, "max_iterations", 30)

        try:
            async with _asyncio.timeout(turn_timeout):
                async for event in self._turn_inner(
                    session, prompt, messages, system_prompt, tools,
                    max_iterations, approval_handler, steering_drain=steering_drain,
                ):
                    yield event
        except _asyncio.TimeoutError:
            yield _flatten_event(error_event(
                f"Turn timed out after {turn_timeout}s", recoverable=False,
            ))
            yield _flatten_event(done_event(session.get("id", "")))

    async def _turn_inner(
        self, session: dict[str, Any], prompt: str, messages: list[dict[str, Any]], system_prompt: str, tools: list[dict[str, Any]] | None,
        max_iterations: int, approval_handler: Any, steering_drain: Any = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Inner turn loop, separated for timeout wrapping."""
        streamed_any_content = False
        # Role-restricted agents (subagents): reject disallowed tools even if
        # the model hallucinates them past the filtered schema list.
        allowed_tools = session.get("allowed_tools")
        _allowed_set: set[str] | None = None
        if isinstance(allowed_tools, (list, tuple, set)) and "all" not in {
            str(a).lower() for a in allowed_tools
        }:
            _allowed_set = {str(a) for a in allowed_tools}
        for iteration in range(max_iterations):
            pending_tool_calls: list[dict[str, Any]] = []
            provider_events: list[dict[str, Any]] = []
            partial_content: list[str] = []
            has_tool_calls = False

            try:
                async for event in self._guarded_provider_stream(
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=tools if tools else None,
                ):
                    # Normalize event
                    normalized = self._normalize_event(event)
                    provider_events.append(normalized)

                    # Accumulate partial content for error recovery
                    if normalized.get("type") == "content":
                        partial_content.append(normalized.get("text", ""))
                        streamed_any_content = True

                    # Security + extension checks for tool calls
                    if normalized.get("type") in ("tool_call", "tool_calls"):
                        has_tool_calls = True
                        # Normalize type to singular for downstream consistency
                        normalized["type"] = "tool_call"
                        # Extract calls from ToolCallBatch if present
                        if "calls" in normalized and "name" not in normalized:
                            calls = normalized.pop("calls", [])
                            if calls:
                                # Yield individual tool_call events for each call
                                for call in calls:
                                    func = call.get("function", {})
                                    single = {
                                        "type": "tool_call",
                                        "name": func.get("name", ""),
                                        "arguments": func.get("arguments", {}),
                                    }
                                    if "id" in call:
                                        single["id"] = call["id"]
                                    if "index" in func:
                                        single["_index"] = func["index"]
                                    # Process each individually
                                    tc_event = dict(single)
                                    # Role restriction: reject before any gating
                                    if _allowed_set is not None and str(single.get("name", "")) not in _allowed_set:
                                        yield _flatten_event(
                                            error_event(
                                                f"Blocked: tool '{single.get('name', '')}' is not allowed for this agent's role",
                                                recoverable=True,
                                            )
                                        )
                                        continue
                                    # Check security BEFORE yielding
                                    gate = self._get_approval_gate()
                                    allowed, reason = await gate.check(
                                        tc_event, session, approval_handler=approval_handler
                                    )
                                    if not allowed:
                                        yield _flatten_event(
                                            error_event(
                                                f"Blocked: {reason}",
                                                recoverable=True,
                                            )
                                        )
                                        continue

                                    # Check extensions
                                    if self.extensions is not None:
                                        try:
                                            ext_result = self.extensions.intercept(tc_event)
                                            if ext_result.get("action") == "block":
                                                yield _flatten_event(
                                                    error_event(
                                                        f"Blocked: {ext_result.get('reason', 'by extension')}",
                                                        recoverable=True,
                                                    )
                                                )
                                                continue
                                        except Exception as e:
                                            logger.exception(
                                                "Extension intercept failed — treating as deny: %s",
                                                e,
                                            )
                                            yield _flatten_event(
                                                error_event(
                                                    f"Extension intercept failed: {e}. Tool call denied.",
                                                    recoverable=True,
                                                )
                                            )
                                            continue

                                    pending_tool_calls.append(tc_event)
                                    yield _flatten_event(tc_event)
                                continue  # Skip the default yield below since we already yielded
                            continue

                        # Role restriction: reject before any gating
                        if _allowed_set is not None and str(normalized.get("name", "")) not in _allowed_set:
                            yield _flatten_event(
                                error_event(
                                    f"Blocked: tool '{normalized.get('name', '')}' is not allowed for this agent's role",
                                    recoverable=True,
                                )
                            )
                            continue

                        # Check security BEFORE yielding
                        gate = self._get_approval_gate()
                        allowed, reason = await gate.check(normalized, session, approval_handler=approval_handler)
                        if not allowed:
                            yield _flatten_event(
                                error_event(
                                    f"Blocked: {reason}",
                                    recoverable=True,
                                )
                            )
                            continue

                        # Check extensions
                        if self.extensions is not None:
                            try:
                                ext_result = self.extensions.intercept(normalized)
                                if ext_result.get("action") == "block":
                                    yield _flatten_event(
                                        error_event(
                                            f"Blocked: {ext_result.get('reason', 'by extension')}",
                                            recoverable=True,
                                        )
                                    )
                                    continue
                            except Exception as e:
                                logger.exception(
                                    "Extension intercept failed — treating as deny: %s", e
                                )
                                yield _flatten_event(
                                    error_event(
                                        f"Extension intercept failed: {e}. Tool call denied.",
                                        recoverable=True,
                                    )
                                )
                                continue

                        pending_tool_calls.append(normalized)

                    # Yield the event (skip complete events)
                    if normalized.get("type") in ("complete", "done"):
                        continue
                    yield normalized

            except Exception as exc:
                # Retry transient errors (connection, timeout, 5xx) up to 2 times.
                # "timed out" matches requests' ReadTimeout wording; "timeout"
                # alone misses it.
                exc_str = str(exc).lower()
                is_transient = any(
                    s in exc_str for s in ("connection", "timeout", "timed out", "reset", "502", "503", "504", "refused", "broken pipe")
                )
                if is_transient and iteration < 2:
                    import asyncio as _aio
                    backoff = 2 ** iteration  # 1s, 2s
                    logger.warning("Transient provider error (attempt %d), retrying in %ds: %s",
                                   iteration + 1, backoff, exc)
                    yield _flatten_event(system(
                        f"Connection issue, retrying in {backoff}s...",
                        level="warning",
                    ))
                    await _aio.sleep(backoff)
                    continue  # Retry this iteration

                logger.exception("Provider stream failed")
                if partial_content and not streamed_any_content:
                    # Only emit accumulated content when NOTHING was streamed
                    # live — otherwise transports render the text twice and the
                    # duplicate lands in session history permanently.
                    yield _flatten_event(content_event("".join(partial_content)))
                yield _flatten_event(
                    error_event(
                        f"Stream error: {exc}",
                        recoverable=True,
                    )
                )
                return

            # ── Check for truncation ──
            truncated = any(e.get("done_reason") == "length" for e in provider_events)
            if truncated:
                yield _flatten_event(system(
                    "Response truncated: max_tokens limit reached. "
                    "Say 'continue' to resume, or increase max_tokens in config.",
                    level="warning",
                ))

            # ── If no tool calls, the model produced final content ──
            if not has_tool_calls:
                yield _flatten_event(done_event(session.get("id", "")))
                return

            # ── Execute tools and feed results back to messages ──
            tool_results_events: list[dict[str, Any]] = []
            has_tool_results = any(e.get("type") == "tool_result" for e in provider_events)
            if pending_tool_calls and not has_tool_results:
                for tc in pending_tool_calls:
                    async for result_event in self._execute_tool(
                        tc, session, approval_handler=approval_handler
                    ):
                        tool_results_events.append(result_event)
                        yield result_event

            # Append assistant + tool messages to continue the conversation
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": "".join(partial_content)}
            if pending_tool_calls:
                import json
                import uuid as _uuid
                tc_blocks = []
                for tc in pending_tool_calls:
                    args = tc.get("arguments", {})
                    func_block = {
                        "name": tc.get("name", ""),
                        "arguments": json.dumps(args) if isinstance(args, dict) else str(args),
                    }
                    if "_index" in tc:
                        func_block["index"] = tc["_index"]
                    tc_blocks.append({
                        "id": tc.get("id", f"call_{_uuid.uuid4().hex[:8]}"),
                        "type": "function",
                        "function": func_block,
                    })
                assistant_msg["tool_calls"] = tc_blocks
            messages.append(assistant_msg)

            for tr in tool_results_events:
                content = tr.get("result", tr.get("data", ""))
                if isinstance(content, dict):
                    content = json.dumps(content)
                tc_id = tr.get("tool_call_id", "")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": str(content),
                })

            # Tool boundary: surface any steering the user typed mid-turn
            # so the next provider round-trip can change course.
            if steering_drain is not None:
                try:
                    injected = list(steering_drain())
                except Exception:
                    injected = []
                for note in injected:
                    note = str(note).strip()
                    if not note:
                        continue
                    messages.append({
                        "role": "user",
                        "content": f"[steering] {note}",
                    })
                    yield _flatten_event(steering_feedback(note))

        # Max iterations reached — don't discard the turn's work. One final
        # tool-less call asks the model to summarize what it gathered; the
        # raw error only surfaces if even that fails. (Live evidence: 50
        # tools / 7 minutes of research died behind this error with zero
        # answer delivered.)
        messages.append({
            "role": "user",
            "content": (
                "[SYSTEM] Iteration budget exhausted. Do NOT call any more "
                "tools. Summarize your findings and answer the user's request "
                "with what you have, noting anything left incomplete."
            ),
        })
        wrapped_up = False
        try:
            async for ev in self._stream_events_async(system_prompt, messages, None):
                etype = ev.get("type", "")
                if etype in ("content", "text", "token"):
                    text = ev.get("text") or ev.get("content") or ""
                    if text:
                        yield _flatten_event(content_event(str(text)))
                elif etype == "done":
                    wrapped_up = True
                    break
        except Exception:
            logger.exception("Iteration wrap-up call failed")
        if not wrapped_up:
            yield _flatten_event(error_event("Max iterations reached", recoverable=False))
        yield _flatten_event(done_event(session.get("id", "")))

    async def _stream_events_async(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Wrap a synchronous provider generator in an async iterator.

        Runs the blocking I/O in a thread to avoid blocking the event loop.
        This allows concurrent requests in FastAPI and responsive REPL.
        Uses circuit breaker to fail fast when provider is unhealthy.
        """
        import asyncio

        if self.provider is None:
            raise RuntimeError("WispAgentCore has no provider configured")
        provider = self.provider

        # Define the streaming callable for circuit breaker
        async def _call_provider() -> AsyncIterator[dict[str, Any]]:
            if hasattr(provider, "generate_stream_events_async"):
                async for event in provider.generate_stream_events_async(
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=tools,
                ):
                    yield event
                return

            # Fallback: run sync generator in a thread via queue.
            import threading

            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            done = object()  # sentinel
            producer_error: list[BaseException] = []
            cancelled = threading.Event()

            def _sync_producer() -> None:
                try:
                    for event in provider.generate_stream_events(
                        system_prompt=system_prompt,
                        messages=messages,
                        tools=tools,
                    ):
                        if cancelled.is_set():
                            break
                        loop.call_soon_threadsafe(queue.put_nowait, event)
                    loop.call_soon_threadsafe(queue.put_nowait, done)  # type: ignore[arg-type]
                except Exception as exc:
                    # Deliver the failure to the consumer instead of letting it
                    # die in the thread excepthook as a clean-looking end.
                    producer_error.append(exc)
                    with contextlib.suppress(RuntimeError):
                        loop.call_soon_threadsafe(queue.put_nowait, done)  # type: ignore[arg-type]

            thread = threading.Thread(target=_sync_producer, daemon=True)
            thread.start()

            try:
                while True:
                    event = await queue.get()
                    if event is done:
                        if producer_error:
                            raise producer_error[0]
                        break
                    yield event
            finally:
                cancelled.set()
                while not queue.empty():
                    try:
                        queue.get_nowait()
                    except Exception:
                        break
                thread.join(timeout=5.0)

        # Use circuit breaker if configured
        if self._circuit_breaker is not None:
            breaker = self._circuit_breaker

            async def _emit_circuit_open() -> AsyncIterator[dict[str, Any]]:
                retry = breaker.retry_after()
                yield _flatten_event(provider_status_event(
                    "circuit_open",
                    detail="Provider failing repeatedly — pausing requests.",
                    retry_after=retry,
                ))
                yield _flatten_event(error_event(
                    f"Provider temporarily unavailable (circuit open, "
                    f"retry in {retry:.0f}s).",
                    recoverable=True,
                ))

            # Fail honestly instead of letting CircuitOpenError surface as a
            # generic stream error — transports render the status event.
            if breaker.state is CircuitState.OPEN:
                async for event in _emit_circuit_open():
                    yield event
                return

            try:
                async for event in self._circuit_breaker.stream(_call_provider):
                    yield event
            except CircuitOpenError:
                # Raced into OPEN between the state check and the call.
                async for event in _emit_circuit_open():
                    yield event
                return
            except Exception:
                transition = breaker.consume_transition()
                if transition is not None and transition.to_state is CircuitState.OPEN:
                    async for event in _emit_circuit_open():
                        yield event
                raise
            else:
                transition = breaker.consume_transition()
                if transition is not None and transition.to_state is CircuitState.CLOSED:
                    yield _flatten_event(provider_status_event(
                        "circuit_closed",
                        detail="Provider recovered — resuming normal operation.",
                    ))
        else:
            async for event in _call_provider():
                yield event

    def _build_system_prompt(self, session: dict[str, Any], query: str | None = None) -> str:
        """Build rich system prompt from session context."""
        from wisp.context_assembler import ContextAssembler, PromptContext

        ws = session.get("workspace", ".")
        ws_path = Path(ws).resolve()

        # Lazy-init assembler (module-level, shared across all core instances)
        global _ASSEMBLER
        if _ASSEMBLER is None:
            _ASSEMBLER = ContextAssembler()  # type: ignore[no-untyped-call]  # context_assembler not yet annotated
        assembler = _ASSEMBLER

        # Check cache for static prompt — include mtimes of key context files
        # so that edits to rules.md, skills, etc. invalidate the cache.
        context_mt = 0.0
        for candidate in (
            ws_path / ".wisp" / "rules.md",
            ws_path / ".wisp" / "conventions.md",
        ):
            try:
                context_mt = max(context_mt, candidate.stat().st_mtime)
            except OSError:
                pass
        # Memory changes must invalidate too: a fact remembered in this
        # process (or a summary upserted by the last turn) otherwise stays
        # invisible until restart, because nothing else bumps context_mt.
        try:
            from wisp.memory import _get_memory_file
            context_mt = max(context_mt, _get_memory_file().stat().st_mtime)
        except OSError:
            pass
        try:
            from wisp.agent_memory import SESSIONS_FILE
            context_mt = max(context_mt, SESSIONS_FILE.stat().st_mtime)
        except OSError:
            pass

        # Subagent prompts ride in role_extra; they MUST be part of the cache
        # key or a subagent's task prompt poisons the parent/siblings (all
        # cores sharing one workspace share this dict).
        subagent_prompt = str(session.get("subagent_system_prompt", "") or "")
        prompt_variant = hashlib.sha256(subagent_prompt.encode("utf-8")).hexdigest()[:16] if subagent_prompt else ""
        cache_key = (ws, context_mt, prompt_variant)
        static_prompt = _SYSTEM_PROMPT_CACHE.get(cache_key)

        if static_prompt is None:
            # For subagents, skip heavy context building (repo map, lint, module
            # summary) — the orchestrator's system prompt already includes
            # relevant context. This saves 5-10s of I/O per subagent spawn.
            is_subagent = bool(session.get("subagent_system_prompt"))

            skills_block = self._build_skills_block(ws) if not is_subagent else ""
            project_ctx = self._detect_project_context(ws)
            memory_block = self._build_memory_block(ws) if not is_subagent else ""
            git_ctx = self._build_git_context(ws) if not is_subagent else ""
            repo_map = self._build_repo_map(ws) if not is_subagent else ""
            lint_ctx = self._build_lint_context(ws) if not is_subagent else ""
            module_summary = self._build_module_summary(ws) if not is_subagent else ""

            # Load rules.md if present
            rules_path = ws_path / ".wisp" / "rules.md"
            role_extra = ""
            if rules_path.exists():
                try:
                    role_extra = rules_path.read_text(encoding="utf-8")
                except Exception:
                    pass

            # If the session has a subagent system prompt (set by SubagentRunner),
            # use it as role_extra so it's included in the assembled prompt
            # instead of creating a duplicate system message.
            subagent_prompt = session.get("subagent_system_prompt", "")
            if subagent_prompt:
                role_extra = (role_extra + "\n\n" + subagent_prompt).strip() if role_extra else subagent_prompt

            ctx = PromptContext.from_legacy(
                workspace=ws,
                default_system=assembler.default_system,
                role_extra=role_extra or None,
                skills_block=skills_block or None,
                project_context=project_ctx or None,
                memory_block=memory_block or None,
                git_context=git_ctx or None,
                repo_map=repo_map or None,
            )
            static_prompt = assembler.build(ctx)

            tools_block = self._build_tools_block()
            if tools_block:
                static_prompt += "\n\n" + tools_block

            if lint_ctx:
                static_prompt += "\n\n" + lint_ctx

            if module_summary:
                static_prompt += "\n\n" + module_summary

            _SYSTEM_PROMPT_CACHE[cache_key] = static_prompt

        # Add query-specific context
        if query:
            relevant = self._get_relevant_files(ws, query)
            if relevant:
                static_prompt += f"\n\n## Files Relevant to Query\n{relevant}\n"

        # Add compaction notice
        if session.get("compaction_history"):
            count = len(session["compaction_history"])
            static_prompt += f"\n[Session compacted {count} times.]\n"

        # Operating context is per-turn (mode changes, agent counts,
        # fresh notifications) — deliberately OUTSIDE the static cache.
        operating = self._build_operating_context(session, query=query)
        if operating:
            static_prompt += "\n\n" + operating

        return static_prompt

    def _build_operating_context(self, session: dict[str, Any], query: str | None = None) -> str:
        """Declare this agent's own operating posture for the current turn.

        Mirrors what a hosted agent harness announces: sandbox/approval
        policy, identity, workspace, and live background-agent state.
        Returns empty string when there is nothing non-default to say
        (e.g. stripped-down test configs) so prompts stay lean.
        """
        lines: list[str] = []

        cfg = self.config
        if cfg is not None:
            model = getattr(cfg, "model", "") or "unknown"
            provider = getattr(cfg, "provider", "") or ""
            identity = f"- model: {model}"
            if provider:
                identity += f" (provider: {provider})"
            lines.append(identity)

            mode = getattr(cfg, "permission_mode", "")
            mode_name = getattr(mode, "value", None) or str(mode)
            if mode_name and mode_name != "full":
                auto = bool(getattr(cfg, "auto_approve", False))
                approval = "auto-approved" if auto else "user approves write actions"
                lines.append(f"- permission mode: {mode_name} ({approval})")

            depth = int(getattr(cfg, "_subagent_depth", 0) or 0)
            if depth > 0:
                lines.append(f"- you are a subagent (nesting depth {depth}); "
                             f"do not spawn children unless explicitly asked")

        ws = session.get("workspace", "")
        if ws:
            lines.append(f"- workspace: {ws}")
        sid = session.get("id", "")
        if sid:
            lines.append(f"- session: {sid}")

        # Live background-agent state + settled-work notifications.
        manager = getattr(self.tool_executor, "background_agents", None)
        if manager is not None:
            counts = manager.counts()
            active = counts.get("running", 0)
            finished = sum(counts.get(k, 0) for k in ("completed", "failed", "cancelled"))
            if active or finished:
                lines.append(f"- background agents: {active} running, {finished} finished "
                             f"(inspect with subagent_list)")
            notifications = manager.drain_notifications()
            if notifications:
                lines.append("- background agents finished since your last turn:")
                lines.extend(f"  {n}" for n in notifications)

        # Plugin / MCP surface: how many tools beyond built-ins are live.
        if self.extensions is not None:
            try:
                from wisp.tools.registry import TOOL_SCHEMAS as _BUILTIN_SCHEMAS
                ext_n = len(self._get_tool_schemas()) - len(_BUILTIN_SCHEMAS)
                if ext_n > 0:
                    lines.append(f"- external tools: {ext_n} provided by plugins/MCP servers")
            except Exception:
                pass  # inventory is advisory — never break prompt building

        if not lines:
            return ""
        return "## Operating context\n" + "\n".join(lines)

    def invalidate_caches(self) -> None:
        """Invalidate all caches — call when workspace context changes."""
        _SYSTEM_PROMPT_CACHE.clear()
        logger.debug("Engine caches invalidated")

    def _build_skills_block(self, workspace: str) -> str:
        """Discover and format skills for the system prompt."""
        try:
            from wisp.skills import discover_skills

            skills = discover_skills(workspace)
            if not skills:
                return ""
            lines = ["## Skills"]
            for skill in skills:
                lines.append(f"- {skill.name}: {skill.description}")
                if skill.instructions:
                    lines.append(f"  Instructions: {skill.instructions[:200]}")
            return "\n".join(lines)
        except Exception as e:
            logger.debug("Failed to build skills block: %s", e)
            return ""

    def _detect_project_context(self, workspace: str) -> str:
        """Detect project type and format context."""
        try:
            from wisp.project_context import detect_project_context, format_context

            ctx = detect_project_context(workspace)
            return format_context(ctx)
        except Exception as e:
            logger.debug("Failed to detect project context: %s", e)
            return ""

    def _build_memory_block(self, workspace: str) -> str:
        """Cross-session memory: remembered facts + recent summaries."""
        try:
            from wisp.agent_memory import get_agent_memory
            from wisp.memory import list_all_facts

            facts = list_all_facts()
            mem = get_agent_memory()
            try:
                all_summaries = mem.load_all()
            except Exception:
                all_summaries = []
            # Same-workspace summaries are most relevant; fill remaining
            # slots with the newest others (recall searches globally too).
            same_ws = [x for x in all_summaries if x.workspace == workspace]
            others = [x for x in all_summaries if x.workspace != workspace]
            summaries = (same_ws + others)[:3]
            return format_cross_session_block(facts, summaries)
        except Exception as e:
            logger.debug("Failed to build memory block: %s", e)
            return ""

    def _build_git_context(self, workspace: str) -> str:
        """Build git context string."""
        try:
            from wisp.git_context import format_git_context

            return format_git_context(workspace)
        except Exception as e:
            logger.debug("Failed to build git context: %s", e)
            return ""

    def _build_repo_map(self, workspace: str) -> str:
        """Build repo map for the workspace."""
        try:
            from wisp.repo_map import RepoMap

            ws_path = Path(workspace).resolve()
            rm = RepoMap(ws_path)
            entries = rm.build(use_cache=True, fast_mode=True)
            if entries:
                # Configurable max tokens for repo map (default 1200)
                max_tokens = 1200
                if self.config is not None:
                    max_tokens = getattr(self.config, "repo_map_max_tokens", 1200)
                map_text = rm.format_for_llm(max_tokens=max_tokens)
                return f"## Codebase Map\n{map_text}\n"
        except ImportError:
            pass
        except Exception as e:
            logger.debug("Failed to build repo map: %s", e)
        return ""

    def _build_lint_context(self, workspace: str) -> str:
        """Build a concise lint/check config summary for the workspace.

        Tells the model what verification tools are available so it can
        write code that passes checks on the first attempt — no need to
        call lsp_diagnostics just to discover what's configured.
        """
        ws_path = Path(workspace).resolve()
        lines: list[str] = []
        detected: set[str] = set()

        # Detect file extensions in the project
        ext_to_linter = {
            ".py": "py_compile / ruff / mypy",
            ".ts": "tsc --noEmit",
            ".tsx": "tsc --noEmit",
            ".js": "eslint",
            ".jsx": "eslint",
            ".rs": "cargo check / cargo clippy",
            ".go": "go vet",
        }
        fast_ext_to_check = {
            ".py": "ruff",
            ".ts": "tsc",
            ".tsx": "tsc",
            ".js": "eslint",
            ".jsx": "eslint",
            ".rs": "cargo",
            ".go": "go",
        }

        try:
            for root, dirs, _files in os.walk(ws_path):
                # Skip hidden and venv directories
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in (
                    "node_modules", "venv", ".venv", "__pycache__", "target", "dist", "build",
                )]
                for f in _files:
                    ext = Path(f).suffix.lower()
                    if ext in ext_to_linter and ext not in detected:
                        detected.add(ext)
                if len(detected) >= len(ext_to_linter):
                    break
                # Limit depth for performance
                if root.count(os.sep) - ws_path.as_posix().count(os.sep) > 3:
                    dirs[:] = []
        except Exception:
            pass

        if not detected:
            return ""

        lines.append("## Available Code Checks")
        lines.append("After writing or editing a file, these checks are auto-run. Write code that passes them:")
        for ext in sorted(detected):
            linter = ext_to_linter.get(ext, "unknown")
            lines.append(f"- **{ext}** files: `{linter}`")

        # Check which linter binaries are actually available
        available = []
        import shutil
        for ext in sorted(detected):
            binary = fast_ext_to_check.get(ext)
            if binary and shutil.which(binary):
                available.append(f"`{binary}`")
        if available:
            lines.append(f"\nInstalled: {', '.join(available)}")

        return "\n".join(lines)

    def _build_module_summary(self, workspace: str) -> str:
        """Build a concise module structure overview for the workspace.

        Scans top-level directories, identifies packages and key modules,
        so the model has a mental map before the first turn — no need to
        run list_files just to understand the project layout.
        """
        ws_path = Path(workspace).resolve()
        lines: list[str] = []
        packages: list[tuple[str, str]] = []  # (name, description)

        # Known project type markers
        markers = {
            "pyproject.toml": "Python project",
            "setup.py": "Python project",
            "setup.cfg": "Python project",
            "package.json": "Node/TS project",
            "Cargo.toml": "Rust project",
            "go.mod": "Go project",
            "Makefile": "C/C++ project",
            "CMakeLists.txt": "C/C++ project",
        }
        project_types: list[str] = []
        for marker, label in markers.items():
            if (ws_path / marker).exists():
                project_types.append(label)

        try:
            entries = sorted(os.listdir(ws_path))
        except OSError:
            return ""

        # Identify top-level source directories
        src_dirs: list[str] = []
        for entry in entries:
            entry_path = ws_path / entry
            if entry.startswith("."):
                continue
            if entry in ("node_modules", "venv", ".venv", "__pycache__", "target",
                         "dist", "build", ".git", ".wisp", "tests", "test"):
                continue
            if entry_path.is_dir():
                # Check if it's a package (has __init__.py) or has source files
                pkg_init = entry_path / "__init__.py"
                has_src = (
                    any(entry_path.glob("*.py")) or any(entry_path.glob("*.ts"))
                    or any(entry_path.glob("*.rs")) or any(entry_path.glob("*.go"))
                )
                if pkg_init.exists():
                    # Python package — peek at docstring
                    desc = ""
                    try:
                        first_line = pkg_init.read_text(encoding="utf-8").strip().split("\n")[0]
                        if first_line.startswith('"""') or first_line.startswith("'''"):
                            desc = first_line.strip('"\'').strip()
                    except Exception:
                        pass
                    packages.append((entry, desc))
                    src_dirs.append(entry)
                elif has_src:
                    src_dirs.append(entry)

        config_files = [e for e in entries if not (ws_path / e).is_dir()
                        and e in markers and not markers[e].startswith("Python")]

        if not packages and not src_dirs and not project_types and not config_files:
            return ""

        lines.append("## Project Structure")
        if project_types:
            lines.append("Type: " + ", ".join(project_types))

        if packages:
            lines.append("\nKey packages:")
            for name, desc in packages:
                if desc:
                    lines.append(f"- **{name}/** — {desc}")
                else:
                    lines.append(f"- **{name}/**")

        # List other notable top-level items
        other_dirs = [e for e in src_dirs if e not in [p[0] for p in packages]]
        if other_dirs:
            lines.append("\nOther source directories: " + ", ".join(f"`{d}/`" for d in other_dirs))

        if config_files:
            lines.append("Config: " + ", ".join(f"`{f}`" for f in config_files))

        return "\n".join(lines)

    def _get_relevant_files(self, workspace: str, query: str) -> str:
        """Get files relevant to the query from repo map."""
        try:
            from wisp.repo_map import RepoMap

            ws_path = Path(workspace).resolve()
            rm = RepoMap(ws_path)
            rm.build(use_cache=True, fast_mode=True)
            relevant = rm.get_relevant_files(query, top_k=5)
            if relevant:
                return "\n".join(f"- {f}" for f in relevant)
        except Exception as e:
            logger.debug("Failed to get relevant files: %s", e)
        return ""

    def _build_tools_block(self) -> str:
        """Generate the prompt's tool menu from live registries.

        Single source of truth: TOOL_SCHEMAS plus whatever extensions
        (MCP servers, plugins) expose at runtime. A newly registered tool
        is announced automatically; renaming one cannot leave a phantom
        name behind (the previous hardcoded dict drifted exactly this
        way — it advertised 'spawn_subagent', which never existed).
        """
        entries: list[tuple[str, str]] = []
        seen: set[str] = set()

        def _describe(fn: dict[str, Any]) -> tuple[str, str]:
            name = str(fn.get("name", "") or "")
            desc = str(fn.get("description", "") or "").strip()
            first = desc.split(". ")[0].rstrip(".").strip()
            if len(first) > 140:
                first = first[:137] + "..."
            return name, first

        from wisp.tools.registry import TOOL_SCHEMAS

        for schema in TOOL_SCHEMAS:
            fn = schema.get("function", {}) if isinstance(schema, dict) else {}
            name, first = _describe(fn)
            if name and name not in seen:
                seen.add(name)
                entries.append((name, first))

        ext_count = 0
        if self.extensions is not None:
            try:
                for schema in self.extensions.tools() or []:
                    fn = schema.get("function", {}) if isinstance(schema, dict) else {}
                    name, first = _describe(fn)
                    if name and name not in seen:
                        seen.add(name)
                        entries.append((name, first))
                        ext_count += 1
            except Exception as e:
                logger.warning("Failed to list extension tools: %s", e)

        lines = ["## Tools available"]
        lines.extend(f"- {n}: {d}" for n, d in entries)
        if ext_count:
            lines.append(
                f"({ext_count} additional tool(s) provided by plugins/MCP servers)"
            )
        return "\n".join(lines)

    def _get_tool_schemas(self) -> list[dict[str, Any]]:
        """Get all tool schemas — built-in + extensions."""
        from wisp.tools.registry import TOOL_SCHEMAS

        schemas = list(TOOL_SCHEMAS)

        if self.extensions is not None:
            try:
                ext_tools = self.extensions.tools()
                if ext_tools:
                    schemas.extend(ext_tools)
            except Exception as e:
                logger.warning("Failed to get extension tools: %s", e)

        return schemas

    async def _execute_tool(self, event: dict[str, Any], session: dict[str, Any], approval_handler: Any = None) -> AsyncIterator[dict[str, Any]]:
        """Execute a tool call via ToolExecutor, yielding flattened events.

        Schema validation is done here as defense-in-depth.
        ToolExecutor handles permission checks, hooks, and dispatch.
        """
        name = event.get("name", "")
        args = event.get("arguments", {})
        workspace = session.get("workspace", ".")

        # ── Schema validation (defense-in-depth) ─────────────────
        schema_error = self._validate_tool_args(name, args)
        if schema_error:
            yield _flatten_event(
                tool_result_event(
                    name,
                    self._normalize_tool_result(
                        {"status": "error", "data": schema_error}
                    ),
                    duration_ms=0,
                    tool_call_id=event.get("id"),
                )
            )
            return

        if self.tool_executor is not None:
            # Wrap simple handler (event_dict -> bool) to ToolExecutor's protocol
            # (name, args, reason) -> (approved, modified_args_or_none)
            wrapped_handler = None
            if approval_handler is not None:
                async def _wrap_approval(name: str, args: dict[str, Any], reason: str) -> tuple[bool, None]:
                    approved = await approval_handler({"name": name, "arguments": args})
                    return approved, None
                wrapped_handler = _wrap_approval

            async for agent_event in self.tool_executor.execute(
                name, args, workspace,
                tool_call_id=event.get("id"),
                approval_handler=wrapped_handler,
            ):
                yield _flatten_event(agent_event)
        else:
            # Fallback: direct execution when no ToolExecutor wired
            from wisp.tools.registry import TOOL_IMPLS as _BUILTINS, execute_tool
            start = time.time()
            if name not in _BUILTINS and self.extensions is not None:
                ext_result = self.extensions.call_tool(name, args, workspace)
                if ext_result is not None:
                    yield _flatten_event(
                        tool_result_event(
                            name,
                            self._normalize_tool_result(ext_result),
                            duration_ms=0,
                            tool_call_id=event.get("id"),
                        )
                    )
                    return
            try:
                # Tools are blocking I/O (web requests, subprocess); run them
                # off the loop or one slow fetch freezes every concurrent
                # turn — parent AND sibling subagents — while their wall-
                # clock timeouts keep ticking.
                raw_result: str | dict[str, Any] = await asyncio.to_thread(
                    execute_tool, name, args, workspace=workspace
                )
            except Exception as e:
                logger.exception("Tool execution failed: %s", name)
                raw_result = {"status": "error", "data": str(e)}
            duration_ms = (time.time() - start) * 1000
            normalized = self._normalize_tool_result(raw_result)
            yield _flatten_event(
                tool_result_event(
                    name, normalized, duration_ms=duration_ms, tool_call_id=event.get("id")
                )
            )

    def _normalize_tool_result(self, result: Any) -> dict[str, Any]:
        """Normalize any tool result to a standard JSON-serializable schema.

        Schema:
            {
                "status": "ok" | "error",
                "data": str | dict | list,     # human-readable or structured result
                "metadata": {                   # optional metadata
                    "tool": str,
                    "args": dict,
                    "result_length": int,
                    ...
                }
            }
        """
        import json
        from pathlib import Path

        # Already in standard schema
        if isinstance(result, dict) and "status" in result:
            # Ensure data is serializable
            data = result.get("data", "")
            return {
                "status": result["status"],
                "data": self._serialize_value(data),
                "metadata": self._serialize_value(result.get("metadata", {})),
            }

        # JSON string that contains a structured result — parse it
        if isinstance(result, str) and result.startswith("{"):
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict) and "status" in parsed:
                    return {
                        "status": parsed["status"],
                        "data": self._serialize_value(parsed.get("data", "")),
                        "metadata": self._serialize_value(parsed.get("metadata", {})),
                    }
            except json.JSONDecodeError:
                pass

        # Error tuple/list (must have exactly 2 elements, first is "error")
        if (
            isinstance(result, (list, tuple))
            and len(result) == 2
            and result[0] == "error"
        ):
            return {
                "status": "error",
                "data": str(result[1]),
                "metadata": {"raw": str(result)},
            }

        # Exception
        if isinstance(result, BaseException):
            return {
                "status": "error",
                "data": str(result),
                "metadata": {"exception_type": type(result).__name__},
            }

        # None
        if result is None:
            return {"status": "ok", "data": "", "metadata": {}}

        # Path
        if isinstance(result, Path):
            return {"status": "ok", "data": str(result), "metadata": {"is_path": True}}

        # Bytes
        if isinstance(result, bytes):
            try:
                text = result.decode("utf-8")
            except UnicodeDecodeError:
                text = result.decode("utf-8", errors="replace")
            return {"status": "ok", "data": text, "metadata": {"was_bytes": True}}

        # String
        if isinstance(result, str):
            return {"status": "ok", "data": result, "metadata": {}}

        # Dict
        if isinstance(result, dict):
            return {"status": "ok", "data": result, "metadata": {}}

        # List
        if isinstance(result, list):
            return {"status": "ok", "data": result, "metadata": {}}

        # Anything else — coerce to string
        return {
            "status": "ok",
            "data": str(result),
            "metadata": {"original_type": type(result).__name__},
        }

    def _serialize_value(self, value: Any) -> Any:
        """Serialize a value to JSON-compatible types."""
        import json
        from pathlib import Path

        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return value.decode("utf-8", errors="replace")
        if isinstance(value, dict):
            return {k: self._serialize_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._serialize_value(v) for v in value]

        # Fallback: JSON round-trip
        try:
            return json.loads(json.dumps(value, default=str))
        except (TypeError, ValueError):
            return str(value)

    # Silent-stall / empty-stream guard for parent turns. Same knob as
    # SubagentRunner: a healthy provider starts streaming in seconds, so
    # silence past this deadline means the request is dead.
    FIRST_TOKEN_DEADLINE_S = float(os.environ.get("WISP_FIRST_TOKEN_DEADLINE", "90"))

    # Provider bookkeeping events that don't count as real output when
    # deciding whether a stream came back empty.
    _BOOKKEEPING_TYPES = {"done", "stream_complete", "checkpoint", "usage", "stream_stats"}

    async def _guarded_provider_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Wrap one provider round-trip with stall + empty-stream recovery.

        Observed against NVIDIA's endpoint: ~1-in-5 identical requests
        close cleanly with ZERO deltas (fast, silent, useless), and some
        hold requests with no first byte indefinitely. Both previously
        produced a silently empty turn. Now: one transparent retry on a
        fresh request; a second failure surfaces as a visible error
        instead of nothing.
        """
        import asyncio as _aio

        for attempt in (1, 2):
            got_meaningful = False
            stream_stats: dict[str, Any] | None = None
            stream = self._stream_events_async(
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
            )
            stalled = False
            try:
                while True:
                    if not got_meaningful:
                        try:
                            event = await _aio.wait_for(
                                stream.__anext__(),
                                timeout=self.FIRST_TOKEN_DEADLINE_S,
                            )
                        except StopAsyncIteration:
                            break  # clean end, no meaningful events
                        except _aio.TimeoutError:
                            stalled = True
                            break
                    else:
                        try:
                            event = await stream.__anext__()
                        except StopAsyncIteration:
                            break
                    normalized = self._normalize_event(event)
                    ntype = str(normalized.get("type", ""))
                    if ntype == "stream_stats":
                        stream_stats = normalized
                    if ntype not in self._BOOKKEEPING_TYPES:
                        got_meaningful = True
                    yield event
            finally:
                if stalled or not got_meaningful:
                    aclose = getattr(stream, "aclose", None)
                    if aclose is not None:
                        await aclose()

            if got_meaningful:
                return

            if attempt == 1:
                if stalled:
                    reason = f"no data for {self.FIRST_TOKEN_DEADLINE_S:.0f}s"
                    detail = ""
                else:
                    reason = "closed without any content"
                    # HTTP-200-with-zero-deltas under parallel load is the
                    # throttle signature; the counters separate "server sent
                    # literally nothing" from "sent chunks we couldn't use".
                    detail = ""
                    if stream_stats:
                        detail = (
                            f" [sse_lines={stream_stats.get('sse_lines')} "
                            f"usable={stream_stats.get('usable_deltas')} "
                            f"empty_choice_chunks={stream_stats.get('empty_choice_chunks')} "
                            f"finish={stream_stats.get('finish_reason')}]"
                        )
                logger.warning(
                    "Provider stream %s%s (attempt %d) — retrying once",
                    reason, detail, attempt,
                )
                # Immediate retry into a throttling endpoint reproduces the
                # failure; a short jittered pause gives the window a chance
                # to reopen without meaningfully delaying healthy streams.
                if not stalled:
                    await _aio.sleep(0.75 + (_aio.get_event_loop().time() * 1000 % 1.5))
                continue

            yield _flatten_event(error_event(
                "Provider returned no usable response after a retry — "
                "the model endpoint is misbehaving. Try again shortly.",
                recoverable=True,
            ))

    def _normalize_event(self, event: Any) -> dict[str, Any]:
        """Normalize provider event to standard format.

        Whitelist known fields instead of copying __dict__ to avoid
        leaking internal state or circular references.
        """
        if isinstance(event, dict):
            return dict(event)

        result: dict[str, Any] = {}

        # Extract type/phase
        if hasattr(event, "type"):
            result["type"] = event.type
        elif hasattr(event, "phase"):
            result["type"] = event.phase
        else:
            result["type"] = "unknown"

        # Whitelist known safe fields
        safe_fields = {
            "text",
            "name",
            "arguments",
            "result",
            "message",
            "duration_ms",
            "turns",
            "session_id",
            "summary",
            "reason",
            "level",
            "recoverable",
            "tool_call_id",
            "id",
            "calls",
            "done_reason",
        }
        for field_name in safe_fields:
            if hasattr(event, field_name):
                result[field_name] = getattr(event, field_name)

        # Map provider-specific event types to canonical types
        # (ToolCallBatch uses 'tool_calls' which we handle in turn())

        return result

    def _validate_tool_args(self, name: str, args: dict[str, Any]) -> Optional[str]:
        """Validate tool arguments against the registered JSON schema.

        Returns an error message string if validation fails, or None
        if the tool is not found or validation succeeds.
        """
        from wisp.tools.registry import TOOL_SCHEMAS

        # Find the schema for this tool
        schema = None
        for ts in TOOL_SCHEMAS:
            if ts.get("function", {}).get("name") == name:
                schema = ts.get("function", {}).get("parameters", {})
                break

        if schema is None:
            return None  # Unknown tool — let security layer handle it

        try:
            import jsonschema
            jsonschema.validate(instance=args, schema=schema)
            return None
        except Exception as exc:
            return f"Schema validation failed for tool '{name}': {exc}"

    def _get_approval_gate(self) -> ApprovalGate:
        """Lazily create the approval gate from current security policy."""
        if self._approval_gate is None:
            self._approval_gate = ApprovalGate(self.security)
        return self._approval_gate

    def _make_action(self, event: dict[str, Any]) -> Any:
        """Create Action from tool_call event."""
        from wisp.infra.security import Action

        return Action(
            name=event.get("name", ""),
            args=event.get("arguments", {}),
        )

    def _make_context(self, session: dict[str, Any]) -> Any:
        """Create Context from session."""
        from pathlib import Path
        from wisp.infra.security import Context

        return Context(workspace=Path(session.get("workspace", ".")))
def format_cross_session_block(
    facts: list[Any], summaries: list[Any]
) -> str:
    """Render remembered facts + past-session summaries for the prompt.

    Pure function so the injection contract is testable without disk.
    """
    lines: list[str] = []
    fact_items: list[str] = []
    for fact in facts or []:
        content = fact.get("content") if isinstance(fact, dict) else str(fact)
        if content and content.strip():
            fact_items.append(content.strip())
    if fact_items:
        important = [f for f in facts or [] if isinstance(f, dict) and f.get("important")]
        important_contents = {
            (f.get("content") or "").strip() for f in important
        }
        ordered = (
            [f for f in fact_items if f in important_contents]
            + [f for f in fact_items if f not in important_contents]
        )[:15]
        lines.append("## Cross-Session Memory")
        lines.append(
            "Facts the user asked you to remember across conversations:")
        lines.extend(f"- {f}" for f in ordered)

    if summaries:
        if lines:
            lines.append("")
        from wisp.agent_memory import get_agent_memory

        lines.append(get_agent_memory().format_for_prompt(list(summaries)))
    return "\n".join(lines)
