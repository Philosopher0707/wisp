"""ADR-0053's instrument — what the turn's criteria set is, and what the gate keys on.

Committed so the measurement is re-runnable from the repository (F75: an instrument that
cannot be committed is not a re-runnable measurement).

Three measurements, all offline and deterministic:

1. **The criteria set.** For each case, the required-criteria ids the turn path computes,
   with the flag OFF and ON — showing that the declared ids are new, and that with the
   flag OFF the set is exactly the floor guard's.
2. **The verdict, and whether it is still a projection of the floor guard.** The
   discriminating case is a declared criterion that FAILS while `guard.rejection()`
   returns `None` — i.e. the floor guard is satisfied and the declaration is not. Under
   the flag that verdict is `FAIL`; floor-only it is `INCONCLUSIVE`.
3. **The gate's condition** (`verdict_keys_on_declared`) — true only for a FAIL whose
   deciding criterion is not the floor's.

Run:  env -u PYTHONPATH .venv/bin/python scripts/turn_criteria_measurement.py
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from wisp.core.goal import derive_goal_state  # noqa: E402
from wisp.core.turn_criteria import (  # noqa: E402
    floor_only,
    turn_acceptance_verdict,
    verdict_keys_on_declared,
)
from wisp.core.verification import FLOOR_CRITERION_ID, VerificationFloorGuard  # noqa: E402

DECL_SYMBOL_PRESENT = """\
--- criteria ---
symbol_defined: app.py::parse_duration
--- /criteria ---
Fix the bug in app.py.
"""

DECL_SYMBOL_ABSENT = """\
--- criteria ---
symbol_defined: app.py::definitely_not_defined_here
--- /criteria ---
Fix the bug in app.py.
"""

PLAIN = "Fix the bug in app.py.\n"

APP_PY = '''\
def parse_duration(text):
    return 0
'''


def _workspace() -> str:
    d = tempfile.mkdtemp(prefix="turn-criteria-")
    (pathlib.Path(d) / "app.py").write_text(APP_PY)
    return d


#: (label, prompt, guard-state). `verify` is `verify_ok_after_edit`.
CASES = [
    ("plain prompt, no mutation", PLAIN, dict(wrote_code=False)),
    ("plain prompt, verified mutation", PLAIN, dict(wrote_code=True, verify_ok_after_edit=True)),
    ("declared symbol PRESENT, no mutation", DECL_SYMBOL_PRESENT, dict(wrote_code=False)),
    ("declared symbol ABSENT, no mutation", DECL_SYMBOL_ABSENT, dict(wrote_code=False)),
    ("declared symbol ABSENT, verified mutation", DECL_SYMBOL_ABSENT,
     dict(wrote_code=True, verify_ok_after_edit=True)),
    ("declared symbol ABSENT, FAILED verification", DECL_SYMBOL_ABSENT,
     dict(wrote_code=True, verify_ok_after_edit=False)),
]


def main() -> int:
    ws = _workspace()
    print("=" * 108)
    print("ADR-0053 INSTRUMENT — the turn's criteria set, the verdict, and the gate's condition")
    print("=" * 108)
    print(f"workspace: {ws}\n")

    hdr = (f"{'case':44s} {'OFF verdict':>12s} {'OFF goal':>17s} "
           f"{'ON verdict':>11s} {'ON goal':>17s} {'gate?':>6s}")
    print(hdr)
    print("-" * len(hdr))

    declared_ids_seen: set[str] = set()
    discriminating = 0
    for label, prompt, state in CASES:
        guard = VerificationFloorGuard(**state)
        off_v, _off_tc = turn_acceptance_verdict(guard, prompt, ws, enabled=False)
        on_v, on_tc = turn_acceptance_verdict(guard, prompt, ws, enabled=True)
        declared_ids_seen.update(on_tc.declared_ids)

        def goal(v):
            return derive_goal_state(terminal_outcome="succeeded",
                                     acceptance_verdict=v.verdict,
                                     turn_succeeded=True).value

        gate = verdict_keys_on_declared(on_v)
        # A case is discriminating when the declaration moves the verdict.
        if off_v.verdict != on_v.verdict:
            discriminating += 1
        print(f"{label:44s} {str(off_v.verdict.value):>12s} {goal(off_v):>17s} "
              f"{str(on_v.verdict.value):>11s} {goal(on_v):>17s} {str(gate):>6s}")

    print()
    print("=" * 108)
    print("THE CRITERIA SET — what the flag adds, and that OFF is today's behaviour")
    print("=" * 108)
    guard = VerificationFloorGuard()
    off_tc = floor_only(guard)
    _v, on_tc = turn_acceptance_verdict(guard, DECL_SYMBOL_ABSENT, ws, enabled=True)
    print(f"  OFF (today): {[c.criteria_id for c in off_tc.criteria]}")
    print(f"  ON  (declared): {[c.criteria_id for c in on_tc.criteria]}")
    print(f"  declared ids the flag introduces: {sorted(declared_ids_seen)}")
    print(f"  the floor criterion's id (unchanged): {FLOOR_CRITERION_ID!r}")

    print()
    print("=" * 108)
    print("THE GATE'S CONDITION — is it the floor guard's condition under another name?")
    print("=" * 108)
    print("  `verdict_keys_on_declared(v)` is True iff v is FAIL and EVERY criterion it")
    print("  names is a non-floor one. Measured on the cases above, the two conditions")
    print("  disagree exactly where a declared criterion fails while the floor guard is")
    print("  satisfied — `guard.rejection()` returns None there (nothing was mutated, so")
    print("  the floor criterion is vacuously satisfied), which is the redundancy ADR-0051")
    print(f"  measured as 192/192. Discriminating cases here: {discriminating}.")

    print()
    print("=" * 108)
    print("STATUS: the turn's criteria set is non-floor when a declaration is present and the")
    print("flag is ON; with the flag OFF it is exactly the floor guard's criterion.")
    print("=" * 108)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
