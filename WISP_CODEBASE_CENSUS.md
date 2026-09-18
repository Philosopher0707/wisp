# WISP CODEBASE CENSUS

> **SUPERSEDED — counts and several findings are stale.** Verified against the
> current tree:
>
> - LOC / file counts have moved (the census predates later phases).
> - The "two parallel graph runtimes" claim (§10, and P1-1 in the health
>   report) is obsolete: `core/agentic_graph.py` **does not exist**.
> - The claimed 8-module arena/server cycle is **not reproducible**.
> - `multi_agent/delegation.py` and `DelegationAnalyzer` (referenced in the
>   module map) were **deleted** in `11fc949`.
>
> Structural observations that still hold: authority containment via
> `ToolExecutor` → `authorize()`, 0 bare excepts, and the graph/governance test
> fortresses. For current numbers see `REPOSITORY_INTELLIGENCE_REPORT.md` §2–3
> and `repository_manifest.json`.


## Executive summary (answers §29.1–15)

1. **Total LOC:** core Python project = **112,173 code lines** (cloc; 836 files). pygount cross-check: 101,140 (delta = docstring accounting, documented below). Full repo incl. satellites ≈ 330K cloc-code, dominated by vendored/JS builds (see exclusions).
2. **Production LOC:** **61,844** (`wisp/` 58,949 + `scripts/` 1,452 + root 1,443).
3. **Test LOC:** **50,504** (`tests/` 50,329 + `benchmarks/` 175), 4,865 test functions.
4. **Files:** 836 core Python files (370 `wisp/` + 356 `tests/` + misc); 2,249 repo-wide incl. satellites.
5. **Modules/packages:** ~45 top-level `wisp/` modules + 16 subpackages.
6. **Top 10 subsystems by LOC:** agent_loop+legacy 6.4K · repl_cli 5.6K · multi_agent 5.3K+2.6K orchestration-adjacent · graph 4.8K · repomap_context 6.2K incl. context_sys · tools 4.0K · transports 3.8K · server 3.4K · providers 2.7K · governance 2.2K+2.4K persistence-adjacent (see §4 table).
7. **Top 10 files:** tool_executor.py 1649 · stateless.py 1562 · transport/cli.py 1559 · repo_map.py 1532 · subagent_orchestrator.py 1331 · __main__.py 1171 · tools/registry.py 1104 · mcp/manager.py 975 · graph/executor.py 966 · workspace.py 915.
8. **Complexity concentrated:** turn/dispatch loops (`main` cc99, `_turn_inner` cc92, `_drive` cc64, `execute` cc54) — dispatch tables, justified but brittle.
9. **Authority concentrated:** `ToolExecutor.execute` → `authorize()` → `ApprovalGate` + workspace trust. Small, well-pinned choke point.
10. **Biggest architectural risk:** two parallel graph runtimes (legacy `core/agentic_graph.py` vs `wisp/graph/`) plus an 8-module arena/server import cycle — divergence and teardown risk (P1, §26).
11. **Strongest property:** authority containment — one enforcement path, 0 bare excepts, fail-closed validators, 384 security tests + 58 property/fuzz tests over the trust boundary.
12. **Over-engineered?** No for the core agent; borderline in CLI layering (3 dispatch systems) and enterprise M1–M7 scaffolding vs usage. Capability/LOC is healthy: ~6.3K prod lines bought phases 7–12.
13. **Do NOT change:** ToolExecutor/authorize chain, graph validation/fingerprint/audit chain, containment helpers' semantics, SQLite store schemas.
14. **Fix before continuing:** decide the fate of legacy `agentic_graph` + `core/subagent` strata (delete or isolate); break the arena/server cycle; unify the 3 path-containment implementations into one helper.
15. **100K+ bottleneck:** validator O(N·E) reachability patterns, 30-branch `__main__` dispatch, test-suite time (already ~15s for the focused subset).

## 1. Exact LOC census

Command: `cloc --exclude-dir=__pycache__,.pytest_cache --json wisp tests scripts` (+ per-area runs). Cross-check: `pygount --format=summary ... wisp tests scripts`.

| Area | Files | Blank | Comment | Code |
|---|---|---|---|---|
| `wisp/` | 441* | 12,627 | 12,711 | 58,949 |
| `tests/` | 379* | 13,719 | 5,589 | 50,329 |
| `scripts/` | 8 | 330 | 160 | 1,452 |
| root `.py` | 8 | 311 | 153 | 1,443 |
| **Core Python** | **836** | **26,987** | **18,613** | **112,173** |

\* file counts include non-Python (81 JSON + misc under `wisp/`, fixtures under `tests/`); 370 + 356 are `.py` files.

- TOTAL REPOSITORY LOC (cloc code): 330,398 across 2,249 files — includes satellites below.
- PRODUCTION LOC: 61,844. TEST LOC: 50,504. NON-CODE (docs md): ~32K (`docs/` 14.4K + 99 root/arch md).
- GENERATED: `android/app/build` intermediates (90 JSON), `graphify-out/cache` (80 JSON).
- EXCLUDED: `node_modules` (~4M+ lines: wisp-desktop 2.43M, vscode-extension 1.05M, wisp-tui 477K, wisp-ts 424K full-tree), `.venv`, `.git`, build/out/dist/release, caches.
- Satellites sans deps: wisp-desktop 24.8K (TS/Electron) · wisp-ts 9.0K · vscode-extension 5.3K · wisp-tui 4.7K · android ~1.8K hand-written (+generated build/) · agent/ 3.2K · warp-integration 483.

Discrepancy note: pygount reports 101,140 code vs cloc 112,173 — pygount files ~9.5K docstring lines under documentation, cloc under code. Both agree on comments (~19.3K) and file counts. Use cloc code + pygount structure together.

## 2. Language breakdown (repo-wide cloc code, desc)

Python 113,954 (744f) · JavaScript 125,306 (97f, mostly desktop bundled/minified) · JSON 28,865 (848f, fixtures+locks+generated) · TypeScript 20,746 (234f) · Markdown 17,677 (99f) · CSS 10,622 · XML 8,376 (android manifests) · Text 2,369 · Kotlin 1,368 · YAML 427 · Shell 141 · TOML 63 · rest <100 each.

Core project language: **Python 98%+** of production logic.

## 3. Ratios

- test/prod = 50,504/61,844 ≈ **0.82** (high; reflects adversarial/phase-test policy).
- security tests: 38 files / 5,415 loc / 384 tests. property/fuzz: 3 files / 687 loc / 58 tests. race: 7 files / 875 loc / 44 tests. integration/e2e: 21 files / 5,800 loc / 217 tests. unit: 285 files / 55,706 loc / 4,082 tests.
- tests per source file: 356/370 ≈ 0.96. test LOC per source LOC: ~0.86.
- Interpretation: healthy for a governance-critical agent; graph subsystem is the fortress (489 tests / 6.3K test loc on 4.8K source).

## 4. Architectural breakdown (production code LOC, AST-measured)

| Subsystem | Files | Code | %prod |
|---|---|---|---|
| agent_loop (+legacy graph, autonomous) | 9 | 4,770 | 7.7 |
| repl_cli (__main__, cli/, repl/, commands) | 26 | 5,632 | 9.1 |
| multi_agent (orchestrator, runners, worktrees) | 17 | 5,262 | 8.5 |
| graph runtime (all of wisp/graph/) | 22 | 4,798 | 7.8 |
| repomap + context system + symbols | 20 | 6,250 | 10.1 |
| tools (registry + 15 impls) | 21 | 3,973 | 6.4 |
| ToolExecutor | 1 | 1,649 | 2.7 |
| transports (cli/ws/headless/tui/file) | 14 | 3,814 | 6.2 |
| server/API (25 routes + app) | 33 | 2,920 | 4.7 |
| providers (9 files, 4 backends + mock) | 13 | 2,659 | 4.3 |
| workspace + mutation + checkpoints + git ctx | 6 | 2,443 | 3.9 |
| persistence (store/audit/runs/task/memory) | 15 | 2,414 | 3.9 |
| governance (auth/policy/contracts) | 23 | 2,176 | 3.5 |
| observability (trace/eval/bench/metrics) | 23 | 2,120 | 3.4 |
| TUI app | 41 | 1,629 | 2.6 |
| MCP (manager + servers + route + ext) | 6 | 1,406 | 2.3 |
| ACP (adapter/protocol/session) | 3 | 966 | 1.6 |
| config/env | 2 | 980 | 1.6 |
| extensions/plugins/skills | 14 | 1,811 | 2.9 |
| sandbox + lsp + speculative + misc | ~25 | 3,600 | 5.8 |

Public API per subsystem: measured 1,238 public top-level defs repo-wide; CLI 11 dispatcher cmds + 30 `__main__` branches; server 70 route decorators; SDK 2 entry points (`Wisp`, `run_graph`).

## 5–6. Map/graph summaries

See WISP_DEPENDENCY_MAP.md (human + machine sections, top-20 fan-in/out, 5 cycles). Hubs: `wisp.config` (40 fan-in), `server.main` (31 fan-out), `core.stateless` (27). God-module watch: `tool_executor.py`, `stateless.py`, `transport/cli.py` (top size + high coupling, single responsibility each — justified, monitor).

## 7. Complexity hotspots (AST cyclomatic, top)

`main` 99 · `_turn_inner` 92 · graph `cli._main` 84 · `_drive` 64 · legacy `_run_repl_legacy` 61 · `agent_websocket` 60 · `_render_event` 59 · `check_dangerous_command` 57 · `ToolExecutor.execute` 54 · `guarded_provider_stream` 53. All are dispatch/loop cores — complexity is structural (branch tables), not algorithmic. Maintenance risk: MEDIUM (dispatch tables rot by accretion; `__main__.py` is the worst).

## 8–20. Summaries (detail in HEALTH report)

- Duplication: 3 CLI layers; 2 graph runtimes; 3 path-containment impls; JSONL+SQLite audit split; live+unwired RepoMap; legacy `core/subagent` vs `multi_agent`.
- Errors: 1,437 handlers, **0 bare**, 802 broad-`Exception` (56%, concentrated in loop/transport resilience paths — posture, not sloppiness).
- Async: 18 `create_task` sites; bulk concurrency via gather/wait + semaphores + RLock stores; no global event-loop hazards found.
- Security: single authority path verified (no subprocess/tool side-channel in `wisp/graph/`; 22 subprocess modules are tools/sandbox/git/lsp — all behind ToolExecutor).
- MCP/ACP: thin adapters (1.4K/1.0K), coupled to server routes + tool registry, not core loop.
- Providers: 1.3K total, OpenAI largest (450); no provider logic in core abstractions (protocol + factory seam holds).
- Tests: fortress = graph/governance/workspace; desert = TUI screens, server routes (141 tests/15 files vs 70 endpoints), speculative/subagent-legacy.
- Dead code (LOW–MEDIUM confidence, name-reference heuristic): ~786 name-matches are mostly string-dispatched (`cmd_*`, route handlers, test helpers) — a handful of real candidates listed in HEALTH §18 (e.g. unwired `core/context/repomap.py`, `test_distill`/`summarizer` orphans) — verify before deleting.
- Deps: 18 direct + 5 dev, no bloat; heaviest: torch-adjacent skills are repo-external; `numpy/tiktoken` serve compression paths.
- Buckets: 175 <100 · 117 100–249 · 53 250–499 · 18 500–999 · 7 1000–1999 · 0 ≥2000. Healthy distribution.
- Ratio verdict: **moderate, capability-dense** — phases 7–12 added ~6.3K prod lines (~10%) for an entire graph/planner/optimizer/coding/workspace stack.

## 22. Phase history (wisp/ physical py lines)

pre-graph 65,257 → phase7 67,510 → phase8 68,591 → phase9 69,138 → phase10 69,899 → phase11 70,401 → phase12 71,548. Tests 50,172 → 55,474. Growth is **linear-to-decelerating** and capability-proportional; test LOC grows slightly faster (ratio 0.77→0.82).

## 23–27. Pointers

Interesting findings, forecast, scorecard, classified findings, and recommendations live in WISP_ARCHITECTURE_HEALTH.md. Machine data: wisp_codebase_metrics.json + /tmp/census_out/*.json (method reproducible via the two commands in §1).

WISP CODEBASE CENSUS — COMPLETE
