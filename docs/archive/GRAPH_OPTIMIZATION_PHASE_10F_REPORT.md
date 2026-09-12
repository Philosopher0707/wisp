# Graph Optimization Report — Phase 10F (Closure & Freeze)

## 1–2. Guarantee & non-guarantees

Every accepted result is valid, authority/resource-monotone, approval- and
verifier-preserving, deterministic, idempotent, fingerprint-bound. NOT
guaranteed: optimality, minimality, cross-run learning, or anything about
model behavior at runtime.

## 3–5. Invariants

Authority, structural (mappings never grow; node growth only via verifier
insertion), and resource (component-wise, UNKNOWN strict-equal) monotonicity
are enforced in the driver per pass, not just per pipeline. Approvals pinned
exactly; verifiers preserved as a superset (insertion allowed, removal never).

## 6–9. Control flow

Router/join/approval/verifier edges survive all passes (join completion is a
control source; conditional edges are never removal candidates; insertion
targets plain work nodes only). Transport metadata is annotation-only and
survives later passes (tested). Sinks/entrypoints/failure paths pinned by
fixture matrix.

## 10–13. Determinism, idempotence, fingerprints, proposals

Fixed order, sorted iteration, derived IDs, no timestamps/models.
`optimize(optimize(G)) == optimize(G)` fuzz-proven (steady-state
diagnostics stable from run 2). Fingerprint covers final graph; proposals
store it; execution recompiles identically; drift → STALE_PROPOSAL (tested
end-to-end incl. tampered-IR refusal).

## 14–15. Runtime/artifact isolation

AST-enforced across optimizer modules (no put/generate/run/authorize/
system/popen/open/ToolExecutor/Subagent). Compile+optimize+fingerprint+
propose creates no artifact files (tested). Secrets never enter the pass
(paths/sizes only).

## 16–17. Cross-pass & adversarial testing

11-combination matrix (subsets + full pipeline), hostile contexts (extra
tools/models/providers/workspace/budgets ignored), hostile IR (rejected
pre-optimizer), pass-trust test (10D re-derives post-10B, proven by
a__verify-absent/c__verify-present), 40-seed malformed fuzz, 1000-node and
10k-edge bounded rejections, deep router/join/approval/verifier structures.

## 18. Baselines

32-fan: 0.5 validate + 1.7 optimize + 0.4 revalidate ms · 128-fan:
4.3/9.1/4.3 · 512-fan: 66.8/146.6/78.5 · 19×50-mapping: 1.5 ms.
Validator reachability dominates at scale (pre-existing, out of scope).

## 19–20. Limitations & deferred

Interior handoffs get no obligation; same-model verification if host says
so; X permanently UNKNOWN; no merging/collapsing; provider scheduling
untouched. Deferred: OPT-002 interior coverage, fanout collapsing,
redundancy elimination, model/cost passes, optimizer LLM — all FUTURE,
not Phase 10.

## 21. Complete

Pipeline PLAN→COMPILE→OPTIMIZE→VALIDATE→FINGERPRINT→APPROVE→EXECUTE holds
end to end. 915 tests green, ruff clean, eval green.

## Phase 10 Freeze

Status: FROZEN

Passes:
  10B — dependency
  10C — transport
  10D — verification
  10E — resource

No additional optimization pass is part of Phase 10.
