# PHASE_CURRENT_FINDINGS.md — the findings register

**Mission:** the corpus governance layer, Deliverable 1 of 4.
**Baseline:** `HEAD` = `e892a7d`, `main`. Tree = 29 WIP entries (the user's, §8) — untouched.
**Type:** a **derived register**. It introduces **no decision**.
**Deliverable:** `CURRENT_FINDINGS.md` (271 lines, 104 rows) + `scripts/derive_current_findings.py`
+ `tests/reliability/test_current_findings_pins.py` (28 tests).

---

## 1. Why the page exists

The corpus governs the **code's** decisions (61 ADRs, append-only) and the **authorities'**
current state (`CURRENT_AUTHORITIES.md`, derived, guarded). It does not govern its own
**work-in-progress**. The findings log has **104 entries in three homes**, and no single place
answers *"what is the current status of `Fn`?"*:

| home | findings | how a reader is sent there |
|---|---|---|
| `WISP_MIGRATION_STATUS.md` §23 | F1–F44, **F64–F74** | §23's own note says *"F64–F71 resume here"* |
| `WISP_MIGRATION_STATUS.md` §0 | F45–F63 | the per-mission sections |
| `CONTEXT.md` §0 + the phase reports | **F75–F104** | no pointer at all |

**Measured, not read.** The table below is the `git`-checked count of findings that lead a
table row in the ledger:

```
F-numbers leading a table ROW in WISP_MIGRATION_STATUS.md: 74
F1..F104 with NO ledger row (30): 75 … 104
```

So **30 of the 104 findings have no ledger row**, and §23's note undercounts its own table by
three (it says F64–F71; the table runs to F74). A reader told *"the ledger is the findings log"*
cannot find 30 of them.

---

## 2. What was built

**`CURRENT_FINDINGS.md`** — one row per finding, sorted by number:

`id · title · status · class · decided_by · source · tripwire`

Three sections: **(a)** the status vocabulary stated once, with the sources' own words mapped
onto it; **(b)** the defect-class index, grouped by the recurring class, each pointing at where
the class is stated; **(c)** the open count by status, measured at generation.

**`scripts/derive_current_findings.py`** — the generator, **committed**.

This is the one design decision worth arguing. The page declares itself *derived, regenerated,
never edited*; without a committed generator, "regeneration" means *by hand*, which is the drift
the page exists to prevent. `CONTEXT.md` §0.0.9 records finding **F75** — *"an instrument that
cannot be committed is not a re-runnable measurement"* — and `scripts/` is the repository's
committed convention (`scripts/next_*.py` are tracked). So the derivation is in `scripts/`, and
the guard's strongest property is that **regenerating reproduces the page byte-for-byte**,
modulo the commit it names.

**Every status is transcribed, not inferred.** The `source` cell is `path:line` **and quotes the
source's own status words**. A row cannot say `FIXED` while the row it cites says `NOT FIXED`
without the contradiction being visible on the line itself — and `test_every_row_quotes_its_source`
requires that quotation to be present.

---

## 3. The register, at a glance

| status | count |
|---|---|
| `OPEN` | 15 |
| `FIXED` | 49 |
| `CLOSED` | 34 |
| `SUPERSEDED` | 1 |
| `DECIDED` | 3 |
| `DEFECT-PIN` | 1 |
| `UNRESOLVED` | 1 |
| **total** | **104** |

**Not closed — `OPEN` + `UNRESOLVED` — 16 of 104.** Six of those are the **host**, not the
architecture: `F11`, `F80`, `F88` (a declared dependency that cannot be installed here), `F36`
(the full suite cannot run in one process), `F17` (a flaky test), `F75` (an instrument that
cannot be committed). The remaining ten are architectural and are the ones a decision would move.

**The defect-class index** found the corpus's recurring classes to be real and countable: 11
findings are `instrument-defect`, 13 are `record-integrity`, 10 are `unwired-control`, 10 are
`duplicated-authority`, 10 are `measurement-method`, 6 are `false-success`, 5 are `model-vs-path`,
4 are `count-canonicality`, 1 is `record-gap`. The instrument-defect class is broken out by
sub-case, because `CONTEXT.md` §10 names six and only four carry an `F`-number.

---

## 4. §Findings — what the page could not pin

The page may **record** a conflict; it may not resolve one. Five conflicts and three unpinnable
claims were found. **No new `F`-number was coined** — numbering a finding is a decision about
the corpus's log.

### 3.1 `F77` has no source at all

`CONTEXT.md:215` names `PHASE_DAG_RETIREMENT.md` as the report that found `F77`. **That report
contains no `F77` and has no findings section.** Its §7.1 records an instrument defect — a bare
string scan over `wisp/**/*.py` that counted this deliverable's own docstring as a caller — under
**no number**. So `F77` is cited and undefined, and it is very likely the §7.1 defect that was
never numbered.

This is the mission's most valuable single result: **a finding whose source does not exist.** It
is recorded as `UNRESOLVED` with a §Findings entry, never guessed.

### 3.2 Five findings carry two contradictory statuses

| finding | the ledger row says | the later source says |
|---|---|---|
| `F8` | *"NOT FIXED — environment gap"* | the **same row's** later text: *"**FIXED 2026-09-24** — provisioned offline"* |
| `F37` | *"**NOT FIXED — newly exposed**"* | `CONTEXT.md` §0's phase table: *"**F37 FIXED**"* |
| `F39` | *"**NOT FIXED — reported, not repaired**"* | `CONTEXT.md` §0: *"**`ADR-0038 SATISFIED`**"* |
| `F89` | *"**Named, not fixed**"* | `CONTEXT.md` §0: *"2.1 **CLOSED**"* |
| `F19` | *"Verified absent; recorded"* | `PHASE_LAYER_B_BOUNDARY.md:281`: all three tracked, predating P5 by 152 commits |

`F8` is the sharpest: the row's **headline and its own body** disagree. The row was amended in
place rather than superseded, so its status cell is self-contradictory. Each row here states the
status the *later* source records and lists the conflict — it does not resolve it.

### 3.3 `F75–F104` have no ledger row, and `F64–F71` have two homes

Recorded above and in the page's §Findings. `F64–F71` exist at both `:112-119` (stale, *"RECORDED,
NOT DECIDED"*) and `:1882-1889` (*"DECIDED — ADR-0049"*). The ledger itself flags this at `:64-68`
as *"the navigability defect this chain was about and should not be extended"* — and then extended
it. **The ledger was not edited**: reorganising 30 rows is an editorial decision that
`WISP_MIGRATION_STATUS.md` §23 already declined once (*"Recorded, not reorganized"*).

---

## 5. Verification

| check | result |
|---|---|
| the guard | **28 passed** |
| **non-vacuity** | **11/11 CAUGHT** by page mutation; **2/2 REFUSED** by the generator; tree restored byte-identical (sha256) |
| the generator's own refusals | a `|` in a cell → refused; a missing finding → refused |
| the register's totality | 104 rows, `F1`…`F104`, sorted, one row each |
| sources | 104/104 resolve to a real `path:line` in range |
| tripwires | 59 rows name a tripwire; all resolve to a real test file (and class/test where named) |

### 5.1 Two defects found by *running*, both the instrument's

**The probe's first run reported 2 WRONG-TEST.** Both were defects in the *probe*, not the guard,
and both are the class this corpus cares about:

1. **`findings-dropped` replaced only `- **F77.**`** — and the guard correctly stayed green,
   because the entry's own prose also names `F77`. *A mutation that does not break the property it
   names is not a mutation.* Fixed by removing every `F77` from the section.
2. **`conflict-resolved` inserted `"The correct status is …"`** — capitalised — while the guard
   matches the lowercase phrase. The guard was right; the probe was testing a different string.

**The generator's first run emitted a broken page.** `F37`'s and `F39`'s source cells quoted a
table row verbatim, and the quoted `|` became a column break — so the guard parsed **102** rows
and reported *"2 finding(s) have no row: F37, F39"*. The register looked complete and was two rows
short. Fixed at the source (no pipe in a cell) **and** the generator now refuses such a table
(`_check_cells`), because a generator that emits a broken table is worse than one that refuses.

---

## 6. The residual

- **`F77` remains undefined.** The page records it; nothing here decides what it was. If the
  intended referent is `PHASE_DAG_RETIREMENT.md` §7.1's instrument defect, numbering it is a
  decision for the corpus's log owner — this page may not.
- **The ledger's split is unamended.** 30 findings remain invisible to a reader sent to the
  ledger. The page's §Findings says so; moving them is an editorial decision.
- **Five status conflicts stand.** The page states the later source's status and lists the
  conflict. Resolving them means amending ledger rows, which is an editorial decision.
- **The register is a projection of its generator, not of the corpus.** A status changed in a
  ledger row without regenerating will not appear on the page — but the guard's reproducibility
  test fails on a hand-edit, so the page cannot drift *silently*; it can only go *stale*, which is
  what the generation banner and the named commit exist to expose.

---

## 7. What this deliverable did not do

- **No decision.** No `F`-number coined, no conflict resolved, no ledger row amended.
- **No ADR.** A derived page cites decisions; it does not make them. Nothing here surfaced a
  conflict that requires an ADR — the conflicts are between *records*, and the corpus's rule for a
  stale record is to regenerate or amend it, not to decide it.
- **No production code touched.** The change is two new files (the page, the generator) and one
  test file.
- **The user's WIP was not staged.** `wisp/core/graph/__init__.py` is still the only modified
  tracked file, and it is unchanged.
