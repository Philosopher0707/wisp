"""Claim audit: what the model says it did, checked against what the ledger observed. Pure.

INVARIANTS (docs/harness/reasoning-core-design.md, section 5)
  RC2  The model's prose is never an input to a decision. Here it is DATA under audit: `extract` returns facts about the text, and
       `audit` compares them to the ledger.
  RC7  Precision over recall. A claim is reported only when its kind is recognised in a plain, past/present, affirmative, unhedged
       sentence. Questions, hypotheticals, plans ("I'll run the tests"), negations, exceptions and anything under an explicit "I did not
       run it" disclaimer are never claims.
  RC10 No model, clock, randomness, environment, network or filesystem.

A claim is UNSUPPORTED when no observed fact of the needed kind backs it, CONTRADICTED when an observed fact says otherwise, and
SUPPORTED otherwise. The audit is a floor, not a wall: phrasing can dodge it, and the verification floor stays the authority on truth.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from enum import StrEnum

from wisp.core.reasoning.ledger import Fact, FactKind, Ledger


class ClaimKind(StrEnum):
    TESTS_PASS = "tests_pass"
    BUILD_OK = "build_ok"
    LINT_OK = "lint_ok"
    FIXED = "fixed"
    FILE_CHANGED = "file_changed"
    COMMAND_RAN = "command_ran"


class Verdict(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"


@dataclass(frozen=True)
class Claim:
    kind: ClaimKind
    text: str
    start: int
    end: int
    subject: str = ""  # a file name or a command, when the claim names one


@dataclass(frozen=True)
class Extraction:
    claims: tuple[Claim, ...]
    disclaimed: frozenset[ClaimKind]  # kinds the message explicitly says were NOT verified; their claims are dropped


@dataclass(frozen=True)
class Audit:
    claim: Claim
    verdict: Verdict
    fact_ids: tuple[str, ...]
    reason: str


_FENCE = re.compile(r"```.*?```", re.DOTALL)
_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z`\"'(\[*\-])|\n+")

# A sentence is not a claim if any of these appears: it is a question, a plan, a condition, a purpose, a modal, a negation or an exception.
_NOT_A_CLAIM = re.compile(
    r"\?\s*$|\b(?:should|would|could|might|may|can|cannot|can't|will|won't|shall|if|unless|once|until|whether|so that|in order to|"
    r"let me|let's|i'll|i will|i'm going to|i am going to|going to|need to|needs to|needed to|want to|try to|trying to|supposed to|"
    r"expected to|to (?:verify|check|confirm|make sure|ensure|see)|next|then i|when you|you (?:can|should|need|could)|"
    r"not|n't|never|no longer|without|still|except|but|however|although|though|fail(?:s|ed|ing|ure)?|error|errors|broken|regress(?:ion|ed)?|"
    r"reported|reports that|says|said|states|stated|according to|claims|claimed|mentions|last (?:week|month|year)|yesterday|previous(?:ly)?|earlier|"
    r"upstream|ci|jenkins|github actions|(?:on|in|from) (?:the pipeline|main|master))\b",
    re.IGNORECASE)
# "no errors" is the content of a clean-result claim, not a failure report; it must not trip the failure words above.
_ZERO_FINDINGS = re.compile(r"\b(?:reports?\s+)?no\s+(?:\w+\s+)?(?:errors?|issues?|warnings?)\b", re.IGNORECASE)

_DISCLAIMER = re.compile(
    r"\b(?:i\s+)?(?:did\s+not|didn't|have\s+not|haven't|has\s+not|hasn't|could\s+not|couldn't|was\s+unable\s+to|wasn't\s+able\s+to|am\s+unable\s+to|"
    r"cannot|can't|never)\s+(?:yet\s+)?(?:run|execute|test|verify|check|build|compile|validate)\b|\b(?:untested|unverified)\b|"
    r"\bnot\s+(?:yet\s+|been\s+|actually\s+)?(?:tested|verified|run|executed|validated)\b", re.IGNORECASE)

_P = re.IGNORECASE
_TESTS_PASS = [
    re.compile(r"\b(?:all|every)\s+(?:of\s+)?(?:the\s+)?(?:\w+\s+){0,2}tests?\s+(?:now\s+|all\s+)?(?:pass(?:ed|es|ing)?|are\s+(?:now\s+)?(?:passing|green))\b", _P),
    re.compile(r"\btests?\s+(?:now\s+|all\s+)?(?:pass(?:ed|es)?|are\s+(?:now\s+)?(?:passing|green))\b", _P),
    re.compile(r"\b(?:the\s+)?(?:test\s+)?suite\s+(?:now\s+)?(?:pass(?:ed|es)?|is\s+(?:now\s+)?(?:passing|green))\b", _P),
    re.compile(r"\b\d+\s*(?:/\s*\d+\s*)?(?:\w+\s+)?tests?\s+(?:now\s+)?pass(?:ed|ing)?\b", _P),
    re.compile(r"\bpytest\s+(?:now\s+)?(?:passes|passed|is\s+green)\b", _P),
]
_BUILD_OK = [
    re.compile(r"\b(?:the\s+)?build\s+(?:now\s+)?(?:succeed(?:s|ed)|pass(?:es|ed)|is\s+(?:now\s+)?(?:green|successful|passing)|works|completes|completed)\b", _P),
    re.compile(r"\bbuilds?\s+(?:now\s+)?(?:successfully|cleanly)\b", _P),
    re.compile(r"\b(?:compiles|compiled)\s+(?:now\s+)?(?:successfully|cleanly|without\s+errors)\b", _P),
    re.compile(r"\btype[- ]?check(?:s|ing)?\s+(?:now\s+)?(?:pass(?:es|ed)?|is\s+(?:now\s+)?clean|succeeds)\b", _P),
    re.compile(r"\b(?:mypy|tsc|pyright)\s+(?:now\s+)?(?:pass(?:es|ed)?|is\s+clean|reports\s+no\s+(?:errors|issues))\b", _P),
]
_LINT_OK = [
    re.compile(r"\b(?:lint|linting|linter|ruff|eslint|flake8|pylint)\s+(?:now\s+)?(?:pass(?:es|ed)?|is\s+clean|reports\s+no\s+(?:errors|issues|warnings))\b", _P),
    re.compile(r"\bno\s+lint\s+(?:errors|issues|warnings)\b", _P),
]
_FIXED = [
    re.compile(r"\bi(?:'ve|\s+have)?\s+(?:now\s+)?(?:fixed|resolved|repaired|patched|solved)\b", _P),
    re.compile(r"\b(?:the\s+|this\s+)?(?:bug|issue|problem|error|failure|crash|regression)\s+(?:is|has\s+been|was)\s+(?:now\s+)?(?:fixed|resolved|solved)\b", _P),
    re.compile(r"\b(?:fixed|resolved)\s+(?:the|this)\s+(?:bug|issue|problem|error|failure|crash)\b", _P),
]
_FILE_CHANGED = re.compile(
    r"\bi(?:'ve|\s+have)?\s+(?:now\s+)?(?:updated|modified|edited|changed|created|added|wrote|written|rewrote|refactored|renamed|deleted|removed)\s+"
    r"(?:the\s+)?(?:file\s+)?[`'\"]?([\w./\-]*[\w\-]\.[A-Za-z0-9]{1,6})[`'\"]?\b", _P)
_COMMAND_RAN = re.compile(r"\bi\s+(?:have\s+|'ve\s+)?(?:just\s+|then\s+|also\s+)?(?:ran|executed|run)\s+`([^`\n]{2,160})`", _P)

_RUNNER_KINDS = {
    ClaimKind.TESTS_PASS: frozenset({"test"}),
    ClaimKind.BUILD_OK: frozenset({"build", "typecheck"}),
    ClaimKind.LINT_OK: frozenset({"lint"}),
}
_FIX_EVIDENCE = frozenset({"test", "build", "typecheck"})
_VERIFICATION_CLAIMS = frozenset({ClaimKind.TESTS_PASS, ClaimKind.BUILD_OK, ClaimKind.LINT_OK, ClaimKind.FIXED})


def _sentences(text: str) -> list[tuple[str, int]]:
    cleaned = _FENCE.sub(lambda m: " " * len(m.group(0)), text)  # fenced code is not prose; keep offsets
    out: list[tuple[str, int]] = []
    pos = 0
    for m in list(_SPLIT.finditer(cleaned)) + [None]:  # type: ignore[list-item]
        end = m.start() if m else len(cleaned)
        chunk = cleaned[pos:end]
        if chunk.strip():
            out.append((chunk, pos))
        pos = m.end() if m else end
    return out


def extract(text: str) -> Extraction:
    """The claims in `text`, minus anything hypothetical, negated, planned or disclaimed (RC7)."""
    if not isinstance(text, str) or not text.strip():
        return Extraction((), frozenset())
    disclaimed = frozenset(_VERIFICATION_CLAIMS) if _DISCLAIMER.search(_FENCE.sub(" ", text)) else frozenset()
    claims: list[Claim] = []
    for sentence, offset in _sentences(text):
        s = sentence.strip()
        if not s or _NOT_A_CLAIM.search(_ZERO_FINDINGS.sub(" ", s)):
            continue
        spans: list[tuple[ClaimKind, re.Match[str], str]] = []
        for kind, patterns in ((ClaimKind.TESTS_PASS, _TESTS_PASS), (ClaimKind.BUILD_OK, _BUILD_OK), (ClaimKind.LINT_OK, _LINT_OK), (ClaimKind.FIXED, _FIXED)):
            for rx in patterns:
                m = rx.search(s)
                if m:
                    spans.append((kind, m, ""))
                    break
        m = _FILE_CHANGED.search(s)
        if m:
            spans.append((ClaimKind.FILE_CHANGED, m, m.group(1)))
        m = _COMMAND_RAN.search(s)
        if m:
            spans.append((ClaimKind.COMMAND_RAN, m, m.group(1).strip()))
        for kind, m, subject in spans:
            if kind in disclaimed:
                continue
            start = offset + sentence.index(s) + m.start()
            claims.append(Claim(kind, s, start, start + len(m.group(0)), subject))
    return Extraction(tuple(claims), disclaimed)


def _norm(cmd: str) -> str:
    return " ".join(cmd.split())


def _latest(runs: tuple[Fact, ...]) -> Fact | None:
    return runs[-1] if runs else None


def _verification_verdict(ledger: Ledger, kinds: frozenset[str] | None) -> tuple[Verdict, tuple[str, ...], str]:
    runs = ledger.runs(kinds)
    valid = tuple(f for f in runs if not ledger.is_stale(f) and f.ok is not None)
    last = _latest(valid)
    if last is None:
        stale = tuple(f for f in runs if ledger.is_stale(f))
        if stale:
            return Verdict.UNSUPPORTED, (stale[-1].id,), "the last matching run predates a later edit"
        return Verdict.UNSUPPORTED, (), "no matching verification run was observed"
    if last.ok:
        return Verdict.SUPPORTED, (last.id,), f"{last.runner or last.subject} passed after the last edit"
    return Verdict.CONTRADICTED, (last.id,), f"{last.runner or last.subject} failed after the last edit"


def audit_claim(claim: Claim, ledger: Ledger) -> Audit:
    kind = claim.kind
    if kind in _RUNNER_KINDS:
        verdict, ids, reason = _verification_verdict(ledger, _RUNNER_KINDS[kind])
        return Audit(claim, verdict, ids, reason)
    if kind is ClaimKind.FIXED:
        edits = tuple(f for f in ledger.mutations() if f.ok)
        if not edits:
            return Audit(claim, Verdict.UNSUPPORTED, (), "no edit was observed, so nothing was fixed by this session")
        verdict, ids, reason = _verification_verdict(ledger, _FIX_EVIDENCE)  # a lint pass alone says nothing about a behaviour fix
        return Audit(claim, verdict, (edits[-1].id,) + ids, reason)
    if kind is ClaimKind.FILE_CHANGED:
        base = posixpath.basename(claim.subject)
        hits = tuple(f for f in ledger.mutations() if any(posixpath.basename(p) == base for p in f.paths))
        ok_hits = tuple(f for f in hits if f.ok)
        if ok_hits:
            return Audit(claim, Verdict.SUPPORTED, (ok_hits[-1].id,), f"an edit to {base} was observed")
        denied = tuple(f for f in ledger.facts if f.kind is FactKind.GATE_DECISION and any(posixpath.basename(p) == base for p in f.paths))
        if hits or denied:
            return Audit(claim, Verdict.CONTRADICTED, tuple(f.id for f in hits + denied), f"the attempted edit to {base} did not succeed")
        return Audit(claim, Verdict.UNSUPPORTED, (), f"no edit to {base} was observed")
    if kind is ClaimKind.COMMAND_RAN:
        want = _norm(claim.subject)
        hits = tuple(f for f in ledger.facts if f.kind in (FactKind.TOOL_RESULT, FactKind.VERIFICATION_RUN) and want and want in _norm(f.subject))
        if hits:
            return Audit(claim, Verdict.SUPPORTED, (hits[-1].id,), "that command was observed running")
        return Audit(claim, Verdict.UNSUPPORTED, (), "no run of that command was observed")
    return Audit(claim, Verdict.UNSUPPORTED, (), "unrecognised claim kind")


def audit(text: str, ledger: Ledger) -> tuple[Audit, ...]:
    """Extract the claims in `text` and check each against `ledger`. Deterministic."""
    return tuple(audit_claim(c, ledger) for c in extract(text).claims)
