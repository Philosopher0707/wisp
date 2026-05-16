# Wisp Agent + Subagent Architecture

```mermaid
flowchart TB
    subgraph User["👤 User"]
        U1["Prompt / REPL / API"]
    end

    subgraph MainAgent["🔮 WispAgentCore (Main Agent)"]
        direction TB
        MA1["_build_system_prompt()"]
        MA2["_arun() — Turn Loop"]
        MA3["_run_turn_streaming_events()"]
        MA4["_run_tool_calls()"]
        MA5["_maybe_compact_session()"]
        MA6["_auto_parallel_research()"]
        MA7["_check_delegation()"]
        MA8["run_task() — Non-interactive"]

        MA1 --> MA2
        MA2 --> MA3
        MA3 --> MA4
        MA2 --> MA5
        MA2 --> MA6
        MA2 --> MA7
        MA8 -.-> MA3
    end

    subgraph SubagentSpawn["🔄 Subagent Spawning"]
        direction TB
        SS1["spawn_subagent tool call"]
        SS2["Depth Check: depth < 2?"]
        SS3["Branch Check: branch < 3?"]
        SS4["Build SubagentContract"]
        SS5["Cache Check"]
        SS6["SubagentOrchestrator.run()"]

        SS1 --> SS2
        SS2 -->|Yes| SS3
        SS2 -->|No| SS_ERR["[Error: max depth exceeded]"]
        SS3 -->|Yes| SS4
        SS3 -->|No| SS_ERR2["[Error: max branching exceeded]"]
        SS4 --> SS5
        SS5 -->|Miss| SS6
        SS5 -->|Hit| SS_CACHE["Return cached output"]
    end

    subgraph Orchestrator["🎛️ SubagentOrchestrator"]
        direction TB
        OR1["run() / run_parallel()"]
        OR2["_create_worktree()"]
        OR3["_build_child_config()"]
        OR4["_default_system_prompt()"]
        OR5["Isolation = thread?"]
        OR6["_spawn_subagent_thread()"]
        OR7["_spawn_subagent_process()"]
        OR8["_run_agent_sync()"]
        OR9["_run_agent()"]
        OR10["Result + Telemetry"]

        OR1 --> OR2
        OR2 --> OR3
        OR3 --> OR4
        OR4 --> OR5
        OR5 -->|Yes| OR6
        OR5 -->|No| OR7
        OR6 --> OR8
        OR7 --> OR8
        OR8 --> OR9
        OR9 --> OR10
    end

    subgraph ThreadMode["🧵 Thread Isolation (Default)"]
        direction TB
        TM1["asyncio.to_thread()"]
        TM2["_run_agent_sync()"]
        TM3["asyncio.new_event_loop()"]
        TM4["loop.run_until_complete()"]
        TM5["_run_agent()"]
        TM6["WispAgentCore.run_task()"]
        TM7["Cancel pending tasks"]
        TM8["loop.close()"]

        TM1 --> TM2
        TM2 --> TM3
        TM3 --> TM4
        TM4 --> TM5
        TM5 --> TM6
        TM6 --> TM7
        TM7 --> TM8
    end

    subgraph ProcessMode["🔒 Process Isolation"]
        direction TB
        PM1["mp.Pipe()"]
        PM2["mp.Process(target=_run_subagent_worker)"]
        PM3["Child Process"]
        PM4["_run_agent_sync()"]
        PM5["Serialize result via Pipe"]
        PM6["Parent receives result"]
        PM7["SIGTERM on timeout"]

        PM1 --> PM2
        PM2 --> PM3
        PM3 --> PM4
        PM4 --> PM5
        PM5 --> PM6
        PM6 -->|Timeout| PM7
    end

    subgraph ChildAgent["👶 Child Agent (Depth+1)"]
        direction TB
        CA1["WispAgentCore"]
        CA2["_subagent_depth = parent.depth + 1"]
        CA3["_subagent_branch_count = parent.branch + 1"]
        CA4["Can spawn its own subagents?"]
        CA5["Yes (if depth < 2)"]
        CA6["No (if depth >= 2)"]

        CA1 --> CA2
        CA2 --> CA3
        CA3 --> CA4
        CA4 -->|depth < 2| CA5
        CA4 -->|depth >= 2| CA6
    end

    subgraph AutoResearch["🔍 Auto-Parallel Research"]
        direction TB
        AR1["Detect research keywords"]
        AR2["_research_angles()"]
        AR3["Config-driven angles"]
        AR4["Generic fallback"]
        AR5["spawn_subagents(contracts)"]
        AR6["Synthesize results"]
        AR7["Inject as system context"]

        AR1 --> AR2
        AR2 --> AR3
        AR2 -->|No match| AR4
        AR3 --> AR5
        AR4 --> AR5
        AR5 --> AR6
        AR6 --> AR7
    end

    subgraph AutoDelegation["🔄 Auto-Delegation"]
        direction TB
        AD1["DelegationAnalyzer"]
        AD2["CapabilityMatcher"]
        AD3["Detect mismatch"]
        AD4["Build contracts"]
        AD5["spawn_subagents()"]
        AD6["Inject results"]

        AD1 --> AD3
        AD2 --> AD3
        AD3 --> AD4
        AD4 --> AD5
        AD5 --> AD6
    end

    subgraph Safety["🛡️ Safety Guards"]
        direction TB
        SF1["Timeout: min(requested, 300s)"]
        SF2["Circuit Breaker"]
        SF3["Dangerous Command Block"]
        SF4["Approval Gating"]
        SF5["Plan Mode (read-only)"]
        SF6["Max Iterations: 30"]
        SF7["Max Reflections: 3"]
    end

    %% Connections
    U1 --> MA2
    MA4 -->|spawn_subagent| SS1
    SS6 --> OR1
    OR6 --> TM1
    OR7 --> PM2
    TM6 --> CA1
    PM3 --> CA1
    CA5 -->|spawn_subagent| SS1
    MA6 --> AR1
    MA7 --> AD1
    MA4 --> SF2
    MA4 --> SF3
    MA4 --> SF4
    MA4 --> SF5
    MA2 --> SF6
    MA2 --> SF7
    OR10 -->|Return| MA4

    style MainAgent fill:#e1f5fe
    style Orchestrator fill:#fff3e0
    style ThreadMode fill:#e8f5e9
    style ProcessMode fill:#ffebee
    style ChildAgent fill:#f3e5f5
    style Safety fill:#fff8e1
```

## Legend

| Component | Color | Description |
|-----------|-------|-------------|
| 🔮 Main Agent | Blue | `WispAgentCore` — primary agent loop |
| 🎛️ Orchestrator | Orange | `SubagentOrchestrator` — manages subagent lifecycle |
| 🧵 Thread Mode | Green | Default isolation, fast, shared memory |
| 🔒 Process Mode | Red | Sandboxed, truly killable, pipe IPC |
| 👶 Child Agent | Purple | Subagent with depth+1, can spawn if depth < 2 |
| 🛡️ Safety | Yellow | Guards against runaway execution |

## Key Flows

### 1. Single Subagent Spawn
```
User Prompt → _arun() → tool_calls → spawn_subagent
    → Depth Check (depth < 2?) → Branch Check (branch < 3?)
    → Build Contract → Cache Check → Orchestrator.run()
    → Thread/Process → _run_agent_sync() → run_task()
    → Return output to parent
```

### 2. Parallel Subagents (Research)
```
User Prompt → _auto_parallel_research()
    → Detect keywords → _research_angles()
    → Build 2-4 contracts → spawn_subagents()
    → Parallel execution → Synthesize → Inject context
```

### 3. Auto-Delegation
```
User Prompt → _check_delegation()
    → DelegationAnalyzer + CapabilityMatcher
    → Detect mismatch → Build contracts
    → spawn_subagents() → Inject results
```

### 4. Nested Subagent (Depth 2)
```
Main Agent (depth=0)
    → Spawns Worker (depth=1)
        → Worker spawns Task Agent (depth=2)
            → Task Agent tries to spawn → BLOCKED (depth >= 2)
```

## Configuration

```json
{
  "max_subagent_depth": 2,
  "max_subagent_branching": 3,
  "max_subagent_timeout": 300,
  "research_angles": {
    "docker": ["Containers", "Orchestration", "Security"],
    "kubernetes": ["Architecture", "Operators", "Networking"]
  }
}
```
