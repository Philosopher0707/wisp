# M4 Design: Managed Governance with Offline Continuity (rev 1)

Date: 2026-09-04. Parent: ADR `2026-09-04-policy-bundle-format.md` + target
arch §1 (modes) + §precedence. Gate green after every commit.

## 0. Decisions locked

- Bundle = canonical JSON + detached Ed25519 (`cryptography`, NEW pinned
  dep `cryptography>=41.0`); `revocation_seq` for emergency revocation.
- New package `wisp/policy/` (NOT `wisp/policies` — check for collision at
  build time; rename to `wisp/policy_bundle/` if taken).
- CLI: `wisp policy <inspect|verify|explain|dry-run|health|import|export>`
  via `cmd_policy(args)` in `__main__.py`, following `cmd_mcp` pattern.
  Logic lives in `wisp/policy/` (unit-tested); CLI is a thin adapter.
- Server API: minimal routes `POST /api/policy/publish`, `GET
  /api/policy/current`, `POST /api/policy/revoke`, `GET
  /api/audit/export` — local bundle files remain the authority; the API is
  a distribution convenience (spec §5 ordering honored).

## 1. Modules

| Module | Contents |
|---|---|
| `bundle.py` | `PolicyBundle` frozen dataclass (bundle_version, org_id, issued_at, expires_at, revocation_seq + sections: providers/models, mcp allowlist, plugin allowlist, shell/network restrictions, redaction rules, approval matrix, telemetry policy); `canonical_bytes()` (sorted keys, compact separators); `sign()` / `verify()` (ed25519, base64 sig); `generate_keypair()` |
| `loader.py` | `EffectivePolicy` (merged view + `controlling_layer(tool)` provenance); `load_local()` (no network, ever); `load_managed(cache_dir, refresh_fn)` (verify-then-cache; stale-until-expiry); `is_expired()`; expiry → deny-by-default trimming |
| `explain.py` | `explain_denial(tool, args, policy)` → plain-language string + layer; `dry_run(tool, args, policy)` → allowed + obligations without executing |

Bundle sections reuse M1a vocabulary where it exists (risk classes,
`BLOCK_REASONS`-style codes); unknown sections are preserved-but-ignored
with a warning (forward compatibility).

## 2. Precedence (narrow-only merge)

Each layer is a partial bundle; merge starts from the built-in floor and
applies org → admin → workspace → session. For allowlists: intersection.
For restrictions (shell/network/redaction): union. For approval matrix:
strictest wins per tool. A lower layer can never widen: merge functions
take `(higher, lower)` and only narrow — property-tested over random
layer pairs (hand-rolled loops, no new deps).

## 3. Modes

- `local-only`: `load_local()` only; no socket use (test asserts no
  network via monkeypatched socket).
- `managed`: `load_managed()` verifies signature + expiry, caches verified
  bytes; refresh failures keep serving cache until expiry.
- `disconnected` = managed with refresh disabled; past expiry the loader
  returns a trimmed policy (all approvals required, network off, exec
  denied) — never an error, never silent allow.

## 4. Tests

`test_policy_bundle.py` (sign/verify round-trip, tamper rejected, expiry
rejected, unknown-section tolerance, canonical-bytes stability golden),
`test_policy_precedence.py` (narrow-only property over layer pairs,
intersection/union/strictest semantics, provenance attribution),
`test_policy_modes.py` (local-only makes no network calls; stale cache
served pre-expiry; post-expiry trim denies exec), `test_policy_cli.py`
(inspect/explain/dry-run/health golden outputs), `test_policy_routes.py`
(publish→current→revoke flow, tampered publish rejected 422).

## 5. Deferred

Device registration + key distribution ceremony (needs human workflow
design); Postgres control plane; bundle encryption at rest (file perms
0600 + OS keychain for private keys suffice for M4).
