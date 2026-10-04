# Checklist — phase completion

Work this before writing "complete" anywhere. A `no` means the phase is
**partial**, and the report must say which item is open.

## Delivered

- [ ] The contract or decision record exists and the implementation matches it clause by clause.
- [ ] The change is reachable from a **production entry point**, proven by a test that drives it.
- [ ] The flag exists, defaults **off**, and OFF reproduces previous behaviour exactly.
- [ ] The rollback path is documented and needs no code change.
- [ ] The bound is implemented, owned, reset, and surrenders honestly.

## Verified

- [ ] Every verification category that applies was run (standard / boundary / conflict / failure / replay / concurrency / regression).
- [ ] Skipped categories are named, with the reason.
- [ ] The **conflict matrix** has no untested row.
- [ ] The failure modes (absent / unavailable / raises / stale / duplicated) are tested.
- [ ] Replay reproduces the live decision, including after a restart.
- [ ] Concurrency isolation is tested or proven by AST locality.

## Architecture preserved

- [ ] Every protected authority is **zero-diff**, or its delta is stated and pinned.
- [ ] The pre-existing gate is still consulted **first** (asserted by ordering).
- [ ] Exactly one producer of the decision (asserted by a non-vacuous AST ratchet).
- [ ] No new global mutable state (asserted by AST).
- [ ] No second authority: the new component's imports contain only the value/message types it needs.
- [ ] No existing ratchet was weakened.

## Regression

- [ ] The stable failure set (intersection of two runs) was used as the baseline.
- [ ] The post-change set was compared as **sets**, in **both** directions.
- [ ] Every NEW failure is either fixed or attributed with evidence.
- [ ] Every GONE failure is attributed (a set that shrank is also a change).
- [ ] If the two-run intersection was not achieved, the weaker method is **stated**.
- [ ] No claim of "the suite passes" was made.

## Recorded

- [ ] The phase report exists, with the **honest limits** section filled in.
- [ ] Every deferral has a **reason** and a **tripwire**.
- [ ] Defects found and fixed during the phase are listed (including your own).
- [ ] Findings are recorded in the repository's ledger or findings log.
- [ ] The decision record's **reversal condition** is written.
- [ ] Docs updated where a symbol or behaviour changed (and only where it changed).

## Scope

- [ ] Nothing outside the listed files changed.
- [ ] Every discovered-but-unrelated problem is recorded, not fixed.
- [ ] Pre-existing uncommitted work was neither reverted nor absorbed silently.
- [ ] The next seam is named, or "no seam remains" is stated explicitly.
