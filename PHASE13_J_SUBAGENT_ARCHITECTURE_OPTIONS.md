# PHASE 13-J — ARCHITECTURE OPTIONS (A/B/C) FROM EVIDENCE

## Architecture A — Repair current fanout

Keep: contracts, role allowlists, shared retry budget, background manager, BOTH join semantics, per-field authority split, graph runtime untouched.
Fix: nullable alignment + declared bounded keys + additionalProperties (§J-C1/C2) + validate-before-Gate1 + audit (§J-V1/V2) + branch/task caps (§J-CC1/CC2, §J-C3/C4) + distinct digests (§J5) + joined cancels (§J-X1/X2) + tie-breaker budgeting (§J-R2) + fanout-path timeout clamp (§J-T1).

| Dimension | Assessment |
|---|---|
| correctness | Fixes all P1s within existing seams; each item independently testable vs J diagnostics |
| complexity | ~9 small diffs, no new abstractions |
| observability | Improves (audited validation, distinct digests) |
| security | Closes J-A1/J-A2 without touching gates |
| retry integrity | Extends G1E budget to tie-breaker; physical layer unchanged (by design) |
| context efficiency | Unchanged (acceptable: bounded return path) |
| cancellation | Bounded joins replace fire-and-forget |
| persistence | Unchanged (sufficient) |
| testability | J diagnostics flip from pin-broken to pin-fixed |
| graph compat | Zero touch |
| migration cost | Lowest: no migration, only repairs |

## Architecture B — Thin worker runtime

Workers as bounded execution contexts (objective + refs + permissions + budget + timeout → artifact). Verdict from evidence: THIS LARGELY EXISTS — fresh sessions, role prompts, budgets, 8k caps, artifact-ish summaries. Delta vs current = remove child conversational loop + return artifact handles instead of digest strings. Gain: smaller context, cleaner join. Cost: new worker protocol, new result type, migration of 4 orchestration patterns + graph bridge; LOSES the generality that lets researcher children plan multi-step investigations (I0 incident legitimately used researcher planning). Evidence does not justify the cost: no failure traces to "workers converse too much."

## Architecture C — Graph-native subagents

Fanout as typed graph nodes with JoinPolicy joins. Verdict from evidence: REJECT. Fanout join needs are ALL (blocking) and poll-BEST_EFFORT (background) — both already correct; JoinPolicy adds expressive power with zero motivating failure. Cost: planner node types, DSL, migration of orchestrator + patterns + bridge, G1C coupling risk. The graph runtime is healthy precisely because it is separate; merging would entangle two working systems.

## Selection

A, on evidence: every P1/P2 maps to a bounded repair; no abstraction is wrong; B's delta is unmotivated by failures; C solves problems the traces do not contain. D (remove fanout) rejected: legitimate parallel research exists and I2 partitioning addresses visibility without amputation.
