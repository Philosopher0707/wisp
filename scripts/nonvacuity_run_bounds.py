#!/usr/bin/env python3
"""Non-vacuity probe for the four mandatory bounds. Run when no other pytest is
in flight (F112). Mutation 1 is the one that matters: it restores the behaviour
the invariant exists to forbid — a default."""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
GUARD = "tests/reliability/test_run_bounds.py"
PYTEST = [".venv/bin/python", "-m", "pytest", GUARD, "-q", "-p", "no:cacheprovider", "--tb=no"]
SRC = REPO / "wisp/runtime/bounds.py"


def _sub(t: str, old: str, new: str) -> str:
    if old not in t:
        raise SystemExit(f"PROBE DEFECT — anchor not found: {old[:70]!r}")
    return t.replace(old, new, 1)


def mutations() -> list[tuple[str, str, str]]:
    s = SRC.read_text(encoding="utf-8")
    out: list[tuple[str, str, str]] = []
    # 1 — a DEFAULT appears: a policy nobody chose.
    out.append(("a-default-appears",
                _sub(s, "    max_cost_usd: float\n", "    max_cost_usd: float = 2.0\n"),
                "TestNoDefaults"))
    # 2 — the missing-check is dropped.
    out.append(("missing-check-dropped",
                _sub(s, "    missing = tuple(n for n in BOUND_NAMES if n not in data)\n"
                        "    if missing:",
                     "    missing = ()\n    if missing:"),
                "TestNoDefaults"))
    # 3 — zero allowed on a consumption bound.
    out.append(("consumption-zero-allowed",
                _sub(s, "            if name in CONSUMPTION and value <= 0:",
                     "            if False:"),
                "TestTheRanges"))
    # 4 — the raw type check removed, so a bool slips through as 1.
    out.append(("bool-check-removed",
                s.replace("if isinstance(value, bool) or not isinstance(value, (int, float)):",
                     "if False:"),
                "TestTheRanges"))
    # 5 — exhaustion reports only the first bound.
    out.append(("exhaustion-reports-one",
                _sub(s, "        return tuple(n for n in BOUND_NAMES if usage[n] >= getattr(self, n))",
                     "        return tuple([n for n in BOUND_NAMES if usage[n] >= getattr(self, n)][:1])"),
                "TestExhaustion"))
    # 6 — unknown keys tolerated.
    out.append(("unknown-keys-tolerated",
                _sub(s, "    extra = tuple(sorted(set(data) - set(BOUND_NAMES)))\n    if extra:",
                     "    extra = ()\n    if extra:"),
                "TestNoDefaults"))
    return out


def main() -> int:
    before = hashlib.sha256(SRC.read_bytes()).hexdigest()
    original = SRC.read_text(encoding="utf-8")
    caught, results = 0, []
    try:
        for name, mutated, expected in mutations():
            SRC.write_text(mutated, encoding="utf-8")
            p = subprocess.run(PYTEST, cwd=REPO, capture_output=True, text=True)
            if p.returncode == 0:
                results.append((name, "MISSED", "the guard passed on a broken tree"))
            elif expected not in p.stdout:
                results.append((name, "WRONG-TEST", f"failed, but not in {expected}"))
            else:
                results.append((name, "CAUGHT", expected))
                caught += 1
            SRC.write_text(original, encoding="utf-8")
    finally:
        SRC.write_text(original, encoding="utf-8")

    print(f"{'mutation':<28} {'result':<12} target")
    print("-" * 72)
    for name, res, detail in results:
        print(f"{name:<28} {res:<12} {detail}")
    print(f"\nmutations: {caught}/{len(results)} CAUGHT")
    restored = hashlib.sha256(SRC.read_bytes()).hexdigest() == before
    print(f"tree restored byte-identical: bounds.py={restored}")
    return 0 if caught == len(results) and restored else 1


if __name__ == "__main__":
    raise SystemExit(main())
