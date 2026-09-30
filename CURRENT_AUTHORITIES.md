# CURRENT_AUTHORITIES.md — the current state of the completion and recovery authorities

> **DERIVED DOCUMENT — REGENERATE, DO NOT EDIT IN PLACE.**
> Regenerate with `env -u PYTHONPATH .venv/bin/python scripts/derive_current_authorities.py`.
> It states the **current** state, not history: it cites ADRs, it does not re-derive them, and it
> **introduces no decision**. If two ADRs conflict, that is a **finding** (recorded in §5), never a
> decision taken here.
>
> **Every claim below carries a pin**: `ADR-XXXX §Y` for the decision, and `path:line` for the code.
> The generator **checks every pin against the tree** and **generates §3 from `goal.PRECEDENCE`**; a
> pin that does not name its symbol, a row the arbiter does not return, or an ADR chain that omits an
> ADR superseding one it cites **refuses the derivation**. §5 is this page's own findings record —
> **append-only**, emitted unchanged (ADR-0062 R8).
>
> Generated 2026-10-01 at `5245f46` · covers **ADR-0001 … ADR-0074** · supersession chains in §1.1–1.6.
> The commit, the date and the range are **read from `git` and the ADR log**, not written (F97).
>
> **Sibling registers:** `CURRENT_FINDINGS.md`, `CURRENT_OPEN_ITEMS.md`, `CURRENT_FLAGS.md` — all
> derived; none may decide. See `CONTEXT.md`'s **"The derived registers"**.

---

## 1. The six authorities

The chain is **one-way**. Each authority may consume a lower one; none may re-decide a higher one's question.

```
provider terminal → stream state → turn predicate → acceptance verdict
                                                    ↘ progress verdict ↘
                                                        goal state → recovery ladder state
```

### 1.1 turn predicate

| | |
|---|---|
| **Current owner** | `goal.terminal_outcome_from_evidence` (`wisp/core/goal.py:132`); `turn_succeeded` is a **projection** of it, computed once per turn (`wisp/core/runtime.py:995`) |
| **Cannot decide** | goal state · recovery · verification |
| **Current ADRs** | **ADR-0035** §Decision 2 (turn level remains terminal evidence) → **ADR-0044** R1/R2 (the *only* implementation; the flag is a projection) → **ADR-0047** R2 (no longer an arbitration input; still recorded) |
| **Durable record fields** | `terminal_outcome`, `turn_succeeded` — the goal-state record (`wisp/core/runtime.py:1338`, `wisp/core/runtime.py:1353`); `terminal_outcome`, `turn_succeeded` — the attempt journal line `{"kind":"attempt"}` (`wisp/core/convergence.py:1323`) |

### 1.2 stream state

| | |
|---|---|
| **Current owner** | `guarded_provider_stream` (`wisp/core/provider_stream.py:117`); the two facts are `got_meaningful` (`wisp/core/provider_stream.py:143`) and `saw_terminal` (`wisp/core/provider_stream.py:144`) |
| **Cannot decide** | turn success · acceptance · goal state |
| **Current ADRs** | **ADR-0039** R5 (the guard owns *recovery*, not canonicalization; it reads the normalization boundary) → **ADR-0041** R3/R7 — **superseded** by **ADR-0043** R1–R7 (payload-based meaningfulness for every type; the classifier owns no vocabulary but `TERMINAL_TYPES`) |
| **Durable record fields** | **none.** It is an in-flight guard, not a recorded fact: its output is consumed by the turn predicate within the same turn. Nothing replays it, so there is nothing to journal — stated so a reader does not go looking for a key that does not exist |

### 1.3 acceptance verdict

| | |
|---|---|
| **Current owner** | `acceptance.evaluate` (`wisp/core/acceptance.py:213`) |
| **Cannot decide** | goal state · recovery · **whether to gate** (the gate is a separate, flag-controlled consumer) |
| **Current ADRs** | **ADR-0016** (two stages; stage 3a does not gate) · **ADR-0017** (the floor criterion is an *implication*) · **ADR-0018** (the engine publishes the guard; the runtime only reads it) · **ADR-0042** (the verdict is an **input**, not a second authority) |
| **Durable record fields** | `verdict` in the verdict envelope (ADR-0013, `wisp/core/acceptance.py:204`) · `acceptance_verdict` in the goal-state record (`wisp/core/runtime.py:1339`) · `verdict`, `unmet`, `evidence_ids` in the attempt journal (`wisp/core/convergence.py:1333-1334`) |

### 1.4 progress verdict

| | |
|---|---|
| **Current owner** | `progress.evaluate_progress` (`wisp/core/progress.py:155`); verdicts `NO_PROGRESS` / `MEANINGFUL_PROGRESS` / `PROGRESS_UNDETERMINABLE` (`wisp/core/progress.py:50-63`) |
| **Cannot decide** | acceptance · goal state · **which rung** |
| **Current ADRs** | **ADR-0046** R1–R11 (a second *input* to the recovery decision, not a re-classification of the failure) → **ADR-0047** R6 (it is the witness that the state changed) and R8 (a regression, a tampered input, or an unmeasurable attempt cannot unlock it) |
| **Durable record fields** | `progress`, `progress_signals` (`wisp/core/convergence.py:1305-1306`, `wisp/core/convergence.py:1340-1341`) · `measurement_observations` + `measurement_digest` — the raw payloads, digested over `WITNESS_FIELDS` only (F63; `wisp/core/convergence.py:1192-1205`) |

### 1.5 goal state

| | |
|---|---|
| **Current owner** | `goal.derive_goal_state` (`wisp/core/goal.py:146`); the contract as data is `goal.PRECEDENCE` (`wisp/core/goal.py:108-117`), 8 rows |
| **Cannot decide** | recovery rung · **whether `done` was withheld** |
| **Current ADRs** | **ADR-0035** (the contract; rows 0–6) → **ADR-0036** (amends ADR-0035 by reconciling its two clauses; adds the predicate to the goal record) → **ADR-0037** (completes ADR-0036) → **ADR-0042** (states the relation ADR-0035 left implicit; no behaviour change) → **ADR-0044** (removes the duplicated predicate) → **ADR-0047** R1–R5 (rows 3–6 revised) → **ADR-0049** (makes this table canonical at eight rows; resolves older numbering by content; ratifies two cells) |
| **Durable record fields** | `SessionEvent.goal_state_event` (`wisp/core/runtime.py:1335`), 9 keys: `goal_state`, `terminal_outcome`, `acceptance_verdict`, `stagnation_verdict`, `stagnation_allows_goal_met`, `turn_succeeded`, `cancelled`, `escalated`, `failure_code` (`wisp/core/runtime.py:1337-1362`). **`stagnation_allows_goal_met` is the one replay must read** — `stagnation_verdict` ignores `trap_fired` (F35, ADR-0036 §6) |

### 1.6 recovery ladder state

| | |
|---|---|
| **Current owner** | `RecoveryLadder.ladder_state` (`wisp/core/recovery.py:742`) — **renamed** from `terminal_outcome` |
| **Cannot decide** | completion · goal state |
| **Current ADRs** | **ADR-0024** (denial enforced by CLASS) · **ADR-0025** (an unsafe rollback escalates) · **ADR-0026** (a mechanism, not yet consulted by the turn loop) → **ADR-0044** R6 (the rename, which removed a cross-layer name collision) → **ADR-0046** (progress widens one class's legal rungs) → **ADR-0047** R6–R13 (R5's unit is the *strategy*, not the rung) |
| **Durable record fields** | `RecoveryDecision.seq` and `ladder_history` (`wisp/core/recovery.py:541`, `wisp/core/recovery.py:556`) · `AttemptRecord.rung`, `.directive`, `.failure_class` (`wisp/core/convergence.py:1277-1278`, `wisp/core/convergence.py:1299`) · `BudgetGovernor.snapshot()` — reports `productive_continuations` (`wisp/core/recovery.py:472`) |

---

## 2. (a) The run-level aggregation — stated once

> **A run's state is the ladder's escalation if it surrendered, otherwise the last attempt's derived state.**

Source: **ADR-0047 R13**; `goal.derive_goal_state`'s row 2 consumes `escalated` (`wisp/core/goal.py:195`), and the
convergence loop reads the last `AttemptRecord.goal_state` (`wisp/core/convergence.py:1281`). This is the *only*
place the aggregation is stated; a second statement would be a second authority for one question.

---

## 3. (b) The precedence matrix

Generated from `goal.PRECEDENCE` (each row and its result) and `derive_goal_state` (each code pin),
following **ADR-0035**'s ordered arbiter as revised by **ADR-0047** R1.
`T` = terminal outcome, `A` = the P3 acceptance verdict, `S` = the P7 stagnation predicate.

| # | Condition | Result | Source |
|---|---|---|---|
| 0 | a terminal state is already recorded | **frozen** — never rewritten | ADR-0035 row 0 / ADR-0020 · `wisp/core/goal.py:190-191` |
| 1 | operator cancellation | `CANCELLED` | ADR-0035 row 1 · `wisp/core/goal.py:193` |
| 2 | ladder exhausted | `ESCALATED_TO_HUMAN` | ADR-0035 row 2 · `wisp/core/goal.py:195` |
| 3 | `A` = `FAIL` | `GOAL_FAILED` | ADR-0035 row 3 (clause split by ADR-0047 R1) · `wisp/core/goal.py:201` |
| 4 | `T` = `FAILED` **and no `A` = `PASS`** | `GOAL_FAILED` | **ADR-0047 R1** · `wisp/core/goal.py:203` |
| 5 | `S` — `may_report_goal_met()` is `False` | `GOAL_STAGNATED` | ADR-0035 row 4 · `wisp/core/goal.py:205` |
| 6 | `A` = `PASS` | `GOAL_MET` | ADR-0035 row 6 · `wisp/core/goal.py:207` |
| 7 | otherwise — no decisive verdict | `GOAL_UNVERIFIED` | fall-through, code only — **ADR-0049 R1** · `wisp/core/goal.py:213` |

**This table is canonical by ADR-0049 R1** — `goal.PRECEDENCE` (`wisp/core/goal.py:108-117`), eight rows.
ADR-0035 §Precedence's rows 0–6 and ADR-0047's renumbering are **historical**: resolve any "row N" against
this table **by content**, not by number. ADR-0035's row 4 is stagnation (this table's row 5); ADR-0047
R1's "Row 4" is the fatal clause (this table's row 4) — **different rows**. Full restatement, the mapping
and the resolution rules are in **ADR-0049**.

**The input combination F60 moved is row 4's qualifier: `T` = `FAILED` + `A` = `PASS` was `GOAL_FAILED`,
and is now `GOAL_MET`.** That is the change ADR-0047 R1 documents, and it is the reason `turn_succeeded`
stopped arbitrating (R2). **Two further cells moved and ADR-0047 does not state them — both are ratified
by ADR-0049** (R2: `INCOMPLETE`+`PASS` → `GOAL_MET`; R3: `fatal`+`PASS`+`stagnating` → `GOAL_STAGNATED`).
See §5 F-1.

**The rules the row order cannot express.** `fatal > stagnation`, `stagnation > PASS`, `PASS > fatal` is a
**cycle**, so no ordering of rows can express it (ADR-0047 R3). It is broken where it is semantically
broken: row 4 is the only clause whose *meaning* depends on the verdict, because it is the only one about
the **attempt** rather than the **objective**.

---

## 4. What is *not* in this page

- **Enforcement.** Every flag above defaults **OFF** — `goal_state`/`WISP_GOAL_STATE`,
  `recovery_ladder`/`WISP_RECOVERY_LADDER`, `stagnation_gate`/`WISP_STAGNATION_GATE` (ADR-0035 §9;
  ADR-0037). This page describes *semantics*; enablement was ADR-0016's question, and **ADR-0051
  replaced its condition** with a precondition on the criteria set (satisfied by ADR-0053) plus a
  declared-population measure. M1's state is **`PARTIAL`** — the mechanism is built and driven, and
  the population is short by one capable model (ADR-0054). *(Corrected 2026-09-25: this line said
  "remains `NOT_YET_DETERMINABLE`" until the authorization-parity phase, which is after ADR-0051
  superseded it. The page's guard checks `path:line` pins, not prose, so it could not catch it.)*
- **History.** Why a row reads the way it does is in the ADR, not here.
- **The graph.** `core/task_graph.py` journals per-turn node status; `runtime.py` states plainly that it
  is *"RECORDED, not enforced"*. It is a consumer of the turn predicate, not an authority over it.

---

## 5. This page's own findings, and where they were decided

§5 exists because a derived page may **state** the current state but may not **decide** it. Both claims
below were recorded here as findings and have since been decided — the resolution is cited, not
re-argued, because a page that re-argued a decision would be a second authority for it.

**F-1 — DECIDED (ADR-0049 R2/R3).** *The arbitration had moved further than ADR-0047 stated.*
ADR-0047 R1 said *"Only one input combination moved"*; a differential against `b9af5f0^` over all 48
input combinations found **7 cells changed in three semantic classes** — two documented, and two not:
`INCOMPLETE`+`PASS` → `GOAL_MET` (was `GOAL_UNVERIFIED`), and `fatal`+`PASS`+`stagnating` →
`GOAL_STAGNATED` (was `GOAL_FAILED`).
**ADR-0049 R2 ratifies the first** (the objective's evidence decides where it is decisive — ADR-0047's own
principle, applied to `INCOMPLETE` rather than `FAILED`), and **ADR-0049 R3 scopes the second** (a fatal
error *with* a `PASS` is not fatal, so the cell reduces to `PASS` + stagnation, which is row 5).
The measured routing is in `tests/reliability/test_precedence_canonical.py::TestTheRatifiedCells`.

**F-2 — DECIDED (ADR-0049 R1).** *The precedence table was total only in the code, and "row N" was
ambiguous between two ADRs and the implementation.* ADR-0035's arbiter had rows **0–6** and no
fall-through; the code has always had a row 7.
**ADR-0049 R1 makes `goal.PRECEDENCE` (`wisp/core/goal.py:108-117`, eight rows 0–7) the canonical table**
and declares every older numbering **historical**, to be resolved against it **by content**. The
resolution rule is §3's table plus ADR-0049's §Resolution rules; the mapping is pinned by
`tests/reliability/test_precedence_canonical.py::TestTheContentMappingIsReproducible`, which drives
ADR-0035's conditions over the full input space and reports which canonical row answers each one.

**No open findings.** A future conflict between two ADRs is recorded here and stops — it is never
resolved on this page.

---

## 6. Regeneration procedure

1. Change an ADR, or move code a pin names. 2. Re-pin in the generator's data — **never on this page**.
3. Run `env -u PYTHONPATH .venv/bin/python scripts/derive_current_authorities.py`. It **refuses, and
writes nothing**, if a pin no longer names its symbol, if a §3 row's branch does not return what
`goal.PRECEDENCE` states, or if an authority's ADR chain cites an ADR the log supersedes without citing
the ADR that supersedes it. 4. §3 is generated from the code; there is no table to edit.
5. Any claim that no longer pins becomes a new finding appended to `SECTION_5` in the generator; a
finding is never deleted, and a decided one is marked `DECIDED` with its ADR.
