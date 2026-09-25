"""Autonomous convergence entry point (NEXT mission).

`core/convergence.py` owns the loop. This module is the *wiring*: it builds
a real `run_turn` from the runtime, derives acceptance criteria for a plain
objective, and exposes one callable the CLI and the benchmark both use.

Why a separate module rather than a method on `AgentRuntime`: the runtime's
job is one turn, and its `run_turn` is the single-turn authority every
transport already depends on. Giving it an objective-level loop would make
one object own two different lifetimes. The loop composes the runtime
instead, and `core/convergence.py` stays testable with no runtime at all.

**Fresh session per attempt.** Each attempt gets a new session id. Carrying
the previous attempt's transcript forward would (a) grow without bound and
(b) feed the model its own failed reasoning as if it were established fact.
What crosses an attempt boundary is the *measured evidence* — a few lines of
facts — not the conversation. That is artifact-oriented context flow, and it
is the whole reason the loop can be bounded.
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

from wisp.core.convergence import (
    AttemptRequest,
    CommandProbe,
    ConvergenceController,
    ConvergenceResult,
    MeasureSpec,
    Objective,
    TurnObservation,
    WorkspaceSnapshot,
    criteria_for,
    explain_acceptance,
    read_journal_baseline,
)
from wisp.core.goal import TerminalOutcome, terminal_outcome_from_evidence

logger = logging.getLogger(__name__)

MAX_TRACKED_FILES = 4000

#: ADR-0048 R5 — strict criteria derivation. Read at this composition point, not
#: inside the pure function, so `explain_acceptance` stays testable without env.
#:
#: **Default OFF**, i.e. today's behaviour: an `UNDETERMINED` requirement is
#: recorded and not acted on. ON, it yields a required criterion the harness
#: cannot evidence, which `acceptance.evaluate`'s rule 3 turns into
#: `INCONCLUSIVE` — so an objective whose acceptance condition the host cannot
#: determine stops completing on a no-op. That is the fix for the measured false
#: `GOAL_MET` (ADR-0048 MODE A), and it is off by default because it also makes
#: such objectives uncompletable until they are clarified.
STRICT_DERIVATION_ENV = "WISP_CRITERIA_STRICT_DERIVATION"


def _strict_derivation_enabled() -> bool:
    """True when `WISP_CRITERIA_STRICT_DERIVATION` is set truthy."""
    import os
    return str(os.environ.get(STRICT_DERIVATION_ENV, "")).strip().lower() in (
        "1", "true", "yes", "on")


def _event_field(event: Any, key: str, default: Any = None) -> Any:
    """Read a field from a flat dict event or a typed `AgentEvent`.

    Both shapes reach this seam: `_flatten_event` yields dicts, while some
    paths hand over the typed object. Reading one and calling `.get()` on
    the other is F40's defect class, so the adapter handles both.
    """
    if isinstance(event, dict):
        if key in event:
            return event[key]
        data = event.get("data")
        return data.get(key, default) if isinstance(data, dict) else default
    data = getattr(event, "data", None)
    if isinstance(data, dict) and key in data:
        return data[key]
    return getattr(event, key, default)


def _event_type(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("type", ""))
    return str(getattr(event, "type", ""))


def observe_turn(events: list[Any]) -> TurnObservation:
    """Reduce one turn's event stream to facts.

    The turn predicate is NOT re-derived here. `saw_done` and
    `saw_fatal_error` are the two facts ADR-0044's single predicate uses, and
    `terminal_outcome_from_evidence` is the existing authority that turns
    them into an outcome; `turn_succeeded` is then a projection of that
    outcome, exactly as ADR-0044 defines it. A second implementation of the
    rule is the defect that ADR removed.
    """
    saw_done = False
    saw_fatal_error = False
    tool_calls = 0
    failure_code: str | None = None
    failure_message = ""
    failure_recoverable = False

    for event in events:
        etype = _event_type(event)
        if etype == "done":
            saw_done = True
        elif etype == "tool_call":
            tool_calls += 1
        elif etype == "error":
            message = str(_event_field(event, "message", "") or "")
            recoverable = bool(_event_field(event, "recoverable", False))
            code = _event_field(event, "code", None)
            if recoverable:
                # A recoverable error is the engine saying "this attempt was
                # not fatal" — carried verbatim, not interpreted.
                failure_message, failure_recoverable = message, True
                if code:
                    failure_code = str(code)
            else:
                saw_fatal_error = True
                failure_message, failure_recoverable = message, False
                failure_code = str(code) if code else failure_code

    outcome = terminal_outcome_from_evidence(
        saw_done=saw_done, saw_fatal_error=saw_fatal_error)
    return TurnObservation(
        turn_succeeded=outcome is TerminalOutcome.SUCCEEDED,
        terminal_outcome=outcome.value,
        failure_code=failure_code,
        failure_message=failure_message,
        failure_recoverable=failure_recoverable,
        tool_calls=tool_calls,
    )


def compose_attempt_prompt(request: AttemptRequest) -> str:
    """The prompt for one attempt: objective + acceptance + strategy + evidence.

    Attempt 0 is the objective as the user stated it, plus the acceptance
    conditions — nothing else. The conditions are not an extra: an agent
    cannot converge on a target it has not been told, and the criteria are
    what "done" means for this objective. They are the *harness's*
    conditions, so stating them hands over no authority; the agent still
    cannot write the evidence that satisfies them.

    Later attempts append the directive the chosen rung implies and the
    harness's own measurements, so the model is told what is *observably*
    still wrong rather than being asked to try harder.
    """
    parts = [request.objective.strip()]
    if request.criteria:
        parts.append("")
        parts.append("Acceptance conditions (measured by the harness after "
                     "this attempt — not by you):")
        parts.extend(f"  - {condition}" for condition in request.criteria)
    if request.attempt > 0:
        parts.append("")
        parts.append(f"--- attempt {request.attempt + 1} ---")
        if request.directive:
            parts.append(request.directive)
        if request.evidence:
            parts.append("")
            parts.append("Measured evidence for the current state "
                         "(produced by the harness, not by you):")
            parts.extend(f"  - {line}" for line in request.evidence)
    return "\n".join(parts)


def workspace_fingerprint(workspace: str) -> dict[str, tuple[int, float]]:
    """Cheap `rel_path -> (size, mtime)` map, bounded.

    Used only to report which files an attempt touched. Never hashed: the
    acceptance decision comes from the probe, not from here.
    """
    root = Path(workspace)
    out: dict[str, tuple[int, float]] = {}
    try:
        for i, path in enumerate(sorted(root.rglob("*"))):
            if i >= MAX_TRACKED_FILES:
                break
            if not path.is_file():
                continue
            parts = path.relative_to(root).parts
            if any(p in (".git", "__pycache__", ".pytest_cache", ".wisp",
                         "node_modules", ".venv") for p in parts):
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            out[str(path.relative_to(root))] = (st.st_size, st.st_mtime)
    except OSError:
        pass
    return out


def changed_files(before: dict[str, tuple[int, float]],
                  after: dict[str, tuple[int, float]]) -> tuple[str, ...]:
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    return tuple(sorted(changed))


async def converge_on_objective(
    objective_text: str,
    workspace: str,
    *,
    model: str | None = None,
    permission_mode: str | None = None,
    max_attempts: int = 3,
    criteria: tuple[Any, ...] | None = None,
    specs: tuple[MeasureSpec, ...] | None = None,
    allow_rollback: bool = False,
    journal_path: str | Path | None = None,
    resume: bool = False,
    root: Any = None,
    on_attempt: Any = None,
) -> ConvergenceResult:
    """Run an objective to convergence, or to an honest terminal state.

    Returns a `ConvergenceResult` whose `converged` property is True only
    when every required criterion had valid, harness-produced evidence.

    `permission_mode` is the caller's choice and defaults to the configured
    value (`auto_edit`, in which `run_bash` is blocked). Acceptance does not
    depend on it — the harness measures, not the agent — but an agent that
    cannot run the project's tests cannot check its own work.
    """
    from wisp.composition import CompositionRoot
    from wisp.config import WispConfig

    config = WispConfig()
    if model:
        config = config.replace(model=model)
    if workspace:
        config = config.replace(workspace=workspace)
    if permission_mode:
        config = config.replace(permission_mode=permission_mode)

    # Acceptance criteria are derived by the HOST when the caller does not
    # supply them. A model-declared criterion would let the judged write the
    # exam, which is the false-success shape this whole layer exists to stop.
    #
    # The baseline is measured BEFORE any attempt, so the command criteria
    # can be baseline-relative: on a repository whose suite is already red,
    # "make it exit 0" is a requirement nobody stated, and requiring it
    # would make every objective end in exhaustion. `derive_acceptance` runs
    # twice on purpose — the spec list is baseline-independent, so the first
    # call produces it and the second closes the criteria over the baseline.
    #
    # It is also the `before` that attempt 0's PROGRESS is measured against.
    # That is why it is never re-measured on a resume: the workspace has
    # already moved, so a fresh measurement would be a baseline of the mutated
    # state — a different exam for attempt N than attempt 0 was given.
    resumed_baseline = (read_journal_baseline(journal_path) if resume else None)
    criteria_derived = criteria is None
    derivation = None
    if criteria_derived:
        strict = _strict_derivation_enabled()
        _, derived_specs = explain_acceptance(objective_text, workspace,
                                              strict=strict)
        specs = derived_specs if specs is None else specs
        probe = CommandProbe(specs)
        baseline = resumed_baseline or probe.measure(workspace)
        derivation = explain_acceptance(objective_text, workspace,
                                        baseline=baseline, strict=strict)
        criteria = derivation.criteria
        logger.info(
            "criteria derivation (strict=%s): %s", strict,
            ", ".join(f"{cid}={reason}"
                      + (f" on {span!r}" if span else "")
                      for cid, reason, span in derivation.reasons) or "none")
    else:
        probe = CommandProbe(specs or ())
        baseline = resumed_baseline or (probe.measure(workspace)
                                       if specs else None)
    objective = Objective(
        goal=objective_text, workspace=workspace,
        criteria=tuple(criteria or ()), max_attempts=max_attempts,
        allow_rollback=allow_rollback,
        derivation=derivation.reasons if criteria_derived else (),
    )

    own_root = root is None
    if own_root:
        root = CompositionRoot(config)
        root.start()

    try:
        async def run_turn(request: AttemptRequest) -> TurnObservation:
            session = await root.runtime.get_or_create_session(
                # A fresh session per attempt: the transcript must not carry
                # the previous attempt's failed reasoning forward.
                session_id=f"converge-{uuid.uuid4().hex[:10]}",
                model=config.model,
                workspace=config.workspace,
            )
            before = workspace_fingerprint(workspace)
            events: list[Any] = []
            started = time.monotonic()
            async for event in root.runtime.run_turn(
                    session, compose_attempt_prompt(request)):
                events.append(event)
            observation = observe_turn(events)
            after = workspace_fingerprint(workspace)
            observation = TurnObservation(
                turn_succeeded=observation.turn_succeeded,
                terminal_outcome=observation.terminal_outcome,
                failure_code=observation.failure_code,
                failure_message=observation.failure_message,
                failure_recoverable=observation.failure_recoverable,
                changed_files=changed_files(before, after),
                tool_calls=observation.tool_calls,
                session_id=str(session.get("id", "")),
            )
            if on_attempt is not None:
                try:
                    on_attempt(request, observation,
                               round(time.monotonic() - started, 1))
                except Exception:
                    logger.debug("on_attempt callback failed", exc_info=True)
            return observation

        controller = ConvergenceController(
            run_turn=run_turn, probe=probe, max_attempts=max_attempts,
            allow_rollback=allow_rollback,
            snapshot=WorkspaceSnapshot() if allow_rollback else None,
            journal_path=journal_path,
            baseline=baseline,
        )
        return await controller.converge(objective, resume=resume)
    finally:
        if own_root:
            try:
                root.shutdown()
            except Exception:
                logger.debug("root shutdown failed", exc_info=True)
