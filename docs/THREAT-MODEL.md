# Threat Model (M7)

## Actors

- **Developer** (trusted, operates the CLI locally).
- **Model output** (UNTRUSTED — may contain prompt-injected directives).
- **Workspace content** (untrusted until classified; quarantined checkouts).
- **Extensions** (MCP servers, plugins, hooks, skills — scoped, consented).
- **Org admin** (trusted via signed bundles; can narrow, never widen).
- **Network peer** (untrusted; LAN server binds are deny-by-default).
- **Desktop app user** (trusted; the macOS app's Terminal tab is their own shell, outside the agent's tool policy by decision, ADR 2026-10-07).
- **Web pages opened in the app's Browser tab** (untrusted; isolated from the app's backend key and IPC).

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
| Desktop: code in the app window drives the host terminal | Sandboxed renderer, no Node integration; terminal IPC accepts only the main window's renderer; backend key kept out of the shell's environment; working directory must be inside the home folder (desktop ADR) |
| Desktop: a web page reaches the app's backend or IPC | Browser tab is a separate `WebContentsView`: own partition, no preload, sandboxed, every permission denied, http(s) navigation only |
| Desktop: the app rewrites the user's session history | Other stores opened read-only; opening a session copies it; delete and rename of foreign sessions refused (409); tests hash the sources |

## Residual risks (accepted, tracked)

- Container/VM sandbox backends beyond the Docker adapter (M3 deferred).
- CI artifact signing + provenance (M7 deferred to CI keys).
- Live-model nightly matrix is policy, not enforced code (M5).
- Desktop: the in-app Terminal is not governed by the agent policy, so a compromised renderer could type into it (revisit if the main renderer ever shows untrusted HTML).
- Desktop: the Mac app is ad-hoc signed, not Developer-ID signed or notarized; the auto-updater will not accept unsigned updates.
