# PHASE 13-J — SUBAGENT FAILURE TAXONOMY

Each entry: instance → mechanism → severity → confidence. All PROVEN by cited code unless marked.

## Contract

- J-C1 nullable mismatch (`model`/`timeout_seconds`/`max_iterations`): schema rejects, runtime accepts. P1. PROVEN (registry.py:265-268 vs tool_executor.py:1930-1933, _runner.py:731).
- J-C2 undeclared model-influenceable keys (`auto_approve`, `auto_retry`): read but unschemaed, no `additionalProperties` guard. P1. PROVEN.
- J-C3 `hit_iteration_limit` defined, never written. P3. PROVEN (task.py:226, zero writers).
- J-C4 budget-exhaust break returns success:True path. P2. PROVEN (_runner.py:538-544, 577-584).

## Validation

- J-V1 validation after Gate1 approval (global pipeline). P1. PROVEN + test-pinned.
- J-V2 validation failures unaudited (executor never invoked; 1743-path has no AuditLog write). P3. PROVEN by absence.
- J-V3 salvage/defaulting mutates args pre-validation (`write_file`); approval-modified args never re-validated. P2. PROVEN mechanism (stateless.py:2063-2085, executor:789-791).

## Planning

- J-P1 over-trigger on analysis tasks (surface + task-shape). P2. PROVEN (I0 A/B).
- J-P2 4-sections→3-tasks decomposition unexplained by host (model-authored). Informational. PROVEN (H0/I0).

## Capability

- J-CAP1 12 delegation tools always visible to main agent. P2. PROVEN (I1 matrix).
- J-CAP2 `spawn_background`/`subagent_*`/`orchestrate_*` survive auto_edit stripping that removes only `spawn`/`fanout`. P2. PROVEN (policy_engine.py:289-294).
- J-CAP3 generalist(all)+full recurses unbounded by role layer (depth only). P2. PROVEN.

## Authority

- J-A1 child `auto_approve=true` settable via unschemaed key → child bash skips approval. P1. PROVEN chain (no exploitation evidence).
- J-A2 `max_retries` derivable from model-sent `auto_retry` (0 disables, default mints 2). P2. PROVEN.
- J-A3 everything else correctly split (workspace/tools/depth/budgets host; text/role/timeout-proposal model). Healthy. PROVEN.

## Context

- J-CX1 N× replication of base+role+tools+env per worker; fresh history/memory/map skipped (good). P2 cost. PROVEN.
- J-CX2 partitioner (`>10 msgs`) never fires for fresh children. Informational. PROVEN.
- J-CX3 return path capped (8k/2k/240 chars) — bounded. Healthy. PROVEN.

## Concurrency

- J-CC1 `max_subagent_branching` dead on live paths. P2. PROVEN.
- J-CC2 no `len(tasks)` cap; no `max_concurrent` maximum. P2. PROVEN (test-pinned).
- J-CC3 background MAX_RUNNING=8 refusal; semaphore throttles (not caps). Healthy. PROVEN.

## Retry

- J-R1 shared logical budget real (G1E): 1+min(max_retries,5); fanout default 3 executions. Healthy. PROVEN.
- J-R2 vote tie-breaker + reducer unbudgeted (+1 each). P2. PROVEN.
- J-R3 graph-outer × subagent-inner stacking unbudgeted. P2. PROVEN.
- J-R4 physical attempts (~stream3×http3×iters, ≤405/child coder-default) unbudgeted by design. P2 cost. PROVEN mechanism.

## Timeout

- J-T1 fanout-path timeout unclamped (`max_subagent_timeout` only on guard APIs). P2. PROVEN.
- J-T2 timeout→failed conflation in digests (flag preserved underneath). P2. PROVEN.
- J-T3 L5a ×1.5 timeout retry (1 extra, budgeted). Healthy. PROVEN.

## Cancellation

- J-X1 exec_task.cancel without await (blocking join weakened). P2. PROVEN.
- J-X2 background cancel stamps without join (race window). P2. PROVEN.
- J-X3 2s-reap orphans stay tracked (logged, bounded). P3. PROVEN.
- J-X4 tool threads leak by design (counted). P3 (documented design). PROVEN.

## Persistence

- J-S1 background entries persisted + pruned at 50 (post-prune → Unknown agent_id). P3. PROVEN.
- J-S2 persistence failures counted, never surfaced. P3. PROVEN.

## Join

- J-J1 blocking ALL-barrier, no timeout (slowest gates). P2. PROVEN.
- J-J2 background poll-join BEST_EFFORT-ish, `still_running` honest. Healthy. PROVEN.
- J-J3 cancelled→failed everywhere (no cancelled-as-success). Healthy. PROVEN.

## Result integrity

- J-RI1 no truncation-to-success on any path; flags survive all truncations. Healthy. PROVEN.
- J-RI2 evidence loss via 240-char digests (synthesis from stubs). P2. PROVEN mechanism.

## Observability

- J-O1 per-round payload bytes unlogged (H1 standing null). P2. PROVEN by absence.
- J-O2 provider/model selection precedence undocumented (spec→None→parent; role/local never consulted on fanout). P3. PROVEN.

## UX

- J-U1 invalid proposals interrupt via approval before dying. P1 (folded into J-V1).
- J-U2 refusal hints advertise full mode ("switch to full mode"). P3. PROVEN (stateless.py:421-422).
