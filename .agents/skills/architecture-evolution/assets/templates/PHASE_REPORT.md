# PHASE REPORT — <subject>

**Status:** complete / partial / blocked · **Date:** <date>
**Contract:** `<path>` · **Decision record:** `<ADR id>`

## 1. Mission result

What was asked, and what is now true. One paragraph, no adjectives.

## 2. Baseline

| Property | Value |
|---|---|
| Revision | |
| Working tree | |
| Pre-existing work | `<disclaimed, and not reverted>` |
| Stable failure set | |

## 3. Files changed

| File | Nature | This phase's contribution |
|---|---|---|
| | production / test / docs | |

> Where a file also carried pre-existing uncommitted work, say so. A `git diff`
> shows far more than this phase's work, and the reader must be told which part
> is whose.

## 4. Exact production changes

Per file: the change, and why it is the smallest seam that works. Include the
verbatim shape of anything a reviewer must judge (the gate, the bound, the flag).

## 5. The seam implemented

```
<diagram: owner -> what crosses -> receiving site -> effect>
```

## 6. Ownership proof

The claims that the architecture is intact, each with the **mechanism** that
proves it (a ratchet, a zero-diff, a test) — not with an assurance.

| Claim | Proof |
|---|---|
| the owner still owns the decision | |
| no second authority | |
| no global mutable state | |
| default path unchanged | |

## 7. Bound semantics

Maximum, owner, reset, exhaustion behaviour, and the edge interaction.

## 8. Defects found and fixed during the phase

| # | Defect | How it surfaced | Fix |
|---|---|---|---|
| | | | |

## 9. Test matrix

| Category | Cases | Result |
|---|---|---|
| standard / boundary / conflict / failure / replay / concurrency / regression | | |

## 10. Regression

The **set**, not a count. Baseline, method, NEW, GONE, and the method caveat if
the two-run intersection was not achieved.

## 11. Rollback

| Level | Switch | Effect | Default |
|---|---|---|---|
| | | | |

## 12. Safety / non-goal verification

| Must not have changed | Verified by |
|---|---|
| `<protected authority>` | `<zero-diff>` |

## 13. Deviations and findings

Anything that differed from the contract, and anything discovered. **Include the
defects you introduced and fixed** — naming them first is the strongest evidence
that the rest was checked.

## 14. Remaining gaps

| Gap | Status (deferred / blocked / pre-existing) | What would close it |
|---|---|---|
| | | |

## 15. Acceptance checklist

`<copy from references/checklists/phase-completion.md, with each box resolved>`

## 16. Final status

```text
implemented and verified : <list>
deferred, with reasons    : <list>
blocked                   : <list, or "nothing">
```

## Rules for this report

- **State the honest limits.** "Built and tested, but nothing on the live path
  calls it yet" is a complete and useful sentence. Letting a phase's shape imply
  more than was delivered is what makes such a report worthless.
- **Never claim a suite passed** unless it was run. Report the set.
- **Attribute every failure set difference.** Unexplained movement is the finding.
