#!/usr/bin/env python3
"""Non-vacuity probe for the injection scan.

Every guard must **fail when its property is broken** and the tree must come back
byte-identical. Run when no other pytest is in flight (F112).

Mutation 1 is the important one: it **reproduces the 36% failure** by adding the
markers an intuition-built detector reaches for, so the corpus is shown to catch
the mistake the guardrail exists to avoid — rather than merely asserting that it
would.
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
GUARD = "tests/reliability/test_injection_scan.py"
PYTEST = [".venv/bin/python", "-m", "pytest", GUARD, "-q", "-p", "no:cacheprovider", "--tb=no"]

SCAN = REPO / "wisp/core/injection_scan.py"
CORPUS = REPO / "tests/fixtures/injection_corpus.py"


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _sub(text: str, old: str, new: str) -> str:
    if old not in text:
        raise SystemExit(f"PROBE DEFECT — anchor not found: {old[:70]!r}")
    return text.replace(old, new, 1)


def mutations() -> list[tuple[str, pathlib.Path, str, str]]:
    s = SCAN.read_text(encoding="utf-8")
    c = CORPUS.read_text(encoding="utf-8")
    out: list[tuple[str, pathlib.Path, str, str]] = []

    # 1 — THE 36% MISTAKE, reproduced. Bare words an intuition-built detector
    #     reaches for; they appear in this repository's own output constantly.
    out.append(("benign-trips--the-36%-mistake", SCAN,
                _sub(s, "TIER2_BASE64 = re.compile",
                     'TIER1_PATTERNS = TIER1_PATTERNS + (\n'
                     '    ("intuition-system-prompt", re.compile(r"system prompt", re.I)),\n'
                     '    ("intuition-ignore", re.compile(r"ignore", re.I)),\n'
                     ')\n\nTIER2_BASE64 = re.compile'),
                "TestTheFalsePositiveRate"))

    # 2 — a marker REMOVED. (Renaming it, which the first version of this probe
    #     did, is not a deletion: the marker still fires, so the guard was right
    #     to stay green. A mutation must break the property, not merely change the
    #     code.)
    lines = s.splitlines(keepends=True)
    i = next(k for k, l in enumerate(lines) if '("exfiltrate-credentials"' in l)
    out.append(("marker-deleted", SCAN, "".join(lines[:i] + lines[i + 3:]),
                "TestTheRecall"))

    # 3 — the marker set shrunk below the floor, by replacing the whole tuple.
    tstart = s.index("TIER1_PATTERNS: tuple")
    tend = s.index("#: Tier 2")
    out.append(("marker-set-shrunk", SCAN,
                s[:tstart] + "TIER1_PATTERNS: tuple = ()\n\n" + s[tend:],
                "TestEveryMarkerIsCovered"))

    # 4 — SUSPECT shown instead of withheld: fail-closed is abandoned, and an
    #     attacker wraps every payload in quotes.
    out.append(("suspect-shown--fail-open", SCAN,
                _sub(s, "        return ScanResult(\n            Verdict.SUSPECT, 1, markers,",
                     "        return ScanResult(\n            Verdict.CLEAN, 1, markers,"),
                "TestTheRecall"))

    # 5 — the discussing-context tier disabled ENTIRELY. The first version of this
    #     mutation only disabled the span check and left the quote check, so a
    #     quoted marker was still SUSPECT and the tiering property survived; the
    #     guard was right to stay green and the *probe* was wrong.
    out.append(("discussing-tier-disabled", SCAN,
                _sub(s, "def _is_discussed(text: str, start: int, end: int,\n"
                        "                  spans: Iterable[tuple[int, int]]) -> bool:",
                     "def _is_discussed(text: str, start: int, end: int,\n"
                     "                  spans: Iterable[tuple[int, int]]) -> bool:\n"
                     "    return False\n\n\ndef _unused(text, start, end, spans) -> bool:"),
                "TestThePrecisionLimitIsCountedSeparately"))

    # 6 — scan stops being total.
    out.append(("scan-raises-on-empty", SCAN,
                _sub(s, '    if not text or not text.strip():\n        return ScanResult(Verdict.CLEAN, 0, reason="empty")',
                     '    if not text or not text.strip():\n        raise ValueError("empty")'),
                "TestTheScanIsTotal"))

    # 7 — a corpus sample deleted: the cheapest way to make a suite pass.
    cstart = c.index('    Sample(\n        "csv-metrics"')
    cend = c.index("    ),\n", cstart) + len("    ),\n")
    out.append(("corpus-sample-deleted", CORPUS, c[:cstart] + c[cend:],
                "TestTheCorpusCannotShrink"))
    return out


def main() -> int:
    before = {p: sha(p) for p in (SCAN, CORPUS)}
    originals = {p: p.read_text(encoding="utf-8") for p in (SCAN, CORPUS)}
    caught = 0
    results: list[tuple[str, str, str]] = []
    try:
        for name, target, mutated, expected in mutations():
            target.write_text(mutated, encoding="utf-8")
            proc = subprocess.run(PYTEST, cwd=REPO, capture_output=True, text=True)
            if proc.returncode == 0:
                results.append((name, "MISSED", "the guard passed on a broken tree"))
            elif expected not in proc.stdout:
                results.append((name, "WRONG-TEST", f"failed, but not in {expected}"))
            else:
                results.append((name, "CAUGHT", expected))
                caught += 1
            target.write_text(originals[target], encoding="utf-8")
    finally:
        for p, text in originals.items():
            p.write_text(text, encoding="utf-8")

    print(f"{'mutation':<34} {'result':<12} target")
    print("-" * 80)
    for name, res, detail in results:
        print(f"{name:<34} {res:<12} {detail}")
    print()
    print(f"mutations: {caught}/{len(results)} CAUGHT")
    restored = {p.name: sha(p) == before[p] for p in before}
    print("tree restored byte-identical: " + ", ".join(f"{k}={v}" for k, v in restored.items()))
    if not all(restored.values()):
        print("TREE NOT RESTORED", file=sys.stderr)
        return 3
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
