# Graph Compiler Report (Phase 9)

## Architecture

Intent → `plan_graph` (model, untrusted) → IR dict → `compile_ir`
(deterministic, zero model calls) → `Graph` → `validate_graph` → policy
narrowing (`narrow_ir`) → proposal (`APPROVAL_REQUIRED`) → `execute_proposal`
(fingerprint-pinned, `approve is True`) → `GraphExecutor` →
`ToolExecutor → authorize()`.

## Graph IR

No new type system: the IR is the strict dict the DSL already parses, plus
`{objective, execution_shape: SINGLE_AGENT|GRAPH, graph: {...}}`.
Strictness comes free from `dsl.graph_from_dict` (typed coercion,
path-named errors, size caps). IR hashes (`ir_hash`) identify proposals;
the COMPILED graph fingerprint is the security identity.

## Planner

`plan_graph(objective, provider)`: fixed system prompt (no authority,
artifact refs, bounded cycles, independent verifiers), `generate_structured`
first, `extract_json_from_markdown` text fallback, 256 KB cap. Never
executes, never touches tools/policy. Planner model/provider resolve via
existing `get_provider(config)`; fallbacks stripped at narrow time.

## Compiler

`compile_ir`: narrow → parse → shape check → validate. Deterministic:
same IR + same policy → same graph + same fingerprint (tested).
`function/module/callable/import/command/shell/workspace/approved`
fields are host-controlled → `POLICY_REJECTED`. Runs zero model calls
(stop condition: compiler importing provider code would fail review).

## Security

Planner output cannot grant authority: tools intersected with host policy
(`'all'` forbidden under restrictive policy), models/providers allowlisted,
budgets/concurrency/retries clamped (never raised), workspace forced to
host, retries bounded, cycles via existing bounded validation, graph caps
reused. 75 adversarial tests green; no P0/P1.

## Policy

`narrow_ir` returns `(narrowed_ir, notes)`: drops forbidden tools (empty →
REJECT), clamps budgets/concurrency/retries with notes, pops fallback
chains. Narrowing that breaks validity → REJECT at validate step, never a
silently different graph. Narrowing audited as `graph.policy_narrowed`.

## Approval

`plan` never executes. `execute <id> --yes` (or SDK `approve=True`)
runs the EXACT stored IR recompiled; fingerprint drift → STALE_PROPOSAL.
`approve` uses `is True` (Phase 8 `"false"` regression covered by test).

## Graph-vs-Single-Agent

Planner sets `execution_shape`; compiler enforces SINGLE_AGENT = exactly
one node. Trivial tasks stay single-node; no fanout pressure.

## Evaluation

`scripts/eval_planner.py`: 6 corpus cases green (trivial, bug chain, fan,
security w/ independent verifier, fake-deps flagged OPT-001, missing
verifier flagged OPT-002). Details: `GRAPH_PLANNER_EVALUATION.md`.

## Limitations (honest)

- Planner quality depends on the model; compiler guarantees validity,
  not brilliance. Quality scores are advisory.
- No optimizer (Phase 10): OPT-001..004 findings are emitted, not fixed.
- `function:`/`gate:` nodes are host-wired; planner graphs express gates
  via verifier accept-edges (documented in eval script).
- Proposal store is workspace-local SQLite; no expiry reaper (EXPIRED
  status reserved).
- Text-fallback parsing needs a capable model; weak models should use
  providers with structured output.
