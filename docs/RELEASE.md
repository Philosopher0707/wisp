# Release Process (M7)

## Channels

| Channel | Source | Cadence | Audience |
|---|---|---|---|
| `stable` | version tags `vX.Y.Z` | as needed, ≥2wk soak | everyone |
| `preview` | `preview/*` branches | weekly | early adopters |
| `nightly` | `main` head | daily | CI + live-model matrix |

Promotion requires the evidence gate (§Evidence gate): unit tests, ruff,
contract suites, eval metrics vs baseline (no success/safety regression),
and the SBOM license check.

## Versioning

Semantic versioning. Breaking contract changes (any `wisp/contracts/`,
`wisp/auth/`, `wisp/runs/`, `wisp/policy/`, `wisp/trace/` wire shape)
require a MAJOR bump + migration note. Deprecations: one minor cycle with
`DeprecationWarning` + target removal version, then removal.

## Upgrade / rollback

- `wisp.db` migrations are additive-only (`CREATE TABLE IF NOT EXISTS`,
  guarded `ALTER TABLE`); downgrade never deletes user data. Sessions,
  policy cache (`~/.wisp/policy/`), and audit logs survive both directions.
- Rollback = reinstall prior tag + restart; `wisp release health` confirms
  store/audit/policy status after either direction.

## Evidence gate (per release)

```
python -m pytest tests/test_contracts_*.py tests/test_auth_*.py \
  tests/test_runs_*.py tests/test_policy_*.py tests/test_trace_*.py \
  tests/test_task_*.py tests/test_eval_*.py tests/test_release_*.py -q
python -m ruff check wisp/
wisp release verify-deps && wisp release licenses
wisp release sbom --out sbom-<version>.json   # signed in CI (follow-up)
```

## Deferred to CI (explicit, not pretended)

Signed release artifacts, provenance attestations (SLSA-style),
reproducible-build checks, platform installers (macOS/Linux/WSL),
dependency vulnerability scanning. Tracking: promote only with these green.

## Desktop app (macOS) — current status

The tag workflow (`.github/workflows/release.yml`, on `v*.*.*`) builds the Android APK and the Docker image. **It does not
build or publish the Mac app.** `wisp-desktop/electron-builder.yml` declares a GitHub publish target for auto-update, but
nothing uploads artifacts to it yet.

What exists today (local, see [`wisp-desktop/README.md`](../wisp-desktop/README.md)):

```
cd wisp-desktop && npm run package            # bundles the backend, builds, produces .dmg/.zip (arm64) in release/
node scripts/verify-packaged.mjs              # exits non-zero unless the built .app passes every check
```

Artifacts are **ad-hoc signed**: other Macs show a Gatekeeper warning, and the auto-updater will not accept unsigned updates.

To make it a real release channel, add (by someone who holds the credentials, as repository secrets, never in the repo):

1. An Apple **Developer ID Application** certificate and its password, for signing (set `mac.identity`, drop the ad-hoc hook).
2. **Notarization** credentials (an Apple ID app-specific password or an App Store Connect API key, plus the team id).
3. A `macos-latest` job that runs `npm ci` (npm 10 lockfile), `npm run package`, `verify-packaged.mjs`, then uploads
   `Wisp-<version>-mac.{dmg,zip}` and `latest-mac.yml` to the GitHub release for the tag.

Until then, treat the Mac app as a preview channel build.

