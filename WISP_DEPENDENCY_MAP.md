# WISP DEPENDENCY MAP

> **SUPERSEDED — the cycle list below is stale.** An independent Tarjan SCC run
> over the current tree (366 modules / 841 edges) does not reproduce cycle #5
> (the 8-module `arena ↔ background_agent ↔ entry ↔ server.main ↔ routes{…}`
> chain). The actual cycle set is:
>
> 1. `repl.commands` + its submodules (a registry pattern — arguably benign)
> 2. `cli.ui.guard ↔ transport ↔ transport.cli`
> 3. `server ↔ server.main ↔ server.routes.jsonrpc`
> 4. `provider_catalog ↔ provider_select`
> 5. `transport.tui ↔ tui.screens.workspace`
> 6. `core.doctor ↔ core.runtime`
> 7. `cli.dispatcher ↔ graph.cli`
>
> The fan-in / fan-out tables remain broadly accurate. For the current model see
> `REPOSITORY_INTELLIGENCE_REPORT.md` §5.


## Layers (derived, top-down)

```
REPL/CLI (__main__, cli/repl.py, dispatcher)
  ↓
Coding (coding.py: TaskContext, strategy gate)
  ├── Agent Loop (core/stateless, runtime, composition, entry)
  └── Graph API (graph/api, graph/cli)
         ↓
Graph Runtime (executor, scheduler, planner, optimizer, coding_graphs)
  ↓ (node execution via runner)
Subagents (orchestrator, runners, worktrees, background)
  ↓
ToolExecutor → authorize() → ApprovalGate / workspace trust
  ↓
Tools (registry + filesystem/bash/git/lsp/search/web) + Sandbox + RepoMap
  ↓
Persistence (UnifiedStore SQLite, audit_log) + Artifacts (.wisp/)
```

Cross-cutting (depended on upward): `wisp.config` (40), `colors`,
`terminal_width`, `core.events`, `tools._utils`, `auth.secrets`,
`infra.store`, `async_utils`. Providers plug in sideways via protocol +
factory; never imported by core loop internals.

## Suspicious upward dependencies

- `graph/cli.py` → `cli/dispatcher.py` (execution layer imports REPL layer; deferred import, works, wrong direction — P2).
- `transport/tui.py` ↔ `tui/screens/workspace.py` (cycle).
- `server/*` ↔ `entry`/`arena` (8-module cycle — P1).
- `core/doctor.py` ↔ `core/runtime.py` (cycle; doctor inspects runtime).
- `provider_catalog` ↔ `provider_select` (cycle).

## Cycles (Tarjan, absolute-import graph)

1. `core.doctor ↔ core.runtime`
2. `provider_catalog ↔ provider_select`
3. `cli.dispatcher ↔ graph.cli`
4. `transport.tui ↔ tui.screens.workspace`
5. `arena ↔ background_agent ↔ entry ↔ server.main ↔ server.routes.{arena,diff,review,runs}` ← P1

## Top fan-in (dependents)

wisp.config 40 · server.deps 27 · server.routes.workspace 20 · colors 18 ·
graph.types 16 · core.events 15 · tools._utils 15 · infra.security 11 ·
infra.store 11 · auth.secrets 11 · terminal_width 10 · repl.commands 10 ·
core.contracts 8 · async_utils 8 · provider_select 8 · tools.errors 8 ·
infra.audit 7 · wisp.tools 7 · graph.store 7 · wisp.entry 7.

## Top fan-out (dependencies)

server.main 31 · __main__ 27 · core.stateless 27 · composition 24 ·
tui.screens.workspace 23 · tool_executor 22 · entry 20 · tools.registry 16 ·
transport.cli 14 · graph.cli 14 · core.doctor 12 · cli.repl 12 ·
multi_agent._runner 10 · core.runtime 10 · sdk 9 · graph.executor 9.

## Machine-readable

`wisp_codebase_metrics.json` (counts). Method for edges/cycles: AST walk of
absolute `wisp.*` imports (including function-level deferred imports) +
Tarjan SCC; the exact procedure is documented in WISP_CODEBASE_CENSUS.md §1
and is re-runnable read-only in ~2s.
