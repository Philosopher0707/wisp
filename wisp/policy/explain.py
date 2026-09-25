"""Plain-language policy explanation + dry-run (M4, pure).

These functions describe **what a bundle says**, not what the runtime does. Wisp
does not currently load a bundle at any entry point, so a verdict here is a
statement about the document, not about the agent's permissions — see
`PHASE_10_M4_GOVERNANCE_UNWIRED.md`. The wording below is deliberately phrased
as a rule ("Rule: deny X") rather than a report ("Denied X") for that reason.
"""
from __future__ import annotations
from typing import Any

from wisp.policy.loader import EffectivePolicy


def explain_denial(tool: str, args: dict[str, Any], policy: EffectivePolicy) -> str:
    layer = policy.provenance.get(f"approval:{tool}", "built-in floor")
    level = policy.approval_matrix.get(tool, "approve")
    target = args.get("path", args.get("command", ""))
    return (
        f"Rule: deny {tool}{f' on {target}' if target else ''} — "
        f"approval level is '{level}', controlled by the {layer} policy layer. "
        f"Narrow the request or ask your administrator to amend that layer."
    )


def dry_run(tool: str, args: dict[str, Any], policy: EffectivePolicy) -> dict[str, Any]:
    """Evaluate a planned action against the bundle without executing it.

    The returned `allowed` reflects **the bundle's** verdict. It does not
    predict the runtime's decision, because no bundle is loaded there.
    """
    level = policy.approval_matrix.get(tool, "approve")
    if level == "deny":
        return {"allowed": False, "reason": explain_denial(tool, args, policy),
                "obligations": [], "controlling_layer":
                    policy.provenance.get(f"approval:{tool}", "built-in floor")}
    obligations = ["explicit-user-approval"] if level == "approve" else []
    return {"allowed": True, "reason": f"{tool} permitted at level '{level}'",
            "obligations": obligations, "controlling_layer":
                policy.provenance.get(f"approval:{tool}", "built-in floor")}
