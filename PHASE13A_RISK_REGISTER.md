# PHASE 13A — RISK REGISTER

Severity: P0 catastrophic-correctness | P1 serious, gate hardening |
P2 meaningful | P3 later. Every P0/P1: trigger, reproduction, subsystem,
impact, root cause, evidence, remediation, regression-test requirement.

## P0

### P0-1 Join-timeout partial release recorded SUCCESS → run SUCCEEDED
- Trigger: join node with `join_timeout_s` fires before branches settle;
  join is a sink (or feeds only-success paths).
- Reproduction: graph with slow branches + short join timeout; no
  concurrency needed — deterministic (code path, no race).
- Subsystem: `wisp/graph/executor.py` (_drive join release ~704-718;
  final sink rule ~495-509).
- Impact: timeout silently becomes success; downstream/verifier acts on
  partial data; resume sees terminal run.
- Root cause: release writes `NodeStatus.SUCCESS` with `timed_out:True`
  instead of a distinct terminal state; final rule counts it as success.
- Evidence: TESTED — source-verified both sites; evidence trail
  (`timed_out:True`, `join_released reason=timeout: partial release`)
  exists but status is success.
- Remediation: record TIMEOUT (or new PARTIAL) on timeout-release; final
  rule treats PARTIAL as non-success (FAILED or CANCELLED per policy).
  Files: `graph/executor.py`, `graph/types.py`; invariant: honest terminal
  status; test: join-timeout deterministically yields non-success + late
  branch settle doesn't rewrite terminal row.
- Risk of change: low (status-enum addition, contained).

## P1

### P0-ADJ Tool failure → message protocol integrity (LIVE, fixed by adjacent hotfix)
- Trigger: any pre-execution refusal (fetch breaker, repeat guard, hooks,
  plan/danger/perm blocks, declines, pre-hooks) on an OpenAI-schema turn;
  plus slow JSON-string tool errors on the spinner path.
- Reproduction: tests/test_tool_protocol_integrity.py (31 tests: TLS-fail
  twin session, breaker chain, parallel/out-of-order, timeout/exception/
  cancel, adversarial history, preflight matrix).
- Impact: provider 400 (missing tool_call_id) after honest tool failures;
  false ✓ on failed tools.
- Root cause: early emissions dropped the inbound ID; history defaulted
  ""; serializer sent ""; spinner check didn't parse JSON envelopes.
- Fix: ID forwarded at all 9 emission sites; shared
  renderer.result_is_error across spinner/header/counters; preflight
  pairing validator in OpenAI._build_payload (OpenRouter/NVIDIA inherit;
  Ollama has no such IDs). No generated IDs, no deleted history.
- Regression: G1B/G1D/G1E suites green; full suite identical to clean
  tree. Report: PHASE13_TOOL_PROTOCOL_INTEGRITY.md.

### P1-1 Truncated stream salvaged into real file write (invented path possible)
- Trigger: mid-stream provider stall during a `write_file` call whose args
  arrived as `{"_raw"}` (openai.py:320-323), esp. auto-approve mode.
- Reproduction: scripted truncated SSE ending inside tool-args JSON.
- Subsystem: `core/stateless.py` _validate_tool_args salvage (1866-1886).
- Impact: partial content written as complete; invented `./output.md|.txt`
  when path omitted; stall warning ephemeral (runtime.py:439-450) while
  truncated text durable (530-534) → resume treats partial as complete.
- Root cause: salvage prioritizes never-stalling over never-corrupting;
  no truncation marker propagated to args/audit/persist.
- Evidence: TESTED (end-to-end code path read). Authorize sees final args
  (1548→executor 647 — VERIFIED, so NOT an auth bypass).
- Remediation: on `_raw`-only or stall-flagged args for mutating tools,
  refuse with truncation error (fail-closed) or require explicit
  confirmation carrying a truncated-content warning; persist stall marker
  in transcript. Test: truncated-write deterministically refused/flagged.

### P1-2 Cancelled node re-queued when attempts remain
- Trigger: task-level cancel of a node with `attempt < max_attempts`.
- Subsystem: `graph/types.py:60-62` (CANCELLED ∈ FAILED_NODE_STATUSES) +
  `executor.py:578`. Run-level cancel unaffected (drive checks first).
- Impact: cancellation loses terminality; wasted re-execution post-cancel.
- Evidence: TESTED (code). Remediation: exclude CANCELLED from auto-retry
  set (cancel is a decision, not a failure); test: cancel-then-no-requeue.
  Risk: low.

### P1-3 Mid-stream stall persisted as complete answer (non-tool path)
- Same mechanism as P1-1 minus tool: `chunk_stall` provider_status is
  non-[SYSTEM] → unpersisted; partial assistant text durable; `done`
  emitted (stateless.py:542-592). Ollama `StreamError(partial_*)` variant:
  partial_* never joins content yet prefix persists (ollama_client
  610-615, stateless 293-296). Evidence: TESTED. Remediation: persist a
  `[TRUNCATED]` marker message; `done` carries `truncated:true`. Test:
  stall-fixture turn yields marker + flagged done.

### P1-4 Concurrent JSONL audit writes fork hash chain, silently
- Trigger: ≥2 threads/processes recording to one AuditTrail file.
- Reproduction: harness /tmp/h13a_race.py H2 — 16×25 writes, 400/400
  present, `verify()` fails at entry 2–4, zero errors raised. Reran: fails.
- Subsystem: `infra/audit.py` (`_last_hash` in-memory race + unlocked
  append). Same RMW shape in `ImmutableAuditTrail.record_decision`
  (268-282, INFERRED — separate instances/processes share one DB).
- Impact: audit integrity broken with no signal; `verify()` exists but
  nothing calls it on the write path (only trace CLI / health check).
- Evidence: TESTED. Remediation: inter-process lock (fcntl, as
  tools/audit.py JSONL sink already does 157-166) + atomic read-modify-write
  (BEGIN IMMEDIATE, as rate limiter deps.py:265-297 already does) or
  per-process chain segments; test: H2 harness as regression (must verify
  clean).

### P1-5 Corrupt trust file destroys entries, then overwrites
- Trigger: malformed trust JSON (disk fault, concurrent write, hand edit).
- Subsystem: `trust.py:88-92` (`except Exception: trusted = []`, then
  append+truncate+write). No log. Fail direction is closed (more approval)
  but data loss is silent and permanent.
- Evidence: TESTED (code). Remediation: on parse failure, back up corrupt
  file aside + start empty with warning + audit event; never truncate
  before successful parse. Test: corrupt-file fixture preserves backup.

### P1-6 Subagent full-task retries without idempotency gate (≤12× re-execution)
- Trigger: transient 429/timeout/crash after partial tool mutation with
  auto_retry on (defaults set 2 retries at tool_executor.py:1488,1590);
  worst composition map-reduce→parallel-guard→timeout-retry ≈12×.
- Subsystem: `multi_agent/subagent_orchestrator.py` R10 (850-869, no
  idempotency check) / R11 (978-997) / R12 (1413-1473); docstring 1416-1418
  falsely claims timeouts never retry (R10 does).
- Impact: duplicate side effects (shell writes, file edits, git applies,
  patch re-application) + cost.
- Evidence: TESTED (inventory). Remediation: adopt graph-R7-style gate
  (refuse auto-retry after mutating tools unless idempotent) + fix
  docstring + carry `retry_count` through R11/R14. Test: mutate-then-429
  fixture runs tools exactly once.

### P1-7 Audit loss invisible (two sinks)
- `infra/security.py:235-236` audit write failure → `pass`, caller success.
- `infra/audit.py:130-133` write failure still RETURNS HASH as if durable.
- Evidence: TESTED (code). Remediation: return success/failure honestly
  (`""`/False on failure, matching GraphSecurityAuditor's existing ""
  contract); emit degraded-mode metric. Test: read-only-dir fixture
  asserts failure signal.

### P1-8 Checkpoint failure proceeds without safety net, reports ok
- `tools/checkpoints.py:153-160` resolve/read/store failure → None;
  mutation proceeds; later rewind impossible; op reports ok.
  Same shape `workspace.py:538-545`. Debug-log only, no audit.
- Evidence: TESTED. Remediation: fail the mutation closed OR surface
  `checkpoint: none` in result + audit event so rewind-impossibility is
  visible. Test: broken-checkpoint-dir fixture asserts visible flag.

## P2

- P2-1 Tool-timeout thread leak exhausts 8/4 pools (tool_executor
  1234-1253; BY DESIGN per comment; observable via counters, unrecoverable
  short of restart). Remediation: bounded wait + pool-shed load-shedding.
- P2-2 Docker timeout kills CLI, not container payload (sandbox
  `__init__.py:162-165` vs Noop killpg / PTY killpg). Post-timeout
  container mutation invisible. Remediation: killpg/docker-kill parity.
- P2-3 `SQLiteRunStore.transition` non-atomic RMW + `seq=len()` race +
  `Scheduler.admit` check-then-act (runs/store.py:119-140,
  scheduler.py:32-36). Remediation: BEGIN IMMEDIATE txn (precedent:
  deps.py:265-297).
- P2-4 `build_tool_message(result=None)` re-executes pipeline incl.
  stateful tools (tool_executor.py:906-917, admitted in comment).
  Remediation: enforce caller's "should not happen" with exception instead
  of re-execution.
- P2-5 Plain `write_file` torn-write (direct O_TRUNC, no tmp+rename/fsync;
  tools/_utils.py:333-365) vs workspace apply's tmp+replace. Remediation:
  tmp+rename parity (precedent: workspace.py:623-638, memory.py:166-174).
- P2-6 UnifiedStore init-failure → silent empty fallback DB
  (store.py:50-60): corrupt primary presents as empty. Remediation:
  fail loud + keep read-only access to primary.
- P2-7 Concurrent first-touch graph-DB init → intermittent
  `database is locked` (1/10 trials, 16-way). Remediation: retry-on-busy
  at init or serialize DDL (CREATE IF NOT EXISTS already idempotent;
  add busy_timeout pragma parity + init retry).
- P2-8 Unbounded growth: module caches (code_index `_CACHE`,
  `_store_cache`, `_flock_cache`, models cache), no-retention
  session/event/trace/artifact tables, unclosed thread-local conns
  (GraphStore has no `close()`; asyncio.to_thread spawns unbounded
  threads). Remediation: TTL+cap parity with prompt_cache.py:37-71
  precedent; retention policy for event tables; close-on-thread-exit.
- P2-9 Background cancel reversible via `send()` (RUNNING again);
  cancel not terminal-durable. Remediation: terminal-cancel state or
  explicit resume semantic.
- P2-10 Budget memory-only + retries uncounted (executor.py:250,384;
  runtime.py:561-570) → crash resets budget; spend undercounted ~N×.
  Remediation: durable budget row (attempts already durable per row).
- P2-11 Async hook timeout never kills child (hook_types.py:419-456);
  verdict blocks but side effects happened. Remediation: kill-on-timeout
  parity with sync path.
- P2-12 `hardened_post/get` blocking `time.sleep` in async paths
  (transport.py:833,861,915,940) stalls loop during backoff.
  Remediation: async sleep parity (async_retry_with_backoff exists).
- P2-13 Approval session grants memory-only (`to_dict/from_dict` dead
  code — zero callers). Fail-closed (re-ask), so P2 not P1; remediation:
  wire persistence or delete dead code.
- P2-14 MCP `auto_retry` retries ANY exception (manager.py:889-940) —
  unsafe template; data path currently avoids it. Remediation: transient
  classification parity with R3.
- P2-15 Plain-file memory.json: no fsync + corrupt→empty
  (memory.py:100-102,166-174) + dirty-flag dropped after failed flush
  (148-150, loss only if exit precedes next save). Remediation: fsync
  parity with workspace journal; keep dirty on failure.
- P2-16 SemanticIndex shared same-thread conn across to_thread workers →
  fail-loud ProgrammingError; writes unsynchronized (semantic_index
  70-91). Remediation: thread-local parity with stores.
- P2-17 Robots fail-open + caches allow (web.py:276-289): politeness, not
  boundary — P2 only if anyone treats it as control (document it isn't).
- P2-18 Zero-vector embedding poisoning (semantic_index.py:326-330):
  silent index corruption. Remediation: propagate error, skip item.
- P2-19 Server-failure silent local fallback → double-execution risk
  (`__main__.py:269-273`). Remediation: log + require explicit flag.
- P2-20 `entries()` returns truncated-as-complete on read failure
  (audit.py:202-204). Remediation: raise or flag partial.
- P2-21 17 failing tests on frozen HEAD (8-cluster: Ollama URL validation
  vs `"---"` placeholder, factory.py:158; + memory-cache ×2, bash-kill ×1,
  prompt-sync ×1, verification ×1, misc ×4). Remediation: fix contract or
  fixtures; gate CI green before Phase 13 implementation.
- P2-22 Ollama `StreamComplete/"complete"` ∉ guard bookkeeping (possible
  empty-turn-as-success; type-confirmation needed — UNKNOWN until
  stream_events.py check + fixture). Remediation: verify, then normalize.
- P2-23 No provider failover by design (factory single-provider).
  Remediation decision (not necessarily implementation): document or build.
- P2-24 Observability gaps: retry attempts invisible to telemetry; failure
  reasons logs-only (never ImmutableAuditTrail — covers graph.* only);
  budget consumers unanswerable from persisted state. Remediation: attempt
  dimension in telemetry + failure-reason events.

## P3

- P3-1 Chunk-deadline default divergence (90 stateless vs 30 contracts).
- P3-2 R2 retry keyed on turn `iteration`, not attempt count (semantics
  oddity; iterations ≥2 get no retry).
- P3-3 `_run_with_retry` docstring contradicts R10 (docs are wrong, code
  retries once) — merged into P1-6 remediation.
- P3-4 No Retry-After honored; fixed exponential only.
- P3-5 Redirect chain multiplies per-hop 30s budget (5 hops → 150s).
- P3-6 Ollama dead self-hash check (always-equal comparison).
- P3-7 Dead recovery branch stateless.py:529-534 (never fires; comment
  correct — do not "fix" into double-emit).
- P3-8 Telemetry/subscriber/connection/session-registry unbounded small
  maps; steering-inbox cross-thread dict (GIL-benign, drops possible).
- P3-9 memory `load_memory` unlocked vs timer thread (same family P2-15).
- P3-10 TUI WS gives up after 20 attempts (availability note).
- P3-11 Eval: 4 builtin scenarios (task/injection/bypass/cancel); metrics
  support recovery/interruption but no scenario drives them; no
  reliability/fault-injection/long-run tiers. Proposed matrix in main
  report §30.
- P3-12 LSP gate timeout degrades to allow (fail-open by documented
  design — confirm design intent, else escalate).
- P3-13 Approval UX shows salvaged args without truncation flag (feeds
  P1-1 remediation).
