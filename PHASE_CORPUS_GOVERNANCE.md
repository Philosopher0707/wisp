# PHASE_CORPUS_GOVERNANCE.md — the corpus governance layer

**Mission:** the corpus governance layer, Deliverable 4 of 4 — the entry point, and the mission's report.
**Baseline:** `HEAD` = `e892a7d`, `main`. Tree = 29 WIP entries (the user's, §8) — untouched throughout.
**Type:** four **derived registers** plus their guards. The mission introduces **no decision**.
**Commits:** `10e923a` (D1), `2a8cac9` (D2), `5580257` (D3), and this one (D4).

---

## 1. What the mission was, and what it found

The corpus governed the **code's** decisions (the ADR log, append-only) and, since
`CURRENT_AUTHORITIES.md`, the **authorities'** current state. It did not govern its **own
work-in-progress**: the open findings, the open items, the flags and their interactions, the phase
reports' residuals. Each lived in prose scattered across a 2,264-line handoff, a ledger with a
§0/§23 split, and 40+ phase reports. That is the same hazard `CURRENT_AUTHORITIES.md` exists to
close, one level up.

**Four deliverables, each a derived page plus a guard:**

| # | page | rows | generator | guard | tests | non-vacuity |
|---|---|---|---|---|---|---|
| D1 | `CURRENT_FINDINGS.md` | 104 | `scripts/derive_current_findings.py` | `test_current_findings_pins.py` | 28 | **11/11** + 2/2 refusals |
| D2 | `CURRENT_OPEN_ITEMS.md` | 102 | `scripts/derive_current_open_items.py` | `test_current_open_items_pins.py` | 32 | **15/15** + 3/3 refusals |
| D3 | `CURRENT_FLAGS.md` | 26 | `scripts/derive_current_flags.py` | `test_current_flags_pins.py` | 38 | **16/16** + 4/4 refusals |
| D4 | *(the entry point)* | — | — | `test_derived_registers_entry_point.py` | 31 | **10/10** |

**Every probe restored the tree byte-identical** (sha256, verified in each probe's own output).

---

## 2. Deliverable 4 — the entry point

**What was added:**

1. **`CONTEXT.md`'s "The derived registers"** — a table naming all four pages, the question each
   answers, and each one's generator and guard, plus the rule that governs all four: *a derived page
   cites, it does not decide; when two sources disagree, both are cited and the disagreement goes in
   the page's own §Findings.* It also states the precedent — `CURRENT_AUTHORITIES.md` recording
   **F64**/**F65** and refusing to decide them.
2. **A bidirectional pointer in each of the four banners**, so a reader who lands on any one can
   reach the other three.
3. **A 31-test guard** asserting: all four pages exist; each banner carries the regeneration rule and
   disclaims decision authority; each banner names the other three and **does not name itself twice**;
   the section names all four with a generator and a guard each; and **the section is a pointer, not a
   summary** — it must not restate a flag default, a finding row or an item state, and must be short
   enough to be a pointer.

**No new page.** The section points at pages; it does not add one.

**`AGENTS.md` carries no document map** — checked (`grep` for the four filenames returns two
incidental mentions in prose, not a map), so there was nothing to add there. Recorded because the
brief says *"`AGENTS.md` if it carries a document map"*, and the honest answer is that it does not.

---

## 3. The findings — F105–F114, all reported, none repaired

Every one is a defect in the **artifacts**, and repairing any of them is an editorial decision this
mission was not authorised to make. The derived pages' own §Findings sections record them without
numbering them, as the brief requires; the numbering is here, in the report, as the corpus's practice.

| # | finding | class |
|---|---|---|
| **F105** | **`F77` has no source.** `CONTEXT.md:215` names `PHASE_DAG_RETIREMENT.md` as the report that found it; **that report contains no `F77` and has no findings section.** Its §7.1 records an instrument defect — a bare string scan over `wisp/**/*.py` that counted this mission's own docstring as a caller — **under no number**. | record-gap |
| **F106** | **30 of 104 findings have no ledger row.** §23's table runs `F1`–`F44` **and `F64`–`F74`** (its note says *"F64–F71 resume here"* — undercounting its own table by three); §0 carries `F45`–`F63`; **`F75`–`F104` exist only in `CONTEXT.md`'s phase table and in the phase reports.** | record-integrity |
| **F107** | **Two id namespaces collide.** §12's `F1`–`F5` are **open-item** ids and collide with the findings log's `F1`–`F104`; `M4` names **three** different things (see §12's row, the governance layer, and `WISP_MIGRATION_STATUS.md:2041`). | record-integrity |
| **F108** | **The state vocabulary is not one vocabulary, and a reason is recorded as a state.** The ledger states six words; §12 uses seven; the brief lists a third set. **`DECIDED`** names *why* an item is finished, not *that* it is — the one measured instance. The inverse holds too: **`BLOCKED` is defined and used by no source.** | vocabulary |
| **F109** | **The brief's paraphrase of ADR-0002 is wrong and would have decided the wrong way.** The brief says *"read once, at the composition point"*; ADR-0002 says *"read at the **consumption site**"*, and its Consequence clause says why. Under the paraphrase, `verification_loop`'s two sites and `turn_spans`'s two sites become violations of a rule the corpus does not have. | model-vs-path |
| **F110** | **Five of the brief's nineteen flag names do not resolve.** `verification_gate` and `graph_mutation` appear **nowhere in `wisp/`**; three others are **not `WispConfig` fields** (read from `os.environ`, so invisible to `config.py`, `wisp doctor`, and any config consumer). | record-integrity |
| **F111** | **The canonical block is not measurable on a starved host — and it fails worse than F36 records.** See §4. | measurement-method |
| **F112** | **Two concurrent pytest processes race on the shared temp dir** — `PermissionError: EEXIST: mkdir '…/T/pytest-of-philosopher'`, **869 errors** from one overlapping run. §6 attributes this to the shim; the cause is contention. | measurement-method |
| **F113** | **The pattern's own page has no committed generator.** `CURRENT_AUTHORITIES.md` declares *"REGENERATE, DO NOT EDIT IN PLACE"* and gives a manual §6 procedure — so its "regeneration" is by hand, which is **F75**'s class, and the one asymmetry among the four. | instrument-defect |
| **F114** | **`PHASE_EXTERNAL_INPUT_PATH.md` was absent from §13's document index** — the previous landing's own report, unreachable from the index. Corrected here, with the omission recorded. | record-integrity |

---

## 4. F111 — the canonical block, and why no count is quoted

**This is the mission's most important result, and it is about the instrument, not the subject.**

The block was re-measured in **every** deliverable's change, as **F85** requires. The measurement
**does not reproduce**, and the evidence is decisive:

| run | what else was running | result |
|---|---|---|
| the pre-change baseline (54 files) | nothing | **1505 tests — 1504 pass, 1 fails** (F38) |
| the block + D1's guard (55 files) | nothing | **154 failed, 1379 passed** in 359 s |
| the block **without** D1's guard (54 files) | nothing | **154 failed, 1351 passed** in 406 s |
| the three files the block failed, **in isolation** | nothing | **114 passed in 2.6 s** |

**The same 154 failures occur with and without the new guard, and the failing files pass in
isolation** — so the failures are **the host, not the change**. Free memory measured at **60–87 MB of
16 GB**, and the block's wall time tripled (118 s → 360 s).

**F36 describes the milder half of this.** It says the kernel *kills* the run (`exit=137`). Here the
run **completes and reports a wrong answer** — 154 failures that do not exist — which is the
instrument-defect class one level up: *the instrument reports its own defect as a result about the
subject.* So `CONTEXT.md` §11's rule is necessary but not sufficient:

> **Never quote a count from prose — and never quote one from a block run on a starved host.**

A count's *scope* includes the host's free memory (**F94**). `CONTEXT.md` §11 now carries the warning,
the four measurements and the reason, and the heading is annotated
**⚠️ NOT MEASURABLE ON THIS HOST**.

**A second host condition, also found by running (F112).** Two concurrent pytest processes race on
`/private/var/folders/…/T/pytest-of-<user>` and produce `PermissionError: EEXIST` at fixture setup —
**869 errors** from a single overlapping run, which reads as a code failure and is not. §6 says *"The
WorkBuddy shim blocks pytest's temp `mkdir`"*; the measured cause is **contention between processes**.
**Run the block alone.**

---

## 5. Verification

| check | result |
|---|---|
| D1's guard | **28 passed**; non-vacuity **11/11** CAUGHT, generator **2/2** refused |
| D2's guard | **32 passed**; non-vacuity **15/15** CAUGHT, generator **3/3** refused |
| D3's guard | **38 passed**; non-vacuity **16/16** CAUGHT, generator **4/4** refused |
| D4's guard | **31 passed**; non-vacuity **10/10** CAUGHT |
| the four guards together | **129 passed** in one process |
| the canonical block | **re-measured in every change; NOT MEASURABLE on this host** (§4) |
| `ruff` on the changed files | no new errors; `ruff check wisp/` unchanged (no `wisp/` file was touched) — **F71** |
| the tree | every probe restored it **byte-identical** (sha256) |

### 5.1 Seven instrument defects, all found by running

Every one is a defect in **a probe or a generator** — the class `CONTEXT.md` §10 catalogs — and none
was found by reading:

1. **D1's generator emitted a broken table.** `F37`'s and `F39`'s source cells quoted a ledger row
   verbatim, and the quoted `|` became a column break — so the guard parsed **102** rows and reported
   *"2 finding(s) have no row: F37, F39"*. **The register looked complete and was two rows short.**
   Fixed at the source, **and** the generator now refuses such a table (`_check_cells`).
2. **D1's probe reported 2 WRONG-TEST.** One mutation replaced only `- **F77.**` while the entry's own
   prose also names `F77`; another inserted a capitalised phrase the guard matches in lowercase. Both
   were the probe's defects and the guard was right to stay green.
3. **D2's probe reported 2 WRONG-TEST.** A single deleted row does not break a floor of 50 — the
   **count** check is the primary protection and the floor is a backstop. Fixed by adding a mutation
   that *does* break the floor and one that puts an id in both tables.
4. **D3's `capability_filtering` pin named a module, not a location.** The guard rejected it; it
   became `—` with a §Findings entry.
5. **D3's `rule-authority-dropped` mutation was incomplete** — it removed the rule's inline
   attribution to ADR-0002 but left the section heading's. **The guard's own test was also too weak**
   (`"ADR-0002" in §(a)` is satisfied by the heading alone), so both were fixed.
6. **D3's `paraphrase-finding-dropped` replaced the wrong occurrence** — `"read once"` appears twice
   and the whole-page replace hit §(a) first. Scoped to §Findings.
7. **D4's question-parsing regex expected quote-wrapped questions**; the section uses italics. The
   guard's own test was wrong, not the page.

**And two `read_at` pins were rejected by the generator on the first run** (`wisp/autonomous.py:79`,
`:84`) because those lines name the flag through a **constant** (`STRICT_DERIVATION_ENV`), not
literally. The check was right; the pins moved to the lines that do name it.

---

## 6. The residual

- **No count for the canonical block** (§4). The mission's `F85` obligation was discharged by
  re-measuring; the honest result is that the measurement is host-dependent.
- **F105–F114 stand.** Each is recorded in `CONTEXT.md` §0.0.17 and in the relevant page's §Findings.
  Repairing any of them — amending a ledger row, renaming a namespace, unifying a vocabulary, giving
  `CURRENT_AUTHORITIES.md` a generator — is an editorial decision.
- **The registers are projections of their generators, not of the corpus.** A source edited without
  regenerating makes a page *stale*, not wrong; each guard's reproducibility test fails on a hand
  edit, so staleness cannot happen *silently*. What none of them can do is notice a source that no
  longer exists — which is what their §Findings sections are for.
- **D3's eight unpinned read sites.** Recorded as `—` rather than naming a module the page cannot
  verify. Tracing them means auditing the transports and the prompt assembler.
- **The Tier 2 items are untouched**, as the brief requires: M8's removal, budget tuning, ADR-0016's
  measurement.

---

## 7. What the mission did not do

- **No ADR.** A derived page cites decisions; none of the four made one, and nothing here surfaced a
  conflict that requires one. The disagreements found are between **records**, and the corpus's rule
  for a stale record is to regenerate or amend it, not to decide it.
- **No production code.** No `wisp/` file was touched.
- **No flag added, no default changed.**
- **No ledger row, §12 row, ADR or phase report edited.** Every one of F105–F114 is *recorded*.
- **The user's WIP was never staged.** `wisp/core/graph/__init__.py` is still the only modified
  tracked file, and its diff is unchanged.
