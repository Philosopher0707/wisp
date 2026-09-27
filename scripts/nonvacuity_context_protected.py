#!/usr/bin/env python3
"""Non-vacuity probe for the protected-context rule.

Every guard must **fail when its property is broken** and the tree must come back
byte-identical. Run when no other pytest is in flight (F112).

Mutation 1 is the one that matters: it restores the *original* behaviour — truncate
instead of raise — which is exactly the defect the spec names.
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
GUARD = "tests/reliability/test_context_protected.py"
PYTEST = [".venv/bin/python", "-m", "pytest", GUARD, "-q", "-p", "no:cacheprovider", "--tb=no"]
SRC = REPO / "wisp/core/context_trust.py"


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _sub(text: str, old: str, new: str) -> str:
    if old not in text:
        raise SystemExit(f"PROBE DEFECT — anchor not found: {old[:70]!r}")
    return text.replace(old, new, 1)


def mutations() -> list[tuple[str, str, str]]:
    s = SRC.read_text(encoding="utf-8")
    out: list[tuple[str, str, str]] = []

    # 1 — THE ORIGINAL DEFECT, restored: a protected item that does not fit is
    #     truncated and the run continues on a prompt nobody wrote.
    out.append(("truncate-instead-of-raise",
                _sub(s, "        if item.protected or item.tag in PROTECTED_TAGS:\n"
                        "            raise ContextOverflow(",
                     "        if False:\n            raise ContextOverflow("),
                "TestProtectedItemsAreNeverShortened"))

    # 2 — the tag mechanism disabled: the system prompt stops being protected.
    out.append(("system-tag-unprotected",
                _sub(s, "PROTECTED_TAGS = frozenset({TrustTag.SYSTEM})",
                     "PROTECTED_TAGS = frozenset()"),
                "TestProtectedItemsAreNeverShortened"))

    # 3 — the explicit mechanism disabled: the task stops being protected.
    out.append(("protected-field-ignored",
                _sub(s, "        if item.protected or item.tag in PROTECTED_TAGS:",
                     "        if item.tag in PROTECTED_TAGS:"),
                "TestProtectedItemsAreNeverShortened"))

    # 4 — the machine-readable code removed.
    out.append(("code-removed",
                _sub(s, '    code = "context_overflow"', '    code = "something_else"'),
                "TestProtectedItemsAreNeverShortened"))

    # 5 — the exception stops being an exception (a silent sentinel instead).
    out.append(("exception-becomes-subclass-of-nothing",
                _sub(s, "class ContextOverflow(RuntimeError):",
                     "class ContextOverflow(object):  # noqa"),
                "TestProtectedItemsAreNeverShortened"))
    return out


def main() -> int:
    before = sha(SRC)
    original = SRC.read_text(encoding="utf-8")
    caught = 0
    results: list[tuple[str, str, str]] = []
    try:
        for name, mutated, expected in mutations():
            SRC.write_text(mutated, encoding="utf-8")
            proc = subprocess.run(PYTEST, cwd=REPO, capture_output=True, text=True)
            if proc.returncode == 0:
                results.append((name, "MISSED", "the guard passed on a broken tree"))
            elif expected not in proc.stdout:
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
    print()
    print(f"mutations: {caught}/{len(results)} CAUGHT")
    restored = sha(SRC) == before
    print(f"tree restored byte-identical: context_trust.py={restored}")
    if not restored:
        print("TREE NOT RESTORED", file=sys.stderr)
        return 3
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
