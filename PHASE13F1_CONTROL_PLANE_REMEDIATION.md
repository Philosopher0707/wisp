# PHASE 13F.1 — CONTROL-PLANE SEMANTICS REMEDIATION

## 1. Phase 13F findings addressed
F-override (policy deny + `y` = run) → REMOVED. F-disagreement
(gate deny vs M2 allow-with-obligation) → reconciled, tested.
F-unstructured denial → structured envelope (4 statuses). F-audit-gap
(`log_blocked` zero callers) → wired (9 executor sites + refusal
factory). F-policy-coverage (spawn/fanout vs spawn_background/thin) →
classified table + tests. T10 wording → distinct display lines.

## 2. Decision model
ALLOW: execute, no prompt. REQUIRE_APPROVAL: prompt; yes→execute,
no/timeout/cancel→structured denial, no execution. DENY: hard —
no prompt, no override, no execution. Exactly one interpretation,
enforced at gate AND executor AND M2.

## 3. Flow before vs after
Before: `policy deny → prompt → y → execute` (two override paths:
gate flip + executor M2-obligation). Autonomous handler made blocks
theater. After: `policy DENY → structured POLICY_DENIED` (no prompt);
`REQUIRE_APPROVAL → prompt → yes → execute / no → USER_DENIED`;
M2 denies what policy denies (shared set); executor pre-checks policy
before M2 for direct callers.

## 4. Fanout classification
AUTO_EDIT + fanout → REQUIRE_APPROVAL. Rationale: fanout performs no
mutation itself; children are mode-filtered (`filter_allowed_for_mode`
strips fanout/spawn/bash/git — tested) and handler-less children fail
closed on gated tools. Approving fanout = approving its filtered
subtree. Wording: "Approval required", never "Blocked".

## 5. spawn classification
REQUIRE_APPROVAL (same delegation logic; tested alongside fanout).

## 6. spawn_background classification
INTENTIONALLY policy-ALLOW + executor-prompted. It is the detached
primitive (background agents cannot prompt by design); parent turns
still prompt once via the executor (tested). No-handler contexts
execute it — its designed purpose. Differs from spawn/fanout only in
the headless case; documented + pinned.

## 7. Thin-mutator classification
`exec_sandbox` (shell via sandbox router + identical danger gate) and
`fs_mutate` (file read/write/edit): policy-ALLOW, executor-prompted,
M2 EXEC-obligation. The run_bash asymmetry is defended by
sandbox confinement + the same deny-list gate, not silent; documented
+ pinned. No set change (evidence did not support a ban).

## 8. Denial envelope
`denial_result()` (`wisp/core/events.py`): `{status, authorized:false,
executed:false, retryable:false, reason, data}` with statuses
POLICY_DENIED / USER_DENIED / APPROVAL_TIMEOUT / CANCELLED.
History carries it as JSON (model-readable); shared error predicate
treats any non-"ok" status as failure (verified no success path uses
other statuses — remaining literals are health/store/route records).
`tool_call_id` forwarded verbatim everywhere (S6 tested).

## 9. Audit coverage
Executor: `_audit_denial` on hard-deny, M2, decline, timeout, cancel,
forced-no-handler, danger, perm, plan (9 sites, exactly one each;
repeat/breaker/pre-hooks stay unaudited flow-control — documented).
Refusal factory: audits USER_DENIED/APPROVAL_TIMEOUT/CANCELLED +
role/extension blocks; skips gate+POLICY_DENIED (owned by
SecurityPolicy._audit — exactly-once by construction). Denial tests
assert single `blocked` entries with no `executed:true`.

## 10. tool_call_id preservation
Unchanged from prior phases; S6 re-proven for every new denial path
(hard-deny, decline, timeout, cancel envelopes all carry the inbound
ID; tests assert per-path).

## 11. Continuation semantics
Unchanged (no turn-stop, R5 deferred): denial → structured result →
next generation. Reads continue; mutations re-prompt; denied tools
stay denied on re-proposal (tested incl. cross-turn re-gating).

## 12. Retry semantics
Denials consume no G1E budget (pre-execution; decline×2→approve test
proves no burnout). Orchestrator denial/timeout never retry
(pre-existing). G1E suite green unmodified.

## 13. Security invariants
S1 POLICY_DENIED→executed=false: PASS. S2 →no prompt: PASS (handler
uncalled asserted). S3 →`y` cannot execute: PASS (gate + executor).
S4 →regeneration independently gated: PASS. S5 USER_DENIED→no exec:
PASS. S6 ID preserved: PASS. S7 audit event exists: PASS (executor +
factory + policy layers). S8 no budget burn: PASS. S9 reads continue:
PASS. S10 cancellation ends turn: PASS (propagates, tested).
10/10 PASS.

## 14. Tests added/updated
New `tests/test_13f1_remediation.py` (31 tests: STEP 3/4/5/6/7/8/9/13).
Rewritten to new contract: `test_13f_forensics.py` (7 inversions),
`test_permission_mode.py` (18: read_only envelope, bash/git hard-deny,
bash-no-prompt inversion, ask_all fail-closed), `test_security_policy.py`
(ask_all=REQUIRE), `test_transport_cli.py` (timeout raises),
`test_core_stateless.py` (role-denial envelope), `test_tool_protocol_integrity.py`
(decline envelope).

## 15. Full test results
Targeted order: 13F(18)+13F.1(31) ✓; policy/approval 144 ✓;
executor/audit/bypass 31 ✓; protocol/history/providers/core 113+1fix ✓;
G1E/G1D/G1C ✓ (56); G1B/G1A/providers ✓ (68); reliability 46/46 ✓;
full suite: see §18 pending → filled after run.

## 16. Changed production files (11 — over the ≤6 target, justified)
`infra/policy_engine.py` (3-state + stickiness fix), `infra/security.py`
(field + helper), `core/approval_gate.py` (ordering), `core/contracts.py`
(denial kind), `core/events.py` (envelope), `core/stateless.py`
(refusals + audit), `tool_executor.py` (pre-check + envelopes + audit),
`auth/decision.py` (M2 agreement), `cli/approval.py` (timeout verdict),
`transport/cli.py` (raise on timeout), `transport/renderer.py`
(predicate). Each is load-bearing for one contract facet; no
refactors, no renames, no new systems.

## 17. Changed test files
`test_13f1_remediation.py` (new), `test_13f_forensics.py`,
`test_permission_mode.py`, `test_security_policy.py`,
`test_transport_cli.py`, `test_core_stateless.py`,
`test_tool_protocol_integrity.py`.

## 18. Regressions
None beyond intended contract inversions (all updated). Full suite: 5593 passed / 23 failed / 2 xfailed — failure set
byte-identical to clean-tree baseline; zero regressions.

## 19. Deferred R5
No denial counter / turn-stop. Continuation unchanged.

## 20. Remaining unresolved
(a) Hook-denial UX now hard (no override) — intended, flagging.
(b) fs_mutate-read over-prompts (EXEC default) — fail-closed, P3 note.
(c) Websocket/TUI timeout→USER_DENIED labeling (only CLI raises
 placetimeout) — document, web client timeout path untested.
(d) Autonomous mode loses fanout/bash in AUTO_EDIT (by design — FULL
 for those).

## Implementation log (condensed)
STEP 1: baseline a6fb8e0/31 entries/py3.11; 13F:18, approval:37,
policy:103, e2e:10 — reproducible. STEP 2: 4 decision types mapped;
approval_required reused; found autonomous-override + catch-all
clobber (fixed fail-closed). STEP 3: ordering implemented; 4 stop
tests green. STEP 4: fanout REQUIRE (child-filter test). STEP 5:
table + thin rationale. STEP 6: M2 shared-set deny. STEP 7: envelope
+ predicate. STEP 8: no change. STEP 9: audit wiring. STEP 10:
display lines (no render refactor). STEP 11: 7+22+1+1+1 test updates.
STEP 12: targeted order green. STEP 13: matrix green. STEP 14:
full suite + ruff. STEP 15/16: below.
