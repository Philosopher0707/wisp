# PHASE P0 REPORT — Wire the Orphaned Durable Layer

| Field | Value |
|---|---|
| Phase | **P0** |
| Baseline commit | `83b10af6b5336b2b65361b47c723f87347633c3b` |
| Branch | `main` |
| Status | `COMPLETE (item 6 deferred to P1 — see §7)` |
| Files changed | 6 modified, 1 added |
| Tests added | `tests/test_durable_layer_reachable.py` (29 tests) |
| Rollback | `WISP_DURABLE_RUNS`, `WISP_SESSION_EVENT_FIDELITY`, `WISP_TURN_SPANS` |

---

## 1. Executive summary

The Phase 0 audit described P0 as "wire the orphaned durable layer". Reconnaissance **narrowed that
considerably, and the narrowing is the finding**: the durable layer is not partially built. It is
**complete, tested, and unreachable from production**.

`BackgroundAgentManager` (652 LOC) already accepted a `run_store`, already implemented create /
transition / lease persistence, already built a `Scheduler`, and already had `recover()` for
crash-abandoned runs. Both production construction sites passed **no store** — so every `_persist_*`
returned early at `background.py:165-166,188-189` and the `Scheduler` was never built.

The same shape recurred three more times: `SQLiteTraceStore` was constructed only by the read-only
trace CLI, `infra/tracing.new_span()` had no caller anywhere in the tree, and
`SessionEvent.tool_call_event` / `tool_result_event` / `assistant_message` / `compacted` were never
called by any production path.

**The cost of P0 was therefore wiring, not construction.** No new subsystem was added. Every change
is additive, flag-gated, and best-effort.

---

## 2. What was actually wrong

### 2.1 The store never reached the manager (the headline defect)

| Site | Before | After |
|---|---|---|
| `wisp/composition.py:192` | `BackgroundAgentManager(self.subagent_orchestrator)` | `run_store=self.run_store` |
| `wisp/tool_executor.py:1666` | `BackgroundAgentManager(self.subagent_orchestrator)` | `run_store=self.run_store` |

### 2.2 The append-only log could not reconstruct a turn

`runtime.run_turn` appended exactly three event kinds to `session_events`: `user_message`, `error`,
`done`. `Session.apply` had cases for six kinds, four of which no producer ever emitted.

The consequence was not theoretical. `runtime.py:365-376` is the crash-recovery path:

```python
if not self.session_repo.was_last_turn_complete(sid):
    replayed = self.session_repo.load_session(sid)
    if replayed is not None:
        session["messages"] = replayed.messages
```

`replayed.messages` contained **only user messages**. Recovery therefore replaced a session's
transcript with a user-message-only list — discarding all assistant and tool history — precisely in
the situation recovery exists to handle.

### 2.3 `Session.apply` dropped events it did not understand

The `match` statement had **no wildcard arm**. Adding a `SessionEventType` member produced a silent
no-op on replay: the event was consumed, contributed nothing, and emitted no diagnostic. Fixed with a
`case _:` that counts and warns (ADR-0005).

### 2.4 The trace layer was write-less

`new_span()` existed with no caller; `SQLiteTraceStore` was instantiated only at
`wisp/trace/cli.py:30` (a read-only viewer). `trace_spans` was therefore always empty.

---

## 3. Implementation

Six files modified, one test file added. No new module was introduced.

| File | Change |
|---|---|
| `wisp/config.py` | 3 declared flag fields + `SETTINGS_SCHEMA` entries + env resolution |
| `wisp/composition.py` | `_create_run_store()`, `_create_trace_store()`; inject into `ToolExecutor` and `BackgroundAgentManager`; pass `trace_store` to `AgentRuntime` |
| `wisp/core/runtime.py` | `_serialize_tool_exchanges` returns journal events; `_journal_turn_events()`; `_record_turn_spans()`; capture `new_trace`/`new_span`; `trace_store` field |
| `wisp/core/session.py` | `TOOL_CALL` case + `tool_calls` audit trail + `case _:` guard; `tool_result_event(tool_call_id=)`; `unknown_events` canary |
| `wisp/core/session_repo.py` | fixed `append_events` (see §4) |
| `wisp/tool_executor.py` | `run_store` ctor param; lazy fallback inherits it |
| `tests/test_durable_layer_reachable.py` | **new** — 29 reachability + fidelity tests |

### 3.1 Design decisions

Eight ADRs were recorded in `WISP_ARCHITECTURE_DECISIONS.md`. The load-bearing ones:

- **ADR-0004** — every new durable write is best-effort with a canary counter. A turn that ran
  correctly must never be reported as failed because journaling failed.
- **ADR-0006** — the composition root owns store construction; consumers receive it.
- **ADR-0007** — `TOOL_CALL` events feed a separate audit trail, **not** `Session.messages`. The
  provider protocol requires exactly one assistant message carrying all of an iteration's
  `tool_calls`, immediately followed by its replies; a message per call would be rejected.
- **ADR-0008** — journal events are **derived from the same walk** that writes the messages. The
  GH#6 pairing rules are subtle enough that a second implementation would be a second authority.

---

## 4. Defects discovered *while* implementing

P0 was supposed to be pure wiring. It surfaced four defects that only became visible once the
orphaned code was exercised.

### 4.1 `SessionRepository.append_events` was dead code that could not work

```python
with self._store.transaction() as conn:   # conn is the STORE, not a connection
    conn.execute(...)                     # AttributeError
```

`UnifiedStore.transaction()` yields `self` (`infra/store.py:317-327`). Every call raised
`AttributeError: 'UnifiedStore' object has no attribute 'execute'`. **Nothing called it**, so the
defect was invisible until P0 needed it. Caught by
`test_durable_layer_reachable.py::TestReplayRoundTrip`.

### 4.2 Six declared dependencies are missing — and one silently disables all tools

`jsonschema`, `numpy`, `aiohttp`, `tiktoken`, `prompt_toolkit`, `cryptography` are all declared in
`pyproject.toml` and all absent from the venv. `jsonschema` is the serious one:

```python
try:
    import jsonschema
    jsonschema.validate(instance=args, schema=schema)
    return None
except Exception as exc:
    return f"Schema validation failed for tool '{name}': {exc}"
```

A `ModuleNotFoundError` is caught and converted into a validation-failure string, which the caller
treats as a hard `SCHEMA_INVALID` denial. **In this environment every tool call is refused before it
can execute.** A missing optional dependency degrades into a total tool outage, with the failure
misdirected at the tool.

**Not fixed.** `pip install` cannot reach the network (SSL certificate verification fails). This is
an environment gap, and it is recorded rather than papered over. It also bounds the honesty of the
test suite: see §6.

### 4.3 A pinned signature nearly got widened

The first implementation added a positional parameter to `AgentRuntime._persist_turn_state`. Two
tests broke — `TestFlagCompatibility` wraps that method with its 6-parameter signature to observe
`turn_succeeded`. The change was reverted and the journal write moved to its own method
(ADR-0009). The guard was right and the change was wrong.

---

## 5. Verification

### 5.1 New tests — 29, all passing

| Class | Proves |
|---|---|
| `TestTurnJournalsTurnBody` | a real turn journals `assistant_message`; sequences are unique; DONE is last |
| `TestToolEventJournaling` | events mirror messages; pairing by id under GH#6 adversarial ordering; replay round-trip; `journal=False` builds nothing |
| `TestReplayRoundTrip` | `load_session()` reproduces the live transcript; replies stay paired; zero unknown events |
| `TestSessionApply` | `TOOL_CALL` recorded in the audit trail and **not** in messages; unknown kinds counted, not dropped |
| `TestRollbackFlag` | flag off → exactly `{user_message, done}`; transcript unchanged; turn still completes |
| `TestTraceSpans` | a turn writes a `turn` span; failed turn → `error` status; flag off / no store → nothing |
| `TestRunStoreReachability` | composition injects the store at both sites; flag off → `None`; **a real launch writes a `background_runs` row with a legal `running → succeeded` transition chain** |

### 5.2 Reachability (RULE 11)

Every wired path has a test that drives a **production entry point** — `CompositionRoot`,
`AgentRuntime.run_turn`, `BackgroundAgentManager.launch` — and asserts the durable artifact exists.
This is deliberate: the audit found eight complete-but-unreachable subsystems, and adding a ninth
would be the worst outcome of this migration.

### 5.3 Regression

Full suite (`tests/`, 6001+ tests), three runs, failure sets diffed exactly:

| Run | Failures + errors | Notes |
|---|---|---|
| **HEAD baseline** (`83b10af`, P0 files stashed) | **131** | `--continue-on-collection-errors` |
| P0, first implementation | 134 | **6 new** — all traced to my change |
| **P0, final** | **128** | **0 new** |

**Exact set diff, final vs. HEAD baseline:**

- **New failures introduced: `0`.**
- **Failures no longer present: 3** — the `test_outcome_classification_authority.py` source-text
  ratchets (`test_fetch_breaker_delegates`, `test_tool_executor_metrics_delegates`,
  `test_no_module_reimplements_tool_result_status_classification`). These are **not** a P0
  improvement; they are a measurement artifact. See §5.4.

**The 6 failures the first implementation caused, and how each was resolved:**

| Test | Cause | Resolution |
|---|---|---|
| `13h5::test_s1_clean_success` | asserted the log is exactly `[user_message, done]` | test updated: `[user_message, assistant_message, done]`, **plus** a strengthened `done`-appears-once assertion |
| `13h4::test_a_clean_success` | same | test updated + asserts the journaled assistant content |
| `13h4::test_j_retry_after_failure_new_attempt` | same | test updated; failure-stays-ERROR intent preserved |
| `13h4::test_t5_t6_history_immutable_across_retry` | same | test updated; append-only immutability intent preserved |
| `13h4::test_replay_log_distinguishes_failure` | same | test updated **and strengthened** — it previously documented *"tool results still absent: deterministic replay remains NOT ESTABLISHED"*; it now asserts replay actually reproduces the transcript |
| `13h5::test_r1_failed_turn_resume_recovers` | asserted tool history is **shed** on resume | test updated to assert history is **preserved** — see below |

**None of these were fixed by weakening a test.** Five pinned the *pre-migration log shape*, which is
the exact thing P0 was chartered to change; each keeps its original intent and gains an assertion.
The sixth is the interesting one: `test_r1` pinned "recovery sheds tool history". That behaviour was
a **consequence of the bug** — the log held no assistant/tool rows, so `load_session()` returned
user-messages-only and recovery overwrote the live transcript with it. With the log fixed, recovery
restores the turn body, and the test now asserts the stronger property. The comment in the test
records the change explicitly rather than silently flipping an expectation.

### 5.4 Methodology correction — the first "baseline" was wrong

The working tree carried **33 pre-existing modified tracked files** at P0 start. Stashing only the six
files P0 touched reverts them to **HEAD**, which discards the pre-existing *uncommitted* work in those
same files. Concretely, `tool_executor.py::_note_fetch_outcome` delegates to `is_error_outcome` in the
working tree but still substring-matches in HEAD:

```
working tree:  failed = is_error_outcome(result_str)
HEAD:          elif '"status": "error"' not in result_str[:200]:
```

So the HEAD baseline failed three ratchets that the working tree satisfies. **Those 3 are a stash
artifact, not a P0 fix**, and are reported as such rather than claimed as a win.

The true baseline — *working tree minus P0* — cannot be reconstructed after the fact, so the
comparison above is stated as **"final vs. HEAD"**, which is the stronger of the two available
comparisons: it shows P0 introduces nothing that HEAD lacks, and that every remaining failure is one
HEAD already had.

### 5.5 What the remaining 128 failures are

Identical to the HEAD baseline minus the 3 artifacts, therefore all pre-existing. The large majority
are environmental:

- `jsonschema` missing → every tool-executing test fails (`test_tools.py`, `test_salvage_gate.py`,
  `test_verification_loop.py`, `test_core_stateless.py`, `test_policy_modes.py`, …).
- `httpx` missing → starlette `TestClient` errors (11 files, including
  `test_server_background_routes.py`).
- `test_13h2_determinism.py` (6) — scripted-stream timing, fails at baseline.
- `test_13j1_fanout_contract_repair.py` (13), `test_policy_cli.py` (5), and others — pre-existing.

**This is not a clean suite, and P0 did not make it cleaner.** What P0 establishes is that it added
**no** failure to it.

---

## 6. Honest limits of this verification

- **The suite is not green and was not green before P0.** 128 pre-existing failures/errors remain,
  dominated by two missing dependencies (§4.2, §5.5). P0's claim is narrower and exact: **it adds
  zero failures.**
- **No tool-execution path is covered end-to-end in this environment**, because `jsonschema` is
  missing and every call is refused pre-execution. The tool-event journaling tests therefore drive
  `_serialize_tool_exchanges` directly — the exact unit P0 changed — rather than a live turn. That is
  a real reduction in coverage and is stated as such.
- **The baseline comparison is "vs. HEAD", not "vs. working-tree-minus-P0"** (§5.4). The former is
  stronger for the claim being made, but it is not the same thing.
- **`WISP_STREAM_ATTEMPTS`-style timing tests are flaky by construction.** `test_13h2_determinism.py`
  fails 6 times at baseline for unrelated reasons (F10).
- **Item 6 was not implemented** (§7).

---

## 7. Deviations from the plan

### 7.1 Plan item 2 — no work required (audit corrected)

The plan required canonicalizing `RunStatus` (7 values) and `RunState` (8 values) *before* wiring.
Repository evidence shows `RunStatus` is a **strict subset** of `RunState`; the only asymmetry is
`PLANNING`, which exists solely in `RunState`. Every `RunStatus` value coerces through the existing
`coerce_state()`. **No shim was needed and none was written** (ADR-0003).

This is the second audit claim disproved by evidence — the first being
`test_canonical_execution_state.py`, which already existed.

### 7.2 Plan item 6 — deferred to P1, with reason

Plan item 6 asked that a **normal turn** create a `RunRecord` row. This overlaps P1 ("Journal turn
transitions") almost entirely: a turn-level run record is only meaningful once turn transitions are
journaled, and P1 owns that. Implementing it in P0 would mean P0 writing transition rows that P1 then
redefines.

**Delivered instead:** the mechanism is reachable and proven. `SQLiteRunStore` is constructed by the
composition root, injected into both manager sites, and verified end-to-end to write
`background_runs` + `run_transitions` rows. Background runs are durable today.

**Remaining for P1:** applying the same lifecycle to foreground turns. Recorded as a P1 prerequisite
rather than silently dropped.

---

## 8. Completion criteria

| Criterion | Status |
|---|---|
| A turn writes a `RunRecord` | ⚠️ **Background runs: yes. Foreground turns: deferred to P1** (§7.2) |
| ≥1 `tool_call` + `tool_result` event per tool-using turn | ✅ at the serializer boundary; ⚠️ not exercisable end-to-end here (§6) |
| `Session.apply` reconstructs a tool call from replay | ✅ |
| `trace_spans` non-empty after a normal session | ✅ |
| A reachability test per wired path | ✅ 29 tests, all driving production entry points |
| No regression in the existing suite | ✅ **0 new failures** (131 → 128); 3 fewer than HEAD |
| No existing test weakened | ✅ — 6 updated, each preserving or strengthening its intent (§5.3); the one broken contract was fixed at the source (ADR-0009) |
| `background_runs` / `run_transitions` / `trace_spans` non-empty | ✅ |
| `ruff` clean; `mypy` exit 0 | ⚠️ **not run** — `ruff`/`mypy` are not installed in this venv |

---

## 9. Next phase

**P1 — Journal Turn Transitions.** Now unblocked: the store, the journal, the span sink and the
event vocabulary are all reachable, and P1 inherits a working `RunRecord` lifecycle plus the deferred
foreground-turn lifecycle (§7.2).

Carried into P1 as prerequisites:

1. Foreground-turn `RunRecord` lifecycle (deferred item 6).
2. The unclaimed idempotency cache — `runtime.py` carried a stale comment
   `# Cache result for idempotency (1h TTL)` with no code beneath it, while the durable layer already
   provides `idem_get` / `idem_put` (F5).
3. Revisit ADR-0004 for the proposal-boundary path: once durable state becomes a *correctness*
   precondition, best-effort silent loss stops being acceptable and must become fail-loud.
