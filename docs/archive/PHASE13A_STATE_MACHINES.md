# PHASE 13A — STATE MACHINES

Skipped terminal states in `[]` where code was read. Grades per §40.

## Agent (turn): CREATED → RUNNING → SUCCESS / FAILURE / CANCELLED / TIMEOUT
- Predecessors/successors valid: turn wall-clock `async with timeout`
  (stateless.py:150,221-235) raises TimeoutError → error+done events.
  SIGINT → task.cancel → flush + resume hint (entry.py:438-441,485-498);
  no success recorded. **TESTED (code).**
- Durable: session persisted per turn (runtime.py:528-534,601-605);
  per-turn token telemetry memory-only (566-570). UNKNOWN: none.
- Impossible: SUCCESS after cancel — none found (C1-C3 clean).

## Graph: CREATED → RUNNING → PAUSED / CANCELLED / FAILED / COMPLETED
- `run()` → `set_run_status` durable each transition (store.py:149-152;
  terminal write executor.py:509). Cancel: `_cancelled` + CANCELLED +
  event + audit (373-382). Budget: in-flight `t.cancel()` + FAILED/
  budget_exceeded (384-398).
- PAUSED (approval): checkpoint stores statuses/taken/completed but NOT the
  decision (438-439, snapshot 940-943); decision durable only as
  audit/event side-effect. Resume re-derives → fail-closed re-ask.
  INFERRED (no crash-between-grant-and-execute test). Acceptable.
- Violation: join-timeout partial release → SUCCESS (704-718) → COMPLETED
  with `timed_out:True` branches unsettled. Impossible state made possible:
  COMPLETED ∧ ¬all-branches-settled. **TESTED. P0-1.**

## Node: PENDING → RUNNING → SUCCESS / FAILURE / TIMEOUT / CANCELLED / SKIPPED
- Launch/settle/skip rows durable (294-295, 655-658, 423-425).
- Stale generations dropped + marked superseded/cancelled (341-357) —
  duplicate completion handled. **OBSERVED.**
- Violation: CANCELLED re-queued to PENDING when attempts remain (578 +
  types.py:60-62). Terminality lost at node level. **TESTED. P1-2.**
- TIMEOUT without `retry_on_timeout` (default False) refused retry (579-581)
  — correct. TIMEOUT nodes park; non-idempotent interrupted nodes park as
  CANCELLED, never auto-resumed (212-219) — correct.

## Workspace: SNAPSHOT → ISOLATED → CHANGED → MERGED → APPLIED
- Every transition durable (journal, fsync before first replace 591-600).
- Invalid: APPLIED without journal — impossible by construction (journal
  precedes replace; PROVEN ordering).
- Crash in any arrow → `recover()` forward-completes or refuses rollback
  on post-state mismatch (760-790). **TESTED (injector, both arrows).**
- No inter-process lock; concurrency control is refuse-on-drift (513-526)
  — refusal rather than merge. External edit/delete/rename/symlink/
  permission change during op → EXTERNAL_MODIFICATION/STALE, never silent
  partial. **TESTED (code + existing 105 workspace tests incl. fault
  injection per health report).**

## RunStore transitions (background agents)
- `transition()` read-modify-write non-atomic (runs/store.py:119-140);
  concurrent transitioners duplicate `seq` / interleave silently.
  Impossible state made possible: two identical seq rows; status advanced
  with no history row (crash between UPDATE and INSERT). **TESTED (code).
  P2-3.** Leases are the one atomic op (conditional UPDATE + rowcount,
  infra/store.py:684-695). `recover()`: stale RUNNING→PAUSED, others→
  CANCELLED, never success (background.py:216-243) — correct.

## Liveness (graph)
- Scheduler: ready-queue + 0.5s slice-wait (311-315) so long node timeout
  can't delay cancel; cycle replay bounded by CycleSpec.max_iterations
  (882-887); retry bounded by max_attempts + absolute 10.
- Dead-end audit: approval deny → terminal CANCELLED (448-458, no dead end);
  join with zero branches: `branch` empty + no timeout → falls to
  `evaluate_join` — ALL-policy on empty branch: VERIFIED? UNKNOWN — not
  exercised. Verifier-RETRY bounded by cycle budget (orthogonal but bounded).
- No permanently-pending path found in code read; 16-way concurrent-run
  harness: 16/16 `succeeded`. **TESTED (harness) + INFERRED (review).**
- Permanent-pending risk UNKNOWN for: resume+changed graph/policy, route+
  stale result, join+predecessor-skip — listed as Phase 13 test gates, not
  findings.
