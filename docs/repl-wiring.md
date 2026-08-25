# Wisp REPL — complete wiring map

Authoritative trace of the interactive path, as of this audit round. Every
arrow was verified against source, not memory. Findings are at the bottom.

## 1. Process entry

```
python -m wisp <cmd> …
└─ __main__.main()            hand-rolled argv parse (no argparse)
   ├─ extract_global_flags()  --model/-m --skill/-s --session/-S
   │                          --workspace/-w --auto-approve/-y
   │                          --show-thinking/-T --print --output-format --quiet
   └─ subcommand dispatch     run|repl|tui|skills|config|check|models|…
      └─ entry.run_mode(mode, prompt, **kwargs)
```

## 2. CompositionRoot (`composition.py`) — what gets wired

Constructed synchronously *before* any event loop exists.

| Service | Created | Consumed by |
|---|---|---|
| `UnifiedStore(db_path)` | `.wisp/wisp.db` under workspace | runtime sessions, session_repo events |
| `ImmutableAuditTrail` | wraps store | SecurityPolicy |
| `SecurityPolicy(permission_mode)` | from config | core approval gate |
| `ExtensionHost` | plugin/hook/mcp/skill extensions registered | core intercept + tools |
| `Telemetry` | counters | metrics export at shutdown |
| `InterceptHookManager` / `ToolHookManager` | `.wisp/hooks` scripts | pre/post tool paths |
| `MCPManager(workspace)` | lazy stdio connects on first use | ToolExecutor dispatch + MCPExtension schemas |
| `FileLock(workspace)` | cross-session file writes | ToolExecutor |
| `LSPManager` | language servers on demand | editor-ish tools |
| `AgentRuntime(store, security, extensions, telemetry, core_factory…)` | owns sessions, locks, core cache, orchestrator | everything |
| shared thread pool (`thread_pool_size`) | `asyncio_utils` | *(was dead — see F-1)* |

Lifecycle: `root.start()` validates config + starts registry services;
`root.shutdown()` stops registry, exports metrics, shuts LSP + background
loop + executor. MCP stdio teardown historically relied on `atexit`
(F-2 below).

## 3. REPL bootstrap (`entry._run_repl`)

```
loop = asyncio.new_event_loop()          # persistent for whole session
root.bind_loop(loop)                     # NEW: registers shared executor (F-1)
session = runtime.get_or_create_session(sid, model, workspace)
_load_command_history()                  # ~/.wisp/history (WISP_HISTORY_FILE)
signal.SIGINT ← make_repl_sigint_handler(transport, get_task, restore)
banner (continuation if messages exist)
adapter = AgentAdapter(runtime, config, session, loop=loop)   # ONE per session
```

`AgentAdapter` exists so the slash-command registry can keep its old
"agent" interface (`agent.session`, `agent.messages`, `agent.config`,
`agent.client`). `/new` replaces `adapter.session`; every consumer reads it
live (`_current_session_id()`, `_run_turn`, `_force_save`).

## 4. Input layer (`transport/cli.py`)

- `_input_line("➜ ")`: tty → readline-backed `input()`; ANSI-stripped;
  trailing `\` continuation; non-tty → stdin lines; EOF → None.
- `_input_multiline`: double-blank-line submits; Ctrl+C clears input
  (raises→catches internally, returns ""); EOF ends; non-tty reads rest of
  stdin (None when exhausted).
- SIGINT ownership: `entry.make_repl_sigint_handler()` — turn running ⇒
  cancel task + de-arm (second press = default handler = hard quit);
  idle ⇒ raise KeyboardInterrupt (single-line exits; multiline clears).

## 5. Routing (`entry` while-loop)

1. empty line → re-prompt
2. `exit|quit` → break
3. `/multiline[ args]` exact-token interception (mode toggle, no registry)
4. `/…` → `commands.dispatch(prompt, adapter)`
   - registry: decorator-registered, alias-theft raises at import time
   - unknown command → message + continue
   - handler returns str → follow-up turn via `_run_turn(str)`
   - `ExitREPL` → break
5. else plain prompt → `_run_turn(prompt)`

Commands (~24): help clear model skill session save tokens metrics compact
approve thinking bash workspace grep ls read drop spawn swarm new continue
exit init (+ aliases).

## 6. Turn execution

```
_run_turn(prompt)
└─ task = loop.create_task(_turn()); loop.run_until_complete(task)
   └─ runtime.run_turn(adapter.session, prompt, approval_handler)
      ├─ per-session asyncio.Lock  (held lock is never LRU-evicted)
      ├─ crash recovery: last event ≠ DONE → replay messages from repo
      ├─ maybe_compact(session)          # boundary-snapped, 90s-bounded LLM
      ├─ append user message (+repo event)
      ├─ core = cached WispAgentCore     # invalidated on config fingerprint
      ├─ async for ev in core.turn(...)  # the engine (stateless.py)
      │    normalize flat dicts, collect content/tool_calls/tool_results
      └─ finally: persist assistant tool_calls (JSON-string args, stable ids),
         matching tool results, content; DONE event; store.save_session
```

Inside `core.turn`: schema build (role-filtered via `allowed_tools`) →
provider stream → normalization → role rejection → approval gate →
extension intercept → yield `tool_call`; execution happens in the transport
consumer? **No — in the core**, via `tool_executor.execute()` (below), whose
`tool_result` events are yielded back through the same generator.

## 7. Tool execution (`tool_executor.execute`)

Order: pre-tool hooks → plan-mode guard → dangerous-command check →
permission gate (read_only hard block / forced approval) → approval handler →
dispatch:
- `spawn`/`fanout` → SubagentContract (depth-inherited) → orchestrator;
  lifecycle events stream through an asyncio.Queue interleaved with waiting
- external (`mcp:*`, legacy, bare-name w/o builtin collision) → MCPManager
- builtins → special-cased `run_bash`, else `registry.execute_tool`
Post: write-verify lint feedback, hooks, metrics, audit trail.

Approval UX: y/Y/a/n/N/d/c with session memory (`allowed/denied_tools`,
policy); reader thread uses select() so cancellation releases it in ~0.2s;
spinner stopped during prompt.

## 8. Event rendering (`CLITransport._render_event`)

Per event: ProgressTracker phase detection (phase bar on change) →
type switch:
- THINKING/CONTENT buffered, flushed on transitions & end
- TOOL_CALL → flush buffers, spinner.start(label)
- TOOL_RESULT → <50ms: stop spinner + inline block; else succeed/fail label
- DONE/SYSTEM/ERROR/SUBAGENT/PROVIDER_STATUS → dedicated renderers
All output mode-aware via BoxChars/status_symbols/display_width.

Turn end (`entry`): stats + file ticker + separator; resume banner on
cancel/interrupt/error; spinner killed on every exception path.

## 9. Exit paths

Any of: `exit|quit`, ExitREPL, EOF, idle Ctrl+C, force-quit KI, input error.
Cleanup order: restore signal handler → cancel task + settle →
save history → save session (warn on failure) → close own loop →
(`_run_cli` finally) drain loop + close + transport.stop →
(`run_mode` finally) root.shutdown().

## Findings

- **F-1 (fixed)** shared executor was registered only when a loop was
  already running — never true at composition time — so
  `thread_pool_size` was inert and `to_thread`/`run_in_executor(None)`
  used ad-hoc default executors. Now: `root.bind_loop(loop)`.
- **F-2 (fixed)** MCP stdio servers were torn down only by process
  `atexit`; `root.shutdown()` now disconnects them explicitly.
- **F-3 (fixed)** an exception escaping `delegate_task.result()` aborted the
  turn before persistence; now converted to a recoverable error event.
- Verified sound: per-session lock pinning, alias-theft guard, /new swap,
  crash replay + args healing, approval select-thread, spawn event bridge,
  role filtering chain, depth inheritance chain.
