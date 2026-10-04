"""Mutation-test the guardrail suite.

A green guardrail suite is only evidence if each gate turns RED when the defect
it exists to catch is actually present. This plants the real defect behind each
gate, asserts that gate fails, then restores the file byte-for-byte.

Usage:  python tests/mutation_check_guardrails.py
Exit 0 = every gate caught its planted defect and every file was restored.

Run with a clean tree: this rewrites files in place, so a dirty tree means a
failed restore can take your work with it.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
SUITE = "tests/test_guardrails.py"

# (label, relative file, kind, payload, gate that MUST go red)
MUTATIONS = [
    (
        "F-gate: unused import in agent/",
        "agent/ui/formatter.py",
        "append",
        "\n\nimport xml.etree.ElementTree as _planted_unused  # PLANTED -> F401\n",
        "test_no_lint_errors_in_shipping_packages",
    ),
    (
        "S105 gate: hardcoded credential",
        "wisp/async_utils.py",
        "append",
        '\n\nPLANTED_DB_PASSWORD = "hunter2"\n',
        "test_hard_security_rules_are_clean",
    ),
    (
        "S110 gate: silent except in an authority path",
        "wisp/auth/decision.py",
        "append",
        "\n\ndef _planted_silent():\n    try:\n        print(1)\n    except Exception:\n        pass\n",
        "test_silent_except_is_absent_from_authority_paths",
    ),
    (
        "placeholder gate: undocumented bare-pass function",
        "wisp/async_utils.py",
        "append",
        "\n\ndef _planted_bare_pass(value):\n    pass\n",
        "test_no_undocumented_placeholder_implementations",
    ),
    (
        "NotImplementedError gate: TODO-raising stub",
        "wisp/async_utils.py",
        "append",
        '\n\ndef _planted_todo():\n    raise NotImplementedError("TODO: implement this")\n',
        "test_no_notimplementederror_placeholders",
    ),
    (
        "stale-allowlist gate: audited S105 line shifts",
        "wisp/core/acceptance.py",
        "insert_after_first_line",
        "\n",
        "test_audited_exceptions_are_not_stale",
    ),
]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_suite() -> tuple[int, str]:
    proc = subprocess.run(
        [PY, "-m", "pytest", SUITE, "-p", "no:cacheprovider", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _apply(path: Path, kind: str, payload: str, original: bytes) -> None:
    if kind == "append":
        with path.open("a", encoding="utf-8") as fh:
            fh.write(payload)
    elif kind == "insert_after_first_line":
        lines = original.decode("utf-8").splitlines(keepends=True)
        lines.insert(1, payload)
        path.write_text("".join(lines), encoding="utf-8")
    else:  # pragma: no cover - programming error
        raise ValueError(f"unknown mutation kind {kind!r}")


def main() -> int:
    baseline_rc, baseline_out = _run_suite()
    print(f"baseline suite rc={baseline_rc} (must be 0)")
    if baseline_rc != 0:
        print("FAIL: baseline is not green, so mutation results would be meaningless")
        print(baseline_out[-2000:])
        return 1

    escaped: list[str] = []
    for label, rel, kind, payload, gate in MUTATIONS:
        path = REPO_ROOT / rel
        original = path.read_bytes()
        before = _sha(path)
        try:
            _apply(path, kind, payload, original)
            rc, out = _run_suite()
            caught = rc != 0 and gate in out
            print(f"[{'CAUGHT' if caught else '*** ESCAPED ***'}] {label}")
            if not caught:
                escaped.append(label)
                seen = [ln for ln in out.splitlines() if ln.startswith(("FAILED", "ERROR"))]
                print(f"    gate={gate} rc={rc} failing={[s.strip() for s in seen]}")
        finally:
            path.write_bytes(original)
            if _sha(path) != before:
                escaped.append(f"{label} (file not restored)")
                print(f"    *** RESTORE FAILED for {rel} ***")
            else:
                print(f"    restored {rel}")

    print()
    if escaped:
        print(f"RESULT: {len(escaped)} problem(s):")
        for item in escaped:
            print(f"  - {item}")
        return 1
    print(f"RESULT: all {len(MUTATIONS)} gates caught their planted defect; all files restored.")
    return 0


if __name__ == "__main__":
    sys.exit(main())