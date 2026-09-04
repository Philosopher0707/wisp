# Administrator Guide (M7)

## Modes

- **Local-only (default):** nothing to configure. No network calls.
- **Managed:** distribute the org public key + bundle URL/file. Devices run
  `wisp policy import <file>` (air-gap) or configure refresh; verify with
  `wisp policy health`.
- **Disconnected:** same as managed with no refresh; last verified bundle
  serves until expiry, then authority trims (deny-by-default).

## Reference bundle

`docs/examples/reference-policy-bundle.json` (+ `.sig`, generated with
`wisp.policy.bundle.generate_keypair` / `sign_bundle`). Sections:
approved providers/models, MCP/plugin allowlists, shell/network
restrictions, redaction rules, approval matrix, telemetry policy, expiry,
`revocation_seq`.

## Key management

- Org signing key: offline, backed up; rotation via overlapping validity.
- Emergency revocation: publish a short-lived bundle with bumped
  `revocation_seq`, or `POST /api/policy/revoke`.
- Device keys (M4 future): local Ed25519, public half registered.

## Precedence (narrow-only)

built-in floor → organization → local admin → workspace → session.
Inspect effective policy: `wisp policy inspect`. Explain a denial:
`wisp policy explain TOOL --args '{...}'`. Dry-run a change:
`wisp policy dry-run TOOL --args '{...}'`.

## Profiles

Ship `enterprise-managed` as the fleet default; `offline-secure` for
air-gapped labs; `read-only-review` for auditors; `ci-headless` for
pipelines (safest by default).

## Upgrades

Additive DB migrations; sessions/policy cache/audit survive upgrades and
rollbacks. Confirm with `wisp release health` after either direction.
