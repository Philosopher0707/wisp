# Tool Wiring Map — advertisement vs dispatch

Every surface that tells the model a capability exists, and the path that
must actually execute it. **Invariant (enforced by
`tests/test_tool_wiring.py`): every advertised name resolves to an execution
path — schemas without dispatch are lies to the model.** First violated by
`skill__*` (fixed in `3f5e61c`); this map keeps the next violation from
landing silently.

## Matrix

| Capability | Advertised via | Dispatched via | Status |
|---|---|---|---|
| Built-in tools (~40) | `TOOL_SCHEMAS` → provider payload + prompt `## Tools available` | `ToolExecutor._execute_tool` ladder → `registry.execute_tool` (`TOOL_IMPLS`) | ✅ |
| Subagents: `spawn`, `fanout` | builtin schemas | executor special-case (streaming queue) → orchestrator | ✅ |
| `spawn_background` | builtin schemas | executor → `BackgroundAgentManager.launch`; results via `subagent_result` / push | ✅ |
| `subagent_list/result/send/cancel` | builtin schemas | executor → manager | ✅ |
| `capture_skill` | builtin schema | executor → `SkillCapture` | ✅ |
| MCP tools `mcp:server/tool` (+legacy `mcp__`) | `MCPExtension.tools()` from live servers | `_is_external_call` → `_call_mcp_tool` → `manager.call_tool`; builtins win bare-name collisions | ✅ |
| Skills `skill__<name>` | `SkillExtension.tools()` from SKILL.md discovery | `ExtensionHost.call_tool` → returns SKILL.md body + suggestion footer; unknown skill errors cleanly | ✅ fixed `3f5e61c` |
| Plugins `{ns}__{tool}` | **nothing today** — `PluginManifest` carries skills/commands/hooks/mcp_servers but no tool contract, so `PluginExtension.tools()` always returns `[]` | none | ➖ honestly inert; if plugins ever advertise, the invariant test forces dispatch to land first |
| Hooks extension | advertises `[]` | intercept-only by design | ✅ |

## Flow (CLI agent)

```
prompt ─▶ AgentRuntime.run_turn ─▶ WispAgentCore.turn
   tools = _get_tool_schemas()      # TOOL_SCHEMAS ∪ ExtensionHost.tools()
   system prompt                    # lists every advertised name
        ▼
provider stream ── tool_call event ──▶ ToolExecutor.execute(name, args)
                                          1 hooks / plan / danger / permission-mode
                                          2 approval gate (engine handler(event)->bool)
                                            · policy AUTO/BLOCK short-circuits
                                            · ask_all routes writes to handler
                                          3 dispatch:
                                              builtin       → TOOL_IMPLS
                                              mcp:*         → manager.call_tool
                                              else          → ExtensionHost.call_tool
                                                          (skills serve instructions)
```

## Background-agent settlement wiring

| Transport | Mechanism |
|---|---|
| WebSocket | `server/routes/agents.py` — subscriber task pushes `agent_started/settled` frames per client |
| CLI | `CLITransport(background_agents=...)`; watcher task auto-starts on first `send()`, prints `[bg] ✓/✗ <label> settled in Ns — summary · fetch: subagent_result {...}` between turn output; `stop()` cancels + unsubscribes |
| TUI | polls via `subagent_list` / `subagent_result` tools (no push yet) |

Settlement event shape (from `BackgroundAgentManager._publish_settlement`):
`{type, agent_id, label, status, ok, turns, elapsed_seconds, task, error|summary}`.
Rendering precedence: `error` beats `summary` — a failed agent's reason
matters more than partial output.

## Known seams to watch

1. **Extension ordering**: host dispatch is last (builtins → MCP → host), so a
   hostile extension cannot shadow core tools; covered by
   `test_builtin_wins_over_extension`.
2. **Watcher race**: CLI subscribes on first rendered event of a turn; a
   background agent that spawns AND settles within the same pre-render window
   would miss its notice (poll `subagent_list` remains correct). Accepted:
   renders start flowing within milliseconds of any turn.
3. **Plugins** stay unadvertised until a real manifest→code loading contract
   exists. The invariant test converts any future silent advertisement into a
   red suite.
