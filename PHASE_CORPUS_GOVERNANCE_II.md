# PHASE_CORPUS_GOVERNANCE_II.md — closing the ten findings

**Mission:** the corpus governance layer, II — the decisions the first mission reported and did not take.
**Predecessor:** `PHASE_CORPUS_GOVERNANCE.md`, `HEAD` = `3763d4d`.
**Baseline:** `main` at `3763d4d`. Tree = the 29 WIP entries in `CONTEXT.md` §8 plus one tracked
modification (`wisp/core/graph/__init__.py`) — untouched throughout.
**Type:** one **decision** (an ADR), one **build** (a generator), one **measurement** (a disposition),
and the **edits** the decision authorises.

> **§1 (the mission's summary) is written last, in §5's commit.** Sections §2–§5 are written with
> their deliverable, so each commit carries its own report.

---

## §2 — Deliverable 1: ADR-0062, the eight editorial decisions

**Commit:** `docs: ADR-0062 — the corpus's editorial decisions` (this one).

### What was decided, and why it is one ADR

Eight findings, one subject — **the corpus's own shape** — and one property: **none of them changes
what the product does.** Stated separately each is a paragraph; stated together they are a policy.
That is the reason for one ADR rather than eight.

| rule | finding | the decision |
|---|---|---|
| **R1** | `F106` | **`CURRENT_FINDINGS.md` is the canonical register for a finding's status.** The ledger stays append-only and **un-backfilled**; its §23 note is corrected to name the register and to state its own actual range (`F64`–`F74`, not `F64`–`F71`). |
| **R2** | `F107` | **The id namespaces are disambiguated by prose prefix** — `FIND-Fn` for a finding, `ITEM-Fn` for §12's rows — and the three meanings of `M4` are annotated once. Neither namespace is renamed. |
| **R3** | `F108` | **The ledger's six words govern, with one reason column.** `DECIDED` is a **reason** and moves to it (the state becomes `COMPLETE`); **`BLOCKED` is kept as a state** and its emptiness is recorded as *"no item is currently in this state"*. |
| **R4** | `F109` | **The reading rule is ADR-0002's, recorded verbatim** — *read at the **consumption site*** — and the paraphrase *"read once, at the composition point"* is **named as the defect**. |
| **R5** | `F110` | **`verification_gate` and `graph_mutation` are wrong names**, not aliases and not deprecated names. The real flags are `verification_loop` and `task_graph`. **No alias is added.** |
| **R6** | `F112` | **No two pytest processes run concurrently**, and a block run passes **`--basetemp`**. A method rule and a method flag — not a code change. |
| **R7** | `F114` | **A phase report enters the document index in the change that creates it.** `F104`'s class, stated as a rule. |
| **R8** | `F113` (policy half) | **A derived page must have a committed generator**, with an **append-only** section permitted if the guard asserts it is unchanged. |

### The four decisions where the brief's recommendation was tested, not adopted

The brief said its recommendations were hypotheses. Driven:

1. **`F106` — backfill or not.** The brief recommends **not** backfilling, and the reason survives
   driving: thirty rows duplicated across two append-only files is **a second producer of one fact**,
   the defect class this corpus names most often. Adopted, with the reversal trigger the brief did
   not name: *a consumer that parses the ledger for a finding's status.* A canonical source no
   consumer can reach is a citation, not a source.
2. **`F108` — `BLOCKED`.** The brief recommends **keeping** the word. Driven, the brief's own
   framing is slightly off: it says *"a vocabulary with a word whose meaning is defined is not the
   defect `F108` names"*, which is right, but the honest phrasing is *"no item is currently in this
   state"* — an observation about the items, not about the word. Adopted with that correction.
3. **`F109` — the paraphrase's provenance.** The brief says the paraphrase is wrong and does not say
   where it came from. **Driven: it has a source.** ADR-0056's Consequences reads *"Both flags still
   default OFF, read once, independently"* — a **local** statement about the objective path, which
   reads both of *its* flags at one composition point. The paraphrase **conflates a local
   description with ADR-0002's general rule.** That is a materially better decision than "the
   paraphrase is wrong": it names the mechanism by which a wrong rule propagates, so the next reader
   can recognise it.
4. **`F112` — the cause.** The brief accepts the corpus's diagnosis. **Driven, the corpus's own
   diagnosis is wrong**: `CONTEXT.md` §6 attributed the failure to *"the WorkBuddy shim blocks
   pytest's temp `mkdir`"*, and the measured cause is **contention between processes**. R6 states
   both, because a wrong cause makes a rule unlearnable.

### What the ADR refused to decide

- **What `F77` was.** That is a measurement — §4.
- **Whether the ledger gains a row.** It does not; R1 says so.
- **Any production meaning.** No flag default, no gate, no authority's scope. **No `wisp/` file is
  touched by any rule in ADR-0062**, and R6 changes how a measurement is *run*, not what it measures.
- **Whether a finding is created or renumbered.** F105–F114 already exist; the ADR decides their
  disposition.

### Verification

| check | result |
|---|---|
| `WISP_ARCHITECTURE_DECISIONS.md` | **62 ADRs**; `## ADR-0062` present; index row `\| 0062 \|` present |
| the eight rules | **8/8** present as `**Rn — …` |
| each rule cites its finding | **8/8** — `F106`, `F107`, `F108`, `F109`, `F110`, `F112`, `F114`, and `F113` in R8's heading |
| each rule names its reversal trigger | **8/8** — each Rn ends with *Reversal trigger.* |
| the docs guards | `test_doc_drift.py` + `test_current_authorities_pins.py` → **46 passed** |
| production behaviour | **none decided**; no `wisp/` file touched |

---

## §3 — Deliverable 2: `CURRENT_AUTHORITIES.md` gains a committed generator

**Commit:** `feat: CURRENT_AUTHORITIES.md is generated — scripts/derive_current_authorities.py (corpus governance II, D2)`.
**Authorised by:** ADR-0062 R8 (`F113`, the policy half, landed in D1). This closes the build half.

### What was built

`scripts/derive_current_authorities.py`, following the three siblings' shape (`_check()` → refuse
with exit 2 and write nothing; `render()` → the page). What each part of the page now is:

| part | how it is produced | what refuses it |
|---|---|---|
| header — commit, date, ADR range | `git rev-parse --short HEAD`, `git log -1 --format=%cs`, `max(## ADR-NNNN)` in the log | — (read, never written) |
| §1 six authorities | transcribed data (`AUTHORITIES`) | every `path:line` pin: in range, non-blank, and naming within ±2 lines an identifier its own line names — **the guard's rule, applied before writing**; floor ≥30 pins / ≥15 checkable |
| §1 ADR chains | transcribed, **resolved against the log** | a cited ADR that does not exist, is `SUPERSEDED` in the index, or is **superseded/replaced by an ADR the chain omits** (clause-scoped: `ADR-0043` superseding `ADR-0041 R3/R7`) |
| §3 rows and results | **`goal.PRECEDENCE`, read at render time** | a row whose `derive_goal_state` branch returns a different `GoalState` member (AST), or whose selector inputs drive the arbiter elsewhere; row 0 no longer frozen |
| §3 code pins | **`derive_goal_state`'s AST** — the `if` carrying `# row N`, row 0's `already_recorded` guard, the final `return`; the `PRECEDENCE` span likewise | a branch set that disagrees with `PRECEDENCE`'s rows |
| §2, §4 | transcribed data | pins, as §1 |
| **§5** | **`SECTION_5`, emitted unchanged — append-only** | an empty `SECTION_5`; the generator never writes a finding |
| §6 | rewritten: the procedure **is** the generator | — |

**Driven, the body is the old page's body.** Before writing, the rendered page was diffed against the
hand-maintained one: **every §1–§5 line is byte-identical — all eight matrix rows and their eight
AST-derived code pins included** — except §3's first line (it now says the matrix is *generated*). The
only other changes are the header and §6, both intended.

### The guard — extended, not weakened

`tests/reliability/test_current_authorities_pins.py`: **35 → 45 tests**, every existing property kept.
Written **RED-first**: before the generator existed, **10 failed, 35 passed**.

| new test | property |
|---|---|
| `test_the_generator_is_not_ignored_by_git` | F75's actual failure mode (`.gitignore` excluded the instrument) — *committable* is the property, so the test runs on the uncommitted tree it is written in |
| `test_the_banner_names_the_generator` | as the siblings' banners do |
| `test_regenerating_reproduces_the_page` | byte-for-byte, modulo **every** `` `sha` `` and the `Generated <date>` field (the lesson of `3763d4d`) |
| `test_the_generator_accepts_the_current_tree` | `_check() == []` |
| `test_the_generator_refuses_a_stale_pin` / `…_a_matrix_row_the_code_does_not_return` / `…_an_unresolved_adr_chain` | each refusal, driven through the generator's own check, with a floor asserting the unmutated input passes |
| `test_the_matrix_reads_precedence_at_render_time` | the observation point is `goal.PRECEDENCE` itself, not a copy (**F96**) — monkeypatching it moves §3's row |
| `test_the_header_range_is_the_log_extent` | **F97** mechanised in the strong form: the range *equals* the log's extent |
| `test_section_5_is_the_generators_record_verbatim` | a hand edit to §5 fails |
| `test_no_finding_is_lost_against_the_committed_page` | **append-only as a property**: compared against `HEAD`'s page, not a list in the test, so a new finding does not fire it (**F92**) |

`test_derived_registers_entry_point.py` now also requires the entry-point section to name
`scripts/derive_current_authorities.py`, and `CONTEXT.md`'s table says so (it read *"(hand-regenerated;
see its §6)"*).

### Found by running

1. **The header was stale again — `F97`'s class, one ADR later.** The page said `ADR-0001 … ADR-0061`;
   ADR-0062 had landed in D1. The existing guard could not see it — it checks the range *covers every
   ADR the page cites*, and the page cites none past 0054. The new range test and the generated
   header close it: the range is now read, not typed.
2. **My own §5 comparison was off by a newline** (the section split keeps the `\n` before `---`). The
   guard failed on a correct page; the test was fixed, the page was not touched — an instrument
   defect of the kind §10 catalogs, caught on the first run.

### A scope boundary, measured and stated

The chain check enforces **supersession** (`supersedes` / `replaces`), not **amendment**. Measured over
the index, three amendment edges fall outside a chain: `ADR-0036 → ADR-0035` (§1.1), `ADR-0040 →
ADR-0039 R2` (§1.2, which cites R5) and `ADR-0051 → ADR-0016`'s 3b condition (§1.3, whose *Cannot
decide* is "whether to gate"). Each amends a clause the chain does not rely on, so enforcing amendment
would fire three times on a correct page — **F92**'s nuisance class. Recorded here, not numbered.

The old §6 also asked for a *historical* differential of the arbiter against an earlier revision. That
check lives, executable, in `tests/reliability/test_precedence_canonical.py`; the generator does not
duplicate it.

### Verification

| check | result |
|---|---|
| RED before the generator | **10 failed, 35 passed** |
| the guard | **45 passed** |
| the four register guards + entry point + `test_doc_drift.py` | **179 passed** in one process (`--basetemp`, alone — ADR-0062 R6) |
| **non-vacuity** | **7/7**: NV1 hand-edit of §1, NV2 edit of §5, NV3 hand-edit of the range → **CAUGHT** by the guard; NV4 a finding dropped from `SECTION_5` and regenerated → **CAUGHT** (append-only); NV5 a stale pin, NV6 a chain omitting `ADR-0043`, NV7 the arbiter's row-6 branch returning `GOAL_FAILED` → **REFUSED** by the generator, **page not written**. Tree restored **byte-identical** (sha256) after each |
| production code | **none touched** — NV7 mutated `wisp/core/goal.py` and restored it byte-identical |
