# PHASE 13 — REPLAY BUNDLE SPEC (G0, version `g0.1`)

A replay bundle preserves enough context to **reconstruct what happened**
(observability) and, where the runtime is deterministic, **reproduce what
would happen again** (replay). These are different claims:

- **Observability**: bundle explains the run (decisions, evidence, refs).
  Achievable for every terminated run. G0 fixtures prove it.
- **Reproducibility**: re-executing with the bundle yields the same
  trajectory. Requires captured randomness + deterministic tools + a live
  equivalent provider. NOT claimed in g0.1.
- **Deterministic replay**: bit-identical re-execution. Requires all of
  the above plus hermetic tools/filesystem/network. NOT claimed, and not
  achievable while provider calls are non-idempotent and budget/attempt
  counters are memory-only (13A §13).

## Field contract

| Field | Necessity | Handling |
|---|---|---|
| `bundle_version` | required | `"g0.1"` |
| `wisp_version`, `commit_sha` | required | exact source identity |
| `request` (objective/constraints/facts) | required | free text, redacted |
| `repo_references` | optional | commit/branch, never file bytes |
| `graph_hash`, `graph_definition` | required for graph runs | content-addressed (`graph_hash` = fingerprint) |
| `policy_fingerprint`, `policy_reference` | required | narrow-only merge input identity |
| `provider`, `model`, `model_configuration` | required | temperature/max_tokens/deadlines; temperature>0 ⇒ nondeterministic (flagged) |
| `execution_configuration` | required | timeouts, concurrency, retry caps |
| `node_attempts`, `retry_information`, `timeout_information`, `cancellation_information` | required when they occurred | counts + reasons; UNKNOWN where telemetry lacks them |
| `artifact_references`, `artifact_hashes` | required when produced | content-addressed ids + sha256 |
| `workspace_snapshot`, `changesets`, `workspace_journal` | required for mutations | snapshot id + changeset ids + journal disposition |
| `audit_head`, `audit_references` | required | chain head hash + entry range; fork flag if `verify()` bad |
| `environment_metadata` | optional | os/arch/python; NEVER env values |
| `seeds` | optional | present only when the run was seeded |
| `stream_completion_state`, `truncation_state` | required when partial | `complete` \| `truncated` \| `stalled` \| UNKNOWN |
| `observed_decisions` (verdicts, routes) | required | gate/verifier/router labels + evidence refs |
| `timestamps` | required | wall start/end per attempt |

Redaction: every free-text field passes through `auth.secrets.redact`;
`validate()` additionally scans the serialized bundle with
`scan_for_secrets` and fails on any hit. Secrets must never enter the
bundle: API keys, env values, auth headers, private paths (home-dir
prefixed paths are truncated to basename).

Nondeterminism flags: `deterministic: false` if any of temperature>0,
unseeded retries, wall-clock timeouts, or `truncation_state != complete`.
