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
