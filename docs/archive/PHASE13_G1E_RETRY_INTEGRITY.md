# PHASE 13 G1E — RETRY AMPLIFICATION & IDEMPOTENCY INTEGRITY

## Original P1-6
Nested subagent retries multiplied without shared accounting: guarded 3×
ignored `retry_count`, timeout/validation each fired once per contract
regardless of `max_retries`, `_run_with_retry` iterations re-armed inner
retries, map_reduce retried with inherited-but-unconsumed counts.
Measured baseline: 1 logical task → 3 agent attempts (guarded layer
alone); composed worst case ~12 (13A estimate, confirmed by layer audit:
3 guarded × 2 timeout × provider ops beneath).

## Retry Taxonomy
| Layer | Mechanism | Bound | Owner |
|---|---|---|---|
| A transport | hardened_post/get `max_attempts=3` | 3 ops, backoff+jitter | transport (op-class) |
| B provider-stream | guard `WISP_STREAM_ATTEMPTS=3`, chunk/first-token deadlines | 3 ops | provider op (G1B authoritative) |
| B turn-legacy | stateless transient refetch, iteration<2 | 2 fetches, no tool re-exec | provider op |
| B structured JSON | enforce_json `max_retries=2` | 3 generations | provider op |
| C tool | worktree git remove 5× (idempotent), web proxy fallback (alt channel) | bounded, no blind retry | tool (no generic tool retry exists) |
| D agent/parallel-guard | `_guarded` 1+remaining | shared budget | logical (now) |
| D agent/timeout | run() ×1.5 once | shared budget + safety | logical (now) |
| D agent/validation | `_validate_output` repair once | shared budget + safety | logical (now) |
| D agent/outer | `_run_with_retry` 1+max | shared budget, result-derived | logical (now) |
| D pattern | map_reduce retry_failed round | inherits stamped count | logical (now) |
| E graph | node RetryPolicy (default 1; abs cap 10), idempotency-gated, audited | bounded, refuses unsafe | node (unchanged) |
| F human | resume = new contracts; operator retry explicit | explicit | operator |

Attempts (logical re-executions, shared budget) vs operations (bounded
transport/provider generations, separate per-layer caps): both bounded,
counted separately, neither mints the other.

## Ownership
Human/operator → logical task (`max_retries`, explicit per spawn/fanout
call site) → graph/node (`RetryPolicy`, unchanged) → agent attempt
(`run()`, consumes) → provider operations (guard/transport caps,
unchanged). A lower layer restarts only its own operation; logical
re-execution always consumes the task's budget. run_parallel fanout
contracts now carry explicit `max_retries=2` (was implicit 3× on
default-0); spawn paths already did.

## Budget
`SubagentContract.retry_count` (consumed) / `max_retries` (cap, clamped
[0, 5] via MAX_SUBAGENT_RETRIES, strings/None coerce fail-closed to 0).
Every re-execution reconstructs with count+1 (`_consume_budget`) or an
explicit count (`_with_count`); outer loops derive the next count from
the stamped result (`result.retry_count`), so inner consumption is
observed. Monotonic: counts only grow, caps never raised, children
inherit. Total executions per logical task ≤ 1 + max_retries (plus
documented provider-op multipliers beneath, which are operations).

## Idempotency
Automatic re-execution requires: all contract tools READ, or effective
worktree isolation (fresh per attempt, patch applied once on success),
or zero mutating tool calls in the attempt's own log (unknown tools
fail closed as EXEC). Shared-workspace outcome evidence is blind, so
mutating-capable shared tasks are refused automatic retry (operator
retry remains). `ToolRisk` reused — no second taxonomy. Authorization
stays independent (a separate, still-mandatory gate).

## Nested Retry
`_guarded` × `run()` × `_run_with_retry` × map_reduce now share one
counter: measured nested worst case 4 → bounded by 1+max (test pins
3 executions at max 2 with inner timeout consumption observed).
map_reduce derives retry counts from stamped results; exhausted budget
skips the retry round.

## G1A/G1B/G1C/G1D Interaction
Proven by suite, not by assertion: G1D gate tests green (refusals still
refuse; complete rounds execute); G1C terminality suite green; G1B
stream/contract suites green; workspace/journal untouched (no diff);
provider completion semantics untouched (no diff); graph untouched
(no diff).

## Cancellation
CancelledError propagates through every layer (verified by test at the
parallel-guard level); cancelled/denied RESULT objects never auto-retry
(`_is_denial` + "cancell" guards in `_run_with_retry`; transient-only
predicate in `_guarded`). G1C cancellation invariant preserved.

## Resume
Resume constructs fresh contracts (explicit new attempts); persistence
is append-only telemetry carrying no budget authority (pinned: records
contain no retry_count/max_retries keys); restart cannot inflate budget.

## Crash
Attempts are in-memory; process death ends them. No parallel retry
persistence invented (G2 if ever needed). Worktree orphan sweep
(pre-existing) bounds leaked isolation dirs.

## Observability
`result.retry_count` stamped with consumed budget at every layer;
`TASK_RETRY` events now emitted for timeout + validation paths (owner,
attempt, backoff, reason in logs); existing guarded emits kept.
In-memory/trace only (G2 for durable audit).

## Remaining Limitations
- Outer-layer safety for isolated tasks uses the declared flag +
  creation-failure memo (nearly exact; run()-internal gates are exact).
- Foreign (non-orchestrated) SubagentResults default retry-permissive
  for the safety check (legacy behavior preserved).
- Graph×subagent product bounds are declared, not plumbed (node ≤10
  abs × task 1+max; each layer bounded and audited).
- Turn-legacy/provider-op multipliers unchanged (bounded, documented).
- Two test contracts gained explicit `max_retries=1` (behavior they
  always assumed, now declared).
