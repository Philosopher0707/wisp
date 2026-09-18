"""Documentation drift guard: guidance docs must not reference deleted code.

Context (see PHASE_FINDINGS_REMEDIATION_REPORT.md / F19, F20):

`AGENTS.md` and `ARCHITECTURE.md` are read by coding agents as instructions, so
a wrong fact there is more costly than a wrong fact anywhere else. Both had
drifted:

  - `_MockIO` — a test double that **does not exist** (the real patterns are a
    local `_MockRuntime` plus `StringIO`)
  - `DelegationAnalyzer` / `multi_agent/delegation.py` — **deleted** in
    `11fc949`
  - test counts off by ~20% (310 files / 4,200 tests vs the actual 357 / 5,374)
  - "tests mirror source paths" — they do not; `tests/` is flat

These tests fail if a deleted symbol is referenced again, or if a documented
count drifts beyond a tolerance. They deliberately check *facts*, not prose.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

GUIDANCE_DOCS = ("AGENTS.md", "ARCHITECTURE.md", "CLAUDE.md")

# Symbols that no longer exist in the codebase. A guidance doc naming one is
# telling an agent to use something that will fail on import.
DELETED_SYMBOLS = (
    "DelegationAnalyzer",
    "_MockIO",
    "agentic_graph",
)


def _doc(name: str) -> str:
    return (REPO / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("doc", GUIDANCE_DOCS)
@pytest.mark.parametrize("symbol", DELETED_SYMBOLS)
def test_guidance_docs_do_not_reference_deleted_symbols(doc, symbol):
    path = REPO / doc
    if not path.exists():
        pytest.skip(f"{doc} not present")
    assert symbol not in path.read_text(encoding="utf-8"), (
        f"{doc} references `{symbol}`, which no longer exists in the codebase"
    )


def test_deleted_module_is_not_in_the_architecture_file_tree():
    text = _doc("ARCHITECTURE.md")
    assert "delegation.py" not in text, (
        "ARCHITECTURE.md lists multi_agent/delegation.py, which was deleted"
    )


# ── Documented counts must roughly match reality ─────────────────────

def _actual_test_files() -> int:
    return len(list((REPO / "tests").glob("test_*.py")))


def _documented_test_file_count(doc: str) -> int | None:
    m = re.search(r"(\d{3})\s+test files", _doc(doc))
    return int(m.group(1)) if m else None


def test_agents_md_test_file_count_is_within_tolerance():
    documented = _documented_test_file_count("AGENTS.md")
    if documented is None:
        pytest.skip("AGENTS.md no longer states a test-file count")
    actual = _actual_test_files()
    # Generous tolerance: the point is to catch order-of-magnitude drift
    # (the stale figure was 310 against 357 actual), not to force a bump on
    # every added file.
    assert abs(documented - actual) <= 40, (
        f"AGENTS.md claims {documented} test files; actual is {actual}. "
        "Update the doc rather than deleting this guard."
    )


def test_guidance_docs_do_not_claim_tests_mirror_source_paths():
    """They do not — `tests/` is flat, plus two topic subdirectories."""
    text = _doc("AGENTS.md")
    assert "Tests mirror source paths" not in text, (
        "AGENTS.md claims tests mirror source paths; they live in a flat tree"
    )
    # And the corrected statement should be present.
    assert "flat" in text.lower()
