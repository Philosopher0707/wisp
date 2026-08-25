# Wisp Architecture

This document provides a detailed technical overview of Wisp's architecture.

## System Overview

Wisp is a **local-first coding agent** built as an SDK with clean layer separation:

```
User Input
    │
    ▼
┌────────────────────────────────────────────────────────────────┐
│                     TRANSPORT LAYER                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│  │   CLI    │ │   TUI    │ │ WebSocket│ │   SSE    │          │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘          │
│         │           │           │           │                   │
│         └───────────┴───────────┴───────────┘                   │
│                         │                                       │
│                   Transport ABC                                 │
│              (send, recv, approve, start, stop)                 │
└────────────────────────────────────────────────────────────────┘
    │
    ▼
┌────────────────────────────────────────────────────────────────┐
│                      AGENT RUNTIME                             │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    AgentRuntime                           │  │
│  │  • Session CRUD (SQLite)                                 │  │
│  │  • Per-session asyncio.Lock (concurrent turn safety)     │  │
│  │  • Core instance caching (fingerprint-based invalidation)│  │
│  │  • Auto-compaction (LLM summarization + fallback)        │  │
│  │  • Telemetry (latency, tokens, turns)                    │  │
│  │  • Subagent orchestration                                │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
    │
    ▼
┌────────────────────────────────────────────────────────────────┐
│                       CORE LAYER                                │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                  WispAgentCore (stateless)               │  │
│  │  turn(session, prompt, approval_handler)                 │  │
│  │   → Builds system prompt (context assembler)             │  │
│  │   → Streams provider events (with circuit breaker)       │  │
│  │   → Parses tool calls (ToolCallBatch support)            │  │
│  │   → Security/extension checks                            │  │
│  │   → Executes tools via ToolExecutor                      │  │
│  │   → Yields flat dict events                              │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
    │
    ▼
┌────────────────────────────────────────────────────────────────┐
│                    INFRASTRUCTURE                               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│  │ Provider │ │ Security │ │  Store   │ │ Extensions│         │
│  │ (Ollama) │ │ (Policy) │ │ (SQLite) │ │ (Hooks)  │          │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘          │
└────────────────────────────────────────────────────────────────┘
```

## Data Flow: Single Turn

```
1. User types prompt
   │
   ▼
2. Transport.recv() → returns prompt string
   │
   ▼
3. Runtime.run_turn(session, prompt)
   │  ├─ Acquires session lock
   │  ├─ Adds user message to session
   │  ├─ Auto-compact if needed
   │  └─ Gets cached core (or creates)
   │
   ▼
4. Core.turn(session, prompt)
   │  ├─ Builds system prompt (ContextAssembler)
   │  │   ├─ Rules.md, conventions.md
   │  │   ├─ Skills (.agents/skills/)
│  │   ├─ Project context (detect type)
│  │   ├─ Repo map (tree-sitter + pagerank)
│  │   ├─ Git context (branch, diff, log)
│  │   ├─ Lint context (available checkers)
│  │   └─ Module summary (top-level packages)
│  │
│  ├─ Gets tool schemas (built-in + extensions)
│  │
│  ├─ Loop: provider → tool_calls → execute → append
│  │   ├─ Stream events from provider (circuit breaker)
│  │   ├─ Normalize events (tool_calls → individual tool_call)
│  │   ├─ Security check (ApprovalGate)
│  │   ├─ Extension intercept
│  │   ├─ Execute tools (ToolExecutor)
│  │   └─ Append assistant + tool messages
│  │
│  └─ Yields events: thinking, content, tool_call, tool_result, done
   │
   ▼
5. Transport.send(event) for each event
   │  ├─ Renderer formats for terminal
   │  ├─ ProgressTracker detects phase
   │  ├─ Spinner for tool execution
   │  └─ Turn stats + file ticker on done
   │
   ▼
6. Session saved, lock released
```

## Core Components

### WispAgentCore (`wisp/core/stateless.py`)

**Stateless turn engine** — no instance state, all dependencies injected.

```python
core = WispAgentCore(
    config=WispConfig(),
    provider=OllamaProvider(config),
    security=SecurityPolicy(...),
    extensions=ExtensionHost(),
    tool_executor=ToolExecutor(...),
)
async for event in core.turn(session, "refactor auth.py"):
    print(event)
```

**Key methods:**
- `turn(session, prompt, approval_handler)` — main entry point
- `_build_system_prompt()` — assembles rich context
- `_stream_events_async()` — wraps sync provider in thread + circuit breaker
- `_execute_tool()` — delegates to ToolExecutor

**Module-level caches** (shared across parent + subagents):
- `_SYSTEM_PROMPT_CACHE` — keyed by `(workspace, context_mtime)`
- `_ASSEMBLER` — lazy ContextAssembler

### AgentRuntime (`wisp/core/runtime.py`)

**Stateful session manager** — owns sessions, not logic.

```python
runtime = AgentRuntime(
    store=UnifiedStore(...),
    security=SecurityPolicy(...),
    extensions=ExtensionHost(),
    telemetry=Telemetry(),
    core_factory=lambda: WispAgentCore(...),
)
session = await runtime.get_or_create_session("sid", "llama3.2", ".")
async for event in runtime.run_turn(session, "prompt"):
    ...
```

**Concurrency:** Per-session `asyncio.Lock` prevents interleaved turns.

**Core caching:** `_get_core()` uses config fingerprint for invalidation.

### Transport ABC (`wisp/transport/base.py`)

```python
class Transport(ABC):
    async def send(self, event: dict) -> None: ...
    async def recv(self) -> str | None: ...
    async def approve(self, tool_call: dict) -> bool: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
```

**Implementations:**
- `CLITransport` — REPL with structured panels, spinners, approval prompts
- `TUITransport` — Textual-based full-screen UI
- `WebSocketTransport` — JSON event streaming for web clients
- `SSETransport` — Server-Sent Events for simple HTTP streaming
- `HeadlessTransport` — Collects events for testing/programmatic use
- `FileTransport` — Logs events to file

### Renderer (`wisp/transport/renderer.py`)

**Pure functions** — mode-aware, testable.

```python
render_tool_call(name, args, box_mode=True)
render_thinking_block(text, box_mode, width)
render_content_block(text, box_mode, width)
render_phase_bar(phase, stats, width)
render_turn_stats(stats, width)
render_file_ticker(files, width)
_box(content, title, style, double, width)
_rule(char, label, style_fn, width)
```

**Output modes** (via `wisp.terminal_width`):
```python
OutputMode.UNICODE    # emoji, box chars
OutputMode.ASCII      # ASCII box chars
OutputMode.ACCESSIBLE # semantic labels, no emoji
OutputMode.MINIMAL    # flat, no boxes
```

### Provider Protocol (`wisp/providers/protocol.py`)

```python
class Provider(ABC):
    def generate_stream_events(...) -> Generator[dict, None, None]: ...
    async def generate_stream_events_async(...) -> AsyncIterator[dict]: ...
    def health_check(self) -> dict: ...
    def list_models(self) -> list[dict]: ...
    def get_model_info(self, model: str) -> dict: ...
    def generate_structured(self, system, messages, schema) -> dict: ...  # optional
```

**Event types from provider:**
- `{"type": "content", "text": "..."}`
- `{"type": "thinking", "text": "..."}`
- `{"type": "tool_call", "name": "...", "arguments": {...}}`
- `{"type": "tool_calls", "calls": [...]}` — batched (preferred)
- `{"type": "done", "done_reason": "stop|length"}`
- `{"type": "error", "message": "..."}`

### Tool System (`wisp/tools/`)

~40 tools across modules:
| Module | Tools |
|--------|-------|
| `filesystem.py` | read_file, write_file, edit_file, edit_file_multi, list_files |
| `bash.py` | run_bash |
| `web.py` | web_fetch, web_search |
| `git.py` | git_status, git_diff, git_branch, git_commit, git_push, gh_pr_create |
| `lsp.py` | lsp_diagnostics, lsp_definition, lsp_references, lsp_hover, lsp_symbols |
| `memory.py` | remember, recall |
| `search.py` | search_symbols, search_codebase |
| `plan.py` | plan_task, mark_step_done, update_plan |
| `diagnose.py` | diagnose |
| `tests.py` | run_tests |
| `subagent.py` | spawn, fanout |

**Executor-dispatched tools** (schemas in `registry.py`, handled by
`ToolExecutor` against the orchestrator/manager — no registry impl):
spawn_background, subagent_list/result/send/cancel,
orchestrate_vote/map_reduce/chain/dag, capture_skill.

**Registry:** `TOOL_SCHEMAS` (list) + `TOOL_IMPLS` (dict) in `registry.py`

**Executor:** `ToolExecutor` handles permissions, hooks, dispatch, metadata.
The system prompt's "## Tools available" menu is GENERATED from these live
registries (plus extension/MCP tools) — never hand-maintained.

### Multi-Agent (`wisp/multi_agent/`)

```python
orch = SubagentOrchestrator(parent_agent=my_agent)

# Single
result = await orch.run(SubagentContract(task="Audit auth.py"))

# Parallel
results = await orch.run_parallel([contract1, contract2])

# Map-reduce
result = await orch.run_map_reduce(task="Review", items=files, mapper=..., reducer="Synthesize")

# Voting
result = await orch.run_vote(task="Is this vulnerable?", agents=[...], threshold=0.6)

# Chain
result = await orch.run_chain([writer, reviewer], pass_context=True)

# DAG — dependency graph; upstream outputs are injected into dependents
dag = TaskDAG()
dag.add_node(TaskNode(name="design", task=contract_a))
dag.add_node(TaskNode(name="build", task=contract_b, dependencies=["design"]))
result = await orch.run_dag(dag)
```

### Background Agents (`wisp/multi_agent/background.py`)

`BackgroundAgentManager` wraps orchestrator runs in asyncio tasks with a
bounded registry (8 running / 50 finished). Non-blocking delegation for
the parent turn:

- `launch(contract)` → `{agent_id}` immediately; `send(agent_id, msg)`
  resumes the SAME child session (`_resume_session_id`)
- Lifecycle fan-out: subscribers (`subscribe()`) receive `agent_started`,
  `agent_progress`, `agent_settled` events — consumed by the WebSocket
  pusher and the per-turn operating-context drain
- Surfaced via REPL `/agents`, REST `/api/agents/background*`, and the
  model-facing subagent_* tools

### Skill Capture (`wisp/skill_capture.py`)

Records tool-call sequences, detects repeated tail workflows, renders
Warp-compatible SKILL.md files. Re-captures merge via a `wisp_captures`
count; differing sequences become variants; foreign skills get sibling
slugs instead of being overwritten. Model-facing via the `capture_skill`
tool; human-facing via `/skill suggest` and `/skill save`.

**Roles** (pre-configured tool sets + timeouts):
- `coder` — full toolset, 10 min, 30 iterations
- `reviewer` — read + lsp + diagnose, 5 min, 10 iterations
- `tester` — read + run_tests + bash, 5 min, 15 iterations
- `researcher` — web_search + search_codebase, 5 min, 10 iterations
- `planner` — plan_task + read, 3 min, 5 iterations
- `debugger` — diagnose + lsp + bash, 5 min, 15 iterations
- `generalist` — balanced, 5 min, 15 iterations

---

## Configuration System

`WispConfig` (`wisp/config.py`) — immutable dataclass with `replace()`.

**Resolution:** env var > config file > default

```python
config = WispConfig()  # reads env + ~/.config/wisp/config.json
config = config.replace(model="llama3.2", temperature=0.1)
```

**Key settings:**
- `provider`, `model`, `temperature`, `max_tokens`
- `auto_approve`, `permission_mode` (full/ask_all/auto_edit/read_only)
- `show_thinking`, `show_tool_output`, `compact_mode`
- `max_iterations`, `turn_timeout`, `max_reflections`
- `auto_compact`, `compact_threshold_tokens`, `compact_keep_recent`
- `circuit_breaker_failure_threshold`, `circuit_breaker_recovery_timeout`

---

## Testing Strategy

### Unit Tests (Fast, No I/O)

```python
# Mock provider
class _MockProvider:
    def generate_stream_events(self, system_prompt, messages, tools=None):
        yield {"type": "content", "text": "Hello"}
        yield {"type": "done"}

core = WispAgentCore(provider=_MockProvider(), ...)
```

### Transport Tests (`_MockRuntime` + `_MockIO`)

```python
# StringIO-based stdin/stdout
transport = CLITransport(_MockRuntime(), config)
transport._stdin = StringIO("prompt\n")
transport._stdout = StringIO()
```

### Integration Tests

Marked with `@pytest.mark.live` — require running Ollama.

---

## Extending Wisp

### Add a Tool
1. Schema in `TOOL_SCHEMAS` (`wisp/tools/registry.py`)
2. Implementation in `wisp/tools/<module>.py`
3. Register in `TOOL_IMPLS`
4. Tests in `tests/test_tools_registry.py`

### Add a Transport
1. Extend `Transport` ABC
2. Implement 5 methods
3. Register in `wisp/transport/__init__.py`
4. Use `renderer.py` functions for output

### Add a Provider
1. Implement `Provider` protocol
2. Add to `ProviderFactory` in `wisp/providers/factory.py`
3. Register in config schema

### Add an Extension
1. Implement `Extension` interface
2. Register via `ExtensionHost.register()`
3. Can intercept tool calls, add tools, hook events

---

## Performance Considerations

- **Core caching**: System prompt cached per workspace (invalidated on file mtime change)
- **Repo map**: Pagerank-based, cached, fast_mode for subagents
- **Thread pool**: Shared executor for sync→async bridging (configurable size)
- **Circuit breaker**: Prevents cascade failures on provider issues
- **Compaction**: LLM summarization keeps context window healthy

---

## Security Model

```
Tool Call → ApprovalGate.check() → allowed?
                    │
                    ├─ PermissionMode.FULL → always allow
                    ├─ PermissionMode.ASK_ALL → prompt user
                    ├─ PermissionMode.AUTO_EDIT → prompt for bash only
                    └─ PermissionMode.READ_ONLY → deny writes
                    │
                    ▼
           ExtensionHost.intercept() → block/modify?
                    │
                    ▼
           ToolExecutor.execute() → permission check + hooks
```

---

## File Structure

```
wisp/
├── __init__.py              # Public exports
├── __main__.py              # CLI entry point
├── config.py                # WispConfig
├── sdk.py                   # High-level sync API
├── entry.py                 # CompositionRoot + mode runners
├── composition.py           # Service wiring
├── core/
│   ├── __init__.py
│   ├── stateless.py         # WispAgentCore (THE core)
│   ├── engine.py            # Back-compat re-export
│   ├── events.py            # AgentEvent, factories
│   ├── runtime.py           # AgentRuntime
│   ├── session.py           # Session models
│   ├── compaction.py        # Compactor
│   └── approval_gate.py     # ApprovalGate
├── transport/
│   ├── __init__.py
│   ├── base.py              # Transport ABC
│   ├── cli.py               # CLITransport
│   ├── tui.py               # TUITransport
│   ├── server.py            # WebSocketTransport
│   ├── sse.py               # SSETransport
│   ├── headless.py          # HeadlessTransport
│   ├── file.py              # FileTransport
│   ├── renderer.py          # Pure rendering functions
│   ├── progress.py          # ProgressTracker
│   └── spinner.py           # Spinner
├── providers/
│   ├── __init__.py
│   ├── protocol.py          # Provider ABC
│   ├── factory.py           # ProviderFactory
│   └── ollama.py            # OllamaProvider
├── tools/
│   ├── __init__.py
│   ├── registry.py          # TOOL_SCHEMAS, TOOL_IMPLS
│   ├── filesystem.py
│   ├── bash.py
│   ├── web.py
│   ├── git.py
│   ├── lsp.py
│   ├── memory.py
│   ├── search.py
│   ├── plan.py
│   ├── diagnose.py
│   ├── tests.py
│   └── subagent.py
├── multi_agent/
│   ├── __init__.py
│   ├── subagent_orchestrator.py
│   ├── _runner.py
│   ├── _patterns.py
│   ├── task.py
│   ├── roles.py
│   ├── delegation.py
│   └── ...
├── infra/
│   ├── __init__.py
│   ├── store.py             # UnifiedStore (SQLite)
│   ├── security.py          # SecurityPolicy, Action, Context
│   ├── audit.py             # ImmutableAuditTrail
│   ├── extensions.py        # ExtensionHost
│   ├── telemetry.py         # Telemetry
│   ├── lifecycle.py         # ServiceRegistry
│   ├── circuit_breaker.py   # CircuitBreaker
│   ├── policy_engine.py
│   ├── hook_types.py
│   └── ...
├── ollama_client.py         # Ollama HTTP client
├── stream_events.py         # Typed stream events (TokenBatch, ToolCallBatch, etc.)
├── stream_parser.py         # Ollama stream parser
├── context_assembler.py     # System prompt builder
├── repo_map.py              # Code map with pagerank
├── project_context.py       # Project type detection
├── git_context.py           # Git info formatter
├── terminal_width.py        # display_width, BoxChars, OutputMode
├── colors.py                # Terminal colors
├── async_utils.py           # Thread pool, async helpers
├── tool_executor.py         # Tool execution engine
├── commands.py              # Slash commands
└── extensions/              # Plugin/hook/skill extensions
```