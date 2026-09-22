# PHASE P1 REPORT — Journal Turn Transitions

| Field | Value |
|---|---|
| Phase | **P1** |
| Baseline | P0 complete (working tree; `WISP_MIGRATION_STATUS.md`) |
| Status | `COMPLETE (item 3 deferred — see §7)` |
| Files changed | 4 modified, 2 added |
| Tests added | `tests/test_turn_journal_incremental.py` (11), `tests/test_action_idempotency_key.py` (21) |
| Rollback | `WISP_TURN_JOURNAL` |

---

## 1. Executive summary

P0 made the turn body durable — but only at **turn end**, inside `run_turn`'s `finally` block. A
`finally` never runs on SIGKILL, so a killed turn still left only its `user_message` on disk. P1
closes that: each exchange is journaled **the moment it closes**.

The other half of P1 is durable idempotency, which the audit found entirely absent from the live turn
path. Rather than wire up the unused `idempotency` table, P1 makes **the journal itself** the
idempotency record — see §4, which explains why the table was the wrong instrument.

**The plan's "Components Affected" list was wrong, and that is the best news in this phase.**
`core/stateless.py` is listed first, but reconnaissance showed the engine **already yields the
`tool_call` event before dispatching** (`stateless.py:535` yields; `:812` executes). The runtime
consumes that generator, so it sees the call pre-dispatch. **P1 therefore never touches
`stateless.py`** — removing the single largest risk from the phase (ADR-0012).

---

## 2. What was actually wrong

### 2.1 The turn interior was still a black box on SIGKILL

| | Before P1 | After P1 |
|---|---|---|
| Events on disk mid-turn | `user_message` only | every **closed** exchange |
| A killed turn | unrecoverable interior | replayable to the last closed exchange |
| Write site | one batch, `finally` block | per exchange, plus a turn-end remainder |

### 2.2 No durable idempotency anywhere

Verified by grep: `RunStore.idempotent_get/put` and `Scheduler.already_done/memoize/recall` had
**no production caller** — only `test_runs_scheduler.py` and `test_runs_store.py`. The `idempotency`
table was never written to. The only guard was an in-memory per-turn repeat guard, which dies with
the process it would need to survive.

---

## 3. Implementation

Four files modified, two added. **`stateless.py` untouched.**

| File | Change |
|---|---|
| `wisp/core/runtime.py` | extracted `_group_exchanges()` + `_exchange_parts()`; added `_closed_exchange_events()`; incremental flush in the stream loop; turn-end prefix slicing; removed the stale idempotency comment |
| `wisp/core/action_key.py` | **new** — canonical invocation digest |
| `wisp/core/session.py` | `action_key` on both tool events; `_unresolved_actions` map; `unresolved_actions()` |
| `wisp/config.py` | `turn_journal` flag (env `WISP_TURN_JOURNAL`) |
| `tests/test_turn_journal_incremental.py` | **new** — 11 tests |
| `tests/test_action_idempotency_key.py` | **new** — 21 tests |

### 3.1 The refactor that makes it safe

P0's grouping rule (GH#6) lived inline in the `finally` block. P1 extracts it:

```
_group_exchanges(tool_sequence, closed_only=False)   ← THE rule
        ├── _closed_exchange_events(...)   → incremental writer (closed_only=True)
        └── _serialize_tool_exchanges(...) → turn-end writer + transcript
```

Both writers go through one function. The incremental writer commits a **prefix** of the turn-end
event list, so the turn-end writer slices that prefix off — sound only because grouping is
*sequential* (later events never re-open an earlier exchange). That property is pinned directly by
`test_grouping_helper_is_sequential_and_prefix_stable`.

---

## 4. Why the `idempotency` table was not wired up

The obvious implementation was to use the infrastructure that already exists. Three reasons not to:

1. **`idem_put` is first-write-wins** (`ON CONFLICT DO NOTHING`, `infra/store.py:704-714`), so one
   key cannot hold both an *intent* marker and a later *result*.
2. **No TTL, no scope** — keys accumulate forever and collide across sessions.
3. **Exactly-once is unachievable for an arbitrary tool.** If the process dies after the effect but
   before the record, the outcome is genuinely unknown. A table asserting "this ran" would produce
   **false success** — the failure mode RULE 12 forbids.

**Instead:** the journal carries a canonical `action_key` on both sides.

| Journaled | Means | Written |
|---|---|---|
| `TOOL_CALL(action_key=K)` | intent — K dispatched | **before** dispatch |
| `TOOL_RESULT(action_key=K)` | resolution — K's outcome known | after return |

`Session.unresolved_actions()` reports keys with a call and no *real* result. A `synthesized`
placeholder does **not** resolve — it records that the outcome was never learned, which is exactly
the ambiguity being reported.

**The honest position:** recovery *detects* the ambiguous action and must surface it. It must not
silently repeat it. Repeating is how one crash turns one edit into two. ADR-0010.

---

## 5. Verification

### 5.1 New tests — 32, all passing

| Class | Proves |
|---|---|
| `TestIncrementalDurability` | a **probe running inside the turn** (from the provider, upstream of `finally`) sees the closed exchange already on disk, and that partial journal is replayable |
| `TestRollbackFlag` | flag off → probe sees only `user_message`; flag on/off produce the **same final journal** (the flag changes *when*, never *which*) |
| `TestNoDoubleWrite` | each event written exactly once; sequences unique, increasing, **gapless** across both writers; DONE still last |
| `TestBoundaryAgreement` | closed set is a growing prefix; trailing partial is not closed; replay after incremental journaling matches the transcript and is provider-valid |
| `TestKeyStability` (8) | same call → same key; different args/tools → different keys; key order irrelevant; JSON string and dict agree; unserializable args do not raise |
| `TestUnresolvedActions` (11) | resolved actions not reported; unresolved reported once, in dispatch order; synthesized placeholders do **not** resolve; replay rebuilds the set; legacy key-less events ignored |
| `TestKeysSurvivePersistence` (2) | keys and resolution survive the SQLite round trip |

### 5.2 The incrementality test, specifically

Proving "durable before the turn ends" needs a probe that runs *before* the `finally` block. The
provider is the right place — it runs inside `core.turn`, strictly upstream. `_ProbingProvider`
records what the database holds on its second round. With the flag on it sees
`[user_message, assistant_message, tool_result]`; with the flag off, `[user_message]`. That contrast
is what makes the test meaningful rather than merely green.

### 5.3 Regression

| Run | Failures + errors |
|---|---|
| HEAD baseline | 131 |
| P0 final | 128 |
| **P1 final** | **128** |

`diff -q` on the two failure sets: **identical**. P1 introduced **0 new failures** and fixed none
(additive, as designed).

---

## 6. Honest limits

- **No SIGKILL test.** The probe proves events are durable *mid-turn*; it does not prove behaviour
  under an actual `SIGKILL`. `run_turn`'s generator `finally` still runs on `aclose()`, so an
  in-process test cannot simulate a hard kill. `tests/reliability/test_killpoints.py` exists as
  infrastructure for this; wiring it up is deferred (see §7).
- **The tool path is still not exercisable end-to-end** here — `jsonschema` is missing, so every
  call is refused pre-execution (P0 finding F8). The exchange still forms (a refusal reply closes it),
  which is enough to test journaling, but the *successful tool* path is untested in this environment.
- **128 pre-existing failures remain.** P1's claim is exact and narrow: it adds none.
- **`ruff`/`mypy` not installed**, so not run.

---

## 7. Deviations from the plan

### 7.1 Item 3 deferred — the journal is not yet the *primary* record

The plan asked to "replace the whole-session snapshot as the *only* durable record with an
append-only journal; the snapshot becomes a materialized view."

**Not done, deliberately.** `UnifiedStore.load_session` (the blob) is read by five production
consumers — `__main__.py`, `supervisor.py`, `sdk.py`, `acp_session.py`,
`server/routes/sessions.py` — while `SessionRepository.load_session` (replay) is a different
function with a different shape. Switching consumers is a cross-cutting change, and it carries a
real hazard: **pre-P0 sessions have no turn body in the log**, so a naive switch would make old
sessions reconstruct *worse* than the blob does.

The safe shape is **journal-first with blob fallback**, plus a migration check. That deserves its own
phase with its own tests rather than being bundled into P1. Recorded as the first prerequisite of P2.

### 7.2 No killpoint test

The plan's `test_crash_replay_no_duplicate.py` presumes an out-of-process kill. The *detection*
primitive it needs is now implemented and tested (`unresolved_actions()`); the killpoint harness
integration is deferred with §7.1, since both need the journal to be the primary record to be
meaningful.

---

## 8. Completion criteria

| Criterion | Status |
|---|---|
| A killed turn is replayable to its last recorded transition | ✅ mid-turn journal is replayable (probe test); ⚠️ no true SIGKILL test (§6) |
| No duplicate tool effect in the crash-injection test | ⚠️ **detection implemented and tested; killpoint harness deferred** (§7.2) |
| Journal write overhead measured and reported | ⚠️ **not measured** — one extra `to_thread` SQLite write per closed exchange; unmeasured because the tool path cannot execute here (§6) |
| Rollback by one flag | ✅ `WISP_TURN_JOURNAL`; flag-off final journal is byte-identical |
| No regression | ✅ **0 new failures**; failure set identical to P0's |
| No existing test weakened | ✅ none touched |

---

## 9. Next phase

**P2 — Introduce the Proposal Boundary.** Unblocked: durable run state (P0), a replayable incremental
journal (P1), and action-level idempotency (P1) all exist.

Carried into P2 as prerequisites:

1. **Journal-first reconstruction with blob fallback** (§7.1) — must land before anything treats the
   journal as the primary record.
2. **Killpoint integration** (§7.2) — the harness exists; the journal now supports it.
3. **Revisit ADR-0004 for the proposal path.** Once durable state becomes a *correctness*
   precondition, best-effort silent loss stops being acceptable and must become fail-loud. P2's
   proposal boundary is where that threshold is crossed.
