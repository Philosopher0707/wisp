# M5 Design: Evidence-Driven Reliability (rev 1)

Date: 2026-09-04. Parent: ADR `2026-09-04-telemetry-privacy.md`. Gate green
after every commit. No new dependencies (OTLP via stdlib HTTP).

## 0. Ground truth

- `uuid7()` trace/span IDs + `TraceContext` (`wisp/infra/tracing.py:18,65,74`).
- Hash-chained `ImmutableAuditTrail` with `verify()` (`wisp/infra/audit.py`).
- Benchmark runner + tasks + scoring (`wisp/benchmark/`); `wisp bench` CLI.
- M2 `redact_record()`; M1a `trace_id`/`span_id` lineage on every event.

## 1. New package `wisp/trace/`

| Module | Contents |
|---|---|
| `span.py` | `Span` frozen dataclass (trace_id, span_id, parent_span_id, kind, name, started_at, finished_at, attrs, status, version); kinds: run/turn/model_request/tool_call/policy_decision/approval/retry/subagent/checkpoint/artifact |
| `store.py` | `TraceStore` ABC + `SQLiteTraceStore` over new `trace_spans` table (additive migration, M3 pattern); `query(trace_id)`, `query_run(run_id)`, redaction applied at `append()` |
| `export.py` | `export_evidence(trace_id)` → redacted JSON (spans + linked audit entries + repro manifest slot); `replay_plan(trace_id)` → ordered tool-call sequence for `--dry-run` (no execution, ever) |
| `otlp.py` | Tier-gated OTLP/HTTP-JSON exporter (stdlib `urllib`): tiers metrics-only/metadata/redacted-content; `local-only-full` refuses export; no payload above configured tier |

## 2. Eval harness `wisp/eval/`

- `scenarios.py`: manifest-driven scenarios (task goal, fixture workspace,
  fake-provider script, assertions) incl. prompt-injection + approval-bypass
  cases.
- `metrics.py`: success, safety (bypass attempts blocked / total),
  latency p50/p95, cost (tokens), recovery (parked→resumed), interruption
  (cancel honored) — pure functions over span lists + benchmark results.
- Runs on deterministic fake providers only; live-model governance
  (isolated HOME, nightly) is documented policy, not code, in M5.

## 3. CLI (T5)

- `wisp trace <run-id|trace-id>`: span tree with timings + statuses.
- `wisp replay --dry-run <id>`: planned tool sequence, no execution.
- `wisp audit verify [--path]`: hash-chain verification report.
- `wisp task export-evidence <id>`: redacted JSON to stdout/file (first
  slice of M6 task UX, justified: evidence needs a handle now).

## 4. Tests

`test_trace_spans.py`, `test_trace_store.py` (tmp SQLite + migration),
`test_trace_export.py` (redaction at append; export contains no secrets;
replay plan ordering), `test_trace_otlp.py` (local HTTP sink asserts tier
gating + payload shape), `test_eval_metrics.py` (pure metrics incl.
adversarial scenarios), `test_trace_cli.py` (golden outputs).

## 5. Deferred

Postgres trace backend; live-model nightly matrix (CI config, not code);
sampling/tail-based retention policies.
