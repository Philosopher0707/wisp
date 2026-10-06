---
name: wisp-test-hermetic
description: Run targeted wisp tests hermetically (empty HOME, bounded), regenerate the derived pages and run the pin tests after code edits. Use before every wisp commit or PR.
---

# wisp-test-hermetic

CI is the full-suite arbiter (a local full run stalls). Run only what the change touches, with an empty `HOME`.

1. Collect first: `pytest --co -q <files>`. A collection error aborts a whole run (a missed consumer once cost a 3,000-test run).
2. Choose files by symbol: `grep -rlE --include='*.py' "<symbols>" tests` (always `*.py`: a JSON fixture makes pytest refuse the list).
3. Run, bounded, hermetic, in the background for anything long:
   `mkdir -p ../freshhome; HOME=$PWD/../freshhome perl -e 'alarm 600; exec @ARGV' pytest <files> -q -p no:cacheprovider`
4. Always include `tests/reliability` (pin tests) and `tests/test_layer_direction.py` when adding a module or exception.
5. `ruff check wisp tests` and bare `mypy` (CI scope, not one file).
6. After code edits regenerate the derived pages: `python scripts/derive_register.py`, `derive_current_flags.py`,
   `derive_current_authorities.py`. "DERIVATION REFUSED" means a pin moved: re-anchor the line number in the matching
   `scripts/derive_*.py` table (a new exception class needs a `register` row), regenerate, rerun the pin tests.

Rules: RED first (watch the new test fail for the right reason), then fix. Mutation-check the fix on a **copy** of the tree
(break one line, expect exactly its own test to fail, restore, `cmp`). Never `# noqa`, `# type: ignore`, loosen a test or add
an allowlist entry to get green. Do not switch a worktree's branch while a background run uses it.
Tests must not depend on the developer's machine: reproduce with an empty `HOME` and no `.venv`.
