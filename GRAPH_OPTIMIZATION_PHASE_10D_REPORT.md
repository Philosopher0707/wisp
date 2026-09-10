# Graph Optimization Report — Phase 10D (OPT-002 Verification)

## Obligation semantics (Q1–Q4)

**O-TERM**: for every AGENT producer P and sink S (S ≠ P, S an
agent/work node — never verifier/approval/gate) with S reachable from P,
the run result is unprotected. Derived purely from node types +
reachability; no invented policy. FUNCTION/JOIN/ROUTER sinks raise
obligations (insertion-gated to AGENT/FUNCTION only); VERIFIER/APPROVAL/
GATE sinks raise none (check downstream already). A verifier 500 lines
away "existing somewhere" proves nothing — satisfaction requires proof:

- **Verifier gate**: V is VERIFIER type, directly consumes P's output
  (direct edge in), reaches S, and S is unreachable from P without V's
  accept-family edges (post-dominance over control flow).
- **Human gate**: every P⇝S path crosses an APPROVAL node.
- Insufficient: unrelated-path verifiers, upstream verifiers without
  accept-gating, downstream-of-sink verifiers, router-hidden verifiers,
  fake verifiers with no accept edge.

## Insertion contract (Q5–Q11)

Insert P→V*→S (accept) replacing the choke edge ONLY when: exactly one
direct unconditional unmapped P→S edge exists (mapped = declared dataflow
→ advisory); S is AGENT/FUNCTION, not entrypoint, with no other
unconditional preds; S⇝P unreachable (no cycle); host `verifier_profile`
complete (tools+model+provider) and ⊆ graph+policy authority; id
`{P}__verify` free. V* contract: profile tools/model/provider, timeout
min(300, producer), retries 1, idempotent, verdict-shaped description.
Approval nodes never touched; ambiguous cases stay advisory. V* input is
P's whole-output embedding (same incidental channel the removed edge had);
declared mapping semantics are never altered.

## Runtime separation (Q12)

Pass is pure topology + contracts: AST-test bans put/generate/run/
execute/authorize/system/popen and ToolExecutor/Subagent/provider
machinery. Inserted V* is a definition; execution, evidence, and ALLOW
happen only at runtime. No artifact I/O (paths-only reasoning inherited
from 10C).

## Fingerprint/proposal (Q13–Q14)

Existing pipeline unchanged: insertion flows through revalidate →
fingerprint → proposal hash → pinned execution → STALE_PROPOSAL on drift.
No-op preserves fingerprint (tested); insertion changes it (tested).

## Invariants tested (Q15–Q16)

`optimized_authority ⊆ original_authority` (framework gate + fuzz);
verifiers/approvals preserved exactly (fuzz-asserted); determinism
(rerun-stable, fixed IDs); validity (revalidate). Advisory-only: missing
profile, mapped edges, multi-pred sinks, cycle risk, entrypoint sinks,
join/router sinks, collisions, ambient multi-hop obligations.

## Performance (Q17)

100-node chain + full analysis: 5.9 ms. Reachability-based (no path
enumeration); single pipeline run; diagnostics bounded.

## Limitations (Q18)

Interior (non-sink) handoffs get no obligation; same-model verification
possible when the host profile says so (flagged in diagnostics);
FUNCTION-sink obligations assume gateability; quality_score's OPT-002
heuristic unchanged (advisory). OPT-004+ untouched.
