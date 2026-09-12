# PHASE 13-J — SUBAGENT & FANOUT RUNTIME FORENSIC DIAGNOSIS

AUDIT ONLY. Zero production files modified. Diagnostic tests: `tests/reliability/test_13j_fanout_contract.py` (10 green, pin current behavior). Baseline HEAD `de130fe`.

## Executive diagnosis

**Why does fanout currently fail?** The reported `model=None` failure is a schema↔runtime contract mismatch, not a runtime defect: the fanout schema declares `"model": {"type": "string", "default": None}` (`registry.py:268`) — explicit `null` fails jsonschema validation — while the executor (`tool_executor.py:1933`: `spec.get("model") or None`) and child config (`_runner.py:731`: `contract.model or parent.model`) already handle `None` as inherit-parent. Three sibling fields (`timeout_seconds`, `max_iterations`) carry the identical `"type": "number", "default": None` hole. A second, independent defect compounds it: schema validation runs AFTER Gate1 approval (`stateless.py:437-454` approve → yield → `1743` validate), so the malformed call reached the approval UI before dying — proven end-to-end by new diagnostic tests (handler consulted with invalid args; validation error after allow, id preserved).

**Why does fanout trigger for technical codebase analysis?** I0/I1/I2 settled this: 13 always-visible delegation schemas with use-conditions ("splits into independent work units") + unconditional subagent-protocol block + task-shape matching by the model. The runtime does not solicit fanout; the surface invites it. No new trigger mechanism found in this phase.

**Is the architecture repairable?** Yes. Every failure found is a bounded defect inside sound abstractions (explicit lifecycle states, shared logical retry budget, role allowlists with 3-layer enforcement, per-field authority split). Nothing requires a new abstraction.

**Should Wisp redesign it?** No. Verdict: REPAIR.

## Evidence table

| Finding | Evidence | Severity | Confidence |
|---|---|---:|---|
| J1 nullable-schema mismatch (`model`, `timeout_seconds`, `max_iterations`: `default: None` vs `type: string/number`) | registry.py:265-268; validator rejects explicit null (test-pinned); runtime inherits fine (tool_executor.py:1930-1933, _runner.py:731) | P1 (incident class; every explicit-null proposal dies post-approval) | PROVEN |
| J2 validation AFTER approval globally | stateless.py:437-454 → 1743-1755; diagnostic tests prove handler-sees-invalid + validate-after-allow | P1 (invalid calls interrupt humans; wasted approval UX on every malformed proposal) | PROVEN |
| J3 model-influenceable `auto_approve`/`auto_retry` keys: read by executor (`tool_executor.py:1945,1949`), absent from schema, no `additionalProperties` guard → model can set child `auto_approve=true` (child bash skips approval) and `auto_retry`→retry budget | tool_executor.py:1945,1949; registry.py:260-271 (no guard); _runner.py:733 + executor:800-801 (auto_approve honored) | P1 (authority boundary: model-selected flag escalates child past approval posture; no exploitation evidence) | PROVEN |
| J4 `max_subagent_branching` unenforced on live paths (checked only in legacy guard APIs) | config.py:735-736 vs run/run_parallel/_run_with_retry/launch (zero branch checks) | P2 (worst case bounded by accident: MAX_RUNNING=8, timeouts, token budget — not design) | PROVEN |
| J5 timeout/cancel conflated to failure in parent digests; `hit_iteration_limit` dead; budget-exhaust path returns success:True | background.py:356; subagent_tools.py:121-123; task.py:226 (no writers); _runner.py:538-544+577-584 | P2 (observability/synthesis honesty; no false-success: flags preserved underneath) | PROVEN |
| J6 fire-and-forget cancels (exec_task.cancel w/o await; background cancel stamps without join; 2s reap orphans) | tool_executor.py:874-875; background.py:528-540; orchestrator.py:68-84 | P2 (orphan workers/threads; bounded, logged) | PROVEN |
| J7 no `len(tasks)` cap, no `max_concurrent` maximum in schema | registry.py:257-274 (test-pinned) | P2 (20-item blocking fanout launches 20 runs; throttled, not capped) | PROVEN |
| J8 depth refused late (failed entry minted, not launch refusal) | orchestrator.py:723-732 (no pre-check in launch) | P3 | PROVEN |
| J9 vote tie-breaker + reducer unbudgeted; graph×subagent stacking unbudgeted | _patterns.py:121-130,211-225; runner.py:59-70 | P2 (logical-cost leak outside G1E budget) | PROVEN |
| J10 context replication N× (fresh session but full base+role+tools+env per child; partitioner never fires for fresh children) | stateless.py:1127-1133; _runner.py:447-463 | P2 (cost/perf; return path capped at 8k/2k/240 chars — bounded) | PROVEN |
| J11 skill bodies reintroduce delegation post-filter (I1 residual, confirmed mechanism) | extensions/skills.py:77-110 (≤50KB body, no capability check) | P2 | PROVEN |
| J12 trigger = surface + task-shape, not runtime solicitation | I0 A/B (PRESENT→PRESENT), I1 matrix, §16 inventory (this phase: 20 trigger strings catalogued) | P2 (drives I2 partition work; no runtime defect) | PROVEN |

No P0 (no bypass of execution authorization; J3 is escalation-by-design-gap, unexploited, repairable without redesign).

## Complete call graph

```text
REPL run_turn (repl.py) → runtime.run_turn → core.turn (stateless.py:196)
  → _build_system_prompt (261) → provider round → tool_call events
  → Gate1 check_decision UNVALIDATED (437-454 / 522-543) → yield
  → _execute_tool (post-yield): completion-gate (1723) → _validate_tool_args (1743, jsonschema)
  → tool_executor.execute (1767): hard-deny → authority → Gate2 approval (memoized)
  → _route_fanout (345) → _fanout (1853): tasks-shape checks (1867-1928)
      → SubagentContract build (1935-1950, depth stamp 1960-1964)
      → background: manager.launch → create_task(_run_entry) → orchestrator._run_with_retry
        → run() → SubagentRunner.run() → child core.turn (fresh session, role tools)
        → SubagentResult → entry settle (COMPLETED/FAILED/CANCELLED) → digest
      → blocking: orchestrator.run_parallel → asyncio.wait ALL → aggregated results
  → subagent_wait poll-join (subagent_tools.py) → synthesis
```

Per-boundary contracts/validation/ownership/retry/persistence/cancellation/timeout/authority: see agent reports in working notes; ownership summary — model owns task text/role/timeout-proposal/model-proposal/concurrency; host owns workspace, tools, depth/branch, budgets, approval, execution, join.

## State machine

```text
proposal → Gate1 approval (UNVALIDATED) → yield → validation ─┬─ error result (audited? NO) → history → next round
                                                              └─ ok → Gate2 → dispatch → launch
background: launch → running → completed/failed/cancelled (terminal set) → digest (ok = success only)
blocking: launch → ALL-barrier → per-child ok → all_ok → totals
failure transitions: timeout→failed(flag kept); cancel→cancelled→digest-failed; prune→Unknown agent_id;
depth-refuse→failed entry; gather-exception→synthesized failure; budget-out→break (success:True path — defect J5)
```

## Architecture recommendation

REPAIR. Ordered minimal path: (1) nullable schema types + explicit `auto_approve`/`auto_retry` schema entries with bounds (or drop the reads) + `additionalProperties: false`; (2) validate-before-Gate1 globally + audit validation failures; (3) enforce `max_subagent_branching` on fanout/spawn paths; (4) preserve timeout/cancelled distinctly in digests; wire or remove `hit_iteration_limit`; (5) await cancels with bounded join; (6) cap `len(tasks)` + `max_concurrent` maximum; (7) depth pre-check at launch; (8) budget the tie-breaker/reducer; (9) keep context design (bounded return path suffices). Retain: contracts, role allowlists, shared retry budget, background manager, join semantics, graph runtime untouched. Integration: G1A journals background entries; G1B/G1D result flags preserved through truncations (proven); G1E budget is the mechanism repairs (3)/(9) extend; G1C untouched (fanout join stays ad-hoc ALL/poll — do NOT port JoinPolicy); H3 terminal paths unaffected (repairs add terminal signals, never remove); H0 steering, H1 pruner/meter, H4 success derivation orthogonal.

## Final Decision Framework

```text
CURRENT FANOUT VERDICT:
REPAIR

PRIMARY ROOT CAUSE:
Schema↔runtime contract mismatch on nullable overrides (model/timeout_seconds/max_iterations):
validator rejects what the executor already handles — compounded by validation running
after approval, so malformed proposals interrupt humans before dying.

SECONDARY ROOT CAUSES:
Model-influenceable auto_approve/auto_retry keys with no schema guard; unenforced
branch cap; digest conflation of timeout/cancel; fire-and-forget cancels; uncapped
task lists; always-visible 12-tool delegation surface driving over-trigger (I0–I2).

MOST IMPORTANT BROKEN CONTRACT:
Provider-visible JSON Schema ≠ executor-accepted contract (nullability + undeclared keys).

CURRENT SECURITY POSTURE:
Execution authorization intact (no bypass found); one design-gap escalation path (J3:
model-set child auto_approve) with no exploitation evidence — repairable in place.

CURRENT RELIABILITY POSTURE:
Lifecycle/join/result semantics honest (no false-success, no cancelled-as-success);
cost control accidental at the top end (branching/timeout races bounded by budgets,
not design); trigger-happy surface (partition work in I2 addresses visibility).

RECOMMENDED TARGET ARCHITECTURE:
Current architecture + repaired contracts (nullable alignment, validate-first,
declared bounded keys, enforced branch/task caps, distinct terminal digests,
joined cancels). NO new runtime, NO graph port, NO thin-worker rewrite.

WHY:
Every defect is bounded inside sound abstractions; the abstractions themselves
(contracts, budgets, allowlists, explicit states, split joins) are the correct
shape for the invariant. A rewrite would re-earn the same shape at higher risk.

MINIMUM MIGRATION PATH:
Items (1)–(9) above, each independently testable against the pinned J diagnostics;
I2 partition work proceeds in parallel (visibility track independent of contract track).

WHAT MUST NOT CHANGE:
Authorization order and gates; G1A–G1E guarantees; graph runtime; H3 terminal paths;
role-allowlist architecture; approval semantics; skill invocation contract.

NEXT IMPLEMENTATION PHASE:
13-J1 — contract-boundary repair: nullable schema alignment + validate-before-approval
+ declared bounded auto_approve/auto_retry (or removal) + branch/task caps.
```
