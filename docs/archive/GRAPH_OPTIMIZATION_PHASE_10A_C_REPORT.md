# Graph Optimization Report (Phase 10A–10C)

## 10A — Framework

`wisp/graph/optimizer.py`: pass functions (`PassFn`), `PassResult`,
`OptimizationContext` (policy, inline threshold 1KB–4MB, host size hints —
no providers/I/O/credentials), fixed `PASS_ORDER`, single pipeline run
(`MAX_PASSES = 1`, no fixed-point loop). Driver: validate → passes →
per-pass `optimized_authority ⊆ original_authority` gate (violations drop
that pass's output, keep prior) → revalidate → UNCHANGED/OPTIMIZED/REJECTED.
No new graph type. New audit event (justified): `graph.optimization_rejected`.

## 10B — Dependency semantics (OPT-001)

A real dependency = mapping, condition, control-decision source
(router/verifier/gate), join/approval/function/gate target signal, declared
cycle membership, or side-effect-unsafe endpoint. Conservatively kept:
unmapped edges into signal targets, from control sources, in cycles, at
unsafe endpoints, and sole-incoming edges (removal would strand the node —
non-entry roots with no preds never run). Entrypoint incoming is runtime-dead
but unreachable in valid acyclic graphs, so the rule rarely fires (pinned).

Example: `A→B, C→B, A→C` (unmapped) → drops one edge to B, keeps one;
`A→B` mapped → preserved; `gen→ver→publish(accept)` → preserved.

## 10C — Artifact/context semantics (OPT-003)

Compile-time boundary (hard): the pass NEVER executes nodes, sees runtime
values, calls `ArtifactStore.put()`, writes files, or hashes content
(AST-test enforced). It records transport CONTRACTS per mapping —
INLINE (default/unknown-size/control/over-limit) vs ARTIFACT (host-hinted
size > threshold) — plus consumer-schema-declared flags, in proposal
metadata. Runtime `ArtifactStore` (scrub/size/containment) stays solely
authoritative; runtime already resolves `artifact://` values through
ordinary mappings, so no Graph schema change was needed. Unknown size →
INLINE + silent (never speculate).

## Security

`optimized_authority ⊆ original_authority` enforced mechanically on
tools/models/providers/workspace/budgets/retries/concurrency/nodes/approvals
(fuzz-tested). Approval nodes can never be added or removed. Optimizer
touches no artifact bytes, so no secret channel is possible (hints are ints;
provenance bounded, paths only). Red-team: expansion attempts dropped,
malicious hints ignored, cycle/route/approval preservation tested.

## Fingerprinting

compile → optimize → revalidate → fingerprint → proposal stores the FINAL
hash + narrowed IR; `execute_proposal` recompiles + re-optimizes
deterministically and rejects drift (`STALE_PROPOSAL`). Unchanged graphs
keep identical fingerprints.

## Performance

32-node fan: 4.0 ms · 128-node chain: 24.7 ms · 950-mapping analysis:
6.0 ms. Local, linear-ish, no model calls.

## Tests

37 optimizer tests (framework 9, dependency 13, transport 9, integration 3,
red-team 3) + full suite **785 green**, ruff clean, eval corpus green.

## Report questions (condensed)

1. Real dependency = mapping/condition/control-signal/cycle/side-effect.
2. Conservative: signals, cycles, unsafe endpoints, sole edges, unknown sizes.
3. Edge removed only if all 9 checklist items hold + revalidation passes.
4. Large = host-hinted bytes > 64 KB default (1KB–4MB bounded).
5. Safe iff control-free, under artifact hard limit, schema noted; runtime decides.
6. Guarantees preserved: runtime scrub/size/containment untouched; pass sees paths only.
7–11. No: authority/budgets/workspace/approval/secrets all pinned by gate + tests.
12–14. Yes: revalidated; fingerprint post-optimization; approved == executed.

## Left for later

OPT-002 verification optimization, OPT-004 fanout/resource optimization
(plus redundancy, model/cost, LLM passes) — diagnostics emitted, not implemented.
