# Wisp Architecture Map — every wire, every API surface

Generated 2026-08-25 by direct source extraction (all anchors file:line-verified).
Scale: ~11.5k LOC across core/transport/tools/multi_agent/infra/server.

> **Addendum 2026-09-04 — enterprise track (M1–M7).** Eight new packages sit
> alongside this map without rewriting it: `wisp/contracts/` (versioned
> envelopes + edge adapters), `wisp/auth/` (Principal, layered `authorize()`,
> workspace trust, secrets, consent — `ToolExecutor` the only action path),
> `wisp/runs/` (RunStore, scheduler, compensation, repro), `wisp/policy/`
> (Ed25519 bundles, precedence, modes, admin CLI, control-plane routes),
> `wisp/trace/` + `wisp/eval/` (span store, evidence export, replay, OTLP,
> eval harness), `wisp/task/` (lifecycle, plan review, profiles),
> `wisp/release/` (lock, SBOM, health, diagnostics). New CLI: `task`,
> `policy`, `trace`, `replay`, `audit`, `release`, `completion`. New tables:
> `run_transitions`, `trace_spans`, `task_plans` (+ lease columns on
> `background_runs`). Full map: `docs/enterprise-target-architecture.md`;
> specs: `docs/superpowers/specs/2026-09-04-*.md`; 285 test files / 3,950
> tests. Section-by-section re-extraction of this document is deferred —
> treat anchors below as 2026-08-25-accurate for pre-enterprise modules.

## 0. The one-paragraph version

`python -m wisp <cmd>` (`wisp/__main__.py`) parses flags → `cmd_*(...)` handlers →
`entry.run_mode(mode)` → builds `WispConfig` → constructs **CompositionRoot**
(`composition.py:37`), which wires every service in dependency order. The REPL loop
(`entry._run_repl`) feeds prompts to `AgentRuntime.run_turn` → cached
`WispAgentCore.turn()` streams **flat dict events** → `CLITransport._render_event`
paints them. Tool calls re-enter `ToolExecutor`, which consults the policy engine,
runs pure impls from `tools/registry.py`, and dispatches subagent tools into
`SubagentOrchestrator`. Everything persists through `UnifiedStore` (SQLite).

## 1. Composition graph (construction order, `composition.py`)

```
CompositionRoot(config)
├─ store            = UnifiedStore(db_path = config.db_path | ws/.wisp/wisp.db)   :57
├─ audit_trail      = ImmutableAuditTrail(store)                                  :58
├─ security         = SecurityPolicy(permission_mode, audit_trail)                :59
├─ extensions       = ExtensionHost() ← Plugin/Hook/MCP/Skill extensions          :63,89-92
├─ telemetry        = Telemetry()                                                 :64
├─ thread pool      = set_shared_executor_size(config.thread_pool_size||8)        :69-71
│                     + loop.set_default_executor(non_owning_executor())          :77-80
├─ hooks            = InterceptHookManager(ws), ToolHookManager(ws)               :86-87
├─ mcp_manager      = MCPManager(str(ws))  [shared: MCPExtension + executor]      :88
├─ file_lock        = FileLock(str(ws))                                           :97
├─ lsp_manager      = get_lsp_manager(str(ws))  [server-wide singleton]           :111
├─ tool_registry    = ToolRegistry()                                              :114
├─ tool_executor    = ToolExecutor(config, tool_hooks, mcp, file_lock,
│                                  lsp_manager, subagent_orchestrator=None, ext) :117-126
├─ compactor        = Compactor(provider_factory, compaction_model)               :129-133
├─ runtime          = AgentRuntime(store, security, extensions, telemetry,
│                                  core_factory=_create_core, compactor,
│                                  orchestrator=None, session_repo, config)      :136-146
├─ subagent_orch    = SubagentOrchestrator(config, workspace, tool_executor,      :150-157
│                                  hook_manager, agent_runtime=runtime, store)
│   └─ set_global_token_budget(config.subagent_token_budget || None)              :161
├─ BACK-WIRING      runtime.orchestrator = orch; executor.subagent_orchestrator   :164
├─ background_agents= BackgroundAgentManager(orch) → executor + orch refs         :169-172
└─ _registry        ServiceRegistry: store, extensions, telemetry                 :175-178
```

**Cycle broken by two-phase wiring**: executor is born with `orchestrator=None`;
the orchestrator receives the finished executor at construction, then is injected
back into both runtime and executor (`composition.py:164`). Any new service needing
both must follow this late-bind pattern.

## 2. Entry surface

**Global flags** (`__main__.py:976-1001`): `-m/--model -s/--skill -S/--session
-w/--workspace -y/--auto-approve -T/--show-thinking --print --output-format --quiet`

**Subcommands** (dispatch `__main__.py:1013-1170`): `run · repl · tui · session
(list|show|delete|trim|compact) · compact · skills · config · check · models ·
memory · mcp · git · plan · progress · diagnose · locks · changes · acp · server ·
swarm · agents · bench` — each routes to a `cmd_*` handler in the same module;
`repl/tui` land in `entry.run_mode("cli"|"tui")`; headless `--print` uses
`run_headless(permission_mode="full")` (`__main__.py:204-210`).

**run_mode modes** (`entry.py:44-68`): `server` (own root in lifespan) |
`cli` (`_run_cli`: single-shot if prompt given, else `_run_repl`) | `tui`
(`WispTUIApp(config, transport, runtime).run()`). Overrides applied via
`config.replace(...)`. REPL lifecycle: root.start → CLITransport.start →
bind_loop(loop) → get_or_create_session → banner → AgentAdapter → REPL
SIGINT handler → input loop; shutdown restores signals → cancels turn →
saves history+session → root.shutdown() (registry.stop + metrics export +
LSP/MCP teardown). Headless path caches its root in module globals keyed on
config-file mtime only — env-var changes do NOT bust it (`entry.py:612-661`).

## 3. Core engine (`core/stateless.py`, 1,4k lines)

**Turn anatomy** — `WispAgentCore.turn(session, prompt, approval_handler,
steering_drain)` (`stateless.py:116`):

```
 1. wall clock: _turn_deadline.set(monotonic()+turn_timeout)       :134 (ContextVar)
    turn_timeout default 1800s (config/env WISP_TURN_TIMEOUT)      :130
 2. build messages (system prompt incl. skills/repo-map/memory/rules)
    + tool schemas filtered by session["allowed_tools"]            :150-162
    max_iterations: schema default 50 (config.py:127); stateless
    getattr-fallback 30 applies only to attr-less test configs      :164
 3. asyncio.timeout(turn_timeout) wraps _turn_inner                :167
 4. provider stream via _guarded_provider_stream                   :203→1345
 5. iterate events: content buffered → tool_call?
    (provider tool_calls batches normalized to singular)           :223-290
 6.   ├─ steering drain at each tool boundary                      :449-461
    │   ([steering] user msgs + steering_feedback events)
 7.   └─ approval gate → ToolExecutor.execute → role:"tool" msgs   :403-445
 8. no tool calls → done_event, return                             :399-401
 9. iteration budget exhausted → forced wrap-up stream or          :464-497
    error(CODE_ITERATION_BUDGET)

Auto-delegation was REMOVED (11fc949): subagents run only as explicit
spawn/fanout tool calls; capability_matcher.should_delegate remains as
a library helper without a core-loop consumer.
```

**Guards**: `FIRST_TOKEN_DEADLINE_S = env WISP_FIRST_TOKEN_DEADLINE, default 90`
(`:1339`); empty-stream retry ×`WISP_STREAM_ATTEMPTS`(3, jittered backoff;
429/5xx scale 1.5×attempt); turn wall-clock 1800s; iteration budget 30 with
forced synthesis; child budget clamp ×1.5 on timeout retry.

**Event vocabulary** (`core/events.py`, flat dicts at the transport boundary):
`thinking · tool_call · tool_result · content · error · done · system ·
approval_request · steering_paused · steering_inject · steering_resumed ·
provider_status · subagent` — 13 types. Consumers must accept BOTH flat
(`name`/`text` top-level) and nested `data:{}` shapes (renderer squeezes via
`_flatten_event`).

**Session management** (`core/runtime.py`): CRUD + per-session asyncio locks
(LRU-evicted at 1000, `:69/:537`) + core cache keyed on **`config.fingerprint()`**
(`:442-443`, invalidated on any config change) + auto-compaction when history
exceeds `max_messages` (default 50, `:88`; Compactor LLM summary, fallback
truncation) + per-session approval state + steering inbox + session_repo event
persistence.

## 4. Transport layer

**ABC** (`transport/base.py:18`): async `send(event) · recv() · approve() ·
start() · stop()`.

| Transport | Purpose | Constructed by | Notes |
|---|---|---|---|
| `cli.py` CLITransport | REPL rendering | entry | buffers thinking/content; wait-clock; spinner-aware log handler |
| `websocket.py` | live WS streaming + bidirectional approval | server lifespan | connection↔session routing |
| `headless.py` | collects events → result dict | run_headless | no I/O; `--print`/stream-json |
| `tui.py` TUITransport | Textual bridge | entry (deferred import) | submit_prompt/set_approval handshake |
| `file.py / multi.py / metrics.py` | composable wrappers | operator code | decorator pattern over ABC |
| helpers | renderer.py (pure fns) · spinner.py (120ms \r anim + ACTIVE_SPINNER registry) · progress.py (phase/tool/file tracking) · typeahead.py (steering capture) | | |

**Renderer API** (pure, mode-aware): `render_tool_call · render_phase_bar ·
render_turn_stats(+ctx meter) · render_file_ticker · render_subagent_status ·
render_provider_status · render_diff_box/panel · shorten_diff_title · _box · _rule`.

**Modes** (`terminal_width.py`): unicode | ascii | accessible | minimal via
`WISP_OUTPUT_MODE`/NO_COLOR/auto-tty; `status_symbols()` picks glyphs; every
render path must handle all four (goldens enforce it).

## 5. Tools & execution pipeline

**Inventory: 41 tools** (`tools/registry.py`, OpenAI wire format
`{"type":"function","function":{name,description,parameters}}`):
read/write/edit_file(_multi) · run_bash · web_fetch/search (lazy requests) ·
list_files · search_symbols/codebase · remember/recall (cross-session memory) ·
spawn/fanout (fanout non-blocking by default → background manager)/
spawn_background/subagent_{list,result,send,cancel,wait} ·
orchestrate_{vote,map_reduce,chain,dag} · capture_skill · git_status/diff/branch/
commit/push · gh_pr_create · lsp_{diagnostics,definition,references,hover,symbols} ·
diagnose · plan_task/mark_step_done/update_plan · run_tests.
Subagent tools dispatch in executor (`tool_executor.py:730+`), never MCP-shadowed
(`_SUBAGENT_TOOLS` :143). Web tools resolve lazily via `_lazy_tool`/`_resolve_lazy`
(registry.py:545+) so CLI launch skips the requests stack.

**Execution flow**: model JSON → signature-filtered args (`inspect.signature`,
lazy-aware) → policy decision → intercept hooks → impl → envelope
`{"status","tool","data","metadata"}` → repeat-guard (web_fetch memo 600s) →
metrics. Fanout adds workspace-grounding preamble + progress callbacks + depth
inheritance (`tool_executor.py:1566+`).

**Approval matrix**: FULL=all allowed · AUTO_EDIT=bash/git/push/pr blocked,
subagents blocked (`policy_engine.py:259`) · ASK_ALL=writes prompt · READ_ONLY=
everything blocked. Interactive options `y Y a n N d c` with honest cancel.

## 6. Multi-agent system

**Contract** (`task.py:51-171`): identity(name/role validated vs ROLE_CONFIGS),
task+system_prompt(built per-role, stamped into session), tools(default ["all"]
→ session["allowed_tools"])/allowed_skills, budgets(max_iterations 15,
timeout_seconds 120 clamped ×1.5 on retry bounded by PARENT deadline−5s,
max_tokens→ctx, max_output_chars 8000 → _compress_output preserving sections/
code blocks), output_format(text|json; cache TTL 300s/60s)/output_schema(
post-run validate_subagent_output, auto_retry_parse injects errors once),
environment(model override, workspace→git-root-or-worktree, worktree_isolated
ANDed with role.wants_isolation, auto_approve) + runtime stamps(_subagent_depth,
_branch_count, retry_count, _cache_context, _shared_context, _resume_session_id).

**Roles** (`roles.py:38`, format tools/timeouts/iterations/isolation-wanted):
coder 8t/180s/15i/iso · reviewer 8t/120s/8i/iso · tester 8t/180s/12i/iso ·
researcher 10t/**240s**/14i/no-iso (240 raised from live E2E) · planner
5t/90s/8i/no-iso · debugger 8t/180s/12i/iso · generalist all/120s/10i/iso.
Isolation-capable roles get git-worktree sandboxes when contract asks;
researcher/planner never isolated (read-only by design).

**Runner lifecycle** (`_runner.py:83`): child cfg → session create/resume →
provider health hint (soft) → wall-clock deadline w/ per-iteration derivation →
turn → output compression → **honest emit** (task_completed vs task_failed+role,
`:239-260`) → store sync-back. FirstTokenTimeout → fail-fast timed_out for
orchestrator retry.

**Orchestrator** (`subagent_orchestrator.py`): `run_parallel` semaphore(adaptive:
budget ratio, load avg) + transient retry ×2 (429/rate-limit/connection markers,
backoff ≤6s, task_retry events) · `spawn_with_guards` → `_run_with_retry`
(no timeout retries, no budget retries, exp backoff) · budget admission before
launch · worktree isolation w/ conflict revert · aggregate_telemetry.
Pattern combination rules (`_patterns.py`): **map_reduce** retries non-timeout
mapper failures, reducer input = concatenated outputs truncated to 80% ctx.
**vote**: identical tasks → normalize(lowercase/ws-collapse) → similarity groups
(substring short / exact long) → largest group wins, consensus at count/total ≥0.6,
tie broken by a dedicated subagent; success = consensus_reached. **chain**: ignores
max_concurrent>1 (warns), passes last-3 context blocks downstream, stops on failure
unless continue_on_error. **dag**: Kahn topological levels, level-parallel with
semaphore, upstream outputs injected via metadata["_dep_results"], a failed node
blocks all transitive dependents.

Background agents (`background.py`): registry caps MAX_RUNNING_AGENTS=8;
_run_entry wraps orchestrator._run_with_retry; terminal states completed/failed/
cancelled; continuation via dataclasses.replace(contract, task=message) stamped
with _resume_session_id — requires the session to have been persisted (runner
with agent_runtime); pub-sub fans agent_started/progress/settled to subscriber
queues (WebSocket push, dashboards).

## 7. Infra & surfaces

**Store** (`infra/store.py`): SQLite WAL + busy_timeout 5000ms +
thread-local connections under RLock; autocommit with a transaction()
BEGIN/COMMIT/ROLLBACK wrapper. Tables: sessions · runs · events · memory ·
background_runs · session_events · idempotency (+6 indexes). Two construction
paths: CompositionRoot uses `config.db_path || ws/.wisp/wisp.db`; module-level
get_store() singleton keys on the raw path string and defaults
~/.config/wisp/wisp.db with /tmp fallback (`store.py:589-596`).
Memory recall = substring search + LRU eviction.

**Policy engine** (`infra/policy_engine.py`): rules sorted by priority —
first DENY wins / last explicit ALLOW wins / default-deny unmatched.
Predicates return DENY→block, ALLOW→allow, None→defer; PolicyDecision may
rewrite args. Mode rule priorities: full=0 · read_only=10 (safe-read
whitelist incl. lsp_*/web_*/recall) · ask_all safe=20 block=21 · auto_edit
block=30 · catch_all=1000. A PERMISSION_MODE typo silently lands in the
catch-all default-deny.

**Telemetry** (`infra/telemetry.py`): IN-MEMORY ONLY (lock-guarded) — turn
latency histogram (last 1000), token totals, per-tool calls/errors/durations
(last 100/tool); metrics() → p50/p95/p99; health degraded at error-rate ≥50%
over ≥5 calls. Sole durability: export_metrics() → ~/.config/wisp/metrics.json
during root shutdown — lost on crash/SIGKILL.

**Server** (FastAPI, `server/routes/`, ~66 endpoints across ~25 routers):
sessions(+fork/PATCH) · files(tree/edit/binary/rename) · runs/background ·
models · codebase(index/symbols) · git(status/diff/commit/push) · bash ·
prompt · jsonrpc · mcp(servers/tools) · swarm · arena · plugins · hooks ·
review · search · diagnostics · workspace · context · complete · diff ·
suggestions. Auth: REST X-API-Key/Bearer header; WebSocket first-frame
{"type":"auth","api_key"} (dev-open when WISP_API_KEY unset). WS client
msgs: prompt/tool_approval/interrupt/pause/resume/swarm_*/agents_*/ping;
server msgs: ready/content/thinking/tool_call/tool_result/approval_request/
tool_approved/status/error/complete/agents_*.

**Slash commands** (`commands.py`, @register): help clear model provider skill
session save tokens metrics compact approve thinking bash workspace grep ls read
drop spawn agents swarm new continue exit init sessions (+aliases h/? cls y T ! sh
cd w g search cat pop sub delegate multi c go on q bye ss).

**Env surface** (35 vars): provider `WISP_{API_KEY,API_BASE,MODEL,PROVIDER,
OLLAMA_URL}` · limits `MAX_TOKENS,MAX_ITERATIONS,MAX_CONTEXT_TOKENS,
CHARS_PER_TOKEN,AUTO_COMPACT,COMPACT_*,MAX_REFLECTIONS` · guards
`FIRST_TOKEN_DEADLINE,STREAM_ATTEMPTS,CB_*` · UX `OUTPUT_MODE,NO_COLOR,
HIGH_CONTRAST,HISTORY_FILE,LOG_FORMAT` · ops `WORKSPACE,WORKSPACE_MUTABLE,
TRUST_ALL_WORKSPACES,CONFIG_DIR,WS_AUTO_APPROVE,HEADLESS_AUTO_APPROVE,
KEEP_WORKTREES,PRODUCTION_MODE,WEB_PROXY,E2E_LIVE`.
policy/ux extras missed earlier: `PERMISSION_MODE (default auto_edit!),
TEMPERATURE(0.2), SHOW_THINKING, SHOW_TOOL_OUTPUT, WRITE_TOOLS,
COMPACT_THRESHOLD_TOKENS(%75), COMPACT_KEEP_RECENT(6), SKILL_DIRS,
AUDIT_LOG, CORS_ORIGINS, ENABLE_HSTS, ACCESSIBLE,
ALLOWED_OLLAMA_HOSTS, SUBAGENT_MODELS, THREAD_POOL_SIZE(8),
SUBAGENT_TOKEN_BUDGET(2M)`.
Config resolution: env > ~/.config/wisp/config.json > SETTINGS_SCHEMA
default (`config.py:305-312`); ~35 settings total.

## 8. The dots connected — one prompt's journey

```
keypress ➜ readline/_input_line (ESC-strip, typeahead capture registered)
 ➜ entry._run_turn: reset buffers → start_wait_clock → AgentRuntime.run_turn
 ➜ per-session lock → cached WispAgentCore.turn(session,prompt,approve,steer_drain)
 ➜ _maybe_delegate? (≤10s classify) → announce BEFORE children launch
 ➜ _guarded_provider_stream (first-token 90s, empty×3) → events…
 ➜ tool_call → policy engine → hooks → registry impl / SubagentOrchestrator
     ↳ children: grounded tasks → semaphore(adaptive) → runner turns
       → task_retry backoff on 429 → honest ✓/✗ lifecycle → digest
 ➜ events stream to CLITransport._render_event (wait-clock stops on first)
 ➜ spinner 120ms ↔ tool result ✓/✗ · diff card · phase bar · ctx meter
 ➜ done → stats+ticker+rule → save_session(SQLite) → readline history
```

## 9. Fragile-edge register (known sharp wires)

1. Dual event shapes (flat vs nested `data`) — every new consumer must squeeze both.
2. Two-phase wiring cycles (executor↔orchestrator↔runtime): new services must
   late-bind or they construct `None`.
3. Lazy imports: `requests`/`textual` deferred — anything touching those modules
   at import time breaks the CLI fast-path (guarded by test_lazy_imports).
4. Spinner/log/tty interleaving: any direct stderr writer races the animation
   line; route through `_SpinnerAwareHandler`.
5. CSS auto-sizing: textual 8.x collapses `width:auto` containers with fr
   children — pixel-level goldens are the only guard that caught it.
6. `tool_call` vs `tool_calls` bifurcation: providers may emit batches;
   normalization lives inside the iteration loop (`stateless.py:223-290`) —
   refactors there can leak batch shapes downstream.
7. Steering has three separate event types (`steering_paused/inject/resumed`)
   with different payloads — no unified envelope for transports.
8. Error-path message duplication risk: `run_turn`'s finally re-records
   assistant/tool messages (`runtime.py:249+`) even if partial iteration
   already appended them (`stateless.py:434-445`).
9. Timeout retry skipped when parent turn has <35s left (`orchestrator.py`
   guard) — child dies though more time would save it.
10. Worktree isolation memoizes its FIRST failure permanently for the
   orchestrator instance — a transient git lock disables isolation for all
   later children.
11. Background send() impossible for stateless-path children (no persisted
   session); DAG has no continue-on-error; schema-fix retry reuses the
   original timeout/iteration budget.
12. get_store() singletons keyed by raw path string — relative vs absolute
   spellings of one db create separate pools/locks.
13. Telemetry volatile: lost on crash since export runs only in graceful
   shutdown.
14. WS auth window: socket accepted before auth frame processed.
15. Rate limiter keys on client IP only and self-disables on any SQLite error.
16. Headless-root cache ignores env changes (mtime-only key) — stale model.
17. Non-owning executor registered on two loops when bind_loop targets a
   different loop than __post_init__ saw — first loop's executor leaks.
18. Global vs per-turn SIGINT handlers race on nested Ctrl+C.
