# Wisp Enterprise Target Architecture

Status: Accepted (M1b). Companion: M1a contract freeze
(`docs/superpowers/specs/2026-09-04-enterprise-contracts-m1a-design.md`,
`wisp/contracts/`). Detail decisions: `docs/adr/2026-09-04-*.md`.

## 1. Operating modes

| Mode | Policy source | Network | Authority on expiry |
|---|---|---|---|
| `local-only` (default) | Local config + built-in safety floor | None required; makes no network call | N/A (no expiry) |
| `managed-local` | Signed org bundle, periodically re-verified + cached | Periodic only | Falls back to last verified bundle until its expiry |
| `disconnected` | Last verified bundle | None | Past expiry: safely *reduces* authority (deny-by-default), never silently allows |

Local-only is feature-complete within its configured authority. Managed layers
may only narrow access, never widen a higher-level restriction:

`built-in safety floor → organization → local admin → workspace → session`

## 2. Trust boundaries

1. **Model output is untrusted.** No model output gains authority directly;
   every external effect passes through `ToolExecutor` (the only action path)
   after `ApprovalGate`/`SecurityPolicy` evaluation.
2. **Workspace trust** (`trusted | review-required | read-only | quarantined`):
   untrusted checkouts cannot auto-load skills, plugins, hooks, or executable
   project config.
3. **Extension boundary:** MCP servers, plugins, and hooks run with
   per-server scopes and first-use consent; unsigned extensions are
   quarantined (see trust ADR).
4. **Network boundary:** default deny; per-run modes `off | allowlisted |
   unrestricted-with-approval`.
5. **Human boundary:** approvals serialize through one prompt path with
   recorded verdicts (`ApprovalCancelled` → recorded denial, never replay).

## 3. Data classifications

| Class | Examples | Handling |
|---|---|---|
| `restricted` | Provider API keys, OAuth tokens, private keys | OS keychain only; never in sessions, traces, audit, or telemetry |
| `confidential` (default for code/prompts) | Source, prompts, diffs, tool args | Local by default; central export only opt-in, minimized, redacted |
| `internal` | File paths, tool names, counts, latencies | May flow to org telemetry at `metadata` tier |
| `public` | Version strings, policy-bundle IDs, schema versions | Exportable freely |

## 4. Supported matrix

- **OS:** macOS, Linux, Windows/WSL.
- **Model providers:** local endpoints (Ollama-class) preferred offline;
  cloud providers via the existing provider factory; orgs may restrict to an
  approved catalog via policy bundle.
- **MCP transports:** `stdio`, `sse`, `streamable-http` (mirrors
  `MCPServerConfig.transport`).

## 5. Non-goals

- Cloud-hosted code execution by default; no central code storage.
- Rewriting the turn engine, transports, or tool registry.
- More agent autonomy before M1–M3 land (ordering rule stands).
- A hosted control plane as a runtime dependency — the enterprise API
  (policy publishing, device registration, revocation, catalog, audit
  export) is a convenience over signed bundles, which remain the authority.

## 6. Milestone map

M1 (this phase): contracts + target + ADRs. M2: Principal/authority layer.
M3: RunStore + durable scheduler + sandbox. M4: bundle distribution +
managed/disconnected modes. M5: trace store, OTLP export, eval harness.
M6: task CLI, plan-review-apply, profiles. M7: SBOM, signing, releases.
