# PHASE POST-M13 — FINAL EXECUTION SEMANTICS CLOSURE

**Status:** COMPLETE · **ADRs:** 0043, 0044 · **Phase:** PM-24 · **Date:** 2026-09-25 · **HEAD:** `7c15626`

---

## 1. Executive Result

```text
EXECUTION SEMANTICS: CLOSED
```

The end-to-end audit found **two remaining sources of semantic ambiguity**, both of the same class the
mission set out to make impossible, and both are now removed:

1. **A vocabulary list still participated in a semantic decision** (ADR-0043). ADR-0041 had fixed F43
   by reordering the checks and shrinking the bookkeeping set — but the audit found the *same defect
   through a second door*: an **unrecognised, payload-less event** counted as response output and could
   bless an empty attempt. Measured: `unknown + bare terminal → 1 call, no error` where
   `bare terminal alone → 3 calls, error`. The mechanism is now **deleted**, not derived around:
   meaningfulness is the payload question, asked the same way for every event type.

2. **The turn-level predicate was implemented twice** (ADR-0044). `turn_succeeded` and
   `terminal_outcome` were the same predicate computed in two places, then **both handed to one
   arbiter** — and `goal.py`'s docstring claimed they "can never disagree" while nothing enforced it.
   The flag is now a **projection** of the outcome, computed once. A cross-layer name collision
   (`RecoveryLadder.terminal_outcome` returning a `GoalState`-shaped string) was removed with it.

**Everything else in the chain was already closed and is now proven so**, not merely documented: the
ten-scenario authority matrix, the non-collapse invariants, the replay equality, and the provider
differential.

```text
PRODUCTION   : ~+125 / −25 across 4 files        TESTS  : 1 new file (40 tests) + 12 updated
FALSIFICATION: 0 of 22                           NEW FAILURES: 0
REPLAY       : UNCHANGED — no migration          SECURITY: UNCHANGED
```

---

## 2. Provenance

| | |
|---|---|
| HEAD | `7c15626` "docs(m11): record the M11 phase; findings F29-F31; repair the commit table" |
| branch | `main` · commits **0** · staged **0** |
| modified tracked | 52 · untracked 77 |

`wisp/core/stateless.py` and `wisp/core/runtime.py` carry **pre-existing uncommitted WIP** from earlier
phases; the diff for those files is attributed **hunk-by-hunk** in §19. `provider_stream.py` and
`recovery.py` were otherwise clean. Nothing was reset, stashed, cleaned, checked out or staged.

---

## 3. Final State Machine

```text
PROVIDER
  emits: typed dataclass OR canonical dict          (ADR-0039 R1)
     │
     ▼  events.canonical_event                       (ADR-0040 — the ONE canonicalizer)
CANONICAL EVENT  {type, …16-field whitelist}         unknown fields dropped
     │
     ▼  guarded_provider_stream
STREAM CLASSIFICATION                                (ADR-0043)
     1. terminal?  → saw_terminal = True
     2. carries payload? → got_meaningful = True     ← the ONLY meaningfulness rule
     3. terminal → consume & break (markers not forwarded)
     4. otherwise → forward
     │  bounded retry (max_attempts) · stall detection · truncation notice
     ▼
STREAM STATE  complete | empty | truncated | failed
     │
     ▼
TURN STATE   terminal_outcome = from(saw_done, saw_fatal_error)   (ADR-0044 R1)
             turn_succeeded   = outcome is SUCCEEDED              (ADR-0044 R2 — projection)
     │
     ▼
VERIFICATION acceptance_verdict (P3) ← VerificationFloorGuard      (ADR-0018 / ADR-0035)
     │
     ▼
GOAL STATE   derive_goal_state(outcome, acceptance, stagnation, …)  (ADR-0035, 6 states)
     │
     ▼
ROUTING      terminal_outcome · escalation · incomplete-turn replay
     │
     ▼
PERSISTENCE  journal events · goal record · last-event semantics
     │
     ▼
REPLAY       same durable inputs → same state, live or reconstructed
```

**Every arrow is one-way.** No lower layer certifies a higher one.

---

## 4. Final Authority Graph

| Semantic fact | Exact authority | Competing implementations found |
|---|---|---|
| provider ended stream | `provider_stream.TERMINAL_TYPES` | **none** (ADR-0043 removed the last re-spelling) |
| event is terminal | `ntype in TERMINAL_TYPES` (one site) | none |
| event is bookkeeping | **no authority — the concept is gone** | n/a |
| stream is meaningful | `_event_has_payload` (one site, all types) | none |
| stream should retry | the guard's `attempt < max_attempts` | none |
| stream failed | the guard's post-loop branches | none |
| turn completed | `SessionRepository.was_last_turn_complete` (journal read) | none |
| turn succeeded | **derived** from `terminal_outcome` | **was two — closed by ADR-0044** |
| the turn's terminal evidence | `goal.terminal_outcome_from_evidence` | none |
| verification passed | `VerificationFloorGuard` → `floor_guard_verdict` | none |
| acceptance satisfied | `acceptance_verdict` (P3) | none |
| stagnation exists | `StagnationDetector.may_report_goal_met` | none |
| recovery should occur | `classify_failure_signal` → `RecoveryLadder` | none |
| escalation required | `RecoveryLadder.escalate` → `escalated` | none |
| goal met | `derive_goal_state` (ADR-0035) | none |
| next turn should replay | `was_last_turn_complete` (journal read) | none |

Compatibility projections are explicitly permitted and named: `turn_succeeded` (of
`terminal_outcome`), the goal record's fields (of the arbiter), `was_last_turn_complete` (of the
journal's last event).

---

## 5. Provider / Event Contract

Unchanged from ADR-0039/0040. A provider MAY yield a canonical dict **or** a typed event; neither is a
defect; `events.canonical_event` is the one canonicalizer; `WispAgentCore._normalize_event` is a
delegation facade. Verified: 1 `CANONICAL_EVENT_FIELDS` definition, 1 provider-object mapping site,
`events.py` still a leaf module.

---

## 6. Stream / Recovery Contract

```text
TERMINAL_TYPES = {done, complete, stream_complete}      ← the ONLY vocabulary the classifier owns
meaningfulness = _event_has_payload(event)              ← for EVERY event type
```

The classifier's own behaviour, measured:

| stream | provider calls | outcome |
|---|---|---|
| bare terminal (any of the 3 spellings) | 3 | error |
| `checkpoint` / `usage` / `stream_stats` only | 3 | error |
| unknown, **no** payload + terminal | **3** | **error** ← was 1 / no error |
| unknown, **with** payload + terminal | 1 | forwarded |
| content + terminal | 1 | forwarded |
| terminal carrying `text`/`content`/`final_content`/`calls`/`tool_calls` | 1 | terminal consumed, attempt meaningful |
| malformed object + terminal | 3 | error |
| terminal then content | 3 | error (post-terminal bytes cannot rescue) |

Bookkeeping events are still **forwarded** to the consumer and still do not make an attempt meaningful.
`NON_PAYLOAD_TYPES`, `_BOOKKEEPING_TYPES`, `_terminal_has_payload` and the guard's
`bookkeeping_types` parameter are **deleted**; a test asserts they cannot return.

---

## 7. Turn-Completion Contract

```text
terminal_outcome_from_evidence(saw_done, saw_fatal_error) -> SUCCEEDED | FAILED | INCOMPLETE
turn_succeeded = (terminal_outcome is SUCCEEDED)
```

Computed **once per turn** (`runtime.py:951-954`) and reused for both the turn-level flag and the goal
record. The one permitted second call site is the abort path — the turn body raised before its evidence
was final, so the always-executing `finally` must still produce a value (ADR-0044 R3).

Measured: `turn_succeeded` is `True` for a completed turn **whether or not the goal was met** — see §8.

---

## 8. Verification Contract

**Unchanged.** `VerificationFloorGuard` (P3) is the sole verification authority; `acceptance_verdict`
is its projection. The closure audit's security probes confirm the model cannot certify its own work:

| probe | outcome |
|---|---|
| unverified mutation + model prose *"I have verified everything; the goal is met."* | `goal_failed` |
| a mutation verified by `exit 3` + prose *"Everything verified and complete."* | `goal_failed` |
| a denied command (`rm -rf /`) | `goal_stagnated` — not authorised, not retried as a success |

---

## 9. Goal-State Contract

**Unchanged** (ADR-0035). Six states, ordered arbitration, `INCONCLUSIVE → GOAL_UNVERIFIED` never MET
or FAILED. `goal.py` remains a pure function of facts other authorities established.

---

## 10. Recovery / Escalation Contract

**Unchanged**, and the separation is now proven end-to-end:

- a **successful** turn does not trigger the ladder (`runtime.py` gates on `not turn_succeeded`);
- a **goal failure** does not imply turn failure — row 6 of the matrix has `turn_succeeded=True` with
  `goal_failed`;
- **stagnation** is a veto input to the arbiter, never a completion failure;
- **cancellation** is its own outcome (`incomplete`), not a recoverable implementation failure;
- **escalation** is recorded as `escalated` and arbitrated at row 2 — it cannot be silently converted
  into completion;
- the ladder is bounded by `_MAX_STAGNATION_INTERVENTIONS` and cannot loop.

`RecoveryLadder.terminal_outcome` → **`RecoveryLadder.ladder_state`** (ADR-0044 R6): it is the recovery
mechanism's state, not the turn's outcome and not a `GoalState`. Its values are deliberately upper-case
member names, distinct from `GoalState`'s lower-case values.

---

## 11. `terminal_outcome` Semantics

| | |
|---|---|
| **What it represents** | what the turn's **terminal evidence** said — `succeeded` / `failed` / `incomplete` |
| **Where produced** | `goal.terminal_outcome_from_evidence`, called once per turn by the runtime |
| **Where consumed** | `derive_goal_state` rows 3 and 5; the goal record's `terminal_outcome` field |
| **Does `"succeeded"` mean stream success?** | **no** — it means the turn reached a `done` with no fatal error |
| **Can it be confused with `GoalState`?** | it is a *different* enum (`TerminalOutcome`, 3 values) and a *different* field. R5 states the boundary; the phase's tests assert `turn_succeeded` and `goal_state` separately |
| **Is it part of the durable contract?** | yes — it is journalled in the goal record and read by replay |

Measured: `terminal_outcome == "succeeded"` **with** `goal_state == "goal_failed"` occurs (a failed
verification). That is accurate at turn level and is the exact pair ADR-0042 forbids collapsing.

**No rename** — it is a durable, replay-read field, and its meaning is now stated rather than changed.

---

## 12. `was_last_turn_complete` Semantics

| | |
|---|---|
| **What it is** | "the last persisted event is a DONE event" (`session_repo.py:243`) |
| **What it is NOT** | a success verdict, an acceptance verdict, or a goal state |
| **Its one consumer** | `runtime.py:588` — the incomplete-turn / crash-recovery replay |
| **Producers** | the runtime's persist loop, indirectly (it is derived, not assigned) |

Verified across every scenario in the matrix: it tracks the **journal's last event**, not the turn's
success. It is `True` for a completed turn whose goal **failed** (a failed verification) and `False` for
a provider failure. The phase's suite asserts it separately from `turn_succeeded` and `goal_state` in
every row, so it cannot silently become a proxy for either.

---

## 13. Exhaustion / Wrap-up Semantics

| scenario | `turn_succeeded` | `was_last_turn_complete` | `goal_state` | outcome |
|---|---|---|---|---|
| exhaustion, wrap-up **succeeds** | **True** | True | **`goal_unverified`** | succeeded |
| exhaustion, wrap-up **fails** | False | False | `goal_failed` | failed |

Exhaustion is an **absence of evidence**, and ADR-0035 maps that to `GOAL_UNVERIFIED` — never
`GOAL_FAILED`, never `GOAL_MET`. The next turn correctly does **not** replay a completed exhausted turn.

**Follow-up C — is a structured exhaustion reason needed?** **No, and ADR-0035 already decided it.**
`goal.py`'s own docstring states it: *"`BUDGET_EXHAUSTED` and `TIMED_OUT` are deliberately **not**
states: they reach the runtime as fatal error codes and are carried as the `reason` of
`GOAL_FAILED`."* The goal record already carries `failure_code` (populated from `terminal_error_code`),
plus `cancelled`, `escalated`, `terminal_outcome` and `turn_succeeded`. Adding
`WRAPUP_SUCCEEDED`/`WRAPUP_FAILED` would duplicate what `turn_succeeded` + `terminal_outcome` already
say. **No new durable field; no new GoalState value.**

---

## 14. Unknown-Event Semantics

**Closed.** An unknown event is **not** special-cased at all: it is subject to the same payload rule as
every other type.

```text
unknown, no payload      -> not meaningful  -> does NOT bless an empty attempt
unknown, with payload    -> meaningful      -> forwarded, counts as output
```

Proven by probe **S1** across seven empty-attempt shapes (unknown, unknown+terminal, bare terminal,
checkpoint/usage/stream_stats+terminal, malformed object, empty-string payload): **none** blessed.

The `unknown` event type is still produced by canonicalization for an unrecognisable shape
(`type="unknown"`, ADR-0039 R3) — that is the *canonical* type, and it carries no payload, so it is
correctly not a response.

---

## 15. Payload Semantics

The payload key set is `text`, `content`, `final_content`, `tool_calls`, `calls`, and a key counts only
when its value is a non-empty string or a non-empty list/tuple/dict.

**`final_content` is retained deliberately (ADR-0043 R6).** It is unreachable for *typed* providers —
canonicalization projects a `StreamComplete` to `{type, done_reason}` — but **reachable for dict
providers**, because `canonical_event` copies a plain dict unchanged:

```text
canonical_event({"type":"done","final_content":"x"})  ->  {'type':'done','final_content':'x'}
canonical_event(StreamComplete(final_content="x"))    ->  {'type':'complete','done_reason':''}
```

Removing it would make a dict provider's payload invisible and turn a real response into a silent empty
attempt — a *regression in permissiveness*. So the asymmetry is documented as a **deliberate
compatibility boundary**, not a dead key, and pinned by
`test_final_content_is_a_deliberate_compatibility_key`.

---

## 16. Provider Differential Matrix

| dimension | typed vs dict | evidence |
|---|---|---|
| guard outcome (content) | identical | `test_guard_outcome_is_identical` |
| guard outcome (tool call) | identical | same |
| guard outcome (raise) | identical | same |
| all three terminal spellings | identical | `test_all_terminal_spellings_agree` |
| turn state | identical | `test_turn_and_goal_state_are_identical` |
| acceptance verdict | identical | same |
| goal state | identical | same |
| `terminal_outcome` | identical | same |
| `was_last_turn_complete` | identical | same |
| exhaustion row | identical | `test_exhaustion_row_agrees` |

**A provider gains and loses no semantic authority by representation.**

---

## 17. Hidden-Authority Audit

Every candidate pattern was classified. Findings and dispositions:

| occurrence | classification | disposition |
|---|---|---|
| `turn_succeeded` (18 sites, runtime) | **authoritative decision** — *was two implementations* | **fixed** — now a projection (ADR-0044) |
| `terminal_outcome` (goal record) | authoritative, turn-level | meaning stated (§11); no rename |
| `RecoveryLadder.terminal_outcome` | **derived projection, misnamed** | **renamed** to `ladder_state` (ADR-0044 R6) |
| `events.is_terminal_outcome(result)` | tool-result classification — a third, unrelated domain | no change (name is local to tool results) |
| `_BOOKKEEPING_TYPES` / `NON_PAYLOAD_TYPES` | **vocabulary in a semantic decision** | **deleted** (ADR-0043) |
| `session.py`'s `goal_state` (10 sites) | **persistence plumbing** (event type, payload key, list field) | no change — stores, does not decide |
| `stagnation.py`'s `goal_met` (5 sites) | the `may_report_goal_met` predicate — a *veto input* | no change |
| `wisp/planner.py::is_complete` | a **task-list** method (all tasks done/skipped), and the module is **unwired** | no change — different domain |
| `was_last_turn_complete` (2 sites) | one definition, one consumer | no change |
| `should_retry` | **0 occurrences** | n/a |
| `completed` / `finished` / `escalat` (elsewhere) | telemetry, graph run state, background agents, subagent contracts | no change — outside the execution chain |

**No undeclared completion authority remains.** The two that existed are both closed.

---

## 18. Security Audit

**Unchanged.** No file in the authorization path was touched:

```text
tool proposal → argument validation → authorization → approval → execution → verification
```

Probes confirm the model cannot certify its own mutation (§8), that a denied command does not become an
authorized retry, and that a fatal error is not masked by a terminal event. The classifier change
narrows what counts as a response — strictly *less* permissive. Canonicalization remains a whitelist
projection: it removes keys, never executes.

---

## 19. Performance Impact

**Net neutral or better.**

- The classifier **removed** work: one set lookup per event (`ntype not in bookkeeping`) is gone, and
  `bookkeeping = set(bookkeeping_types)` no longer allocates a set per stream.
- `_event_has_payload` is called once per event where `_terminal_has_payload` was called only for
  terminals — so non-terminal events gain one small loop over 5 keys. Measured cost is negligible
  (a dict `.get` per key, short-circuiting on the first hit).
- The turn predicate is now computed **once** instead of twice on the normal path — one fewer call.
- No new persistence writes, no new model calls, no new round trips, no new graph nodes, no new state.
- **No new durable field** (§13).

Diff: **~+125 / −25 across 4 files** — `provider_stream.py` +35/−11, `stateless.py` +36/−5,
`runtime.py` +45/−7, `recovery.py` +9/−2.

---

## 20. Tests

| | before | after |
|---|---|---|
| `tests/reliability/` | 402 passed | **427 passed** |
| canonical migration set (29 files) | 848 passed / 1 failed | **848 passed / 1 failed** |
| stream / recovery / forensics suites | 216 passed / 2 failed | **216 passed / 2 failed** |

```text
NEW      : tests/reliability/test_post_m13_execution_semantics_closure.py
           (508 lines, 40 tests — semantic invariants, differential, adversarial)
MODIFIED : 12 test files
            4 updated for ADR-0043 (the deleted vocabulary, and the changed empty/malformed diagnostic)
            2 updated for ADR-0044 (the literal turn predicate, the ladder rename)
            4 call sites updated for the removed guard parameter
            2 comments/docstrings aligned
NEW FAILURES : 0
```

### 20.1 The tests that had to change — and why

| test | why | how |
|---|---|---|
| `test_13h_forensics::test_h1_…`, `test_stream_guard_phase2`, `test_stream_retry_hygiene_phase3` (×2), `test_repl_audit_pindown` | passed the removed `bookkeeping_types` positionally | argument removed; comments updated |
| `test_13h2::test_malformed_event_without_terminal_marker` | the diagnostic changed: a payload-less malformed event is now an **empty** attempt, not a truncated one | asserts the empty-stream diagnostic; the invariant (no `done`, errors present, nothing silent) is unchanged |
| `test_13h4::test_completion_derives_from_terminal_evidence`, `test_acceptance_verdict::test_completion_rule_is_unchanged` | pinned the **literal expression** of the turn predicate | assert the single-predicate form, and that the predicate still consults terminal evidence only |
| `test_post_m13_f40::test_F43_…`, `test_post_m13_f43_f44::TestF43Vocabulary` | referenced the deleted constant | assert the constant is **gone** and the guard takes no vocabulary argument |
| `test_recovery_ladder` (5), `test_stagnation_detection` (1) | read `ladder.terminal_outcome` | read `ladder.ladder_state` |

None was deleted; none was weakened to fit the output. Each rewrite preserved the test's original
purpose.

---

## 21. Falsification Results

**0 of 22 falsified.**

| | probe | result |
|---|---|---|
| S1 | an empty attempt is blessed by any of 7 shapes | NOT FALSIFIED — none blessed |
| S2 | a payload key is ignored | NOT FALSIFIED — all 5 count |
| S3 | terminal spellings differ | NOT FALSIFIED — all `(3, error)` |
| S4 | bookkeeping becomes terminal | NOT FALSIFIED |
| S5 | post-terminal content rescues an empty attempt | NOT FALSIFIED — 3 calls, error |
| T1 | the turn predicate is re-implemented | NOT FALSIFIED — one implementation |
| T2 | more than 2 computation paths | NOT FALSIFIED — 2 (normal + abort) |
| T3 | outcome and flag disagree | NOT FALSIFIED — all 4 combinations agree |
| T4 | the ladder shares the turn outcome's name | NOT FALSIFIED — renamed |
| A1 | turn success implies goal met | NOT FALSIFIED — `goal_unverified` |
| A2 | a wrap-up claiming verification certifies | NOT FALSIFIED — `goal_failed` |
| A3 | `turn_succeeded` forces `GOAL_MET` | NOT FALSIFIED — `goal_failed` |
| A4 | exhaustion means goal failure | NOT FALSIFIED — `goal_unverified` |
| A5 | wrap-up success means goal success | NOT FALSIFIED — `goal_unverified` |
| A6 | a fatal error still yields a successful turn | NOT FALSIFIED — `False` |
| A6b | a **post-terminal** error retroactively fails a turn | NOT FALSIFIED — `True` (correctly not fatal) |
| A7 | repo-complete is read as a success verdict | NOT FALSIFIED — `was=True`, `goal_failed` |
| R1 | replay differs from live | NOT FALSIFIED |
| R2 | exhausted+wrap-up is not `goal_unverified` | NOT FALSIFIED |
| R3 | the next turn replays a completed exhausted turn | NOT FALSIFIED |
| X1 | model prose certifies an unverified mutation | NOT FALSIFIED — `goal_failed` |
| X2 | a denied command is treated as success | NOT FALSIFIED — `goal_stagnated` |

**A6's first version was FALSIFIED, and the probe was wrong, not the code.** It placed a fatal error
*after* the terminal marker; the §10 ordering rule correctly ignores post-terminal bytes (a completed
stream cannot be retroactively failed). Corrected to place the error where it can be seen, and **A6b**
now pins the ordering rule itself.

---

## 22. ADR Changes

**Two ADRs, both genuine architectural choices** — the mission's test for whether an ADR is warranted.

| ADR | decision | relationship |
|---|---|---|
| **0043** | Meaningfulness is payload-based for **every** provider event; the classifier owns no vocabulary but the terminal authority | **supersedes ADR-0041 R3 and R7**; ADR-0039 R7 stands |
| **0044** | `terminal_outcome` is the turn-level authority and `turn_succeeded` derives from it; the recovery ladder's state is renamed | defines what ADR-0035 left implicit; **changes no persisted contract** |

Rejected alternatives are recorded in each. ADR-0041's history is **preserved append-only** — its text
is untouched; ADR-0043's index row names the supersession.

`WISP_ARCHITECTURE_DECISIONS.md` is now **44 sections / 44 index rows**; `CONTEXT.md`'s range reads
`ADR-0001 … ADR-0044`. **No existing ADR was modified.**

**No ADR was created for the three audited follow-ups** — A and C were already decided (by ADR-0043 R6
and ADR-0035 respectively), and B *is* ADR-0043.

---

## 23. Remaining Non-Blocking Observations

Three, none affecting closure:

1. **`_terminal_has_payload`'s old field list** included `final_content`, now retained on purpose
   (§15). A future cleanup might name the payload keys in one constant shared in *intent* with
   `canonical_event`'s whitelist — but they should **not** be the same list: a whitelist *projects*,
   a payload check *detects*.
2. **An unknown non-terminal type carrying a payload** is still meaningful (correctly — it carries
   output). It is forwarded, so the consumer sees it; only its `type` is unrecognised. Widening this
   would go beyond the evidence.
3. **`terminal_outcome = "succeeded"` for a turn whose goal failed** is accurate at turn level but the
   vocabulary is close enough to invite collapse. §11 and the tests hold it apart; a future decision
   could rename the *value* without touching the field.

Two pre-existing, unrelated failures remain in the wider suite and are **not** from this phase:

- `test_repl_audit_pindown::test_dead_daemon_with_model_is_unreachable_not_ok` — `provider_catalog.py`
  is uncommitted WIP (mtime 2026-09-19) and imports only stdlib, so it has no connection to any file
  this phase touched.
- `test_13h_forensics::test_h5_empty_index_false_negative` — `ModuleNotFoundError: numpy`, a
  documented-absent dependency (environment).

---

## 24. Final Verdict

```text
EXECUTION SEMANTICS: CLOSED
```

Demonstrated, per the mission's closure definition:

| requirement | evidence |
|---|---|
| **Provider layer** — one canonical representation | ADR-0040 verified: 1 whitelist, 1 mapping site, leaf module |
| **Stream layer** — one terminal authority, one recovery classification | ADR-0043: `TERMINAL_TYPES` is the only vocabulary; meaningfulness is the payload rule |
| **Turn layer** — one definition of completion/success | ADR-0044: one predicate, computed once; the flag is a projection |
| **Verification layer** — one authority | `VerificationFloorGuard` → P3; probes X1/X2 |
| **Goal layer** — one authority | `derive_goal_state`; six states, ordered arbitration |
| **Recovery layer** — one authority | `RecoveryLadder`; `ladder_state` no longer collides |
| **Persistence** — replay reconstructs the same state | probe R1; no migration |
| **Compatibility** — historical records keep their meaning | journal-read replay; no field renamed or reinterpreted |
| **Representation independence** | the differential matrix (§16) |
| **Authority integrity** — no hidden boolean | the audit (§17); the two found are closed |
| **Model authority** — the model cannot certify completion, verification, recovery, escalation or goal success | probes A2/A3/X1/X2; ADR-0042 |

```text
PRODUCTION_CHANGES : ~+125 / −25 across 4 files
TEST_CHANGES       : 1 new file (40 tests) + 12 files updated
DEFAULT_CHANGES    : 0
NEW FAILURES       : 0
FALSIFICATION      : 0 of 22
REPLAY / SECURITY  : UNCHANGED
ADR LOG            : 44 sections / 44 index rows
NEXT               : the three non-blocking observations in §23
```
