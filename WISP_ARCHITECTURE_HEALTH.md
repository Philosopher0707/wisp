# WISP ARCHITECTURE HEALTH

> **SUPERSEDED — read with caution.** This document was verified against an
> earlier revision and **two of its headline findings no longer hold**:
>
> - **P1-1** names legacy `core/agentic_graph.py::GraphRunner` as a divergence
>   risk. **That file does not exist**; `core/subagent/` is empty. The legacy
>   strata has since been removed.
> - **P1-2** names an 8-module cycle
>   `arena ↔ background_agent ↔ entry ↔ server.main ↔ routes{arena,diff,review,runs}`
>   as the top teardown risk. An independent Tarjan SCC run over 366 modules /
>   841 edges **does not reproduce it**. The actual cycle set is different.
>
> Also stale: the LOC and test counts, and the claim that tests mirror source
> paths. For the current, evidence-backed model see
> `REPOSITORY_INTELLIGENCE_REPORT.md`, `FINDINGS_ADJUDICATION.md`, and
> `PHASE_FINDINGS_REMEDIATION_REPORT.md`.


## Scorecard (0–10, evidence-backed)

| Dimension | Score | Evidence |
|---|---|---|
| modularity | 7 | 22 domains, median file 117 code lines; minus legacy strata |
| dependency direction | 6 | 5 import cycles (arena/server 8-module worst); `graph.cli→dispatcher` deferred cycle |
| cohesion | 7 | one-class-per-file mostly holds in graph/transport; `__main__`/`stateless` excepted |
| coupling | 5 | `wisp.config` fan-in 40; `server.main` fan-out 31; god-module watch list §7 |
| testability | 8 | injected runners/stores/providers everywhere new; legacy singletons persist |
| security boundaries | 8 | single authority path; 0 bare excepts; fail-closed validators |
| authority containment | 9 | ToolExecutor→authorize→ApprovalGate; 384 adversarial tests; no bypass found |
| error handling | 6 | 56% broad-Exception; resilience posture but masks faults in loop code |
| concurrency safety | 7 | RLock stores, semaphores, sliced waits; arena cycle is the teardown risk |
| API discipline | 5 | 1,238 public defs; 3 CLI dispatch layers; 70 endpoints vs 141 route tests |
| observability | 7 | spans/audit/telemetry/trace present; TUI gaps |
| maintainability | 6 | dispatch-table accretion (`__main__` cc99); docs good |
| extensibility | 7 | Transport ABC, ToolRegistry, pass registry, provider protocol all clean seams |
| performance architecture | 6 | validator O(N·E) known; caches with TTLs; no profiling culture |

## Critical findings

- P0: none found. (Stated explicitly: the audit did not surface an immediate correctness/security defect.)
- P1-1 (divergence risk): legacy `core/agentic_graph.py::GraphRunner` (cc48, still wired to autonomous runtime) vs `wisp/graph/` engine. Two schedulers, two retry models. FIX BEFORE PHASE 13: delete or quarantine behind a flag.
- P1-2 (teardown/import risk): 8-module cycle arena↔background_agent↔entry↔server.main↔routes{arena,diff,review,runs}. FIX BEFORE PHASE 13: break via lazy imports or interface split.
- P2-1: three path-containment implementations (`tools/_utils._resolve_path`, `graph.artifacts._contain`, `workspace._contain`) — same semantics, triple maintenance. FIX DURING PHASE 13: unify to one helper.
- P2-2: three CLI dispatch layers (`__main__` 30 branches, `Dispatcher` 11 cmds, legacy `repl/commands/cmd_*`). FIX DURING PHASE 13: continue strangler-fig migration, delete dead `cmd_*`.
- P2-3: unwired `core/context/repomap.py` duplicates live `repo_map.py`. ACCEPT or delete (verify no consumer first).
- P2-4: `runs/SQLiteRunStore` vs `infra/store.py UnifiedStore` overlap — verify whether two SQLite schemas exist for runs; unify if so. (MEDIUM confidence.)
- P2-5: JSONL `AuditTrail` + SQLite `ImmutableAuditTrail` + `tools/audit.py` — documented split (session vs tool decisions) but triple redaction logic (`_redact_value` vs `redact_record` vs `_scrub_args`, different coverage). FIX DURING PHASE 13: converge on `auth.secrets`.
- P3-1: dispatch-table CC hotspots (`main`, `_turn_inner`, graph `cli._main`, `_render_event`). Extract tables, not branches.
- P3-2: validator `_reachable`-per-node cost dominates at 500+ nodes (measured 67–79ms of 147ms at 512-fan).
- INFO: `heuristic deny-list` bash confinement is documented non-boundary; post-authorize arg rewrites un-re-authorized (upstream, pre-existing).

## Interesting (23A–L, condensed)

- A (small, huge capability): `wisp/graph/` 4.8K lines = full durable governed runtime; `workspace.py` 915 lines = isolation+merge+recovery; `auth/secrets.py` ~70 lines guards every sink.
- B (huge, little capability): `wisp-tui/`+desktop TS surface (~40K sans deps) vs experimental status; `semantic_index.py` embeddings stack unused without Ollama.
- C (hub): `wisp.config` (40 dependents) — every behavior switch flows through one frozen dataclass; change is safe but the file is a merge hotspot.
- D (hidden coupling): `transport/cli.py` ↔ background pub/sub ↔ telemetry rings — terminal rendering coupled to worker lifecycle.
- E (accidental): `entry.py::_run_repl_legacy` (cc61) + `repl/commands/*` — pre-dispatcher strata kept alive by fallback.
- F (security choke): `ToolExecutor.execute:638` → `authorize()` → `ApprovalGate` — 3 functions cover ~all authority.
- G (reliability choke): `UnifiedStore` single SQLite file + `stateless._turn_inner` — corruption/hang there stops everything; WAL+timeouts mitigate.
- H (fortress): graph (489 tests), subagent orchestration (293+114), workspace (105 incl. fault injection), policy/auth (87+).
- I (desert): TUI screens, server routes (70 endpoints/141 tests), `core/subagent/*` legacy, speculative/.
- J (contradiction): `graph.cli` imports dispatcher (UI→UI cycle); optimizer passes import validator private `_TOOL_RX` (layering smell, admitted).
- K (duplication opportunity): containment helpers (above); `ChangeTracker` vs ChangeSet (record vs proposal — complementary, keep both, document).
- L (scaling): at 250K LOC the risks are `__main__` dispatch, validator algorithms, and test-suite time; at 1M the single-file SQLite store and in-memory session maps need sharding (distant).

## Forecast

At observed velocity (~1K prod LOC/phase, tests slightly faster), 100K core arrives in ~25 similar phases — not a near-term risk. Complexity risk concentrates in dispatch tables and validator algorithms, not file count. Guardrails (not features): (1) freeze new top-level `wisp/` dirs without ADR; (2) CI check on import cycles (data in WISP_DEPENDENCY_MAP.md); (3) cap `__main__` branches — new commands go through Dispatcher; (4) one containment helper; (5) validator perf budget test (512-fan < 1s).

## Recommendations

- FIX BEFORE PHASE 13: P1-1, P1-2.
- FIX DURING PHASE 13: P2-1, P2-2, P2-5.
- FIX AFTER PHASE 13: P3-1, P3-2, TUI/server-route coverage.
- ACCEPT: satellite dirs, embeddings-optional design, broad-except resilience posture in loop code (with logging, which exists).
