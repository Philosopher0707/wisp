# PHASE 13-H5 — Success Derivation & Repository Completion Remediation

Implements H4 candidate A. Surgical diff: **`wisp/core/runtime.py` only
(+24/−4)**. No G1B/H3/replay/schema changes. TDD failing-first throughout,
built in a disposable worktree at `de130fe` (main tree untouched except via
final file sync; foreign 13-I2 WIP there never touched).

## 1. Executive Summary

Repository completion now derives from terminal evidence instead of bare
generator exhaustion. H3 errors, timeouts, malformed/empty failures, and
iteration exhaustion leave repo-INCOMPLETE with an ERROR row (never DONE);
clean completions (including the natural-empty `done`) are unchanged;
cancel keeps its reference behavior. Full suite: zero new failures vs the H3
baseline. Replay risk stays RISK 2 with the failure mode inverted
(false-complete → true-incomplete).

## 2. H4 Finding Being Remediated

`turn_succeeded=True` unconditional on exhaustion (`runtime.py:463`);
forward invariant failed on every non-clean path; flag unpersisted and
unread except by the repo-DONE gate.

## 3. Scope

Touched: `run_turn` event tracking + the single completion assignment +
`_persist_turn_state` marker branch. Explicitly not touched: G1B streams, H3
closure text, retry budgets, graph, executor, auth, pruning, schemas (repo
ERROR uses the existing `SessionEvent.error`), replay implementation,
telemetry, H4/H2 reports.

## 4. Failing-First RED Evidence

`tests/reliability/test_13h5_success_derivation.py` (19 tests) vs unmodified
HEAD: **11 failed** (E1–E4, timeout, exhaustion, malformed, ordering, R1
resume, append-only, flag spy — all on repo-DONE written despite terminal
error), **8 passed** (S1–S3, cancel, natural-empty, success singularity).
Recorded pre-fix; all 19 pass post-fix.

## 5. Implementation

- Loop tracks `saw_done`, `saw_fatal_error` (error event with `recoverable`
falsy), `terminal_error_message` (last error text).
- Decision: `turn_succeeded = saw_done and not saw_fatal_error` — the one
authoritative point (§7); the field stays, its authority now flows from
evidence (§18), so stale-True is impossible by construction (spy-pinned).
- Persist: DONE on success; else the terminal ERROR row (existing schema);
never both; cancel/exception paths byte-identical.

## 6. Terminal Event Authority

Existing taxonomy only: `done` = completion claim, `error` with
recoverable-falsy = fatal, recoverable errors = diagnostics. Unknown types
cannot complete (only `done` sets the bit). No parallel protocol.

## 7. Completion Predicate

`saw_done and not saw_fatal_error`. Fatal-only (not any-error) is load-bearing:
denials and recovered mid-turn transients followed by a clean `done` still
complete — otherwise the crash-recovery replay branch would wipe live tool
history on the next turn. Documented in-code.

## 8–9. Error / Success Matrices

E1 stream / E2 partial / E3 reasoning-only / E4 empty → incomplete, no DONE.
S1 clean / S2 tool-bearing / S3 multi-iteration → DONE, unchanged.

## 10. Timeout / Cancellation

Timeout (fatal error + formal done) → incomplete + ERROR row; watchdog
untouched. Cancel → incomplete, recovery identical to before (reference
behavior preserved, pinned).

## 11. Exhaustion

Wrap-fail (E5101 error + formal done) → incomplete + ERROR row;
exhaustion ≠ success pinned. No new taxonomy. Wrap-ok unchanged.

## 12. Malformed / Empty

Both incomplete. Natural-empty `done` (H2 bookkeeping semantics) still
completes — provider semantics not reclassified.

## 13. Persistence Ordering

Terminal event (streamed) → decision (loop exhaustion) → marker (worker
thread). Proven by closure-last + repo-matches-tail assertions. DONE and
ERROR mutually exclusive per turn; at most one marker per turn.

## 14. Resume Semantics

R1 failed turn: repo-INCOMPLETE → recovery replays persisted prompts (tool
history shed), new attempt executes and completes — no longer silently
trusted complete. R2 success / R3 timeout-honest / R4 cancel-recovery:
pinned (R4 behavior identical, now joined by all failure classes).

## 15. Append-Only History

Fail-then-retry yields `[user, error, user, done]`; pre-retry rows
byte-identical afterwards. Attempt 1 never mutates.

## 16. `turn_succeeded` Compatibility

Field retained, authority corrected (not removed). Sole consumer remains the
repo gate — H4's blast-radius finding holds, now with honest input. Spy test
pins `False`-on-error / `True`-on-clean.

## 17. Blob-Save Deferred Finding

Untouched by construction (save path not entered by this diff); H4 P2 stands
as filed. No second persistence change combined.

## 18. Replay-Risk Reassessment

Stays **RISK 2**: resume is still state-driven (now honestly so) with
append-only history; nothing auto-skips work. Improvement is truthfulness,
not risk class. DETERMINISTIC REPLAY: NOT ESTABLISHED (unchanged).

## 19. Security

`test_security_policy` + `test_permission_mode` green in-subset; completion
derivation touches neither policy evaluation nor approval paths — no new
authority; model output still cannot write DONE (only `done` events do, and
fatal errors veto).

## 20. Regression Results

H5+H2+H4: 133 reliability green. §23 subset (G1B/C/D/E, journal, security,
breaker, transport, core, runtime×3, session×2, compaction, headless×2,
server×3, stream×4): 341 green. Full suite: 46 failed vs H3-baseline 48 —
delta is 2 foreign-file tests absent from the clean room; **zero new
failures**, 5596 passed. H2/H4 compatibility updates confined to
repo-outcome assertions (reports untouched).

## 21. Production Diff

`wisp/core/runtime.py`: +24/−4 (tracking vars + 2 dispatch branches +
derived assignment + persist marker branch + param). Nothing else.

## 22. Remaining Unknowns

Standing: provider-side timings, transport POST counts, out-of-tree SQLite
readers, live-incident history. New: none.

## 23. Deferred Work

turn_succeeded field removal (trivial now, keep for a quiet phase);
attempt-ids (H4-C); meter/pruner (H2-F/E); bookkeeping-set mismatch (H2 P1 —
note: H5 makes its consequence visible as repo-incomplete rather than DONE,
downgrading its sting without fixing the classifier).

## 24. Verdict

```text
VERDICT: PASS
NEXT PHASE: 13-H6 — from evidence: attempt identity (H4-C) if resume reasoning needs it; else the H2 bookkeeping-mismatch classifier. Do not bundle either into this diff.
```
