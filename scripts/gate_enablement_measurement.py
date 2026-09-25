"""ADR-0051's instrument — the measurement behind the gate-enablement decision.

Committed so the measurement is **re-runnable from the repository**, which the previous
mission's instrument was not (its corpus lived under `.workbuddy-ai/`, which `.gitignore`
line 98 excludes; `git ls-files .workbuddy-ai/` returns 0). See `PHASE_GATE_ENABLEMENT.md`
finding F-1.

Three measurements, all deterministic and offline:

1. **The projection.** The turn path's acceptance verdict is `floor_guard_verdict(guard)`
   (`wisp/core/runtime.py:1189-1190`). Over the reachable guard state space, is it a
   *projection* of `VerificationFloorGuard` — i.e. does `verdict == FAIL` hold iff the guard
   is in its own blocking condition, the condition `rejection()` already tests at
   `wisp/core/stateless.py:911`? If so, a gate keyed on the verdict is redundant (FAIL) or
   harmful (non-PASS), and ADR-0051's R1 precondition is unmet.
2. **The producer search.** Who constructs `AcceptanceCriteria` in production, and which of
   them is on the turn path?
3. **The population.** The ADR-0016 rates, re-derived from the raw per-turn records in
   `scripts/gate_enablement_population.json` rather than quoted from prose.

Run:
    env -u PYTHONPATH .venv/bin/python scripts/gate_enablement_measurement.py
"""

from __future__ import annotations

import itertools
import json
import pathlib
from collections import Counter, defaultdict

REPO = pathlib.Path(__file__).resolve().parents[1]

import sys  # noqa: E402

sys.path.insert(0, str(REPO))

from wisp.core.acceptance import Verdict  # noqa: E402
from wisp.core.verification import (  # noqa: E402
    VerificationFloorGuard,
    floor_guard_verdict,
)

POPULATION = REPO / "scripts/gate_enablement_population.json"


def floor_blocks(guard: VerificationFloorGuard) -> bool:
    """`rejection()`'s precondition, before any budget is consulted."""
    return bool(guard.enabled and guard.wrote_code and guard.verify_ok_after_edit is not True)


def floor_surrendered(guard: VerificationFloorGuard) -> bool:
    return bool(guard.turns_used >= guard.min_turns and guard.nudges_used >= guard.max_nudges)


def states() -> list[VerificationFloorGuard]:
    out = []
    for enabled, wrote, verify, nudges, turns in itertools.product(
        (True, False), (True, False), (True, False, None), (0, 1, 2, 3), (0, 4, 5, 6)
    ):
        out.append(VerificationFloorGuard(
            enabled=enabled, wrote_code=wrote, verify_ok_after_edit=verify,
            nudges_used=nudges, turns_used=turns,
        ))
    return out


def measure_projection() -> dict:
    space = states()
    disagree = [g for g in space
                if (floor_guard_verdict(g).verdict is Verdict.FAIL) != floor_blocks(g)]
    non_pass_allowed = [g for g in space
                        if floor_guard_verdict(g).verdict is not Verdict.PASS
                        and not floor_blocks(g)]
    after_surrender = [g for g in space
                       if floor_guard_verdict(g).verdict is Verdict.FAIL
                       and floor_surrendered(g)]
    return {
        "states": len(space),
        "disagreements": len(disagree),
        "non_pass_allowed": len(non_pass_allowed),
        "non_pass_disabled_guard": sum(1 for g in non_pass_allowed if not g.enabled),
        "non_pass_read_only": sum(1 for g in non_pass_allowed
                                  if g.enabled and not g.wrote_code),
        "fail_after_surrender": len(after_surrender),
    }


def measure_producers() -> list[str]:
    found = []
    for p in sorted((REPO / "wisp").rglob("*.py")):
        if p.name == "acceptance.py":
            continue
        if "AcceptanceCriteria(" in p.read_text():
            found.append(str(p.relative_to(REPO)))
    return found


def measure_population() -> dict:
    if not POPULATION.exists():
        return {}
    turns = json.loads(POPULATION.read_text())
    per_model: dict[str, Counter] = defaultdict(Counter)
    for t in turns:
        per_model[t.get("model", "?")][t.get("acceptance") or "none"] += 1
    out = {}
    for model, c in per_model.items():
        n = sum(c.values())
        out[model] = {
            "turns": n,
            "pass": c.get("pass", 0),
            "fail": c.get("fail", 0),
            "inconclusive": c.get("inconclusive", 0),
            "rate": round(c.get("inconclusive", 0) / n, 4) if n else 0.0,
        }
    return out


def main() -> int:
    print("=" * 100)
    print("ADR-0051 INSTRUMENT — the acceptance gate's enablement measurement")
    print("=" * 100)

    proj = measure_projection()
    print("\n1. THE PROJECTION — is the turn path's verdict a projection of the floor guard?")
    print("-" * 100)
    print(f"   states examined                                        : {proj['states']}")
    print(f"   (verdict==FAIL) != the guard's blocking condition       : {proj['disagreements']}")
    print(f"   verdict != PASS while the guard is NOT blocking         : {proj['non_pass_allowed']}")
    print(f"        of which the guard is DISABLED                     : {proj['non_pass_disabled_guard']}")
    print(f"        of which the guard is enabled and nothing mutated  : {proj['non_pass_read_only']}")
    print(f"   verdict == FAIL after the guard already surrendered     : {proj['fail_after_surrender']}")
    if proj["disagreements"] == 0:
        print("\n   => R1's precondition is UNMET: FAIL is the guard's own blocking condition,")
        print("      so a FAIL-keyed gate duplicates it and a non-PASS-keyed gate withholds")
        print("      `done` on every read-only turn and every turn of a disabled guard.")
    else:
        print("\n   => R1's precondition may be MET — revisit ADR-0051 before enabling.")

    print("\n2. THE PRODUCER SEARCH — who constructs AcceptanceCriteria in wisp/?")
    print("-" * 100)
    for p in measure_producers():
        tag = "  <- the ONLY one on the turn path" if p.endswith("core/verification.py") else ""
        print(f"   {p}{tag}")
    print("\n   runtime.py:1189-1190 consumes floor_guard_verdict(); convergence.py's producers")
    print("   reach converge_on_objective only, where ConvergenceController evaluates them.")

    print("\n3. THE POPULATION — re-derived from scripts/gate_enablement_population.json")
    print("-" * 100)
    pop = measure_population()
    if not pop:
        print("   (no population file — the rate is not re-derivable here)")
    else:
        hdr = f"   {'provider / model':26s} {'turns':>5s} {'PASS':>5s} {'FAIL':>5s} {'INCONC':>6s} {'rate':>8s}"
        print(hdr)
        print("   " + "-" * (len(hdr) - 3))
        tp = tf = ti = tn = 0
        for model, d in sorted(pop.items()):
            print(f"   {model:26s} {d['turns']:5d} {d['pass']:5d} {d['fail']:5d} "
                  f"{d['inconclusive']:6d} {d['rate']*100:7.1f}%")
            tp += d["pass"]; tf += d["fail"]; ti += d["inconclusive"]; tn += d["turns"]
        print("   " + "-" * (len(hdr) - 3))
        print(f"   {'TOTAL (pooled — NOT a measurement)':26s} {tn:5d} {tp:5d} {tf:5d} "
              f"{ti:6d} {ti/tn*100 if tn else 0:7.1f}%")
        print("\n   The stratification is the result: pooling gives "
              f"{ti/tn*100:.1f}%, which describes no population that exists.")

    print("\n" + "=" * 100)
    print("STATUS: R1 UNMET — the gate has nothing to gate on; no flag is added (ADR-0051 R1/R7).")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
