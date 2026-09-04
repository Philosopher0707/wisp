# M7 Design: Release Engineering (rev 1)

Date: 2026-09-04. Parent: target arch §6. No new dependencies (SBOM via
`importlib.metadata`). Gate green after every commit.

## 0. Ground truth

- No lockfiles; `pyproject.toml` version `0.1.0`, requires-python >=3.11.
- `/doctor` REPL + `wisp check` (provider health) exist; no release CLI.
- `ImmutableAuditTrail`/`AuditTrail` verify, policy health, redaction —
  all reusable for diagnostics.

## 1. New package `wisp/release/`

| Module | Contents |
|---|---|
| `lock.py` | `declared_deps()` (parse pyproject), `installed_versions()` (importlib.metadata, injectable for tests), `generate_lock()` → `requirements.lock` content, `verify_lock()` → mismatches; `audit_licenses(allowlist)` over metadata |
| `sbom.py` | CycloneDX 1.5-lite JSON (`bomFormat`, `specVersion 1.5`, serialNumber, components name/version/licenses/supplier) from installed metadata; `write_sbom()` |
| `health.py` | `health_check()` → list of (name, ok, detail): python version, sqlite writable (tmp), audit chain intact, policy cache verified-or-absent, disk space, cryptography present |
| `diagnostics.py` | `support_bundle()` → redacted dict (versions, config minus secrets via `redact_record`, policy health, recent error counts from telemetry counters if available); `write_bundle(path)` |

## 2. CLI

`wisp release <lock|verify-deps|sbom|health|diagnostics|licenses>`
(+ `--out FILE` for sbom/diagnostics). Thin adapters, --json optional?
Keep text + file outputs (JSON files are already machine-readable).

## 3. Docs (T3, single commit)

- `docs/RELEASE.md`: channels (stable/preview/nightly), semver, deprecation
  policy, upgrade/rollback (sessions, policy cache, audit preserved).
- `docs/SECURITY.md`: advisories, CVE response, report channel placeholder.
- `docs/THREAT-MODEL.md`: actors, boundaries (from target arch), top risks
  + mitigations mapped to milestones.
- `docs/COMPLIANCE.md`: evidence map (control → artifact → command).
- `docs/ADMIN-GUIDE.md` + `docs/QUICKSTART.md`: concise operational docs +
  reference bundle example (`docs/examples/reference-policy-bundle.json`).

## 4. Tests

`test_release_lock.py` (hermetic metadata fakes), `test_release_sbom.py`
(shape + license audit), `test_release_health.py` (all-ok + induced
failure), `test_release_diagnostics.py` (no secrets in bundle),
`test_release_cli.py` (goldens + exit codes).

## 5. Deferred (process, not code)

Signed release artifacts + provenance attestations (CI signing keys);
reproducible-build checks; platform installers; nightly CI matrix.
Documented as required follow-ups in RELEASE.md, not pretended in code.
