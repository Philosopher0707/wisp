#!/usr/bin/env python3
"""Non-vacuity probe for the redaction point. Run when no other pytest is in flight (F112).

Mutation 1 is the one that matters: it makes the trace point legal, which is the
defect the module exists to refuse.
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
GUARD = "tests/reliability/test_redaction_point.py"
PYTEST = [".venv/bin/python", "-m", "pytest", GUARD, "-q", "-p", "no:cacheprovider", "--tb=no"]
SRC = REPO / "wisp/runtime/redaction.py"


def _sub(t: str, old: str, new: str) -> str:
    if old not in t:
        raise SystemExit(f"PROBE DEFECT — anchor not found: {old[:70]!r}")
    return t.replace(old, new, 1)


def mutations() -> list[tuple[str, str, str]]:
    s = SRC.read_text(encoding="utf-8")
    return [
        # 1 — the prompt point becomes legal: the model loses a value it must USE.
        ("prompt-point-allowed",
         _sub(s, "    if point is RedactionPoint.PROMPT:", "    if False:"),
         "TestThePromptPointIsRefused"),
        # 2 — the trace stops being redacted: the secret is written at rest, which
        #     is the failure the whole decision exists to prevent.
        ("recorded-value-unredacted",
         _sub(s, "    return redact(value, point=RedactionPoint.TRACE, redactor=redactor)",
              "    return value"),
         "TestTheTracePointIsTheLiveOne"),
        # 3 — a SECOND place applies the redactor, so a trace can acquire an
        #     unredacted value by the back door.
        ("second-redaction-site",
         _sub(s, "def recorded_value(",
              "def _bypass(value, redactor=None):\n    return redactor(value) if redactor else value\n\n\ndef recorded_value("),
         "TestTheFloor"),
        # 4 — the cost is denied: a redacted trace claims to be replayable.
        ("redacted-trace-claims-replayable",
         _sub(s, "    return not redacted", "    return True"),
         "TestTheCostIsReal"),
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

    print(f"{'mutation':<28} {'result':<12} target")
    print("-" * 72)
    for name, res, detail in results:
        print(f"{name:<28} {res:<12} {detail}")
    print(f"\nmutations: {caught}/{len(results)} CAUGHT")
    restored = hashlib.sha256(SRC.read_bytes()).hexdigest() == before
    print(f"tree restored byte-identical: redaction.py={restored}")
    return 0 if caught == len(results) and restored else 1


if __name__ == "__main__":
    raise SystemExit(main())
