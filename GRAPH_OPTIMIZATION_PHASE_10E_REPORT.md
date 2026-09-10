# Graph Optimization Report — Phase 10E (OPT-004 Fanout & Resources)

## 1–3. Fanout vs concurrency vs demand

Fanout = downstream branch count (structure). Concurrency = simultaneous-
execution bound (schedule overlap). Demand = implied workload (branches ×
retries). High fanout is not bad; lower concurrency is not automatically
equivalent — unless proven overlap-only, as below.

## 4–5. Compile-time metrics & the one safe transform

`structural_envelope` S(G)=(nodes, edges, mappings). `resource_envelope`
R(G)=(F max fanout, C effective concurrency, T retry multiplier, B budget
vector with None=+∞, X tool envelope = always UNKNOWN — never zero).
The ONLY structural mutation: lowering `policies.max_concurrency` to
min(graph, active policy, node count). Overlap-only by construction:
outputs, failures, joins, verifiers, approvals, mappings untouched.
Concurrency is never raised; budgets/retries never touched (retry policy
explicitly out of scope — changing it alters failure semantics).

## 6–8. Thresholds & excessive-but-valid fanout

Host `OptimizationContext.max_fanout` (64)/`max_branches` (128), bounded
1..1024/4096. Excess over threshold → OPT-004 advisory, never
auto-serialization. Fanout encodes independence/diversity/isolation.

## 9–10. No topology reduction, no merging

Duplicate detection is advisory-only (exact contract+edge signature match,
reported as assumed-intentional-diversity). Similarity is not proof;
agents are never merged.

## 11–14. Verifiers/approvals/joins/routers preserved

Pass mutates no nodes/edges except the policy scalar, so preservation is
structural. Join completion edges gained control-source protection (10B
fix found during 10E testing). Router branches, approval reachability,
accept/reject wiring all pinned by tests.

## 15–17. Side effects, retries, budgets

Retry×fanout envelope computed with capped arithmetic (10¹²); retry
policies never modified. Budgets never modified (lowering them could
newly fail runs — documented, advisory only). Node budgets untouched.

## 18. Caps reused

max_nodes/edges/mappings/fanout/concurrency/retries/budgets all enforced
by the existing validator pre/post pass; over-cap input → bounded REJECT.

## 19. Provider/model concurrency untouched

Runtime semaphores unmodified; graph-vs-provider concurrency distinction
preserved. Static model/provider names used only for diagnostics.

## 20. Wall-safety proof for the clamp

Reduction C→C' is applied only if no finite runtime budget exists, or the
serial total (Σ node timeouts, an upper bound on wall time at ANY
concurrency) fits it. Sufficient-not-necessary; otherwise withhold +
advisory. (An earlier total/C estimate was corrected: optimistic bounds
prove nothing.)

## 21–22. Determinism

Fixed order, sorted iteration, no IDs created, no timestamps/models.
Rerun-stable fingerprints tested.

## 23. Runtime boundary

AST-enforced: no put/generate/run/authorize/system/popen, no ToolExecutor,
no subprocess, no `open()` in optimizer modules.

## 24–25. Audit & order

No new taxonomy (rejections reuse `graph.optimization_rejected`).
Appended last in PASS_ORDER (sees post-10B/10C/10D structure); no reorder.

## 26. Monotonicity

`envelope_le` component-wise (F/C/T integers, budgets with None=+∞,
X strict-equal — UNKNOWN→UNKNOWN passes, any drift fails) enforced per
pass in the driver alongside the authority gate. S(G')≤S(G) fuzz-asserted.

## Performance (§29)

32-fan 4.0 ms (clamped 128→34) · 128-fan 10.9 ms · 512-fan 89 ms
(dominated by pre-existing validator reachability cost, not the pass) ·
19×50-mapping 0.8–6 ms. Adjacency precomputation keeps the pass linear;
a 167 ms quadratic was found and fixed during development.

## Tests

57 new (29 functional + 28 security). Full suite **880 green**, ruff
clean, eval corpus green.

## Advisory-only list

Excessive fanout, join width, retry envelopes, duplicates, withheld
clamps, unknown tool envelopes.

## Limitations

No branch merging/collapsing (unprovable); budgets read-only; provider
scheduling untouched; X permanently UNKNOWN (authority gate covers tools
instead); validator cost dominates at 500+ nodes (pre-existing).
