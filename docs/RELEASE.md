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
