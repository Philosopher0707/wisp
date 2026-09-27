#!/usr/bin/env python3
"""Non-vacuity probe for the cost meter. Run when no other pytest is in flight (F112).

Mutation 1 is the one that matters: it reintroduces the SILENT ZERO for an unknown
model — the default that would make `max_cost_usd` unenforceable exactly where
nobody is looking.
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
GUARD = "tests/reliability/test_cost_meter.py"
PYTEST = [".venv/bin/python", "-m", "pytest", GUARD, "-q", "-p", "no:cacheprovider", "--tb=no"]
SRC = REPO / "wisp/runtime/cost.py"


def _sub(t: str, old: str, new: str) -> str:
    if old not in t:
        raise SystemExit(f"PROBE DEFECT — anchor not found: {old[:70]!r}")
    return t.replace(old, new, 1)


def mutations() -> list[tuple[str, str, str]]:
    s = SRC.read_text(encoding="utf-8")
    return [
        # 1 — THE SILENT ZERO: an unknown model costs nothing, so the bound never fires.
        ("unknown-model-charges-zero",
         _sub(s, "        raise UnknownModel(model, policy=self.on_unknown)",
              "        return Price(0.0, 0.0)"),
         "TestTheUnknownPath"),
        # 2 — the policy acquires a default, so a caller cannot be made to choose.
        ("policy-gets-a-default",
         _sub(s, "    on_unknown: UnknownPolicy\n",
              "    on_unknown: UnknownPolicy = UnknownPolicy.DECLARED\n"),
         "TestTheUnknownPath"),
        # 3 — a single blended rate: output priced as input, under-charging the runs
        #     that generate the most.
        ("output-priced-as-input",
         _sub(s, "        return (input_tokens / 1000.0) * self.input_per_1k + \\\n"
                 "               (output_tokens / 1000.0) * self.output_per_1k",
              "        return ((input_tokens + output_tokens) / 1000.0) * self.input_per_1k"),
         "TestTheArithmetic"),
        # 4 — the summary loses the table version, so a cost stops being attributable.
        ("summary-drops-the-version",
         _sub(s, '        return {"spent_usd": round(self.spent_usd, 6),\n'
                 '                "price_table_version": self.version,',
              '        return {"spent_usd": round(self.spent_usd, 6),'),
         "TestTheTableIsIdentifiable"),
    ]


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

    print(f"{'mutation':<30} {'result':<12} target")
    print("-" * 74)
    for name, res, detail in results:
        print(f"{name:<30} {res:<12} {detail}")
    print(f"\nmutations: {caught}/{len(results)} CAUGHT")
    restored = hashlib.sha256(SRC.read_bytes()).hexdigest() == before
    print(f"tree restored byte-identical: cost.py={restored}")
    return 0 if caught == len(results) and restored else 1


if __name__ == "__main__":
    raise SystemExit(main())
