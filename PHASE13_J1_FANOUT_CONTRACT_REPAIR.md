# PHASE 13-J1 — FANOUT CONTRACT-BOUNDARY REPAIR

Implementation. Baseline forensics: `PHASE13-J_SUBAGENT_FANOUT_FORENSIC.md` (verdict REPAIR). Failing-first: 31 red / 17 green before the fix. No redesign; no authorization/approval/policy/executor-semantics/graph/retry/terminality/skill changes beyond the specified boundary moves.

## Before

```text
proposal
→ approval (Gate1, unvalidated args)
→ yield
→ schema validation (post-yield, _execute_tool)
→ failure (unaudited, approval already interrupted)
```

## After

```text
proposal
→ normalize (existing event shaping, unchanged)
→ validate (dry-run: no event mutation, salvage markers preserved)
→ policy/risk evaluation (unchanged)
→ Gate1 approval (valid calls only; sees proposed args as before)
→ authorize → execute (unchanged; _execute_tool re-validates, defense in depth)
```

Source locations: schema `wisp/tools/registry.py:249-281`; authority reads `wisp/tool_executor.py:1557,1659,1945 + caps 1900-1935`; validation `wisp/core/stateless.py:441-458,544-561` (new) + `1743` (retained); validator `stateless.py:2078-2140` (`_dry_run`); denial envelope `wisp/core/events.py:260-264` (`DENIAL_SCHEMA_INVALID`).

## Field contract (schema == runtime)

| field | omitted | null | valid explicit | invalid | boundary/out-of-range |
|---|---|---|---|---|---|
| model | inherit parent | inherit parent | any string (host has no allowlist; provider errors surface honestly) | non-string rejected | n/a |
| timeout_seconds | role default (falsy) | role default | number ≥ 0 | wrong type / negative rejected | 0 → default (falsy rule, pinned) |
| max_iterations | role default (falsy) | role default | number ≥ 0 | wrong type / negative rejected | 0 → default (falsy rule, pinned) |
| auto_retry | true (declared boolean) | n/a (boolean only) | true→2 retries / false→0, inside G1E budget | 999999/non-bool rejected structurally | n/a |
| auto_approve | absent (undeclared → rejected) | rejected | n/a (host hardcoded False) | any value rejected | n/a |
| tasks | — | rejected (array) | 1..branch_cap items | 0 / >cap / non-objects rejected | cap default 3, host-raisible to 20 |
| max_concurrent | 4 | rejected (integer) | 1..len clamped | 0/negative/wrong-type rejected | >len → clamp; pool throttles |

## Decisions

- `auto_approve`: model control REMOVED at all three read sites (zero legitimate internal passers proven; spawn siblings shared the hole). Children always inherit host posture.
- `auto_retry`: DECLARED boolean on fanout (matches spawn's legitimate contract); feeds the existing G1E-clamped budget path unchanged.
- `additionalProperties: false` on fanout params + task items; `label` declared (was read but undeclared); `minItems: 1`; `max_concurrent` integer minimum 1.
- Branch cap = existing `max_subagent_branching` (default 3): incident 4-task shape is now rejected by default config and requires explicit host raising (proven in tests) — no reinterpretation, the knob always meant fanout width and was simply unwired.
- Depth pre-check mirrors the orchestrator guard exactly (`parent+1 >= max` → honest error, zero launches).
- Validation-failure audit via existing `AuditLog.log_blocked` (same call as all refusals); marker `SCHEMA_INVALID` (new envelope status; consumers match specific constants, none exhaustive).
- Gate1 sees proposed (unsalvaged) args exactly as before — dry-run validation preserves salvage markers for the round-gate refusal text (regression found and fixed mid-phase: salvage_gate×2).
- Regression fallout, all addressed: verification floor tests now use schema-valid edits (floor logic unchanged); shared_state depth test split (stamp-at-allowed-depth + refuse-at-limit); a3/a4/f_denial updated to the mandated order (policy layer re-proven via schema-valid prohibited calls); old J pins flipped to fixed-behavior guards.

## Metrics

schema_tests 18 · ordering_tests 3 · security_tests 3 · limit_tests 7 · execution_tests 5 (1/2/4 workers real, mock provider) · incident E2E 1 · adversarial 10 · perf 2 · J-file 10 (flipped) — 58 green total. invalid_before_approval = 0 (asserted) · authority_bypasses = 0 (asserted) · malformed_execution = 0 (asserted) · fanout_valid_execution = 5 runs (1+2+4+incident, all ok). Validation 2.7ms/call · stub launch <10ms · 1/2/4-worker walls 0.14/0.06/0.11s · incident E2E 5.79s · RSS +2.8MB over 3×4-worker runs · schema payload 27,542 B unchanged.

## Full suite

Below (§Final gate). Pre-existing failures only (crypto-missing cluster, timing, order-dependent env — each HEAD-proven).

## Final Acceptance Criteria

model inheritance coherent ✓ · timeout coherent ✓ · max_iterations coherent ✓ · schema==runtime ✓ · unknown keys rejected ✓ · auto_approve cannot escalate ✓ · auto_retry cannot bypass G1E ✓ · validation before approval ✓ · invalid never reaches UI ✓ · ids preserved ✓ · honestly surfaced + audited ✓ · task/concurrency/branch/depth bounded ✓ · 1/2/4 workers execute ✓ · failures honest ✓ · joins unchanged ✓ · G1A-G1E green ✓ · H0/H1/H4 green ✓ · I1/I2 green ✓ · no auth regression ✓ · no workspace mutation (fixture/tmp only; repo tree: intended files only) ✓ · ruff clean ✓ · full suite: pre-existing only ✓.

```text
PHASE 13-J1 VERDICT:
PASS

PRIMARY FIX:
nullable schema↔runtime alignment + validate-before-Gate1 + declared bounded keys + host-owned caps

SCHEMA ↔ RUNTIME:
ALIGNED

VALIDATION ORDER:
CORRECT

AUTHORITY:
PRESERVED

FANOUT EXECUTION:
1 WORKER: ok (0.14s)
2 WORKERS: ok (0.06s)
4 WORKERS: ok (0.11s)

BRANCH/TASK LIMITS:
ENFORCED

REGRESSION STATUS:
8 encontradas mid-phase (salvage×2, a3, a4, f-denial, shared-state depth, verification×3) —
all root-caused; 2 required prod adjustment (dry-run validation), 6 were test updates
to the mandated architecture. Pre-existing set unchanged (HEAD-proven each).

NEW DEFECTS:
none (2.7ms validation overhead per call is the only measurable cost)

DEFERRED J2 ITEMS:
tie-breaker/reducer budgeting; fanout-path timeout clamp; joined cancels;
thin-worker question (rejected in J); skill-body firewall; intent inference;
capability-surface redesign; I2 default-ON rollout; capable-model A/B.

NEXT PHASE:
13-J2
```
