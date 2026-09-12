# PHASE 13A — SYSTEM RELIABILITY & PRODUCTION HARDENING AUDIT

## 1. Executive summary
Wisp's happy path is well-built; its failure paths are honest in the
small (typed errors, refusal-shaped timeouts, forward workspace recovery —
all TESTED) and dishonest in exactly three load-bearing places: (a)
timeout recorded as SUCCESS at graph joins (P0-1); (b) truncated provider
output committed as complete, including salvage into real file writes
(P1-1/P1-3); (c) audit integrity silently forked under concurrency with
losses that report success (P1-4/P1-7). Retries are the second theme:
4-deep nested provider retries (≈18 POSTs/turn), idempotence-gated graph
retries (good) beside ungated subagent re-execution (≤12×, P1-6), and
leaked work behind every blocking timeout (threads, container payloads,
hook children). Persistence is a single shared SQLite file (PROVEN) with
WAL but no app-level busy-retry, non-atomic transitions, and memory-only
budget/approval-grants. Full suite: 5387 passed / 17 failed (pre-existing,
8-cluster single root cause). P0:1 P1:8 P2:24 P3:13. Production tree
unmodified (verified `git status`).

## 2. Baseline
Phase 12.5 frozen at `c52edc2`: one runtime, one containment
(`wisp/pathsec.py`), one one-shot dispatch table, one redaction semantic,
`ToolExecutor → authorize() → ApprovalGate`. Census: 112K core LOC, 61.8K
prod / 50.5K test. All §36 invariants re-verified true during this audit
(see §33 cross-checks); findings are reliability defects, not invariant
violations — except P0-1, which violates "cancellation/timeout cannot
become false success".

## 3. Methodology
Read-only: rg/AST inventory (4 parallel sweeps: handlers, retry/timeout/
cancel, persistence, concurrency+providers), targeted source reads to
confirm each load-bearing claim, custom harnesses in /tmp (race: threads×
writes/graphs/audit; perf: workspace/graph/validator/store scaling;
crash: `fail_at` injector), full suite ×2 + subset reruns, live SQLite
inspection (read-only `.tables`). No file under `wisp/`/`tests/` touched.

## 4. Reliability model
Failure classes and their handling: see PHASE13A_FAILURE_TAXONOMY.md (17
classes, each with detection/propagation/retryability/recovery/durability/
visibility/silent-success verdict + evidence grade).

## 5. Failure taxonomy
See PHASE13A_FAILURE_TAXONOMY.md. Headline: MODEL/PROVIDER/TOOL failures
are detected and typed; GRAPH/WORKSPACE failures are durable and mostly
honest; PERSISTENCE/CORRUPTION failures are where silence lives (audit
sinks, trust file, chain forks); TIMEOUT/CANCELLATION are cooperative with
documented leaks; RESOURCE_EXHAUSTION is capped at every user-controlled
surface except long-lived caches/tables.

## 6. State machines
See PHASE13A_STATE_MACHINES.md. Agent/turn and workspace machines are
sound (TESTED, incl. crash-injector recovery). Graph machine has one
impossible state made possible (COMPLETED ∧ unsettled branches, P0-1) and
one terminality leak (CANCELLED→PENDING re-queue, P1-2). RunStore
transitions can duplicate `seq` / lose history rows (P2-3).

## 7. Error handling
1440 except-clauses / 206 files; 804 broad-`Exception` (56%); 0 bare;
17 BaseException (all cancellation-first, legitimate); 94 re-raise (6.5%).
Posture, not sloppiness — but 12 dangerous suppressions catalogued (D1–
D12, all file:line in subagent record): trust-destroy (D3→P1-5),
hash-despite-failure (D5→P1-7), dirty-dropped (D8→P2-15), checkpointless-ok
(D7→P1-8), zero-vector poisoning (D1→P2-18), robots fail-open (D2→P2-17),
audit pass (D4→P1-7), partial-entries (D6→P2-20), server-fallback double-run
(D10→P2-19), recount-overwrite (D11→P3), empty-string conflation (D12→P3),
dependents-[] (D9→P3). Rest: legitimate (teardown best-effort ~40,
cosmetic ~50, optional-probe ~80, narrow-except correct).

## 8. Retry analysis
20 retry implementations inventoried (R1–R20, owners/boundaries/backoffs in
record). Nested: provider 4-deep (≈18 POSTs worst case, billed, attempts
uncounted); subagent fanout→guard→timeout→validation (≈12× re-execution
after mutation, P1-6); graph→subagent handoff (R9 passes budget the callee
ignores at that call site); correction-edge replay orthogonal to
max_attempts (bounded by cycle iterations — acceptable). Counters: R2 keys
on turn iteration (not attempts); R10/R13 share one `retry_count` while
R11/R14 stack outside it; `_run_with_retry` docstring contradicts R10
(fix with P1-6). Good citizens preserved: R5/R1 no-retry-after-output,
R7 idempotence gate, R17 conflict-restore, web 404 do-not-retry.

## 9. Timeout analysis
20 timeouts inventoried (T1–T20). Coherent async story; every blocking
boundary leaks by (documented) design: tool threads survive (T4, pools 8/4
exhaustible → P2-1), `docker exec` payload survives (T6 → P2-2), async hook
child survives (T14 → P2-11), `future.cancel()` on threadpool git survives
(T18). `subagent_wait`/`background.result` expiry orphans intentionally
(no cancel issued — LLM must re-wait; P3 usability note). Join-timeout →
SUCCESS is the defect (P0-1), not a leak. Knob divergence: chunk deadline
90 vs canonical 30 (P3-1).

## 10. Cancellation
Cooperative everywhere; bounded joins (2–3s); orphans tracked. Clean:
SIGINT/WS-disconnect (no success), approval-cancel as verdict with
do-not-retry, background/legacy-bg cancel (never success), pool cascade,
recover() (never success). Defects: node-level cancel→retry (P1-2);
cancel reversible via `send()` (P2-9). Cancel-before-start/during-retry/
during-join/during-approval/during-apply: approval + apply paths are
synchronous decisions (no race); during-retry collapses to P1-2/P1-6;
during-join collapses to P0-1. Immediately-before-checkpoint: checkpoint
rows are per-transition durable; worst case re-runs one node (idempotence
gate holds).

## 11. Crash recovery
TESTED with built-in `fail_at` injector: mid-commit partial state →
`recover()` forward-completes to convergence; untouched files intact;
stage-failure symmetric. Corrupt checkpoint refuses resume (fail-closed);
corrupt journal dropped only when provably pre-first-replace (write-order
PROVEN). Graph resume re-derives attempts/budget from rows (budget resets
— P2-10). Session resume sees partial-as-complete (P1-1/P1-3 family).
memory.json ≤2s loss + no fsync (P2-15). Audit torn-last-line detectable
via `verify()`, nothing auto-verifies (P1-4).

## 12. Persistence
Single shared file PROVEN live (both table families in `.wisp/wisp.db` —
P2-4 question resolved: NO parallel runs system; divergence only via
explicit divergent paths + lazy table creation). WAL everywhere; sync
NORMAL (UnifiedStore, OS-crash loss deliberate) vs FULL default
(GraphStore); busy_timeout only on UnifiedStore; FK only on UnifiedStore;
GraphSecurityAuditor on rollback-journal. Autocommit everywhere except two
`transaction()` users (docstring "all writes" is FALSE). No app busy-retry;
`transition()`/`admit` races (P2-3). Schema: versionless IF-NOT-EXISTS +
probe-ALTER (safe concurrent DDL); corrupt primary → silent empty fallback
(P2-6). Concurrent first-touch init intermittently `database is locked`
(1/10 trials — P2-7). Only correct cross-process write txn in tree:
rate limiter BEGIN IMMEDIATE (precedent for fixes).

## 13. Durability
Matrix (durable?/recovery/acceptable?): graph run Y/SQL/yes; node state
Y-per-transition + memory-authoritative mid-run/yes; retry count
split (counter memory, attempts durable)/yes; budget N/recompute on resume/
NO (P2-10); approval decision side-effect-only + grants memory-only
(dead to_dict/from_dict)/re-ask, fail-closed/yes; artifact Y (file+row)/
yes; changeset plan-as-journal/yes; journal Y fsync/forward-recover/yes;
audit Y-best-effort/loss permitted/NO (P1-7); provider result transcript-Y
raw-N/resume-partial-as-complete/NO (P1-3).

## 14. Idempotency
Exactly-once claimed nowhere (honest). At-most-once per `execute()` except
legacy `build_tool_message` re-execution hazard (P2-4). Workspace apply
effectively-once via idempotent commit + forward recovery (RENAME check
weaker — existence-only). Provider pre-output at-least-once / post-output
at-most-once via `got_meaningful` rule (correct). Graph nodes at-least-once
gated by contract (correct; default `idempotent=True` with validator
contradiction flag). Non-idempotent assumed without evidence: subagent
retries (P1-6), `transition()` serialization, audit chain serialization,
`admit()` (P2-3 family).

## 15. Concurrency
15 unbounded/unsynchronized shared-state items (A1 #1–#15 in record):
caches without locks (`_CACHE`, `_CONTEXT_TTL`, `_ASSEMBLER`, models
cache), unbounded registries (`_store_cache`, checkpoint registry,
`_flock_cache`, session/event/trace tables), racy readers
(`SharedContext` lock-free reads, steering-inbox cross-thread,
`ConnectionManager.send`, `SessionRegistry`), unclosed thread-local conns
(GraphStore: none ever closed). Harness: H1 clean (16×20, 0.02s, no
errors); H3 clean steady-state (16/16); H2 FORKS CHAIN (P1-4); H3b
intermittent init lock (P2-7).

## 16. Long-run stability
UNKNOWN — no 10/30/60-min soak performed (stated gap, proposed gate §35).
Suspects for monotonic growth, ranked: event/trace/session tables (no
retention), `_store_cache`/registries, thread-local conns, telemetry
per-agent dicts (prune exists, no auto-call found), `_flock_cache`.
Bounded correctly: telemetry rings, checkpoint rings, bg caps (if
`prune()` called), repeat-cache/fetch-breaker, planner rotation.

## 17. Resource exhaustion
Caps verified present at every user-controlled surface (graph absolutes,
artifact 4MB/4096, changeset 100 files/1MB, web 2MB/5-hops, bash length+
output+timeout, context 6K tokens, bg 8/50, join param 1024, ID/reason
lengths). Gaps are §15's unbounded long-lived structures + thread leak
accumulation (P2-1) + socket growth with concurrency (unmeasured).

## 18. Provider degradation
Matrix (OpenAI/OpenRouter/NVIDIA-shared, Ollama) in record: timeouts sane,
429/5xx retried (nested — §8), 401 fail-fast with good hint + pre-flight,
malformed skipped with counters (OpenAI) or silently (Ollama — empty-vs-
corrupt indistinguishable downstream), truncation surfaced only for
`length` (mid-stream stall NOT — P1-3), budget/audit blind to failures
(P2-10/P2-24), NO failover anywhere (P2-23), one non-hardened path
(OpenRouter catalog fetch), no Retry-After (P3-4).

## 19. Streaming
Guard design is correct (pre/post-first-byte split, aclose, backoff only
on empty, never-retry-after-output). Three partial-as-complete paths
(P1-1/P1-3, all TESTED) + one normalization gap pending type-confirmation
(Ollama `StreamComplete/"complete"` ∉ bookkeeping → possible empty-turn
success — UNKNOWN, P2-22 with verification step) + one dead branch to
leave alone (P3-7). Duplicate-chunk: no dedup key observed (UNKNOWN).

## 20. Graph reliability
Scheduling/readiness/joins/routing/verifier/retries/cancel/pause-resume/
checkpointing/artifacts/approval/budget/failure-cascade reviewed; fortress
subsystem holds up except P0-1, P1-2, R8/R7 double-budget (bounded —
accepted with test), R9 inert budget handoff (fix with P1-6), stale-
generation handling correct, corrupt-row handling correct.

## 21. Graph liveness
No deadlock/unreachable/permanently-pending path found; cycle replay and
retries absolutely bounded; 0.5s slice keeps cancel responsive; 16-way
harness 16/16 terminal. UNKNOWN (gates, not findings): resume+changed
graph/policy, route+stale result, join+predecessor-skip, empty-branch join
semantics.

## 22. Workspace reliability
Snapshot/isolation/ChangeSet/conflict/merge/repair/apply/journal/rollback
all sound; drift-refusal covers external edit/delete/rename/symlink/perm;
crash recovery PROVEN both arrows; canonical workspace cannot silently
partially apply (refuses or forward-completes — TESTED). No finding above
P3. Exemplary subsystem; use its patterns (tmp+replace, fsync-first
journal, idempotent commit) as fix precedents.

## 23. Artifact reliability
Content-addressed + write-if-absent + hash-verified + capped: idempotent
and sound. Graph state cannot point at nonexistent artifacts through the
write path (creation is atomic-ish: file-x + row + event); manual deletion
underneath is undetected (UNKNOWN, low value — note only).

## 24. Authorization reliability
Authority path re-verified intact; salvage happens pre-authorize with
final-args authorize (VERIFIED — not a bypass). No retry/resume/routing/
apply path bypasses `authorize()` (retries re-enter `execute()`).
Stale-approval reuse: session grants memory-only + restart re-asks
(fail-closed); within-session grant scope is `should_ask`/`allow_tool`
lifetimes — no expiry observed (P3 question, not finding). Concurrent
approval races: approval is per-call synchronous verdict; no shared
approval-state mutation found. Invariant HOLDS: no path reaches privileged
op without authorization.

## 25. Audit reliability
Decisions durable across 3 sinks but best-effort (loss permitted, P1-7);
chain integrity broken under concurrency (P1-4); read-path partial silent
(P2-20); redaction effective incl. exceptions (12.5E + malformed-coercion,
unchanged); oversized payloads capped upstream; serialization failure
cannot break persistence (coercion — 12.5E). `verify()` detects forks but
is on-demand only.

## 26. Observability
Operator CAN answer: what runs (bg manager/snapshots), what failed where
(typed errors + events), what changed (ChangeSets + journals), what
artifact (content-addressed rows), what consumed budget (partially —
undercounted, P2-10). CANNOT answer from persisted state: what was retried
how often (attempts invisible to telemetry — P2-24), why retried (logs
only), provider failure reasons post-hoc (logs only), whether a completed
answer was truncated (no marker — P1-3), whether audit is intact without
manual verify (P1-4). Logs ≠ observability: failure reasons live only in
logs. Why-stuck: graph status/trace exist; join-timeout evidence exists
but status lies (P0-1).

## 27. Replay/reproducibility
Deterministic: graph definition+fingerprint, policy fingerprint, validator
verdicts. Reproducible-with-state: node inputs/outputs (rows), tool
exchanges (transcript), artifact refs, workspace snapshots/journals, retry
rows (attempts), audit chain (when intact). Probabilistic: anything past a
live provider. Impossible: raw stream bytes, truncation notices, per-turn
telemetry, in-memory budget/attempt counters, pre-crash memory facts (≤2s).
No deterministic-replay claim made by code (honest). Phase 13 gate: define
a replay bundle (graph hash + policy fp + inputs + artifact refs + journal
+ audit head) before claiming reproducibility.

## 28. Evaluation coverage
4 builtin scenarios (task/injection/bypass/cancel) + metrics supporting
recovery/interruption rates no scenario drives. Measures: correctness
(basic-read), security (injection/bypass), recovery (cancel, shallow).
Gaps: reliability (retry/idempotency), persistence/crash, concurrency,
provider-degradation, streaming-truncation, long-run. Proposed matrix —
T1 unit (existing fortress), T2 integration (existing), T3 adversarial
(P0-1/P1-1 fixtures), T4 fault injection (fail_at precedent generalized:
kill -9 points × recovery asserts), T5 long-run soak (RSS/fd/growth
ceilings), T6 production simulation (degraded-provider + concurrent-run
mix). NOT implemented (audit-only).

## 29. Performance
See PHASE13A_PERFORMANCE.md. No perf P0/P1: worst measured (100-file apply
~85ms, 200-chain 134ms, 512-fan validate 36ms) is healthy. Superlinear
workspace apply is the curve to watch; caps convert it from risk to cost.

## 30. Scale projection
See PHASE13A_PERFORMANCE.md. First bottleneck: workspace journal-per-op
fsync + full-scope hash (~250K LOC, many-file changesets). Second:
single-file SQLite under concurrent server+CLI writers (busy-waits then
loud failure — degrades loudly, good). Hard ceiling: in-memory session
maps + unbounded tables + unclosed conns (1M LOC / 100 runs). Linear
extrapolation NOT assumed: apply cost measured superlinear; SQLite
contention is step-shaped (fine → locked); connection growth is
thread-count-shaped.

## 31. Test health
5387 passed / 17 failed / 2 xfailed, 196s. The 17 are pre-existing on
frozen HEAD (production untouched this phase): 8-cluster single root cause
(Ollama URL validator vs `"---"` placeholder, factory.py:158 — contract
mismatch, test-rot or validation-tightening aftermath); memory-cache ×2,
bash-kill ×1 (marker survived — env/process?), prompt-sync ×1, verification
×1, supervisor ×1, misc ×3. Identical set in 10-file subset rerun → NOT
full-suite order dependence. No network dependence observed (all mock);
no leak triage performed (P3). Gate: green CI before Phase 13
implementation (P2-21).

## 32. Security/reliability intersection (mandatory)
- Retry → bypass: NONE (re-authorize verified). Retry → duplicate effect:
  YES (P1-6) — reliability mechanism weakens integrity, not authority.
- Resume → stale approval: NO (re-ask; fail-closed) but grants have no
  expiry/scope-persistence story (P3 question).
- Recovery → policy bypass: NONE (resume re-validates; corrupt checkpoint
  refuses).
- Crash recovery → duplicate mutation: workspace NO (idempotent commit);
  plain file writes YES narrow window (P2-5); subagent re-execution YES
  (P1-6).
- Artifact recovery → path escape: NO (containment canonical, 12.5C).
- Cache → stale credential: provider-models cache keyed by key-fingerprint
  (rotation creates new key — safe); `_provider_cache` per-runner (safe).
- Concurrency → audit ordering: YES (P1-4 forks chain).
- Fallback → reduced sandboxing: server-fallback re-runs locally with SAME
  sandbox tier (no reduction) but double-execution (P2-19).
- Failover → policy drift: N/A (no failover); LSP timeout→allow is the
  live fail-open to confirm-or-fix (P3-12).
- Post-authorize arg rewrite (health INFO): 12.5-verified upstream
  pre-existing; salvage now confirmed pre-authorize (not this).

## 33. P0/P1/P2/P3 findings
See PHASE13A_RISK_REGISTER.md (full register with trigger/reproduction/
impact/root-cause/evidence/remediation/test/risk per P0/P1 item).
P0:1 (join-timeout→success). P1:8 (truncated-write salvage, node cancel→
retry, stall-as-complete, audit chain fork, trust destruction, ungated
subagent retries, audit-loss-invisible, checkpointless-ok). P2:24.
P3:13. No severity inflated: each P0/P1 has deterministic trigger or
harness reproduction.

## 34. Remediation roadmap (dependency order, not severity)
1. P0-1 honest join status (unlocks truthful everything downstream;
   no dependencies).
2. P1-4 audit write-path integrity — lock + atomic RMW (precedents:
   tools/audit flock, rate-limiter BEGIN IMMEDIATE); P1-7 honest return
   rides the same change.
3. P1-1/P1-3 truncation markers (persist `[TRUNCATED]` + `done.truncated`;
   refuse-or-flag `_raw` mutating writes). Depends on nothing; unblocks
   honest resume.
4. P1-2 cancel excluded from retry set (one-line semantic fix + test).
5. P1-6 subagent idempotence gate (adopt R7 pattern; fix R9 handoff,
   R10/R13 counter sharing, docstring; P2-4 + P3-3 ride along).
6. P1-5/P1-8 fail-closed-or-visible (backup-aside trust; checkpoint flag +
   audit). Independent, batch.
7. P2 wave: P2-3 txn parity → P2-7 init retry → P2-1 pool shedding →
   P2-2 kill parity → P2-5 tmp+rename → P2-6 loud fallback → P2-8
   retention/caps → P2-10 durable budget → P2-24 telemetry attempts.
8. P2-21 green CI BEFORE any of the above merges (clean signal).
9. P3: knob unification, docs, dead-code removal, eval matrix build-out.

## 35. Phase 13 implementation gates
G0 CI green (P2-21) + soak harness (10-min RSS/fd ceilings) + T4 fault-
injection fixtures (kill points × asserts) + replay-bundle definition.
G1 honest status (P0-1, P1-2, P1-1/P1-3) with regression fixtures.
G2 durable audit (P1-4, P1-7) with H2-as-regression.
G3 bounded side effects (P1-6, P2-4, P2-5) with exactly-once matrix tests.
G4 hardening wave (P2-1/2/3/6/7/8/10) + observability (P2-24) + eval tiers
T3–T6. No behavior work enters Phase 13 before G0.

## 36. Limitations
No soak run (UNKNOWN long-run growth); no network fault injection (provider
timing UNKNOWN); no multi-process audit contention test (threads only);
resume+changed-graph/policy, route+stale-result, empty-join semantics
UNKNOWN; duplicate-chunk dedup UNKNOWN; per-endpoint server timeout
posture partially inventoried (uvicorn defaults); satellite dirs (TUI/
desktop/mobile) out of scope; findings are single-machine CPython 3.11.

PHASE 13A — AUDIT COMPLETE

P0: 1
P1: 8
P2: 24
P3: 13

RECOMMENDED NEXT IMPLEMENTATION GATE:
G0 — green CI (fix P2-21 contract mismatch) + 10-minute soak harness with RSS/fd/growth ceilings + kill-point fault-injection fixtures + replay-bundle definition; then G1 honest-status fixes (P0-1, P1-2, P1-1/P1-3). No Phase 13 behavior work before G0.
