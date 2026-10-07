# Threat Model (M7)

## Actors

- **Developer** (trusted, operates the CLI locally).
- **Model output** (UNTRUSTED — may contain prompt-injected directives).
- **Workspace content** (untrusted until classified; quarantined checkouts).
- **Extensions** (MCP servers, plugins, hooks, skills — scoped, consented).
- **Org admin** (trusted via signed bundles; can narrow, never widen).
- **Network peer** (untrusted; LAN server binds are deny-by-default).

## Trust boundaries (see `docs/enterprise-target-architecture.md` §2)

1. Model → executor: every effect passes `ToolExecutor` + `authorize()`.
2. Workspace → loader: trust classification gates skills/plugins/config.
3. Extension → host: per-server scopes, origin pinning, quarantine.
4. Network → process: default-deny egress, per-run modes.
5. Human → turn: serialized recorded approvals; cancel is data, not replay.

## Top risks → mitigations (milestone)

| Risk | Mitigation |
|---|---|
| Prompt injection → tool authority | Eval scenarios pin denial; L4 arg scan; approval gate (M2/M5) |
| Malicious hook persistence | Hook-dir mutation guard in `authorize()` L4 (M2) |
| Subagent privilege escalation | Narrowing derivation + capability enforcement (M2) |
| Credential exfiltration | Keychain handles; scrubbed subprocess env; redaction at construction (M2) |
| Irreversible or out-of-workspace action by the model | Deterministic gates before every tool call, before approval: shell parser + canonical-path confinement; fail closed on anything unanalysable (`docs/harness/invariant-gates.md`) |
| Secret reaching the model or the network | Tool results scrubbed before the model sees them; a secret or credentials file in a network command is refused |
| Unprompted dependency change | Dependency lock closed by default; only the operator opens it |
| "Verified" claimed on a command that proves nothing | Completion guard counts only a recognised test/lint/build run whose exit status decides the result |
| Tampered policy | Ed25519 bundles; revocation_seq; expiry trims authority (M4) |
| Lost approval / repeated write after crash | Durable transitions + idempotency first-write-wins (M3) |
| Unverifiable release | SBOM + lock verify + license audit + evidence gate (M7) |
| LAN RCE via server | Loopback default; auth required off-loopback; deny-by-default approvals (audit §2 + M2) |

## Residual risks (accepted, tracked)

- Container/VM sandbox backends beyond the Docker adapter (M3 deferred).
- CI artifact signing + provenance (M7 deferred to CI keys).
- Live-model nightly matrix is policy, not enforced code (M5).
