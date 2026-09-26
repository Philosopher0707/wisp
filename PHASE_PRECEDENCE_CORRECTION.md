# PHASE — PRECEDENCE CORRECTION (Deliverable 1)

**Deliverable:** **ADR-0049** — the canonical precedence table is `goal.PRECEDENCE` (eight rows, 0–7);
every older numbering is historical and is resolved **by content**.
**Type:** record update. **No code change. No behaviour change.**
**Baseline:** `dd21f6d`. **Predecessor:** `PHASE_CURRENT_AUTHORITIES.md` (F-1, F-2).

---

## 1. What was produced

| Artefact | What it is |
|---|---|
| **ADR-0049** in `WISP_ARCHITECTURE_DECISIONS.md` (48 → 49) | R1 (canonical table, resolve by content), R2 (INCOMPLETE is not a row condition; ratify two cells), R3 (scope ADR-0047 R3). The canonical table restated in full, verbatim from `goal.PRECEDENCE`; the resolution rules; the behaviour-change evidence; five rejected alternatives. Index row added. |
| `tests/reliability/test_precedence_canonical.py` | **27 tests.** Pins the ADR's restatement to the code, drives the ratified cells, and reproduces the content mapping. |
| `CURRENT_AUTHORITIES.md` §5 | Regenerated: F-1 and F-2 are now **DECIDED**, with the ADR cited. §3's table notes it is canonical by ADR-0049 R1. |
| `tests/reliability/test_current_authorities_pins.py` | One guard **rewritten** — see §6, it had pinned the old state. |

---

## 2. The correction — the brief's pinned claim was **inverted**

The brief carried a pinned claim about the numbering. It is wrong in three ways, all in the same
direction, and it matters because R1 of this very ADR is the rule that resolves "row N".

**The claim:**

> *"'Row 4' in ADR-0035 §Precedence and 'row 4' in ADR-0047 R1 name the same row by **content**
> (stagnation), which the canonical table expresses as row 4 as well; 'row 4' in ADR-0047's *revised*
> table is the fatal clause and corresponds to the canonical row 5."*

**The measurement.** ADR-0035's conditions were evaluated in order over the full 48-combination input
space and the result compared with what `derive_goal_state` actually returns
(`.workbuddy-ai/memory/post-m13-precedence/row_mapping.py`):

| "row 4" in | Its condition, verbatim | Resolves to |
|---|---|---|
| **ADR-0035 §Precedence** | `may_report_goal_met()` is `False` — **stagnation** | canonical row **5** |
| **ADR-0047 R1** | *"fatal terminal error, and no P3 PASS"* — **the fatal clause** | canonical row **4** |

- The brief said ADR-0035's row 4 resolves to canonical **4** → it resolves to **5**.
- The brief said ADR-0047's row 4 resolves to canonical **5** → it resolves to **4**.
- The brief said the two "name the same row by content (stagnation)" → they are **different content**.
  ADR-0047 R1's own text is unambiguous: *"**Row 4 is now «fatal terminal error, and no P3 PASS»**"*
  (`WISP_ARCHITECTURE_DECISIONS.md:3993`).

**Why this is recorded rather than quietly fixed.** It is the exact defect R1 exists to close: a
numbering that reads plausibly and resolves wrongly. **Had the claim been adopted, R1 would have written
a rule mapping the fatal clause to stagnation** — inverting the two rows the ADR is about. The brief's
own method section anticipates this ("*Before acting on a 'this is missing' or 'this is broken' claim,
drive the code*"), and this is the twelfth instance the corpus has recorded of a plausible claim that
the measurement contradicted.

**One further imprecision, corrected in R2.** The brief's R2 said `terminal_outcome == INCOMPLETE` is
"handled by row 6". Measured, only the **`PASS`** case reaches row 6; an `INCOMPLETE` turn with
`INCONCLUSIVE` or no verdict reaches row **7**, with `FAIL` row **3**, and with stagnation row **5**.
Since R1 mandates resolving by content, an R2 that mis-stated the content would contradict its own R1,
so R2 states the routing as a table.

---

## 3. What was restated

ADR-0049 restates the canonical table **in full and verbatim** — the Condition and Result cells are
`goal.PRECEDENCE`'s own strings, backticked, so a reader needs no other document to resolve any row and
a test can compare the restatement to the code mechanically:

| # | Condition | Result |
|---|---|---|
| 0 | `already-recorded terminal state` | `frozen — never rewritten (ADR-0020)` |
| 1 | `operator cancellation` | `CANCELLED` |
| 2 | `ladder exhausted / human escalation` | `ESCALATED_TO_HUMAN` |
| 3 | `P3 FAIL` | `GOAL_FAILED` |
| 4 | `fatal terminal error, and no P3 PASS` | `GOAL_FAILED` |
| 5 | `may_report_goal_met() is False` | `GOAL_STAGNATED` |
| 6 | `P3 PASS` | `GOAL_MET` |
| 7 | `otherwise — no decisive verdict` | `GOAL_UNVERIFIED` |

Pinned to `wisp/core/goal.py:108-117` (the table) and `wisp/core/goal.py:146-215` (the arbiter; the
arbitration body is `:190-213`).

**The two cells ratified** (R2) and the cell scoped (R3), each driven rather than asserted:

| Cell | Row | Why |
|---|---|---|
| `INCOMPLETE` + `PASS`, **both** `turn_succeeded` states → `GOAL_MET` | 6 | R2 — the objective's evidence decides where it is decisive (ADR-0047's principle, applied to `INCOMPLETE` rather than `FAILED`) |
| `INCOMPLETE` + `PASS` + stagnating → `GOAL_STAGNATED` | 5 | R2 — stagnation vetoes goal-met, and row 5 outranks row 6 |
| `fatal` + `PASS` + stagnating → `GOAL_STAGNATED` | 5 | R3 — a fatal error *with* a `PASS` is **not fatal**; the cell reduces to the `PASS` case |
| `fatal` + no `PASS` + stagnating → `GOAL_FAILED` | 4 | R3 — the rule R3 scopes **still holds** where R3 stated it, and where R3's own test exercises it |

### The driven content mapping

Not read — computed, by evaluating ADR-0035's conditions in order and asking which canonical row answers:

| ADR-0035 row | Covers canonical row(s) |
|---|---|
| 3 — `P3 FAIL ∨ fatal terminal error` | **3** (×12), **4** (×8), **5** (×2), **6** (×2) |
| **4** — `may_report_goal_met() is False` | **5** (×12) |
| 5 — `P3 INCONCLUSIVE ∨ terminal outcome INCOMPLETE` | **6** (×2), **7** (×6) |
| 6 — `turn_succeeded ∧ P3 PASS` | **6** (×1) |

**ADR-0035's table is silent for 3 of 48 combinations** — quantified here for the first time:
`succeeded`+`pass`+`turn_succeeded=False` (canonical row 6 answers it) and `succeeded`+no-verdict in both
`turn_succeeded` states (canonical row 7). That is F-2's non-totality, measured.

Note that the mapping is **not 1:1**: ADR-0035's row 3 alone spans four canonical rows, because its
`∨ fatal terminal error` term covered cells the canonical table now routes by verdict. That is *why*
resolving by number cannot work and R1 resolves by content.

---

## 4. The differential evidence

The ADR claims the code did not move. The evidence is a 48-combination differential of
`derive_goal_state`, produced **before** the ADR and reproduced **after**:

| | |
|---|---|
| **Digest before** | `bb8b54e638a0d1304c5200ad28a0b2ab0bf9616baa1482646ddbbb0d37f1a139` |
| **Digest after** | `bb8b54e638a0d1304c5200ad28a0b2ab0bf9616baa1482646ddbbb0d37f1a139` |
| **Identical** | **yes** |
| **State census (both runs)** | `goal_failed` 20 · `goal_met` 6 · `goal_stagnated` 14 · `goal_unverified` 8 |
| **Delta vs `b9af5f0^`** | 7 of 48 cells moved, in three classes |

```
outcome=failed     verdict=pass  stagnating=False turn_succeeded=False  goal_failed     -> goal_met
outcome=failed     verdict=pass  stagnating=False turn_succeeded=True   goal_failed     -> goal_met
outcome=failed     verdict=pass  stagnating=True  turn_succeeded=False  goal_failed     -> goal_stagnated
outcome=failed     verdict=pass  stagnating=True  turn_succeeded=True   goal_failed     -> goal_stagnated
outcome=incomplete verdict=pass  stagnating=False turn_succeeded=False  goal_unverified -> goal_met
outcome=incomplete verdict=pass  stagnating=False turn_succeeded=True   goal_unverified -> goal_met
outcome=succeeded  verdict=pass  stagnating=False turn_succeeded=False  goal_unverified -> goal_met
```

The seven cells sort into the three classes the ADR names: (a) `FAILED`+`PASS` → `GOAL_MET` (ADR-0047
R1); (b) the `turn_succeeded` demotion (ADR-0047 R2); (c) the four cells R2 and R3 ratify here.

**`wisp/core/goal.py` is byte-identical to `HEAD`** (`git diff --stat` empty), which is the mechanical
reason the digest cannot have moved — and the digest is the evidence that the *documentation* did not
smuggle in a behaviour claim.

Artifacts: `.workbuddy-ai/memory/post-m13-precedence/differential-{BEFORE,AFTER}.txt`.

---

## 5. The guard, and its non-vacuity

`tests/reliability/test_precedence_canonical.py` — **27 tests**, three properties:

1. **The restatement is pinned to the code.** The ADR's table is parsed out of the markdown and compared
   to `goal.PRECEDENCE` row for row, so neither can drift from the other.
2. **The ratified cells behave as ratified** — driven through the real arbiter.
3. **The resolution rules hold** — row 0 is a prior state, row 7 is the fall-through, first match wins —
   and the content mapping is reproducible.

**Falsification.** Seven probes, each restoring the tree byte-identical (sha256 verified), with controls
green either side:

| Probe | Caught |
|---|---|
| P1 the ADR's restated row 6 result drifts | ✅ 1 test |
| P2 the code gains a 9th row | ✅ 2 tests |
| P3 un-ratify `INCOMPLETE`+`PASS` | ✅ 3 tests |
| P4 add an `INCOMPLETE` branch to the arbiter | ✅ 1 test |
| P5 drop the table subsection's location pin | ✅ *(after the gap below was closed)* |
| P6 delete the canonical-table subsection | ✅ 3 tests |
| CONTROL, before and after | ✅ green |

**P5 did not falsify on the first attempt.** My `_adr_section` helper returned the **whole ADR-0049
section**, so the "the table is pinned to its location" check passed on an occurrence in *R1* rather than
in the table's own subsection — deleting the table's pin was invisible. Closed by adding `_subsection`,
which **slices** to the named `####` block. *A test whose name says "the table is pinned" must read the
table's own text.* This is the same class as the previous phase's P5.

---

## 6. A guard that had pinned the old state

`test_current_authorities_pins.py::test_the_page_records_its_unpinnable_claims` asserted the literal
phrase **`"could not pin"`** appeared in the page. That phrase described the page's state *before*
ADR-0049 decided the two claims it had recorded — so **the guard went red when the page was correctly
regenerated**, which is the guard reporting the right change as a failure.

**It was not weakened; it was rewritten to assert the property instead of the phrase.** The property is:
the page may not carry an undispositioned finding. Every `**F-n — …**` in §5 must be marked `DECIDED`
(with an `ADR-NNNN` cited) or `OPEN`, and §5 must not re-argue a decision (that would make the page a
second authority for it). Four probes, all caught:

| Probe | Caught |
|---|---|
| Q1 a finding loses its disposition | ✅ *(after the gap below was closed)* |
| Q2 `DECIDED` with no ADR cited | ✅ |
| Q3 §5 deleted | ✅ |
| Q4 §5 starts re-arguing the decision | ✅ |
| Q5 a finding's name is corrupted | ✅ |

**Q1 did not falsify on the first attempt, and the reason is instructive.** The finding regex was
`\*\*(F-\d+) — ([^*]+)\*\*` — a `+` group. Emptying a disposition made the regex **skip the finding
entirely**, and an empty finding list satisfies a `for` loop, so the test passed **vacuously**. Closed by
widening the group to `[^*]*` **and** adding a floor (`len(findings) >= 2`), because the floor is what
makes the loop non-vacuous even if the format moves again.

Both of this phase's non-falsifying probes were the same defect: **a check that could pass by finding
nothing.** Neither was found by reading the test.

---

## 7. What was **not** changed, and why

- **ADR-0035's and ADR-0047's text is untouched.** The decision log is append-only; editing either would
  destroy the record of which revision a row number belongs to, and ADR-0047's implicit renumbering is
  itself the evidence that a numbering can be introduced and then misread. ADR-0049 names them
  **historical** and restates the table in full so the answer is reachable without either.
- **`wisp/core/goal.py` is untouched.** Verified byte-identical; the differential is the same artifact
  before and after.
- **`goal.PRECEDENCE` was not renumbered** to match ADR-0035. The code's numbering is what the tests, the
  docstrings and the derived page already cite; renumbering would be a behaviour-adjacent change made to
  satisfy a document.
- **The F8 published denial status** was not approached — out of scope by the brief, and its surfaces
  (`_DENIAL_STATUSES`, `OUTCOME_BY_STATUS`, the prompt list) are enumerated in
  `PHASE_F8_ERROR_CLASSIFICATION.md` §4 for its own ADR.
- **The 11 pre-existing ruff errors** were not repaired — unrelated modules, and repairing them would
  sweep a diff into a decision commit. The counts are measured in §8.
- **F38** was not touched.
- **ADR-0047 was not reopened.** R3 is *scoped* to the case its own text and its own test describe;
  the decision, its rejected alternatives and its test are untouched.

---

## 8. Regression

```
env -u PYTHONPATH .venv/bin/python -m pytest <38 files> -q -p no:cacheprovider --tb=no -rf
```

| | |
|---|---|
| **Before Deliverable 1** | 1115 tests — 1114 passed, 1 failed |
| **After Deliverable 1** | **1143 tests — 1142 passed, 1 failed** |
| **The one failure, both runs** | `test_node_identity.py::…::test_a_parallel_round_is_journaled_as_one_exchange_per_call` — **F38**, pre-existing |
| **New failures** | **none** — the failure set is identical in both directions |
| **Now-passing** | none |
| **Delta** | **+28** — 27 in `test_precedence_canonical.py` and 1 net in the pins file |

The full suite was **not** run: F36 says it cannot run in one process on this host. The method is weaker
than a two-run intersection and is stated as such.

**Gates, measured not asserted** (F71): `ruff check wisp/` → **11 errors**, unchanged by this phase;
pinned `mypy` → **1844 errors in 228 files**, unchanged. Neither is green; this phase introduces no new
error in either.

---

## 9. Honest limits

- **R1 cannot rewrite the older documents**, so a reader who skips R1 will still resolve "row 4" wrongly.
  The mitigation is that the canonical table is restated **in full** in ADR-0049 and pinned by a test, so
  the answer is reachable without either older document — but the ambiguity is *documented*, not removed.
- **R2's ratification is a judgement, not a measurement.** The cells' *behaviour* is measured; the claim
  that it is *correct* is an argument from ADR-0047's principle. It is stated as an argument so a future
  reader can attack the argument rather than the code.
- **`goal.PRECEDENCE` is documentation-as-data**, so it can drift from `derive_goal_state`. The guard
  drives each row through the real arbiter, which catches a *behavioural* drift; it cannot catch a
  cosmetic edit to the condition strings, because it compares the ADR to the table rather than the table
  to the arbiter's branches. Stated as a boundary.
- **The content mapping is computed from ADR-0035's conditions as transcribed**, not parsed from the
  ADR's markdown. If ADR-0035's table were edited (it is append-only, so it should not be), the
  transcription in the test would be the thing to update — and the test says so.
- **`INCOMPLETE` + `PASS` reachability is unmeasured.** R2 ratifies the cell; how often a turn exhausts
  its budget while every criterion measures satisfied is a measurement nobody has taken.
