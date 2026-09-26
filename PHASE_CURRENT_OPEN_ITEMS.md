# PHASE_CURRENT_OPEN_ITEMS.md — the open-items register

**Mission:** the corpus governance layer, Deliverable 2 of 4.
**Baseline:** `HEAD` = `e892a7d`, `main`. Tree = 29 WIP entries (the user's, §8) — untouched.
**Type:** a **derived register**. It introduces **no decision**.
**Deliverable:** `CURRENT_OPEN_ITEMS.md` (267 lines, 102 rows) +
`scripts/derive_current_open_items.py` + `tests/reliability/test_current_open_items_pins.py` (32 tests).

---

## 1. Why the page exists

`CONTEXT.md` §12 is the live open-items table, and it is a **handoff section in a 2,201-line
document**. It has been stale: its `G1` row said `OPEN` until 2026-09-25 while §0.0.14 recorded
it closed — **F98**. Nothing compares §12 to §0.0, or to the ledger, or to the ADRs' residuals.

**Four sources name open items, and nothing reconciles them:**

| source | what it names |
|---|---|
| `CONTEXT.md` §12 | the live open-items table (two sub-tables: 21 legacy items, 17 migration items) |
| `CONTEXT.md` §0.0.x | the per-mission narratives, where a closure is usually recorded |
| `WISP_MIGRATION_STATUS.md` | the phase ledger's non-`COMPLETE` rows and each phase's deferred items |
| each ADR's `### Residuals`, each phase report's residual section | the residuals a decision named |

---

## 2. What was built

`CURRENT_OPEN_ITEMS.md` — **102 rows**, in two tables:

- **§The register — items not yet closed** (64 rows)
- **§The closed items — kept, with their closure's source** (38 rows)

A page that deleted its closed items could not be checked for a regression: an item that
reopens would simply vanish from the open table with nothing to compare against.

Columns: `id · title · state · blocked_by · tripwire · source`. Plus §(a) the state vocabulary,
§(b) *the reasons are not states*, §(c) the open count by state, and §Findings.

| state | count |
|---|---|
| `NOT STARTED` | 57 |
| `IN PROGRESS` | 1 |
| `PARTIAL` | 6 |
| `BLOCKED` | 0 |
| `COMPLETE` | 38 |
| `SUPERSEDED` | 0 |
| **total** | **102** |

**`scripts/derive_current_open_items.py`** is committed, for the same reason as Deliverable 1's:
the page declares itself derived, and `CONTEXT.md` §0.0.9's **F75** says *"an instrument that
cannot be committed is not a re-runnable measurement."* The guard's strongest property is that
regenerating reproduces the page byte-for-byte, modulo the commit it names.

---

## 3. §(b) — the reasons are not states, and the vocabulary is not one vocabulary

The brief says the state column uses *"the ledger's own vocabulary"*. **Measured, there are at
least three vocabularies**, and the brief's own list is not the ledger's:

| source | its words |
|---|---|
| `WISP_MIGRATION_STATUS.md:41` — **the stated authority** | `NOT STARTED` · `IN PROGRESS` · `COMPLETE` · `BLOCKED` · `PARTIAL` · `SUPERSEDED` |
| the mission brief | `OPEN` · `IN_PROGRESS` · `BLOCKED` · `DECIDED` · `CLOSED` · `SUPERSEDED` |
| `CONTEXT.md` §12 | `OPEN` · `IN_PROGRESS` · `CLOSED` · `DECIDED` · `DONE` · `FIXED` · `Accepted` · `Unresolved` |

**Three of the brief's six words are absent from the ledger's** (`OPEN`, `DECIDED`, `CLOSED`), and
**two of the ledger's are absent from the brief's** (`NOT STARTED`, `PARTIAL`). The register uses
the ledger's six and maps every other source's word onto them.

**Two measured defects, recorded not repaired:**

1. **A reason recorded as a state.** `DECIDED` — §12 uses it as a state word for `M8`, `M11` and
   `Layer C`. It names *why* the item is finished (an ADR decided it) rather than *that* it is.
   The ledger has no such word. Mapped to `COMPLETE`, with the ADR cited in the source cell.
2. **A state with no members.** `BLOCKED` is defined by the ledger and used by **no** source.
   Every blocked item is recorded as `IN PROGRESS` or `NOT STARTED` plus a stated obstacle. The
   `blocked_by` column exists precisely for that, which is why the vocabulary's `BLOCKED` word is
   redundant — the inverse of defect 1.

The brief also names `BLOCKED_ON_PRECONDITION` as the example of a coined reason-state.
**Measured: that string appears nowhere in this corpus.** The real instance is `DECIDED`.

---

## 4. §Findings — what the page could not pin

### 4.1 The `§12`-against-`§0.0` cross-check found no disagreement — and that is a result

The brief requires the cross-check because the two have disagreed (**F98**). §12's rows for `E`,
`G1`, `R1b` and `W1` all read `CLOSED`, and §0.0's narrative for each records the same closure
with the same ADR. The page records that **the check was run and found nothing**, rather than
claiming the sources cannot disagree — it was derived *after* F98's repair, so it cannot see the
pre-repair state.

### 4.2 Two id namespaces collide

- **`F1`–`F5`.** §12 uses them as **open-item** ids (Phase 10's defect ledger). The findings log
  and `CURRENT_FINDINGS.md` use `F1`–`F104` as **finding** ids. §12's `F1`
  (`metadata["_budget"]` write-only) and the findings log's `F1`
  (`test_canonical_execution_state.py` already exists) are different things with the same name.
- **`M4`.** §12's `M4` is *ADR-0004 revisited*; this mission's brief and
  `PHASE_10_M4_GOVERNANCE_UNWIRED.md` use *M4* for the **governance layer**, which §12 carries as
  row **`E`**; `WISP_MIGRATION_STATUS.md:2041`'s `M4` is a third usage. **F99** records the same
  collision from the other side.

Recorded, not repaired — renaming either namespace is an editorial decision. Every row carries its
source, which disambiguates.

### 4.3 *"Every ADR's named residuals"* resolves to six ADRs, not 61

Measured: `### Residuals` headings occur in `WISP_ARCHITECTURE_DECISIONS.md` at **ADR-0053, 0054,
0057, 0058, 0059 and 0061** — eight headings, six ADRs. ADR-0001–0052's residuals, where they
exist, are in their phase reports. The brief's wording implies all 61 ADRs carry a residual list;
six do. The page states the measured scope.

---

## 5. Verification

| check | result |
|---|---|
| the guard | **32 tests** (31 pass; the one failure is `test_the_generator_is_committed`, which the commit satisfies) |
| non-vacuity | §6 |
| sources | 102/102 resolve to a real `path:line` in range |
| tripwires | every named tripwire resolves to a real test file |
| the vocabulary | every row's state is one of the ledger's six; §(a) maps every other source's word |
| the counts | §(c)'s counts sum to the tables' row count |

### 5.1 A host condition that changed the measurement, twice

**This is the deliverable's most important finding, and it is about the instrument, not the
subject.**

The canonical block was measured three times in this session:

| run | what else was running | result |
|---|---|---|
| the pre-change baseline | nothing | **1505 tests — 1504 pass, 1 fails** (F38) |
| the block, while isolation probes ran | two other pytest processes | **869 errors** — `PermissionError: EEXIST: mkdir '…/T/pytest-of-philosopher'` |
| the block, alone, on a starved host (60 MB free) | nothing | **154 failed, 1379 passed** in 364 s |

**The 154 failures are not code.** The failing files pass in isolation — **114 passed in 2.6 s**
— and the same block passed 1504/1 before the changes. The cause is host memory pressure:
`CONTEXT.md` §6 records **F36** (*"the full suite cannot be run in one process on a
memory-starved host"*), and this host is at **60–87 MB free of 16 GB**.

**F36 describes the milder half.** It says the kernel *killed* the run (`exit=137`). Here the run
**completed and reported a wrong answer** — 154 failures that do not exist. That is the
instrument-defect class one level up: *the instrument reports its own defect as a result about the
subject.* And it refines `CONTEXT.md` §11's rule: **"never quote a count from prose — run the
block"** is necessary but not sufficient, because **the block itself can lie under memory
pressure**. The count has a *scope* (**F94**), and the scope includes the host's free memory.

**Consequence for this deliverable: no canonical block count is quoted.** The honest report is
that the block was re-measured in this change (as **F85** requires) and the measurement is
**NOT RELIABLE on this host at this time**, with the evidence above. Quoting `1533/1379/154` would
be quoting a defect in the environment as a fact about the change.

**A second, independent host condition, also found by running:** two concurrent pytest processes
race on the shared `pytest-of-<user>` temp directory and produce `PermissionError: EEXIST` at
fixture setup — **869 errors** from a *single* overlapping run. `CONTEXT.md` §6 says *"The WorkBuddy
shim blocks pytest's temp `mkdir`"*; the measured cause is contention, not the shim. Recorded
because it is trivially reproducible and would otherwise be read as a code failure.

---

## 6. The residual

- **The vocabulary is not unified.** The register maps three vocabularies onto the ledger's six.
  Unifying them means editing §12 and the ledger's stated vocabulary — an editorial decision.
- **The id collisions stand.** `F1`–`F5` and `M4` each name two different things.
- **The register's scope is four sources, not every source.** A residual named only inside a
  phase report's prose (not in a residual section) would not be found. The page states its scope
  rather than claiming totality.
- **The block's count is unmeasured for this change.** See §5.1.

---

## 7. What this deliverable did not do

- **No decision.** No new item id, no resolution of a disagreement, no source edited.
- **No ADR.** Nothing here surfaced an ADR-worthy conflict: the disagreements are between
  *records*, and the corpus's rule for a stale record is to regenerate or amend it.
- **No production code touched.** Two new files (the page, the generator) and one test file.
- **The user's WIP was not staged.**
