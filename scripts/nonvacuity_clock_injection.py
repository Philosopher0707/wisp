#!/usr/bin/env python3
"""Non-vacuity probe for the clock invariant. Run when no other pytest is in flight (F112).

Mutation 1 is the one that matters: it reintroduces a direct wall-clock read, which
is exactly the state the invariant exists to forbid.
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
GUARD = "tests/reliability/test_clock_injection.py"
PYTEST = [".venv/bin/python", "-m", "pytest", GUARD, "-q", "-p", "no:cacheprovider", "--tb=no"]
CLOCK = REPO / "wisp/runtime/clock.py"
IDEM = REPO / "wisp/runtime/idempotency.py"
GUARD_FILE = REPO / "tests/reliability/test_clock_injection.py"


def _sub(t: str, old: str, new: str) -> str:
    if old not in t:
        raise SystemExit(f"PROBE DEFECT — anchor not found: {old[:70]!r}")
    return t.replace(old, new, 1)


def mutations() -> list[tuple[str, pathlib.Path, str, str]]:
    c = CLOCK.read_text(encoding="utf-8")
    i = IDEM.read_text(encoding="utf-8")
    return [
        # 1 — a direct wall-clock read returns to the layer.
        ("wall-clock-read-returns", IDEM,
         _sub(i, "from wisp.runtime.clock import Clock, SystemClock",
              "import time\nfrom wisp.runtime.clock import Clock, SystemClock")
         .replace("started_at=self._clock.now())", "started_at=time.time())", 1),
         "TestTheInvariant"),
        # 2 — the clock stops being consulted for staleness.
        ("staleness-ignores-the-clock", IDEM,
         _sub(i, "        age = (now if now is not None else self._clock.now()) - record.started_at",
              "        age = 0.0 - record.started_at"),
         "TestTheIdempotencyGuardUsesTheClock"),
        # 3 — the manual clock advances by real time, i.e. it sleeps.
        ("manual-clock-sleeps", CLOCK,
         _sub(c, "    def advance(self, seconds: float) -> float:",
              "    def advance(self, seconds: float) -> float:\n        import time; time.sleep(seconds / 1000)"),
         "TestTheClock"),
        # 4 — the scanned SET collapses, so the invariant is checked over nothing
        #     and passes. (Two earlier versions of this mutation were probe defects:
        #     one flipped `is_clock` and broke an unrelated test; the next LOWERED
        #     the floor, which makes the check vacuous but does not make it fail —
        #     **a threshold lowered to zero still passes.** The mutation has to
        #     shrink the set the floor is a floor ON.)
        ("scan-set-collapses", GUARD_FILE,
         _sub(GUARD_FILE.read_text(encoding="utf-8"),
              '    return sorted(p for p in LAYER.rglob("*.py")\n'
              '                  if "__pycache__" not in p.parts)',
              "    return []"),
         "TestTheInvariant"),
    ]


def main() -> int:
    targets = {CLOCK, IDEM, GUARD_FILE}
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in targets}
    originals = {p: p.read_text(encoding="utf-8") for p in targets}
    caught, results = 0, []
    try:
        for name, target, mutated, expected in mutations():
            target.write_text(mutated, encoding="utf-8")
            p = subprocess.run(PYTEST, cwd=REPO, capture_output=True, text=True)
            if p.returncode == 0:
                results.append((name, "MISSED", "the guard passed on a broken tree"))
            elif expected not in p.stdout:
                results.append((name, "WRONG-TEST", f"failed, but not in {expected}"))
            else:
                results.append((name, "CAUGHT", expected))
                caught += 1
            target.write_text(originals[target], encoding="utf-8")
    finally:
        for p, text in originals.items():
            p.write_text(text, encoding="utf-8")

    print(f"{'mutation':<30} {'result':<12} target")
    print("-" * 74)
    for name, res, detail in results:
        print(f"{name:<30} {res:<12} {detail}")
    print(f"\nmutations: {caught}/{len(results)} CAUGHT")
    restored = all(hashlib.sha256(p.read_bytes()).hexdigest() == before[p] for p in targets)
    print(f"tree restored byte-identical: {restored}")
    return 0 if caught == len(results) and restored else 1


if __name__ == "__main__":
    raise SystemExit(main())
