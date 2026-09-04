# ADR: Local Identity Source

## Status: Accepted (M1b)

## Context

Phase 1 needs a `Principal` model (OS user, workspace, profile, org
identity, agent/subagent identity, delegated credential) so every effectful
action carries a policy decision + correlation ID. Identity must work fully
offline: no network identity provider may be on the critical path.

## Decision

- **Root of identity is local:** OS user (`uid`/username) + workspace path +
  Wisp profile name. These three are always available, never leave the
  machine, and form the default principal in local-only mode.
- **Org identity is bundle-derived:** when a verified policy bundle is
  present, its `org_id` + bundle version attach to the principal. No separate
  login flow; possession of a verifiable bundle *is* the org claim.
- **Agent/subagent identity is derived, never inherited:** subagents receive
  a child principal (`parent_principal_id`, restricted capability set, depth)
  with strictly fewer rights — addressing the audit finding that subagents
  inherit root authority (`docs/audit-2026-08-24.md` §5).
- **Delegated credentials** (MCP OAuth, provider keys) live in the OS
  keychain, referenced by handle — never embedded in the principal, sessions,
  traces, or audit records.
- **Device registration** (M4): a locally generated Ed25519 device keypair;
  the public half registers with the control plane. The private half never
  leaves the keychain.

## Consequences

- `Principal` is a frozen dataclass with `principal_id`/`correlation_id`
  strings — the exact fields M1a reserved in `ToolRequest` and
  `PolicyDecisionEnvelope`, whose supplier is now named.
- Offline use is unaffected: local-only principals need no keys, no network.
