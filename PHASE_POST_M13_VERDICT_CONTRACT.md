# PHASE POST-M13 — VERDICT PRECEDENCE CONTRACT

**Phase type:** read-only contract forensics. **No production code was modified.**
**Date:** 2026-09-24
**Predecessor:** `PHASE_POST_M13_AUTHORITY_RECON.md`
**Question:** *What authority contract must exist before the live turn loop consumes verdicts?*

---

## 1. Mission / Scope

Determine how the existing verdict/decision mechanisms relate to one another, which decisions each is
allowed to make, what happens when they disagree, and what single deterministic contract a future live
turn consumer must obey.

**In scope:** contract discovery from existing sources. **Out of scope:** implementation, M13
enforcement, wiring the consumer, F8 repair, tripwire changes, and every candidate listed in the brief's
§14.

**One correction to the predecessor's framing, found in this phase.** The reconnaissance treated the
four mechanisms as four peers waiting on one consumer. That is not what the source shows. **P3's
`CompletionVerdict` is not a separate authority — it is a projection of the live completion authority
(`VerificationFloorGuard`) into a richer vocabulary, and it is derived from the same guard the engine
already gates on.** Details in §6.3 and §10.1. The consequence is that the missing edge is narrower and
the precedence question is smaller than the predecessor implied — but it is still unresolved.

---

## 2. Baseline

Captured before any investigation. No `stash`, `reset`, `checkout`, or `clean` was used.

```
$ git log -1 --oneline
7c15626 docs(m11): record the M11 phase; findings F29-F31; repair the commit table
$ git branch --show-current
main
$ git status --short | wc -l
80
$ git diff --stat | tail -1
39 files changed, 1247 insertions(+), 176 deletions(-)
```

| Property | Value |
|---|---|
| HEAD | `7c15626` |
| Branch | `main` |
| Working tree | 39 modified (tracked) + 41 untracked |
| Diff size | 131,995 bytes |

Snapshot: `.workbuddy-ai/memory/post-m13-contract/{head,branch,status-before,diffstat-before}.txt`,
`diff-before.patch`.

**Cross-check:** the diff is **131,995 bytes — byte-for-byte the same size as the snapshot taken at the
start of the predecessor phase.** Combined with `git status` differing only by the two documents those
phases added, this independently confirms that neither the reconnaissance phase nor this one changed a
tracked file.

The pre-existing WIP (Phase 10 / Phase 13 docs, `wisp-desktop/`, `wisp/server/routes/`,
`wisp/pathsec.py`, `wisp/provider_catalog.py`, `tests/conftest.py`, `wisp/multi_agent/_circuit_breaker.py`
and others) is treated as **immutable** and was not touched.

---

## 3. Sources Consulted

**Primary contract sources**

| Source | Read for |
|---|---|
| `PHASE_M13_REPORT.md` §7 items 8–9 | the two deferred enforcement paths and their tripwires |
| `WISP_ARCHITECTURE_DECISIONS.md` ADR-0016 | completion-gate staging and reversal condition |
| ADR-0026 | the ladder's deferral and its **named reversal flag** |
| ADR-0032 | the adapter's exact role; what "precedence" means *inside* the classifier |
| ADR-0017, ADR-0018 | the floor criterion's shape; who publishes the guard |
| ADR-0028, ADR-0027 | which records are state-bearing vs best-effort |
| `WISP_MIGRATION_STATUS.md` §22 | the current M13 state (not the historical summaries) |
| `CONTEXT.md` §0, §12 | current execution/completion architecture and open items |
| `WISP_TARGET_ARCHITECTURE.md` §5, §16 | **the only explicit precedence statement in the repository** |

**Implementation sources traced by call site**

`core/acceptance.py` · `core/verification.py` · `core/recovery.py` · `core/stagnation.py` ·
`core/runtime.py` · `core/stateless.py` · `core/events.py` · `core/session.py` ·
`tests/reliability/test_13h5_success_derivation.py` · `tests/test_stagnation_live_wiring.py`

---

## 4. Verdict / Signal Inventory

### 4.1 P3 — `CompletionVerdict`

| Property | Finding | Evidence |
|---|---|---|
| Producer | `evaluate(criteria, evidence, observations)` — pure, no model, no I/O | `acceptance.py:213` |
| Inputs | `AcceptanceCriteria` + `Evidence` + observations payload | `acceptance.py:213-215` |
| When computed | post-turn, from the guard the engine published | `runtime.py:1043-1049` |
| Vocabulary | `Verdict.PASS` / `FAIL` / `INCONCLUSIVE` — total by construction | `acceptance.py:40-51` |
| Persisted | yes, as a **journal-only audit record**, gated by `record_verdict` (**default `False`**) | `session.py:35,204`; `config.py:912-913`; `runtime.py:1042` |
| Authoritative today | **no** — recorded only | `acceptance.py:20`; `runtime.py:1025-1032` |
| `route_for()` | pure map `PASS→ALLOW`, `FAIL→REJECT`, `INCONCLUSIVE→RETRY` | `acceptance.py:57-70` |
| `route_for()` descriptive or normative? | **descriptive in effect** — it is derived, never assigned (`routed_decision` is a property), so it creates no second authority; but nothing reads it, so it currently *describes* a routing that never happens | `acceptance.py:194-196` |
| Failure semantics | a failing **deterministic** criterion → `FAIL`, short-circuiting; a required criterion with no valid evidence → `INCONCLUSIVE` ("absence of evidence is not evidence of failure"); no required criteria → `INCONCLUSIVE` ("nothing was verified") | `acceptance.py:220-236, 260-300` |

**The key structural fact.** `evaluate` is not fed arbitrary evidence. On the live path it is reached
only through `floor_guard_verdict(guard)` (`verification.py:293-301`), which builds criteria and
evidence **from `VerificationFloorGuard`'s own state**. So P3's verdict is a *restatement* of the floor
guard in a three-valued vocabulary.

### 4.2 P6 — `RecoveryLadder`

| Property | Finding | Evidence |
|---|---|---|
| Producer | `RecoveryLadder.decide(failure_class, evidence, ...)` | `recovery.py:512` |
| Inputs | a `FailureClass` (required), `evidence` (required — raises if empty), `tool_name`, `records` | `recovery.py:512-537` |
| Output | `RecoveryDecision(failure_class, rung, reason, evidence, seq)`, or escalation → `RecoveryRung.HUMAN` + `HumanIntervention` | `recovery.py:558-562, 564-580` |
| Nature of the decision | **routing/recovery** — which rung next; cost-ordered, budget-governed, never re-trying a tried rung | `recovery.py:496-510` |
| Does it override completion? | **No.** Nothing in `decide` mentions completion; `RecoveryDecision` carries no completion claim | `recovery.py:512-562` |
| Does it assume a classification exists? | **Yes** — `failure_class` is an input; the ladder does not classify | `recovery.py:512` |
| Bounded retries | `legal_rungs` drops tried rungs (R5) and budget-exhausted ones; escalation is the fallback and is **never a candidate** | `recovery.py:502-509` |
| Terminal outcome | `terminal_outcome` returns `"ESCALATED_TO_HUMAN"` or `"IN_PROGRESS"` | `recovery.py:585-586` |
| Live consumer | **none** | ADR-0026; predecessor §5.1 |

### 4.3 P7 / M13 — stagnation

| Property | Finding | Evidence |
|---|---|---|
| Vocabulary | `UNKNOWN` / `PROGRESSING` / `STAGNATING` | `stagnation.py` (`StagnationVerdict`) |
| `observe()` | disabled → `PROGRESSING`; **empty signal → `UNKNOWN`** (not appended, not flat, not fed to the trap); no previous → `UNKNOWN`; strict progress → `PROGRESSING` (resets the flat run); else `flat++`, and `≥ min_consecutive` → `STAGNATING` | `stagnation.py:225-262` |
| What stagnation *means* | "this turn is going round in circles" — a run of non-progressing observations, judged **per turn** | `runtime.py:680-683` |
| Is it a failure verdict? | **No.** It is an observation-derived verdict about *progress*. It becomes a failure only by being *routed* — `route_to_recovery` converts it to `FailureClass.STAGNATION` | `stagnation.py:331-350` |
| Recovery trigger? | `route_to_recovery()` exists and maps to `FailureClass.STAGNATION` whose legal rungs are `GLOBAL_REPLAN → DIAGNOSTIC → HUMAN`, with `RETRY`/`REPAIR` **forbidden** | `stagnation.py:331-350`; `recovery.py:107-109, 131`; pinned by `test_stagnation_detection.py:211,214` |
| Can it independently prevent completion? | **The predicate exists and says yes** — `may_report_goal_met()` returns `False` once stagnating (or when the trap fired) | `stagnation.py:294-305` |
| Can it independently declare completion? | **No.** It has no positive completion claim; the most it says is "you may report goal-met" | `stagnation.py:294-305` |
| `UNKNOWN` operationally | "no verdict yet" — no baseline, an empty observation, or a flat run shorter than `min_consecutive`. It does **not** block: `may_report_goal_met()` is `True` while flat < min and the trap has not fired | `stagnation.py:252-262, 301-305` |
| May an empty observation affect completion? | **No** — F32's fix; an empty observation returns `UNKNOWN` before touching any state | `stagnation.py:235-241` |
| Refused calls in the identity model | a refused call emits no `tool_call` event, so `_refusal_result_event` stamps the `action_key` — the one helper every refusal goes through | `stateless.py:2273`; ADR-0034 |

**Two outputs, one source.** `may_report_goal_met()` is a *completion-side* predicate;
`route_to_recovery()` is a *recovery-side* routing. Same detector, two different questions. This is
existing evidence for the brief's §8 separation, and it is the strongest structural support for Model B
(§11).

### 4.4 M12 — failure classification

| Property | Finding | Evidence |
|---|---|---|
| Nature | **classification**, not a verdict — returns a `FailureClass` | `recovery.py:181-182`; ADR-0032 decision 3 |
| What it decides | *which* taxonomy entry applies; it "never re-derives what an outcome means" | ADR-0032 (quoted) |
| Precedence *inside* it | refusal → cancellation → error code → transport markers → `recoverable` → `IMPLEMENTATION` | ADR-0032 decision 3 |
| Default | `IMPLEMENTATION`, deliberately **not** `SECURITY` (an unrecognised failure must not acquire the strongest prohibition) | ADR-0032 |
| Does classification precede recovery? | **Yes by construction** — the ladder takes a `FailureClass` as input | `recovery.py:512` |
| Can it independently affect completion? | **No** — it returns a class, and nothing consumes it | predecessor §5.1 |
| Does it only supply input to the ladder? | **Yes** — that is its entire designed role | ADR-0032 decision 3 |
| Tool denial vs code failure | denials are recognised by `is_denial_text` via `_ENGINE_DENIAL_PREFIXES` (**prefix**, so *"not blocked:"* is not a refusal); error codes via `CODE_FAILURE_CLASS` | `events.py:352,419`; ADR-0032 decisions 1–2, 4 |
| Are transient/permanent distinctions authoritative? | **The distinction is authoritative for the retry loop** (`TRANSIENT_MARKERS` is live) but **not for completion** | `subagent_orchestrator.py:938-952` |
| Live consumer | the **denial predicate** is live; the **taxonomy adapter is not** | predecessor §5.1 |

### 4.5 13-H5 — terminal evidence

| Property | Finding | Evidence |
|---|---|---|
| What decides successful completion | `turn_succeeded = saw_done and not saw_fatal_error` — a **single decision point** | `runtime.py:875-878` |
| Is `turn_succeeded` still authoritative? | **Yes** — it drives graph node status, span status and persistence | `runtime.py:998,1008,1018,1120,1224,1274` |
| Is terminal evidence evidence or authority? | **Authority.** It is the completion rule, not an input to one | `runtime.py:875-878` |
| `done` | sets `saw_done` | `runtime.py:867-868` |
| `error` | fatal iff `recoverable` is falsy → `saw_fatal_error`; also records `terminal_error_message` | `runtime.py:869-873` |
| Which errors are fatal | `CODE_TURN_TIMEOUT` (`stateless.py:308-309`), `CODE_PROVIDER_STREAM` (`:706-707`), `CODE_ITERATION_BUDGET` (`:959-960`), protocol-integrity failure (`:900-903`); refusals/denials are `recoverable=True` (`:470-660`) and therefore **not** fatal | as cited |
| Bare exhaustion | does **not** count as success | `runtime.py:875-877` |
| Does an acceptance verdict influence it? | **No** | `runtime.py:1025-1032` |

---

## 5. Live Turn Authority Trace

```
1. run_turn sets up per-turn state
   detector + ProgressSignal + call_args_by_id + terminal flags          runtime.py:680-725
        │
2. core.turn(session, prompt, ...) — async generator                      runtime.py:728
        │
3. ENGINE: provider round
        ├─ stream → content / thinking / tool_calls                       events.py:20-38
        │
4. TOOL PROPOSAL
        ├─ _validate_tool_args(name, args)   ← schema validation          stateless.py:2154
        │     └─ [F8: jsonschema absent → a VALID call is refused here]   stateless.py:2214-2227
        ├─ refusal path → _refusal_result_event(tc, ws)                   stateless.py:2273
        │     └─ stamps action_key  (a refused call emits no tool_call)   ADR-0034 / F33
        ├─ authorization: ApprovalGate / SecurityPolicy
        └─ execution: ToolExecutor
        │
5. TOOL RESULT
        └─ guard.note_tool_result(...)   ← feeds the floor guard          stateless.py:857
        │
6. PRE-`done` GATE  ◄── the only live completion *gate*
        ├─ rejection = guard.rejection()                                  stateless.py:766
        │     ├─ not None → inject nudge, `continue` (another round)       stateless.py:788-790
        │     └─ None    → fall through
        ├─ if guard.resolved(): capture_resolved_skill(...)  [side effect] stateless.py:793-804
        └─ yield done_event(...)                                          stateless.py:805
        │
7. RUNTIME consumes the event stream
        ├─ content        → assistant_content
        ├─ tool_call      → call_args_by_id (M13 action identity)         runtime.py:745-762
        ├─ tool_result    → M13 observes one closed exchange              runtime.py:763-780
        ├─ done           → saw_done = True                               runtime.py:867-868
        └─ error          → saw_fatal_error if not recoverable            runtime.py:869-873
        │
8. COMPLETION DECISION
        └─ turn_succeeded = saw_done and not saw_fatal_error   (13-H5)    runtime.py:878
        │
9. POST-TURN RECORDING (none of it consumed)
        ├─ graph node status from turn_succeeded                          runtime.py:998-1018
        ├─ P3 verdict recorded (opt-in, default OFF)                      runtime.py:1042-1052
        ├─ M13 stagnation recorded (flag-gated, only when reached)        runtime.py:1054+
        ├─ journal + spans                                                runtime.py:1116-1130
        └─ persist turn state                                             runtime.py:1205-1274
```

### Who owns each decision

| Decision | Owner today | Evidence |
|---|---|---|
| May this turn finish at all? | **`VerificationFloorGuard.rejection()`** (engine) — it can refuse to emit `done`, bounded, and surrenders honestly | `stateless.py:766`; `verification.py:159-191` |
| Did the turn succeed? | **terminal evidence** — `done` and no fatal `error` | `runtime.py:878` |
| Was this turn *verified*? | **`VerificationFloorGuard.resolved()`** — one floor implementation, published per ADR-0018 and read by the runtime | `verification.py:193-196`; `stateless.py:367`; ADR-0018 |
| Was the goal met? | **Nobody.** `GOAL_MET` is unimplemented and `may_report_goal_met()` has no caller | §4.3; predecessor §5.1 |
| Should we recover? | **Nobody.** The ladder is complete, tested, and unconsumed | ADR-0026 |
| What class of failure is this? | **`is_denial_text`** for denials (live); `classify_failure_signal` for the taxonomy (**no consumer**) | `events.py:419`; predecessor §5.1 |
| Is the acceptance verdict authoritative? | **Nobody.** Recorded, opt-in, unconsulted | `acceptance.py:20` |

### Where a future consumer must sit

Two distinct points, and they are **not** interchangeable:

- **A completion consumer must sit at the engine's pre-`done` gate** (`stateless.py:761-806`), beside
  `guard.rejection()`. That is the only place where completion can be *prevented*. A consumer placed
  after `done` is emitted can only *report* a verdict about a turn that has already been declared
  finished — which is precisely the situation P3's audit record is in today.
- **A recovery consumer must sit where a failure class is available and the next execution step is
  still undecided** — the runtime's error handling / turn boundary (`runtime.py:869-878` and the
  surrounding loop), because routing changes what happens *next*, not what happened.

**The current missing edge is therefore not one site but one relation:** the two tracks
(completion, recovery) have no shared decision point and no stated precedence.

---

## 6. Authority Classification

Each mechanism is assigned a role **only where source supports it**.

| Mechanism | Role | Boundary / caveat |
|---|---|---|
| `OscillationTrap` | **OBSERVATION** | produces evidence (repeat/cycle), explicitly *not* sufficient on its own — `observe` remains the authority (`stagnation.py:280-288`) |
| `ProgressSignal` | **OBSERVATION** | a fold of observed work; carries `is_empty` so "no information" is representable (`stagnation.py:63-91`) |
| `StagnationDetector.observe()` / `.verdict` | **VERDICT** (about progress) | a verdict about *progress*, not about *success* or *failure* (`stagnation.py:225-278`) |
| `may_report_goal_met()` | **VERDICT → COMPLETION-side predicate** | the only completion-facing output of P7; unconsumed (`stagnation.py:294`) |
| `route_to_recovery()` | **ROUTING DECISION** | converts a progress verdict into a recovery route; unconsumed (`stagnation.py:331`) |
| `classify_failure_signal()` | **CLASSIFICATION** | explicitly not a verdict: it selects a taxonomy entry and "never re-derives what an outcome means" (ADR-0032) |
| `is_denial_text()` / `_ENGINE_DENIAL_PREFIXES` | **CLASSIFICATION** | live; consumed by the retry loop (`events.py:419`) |
| `FailureClass` / `CODE_FAILURE_CLASS` | **CLASSIFICATION** vocabulary | total by test (ADR-0032 decision 4) |
| `RecoveryLadder.decide()` | **RECOVERY DECISION** | picks the next rung; makes no completion claim (`recovery.py:512`) |
| `RecoveryLadder.terminal_outcome` | **RECOVERY DECISION** (outcome) | returns `ESCALATED_TO_HUMAN` / `IN_PROGRESS`; a **string**, not an enum (`recovery.py:585-586`) |
| `BudgetGovernor` | **RECOVERY DECISION** (constraint) | gates rungs by budget (`recovery.py:496-509`) |
| `VerificationFloorGuard.rejection()` | **COMPLETION AUTHORITY** (negative) | the only live mechanism that can *prevent* completion (`stateless.py:766`) |
| `VerificationFloorGuard.resolved()` | **COMPLETION AUTHORITY** (positive, narrow) | `enabled and wrote_code and verify_ok_after_edit is True` (`verification.py:193-196`) |
| `turn_succeeded` | **COMPLETION AUTHORITY** (turn-level) | terminal evidence; drives node status, spans, persistence (`runtime.py:878`) |
| `evaluate()` → `CompletionVerdict` | **VERDICT** (a *projection* of the guard) | three-valued restatement of `floor_guard_verdict`; recorded, unconsulted (`verification.py:293-301`) |
| `route_for()` | **ROUTING DECISION** (derived) | pure map from verdict to the graph router's vocabulary; never assigned, never read (`acceptance.py:64-70`) |
| `SessionEvent.VERDICT` | **AUDIT RECORD** | journal-only, opt-in (`session.py:35`; `config.py:912`) |
| `SessionEvent.STAGNATION` | **AUDIT RECORD** | journal-only, flag-gated, written only when reached (`session.py:51`; `runtime.py:1054+`) |
| `SessionEvent.RECOVERY` | **AUDIT RECORD** | journal-only (`session.py:46`) |
| `SessionEvent.ESCALATION` | **AUDIT RECORD → state-bearing** | the **only** state-bearing kind (ADR-0028; `session.py:73-75`) |

### 6.1 The role ladder, applied

```
OBSERVATION          ProgressSignal, OscillationTrap
      ↓
CLASSIFICATION       classify_failure_signal, is_denial_text, CODE_FAILURE_CLASS
      ↓
VERDICT              StagnationDetector.verdict, CompletionVerdict
      ↓
ROUTING DECISION     route_to_recovery, route_for
      ↓
RECOVERY DECISION    RecoveryLadder.decide
      ↓
COMPLETION AUTHORITY VerificationFloorGuard.rejection/resolved, turn_succeeded
      ↓
AUDIT RECORD         VERDICT, STAGNATION, RECOVERY, ESCALATION
```

**Every layer above `AUDIT RECORD` exists. Nothing in the `ROUTING` or `RECOVERY DECISION` layers is
consumed.** The `COMPLETION AUTHORITY` layer is occupied — but only by the floor guard and terminal
evidence, which are the two mechanisms that predate the migration.

### 6.2 What is *not* a verdict

- `classify_failure_signal()` — a **classifier**. It returns a taxonomy entry.
- `STAGNATING` — a **verdict about progress**. It becomes a *failure* only when routed, and it can
  *prevent* a goal-met claim without ever asserting failure.
- `route_to_recovery()`'s return — a **routing decision**. It cannot imply success or failure.

### 6.3 The correction: P3 is not a fourth peer

`floor_guard_verdict(guard)` (`verification.py:293-301`) builds its criteria and evidence **from the
guard**, using ADR-0017's implication form `(not guard.wrote_code) or guard.resolved()`. So:

| Guard state | P3 verdict |
|---|---|
| `wrote_code` False | `INCONCLUSIVE` (criterion vacuous, no evidence exists) |
| `wrote_code`, unverified | `FAIL` (deterministic check fails) |
| `wrote_code`, verified | `PASS` |

P3 does not compete with the floor guard; **it is the floor guard, expressed three-valued.** ADR-0018
made this deliberate — reading the published guard was chosen precisely so there would not be "a second
authority for *was this verified*".

**Therefore the predecessor's "four mechanisms, one missing edge" needs refinement:** three mechanisms
(P6, P7, M12) are genuinely unconsumed, while P3's *mechanism* is live — what is unconsumed is P3's
*richer vocabulary*, and the graph-router routing derived from it.

---

## 7. Disagreement Matrix

Every proposed relation cites a source. Where no source establishes one, the cell reads
`UNSPECIFIED`.

| Situation | P3 | P7/M13 | M12/P6 | Terminal evidence today | What *should* happen |
|---|---|---|---|---|---|
| Valid work + progress | `INCONCLUSIVE` (nothing to verify) or `PASS` | `PROGRESSING` | no failure | `done`, no fatal → success | `turn_succeeded = True`. **Supported.** All four agree; no conflict. Evidence: `acceptance.py:220-223`; `stagnation.py:255-257`; `runtime.py:878` |
| No progress, criteria satisfied | `PASS` | `STAGNATING` (after `min_consecutive`) | none | `done`, no fatal → success | `UNSPECIFIED` — **the disagreement case.** P3 says accept, P7 says `may_report_goal_met() = False`. Nothing reconciles them. Evidence: `stagnation.py:294-305`; `runtime.py:878` |
| Tool denial | `INCONCLUSIVE` (no evidence) | `UNKNOWN` or `PROGRESSING` | `SECURITY` class; `RETRY` forbidden | `recoverable=True` → **not fatal** → success if `done` seen | `UNSPECIFIED` for completion. **Classification is specified**: the ladder forbids retrying `SECURITY` (`recovery.py:110,132-135`) but the ladder is unconsumed. Evidence: ADR-0032; `runtime.py:870` |
| Code failure | `FAIL` (if the deterministic floor check fails) | `UNKNOWN` | `IMPLEMENTATION` (the default) | fatal only if `recoverable` falsy | `UNSPECIFIED` for the relation. Each side is individually specified. Evidence: `acceptance.py:260-274`; `recovery.py:219` |
| Transient failure | `INCONCLUSIVE` | `UNKNOWN` | `TRANSIENT` → `{RETRY, HUMAN}` | retried by the **orchestrator's** loop, not the ladder | **Recovery is specified**; completion is `UNSPECIFIED`. Evidence: `recovery.py:87`; `subagent_orchestrator.py:938-952` |
| Permanent failure | `FAIL` | `UNKNOWN` | `IMPLEMENTATION`/`SECURITY` | fatal if `recoverable` falsy | `UNSPECIFIED`. Note ADR-0032: the default is deliberately **not** `SECURITY` |
| Verification failure | `FAIL` | `UNKNOWN` | `VERIFICATION` → `{REPAIR, LOCAL_REPLAN, DIAGNOSTIC, HUMAN}` | the guard **blocks `done`** via `rejection()`, bounded | **Supported as the only live cross-mechanism relation**: P3's `FAIL` and the guard's rejection are the same condition by construction. Evidence: `verification.py:159-191, 293-301` |
| Incomplete evidence | `INCONCLUSIVE` | `UNKNOWN` | none | `done` seen → apparent success | `UNSPECIFIED`. P3 says "not verified", terminal evidence says "succeeded" — **they do not interact at all** |
| Provider exhaustion | `INCONCLUSIVE` | `UNKNOWN` | `ENVIRONMENT` (`CODE_PROVIDER_STREAM`) | **fatal** (`recoverable=False`) → failure | **Supported for completion**: terminal evidence handles it. Recovery is `UNSPECIFIED` (ladder unconsumed) |
| Successful tool execution, unmet goal | `INCONCLUSIVE`/`FAIL` | `PROGRESSING`/`UNKNOWN` | none | `done`, no fatal → **success** | `UNSPECIFIED` — and this is the case the target architecture's `GOAL_MET` ("all acceptance criteria verified") is designed to prevent. §16 is unimplemented |

**Summary of the matrix:** exactly **one** cross-mechanism relation is actually established in source —
the floor guard and P3's `FAIL` are the same condition, by construction. Every other relation is
`UNSPECIFIED`.

---

## 8. Existing Precedence Rules

### 8.1 What exists inside a mechanism

| Precedence | Where | Scope |
|---|---|---|
| refusal → cancellation → error code → transport markers → `recoverable` → `IMPLEMENTATION` | ADR-0032 decision 3; `recovery.py:181-219` | **inside the classifier only** — it orders *which class applies*, not which mechanism wins |
| `PASS → ALLOW`, `FAIL → REJECT`, `INCONCLUSIVE → RETRY` | `acceptance.py:57-61` | **a mapping, not a precedence** — one verdict in, one route out |
| deterministic check first, and decisive | `acceptance.py:220-222, 260-274` | **inside `evaluate`** — rule ordering within one verdict computation |
| cheapest rung first; escalation never a candidate | `recovery.py:502-510, 544` | **inside the ladder** |
| `RETRY`/`REPAIR` forbidden for `STAGNATION` and `REPEATED`; everything forbidden for `SECURITY` | `recovery.py:122-136` | **inside the ladder** — a prohibition table |

**None of these is a precedence between mechanisms.** They are internal orderings.

### 8.2 The only explicit cross-mechanism precedence in the repository

`WISP_TARGET_ARCHITECTURE.md` §16 ("Termination") states an ordered termination taxonomy **and names
its precedence**:

> ```
> 1. GOAL_MET           — all acceptance criteria verified        (success-based)
> 2. GOAL_FAILED        — criteria proven unsatisfiable           (failure-based)
> 3. ESCALATED_TO_HUMAN — recovery ladder exhausted               (escalation)
> 4. BUDGET_EXHAUSTED   — the BudgetGovernor reports zero         (budget-based)
> 5. CANCELLED          — operator action                         (cancellation)
> 6. TIMED_OUT          — wall-clock deadline                     (timeout)
> ```
>
> *"Precedence is explicit: `GOAL_MET` beats `BUDGET_EXHAUSTED`; `CANCELLED` beats everything except an
> already-recorded terminal state. Every termination writes a durable, reason-bearing transition."*
> — `WISP_TARGET_ARCHITECTURE.md:391-405`

And §5.1 gives the goal state machine: `GOAL_OPEN → PLANNING → GRAPH_READY → EXECUTING →
{VERIFYING → GOAL_MET | RECOVERING}` and `EXECUTING → STAGNATED → ESCALATING`; terminal:
`GOAL_MET · GOAL_FAILED · BUDGET_EXHAUSTED · CANCELLED · ESCALATED_TO_HUMAN`
(`WISP_TARGET_ARCHITECTURE.md:165-177`).

**This is a real precedence specification, and it is the only one.** But three facts prevent it from
being *the contract*:

1. **Its vocabulary is not implemented.** `GoalState` appears in **no `.py` file** — only in
   `WISP_GRAPH_DOMAIN_MODEL.md:346` and a `CONTEXT.md` description of that document. `BUDGET_EXHAUSTED`,
   `GOAL_FAILED` and `TerminationReason` do not appear in `wisp/` at all. The single survivor is
   `ESCALATED_TO_HUMAN`, as a **string literal** returned by `RecoveryLadder.terminal_outcome`
   (`recovery.py:585-586`) — and `recovery.py:465` instructs *"the caller must surface
   `ESCALATED_TO_HUMAN`"* to a caller that does not exist.
2. **It is not ratified.** No ADR mentions termination precedence, `GOAL_MET`, or `GoalState`. The
   target document is cited by the migration for **§14** (the replay/projection guarantee) and **§5** —
   never §16. Every other architectural decision in this repository has an ADR; this one does not.
3. **It cannot express `INCONCLUSIVE`.** §16 has six termination reasons and none of them is
   "I could not tell". Yet `INCONCLUSIVE` is the value ADR-0016 and ADR-0017 make central — the whole
   reason P3's vocabulary is new. A precedence that cannot represent the implemented mechanism's most
   important value cannot be that mechanism's contract.

### 8.3 Observed divergence between the spec and the implementation

§5.1 shows `STAGNATED → ESCALATING`. The implementation routes stagnation to
`FailureClass.STAGNATION`, whose candidate rungs are `GLOBAL_REPLAN → DIAGNOSTIC` with `HUMAN` as the
*fallback* — `legal_rungs` explicitly excludes escalation as a choice (`recovery.py:502-503`). So the
spec shows escalation where the implementation prefers a replan. Recorded as a divergence requiring
reconciliation, not as a defect claim.

---

## 9. Missing / Undefined Precedence

> **No authoritative precedence relation currently exists among the mechanisms that are implemented.**

A precedence relation is *specified* (§8.2) for a termination vocabulary that is *not implemented*, is
*not ratified by an ADR*, and *cannot express `INCONCLUSIVE`*. For the mechanisms that do exist — P3's
verdict, P6's ladder, P7's stagnation, M12's classifier — there is **no ordering, no arbitration rule,
and no statement of which one may veto another.**

### The minimum architecture decision required

> **Decide and ratify, in an ADR, the completion/recovery authority and the precedence relation between
> the four implemented mechanisms — extending the target §16 taxonomy so that it can represent
> `INCONCLUSIVE`, and stating explicitly whether `INCONCLUSIVE` blocks, permits, or is orthogonal to
> completion.**

That is one decision. It is not a redesign: §16 already supplies the shape (acceptance-verified →
`GOAL_MET`; ladder-exhausted → `ESCALATED_TO_HUMAN`; budget → `BUDGET_EXHAUSTED`), and the implemented
mechanisms already supply the inputs. What is missing is (a) the mapping from
`PASS`/`FAIL`/`INCONCLUSIVE` onto that taxonomy, and (b) the arbitration rule when P7 says "do not
report goal-met" while P3 says `PASS`.

---

## 10. Recovery vs Completion Boundary

### 10.1 They are already two different questions in the architecture

The separation is not hypothetical — it is *already built*:

| Question | Mechanism | Output | Consumer |
|---|---|---|---|
| **A. Should the turn recover?** | `route_to_recovery(detector, ladder)` | a `RecoveryDecision` (a rung) | **none** |
| **B. Should the turn be considered complete?** | `may_report_goal_met()` | a bool | **none** |

One detector, two outputs, two questions. Likewise at the top level: the ladder decides *what next*
(`recovery.py:512`) and the guard decides *may this finish* (`verification.py:159`). Neither consults
the other.

### 10.2 Can one exist without the other?

**Yes, and the source demonstrates both directions:**

- **Recovery without failure.** `route_to_recovery` routes `FailureClass.STAGNATION` — a *progress*
  verdict, not a failure. `STAGNATION`'s class docstring reads `# working, not progressing`
  (`recovery.py:63`). So recovery is reachable without any failure having occurred.
- **Non-completion without recovery.** `VerificationFloorGuard.rejection()` blocks `done` and injects a
  nudge (`stateless.py:766-790`) — a *completion* intervention with no ladder involvement.
- **Completion without acceptance.** Today, trivially yes: `turn_succeeded` never consults P3
  (`runtime.py:878` vs `:1025-1032`). Under the target §16 it would be **no** — `GOAL_MET` requires "all
  acceptance criteria verified".

**Conclusion:** the two questions are architecturally distinct and must remain so. The brief's warning
against collapsing them is already satisfied by the mechanisms; what is missing is only their relation.

### 10.3 Where they meet

They do not meet today. The completion decision happens in the engine before `done`
(`stateless.py:761-806`) and in the runtime after the stream (`runtime.py:867-878`); the recovery
decision has no site at all. **The relation between the two tracks is `UNSPECIFIED`.**

---

## 11. Supported Authority Model

| Model | Supported? | Evidence |
|---|---|---|
| **A — one unified verdict** (all signals → single normalized verdict → one consumer) | **NO** | No normalizer exists. The four mechanisms have four vocabularies (`PASS/FAIL/INCONCLUSIVE`, rungs, `UNKNOWN/PROGRESSING/STAGNATING`, `FailureClass`) and nothing maps between them. Inventing a normalizer would be a redesign, which this phase forbids |
| **B — two independent authorities** (failure/progress → recovery authority; acceptance/evidence → completion authority) | **PARTIALLY — and it is what the mechanisms already imply** | `may_report_goal_met()` vs `route_to_recovery()` are two outputs of one detector (`stagnation.py:294,331`); the ladder and the guard are separate (`recovery.py:512`; `verification.py:159`). But the *relation between the two tracks* is unstated |
| **C — hierarchical** (failure/recovery → acceptance → terminal evidence) | **NOT SUPPORTED as implemented.** §16 *implies* a hierarchy for termination reasons, but it governs an unimplemented vocabulary and cannot express `INCONCLUSIVE` (§8.2) | `WISP_TARGET_ARCHITECTURE.md:391-405` |
| **D — another architecture already implied** | The §16 termination taxonomy is the only candidate, and it fails the three tests in §8.2 | as cited |

**The model the repository supports is B, with one unanswered question: what happens when the recovery
track and the completion track disagree?** That question is the architecture decision in §9.

---

## 12. Minimum Contract

The smallest contract that must be established before implementation. Where the repository does not
supply an answer, the field reads `UNSPECIFIED — ARCHITECTURE DECISION REQUIRED`.

### 12.1 Authority — who may declare what

| Declaration | Today | Specified? |
|---|---|---|
| `COMPLETE` (turn-level) | `turn_succeeded` (terminal evidence) + `guard.rejection()` gating `done` | **Yes, for the turn.** `runtime.py:878`; `stateless.py:766` |
| `COMPLETE` (goal-level, `GOAL_MET`) | **nobody** | `UNSPECIFIED` — `GoalState` is unimplemented |
| `FAILED` | terminal evidence with a fatal `error`; P3 `FAIL` records it but is unconsulted | **Partly.** `runtime.py:869-878` |
| `INCOMPLETE` | **nobody** — no such state exists; the closest is "no `done` seen", which simply fails | `UNSPECIFIED` |
| `RECOVER` | `RecoveryLadder.decide()` — complete, no consumer | **Yes as a mechanism**; unconsumed |
| `ESCALATE` | `RecoveryLadder.escalate()` → `HumanIntervention`, `ESCALATION` journal event (state-bearing) | **Yes as a mechanism**; unconsumed |

### 12.2 Inputs

Specified per mechanism (§4). **The *set* of inputs that feeds the completion authority is
`UNSPECIFIED`** — today it is terminal evidence alone; P3/P7/P6 are not inputs.

### 12.3 Precedence

`UNSPECIFIED — ARCHITECTURE DECISION REQUIRED` (§9).

### 12.4 Unknown / inconclusive

| Value | Meaning | Effect on completion today | Specified? |
|---|---|---|---|
| `INCONCLUSIVE` | criteria exist but no valid evidence — **not** failure | none (unconsulted); `accepted` is `False`; `route_for` → `RETRY`, never `ALLOW` | **Mechanism: yes** (`acceptance.py:227-232, 285-293`). **Effect: `UNSPECIFIED`** |
| `UNKNOWN` | no verdict yet — no baseline, an empty observation, or too short a flat run | does **not** block: `may_report_goal_met()` is `True` | **Yes** (`stagnation.py:252-262, 301-305`) |
| `STAGNATING` | a run of non-progressing observations at or beyond `min_consecutive` | **blocks** goal-met via `may_report_goal_met() = False` — but the predicate has no caller | **Predicate: yes.** **Effect: `UNSPECIFIED`** |

**"Unknown" is not treated as failure anywhere in the source.** `INCONCLUSIVE` is explicitly documented
as "absence of evidence is not evidence of failure" (`acceptance.py:227-228`), and `UNKNOWN` is a
non-blocking "no verdict yet". **Proven, not assumed.**

### 12.5 Recovery — before, after, or parallel?

`UNSPECIFIED`. The mechanisms are disjoint (§10.3). The target §5.1 places `RECOVERING` after
`VERIFYING` and before re-entry, which implies *after* completion evaluation — but that document is
unratified and unimplemented (§8.2).

### 12.6 Persistence

| Record | Kind | Gate | Evidence |
|---|---|---|---|
| `VERDICT` | journal-only, audit | `record_verdict`, **default `False`** | `session.py:35`; `config.py:912-913` |
| `STAGNATION` | journal-only, audit, only when the verdict is reached | `graph_oscillation_guard`, default `True` | `session.py:51`; `runtime.py:1054-1065` |
| `RECOVERY` | journal-only, audit | — (no producer today) | `session.py:46` |
| `ESCALATION` | journal-only, **state-bearing** | the only correctness-precondition record | `session.py:73-75`; ADR-0028 |

All four are in `JOURNAL_ONLY_RECORDS` (`session.py:81-84`) with shapes at `:90-99`, so the blob path
salvages them and the tests assert the blob lacks each one.

### 12.7 Replay

**Yes, for everything that is journaled.** `Session.apply` handles `VERDICT` (`session.py:204`),
`STAGNATION` (`:242`, `:532`), `RECOVERY`, `ESCALATION`; `reconstruct()` salvages the journal-only
records on the blob path (`session_repo.py:211`). The records replay deterministically.

**But there is no live *authority* result to compare against** — so "replay must not differ from live
evaluation" is `UNSPECIFIED` for the future consumer (§13).

### 12.8 Rollback

| Flag / tripwire | Protects | Evidence |
|---|---|---|
| `WISP_RECOVERY_LADDER` | the ladder's enforcement | **named in ADR-0026's reversal condition** |
| `graph_oscillation_guard` | M13's detector; `observe` returns `PROGRESSING` when disabled | `stagnation.py:232-233, 301-302` |
| `record_verdict` | P3's audit record; default `False` | `config.py:912-913` |
| AST tripwire `test_the_ladder_is_not_consulted_yet` | the turn loop does not consult the ladder | `test_stagnation_live_wiring.py:399-413` |
| AST tripwire `test_goal_met_is_not_yet_gated_on_the_live_path` | the turn loop does not call `may_report_goal_met()` | `test_stagnation_live_wiring.py:416-428` |
| `test_the_mechanism_still_has_one_authority` | one authority for the mechanism | `test_stagnation_live_wiring.py:431-439` |

**Both M13 enforcement tripwires are intact and were not modified.** They must remain intact through
any implementation phase until that phase deliberately replaces them with their inverses, as the
P9/M15 and M11/M13 precedents did.

---

## 13. Contract Invariants

| # | Invariant | Status | Evidence / reason |
|---|---|---|---|
| I1 | A terminal event alone cannot override a stronger explicit failure verdict | **REQUIRES ADR** | Today the reverse holds: terminal evidence *is* the rule and nothing stronger exists as a live input (`runtime.py:878`). The invariant presumes a hierarchy that is `UNSPECIFIED` |
| I2 | An `INCONCLUSIVE` acceptance result cannot silently become `PASS` | **SUPPORTED** | `evaluate` returns `PASS` only via `ALL_REQUIRED_SATISFIED` with cited valid evidence (`acceptance.py:295-300`); `accepted` is `verdict is PASS` (`:198-200`); `route_for(INCONCLUSIVE)` is `RETRY`, never `ALLOW` (`:57-61, 64-70`); pinned by `test_acceptance_verdict.py:399` |
| I3 | `STAGNATING` cannot be treated as successful completion | **SUPPORTED** | `may_report_goal_met()` returns `False` once stagnating, and also when the trap fired alone (`stagnation.py:294-305`); pinned by `test_stagnation_detection.py:259-302` |
| I4 | `UNKNOWN` cannot silently become `PASS` | **SUPPORTED** | `UNKNOWN` is not a `Verdict` value; no conversion path exists. Operationally it is non-blocking, which is a *different* claim from "it becomes PASS" (`stagnation.py:252-262`) |
| I5 | Recovery routing cannot itself imply successful completion | **SUPPORTED** | `RecoveryDecision` carries `failure_class`, `rung`, `reason`, `evidence`, `seq` — no completion claim (`recovery.py:558-562`); `terminal_outcome` returns `ESCALATED_TO_HUMAN`/`IN_PROGRESS` (`:585-586`) |
| I6 | A failure classification is not itself a completion verdict | **SUPPORTED** | ADR-0032: the adapter "decides *which* taxonomy entry applies and never re-derives what an outcome means"; it returns `FailureClass` (`recovery.py:181-182`) |
| I7 | An empty observation cannot create completion | **SUPPORTED** | `observe()` returns `UNKNOWN` for an empty signal without appending it, counting it flat, or feeding the trap (`stagnation.py:235-241`); the live signal is never empty (`test_stagnation_live_wiring.py:316`) |
| I8 | The M13 enforcement tripwires remain intact until an implementation phase replaces them | **SUPPORTED — verified in this phase** | `test_stagnation_live_wiring.py:399-428`; both pass in the current tree |
| I9 | Journal replay must not produce a different authority result from live evaluation | **REQUIRES ADR** | The *records* replay deterministically (`session.py:204,242,532`; `session_repo.py:211`), but no live authority result exists to be equal to. The invariant is meaningful only once a consumer exists |

**Six supported, two requiring an ADR.** No invariant was promoted from inference to contract.

---

## 14. Future Test Matrix

Design only — **no tests were written or modified in this phase.**

| # | Test | Input | Expected decision | Authority responsible | Evidence source | Implementable now? |
|---|---|---|---|---|---|---|
| **Agreement** |||||||
| T1 | all mechanisms indicate success | mutated+verified turn, progressing signal | complete | floor guard + terminal evidence | `verification.py:193`; `runtime.py:878` | **Yes** |
| T2 | all mechanisms indicate failure | fatal `error`, `recoverable=False` | failed | terminal evidence | `runtime.py:869-878` | **Yes** |
| **Conflict** |||||||
| T3 | P3 `PASS` + P7 `STAGNATING` | criteria satisfied, `consecutive_flat ≥ min_consecutive` | `UNSPECIFIED` | — | §7 | **No** — needs the ADR |
| T4 | P3 `PASS` + M12 permanent failure | satisfied criteria, `IMPLEMENTATION` class | `UNSPECIFIED` | — | §7 | **No** |
| T5 | P3 `INCONCLUSIVE` + terminal success | no evidence, `done`, no fatal | `UNSPECIFIED` (today: success) | — | `runtime.py:878` | **No** |
| T6 | P3 `FAIL` + terminal success | deterministic check failed, `done` | `UNSPECIFIED` (today: success) | — | `acceptance.py:260-274` | **No** |
| T7 | P7 `UNKNOWN` + terminal success | no baseline, `done` | complete | terminal evidence | `stagnation.py:252-253` | **Yes** (asserts UNKNOWN does not block) |
| T8 | P7 `STAGNATING` + terminal success | flat run ≥ min, `done` | `UNSPECIFIED` (today: success) | — | §7 | **No** |
| **Recovery** |||||||
| T9 | transient failure → recovery | `TRANSIENT` class, evidence | rung ∈ `{RETRY, HUMAN}` | `RecoveryLadder.decide` | `recovery.py:87` | **Yes** (unit-level; already covered by `test_recovery_ladder.py`) |
| T10 | permanent failure → no retry | `SECURITY` class | escalation; `RETRY` forbidden | `FORBIDDEN_RUNGS` | `recovery.py:110,132-135` | **Yes** |
| T11 | stagnation → recovery path | `STAGNATING` + a ladder | `GLOBAL_REPLAN` or `DIAGNOSTIC`; never `RETRY`/`REPAIR` | `route_to_recovery` | `stagnation.py:331-350`; `recovery.py:107-109,131` | **Yes** |
| T12 | recovery exhaustion → escalation | all rungs tried/budget-exhausted | `HumanIntervention`, `ESCALATED_TO_HUMAN` | `RecoveryLadder.escalate` | `recovery.py:564-580, 585-586` | **Yes** |
| T13 | **recovery decision reaches the live turn** | a real turn with a classified failure | the turn's next step changes | **no authority** | — | **No — the missing edge** |
| **Replay** |||||||
| T14 | live decision == replayed decision | journal a `VERDICT`/`STAGNATION`/`RECOVERY`/`ESCALATION`, reconstruct | identical records | `Session.apply` / `reconstruct()` | `session.py:204,242,532`; `session_repo.py:211` | **Yes for records** |
| T15 | replay of an *authority* decision | as T14, plus a live verdict | identical authority result | `UNSPECIFIED` | §12.7 | **No** — needs the ADR |
| **Safety** |||||||
| T16 | empty `ProgressSignal` never creates completion | `ProgressSignal()` | `UNKNOWN`, no completion effect | `StagnationDetector.observe` | `stagnation.py:235-241` | **Yes** |
| T17 | M13 tripwires remain intact | AST over the turn loop | no `.decide` call, no `may_report_goal_met` call | the tripwires themselves | `test_stagnation_live_wiring.py:399-428` | **Yes** |
| T18 | a refusal carries the action identity | a refused call | `action_key` present on the refusal event | `_refusal_result_event` | `stateless.py:2273` | **Yes** |

**Ten of eighteen are implementable now.** The eight that are not all depend on the single
architecture decision in §9 — which is the point of this phase.

---

## 15. F8 / External Dependencies

`jsonschema` is declared (`pyproject.toml:22`) but not importable; `stateless.py:2214-2227` converts the
`ModuleNotFoundError` into `"Schema validation failed for tool '<name>': ..."`, a hard denial. The
predecessor reproduced this with a **valid** argument dict.

**Classification: `secondary enablement dependency`.** It does **not** change the verdict semantics
investigated here:

- P3's verdict is computed from `VerificationFloorGuard` state, not from tool execution — so the
  three-valued vocabulary is unaffected.
- The stagnation signal is built from `action_key` + outcome hash, both of which exist on the refusal
  path — which is exactly why F33's fix stamps the refusal (`stateless.py:2273`).
- Terminal evidence is derived from the event stream, which is unaffected.

**What F8 does block** is the P3 **stage-3b measurement** (ADR-0016's reversal condition), because
`wrote_code` never becomes true when no tool can execute. That is a *measurement* blocker on a
*future* enablement, not a contract blocker.

**Not repaired in this phase.** Validation was not changed from fail-closed to fail-open.

---

## 16. Rejected Alternatives

| Alternative | Why rejected |
|---|---|
| **Adopt §16's precedence as the contract** | Its vocabulary is unimplemented (`GoalState` in no `.py` file), it is unratified by any ADR, and it cannot express `INCONCLUSIVE` — the implemented mechanism's central value (§8.2). Adopting it would be inventing semantics the repository does not implement |
| **Model A: build a normalizer over all four mechanisms** | Requires inventing a mapping none of the sources state, and introduces a fifth vocabulary. Forbidden by the brief ("do not invent the missing semantics") and by the repo's own one-authority discipline |
| **Treat P3 as a fourth peer authority** | The source contradicts it: `floor_guard_verdict` builds criteria and evidence from the guard, so P3 *is* the guard in another vocabulary. ADR-0018 chose the published-guard read precisely to avoid a second authority (§6.3) |
| **Treat `classify_failure_signal` as a verdict** | ADR-0032 says it is an adapter that "never re-derives what an outcome means" and returns a `FailureClass` (§6.2) |
| **Treat `STAGNATING` as a failure verdict** | It is a verdict about *progress*; `FailureClass.STAGNATION`'s docstring is `# working, not progressing` (`recovery.py:63`). It becomes a failure only by being routed |
| **Declare the contract `BLOCKED_BY_EXTERNAL_DEPENDENCY`** | The contract is determinable without F8 — the authority and precedence questions are entirely internal (§15). F8 blocks a measurement, not the contract |
| **Declare the contract `READY_FOR_IMPLEMENTATION`** | The authority and precedence semantics are *not* sufficiently specified. §16 is the only precedence statement and fails three tests; the arbitration rule for P3-vs-P7 disagreement does not exist anywhere |
| **Repair F8 here** | Explicitly excluded by the brief; it is an authority-semantics change (fail-closed → fail-open), not a contract question |
| **Reopen M1/M5/M6/M7/M8/M10/M13-enforcement** | Excluded by the brief; the predecessor's verdicts stand unless contradicted, and nothing in this phase contradicted them |

---

## 17. Implementation Preconditions

Before any consumer is built, all of the following must hold:

1. **An ADR ratifying the completion/recovery authority and their precedence**, extending the §16
   termination taxonomy so that it can represent `INCONCLUSIVE`, and stating explicitly whether
   `INCONCLUSIVE` blocks, permits, or is orthogonal to completion. *(The one decision in §9.)*
2. **An explicit arbitration rule** for the P3-`PASS` / P7-`STAGNATING` disagreement — today the two
   mechanisms make contradictory claims and nothing reconciles them.
3. **A statement of whether the recovery track runs before, after, or in parallel with completion
   evaluation** (§12.5).
4. **The consumer site named in source**: the engine's pre-`done` gate (`stateless.py:761-806`) for
   completion, and the runtime turn boundary (`runtime.py:869-878`) for recovery (§5).
5. **The M13 tripwires replaced by their inverses in the same commit** that wires the consumer — the
   P9/M15 and M11/M13 precedents. They must not be weakened beforehand (§12.8).
6. **Rollback preserved**: `graph_oscillation_guard` and `record_verdict` are the two switches that
   **exist in code** today, and their declared defaults must be unchanged. `WISP_RECOVERY_LADDER` is
   **reserved by ADR-0026's reversal condition but does not exist in any `.py` file** (it appears only
   in `WISP_MIGRATION_PLAN.md:482`, `WISP_ARCHITECTURE_DECISIONS.md:766` and `PHASE_P6_REPORT.md:10,169`)
   — the implementation phase must **add** it, not assume it.
7. **F8 accepted as a measurement blocker**, not a contract blocker (§15) — the P3 stage-3b measurement
   cannot be taken until the tool path works, and that is a separate decision.

---

## 18. Final Verdict

```
CONTRACT STATUS:
REQUIRES_ARCHITECTURE_DECISION

AUTHORITATIVE COMPLETION SOURCE:
Two-stage, and narrower than the predecessor implied:
  (1) the engine's pre-`done` gate — VerificationFloorGuard.rejection()
      (core/verification.py:159), consulted at core/stateless.py:766;
  (2) the runtime's derivation — turn_succeeded = saw_done and not saw_fatal_error
      (core/runtime.py:878, 13-H5).
Goal-level completion (GOAL_MET) is UNSPECIFIED — GoalState appears in no .py file.
P3's CompletionVerdict is NOT a separate authority: it is floor_guard_verdict()
(verification.py:293-301) restating the same guard three-valued, recorded opt-in
and unconsumed (acceptance.py:20; runtime.py:1042-1052).

RECOVERY AUTHORITY:
RecoveryLadder.decide() (core/recovery.py:512) — complete, tested, no live consumer
(ADR-0026). Terminal outcome at recovery.py:585-586. The classifier that would feed
it, classify_failure_signal() (recovery.py:181), also has no consumer (ADR-0032).

VERDICT PRECEDENCE:
UNSPECIFIED for every implemented mechanism. No ordering, arbitration rule, or veto
exists among P3's verdict, P6's ladder, P7's stagnation and M12's classifier.
The repository's ONLY explicit precedence statement is
WISP_TARGET_ARCHITECTURE.md:391-405 (§16), which orders a termination vocabulary that
is (a) unimplemented, (b) cited by no ADR — only §14 and §5 of that document are ever
cited, and (c) unable to express INCONCLUSIVE. Its §5.1 also diverges from the
implementation on stagnation routing (ESCALATING vs GLOBAL_REPLAN).

MISSING AUTHORITY EDGE:
Two tracks with no shared decision point.
  completion: AgentRuntime.run_turn's pre-`done` gate (core/stateless.py:761-806),
              which is the ONLY site where completion can be prevented;
  recovery:   the runtime turn boundary (core/runtime.py:869-878), where a failure
              class is available and the next step is still undecided.
Neither consults the other, and no rule states their precedence.

IMPLEMENTATION PRECONDITION:
One statement — ratify in an ADR the completion/recovery authority and the precedence
between the four implemented mechanisms, extending the §16 termination taxonomy to
represent INCONCLUSIVE and stating whether INCONCLUSIVE blocks, permits, or is
orthogonal to completion, together with the arbitration rule for a P3-PASS /
P7-STAGNATING disagreement.

PRODUCTION CHANGES:
NONE. No file under wisp/ or tests/ was modified, no test was weakened, no tripwire was
changed, and F8 was not repaired. Verified: the tracked-file diff is byte-identical in
size to the pre-phase snapshot, and `git status` differs only by this phase's own
documents.
Files written:
  - PHASE_POST_M13_VERDICT_CONTRACT.md                       (this document)
  - .workbuddy-ai/memory/post-m13-contract/{head,branch,status-before,diffstat-before}.txt
  - .workbuddy-ai/memory/post-m13-contract/diff-before.patch

REGRESSIONS:
NONE EXPECTED — READ-ONLY PHASE. No test was run against a modified tree, because no
tree was modified. The predecessor's baseline (129 failures, set-identical; 714
migration tests; 222 M11-M16 tests) was taken on this same tree and remains the
reference.

NEXT PHASE:
ARCHITECTURE DECISION REQUIRED — an ADR for the completion/recovery authority and
verdict precedence, per §9 and §17.
```

---

## Appendix — The thirteen success-criteria questions, answered

| # | Question | Answer | Evidence |
|---|---|---|---|
| 1 | What is a signal? | An observation of what happened, carrying no claim about success — `ProgressSignal` (work units, outcomes, criteria counts) | `stagnation.py:63-91` |
| 2 | What is a classification? | A selection of which category an already-observed outcome belongs to, deriving nothing new — `classify_failure_signal` → `FailureClass`; `is_denial_text` → bool | ADR-0032; `recovery.py:181` |
| 3 | What is a verdict? | A conclusion against criteria, in a total vocabulary — `CompletionVerdict` (`PASS`/`FAIL`/`INCONCLUSIVE`); `StagnationVerdict` about progress | `acceptance.py:40-51`; `stagnation.py:225-278` |
| 4 | What is a recovery decision? | A choice of next rung, cost-ordered and budget-governed — `RecoveryDecision` | `recovery.py:512-562` |
| 5 | What is a completion decision? | Today: "may this turn finish" (guard) and "did it succeed" (terminal evidence). Goal-level: unimplemented | `stateless.py:766`; `runtime.py:878` |
| 6 | Which component owns each? | §5 "Who owns each decision" — the two completion owners are the guard and terminal evidence; recovery has **no** owner; goal-met has **no** owner | §5 |
| 7 | Where do they meet? | **They do not.** Two disjoint tracks; the only established cross-mechanism relation is guard↔P3, which are the same condition by construction | §6.3, §10.3 |
| 8 | What happens when they disagree? | `UNSPECIFIED` — no arbitration rule exists for any pair | §7, §9 |
| 9 | What is the precedence? | `UNSPECIFIED` for implemented mechanisms; specified only for an unimplemented, unratified vocabulary that cannot express `INCONCLUSIVE` | §8, §9 |
| 10 | What does `INCONCLUSIVE` mean? | Criteria exist but no valid evidence — explicitly **not** failure; `accepted` is False; routes to `RETRY`, never `ALLOW`. Its effect on completion is `UNSPECIFIED` | `acceptance.py:227-232, 285-293, 198-200` |
| 11 | What does `UNKNOWN` mean? | No verdict yet (no baseline, an empty observation, or a flat run shorter than `min_consecutive`); it does **not** block goal-met | `stagnation.py:252-262, 301-305` |
| 12 | What does `STAGNATING` mean? | A run of non-progressing observations at or beyond `min_consecutive`; blocks goal-met; becomes a failure only when routed | `stagnation.py:259-261, 294-305, 331-350` |
| 13 | Can recovery happen without failure? | **Yes** — `FailureClass.STAGNATION` is `# working, not progressing`; `route_to_recovery` triggers on a progress verdict | `recovery.py:63`; `stagnation.py:331-350` |
| 14 | Can completion happen without acceptance? | **Yes today** (`turn_succeeded` never consults P3). **No under the target §16** (`GOAL_MET` requires "all acceptance criteria verified"). The two disagree — this is the `UNSPECIFIED` case | `runtime.py:878` vs `WISP_TARGET_ARCHITECTURE.md:396` |
| 15 | Can terminal evidence override a stronger verdict? | The question presumes a stronger verdict exists as a live input; none does. Today terminal evidence *is* the rule | `runtime.py:875-878` |
| 16 | Can the result be replayed deterministically? | **Yes for the records** (`VERDICT`, `STAGNATION`, `RECOVERY`, `ESCALATION` all journal-only and replayed by `apply`/`reconstruct`). **`UNSPECIFIED` for an authority result**, since none exists to compare | `session.py:204,242,532`; `session_repo.py:211` |
| 17 | What exact contract must the future consumer implement? | The contract in §12, which cannot be completed until the §9 decision is made | §12, §9 |
