# PHASE 13A — FAILURE TAXONOMY

17 classes. Each: detection / propagation / retryability / recovery /
durable state / user visibility / audit visibility / observability /
silent-success risk. Grades: OBSERVED / TESTED / PROVEN / INFERRED / UNKNOWN.

## MODEL_FAILURE (malformed/invalid model output)
- Detection: `_validate_tool_args` schema check (stateless.py:1843);
  bad tool-args JSON parked as `{"_raw"}` (openai.py:320-323).
- Propagation: salvaged into executable args (stateless.py:1866-1886),
  including invented default path `./output.md|.txt`.
- Retryability: no retry; salvage is terminal for the call.
- Recovery: none — write executes on truncated content.
- Durable: the partial file bytes; no truncation marker persisted.
- User/audit: approval shows salvaged args (post-salvage authorize —
  VERIFIED pre/post ordering stateless.py:1548→tool_executor.py:647);
  no audit flag that salvage occurred. **TESTED (code-path verified).**
- Silent success: YES — P1-1.

## PROVIDER_FAILURE (429/5xx/timeout/reset/auth/quota)
- Detection: transient classifiers (transport.py:443-571,
  provider_stream.py:94-100); counters `sse_lines/usable_deltas`
  (openai.py:241-244).
- Propagation: machine-readable `error(status)` → guard retry
  (openai.py:225-231 → provider_stream.py:155-167).
- Retryability: 4-deep nested (stateless R2 → guard R1 → hardened_post R3
  → client R4/R5/R6); worst ≈18 POSTs/turn-iteration. No Retry-After.
- Recovery: none — no failover (factory.py:55-141 builds exactly one
  provider). 401 surfaces immediately with hint (openai.py:211-212).
- Durable: nothing (attempts uncounted; runtime.py:561-570 counts once).
- User: turn error `recoverable=True/False`. Audit: logs only.
- Silent success: no, but cost-amplified. **TESTED (inventory).**

## TOOL_FAILURE (timeout/crash/nonzero)
- Detection: rc codes, ToolError, `asyncio.timeout` (tool_executor
  1209-1261). Timeouts leak worker threads BY DESIGN (1235-1237);
  counters `leaked_tool_threads` exist.
- Propagation: `{"status":"error"}` JSON (tool_executor.py:314+); never
  traceback to model.
- Retryability: no tool-level retry (single `await _execute_tool`,
  tool_executor.py:828) — except legacy `build_tool_message(result=None)`
  re-executes whole pipeline (906-917, comment admits stateful double-run).
- Recovery: pool exhaustion is unrecoverable short of restart (8/4 slots).
- User: error JSON. Audit: best-effort warning (852-853).
- Silent success: no — except leak is silent capacity loss. **TESTED.**

## AUTHORIZATION_FAILURE
- Detection: `authorize()` denials-only layers (tool_executor.py:647+);
  quarantine markers deny even in FULL mode.
- Propagation: denial tool-result, turn continues. Approval cancel →
  explicit denial, do-not-retry (736-746).
- Retryability: retries re-authorize (no bypass found — authority path
  untouched since Phase 12).
- Durable: tool verdicts post-hoc in AuditLog; session grants memory-only
  (`to_dict/from_dict` exist, ZERO callers — restart re-asks, fail-closed).
- Silent success: none found. **TESTED (ordering verified).**

## POLICY_FAILURE
- Detection: bundle verify → False=invalid, fail-closed (bundle.py:116).
- Silent success: no. **OBSERVED.**

## VALIDATION_FAILURE (graph DSL)
- Detection: validator absolute caps (nodes 1024, edges 8192, retries 10,
  depth default 8, validator.py:17-22). Non-finite timeouts refused.
- Silent success: no. **OBSERVED.**

## GRAPH_FAILURE (node fail/timeout/join/verifier)
- Detection: NodeStatus per node, durable per transition
  (store.py:166-178); corrupt rows discarded+re-run (executor.py:207-210).
- Propagation: FAILED sinks → RunStatus.FAILED (honest, 503-508).
- Retryability: idempotence-gated (582-588 refuse unsafe) — correct.
- Two violations: (1) join-timeout partial release records SUCCESS
  (704-718) → sink-of-joins yields SUCCEEDED (**timeout→success, P0-1,
  TESTED**); (2) CANCELLED ∈ retry set → cancelled node re-queued when
  attempts remain (types.py:60-62 + executor.py:578; run-level cancel
  unaffected — drive checks `_cancelled` first, 373-383. **TESTED**).
- Audit: `graph.retry_allowed`, `graph.cancel`, `join_released` events exist.

## WORKSPACE_FAILURE (drift/crash/partial apply)
- Detection: base-hash drift guard refuses (workspace.py:513-526, 950-955).
- Recovery: fsync'd journal + forward `recover()` — TESTED end-to-end
  with `fail_at` injector: mid-commit partial (a.txt∃ b.txt∄) →
  `recover: completed:1`, untouched files intact.
- Durable: journal files; ChangeSet memory-only (journal plan is the
  durable form).
- Silent success: no — refuses loudly. **TESTED.**

## PERSISTENCE_FAILURE (SQLite busy/corrupt/locked)
- Detection: OperationalError propagates (only `healthy()` swallows).
- Single shared file, two table namespaces (PROVEN live: one `.wisp/wisp.db`
  holds both families). WAL everywhere; sync NORMAL (UnifiedStore, OS-crash
  loss by design) vs FULL default (GraphStore).
- No app-level busy-retry; inconsistent pragmas (GraphStore lacks
  busy_timeout; GraphSecurityAuditor lacks WAL).
- `transition()` non-atomic RMW; `seq=len()` races; `Scheduler.admit`
  check-then-act (runs/store.py:119-140, scheduler.py:32-36).
- Init-failure fallback repoints to empty temp DB (store.py:50-60) —
  corrupt primary presents as empty. **TESTED (init race 1/10 trials
  `database is locked` on concurrent first-touch).**
- Silent success: audit sinks swallow (see ARTIFACT/AUDIT-adjacent D4/D5).

## ARTIFACT_FAILURE (missing/corrupt/traversal)
- Detection: hash mismatch rejected (artifacts.py:103-109); content-addressed
  id + `open("x")` write-if-absent (77-81) → idempotent.
- Caps: 4MB/artifact, 4096/run (40-41, 68-74). **OBSERVED.**

## NETWORK_FAILURE — see PROVIDER_FAILURE. No Retry-After honored anywhere.

## TIMEOUT
- 20-entry inventory (§9 main report). Coherent pattern: cooperative cancel
  of async work; blocking threads (T4), container payloads (T6-Docker),
  hook children (T14-async) LEAK. Join-timeout → SUCCESS (P0-1).
- Deadline knobs diverge: chunk 90 (stateless) vs canonical 30 (contracts).
  **TESTED (inventory + code).**

## CANCELLATION
- Cooperative everywhere; bounded joins (2s/3s); orphans tracked, never
  awaited past deadline. Approval-cancel is verdict (clean).
- Cancel→SUCCESS: only via join-timeout (P0-1). Cancel→retry: node-level
  (P1-2). Background cancel reversible via `send()` (RUNNING again) —
  cancel not terminal-durable (P2). **TESTED.**

## PROCESS_CRASH
- Graph: run row + node rows + checkpoint durable; budget/attempt-counter/
  live scheduler memory-only → resume re-derives (attempts from rows).
- Workspace: forward recovery PROVEN via injector.
- Session transcript: persisted per turn; truncation/retry notices ephemeral
  (runtime.py:443-444) → resume sees partial as complete (P1-3).
- memory.json ≤2s loss (debounce) + no fsync; corrupt→empty. **TESTED.**

## CORRUPTION
- DB: no checksums/integrity pragma; corrupt trust file → entries destroyed
  then overwritten (trust.py:88-92, P1-5); corrupt checkpoint → resume
  refused (fail-closed, correct); corrupt journal → dropped (safe: drop only
  valid pre-first-replace, PROVEN by write ordering 532→546+fsync).
- Audit JSONL torn last line possible; `verify()` detects, nothing
  auto-verifies on write path. **TESTED (H2 chain fork under concurrency).**

## RESOURCE_EXHAUSTION
- Caps present: graph absolutes, artifact 4MB/4096, changeset 100 files/1MB,
  web 2MB body/5 redirects, bash output cap, context 6K tokens, bg 8
  running/50 finished, telemetry dual-bounded, checkpoint ring 20/10MB.
- Unbounded: module caches (`_CACHE` code_index, `_store_cache`,
  `_flock_cache`, provider-models cache, checkpoint registry across
  workspaces, session/event/trace tables with no retention, telemetry
  per-agent dicts without prune auto-call). Thread-local conns never
  closed (GraphStore: no `close()` at all). **OBSERVED + TESTED (H1 clean).**

## INTERNAL_BUG — see RISK_REGISTER P0/P1 list.
