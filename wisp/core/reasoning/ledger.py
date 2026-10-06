"""The evidence ledger: what is actually known about a turn, and how each fact was obtained. Pure.

INVARIANTS (docs/harness/reasoning-core-design.md, section 5)
  RC1  Deterministic: the ledger is a function of the events observed, in order.
  RC8  An OBSERVED fact can only be created by `Ledger.observe`, from an engine event that carries a source id. Nothing here takes
       model text, and `Fact` refuses to be built as OBSERVED any other way.
  RC10 No model call, clock, randomness, environment, network or filesystem.

A verification fact goes stale at the next mutation attempt, as the verification floor already does (`verify_ok_after_edit = None`): a
passing run says nothing about code that has changed since.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from wisp.core.gates.verify import classify
from wisp.core.verification import _MUTATING_TOOLS, _VERIFY_TOOLS, _run_tests_is_evidence, _verify_result_is_success

_MINT = object()  # only Ledger.observe holds this; a Fact cannot claim to be OBSERVED without it (RC8)

_RUN_TESTS_SUMMARY = "## Test Results ("


class FactKind(StrEnum):
    TOOL_RESULT = "tool_result"
    GATE_DECISION = "gate_decision"
    VERIFICATION_RUN = "verification_run"
    FILE_MUTATION = "file_mutation"
    PROBE = "probe"
    PROVIDER_ERROR = "provider_error"


class Status(StrEnum):
    """The same four words as CLAUDE.md section 1.2."""

    OBSERVED = "observed"
    INFERRED = "inferred"
    ASSUMED = "assumed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ObservedEvent:
    """What the engine saw, in the shape the ledger accepts. `source` is the engine's own id for the call; it is required."""

    type: str  # "tool_result" | "provider_error"
    name: str
    source: str
    result_text: str = ""
    command: str | None = None
    paths: tuple[str, ...] = ()
    failed: bool = False  # the tool reported an error status
    denied: bool = False  # refused by a gate or policy before it ran

    def __post_init__(self) -> None:
        if self.type not in ("tool_result", "provider_error"):
            raise ValueError(f"unknown event type {self.type!r}")
        if not self.source:
            raise ValueError("an observed event needs a source id")


@dataclass(frozen=True)
class Fact:
    id: str
    kind: FactKind
    status: Status
    source: str
    step: int
    mutation_index: int
    subject: str
    ok: bool | None = None
    runner: str = ""
    verify_kind: str = ""  # test | lint | typecheck | build
    paths: tuple[str, ...] = ()
    content_hash: str = ""
    _mint: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.status is Status.OBSERVED:
            if self._mint is not _MINT:
                raise ValueError("an OBSERVED fact can only be created by Ledger.observe")
            if not self.source:
                raise ValueError("an OBSERVED fact needs a source id")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _run_tests_ok(text: str) -> bool | None:
    """True for a real green suite, False for a real red one, None for a vacuous or unreadable run."""
    if _RUN_TESTS_SUMMARY not in text:
        return None
    if _run_tests_is_evidence(text):
        return True
    return None if "(0/0 passed)" in text else False


@dataclass
class Ledger:
    _facts: list[Fact] = field(default_factory=list)
    _mutations: int = 0

    @property
    def facts(self) -> tuple[Fact, ...]:
        return tuple(self._facts)

    @property
    def mutation_count(self) -> int:
        return self._mutations

    def observe(self, ev: ObservedEvent) -> Fact:
        """Record one engine event. The only way an OBSERVED fact comes to exist."""
        step = len(self._facts) + 1
        fid = f"F{step}"
        text = ev.result_text or ""
        digest = _digest(text)
        kw = dict(id=fid, status=Status.OBSERVED, source=ev.source, step=step, content_hash=digest, _mint=_MINT)

        if ev.type == "provider_error":
            fact = Fact(kind=FactKind.PROVIDER_ERROR, mutation_index=self._mutations, subject=ev.name or "provider", ok=False, **kw)
        elif ev.denied:
            fact = Fact(kind=FactKind.GATE_DECISION, mutation_index=self._mutations, subject=ev.name, ok=False, paths=ev.paths, **kw)
        elif ev.name in _MUTATING_TOOLS:
            self._mutations += 1  # an attempt invalidates earlier verification, successful or not (as the floor does)
            fact = Fact(kind=FactKind.FILE_MUTATION, mutation_index=self._mutations, subject=ev.name, ok=not ev.failed, paths=ev.paths, **kw)
        elif ev.name == "run_tests":
            fact = Fact(kind=FactKind.VERIFICATION_RUN, mutation_index=self._mutations, subject="run_tests", ok=_run_tests_ok(text),
                        runner="run_tests", verify_kind="test", **kw)
        elif ev.name in _VERIFY_TOOLS and ev.command is not None:
            verdict = classify(ev.command)
            success = (not ev.failed) and _verify_result_is_success(text)
            if verdict.ok:
                fact = Fact(kind=FactKind.VERIFICATION_RUN, mutation_index=self._mutations, subject=ev.command, ok=success,
                            runner=verdict.runner, verify_kind=verdict.kind, **kw)
            else:
                fact = Fact(kind=FactKind.TOOL_RESULT, mutation_index=self._mutations, subject=ev.command, ok=success, **kw)
        else:
            fact = Fact(kind=FactKind.TOOL_RESULT, mutation_index=self._mutations, subject=ev.command or ev.name, ok=not ev.failed, paths=ev.paths, **kw)
        self._facts.append(fact)
        return fact

    # ── reading ──
    def is_stale(self, fact: Fact) -> bool:
        return fact.kind is FactKind.VERIFICATION_RUN and fact.mutation_index < self._mutations

    def effective_status(self, fact: Fact) -> Status:
        """A stale verification is UNKNOWN, never OBSERVED (design rule R6)."""
        return Status.UNKNOWN if self.is_stale(fact) else fact.status

    def runs(self, verify_kinds: frozenset[str] | None = None) -> tuple[Fact, ...]:
        return tuple(f for f in self._facts if f.kind is FactKind.VERIFICATION_RUN and (verify_kinds is None or f.verify_kind in verify_kinds))

    def mutations(self) -> tuple[Fact, ...]:
        return tuple(f for f in self._facts if f.kind is FactKind.FILE_MUTATION)

    def snapshot_hash(self) -> str:
        """A digest of the whole ledger, for replay (RC11): the same events give the same hash."""
        parts = [f"{f.id}|{f.kind}|{f.status}|{f.source}|{f.mutation_index}|{f.subject}|{f.ok}|{f.runner}|{f.verify_kind}|{','.join(f.paths)}|{f.content_hash}" for f in self._facts]
        return _digest("\n".join(parts))
