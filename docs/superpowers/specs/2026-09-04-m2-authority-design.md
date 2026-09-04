# M2 Design: Enforceable Local Authority (rev 1)

Date: 2026-09-04. Parent: `docs/enterprise-target-architecture.md` + ADRs
(`docs/adr/2026-09-04-*.md`). Mode: additive package + narrow integration
points; the 128-test gate must stay green after every commit.

## 1. New package `wisp/auth/` (all frozen dataclasses / pure functions)

| Module | Contents |
|---|---|
| `principal.py` | `Principal` (os_user, workspace, profile, org_id, kind, parent_principal_id, capabilities frozenset); `local_principal()` factory; `derive_subagent()` (strictly narrows) |
| `decision.py` | `AuthorizationDecision` (allowed, reason, controlling_layer, approval_required, obligations); `authorize(principal, tool, args, workspace_trust, sensitivity)` — layered: capabilities → workspace trust → tool risk → arg/target scan → sensitivity → approval |
| `workspace_trust.py` | `WorkspaceTrust` (trusted/review_required/read_only/quarantined); `classify_workspace(path, trusted_roots, quarantine_markers)` — pure |
| `secrets.py` | `redact(text)`, `scan_for_secrets(text)` (API keys, tokens, PEM blocks), `redact_record(obj)` for audit payloads |
| `consent.py` | `ConsentRecord` (server_id, origin_hash, scopes, timestamp); `record_consent()` / `check_consent()` over `.wisp/consents.jsonl`; `quarantined()` helper |

`Principal.principal_id` / `correlation_id` populate the M1a-reserved
`ToolRequest` / `PolicyDecisionEnvelope` fields (ADR identity supplier).

## 2. Integration points (narrow, each gated)

- **I1 — executor consult:** `ToolExecutor.execute` runs `authorize()` first;
  denial yields the standard denied `tool_result` (existing shape) with
  `block_reason="permission"`. Additive check before existing branches.
- **I2 — fallback closure:** `stateless._execute_tool` fallback
  (`stateless.py:1506`) currently bypasses approval/policy/audit. Fix:
  fallback allows safe-read tools only; anything else yields a denied error
  result directing to wire an executor. Structural test pins this.
- **I3 — audit redaction:** the two `AuditLog` call sites
  (`tool_executor.py:600-623`) pass `redact_record(func_args/result)`.
  Keychain handles/keys never reach the trail.

## 3. Structural tests (`tests/test_no_bypass.py`)

- Fallback denies non-read tools without an executor.
- `authorize()` denies quarantined-workspace writes even in FULL mode.
- Subagent principals never exceed parent capabilities (property over
  derivation).
- Audit payloads contain no raw secrets (golden with fake key material).

## 4. Explicitly deferred

- Full MCP-manager/ACP wiring of consent (needs bundle allowlists — M4);
  `consent.py` ships the record + check API with tests.
- Replacing `PermissionMode` (kept as the session-preference layer;
  `authorize()` maps it to approval requirements).
- OS keychain backend (file-backed handle store with 0600 perms is the M2
  stopgap; keychain adapter is M4 with device registration).
