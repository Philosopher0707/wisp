# Graph Audit Integration Report (Phase 8 — G-1 CLOSED)

## Executive Summary

Graph security decisions are now recorded in the existing
`ImmutableAuditTrail` hash chain (same table/chain as tool decisions) via
one thin adapter (`wisp/graph/audit.py`, ~120 lines). No new chain, no
schema change, no authority change. **G-1 is CLOSED.** G-2 (remote/
multi-tenant) remains DEFERRED.

Changed: `wisp/graph/audit.py` (new), 19 emit sites in `executor.py`,
CLI-validate audit, 2 fail-closed fixes found during wiring (approval
truthiness, reason scrubbing).

## Existing Audit Architecture

`ImmutableAuditTrail(store)` appends to `audit_log(id, timestamp, action,
tool_name, workspace, allowed, reason, args_summary, prev_hash,
entry_hash)`; hash = sha256 of pipe-joined fields; `verify()` returns
first-tampered index or None. Connections are autocommit; writes are
synchronous; failures are swallowed by callers (`SecurityPolicy._audit`:
"must not break the agent"); no locking (single-writer assumption).

Integration point: adapter opens its own autocommit SQLite connection to
the SAME DB file (`<ws>/.wisp/wisp.db`), wraps it in the UNMODIFIED
`ImmutableAuditTrail`, and serializes appends with an RLock (fixes the
chain TOCTOU for concurrent graph runs). `CREATE TABLE IF NOT EXISTS` —
no migration.

## Security Event Taxonomy (20 events, closed set, machine-checked)

validation_rejected, policy_rejected, fingerprint_mismatch, state_tamper,
approval_requested/granted/denied, route_selected/route_unknown,
gate_decision, verification_allow/reject/retry/escalate, retry_allowed/
refused, cancel, stale_rejected, budget_exceeded, run_terminated.
Unknown names are refused by the adapter. Telemetry (scheduler scans,
durations, queue depth) stays in `graph_events`, never in the chain.

## Trust Boundary

Decision → GraphSecurityAuditor → scrub → canonical event →
ImmutableAuditTrail → hash chain. The adapter records only; audit output
never feeds authorization. `ToolExecutor → authorize()` unchanged.

## Consistency Model

Decision → audit attempt → state transition (matches `SecurityPolicy`).
No cross-table atomicity (SQLite can't span the two connections... same
file, separate connections); ordering is deterministic and recoverable:
every audited decision stores its entry hash in the graph event/row
(`audit_hash`). Missing hash = "not durably audited" — false claims are
structurally impossible since only real returned hashes are stored.

## Failure Semantics

Same as existing: audit failure never blocks execution, is logged, and
returns `""`. Graph behavior on audit failure was tested: run completes,
hash fields are empty, chain verifies for the events that did land.

## Test Results (exact)

- New audit suite: **31** (`tests/security/test_graph_audit.py`, §18 A–O
  + taxonomy-closure test asserting every taxonomy member has an emit site)
- Security suite total: **394** pass · functional graph: **53** pass
- Surrounding regression: **191** pass (dispatcher/transports/subagents/
  runs/tasks/agentic-graph/audit-trail) — **638 green total**
- Fuzz: existing ~230-case suites pass; audit payload fuzz via scrub tests
- Race: 150-iteration soak + jitter/cancel suites pass
- Ruff: clean. CLI validate/show + benchmark re-verified.

## Performance

32-node fan with full auditing: **33 ms** wall. 200 sequential emits:
**38 ms (~0.2 ms/emit)**. Chain verify after 200+ rows: instant. Audit is
not a bottleneck; no batching added (correctness first, per directive).

## Security Review (explicit answers)

- Can graph values grant authority? No — adapter takes allow/reason as
  computed by the engine, never from graph/model fields; event names closed.
- Can model output grant authority? No — audited labels/verdicts are the
  normalized deterministic values; raw output never enters the chain
  (scrubbed bounded evidence only).
- Can audit payloads contain secrets? No — `reason`, `tool_name`, and the
  structured summary all pass through `scrub` (secret + PEM + sk patterns);
  found + fixed during wiring: `reason` was unscrubbed, and `str()`-before-
  scrub defeated PEM detection (now scrub-structured-then-serialize).
- Can audit records be forged? Only with DB-write access (= workspace-write
  access, accepted residual G-3). Chain tampering detected by `verify()`.
- Can stale work create false success? No — generation check drops it AND
  emits `graph.stale_rejected`.
- Can resume bypass integrity? No — mismatch/tamper audited before refusal.
- Can router/verifier/approval bypass audit? No — all three emit on their
  decision paths, including unknown-route and fail-closed approval
  (`decision is True` only; truthy `"false"` denies — found + fixed).
- Can a gate bypass audit? No — `graph.gate_decision` added for allow/deny.

## Remaining Risks

G-2 DEFERRED (no remote graph API; nothing to authorize yet). G-3 accepted
(local DB writer ≡ workspace writer). G-4: `function:` registry is host
trust (unchanged). Coverage matrix: `AUDIT_EVENTS` + emit-site test keeps
taxonomy ↔ code ↔ tests linked mechanically.
