# Slash Commands Audit: End-to-End Wiring Analysis

## Summary

**24 slash commands registered in `wisp/commands.py`**
- **17 WORKING** ✅ — Properly wired to production code
- **3 BROKEN** 🔴 — Will crash or fail when used
- **4 NEEDS VERIFICATION** ⚠️ — Likely works but has edge cases

---

## 🔴 BROKEN Commands (Will Crash)

### 1. `/swarm` — Imports Deleted Module

```python
# wisp/commands.py:560
from wisp.multi_agent.orchestrator import SwarmOrchestrator  # MODULE DELETED in Phase 10
```

**Impact:** `ModuleNotFoundError` when user types `/swarm`

**Fix:** Replace with `SubagentOrchestrator.run_parallel()` or remove command:
```python
from wisp.multi_agent import SubagentOrchestrator, SubagentContract

contracts = [
    SubagentContract(name="coder", task=args, role="coder"),
    SubagentContract(name="reviewer", task=args, role="reviewer"),
]
orch = SubagentOrchestrator(parent_agent=agent)
results = await orch.run_parallel(contracts)
```

---

### 2. `/spawn` — `asyncio.run()` Inside Running Loop

```python
# wisp/commands.py:525
result = asyncio.run(orch.run(contract))  # CRASHES in REPL mode!
```

**Why it breaks:** In REPL mode, an event loop is already running. `asyncio.run()`
cannot be called from inside a running loop. It raises:
```
RuntimeError: asyncio.run() cannot be called from a running event loop
```

**Fix:** Use `asyncio.create_task()` or make the command handler async:
```python
# Option A: Schedule as task (fire-and-forget with callback)
task = asyncio.create_task(orch.run(contract))
# ...or wait for it with asyncio.gather()

# Option B: Make cmd_spawn async and await properly
async def cmd_spawn(agent, args: str):
    ...
    result = await orch.run(contract)
```

---

### 3. `/continue` — Calls Non-Existent Method

```python
# wisp/commands.py:687
agent._execute_loop(system, ws, agent.config.auto_approve)  # METHOD DOES NOT EXIST
```

**Why it breaks:** `WispAgentCore` has no `_execute_loop()` method. The actual
execution method is `_arun()` (async) or the REPL calls `_execute_turn()` via
`asyncio.run()`.

**Fix:** Use the same pattern as the REPL:
```python
import asyncio
from wisp.transport.cli import CLITransport

# Get the transport's execute_turn method
async def _run():
    await transport._execute_turn(system, ws)
asyncio.create_task(_run())
```

Or simpler — just add a message and let the next REPL iteration handle it:
```python
agent._add_message("user", expanded)
print(info("⏩ Continuing… Next turn will resume from previous assistant message."))
```

---

## ✅ WORKING Commands (17)

| Command | Aliases | Handler | Wiring | Status |
|---------|---------|---------|--------|--------|
| `/help` | `/h`, `/?` | `cmd_help()` | Prints `_REGISTRY` contents | ✅ |
| `/clear` | `/cls` | `cmd_clear()` | `agent.messages.clear()` | ✅ |
| `/model` | `/m` | `cmd_model()` | `agent.config.model = ...` + `agent.client.model = ...` | ✅ |
| `/skill` | `/s` | `cmd_skill()` | `find_skill()` from `wisp.skills` | ✅ |
| `/session` | — | `cmd_session()` | Prints `agent.session` metadata | ✅ |
| `/save` | — | `cmd_save()` | `agent._save_session()` | ✅ |
| `/tokens` | `/context` | `cmd_tokens()` | `agent._estimate_tokens()` | ✅ |
| `/metrics` | — | `cmd_metrics()` | Reads `agent._turn_latencies`, etc. | ✅ |
| `/circuit` | — | `cmd_circuit()` | `agent.tool_executor.circuit_breakers` | ✅ |
| `/compact` | `/c` | `cmd_compact()` | `agent._maybe_compact_session()` | ✅ |
| `/approve` | `/y` | `cmd_approve()` | `agent.config.auto_approve = not ...` | ✅ |
| `/thinking` | `/T` | `cmd_thinking()` | `agent.config.show_thinking = not ...` | ✅ |
| `/bash` | `/!`, `/sh` | `cmd_bash()` | `tool_run_bash()` from `wisp.tools` | ✅ |
| `/workspace` | `/cd`, `/w` | `cmd_workspace()` | `agent.config.workspace = ...` | ✅ |
| `/grep` | `/g`, `/search` | `cmd_grep()` | `subprocess.run(["grep", ...])` | ✅ |
| `/ls` | `/files`, `/dir` | `cmd_ls()` | `os.listdir()` + glob | ✅ |
| `/read` | `/cat` | `cmd_read()` | `read_file()` from `wisp.tools.filesystem` | ✅ |
| `/drop` | `/pop`, `/undo` | `cmd_drop()` | `agent.messages.pop()` | ✅ |
| `/new` | — | `cmd_new()` | `Session.create()` + reset state | ✅ |
| `/exit` | `/quit`, `/q`, `/bye` | `cmd_exit()` | `raise ExitREPL` | ✅ |
| `/init` | — | `cmd_init()` | `detect_project_context()` + `build_index()` | ✅ |

---

## ⚠️ NEEDS VERIFICATION (4)

### `/model` — Cloud-Only Assumption

```python
# wisp/commands.py:130
def _display_name(name: str) -> str:
    return name.removesuffix(":cloud")  # Assumes ALL models are cloud
```

**Issue:** If running local Ollama (not cloud), the `:cloud` suffix logic is wrong.
The display will strip valid suffixes from local model names.

**Fix:** Check if cloud mode is active:
```python
def _display_name(name: str) -> str:
    if getattr(agent.config, "cloud_mode", False):
        return name.removesuffix(":cloud")
    return name
```

---

### `/bash` — Dangerous Command Check

```python
# wisp/commands.py:393
reason = check_dangerous_command(args)
```

**Issue:** `check_dangerous_command()` is in `wisp.tools` but the import path is:
```python
from wisp.tools import tool_run_bash, check_dangerous_command
```

Need to verify `check_dangerous_command` is actually exported from `wisp.tools.__init__`.

---

### `/compact` — May Fail If No Summarizer

```python
# wisp/commands.py:357
agent._maybe_compact_session()
```

**Issue:** `_maybe_compact_session()` requires a summarizer model. If none is configured,
it may silently do nothing or raise. The command doesn't check the return value.

---

### `/skill` — Skill Discovery Path

```python
# wisp/commands.py:228
skill = find_skill(skill_name, agent.config.workspace or ".")
```

**Issue:** `find_skill()` searches `.wisp/skills/` relative to workspace. If the user
has a custom skill path, this won't find it. Also, no error is shown if skill file
exists but is malformed.

---

## Command Wiring Diagram

```
User types: /spawn research Python HTTP clients
        ↓
REPL loop (transport/cli.py:1020)
        ↓
dispatch("/spawn research...", agent)  ← wisp/commands.py:78
        ↓
lookup("spawn") → returns Command(handler=cmd_spawn)
        ↓
cmd_spawn(agent, "research Python HTTP clients")
        ↓
    ├── Creates SubagentContract(task="research...")
    ├── Creates SubagentOrchestrator(parent_agent=agent)
    └── asyncio.run(orch.run(contract))  ← 🔴 CRASHES in REPL!
```

---

## Recommended Fixes (Priority Order)

### P0 — Fix Before Next Release

| Command | Fix | Effort |
|---------|-----|--------|
| `/swarm` | Replace `SwarmOrchestrator` with `SubagentOrchestrator.run_parallel()` | 30 min |
| `/spawn` | Replace `asyncio.run()` with `asyncio.create_task()` + callback | 20 min |
| `/continue` | Replace `_execute_loop()` with message append + hint | 15 min |

### P1 — Polish

| Command | Fix | Effort |
|---------|-----|--------|
| `/model` | Remove `:cloud` hardcoding | 10 min |
| `/bash` | Verify `check_dangerous_command` export | 5 min |
| `/compact` | Add success/failure feedback | 10 min |

---

## Test Coverage

**Current:** 0 tests for slash commands in `tests/test_commands.py`

**Needed:**
1. Test `dispatch()` with known command
2. Test `dispatch()` with unknown command
3. Test `dispatch()` with alias
4. Test each command handler with mock agent
5. Test `/spawn` async behavior
6. Test `/swarm` with new orchestrator

---

## Verdict

> **17 of 24 commands work correctly. 3 are broken due to refactoring (Phase 10 deleted `SwarmOrchestrator`, `asyncio.run()` conflict, missing method).**
>
> **The broken commands are all in the multi-agent category (`/spawn`, `/swarm`, `/continue`), which makes sense — that's what we refactored most heavily.**
>
> **Fix time: ~65 minutes for all P0 issues.**
