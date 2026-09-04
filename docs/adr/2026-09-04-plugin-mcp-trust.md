# ADR: Plugin/MCP Trust Model

## Status: Accepted (M1b)

## Context

Wisp loads plugins (`PluginManifest`), MCP servers (`MCPServerConfig` with
`stdio|sse|streamable-http` transports, OAuth scopes), hooks, and skills —
mostly on first use, with no allowlist, origin pinning, or consent record.
Combined with the audit's hook-escalation finding (bash can write hook
config that hot-reloads, `docs/audit-2026-08-24.md` §3), extensions are the
widest authority hole after the permission-mode default.

## Decision

- **Allowlist-first in managed mode:** the policy bundle's MCP/plugin
  catalogs are the authority; anything not listed is denied (managed) or
  consent-gated (local-only).
- **Origin pinning:** each server/plugin records transport + origin
  (command path, URL) at first use; silent origin changes re-trigger
  consent. The M1a `origin` manifest field is the storage for this.
- **Per-server scopes:** MCP servers declare tool scopes; the executor
  enforces them like the existing `disabled_tools` list but positive
  (default-deny). OAuth scopes (`manager.py`) are the floor, not the
  ceiling.
- **First-use consent:** explicit, recorded (server id, origin hash,
  scopes, timestamp) in local state; re-consent on scope/origin change.
- **Quarantine:** unsigned or unlisted extensions load into a sandbox with
  no file-write, no network, no credential access until approved.
- **Dependency/version validation:** plugin `plugin_dependencies` and
  `requires_wisp_version` are enforced at load, not advisory.
- **Signatures:** the M1a `signature` manifest field is verified against
  bundle-published keys once Phase 3 lands; until then it is recorded but
  not trusted.

## Consequences

- Untrusted repositories cannot auto-load executable project configuration
  (workspace-trust model: quarantined checkouts get consent-gates for every
  extension load).
- Structural tests must prove no extension path bypasses `ToolExecutor`
  (Phase 1 acceptance criterion).
