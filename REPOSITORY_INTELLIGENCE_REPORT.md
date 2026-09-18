# Repository Intelligence Report — Wisp

**Investigation type:** read-only architectural reconnaissance
**Repository root:** `/Users/philosopher/Documents/wisp`
**Revision:** `main` @ `5ea0ed9` (1,021 commits, 2026-04-30 → 2026-09-14), working tree **dirty**
**Method:** AST import graph + Tarjan SCC, pytest collection/execution, ruff, mypy, compileall, git history, targeted code reading
**Companion artifact:** `repository_manifest.json` (machine-readable)

> **Rule observed:** no file was modified. The only writes are the two deliverables in this report.
> Every claim below is labelled with evidence (`file:line` or exact command output) and a confidence level.
> **Where prior reconnaissance documents in this repo (`WISP_CODEBASE_CENSUS.md`, `WISP_ARCHITECTURE_HEALTH.md`, `WISP_DEPENDENCY_MAP.md`) disagree with what I observed, I say so explicitly.** Three of their headline claims are now stale.

---

## 1. System Identity

**Wisp is a local-first CLI coding agent with an enterprise governance layer.** It is a Python 3.11+ application that runs frontier cloud models and local inference behind one provider interface, and it wraps the usual single-turn agent loop with a runtime: multi-agent orchestration, AST-grounded repository context, per-turn mutation checkpoints with rewind, and Ed25519-signed governance policies with tamper-evident audit logs.

It is **not** a library-first project that happens to have a CLI. The centre of gravity is the CLI/server product; the SDK (`wisp.sdk.Wisp`, 181 lines) is a thin wrapper over the same composition root.

| Aspect | Finding | Confidence |
|---|---|---|
| Product class | Interactive + headless + server coding agent | HIGH |
| Distribution | `pip install -e .` → console script `wisp` | HIGH |
| Primary language | Python ~99% of production logic | HIGH |
| Platform | **POSIX only**; Windows explicitly unsupported (README:64) | HIGH |
| Licence | MIT | HIGH |
| Production size | `wisp/` = 366 `.py` files, **83,799 raw lines** | HIGH |
| Test size | 376 `test_*.py`, **5,263 `def test_`**, **5,845 collected** | HIGH |
| Maturity | Substantial and real, but with an unreconciled legacy strata and currently-red quality gates | HIGH |

---

## 2. Repository Map

```
wisp/                     366 py / 83,799 lines   ← the entire product
  core/        37 files  10,431  stateless turn engine, runtime, events, provider stream,
                                 verification, compaction, session, doctor
  multi_agent/ 17 files   6,793  orchestrator, runner, background agents, telemetry,
                                 worktrees, DAG, roles, resource budget
  graph/       22 files   5,600  durable graph execution engine (executor/scheduler/
                                 validator/store/artifacts/verifier/control/planner)
  tools/       21 files   4,859  42 tool schemas + impls, checkpoints, primitives
  transport/   14 files   4,721  Transport ABC + CLI/WS/Headless/TUI/File/Multi/Metrics
  server/      32 files   3,723  FastAPI control plane (27 routers, 70 routes)
  infra/       12 files   3,515  UnifiedStore, SecurityPolicy, audit, extensions,
                                 telemetry, hooks, circuit breaker
  cli/         12 files   3,170  dispatcher, repl runner, ui (blocks/guard/pager)
  repl/        11 files   2,164  legacy slash-command registry (strangler-fig tail)
  tui/         41 files   2,016  Textual app + screens
  providers/    9 files   1,711  Provider ABC + factory + ollama/openai/nvidia/openrouter/mock
  mcp/          2 files   1,304  MCP manager (stdio + http), trust gating
  sandbox/      2 files     711  Docker → Pty → Noop confinement router
  policy/       5 files     603  Ed25519 bundles, narrow-only precedence merge
  task/         5 files     494  task lifecycle, plan review, 5 profiles
  auth/         6 files     475  Principal, layered authorize(), workspace trust, secrets
  contracts/    7 files     464  versioned wire envelopes
  release/      6 files     459  SBOM, license audit, lock verify, diagnostics
  runs/         6 files     453  RunRecord, SQLiteRunStore, Scheduler
  trace/        6 files     345  span store, evidence export, OTLP
  eval/         3 files     118  eval scenarios + metrics
  + ~30 top-level modules (config, entry, composition, sdk, workspace, repo_map, …)

tests/         376 test files, 5,845 collected   (flat layout + reliability/ + security/)
docs/          95 markdown files (architecture, ADRs, superpowers plans/specs, archive)
scripts/       8 operator scripts (audit_cli_surface.sh, verify_subsystems.py, …)
.github/       4 workflows (ci.yml, qa.yml, desktop-ci.yml, release.yml)

Satellites (not the Python product):
  wisp-desktop/       Electron + React + TS  — most mature; own CI; spawns the Python server
  vscode-extension/   TS — connects over WebSocket
  android/            Kotlin/Compose — experimental; built in CI
  wisp-ts/            TS — ORPHAN (untracked; only dist/ + node_modules)
  agent/              Python — ORPHAN (zero imports either direction with wisp/)
  warp-integration/   patch + Warp skill, not a program
```

**Note:** the layout is **not** conventional for a Python package — there are ~30 flat top-level modules *plus* 20+ subpackages, and `wisp/` contains two modules named `test_*.py` (`test_distill.py`, `test_runner.py`) that are *production* code (traceback distiller, test runner) and would be collected by a bare `pytest` run from the root.

---

## 3. Technology Stack

| Layer | Technology | Evidence |
|---|---|---|
| Language | Python ≥3.11 (CI runs 3.12) | `pyproject.toml:12` |
| Build | setuptools ≥68, PEP 621 | `pyproject.toml:1-3` |
| Lock | `uv.lock` — 65 resolved packages | `uv.lock` |
| Direct deps | 14: requests, pyyaml, fastapi, uvicorn, websockets, textual, filelock, numpy, jsonschema, aiohttp, tiktoken, prompt_toolkit, pydantic, cryptography | `pyproject.toml:13-28` |
| Dev deps | 5: pytest, httpx, pytest-asyncio, ruff, mypy | `pyproject.toml:31` |
| Lint | ruff, **pyflakes rules only** (`select = ["F"]`), line-length 120 | `pyproject.toml:47-55` |
| Types | mypy **strict**, gated over exactly **8 files**, `follow_imports = "silent"` | `pyproject.toml:61-69` |
| Tests | pytest + pytest-asyncio; one custom marker (`live`) | `pyproject.toml:39-45` |
| Persistence | SQLite (WAL, `busy_timeout=5000`, `synchronous=NORMAL`) | `wisp/infra/store.py:70-77` |
| Crypto | Ed25519 via `cryptography` | `wisp/policy/` |
| Deploy | Dockerfile (`python:3.12-slim`), docker-compose (ollama + wisp), nginx.conf (TLS/WS proxy, not wired into compose) | `Dockerfile:1-32`, `docker-compose.yml` |

**Debt signals in the build config:**
- `setup.py` is a **legacy duplicate** that contradicts `pyproject.toml` on the Python floor (`>=3.10` vs `>=3.11`) — `setup.py:10` vs `pyproject.toml:12`. *Confidence: HIGH.*
- The mypy gate covers **8 of 366 files** — 2% of the package. The "strict" label overstates actual coverage. *Confidence: HIGH.*

---

## 4. Component Model

Responsibilities, with the state each component owns:

| Component | Responsibility | Owns state? | Risk |
|---|---|---|---|
| `core/stateless.py` `WispAgentCore` | The turn loop: prompt assembly → provider stream → tool-call parse → approval → execute → append → terminal | No (by design) | **CRITICAL** |
| `core/runtime.py` `AgentRuntime` | Session CRUD, per-session locks, core caching, compaction, steering, background run records | **Yes** — the stateful half | **CRITICAL** |
| `core/provider_stream.py` | Stream guard: first-token + chunk deadlines, transient retry, honest truncation | No | HIGH |
| `core/verification.py` | Completion gate: blocks finish until verification postdates the last mutation | Per-turn | HIGH |
| `tool_executor.py` `ToolExecutor` | The authority choke point + dispatch + hooks + metrics | Config only | **CRITICAL** |
| `auth/decision.py` `authorize()` | 6-layer narrowing: policy → principal → workspace → sensitivity → args → approval | No | **CRITICAL** |
| `infra/store.py` `UnifiedStore` | Single SQLite file: sessions, runs, events, memory, background_runs, session_events, idempotency, run_transitions, trace_spans, task_plans | **Yes — the durable root** | **CRITICAL** |
| `graph/executor.py` `GraphExecutor` | Durable DAG execution: scheduling, joins, retries, budgets, checkpoints, resume | Per-run | HIGH |
| `multi_agent/subagent_orchestrator.py` | Subagent patterns: run/parallel/map-reduce/vote/chain/DAG with guards | Per-orchestrator | HIGH |
| `multi_agent/background.py` | Background agent registry + lifecycle pub-sub | **Yes** | HIGH |
| `transport/cli.py` `CLITransport` | REPL loop, rendering, the interactive approval state machine | UI state | MEDIUM |
| `server/main.py` | FastAPI control plane + auth + rate limit + headers | None | HIGH |
| `sandbox/router.py` | Docker → Pty → Noop routing, TTL-cached, silent failover | Cached decision | HIGH |

**Architectural shape (hypothesis, §18):** a *layered/hexagonal aspiration* — stateless core + transport adapters + infrastructure ports — that is realised in the newer subsystems (`graph/`, `auth/`, `runs/`, `contracts/`) and only *partially* realised in the older ones (`core/stateless.py`, `__main__.py`, `transport/cli.py`, `repl/`).

---

## 5. Dependency Graph

Independently recomputed over **366 modules / 841 edges** (AST, absolute + relative `wisp.*` imports).

**Dependency hubs (fan-in):**

| Dependents | Module |
|---|---|
| 42 | `wisp.config` |
| 27 | `wisp.server.deps` |
| 20 | `wisp.server.routes.workspace` |
| 18 | `wisp.colors` |
| 17 | `wisp.graph.types` |
| 16 | `wisp.core.events` |
| 15 | `wisp.tools._utils` |
| 13 | `wisp.infra.security` |
| 11 | `wisp.auth.secrets` |
| 11 | `wisp.infra.store` |

**High fan-out:** `server.main` 31 · `core.stateless` 29 · `__main__` 28 · `composition` 24 · `tui.screens.workspace` 23 · `tool_executor` 22 · `entry` 22.

**Cycles (7 SCCs) — and a correction:**

```
[8] wisp.repl.commands <-> .agents .core .doctor .files .provider .session_cmds .skills
[3] wisp.cli.ui.guard <-> wisp.transport <-> wisp.transport.cli
[3] wisp.server <-> wisp.server.main <-> wisp.server.routes.jsonrpc
[2] wisp.provider_catalog <-> wisp.provider_select
[2] wisp.transport.tui <-> wisp.tui.screens.workspace
[2] wisp.core.doctor <-> wisp.core.runtime
[2] wisp.cli.dispatcher <-> wisp.graph.cli
```

> **Correction to prior docs.** `WISP_ARCHITECTURE_HEALTH.md` names an **8-module cycle** `arena ↔ background_agent ↔ entry ↔ server.main ↔ routes{arena,diff,review,runs}` as its **P1-2 teardown risk**. My independent SCC run **does not reproduce it**. The largest cycle today is the `repl.commands` package (a registry pattern where submodules register into the package `__init__` — arguably a false positive, not a true cycle). The genuine cross-layer cycle worth noting is `cli.ui.guard ↔ transport ↔ transport.cli` (UI layer ↔ transport layer) and `cli.dispatcher ↔ graph.cli` (execution layer importing the REPL layer). *Confidence: HIGH (method is reproducible).*

**Also stale:** the same docs name legacy `core/agentic_graph.py::GraphRunner` as **P1-1 divergence risk**. **That file does not exist.** `wisp/core/subagent/` is empty (only `__pycache__`). The legacy strata has since been removed. *Confidence: HIGH.*

**Real duplication that survives:** `pathsec.py` is the canonical containment helper and `tools/_utils._resolve_path`, `graph/artifacts._contain`, `workspace._contain` are thin aliases to it — but **`server/routes/files._resolve_path` and `sandbox.resolve_sandbox_cwd` are two independent re-implementations** of the same realpath+prefix logic. *Confidence: HIGH.*

---

## 6. Entry Points

**CLI (primary).** `wisp/__main__.py:1132 main()` → manual global-flag extraction (`:1164`) → a **31-entry dispatch table** (`_SUBCOMMAND_TABLE`, `:1399-1431`) → `cmd_*` handlers → `wisp.entry.run_mode()`. Implicit mode `wisp "prompt"` falls through to `cmd_run` (`:1451-1455`). Console script: `wisp = wisp.__main__:main` (`pyproject.toml:34`).

Subcommands: run, repl, tui, session, compact, skills, setup, config, check, models, memory, mcp, policy, trace, replay, audit, task, completion, release, git, plan, progress, diagnose, locks, changes, acp, server, swarm, agents, graph, bench.

**HTTP/WS.** `wisp/server/main.py:200 main()` → uvicorn. **27 routers, ~70 routes.** Exactly **one WebSocket**: `/ws/agent` (`server/routes/agents.py:67`). `POST /api/jsonrpc` exposes `WispAppServer` (`app_server.py:18`).

**Others.** SDK (`sdk.py:78`), headless (`headless.py:31`), TUI (`entry.py:824`), ACP/Zed (`acp_adapter.py:397`, protocol `2025-03-26`), graph (`graph/api.py`).

**Three coexisting CLI dispatch systems** (a real maintenance cost):
1. `__main__` 31-branch table — authoritative for process entry.
2. `cli/dispatcher.py` `Dispatcher` (11 built-ins) — authoritative for slash names it registers.
3. `repl/commands/` legacy `@register` registry (~28 handlers) — the strangler-fig fallback, reached via `_dispatch_legacy` (`dispatcher.py:141-191`).

---

## 7. Runtime Model

**Execution model: single asyncio event loop per process, with thread bridges for blocking work.**

One turn, from entry to side effect (`wisp/core/runtime.py:321` → `wisp/core/stateless.py:196`):

```
Transport.recv() → prompt string
  ↓
AgentRuntime.run_turn(session, prompt)          runtime.py:321
  ├─ validate prompt type                        :334
  ├─ get_or_create_session (validates inputs)    :274-292
  ├─ acquire per-session asyncio.Lock            :340-362
  ├─ crash replay → maybe_compact → prune_live   :365-388
  ├─ append user message                         :401
  └─ _get_core(sid)  (cache key: sid+fingerprint):411-433
        ↓
WispAgentCore.turn(session, prompt, handler)     stateless.py:196
  ├─ build messages + boot seed                  :231-258
  ├─ _build_system_prompt  (BoundedPromptCache)  :261 / :1094
  ├─ _get_tool_schemas → role filter → capability filter  :265-282
  ├─ asyncio.timeout(turn_timeout)               :291
  └─ _turn_inner()  ── the loop ──               :307
        ├─ VerificationFloorGuard init           :333
        ├─ guarded_provider_stream()             :365 → provider_stream.py:75
        │     └─ _stream_events_async()          :930
        │           native async OR sync-in-thread + asyncio.Queue  :967-1043
        │           wrapped in CircuitBreaker    :1046-1092
        ├─ _normalize_event() → yield flat dict  :371 / :636
        ├─ tool-call parse → _ensure_intake_id   :393-400
        ├─ batch expansion OR single path        :402-631
        ├─ _validate_tool_args (jsonschema)      :2111
        ├─ ApprovalGate.check_decision()         :467-484
        ├─ ExtensionHost intercept               :487-520
        ├─ _execute_tool() → ToolExecutor.execute :1776 → :1840
        ├─ append assistant+tool messages + provenance gate  :828-871
        └─ terminal: done | incomplete error | verification reject | iteration wrap-up
  ↓
runtime persists in finally: _serialize_tool_exchanges + to_thread(_persist_turn_state)  :564-590
```

**Statelessness — correcting a documented claim.** `AGENTS.md` and `ARCHITECTURE.md` both assert *"`WispAgentCore` has no mutable state."* **That is false as written.** The dataclass holds two mutable instance fields — `_approval_gate` (`stateless.py:176`, lazily written `:2189`) and `_circuit_breaker` (`:177`, built `:194`) — it mutates the passed-in `session` dict, and it depends on three module-level mutable globals:

- `_ASSEMBLER` (`:65`) — process-wide singleton
- `_SYSTEM_PROMPT_CACHE` (`:72`) — `BoundedPromptCache(maxsize=64)`, thread-locked (`prompt_cache.py:43`)
- `_CONTEXT_TTL` (`:82`) — a dict written by `_ttl_get` (`:85`). **Corrected in Phase 2 (§21): it is keyed by `kind`, not by workspace, so it holds at most 3 entries — bounded, not unbounded.** It has no lock, but the stored `key` is compared before use, so a race costs a cache miss, never a cross-workspace leak.

The accurate statement is: *the core does not own conversation history; the runtime does.* The core is stateless **with respect to history ownership**, not literally state-free. *Confidence: HIGH.*

**Core caching — claim confirmed.** `_get_core` keys on `(session_id, config.fingerprint())` (`runtime.py:830-833`); `MAX_SESSION_CORES = 32` (`:240`); eviction pops insertion-order (`:840-841`). It is genuinely **FIFO, not LRU** (no `move_to_end` on access), despite `_session_locks` eviction being LRU (`:944-950`). *Confidence: HIGH.*

---

## 8. Top Execution Flows

Ten flows traced to the side effect. `F#` used in the risk map.

**F1 — Interactive REPL turn.** `cmd_repl` → `entry._run_repl` → `ReplRunner` → `runtime.run_turn`. Approval via `CLITransport.approve` (`transport/cli.py:729`), a real state machine: autonomous shortcut (`:755`) → non-interactive auto-deny (`:759`) → session memory (`:766`) → spinner stop → diff preview (`:779`) → typeahead pause (`:792`) → `_approval_lock` (`:796`) → key loop y/Y/v/a/d/c/N (`:814-884`). Reader thread uses `select()` with 0.2 s cancel polling (`:886-932`). *Tests: extensive (`test_transport_cli.py`, `test_repl_audit_pindown.py`).* **Risk: medium** (complexity).

**F2 — Single-shot `wisp "prompt"`.** `cmd_run` → `entry.run_mode("run")` → `_run_single_prompt` → same turn path, one turn then exit.

**F3 — Headless `--print`.** `cmd_print` (`__main__.py:224`) → `run_headless` → `HeadlessTransport` (collects events, `approve` defaults **False**, `headless.py:28`) → JSON on stdout. Used by CI. **Risk: low, but note the fail-closed approval default.**

**F4 — Tool call authorization.** `_execute_tool` → `ToolExecutor.execute` (`tool_executor.py:638-965`). Ordered gates: dangerous-command pre-block (`:659`) → policy hard-DENY (`:671`) → `authorize()` (`:690`) → repeat guard (`:714`) → fetch breaker (`:723`) → PRE_TOOL hooks (`:730`) → plan-mode guard (`:737`) → danger re-check (`:745`) → permission-mode guard (`:753`) → **approval gating** (`:760-830`) → event pre-hooks (`:833`) → dispatch (`:847-906`) → audit/metrics/post-hooks (`:908-965`). Then `_execute_tool` (`:1276`) routes via `_SPECIAL_TOOL_ROUTES` (`:357`) → MCP → `run_bash` → generic registry with `_skip_authorize=True` (`:1330`). *This is the best-engineered flow in the repo.*

**F5 — File mutation + checkpoint + rewind.** `write_file`/`edit_file`/`edit_file_multi` → `snapshot_before_mutation` (`tools/checkpoints.py:141`) → mutate → `drop(seq)` on failure (`filesystem.py:144,269,363`). `CheckpointStore` is **in-memory, dual-bounded** (20/file, 10 MB total; `checkpoints.py:32-33`), oldest-first eviction. `tool_rewind` (`:163-223`) snapshots *pre-restore* state (`kind="rewind"`, `:206`) so **rewind is rewindable**; `content=None` deletes; refuses hook-controlled paths (`:198`). *Not durable across process restart* (documented, `:6-9`).

**F6 — Provider stream with stall guard.** `guarded_provider_stream` (`provider_stream.py:75`): first-token deadline (`:131`), chunk deadline (`:156`), transient-error hold+retry with jittered backoff (`:182-194`, `:285-334`), bare-terminal detection (`:269-281`), mid-stream stall after output → `chunk_stall` (`:257`), exhaustion error (`:336`). Wrapped by `CircuitBreaker` (`stateless.py:1046-1092`).

**F7 — Foreground subagent.** `spawn` tool → `SubagentOrchestrator.run` (`subagent_orchestrator.py:711`) → guards: depth (`:722`), role (`:734`), timeout/iteration (`:748`), cache (`:780`), **token-budget admission with 1000-token headroom floor** (`:86`, `:787`), worktree resolve (`:816`), semaphore-bounded runner (`:840`), one ×1.5 timeout retry (`:852`), schema validation (`:883`), success-only cache/persist/telemetry (`:889`), patch capture/apply under `_patch_lock` (`:902-924`). Worker tool visibility filtered by `_effective_child_tools` (`_runner.py:23-42`).

**F8 — Background subagent.** `spawn_background` → `BackgroundAgentManager.launch` (`background.py:247`): durable `Scheduler.admit` when a run store exists, else in-memory head-count; telemetry ring registration (`:274`); `asyncio.create_task(_run_entry)` (`:282`). Settlement reaches the parent on its **next turn** via `drain_notifications` (`:589-623`), gated by a `notified` flag. Bounds: 8 running / 50 finished / 300 s lease.

**F9 — Durable graph run.** `graph run` → `graph_from_yaml` (safe_load, strict coercion) → `validate_graph` (`validator.py:28`) → `GraphExecutor.run` (`executor.py:145`) → `_drive` with `asyncio.Semaphore(min(max_concurrency, budget))` (`:251`), per-provider semaphores (`:253`), `settle_one` sliced at 0.5 s for cancel responsiveness (`:308`), per-transition checkpoints (`:468`). Resume (`:149-231`) refuses on graph-hash mismatch (`:155`), workspace mismatch (`:167`), corrupt checkpoint (`:179-196`); replays `success` rows (`:206`), re-runs `running`/`pending` if idempotent else cancels (`:212-219`), and **does not trust recorded terminal failures** (`:214-215`). *This is the strongest subsystem in the repo.*

**F10 — Live WebSocket turn.** `/ws/agent` (`server/routes/agents.py:67`) — auth is **per-frame**, not the FastAPI dependency (`:131-147`), so **the rate limiter does not apply to WS**. `WebSocketTransport` routes connection↔session (`websocket.py:270-274`) and uses a `ContextVar _active_turn` (`:37`, `:324`) so concurrent turns deliver approvals to the right client. `disconnect` fails closed, denying pending approvals (`:334-357`).

**Supporting flows also traced:** session compaction (`runtime.py:857` → `compaction.py:63`, LLM summarize with truncation fallback), verification gate (`verification.py:110-168`), MCP tool call (forced through approval, hard-blocked in READ_ONLY), ACP permission bridge (60 s blocking `threading.Event`, `acp_adapter.py:365-394`).

---

## 9. Data Model

Major entities and lifecycle. **Ownership is the important column** — most drift bugs live where "who may write this" is implicit.

| Entity | Created | Validated | Stored | Mutated by | Owner |
|---|---|---|---|---|---|
| `session` dict (`id, model, workspace, messages, compaction_history`) | `runtime.get_or_create_session` (`:274`) | input validation (`:285-292`) | `store.save_session` (`:347`) | **runtime and core both** (core writes into the shared dict at `stateless.py:754,847,871,884,897`) | Runtime — but the shared-mutation contract is implicit |
| `WispConfig` | many construction sites | `validate_or_raise` (`config.py:883`) | `~/.config/wisp/config.json` (0o600) | `replace()` returns a new instance | Nominally immutable; **not frozen** |
| Session events | `session_repo` append | — | `session_events` table | append-only | Runtime |
| Tool call/result envelopes | provider stream | `_validate_tool_args` (jsonschema, `stateless.py:2111`) | inside `messages` | core | Core |
| Checkpoint snapshot | `snapshot_before_mutation` | — | **in-memory only** | `drop` on failure | `CheckpointStore` (per-workspace) |
| Graph run + node runs | `GraphExecutor.run` | `validate_graph` (fail-closed) | `graph_*` tables | executor | `GraphStore` |
| Graph artifacts | `ArtifactStore.put` | type allowlist + containment + redaction + size cap | `graph_artifacts` | immutable | `ArtifactStore` |
| Policy bundle | admin CLI / control plane | Ed25519 verify + expiry + revocation | `~/.config/wisp/` | replace-whole | `policy/` |
| Approval decision | `ApprovalGate` / transport | — | `_approval_states` (in-memory) + `run_transitions` | runtime | Runtime |
| Background run | `BackgroundAgentManager.launch` | `Scheduler.admit` | `background_runs` | manager | Manager |
| Trace span | trace store | **redaction at append** | `trace_spans` | append-only | Trace store |

**Notable:** `session` is the one entity with **two writers** (runtime and core) and no type-level ownership enforcement. Everything else has a single owner. *Confidence: HIGH.*

---

## 10. State Model

| Class | Where | Who creates / owns / mutates | Sync | Lifecycle | Persistence |
|---|---|---|---|---|---|
| Session dict | memory, passed by reference | runtime creates; **runtime + core mutate** | per-session `asyncio.Lock` (`runtime.py:342`) | until evicted/saved | SQLite per turn (`:649`) |
| `_session_cores` | `AgentRuntime` | runtime | `threading.Lock` `_core_lock` (`:242`) | FIFO, cap 32 | none |
| `_session_locks` | `AgentRuntime` | runtime | self | LRU, skips held locks (`:956`) | none |
| `_steering_inbox`, `_approval_states`, `_touched_files`, `_turn_counts`, `_session_access` | `AgentRuntime` | runtime | LRU eviction at `_max_session_state` (`:268`) | per-session | partial |
| `_SYSTEM_PROMPT_CACHE` | module global | process | `threading.Lock` (`prompt_cache.py:43`) | LRU 64 | none |
| `_ASSEMBLER` | module global | process | none | process | none |
| `_CONTEXT_TTL` | module global | process | none | **bounded — 3 fixed keys** (Phase 2 correction) | none |
| `_SHARED_EXECUTOR` | `async_utils.py:25` | process | `_loop_lock` (`:54`) | process | none |
| `UnifiedStore` | SQLite file | composition | `threading.RLock` (`:32`) + thread-local conns (`:84`) | process | **durable** |
| `_cancelled`, `_approvals` (graph) | `GraphExecutor` | caller thread + drive loop | **none** | **never pruned** | no |
| `_entries`, telemetry rings | `BackgroundAgentManager` | manager | none | pruned only on explicit `prune()` | best-effort |
| Checkpoints | memory | per-workspace | none | bounded 20/file, 10 MB | **no** |
| `sub_event_queue` | `ContextVar` | per-task | task-local by construction | per tool call | no |

**Hidden / implicit state worth naming:**
- `_CONTEXT_TTL` is an unlocked module-global cache written during prompt assembly. **Phase 2 correction: it is keyed by `kind` — only 3 literals (`"git_ctx"`, `"lint_ctx"`, `"module_summary"`, `stateless.py:1445,1542,1654`) — so it is bounded to 3 entries. It is NOT a memory leak.** The real cost is cache thrash: alternating workspaces invalidate each other every call. The stored `key` is compared before serving (`:89`), so no cross-workspace data leak. *Confidence: HIGH (read + reasoned).*
- The **shared `session` dict** is the load-bearing implicit contract between runtime and core.
- `_cancelled`/`_approvals` on `GraphExecutor` grow per run id and are never pruned — a long-lived executor leaks. *Confidence: HIGH.*
- `BackgroundAgentManager._entries` grows unbounded unless `prune()` is called explicitly; `launch` never auto-prunes. *Confidence: HIGH.*

---

## 11. Concurrency Model

```
Process
 ├── asyncio event loop (one per process; CompositionRoot registers the shared executor)
 │    ├── turn task (per session, serialized by per-session asyncio.Lock)
 │    │    └── provider stream
 │    │         ├── native async generator, OR
 │    │         └── raw threading.Thread producer → asyncio.Queue (stateless.py:1014)
 │    ├── tool execution task (asyncio.create_task, tool_executor.py:880)
 │    │    └── event channel via ContextVar sub_event_queue (task-local)
 │    ├── subagent tasks (semaphore-bounded: orchestrator, DAG, graph executor)
 │    ├── background agent tasks (create_task, background.py:282/513)
 │    └── server tasks (WS pusher, per-turn task, swarm run)
 └── thread pool (shared, size 8, composition.py:80-96)
      └── blocking work: persistence, compaction, sync providers, tool fallback
```

**Primitives actually present:**
- `asyncio.Semaphore`: 7 sites — orchestrator (`:414,642,1077,1203`), DAG (`dag.py:184`), graph executor (`:252,260`).
- `asyncio.Lock`: 7 sites — per-session (`runtime.py:342`), `_patch_lock` (`orchestrator:415`), circuit breaker, shared context, CLI approval, server connections.
- `asyncio.Queue`: 7 sites — provider bridges, background pub-sub, telemetry, tool executor.
- `threading.RLock`/`Lock`: `UnifiedStore` (`store.py:32`), `_core_lock` (`runtime.py:242`), `BoundedPromptCache`.
- `asyncio.create_task`: 13 sites.

**Suspicious boundaries (observed, not asserted as bugs):**
1. **Background admission bypass** — `send()` resets an entry to RUNNING and spawns a task with **no `_max_running`/scheduler admission check** (`background.py:468-524`), so continuation can exceed the advertised 8-running bound. *Confidence: HIGH (code-visible).*
2. **Cancel/resume race** — `GraphExecutor.cancel()` mutates `_cancelled` and the DB from the caller thread (`:233-242`) while `_drive` reads it (`:316,373`); no lock. *Confidence: MEDIUM.*
3. **Unlocked lazy init** — `GraphExecutor._stores()` assigns `self._store`/`self._artifacts` without a lock (`:91-96`). *Confidence: HIGH.*
4. **Per-instance patch lock** — `_patch_lock` is per-orchestrator (`orchestrator:415`), so two orchestrator instances can apply patches to one workspace concurrently. *Confidence: MEDIUM.*
5. **TUI task ownership is partial** — `OwnedTasks` (`tui/task_owner.py:18`) exists precisely to prevent leaks, but three bare `create_task` sites remain (`screens/workspace.py:195,418`, `data/ws_client.py:47`). *Confidence: HIGH.*
6. **`sub_event_queue` via ContextVar** is a genuinely good invariant, and the code comment at `tool_executor.py:870-877` explains exactly why (a shared executor would otherwise let child A's events clobber child B's channel). **This is the model to copy.**

---

## 12. Failure Model

| Failure | Detection | Handling | User-visible result |
|---|---|---|---|
| Provider transient (429/5xx/socket) | `provider_stream.py:182` | hold + retry with jittered backoff (`:285-334`) | transparent, or error after exhaustion |
| First-token stall | `:139` | `stalled=True`, retry | retry, then error |
| Empty / bare-terminal stream | `:269-281` | error "ended without terminal marker" | error event, **no `done`** |
| Mid-stream stall after output | `:257` | `provider_status("chunk_stall")` | partial output + status |
| Circuit open | `stateless.py:1049` | `provider_status("circuit_open")` + error | error |
| Turn wall-clock timeout | `stateless.py:298` | `CODE_TURN_TIMEOUT` error + done | honest timeout |
| Non-complete provider round | `:703-726` | incomplete error, no `done` | honest incomplete |
| Tool exception | `:1888` | `{"status":"error","data":str(e)}` | error fed back to model |
| Mutating tool on incomplete round | `:1795-1813` | refused | refusal envelope |
| Approval deny/timeout/cancel | `approval_gate.py:112-157` | typed denial (`USER_DENIED`/`APPROVAL_TIMEOUT`/`CANCELLED`) | structured denial |
| Provenance violation | `stateless.py:863` | **fatal** — turn ends | protocol-integrity error |
| Iteration limit | `:887-928` | wrap-up tool-less call, then budget error or done | honest budget |
| Compaction failure | `runtime.py:897`, `compaction.py:155` | truncation fallback | degraded context, no crash |
| Docker absent | `sandbox/router.py:214` | failover to Pty → Noop, **silent** | loud `UNCONFINED` warning per call |
| Server without API key | `server/main.py:160-197` | `SystemExit(2)` on non-loopback | refuses to boot |

**Genuinely strong:** every terminal path is *honest* — the code prefers "no `done` + an error" over a fabricated success, and the `E11xx`/`E21xx`/`E51xx` error codes are structured (`core/events.py:301-304`).

**Weak spots:** 802 broad `except Exception` handlers (concentrated in loop/transport resilience paths — a deliberate posture, but it masks faults in loop code); the sandbox failover is silent at the decision point; and the verification heuristic below.

---

## 13. Security Boundary Model

```
UNTRUSTED                                    TRUSTED
  model output ─┐
  workspace    ─┼─→ ToolExecutor.execute ─→ authorize() ─→ ApprovalGate ─→ sandbox ─→ FS/proc/net
  extensions   ─┤        ▲  THE choke point (model path)
  network peer ─┘        │
  graph YAML  ───────────┘ (validator is advisory only)
```

**The model path is genuinely chokepointed.** `stateless._execute_tool` (`:1840`) is the only route to `ToolExecutor.execute`; when no executor is wired the fallback is **risk-gated to `ToolRisk.READ` only** (`:1846-1887`) — writes are refused. Subagents share the executor (`composition.py:304,322`; `_runner.py:428`). Graph AGENT nodes go runner → orchestrator → agent loop; `wisp/graph/` contains **zero** subprocess/`execute_tool` calls. *Confidence: HIGH.*

**`authorize()` is a real 6-layer narrowing model** (`auth/decision.py:41-139`), each denial naming its `controlling_layer`:
L0 organization policy → L1 principal capabilities → L2 workspace trust → L3 risk vs sensitivity → L4 arguments/target → L5 approval. *Confidence: HIGH.*

**Where the "only action path" claim breaks — REST control plane.** Three routes mutate state with **API-key auth only**, with no `authorize()` and no `require_tool_allowed`:

| Route | Effect | Evidence |
|---|---|---|
| `POST /api/git/commit` | `git add -A` + `git commit` on the workspace | `server/routes/git.py:67-81` |
| `POST /api/hooks` | writes `.wisp/hooks/*.json` — **the exact directory the agent is denied write access to** | `server/routes/hooks.py:75-112` |
| `POST /api/context` | writes `.wisp/rules.md` (a persistent instruction file) | `server/routes/context.py:38-45` |

Compare `server/routes/files.py:122-172` and `bash.py:35`, which *do* call `require_tool_allowed`. So the REST surface is **inconsistently gated**. The hook-write route is the sharpest edge: the agent's own hook-dir protection (enforced three ways — `_utils._is_hook_controlled_path`, `authorize` L4, and the deny-list) is **bypassed at the HTTP boundary**. *Confidence: HIGH (verified by reading the route bodies).*
*Mitigating context:* the REST principal is the authenticated developer, not the model — so this is a control-plane consistency defect rather than a model-escape. It matters if the API key is ever reachable from model-influenced code, or under multi-tenant deployment (which the threat model already flags as unimplemented).

**Other boundaries:**
- **Sandbox:** `SandboxRouter` tries Docker → Pty → Noop, TTL-cached (60 s), silent failover (`router.py:214-246`). Note **two routing mechanisms coexist**: `tools/bash.py:78` uses the singleton `get_sandbox()`, while `tools/primitives.py:83` uses `get_router()`.
- **Dangerous-command check:** a regex deny-list (`tools/_utils.py:75-245`) catching sudo/`rm -rf /`/dd-to-device/curl|sh/fork bombs/`.wisp/hooks` writes. Its own docstring (`:79`) states it **cannot catch obfuscation** — correctly documented as a heuristic, not a boundary.
- **Secrets:** `auth/secrets.py` is canonical (12 pattern families) and applied at record construction. Two weaker/duplicating implementations exist: `graph/security.py` (forks extra patterns) and `infra/security.py:296` (key-name masking only, for display).
- **Path containment:** canonical `pathsec.resolve_contained` (`:13-43`) with NUL/control-char rejection + realpath + separator-anchored prefix, plus `O_NOFOLLOW` reads/writes in `tools/_utils.py:306-386`. **Two divergent re-implementations** remain (`server/routes/files.py:40-62`, `sandbox/__init__.py:51-66`).
- **Server auth:** `verify_api_key` accepts header only (query params deliberately removed as a log-leak, `deps.py:175`); 24 h key-rotation grace; SQLite rate limiter 30 req/60 s; security headers + opt-in HSTS (`main.py:98-126`). `--no-auth` is loopback-only and loudly warned (`main.py:185-197`).
- **MCP:** tools **do not** bypass the executor (`tool_executor.py:1300`); they are hard-blocked in READ_ONLY (`:1203`) and **always forced through approval** (`:1218-1223`). Workspace-sourced `always_load` servers require explicit `WorkspaceTrustManager` consent to prevent clone-and-run RCE (`mcp/manager.py:825-835`).

---

## 14. Performance Model

| Hot path | Class | Evidence |
|---|---|---|
| Graph validator contract section | **Likely** | `_validate_contracts` scans all edges inside the per-node loop → **O(N·E)** (`validator.py:195-203`); `_reachable` itself is O(N+E) (`:158-169`). Prior measurement: 67–79 ms of 147 ms at 512 fan-out. |
| RepoMap build + PageRank | **Likely** | `repo_map.py:197 build()`, `:502/:608 _compute_pagerank`; cached to disk (`:761-824`) with skeleton fast-path (`:222`) |
| Prompt assembly | **Measured (mitigated)** | `BoundedPromptCache` LRU 64 keyed by workspace+mtime (`stateless.py:72,1176,1245`) |
| Per-turn context scans (git/lint/module) | **Likely** | `_ttl_get` memo (`stateless.py:85`) — but the memo is **unbounded and unlocked** |
| Telemetry ring append | **Likely** | every `append` recomputes `sum(len(e.text))` over the deque under the lock → O(n²) amortised (`telemetry.py:142-145`); byte accounting uses chars not UTF-8 bytes (`:132`) |
| Token accounting | **Potential** | `tiktoken` is a declared dependency; no `lru_cache` on the encode path was found |
| SQLite contention | **Potential** | single DB file, WAL + `busy_timeout=5000`; per-instance RLock + thread-local connections (`store.py:32,84`) |
| Compaction | **Potential** | LLM summarization inside the turn (`compaction.py:112`) via `run_in_executor` (`:154`) |
| Graph fan-out | **Measured (bounded)** | semaphore `min(max_concurrency, budget)` (`executor.py:251`); validator caps MAX_NODES 1024 / MAX_EDGES 8192 (`validator.py:17-22`) |
| `_CONTEXT_TTL` growth | **Potential** | unbounded module-global dict |

**No profiling culture found:** there is no benchmark harness for the hot paths above (`wisp/benchmark/` measures *task success*, not performance), no `py-spy` integration in CI, and no perf regression test. *Confidence: HIGH.*

---

## 15. Test Model

| Metric | Value | Evidence |
|---|---|---|
| Test files | 376 (`test_*.py`) | filesystem |
| Test functions | 5,263 | `grep -c "def test_"` |
| Collected | **5,845 tests, 3 collection errors** | `pytest --collect-only -q` |
| Test LOC | ~50 K | consistent with prior census |
| test/prod ratio | ~0.82 | prior census; broadly confirmed by file counts |
| Layout | **flat**, not source-mirroring | contradicts `AGENTS.md:112` |

**Corrections to documented claims:**
- `AGENTS.md:90` says "310 test files, ~4,200 tests". Actual: **376 files, 5,845 collected** — stale by ~20–40%.
- `AGENTS.md:9` says transport tests use `_MockRuntime` + `_MockIO`. **No `_MockIO` class exists**; the real patterns are `MockProvider` (151 refs), `FakeProvider` (16), and locally-defined `_MockRuntime` (5 definitions).
- Tests do **not** mirror source paths (`wisp/transport/progress.py → tests/test_progress.py` is the exception, not the rule).

**Categories present:** `@pytest.mark.asyncio` 832, `parametrize` 58, `skipif` 8, `live` 3, `xfail` 2. Directories: `reliability/` (17 files), `security/` (20), `e2e` 9, contract 15, integration 11, race 7, fuzz 2 (hand-rolled — **no Hypothesis anywhere**).

**Fortress vs desert:**
- **Fortress:** graph, subagent orchestration, workspace, policy/auth, transport/CLI — deep adversarial suites with real fixtures (`tests/conftest.py:22 isolated_wisp_env`, `:58 autouse _neutralize_server_auth`, `tests/reliability/conftest.py:9 _hermetic_home`).
- **Desert:** server routes with no matching test — `codebase.py`, `complete.py`, `diagnostics.py`, `jsonrpc.py`, `models.py`, `plugins.py`, `suggestions.py`, `swarm.py`. Subpackages with **zero** direct test files: `wisp/infra/`, `wisp/plugins/`, `wisp/providers/` (indirect only), `wisp/ui/`, `wisp/mcp_servers/`.

**Known-uncollectable tests (verified):** `tests/test_auto_delegate_defense.py`, `test_delegation_research_only.py`, `test_input_and_interrupts.py` fail at **import** time — two import `wisp.multi_agent.delegation` (**that module no longer exists**) and one imports `_has_unclosed_brackets` from `wisp.transport.cli` (**that symbol no longer exists**). Because pytest aborts on collection errors by default, a bare `pytest tests/` reports `3 errors in 1.49s` and **runs zero tests**.

> **Critical nuance for CI:** all three erroring files are **untracked** (`git ls-files` confirms), as is the ruff-failing `wisp/multi_agent/_circuit_breaker.py`. CI (`.github/workflows/ci.yml:36,40`) does **not** pass `--ignore`, but it also checks out only tracked files — so on a clean CI checkout these files are absent and the test/ruff steps are not blocked by them. **They break the local working tree, not CI.** The reverse is true for mypy: the 16 failing errors are in `wisp/core/runtime.py` and `wisp/core/stateless.py`, which **are tracked** — see below.

**Hermeticity gap (Phase 2: root-caused).** The full suite **hangs at 59%** on exactly one test: **`tests/test_provider_select.py::TestProviderCommand::test_switch_requires_key_when_missing`** (`test_provider_select.py:210`). It deletes `WISP_API_KEY`/`OPENAI_API_KEY`, calls `C.cmd_provider(agent, "openai")`, and asserts the key prompt appears — but it **never mocks `getpass`**. The call chain is `cmd_provider` → `_ensure_api_key` (`repl/commands/provider.py:343`) → `getpass.getpass(...)`, which reads `/dev/tty` and blocks forever. Confirmed by isolation: the single test node does not complete within 40 s and prints the prompt.

This is a **local-development hazard, not a CI failure**: on a runner with no TTY, `getpass` raises and `_ensure_api_key`'s `except Exception` returns `False`, so the assertions pass. On a developer machine with a TTY it hangs indefinitely. `getpass` reads `/dev/tty`, so `< /dev/null` does **not** help, and setting `WISP_API_KEY` does **not** help either because the autouse `isolated_wisp_env` fixture (`tests/conftest.py:22`) strips `WISP_*` before the test runs. *Confidence: HIGH (isolated and reproduced).*

**Environment defect found (not a code defect).** The project `.venv` is **missing 4 of the 14 declared direct dependencies**: `aiohttp`, `tiktoken`, `prompt_toolkit`, and `cryptography`. This produced **25 apparent test failures**, all of which are artifacts:

| Failing group | Count | Cause | Proof |
|---|---|---|---|
| `test_policy_modes.py` (10), `test_policy_routes.py` (6), `test_policy_bundle.py` (4), `test_enterprise_integration.py` (2) | 22 | `ModuleNotFoundError: No module named 'cryptography'` (`policy/bundle.py:84`) | With `cryptography==50.0.1` supplied in an isolated env, **all 30 pass** |
| `test_cli_surface_e2e.py::TestGroup3Headless` (3) | 3 | ambient `HTTP_PROXY`/`HTTPS_PROXY` leak into the hermetic child (`test_cli_surface_e2e.py:52` filters only `WISP_API_KEY`/`VIRTUAL_ENV`/`PYTHONHOME`), so localhost calls hit a proxy → `502 upstream connect failed` | With proxy vars unset, **all 38 pass** |

**So: zero product defects among the 25 failures.** CI installs `.[dev]` and runs without a TTY or proxy, so it is unaffected by either. The two real quality-gate problems remain the mypy gate (§3, §21 Q3) and the collection-error abort.

**Important caveat on my own runs:** my first execution environment injected a `sitecustomize.py` shim that blocked `mkdir` under the pytest temp root, producing **false failures**. Re-run with a clean interpreter (`env -u PYTHONPATH`), `tests/reliability/test_13h2_determinism.py` gives **39 passed in 26.7s**. I report this so the numbers below are not mistaken for product defects.

---

## 16. Architectural Invariants

| # | Invariant | Enforcement | Violations | Confidence |
|---|---|---|---|---|
| I1 | Model-initiated tool calls route through `ToolExecutor.execute → authorize()` | single delegation point (`stateless.py:1840`); READ-only risk fallback (`:1846`) | none found on the model path | HIGH |
| I2 | **All** side effects route through the executor | convention only | `POST /api/git/commit`, `POST /api/hooks`, `POST /api/context` | HIGH |
| I3 | Validator output is an opinion; `authorize()` is the law | graph runner passes data, never authority (`graph/runner.py:1-15`) | none found | HIGH |
| I4 | Subagent event channels are task-local, never instance-scoped | `ContextVar sub_event_queue` (`tool_executor.py:870-877`) | none found | HIGH |
| I5 | Hook-controlled dirs are unwritable by agent tools | pathsec guard + `authorize` L4 + deny-list (3 independent) | bypassed at the REST boundary | HIGH |
| I6 | A turn that mutates code must observe exit-0 verification before completing | `VerificationFloorGuard` (`verification.py:131-163`) | none found — **Phase 2 verified the gate works** (see §21) | HIGH |
| I7 | Graph resume never repeats a successful node | fingerprint pin (`executor.py:155`) + node_run replay (`:206`) | none found | HIGH |
| I8 | Terminal status precedence: failure > cancelled > succeeded | `executor.py:489-520`; join timeout yields TIMEOUT, never SUCCESS (`:715-748`) | none found | HIGH |
| I9 | Configuration is immutable after construction | `replace()` returns a new instance; `fingerprint()` keys the core cache | not enforced by type; config read from env at multiple sites | MEDIUM |
| I10 | The system prompt's tool menu is generated from live registries | `_build_tools_block` / `_get_tool_schemas` (`stateless.py:1671,1750`) | none found | HIGH |
| I11 | Errors are honest — never fabricate a `done` | `provider_stream.py:269-281`; `stateless.py:703-726` | see I6 | HIGH |

**I6 — Phase 2 correction: the gate is sound.** My Phase 1 reading of `verify_ok_after_edit = not result_text.startswith("[exit code:")` (`verification.py:123`) flagged a heuristic weakness. That was **wrong**, because I tested it with a synthetic input that cannot occur. `_format_bash_output` emits the `[exit code: N]` prefix **only when the exit code is non-zero** (`tools/bash.py:39-40`): a successful command's result is bare stdout (or `"(no output)"`). Verified empirically by driving the real formatter into the real guard:

| real `run_bash` outcome | produced text | `verify_ok` | `resolved()` |
|---|---|---|---|
| exit 0, `"3 passed"` | `'3 passed in 1.2s'` | **True** | **True** |
| exit 0, silent | `'(no output)'` | **True** | **True** |
| exit 1 | `'[exit code: 1]\n1 failed…'` | False | False |
| exit 127 | `'[exit code: 127]\ncommand not found'` | False | False |

So the gate correctly credits a green run and correctly refuses a red one. The residual risk is an **undocumented cross-module contract** — `verification.py` depends on `bash.py` omitting the prefix on success; if that formatter ever emits the code unconditionally, the gate silently inverts. Plus one narrow false negative: a *successful* command whose stdout begins with the literal `[exit code: N]` (e.g. a script echoing that string) is read as a failure — confirmed, `resolved()` stays False. *Confidence: HIGH (executed).*

**Invariants the system depends on that are NOT written down anywhere I could find:** the runtime/core shared-`session`-dict mutation contract (§10), and the requirement that `ToolExecutor` remains the sole holder of `_skip_authorize=True` (`registry.py:908-930` documents it, but nothing enforces it).

---

## 17. Critical Components

Ranked by business impact + dependency centrality + statefulness + security exposure + failure blast radius + complexity:

| Rank | Component | Why critical |
|---|---|---|
| 1 | `tool_executor.py` + `auth/decision.py` | **Single authority choke point.** A defect here is a total-governance defect. 2,188 + 475 lines, 6-layer logic, every tool call. |
| 2 | `infra/store.py` `UnifiedStore` | **Single durable root.** Corruption or contention stops sessions, runs, tasks, traces, idempotency at once. One SQLite file. |
| 3 | `core/stateless.py` `WispAgentCore` | 2,319 lines, CC hotspots (`_turn_inner` ~92), the whole product's behaviour. Also holds the unbounded `_CONTEXT_TTL`. |
| 4 | `graph/executor.py` + `graph/validator.py` | Durable state machine with resume semantics; O(N·E) validator; unlocked lazy init; unpruned per-run dicts. |
| 5 | `core/runtime.py` `AgentRuntime` | Owns session state, locks, core cache, compaction; the two-writer session contract. |
| 6 | `server/deps.py` + `server/routes/*` | 70 routes; auth/rate-limit logic-bearing; **inconsistent policy gating**; WS bypasses the rate limiter. |
| 7 | `multi_agent/background.py` | Lifecycle pub-sub + admission; **admission bypass on `send()`**; unbounded registry growth. |
| 8 | `sandbox/router.py` + `tools/_utils.py` | Confinement decision + danger heuristic; silent failover; documented non-boundary. |
| 9 | `transport/cli.py` `CLITransport` | 1,886 lines, the approval state machine, the most-churned file in history (131 commits). |
| 10 | `__main__.py` | 31-branch dispatch table; highest-churn entry surface; the accretion point. |

**Where a defect has the largest consequences:** #1 and #2. **Where a defect is most likely to hide:** #4, #6, #7 (state that grows, gates applied inconsistently, admission checks that don't apply on every path).

---

## 18. Current Architectural Hypothesis

> **The system appears to be:** a **layered/hexagonal agent runtime** — a stateless turn engine behind a `Transport` ABC, an infrastructure ring of ports (provider, store, security, extensions, sandbox), and a governance layer bolted on as separate milestone packages (`contracts/`, `auth/`, `runs/`, `policy/`, `trace/`, `eval/`, `task/`, `release/`). The newer rings are cleanly dependency-inverted; the older core is a pragmatic monolith that the newer rings wrap.
>
> **Its primary execution model is:** one asyncio event loop per process, one serialized turn per session, with thread-pool bridges for blocking I/O; all concurrency bounded by semaphores and queues.
>
> **Its primary state model is:** a single in-memory `session` dict per conversation (mutated by two components) persisted to **one SQLite file** after every turn; everything else is either a bounded cache or a per-run record.
>
> **Its primary data flow is:** `transport.recv → runtime.run_turn → core.turn → provider stream → tool-call parse → ApprovalGate → ToolExecutor.execute → authorize() → sandbox → filesystem/process → tool result → message append → persist → transport.send`.
>
> **Its primary failure model is:** *fail honest and fail closed* — never fabricate a `done`, deny when no approver exists, refuse to boot unauthenticated, refuse to resume a changed graph. Resilient to provider faults (retry + circuit breaker), tolerant of sandbox absence (loud failover).
>
> **Its major architectural boundaries are:** (1) `ToolExecutor.execute` — model authority; (2) `authorize()` — the 6-layer narrowing law; (3) `UnifiedStore` — durability; (4) the `Transport` ABC — UI/IO; (5) `SandboxRouter` — process confinement; (6) the graph validator — *advisory only*.

### Challenging the hypothesis — and what I did about it

| Challenge | Test performed | Result |
|---|---|---|
| "Stateless core" is the load-bearing claim — is it true? | Read the dataclass fields and module globals | **Refuted as written.** Two mutable instance fields + three module globals. *Nuance: stateless w.r.t. history ownership.* |
| "One authority path" is the security claim — does it hold? | Grepped every `subprocess`/`open`-for-write/network site; read each REST route body | **Refuted for the REST control plane.** 3 routes mutate without `authorize()`. Model path holds. |
| Prior docs claim an 8-module arena/server cycle | Re-ran Tarjan SCC independently over 366 modules | **Not reproduced.** Actual cycle set is different (7 SCCs, largest is the repl.commands registry). |
| Prior docs claim legacy `core/agentic_graph.py` divergence | Checked the filesystem | **File does not exist**; `core/subagent/` is empty. Claim is obsolete. |
| Is the codebase healthy enough that CI is green? | Ran ruff, mypy, compileall, pytest | **No.** mypy exits 1 (16 errors); ruff fails on the working tree; 3 test files uncollectable; full suite hangs on a credential prompt. |
| Is `wisp/coding.py` the layer the dependency map says it is? | AST importer search | **No production importer** — tests only. |

---

## 19. Open Questions — Resolved in Phase 2

All ten Phase 1 questions were investigated to closure. **Two of my own Phase 1 claims turned out to be wrong** (Q6, Q8) and are corrected here rather than quietly dropped.

| # | Question | Resolution | Confidence |
|---|---|---|---|
| **Q1** | Are the ungated REST routes actually reachable? | **Narrowed, severity downgraded.** `server/main.py:157-197` binds loopback (`127.0.0.1`) by default and `SystemExit(2)` refuses to boot without a key or an explicit `--no-auth`. So the three ungated routes require *either* the API key (`~/.config/wisp/auth_keys.json`, 0o600) *or* an explicitly warned `--no-auth` loopback boot. Not remote RCE. Residual: with `--no-auth` loopback **and** an approved `run_bash`, the agent could `curl` its own server to write `.wisp/hooks/*.json` — a narrow model→authority path that the hook-dir guard does not cover. Still a genuine consistency defect vs `routes/files.py:122` and `bash.py:35`, which do gate. | HIGH |
| **Q2** | Which test hangs on the credential prompt? | **RESOLVED.** `tests/test_provider_select.py::TestProviderCommand::test_switch_requires_key_when_missing` (`:210`). No `getpass` mock. Hangs on a TTY; passes in CI (no TTY → `getpass` raises → `_ensure_api_key` catches and returns `False`). Local-dev hazard, **not** a CI failure. | HIGH |
| **Q3** | Is the mypy gate red in CI too? | **RESOLVED — yes.** `uv.lock` pins mypy **2.3.1**; CI installs `mypy>=1.10` unpinned. Running mypy 2.3.1 yields the **identical 16 errors** in `core/runtime.py` + `core/stateless.py` and **exit 1**. Root cause: both files are in the `files` gate list (`pyproject.toml:65`) but were never annotated — the ratchet was applied to the gate ahead of the code, and the stale comments (`pyproject.toml:64`, `ci.yml:44-47`) still describe a smaller gate. | HIGH |
| **Q4** | Do the 3 uncollectable tests mean deleted features? | **RESOLVED.** `wisp/multi_agent/delegation.py` was **deliberately deleted** in `11fc949` ("refactor(delegation): remove prompt-interception auto-delegation"). The 3 untracked test files are stale WIP for a removed feature and for a removed `transport/cli.py` symbol. | HIGH |
| **Q5** | Are `core/speculative` and `multi_agent/resource_budget` truly dead? | **RESOLVED — yes.** Zero importers repo-wide (AST). The only dynamic-import sites are unrelated: a tool-target map (`registry.py:777-779`) and a doctor spec check (`doctor.py:424`). Neither names these modules. | HIGH |
| **Q6** | Does `_CONTEXT_TTL` grow unbounded? | **CORRECTED — my Phase 1 claim was WRONG.** `_ttl_get(kind, key, …)` keys the dict by **`kind`**, and the only three call sites pass fixed literals (`stateless.py:1445,1542,1654`). The dict holds **at most 3 entries**. Not a leak. Residual: no lock (benign — the stored `key` is compared before serving, so a race costs a miss, never a wrong-workspace result), and alternating workspaces thrash the cache. | HIGH |
| **Q7** | Does `BackgroundAgentManager.send()` bypass the running bound? | **CONFIRMED.** `send()` resets the entry and spawns `asyncio.create_task` at `background.py:513` with **no** `_max_running` check and no `Scheduler.admit`. `launch()` **does** enforce both (`:255-263`). The asymmetry is real: N finished agents can be resumed concurrently, exceeding the advertised 8-running bound. | HIGH |
| **Q8** | Is the verification-gate heuristic exploitable? | **CORRECTED — my Phase 1 claim was WRONG.** `_format_bash_output` emits `[exit code: N]` **only on non-zero exit** (`bash.py:39-40`), so `not startswith("[exit code:")` is *correct*: green → verified, red → not. Verified by driving the real formatter into the real guard. Residual: an undocumented cross-module contract, plus a narrow false negative if a successful command's stdout begins with the literal prefix. | HIGH |
| **Q9** | Do the duplicate containment implementations diverge? | **RESOLVED — yes, on one input class.** Differential test of all six implementations over 12 adversarial inputs: canonical `pathsec` and its three aliases **reject control characters**; `server/routes/files._resolve_path` and `sandbox.resolve_sandbox_cwd` **accept them**. NUL bytes and traversal/symlink escapes are rejected everywhere; `graph.artifacts`/`workspace` reject absolute-inside-root by design (`allow_absolute=False`) while others allow — an intentional policy difference, not a bug. | HIGH |
| **Q10** | Is `tiktoken` an unused dependency? | **RESOLVED — it is used.** Lazy imports in `infra/token_counter.py:113`, `context_assembler.py:525,549`, `core/context_pruner.py:129`. My Phase 1 grep was broken (BSD `grep` ignores `--include` placed after the path), not the dependency. | HIGH |

**New question raised in Phase 2 and resolved:** why is the local `.venv` missing 4 declared dependencies? See §15 — it is an environment drift, and it explains all 25 apparent test failures.

---

## 20. Initial Risk Map

Focused **where deeper investigation should go** — this is a map, not an audit, and no fix is proposed.

| Risk | Area | Severity | Confidence | Evidence |
|---|---|---|---|---|
| R1 | **REST control plane gates inconsistently** — `POST /api/hooks` writes the agent-forbidden hook dir; `POST /api/git/commit` commits; `POST /api/context` writes persistent instructions. None call `authorize()`/`require_tool_allowed`. | **HIGH** | HIGH | `routes/hooks.py:75`, `routes/git.py:67`, `routes/context.py:38` vs `routes/files.py:122` |
| R2 | **Quality gates are currently red** — `mypy` exits **1** at the locked version 2.3.1 (16 errors in `core/runtime.py`, `core/stateless.py`; both tracked → bites CI). The gate covers only 8 of 366 files and was ratcheted ahead of the annotations. ruff and the 3 uncollectable tests fail only in the working tree (all untracked → absent from CI). | **HIGH** | HIGH | §15, §21 Q3; `pyproject.toml:65`; `.github/workflows/ci.yml:36,40,48` |
| R2b | **Local `.venv` is missing 4 declared dependencies** (`aiohttp`, `tiktoken`, `prompt_toolkit`, `cryptography`) → 25 spurious test failures locally. Environment drift, not a code defect. | MEDIUM | HIGH | §15 |
| R2c | **Full suite cannot complete locally** — collection errors abort it outright; with the documented `--ignore` flags it hangs at 59% on `test_provider_select.py:210` (`getpass`, no mock). CI is unaffected (no TTY), but developer runs are blocked. | MEDIUM | HIGH | §15, §21 Q2 |
| R3 | **Single durable root** — one SQLite file backs sessions, runs, tasks, traces, idempotency. | **HIGH** | HIGH | `infra/store.py:122-299` |
| R4 | **Two-writer session contract** — runtime and core both mutate the shared dict; nothing enforces it. | MEDIUM | HIGH | `runtime.py:401`, `stateless.py:754,847,871,884,897` |
| R5 | **Unpruned per-run state** — graph `_cancelled`/`_approvals` never pruned; background `_entries` + telemetry rings grow unless `prune()` is called; `_subscribers` queues unbounded. (`_CONTEXT_TTL` was listed here in Phase 1 and **removed** — it is bounded to 3 keys.) | MEDIUM | HIGH | `executor.py:82-85`, `background.py:563-573` |
| R6 | **Verification-gate coupling** — the gate is correct, but depends on `bash.py` omitting the `[exit code:` prefix on success. An undocumented cross-module contract; a successful command echoing that literal is a false negative. | LOW | HIGH | `verification.py:123` + `bash.py:39-40`; §21 Q8 |
| R7 | **Background admission bypass** — `send()` spawns without the running-count check. | MEDIUM | HIGH | `background.py:468-524` |
| R8 | **Validator is O(N·E)** at scale, and is *advisory* — easy to mistake for enforcement. | MEDIUM | HIGH | `validator.py:195-203`; `TRUST_BOUNDARY_MAP.md` §B2 |
| R9 | **Sandbox failover is silent at the decision point**; two routing mechanisms coexist (`get_sandbox` vs `get_router`). | MEDIUM | HIGH | `sandbox/router.py:214`; `bash.py:78` vs `primitives.py:83` |
| R10 | **WS route bypasses the rate limiter** (auth is per-frame, not a FastAPI dependency); `connections.py ConnectionManager` is dead relative to the mounted handler. | MEDIUM | HIGH | `routes/agents.py:67,131`; `server/connections.py:79` |
| R11 | **Documentation drift** — `ARCHITECTURE.md`/`AGENTS.md` reference `delegation.py`, `DelegationAnalyzer`, `_MockIO`, "310 test files / 4,200 tests"; `WISP_*` docs claim a removed cycle and a non-existent `agentic_graph.py`. | MEDIUM | HIGH | verified absences |
| R12 | **Dead code** — `_auto_retry_safe` has its entire body duplicated after `return` (unreachable); `core/speculative/` and `multi_agent/resource_budget.py` have zero importers. | LOW | HIGH | `subagent_orchestrator.py:990-1026` |
| R13 | **Third-party satellite orphans** — `wisp-ts/` and `agent/` are untracked dead trees inflating the working directory. | LOW | HIGH | filesystem + `git ls-files` |
| R14 | **Committed generated artifacts** — `.coverage`, `graphify-out/*`, `qa-results/report.md`, `.wisp/mcp.json` are tracked despite `.gitignore` rules. | LOW | HIGH | `.gitignore:72,77,86` vs tracked files |

**Not a risk (verified clean):** no P0 correctness/security defect found; the model authority path is genuinely chokepointed; error handling has **0 bare `except:`**; the graph engine's durability and tamper-resistance are unusually well built; only **12** TODO/FIXME/HACK markers exist in `wisp/` and all but one (`plugins/registry.py:335`, semver constraints) are prompt text or benchmark fixtures.

---

## 21. Phase 2 — Verification Results

**Method:** each Phase 1 open question was driven to a falsifiable test rather than reasoned about further — the locked toolchain for the CI verdict, isolated environments for dependency questions, direct execution of the real formatter into the real guard for the verification gate, and a six-way differential harness for containment. Still read-only with respect to the codebase: nothing in `wisp/` or `tests/` was modified; the only environment change was transient `uv` caches outside the project.

### What the phase changed

| Phase 1 claim | Phase 2 verdict | How it was tested |
|---|---|---|
| `_CONTEXT_TTL` is an unbounded, unlocked global | **WRONG — bounded to 3 keys** | Read `_ttl_get` + all 3 call sites; the dict is keyed by `kind`, not workspace |
| The verification gate can be satisfied by a malformed result | **WRONG — the gate is sound** | Drove `_format_bash_output` into `VerificationFloorGuard`: green→True, red→False |
| The mypy gate is red locally but maybe fine in CI | **CONFIRMED red in CI** | Ran mypy 2.3.1 (the `uv.lock` pin) → identical 16 errors, exit 1 |
| `tiktoken` may be an unused dependency | **WRONG — it is used** | AST/grep for real import sites (my Phase 1 grep was broken by BSD `grep --include` ordering) |
| The suite has a hermeticity gap | **CONFIRMED and root-caused** | Isolated the exact test node; reproduced the hang |
| The containment helpers diverge | **CONFIRMED, one input class** | 6 implementations × 12 adversarial inputs |
| `send()` may bypass the running bound | **CONFIRMED** | Read both paths: `launch()` gates, `send()` does not |
| 25 test failures indicate real defects | **WRONG — all environmental** | Re-ran each group with the missing dep supplied / proxies cleared → all pass |

### Process lessons worth carrying forward

1. **My Phase 1 execution environment produced false negatives.** An injected `sitecustomize.py` shim blocked pytest's temp directories, and ambient `HTTP_PROXY` leaked into hermetic subprocesses. Both caused failures that had nothing to do with the product. Any future run must clear `PYTHONPATH` and proxy vars.
2. **A synthetic input is not a test.** The Q8 "defect" existed only because I hand-built a string the producer never emits. Driving the real producer into the real consumer immediately dissolved it.
3. **Broken tooling silently fabricates findings.** BSD `grep` ignores `--include` placed after the path — that single ordering mistake produced the false "`tiktoken` is unused" and "no caching layers" observations in Phase 1.
4. **Two of ten open questions were my own errors, not the codebase's.** That ratio is the strongest argument for the "verify third" step the methodology prescribes.

### Remaining genuinely-open items (not resolvable by reading)

- Whether the `.venv` drift (R2b) also exists on the maintainer's machine or is specific to this checkout — **needs the maintainer's environment**.
- Whether `--no-auth` loopback + an approved `run_bash` is considered in-scope for the threat model — **a policy question**, not a code question (`docs/THREAT-MODEL.md` lists no such scenario).
- Whether the graph validator's O(N·E) contract section is reachable at a scale that matters — Phase 1 carried forward a prior 512-fan measurement; **no independent profiling was done**.

---

## Investigation Journal (condensed)

| Observation | Evidence | Confidence | Implication |
|---|---|---|---|
| Authority is chokepointed for the model | `stateless.py:1840` sole delegation; READ-only fallback `:1846` | HIGH | The core security claim is true where it matters most |
| REST routes mutate without `authorize()` | `routes/hooks.py:75`, `git.py:67`, `context.py:38` | HIGH | Control-plane consistency defect; hook dir is the sharp edge |
| `WispAgentCore` is not literally stateless | `stateless.py:176,177,65,72,82` | HIGH | Doc claim must be corrected; `_CONTEXT_TTL` is an unbounded global |
| Prior P1-1 (`agentic_graph`) is obsolete | file absent; `core/subagent/` empty | HIGH | Prior docs cannot be trusted without re-verification |
| Prior P1-2 (8-module arena cycle) not reproduced | independent Tarjan SCC | HIGH | The named teardown risk is gone or was misdiagnosed |
| mypy gate is red | 16 errors, exit 1 | HIGH | "Strict typing" covers 8/366 files and currently fails |
| Test suite hangs on `getpass` | observed 15-min stall | HIGH | Hermeticity gap; CI hang risk |
| 3 test files uncollectable | import errors on deleted symbols | HIGH | Features were removed without removing their tests |
| Graph engine is the strongest subsystem | `executor.py:149-231` resume semantics | HIGH | Model to imitate for other subsystems |
| ContextVar sub-event routing | `tool_executor.py:870-877` | HIGH | Correct pattern for shared-executor concurrency |
| `_auto_retry_safe` body duplicated after `return` | `subagent_orchestrator.py:990-1026` | HIGH | Harmless but signals an incomplete refactor |
| `wisp.coding` has no production importer | AST importer search | HIGH | The dependency map's "Coding layer" is aspirational |
| **P2** `_CONTEXT_TTL` is bounded, not unbounded | `_ttl_get` keyed by `kind`; 3 fixed literals | HIGH | **Corrects my Phase 1 claim** — not a leak |
| **P2** verification gate is sound | real formatter → real guard: green True, red False | HIGH | **Corrects my Phase 1 claim** |
| **P2** mypy gate red in CI | mypy 2.3.1 (uv.lock pin) → same 16 errors, exit 1 | HIGH | Gate ratcheted ahead of annotations |
| **P2** the hang is one named test | `test_provider_select.py:210`, no getpass mock | HIGH | Local-dev hazard; CI unaffected (no TTY) |
| **P2** 25 failures are all environmental | 4 missing deps + ambient proxy leak | HIGH | Zero product defects among them |
| **P2** containment diverges on control chars | 6 impls × 12 adversarial inputs | HIGH | The two re-implementations lack the guard |
| **P2** `send()` bypasses admission | `launch()` gates at `:255`, `send()` spawns at `:513` | HIGH | Documented 8-running bound is not universal |

---

## Quality Bar Self-Assessment

| Question | Answer |
|---|---|
| Can I explain this repository to a senior engineer who has never seen it? | **Yes** — §1–§7. |
| Can I trace a request from entry point to side effect? | **Yes** — F1–F10, with `file:line` at each hop. |
| Can I explain where state lives? | **Yes** — §10, including hidden state. |
| Can I explain the concurrency model? | **Yes** — §11, with the 6 suspicious boundaries named. |
| Can I explain the major failure paths? | **Yes** — §12, with the honest-terminal-path invariant. |
| Can I identify the most important architectural boundaries? | **Yes** — §18, six boundaries, one of which (the validator) is advisory. |
| Can I identify the most dangerous unknowns? | **Yes** — §19 Q1, Q2, Q3, Q8. |
| Can I defend my conclusions with actual code evidence? | **Yes** — every row cites `file:line` or command output, and I explicitly flag the three prior-doc claims I refuted. |

**Deliberately not done, per the phase rules:** no refactoring, no rewrite proposals, no dependency changes, no architectural recommendations. Evidence gathering only.

*Repository Intelligence Report — reconnaissance phase complete.*
