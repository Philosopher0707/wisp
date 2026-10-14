"""Hold a verifier's ALLOW against what the harness saw, not against what the verifier says it saw.

`verdict_from_reply` reads the verdict out of the child's text, and the `evidence` list in that text is the model's own account. This module is the second half of
the decision: the child's tool results as the executor returned them (`SubagentResult.observations`, see `wisp.multi_agent.observations`) decide whether an ALLOW
may stand. It never raises an ALLOW, it only withdraws one; every other decision passes through.

* A verifier that is allowed `run_tests` needs its LAST test run to be a real green suite (the completion gate's own reading,
  `wisp.core.verification._run_tests_is_evidence`), and nothing that can change files after it.
* Any other verifier needs at least one tool result of its own: it must have looked at something.
* The node's config (`evidence: tests`) can ask for more. It can never ask for less; a graph may be written by a planner, so a waiver in it would be a bypass.

What this does not prove: that the tests run are the right tests (a verifier may run a subset), or that a green suite means the change is right. It proves a model
did not make up a test run.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from wisp.core.verification import _MUTATING_TOOLS, _VERIFY_TOOLS
from wisp.multi_agent.observations import clean, tests_state

#: Tools whose result says nothing about what is on disk afterwards: anything that can write, and the two shells.
_CHANGES_FILES = frozenset(_MUTATING_TOOLS | _VERIFY_TOOLS)
_MAX_CODES = 32
_MAX_MESSAGE = 2048

_EXPLANATION = {
    "NO_TEST_RUN": "The harness saw no run_tests result in this verification, so the ALLOW is not accepted. Run the tests and report what they say.",
    "TESTS_NOT_GREEN": "The last run_tests result the harness saw was not a passing suite (failures, errors, or nothing ran), so the ALLOW is not accepted.",
    "STALE_TEST_RUN": "Something that can change files ran after the last passing run_tests, so that run proves nothing about the files now. Run the tests last.",
    "NO_OBSERVATION": "The harness saw no tool result from this verification, so the ALLOW is not accepted. Look at the change (read a file, run a check) first.",
}


_clean = clean
_tests_state = tests_state


def _demands_tests(allowed_tools: Any, required: Any) -> bool:
    tools = [t for t in allowed_tools if isinstance(t, str)] if isinstance(allowed_tools, (list, tuple)) else []
    return "run_tests" in tools or (isinstance(required, str) and required.strip().lower() == "tests")


def check_verdict(verdict: dict[str, Any], allowed_tools: Sequence[str] | None, observations: Any, required: Any = None) -> dict[str, Any]:
    """The verdict with an unbacked ALLOW turned into a REJECT whose reason code and message say what the harness did not see.

    Always adds ``harness_evidence``: ``{"run_tests": none|red|stale|green, "tool_results": n}``, so a run's record shows what stood behind the verdict.
    """
    seen = _clean(observations)
    state = _tests_state(seen)
    out = {**verdict, "harness_evidence": {"run_tests": state, "tool_results": len(seen)}}
    decision = verdict.get("decision")
    if not isinstance(decision, str):
        return {**out, "decision": "REJECT"}
    if decision.upper() != "ALLOW":
        return out
    if _demands_tests(allowed_tools, required):
        code = {"green": "", "none": "NO_TEST_RUN", "red": "TESTS_NOT_GREEN", "stale": "STALE_TEST_RUN"}[state]
    else:
        code = "" if seen else "NO_OBSERVATION"
    if not code:
        return out
    codes = [str(c) for c in (verdict.get("reason_codes") or []) if isinstance(c, (str, int))]
    message = f"{verdict.get('message') or ''} {_EXPLANATION[code]}".strip()
    return {**out, "decision": "REJECT", "reason_codes": [*codes, code][:_MAX_CODES], "message": message[:_MAX_MESSAGE]}
