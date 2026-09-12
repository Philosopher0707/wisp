# PHASE 13 G1C — GRAPH TERMINALITY & JOIN HONESTY

## Original P0
13A C8: join-timeout partial release recorded SUCCESS; sinks of such joins
finalized SUCCEEDED. G1C investigation found the precise shape: the timeout
branch (`executor.py` `_resolve_join`) wrote `SUCCESS` + `timed_out: True`
while branches were unsettled, and the final sink rule counted it as
success. Empirically, scheduler readiness presents only settled branches
to non-streaming joins (each join settles on its first evaluation —
probed: exactly one `_resolve_join` call per ALL/ANY/BEST_EFFORT join),
so the branch is defense-in-depth today; the final predicate (§9) is the
live guard (it also closes pre-fix SUCCESS+timed_out rows on resume).

## Terminality Model
Run: QUEUED → RUNNING → SUCCEEDED | FAILED | CANCELLED (+PAUSED/
AWAITING_APPROVAL waypoints). No new states: join timeout maps to node
TIMEOUT → run FAILED with explicit timeout evidence. Node: PENDING →
RUNNING → SUCCESS | FAILURE | TIMEOUT | CANCELLED | SKIPPED. TIMEOUT ∈
FAILED_NODE_STATUSES (pre-existing) routes timeout joins to FAILED.

## Join Semantics
| Policy | Required | Success | Failure | Timeout | Cancelled branch |
|---|---|---|---|---|---|
| ALL | every branch succeeds | all SUCCESS | any settled non-success → join FAILURE | TIMEOUT | join FAILURE (contained) |
| ANY | first SETTLED (any status — defined partial) | first settled (failures recorded in output) | n/a (settled = released) | TIMEOUT | released if first settled; else FAILURE path |
| QUORUM/MIN_SUCCESS | param successes | quorum reached | all settled below quorum → FAILURE | TIMEOUT | counts against quorum |
| BEST_EFFORT | none (explicit partial) | all settled ("best_effort: partial") | n/a | TIMEOUT | tolerated, recorded |
| STREAMING | none (no barrier) | first settled | n/a | TIMEOUT | tolerated |
| EMPTY (no preds) | — | — | FAILURE JOIN_UNSATISFIED (resolved: validator allows, executor refuses) | n/a | n/a |

ANY/BEST_EFFORT/STREAMING partiality is DEFINED (reason strings say so),
preserved, and pinned — not redesigned (§29 DSL guardrail).

## Timeout Semantics
Timeout authority rule (§7): the drive is single-threaded; `settle_one`
drains completions before each join evaluation, so a completion settled
before the deadline observation satisfies the join, otherwise timeout
wins. Deterministic — no scheduling/thread/DB order dependence.
Timeout result preserves: join_id, deadline, settled/unsettled branch
lists, branch states (successes/failures maps), reason
(`JOIN_TIMEOUT`), event `graph.join_released` with
`reason="timeout: partial (not released)"`. Partial branch outputs stay
in results/artifacts (never promoted).

## Race Semantics
Completion-vs-timeout: settled-before-observation wins (rule above, both
orderings unit-tested). Completion-vs-cancel: cancel-first → CANCELLED;
complete-first → honest SUCCEEDED (25 iterations per ordering,
event-orchestrated). Completion-vs-failure: failure settles → policy
decides (ALL→FAILURE pinned). Late branch settle post-terminal: in-flight
tasks are destroyed at break; rows never rewritten; run row immutable
except explicit resume-as-new-attempt.

## Empty Join
Resolved: single JOIN node, zero preds → FAILURE JOIN_UNSATISFIED,
run FAILED. Tested + documented (not a new meaning — the pre-existing
unsatisfied-join path).

## Skipped/Cancelled/Failed Branches
- Untaken conditional lane → SKIPPED benign → downstream skips → run
  SUCCEEDS honestly (router test pins; §16 verified, router untouched).
- ALL + skipped/failed/cancelled branch → join FAILURE → run FAILED.
- Cancelled sink (non-benign) → run CANCELLED (pre-existing rule kept).
- SKIPPED ≠ SUCCESS anywhere in joins (only the final benign-skip rule
  excuses non-execution, unchanged).

## Late Completion
Terminal runs cannot be rewritten: single `_final` per drive; `cancel()`
refuses terminal overwrite (pinned); late `put_node_run` rows don't touch
the run row (pinned); resume re-runs non-success (never trusts them) and
restores only SUCCESS rows — except SUCCESS+timed_out rows, which the §9
guard forces to FAILED (tested end-to-end via crafted row + resume).

## Resume
Resume = new execution attempt under the same run_id (existing semantic,
documented, not redesigned): statuses RUNNING for terminal FAILED runs,
non-success rows re-run (idempotent→PENDING, else CANCELLED-park),
SUCCESS rows restore, checkpoint sequence continues, `graph.resumed`
event marks the boundary. Historical terminal truth lives in
events/audit (`graph.run_terminated` rows persist). Resume never
manufactures SUCCESS from unsettled branches (they were never success
rows) and never resurrects success over the timed_out guard.

## Persistence
Join decisions persist via checkpoint snapshots (join settles write
memory + events; launched nodes also write rows — pre-existing shape,
not redesigned). Run row written once per terminal outcome; no path
rewrites it except resume-as-new-attempt and the guarded cancel path.
Join-decide → run-persist are separate statements (no txn): a crash
between them leaves run RUNNING + join TIMEOUT persisted → resume
recomputes FAILED. No success claim without durable support (the only
success-writing path is the guarded final predicate).

## Performance
Fanout-join medians before→after: 8: 13.4→15.3ms, 32: 49.6→42.0ms,
62: 75.1→101.0ms (run variance dominates; branch scan stays O(preds),
no new O(N²)). Timeout-branch microbench 200×: 50.0→41.6ms. No
pathology; correctness dominates.

## Deferred Findings
- P1-2 cancelled-node retry (untouched; verified it cannot rewrite
  terminal results — retries happen pre-terminal only).
- P1-6 subagent retry multiplication (untouched).
- Audit durability of terminality decisions (G2; events/audit calls
  preserved as-is).
- In-flight branch tasks abandoned at terminal break (pre-existing task
  lifecycle; rows may read "running" beside a terminal run — observable,
  not success).
- Resume trusts SUCCESS rows (corrupt-row forgery is G2
  persistence/corruption territory; timed_out rows are now rejected).
- ANY/BEST_EFFORT/STREAMING licensed partiality (defined, pinned).

## Tests
- `pytest tests/test_graph_terminality.py` — 18 passed (P0 unit, gating
  pin, live branch outcomes, empty/router/skip, cancel races ×27,
  resume ×3, monotonicity ×2, bulk 200 + ordering matrix)
- Graph suites (engine/invariants/planner/proposals/optimizer/state/
  nodes/agentic) — 232 passed
- `pytest tests/reliability/` — 46 passed
- Full suite — 5464 passed / 17 failed (G0-known set) / 2 xfailed
- `ruff check` touched files — clean
