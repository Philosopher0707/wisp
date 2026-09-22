# PHASE P6 REPORT — Recovery Ladder

| Field | Value |
|---|---|
| Phase | **P6** |
| Baseline | P5 complete (`WISP_MIGRATION_STATUS.md`) |
| Status | **`COMPLETE`** — the ladder ships as a mechanism; live-loop integration deferred (§7) |
| Files changed | 2 modified, 2 added (`wisp/core/recovery.py`, `tests/test_recovery_ladder.py`) |
| Tests added | `tests/test_recovery_ladder.py` (69) |
| Rollback | `WISP_RECOVERY_LADDER` — moot until the turn loop consults the ladder (§7) |

---

## 1. Executive summary

P6 replaces ad-hoc recovery with an explicit, budgeted, evidence-bearing ladder. All five plan items
landed:

| # | Plan item | Status |
|---|---|---|
| 1 | Closed failure taxonomy (10 classes) | ✅ `FailureClass`, closed and count-pinned; `NodeFailure.failure_code`'s free string is superseded |
| 2 | The 7-rung ladder with legal/forbidden tables | ✅ `LEGAL_RUNGS` + `FORBIDDEN_RUNGS` (total) + `RecoveryLadder` |
| 3 | **Wire the durable rollback path** | ✅ `plan_rollback()` consults `reversibility()` / `rollback_preview()` — their first production caller |
| 4 | Escalation as durable state | ✅ `HumanIntervention`, journaled and resumable |
| 5 | Recovery budgets + `BudgetGovernor` | ✅ one place that answers "how much is left?" |

**The rule that carries the most weight** is the one Phase 10 failed to enforce. Denials must never
auto-retry — and `_DENIAL_MARKERS` was **removed** in Phase 10 because all five canonical denial
statuses matched **nothing** (`CONTEXT.md:456`). A prose-matching guard that matches nothing is worse
than no guard, because it reads as protection. P6 makes the rule structural (ADR-0024).

---

## 2. What was actually wrong

| Concern | Before | After |
|---|---|---|
| Failure vocabulary | `NodeFailure.failure_code: str = "ERROR"` — a **free string**; six of the ten classes existed nowhere | `FailureClass` (10, closed), count-pinned |
| Recovery ordering | independent mechanisms with no ordering, no shared budget, no shared record | `LEGAL_RUNGS` per class, cost-ordered rungs, one `BudgetGovernor` |
| Rollback | `runs/compensation.py` says *"No tool wiring"*; **zero** production callers for `reversibility()` / `rollback_preview()` / `EditRecord` (verified by grep — only the re-export and their own test) | `plan_rollback()` is that caller |
| Escalation | a blocking call — cannot survive a restart, cannot be answered asynchronously, no channel for a non-CLI client | `HumanIntervention`, durable state, resumable |
| Budgets | the audit found **five unordered termination modes** and no object answering "how much is left" | `BudgetGovernor.snapshot()` |

---

## 3. Implementation

Two files modified, two added.

| File | Change |
|---|---|
| `wisp/core/recovery.py` | **new** — `FailureClass`, `RecoveryRung`, `LEGAL_RUNGS`, `FORBIDDEN_RUNGS`, `classify_failure()`, `is_legal_rung()`, `RecoveryBudget`, `BudgetGovernor`, `RecoveryDecision`, `HumanIntervention`, `EscalationState`, `RecoveryLadder`, `RollbackPlan`, `plan_rollback()` |
| `wisp/core/session.py` | `RECOVERY` + `ESCALATION` event kinds (**audit-only**) |
| `tests/test_recovery_ladder.py` | **new** — 69 tests |

### 3.1 The denial rule, made structural (ADR-0024)

- `classify_failure()` maps every denial status onto `FailureClass.SECURITY`, and **imports** the
  vocabulary from `core/events.py` rather than re-listing it.
- `FORBIDDEN_RUNGS[SECURITY]` forbids every rung except escalation.
- **Precedence is explicit**: denial outranks every other signal, so a denied call that also looked
  transient is still `SECURITY` and the rule cannot leak.
- A test is parametrized over the **canonical** status set, so it cannot drift from the vocabulary it
  covers.
- An **AST test** asserts no string literal `"POLICY_DENIED"` appears in `recovery.py`.

### 3.2 An unsafe rollback escalates (ADR-0025)

`plan_rollback()` refuses anything not declared `reversible`, and `RecoveryLadder.decide()` converts the
refusal into an escalation. A recovery that makes things worse is worse than stopping — it destroys the
evidence that would explain the original failure. Assuming an undeclared tool is compensable is the
assumption that produces that outcome.

### 3.3 `FORBIDDEN_RUNGS` is total

The first implementation only listed the three classes with structural prohibitions. A test asserting
totality failed, and **the test was right**: making the table total means "is this class missing, or
does it forbid nothing?" is never a question a reader has to answer. The seven classes with no
prohibition now carry an **empty** frozenset.

---

## 4. Verification

### 4.1 New tests — 69, all passing

| Plan requirement | Class | Proves |
|---|---|---|
| `test_failure_taxonomy_closed` | `TestFailureTaxonomyClosed` (14) | exactly 10 classes; every class has a legal-rung set, a forbidden entry, and can escalate; **every canonical denial status** classifies as `SECURITY`; a success outcome is refused rather than guessed at |
| `test_denial_never_retries` | `TestDenialNeverRetries` (9) | `SECURITY` and `REPEATED` forbid rung 1; `SECURITY` forbids every rung but escalation; a denial escalates immediately; forbidden wins over legal |
| `test_escalation_is_terminal` | `TestEscalationIsTerminal` (8) | exhaustion → `ESCALATED_TO_HUMAN`, never a hang; the intervention is resumable; **no rung repeats** (R5); the ladder moves only downward (R1) |
| `test_durable_rollback_survives_crash` | `TestDurableRollbackSurvivesCrash` (12) | the compensation declarations have a caller; irreversible/unknown tools are refused; an unsafe rollback escalates; the escalation survives the journal and a replay |
| `test_recovery_requires_evidence` | `TestRecoveryRequiresEvidence` (5) | a rung with no evidence is refused, with an explanatory message |
| `test_ladder_budget_enforced` | `TestLadderBudgetEnforced` (11) | each budget bounds its rung; exhaustion **escalates** rather than terminating silently; the snapshot reports everything from one place |
| `test_escalation_payload_is_audit_trail` | `TestEscalationPayloadIsAuditTrail` (5) | the payload lists every attempted rung with its class and reason, serializes for a client, and survives the journal |
| — | `TestReachabilityAndIsolation` (4) | the module is reachable; the events never touch the transcript; records survive replay; no re-listed denial vocabulary |

### 4.2 Regression

| Run | Failures + errors |
|---|---|
| HEAD baseline | 131 |
| P0 – P5 | 128 |
| **P6** | **128** |

Failure set `diff`-compared against P5's. P6 introduced **0 new failures**.

---

## 5. Honest limits

- **The turn loop does not consult the ladder yet.** This is the phase's most important caveat, and it
  is stated rather than implied. `classify_failure()`, `plan_rollback()` and the ladder are reachable
  and fully tested, but the live turn path's recovery behaviour is **unchanged**. Recorded as item
  **M12** (ADR-0026). The plan's rollback flag is therefore moot until that wiring lands.
- **`test_durable_rollback_survives_crash` does not restart a process.** It proves the escalation —
  which carries the ladder history and the reversibility verdict — survives the journal and a replay.
  A true crash-restart test needs the killpoint harness (item M3).
- **`tools/checkpoints.py` remains in-memory.** Its docstring says *"Session-scoped by design"*; P6 did
  not change that. Durable rollback currently rests on `compensation.py`'s declarations plus the
  journal, not on durable checkpoints.
- **`ruff`/`mypy` not installed.**
- **128 pre-existing failures remain.** P6's claim is exact: it adds none.

---

## 6. Completion criteria

| Criterion | Status |
|---|---|
| All seven rungs reachable and tested | ✅ `RecoveryRung` has 7; every rung appears in a legal-rung set |
| Denials never retry (the Phase 10 defect class does not reappear) | ✅ by class, over the canonical vocabulary, AST-pinned |
| Rollback survives a crash | ⚠️ **the escalation does** (journaled, replayable); a true process-restart test needs M3 |
| Escalation is resumable and carries its audit trail | ✅ `HumanIntervention.resumable`; full ladder history |
| No regression | ✅ **0 new failures** |
| `ruff` / `mypy` | ❌ not installed |

---

## 7. Deviations from the plan

### 7.1 The live turn loop was not rewired (ADR-0026)

The plan's objective is to *replace* ad-hoc recovery. P6 ships the replacement as a **mechanism** and
leaves the live recovery path alone. Rewiring it changes behaviour on the **failure** path — the path
with the least existing test coverage — and the plan names the risk as *"ordering and budget
interaction"*, which is exactly what a live rewiring would disturb.

Same shape of decision as P5's item 5. Both are recorded as items rather than silently dropped.

### 7.2 `tools/checkpoints.py` was not made durable

The plan's item 3 says to wire the durable rollback path. `compensation.py` is wired; `checkpoints.py`
is not, and its docstring argues it should not be (*"Session-scoped by design: undo is a live-session
operation, and the durable record of WHAT changed already lives in ChangeTracker"*). P6 respects that
reasoning rather than overriding it — the durable record is the compensation declaration plus the
journal, which is what the escalation carries.

---

## 8. Next phase

**P7 — Stagnation Detection.** Its prerequisites are P1 and P5, both met. The detector
(`core/graph/loop.py::OscillationTrap`) already exists and is **orphaned** — the same pathology this
migration has been removing.

Carried forward:

1. **M12 — wire the recovery ladder into the turn loop** (§7.1), behind `WISP_RECOVERY_LADDER`.
2. **M11 — make the graph mutation drive execution**, with M9 (message list as a projection).
3. **M9 — the message list is not yet a projection of the graph.**
4. **M8 — retire `dag.py`** — needs a green fanout suite first.
5. **M2 / M3 — journal-first reconstruction; killpoint integration.**
6. **M1 — P3 stage 3b** — blocked on a working tool path (`jsonschema`).
7. **M4 — revisit ADR-0004** for the proposal/verdict/graph/recovery records.
