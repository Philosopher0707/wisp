# PHASE 13-H4 — Turn Success Semantics & Replay Integrity Forensic Audit

AUDIT ONLY. Zero production files modified. Harness:
`tests/reliability/test_13h4_success_semantics.py` — **29/29 pass**,
ruff F-clean. §20 subset: **433 passed, 0 failed**.

## 1. Executive Summary

H1's claim is **CONFIRMED** at H3 HEAD, by source and behavior:
`turn_succeeded` has exactly two assignments (`False` init,
unconditional `True` on generator exhaustion, `runtime.py:412,463`) and one
consumer (repo-DONE gate, `:588`). The forward invariant fails
deterministically: H3 error-terminated, timed-out, malformed, empty, and
exhausted turns are all recorded repo-complete. The inverse holds. Consumers
are unaffected in practice because none read the flag — headless/CLI/server/
SDK/benchmark all derive outcome from EVENTS (H3's closure is what flipped
headless `ok` honest). Resume trusts repo-DONE unconditionally (failure starts
a new attempt, never reruns); cancel is the only state recorded incomplete,
via the crash-recovery replay branch. The repo log records prompts and
completion markers only — **DETERMINISTIC REPLAY: NOT ESTABLISHED**.
Replay risk: **RISK 2**. New finding: blob-save failure is swallowed while
repo-DONE is still written (persistence/observability defect, P2).

## 2. Scope / Audit Boundary

§2 lists `wisp/runtime.py`, `wisp/events.py`, `wisp/repl.py`, `wisp/cli.py`,
`wisp/entry.py`, `wisp/renderer.py` — actual paths are `wisp/core/runtime.py`,
`wisp/core/events.py`, `wisp/cli/repl.py`, `wisp/transport/cli.py`,
`wisp/entry.py`, `wisp/transport/renderer.py` (same §2 correction as H2).
All §1 MUST-NOT items honored; defects pinned, none repaired.

## 3. Success State Data Flow

provider result → core.turn events → `run_turn` fan-out → `turn_succeeded`
→ repo-DONE → resume-check / nothing else.

| State | Produced by | Persisted? | Consumed by | Authoritative? |
|---|---|---|---|---|
| provider round state | provider_stream guard | no | core loop | yes (round) |
| terminal event | core (`done`/`error`) | no (streamed) | transports, headless `ok`, benchmark | yes (outcome) |
| `turn_succeeded` | `run_turn` exhaustion/exception | **no** | repo-DONE gate only | no (exhaustion proxy) |
| repo DONE marker | `_persist_turn_state` | yes (SQLite) | resume-replay check | partial (completion, not success) |
| session blob | `_persist_turn_state` | yes (store) | resume load | yes (content, no outcome field) |
| telemetry counters | `record_turn` | yes (memory) | `/tokens`, metrics | no (no outcome dimension) |

## 4. All `turn_succeeded` Assignments

Two, both in `wisp/core/runtime.py`: `False` (`:412`, turn entry),
`True` (`:463`, first line after the event loop exhausts — unconditional,
no predicate on terminal type). Codesearch over `wisp/`: no other producer,
no other reader. Test fixtures construct real runtimes (no flag fixtures).

## 5. Turn Outcome Matrix

11 runtime-level rows (events / done / errors / repo-complete):
A clean 1/0/T · B H3-stall 0/1/T · C timeout 1/1/T · D cancel 0/0/F ·
E malformed 0/2*/T · F empty-dict 0/2*/T · F2 empty-object 1 natural-empty/T ·
G exhausted 1/0/T · H verified-after-nudges 1/0/T · I content+tool 1/0/T ·
J fail-then-ok ([user,DONE,user,DONE]).
(\*live diagnostic + H3 closure.)

## 6. Success Invariant Results

Forward (`success ⇒ actually completed`) **FAILS** — B-row counterexample
(terminal error, no done, repo-complete). Inverse (done ⇒ repo-complete)
**HOLDS** on all rows.

## 7. H3 Interaction

H3 unchanged in H4. Verified: G1B-incomplete → closure error (last event) +
no done + repo-complete. Terminal error and internal success coexist —
contradiction documented (observability, P2); user-facing layers already see
the error.

## 8. Persistence Analysis

Survives restart/resume/replay: repo event log (prompts + DONE/ERROR
markers), session blob (full messages, no outcome), telemetry counters.
The flag itself survives nowhere. Save-failure is swallowed (logged) after
repo-DONE is written — blob loss invisible.

## 9. Resume Semantics

R1 success / R2 H3-fail / R3 timeout / R5 exhaustion: repo DONE → no replay,
next turn is a fresh attempt (provider re-invoked, never suppressed).
R4 cancel: repo incomplete → crash-recovery branch replays persisted prompts,
then completes. R6 (error marker, no DONE): incomplete → completes. R7 has no
meaning in this design (no success marker exists to contradict a terminal).
Failure can never become success in place; rows are append-only.

## 10. Replay Semantics

"Replay" = crash-recovery reconstruction of **prompts** (`Session.replay`
rebuilds user/assistant/tool messages; DONE is a no-op, ERROR becomes a
system line). No outcomes, no tool results from the engine path, no attempt
ids, no determinism claim possible. `turn_succeeded` plays no role in replay
beyond gating the DONE row that suppresses the recovery branch.

## 11. G1E Retry Interaction

Turn-level retry is a new turn: provider re-invoked unconditionally after
failure. `turn_succeeded` is not consulted by any retry path
(informational/irrelevant, not authoritative). G1E (subagent/orchestrator
retry) untouched and untested here beyond non-interference (subset green).

## 12. API / SDK Consumers

No consumer reads the flag or repo marker for outcome: headless
`ok = len(errors)==0`; CLI renders events + unconditional stats; server/WS
stream events; SDK yields events; benchmark scores events. The only
`if turn_succeeded`-equivalent in the tree is the repo gate itself.

## 13. Observability Consistency

`provider_failed=true ∧ terminal=error ∧ repo-complete=true` on every H3
path (P2 observability); timeout same; empty-`done(natural)` same (H2 P1);
save-failure silent (P2 persistence); cancel-incomplete (correct). Metrics
(`turns_total`, latency, tokens) count failures as turns with no outcome
dimension (P3). No retry/persistence/user-visible-correctness defect beyond
the above: every surface the user sees derives from events, which H3 fixed.

## 14. Terminal Resurrection Tests

T1 closure emitted exactly once; T2 no `done` ever follows; T3 no second
marker within the turn; T4 resume after failure is a new provider round;
T5/T6 old rows byte-identical after retry (append-only). G1C-style
terminality holds at the log layer.

## 15. Attempt Identity

Attached to nothing: no turn/attempt ids in repo rows or DONE payloads
(`{turns, reason:natural}` both times). Fail-then-success reads
`[user,DONE,user,DONE]` — attempts distinguishable only by position.

## 16. Contradiction/Fuzz Results

Accepted (documented): error∧repo-complete, timeout∧repo-complete,
empty-done∧repo-complete, save-fail-silence. Correct: cancel∧incomplete.
Unreachable: done∧repo-incomplete (all done paths exhaust normally),
cancel∧success. No new validation added.

## 17. H1 Claim Verification

**CONFIRMED** — source (unconditional assignment, codesearch-pinned) and
behavior (B/C/E/F rows repo-complete without successful completion).

## 18. Replay-Risk Classification

**RISK 2**: success state steers resume (failed ⇒ trusted-complete ⇒ no
rerun) but history is append-only, retries are new attempts, nothing is
overwritten or auto-skipped. Not RISK 3 (no mechanism skips failed work).

## 19. G0–G1 Regression Results

433/433 across reliability (H2+H4), graph-terminality, salvage, retry,
journal, security, breaker, transport-cli, core-stateless, runtime×3,
session×2, compaction, headless×2, server×3, stream×4. `test_runtime_injected_context`
(excluded) fails identically at HEAD baseline per H3 triage. No prod diff
since H3, so H3's full-suite verdict stands.

## 20. Findings

1. Forward invariant fails by design (P2 observability; user surfaces safe
via events). 2. Repo log cannot distinguish failure from success (P2).
3. Blob-save failure silent (P2). 4. Metrics outcome-blind (P3).
5. No attempt identity (P3, blocks future retry/resume reasoning).
6. H1 claim confirmed. 7. No consumer reads the flag — blast radius of a
future redefinition is the repo gate + resume branch only (good news for H5).

## 21. Severity

H4 max **P2** (observability/persistence). No P0/P1: H3 already closed the
user-visible delivery hole; nothing here loses work or grants authority.

## 22. Recommended Remediation

Per-candidate ranking (correctness / replay-risk / migration / G1B-G1E-G1C /
persistence / API-compat): **A derive-success-from-terminal** (highest value,
lowest risk — single predicate at the repo gate; G1B-neutral, API-neutral
since nobody reads the flag); then **C attempt-scoped outcome**
(`turn_id/attempt_id/outcome` rows; needs schema touch); then **E resume
recomputation** (follows from A+C); **F compat shim** unnecessary (no external
consumers — verify once more at implementation); **B enum** as the shape of A;
**D immutability** already holds (append-only — pin, don't build).

## 23. Unknowns

Provider-side timings; transport POST counts (standing); whether any
out-of-tree consumer reads the SQLite `session_events` table directly;
live-incident history (unrecoverable, unchanged).

## 24. Next Gate

H5 (if commissioned): implement candidate A (derive repo completion from
terminal event type: DONE→complete, error→failed, absent→incomplete) behind
the H4 matrix as failing-first gate, plus attempt-ids (C) if resume
reasoning is in scope. Cancel path already yields the desired semantics —
use it as the reference implementation.

```text
VERDICT: PASS WITH KNOWN UNKNOWNs
NEXT PHASE: 13-I2 — success-derivation remediation (A), gated by the H4 matrix; attempt identity (C) only if resume reasoning is required.
```
