# Security Policy (M7)

## Reporting

Report vulnerabilities to the maintainers privately (see repo contact);
do not open public issues for unpatched holes. Target acknowledgment:
2 business days.

## CVE response procedure

1. Reproduce against `main`; assess blast radius (local-only vs managed
   fleet, data classes reachable — see `docs/THREAT-MODEL.md`).
2. Fix on a private branch with a regression test; request review.
3. If user action is needed (upgrade, key rotation, bundle reissue),
   publish an advisory with: affected versions, impact, workaround,
   fixed version.
4. Managed fleets: ship a short-lived bundle with bumped
   `revocation_seq` when the flaw is policy-expressible (e.g. forbid a
   tool/server) ahead of the code fix.
5. Post-mortem ADR for systemic causes.

## Hardening posture (shipped)

- `ToolExecutor` is the only action path; no-executor fallback is
  read-only (M2 structural tests).
- Secrets redacted at record construction (audit, traces, diagnostics).
- Subagents derive narrowed capabilities, never root (M2).
- Policy bundles Ed25519-signed, expiry trims authority (M4).
- Hash-chained audit trail with `wisp audit verify` (M5).

## Supported versions

Latest `stable` tag plus the prior minor while within its deprecation
window. Nightly/preview receive best-effort fixes only.
