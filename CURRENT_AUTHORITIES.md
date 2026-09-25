# CURRENT_AUTHORITIES.md — the current state of the completion and recovery authorities

> **DERIVED DOCUMENT — REGENERATE, DO NOT EDIT IN PLACE.**
> Regenerate this file whenever an ADR changes. It states the **current** state, not history: it cites
> ADRs, it does not re-derive them, and it **introduces no decision**. If two ADRs conflict, that is a
> **finding** (recorded in the phase report), never a decision taken here.
>
> **Every claim below carries a pin**: `ADR-XXXX §Y` for the decision, and `path:line` for the code.
> A claim that cannot be pinned is a finding, not a claim. The two claims this page could **not** pin
> are listed in §5 — they are the reason this page is a derived artifact and not an ADR.
>
> Generated 2026-09-25 at `af3a89a` · covers **ADR-0001 … ADR-0047** · supersession chains in §1.1–1.6.

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
| **Current owner** | `goal.terminal_outcome_from_evidence` (`wisp/core/goal.py:132`); `turn_succeeded` is a **projection** of it, computed once per turn (`wisp/core/runtime.py:952-954`) |
| **Cannot decide** | goal state · recovery · verification |
| **Current ADRs** | **ADR-0035** §Decision 2 (turn level remains terminal evidence) → **ADR-0044** R1/R2 (the *only* implementation; the flag is a projection) → **ADR-0047** R2 (no longer an arbitration input; still recorded) |
| **Durable record fields** | `terminal_outcome`, `turn_succeeded` — the goal-state record (`wisp/core/runtime.py:1274`, `wisp/core/runtime.py:1289`); `terminal_outcome`, `turn_succeeded` — the attempt journal line `{"kind":"attempt"}` (`wisp/core/convergence.py:931-932`) |

### 1.2 stream state

| | |
|---|---|
| **Current owner** | `guarded_provider_stream` (`wisp/core/provider_stream.py:113`); the two facts are `got_meaningful` (`wisp/core/provider_stream.py:139`) and `saw_terminal` (`wisp/core/provider_stream.py:140`) |
| **Cannot decide** | turn success · acceptance · goal state |
| **Current ADRs** | **ADR-0039** R5 (the guard owns *recovery*, not canonicalization; it reads the normalization boundary) → **ADR-0041** R3/R7 — **superseded** by **ADR-0043** R1–R7 (payload-based meaningfulness for every type; the classifier owns no vocabulary but `TERMINAL_TYPES`) |
| **Durable record fields** | **none.** It is an in-flight guard, not a recorded fact: its output is consumed by the turn predicate within the same turn. Nothing replays it, so there is nothing to journal — stated so a reader does not go looking for a key that does not exist |

### 1.3 acceptance verdict

| | |
|---|---|
| **Current owner** | `acceptance.evaluate` (`wisp/core/acceptance.py:213`) |
| **Cannot decide** | goal state · recovery · **whether to gate** (the gate is a separate, flag-controlled consumer) |
| **Current ADRs** | **ADR-0016** (two stages; stage 3a does not gate) · **ADR-0017** (the floor criterion is an *implication*) · **ADR-0018** (the engine publishes the guard; the runtime only reads it) · **ADR-0042** (the verdict is an **input**, not a second authority) |
| **Durable record fields** | `verdict` in the verdict envelope (ADR-0013, `wisp/core/acceptance.py:204`) · `acceptance_verdict` in the goal-state record (`wisp/core/runtime.py:1275`) · `verdict`, `unmet`, `evidence_ids` in the attempt journal (`wisp/core/convergence.py:937-938`) |

### 1.4 progress verdict

| | |
|---|---|
| **Current owner** | `progress.evaluate_progress` (`wisp/core/progress.py:155`); verdicts `NO_PROGRESS` / `MEANINGFUL_PROGRESS` / `PROGRESS_UNDETERMINABLE` (`wisp/core/progress.py:50`) |
| **Cannot decide** | acceptance · goal state · **which rung** |
| **Current ADRs** | **ADR-0046** R1–R11 (a second *input* to the recovery decision, not a re-classification of the failure) → **ADR-0047** R6 (it is the witness that the state changed) and R8 (a regression, a tampered input, or an unmeasurable attempt cannot unlock it) |
| **Durable record fields** | `progress`, `progress_signals` (`wisp/core/convergence.py:909-910`, `wisp/core/convergence.py:944`) · `measurement_observations` + `measurement_digest` — the raw payloads, digested over `WITNESS_FIELDS` only (F63; `wisp/core/convergence.py:796-809`) |

### 1.5 goal state

| | |
|---|---|
| **Current owner** | `goal.derive_goal_state` (`wisp/core/goal.py:146`); the contract as data is `goal.PRECEDENCE` (`wisp/core/goal.py:108-117`), 8 rows |
| **Cannot decide** | recovery rung · **whether `done` was withheld** |
| **Current ADRs** | **ADR-0035** (the contract; rows 0–6) → **ADR-0036** (amends ADR-0035 by reconciling its two clauses; adds the predicate to the goal record) → **ADR-0037** (completes ADR-0036) → **ADR-0042** (states the relation ADR-0035 left implicit; no behaviour change) → **ADR-0044** (removes the duplicated predicate) → **ADR-0047** R1–R5 (rows 3–6 revised) |
| **Durable record fields** | `SessionEvent.goal_state_event` (`wisp/core/runtime.py:1271`), 9 keys: `goal_state`, `terminal_outcome`, `acceptance_verdict`, `stagnation_verdict`, `stagnation_allows_goal_met`, `turn_succeeded`, `cancelled`, `escalated`, `failure_code` (`wisp/core/runtime.py:1273-1292`). **`stagnation_allows_goal_met` is the one replay must read** — `stagnation_verdict` ignores `trap_fired` (F35, ADR-0036 §6) |

### 1.6 recovery ladder state

| | |
|---|---|
| **Current owner** | `RecoveryLadder.ladder_state` (`wisp/core/recovery.py:742`) — **renamed** from `terminal_outcome` |
| **Cannot decide** | completion · goal state |
| **Current ADRs** | **ADR-0024** (denial enforced by CLASS) · **ADR-0025** (an unsafe rollback escalates) · **ADR-0026** (a mechanism, not yet consulted by the turn loop) → **ADR-0044** R6 (the rename, which removed a cross-layer name collision) → **ADR-0046** (progress widens one class's legal rungs) → **ADR-0047** R6–R13 (R5's unit is the *strategy*, not the rung) |
| **Durable record fields** | `RecoveryDecision.seq` and `ladder_history` (`wisp/core/recovery.py:541`, `wisp/core/recovery.py:556`) · `AttemptRecord.rung`, `.directive`, `.failure_class` (`wisp/core/convergence.py:928-929`, `wisp/core/convergence.py:942`) · `BudgetGovernor.snapshot()` — reports `productive_continuations` (`wisp/core/recovery.py:472`) |

---

## 2. (a) The run-level aggregation — stated once

> **A run's state is the ladder's escalation if it surrendered, otherwise the last attempt's derived state.**

Source: **ADR-0047 R13**; `goal.derive_goal_state`'s row 2 consumes `escalated` (`wisp/core/goal.py:195`), and the
convergence loop reads the last `AttemptRecord.goal_state` (`wisp/core/convergence.py:885`). This is the *only*
place the aggregation is stated; a second statement would be a second authority for one question.

---

## 3. (b) The precedence matrix

Reproduced from **ADR-0035**'s ordered arbiter as revised by **ADR-0047** R1, and pinned to the code.
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
| 7 | otherwise — no decisive verdict | `GOAL_UNVERIFIED` | code only — see **§5 F-2** · `wisp/core/goal.py:213` |

**The input combination F60 moved is row 4's qualifier: `T` = `FAILED` + `A` = `PASS` was `GOAL_FAILED`,
and is now `GOAL_MET`.** That is the change ADR-0047 R1 documents, and it is the reason `turn_succeeded`
stopped arbitrating (R2). **Two further cells also moved and ADR-0047 does not state them** — see §5 F-1.

**The rules the row order cannot express.** `fatal > stagnation`, `stagnation > PASS`, `PASS > fatal` is a
**cycle**, so no ordering of rows can express it (ADR-0047 R3). It is broken where it is semantically
broken: row 4 is the only clause whose *meaning* depends on the verdict, because it is the only one about
the **attempt** rather than the **objective**.

---

## 4. What is *not* in this page

- **Enforcement.** Every flag above defaults **OFF** — `goal_state`/`WISP_GOAL_STATE`,
  `recovery_ladder`/`WISP_RECOVERY_LADDER`, `stagnation_gate`/`WISP_STAGNATION_GATE` (ADR-0035 §9;
  ADR-0037). This page describes *semantics*; enablement is ADR-0016's question and remains
  `NOT_YET_DETERMINABLE`.
- **History.** Why a row reads the way it does is in the ADR, not here.
- **The graph.** `core/task_graph.py` journals per-turn node status; `runtime.py` states plainly that it
  is *"RECORDED, not enforced"*. It is a consumer of the turn predicate, not an authority over it.

---

## 5. Claims this page could not pin — recorded as findings, not asserted

**F-1 — the arbitration has drifted further than the ADR states.** ADR-0047 R1 says *"Only one input
combination moved"*. A differential of `derive_goal_state` against `b9af5f0^` over all 48 input
combinations finds **7 cells changed, in three distinct semantic classes**: (a) `FAILED`+`PASS`
(documented, R1); (b) `turn_succeeded=False` no longer blocking `PASS` (documented, R2); (c)
**`INCOMPLETE`+`PASS` → `GOAL_MET`** (was `GOAL_UNVERIFIED`) and **`FAILED`+`PASS`+`stagnating` →
`GOAL_STAGNATED`** (was `GOAL_FAILED`) — **undocumented**. Recorded, not decided: this page states the
code's current behaviour (rows 3–7 above) and does not rule on whether (c) is intended.

**F-2 — the precedence table is only total in the code.** ADR-0035's arbiter has rows **0–6** and no
fall-through, so the combination `T` = `SUCCEEDED` + no verdict matches no row, and its `GOAL_UNVERIFIED`
*requires* `INCONCLUSIVE ∨ INCOMPLETE` — which that combination does not have. The code has always had a
row 7. ADR-0047 neither restates the table nor says it added a row, and it renumbers (*"Row 4 is now…"*)
against a numbering that exists only in the code. **Consequence for a reader: "row N" is ambiguous between
ADR-0035 and the implementation, which is exactly the drift this page exists to prevent.**

---

## 6. Regeneration procedure

1. Change an ADR. 2. Re-pin §1's code locations (they move). 3. Re-run the §5 differential:

```bash
env -u PYTHONPATH .venv/bin/python - <<'PY'
import importlib.util, itertools, subprocess, sys, tempfile, pathlib
# diff the arbiter at HEAD against any earlier revision, over the full input space
PY
```

4. Re-generate the matrix from `goal.PRECEDENCE` and `derive_goal_state`, **not** by editing §3's table.
5. Any claim that no longer pins becomes a new finding in §5; do not delete it silently.
