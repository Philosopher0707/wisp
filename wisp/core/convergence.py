"""Objective-level convergence control (NEXT mission).

**The gap this module closes.** Everything below the turn was already
semantically closed: a turn has one terminal-outcome authority
(ADR-0044), one acceptance verdict (`core/acceptance.py`), one goal-state
arbiter (`core/goal.py`, ADR-0035), one recovery ladder
(`core/recovery.py`, P6) and one stagnation detector (M13). What did not
exist is a component that answers the *objective*-level question:

    the turn ended.  Is the objective satisfied?  If not, what next?

Every `run_turn` caller in the repository — SDK, CLI, REPL, headless,
WebSocket, ACP, server route, TUI, the benchmark runner — dispatches
**exactly one turn** and returns. `PlanStore` persists plans that
`_build_system_prompt` never injects. `RecoveryLadder.decide()` is called
only to *record* a decision, behind a flag that defaults OFF. So the
agent's convergence rested entirely on one turn's internal iteration
budget, and a turn that failed simply ended.

**What this module is not.** It is deliberately *not* a second authority
for any question an existing authority already answers. It owns exactly
one new thing — *the loop* — and it consumes everything else:

| Question | Authority consumed here |
|---|---|
| what would satisfy the objective? | `derive_acceptance()` (new; host-owned, deterministic) |
| is it satisfied? | `acceptance.evaluate()` |
| what is the goal state? | `goal.derive_goal_state()` |
| what kind of failure is this? | `recovery.classify_failure()` |
| what may be tried next? | `RecoveryLadder.decide()` + `LEGAL_RUNGS`/`FORBIDDEN_RUNGS` |
| has progress stopped? | the controller *observes* it; `classify_failure(stagnation=…)` *names* it |

**Independence (L1/L2).** Acceptance evidence is produced by the harness
(``CommandProbe`` / ``SymbolProbe``), never by the model and never by the
turn's own bookkeeping. The model's prose — *"Done, everything works."* —
is not an input to any decision in this module. That is what makes
`GOAL_MET` here mean "the repository says so", not "the model said so".

**Terminal honesty.** Exhaustion is a *state*, not a hang and not a
success. When no legal rung remains, the ladder escalates and this module
returns `ESCALATED_TO_HUMAN`; when attempts run out with criteria unmet it
returns `GOAL_STAGNATED` or `GOAL_UNVERIFIED` — never `GOAL_MET`.

**Reachability.** A capability with no caller is a documented gap, not a
capability (Phase 0 found eight complete, tested, unreachable subsystems).
This module is wired by `wisp/autonomous.py`, which builds the real
`run_turn` from the runtime; and it is exercised by the benchmark suite.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Protocol

from wisp.core.acceptance import (
    AcceptanceCriteria,
    CriterionKind,
    Evidence,
    Verdict,
    content_digest,
    evaluate,
)
from wisp.core.goal import (
    GoalState,
    TerminalOutcome,
    derive_goal_state,
    terminal_outcome_from_evidence,
)
from wisp.core.progress import (
    ProgressVerdict,
    evaluate_progress,
)
from wisp.core.recovery import (
    FailureClass,
    HumanIntervention,
    RecoveryDecision,
    RecoveryLadder,
    RecoveryRung,
    classify_failure,
    classify_failure_signal,
)

logger = logging.getLogger(__name__)


# ── The turn seam ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class TurnObservation:
    """What one turn is observed to have done. Facts only, never a verdict.

    Deliberately narrow: the controller must not be able to re-derive the
    turn's outcome, because that is ADR-0044's single predicate and a second
    derivation is exactly the defect that ADR removed.
    """

    turn_succeeded: bool = False
    terminal_outcome: str = TerminalOutcome.INCOMPLETE.value
    failure_code: str | None = None
    failure_message: str = ""
    #: The engine's own "this was not fatal" signal, carried verbatim so
    #: `classify_failure_signal` sees what the runtime actually said rather
    #: than a guess reconstructed here.
    failure_recoverable: bool = False
    changed_files: tuple[str, ...] = ()
    tool_calls: int = 0
    #: Which session produced this attempt. Provenance only — nothing decides
    #: on it — but a trajectory cannot be reviewed without it, and "attempt 1
    #: was a fresh session, not the same conversation continued" is exactly
    #: the claim a recovery experiment has to be able to check.
    session_id: str = ""


@dataclass(frozen=True)
class AttemptRequest:
    """Everything a caller needs to run one attempt. Data, not authority."""

    objective: str
    attempt: int
    rung: str                      # "INITIAL", or a `RecoveryRung` member name
    directive: str                 # the strategy instruction for this attempt
    evidence: tuple[str, ...] = ()
    #: The acceptance conditions, stated up front on EVERY attempt including
    #: the first. An agent cannot converge on a target it has not been told:
    #: the criteria are what "done" means here, and withholding them until
    #: after a failure makes the first attempt a guess. They are the
    #: *harness's* conditions, so stating them hands over no authority — the
    #: agent still cannot write the evidence.
    criteria: tuple[str, ...] = ()
    fresh_session: bool = True


RunTurn = Callable[[AttemptRequest], Awaitable[TurnObservation]]


# ── The objective ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Objective:
    """A user objective, with the criteria that would demonstrate it.

    `criteria` is REQUIRED to be non-empty for a `GOAL_MET` to be reachable
    at all: `acceptance.evaluate` returns `INCONCLUSIVE` when no required
    criterion is declared, and this module passes that through rather than
    papering over it. A task with nothing to check has not been verified —
    it has been un-examined.
    """

    goal: str
    workspace: str
    criteria: tuple[AcceptanceCriteria, ...] = ()
    #: `0` means "inherit the controller's budget". A concrete default here
    #: would silently shadow `ConvergenceController(max_attempts=…)` — which
    #: is how a bound stops being a bound.
    max_attempts: int = 0
    allow_rollback: bool = False
    #: ADR-0048 R3 — the criteria derivation's reasoning, `(criteria_id, reason,
    #: matched_span)` per command spec. Journalled once, beside the baseline, so
    #: the host's answer to *"does this objective require a green suite?"* is
    #: reviewable instead of invisible. Read by nothing on the decision path.
    derivation: tuple[tuple[str, str, str], ...] = ()


# ── Measurement specs (host-owned, deterministic) ───────────────────────


@dataclass(frozen=True)
class CommandSpec:
    """Run `argv` in the workspace; the criterion is the exit status."""

    criteria_id: str
    argv: tuple[str, ...]
    description: str = ""
    #: Require the command to have *collected* something, so a vacuous green
    #: (0 tests, 0 failures) cannot count as evidence. Mirrors the floor
    #: guard's `_run_tests_is_evidence` rule, which exists for the same
    #: reason: `run_bash` blocked in auto_edit could otherwise "verify"
    #: without executing a single test.
    require_collected: bool = False
    timeout_s: float = 180.0
    #: Globs naming the files this command's verdict DEPENDS ON — the tests,
    #: the fixtures, the test configuration. They are the command's *inputs*,
    #: and the agent can usually write them, so `exit 0` is not by itself
    #: evidence that the objective was met: editing the contract satisfies it.
    #:
    #: This is not hypothetical. A live run of the negative experiment had an
    #: agent rewrite a read-only pinned test — changing its assertion to
    #: something satisfiable inside the workspace — and the suite went green.
    #: Falsification F2, demonstrated end to end. `inputs` is what makes the
    #: measurement tamper-evident: `verify:cmdN:inputs_unchanged` is a
    #: required criterion, and a moved digest fails it.
    inputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class SymbolSpec:
    """A named definition must exist in a named file."""

    criteria_id: str
    path: str
    symbol: str
    description: str = ""


MeasureSpec = CommandSpec | SymbolSpec


# ── The probe ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Measurement:
    """One measurement pass: the facts, the evidence, and a readable digest.

    `lines` is what the *next* attempt is told. It carries the failing
    facts, not the transcript — artifact-oriented context flow, not a
    copied conversation.
    """

    observations: dict[str, Any] = field(default_factory=dict)
    evidence: tuple[Evidence, ...] = ()
    lines: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        """Stable digest of the measured facts — the stagnation witness.

        Two consecutive attempts whose measurements digest identically made
        no measurable progress, which is the observation the controller
        reports to `classify_failure(stagnation=…)`.

        **Over the state-bearing fields only.** `output_tail` is excluded, and
        the reason is measured rather than aesthetic: it ends with the command's
        *elapsed time* (`"3 failed in 0.04s"`), so hashing the whole payload made
        the witness a function of **when it was taken** rather than of **what was
        there**. Two probes of the same unchanged workspace produced different
        digests — which made the stagnation predicate a coin flip (a genuinely
        stagnant run could classify as `IMPLEMENTATION` and take `REPAIR` rather
        than `GLOBAL_REPLAN`) and broke ADR-0046 R10's replay determinism. A
        witness must be a function of the state.
        """
        return content_digest({criteria_id: _witness_payload(payload)
                               for criteria_id, payload
                               in self.observations.items()})

    def to_dict(self) -> dict[str, Any]:
        """The durable form: the facts and the readable lines.

        `evidence` is deliberately not serialized. It is re-produced by the
        probe on every attempt, and the only consumer that needs it —
        `acceptance.evaluate` — is given the fresh one. Journaling it would
        grow the journal without adding a fact that anything reads.
        """
        return {"observations": dict(self.observations),
                "lines": list(self.lines)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Measurement":
        return cls(observations=dict(d.get("observations") or {}),
                   lines=tuple(d.get("lines") or ()))


class Probe(Protocol):
    """Produces acceptance evidence. Injectable so tests run no subprocess."""

    def measure(self, workspace: str) -> Measurement: ...


class CommandProbe:
    """Runs each spec and records evidence with an honest producer name.

    The producer is `convergence.command_probe`, not the agent and not the
    turn's guard. That is the whole point: the record must be able to say
    *who measured*, or it cannot establish independence.
    """

    PRODUCER = "convergence.command_probe"

    def __init__(self, specs: Iterable[MeasureSpec]):
        self._specs = tuple(specs)

    @property
    def specs(self) -> tuple[MeasureSpec, ...]:
        return self._specs

    def measure(self, workspace: str) -> Measurement:
        observations: dict[str, Any] = {}
        evidence: list[Evidence] = []
        lines: list[str] = []

        for spec in self._specs:
            if isinstance(spec, CommandSpec):
                payload, line = self._run_command(spec, workspace)
                # A command spec backs THREE criteria — the absolute one, the
                # no-regression one, and the integrity one. Each needs its own
                # evidence record or it would be `INCONCLUSIVE` for lack of
                # evidence rather than for lack of success.
                criteria_ids = (spec.criteria_id,
                                f"{spec.criteria_id}:no_regression",
                                f"{spec.criteria_id}:inputs_unchanged")
            else:
                payload, line = self._check_symbol(spec, workspace)
                criteria_ids = (spec.criteria_id,)

            observations[spec.criteria_id] = payload
            lines.append(line)
            for criteria_id in criteria_ids:
                evidence.append(Evidence(
                    evidence_id=f"{criteria_id}:{witness_digest(payload)[:16]}",
                    criteria_id=criteria_id,
                    producer=self.PRODUCER,
                    kind=CriterionKind.DETERMINISTIC,
                    content_hash=witness_digest(payload),
                    observations=tuple(
                        f"{k}={v}" for k, v in sorted(payload.items())),
                    metadata={"spec": type(spec).__name__},
                ))

        return Measurement(observations=observations,
                           evidence=tuple(evidence), lines=tuple(lines))

    # -- command ---------------------------------------------------------

    def _run_command(self, spec: CommandSpec, workspace: str) -> tuple[dict[str, Any], str]:
        try:
            proc = subprocess.run(
                list(spec.argv), cwd=workspace, capture_output=True,
                text=True, timeout=spec.timeout_s,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            payload = {
                "exit": proc.returncode,
                "collected": _collected_from_output(out),
                "failed": _failed_from_output(out),
                "output_tail": out[-400:],
            }
            if spec.inputs:
                digest, names = _inputs_digest(workspace, spec.inputs)
                payload["inputs_digest"] = digest
                payload["inputs_files"] = len(names)
            verdict = "exit 0" if proc.returncode == 0 else f"exit {proc.returncode}"
            if spec.require_collected:
                verdict += f", collected {payload['collected']}"
            if payload["failed"]:
                verdict += f", {payload['failed']} failed"
            return payload, f"{spec.criteria_id}: {verdict}"
        except subprocess.TimeoutExpired:
            payload = {"exit": None, "collected": 0, "failed": 0,
                       "inputs_digest": "", "inputs_files": 0,
                       "output_tail": f"timed out after {spec.timeout_s}s"}
            return payload, f"{spec.criteria_id}: timed out"
        except (OSError, ValueError) as exc:
            payload = {"exit": None, "collected": 0, "failed": 0,
                       "inputs_digest": "", "inputs_files": 0,
                       "output_tail": str(exc)[:200]}
            return payload, f"{spec.criteria_id}: not runnable ({exc})"

    # -- symbol ----------------------------------------------------------

    def _check_symbol(self, spec: SymbolSpec, workspace: str) -> tuple[dict[str, Any], str]:
        path = Path(workspace) / spec.path
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ({"defined": False, "file_present": False},
                    f"{spec.criteria_id}: {spec.path} unreadable")
        pattern = re.compile(
            rf"^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+{re.escape(spec.symbol)}\b",
            re.MULTILINE)
        defined = bool(pattern.search(text))
        return ({"defined": defined, "file_present": True},
                f"{spec.criteria_id}: {'defined' if defined else 'MISSING'} "
                f"in {spec.path}")


#: `pytest -q` prints "3 passed"; unittest prints "Ran 3 tests". Either is
#: enough to distinguish a real green run from a vacuous one.
_COLLECTED_PATTERNS = (
    re.compile(r"(\d+)\s+passed"),
    re.compile(r"Ran\s+(\d+)\s+test"),
)

#: "2 failed", "1 error", "Errors: 2" — the three shapes pytest/unittest use.
_FAILED_PATTERNS = (
    re.compile(r"(\d+)\s+failed"),
    re.compile(r"(\d+)\s+error"),
    re.compile(r"Errors:\s*(\d+)"),
    re.compile(r"Failed:\s*(\d+)"),
)


def _collected_from_output(text: str) -> int:
    total = 0
    for pat in _COLLECTED_PATTERNS:
        for m in pat.finditer(text or ""):
            total = max(total, int(m.group(1)))
    return total


def _failed_from_output(text: str) -> int:
    """How many tests failed. Used ONLY for the baseline-relative rule."""
    total = 0
    for pat in _FAILED_PATTERNS:
        for m in pat.finditer(text or ""):
            total = max(total, int(m.group(1)))
    return total


def _inputs_digest(workspace: str, patterns: Iterable[str]) -> tuple[str, tuple[str, ...]]:
    """A content digest of the files a verification command's verdict rests on.

    Paths are sorted so the digest is stable, and each file is hashed by
    content so a touched-but-unchanged file does not count as tampering.
    Returns `(digest, sorted relative paths)`; an unreadable file contributes
    its name with an empty hash rather than being skipped — silently dropping
    an input would make the digest *more* stable exactly when the workspace
    is least trustworthy.
    """
    root = Path(workspace)
    found: dict[str, str] = {}
    for pattern in patterns:
        try:
            matches = list(root.glob(pattern))
        except (OSError, ValueError):
            continue
        for match in matches:
            try:
                if match.is_dir():
                    files = [f for f in match.rglob("*") if f.is_file()]
                elif match.is_file():
                    files = [match]
                else:
                    continue
            except OSError:
                continue
            for f in files:
                if "__pycache__" in f.parts:
                    continue
                try:
                    rel = str(f.relative_to(root))
                except ValueError:
                    continue
                try:
                    found[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
                except OSError:
                    found[rel] = ""
    names = tuple(sorted(found))
    digest = content_digest({name: found[name] for name in names})
    return digest, names


# ── Criteria builders ───────────────────────────────────────────────────


def command_succeeds(spec: CommandSpec, baseline_payload: dict[str, Any] | None = None,
                     *, required: bool = True) -> AcceptanceCriteria:
    """DETERMINISTIC: the command exits 0 (and collected, if required).

    The check follows the shape `verification.floor_guard_criteria` already
    established: a criterion that **cannot be evaluated** must not report
    `FAIL`. Absence of a measurement is not evidence of failure — it is
    absence of evidence, and `evaluate`'s rule 3 turns it into
    `INCONCLUSIVE`. Reporting `FAIL` there would collapse the two, which is
    the collapse `Verdict` exists to prevent.

    A measurement that *exists* and says the command did not succeed — a
    non-zero exit, a timeout, an unrunnable command — IS a failure.

    `baseline_payload` makes the criterion **absolute or advisory** rather
    than relative: on a repository whose verification command is already red,
    "make it exit 0" is a requirement nobody stated, so it is recorded as an
    advisory criterion (`required=False`) and the required one becomes
    `command_no_regression`. See `criteria_for` for the rule.
    """

    def _check(payloads: dict[str, Any]) -> bool:
        payload = payloads.get(spec.criteria_id)
        if payload is None:
            return True                      # not measured → rule 3 decides
        if payload.get("exit") != 0:         # includes `None`: tried, unusable
            return False
        if spec.require_collected and int(payload.get("collected") or 0) < 1:
            return False
        return True

    return AcceptanceCriteria(
        criteria_id=spec.criteria_id,
        description=spec.description or f"{' '.join(spec.argv)} exits 0",
        kind=CriterionKind.DETERMINISTIC,
        required=required,
        check=_check,
    )


def command_no_regression(spec: CommandSpec,
                          baseline_payload: dict[str, Any]) -> AcceptanceCriteria:
    """DETERMINISTIC: the command is no worse than it was before the work.

    This is the criterion that makes a *red* baseline usable. Without it, a
    derived "exit 0" requirement on a repository whose suite is already
    failing can never be satisfied, so every objective ends in exhaustion —
    honest, and useless. With it, the requirement is the one a user actually
    holds an agent to on such a repository: **do not make it worse.**

    Subsumed by `command_succeeds` when the baseline is green (a new failure
    raises `failed` above 0), so it costs nothing there.
    """

    criteria_id = f"{spec.criteria_id}:no_regression"
    baseline_failed = int(baseline_payload.get("failed") or 0)

    def _check(payloads: dict[str, Any]) -> bool:
        payload = payloads.get(spec.criteria_id)
        if payload is None:
            return True
        if payload.get("exit") == 0:
            return True
        return int(payload.get("failed") or 0) <= baseline_failed

    return AcceptanceCriteria(
        criteria_id=criteria_id,
        description=(f"{' '.join(spec.argv)} reports no more failures than "
                     f"the baseline ({baseline_failed})"),
        kind=CriterionKind.DETERMINISTIC,
        required=True,
        check=_check,
    )


def command_inputs_unchanged(spec: CommandSpec,
                             baseline_payload: dict[str, Any]) -> AcceptanceCriteria:
    """DETERMINISTIC: the command's inputs are the ones that failed.

    A green suite proves the objective only if it is the SAME suite. The
    agent can write the tests, so `exit 0` reached by relaxing the contract
    is not evidence — and a live run did exactly that: it rewrote a read-only
    pinned test so its assertion became satisfiable inside the workspace.

    This criterion closes that door with the harness's own observation: the
    digest of the declared inputs, taken at the baseline and at every
    measurement. A moved digest fails, and the failure is *named*, so the
    recovery has something to act on rather than a silent pass.
    """

    criteria_id = f"{spec.criteria_id}:inputs_unchanged"
    baseline_digest = str(baseline_payload.get("inputs_digest") or "")

    def _check(payloads: dict[str, Any]) -> bool:
        payload = payloads.get(spec.criteria_id)
        if payload is None or not spec.inputs or not baseline_digest:
            # Not measured, or nothing declared to protect: defer to the
            # evidence rule rather than inventing a verdict.
            return True
        return str(payload.get("inputs_digest") or "") == baseline_digest

    return AcceptanceCriteria(
        criteria_id=criteria_id,
        description=(f"the inputs of `{' '.join(spec.argv)}` are unchanged "
                     f"from the baseline ({', '.join(spec.inputs)})"),
        kind=CriterionKind.DETERMINISTIC,
        required=True,
        check=_check,
    )


def symbol_defined(spec: SymbolSpec) -> AcceptanceCriteria:
    """DETERMINISTIC: the file defines the named symbol.

    Same rule as above: unmeasured is not failed. The probe always produces
    a payload for a symbol spec (including "the file is gone"), so in
    practice a missing payload here means the probe did not run at all.
    """

    def _check(payloads: dict[str, Any]) -> bool:
        payload = payloads.get(spec.criteria_id)
        if payload is None:
            return True
        return bool(payload.get("defined"))

    return AcceptanceCriteria(
        criteria_id=spec.criteria_id,
        description=spec.description
        or f"{spec.path} defines {spec.symbol}",
        kind=CriterionKind.DETERMINISTIC,
        required=True,
        check=_check,
    )


def criteria_for(specs: Iterable[MeasureSpec], *,
                 baseline: "Measurement | None" = None,
                 promote_absolute: bool = False) -> tuple[AcceptanceCriteria, ...]:
    """The criteria implied by a spec list.

    The rule for a command spec, in one place:

    | Baseline | `verify:cmdN` | `verify:cmdN:no_regression` | `verify:cmdN:inputs_unchanged` |
    |---|---|---|---|
    | green (or unknown) | **required** — exit 0 | required (subsumed) | required when inputs are declared |
    | red | **advisory** — recorded, does not gate | **required** | required when inputs are declared |

    `promote_absolute=True` forces the absolute criterion to be required even
    on a red baseline, and is passed when the objective explicitly asks for
    the failure to be fixed. That is the one case where "make it pass" is a
    requirement the user actually stated.
    """
    out: list[AcceptanceCriteria] = []
    for spec in specs:
        if isinstance(spec, SymbolSpec):
            out.append(symbol_defined(spec))
            continue
        base = (baseline.observations.get(spec.criteria_id)
                if baseline is not None else None)
        green = bool(base) and base.get("exit") == 0
        out.append(command_succeeds(
            spec, base,
            required=(base is None or green or promote_absolute)))
        if base is not None:
            out.append(command_no_regression(spec, base))
            if spec.inputs:
                out.append(command_inputs_unchanged(spec, base))
    return tuple(out)


# ── Objective → acceptance derivation (host-owned) ──────────────────────

class DerivationReason(StrEnum):
    """What the host concluded about the objective's *stated* requirements.

    ADR-0048 R1. The three inferred outcomes exist because two were not enough: the
    derivation's input is prose, and a closed grammar over prose has a third
    answer besides "the requirement is stated" and "it is not" — **"I cannot
    tell"**. Collapsing that into "it is not" is a false success (a no-op
    satisfies guards-only criteria); collapsing it into "it is stated" is a
    false failure (a requirement the user never gave). Neither is a decision
    the host is entitled to make on the objective's behalf.

    ADR-0050 R5 adds a **fourth** outcome, `DECLARED`, which is not inferred at
    all. It **precedes** the three above: when the objective carries a valid
    declaration there is nothing to infer, so the prose grammar is not consulted.
    """

    #: The objective states the requirement in words the grammar recognises.
    STATED = "stated"
    #: The objective is silent about the command, but it states *something else*
    #: machine-checkable (a named symbol in a named file). Silence here is
    #: informative: the objective expressed its requirement, and a green suite
    #: is not part of it. Guards-only is then an honest no-regression
    #: objective (ADR-0047 R5).
    UNSTATED = "unstated"
    #: The objective is silent **and** names nothing else checkable. The host has
    #: no basis for either answer, so it records that fact and — under strict
    #: derivation — declines to complete the objective.
    UNDETERMINED = "undetermined"
    #: The objective **declared** its acceptance conditions (ADR-0050). Not an
    #: inference: the host validated the declaration against the measurable
    #: surface and used it. The prose grammar is not consulted.
    DECLARED = "declared"


class CriteriaDeclarationRejected(Exception):
    """A declaration was present and could not be used (ADR-0050 R4).

    Raised rather than downgraded, and that is the whole point: ADR-0048 R6 states
    that a **silent downgrade is MODE A** — the caller believes the criteria they
    declared are being measured while the host measures something else. So a
    rejected declaration stops the derivation; it never falls back to the prose
    grammar.

    `reason` names which of the five rejected shapes fired, `line` is the
    offending source line (empty when the fault is structural), and the message
    is written for the caller rather than for a log.
    """

    def __init__(self, reason: str, detail: str, *, line: str = "") -> None:
        self.reason = reason
        self.detail = detail
        self.line = line
        where = f" at line {line!r}" if line else ""
        super().__init__(
            f"criteria declaration rejected ({reason}){where}: {detail}. The "
            f"declaration is NOT ignored and the prose grammar is NOT used — "
            f"fix the declaration or remove the block to fall back to the prose "
            f"derivation.")


@dataclass(frozen=True)
class CriteriaDeclaration:
    """A parsed, validated declaration (ADR-0050 R2/R3).

    `specs` are the `MeasureSpec`s the declaration names; `entries` are the raw
    `(kind, spec)` pairs, kept so the record can show what the caller wrote
    rather than only what the host made of it.
    """

    specs: tuple[MeasureSpec, ...] = ()
    entries: tuple[tuple[str, str], ...] = ()


#: The declaration's delimiters (ADR-0050 R1). The block must be **at the head**
#: of the objective: an objective that discusses declarations must not
#: accidentally carry one.
DECLARATION_OPEN = "--- criteria ---"
DECLARATION_CLOSE = "--- /criteria ---"

#: The closed grammar's kinds (ADR-0050 R2). Exactly two, and an unknown kind is
#: rejected rather than ignored.
DECLARATION_KINDS = ("command_succeeds", "symbol_defined")


def _resolvable_argv(argv: tuple[str, ...], workspace: str) -> bool:
    """True when `argv[0]` resolves — workspace-relative, or on PATH.

    ADR-0050 R3: the host validates **runnability, never outcome**. Validating
    that the command will succeed would be the host pre-judging the exam.
    """
    if not argv:
        return False
    program = argv[0]
    candidate = Path(workspace) / program
    try:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return True
    except OSError:
        pass
    return shutil.which(program) is not None


def _resolvable_symbol(path: str, symbol: str, workspace: str) -> tuple[bool, str]:
    """True when `path::symbol` is measurable. Returns `(ok, why_not)`.

    Inside the workspace, the file exists, and the symbol is a Python identifier —
    ADR-0050 R3. The containment check is the same fail-closed rule the rest of
    the tree uses: a declaration may not name a file outside the workspace.
    """
    if not symbol.isidentifier():
        return False, f"{symbol!r} is not a Python identifier"
    try:
        root = Path(workspace).resolve()
        target = (root / path).resolve()
    except OSError as exc:
        return False, f"{path!r} could not be resolved ({exc})"
    if target != root and root not in target.parents:
        return False, f"{path!r} resolves outside the workspace"
    if not target.exists():
        return False, f"{path!r} does not exist in the workspace"
    return True, ""


def parse_criteria_declaration(objective: str, workspace: str) -> "CriteriaDeclaration | None":
    """Parse and validate a declaration, or return `None` when there is none.

    `None` means *the objective carries no declaration* — R7's absent case, which
    leaves the prose grammar to run unchanged. Anything else that goes wrong
    **raises** `CriteriaDeclarationRejected`; there is no third answer, because a
    third answer is the silent downgrade ADR-0048 R6 forbids.

    The grammar is closed and hand-rolled (ADR-0050, "Alternatives rejected"): a
    general YAML parser accepts sequences, nested maps, anchors and non-string
    scalars, and **every shape it accepts is a shape the host must then
    interpret.** A grammar that rejects what it does not understand cannot
    silently reinterpret.
    """
    text = (objective or "").lstrip()
    if not text.startswith(DECLARATION_OPEN):
        return None

    lines = text.splitlines()
    if lines[0].strip() != DECLARATION_OPEN:
        raise CriteriaDeclarationRejected(
            "malformed-open",
            f"the opening delimiter must be exactly {DECLARATION_OPEN!r} on its own line",
            line=lines[0])

    body: list[str] = []
    for line in lines[1:]:
        if line.strip() == DECLARATION_CLOSE:
            break
        body.append(line)
    else:
        raise CriteriaDeclarationRejected(
            "unterminated",
            f"the block opened with {DECLARATION_OPEN!r} and never closed with "
            f"{DECLARATION_CLOSE!r}")

    entries: list[tuple[str, str]] = []
    for raw in body:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        kind, sep, spec = stripped.partition(":")
        kind, spec = kind.strip(), spec.strip()
        # An EMPTY kind is a malformed line, not an unknown kind: "you wrote no kind"
        # and "you wrote a kind I do not know" are different mistakes with different
        # fixes, and collapsing them makes the message less actionable than it can be.
        if not sep or not kind or not spec:
            raise CriteriaDeclarationRejected(
                "malformed-line",
                "a declaration line must be `<kind>: <spec>` with a non-empty kind and spec",
                line=raw)
        if kind not in DECLARATION_KINDS:
            raise CriteriaDeclarationRejected(
                "unknown-kind",
                f"{kind!r} is not one of {DECLARATION_KINDS}; an unknown kind is "
                f"rejected rather than ignored",
                line=raw)
        entries.append((kind, spec))

    if not entries:
        raise CriteriaDeclarationRejected(
            "empty",
            "the block declares no criteria — an empty declaration would leave the "
            "objective unexamined while appearing to state its conditions")

    specs: list[MeasureSpec] = []
    for index, (kind, spec) in enumerate(entries):
        if kind == "command_succeeds":
            try:
                argv = tuple(shlex.split(spec))
            except ValueError as exc:
                raise CriteriaDeclarationRejected(
                    "unmeasurable-spec",
                    f"the command could not be split ({exc})", line=spec) from exc
            if not _resolvable_argv(argv, workspace):
                raise CriteriaDeclarationRejected(
                    "unmeasurable-spec",
                    f"`{spec}` is not runnable here — argv[0] {argv[0] if argv else ''!r} "
                    f"resolves neither inside the workspace nor on PATH",
                    line=spec)
            specs.append(CommandSpec(
                criteria_id=f"declared:cmd{index}",
                argv=argv,
                description=f"`{spec}` exits 0 (declared)",
                require_collected=True,
                inputs=("tests", "test_*.py", "*_test.py", "conftest.py",
                        "pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini"),
            ))
        else:  # symbol_defined
            path, sep, symbol = spec.partition("::")
            if not sep or not path.strip() or not symbol.strip():
                raise CriteriaDeclarationRejected(
                    "malformed-line",
                    "`symbol_defined` takes `<path>::<symbol>`", line=spec)
            ok, why = _resolvable_symbol(path.strip(), symbol.strip(), workspace)
            if not ok:
                raise CriteriaDeclarationRejected(
                    "unmeasurable-spec",
                    f"`{spec}` is not measurable: {why}", line=spec)
            specs.append(SymbolSpec(
                criteria_id=f"declared:symbol{index}",
                path=path.strip(),
                symbol=symbol.strip(),
                description=f"{path.strip()} defines {symbol.strip()}() (declared)",
            ))

    return CriteriaDeclaration(specs=tuple(specs), entries=tuple(entries))


@dataclass(frozen=True)
class CriteriaDerivation:
    """The criteria, plus **why** the host derived them (ADR-0048 R3).

    `reasons` is one `(criteria_id, reason, matched_span)` triple per command
    spec. The span is the objective's **own words** that produced the promotion,
    or `""` when there were none — so a reader can tell a promotion the user
    stated from an inference the host made.

    Read by nothing on the decision path: this is a record, and it is what makes
    the derivation's answer reviewable instead of invisible.
    """

    criteria: tuple[AcceptanceCriteria, ...] = ()
    specs: tuple[MeasureSpec, ...] = ()
    reasons: tuple[tuple[str, str, str], ...] = ()
    strict: bool = False
    #: The specs whose requirement the host could not determine. Non-empty only
    #: under `strict=True`, where each one also contributes a required,
    #: unevidenceable criterion — see `explain_acceptance`.
    undetermined: tuple[str, ...] = ()

    def reason_for(self, criteria_id: str) -> str:
        """The outcome for one command spec, or `""` if it has none."""
        for cid, reason, _span in self.reasons:
            if cid == criteria_id:
                return reason
        return ""

    def span_for(self, criteria_id: str) -> str:
        """The objective's own words that drove the decision, or `""`."""
        for cid, _reason, span in self.reasons:
            if cid == criteria_id:
                return span
        return ""


#: The closed grammar for "define a named symbol in a named file". It is
#: deliberately conservative: an objective it cannot parse confidently
#: yields NO criterion, and the controller then reports INCONCLUSIVE rather
#: than manufacturing a requirement the user did not state.
_DEFINE_RE = re.compile(
    r"\b(?:add|create|implement|define|write)\b[^.]*?\b"
    r"(?:function|method|class)\s+[`'\"]?([A-Za-z_][A-Za-z0-9_]*)[`'\"]?",
    re.IGNORECASE,
)
_PATH_RE = re.compile(r"[A-Za-z0-9_./-]+\.(?:py|js|ts|tsx|go|rs|java|rb)\b")

#: "fix the failing test", "make the suite pass", "get CI green". When the
#: objective states one of these, "exit 0" is a requirement the *user*
#: stated, so it is promoted from advisory to required even on a red
#: baseline. Closed and conservative: an objective that does not match
#: leaves the absolute criterion advisory.
#: Does the objective ask for the *suite* to be green, as opposed to asking for
#: something else on a repository that happens to be red? Only the former
#: promotes the absolute "exits 0" criterion from advisory to required.
#:
#: Three shapes, because the first one alone was too narrow and the narrowness
#: became consequential under ADR-0047. On a red baseline an unpromoted command
#: spec yields only *guard* criteria (`no_regression`, `inputs_unchanged`), so
#: the objective silently becomes "do not make it worse" — which is a legitimate
#: objective ("refactor without breaking anything") but the wrong one for an
#: objective that says the suite must pass. Before ADR-0047 a timeout masked the
#: difference; now a `PASS` completes the run, so the criteria are the only gate
#: and stating them at the right strength is load-bearing.
_WANTS_FIX_RE = re.compile(
    # (1) a repair verb and a suite word — "fix the failing tests"
    r"\b(fix|repair|resolve|make|get|turn)\b[^.]{0,40}?"
    r"\b(pass|passing|green|fail(?:ing|ure|ures)?|test|tests|suite|ci)\b"
    # (2) a passing verb and a suite word — "so that the tests pass"
    r"|\b(pass|passes|passing|green)\b[^.]{0,40}?\b(tests?|suite|ci)\b"
    # (3) the same, in the other order — "the suite passes"
    r"|\b(tests?|suite|ci)\b[^.]{0,20}?\b(pass|passes|passing|green)\b",
    re.IGNORECASE,
)


def derive_acceptance(goal: str, workspace: str, *,
                      baseline: "Measurement | None" = None) -> tuple[
        tuple[AcceptanceCriteria, ...], tuple[MeasureSpec, ...]]:
    """Derive host-owned acceptance criteria from an objective.

    Thin caller of `explain_acceptance(..., strict=False)`, kept at this signature
    because three callers and a wide test surface depend on it (ADR-0009 forbids
    widening a pinned internal signature to carry a new concern — ADR-0048 R4).
    Call `explain_acceptance` directly when the derivation's *reasoning* matters.

    Two sources, both machine-checkable and neither mediated by a model:

    1. **The workspace's own declared verification commands**
       (`environment.collect_environment(...).verification_commands`) — if
       the project says how to check itself, that is the criterion.
    2. **An explicitly named symbol in an explicitly named file** — matched
       by a closed grammar over the objective text.

    Anything else yields nothing. That is the honest behaviour: a criterion
    invented from prose would let the model's own words become the standard
    it is judged against.

    `baseline` is the pre-work measurement. Supplying it makes the command
    criteria baseline-relative (see `criteria_for`), which is what makes the
    derivation usable on a repository whose suite is already red. Without it
    the absolute criterion is required, which is only correct when the
    command currently succeeds.
    """
    d = explain_acceptance(goal, workspace, baseline=baseline)
    return d.criteria, d.specs


def explain_acceptance(goal: str, workspace: str, *,
                       baseline: "Measurement | None" = None,
                       strict: bool = False,
                       use_declaration: bool = False) -> "CriteriaDerivation":
    """Derive the criteria **and record what the host concluded about the objective**.

    ADR-0048. The derivation asks a question about *meaning* — "does this
    objective require a green suite?" — and answers it from the objective's own
    words. Until this function existed, that answer was **unobservable**: nothing
    recorded whether the absolute criterion had been promoted or left advisory, or
    on which words. A promotion that cannot cite the objective's own text is an
    inference the record now shows to be unfounded.

    **`use_declaration=True` consults the objective's declared criteria block
    first (ADR-0050).** A valid declaration yields reason `DECLARED` and the prose
    grammar is **not consulted** — there is nothing to infer when the objective
    has said. A malformed or unmeasurable declaration **raises**
    `CriteriaDeclarationRejected`; it never falls back, because a silent downgrade
    is MODE A. The parameter defaults `False`, so `derive_acceptance` — which
    never passes it — is unaffected (ADR-0009).

    Three inferred outcomes per command spec (ADR-0048 R1):

    | Outcome | Condition | Consequence |
    |---|---|---|
    | `STATED` | the objective states the requirement | the absolute criterion is promoted |
    | `UNSTATED` | silent, but the objective states *something else* checkable | guards-only — an honest no-regression objective (ADR-0047 R5) |
    | `UNDETERMINED` | silent, and nothing else checkable is named | the host has no basis; it may neither promote nor silently degrade |

    **Silence is not consent.** With `strict=False` (the default, and the only mode
    `derive_acceptance` uses) an `UNDETERMINED` spec behaves exactly as today: it is
    recorded and not acted on. With `strict=True` it contributes a **required
    criterion the harness cannot evidence** (ADR-0048 R2), which `evaluate`'s
    existing rule 3 — "a required criterion with no valid evidence" — turns into
    `INCONCLUSIVE`. No new verdict vocabulary and no `FAIL`: the absence of a
    determination is not a determination of failure.

    **Strict mode withholds only where the derivation actually chose.** A spec
    whose absolute criterion is required anyway — because the baseline is green,
    or because the objective stated the requirement — is untouched: the host
    decided nothing, so there is nothing to withhold. `UNDETERMINED` and
    `advisory` together are the whole blast radius, which is exactly the case
    where a no-op would otherwise pass.

    This does not make a wrong answer *right*; it makes it **visible**. Negation is
    still unhandled (ADR-0048 R7): *"do not make the tests pass"* matches the
    grammar, and the recorded span is how a reader sees it.
    """
    if use_declaration:
        declaration = parse_criteria_declaration(goal, workspace)
        if declaration is not None:
            criteria = criteria_for(declaration.specs, baseline=baseline,
                                    promote_absolute=True)
            return CriteriaDerivation(
                criteria=tuple(criteria), specs=declaration.specs,
                reasons=tuple(
                    (spec.criteria_id, DerivationReason.DECLARED.value,
                     f"{kind}: {spec_text}")
                    for spec, (kind, spec_text) in zip(declaration.specs,
                                                       declaration.entries)),
                strict=strict, undetermined=())

    specs: list[MeasureSpec] = []

    # 1. The project's own verification commands.
    try:
        from wisp.environment import collect_environment
        snap = collect_environment(workspace)
        for i, cmd in enumerate(snap.verification_commands):
            argv = tuple(cmd.split())
            if not argv:
                continue
            specs.append(CommandSpec(
                criteria_id=f"verify:cmd{i}",
                argv=argv,
                description=f"`{cmd}` exits 0",
                require_collected=True,
                # The test suite and its configuration are the command's
                # INPUTS. Declaring them is what makes "the suite is green"
                # mean "the same suite is green" — see
                # `command_inputs_unchanged` for the live falsification that
                # made this necessary.
                inputs=("tests", "test_*.py", "*_test.py", "conftest.py",
                        "pyproject.toml", "pytest.ini", "setup.cfg",
                        "tox.ini"),
            ))
    except Exception:
        logger.debug("verification-command detection failed", exc_info=True)

    # 2. An explicitly named definition in an explicitly named file.
    target = _first_existing_path(goal, workspace)
    if target:
        for m in _DEFINE_RE.finditer(goal):
            specs.append(SymbolSpec(
                criteria_id=f"symbol:{m.group(1)}",
                path=target,
                symbol=m.group(1),
                description=f"{target} defines {m.group(1)}()",
            ))

    # ── the derivation's own reasoning (ADR-0048 R3) ────────────────────
    match = _WANTS_FIX_RE.search(goal or "")
    span = match.group(0) if match else ""
    names_something_checkable = any(isinstance(s, SymbolSpec) for s in specs)
    if match:
        reason = DerivationReason.STATED
    elif names_something_checkable:
        reason = DerivationReason.UNSTATED
    else:
        reason = DerivationReason.UNDETERMINED

    criteria = criteria_for(
        specs, baseline=baseline,
        promote_absolute=(reason is DerivationReason.STATED))

    reasons: list[tuple[str, str, str]] = []
    undetermined: list[str] = []
    #: Only a spec whose absolute criterion ended up **advisory** represents an
    #: undetermined *choice*. On a green baseline `criteria_for` requires the
    #: absolute criterion anyway, so the derivation decided nothing and there is
    #: nothing for strict mode to withhold — which is what keeps the flag's blast
    #: radius to the one case that is actually MODE A.
    advisory = {c.criteria_id for c in criteria if not c.required}
    for spec in specs:
        if isinstance(spec, CommandSpec):
            reasons.append((spec.criteria_id, reason.value, span))
            if (strict and reason is DerivationReason.UNDETERMINED
                    and spec.criteria_id in advisory):
                undetermined.append(spec.criteria_id)

    if undetermined:
        # R2 — a required criterion nothing can evidence. `evaluate` rule 3
        # turns it into INCONCLUSIVE with the criterion named in
        # `unmet_criteria`; `check=None` keeps rule 2 from reading it as FAIL.
        extra = tuple(
            AcceptanceCriteria(
                criteria_id=f"{cid}:requirement_declared",
                description=(
                    f"the objective does not state whether `{cid}` must hold, and "
                    "the host will not decide that on the objective's behalf — "
                    "state the acceptance condition explicitly"),
                required=True,
                check=None,
            )
            for cid in undetermined)
        criteria = tuple(criteria) + extra

    return CriteriaDerivation(
        criteria=tuple(criteria), specs=tuple(specs),
        reasons=tuple(reasons), strict=strict,
        undetermined=tuple(undetermined))


def _first_existing_path(goal: str, workspace: str) -> str:
    for token in _PATH_RE.findall(goal or ""):
        candidate = token.strip("./")
        if not candidate or len(candidate) > 256:
            continue
        try:
            if (Path(workspace) / candidate).is_file():
                return candidate
        except OSError:
            continue
    return ""


# ── Strategy change ─────────────────────────────────────────────────────

#: One directive per rung. The rungs and their ordering are `recovery.py`'s
#: vocabulary, reused rather than reinvented — a second strategy vocabulary
#: would be the duplicated-authority defect this repository keeps removing.
#: A directive changes the *approach*, not the wording: RETRY re-runs,
#: LOCAL_REPLAN reconsiders the affected files, GLOBAL_REPLAN replaces the
#: decomposition, DIAGNOSTIC forbids editing and demands information.
_RUNG_DIRECTIVES: dict[str, str] = {
    RecoveryRung.RETRY.name: (
        "The previous attempt did not satisfy the acceptance criteria. "
        "Try again, but read the measured evidence below before acting."),
    RecoveryRung.REPAIR.name: (
        "The previous attempt left the objective unsatisfied. The measured "
        "evidence below names exactly what is still wrong. Repair that "
        "specific failure rather than re-doing the whole task."),
    RecoveryRung.ROLLBACK.name: (
        "The previous attempt's changes were reverted, and the evidence "
        "below describes the reverted state. Re-implement the objective from "
        "scratch with a different approach."),
    RecoveryRung.LOCAL_REPLAN.name: (
        "Your approach to the specific files involved was wrong — the "
        "measured evidence below contradicts it. Reconsider the approach for "
        "those files only; leave the rest of your plan intact."),
    RecoveryRung.GLOBAL_REPLAN.name: (
        "Your overall approach is wrong and repeating it will not converge. "
        "Step back, choose a materially different decomposition of the "
        "objective, and say what you are changing before you edit."),
    RecoveryRung.DIAGNOSTIC.name: (
        "Do not edit any file in this attempt. Investigate and report what "
        "is actually happening, so the next attempt has better information "
        "than the guesses that have already failed."),
    RecoveryRung.HUMAN.name: (
        "Escalating to a human; no further attempt is made."),
}

INITIAL_RUNG = "INITIAL"

#: The payload fields that identify a **state**. Everything else in a payload is
#: either prose for a human or a property of the observation rather than of the
#: workspace, and neither belongs in a witness or an identifier.
#:
#: `output_tail` is the important exclusion. It is a human-readable excerpt that
#: ends with the command's elapsed time, so including it made every witness and
#: every evidence id depend on *when* it was taken. See `Measurement.digest`.
WITNESS_FIELDS: tuple[str, ...] = (
    "exit", "collected", "failed", "defined", "file_present",
    "inputs_digest", "inputs_files",
)


def _witness_payload(payload: Any) -> Any:
    """The state-bearing projection of one measurement payload."""
    if not isinstance(payload, dict):
        return payload
    return {k: payload[k] for k in WITNESS_FIELDS if k in payload}


def witness_digest(payload: Any) -> str:
    """A content digest over a payload's state-bearing fields only.

    Used for the stagnation witness and for evidence identity, so that two
    observations of the same repository state are *the same* observation —
    which is what makes the record replayable and the stagnation predicate
    deterministic.
    """
    return content_digest(_witness_payload(payload))

#: The directives for an attempt that follows a **meaningful-progress**
#: failure. They are not a second strategy vocabulary: they are the same rungs,
#: saying the opposite thing, because the observation is the opposite one.
#:
#: The generic `REPAIR` directive says *"the previous attempt left the objective
#: unsatisfied … repair that specific failure"*, which is right when the
#: approach was wrong and actively misleading when it was working and merely ran
#: out of turn. The continuation directive says the measured thing instead: the
#: objective moved, the work is real, there is more of it than one turn holds,
#: and the existing repository state is the place to continue from.
#:
#: It is **action-first on purpose.** A first draft led with *"inspect what is
#: already implemented, work out what is still missing"* and the live runs were
#: unambiguous about the result: the continuation attempt spent its entire turn
#: inspecting and never wrote anything (3 tool calls, 0 files changed, in a
#: 100 s budget — against the failed attempt's 8 calls and 4 files). A
#: continuation that only reads is not a continuation. So the directive leads
#: with the work, bounds the inspection to what is needed, and states the three
#: failure modes it exists to prevent: **restarting** the task (which discards
#: measurable progress), **re-doing** work already done, and **rewriting the
#: tests** (the F2 tampering shape, which the integrity criterion catches but
#: which is cheaper not to invite).
_CONTINUATION_DIRECTIVES: dict[str, str] = {
    RecoveryRung.REPAIR.name: (
        "The previous attempt measurably advanced this objective — the "
        "harness's own measurement of what improved is below — but the turn "
        "ended before the objective was met. That work is already in the "
        "repository and it is correct as far as it goes. Finish the job: "
        "implement what is still missing, and nothing else. Read only what you "
        "need to see the remaining shape of the work, then write the code. "
        "Do NOT restart the task, do NOT redo work that is already done, and "
        "do NOT edit the tests — they are the measure, not the objective."),
}


def directive_for(rung: str, *, progress: ProgressVerdict | str | None = None) -> str:
    """The strategy instruction for `rung`.

    `progress` selects the continuation wording when the previous attempt
    measurably advanced the objective, and otherwise leaves the rung's own
    directive untouched — so every existing caller gets exactly what it got
    before.
    """
    if progress is not None:
        try:
            meaningful = (ProgressVerdict(progress)
                          is ProgressVerdict.MEANINGFUL_PROGRESS)
        except ValueError:
            meaningful = False
        if meaningful and rung in _CONTINUATION_DIRECTIVES:
            return _CONTINUATION_DIRECTIVES[rung]
    return _RUNG_DIRECTIVES.get(rung, "")


# ── Records ─────────────────────────────────────────────────────────────


@dataclass
class AttemptRecord:
    """One attempt, and everything needed to review it."""

    index: int
    rung: str
    directive: str
    observation: TurnObservation
    verdict: str = Verdict.INCONCLUSIVE.value
    goal_state: str = GoalState.GOAL_UNVERIFIED.value
    unmet: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    #: The measured evidence the NEXT attempt was told, as prose. Recorded so
    #: "attempt 1 received materially different information" is checkable
    #: against the record rather than asserted from the directive alone.
    evidence_lines: tuple[str, ...] = ()
    #: What THIS attempt's measurement reported. Durable because the resumed
    #: run needs it: the stagnation witness and the next attempt's evidence
    #: are both re-derived from the last record, so a restart cannot silently
    #: choose a different rung from the one the interrupted run would have.
    measurement_lines: tuple[str, ...] = ()
    #: The measurement's raw payloads. Durable for the same reason, and more
    #: strongly: `evaluate_progress` compares *payloads*, so a resumed run that
    #: kept only the rendered lines could not reproduce the progress verdict
    #: and would silently pick a different recovery. The rendered lines are a
    #: view of these; these are the fact.
    measurement_observations: dict[str, Any] = field(default_factory=dict)
    failure_class: str = ""
    measurement_digest: str = ""
    #: The objective-relative progress verdict for this attempt, and the
    #: signals that produced it. Recorded per attempt because it is an input to
    #: the recovery decision, and a decision that cannot be reviewed against
    #: its own inputs is an assertion.
    progress: str = ""
    progress_signals: tuple[str, ...] = ()
    started_at: float = field(default_factory=time.time)
    duration_s: float = 0.0

    @property
    def session_id(self) -> str:
        """The session this attempt ran in (provenance; never a decision)."""
        return self.observation.session_id

    @property
    def measurement(self) -> Measurement:
        """The durable measurement, reconstructed. Used by a resumed run."""
        return Measurement(observations=dict(self.measurement_observations),
                           lines=tuple(self.measurement_lines))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "attempt",
            "index": self.index, "rung": self.rung,
            "directive": self.directive,
            "session_id": self.observation.session_id,
            "turn_succeeded": self.observation.turn_succeeded,
            "terminal_outcome": self.observation.terminal_outcome,
            "failure_code": self.observation.failure_code,
            "failure_message": self.observation.failure_message,
            "changed_files": list(self.observation.changed_files),
            "tool_calls": self.observation.tool_calls,
            "verdict": self.verdict, "goal_state": self.goal_state,
            "unmet": list(self.unmet), "evidence_ids": list(self.evidence_ids),
            "evidence_lines": list(self.evidence_lines),
            "measurement_lines": list(self.measurement_lines),
            "measurement_observations": dict(self.measurement_observations),
            "failure_class": self.failure_class,
            "measurement_digest": self.measurement_digest,
            "progress": self.progress,
            "progress_signals": list(self.progress_signals),
            "duration_s": round(self.duration_s, 3),
            "started_at": self.started_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AttemptRecord":
        return cls(
            index=int(d["index"]), rung=d.get("rung", INITIAL_RUNG),
            directive=d.get("directive", ""),
            observation=TurnObservation(
                turn_succeeded=bool(d.get("turn_succeeded")),
                terminal_outcome=d.get("terminal_outcome",
                                       TerminalOutcome.INCOMPLETE.value),
                failure_code=d.get("failure_code"),
                failure_message=d.get("failure_message", ""),
                changed_files=tuple(d.get("changed_files") or ()),
                tool_calls=int(d.get("tool_calls") or 0),
                session_id=d.get("session_id", "")),
            verdict=d.get("verdict", Verdict.INCONCLUSIVE.value),
            goal_state=d.get("goal_state", GoalState.GOAL_UNVERIFIED.value),
            unmet=tuple(d.get("unmet") or ()),
            evidence_ids=tuple(d.get("evidence_ids") or ()),
            evidence_lines=tuple(d.get("evidence_lines") or ()),
            measurement_lines=tuple(d.get("measurement_lines") or ()),
            measurement_observations=dict(d.get("measurement_observations") or {}),
            failure_class=d.get("failure_class", ""),
            measurement_digest=d.get("measurement_digest", ""),
            progress=d.get("progress", ""),
            progress_signals=tuple(d.get("progress_signals") or ()),
            duration_s=float(d.get("duration_s") or 0.0),
            started_at=float(d.get("started_at") or 0.0),
        )


@dataclass
class ConvergenceResult:
    """The objective-level conclusion. `converged` is the only success."""

    goal_state: GoalState
    attempts: tuple[AttemptRecord, ...] = ()
    escalation: HumanIntervention | None = None
    reason: str = ""
    final_measurement: Measurement | None = None

    @property
    def converged(self) -> bool:
        return self.goal_state is GoalState.GOAL_MET

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_state": str(self.goal_state),
            "converged": self.converged,
            "reason": self.reason,
            "attempts": [a.to_dict() for a in self.attempts],
            "escalation": self.escalation.to_dict() if self.escalation else None,
        }


# ── The loop ────────────────────────────────────────────────────────────


class ConvergenceController:
    """Drives attempts until the objective is proven, or honestly is not.

    The loop is the only new mechanism. Every judgement it makes is
    delegated: `acceptance.evaluate` decides whether the criteria are met,
    `goal.derive_goal_state` names the state, `classify_failure` names the
    failure, and `RecoveryLadder.decide` chooses what may be tried next.
    """

    def __init__(
        self,
        *,
        run_turn: RunTurn,
        probe: Probe,
        ladder: RecoveryLadder | None = None,
        max_attempts: int = 3,
        allow_rollback: bool = False,
        snapshot: "WorkspaceSnapshot | None" = None,
        journal_path: str | Path | None = None,
        baseline: Measurement | None = None,
        on_record: Callable[["AttemptRecord"], None] | None = None,
    ):
        self._run_turn = run_turn
        #: Told about each attempt once it is recorded and journaled, so a caller can show progress as it happens. It only
        #: OBSERVES: it is never consulted, and an exception in it is logged and dropped (a display must not end a run).
        self._on_record = on_record
        self._probe = probe
        self._ladder = ladder if ladder is not None else RecoveryLadder()
        self._max_attempts = max(1, int(max_attempts))
        self._allow_rollback = allow_rollback
        self._snapshot = snapshot
        self._journal = Path(journal_path) if journal_path else None
        #: The pre-work measurement. `evaluate_progress` needs a `before` to
        #: compare attempt 0 against, and the pre-work state is the only honest
        #: one: comparing attempt 0 to "nothing" would make every first attempt
        #: undeterminable, and comparing it to attempt 0 itself would make it
        #: vacuous. Journaled, because on a resume the workspace has already
        #: moved and re-measuring would produce a different baseline — which
        #: would silently change both the criteria and the progress verdict.
        self._baseline = baseline
        self._journal_baseline: Measurement | None = None
        self.attempts: list[AttemptRecord] = []
        #: The rung chosen for the NEXT attempt. Set at the end of each
        #: iteration; empty on the first, which is `INITIAL_RUNG`.
        self._last_rung: str = ""
        #: The progress verdict of the LAST attempt, which the next attempt's
        #: directive depends on. Set beside `_last_rung`, re-derived from the
        #: journal on resume, so live and resumed cannot disagree.
        self._last_progress: str = ""
        #: What the last attempt measurably improved. Carried so the
        #: continuation attempt is told what it *already achieved*, not only
        #: what is still wrong: a prompt that says "the objective advanced"
        #: and then shows a measurement that still looks red is contradicting
        #: itself, and a live run showed the model reacting to that by
        #: re-inspecting instead of continuing.
        self._last_progress_signals: tuple[str, ...] = ()

    # -- public ----------------------------------------------------------

    async def converge(self, objective: Objective,
                       resume: bool = False) -> ConvergenceResult:
        """Run attempts until proven, exhausted, or escalated.

        `resume=True` reloads completed attempts from the journal and
        continues from the next index. Mutations are NOT repeated: an
        attempt that already ran is never re-run, which is what makes a
        restart after a crash safe.
        """
        if resume:
            self._load_journal()
            # A resumed run must continue with the SAME recovery strategy the
            # interrupted one had chosen. Without this, `_last_rung` is empty,
            # attempt N+1 runs with no rung and no directive — i.e. the
            # recovery is silently lost across a restart, which is precisely
            # the thing a resume is supposed to preserve. The rung is not
            # remembered; it is RE-DERIVED from the same durable facts the
            # interrupted run derived it from, so live and resumed cannot
            # disagree about it.
            self._resume_recovery()

        # The baseline is durable, and the JOURNALED one wins. On a resume the
        # workspace has already been mutated by the interrupted run, so
        # re-measuring here would produce a baseline of the *mutated* state —
        # which would change both the derived criteria and every subsequent
        # progress verdict. The interrupted run's baseline is the fact; a fresh
        # measurement of a moved workspace is not a substitute for it.
        if self._journal_baseline is not None:
            self._baseline = self._journal_baseline
        elif self._baseline is not None and not self.attempts:
            self._write_baseline(self._baseline)

        # ADR-0048 R3 — record WHY these criteria are the criteria. Written once,
        # beside the baseline, and only when the caller supplied a derivation:
        # "did the host think this objective required a green suite, and on what
        # words?" is then answerable from the journal instead of re-run. The
        # record is read by nothing on the decision path.
        if objective.derivation and not self.attempts:
            self._write_derivation(objective.derivation)

        # The baseline snapshot is taken BEFORE the first mutation so the
        # Rollback rung has something to restore to. Taken once, and only
        # when a rollback could actually be requested.
        if self._snapshot is not None and not self.attempts:
            self._snapshot.capture(objective.workspace)

        criteria = objective.criteria
        max_attempts = objective.max_attempts or self._max_attempts
        # The acceptance conditions, in the agent's words-to-be: what the
        # harness will measure, stated on every attempt. Required criteria
        # only — an advisory criterion is recorded, not demanded, and telling
        # the agent to satisfy something that cannot gate would be a lie.
        stated_criteria = tuple(
            c.description for c in criteria if c.required)
        # A resumed run continues the ORIGINAL budget; it does not restart it.
        start = len(self.attempts)
        # The stagnation witness and the evidence the NEXT attempt is shown
        # are both restored from the journal on resume. They are durable facts
        # — the digest is recorded per attempt and the lines are recorded per
        # measurement — so a resumed run detects the SAME stagnation the
        # interrupted one would have detected and shows the SAME evidence. A
        # resumed run that lost them would silently choose a different rung,
        # which is the divergence a resume exists to prevent.
        previous_lines: tuple[str, ...] = ()
        stagnation_witness = ""
        last_measurement: Measurement | None = None
        #: The measurement the NEXT attempt's progress is compared against.
        #: The baseline for attempt 0 — so a first attempt that times out
        #: mid-work is judged against the repository as it was, not against
        #: itself — and the previous attempt's measurement after that.
        previous_measurement: Measurement | None = self._baseline
        if self.attempts:
            last_record = self.attempts[-1]
            previous_lines = last_record.measurement_lines
            stagnation_witness = last_record.measurement_digest
            previous_measurement = last_record.measurement

        for attempt in range(start, max_attempts):
            rung = INITIAL_RUNG if attempt == 0 else self._last_rung
            directive = directive_for(rung, progress=self._last_progress)
            # What the previous attempt IMPROVED goes first, then what is still
            # unmet. Both are harness measurements; neither is the model's
            # prose. Showing only the second is what made a live continuation
            # attempt re-inspect instead of continue.
            evidence_lines = (self._last_progress_signals + previous_lines
                              if self._last_progress_signals else previous_lines)

            if rung == RecoveryRung.ROLLBACK.name and self._snapshot is not None:
                self._snapshot.restore(objective.workspace)

            started = time.time()
            observation = await self._run_turn(AttemptRequest(
                objective=objective.goal, attempt=attempt, rung=rung,
                directive=directive, evidence=evidence_lines,
                criteria=stated_criteria,
                fresh_session=True,
            ))

            # Measure with the harness. The model is not consulted.
            measurement = self._probe.measure(objective.workspace)
            verdict = evaluate(criteria, measurement.evidence,
                               measurement.observations)

            # Did the attempt move the OBJECTIVE? A separate question from
            # "did it fail", answered from the harness's own payloads by
            # `core/progress.py`, which reads no model text and treats a moved
            # verification input as untrustworthy rather than as progress.
            progress = evaluate_progress(
                previous_measurement.observations if previous_measurement else None,
                measurement.observations,
                changed_files=observation.changed_files,
            )

            # The observation that names stagnation: the same criteria are
            # unmet and nothing measurable changed. The controller only
            # OBSERVES this; `classify_failure` is what names it.
            #
            # Two conditions keep it from over-claiming. It requires that
            # something WAS measured (an empty measurement is identical to
            # every other empty measurement, so reading that as "no progress"
            # would claim stagnation from an absence of measurement), and it
            # requires at least one criterion to actually be unmet (with no
            # criteria the verdict is `INCONCLUSIVE` whatever the probe saw,
            # and an unexamined objective has not been shown to stagnate —
            # it has not been examined).
            repeated = (
                bool(verdict.unmet_criteria)
                and bool(measurement.observations)
                and measurement.digest == stagnation_witness
            )

            outcome = terminal_outcome_from_evidence(
                saw_done=observation.turn_succeeded,
                saw_fatal_error=(observation.terminal_outcome
                                 == TerminalOutcome.FAILED.value),
            )
            # The failure class, when the attempt produced a failure signal.
            #
            # Computed for EVERY attempt, including one whose verdict is PASS.
            # ADR-0047 makes a failed turn able to complete an objective, and
            # the record must still say the turn failed — the two facts are
            # separate, and collapsing them is the defect F60 named. A PASSING
            # attempt still gets no *recovery* input from this: `passed` gates
            # that below.
            #
            # Precedence follows P6's rule exactly — **a denial outranks
            # everything** — so an engine-reported failure is classified before
            # the stagnation observation: a second denied call must reach
            # SECURITY (whose only legal rung is HUMAN) rather than being
            # re-planned as stagnation. Reading it the other way is how the
            # no-retry rule leaks.
            signal_class: FailureClass | None = None
            if observation.failure_code or observation.failure_message:
                signal_class = classify_failure_signal(
                    observation.failure_message,
                    observation.failure_recoverable,
                    observation.failure_code,
                )

            # An authorization event is terminal for the RUN, whatever the
            # objective's verdict says. A denial means the attempt did not
            # proceed as authorized, and absorbing that into a success would
            # launder a security event — so it is excluded from completion
            # *before* the verdict is consulted, and it escalates below through
            # the ladder's own SECURITY row rather than by a rule invented here.
            authorization_event = signal_class is FailureClass.SECURITY

            # ADR-0047: completion follows the OBJECTIVE's evidence. A `PASS`
            # from the harness's own measurement means the objective is met; the
            # turn's outcome is a fact about the attempt and is no longer a
            # precondition. `turn_succeeded` is still recorded on the attempt.
            passed = verdict.verdict is Verdict.PASS and not authorization_event

            if signal_class is not None:
                failure_class: FailureClass | None = signal_class
            elif repeated:
                # The objective-level observation: the same criteria are
                # unmet and nothing measurable changed. `STAGNATION` is the
                # taxonomy's own name for "working, not progressing", and it
                # is the class whose legal rungs (GLOBAL_REPLAN, DIAGNOSTIC)
                # are precisely the strategy-changing ones — which is why
                # this is not reported as `REPEATED` (whose only legal rung
                # is DIAGNOSTIC).
                failure_class = FailureClass.STAGNATION
            else:
                failure_class = classify_failure(
                    verification_inconclusive=(
                        verdict.verdict is Verdict.INCONCLUSIVE),
                    implementation_failed=(verdict.verdict is Verdict.FAIL),
                )

            # Escalate HERE, before the goal state is derived, so `escalated` is
            # a durable fact rather than a prediction of what the ladder is
            # about to do. The early return below prevents a second escalation
            # when `_next_rung` would otherwise ask for one.
            if authorization_event:
                self._ladder.escalate(
                    FailureClass.SECURITY,
                    reason="an authorization event ended the run",
                    evidence=tuple(measurement.lines) or ("authorization event",))

            record = AttemptRecord(
                index=attempt, rung=rung, directive=directive,
                observation=observation,
                verdict=verdict.verdict.value,
                unmet=tuple(verdict.unmet_criteria),
                evidence_ids=tuple(verdict.evidence_ids),
                # What THIS attempt was shown about the previous one. Empty on
                # attempt 0, which is shown nothing because nothing has failed.
                evidence_lines=tuple(evidence_lines),
                measurement_lines=tuple(measurement.lines),
                measurement_observations=dict(measurement.observations),
                failure_class=failure_class.value if failure_class else "",
                measurement_digest=measurement.digest,
                progress=progress.verdict.value,
                progress_signals=progress.signals,
                started_at=started,
                duration_s=time.time() - started,
            )

            # The goal state is derived from the SAME facts the turn-level
            # arbiter uses, so the two levels cannot disagree about what
            # happened. ADR-0047 moved `PASS` above the fatal-error clause, so
            # `GOAL_MET` here no longer requires the turn to have succeeded —
            # but it still requires the acceptance verdict to say PASS, and
            # `escalated` still outranks everything but cancellation.
            record.goal_state = str(derive_goal_state(
                terminal_outcome=outcome,
                acceptance_verdict=verdict.verdict,
                stagnating=(failure_class is FailureClass.STAGNATION),
                turn_succeeded=observation.turn_succeeded,
                escalated=self._ladder.escalated,
            ))
            self.attempts.append(record)
            self._write_journal(record)
            if self._on_record is not None:
                try:
                    self._on_record(record)
                except Exception:  # noqa: BLE001 — an observer must never end the run
                    logger.debug("on_record callback failed", exc_info=True)

            if authorization_event:
                return ConvergenceResult(
                    goal_state=GoalState.ESCALATED_TO_HUMAN,
                    attempts=tuple(self.attempts),
                    escalation=self._ladder.escalation,
                    reason=("an authorization event ended the run — the "
                            "objective is not allowed to absorb it"),
                    final_measurement=measurement)

            if passed:
                return ConvergenceResult(
                    goal_state=GoalState.GOAL_MET, attempts=tuple(self.attempts),
                    reason=("every required criterion has valid evidence "
                            "produced by the harness"),
                    final_measurement=measurement)

            previous_lines = measurement.lines
            last_measurement = measurement
            previous_measurement = measurement
            stagnation_witness = measurement.digest
            self._last_rung = ""
            self._last_progress = ""
            self._last_progress_signals = ()

            if attempt + 1 >= max_attempts:
                break

            decision = self._next_rung(failure_class, measurement.lines,
                                       verdict.unmet_criteria,
                                       progress=progress.verdict)
            if decision is None:
                return ConvergenceResult(
                    goal_state=GoalState.ESCALATED_TO_HUMAN,
                    attempts=tuple(self.attempts),
                    escalation=self._ladder.escalation,
                    reason=self._escalation_reason(),
                    final_measurement=measurement)
            self._last_rung = decision.rung.name
            # The next attempt's directive depends on THIS attempt's progress,
            # so it is carried beside the rung rather than re-derived from the
            # record — a re-derivation would have to re-run `evaluate_progress`
            # on payloads it would first have to fetch, and the recorded verdict
            # is already the durable fact.
            self._last_progress = progress.verdict.value
            self._last_progress_signals = (progress.signals
                                           if progress.meaningful else ())

        # Exhausted. The last record's goal state is the honest answer.
        last = self.attempts[-1] if self.attempts else None
        state = (GoalState(last.goal_state) if last
                 else GoalState.GOAL_UNVERIFIED)
        if state is GoalState.GOAL_MET:          # unreachable by construction
            state = GoalState.GOAL_UNVERIFIED
        return ConvergenceResult(
            goal_state=state, attempts=tuple(self.attempts),
            escalation=self._ladder.escalation,
            reason=(f"attempt budget exhausted ({max_attempts}) with the "
                    f"objective unproven"),
            final_measurement=last_measurement)

    # -- internals -------------------------------------------------------

    def _next_rung(self, failure_class: FailureClass,
                   evidence: Iterable[str],
                   unmet: Iterable[str],
                   *,
                   progress: ProgressVerdict | str | None = None
                   ) -> RecoveryDecision | None:
        """Ask the ladder. Every rung it returns is structurally NEW.

        `RecoveryLadder.legal_rungs` excludes anything already in the
        history, so "try something meaningfully different" is enforced by
        the existing authority rather than remembered here. If the chosen
        rung cannot be executed in this configuration (ROLLBACK without a
        snapshot), the ladder is asked again — the first decision is already
        in its history, so the second cannot repeat it.

        `progress` is passed straight through: when the attempt measurably
        moved the objective, the ladder widens the class's legal set with the
        continuation rungs (`PROGRESS_CONTINUATION_RUNGS`) **and** relaxes R5
        for that case — a rung that produced measurable progress may be
        re-chosen, bounded by the `productive_continuations` budget, because the
        state it would act on is no longer the state it failed on.

        `rejected` exists because of that relaxation. Without it, a rung that
        progress had re-legalised and that is not executable here (ROLLBACK
        without a snapshot) would be chosen by every retry in this loop and the
        loop would exhaust itself instead of moving on.
        """
        evidence = tuple(evidence) or ("verdict=unknown",)
        unmet = tuple(unmet)
        rejected: list[RecoveryRung] = []
        for _ in range(len(RecoveryRung)):
            decision = self._ladder.decide(
                failure_class, evidence,
                reason=f"attempt did not satisfy: "
                       f"{', '.join(unmet) or 'no evidence'}",
                progress=progress,
                exclude=rejected)
            if decision.rung is RecoveryRung.HUMAN:
                # `decide` has already recorded the escalation — this is the
                # terminal-honesty path, not another choice to make.
                return None
            if self._rung_executable(decision.rung):
                return decision
            rejected.append(decision.rung)
            logger.info("rung %s chosen but not executable here — asking again",
                        decision.rung.name)
        return None

    def _resume_recovery(self) -> None:
        """Rebuild the ladder's history and the pending rung from the journal.

        The ladder is a state machine whose state is its `history`; a fresh
        instance starts empty, so R5 ("a rung that would repeat an
        already-failed rung is illegal") would not hold after a restart. The
        history is therefore replayed from the recorded attempts — each
        attempt i>=1 records the rung that was chosen for attempt i-1's
        failure class, so the replay is exact — and the pending rung for the
        next attempt is then chosen the same way the interrupted run would
        have chosen it.

        **The progress verdict is replayed too, and that is not optional.** The
        widening depends on it, so a resume that dropped it would choose a
        different rung from the one the interrupted run chose — the divergence
        a resume exists to prevent. It is read from the record rather than
        recomputed: `evaluate_progress` needs the *previous* measurement's
        payloads, and the record already carries the verdict they produced.

        **Nothing to resume is not an error (F62).** `resume=True` with a missing
        or empty journal used to reach `self.attempts[-1]` on an empty list and
        raise `IndexError` — so `wisp converge --resume` on a fresh run, or with
        a `--journal` that had not been written yet, crashed instead of starting.
        A resume with no durable history is simply a fresh run.
        """
        if not self.attempts:
            return
        for prev, cur in zip(self.attempts, self.attempts[1:]):
            if not prev.failure_class:
                continue
            self._ladder.decide(
                FailureClass(prev.failure_class),
                tuple(cur.evidence_lines) or ("replayed from journal",),
                reason=f"replayed decision for attempt {cur.index}",
                progress=prev.progress or None)

        last = self.attempts[-1]
        self._last_progress = last.progress
        self._last_progress_signals = (
            tuple(last.progress_signals)
            if last.progress == ProgressVerdict.MEANINGFUL_PROGRESS.value
            else ())
        if GoalState(last.goal_state) is GoalState.GOAL_MET or not last.failure_class:
            return
        decision = self._next_rung(
            FailureClass(last.failure_class),
            last.evidence_lines,
            last.unmet,
            progress=last.progress or None,
        )
        self._last_rung = decision.rung.name if decision is not None else ""

    def _rung_executable(self, rung: RecoveryRung) -> bool:
        if rung is RecoveryRung.HUMAN:
            return False                     # escalation is the fallback, not a choice
        if rung is RecoveryRung.ROLLBACK:
            return bool(self._snapshot is not None and self._allow_rollback)
        return True

    def _escalation_reason(self) -> str:
        if self._ladder.escalation is not None:
            return self._ladder.escalation.reason
        return "no executable recovery rung remained"

    # -- durability ------------------------------------------------------

    def _write_journal(self, record: AttemptRecord) -> None:
        """Append one attempt. Append-only, so a crash cannot corrupt it."""
        if self._journal is None:
            return
        self._append_line(record.to_dict())

    def _write_baseline(self, baseline: Measurement) -> None:
        """Record the pre-work measurement, once, as the journal's first line.

        Durable because a resume cannot re-measure it: the workspace has already
        moved, so a fresh measurement would be a baseline of the *mutated*
        state. Both the derived criteria and every progress verdict depend on
        the original, so the original is what must survive.
        """
        if self._journal is None:
            return
        self._append_line({"kind": "baseline",
                           "measurement": baseline.to_dict()})
        self._journal_baseline = baseline

    def _write_derivation(self, reasons: tuple[tuple[str, str, str], ...]) -> None:
        """Record the criteria derivation's reasoning, once (ADR-0048 R3).

        One line per command spec: the criteria id, the outcome, and the
        objective's **own words** that drove it. A promotion whose span is empty
        is an inference with no citation, and this line is what makes that
        visible rather than silent.
        """
        if self._journal is None:
            return
        self._append_line({
            "kind": "derivation",
            "reasons": [{"criteria_id": cid, "reason": reason, "span": span}
                        for cid, reason, span in reasons],
        })

    def _append_line(self, payload: dict[str, Any]) -> None:
        if self._journal is None:
            return
        try:
            self._journal.parent.mkdir(parents=True, exist_ok=True)
            with self._journal.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
            logger.debug("convergence journal write failed", exc_info=True)

    def _load_journal(self) -> None:
        """Reload the baseline and the completed attempts.

        Two record kinds share the file — a `baseline` header and the `attempt`
        lines — discriminated by `kind`. A line with no `kind` is a legacy
        attempt record from before the baseline existed, and is read as one, so
        an older journal still resumes.
        """
        if self._journal is None or not self._journal.exists():
            return
        try:
            text = self._journal.read_text(encoding="utf-8")
        except OSError:
            return
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                kind = payload.get("kind")
                if kind == "baseline":
                    self._journal_baseline = Measurement.from_dict(
                        payload.get("measurement") or {})
                    continue
                if kind not in (None, "attempt"):
                    # `derivation` (ADR-0048 R3) and any record kind a later version adds are not
                    # attempts. Reading one as an attempt raised KeyError, and the `break` below then
                    # dropped EVERY attempt after it: a resumed run started again at attempt 0.
                    continue
                self.attempts.append(AttemptRecord.from_dict(payload))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                # A torn write is the expected crash artifact; stop at it
                # rather than failing the resume.
                logger.debug("truncated convergence journal line ignored")
                break


def read_journal_baseline(journal_path: str | Path | None) -> Measurement | None:
    """The pre-work measurement a previous run recorded, if it recorded one.

    Public because `converge_on_objective` needs it *before* constructing the
    controller: the acceptance criteria are derived from the baseline, so a
    resumed run that re-derived them from the already-mutated workspace would
    judge attempt N against a different exam than attempt 0 was given.
    """
    if journal_path is None:
        return None
    path = Path(journal_path)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("kind") == "baseline":
            return Measurement.from_dict(payload.get("measurement") or {})
    return None


# ── Workspace snapshot (the Rollback rung's substrate) ──────────────────


class WorkspaceSnapshot:
    """A bounded, in-memory copy of a workspace's files.

    Rollback is the one rung that *destroys* work, so it is off by default
    (`Objective.allow_rollback`) and this class refuses to snapshot anything
    larger than `max_files`/`max_bytes`. A snapshot that silently truncated
    would make a restore look successful while leaving files behind, which
    is worse than refusing.
    """

    def __init__(self, max_files: int = 2000, max_bytes: int = 64 * 1024 * 1024):
        self._files: dict[str, bytes] = {}
        self._max_files = max_files
        self._max_bytes = max_bytes
        self.captured = False
        self.refused = ""

    def capture(self, workspace: str) -> bool:
        root = Path(workspace)
        total = 0
        files: dict[str, bytes] = {}
        try:
            for path in sorted(root.rglob("*")):
                if not path.is_file() or _skip(path, root):
                    continue
                if len(files) >= self._max_files:
                    self.refused = f"more than {self._max_files} files"
                    return False
                data = path.read_bytes()
                total += len(data)
                if total > self._max_bytes:
                    self.refused = f"more than {self._max_bytes} bytes"
                    return False
                files[str(path.relative_to(root))] = data
        except OSError as exc:
            self.refused = str(exc)[:200]
            return False
        self._files = files
        self.captured = True
        return True

    def restore(self, workspace: str) -> bool:
        if not self.captured:
            return False
        root = Path(workspace)
        for rel, data in self._files.items():
            target = root / rel
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists() or target.read_bytes() != data:
                    target.write_bytes(data)
            except OSError:
                logger.debug("rollback write failed for %s", rel, exc_info=True)
                return False
        return True


_SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", ".tox", ".wisp",
})


def _skip(path: Path, root: Path) -> bool:
    return any(part in _SKIP_DIRS for part in path.relative_to(root).parts)
