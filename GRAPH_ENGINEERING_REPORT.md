# Graph Engineering Report

## 1. Architecture before

Wisp had parallel orchestration idioms with no shared execution authority:
`SubagentOrchestrator.run_parallel` (semaphore-bounded fan-out),
`multi_agent/dag.py` (`TaskDAG` + level-barrier `DAGScheduler`), background
workers (`BackgroundAgentManager`), and pattern tools (`vote`/`map_reduce`/
`chain`/`dag` in `wisp/tools/orchestration.py`). Data flow was prompt
injection (`_dep_results` truncated into the next prompt); joins were
`asyncio.gather` barriers; retries re-ran whole patterns; no durable
per-node state, no contracts, no router/verification authority split.

## 2. Architecture after

```
Prompt → Context → Harness/Tools → Agent Loop → GRAPH ENGINE → Governance
```

New `wisp/graph/` package sits **above** the agent loop and **below**
governance. `GraphExecutor` owns what runs / waits / retries / stops;
`SubagentOrchestrator` still executes individual agents; `auth`/`policy`/
`ToolExecutor` still authorize every tool call. Model calls are nodes.

```text
                ┌─ implement ─┐
planner → map ──├── security ──┼── merger ── review ── gate ── tests ── final
                └─ tests ─────┘        ↑         │
                                       └─────────┘ correction (bounded ×3)
```

## 3. New modules (`wisp/graph/`)

| Module | Responsibility |
|---|---|
| `types.py` | Frozen graph-domain types: Graph/Node/Edge/Contract/Result/Failure/Route/Join/Retry/Budget/Model/GraphPolicy/Verification/Gate/Artifact/Checkpoint/Run |
| `validator.py` | Fail-closed static validation (5 sections) |
| `scheduler.py` | Ready-queue: dependency predicates, conditional lanes, entrypoint rule |
| `control.py` | Route tables, join evaluation, keyed merge, splitter, write-conflict detect |
| `executor.py` | `GraphExecutor`: drive loop, budgets, retries, persistence, resume, approval, cancel |
| `store.py` | `GraphStore`: `graph_*` tables in the UnifiedStore DB file |
| `artifacts.py` | Content-addressed `artifact://` files + metadata rows |
| `verifier.py` | Verdict normalization + deterministic gates + verdict combination |
| `runner.py` | `SubagentNodeRunner`: Node→Contract→Orchestrator (no provider code) |
| `dsl.py` | YAML definitions; mandatory per-edge `reason` |
| `trace.py` | ASCII/JSON/DOT + quality metrics (pure) |
| `reference.py` | Coding-agent reference graph + deterministic helpers |
| `compat.py` | `chain/dag/fan` lowering (vote/map_reduce stay orchestrator-backed) |
| `cli.py` | `wisp graph ...` verbs + REPL registration helper |
| `api.py` | `run_graph` → `GraphHandle` (wait/status/cancel/resume/trace/metrics) |

## 4. Modified modules

- `wisp/__main__.py` — `graph` subcommand + help (additive, 4 lines of wiring)
- `wisp/cli/dispatcher.py` — `/graph` builtin (same pattern as `/rewind`)
- `wisp/__init__.py` — `Graph/GraphNode/GraphEdge/GraphPolicy/NodeResult/GraphHandle/run_graph`
- `wisp/sdk.py` — `Wisp.graph(graph, inputs)` → `GraphHandle`
- `tests/` — `test_graph_engine.py` (38), `test_graph_invariants.py` (15)
- `scripts/bench_graph.py`, `graphs/repo-audit.yaml` — new support files

Nothing existing was refactored; legacy `spawn/fanout/orchestrate_*` untouched.

## 5. Graph execution lifecycle

validate → `create_run` → status RUNNING → loop
{ready = predicate scan; launch bounded; settle FIRST_COMPLETED; record;
route/join/verdict handling; checkpoint} → sinks decide SUCCEEDED/FAILED →
persist + events. Pause (approval), cancel, and budget-exceeded are explicit
exits with durable state. `resume` re-validates the graph hash first.

## 6. Node lifecycle

PENDING → RUNNING → SUCCESS | FAILURE | TIMEOUT | CANCELLED | SKIPPED.
Failures record typed results (never bare exceptions outward). Retry
re-queues **only** the failed node (attempt < max, idempotent, safe).
Correction edges reset the target + downstream cycle body (bounded).
Approval nodes pause the run; joins settle locally without model calls.

## 7. Scheduler design

Predicate scan over a `SchedulerState(statuses, taken)` — no list order.
Unconditional preds need settlement; conditional preds need a matching taken
label (lane-aware: `reject.tests` matches `reject`). Entrypoint always starts.
Independent nodes are all eligible; the executor's semaphores bound them.

## 8. Persistence model

Same SQLite file as `UnifiedStore` (`WISP_DB`/`<ws>/.wisp/wisp.db`):
`graph_runs` (def snapshot + hash + inputs), `graph_node_runs` (upsert by
node_run_id, idempotency keys), `graph_artifacts`, `graph_events`,
`graph_checkpoints` (JSON snapshots per transition). Resume: completed rows
reused, RUNNING rows re-queued iff idempotent else CANCELLED.

## 9. Artifact model

`<ws>/.wisp/artifacts/<type>-<hash12>.json`, metadata in
`graph_artifacts`. Edge mappings + `resolve_inputs` substitute
`artifact://` refs — transcripts never flow between nodes.

## 10. Failure model

Branch states SUCCESS/FAILURE/TIMEOUT/CANCELLED/SKIPPED as `NodeResult`
values; merges keyed by node id (`results_by_node`), never positional.
Failed branches resolve structurally; joins decide release vs contained
failure; sinks decide the run verdict.

## 11. Retry model

Smallest failed unit only; successes preserved (tested: branch-0 ran once
while branch-1 retried). Backoff `base·2ⁿ` capped, injectable sleep.
Refused when: timeout w/o opt-in, `unsafe_side_effects`, or non-idempotent.
Correction edges replay declared cycle bodies up to `max_iterations`.

## 12. Verification model

Verifier nodes output ALLOW/REJECT/RETRY/ESCALATE + reason codes + evidence
URIs; unknown verdicts normalize to REJECT. Deterministic gates
(`tests_green`, `no_scope_creep`, `schema_valid`, `policy_allowed`) are pure
functions. Multi-verifier combination: any REJECT wins. Generator context ≠
verifier context by construction (separate contracts/artifacts).

## 13. Governance integration

Graph policy is narrow-only, checked statically (tools/models/providers/
nodes/concurrency/retries/budgets) plus workspace equality at run time.
`bypass` permissions rejected. All tool use still flows through
`ToolExecutor.authorize()` inside subagent execution — the graph adds no
authority path (validator + tests pin this).

## 14. Security analysis

No new authority layer: executor cannot invoke tools directly (needs a
runner; the stock runner goes through the orchestrator → ToolExecutor).
Secrets: node inputs/outputs persist to SQLite — same posture as session
store; `artifact://` refs avoid transcript duplication. Non-idempotent
side effects never auto-retry. Graph hash pinned per run (no mid-run
definition drift). Residual risk (§21): artifact payloads are not yet
passed through `redact_record` — recommended follow-up.

## 15. Concurrency model

Global `min(executor, graph policy)` semaphore + named provider semaphores
(`limits={"provider:openai": 8}`). `asyncio.wait(FIRST_COMPLETED)` settle
(no gather-teardown hazard, mirroring `run_parallel`). `max_nodes` caps fan
(100-branch test sets 256 explicitly). No unbounded task creation.

## 16. Cost model

Per-node input/output tokens + cost + duration tracked in results;
graph budgets (tokens/cost/runtime) checked every loop iteration →
`budget_exceeded` fail-closed. Model classes (cheap/standard/strong) ride
`ModelPolicy` into `SubagentContract` creation. Benchmark (§19): fan adds
zero token overhead vs chain on identical work.

## 17. CLI/API changes

`wisp graph list|show|validate|run|resume|status|cancel|trace|inspect|metrics`;
REPL `/graph …`; `wisp.graph(graph, inputs)` SDK. DOT export for topology
debugging. Existing `swarm/task/run/repl` commands unchanged.

## 18. Backward compatibility

`chain_to_graph`/`dag_to_graph` lower legacy shapes; `fan_to_graph` backs
fan-out. `vote`/`map_reduce` intentionally not lowered (judgment-heavy).
All 263 pre-existing tests in touched areas pass; no existing module
refactored.

## 19. Benchmark results

`scripts/bench_graph.py` (8 nodes × 50 ms, max 8): chain wall 0.42 s vs fan
0.06 s (**7.0×**), peak concurrency 1 vs 8, identical tokens/cost
(parallelism factor 0.97 vs 6.81). Fan wins on independent work; coupled
tasks still favour chains — claimed and measured, not assumed.

## 20. Known limitations

1. STREAMING join releases downstream on first success but does not yet
   re-invoke downstream per late branch (no barrier is the guarantee kept).
2. Artifact payloads not yet secret-redacted at append (sessions have the
   same gap; `redact_record` integration is the fix).
3. No planner→graph auto-design (§50–51, optional): DSL + validator are
   ready for it; edge `reason`/`mapping` fields are the acceptance hook.
4. Cross-process cancel of a RUNNING run relies on the next checkpoint;
   in-process cancel is immediate.
5. Per-model (vs provider) concurrency keys accepted in config but only
   provider keys are enforced today.

## 21. Future work

Planner with per-edge `reason`+`mapping` proofs; artifact redaction;
per-item streaming re-invocation; server/WebSocket route for graph runs;
merge-node patch application via checkpoints; learning edge (constraint
extraction from accepted results into splitter policy).
