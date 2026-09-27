#!/usr/bin/env python3
"""Non-vacuity probe for the replay-verification check (Gap B).

Every guard must **fail when its property is broken** and the tree must come back
byte-identical. Method: mutate the source in place, run the guard, restore from a
byte copy, verify the sha256.

**Run when no other pytest is in flight** — two concurrent pytest processes race on
the shared `pytest-of-<user>` temp directory (F112).
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
GUARD = "tests/reliability/test_replay_verification.py"
PYTEST = [".venv/bin/python", "-m", "pytest", GUARD, "-q", "-p", "no:cacheprovider", "--tb=no"]

DIGEST = REPO / "wisp/core/replay_digest.py"
SESSION = REPO / "wisp/core/session.py"
RUNTIME = REPO / "wisp/core/runtime.py"


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _sub(text: str, old: str, new: str) -> str:
    if old not in text:
        raise SystemExit(f"PROBE DEFECT — anchor not found: {old[:80]!r}")
    return text.replace(old, new, 1)


def mutations() -> list[tuple[str, pathlib.Path, str, str]]:
    """(name, file, mutated_text, the test that must fail)."""
    d = DIGEST.read_text(encoding="utf-8")
    s = SESSION.read_text(encoding="utf-8")
    r = RUNTIME.read_text(encoding="utf-8")
    out: list[tuple[str, pathlib.Path, str, str]] = []

    # 1 — the verification becomes a no-op: the tampering tests must fire.
    out.append(("verify-noop", DIGEST,
                _sub(d, "    actual = projection_digest(messages)\n    if actual != recorded:",
                     "    actual = projection_digest(messages)\n    if False:"),
                "TestTheDivergenceIsRaised"))

    # 2 — the projection stops covering content: the digest can no longer see a
    #     changed message.
    out.append(("projection-blind-to-content", DIGEST,
                _sub(d, '_MESSAGE_FIELDS = ("role", "content", "tool_call_id", "name")',
                     '_MESSAGE_FIELDS = ("role",)'),
                "TestTheProjection"))

    # 3 — the projection becomes ORDER-BLIND, so a reorder stops being visible.
    #     Two earlier attempts were probe defects, both worth recording:
    #     `sorted([...])` is invalid Python (dicts are not orderable) so it
    #     errored instead of failing the named test; and `reversed(calls)`
    #     reverses BOTH sides of the comparison, so the distinction the test
    #     checks survives and the guard is correctly green. **A mutation must
    #     break the property, not merely change the code.**
    out.append(("projection-order-blind", DIGEST,
                _sub(d, "            for call in calls\n",
                     '            for call in sorted(calls, key=lambda c: str(c.get("id", "")))\n'),
                "TestTheProjection"))

    # 4 — the exception becomes the run-level type, so the loop would swallow it.
    out.append(("divergence-is-run-level", DIGEST,
                _sub(d, "class ReplayDivergence(RuntimeError):",
                     "from wisp.core.contracts import WispError\n\n\nclass ReplayDivergence(WispError):"),
                "TestTheDivergenceIsRaised"))

    # 5 — the apply branch stops verifying.
    out.append(("apply-does-not-verify", SESSION,
                _sub(s, "                verify(self.messages, recorded, session_id=self.session_id,\n                       sequence=event.sequence_num)",
                     "                pass"),
                "TestTheCheckRunsDuringReplay"))

    # 6 — a missing digest key stops raising.
    out.append(("missing-key-tolerated", SESSION,
                _sub(s, "                if not isinstance(recorded, str) or not recorded:\n                    raise ReplayDivergence(",
                     "                if False:\n                    raise ReplayDivergence("),
                "TestTheCheckRunsDuringReplay"))

    # 7 — the PRODUCTION path stops journaling a digest (the check goes dark).
    out.append(("production-stops-recording", RUNTIME,
                _sub(r, "                if journal_fidelity and self.session_repo is not None:\n                    from wisp.core.replay_digest import projection_digest",
                     "                if False:\n                    from wisp.core.replay_digest import projection_digest"),
                "TestTheCheckIsNotVacuous"))
    return out


def main() -> int:
    before = {p: sha(p) for p in (DIGEST, SESSION, RUNTIME)}
    originals = {p: p.read_text(encoding="utf-8") for p in (DIGEST, SESSION, RUNTIME)}
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

    print(f"{'mutation':<32} {'result':<12} target")
    print("-" * 78)
    for name, res, detail in results:
        print(f"{name:<32} {res:<12} {detail}")
    print()
    print(f"mutations: {caught}/{len(results)} CAUGHT")
    restored = {p.name: sha(p) == before[p] for p in before}
    print("tree restored byte-identical: " + ", ".join(f"{k}={v}" for k, v in restored.items()))
    if not all(restored.values()):
        print("TREE NOT RESTORED — investigate before trusting anything above", file=sys.stderr)
        return 3
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
