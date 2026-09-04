# ADR: Durable Background-Run Semantics

## Status: Accepted (M1b)

## Context

Background agents (`BackgroundAgentManager`) and subagent orchestration hold
state in process memory; sessions persist via `UnifiedStore` (SQLite) but
run lifecycle, scheduling, leases, and recovery rules are implicit. Phase 2
needs a killed CLI to resume non-destructive work from its last durable
checkpoint without repeating applied writes or losing approval decisions.

## Decision

- **One `Run` lifecycle:** `queued → planning → running →
  awaiting_approval → paused → succeeded | failed | cancelled`, recorded as
  append-only `Transition` rows (the M1a `Transition` envelope is the row
  shape). Restart rules per state: `queued/planning` re-enter freely;
  `running` resumes from last checkpoint with idempotency keys;
  `awaiting_approval` re-presents the recorded decision (never auto-denies,
  never auto-approves); `paused` waits for explicit resume; terminal states
  are immutable.
- **`RunStore` abstraction:** SQLite implementation first (over the existing
  `UnifiedStore`), Postgres-compatible later. No process-memory registry is
  ever the source of truth — the manager becomes a cache over the store.
- **Scheduler:** bounded local queue with concurrency/resource limits,
  cancellation propagation, and lease/heartbeat so abandoned work is
  reclaimed, not stuck.
- **Idempotency:** every mutating step carries an idempotency key (the M1a
  `ToolRequest.idempotency_key` field now has its supplier); resume replays
  decisions, not effects — a restarted process never repeats an applied
  write.
- **Compensation:** file edits keep patch/diff records with rollback
  previews; git actions record branch/worktree identity; external tools
  declare reversibility in their schemas.
- **Subagent worktrees:** creation/cleanup/recovery are transitions on the
  parent run, making orphaned worktrees visible and reclaimable.

## Consequences

- M3 implements `RunStore` + scheduler + sandbox behind these semantics;
  the approval-decision durability rule composes with the M1a cancel-as-
  recorded-denial design (a re-presented denial is data, not a new prompt).
- Long-running work gets bounded resources and visible cancellation by
  construction, not convention.
