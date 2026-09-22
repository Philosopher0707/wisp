"""Acceptance criteria, evidence, and verdicts (migration P3, stage 3a).

Makes "this succeeded" a **verdict against criteria backed by evidence**,
produced by something that is not the thing that acted.

Two vocabularies already existed and neither could do this job:

| Existing | Problem |
|---|---|
| `VerificationFloorGuard.resolved()` (`core/verification.py:193`) | a boolean: `wrote_code and verify_ok_after_edit is True`. It cannot say "I could not tell", and it is the *actor's* own bookkeeping |
| `VerificationResult.decision` (`graph/types.py`) | `("ALLOW","REJECT","RETRY","ESCALATE")` — a **router's** vocabulary. Every one of those values presumes a verdict was reached |

So the verdict vocabulary is new and total: `PASS` / `FAIL` / `INCONCLUSIVE`. Routing is then
**derived** from the verdict (`route_for()`), which keeps the existing graph vocabulary as an output
rather than a competitor — one authority for "what is true", another for "what to do about it".

**Stage 3a — this module records; it does not gate.** The plan rates P3 the highest-risk phase in the
migration because it changes completion semantics, and prescribes shipping it in two stages: 3a
introduces criteria and evidence and *records* the verdict; 3b enables the gate behind a flag after a
measurement period. `CompletionVerdict` therefore computes and exposes a verdict that nothing consumes
yet — deliberately. See `WISP_ARCHITECTURE_DECISIONS.md` ADR-0016.

**Evidence is content-addressed**, generalizing `graph/types.py::GraphArtifact`, which is already
hash-verified and carries a `producer`. An evidence record that cannot say who produced it cannot be
used to establish independence.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Iterable


# ── Verdict ─────────────────────────────────────────────────────────────


class Verdict(StrEnum):
    """What verification concluded. Total by construction.

    `INCONCLUSIVE` is the value the previous vocabularies could not express,
    and it is the important one: "no evidence" and "failing evidence" are
    different facts, and collapsing them is how a system reports success it
    cannot support.
    """

    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


#: Routing vocabulary inherited from `graph/verifier.py:19`. Derived from the
#: verdict, never assigned independently — two authorities for "what next"
#: would drift the moment one of them changed.
_ROUTE_FOR: dict[Verdict, str] = {
    Verdict.PASS: "ALLOW",
    Verdict.FAIL: "REJECT",
    Verdict.INCONCLUSIVE: "RETRY",
}


def route_for(verdict: Verdict | str) -> str:
    """Map a verdict onto the graph router's decision vocabulary.

    `INCONCLUSIVE` routes to `RETRY` — not `ALLOW`. The whole point of
    distinguishing it is that it must never be mistaken for success.
    """
    return _ROUTE_FOR[Verdict(verdict)]


# ── Criteria ────────────────────────────────────────────────────────────


class CriterionKind(StrEnum):
    """How a criterion is judged.

    The order is also the evaluation order: `DETERMINISTIC` first, because a
    failing deterministic check makes semantic evaluation pointless and paying
    for it is waste (`test_deterministic_first`).
    """

    DETERMINISTIC = "deterministic"   # exit codes, hashes, file existence
    ARTIFACT = "artifact"             # a content-addressed output exists
    SEMANTIC = "semantic"             # a judge reads the evidence


@dataclass(frozen=True)
class AcceptanceCriteria:
    """One thing that must be true for a task to count as done.

    `required=False` marks an advisory criterion: it is evaluated and
    recorded, but its failure cannot block completion. Without that
    distinction every criterion becomes a gate and the strictest one wins by
    default.
    """

    criteria_id: str
    description: str
    kind: CriterionKind = CriterionKind.DETERMINISTIC
    required: bool = True
    #: For DETERMINISTIC criteria: a pure predicate over the evidence payloads
    #: supplied to `CompletionVerdict.evaluate`. Never a model call.
    check: Callable[[dict[str, Any]], bool] | None = None


@dataclass(frozen=True)
class Evidence:
    """One observation, with provenance.

    Generalizes `GraphArtifact` (`graph/types.py`): content-addressed,
    carrying a `producer` and a `created_at`. `observations` names the raw
    signals the evidence rests on — an evidence record citing nothing is an
    assertion, not evidence.

    `invalidated_at` is what makes the invalidate-on-mutation property
    expressible for evidence generally, not just for the floor guard.
    """

    evidence_id: str
    criteria_id: str
    producer: str
    kind: CriterionKind = CriterionKind.DETERMINISTIC
    content_hash: str = ""
    uri: str = ""
    created_at: float = field(default_factory=time.time)
    invalidated_at: float | None = None
    observations: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return self.invalidated_at is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id, "criteria_id": self.criteria_id,
            "producer": self.producer, "kind": self.kind.value,
            "content_hash": self.content_hash, "uri": self.uri,
            "created_at": self.created_at,
            "invalidated_at": self.invalidated_at,
            "observations": list(self.observations),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Evidence":
        return cls(
            evidence_id=d["evidence_id"], criteria_id=d["criteria_id"],
            producer=d.get("producer", ""),
            kind=CriterionKind(d.get("kind", "deterministic")),
            content_hash=d.get("content_hash", ""), uri=d.get("uri", ""),
            created_at=d.get("created_at", 0.0),
            invalidated_at=d.get("invalidated_at"),
            observations=tuple(d.get("observations") or ()),
            metadata=dict(d.get("metadata") or {}),
        )


def content_digest(payload: Any) -> str:
    """Stable content hash for an evidence payload.

    Deterministic JSON (sorted keys, no whitespace) so the same observation
    hashes the same across processes, and `repr` as the fallback so an
    unserializable payload degrades to *a* hash rather than raising — an
    evidence write must not fail a turn.
    """
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"),
                   default=repr, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


# ── The verdict ─────────────────────────────────────────────────────────


@dataclass
class CompletionVerdict:
    """The recorded outcome of evaluating criteria against evidence.

    Stage 3a: constructed and reported, but **not consulted** by the
    completion rule. The point of this stage is to measure what the gate
    *would* say before letting it say it.
    """

    verdict: Verdict = Verdict.INCONCLUSIVE
    reason_codes: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    unmet_criteria: list[str] = field(default_factory=list)
    message: str = ""

    @property
    def routed_decision(self) -> str:
        """The graph router's decision, derived — never assigned."""
        return route_for(self.verdict)

    @property
    def accepted(self) -> bool:
        return self.verdict is Verdict.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "decision": self.routed_decision,
            "reason_codes": list(self.reason_codes),
            "evidence_ids": list(self.evidence_ids),
            "unmet_criteria": list(self.unmet_criteria),
            "message": self.message,
        }


def evaluate(criteria: Iterable[AcceptanceCriteria],
             evidence: Iterable[Evidence],
             observations: dict[str, Any] | None = None) -> CompletionVerdict:
    """Judge criteria against evidence. Pure; no model, no I/O.

    The rules, in order:

    1. **No required criteria → `INCONCLUSIVE`.** A task with nothing to
       check has not been verified; it has been un-examined. This is the
       `test_criteria_required_gate` rule, and it is the one that makes
       `SUCCEEDED` unclaimable by default.
    2. **A failing DETERMINISTIC criterion → `FAIL`, short-circuiting.**
       Deterministic checks are cheap and decisive; running semantic
       evaluation after one has already failed buys nothing.
    3. **A required criterion with no valid evidence → `INCONCLUSIVE`.**
       Absence of evidence is not evidence of failure.
    4. **A required criterion whose evidence is invalidated → `INCONCLUSIVE`**
       too: an invalidated observation is indistinguishable from no
       observation, and treating it as a failure would be a claim the
       evidence does not support.
    5. Otherwise → `PASS`, if every required criterion is satisfied.

    Advisory (`required=False`) criteria are evaluated and reported but can
    neither pass nor block the verdict.
    """
    criteria = list(criteria)
    evidence = list(evidence)
    payloads = observations or {}

    valid_by_criterion: dict[str, list[Evidence]] = {}
    for ev in evidence:
        if ev.valid:
            valid_by_criterion.setdefault(ev.criteria_id, []).append(ev)

    required = [c for c in criteria if c.required]
    if not required:
        return CompletionVerdict(
            verdict=Verdict.INCONCLUSIVE,
            reason_codes=["NO_REQUIRED_CRITERIA"],
            message=("no required acceptance criteria were declared — "
                     "nothing was verified"),
        )

    unmet: list[str] = []
    cited: list[str] = []
    reasons: list[str] = []

    # Rule 2 — deterministic first, and decisive.
    for c in required:
        if c.kind is not CriterionKind.DETERMINISTIC or c.check is None:
            continue
        try:
            ok = bool(c.check(payloads))
        except Exception:
            ok = False
        if not ok:
            return CompletionVerdict(
                verdict=Verdict.FAIL,
                reason_codes=["DETERMINISTIC_CHECK_FAILED"],
                unmet_criteria=[c.criteria_id],
                message=f"deterministic criterion failed: {c.criteria_id}",
            )

    # Rules 3/4/5.
    for c in required:
        supporting = valid_by_criterion.get(c.criteria_id, [])
        if not supporting:
            unmet.append(c.criteria_id)
            reasons.append("NO_EVIDENCE")
            continue
        cited.extend(ev.evidence_id for ev in supporting)

    if unmet:
        return CompletionVerdict(
            verdict=Verdict.INCONCLUSIVE,
            reason_codes=sorted(set(reasons)) or ["NO_EVIDENCE"],
            evidence_ids=cited,
            unmet_criteria=unmet,
            message=("no valid evidence for required criteria: "
                     + ", ".join(unmet)),
        )

    return CompletionVerdict(
        verdict=Verdict.PASS,
        reason_codes=["ALL_REQUIRED_SATISFIED"],
        evidence_ids=cited,
        message="every required criterion has valid evidence",
    )


def invalidate(evidence: list[Evidence], *, criteria_ids: Iterable[str] | None = None,
               at: float | None = None) -> list[Evidence]:
    """Return copies with `invalidated_at` set. Never mutates in place.

    The invalidate-on-mutation rule the floor guard already implements
    (`core/verification.py:149`) — a later mutation makes prior evidence
    stale. Applied here to evidence generally, so the property is available
    to any criterion rather than only to the one guard that has it today.

    Invalidation **cascades**: invalidating a criterion invalidates the
    evidence that cites it, and a caller re-evaluating afterwards sees
    `INCONCLUSIVE` rather than a stale `PASS`.
    """
    stamp = time.time() if at is None else at
    targets = None if criteria_ids is None else set(criteria_ids)
    out: list[Evidence] = []
    for ev in evidence:
        if ev.invalidated_at is not None:
            out.append(ev)
            continue
        if targets is None or ev.criteria_id in targets:
            out.append(Evidence(
                evidence_id=ev.evidence_id, criteria_id=ev.criteria_id,
                producer=ev.producer, kind=ev.kind,
                content_hash=ev.content_hash, uri=ev.uri,
                created_at=ev.created_at, invalidated_at=stamp,
                observations=ev.observations, metadata=ev.metadata,
            ))
        else:
            out.append(ev)
    return out
