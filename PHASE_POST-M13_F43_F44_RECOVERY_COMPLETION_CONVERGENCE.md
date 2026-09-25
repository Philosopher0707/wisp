# PHASE POST-M13 — F43/F44 RECOVERY & COMPLETION AUTHORITY CONVERGENCE

**Status:** COMPLETE · **ADRs:** 0041, 0042 · **Phase:** PM-23 · **Date:** 2026-09-25 · **HEAD:** `7c15626`

---

## 1. Mission Result

```text
PASS
```

- **F43 RESOLVED.** Recovery classification is now semantic and terminal-first; the last re-spelling of
  the terminal vocabulary is **eliminated**, not derived around. Measured: a bare typed
  `StreamComplete` and a bare `{"type":"done"}` now take **identical** paths (3 calls → error); before,
  the typed one was a *silent empty success* in 1 call.
- **F44 SETTLED ARCHITECTURALLY.** The five authorities were measured across ten scenarios and are
  **already distinct and correctly related**. The `False`→`True` change was a **correct behaviour being
  restored**, not a new authority. No code change; the relation is now stated (ADR-0042) and held apart
  by tests.
- **Falsification: 0 of 14 probes falsified.** Canonical set **848 passed / 1 failed** — the
  pre-existing F38, **identical to the baseline**. **0 new failures.**

---

## 2. Provenance

| | |
|---|---|
| HEAD | `7c15626` "docs(m11): record the M11 phase; findings F29-F31; repair the commit table" |
| branch | `main` |
| commits this phase | **0** |
| staged | **0** |
| modified tracked | 51 (unchanged from before this phase) |
| untracked | 75 |

`wisp/core/stateless.py` and `wisp/core/runtime.py` carry **pre-existing uncommitted WIP** from earlier
phases; nothing was reset, stashed, cleaned, checked out or staged. `wisp/core/provider_stream.py` was
clean before this phase.

---

## 3. F43 Root Cause

The exact causal chain, in the guard's own ordering:

```python
if ntype not in bookkeeping:      # (1) vocabulary lookup decides meaningfulness
    got_meaningful = True
if ntype in TERMINAL_TYPES:       # (2) terminal detection
    saw_terminal = True
    if _terminal_has_payload(normalized):
        got_meaningful = True
    break
```

`WispAgentCore._BOOKKEEPING_TYPES` was
`{"done", "stream_complete", "checkpoint", "usage", "stream_stats"}` — it **re-spelled two terminal
types and omitted the third** (`complete`).

Because step (1) ran first and step (2) could only ever **add** meaningfulness, a bare `done` was
correctly "not meaningful" while a bare `complete` was wrongly "meaningful":

```text
bare typed StreamComplete ("complete")  -> 1 provider call, NO error, empty "success"
bare dict {"type":"done"}               -> 3 provider calls, then an honest error
```

Same semantics, two outcomes — decided entirely by which provider emitted it.

**The repository already knew.** Three tests recorded it as a defect and pinned the symptom; one said
so in as many words:

- `test_13h2_determinism.py::test_empty_object_stream_treated_as_natural_done` —
  *"a **silent empty success. Recorded, not fixed.**"*
- `test_13h5_success_derivation.py::test_natural_empty_success_preserved` — *"H2 bookkeeping behavior …
  H5 does not reclassify provider semantics"*
- `test_13h4_success_semantics.py::test_f2_empty_object_natural_done` — *"# bookkeeping-set mismatch
  (H2)"*

and `test_13h_forensics.py` stated the intended contract: *"terminal markers must be included or a bare
marker counts as meaningful."* **`complete` was simply missing.**

---

## 4. F43 Architecture

| Concern | Owner | Before | After |
|---|---|---|---|
| **Terminal classification** | `provider_stream.TERMINAL_TYPES` | the sole authority, but not consulted first | the sole authority, **consulted first** |
| **Bookkeeping classification** | `provider_stream.NON_PAYLOAD_TYPES` | `WispAgentCore._BOOKKEEPING_TYPES`, re-spelling 2 terminal types and omitting a 3rd | the **non-terminal, payload-less** types only; **disjoint** from the terminal set |
| **Payload / meaningfulness** | `_terminal_has_payload` (terminals) + the non-payload set | a vocabulary list could mark a bare marker meaningful | a terminal's meaningfulness is decided **solely by its payload** |
| **Empty-stream detection** | the guard's `got_meaningful` + `saw_terminal` | representation-dependent | representation-independent |
| **Retry** | the guard's bounded `max_attempts` | unchanged | unchanged |
| **Recovery** | the guard's post-loop branches | unchanged | unchanged |

The terminal entries in the bookkeeping list were a **workaround for classifying bookkeeping before
terminal**. Ordering terminal first **removes the need for them**, so the duplication is *eliminated*
rather than *derived around* — and there is no longer a list that could drift from
`TERMINAL_TYPES`.

---

## 5. F43 Implementation

**2 production files, +49 / −5.**

| File | Change |
|---|---|
| `wisp/core/provider_stream.py` | Added `NON_PAYLOAD_TYPES = frozenset({"checkpoint","usage","stream_stats"})` with the ADR-0041 rationale. Reordered the guard's classification: the `ntype in TERMINAL_TYPES` branch now runs **before** the non-payload check. |
| `wisp/core/stateless.py` | `_BOOKKEEPING_TYPES = NON_PAYLOAD_TYPES` (imported), replacing the literal that re-spelled terminal vocabulary. Comment states the semantic. |

Behaviour, measured before → after:

| stream | before | after |
|---|---|---|
| bare typed `StreamComplete` | **1 call, no error, empty "success"** | **3 calls, error** |
| bare dict `{"type":"done"}` | 3 calls, error | 3 calls, error |
| bare `{"type":"complete"}` | (n/a) | 3 calls, error |
| bare `{"type":"stream_complete"}` | (n/a) | 3 calls, error |
| typed content + terminal | 1 call, content | 1 call, content |
| dict content + done | 1 call, content | 1 call, content |
| bookkeeping-only stream | retried, error | retried, error |
| checkpoint + content + done | 1 call, both forwarded | 1 call, both forwarded |

**The only behaviour change is the one F43 is about**, plus the equivalent spellings it makes symmetric.

---

## 6. F44 Root Cause

F44 was not a regression. The meaning of each state, traced through the call graph:

| State | Definition | Source |
|---|---|---|
| **iteration exhaustion** | the turn loop ran `max_iterations` rounds; it says nothing about whether the *work* succeeded | `stateless.py` loop bound |
| **wrap-up** | one final tool-less round asking the model to summarise. Success = it produced content and a terminal event; failure = it raised | `stateless.py:1070-1098` |
| **provider terminal** | the provider declared the stream finished. Nothing more | `TERMINAL_TYPES` |
| **`was_last_turn_complete`** | **"the last persisted event is a DONE event"** — an *interrupted-turn / replay* signal, **not** a completion verdict | `session_repo.py:243`; consumed at `runtime.py:588` |
| **`turn_succeeded`** | `saw_done and not saw_fatal_error` — turn-level terminal evidence | `runtime.py:938` |
| **`acceptance_verdict`** | P3, fed by `VerificationFloorGuard` — the only input that separates `GOAL_MET` from `GOAL_FAILED` | `verification.py` → `acceptance.py` |
| **`goal_state`** | `derive_goal_state(turn_succeeded, acceptance_verdict, …)` | `goal.py` (ADR-0035) |

**Why the typed path previously read `False`:** the spurious `Max iterations reached` error (F40-1) was
a *fatal* error, so the runtime did not persist `done` and `turn_succeeded` was `False`. Removing the
spurious error (F40) left the genuine terminal event, so the turn now reads as completed — which is
what the **dict** path already did, measured, untouched by F40.

**F44's question, answered:** an exhausted turn whose wrap-up succeeded is a **completed turn with an
unverified goal** — neither a success nor a failure at the goal level. Exhaustion is an *absence of
evidence*, and ADR-0035 maps absence of evidence to `GOAL_UNVERIFIED`, never `GOAL_FAILED`.

---

## 7. Completion Authority Map

Measured across ten scenarios (terminal event / `turn_succeeded` / `was_last_turn_complete` /
acceptance / goal state / outcome):

| scenario | terminal | `turn_succeeded` | `was_last_turn_complete` | acceptance | goal state | outcome |
|---|---|---|---|---|---|---|
| 1 ordinary success | `done` | **True** | True | inconclusive | `goal_unverified` | succeeded |
| 2 provider terminal (typed) | `done` | True | True | inconclusive | `goal_unverified` | succeeded |
| 3 exhaustion, wrap-up **fails** | `done` | **False** | **False** | inconclusive | **`goal_failed`** | failed |
| 4 exhaustion, wrap-up **succeeds** | `done` | **True** | True | inconclusive | **`goal_unverified`** | succeeded |
| 5 the **next** turn | `done` | True | True | inconclusive | `goal_unverified` | succeeded |
| 6 verification **failure** | `done` | **True** | True | **fail** | **`goal_failed`** | succeeded |
| 7 incomplete evidence | `done` | True | True | **fail** | **`goal_failed`** | succeeded |
| 8 provider failure | **error** | False | False | inconclusive | `goal_failed` | failed |
| 9 cancellation | **none** | False | False | inconclusive | `goal_unverified` | **incomplete** |
| 10 escalation (default) | error | False | False | inconclusive | `goal_failed` | failed |

Two rows carry the decision:

- **row 1** — `turn_succeeded == True` with `goal_state == goal_unverified`: **a turn can succeed
  without the goal being met.**
- **row 6** — `turn_succeeded == True` with `goal_state == goal_failed`: **a turn can succeed while the
  goal fails.** The verification verdict decides, not the turn.

```text
OBSERVATION    provider event (typed OR dict)
                    │  canonical_event  (ADR-0040)
                    ▼
CLASSIFICATION canonical type ∈ {content, tool_calls, terminal, bookkeeping, error}
                    │  guard: TERMINAL-FIRST, payload decides  (ADR-0041)
                    ▼
STREAM STATE   saw_terminal · got_meaningful · retry budget → complete | empty | truncated
                    │
                    ▼
TURN STATE     turn_succeeded = saw_done ∧ ¬saw_fatal_error           (runtime.py:938)
                    │
                    ▼
VERIFICATION   acceptance_verdict (P3) — VerificationFloorGuard's floor is its input
                    │
                    ▼
GOAL STATE     derive_goal_state(turn_succeeded, acceptance_verdict, …)   (ADR-0035)
                    │
                    ▼
ROUTING        terminal_outcome · escalation · incomplete-turn replay
```

**Every arrow is one-way.** `provider said done` never becomes `the goal is met`.

---

## 8. ADR Decisions

**Two ADRs were required** — one is a classification-order/authority rule, the other a semantic
definition of a chain ADR-0035 left implicit. Each is independently supersedable, which one combined
ADR would not have been.

### ADR-0041 — Recovery classification is semantic, and terminal detection precedes payload classification

*Decision:* the guard SHALL detect terminal FIRST and SHALL decide a terminal's meaningfulness solely
from its payload; `NON_PAYLOAD_TYPES` SHALL hold the non-terminal, payload-less types and SHALL contain
no terminal spelling. R1–R7.

*Rejected:* (a) **deriving** `BOOKKEEPING_TYPES = TERMINAL_TYPES | NON_PAYLOAD_TYPES` — fixes today
but keeps the hazard, since a future terminal spelling would again need to be remembered; (b)
`_BOOKKEEPING_TYPES = TERMINAL_TYPES` — semantically wrong, `checkpoint`/`usage`/`stream_stats` would
become meaningful; (c) dropping the bookkeeping concept entirely — a `stream_stats`-only stream (the
HTTP-200-with-zero-deltas throttle signature) would be blessed as a response, which
`test_13h_forensics.py` exists to prevent; (d) special-casing `StreamComplete` — a provider-specific
branch is what F43 already was.

### ADR-0042 — The completion chain has five distinct authorities, none derived from a lower one

*Decision:* provider terminal → stream state → turn state → verification → goal state → routing, with a
fixed one-way relation; an exhausted turn that wraps up successfully is a **completed turn with an
unverified goal**. R1–R9. **Changes no behaviour.**

*Rejected:* (a) a new `turn_completed` flag — a second authority over the same journal fact; (b)
exhaustion → `GOAL_FAILED` — exhaustion is an absence of evidence, not evidence of failure; (c) a
successful wrap-up → `GOAL_MET` — that would let the model certify its own work; (d) suppressing the
terminal event so `was_last_turn_complete` stays `False` — fabricating an interrupted turn and forcing
the next turn to replay a completed one; (e) renaming `was_last_turn_complete` — its name is accurate
for what it is (a *turn* signal), and the blast radius is five test files plus the runtime.

**Numbering:** verified before writing — highest was 0039 after PM-22's 0040, collision check for
0041/0042 clean, appended append-only before `## Decision index`. Log integrity now **42 sections / 42
index rows**; `CONTEXT.md` range → `ADR-0001 … ADR-0042`. **No existing ADR was modified.**

---

## 9. Tests

| | Before | After |
|---|---|---|
| `tests/reliability/` + adjacent suites | 691 passed / 1 failed | **691 passed / 1 failed** |
| canonical migration set (29 files) | 848 passed / 1 failed | **848 passed / 1 failed** |

```text
NEW:          1 file — tests/reliability/test_post_m13_f43_f44_authority_convergence.py
              22 test functions (one parametrized ×3) → 24 collected, all pass
MODIFIED:     4 tests that encoded obsolete behaviour (below)
              + 1 comment in tests/test_13h_forensics.py
PRE-EXISTING: 1 — tests/test_13h_forensics.py::test_h5_empty_index_false_negative
ENVIRONMENT:  1 — the same test: ModuleNotFoundError: numpy (a documented-absent dependency)
NEW FAILURES: 0
```

### 9.1 The four tests that had to be rewritten

All four **pinned F43's symptom as the contract** — which is why a green suite never saw it. Each was
rewritten to assert the corrected behaviour while preserving what the test existed for:

| test | was | now |
|---|---|---|
| `test_13h2::test_empty_object_stream_treated_as_natural_done` | `_types == ["done"]`, empty content — *"a silent empty success. Recorded, not fixed."* | renamed `…surfaces_error_without_done`; asserts the error path, and a new sibling asserts typed and dict streams **agree** |
| `test_13h5::test_natural_empty_success_preserved` | *"bare 'complete' marker alone still ends done(natural)"* | renamed `test_natural_empty_not_success`; no `done`, repo incomplete |
| `test_13h4::test_f2_empty_object_natural_done` | `_types == ["done"]  # bookkeeping-set mismatch (H2)` | renamed `test_f2_empty_object_repo_incomplete`; matches its dict twin |
| `test_post_m13_f40::test_F43_bookkeeping_duplication_is_pinned_…` | asserted the *duplication existed* | renamed `…_is_gone`; asserts the vocabularies are **disjoint** |

None was deleted; none was weakened to fit the new output.

### 9.2 The new suite's coverage

**F43** — vocabulary disjointness and single-definition; representation equivalence (parametrized over
`done`/`complete`/content); bare-terminal-is-empty on all four spellings; payload-carrying terminals
still meaningful; content streams unchanged; bookkeeping-only streams still retried; bookkeeping
forwarded not swallowed; a checkpoint does not truncate a stream.

**F44** — all ten scenarios, each asserting `turn_succeeded`, `was_last_turn_complete`, `acceptance`
and `goal_state` **separately**; typed/dict agreement on the exhaustion row; the next turn does not
replay; and three explicit non-collapse assertions (a completed turn can be a failed goal; a successful
turn never forces `GOAL_MET`; a terminal event does not override a fatal error).

---

## 10. Falsification

**0 of 14 probes falsified.**

| probe | attempted falsification | result |
|---|---|---|
| **F43-A** | typed `StreamComplete` vs canonical `complete` take different paths | NOT FALSIFIED — both `(3, ['error'])` |
| **F43-B** | `done` / `complete` / `stream_complete` inconsistent | NOT FALSIFIED — all three identical |
| **F43-C** | a bookkeeping event prevents empty-stream recovery | NOT FALSIFIED — retried, then error |
| **F43-D** | a terminal triggers an extra retry | NOT FALSIFIED — payload-carrying = 1 call |
| **F43-E** | a representation change alters recovery | NOT FALSIFIED — no divergent pairs |
| **F43-F** | the two vocabularies overlap | NOT FALSIFIED — overlap none |
| **F44-A** | exhaustion auto-interpreted as goal failure | NOT FALSIFIED — `goal_unverified` |
| **F44-B** | successful wrap-up auto-interpreted as goal success | NOT FALSIFIED — `goal_unverified` |
| **F44-C** | `turn_succeeded=True` forces `GOAL_MET` | NOT FALSIFIED — `acceptance=fail` → `goal_failed` |
| **F44-D** | verification failure hidden by a successful wrap-up | NOT FALSIFIED — still `goal_failed` |
| **F44-E** | replay differs from live | NOT FALSIFIED — live `goal_unverified` == replay |
| **F44-F** | the next turn treats a wrapped-up exhausted turn as incomplete | NOT FALSIFIED — no replay |
| **F44-G** | a terminal event overwrites a fatal error | NOT FALSIFIED — `done` present, `turn_succeeded=False` |
| **F44-H** | typed and dict disagree on the exhaustion row | NOT FALSIFIED — no diffs |

F44-D is worth calling out: the provider's wrap-up said *"all good, verified"* and the goal still came
out `goal_failed`, because `acceptance_verdict` — not the model's prose — decides.

---

## 11. Replay / Persistence

**UNCHANGED. No migration.** This phase touched **no** persistence module — verified by mtime:

```text
wisp/core/runtime.py       2026-09-24 20:22   untouched by this phase (pre-existing WIP)
wisp/core/session_repo.py  2026-09-23 14:18   untouched
wisp/infra/store.py        2026-09-04 09:24   untouched
wisp/core/goal.py          2026-09-24 17:59   untouched
wisp/core/acceptance.py    2026-09-22 08:34   untouched
wisp/core/verification.py  2026-09-22 08:35   untouched
```

The change is confined to the guard's **in-memory** classification and one constant's provenance. The
persisted event schema, the goal record's fields, and the replay derivation are byte-identical. Probe
**F44-E** confirms live and replay agree.

**Historical semantics.** Turns recorded before this phase with a *typed* provider and an empty stream
were journalled as completed (`done` persisted, `was_last_turn_complete=True`). Replay reproduces that
faithfully — it reads the journal, not the guard — so **historical records keep their recorded
meaning** and are not reinterpreted. The change affects only how *new* empty typed streams are
classified.

---

## 12. Security

**No security authority changed.** Nothing in `authorization`, `approval`, `ToolExecutor`, the
sandbox, tool-argument validation, model authority or completion authority was touched — all are
untouched modules (§11's list plus `infra/security.py`, `tool_executor.py`).

The change is a **classification order inside the stream guard**. It cannot execute provider data,
invoke a tool, or reinterpret tool arguments. It narrows what counts as "the provider produced a
response" — strictly *less* permissive than before, since a bare terminal marker no longer blesses an
empty attempt.

---

## 13. Remaining Findings

**None manufactured; none left unresolved within F43/F44's scope.** Three genuine follow-ups were
recorded inside ADR-0041 and ADR-0042 rather than as findings, because each is a question, not a defect:

1. **`_terminal_has_payload`'s field list still names `final_content`**, which canonicalization drops
   (ADR-0039's measurement). It is harmless — the canonical `text`/`calls` carry the payload — but the
   dead key could be pruned. *(ADR-0041 follow-up 2.)*
2. **Non-terminal types with no payload are still counted as meaningful** if they are unknown. Widening
   the payload rule to all types would go beyond F43's evidence. *(ADR-0041 follow-up 1.)*
3. **An exhausted turn records no structured reason** (budget vs. wrap-up failure); rows 3 and 4 are
   distinguishable only by the presence of an error. *(ADR-0042 follow-up 1.)*

One unrelated pre-existing finding remains from earlier phases: **F43's sibling F42's downstream
`terminal_outcome = "succeeded"` vocabulary for row 6** is accurate at turn level but close enough to
invite the collapse ADR-0042 forbids. Recorded as ADR-0042 follow-up 2.

---

## 14. Final Architecture Diagram

```text
                    PROVIDERS
                 typed OR dict
                       │
                       ▼
              events.canonical_event                      (ADR-0040 — the ONE canonicalizer)
                       │
                       ▼
                 CANONICAL EVENT
                       │
        ┌──────────────┴───────────────┐
        │   guarded_provider_stream    │
        │   1. terminal?  ── yes ──► payload? ──► meaningful | empty ──► break   (ADR-0041)
        │   2. non-payload type? ──► forward, not meaningful
        │   3. otherwise ──► meaningful, forward
        │   retry bounded by max_attempts
        └──────────────┬───────────────┘
                       │  TERMINAL_TYPES is the ONLY terminal authority
                       ▼
   ┌───────────────┬───────────┬────────────┬──────────────┐
   ▼               ▼           ▼            ▼              ▼
main loop      wrap-up    compaction    planner     persistence
   │                                                        │
   ▼                                                        ▼
turn_succeeded = saw_done ∧ ¬saw_fatal_error            journal
   │                                                    (last event DONE?)
   ▼                                                        │
acceptance_verdict (P3, VerificationFloorGuard)              ▼
   │                                              was_last_turn_complete
   ▼                                              = interrupted-turn / replay
derive_goal_state(turn_succeeded, acceptance, …)   signal — NOT a verdict
   │
   ▼
terminal_outcome · escalation · incomplete-turn replay
```

The seven questions the mission set out to separate, each with one owner:

| question | owner |
|---|---|
| "the provider ended the stream" | `TERMINAL_TYPES` |
| "the current turn completed" | `was_last_turn_complete` (journal) |
| "the current turn succeeded" | `turn_succeeded` (`runtime.py:938`) |
| "the work is verified" | `acceptance_verdict` (P3 / `VerificationFloorGuard`) |
| "the goal is met" | `derive_goal_state` (ADR-0035) |
| "the system should recover" | the recovery ladder, driven by `turn_succeeded` |
| "the system should escalate" | `escalated` in the goal record |

---

## 15. Final Status

```text
=== STATUS: COMPLETE ===

F43   :  RESOLVED — recovery classification is semantic and terminal-first;
         the terminal vocabulary is no longer re-spelled anywhere (disjoint sets)
F44   :  SETTLED ARCHITECTURALLY — five distinct authorities, measured and
         documented; no code change; exhaustion is neither goal success nor failure

ADR-0041 : ACCEPTED (F43)      ADR-0042 : ACCEPTED (F44)
ADR LOG  : 42 sections / 42 index rows   CONTEXT range -> ADR-0001 … ADR-0042

PRODUCTION CHANGES : +49 / −5 across 2 files
TEST CHANGES       : 1 new file (22 functions → 24 tests) + 4 tests rewritten + 1 comment
DEFAULT CHANGES    : 0
FALSIFICATION      : 0 of 14
NEW FAILURES       : 0
REPLAY             : UNCHANGED — no migration; historical records keep their recorded meaning
SECURITY           : UNCHANGED

NEXT : the three recorded follow-ups (ADR-0041 §2, §1; ADR-0042 §1), none blocking.
```

---

## 16. Evidence

| Artefact | Contents |
|---|---|
| `tests/reliability/test_post_m13_f43_f44_authority_convergence.py` | 24 tests: F43 equivalence/vocabulary/bookkeeping, F44 matrix, non-collapse |
| `.workbuddy-ai/memory/post-m13-f43-f44/f43_f44_investigation.py` | the F43 asymmetry and the F44 ten-scenario matrix |
| `.workbuddy-ai/memory/post-m13-f43-f44/falsification_probes.py` | F43-A..F, F44-A..H — 0 falsified |
| `WISP_ARCHITECTURE_DECISIONS.md` | ADR-0041, ADR-0042 + index rows |
