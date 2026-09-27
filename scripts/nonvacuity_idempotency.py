#!/usr/bin/env python3
"""Non-vacuity probe for the idempotency guard — one mutation per row of the
failure table. Run when no other pytest is in flight (F112).

Row 4's mutation restores the exact bug the tests found: fail-open returning
success without running the effect.
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
GUARD = "tests/reliability/test_idempotency.py"
PYTEST = [".venv/bin/python", "-m", "pytest", GUARD, "-q", "-p", "no:cacheprovider", "--tb=no"]
SRC = REPO / "wisp/runtime/idempotency.py"


def _sub(t: str, old: str, new: str) -> str:
    if old not in t:
        raise SystemExit(f"PROBE DEFECT — anchor not found: {old[:70]!r}")
    return t.replace(old, new, 1)


def mutations() -> list[tuple[str, str, str]]:
    s = SRC.read_text(encoding="utf-8")
    out: list[tuple[str, str, str]] = []
    # 1 — the record claims the work is DONE before it has run, i.e. the state
    #     written ahead of the side effect is not IN_PROGRESS. (The first version
    #     of this mutation merely ADDED a redundant write after the effect, which
    #     changes nothing — the guard was right to stay green. A mutation must
    #     break the property, not decorate the code.)
    out.append(("row1-state-not-in-progress",
                _sub(s, "        record = Record(key=key, fingerprint=fingerprint,\n"
                        "                        state=RecordState.IN_PROGRESS, started_at=time.time())",
                     "        record = Record(key=key, fingerprint=fingerprint,\n"
                     "                        state=RecordState.COMPLETED, started_at=time.time())"),
                "TestRow1CrashAfterTheSideEffect"))
    # 2 — the conditional write becomes unconditional, so both racers win.
    out.append(("row2-unconditional-write",
                _sub(s, "        existing = self._records.get(key)\n        if existing is not None:\n            return existing, False",
                     "        existing = self._records.get(key)\n        if False:\n            return existing, False"),
                "TestRow2RacingRequests"))
    # 3 — the fingerprint comparison dropped.
    out.append(("row3-fingerprint-unchecked",
                _sub(s, "            if record.fingerprint != fingerprint:      # row 3",
                     "            if False:"),
                "TestRow3KeyReusedWithADifferentBody"))
    # 4 — fail-open returns success WITHOUT running the effect (the bug found).
    out.append(("row4-fail-open-does-nothing",
                _sub(s, "                result = effect(key)\n                stored = self._redact(result) if self._redact is not None else result",
                     "                result = None\n                stored = None"),
                "TestRow4StorageOutage"))
    # 5 — the unstable-key tripwire removed.
    out.append(("row5-tripwire-removed",
                _sub(s, "        if seen is not None and seen.key != key:",
                     "        if False:"),
                "TestRow5UnstableKey"))
    # 6 — a holder in progress is reported as a replay instead of PENDING.
    out.append(("row6-pending-becomes-replay",
                _sub(s, "            return GuardResult(Outcome.PENDING, replace(record, poll_url=poll_url))",
                     "            return GuardResult(Outcome.REPLAYED, replace(record, poll_url=poll_url))"),
                "TestRow6VeryLongRunningOperation"))
    # 7 — redaction applied to the RETURNED value only, not before storing.
    out.append(("row7-redact-after-storing",
                _sub(s, "        self._store.finish(key, state=RecordState.COMPLETED, result=stored)",
                     "        self._store.finish(key, state=RecordState.COMPLETED, result=result)"),
                "TestRow7PiiIsRedactedBeforeStoring"))
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

    print(f"{'mutation':<36} {'result':<12} target")
    print("-" * 82)
    for name, res, detail in results:
        print(f"{name:<36} {res:<12} {detail}")
    print(f"\nmutations: {caught}/{len(results)} CAUGHT")
    restored = hashlib.sha256(SRC.read_bytes()).hexdigest() == before
    print(f"tree restored byte-identical: idempotency.py={restored}")
    return 0 if caught == len(results) and restored else 1


if __name__ == "__main__":
    raise SystemExit(main())
