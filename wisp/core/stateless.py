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
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Optional, TypeVar, cast

from wisp.core.context_pruner import prune_messages
from wisp.core.contracts import DEFAULT_PRUNE_POLICY as _DEFAULT_PRUNE_POLICY
from wisp.core.contracts import ToolRisk, risk_for_tool
from wisp.core.events import (
    CODE_TURN_TIMEOUT,
    CODE_PROVIDER_STREAM,
    CODE_ITERATION_BUDGET,
    DENIAL_POLICY_DENIED,
    DENIAL_SCHEMA_INVALID,
    AgentEvent,
    canonical_event,
    is_error_outcome,
    content as content_event,
    tool_result as tool_result_event,
    error as error_event,
    done as done_event,
    system,
    provider_status as provider_status_event,
    steering_feedback,
    nudge_message,
    steering_message,
)
from wisp.core.provider_stream import (
    TERMINAL_TYPES,
    guarded_provider_stream,
    passthrough_if_canonical,
)
from wisp.core.approval_gate import ApprovalGate
from wisp.infra.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)

if TYPE_CHECKING:
    from wisp.providers.protocol import Provider, ProviderEvent
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
# Bounded LRU (Phase 2.5, D1): the key includes the agent-memory mtime,
# which changes every turn — an unbounded dict grew one entry per turn
# for the process lifetime (invalidate_caches has no callers). 64 entries
# cover workspace × variant × role-hash working set; dict-compatible API.
from wisp.core.prompt_cache import BoundedPromptCache as _BoundedPromptCache

_SYSTEM_PROMPT_CACHE: _BoundedPromptCache = _BoundedPromptCache(maxsize=64)

# TTL-memoized expensive, near-static context builders. The full system
# prompt cache key (above) includes the agent-memory file's mtime, which
# changes EVERY turn — forcing git (subprocess), lint (os.walk) and module
# (glob) scans to rebuild each turn even though they only depend on the
# workspace's *structure*. These per-workspace builders are memoized with
# a short TTL so the expensive I/O happens at most once per period while
# staying near-fresh. (memory_block is cheap — in-memory cache — and must
# stay live, so it is deliberately NOT memoized.)
_CONTEXT_TTL: dict[str, tuple[float, str, object]] = {}

_T = TypeVar("_T")

#: How many times a closed stagnation predicate may withhold `done` in one turn
#: (ADR-0036). The bound is the whole difference between an intervention and a
#: veto: after it is spent the gate surrenders honestly, exactly as the
#: verification floor does, so a stagnating turn still finishes — later, and
#: recorded as `GOAL_STAGNATED` rather than as success.
#:
#: Deliberately a default on this mechanism rather than a config key, mirroring
#: `VerificationFloorGuard.max_nudges` (which is also a constructor default).
#: Deliberately NOT shared with the floor guard's budget: they answer different
#: questions, and sharing one would let a progress signal spend the verification
#: budget.
_MAX_STAGNATION_INTERVENTIONS = 2


def _ttl_get(kind: str, key: str, builder: Callable[[], _T], ttl: float) -> _T:
    """Memoize one context section per `kind`, valid while `key` is unchanged.

    Keyed by `kind` (a fixed literal, three call sites) rather than by
    workspace, so the cache is bounded to three entries. A mismatched `key`
    rebuilds; a race costs a cache miss, never a wrong-workspace value.
    """
    now = time.monotonic()
    ent = _CONTEXT_TTL.get(kind)
    if ent is not None and ent[1] == key and now - ent[0] < ttl:
        return cast(_T, ent[2])
    value = builder()
    _CONTEXT_TTL[kind] = (now, key, value)
    return value



def _flatten_event(ev: AgentEvent | dict[str, Any]) -> dict[str, Any]:
    """Convert canonical AgentEvent to flat dict for backward compatibility."""
    if isinstance(ev, dict):
        return dict(ev)
    flat = dict(ev.data)
    flat["type"] = str(ev.type)
    flat["timestamp"] = ev.timestamp
    return flat


def _tool_result_output(result: Any) -> Any | None:
    """A tool result's own output, or None when it carries none.

    On the live path a tool result is the executor's envelope
    (``{"status", "tool", "data", "metadata"}``) — not text. The verification
    authority classifies a shell tool's **output text**, and the success
    encoding is *positional*: ``tools/bash.py::_format_bash_output`` emits
    ``[exit code: N]`` as the first thing on the line, and only for a non-zero
    exit. An envelope begins ``{``, so that positional test never fires and
    every command — including one that exited non-zero — reads as a success
    (F37). Handing the authority the envelope is what this function prevents.

    Only a **successful** envelope carries output. A refusal, a block, or a
    tool-level error arrives as a non-``ok`` envelope or as a bare message,
    and none of those is verification — returning None lets the caller skip
    them instead of feeding a banner to a parser that reads absence-of-marker
    as success.

    A value that is not an envelope is not tool output either: the executor's
    blocks (``[Blocked: …]``, ``[Denied: …]``) are plain strings on this same
    channel.

    *"Is this envelope a success?"* is the taxonomy's question, not this
    function's: the answer is delegated to ``core.events.is_error_outcome``,
    the binary view of the one outcome classifier. Comparing
    ``.get("status")`` to the literal ``"ok"`` here would be a second
    classifier for a vocabulary this module does not own — and
    ``test_no_module_reimplements_tool_result_status_classification`` exists
    precisely to catch it. The ``"status" not in result`` guard above is
    **not** redundant with the classifier: ``classify_result`` defaults a
    missing status to ``"ok"`` (success), while this function must treat a
    status-less dict as *not an envelope at all* and return None.
    """
    if isinstance(result, dict):
        if "status" not in result:
            return None
        return result.get("data") if not is_error_outcome(result) else None
    if isinstance(result, str) and result.lstrip().startswith("{"):
        import json as _json

        try:
            parsed = _json.loads(result)
        except (ValueError, TypeError):
            return None
        if isinstance(parsed, dict) and "status" in parsed:
            return parsed.get("data") if not is_error_outcome(parsed) else None
    return None


# ── Argument-validation failure kinds (F8's second half) ────────────────
#: The validator RAN and rejected the arguments. A failure of the DATA.
VALIDATION_SCHEMA_INVALID = DENIAL_SCHEMA_INVALID
#: The validator could not run — absent, unimportable, or broken. A failure of
#: the SYSTEM, and the distinction F8 exists to make: when a missing capability
#: is reported as a failure of the *input*, every test downstream becomes a test
#: of the wrong thing. Measured: 24 failures attributed to "pre-existing" that
#: were F8-caused, and six tests in `test_13h2_determinism.py` reporting the
#: wrong thing for the life of the repository.
VALIDATION_CAPABILITY_MISSING = "CAPABILITY_MISSING"


class ValidationFailure(str):
    """A validation failure that says *which kind* it is, without parsing prose.

    A `str` subclass **on purpose**. `_validate_tool_args` returns
    `Optional[str]`; three call sites interpolate the value into a message or an
    event payload, and one puts it in a JSON-serializable `data` field — so the
    value has to *be* a string for every existing consumer. Subclassing keeps all
    of that working (`isinstance(x, str)` is true, `if x:` is true, f-strings
    render the message) while adding the one thing F8's second half needs: a
    caller can tell a *system* failure from a *data* failure **without reading the
    message**, which is the difference between "your arguments are wrong" and
    "we could not check your arguments".

    A dataclass is the obvious shape and the wrong one: it would reach
    `{"status": "error", "data": <object>}` and stop being serializable, and
    `f"Blocked: {x}"` would render a repr instead of the message.
    """

    __slots__ = ("kind",)

    #: Declared, not merely assigned in `__new__`: `__slots__` alone leaves the
    #: attribute invisible to a type checker, and "the failure has no `kind`" is
    #: precisely the claim this class exists to make.
    kind: str

    def __new__(cls, message: str, kind: str) -> "ValidationFailure":
        self = super().__new__(cls, message)
        self.kind = kind
        return self


def _schema_invalid(tool_name: str, exc: BaseException) -> ValidationFailure:
    """The validator ran and rejected the arguments — a **data** failure.

    Byte-identical to the message this site produced before F8's second half, so
    a genuine rejection is unchanged.
    """
    return ValidationFailure(
        f"Schema validation failed for tool '{tool_name}': {exc}",
        VALIDATION_SCHEMA_INVALID)


def _capability_missing(tool_name: str, exc: BaseException) -> ValidationFailure:
    """The validator could not run — a **system** failure, not a verdict.

    The message names the missing capability, says plainly that the arguments were
    never checked, and does not blame the caller's input. It also states that
    re-issuing the call unchanged will fail identically, because the model is the
    reader and "retry" is otherwise the obvious response to a refusal.
    """
    return ValidationFailure(
        f"Tool argument validation is UNAVAILABLE for '{tool_name}': the JSON "
        f"Schema validator could not be loaded "
        f"({type(exc).__name__}: {exc}). This is a failure of the host, NOT of "
        f"the arguments — they were never checked. Install the declared "
        f"`jsonschema` dependency (see pyproject.toml); re-issuing this call "
        f"unchanged will fail identically.",
        VALIDATION_CAPABILITY_MISSING)


def _args_truncated(tool_name: str, raw: str) -> ValidationFailure:
    """The arguments arrived **truncated** — a failure of the HOST (ADR-0052).

    `providers/openai.py` substitutes `{"_raw": <the raw stream>}` when the accumulated
    argument JSON will not parse. That shape is deliberate — `write_file` salvages it — but every
    *other* tool sent it straight to `jsonschema.validate`, which rejected it with *"Additional
    properties are not allowed ('_raw' was unexpected)"*. The model then reads a schema error and
    concludes, correctly, that its call *"got mangled by the wrapper"* — diagnosing a symptom the
    host should have named.

    The cause is a stream that ended mid-JSON, which is the host's. So this carries the same
    `kind` as a missing validator: **a capability failure is published as a failure of the host,
    not as a denial** (ADR-0052), and it is not a verdict on arguments that were never checked.
    """
    shown = raw if len(raw) <= 80 else raw[:77] + "..."
    return ValidationFailure(
        f"Tool call '{tool_name}' arrived TRUNCATED: its arguments did not parse as JSON "
        f"({len(raw)} characters received, starting {shown!r}). The stream ended mid-JSON, so "
        f"the arguments were never checked against the schema — this is a failure of the HOST, "
        f"not a schema violation. Re-issue the call; if it was large, send fewer or smaller "
        f"items.",
        VALIDATION_CAPABILITY_MISSING,
    )


def _is_capability_failure(failure: object) -> bool:
    """True when a validation failure is a failure of the **host** (ADR-0052).

    Reads the `kind` the failure already carries, so no call site parses prose.
    `getattr` rather than a cast: a plain `str` (an older caller, or a future one
    that forgets) is a *data* failure, which is the safe default — it keeps today's
    denial envelope rather than inventing a capability claim.
    """
    return getattr(failure, "kind", None) == VALIDATION_CAPABILITY_MISSING


def _denial_display(status: str, tool_name: str, reason: str) -> str:
    """Human line for a denial envelope (13F.1 §16/§17).

    Machine status stays in `status`; this line renders in the REPL
    header and the model-visible history content. Distinct prefixes so a
    policy denial never reads as an ordinary failure.
    """
    label = {
        "POLICY_DENIED": "Policy denied",
        "USER_DENIED": "User denied",
        "APPROVAL_TIMEOUT": "Approval timed out",
        "CANCELLED": "Cancelled",
    }.get(status, "Blocked")
    return f"{label}: {tool_name} — {reason}" if tool_name else f"{label}: {reason}"


def _ensure_intake_id(tc_event: dict[str, Any]) -> dict[str, Any]:
    """Stamp one stable identity on a provider tool call lacking it.

    A provider call with no ID is "not yet applicable" (§2), not a loss:
    nothing authoritative existed to drop. Mint ONCE here at intake and
    every downstream consumer (assistant block, refusal, _execute_tool →
    result) shares it, so the pair is self-consistent. This is NOT the
    forbidden silent repair: the serializer still never invents, nothing
    is inferred from position/name, and an ID dropped AFTER intake still
    fails closed at the provenance gate. Without this, the assistant
    block mints one UUID while results default to "" (live 400 shape).
    """
    if not tc_event.get("id"):
        import uuid as _uuid
        tc_event["id"] = f"call_{_uuid.uuid4().hex[:8]}"
    return tc_event


def validate_tool_message_provenance(
    assistant_msg: dict[str, Any], tool_msgs: list[dict[str, Any]]
) -> str | None:
    """Check one appended assistant/tool pair before it enters history.

    Returns an explicit violation description, or None when every tool
    message carries a non-empty ID issued by this assistant block. Pure
    (no I/O) so the invariant is unit-pinning without a provider.
    Never repairs: a missing/unknown/wrong ID fails closed at the call
    site (turn ends with a protocol-integrity error, never a 400).
    """
    known = {str(tc.get("id", "")) for tc in assistant_msg.get("tool_calls", []) or []}
    known.discard("")
    seen: set[str] = set()
    for tm in tool_msgs:
        tid = tm.get("tool_call_id", "")
        if not tid:
            return f"tool message has missing/empty tool_call_id (known: {sorted(known)})"
        if tid not in known:
            return f"tool message has unknown tool_call_id ({tid!r}; known: {sorted(known)})"
        if tid in seen:
            return f"duplicate tool message for tool_call_id ({tid!r})"
        seen.add(tid)
    return None


#: Tool-name prefixes an *unrestricted* subagent is not handed. The skills menu is deliberately omitted from a
#: subagent's prompt, so its `skill__*` tools were schemas for names the child never sees listed, re-sent on every
#: provider call (47 tools and 9,017 tokens on one real HOME). A subagent with an explicit tool list is unaffected.
_SUBAGENT_EXCLUDED_TOOL_PREFIXES: tuple[str, ...] = ("skill__",)


def _tool_schema_name(schema: Any) -> str:
    """A provider tool schema's name, whichever shape (OpenAI function, or flat) carries it."""
    if isinstance(schema, dict):
        fn = schema.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            return str(fn["name"])
        return str(schema.get("name", ""))
    return ""


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

    async def turn(self, session: dict[str, Any], prompt: str, approval_handler: Any = None, steering_drain: Any = None, completion_gate: Any = None, declared_gate: Any = None) -> AsyncIterator[dict[str, Any]]:
        """Run one turn, yielding events.

        Loops internally: provider → tool_calls → execute → append → provider
        until the model returns content (no tool calls) or max iterations.

        *steering_drain*, when given, is called at each tool boundary; its
        strings are mid-course corrections appended as user messages so the
        next provider round-trip adapts (M3 of docs/repl-design.md).

        *completion_gate*, when given, is a **read-only** predicate asked at the
        pre-`done` gate: `True` = "completion may proceed". It is ADR-0036's
        seam — the runtime owns M13's stagnation detector and passes a closure
        over it, so the engine can withhold `done` for a bounded number of
        replan interventions without gaining any authority over stagnation, and
        without ever holding the detector (whose `observe()` mutates). Absent,
        it behaves exactly as before.

        Has a wall-clock timeout (config turn_timeout, default 30 min) to
        prevent infinite hangs.
        """
        import asyncio as _asyncio
        from wisp.tools import context as _exec_ctx
        turn_timeout = getattr(self.config, "turn_timeout", 7200) if self.config else 7200
        # Publish the absolute deadline so nested consumers (subagent
        # orchestrator retries) can budget themselves against the same clock.
        # Overwritten by every turn; only read while a turn is live. Lives
        # in wisp.tools.context so leaf tools need no engine import.
        _exec_ctx.turn_deadline.set(time.monotonic() + turn_timeout)
        # Publish the EXECUTING agent's nesting identity for the duration of
        # this turn. The shared ToolExecutor's own config is the root's, so
        # without this a child's spawn/fanout would stamp depth from 0. The
        # engine's config is the executing agent's (root or child), so it is
        # the correct source. Overwritten by every turn, like the deadline.
        _exec_ctx.agent_depth.set(
            int(getattr(self.config, "_subagent_depth", 0) or 0)
            if self.config is not None else 0
        )
        _exec_ctx.agent_branch.set(
            int(getattr(self.config, "_subagent_branch_count", 0) or 0)
            if self.config is not None else 0
        )
        # Build messages list
        messages = list(session.get("messages", []))
        # Turn-0 boot seed (GH#22): workspace reality as the first message,
        # ahead of the user's prompt. Turn-0 means no prior history: either
        # the session carries nothing yet, or exactly the just-added user
        # prompt (the runtime appends it before turn(); post-/clear lands
        # here too since clear empties the transcript). Resumes keep their
        # persisted seed, subagent children stay lean. The static system
        # prompt below is untouched (KV-cache invariant).
        _fresh = (not messages or (len(messages) == 1
                  and messages[0].get("role") == "user"
                  and messages[0].get("content") == prompt))
        if _fresh and not session.get("subagent_system_prompt"):
            try:
                from wisp.core.context.boot import BootContextAssembler
                seed = BootContextAssembler(
                    session.get("workspace", ".")).seed_message()
            except Exception:
                logger.debug("Boot seed failed", exc_info=True)
                seed = None
            if seed:
                messages.insert(0, seed)
        # Avoid duplicating the user message if runtime already added it
        if (
            not messages
            or messages[-1].get("role") != "user"
            or messages[-1].get("content") != prompt
        ):
            messages.append({"role": "user", "content": prompt})

        # Build system prompt with full context awareness
        system_prompt = self._build_system_prompt(session, query=prompt)

        # Tools: built-in + extensions, narrowed for role-restricted subagents (see _provider_tools).
        tools = self._provider_tools(session)

        max_iterations = getattr(self.config, "max_iterations", 30)

        try:
            async with _asyncio.timeout(turn_timeout):
                async for event in self._turn_inner(
                    session, prompt, messages, system_prompt, tools,
                    max_iterations, self._memoize_handler(approval_handler),
                    steering_drain=steering_drain,
                    completion_gate=completion_gate,
                    declared_gate=declared_gate,
                ):
                    yield event
        except _asyncio.TimeoutError:
            yield _flatten_event(error_event(
                f"Turn timed out after {turn_timeout}s", recoverable=False,
                code=CODE_TURN_TIMEOUT,
                hint="raise `turn_timeout` in config (WISP_TURN_TIMEOUT)",
                context=[f"budget: {turn_timeout}s"],
            ))
            yield _flatten_event(done_event(session.get("id", "")))

    async def _turn_inner(
        self, session: dict[str, Any], prompt: str, messages: list[dict[str, Any]], system_prompt: str, tools: list[dict[str, Any]] | None,
        max_iterations: int, approval_handler: Any, steering_drain: Any = None,
        completion_gate: Any = None,
        declared_gate: Any = None,
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
        # Verification floor (harness > model on completion calls): the
        # guard owns wrote_code / exit-0-evidence / grind-budget state so
        # _turn_inner never re-derives the invariant inline.
        from wisp.core.verification import (
            SHORT_REPEAT_NUDGE,
            VerificationFloorGuard,
            _VERIFY_TOOLS,
            compose_nudge,
        )

        verification_enabled = True
        if self.config is not None:
            verification_enabled = getattr(self.config, "verification_loop", True) is True
        guard = VerificationFloorGuard(
            enabled=verification_enabled,
            min_turns=int(getattr(self.config, "grind_min_turns", 5) or 5)
            if self.config is not None else 5,
        )
        # Migration P3 (stage 3a): publish this turn's guard so the runtime can
        # RECORD a completion verdict against it.
        #
        # A DELIBERATE, NARROW EXCEPTION to "the core has no mutable state"
        # (AGENTS.md). The reasoning, and its limits:
        #
        #   - It is not SESSION state. It is a per-turn handle, overwritten at
        #     the start of every turn and read only by the runtime that just
        #     ran that turn. Session state still lives in AgentRuntime.
        #   - It is race-free in practice: cores are cached per (session,
        #     fingerprint), so two sessions never share one, and concurrent
        #     turns on the SAME session are serialized by the session lock.
        #   - The alternative was worse. The runtime cannot reach the guard
        #     any other way, and re-deriving `wrote_code` /
        #     `verify_ok_after_edit` from observed tool events would be a
        #     SECOND implementation of the floor rule — a second authority for
        #     "was this verified", which is the defect class this migration
        #     exists to remove.
        #
        # Nothing here changes the guard's behaviour; the completion invariant
        # is still the guard's alone.
        self._last_guard = guard
        # Reasoning core (docs/harness/reasoning-core-design.md): one per turn, called from three seams below. In
        # `observe` it only records (RC4); it can never fail the turn (RC6).
        reasoning = None
        if self._reasoning_mode().value != "off":
            from wisp.core.reasoning.runtime import TurnReasoning

            reasoning = TurnReasoning(self._reasoning_mode())
        self._last_reasoning = reasoning

        def _note_provider_error(message: str) -> None:
            # Seam 2, the ONE call site. A provider failure reaches here two ways: as an `error` event in the stream (a real 402 does) and
            # as an exception out of the stream.
            if reasoning is not None:
                reasoning.observe_provider_error(
                    message, int(getattr(self.config, "max_tokens", 0) or 0))
        # Stagnation interventions spent this turn (ADR-0036). A LOCAL, not a
        # field: it is per-turn control state and never authority — not
        # journaled, not a goal-state input, and a field would make it shared
        # state across concurrent turns.
        stagnation_interventions_used = 0
        for iteration in range(max_iterations):
            pending_tool_calls: list[dict[str, Any]] = []
            tool_results_events_early: list[dict[str, Any]] = []
            provider_events: list[dict[str, Any]] = []
            partial_content: list[str] = []
            has_tool_calls = False
            # G1B: the provider round-trip ended non-complete (error event
            # or mid-stream stall). The error is already yielded live below;
            # the finish path must not emit done for this iteration.
            provider_failed = False
            # G1D: short descriptor of the round failure for the completion
            # gate refusal + observability (stamped onto tool calls).
            provider_fail_note = "non-complete"

            # ── Pre-flight pruning: condense historical tool payloads
            # Prevents unbounded bloat (30+ tool calls) that stalls
            # the HTTP write (60s timeout) and blows context window.
            # Keep last 3 tool results full, condense older read_file/
            # list_files to status/diff, enforce 8KB/200KB ceilings.
            _pruned_for_provider: list[dict[str, Any]] | None = None
            try:
                # return_stats defaults to False, so the list arm is the
                # only reachable one here; cast records that contract.
                _pruned_for_provider = cast(
                    list[dict[str, Any]],
                    prune_messages(messages, _DEFAULT_PRUNE_POLICY))
            except Exception:
                logger.debug("Context pruning failed — sending raw messages", exc_info=True)
                _pruned_for_provider = None

            try:
                async for event in self._guarded_provider_stream(
                    system_prompt=system_prompt,
                    messages=_pruned_for_provider if _pruned_for_provider is not None else messages,
                    tools=tools if tools else None,
                ):
                    # Normalize event
                    normalized = self._normalize_event(event)
                    provider_events.append(normalized)

                    # G1B: provider-reported failure ends the round-trip
                    # non-complete (mid-stream error, truncation, terminal
                    # exhaustion). A mid-stream stall notice counts too:
                    # the stream never reached terminal completion.
                    ntype = normalized.get("type", "")
                    if ntype == "error" or (
                        ntype == "provider_status"
                        and normalized.get("status") == "chunk_stall"
                    ):
                        provider_failed = True
                        detail = normalized.get("message") or normalized.get("detail") or ""
                        provider_fail_note = str(detail)[:160] or "non-complete"
                        _note_provider_error(str(detail))

                    # Accumulate partial content for error recovery
                    if normalized.get("type") == "content":
                        partial_content.append(normalized.get("text", ""))
                        streamed_any_content = True

                    # Security + extension checks for tool calls
                    if normalized.get("type") in ("tool_call", "tool_calls"):
                        has_tool_calls = True
                        # Normalize type to singular for downstream consistency
                        normalized["type"] = "tool_call"
                        # Intake identity: a provider call without an ID gets
                        # ONE stable ID here; every consumer below (assistant
                        # block, refusal, _execute_tool → result) shares it.
                        _ensure_intake_id(normalized)
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
                                    tc_event = _ensure_intake_id(dict(single))
                                    await self._gate_tool_call(
                                        tc_event, session, approval_handler, _allowed_set)
                                    if "_blocked" in tc_event:
                                        pending_tool_calls.append(tc_event)
                                        tool_results_events_early.append(
                                            self._refusal_result_event(tc_event, session.get("workspace", ".")))
                                        yield _flatten_event(
                                            error_event(
                                                f"Blocked: {tc_event['_blocked']}",
                                                recoverable=True,
                                            )
                                        )
                                        continue

                                    pending_tool_calls.append(tc_event)
                                    yield _flatten_event(tc_event)
                                continue  # Skip the default yield below since we already yielded
                            continue

                        # Role restriction, schema, approval gate, extension
                        # intercept — same 4-stage check as the batch path
                        # above, via _gate_tool_call. Refusal is registered
                        # as a real tool result so history stays
                        # protocol-consistent: the model emitted this call
                        # and MUST see its outcome, otherwise it
                        # deterministically replays the identical call
                        # forever (live pty repro).
                        await self._gate_tool_call(
                            normalized, session, approval_handler, _allowed_set)
                        if "_blocked" in normalized:
                            pending_tool_calls.append(normalized)
                            tool_results_events_early.append(
                                self._refusal_result_event(normalized, session.get("workspace", ".")))
                            yield _flatten_event(
                                error_event(
                                    f"Blocked: {normalized['_blocked']}",
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
                # Retry transient errors (connection, timeout, 5xx, write timeout,
                # RemoteProtocolError) up to 2 times. Use hardened is_transient_error
                # for socket-level errors (httpcore.WriteTimeout etc.) plus
                # string fallback for wrapped errors.
                try:
                    from wisp.core.transport import is_transient_error as _is_transient

                    is_transient = _is_transient(exc)
                except ImportError:
                    _is_transient = lambda e: False  # type: ignore

                # Fallback string check for cases where is_transient misses
                # (e.g., wrapped errors without proper type)
                exc_str = str(exc).lower()
                if not is_transient:
                    is_transient = any(
                        s in exc_str for s in ("connection", "timeout", "timed out", "reset", "502", "503", "504", "refused", "broken pipe", "writetimeout", "readtimeout", "remoteprotocol", "write operation timed out")
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
                _note_provider_error(str(exc))
                if not is_transient:
                    _err_ev = error_event(
                        f"Provider stream failed: {exc}", recoverable=False,
                        code=CODE_PROVIDER_STREAM,
                        hint="check API key/quota and model availability; "
                             "transient network errors retry automatically",
                    )
                else:
                    _err_ev = None
                if partial_content and not streamed_any_content:
                    # Only emit accumulated content when NOTHING was streamed
                    # live — otherwise transports render the text twice and the
                    # duplicate lands in session history permanently.
                    yield _flatten_event(content_event("".join(partial_content)))
                if _err_ev is not None:
                    yield _flatten_event(_err_ev)
                else:
                    yield _flatten_event(
                        error_event(f"Stream error: {exc}", recoverable=True)
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
                if provider_failed:
                    # G1B: the round-trip ended non-complete (error event
                    # or stall already yielded live). done=True would be a
                    # lie — but a silent end would be invisible (13-H1):
                    # close the turn with one terminal error classification
                    # naming the missing answer. Partial content already
                    # streamed live stays for diagnostics.
                    if partial_content:
                        outcome = ("partial response streamed above is retained "
                                   "but incomplete")
                    elif any(e.get("type") == "thinking" for e in provider_events):
                        outcome = ("reasoning streamed above is retained "
                                   "but incomplete")
                    else:
                        outcome = "no usable output was produced"
                    yield _flatten_event(error_event(
                        f"Incomplete provider round ({provider_fail_note}) — "
                        f"{outcome}; no final answer was produced",
                        recoverable=True,
                        code=CODE_PROVIDER_STREAM,
                        hint="retry the request (a fresh attempt starts clean)",
                        context=[f"round: incomplete ({provider_fail_note})"],
                    ))
                    return
                # Verification floor: a turn that changed code but ended with
                # a failing (or never-run) verification command is NOT
                # complete. Reject the finish, inject the harness reminder,
                # and give it another provider round; bounded so it can
                # always finish (floor exhaustion surrenders honestly).
                if reasoning is not None:
                    reasoning.observe_final("".join(partial_content))
                rejection = guard.rejection()
                if rejection is not None:
                    if rejection == SHORT_REPEAT_NUDGE:
                        # Identical repeat: no new tool outcome since the last
                        # nudge, so reuse the short pointer instead of a
                        # verbatim full nudge (and no extra budget was spent).
                        nudge = rejection
                    else:
                        if guard.verify_ok_after_edit is False:
                            reason = (
                                "the most recent verification command FAILED "
                                "(non-zero exit status)"
                            )
                        else:
                            reason = (
                                "no verification command (tests/linter) has been "
                                "run since your code changes"
                            )
                        # Nudge text derives from the invariant's home module
                        # (verification.compose_nudge) so the intervention can
                        # never drift from the gate (GH#27).
                        nudge = compose_nudge(reason)
                    messages.append(nudge_message(nudge))
                    yield _flatten_event(system(nudge, level="warning"))
                    continue
                # Stagnation completion gate (ADR-0036): M13's progress
                # predicate may withhold `done` for a BOUNDED number of replan
                # interventions, and then it surrenders honestly — exactly as
                # the floor guard above does. It is deliberately NOT a veto: a
                # veto would run the turn to the iteration budget, whose wrap-up
                # emits a fatal CODE_ITERATION_BUDGET, and a progress signal
                # would have been converted into a failure.
                #
                # The engine receives a READ-ONLY predicate and nothing else —
                # never the detector, whose `observe()` mutates. M13 remains the
                # single stagnation authority; this gate only asks. It sits
                # AFTER the floor guard so a verification rejection keeps its
                # exact behaviour and the turn is never double-nudged.
                if (completion_gate is not None
                        and stagnation_interventions_used
                        < _MAX_STAGNATION_INTERVENTIONS
                        # A further round must exist. Withholding on the last
                        # iteration would end the loop, run the budget wrap-up,
                        # and turn this honest surrender into a fatal
                        # CODE_ITERATION_BUDGET — a bound that can do that is
                        # not a bound.
                        and iteration + 1 < max_iterations):
                    try:
                        may_complete = bool(completion_gate())
                    except Exception:
                        # Fail open (ADR-0036): a broken predicate must not
                        # become a hung turn. The absence is not silent — the
                        # goal record carries the predicate's own answer.
                        logger.debug("completion gate failed", exc_info=True)
                        may_complete = True
                    if not may_complete:
                        stagnation_interventions_used += 1
                        # The prose comes from M13's home module (GH#27), so the
                        # intervention cannot drift from the signal that caused
                        # it. Imported lazily: with no gate to evaluate, the
                        # engine keeps its zero coupling to stagnation.
                        from wisp.core.stagnation import compose_replan_nudge

                        replan = compose_replan_nudge(
                            stagnation_interventions_used)
                        messages.append(nudge_message(replan))
                        yield _flatten_event(system(replan, level="warning"))
                        continue
                # Declared-criteria completion gate (ADR-0054): the objective's DECLARED
                # criteria (ADR-0050) may withhold `done` for a bounded replan. Same
                # delay-not-veto model as the two gates above, and it SHARES their
                # per-turn extension budget because the bound is on the TURN, not on the
                # concern — two gates spending from one pool keeps the total extension
                # bounded, which is the property ADR-0036 §4 established.
                #
                # The engine receives a READ-ONLY callable and nothing else: the criteria,
                # the specs and the probe live in the runtime's `DeclaredCriteriaGate`, so
                # the engine keeps no criteria and gains no authority. It sits AFTER both
                # gates so neither loses its exact behaviour and the turn is never
                # double-nudged.
                if (declared_gate is not None
                        and stagnation_interventions_used
                        < _MAX_STAGNATION_INTERVENTIONS
                        and iteration + 1 < max_iterations):
                    try:
                        declared_ok = bool(declared_gate())
                    except Exception:
                        # Fail open, as above: a broken predicate must not become a
                        # hung turn.
                        logger.debug("declared-criteria gate failed", exc_info=True)
                        declared_ok = True
                    if not declared_ok:
                        stagnation_interventions_used += 1
                        # The prose comes from the criteria source's own module (GH#27),
                        # so the intervention cannot drift from the signal.
                        from wisp.core.turn_criteria import compose_declared_nudge

                        nudge = compose_declared_nudge(
                            stagnation_interventions_used)
                        messages.append(nudge_message(nudge))
                        yield _flatten_event(system(nudge, level="warning"))
                        continue
                # Announced-step gate: "Let me reproduce the issue first." with no
                # tool call is not a final answer, and with no code changed none of
                # the gates above fires. Same bounded, shared extension budget, so a
                # model that only ever announces still ends the turn.
                from wisp.core.announced_step import (
                    announces_next_step, compose_continue_nudge)

                round_text = "".join(partial_content)
                if (announces_next_step(round_text)
                        and stagnation_interventions_used
                        < _MAX_STAGNATION_INTERVENTIONS
                        and iteration + 1 < max_iterations):
                    stagnation_interventions_used += 1
                    messages.append({"role": "assistant", "content": round_text})
                    nudge = compose_continue_nudge(round_text)
                    messages.append(nudge_message(nudge))
                    yield _flatten_event(system(nudge, level="warning"))
                    continue
                # RESOLVED (verified, not surrendered) → distill the trail
                # into a permanent auto skill, best-effort, never blocking.
                if guard.resolved():
                    try:
                        from wisp.skill_capture import capture_resolved_skill

                        if self.config is None or getattr(
                            self.config, "auto_skill_capture", True
                        ) is True:
                            capture_resolved_skill(
                                prompt, guard.steps,
                                session.get("workspace", "."))
                    except Exception:
                        logger.debug("Auto-skill capture failed", exc_info=True)
                yield _flatten_event(done_event(session.get("id", "")))
                return

            # ── Execute tools and feed results back to messages ──
            tool_results_events: list[dict[str, Any]] = list(tool_results_events_early)
            has_tool_results = any(e.get("type") == "tool_result" for e in provider_events)
            # Args by call id for the verification guard's skill trail.
            _call_args_by_id: dict[str, Any] = {
                str(tc.get("id", "")): tc.get("arguments", {})
                for tc in pending_tool_calls if tc.get("id")
            }
            if pending_tool_calls and not has_tool_results:
                for tc in pending_tool_calls:
                    if "_blocked" in tc:
                        # Refusal already synthesized at the gate; yield the
                        # live event once here. It STAYS in
                        # tool_results_events so it reaches messages below.
                        for early in tool_results_events_early:
                            if early.get("tool_call_id") == tc.get("id") \
                                    and not early.get("_yielded"):
                                early["_yielded"] = True
                                yield early
                        continue
                    # G1D: stamp the round state so the completion gate
                    # (and refusal observability) names the failure.
                    tc = {**tc,
                          "_round_complete": not provider_failed,
                          "_round_state": provider_fail_note
                          if provider_failed else "complete"}
                    async for result_event in self._execute_tool(
                        tc, session, approval_handler=approval_handler,
                    ):
                        # Provenance boundary: only tool_result events carry
                        # the originating call ID into history. Approval
                        # requests, heartbeats, and subagent progress ride
                        # this same channel for live rendering but must
                        # never become role:tool messages (their ID is "").
                        if result_event.get("type") == "tool_result":
                            # Tool output is DATA, never instructions. A result
                            # carrying an instruction shape is WITHHELD —
                            # fail-closed, and withheld rather than killing the
                            # run. See wisp/core/tool_result_guard.py.
                            from wisp.core.tool_result_guard import (
                                withhold_if_injected,
                            )
                            result_event = withhold_if_injected(result_event)
                            if self._invariant_gate_mode().value != "off":
                                from wisp.core.tool_result_guard import scrub_secrets
                                result_event = scrub_secrets(result_event)
                            tool_results_events.append(result_event)
                        yield result_event
                        # Verification-floor tracking: fold every tool outcome
                        # into the guard (ORDERING preserved — any edit
                        # invalidates prior verification, so a bash run
                        # before the last code change never counts).
                        if result_event.get("type") == "tool_result":
                            from wisp.skill_capture import _digest_args

                            t_name = str(result_event.get("name", ""))
                            # F37: the guard classifies a shell tool's OUTPUT
                            # TEXT, and the success encoding is positional. The
                            # event carries the executor's envelope, so unwrap
                            # it — handing over the envelope made every command
                            # read as a verified success.
                            t_out = _tool_result_output(
                                result_event.get("result", ""))
                            if reasoning is not None:
                                reasoning.observe_tool_result(
                                    result_event, t_out,
                                    _call_args_by_id.get(result_event.get("tool_call_id", ""), {}))
                            if t_out is None:
                                if t_name in _VERIFY_TOOLS:
                                    # A refused, blocked or failed verification
                                    # produced no verification, so it is not
                                    # evidence, and its banner must not reach
                                    # the parser (absence of the marker reads as
                                    # success). Skipping matches what the
                                    # pre-dispatch gate already does for a
                                    # denied call, and leaves the prior verdict
                                    # untouched rather than inventing one for a
                                    # command that never ran.
                                    continue
                                # Every other tool is classified by NAME, so its
                                # text is inert — but the fold must still
                                # happen: a refused mutation is still an
                                # attempted mutation. Pass no envelope.
                                t_out = ""
                            elif not isinstance(t_out, str):
                                t_out = str(t_out)
                            t_args = _call_args_by_id.get(
                                result_event.get("tool_call_id", ""), {})
                            g_args = _digest_args(t_args) if isinstance(t_args, dict) else {}
                            t_cmd = t_args.get("command") if isinstance(t_args, dict) else None
                            if isinstance(t_cmd, str) and self._invariant_gate_mode().value != "off":
                                from wisp.core.verification import COMMAND_ARG
                                g_args[COMMAND_ARG] = t_cmd
                            guard.note_tool_result(t_name, t_out, g_args)

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

            new_tool_msgs: list[dict[str, Any]] = []
            for tr in tool_results_events:
                content = tr.get("result", tr.get("data", ""))
                if isinstance(content, dict):
                    content = json.dumps(content)
                tc_id = tr.get("tool_call_id", "")
                new_tool_msgs.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": str(content),
                })
            # Upstream provenance gate (§14/15): the provider preflight is
            # the final defense; this catches the corruption at its origin
            # with a named error instead of a downstream 400. Never repairs.
            _prov_violation = validate_tool_message_provenance(
                assistant_msg, new_tool_msgs)
            if _prov_violation is not None:
                yield _flatten_event(error_event(
                    f"Protocol integrity failure, ending turn: {_prov_violation}",
                    recoverable=False,
                ))
                return
            messages.extend(new_tool_msgs)

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
                    messages.append(steering_message(note))
                    yield _flatten_event(steering_feedback(note))

        # Max iterations reached — don't discard the turn's work. One final
        # tool-less call asks the model to summarize what it gathered; the
        # raw error only surfaces if even that fails. (Live evidence: 50
        # tools / 7 minutes of research died behind this error with zero
        # answer delivered.)
        budget_notice = (
            "[SYSTEM] Iteration budget exhausted. Do NOT call any more "
            "tools. Summarize your findings and answer the user's request "
            "with what you have, noting anything left incomplete."
        )
        messages.append(nudge_message(budget_notice))
        # Transcript unification (issue #2, part B): the notice above is a
        # provider-visible injection like the verification nudge — mirror it
        # as a system event so the runtime persist loop can record it.
        yield _flatten_event(system(budget_notice, level="warning"))
        wrapped_up = False
        try:
            # Prune before final wrap-up as well — same payload bloat risk
            _wrap_messages: list[dict[str, Any]] | None = None
            try:
                _wrap_messages = cast(
                    list[dict[str, Any]],
                    prune_messages(messages, _DEFAULT_PRUNE_POLICY))
            except Exception:
                _wrap_messages = None
            # ADR-0039 R4/R5: consume canonical events from the
            # normalization-only boundary. This deliberately does NOT use
            # `_guarded_provider_stream`: the wrap-up must not retry an empty
            # final round, and it must SEE the terminal marker that the guard
            # consumes for its own bookkeeping. Both reasons are why the
            # boundary exists as a separate thing — not a reason to read raw
            # provider events (which is F40-1).
            async for ev in self._normalized_provider_stream(
                system_prompt,
                _wrap_messages if _wrap_messages is not None else messages,
                None,
            ):
                etype = ev.get("type", "")
                if etype in ("content", "text", "token"):
                    text = ev.get("text") or ev.get("content") or ""
                    if text:
                        yield _flatten_event(content_event(str(text)))
                elif etype in TERMINAL_TYPES:
                    # ADR-0039 R7/R8: the shared terminal authority, not a
                    # local `== "done"`. The typed Ollama path's only terminal
                    # is StreamComplete(phase="complete"), so a `done`-only
                    # check could never fire there (F40-2).
                    wrapped_up = True
                    break
        except Exception:
            logger.exception("Iteration wrap-up call failed")
        if not wrapped_up:
            yield _flatten_event(error_event(
                "Max iterations reached", recoverable=False,
                code=CODE_ITERATION_BUDGET,
                hint="raise `max_iterations` in config; the summary above "
                     "covers what was gathered before the budget ended",
            ))
        yield _flatten_event(done_event(session.get("id", "")))

    async def _stream_events_async(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> AsyncIterator[ProviderEvent]:
        """Wrap a synchronous provider generator in an async iterator.

        Runs the blocking I/O in a thread to avoid blocking the event loop.
        This allows concurrent requests in FastAPI and responsive REPL.
        Uses circuit breaker to fail fast when provider is unhealthy.
        """
        import asyncio

        if self.provider is None:
            raise RuntimeError("WispAgentCore has no provider configured")
        provider = self.provider

        # ── Safety-net pruning: ensure large payloads never reach transport
        # Even if caller forgot to prune, we prune here to enforce write
        # timeout budget (60s) and prevent payload stalling.
        try:
            messages = cast(list[dict[str, Any]],
                            prune_messages(messages, _DEFAULT_PRUNE_POLICY))
        except Exception:
            logger.debug("Pruning in _stream_events_async failed", exc_info=True)

        # Define the streaming callable for circuit breaker
        async def _call_provider() -> AsyncIterator[ProviderEvent]:
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
            queue: asyncio.Queue[ProviderEvent] = asyncio.Queue()
            done = object()  # sentinel
            producer_error: list[BaseException] = []
            cancelled = threading.Event()

            _sync_gen = None

            def _close_sync_gen() -> None:
                # Deterministic teardown on the owning thread — see
                # providers/protocol.py for the incident. Idempotent.
                if _sync_gen is not None:
                    close = getattr(_sync_gen, "close", None)
                    if close is not None:
                        with contextlib.suppress(Exception):
                            close()

            def _sync_producer() -> None:
                nonlocal _sync_gen
                try:
                    _sync_gen = provider.generate_stream_events(
                        system_prompt=system_prompt,
                        messages=messages,
                        tools=tools,
                    )
                    for event in _sync_gen:
                        if cancelled.is_set():
                            break
                        loop.call_soon_threadsafe(queue.put_nowait, event)
                    loop.call_soon_threadsafe(queue.put_nowait, done)  # type: ignore[arg-type]
                except BaseException as exc:
                    # Deliver the failure to the consumer instead of letting it
                    # die in the thread excepthook as a clean-looking end.
                    # BaseException (not just Exception): a provider raising
                    # CancelledError/KeyboardInterrupt must still terminate
                    # the queue protocol, or the consumer wedges forever on
                    # queue.get() (G1D §13). The consumer re-raises, so
                    # cancellation still propagates — never swallowed.
                    producer_error.append(exc)
                    with contextlib.suppress(RuntimeError):
                        loop.call_soon_threadsafe(queue.put_nowait, done)  # type: ignore[arg-type]
                finally:
                    _close_sync_gen()

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
                # Cooperative bounded wait — never thread.join() on the
                # loop thread (see providers/protocol.py for the incident).
                deadline = loop.time() + 1.0
                try:
                    while thread.is_alive() and loop.time() < deadline:
                        await asyncio.sleep(0.02)
                except RuntimeError:
                    pass  # loop closed mid-poll during interpreter teardown
                # Producer dead ⇒ generator suspended ⇒ close() is safe.
                # A producer stuck mid-read closes it from its own finally.
                if not thread.is_alive():
                    _close_sync_gen()

        # Use circuit breaker if configured
        if self._circuit_breaker is not None:
            breaker = self._circuit_breaker

            async def _emit_circuit_open() -> AsyncIterator[ProviderEvent]:
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

    def _profile_names(self) -> frozenset[str] | None:
        """The built-in tool names the configured profile offers, or None for no narrowing.

        A config object with no ``tool_profile`` (a hand-built partial one) is treated as ``full``: the real
        ``WispConfig`` always carries one, so only synthetic configs reach that branch.
        """
        from wisp.tools.profile import builtin_names_for

        if self.config is None:
            return None
        profile = getattr(self.config, "tool_profile", "full")
        return builtin_names_for(profile)

    @staticmethod
    def _builtin_tool_names() -> frozenset[str]:
        """Names in the built-in registry; anything else in a schema list is an extension tool (MCP, skills)."""
        from wisp.tools.registry import TOOL_SCHEMAS

        return frozenset(_tool_schema_name(t) for t in TOOL_SCHEMAS)

    def _provider_tools(self, session: dict[str, Any]) -> list[dict[str, Any]]:
        """The tool schemas this session's provider calls carry: built-in plus extensions, narrowed to a role's
        allowed subset (``session["allowed_tools"]``) and then by the capability partition, when that is on.

        An unrestricted subagent (a subagent prompt and no explicit list) also loses the parent's skill menu
        tools; see ``_SUBAGENT_EXCLUDED_TOOL_PREFIXES``."""
        tools = self._get_tool_schemas()
        allowed = session.get("allowed_tools")
        allowed_set: set[str] | None = None
        if isinstance(allowed, (list, tuple, set)) and "all" not in {str(a).lower() for a in allowed}:
            allowed_set = {str(a) for a in allowed}
        if allowed_set is not None:
            tools = [t for t in tools if _tool_schema_name(t) in allowed_set]
        elif session.get("subagent_system_prompt"):
            tools = [t for t in tools if not _tool_schema_name(t).startswith(_SUBAGENT_EXCLUDED_TOOL_PREFIXES)]
        profile_names = self._profile_names() if allowed_set is None else None
        if profile_names is not None:
            builtin = self._builtin_tool_names()
            tools = [t for t in tools if _tool_schema_name(t) not in builtin or _tool_schema_name(t) in profile_names]

        # 13-I2 capability partition: host-owned visibility filter over
        # provider-bound schemas. Flag OFF (default) preserves the exact
        # legacy surface; rollback needs no code change.
        if self.config is not None and getattr(
                self.config, "capability_filtering", False) is True:
            from wisp.capability_filter import filter_schemas_for_mode
            tools = filter_schemas_for_mode(
                tools, getattr(self.config, "permission_mode", "auto_edit"))
        return tools

    def prompt_overhead_chars(self, session: dict[str, Any]) -> int:
        """Characters every provider call of this session carries before any history: the system prompt and the
        provider-bound tool schemas. Spend accounting multiplies this by the number of calls."""
        import json

        return len(self._build_system_prompt(session)) + len(json.dumps(self._provider_tools(session), default=str))

    def _build_system_prompt(self, session: dict[str, Any], query: str | None = None) -> str:
        """Build rich system prompt from session context."""
        from wisp.context_assembler import (
            ContextAssembler,
            DEFAULT_BASE_SYSTEM,
            PromptContext,
        )

        # Verification-loop rules are prompt-level; gate them on config so
        # they can be turned off without touching the engine guard.
        verification_enabled = True
        if self.config is not None:
            verification_enabled = getattr(self.config, "verification_loop", True) is True

        ws = session.get("workspace", ".")
        ws_path = Path(ws).resolve()

        # Lazy-init assembler (module-level, shared across all core instances)
        global _ASSEMBLER
        if _ASSEMBLER is None:
            _ASSEMBLER = ContextAssembler()
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
        # Role-restricted toolsets MUST also be part of the key — a generalist
        # (33 tools under auto_edit) and a reviewer (10 tools) share the same
        # workspace/mtime but must not reuse each other's tools block, or the
        # prompt advertises run_bash that the schema hides and the model
        # hallucinates it anyway (-> "Blocked: not allowed for this role").
        allowed = session.get("allowed_tools")
        allowed_set: set[str] | None = None
        allowed_hash = ""
        if isinstance(allowed, (list, tuple, set)) and "all" not in {str(a).lower() for a in allowed}:
            allowed_set = {str(a) for a in allowed}
            allowed_hash = hashlib.sha256(",".join(sorted(allowed_set)).encode()).hexdigest()[:8]
        # 13-I2: intersect the menu's allowlist with the mode partition so
        # the prompt never advertises schemas the provider never receives
        # (same hallucination seed the role filter above was built to kill).
        if self.config is not None and getattr(
                self.config, "capability_filtering", False) is True:
            from wisp.capability_filter import visible_tool_names
            menu_set = visible_tool_names(
                allowed_set,
                getattr(self.config, "permission_mode", "auto_edit"), True)
            if menu_set is not None:
                allowed_set = menu_set
                allowed_hash = hashlib.sha256(
                    ",".join(sorted(allowed_set)).encode()).hexdigest()[:8]
        # Thin-harness posture changes the tools menu — part of the key,
        # or a thin turn reuses a 42-tool prompt from the cache.
        thin = "thin" if (self.config is not None
                          and getattr(self.config, "thin_tools", False) is True) else ""
        # The profile narrows the menu only for an unrestricted session, and it is part of the key: two profiles must
        # not share a cached prompt.
        profile_names = self._profile_names() if allowed_set is None else None
        profile_key = "core" if profile_names is not None else ""
        cache_key = (ws, context_mt, prompt_variant, allowed_hash, thin, profile_key)
        static_prompt: str | None = _SYSTEM_PROMPT_CACHE.get(cache_key)

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

            # Verification loop prose mentions run_bash unconditionally; for
            # role-restricted subagents without run_bash (auto_edit generalist)
            # that prose is a hallucination seed — switch to the no-bash
            # variant that mentions run_tests/lsp_diagnostics instead.
            if not verification_enabled:
                _default_system = DEFAULT_BASE_SYSTEM
            elif allowed_set is not None and "run_bash" not in allowed_set:
                from wisp.context_assembler import VERIFICATION_LOOP_RULES_NO_BASH as _NO_BASH

                # Can't reuse assembler.default_system (it already has the
                # run_bash variant); rebuild from base + alt rules.
                _default_system = DEFAULT_BASE_SYSTEM + _NO_BASH
            else:
                _default_system = assembler.default_system

            if profile_names is not None:
                from wisp.context_assembler import adapt_system_prose

                _default_system = adapt_system_prose(_default_system, profile_names)

            ctx = PromptContext.from_legacy(
                workspace=ws,
                default_system=_default_system,
                role_extra=role_extra or None,
                skills_block=skills_block or None,
                project_context=project_ctx or None,
                memory_block=memory_block or None,
                git_context=git_ctx or None,
                repo_map=repo_map or None,
            )
            static_prompt = assembler.build(ctx)

            tools_block = self._build_tools_block(
                allowed_set,
                _SUBAGENT_EXCLUDED_TOOL_PREFIXES if is_subagent and allowed_set is None else (),
                profile_names,
            )
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

        # Environment grounding is per-turn too: cwd, git branch and the
        # commit hash move under the session (commits, /workspace switches).
        env_block = self._build_environment_block(ws)
        if env_block:
            static_prompt += "\n\n" + env_block

        return static_prompt

    def _build_environment_block(self, workspace: str) -> str:
        """Live environment facts for the system prompt ('## Environment').

        Best-effort and cheap (a couple of git subprocess calls + stat);
        returns '' when disabled or when collection yields nothing usable.
        """
        if self.config is not None:
            if getattr(self.config, "env_context", True) is not True:
                return ""
        try:
            from wisp.environment import collect_environment, format_environment_block
            snap = collect_environment(workspace)
            if snap.cwd:
                return format_environment_block(snap)
        except Exception:
            logger.debug("Environment block collection failed", exc_info=True)
        return ""

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

        # NOTE — the `## Environment` block is deliberately NOT built here.
        #
        # `_build_environment_block` owns that section and honours `config.env_context`. This method
        # used to build a second copy of it, which did three things: it put the section in every
        # prompt **twice** (byte-identical), it ran `collect_environment` twice per turn (that shells
        # out to `git` and is not memoized), and it **defeated the switch** — `env_context` gates the
        # other call site only, so turning it off still left a block in the prompt through here.
        # Pinned by `tests/test_operating_context.py::TestTheEnvironmentSectionHasOneProducer`.
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
                    shown = skill.instructions if skill.inline_instructions else skill.instructions[:200]
                    lines.append(f"  Instructions: {shown}")
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
        """Cross-session memory: remembered facts + recent summaries.

        Facts are **bucketed by scope** before rendering. `list_all_facts()` is global by design, so
        a fact recorded in another project arrived here with no provenance — and one of them asserts
        a container layout (`/workspace`) that is false for this repository. The model acted on it,
        every tool call landed outside the workspace, and the turn stalled. See
        `format_cross_session_block` for the failure and `wisp.memory.facts_grouped_by_scope` for the
        bucketing.
        """
        try:
            from wisp.agent_memory import get_agent_memory
            from wisp.memory import facts_grouped_by_scope

            buckets = facts_grouped_by_scope(workspace)
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
            return format_cross_session_block(buckets, summaries, workspace)
        except Exception as e:
            logger.debug("Failed to build memory block: %s", e)
            return ""

    def _build_git_context(self, workspace: str) -> str:
        """Build git context string.

        TTL-memoized (2s): spawning `git` a few times a second is wasteful
        and the state is advisory context, not a hard invariant.
        """
        def _build() -> str:
            try:
                from wisp.git_context import format_git_context

                return format_git_context(workspace)
            except Exception as e:
                logger.debug("Failed to build git context: %s", e)
                return ""
        return _ttl_get("git_ctx", str(workspace), _build, 2.0)

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

        TTL-memoized (10s): the detected extension set / installed linters
        change rarely mid-session; the os.walk to derive them should not
        run on every single turn.
        """
        def _build() -> str:
            ws_path = Path(workspace).resolve()
            lines: list[str] = []
            detected: set[str] = set()

            # Detect file extensions in the project
            ext_to_linter = {
                ".py": "py_compile syntax check (syntax errors only)",
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
            lines.append("After writing or editing a file, a syntax check + affected tests auto-run. "
                         "A passing check stays silent — silence means it ran and passed, not that it was skipped. "
                         "ruff/mypy are never auto-run; invoke them via run_bash if installed. Write code that passes them:")
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
            if ".py" in detected:
                for binary in ("ruff", "mypy"):
                    if shutil.which(binary) and f"`{binary}`" not in available:
                        available.append(f"`{binary}`")
            if available:
                lines.append(f"\nInstalled: {', '.join(available)}")

            return "\n".join(lines)
        return _ttl_get("lint_ctx", str(workspace), _build, 10.0)

    def _build_module_summary(self, workspace: str) -> str:
        """Build a concise module structure overview for the workspace.

        Scans top-level directories, identifies packages and key modules,
        so the model has a mental map before the first turn — no need to
        run list_files just to understand the project layout.

        TTL-memoized (30s): project layout is structural and changes rarely
        mid-session. Per-entry ops are guarded so a single unreadable
        directory (e.g. an unwritable /tmp subdir in CI) cannot crash the
        entire turn with PermissionError.
        """
        def _build() -> str:
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
                try:
                    if (ws_path / marker).exists():
                        project_types.append(label)
                except OSError:
                    pass

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
                try:
                    is_dir = entry_path.is_dir()
                except OSError:
                    continue
                if not is_dir:
                    continue
                # Check if it's a package (has __init__.py) or has source files
                pkg_init = entry_path / "__init__.py"
                try:
                    has_src = (
                        any(entry_path.glob("*.py")) or any(entry_path.glob("*.ts"))
                        or any(entry_path.glob("*.rs")) or any(entry_path.glob("*.go"))
                    )
                    pkg_has_init = pkg_init.exists()
                except OSError:
                    continue
                if pkg_has_init:
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

            try:
                config_files = [e for e in entries if not (ws_path / e).is_dir()
                                and e in markers and not markers[e].startswith("Python")]
            except OSError:
                config_files = []

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
        return _ttl_get("module_summary", str(workspace), _build, 30.0)

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

    def _build_tools_block(self, allowed_set: set[str] | None = None,
                           exclude_prefixes: tuple[str, ...] = (),
                           profile_names: frozenset[str] | None = None) -> str:
        """Generate the prompt's tool menu from live registries.

        Single source of truth: TOOL_SCHEMAS plus whatever extensions
        (MCP servers, plugins) expose at runtime. A newly registered tool
        is announced automatically; renaming one cannot leave a phantom
        name behind (the previous hardcoded dict drifted exactly this
        way — it advertised 'spawn_subagent', which never existed).

        *exclude_prefixes* drops tools by name prefix (an unrestricted subagent's skill tools, which its
        provider call does not carry either). *profile_names* narrows the BUILT-IN tools to the configured profile
        (extension tools pass through), so the menu names exactly what the provider is offered.

        When *allowed_set* is given (role-restricted subagents), only those
        tools are advertised — otherwise the prompt lists tools the model
        cannot call, which it hallucinates anyway and then hits the
        "Blocked: not allowed for this role" gate (live /spawn repro).
        """
        entries: list[tuple[str, str]] = []
        seen: set[str] = set()
        profile_scope = self._builtin_tool_names() if profile_names is not None else frozenset()

        def _describe(fn: dict[str, Any]) -> tuple[str, str]:
            name = str(fn.get("name", "") or "")
            desc = str(fn.get("description", "") or "").strip()
            first = desc.split(". ")[0].rstrip(".").strip()
            if len(first) > 140:
                first = first[:137] + "..."
            return name, first

        # Source of truth is _get_tool_schemas() (thin_tools-aware), not
        # TOOL_SCHEMAS directly — otherwise thin mode advertises 42 tools
        # the provider never receives (the exact hallucination seed the
        # allowed_set filter below was built to prevent for roles).
        for schema in self._get_tool_schemas():
            fn = schema.get("function", {}) if isinstance(schema, dict) else {}
            name, first = _describe(fn)
            if not name or name in seen:
                continue
            if allowed_set is not None and name not in allowed_set:
                continue
            if exclude_prefixes and name.startswith(exclude_prefixes):
                continue
            if profile_names is not None and name in profile_scope and name not in profile_names:
                continue
            seen.add(name)
            entries.append((name, first))

        extension_names: set[str] = set()
        if self.extensions is not None:
            try:
                for schema in self.extensions.tools() or []:
                    fn = schema.get("function", {}) if isinstance(schema, dict) else {}
                    name, _ = _describe(fn)
                    if name:
                        extension_names.add(name)
            except Exception as e:
                logger.warning("Failed to list extension tools: %s", e)

        # _get_tool_schemas() already includes extensions. Count only unique
        # extension names that survived the built-in precedence and filters.
        from wisp.tools.registry import TOOL_SCHEMAS

        if self.config is not None and getattr(self.config, "thin_tools", False) is True:
            from wisp.tools.primitives import PRIMITIVE_SCHEMAS

            builtin_names = {
                str(schema.get("function", {}).get("name", "") or "")
                for schema in PRIMITIVE_SCHEMAS
            }
        else:
            builtin_names = {
                str(schema.get("function", {}).get("name", "") or "")
                for schema in TOOL_SCHEMAS
            }
        advertised_names = {name for name, _ in entries}
        ext_count = len(
            (extension_names - builtin_names) & advertised_names
        )

        lines = ["## Tools available"]
        lines.extend(f"- {n}: {d}" for n, d in entries)
        if ext_count:
            lines.append(
                f"({ext_count} additional tool(s) provided by plugins/MCP servers)"
            )
        return "\n".join(lines)

    def _get_tool_schemas(self) -> list[dict[str, Any]]:
        """Get all tool schemas — built-in + extensions.

        Thin-harness posture (``thin_tools`` on the config): the model sees
        only the 3 primitives. Everything else stays callable server-side
        but leaves the system prompt.
        """
        from wisp.tools.registry import TOOL_SCHEMAS

        if self.config is not None and getattr(self.config, "thin_tools", False) is True:
            from wisp.tools.primitives import PRIMITIVE_SCHEMAS

            return list(PRIMITIVE_SCHEMAS)

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

        G1D completion gate (§4): the dispatcher stamps each call with
        ``_round_complete`` (bool) and ``_round_state`` (descriptor). When
        the provider round that produced this call ended non-complete
        (partial/truncated/stalled/error), MUTATING tools (anything but
        ToolRisk.READ, fail-closed) are refused with an explicit
        incomplete result — authorization ALLOW is necessary but not
        sufficient. Read-only tools proceed (existing salvage/validation
        apply; diagnostics preserved, §19). Direct callers without a stamp
        default to complete (their args are caller-asserted, not streamed).
        """
        name = event.get("name", "")
        args = event.get("arguments", {})
        workspace = session.get("workspace", ".")
        round_complete = event.get("_round_complete", True)
        if not round_complete and risk_for_tool(name) != ToolRisk.READ:
            salvaged = isinstance(args, dict) and "_raw" in args
            yield _flatten_event(
                tool_result_event(
                    name,
                    {"status": "error",
                     "data": (f"[Refused: provider round ended "
                              f"{event.get('_round_state', 'non-complete')}; "
                              f"mutating tool '{name}' requires a complete "
                              f"round (complete ∧ valid ∧ authorized). "
                              f"{'Salvaged candidate retained, not executed. ' if salvaged else ''}"
                              f"Candidate preserved for diagnostics; re-issue "
                              f"after a complete response.]")},
                    duration_ms=0,
                    tool_call_id=event.get("id"),
                )
            )
            return

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

            # Migration M15: a subagent authorizes as a NARROWED child, not as
            # the local human. The principal travels with the session (the same
            # channel `allowed_tools` uses) because the executor is shared: one
            # executor serves many children under `fanout`, and building one per
            # child would leak two thread pools each.
            async for agent_event in self.tool_executor.execute(
                name, args, workspace,
                tool_call_id=event.get("id"),
                approval_handler=wrapped_handler,
                principal=session.get("principal"),
            ):
                yield _flatten_event(agent_event)
        else:
            # Fallback: no ToolExecutor wired — safe reads only (M2 I2).
            # Anything else is denied: without an executor there is no
            # approval, policy, or audit, so execution would be a bypass.
            # (ToolRisk/risk_for_tool now imported at module level.)
            if risk_for_tool(name) != ToolRisk.READ:
                yield _flatten_event(
                    tool_result_event(
                        name,
                        {"status": "error",
                         "data": f"[Denied: {name} requires a wired "
                                 "ToolExecutor (no approval/policy/audit "
                                 "on the fallback path)]"},
                        duration_ms=0,
                        tool_call_id=event.get("id"),
                    )
                )
                return
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
                # Risk-gated to safe reads above (M2 I2 fallback); the public
                # registry gate additionally applies (reads pass it).
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

    # Silence between two chunks of an already-started stream. Healthy
    # providers emit deltas continuously; a gap this long means the
    # connection died mid-response. Without it, one stalled read held the
    # per-session lock until the 30-minute turn watchdog fired.
    CHUNK_DEADLINE_S = float(os.environ.get("WISP_CHUNK_DEADLINE", "90"))

    # NOTE (ADR-0043): there is deliberately no `_BOOKKEEPING_TYPES` here.
    # A vocabulary list of non-terminal event types once participated in the
    # decision "did this attempt produce a response", and that list is what
    # produced F43 (a bare typed terminal blessed an empty attempt) and its
    # sibling (an unrecognised, payload-less event did the same). The guard now
    # decides meaningfulness from the event's PAYLOAD for every type, so there
    # is no list to keep in sync.

    async def _normalized_provider_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> AsyncIterator[dict[str, Any]]:
        """One provider round-trip, canonicalized — and nothing else.

        ADR-0039 R5/R6: normalization and stall recovery are two
        responsibilities. This is the normalization-only boundary:

          * exactly one canonicalization pass per event (R2);
          * terminal events are **forwarded**, never consumed (R6);
          * **no** retry, **no** stall/empty-stream recovery, **no**
            completion decision, **no** authorization, **no** tool
            execution, **no** persistence.

        A consumer that needs canonical events but must not inherit stall
        recovery (the iteration wrap-up) obtains them here instead of
        reaching past the boundary to `_stream_events_async`.
        """
        async for event in self._stream_events_async(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
        ):
            yield self._normalize_event(event)

    async def _guarded_provider_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Wrap one provider round-trip with stall + empty-stream recovery.

        Thin delegate: the guard lives in wisp.core.provider_stream with
        everything injected (stream opener, normalizer, deadlines), so it
        is testable without a core and this class stays the single place
        env-tuned knobs are read.

        ADR-0039 R5: the guard now obtains its events from the
        normalization-only boundary, so it no longer owns canonicalization —
        it owns recovery. The stream is already canonical, so the normalizer
        it is given is a no-copy passthrough that still delegates for any
        non-canonical input (see `passthrough_if_canonical`), which keeps
        the guard total without canonicalizing the same event twice.
        """
        async for event in guarded_provider_stream(
            lambda: self._normalized_provider_stream(
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
            ),
            passthrough_if_canonical,
            first_token_deadline_s=self.FIRST_TOKEN_DEADLINE_S,
            chunk_deadline_s=self.CHUNK_DEADLINE_S,
            max_attempts=max(1, int(os.environ.get("WISP_STREAM_ATTEMPTS", "3"))),
        ):
            yield event

    def _normalize_event(self, event: Any) -> dict[str, Any]:
        """Normalize a provider event to the canonical flat representation.

        ADR-0039 R2: this method is the core's canonicalization entry point,
        and `wisp.core.events.canonical_event` is its **single
        implementation** — the whitelist lives there and nowhere else, so a
        second copy cannot drift from it (F42 was exactly such a copy).
        Delegating also keeps the projection total (ADR-0039 R3).
        """
        return canonical_event(event)

    def _validate_tool_args(self, name: str, args: dict[str, Any], _dry_run: bool = False) -> Optional[str]:
        """Validate tool arguments against the registered JSON schema.

        Returns an error message string if validation fails, or None
        if the tool is not found or validation succeeds.

        With ``_dry_run=True`` validation operates on a deep copy: the
        write_file salvage/path-defaulting below must not mutate the
        event under review, or Gate1 approval would see host-mutated
        args and downstream gates would lose the salvage markers they
        key their refusal text on. _execute_tool keeps the mutating
        call (salvaged args must flow into execution).
        """
        from wisp.tools.registry import TOOL_SCHEMAS

        if _dry_run and isinstance(args, dict):
            import copy
            args = copy.deepcopy(args)

        # Find the schema for this tool
        schema = None
        for ts in TOOL_SCHEMAS:
            if ts.get("function", {}).get("name") == name:
                schema = ts.get("function", {}).get("parameters", {})
                break

        if schema is None:
            return None  # Unknown tool — let security layer handle it

        # write_file is the most common "write this to a file" target and
        # models frequently omit the path when the user says "in a file"
        # without naming it. Instead of failing the tool call and forcing a
        # retry (which often stalls on large 18k payloads), default the
        # path and salvage _raw payloads produced by truncated streaming.
        # G1D: salvage produces a CANDIDATE (parse_mode=SALVAGED); only a
        # complete round may execute it (completion gate in _execute_tool).
        if name == "write_file" and isinstance(args, dict):
            if "_raw" in args and len(args) == 1:
                logger.info("Salvaged _raw tool args for write_file "
                            "(parse_mode=SALVAGED, candidate only)")
                raw = args.get("_raw", "")
                import json as _json

                try:
                    recovered = _json.loads(raw) if isinstance(raw, str) else {}
                    if isinstance(recovered, dict) and "content" in recovered:
                        args.clear()
                        args.update(recovered)
                    elif isinstance(raw, str) and raw.strip():
                        args.clear()
                        args["content"] = raw
                except Exception:
                    if isinstance(raw, str) and raw.strip():
                        args.clear()
                        args["content"] = raw
            if "content" in args and "path" not in args:
                content = str(args.get("content", ""))
                default_path = "./output.md" if content.lstrip().startswith("#") or "##" in content[:500] else "./output.txt"
                args["path"] = default_path

        # A truncated call reaches here as `{"_raw": <raw stream>}` — the shape
        # `providers/openai.py` substitutes when the arguments will not parse. `write_file`
        # salvages it above; every other tool must be told what actually happened, because a
        # schema verdict on the wrapper blames the wrapper. Named BEFORE validation, and
        # published as a host failure (ADR-0052) — the stream ended mid-JSON, so the arguments
        # were never checked.
        if isinstance(args, dict) and len(args) == 1 and isinstance(args.get("_raw"), str):
            return _args_truncated(name, args["_raw"])

        try:
            import jsonschema
            jsonschema.validate(instance=args, schema=schema)
            return None
        except ImportError as exc:
            # F8's second half. The validator is absent, unimportable, or cannot
            # import something it needs — a failure of the SYSTEM, not a verdict
            # on the arguments. This clause must come FIRST: the broad handler
            # below would otherwise launder it into a schema verdict, which is
            # how a *valid* call came to be refused as `SCHEMA_INVALID` for the
            # life of the repository.
            #
            # `ModuleNotFoundError` is an `ImportError`, so the original defect
            # (`import jsonschema` raising) and a `jsonschema`-internal import
            # failure (a `$ref` resolver, say) are both caught here — the brief's
            # "absent, unimportable, or raises from its own code".
            #
            # A schema rejection cannot reach this clause: `ValidationError` and
            # `SchemaError` are not `ImportError`s.
            return _capability_missing(name, exc)
        except Exception as exc:
            # A retry that re-validates the SAME `args` against the SAME `schema`.
            # Kept as it was; note that it is **inert**: nothing mutates `args`
            # between the two calls (the salvage above runs before the `try`), so
            # a deterministic validator raises identically and the `return None`
            # below is unreachable. Recorded, not removed — see
            # `PHASE_F8_ERROR_CLASSIFICATION.md` F-1.
            if name == "write_file" and isinstance(args, dict) and "path" in args:
                try:
                    import jsonschema as _js2

                    _js2.validate(instance=args, schema=schema)
                    return None
                except Exception:
                    pass
            return _schema_invalid(name, exc)

    async def _gate_tool_call(
        self, tc_event: dict[str, Any], session: dict[str, Any],
        approval_handler: Any, allowed_set: set[str] | None,
    ) -> None:
        """Run the pre-execution gate on one tool_call event, in place.

        Role restriction → schema dry-run validation → approval gate →
        extension intercept — the single implementation shared by the
        batched (ToolCallBatch) and singular tool_call paths in
        _turn_inner, which used to duplicate this ~130 lines apart.

        Mutates *tc_event*: on a block it stamps `_blocked` (plus
        `_denial`/`_capability`/`_src`) so the caller can synthesize a
        refusal instead of executing it. An extension failure is itself
        treated as a deny, never raised.
        """
        name = str(tc_event.get("name", ""))

        if allowed_set is not None and name not in allowed_set:
            hint = ""
            if name == "run_bash":
                hint = " — run_bash is blocked in auto_edit mode; use list_files/read_file instead, or switch to full mode"
            elif name in ("spawn", "fanout"):
                hint = " — subagent spawning is blocked in auto_edit; switch to full mode"
            tc_event["_blocked"] = (
                f"tool '{name}' is not allowed for this agent's role{hint}")
            return

        # Structural validation BEFORE approval (13-J1): an invalid
        # proposal must never reach the human prompt. Dry-run: no event
        # mutation, so a write_file salvage below survives (approval
        # sees what was proposed; salvage markers survive for downstream
        # refusal text).
        schema_error = self._validate_tool_args(
            name, tc_event.get("arguments", {}), _dry_run=True)
        if schema_error:
            tc_event["_blocked"] = schema_error
            # ADR-0052: a host that cannot validate has not denied
            # anything, so only a genuine schema rejection is stamped as
            # a denial.
            if _is_capability_failure(schema_error):
                tc_event["_capability"] = True
            else:
                tc_event["_denial"] = "SCHEMA_INVALID"
            return

        # Invariant gates (paths, commands, dependency lock): deterministic and not overridable by approval, so they run BEFORE
        # a human is asked. A refusal rides the existing POLICY_DENIED status, with its own `_src` so it is audited once.
        invariant = self._invariant_gate_decision(
            name, tc_event.get("arguments", {}), session)
        if invariant is not None and invariant.violations:
            if invariant.allowed:
                logger.warning("invariant gate (observe only) would refuse %s: %s", name, invariant.render())
            else:
                tc_event["_blocked"] = invariant.render()
                tc_event["_denial"] = DENIAL_POLICY_DENIED
                tc_event["_src"] = "invariant_gate"
                return

        gate = self._get_approval_gate()
        gdec = await gate.check_decision(
            tc_event, session, approval_handler=approval_handler)
        if not gdec.allowed:
            tc_event["_blocked"] = gdec.reason or "blocked"
            tc_event["_denial"] = gdec.denial or "POLICY_DENIED"
            tc_event["_src"] = "gate"
            return

        if self.extensions is not None:
            try:
                ext_result = self.extensions.intercept(tc_event)
                if ext_result.get("action") == "block":
                    tc_event["_blocked"] = (
                        f"blocked by extension: "
                        f"{ext_result.get('reason', 'unknown')}")
            except Exception as e:
                logger.exception(
                    "Extension intercept failed — treating as deny: %s", e)
                tc_event["_blocked"] = f"extension intercept failed: {e}"

    def _reasoning_mode(self) -> Any:
        from wisp.core.reasoning.decision import parse_mode

        return parse_mode(getattr(self.config, "reasoning_core", "observe"))

    def _invariant_gate_mode(self) -> Any:
        from wisp.core.gates import parse_mode

        return parse_mode(getattr(self.config, "invariant_gates", "enforce"))

    def _invariant_gate_decision(self, name: str, args: Any, session: dict[str, Any]) -> Any:
        """The deterministic gates' verdict on one tool call, or None when they are switched off."""
        from wisp.core.gates import GateContext, GateMode, check_tool_call, parse_lock

        mode = self._invariant_gate_mode()
        if mode is GateMode.OFF:
            return None
        ctx = GateContext(
            workspace=str(session.get("workspace") or "."),
            home=os.path.expanduser("~"),
            mode=mode,
            deps_locked=parse_lock(getattr(self.config, "dependency_lock", "locked")),
            extra_write_roots=tuple(getattr(self.config, "gate_write_roots", ()) or ()),
        )
        return check_tool_call(name, args, ctx, mutating=risk_for_tool(name) != ToolRisk.READ)

    def _get_approval_gate(self) -> ApprovalGate:
        """Lazily create the approval gate from current security policy."""
        if self._approval_gate is None:
            self._approval_gate = ApprovalGate(self.security)
        return self._approval_gate

    @staticmethod
    def _memoize_handler(approval_handler: Any) -> Any:
        """One interactive prompt per (tool, args) per turn.

        The engine's ApprovalGate and ToolExecutor both consult the same
        handler; without memoization every gated tool prompts twice, which
        trains users to mash `y` — exactly how real approval prompts get
        missed and turns look hung. Identical replays of an already-answered
        call resolve instantly from the memo.
        """
        if approval_handler is None:
            return None
        memo: dict[tuple[str, str], bool] = {}

        async def memoized(event: dict[str, Any], args: Any = None,
                           reason: Any = None) -> bool:
            # Dual-protocol: ApprovalGate passes one event dict;
            # ToolExecutor's wrapper passes (name, args, reason).
            if args is None:
                name = event.get("name", "")
                call_args = event.get("arguments", {})
            else:
                name = event
                call_args = args
            import json as _json
            key = (str(name), _json.dumps(call_args, sort_keys=True, default=str))
            if key not in memo:
                result = await approval_handler(
                    {"name": name, "arguments": call_args})
                approved = result[0] if isinstance(result, tuple) else result
                # Collapse to a plain bool: ApprovalGate.check does
                # `if approved:` — a (False, None) tuple is truthy and
                # would turn user denials into approvals.
                memo[key] = bool(approved)
            return memo[key]

        return memoized

    def _refusal_result_event(self, tc: dict[str, Any], workspace: str = ".") -> dict[str, Any]:
        """Synthesize the tool-role refusal for a blocked call.

        Structured envelope (13F.1 R2): the denial kind was stamped as
        ``_denial`` by the refusing site (gate verdicts carry it;
        role/extension refusals default to POLICY_DENIED). ID preserved
        verbatim; never invented here.

        Audit (13F.1 R3, exactly-once): policy-denied GATE refusals are
        already recorded by SecurityPolicy._audit, so they are skipped
        here (``_src == "gate"`` + POLICY_DENIED). Every other refusal
        (user/timeout/cancel verdicts, role/extension blocks) is logged
        once via AuditLog, best-effort.
        """
        from wisp.core.events import capability_failure_result, denial_result
        reason = str(tc.get("_blocked", "blocked"))
        name = tc.get("name", "")
        # ADR-0052 — a capability failure is published as a failure of the HOST, not
        # as a denial. The stamping sites set `_capability` instead of `_denial` for
        # it, so `status` below is never read on that branch.
        capability = bool(tc.get("_capability"))
        status = str(tc.get("_denial") or "POLICY_DENIED")
        audit_reason = (
            f"{getattr(tc.get('_blocked'), 'kind', VALIDATION_CAPABILITY_MISSING)}: {reason}"
            if capability else f"{status}: {reason}")
        if not (tc.get("_src") == "gate" and status == "POLICY_DENIED"):
            try:
                from pathlib import Path as _Path
                from wisp.tools.audit import AuditLog as _AuditLog
                _mode = getattr(getattr(self, "config", None),
                                "permission_mode", "auto_edit")
                _mode = getattr(_mode, "value", _mode)
                _AuditLog(_Path(str(workspace)).resolve() / ".wisp" / "audit.jsonl").log_blocked(
                    str(name), dict(tc.get("arguments", {}) or {}),
                    str(workspace), audit_reason, str(_mode))
            except Exception:
                pass
        if capability:
            ev = capability_failure_result(
                name,
                reason,
                str(getattr(tc.get("_blocked"), "kind", None)
                    or VALIDATION_CAPABILITY_MISSING),
                duration_ms=0,
                tool_call_id=tc.get("id"),
            )
        else:
            ev = denial_result(
                name,
                status,
                _denial_display(status, name, reason),
                duration_ms=0,
                tool_call_id=tc.get("id"),
            )
        flat = _flatten_event(ev)
        flat["tool_call_id"] = tc.get("id", "")
        # Migration M13 — the canonical action identity, on the refusal.
        #
        # A refused call `continue`s BEFORE the call event is yielded (the
        # batch path appends it to `tool_results_events_early` instead), so the
        # runtime never sees its arguments. Without this stamp the only identity
        # a consumer could build from the reply is the tool *name*, which makes
        # `read_file(a.txt)` and `read_file(b.txt)` indistinguishable — and a
        # progress signal that cannot tell them apart flags a productive turn as
        # stagnant (F33). Stamped here, at the ONE helper every refusal goes
        # through, and computed with the same `action_key` the journal uses.
        try:
            from wisp.core.action_key import action_key as _action_key
            flat["action_key"] = _action_key(
                str(name), dict(tc.get("arguments", {}) or {}))
        except Exception:
            pass
        # Reasoning core, seam 1b: every refusal goes through this helper, so it is the one place the ledger learns of a call that never ran.
        reasoning = getattr(self, "_last_reasoning", None)
        if reasoning is not None:
            reasoning.observe_refusal(flat, tc.get("arguments", {}))
        return flat

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
    facts: Any, summaries: list[Any], workspace: str | None = None
) -> str:
    """Render remembered facts + past-session summaries for the prompt.

    Pure function so the injection contract is testable without disk.

    *facts* is the scope-bucketed mapping from `wisp.memory.facts_grouped_by_scope` — ``here`` /
    ``global`` / ``elsewhere``. A plain list is still accepted and rendered unscoped, which is what
    an older caller passes.

    **The provenance headings are the point.** Facts are recalled globally by design (*"memory works
    regardless of which directory the agent is running in"*), so this block routinely carries facts
    about *other* projects — including claims about their environment. With no provenance the model
    read one such claim, *"run_bash runs in a container rooted at /workspace, NOT the macOS path"*,
    as a statement about the repository it was actually in: it ran every tool against ``/workspace``,
    each call failed with *"outside workspace"*, and the turn could not proceed. A fact is only
    useful if the model knows what it is a fact **about**.
    """
    lines: list[str] = []

    def _contents(items: Any) -> list[str]:
        """Contents, **important facts first** — the ordering the block always had.

        A fact carrying `_origin` (from `facts_grouped_by_scope`) is prefixed with the workspace it
        came from, so the model can see *which project* it is a fact about rather than having to
        infer it from a heading.
        """
        out: list[str] = []
        for fact in items or []:
            if isinstance(fact, dict):
                content = fact.get("content")
                origin = fact.get("_origin")
            else:
                content, origin = str(fact), None
            if content and content.strip():
                prefix = f"[from {origin}] " if origin else ""
                out.append(prefix + content.strip())
        important = {
            (f.get("content") or "").strip()
            for f in (items or []) if isinstance(f, dict) and f.get("important")
        }
        return ([c for c in out if c.split("] ", 1)[-1] in important]
                + [c for c in out if c.split("] ", 1)[-1] not in important])

    buckets: dict[str, list[str]]
    if isinstance(facts, dict):
        buckets = {k: _contents(v) for k, v in facts.items()}
    else:                                   # legacy: an unscoped list
        buckets = {"here": _contents(facts)}

    if any(buckets.values()):
        lines.append("## Cross-Session Memory")
        lines.append("Facts the user asked you to remember across conversations.")

        def _emit(title: str, items: list[str], cap: int = 15) -> None:
            if not items:
                return
            lines.append("")
            lines.append(title)
            lines.extend(f"- {f}" for f in items[:cap])

        # This workspace's facts go FIRST and the heading appears even when there are none: with
        # nothing said about the workspace the model is actually in, the only facts it has are the
        # foreign ones — and it used them. An anchor it can read past beats no anchor at all.
        here = buckets.get("here", [])
        lines.append("")
        lines.append(f"**About this workspace** (`{workspace}`):" if workspace
                     else "**About this workspace:**")
        lines.extend(f"- {f}" for f in here[:15]) if here else lines.append(
            "- (nothing remembered for this workspace yet)")

        _emit("**Global — not specific to any workspace:**", buckets.get("global", []))
        _emit(
            "**From OTHER workspaces — each line names the project it came from. Some state how that "
            "project's environment is laid out (paths, containers, toolchains), which is exactly the "
            "kind of fact that is false here. Verify with a tool before acting on one:**",
            buckets.get("elsewhere", []), cap=5,
        )

    if summaries:
        if lines:
            lines.append("")
        from wisp.agent_memory import get_agent_memory

        lines.append(get_agent_memory().format_for_prompt(list(summaries)))
    return "\n".join(lines)
