"""Objective-relative progress semantics (progress-aware recovery mission).

**The gap this module closes.** A turn can end for two reasons that need
*opposite* recoveries and were, until now, indistinguishable:

| Turn ended | What it means | Right answer |
|---|---|---|
| timed out, nothing moved | the attempt was wasted | investigate / escalate — **not** another attempt |
| timed out, the objective measurably moved | the work is real, there is simply more of it than one turn holds | **continue from where it stopped** |

`CODE_TURN_TIMEOUT` is an `ENVIRONMENT` failure either way — the host stopped
the turn, and that classification is *correct* and unchanged. What was missing
is a second, orthogonal fact: **did the attempt move the objective?** A live
experiment produced the first row's evil twin — attempt 0 timed out at 1800 s
having collected 13 tests with 1 still failing — and the ladder, which knows
only the failure class, offered `DIAGNOSTIC` ("do not edit any file"), a rung
that provably cannot continue the work.

**Why this is not a second verification authority.** It introduces no new
correctness question. It compares two `CommandProbe` measurements that
`core/convergence.py` already took, and reports which of the objective's own
numbers moved. The authority for "is it done" remains `acceptance.evaluate`;
the authority for "what is the goal state" remains `goal.derive_goal_state`;
the authority for "what may be tried" remains `RecoveryLadder`. This module
answers one question none of them asked, and it answers it from the harness's
own payloads.

**What is deliberately NOT a progress signal.** §3 of the mission forbids
`files_changed > 0`, `tool_calls > 0` and "the model says it made progress".
All three are *activity*, and a formatting churn satisfies the first two. So
file mutations are recorded as `SUPPORTING` evidence and **can never on their
own produce `MEANINGFUL_PROGRESS`**; only a movement in the objective's own
measurement can. A model's claim is not an input at all — nothing here reads
the model's text.

**Tampering closes the door on itself.** A measurement whose declared
verification inputs moved is not merely "unhelpful" — it is *untrustworthy*,
and an untrustworthy measurement supplies **no** signals. That is why the
`inputs_digest` check runs first and `continue`s: a green suite reached by
editing the contract must not be able to look like progress, any more than it
can look like convergence (F2, repaired by ADR-0045 R12).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable, Mapping


class ProgressVerdict(StrEnum):
    """Whether an attempt moved the objective. Total: exactly three values.

    `PROGRESS_UNDETERMINABLE` is the value the distinction cannot do without:
    "I have nothing to compare against" is neither "it moved" nor "it did not",
    and collapsing it into either is how an absence of measurement becomes a
    claim. It is deliberately **conservative** — it never widens the legal
    rungs, so an undeterminable attempt recovers exactly as it did before this
    module existed.
    """

    NO_PROGRESS = "no_progress"
    MEANINGFUL_PROGRESS = "meaningful_progress"
    PROGRESS_UNDETERMINABLE = "progress_undeterminable"


class SignalKind(StrEnum):
    """What a signal is worth. The classification is the whole discipline.

    * `AUTHORITATIVE` — a movement in one of the objective's own measurements.
      Alone sufficient for `MEANINGFUL_PROGRESS`.
    * `SUPPORTING` — a real change that is not by itself evidence of progress
      (a file the attempt changed, a check that now collects more). Recorded,
      never sufficient.
    * `REGRESSION` — a measurement moved *backwards*. Forces `NO_PROGRESS`
      even alongside an authoritative improvement: fixing one thing while
      breaking another is not progress toward the objective.
    * `UNSAFE` — the measurement cannot be trusted at all (its declared inputs
      changed). Forces `PROGRESS_UNDETERMINABLE`.
    """

    AUTHORITATIVE = "authoritative"
    SUPPORTING = "supporting"
    REGRESSION = "regression"
    UNSAFE = "unsafe"


@dataclass(frozen=True)
class ProgressReport:
    """The verdict, and the evidence that produced it.

    `authoritative` / `supporting` / `regressions` / `unsafe` are human-readable
    lines naming the measurement that moved and by how much, so a trajectory can
    be *reviewed* rather than trusted. They are journaled verbatim.
    """

    verdict: ProgressVerdict = ProgressVerdict.PROGRESS_UNDETERMINABLE
    reason: str = ""
    authoritative: tuple[str, ...] = ()
    supporting: tuple[str, ...] = ()
    regressions: tuple[str, ...] = ()
    unsafe: tuple[str, ...] = ()

    @property
    def meaningful(self) -> bool:
        return self.verdict is ProgressVerdict.MEANINGFUL_PROGRESS

    @property
    def signals(self) -> tuple[str, ...]:
        """Every signal, authoritative first — the reviewable record."""
        return (self.authoritative + self.regressions
                + self.unsafe + self.supporting)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "authoritative": list(self.authoritative),
            "supporting": list(self.supporting),
            "regressions": list(self.regressions),
            "unsafe": list(self.unsafe),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ProgressReport":
        return cls(
            verdict=ProgressVerdict(d.get("verdict", "progress_undeterminable")),
            reason=d.get("reason", ""),
            authoritative=tuple(d.get("authoritative") or ()),
            supporting=tuple(d.get("supporting") or ()),
            regressions=tuple(d.get("regressions") or ()),
            unsafe=tuple(d.get("unsafe") or ()),
        )

    def describe(self) -> str:
        """One line for a journal or a report."""
        detail = "; ".join(self.signals) or "nothing measurable moved"
        return f"{self.verdict.value}: {detail}"


#: The payload keys a `CommandProbe` records. Named here so the comparison
#: below reads as a contract rather than as magic strings — and so a probe that
#: stops recording one of them fails a test rather than silently reporting
#: `NO_PROGRESS` for every attempt.
COMMAND_KEYS = ("exit", "collected", "failed", "inputs_digest")
SYMBOL_KEYS = ("defined", "file_present")


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def evaluate_progress(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any],
    *,
    changed_files: Iterable[str] = (),
) -> ProgressReport:
    """Compare two measurements of the objective and say whether it moved.

    `before` is the previous measurement (the pre-work baseline for attempt 0,
    the previous attempt's measurement after that). `after` is the one just
    taken. Both are `Measurement.observations` — the probe's own payloads,
    keyed by criteria id, which is what makes this **objective-relative**:
    the criteria ids *are* the objective's terms.

    Order of decision, and each step exists for a reason:

    1. **No `before`** → `PROGRESS_UNDETERMINABLE`. There is nothing to
       compare against; inventing a verdict would be a claim.
    2. **A moved `inputs_digest`** → the criterion is skipped entirely and the
       verdict becomes `PROGRESS_UNDETERMINABLE`. The measurement is not
       evidence of anything, so it can neither help nor hurt.
    3. **A regression** → `NO_PROGRESS`. Not "negative progress" — there is no
       such state, and inventing one would put a fourth value in a total enum.
    4. **An authoritative improvement** → `MEANINGFUL_PROGRESS`.
    5. **Anything else** → `NO_PROGRESS`. Supporting signals are recorded; they
       are not sufficient, by design (see the module docstring).
    """
    if before is None:
        return ProgressReport(
            verdict=ProgressVerdict.PROGRESS_UNDETERMINABLE,
            reason="no prior measurement to compare against",
            supporting=tuple(
                f"{len(list(changed_files))} file(s) changed by the attempt"
                for _ in [0] if changed_files))

    authoritative: list[str] = []
    supporting: list[str] = []
    regressions: list[str] = []
    unsafe: list[str] = []

    for criteria_id in sorted(set(before) | set(after)):
        b = before.get(criteria_id) or {}
        a = after.get(criteria_id) or {}
        if not isinstance(b, Mapping) or not isinstance(a, Mapping):
            continue

        # (2) Tamper check FIRST, and it disqualifies the whole criterion.
        before_digest = str(b.get("inputs_digest") or "")
        after_digest = str(a.get("inputs_digest") or "")
        if before_digest and after_digest and before_digest != after_digest:
            unsafe.append(
                f"{criteria_id}: verification inputs changed "
                f"({before_digest[:8]}→{after_digest[:8]}) — the measurement "
                f"is not evidence")
            continue

        if any(k in b or k in a for k in SYMBOL_KEYS):
            before_defined = bool(b.get("defined"))
            after_defined = bool(a.get("defined"))
            if not before_defined and after_defined:
                authoritative.append(f"{criteria_id}: symbol became defined")
            elif before_defined and not after_defined:
                regressions.append(f"{criteria_id}: symbol disappeared")
            continue

        if not any(k in b or k in a for k in COMMAND_KEYS):
            continue

        before_exit, after_exit = b.get("exit"), a.get("exit")
        before_failed, after_failed = _int(b.get("failed")), _int(a.get("failed"))
        before_passed = _int(b.get("collected"))
        after_passed = _int(a.get("collected"))

        if after_exit == 0 and after_passed == 0:
            # A vacuous green: the command succeeded without running anything.
            # Never progress, never convergence — the same rule the floor
            # guard's `_run_tests_is_evidence` enforces. Checked FIRST, because
            # `exit 1 → 0` below would otherwise read it as an improvement.
            supporting.append(
                f"{criteria_id}: exit 0 with 0 collected (vacuous)")
            continue

        # Two shapes of the same fact, and both are needed. A suite run without
        # `-x` reports every failure, so progress shows as `failed` falling. A
        # suite run WITH `-x` (which is what `environment.py` detects) stops at
        # the first failure, so `failed` stays at 1 while the count of tests
        # that now PASS rises. Reading only `failed` would report "no progress"
        # for a project that had just implemented half its functions — and the
        # objective is "make the suite pass", so a rising pass count is the
        # objective's own measurement moving, not a proxy for it.
        #
        # It is not gameable: the only way to inflate the pass count is to add
        # or edit tests, and `tests/` is a declared input — a changed digest
        # disqualifies the whole criterion above.
        if after_failed > before_failed:
            regressions.append(
                f"{criteria_id}: failing checks {before_failed}→{after_failed}")
        elif after_failed < before_failed:
            authoritative.append(
                f"{criteria_id}: failing checks {before_failed}→{after_failed}")
        if after_passed > before_passed and after_failed <= before_failed:
            authoritative.append(
                f"{criteria_id}: passing checks {before_passed}→{after_passed}")
        elif after_passed < before_passed and after_failed >= before_failed:
            regressions.append(
                f"{criteria_id}: passing checks {before_passed}→{after_passed}")
        if after_exit == 0 and after_passed > 0 and before_exit != 0:
            authoritative.append(
                f"{criteria_id}: exit {before_exit}→0 ({after_passed} passed)")

    changed = tuple(sorted(changed_files))
    if changed:
        # Activity, recorded for review. It cannot make the verdict
        # meaningful: §3 of the mission forbids exactly that, because a
        # formatting churn satisfies it.
        preview = ", ".join(changed[:4]) + ("…" if len(changed) > 4 else "")
        supporting.append(
            f"{len(changed)} file(s) changed by the attempt ({preview})")

    if unsafe:
        return ProgressReport(
            verdict=ProgressVerdict.PROGRESS_UNDETERMINABLE,
            reason=("a measurement's declared inputs changed — its evidence "
                    "cannot be trusted, so no progress is claimed"),
            authoritative=tuple(authoritative), supporting=tuple(supporting),
            regressions=tuple(regressions), unsafe=tuple(unsafe))
    if regressions:
        return ProgressReport(
            verdict=ProgressVerdict.NO_PROGRESS,
            reason=("the attempt made one of the objective's measurements "
                    "worse; a regression is not progress"),
            authoritative=tuple(authoritative), supporting=tuple(supporting),
            regressions=tuple(regressions))
    if authoritative:
        return ProgressReport(
            verdict=ProgressVerdict.MEANINGFUL_PROGRESS,
            reason="the objective's own measurement moved forward",
            authoritative=tuple(authoritative), supporting=tuple(supporting))
    return ProgressReport(
        verdict=ProgressVerdict.NO_PROGRESS,
        reason=("no objective measurement moved — activity is not progress"),
        supporting=tuple(supporting))


@dataclass
class ProgressLedger:
    """The per-objective progress record. Durable, replayable, append-only.

    A thin accumulator rather than a second store: the controller journals each
    report with its attempt, and this exists so "was there ever any progress in
    this run?" is answerable without re-reading the journal.
    """

    reports: list[ProgressReport] = field(default_factory=list)

    def record(self, report: ProgressReport) -> ProgressReport:
        self.reports.append(report)
        return report

    @property
    def any_meaningful(self) -> bool:
        return any(r.meaningful for r in self.reports)

    def to_dict(self) -> dict[str, Any]:
        return {"reports": [r.to_dict() for r in self.reports]}
