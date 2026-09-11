# PHASE 13 G1D — SALVAGE-TO-SIDE-EFFECT INTEGRITY

## Original P1-1
Truncated stream emitting a valid-looking `write_file` tool call executed
on approval: `pwned.txt` written (repeatedly, once per replayed round).
Captured pre-fix: provider error honest (G1B), `tool_result ok`, file
exists. Chain: provider dict `tool_call` → `_validate_tool_args`
(schema+salvage) → `ToolExecutor.execute` (authorize ALLOW) → mutation.
Authorization saw final args — the defect was never an auth bypass; it
was salvage/execution without a completion requirement.

## Root Cause
Three syntax-recovery paths (`openai.py` `_raw` producer, stateless
arg salvage + invented default path, filesystem content salvage) fed a
single execution path that asked only `valid ∧ authorized`, never
`complete`. G1B made the round state truthful but nothing downstream
read it at execution time.

## Completion Gate
`_execute_tool` reads the dispatcher stamp (`_round_complete` bool,
`_round_state` descriptor; unstamped direct calls default complete).
Incomplete round + non-READ risk ⇒ explicit refusal
(`[Refused: provider round ended <state>; ... complete ∧ valid ∧
authorized ...]`), partial/salvage state named, candidate preserved.
Round state is stamped onto tool calls at dispatch (`_round_state`).
Risk split reuses `ToolRisk` (unknown→EXEC fail-closed): READ proceeds
with existing salvage/validation; everything else refuses. No new
taxonomy, no auth changes, no retry changes. No signature change was
needed (stamp-derived, so existing test doubles keep working).

## Salvage Semantics
NORMAL_PARSE (schema-valid args, complete round) vs SALVAGED_PARSE
(`_raw` recovery / invented path, logged `parse_mode=SALVAGED`) remain
distinguishable; both still require a complete round to execute.
Salvage produces a CANDIDATE, never authority.

## Mutation Boundary
Ordering (§16): provider result → completion-state validation (gate) →
tool/schema validation → ToolExecutor authorization → mutation. Refusals
never reach authorize; approvals never execute without the gate passing.
Complete+valid+deny still blocks (authorization authoritative);
complete+invalid still fails schema. No step reordered for complete
rounds (byte-identical behavior there).

## Read-only Behavior
Truncated-round `read_file` executes normally (§5/§19: diagnostics
preserved, no mutation possible). Verified by test.

## Security
complete+allow ⇒ executes; complete+deny ⇒ blocked; partial+allow ⇒
refused — the triple regression passes. Path containment untouched and
re-pinned (traversal/absolute probes blocked; invented paths stay
workspace-relative). `_raw` audit (§17): only three syntax-recovery
sites exist (producer, stateless salvage, filesystem salvage); error
translators return "" and stream parsers skip-and-log (no authority
conversion anywhere else).

## Cross-layer Flow
Mock provider → truncated tool_call → stream layer (truncated error,
G1B) → turn (provider_failed) → parser/salvage → gate REFUSES →
ToolExecutor never consulted → workspace unchanged. Complete twin:
executes, file bytes exact. Crash interaction: refused turns create no
journal (`recover` clean); repeat turns refuse identically (no
promotion, no accumulation). Resume/request-level candidate tracking is
in-flow only — durable G2 tracking documented as limitation (§22).

## Retry Boundary
DEFERRED (P1-6 untouched). Refusals are terminal tool_results like any
tool error; no auto-retry added; existing machinery cannot bypass the
gate (it sits inside `_execute_tool`, below every retry caller).

## Performance
Parse-dominated, gate is noise: valid 2000× 694→605ms, salvage 2000×
572→546ms, 200KB args 200× 59.6→55.9ms (jsonschema dominates; variance).

## Remaining Limitations
- Tool-level filesystem salvage reachable by direct (non-turn) callers
  with hand-made `_raw` args (caller-asserted args, same as any direct
  call; turn path always gates).
- Durable candidate/error tracking (G2; refusal is in-flow + logged).
- Turn-tail wrap-up `.get` (pre-existing, adjacent, untouched).
- Licensed ANY/BEST_EFFORT/STREAMING partiality (defined, pinned in G1C).
- One test-double alignment: `test_child_turn_publishes_its_own_depth`'s
  scripted provider gained the terminal marker its round was missing
  (G1B contract; depth-publishing intent unchanged).

## Tests
- `pytest tests/test_salvage_gate.py` — 20 passed (§27 matrix 10,
  salvage semantics 2, truncation/path 3, read-only 1, cross-layer/
  crash/idempotence/observability 3, behavioral fuzz 1)
- Affected suites (stateless/stall/approval/runtime/providers/contract/
  faults/subagent/background/cli/coding/tools) — green except the 6
  G0-known subagent_orchestrator failures
- `pytest tests/reliability/` — 46 passed (pre-G1D count; rerun in full)
- Full suite — see metrics
- `ruff check` touched files — clean
