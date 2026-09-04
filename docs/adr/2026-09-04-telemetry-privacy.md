# ADR: Telemetry Schema and Privacy Policy

## Status: Accepted (M1b)

## Context

Telemetry today is in-memory counters (`wisp/infra/telemetry.py`:
turn/tool counts, token totals, latency histogram, per-tool breakdowns) —
no persistence, no trace model, no export. Phase 4 needs behavior to be
measurable and debuggable without centralizing source code, under the
privacy principle: code/prompts stay local by default; central telemetry is
opt-in, minimized, redacted, independently configurable.

## Decision

- **Local-first store:** append-only SQLite/JSONL trace store keyed by the
  `trace_id`/`span_id` lineage `AgentEvent` already carries. Spans: run,
  turn, model request, tool call, policy decision, approval, retry,
  subagent, checkpoint, final artifact.
- **Data tiers (explicit, per-sink):** `metrics-only` (counts/latencies) →
  `metadata` (+ tool names, model IDs, policy outcomes) → `redacted-content`
  (+ scrubbed args/diffs) → `local-only-full` (never exported). Default
  export tier is `metrics-only`; anything higher requires explicit opt-in.
- **Redaction before persistence/export:** provider keys, keychain handles,
  and secret-scanner hits are stripped at span construction, not at export —
  so a misconfigured sink cannot leak. Audit verification must detect
  tampering and contain no raw secrets.
- **Optional OTLP export:** endpoint + tier configured independently;
  no payload export by default.
- **Schema versioning:** trace/span schemas carry `version` and reuse the
  M1a envelope rules (strict constructors, fixture goldens) when the store
  lands in M5.

## Consequences

- `wisp trace <run-id>`, `wisp audit verify`, `wisp replay --dry-run`, and
  redacted JSON evidence export (M5) all read the local store — no server.
- The eval harness (benchmarks, replay fixtures, prompt-injection scenarios)
  runs against the same span schema, so evals and production share tooling.
