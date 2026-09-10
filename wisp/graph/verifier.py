"""Verifier nodes + deterministic gates.

A verifier produces ALLOW / REJECT / RETRY / ESCALATE — never the primary
artifact. Verification must be evidence-based (test exit codes, diffs,
schema results) and independent from generation (different context, ideally
different model). A verifier that cannot reject is not a gate.

Deterministic gates are pure functions over evidence — never inferred from
natural language.
"""

from __future__ import annotations

from typing import Any

from wisp.graph.types import GateResult, VerificationResult


VALID_DECISIONS = ("ALLOW", "REJECT", "RETRY", "ESCALATE")


def normalize_verdict(output: dict[str, Any]) -> VerificationResult:
    decision = str(output.get("decision", "REJECT")).upper()
    if decision not in VALID_DECISIONS:
        decision = "REJECT"
    codes = output.get("reason_codes", []) or []
    evidence = output.get("evidence", []) or []
    return VerificationResult(decision=decision, reason_codes=list(codes),
                              evidence=list(evidence),
                              message=str(output.get("message", "")))


# ── Deterministic gates (pure; use for exit conditions + merge checks) ──

def gate_tests_green(evidence: dict[str, Any]) -> GateResult:
    if evidence.get("tests_green") is True or evidence.get("exit_code") == 0:
        return GateResult(True, "tests green", evidence_ids(evidence))
    return GateResult(False, "tests not green", evidence_ids(evidence),
                      failure_code="TEST_FAILURE")


def gate_no_scope_creep(changed: list[str], planned: list[str]) -> GateResult:
    extra = sorted(set(changed) - set(planned))
    if extra:
        return GateResult(False, f"out-of-scope changes: {', '.join(extra[:5])}",
                          failure_code="OUT_OF_SCOPE_FILE_CHANGE")
    return GateResult(True, "change set within plan")


def gate_schema_valid(valid: bool, errors: list[str] | None = None) -> GateResult:
    if valid:
        return GateResult(True, "schema valid")
    return GateResult(False, f"schema invalid: {'; '.join((errors or [])[:3])}",
                      failure_code="SCHEMA_INVALID")


def gate_policy_allowed(allowed: bool, reason: str = "") -> GateResult:
    if allowed:
        return GateResult(True, "policy allows")
    return GateResult(False, reason or "policy denied", failure_code="POLICY_DENIED")


def evidence_ids(evidence: dict[str, Any]) -> list[str]:
    out = []
    for v in evidence.values():
        if isinstance(v, str) and v.startswith("artifact://"):
            out.append(v)
    return out


def combine_verdicts(verdicts: list[VerificationResult]) -> VerificationResult:
    """Independent verifiers combine deterministically: any REJECT wins."""
    for v in verdicts:
        if v.decision == "REJECT":
            return v
    for v in verdicts:
        if v.decision == "ESCALATE":
            return v
    for v in verdicts:
        if v.decision == "RETRY":
            return v
    if verdicts:
        return verdicts[0]
    return VerificationResult(decision="REJECT", reason_codes=["NO_VERDICT"])
