# ADR: Policy-Bundle Format and Signature Verification

## Status: Accepted (M1b)

## Context

Phase 3 needs centralized governance as an *optional* layer: approved
providers/models, MCP/plugin allowlists, shell/network restrictions,
redaction rules, approval matrix, telemetry policy, expiry, and emergency
revocation — while local-only mode keeps working with zero network calls.
No crypto library is vendored today (`pyproject.toml` has no
`cryptography`/`nacl`). The M1a `PluginContract`/`MCPServerContract` already
reserve `origin`/`scopes`/`signature` fields for this.

## Decision

- **Format:** a JSON bundle document (`bundle_version`, `org_id`,
  `issued_at`, `expires_at`, `revocation_seq`, plus the policy sections
  above), canonicalized (sorted keys, compact separators) before signing.
- **Signature:** detached Ed25519 signature over the canonical bytes, via the
  `cryptography` package (new pinned dependency, used only for
  `hazmat.primitives.asymmetric.ed25519` — no TLS/X.509 stack involved).
  Rationale over HMAC: devices must verify without holding a shared secret;
  over Sigstore: must work fully offline/air-gapped.
- **Trust roots:** org public keys ship in local admin config; key rotation
  via overlapping validity windows; emergency revocation = short-lived
  bundle with bumped `revocation_seq` (devices reject older sequences).
- **Verification points:** at load, at scheduled re-verify (managed mode),
  and on demand (`wisp policy verify`). Tampered or expired bundles are
  rejected; expiry reduces authority (deny-by-default), never expands it.
- **Precedence:** built-in floor → org bundle → local admin → workspace →
  session; each layer narrows only.

## Consequences

- One new dependency (`cryptography`) to be pinned, SBOM-listed, and
  license-scanned in M7.
- Bundle parsing/verification lives behind a `PolicyBundle` interface so a
  future Postgres-backed control plane (M4) reuses the same verifier.
- `wisp policy {inspect,explain,dry-run,health,import,export}` CLI (M4)
  operates on verified bundles only.
