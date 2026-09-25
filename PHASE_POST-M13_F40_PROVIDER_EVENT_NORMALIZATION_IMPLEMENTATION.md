# PHASE POST-M13 — ADR-0039 F40 PROVIDER EVENT NORMALIZATION IMPLEMENTATION

**Status:** COMPLETE · **ADR:** 0039 · **Phase:** PM-21 · **Date:** 2026-09-25 · **HEAD:** `7c15626`

---

## 1. Mission Result

```text
PASS
```

F40-1, F40-2, F40-3, F40-4 and F42 are all closed, and the structural invariants — one
canonicalization authority, one terminal authority, no raw-event interpretation by any consumer —
are proven by tests that would fail if a future change bypassed the boundary.

---

## 2. Provenance

| | |
|---|---|
| HEAD | `7c15626` "docs(m11): record the M11 phase; findings F29-F31; repair the commit table" |
| branch | `main` |
| commits this phase | **0** |
| staged | **0** |
| modified tracked | 44 (unchanged count) |
| untracked | 72 |

`wisp/core/stateless.py` was already modified before this phase (earlier uncommitted work). Every
other file this phase touched was clean vs HEAD, with mtimes from 2026-08-24 … 2026-09-23 —
**pre-existing WIP, none of it mine.** Nothing was reset, stashed, checked out, cleaned or staged.

### 2.1 Diff attribution (this phase vs pre-existing)

`stateless.py`'s diff was split hunk-by-hunk, because it carries both:

| File | This phase | Pre-existing |
|---|---|---|
| `wisp/core/stateless.py` | **+67 / −48** | +154 / −4 (earlier phases) |
| `wisp/core/events.py` | +72 / −20 | — |
| `wisp/providers/protocol.py` | +29 / −6 | — |
| `wisp/core/provider_stream.py` | +20 / −0 | — |
| `wisp/core/compaction.py` | +11 / −4 | — |
| `wisp/graph/planner.py` | +10 / −3 | — |
| **production total** | **+209 / −81 (6 files)** | |
| **tests** | 1 new file (493 lines, 24 tests) + 2 updated in place | |

---

## 3. Files Changed

**Production (6)**

```text
wisp/core/events.py          the single canonicalization implementation + the whitelist
wisp/core/stateless.py       the normalization-only boundary; guard rewiring; wrap-up
wisp/core/provider_stream.py passthrough_if_canonical; TERMINAL_TYPES stays the one authority
wisp/core/compaction.py      canonicalize before interpreting (F40-3)
wisp/graph/planner.py        canonicalize instead of isinstance-filtering (F40-4)
wisp/providers/protocol.py   declare that a provider MAY emit either form (R1)
```

**Tests**

```text
tests/reliability/test_post_m13_f40_normalization_boundary.py   NEW — 24 tests
tests/reliability/test_13h2_determinism.py                      UPDATED — test_d6 asserted the defect
tests/reliability/test_13h5_success_derivation.py               UPDATED — the assertion was F40-dependent
```

**Documentation** — this report; ledger/memory records.

---

## 4. Architecture Implemented

```text
                 PROVIDERS
                /         \
       typed events       dict events
              \             /
               \           /
                ▼         ▼
    WispAgentCore._normalized_provider_stream()      <- normalization-only
                │  one pass per event (R2)
                │  forwards terminal markers (R6)
                │  NO retry, NO stall recovery (R5)
                ▼
          CANONICAL EVENTS  (flat dict, 16-field whitelist)
                │
     ┌──────────┼───────────┬────────────┐
     ▼          ▼           ▼            ▼
  main loop  wrap-up   compaction    planner
     │
     ▼
  _guarded_provider_stream()      <- recovery-only
     stall detection · retry · terminal bookkeeping
```

The distinction the ADR required is now structural, not conventional:

```text
normalization  ≠  stall recovery
```

- `_normalized_provider_stream` is the **only** caller of `_stream_events_async` (asserted).
- `_guarded_provider_stream` obtains its events from it and keeps its recovery untouched.
- The guard passes `passthrough_if_canonical` so an already-canonical event is **not**
  canonicalized twice, while the guard stays **total** for any non-dict input.

---

## 5. F40 Closure

| | Before | After |
|---|---|---|
| **F40-1** `stateless.py:1081` `.get()` on a typed event | `AttributeError: 'TokenBatch' object has no attribute 'get'`, swallowed → the summary discarded | canonical events; **no exception** (probe F1: `swallowed exceptions: none`) |
| **F40-2** terminal vocabulary `{done}` only | a normalized `StreamComplete` (`"complete"`) never matched, so `wrapped_up` stayed `False` and a spurious error was emitted | reads `TERMINAL_TYPES`; **no spurious error** (probe F3: `errors: none`) |
| **F40-3** `compaction.py:146` raw `.get()` | `AttributeError` → `"[ERROR: …]"` → `_llm_summarize` returned `None` → silent truncation | `REAL-SUMMARY` delivered (probe F4) |
| **F40-4** `planner.py:121` `isinstance(c, dict)` | typed events silently dropped → empty text → `INVALID_PLANNER_OUTPUT` | `{'steps': []}` parsed (probe F5) |

**Live on the real daemon** (`nemotron-3-ultra:cloud`, `max_iterations=1`, a prompt that forces a
tool round so the wrap-up actually runs):

```text
types   : ['thinking','checkpoint','thinking','tool_call','content','tool_result',
           'system','content','content','done']
content : '\nThe file `seed.txt` contains a single line:\n\n**the seed file says hello**'
budget error : False
```

Before ADR-0039 that content was `[]` and the turn ended with `Max iterations reached`. The
`tool_call → tool_result` pair confirms the **main loop is unchanged**.

---

## 6. F42 Closure

Measured, not asserted by inspection:

```text
canonical_event(ToolCallBatch(phase="tool_calls", calls=[{...}]))
  -> {'type': 'tool_calls', 'calls': [{...}]}          calls PRESERVED

canonical_event(StreamComplete(..., done_reason="stop"))
  -> {'type': 'complete', 'done_reason': 'stop'}       done_reason PRESERVED
```

**One authority, structurally.** The whitelist now exists in exactly one place —
`wisp/core/events.py::CANONICAL_EVENT_FIELDS` + `canonical_event` — and **both** entry points
delegate:

```text
WispAgentCore._normalize_event  ->  canonical_event(event)
events.normalize_event          ->  canonical_event(event)   (for provider objects)
```

`events.normalize_event`'s own 14-field whitelist is **deleted**. The second entry point remains for
`AgentEvent`/dict inputs, but it can no longer diverge because it owns no list. Asserted by AST
(`safe_fields` absent from `normalize_event`; exactly one `CANONICAL_EVENT_FIELDS: frozenset[str] =`).

> **Note on the deviation.** ADR-0039 R2 says `events.normalize_event` "SHALL remain a canonicalizer
> of `AgentEvent`/dict inputs only". It now also *delegates* provider objects rather than rejecting
> them. Deleting the branch outright would have degraded any such caller to `type="unknown"` —
> silently losing data, which is the F42 defect class. Delegation removes the divergence without
> that regression. See §14.

---

## 7. Terminal Vocabulary

`provider_stream.TERMINAL_TYPES` is the single authority (`{done, complete, stream_complete}`).
The wrap-up reads it; the local `etype == "done"` is gone (asserted: `'etype == "done"' not in
stateless.py`).

| Spelling | Meaning | Status |
|---|---|---|
| `done` | provider declared the stream finished | canonical (synonym) |
| `complete` | same — the **only** terminal on the typed Ollama path | canonical (synonym) |
| `stream_complete` | no producer anywhere | accepted legacy alias |
| `checkpoint` / `usage` / `stream_stats` | bookkeeping | **not** terminal |

Terminal events are **forwarded** by the normalization boundary (probe F10) and **consumed** by the
guard for its own `saw_terminal` bookkeeping (test `test_guard_still_consumes_the_terminal_marker`).
The two concepts the ADR keeps separate — *provider terminal event* vs *the stream ended* — remain
separate.

---

## 8. Provider Matrix

| Provider representation | Expected | Observed |
|---|---|---|
| OpenAI dict | unchanged | `{'type':'content','text':'a'}` → unchanged |
| Ollama typed | canonicalized | `TokenBatch` → `{'type':'content','text':'a'}` |
| Mock typed | canonicalized | `ToolCallBatch` → `{'type':'tool_calls','calls':[…]}`, calls intact |
| Ollama fallback dict | unchanged | `{'type':'done','done_reason':'stop'}` → unchanged |
| Null provider dict | unchanged | `{'type':'error','text':'no provider'}` → unchanged |

No provider was modified. `OllamaClient` and `MockProvider` still emit their typed dataclasses;
`OpenAIProvider` still emits dicts.

---

## 9. Replay

**UNCHANGED.** Typed event classes referenced in `runtime.py` (the persistence path): **NONE**.
The canonical whitelist is still **16** fields and adds **no** provider-internal metadata. No
migration, no journal rewrite, no schema change, no replay adapter.

---

## 10. Security

Canonicalization is a pure whitelist projection. Verified adversarially with a hostile provider
object whose `.get()` and `__call__` raise:

```text
hostile object  ->  {'type': 'unknown', 'text': 'payload'}
.get() / __call__ never invoked: True
```

It reads only whitelisted attributes; it never executes provider data, invokes a tool, or touches
authorization, approval, `ToolExecutor` or security policy. Tool arguments remain data validated by
the existing gates. Totality (R3) verified on `None`, a list, and an opaque object — all return
`{'type': 'unknown'}`, none raise.

---

## 11. Test Results

| Suite | Before | After | Classification |
|---|---|---|---|
| new `test_post_m13_f40_normalization_boundary.py` | — | **24 passed** | new |
| `tests/reliability/` (whole directory) | 378 passed | **402 passed** | +24 new, **0 regressions** |
| canonical migration set (29 files) | 848 passed / 1 failed | **848 passed / 1 failed** | identical — the pre-existing **F38** |
| targeted provider/core suites | 187 passed | **187 passed** | unchanged |

```text
NEW FAILURES:        0
RESOLVED FAILURES:   2 tests rewritten (they asserted the defect — see §11.1)
PRE-EXISTING:        1 (F38, unchanged)
ENVIRONMENT:         0
```

### 11.1 The two tests that had to be rewritten

Both **encoded F40 as the contract**, which is why a green suite never saw it:

- `test_13h2_determinism.py::test_d6_wrapup_failure_error_plus_done_once_each` — its comment read
  *"MockProvider yields objects; the wrap-up path consumes RAW events … so it cannot see them:
  honest error"*, and it asserted the error. Rewritten to assert the summary is delivered and no
  error is emitted; the class's terminal-uniqueness property is preserved.
- `test_13h5_success_derivation.py::test_exhaustion_not_success` — asserted a `Max iterations
  reached` error **and** `was_last_turn_complete == False`. Rewritten (§15, **F44**).

---

## 12. Falsification Results

**0 of 19 falsified.** Each probe attempted to break the implementation; every attempt failed.

| | Attempted falsification | Result |
|---|---|---|
| F1 | typed `StreamComplete` still raises in the wrap-up | NOT FALSIFIED — swallowed exceptions: none |
| F2 | typed `StreamComplete` still missed as terminal | NOT FALSIFIED — summary delivered |
| F3 | wrap-up still emits the false budget error | NOT FALSIFIED — errors: none |
| F4 | compaction still sees raw typed events | NOT FALSIFIED — `REAL` summary |
| F5 | planner still filters typed events | NOT FALSIFIED — `{'steps': []}` parsed |
| F6 | a second provider-object canonicalizer exists | NOT FALSIFIED — 1 whitelist, 0 local |
| F6b | the two entry points disagree | NOT FALSIFIED — both return `calls` |
| F7 | `calls` disappears | NOT FALSIFIED — preserved |
| F8 | `done_reason` disappears | NOT FALSIFIED — preserved |
| F9 | the boundary retries | NOT FALSIFIED — 1 provider call |
| F10 | the boundary consumes terminal markers | NOT FALSIFIED — yields `complete` |
| F11 | the guard loses its retry | NOT FALSIFIED — retried, recovered |
| F11b | the guard's input changed | NOT FALSIFIED — byte-identical for every typed class |
| F12 | OpenAI dict behaviour changes | NOT FALSIFIED — unchanged, and it is a copy |
| F12b | the guarded dict path changes | NOT FALSIFIED — unchanged |
| F13 | persistence references typed classes | NOT FALSIFIED — none |
| F13b | the single implementation is missing | NOT FALSIFIED — present |
| F14 | a consumer re-spells the terminal vocabulary | NOT FALSIFIED — none |
| F14b | the wrap-up does not read `TERMINAL_TYPES` | NOT FALSIFIED — it does |

---

## 13. Diff Summary

```text
production   +209 / −81   across 6 files   (attribution in §2.1)
tests        1 new file (493 lines, 24 tests) + 2 tests updated
documentation  this report + records
commits       0        staged 0
```

No provider, no persistence, no configuration, no default, no dependency, no feature flag.

---

## 14. Deviations

**One, declared.**

ADR-0039 R2 says `events.normalize_event` "SHALL remain a canonicalizer of `AgentEvent`/dict inputs
only". It now **delegates** provider objects to the single implementation instead of owning a second
whitelist — i.e. it still accepts them, but cannot diverge.

*Why:* deleting the branch would make `normalize_event(provider_obj)` return `type="unknown"` for any
caller, silently discarding data — the exact defect class F42 is. Delegation satisfies R2's operative
requirement (*"No second implementation SHALL canonicalize provider objects"*) while removing the
divergence. R2's *literal* "inputs only" wording is not satisfied verbatim; the *intent* is, more
strictly.

**No other deviation.** Every other rule was implemented as written.

---

## 15. Remaining Findings

Two were found during this phase. **Neither is absorbed; neither is fixed here.**

### F43 — the empty-stream detection is representation-dependent (pre-existing)

`_BOOKKEEPING_TYPES` re-spells two *terminal* spellings (`done`, `stream_complete`) and omits
`complete`. Because the guard tests `ntype not in bookkeeping` **before** its terminal check, a bare
`StreamComplete` counts as a *meaningful* attempt while a bare `{"type":"done"}` does not. Measured:

```text
bare typed StreamComplete  ->  provider calls = 1, no error
bare dict {"type":"done"}  ->  provider calls = 3, then an explicit error
```

So an empty Ollama response is accepted as a successful empty reply while the identical empty OpenAI
response is retried and surfaced. **Pre-existing and unaffected by ADR-0039** — the guard's input is
byte-identical before and after the rewiring (probe F11b), and `_BOOKKEEPING_TYPES` is untouched.
Not fixed here because it changes guard **recovery** behaviour, which ADR-0039 §5 fences off.
Pinned by `test_F43_bookkeeping_duplication_is_pinned_not_silently_accepted`.

### F44 — an exhausted turn whose wrap-up succeeds is recorded complete

`was_last_turn_complete` is "the last persisted event is DONE". With the wrap-up working, an
iteration-budget-exhausted turn now ends `… content, done` → `True`, so `runtime.py:588` skips the
incomplete-turn replay on the next turn.

**This is not introduced by ADR-0039.** Measured: the **dict** provider path — untouched by this
phase — already yielded `True` and no error for the identical script. The typed path's `False` was
purely an F40 artifact (the spurious error was the last persisted event). So the old property held
**only for typed providers**, and ADR-0039 removed that representation dependence — which is its
purpose.

Whether an exhausted-but-summarised turn *should* count as complete is a **completion-authority**
question (ADR-0035 / the recovery ladder), not a normalization one. ADR-0039 explicitly does not own
it. Recorded and pinned in `test_exhaustion_delivers_wrapup_and_flag_pinned_F44`.

---

## 16. Final Status

```text
=== STATUS: COMPLETE ===

F40-1 / F40-2 / F40-3 / F40-4 :  CLOSED
F42                            :  CLOSED (one canonicalization authority, structurally)
ADR-0039                       :  SATISFIED
NORMALIZATION_OWNER            :  wisp.core.events.canonical_event
                                  (WispAgentCore._normalize_event delegates to it)
TERMINAL_AUTHORITY             :  provider_stream.TERMINAL_TYPES
REPLAY                         :  UNCHANGED
SECURITY                       :  UNCHANGED (pure whitelist projection)
PRODUCTION_CHANGES             :  +209 / −81 across 6 files
TEST_CHANGES                   :  1 new file (24 tests) + 2 tests rewritten
DEFAULT_CHANGES                :  0
FALSIFICATION                  :  0 of 19
NEW FAILURES                   :  0
NEW FINDINGS                   :  F43 (pre-existing, pinned), F44 (semantic, pinned)
DEVIATIONS                     :  1, declared (§14)
NEXT                           :  decide F43 (guard recovery) and F44 (completion authority)
```

---

## 17. Evidence

| Artefact | Contents |
|---|---|
| `tests/reliability/test_post_m13_f40_normalization_boundary.py` | 24 tests: boundary, guard, F40 closure, F42, consumer invariants |
| `.workbuddy-ai/memory/post-m13-f40-impl/falsification_probes.py` | F1–F14 (19 probes), 0 falsified |
| `.workbuddy-ai/memory/post-m13-f40-impl/live_repro_and_matrix.py` | real-daemon repro, provider matrix, replay/security invariants |
