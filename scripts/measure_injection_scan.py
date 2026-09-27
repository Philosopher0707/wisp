#!/usr/bin/env python3
"""Measure the injection scan against the corpus, and report the rates.

The skill's order, and it matters: **build the corpus and measure** — then change
the rule on the evidence — then re-measure on the same corpus. This script is
both halves: it prints the report *and* asserts the thresholds, so the number in
the report and the number the tripwire enforces cannot drift apart. They import
the same constants (`wisp.core.injection_scan`).

Run: `env -u PYTHONPATH .venv/bin/python scripts/measure_injection_scan.py`

Exit code is 0 only when every threshold holds. The corpus is
`tests/fixtures/injection_corpus.py`; its bucket sizes are asserted there so a
sample cannot be deleted to make a failing suite pass.
"""
from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tests.fixtures.injection_corpus import (  # noqa: E402
    BENIGN, CORPUS, EXPECTED_SIZES, PAYLOAD, QUOTES, by_bucket,
)
from wisp.core.injection_scan import (  # noqa: E402
    MAX_BENIGN_TRIPS, MAX_QUOTES_BLOCKED, MIN_PAYLOAD_RECALL, Verdict, scan,
)


def _measure() -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {}
    for bucket in (BENIGN, PAYLOAD, QUOTES):
        rows: dict[str, list[str]] = {v.value: [] for v in Verdict}
        for sample in by_bucket(bucket):
            rows[scan(sample.text).verdict.value].append(sample.sample_id)
        out[bucket] = rows
    return out


def main() -> int:
    m = _measure()
    n_benign = len(by_bucket(BENIGN))
    n_payload = len(by_bucket(PAYLOAD))
    n_quotes = len(by_bucket(QUOTES))

    print("=" * 74)
    print("THE INJECTION SCAN — measured, not asserted")
    print("=" * 74)
    print(f"corpus: {len(CORPUS)} samples "
          f"({n_benign} benign, {n_payload} payload, {n_quotes} quotes-a-payload)")
    print()

    print(f"{'bucket':<18} {'clean':>6} {'suspect':>8} {'BLOCK':>6}   rate")
    print("-" * 74)
    for bucket, label in ((BENIGN, "benign"), (PAYLOAD, "payload"),
                          (QUOTES, "quotes-payload")):
        r = m[bucket]
        n = len(by_bucket(bucket))
        blocked = len(r[Verdict.BLOCK.value])
        print(f"{label:<18} {len(r[Verdict.CLEAN.value]):>6} "
              f"{len(r[Verdict.SUSPECT.value]):>8} {blocked:>6}   "
              f"{blocked}/{n} blocked")
    print()

    fp = len(m[BENIGN][Verdict.BLOCK.value]) + len(m[BENIGN][Verdict.SUSPECT.value])
    print(f"FALSE-POSITIVE RATE (benign, blocked OR withheld): "
          f"{fp}/{n_benign} = {100 * fp / n_benign:.0f}%")
    print(f"   threshold: at most {MAX_BENIGN_TRIPS} benign trip(s) — zero, because every")
    print("   benign sample is hand-chosen to represent real output from this repo.")
    print("   FOR ORIGIN: a single-tier, intuition-built scan refused 36% of benign")
    print("   tool output at 87% recall. That number is why this corpus exists.")
    print()

    # Fail-closed means BOTH non-clean verdicts withhold the result, so recall
    # counts both. The tier says *what kind* of doubt; the verdict decides the
    # response, and on doubt the response is to withhold.
    caught = len(m[PAYLOAD][Verdict.BLOCK.value]) + len(m[PAYLOAD][Verdict.SUSPECT.value])
    recall = caught / n_payload
    print(f"RECALL (payload withheld, BLOCK or SUSPECT): {caught}/{n_payload} = {recall:.0%}")
    missed = m[PAYLOAD][Verdict.CLEAN.value]
    if missed:
        print(f"   MISSED (shown to the model): {', '.join(missed)}")
    print()

    q_blocked = len(m[QUOTES][Verdict.BLOCK.value])
    q_suspect = len(m[QUOTES][Verdict.SUSPECT.value])
    q_clean = len(m[QUOTES][Verdict.CLEAN.value])
    print(f"QUOTED-PAYLOAD (the precision limit, counted separately): "
          f"{q_blocked} blocked, {q_suspect} withheld, {q_clean} clean of {n_quotes}")
    print("   Counted apart so it can never flatter the false-positive rate.")
    print()

    failures: list[str] = []
    if fp > MAX_BENIGN_TRIPS:
        failures.append(f"benign tripped {fp} time(s), threshold {MAX_BENIGN_TRIPS}: "
                        + ", ".join(m[BENIGN][Verdict.BLOCK.value]
                                    + m[BENIGN][Verdict.SUSPECT.value]))
    if recall < MIN_PAYLOAD_RECALL:
        failures.append(f"recall {recall:.0%} below {MIN_PAYLOAD_RECALL:.0%}")
    if q_blocked > MAX_QUOTES_BLOCKED:
        failures.append(f"blocked {q_blocked} quoted payload(s), threshold "
                        f"{MAX_QUOTES_BLOCKED}: " + ", ".join(m[QUOTES][Verdict.BLOCK.value]))
    for bucket, expected in EXPECTED_SIZES.items():
        actual = len(by_bucket(bucket))
        if actual != expected:
            failures.append(f"corpus bucket {bucket!r} is {actual}, expected {expected} "
                            "— a sample was deleted or added without updating EXPECTED_SIZES")

    print("-" * 74)
    if failures:
        print("THRESHOLDS NOT MET:")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print("ALL THRESHOLDS MET — benign 0, recall 100%, no quoted payload blocked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
