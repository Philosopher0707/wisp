#!/usr/bin/env python3
"""Permission-policy audit — the mode sets, and whether the copies still agree.

`core/contracts.py:266` names the drift source itself: *"current drift source: three hand-maintained
frozensets in `infra/security.py:38-56`"*. `capability_filter.py:20` says the same thing from the other
side: *"equality with the enforcement set is pinned by test. If a 15th safe tool lands, update this set
AND the pin together."*

So the duplication is KNOWN and PINNED. This audit asks the two questions a pin does not:
  1. Do the copies actually agree *today*, across every site (not just the pinned pair)?
  2. Which copy is ENFORCED, and does the enforcement consult more than one?
"""
from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from wisp.infra import security as S  # noqa: E402
from wisp.infra import policy_engine as P  # noqa: E402
from wisp import capability_filter as C  # noqa: E402


def show(label: str, s: frozenset) -> None:
    print(f"  {label:44s} n={len(s):<3} {sorted(s)}")


def compare(label: str, sets: dict[str, frozenset]) -> bool:
    """Report agreement across named copies of one authority. True when all agree."""
    print(f"\n{label}")
    for name, s in sets.items():
        show(name, s)
    vals = list(sets.values())
    agree = all(v == vals[0] for v in vals)
    if agree:
        print(f"  => AGREE ({len(vals[0])} tools)")
    else:
        union = set().union(*vals)
        inter = set(vals[0]).intersection(*vals[1:])
        print(f"  => ⚠ DISAGREE — union {len(union)}, intersection {len(inter)}")
        for name, s in sets.items():
            only = sorted(s - inter)
            if only:
                print(f"     only in {name}: {only}")
    return agree


def main() -> int:
    print("PERMISSION POLICY AUDIT")
    print("=" * 78)

    print("\nmodes:", [m.value for m in S.PermissionMode])

    compare("READ-ONLY / safe-read set (the set contracts.py calls the drift source)", {
        "infra/security.py::_SAFE_READ_TOOLS": S._SAFE_READ_TOOLS,
        "infra/policy_engine.py::_DEFAULT_SAFE_READ_TOOLS": P._DEFAULT_SAFE_READ_TOOLS,
        "capability_filter.py::READ_ONLY_TOOLS": C.READ_ONLY_TOOLS,
    })

    compare("AUTO_EDIT block set", {
        "infra/security.py::_AUTO_EDIT_BLOCK_TOOLS": S._AUTO_EDIT_BLOCK_TOOLS,
        "policy_engine: DENY | APPROVAL": P._AUTO_EDIT_DENY_TOOLS | P._AUTO_EDIT_APPROVAL_TOOLS,
        "policy_engine::_DEFAULT_AUTO_EDIT_BLOCK": P._DEFAULT_AUTO_EDIT_BLOCK,
    })

    print("\nASK_ALL approval set (one site only — nothing to compare against)")
    show("infra/security.py::_ASK_ALL_BLOCK_TOOLS", S._ASK_ALL_BLOCK_TOOLS)

    # Which copy is enforced? Ask the live policy object, not the module constants.
    print("\n" + "=" * 78)
    print("ENFORCEMENT — which copy actually decides?")
    pol = S.SecurityPolicy(permission_mode="read_only")
    enforced = sorted(getattr(pol, "_safe_read_tools", getattr(pol, "safe_read_tools", ())) or ())
    print(f"  SecurityPolicy(read_only).safe_read_tools  n={len(enforced)}")
    print(f"  equals security._SAFE_READ_TOOLS?          {set(enforced) == set(S._SAFE_READ_TOOLS)}")
    print(f"  equals policy_engine._DEFAULT_SAFE_READ?   "
          f"{set(enforced) == set(P._DEFAULT_SAFE_READ_TOOLS)}")

    print("\n" + "=" * 78)
    print("WIRING — who reads the mode?")
    import subprocess
    out = subprocess.run(
        ["grep", "-rn", "permission_mode", "--include=*.py", "wisp/"],
        cwd=REPO, capture_output=True, text=True).stdout.splitlines()
    print(f"  {len(out)} mentions of `permission_mode` across wisp/")
    files = sorted({l.split(":")[0] for l in out})
    for f in files:
        print(f"    {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
