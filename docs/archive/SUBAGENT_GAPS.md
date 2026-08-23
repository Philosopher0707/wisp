# Subagent System Gap Analysis

## Overview
After reviewing the orchestrator, agent core, and composable patterns, here are the
gaps in the current subagent system — ordered by impact (high → low).

---

## 🔴 Critical Gaps

### 1. No Subagent Depth Tracking in Process Isolation
**Location:** `orchestrator.py::_spawn_subagent_process()`  
**Issue:** The `_subagent_depth` check only exists in `WispAgentCore._spawn_subagent()`
(thread path). Process-based subagents can recursively spawn more process subagents,
potentially exhausting system resources.  
**Fix:** Pass `_subagent_depth` in `contract_dict`, increment in worker, enforce max.

### 2. No Result Caching in Orchestrator
**Location:** `orchestrator.py::run()`  
**Issue:** `WispAgentCore._spawn_subagent()` has `_subagent_cache` with TTL, but
`SubagentOrchestrator.run()` has no caching. Parallel runs and composable patterns
(map-reduce, vote) can't deduplicate identical tasks.  
**Fix:** Add LRU cache keyed by contract hash in `SubagentOrchestrator.__init__()`.

### 3. Schema Validation Uses Optional Dependency
**Location:** `orchestrator.py::_validate_output()`  
**Issue:** Tries to import `jsonschema` (optional), falls back silently. Our custom
`schema_validator.py` (no external deps) is used in `_spawn_subagent` but NOT in
`SubagentOrchestrator._validate_output()`.  
**Fix:** Replace `jsonschema` usage with `wisp.multi_agent.schema_validator`.

### 4. Context Files Not Passed to Process Subagents
**Location:** `orchestrator.py::_spawn_subagent_process()`  
**Issue:** `contract_dict` serializes `context_files`, but `_run_subagent_worker`
doesn't inject them into the system prompt. Process subagents lose partitioned context.  
**Fix:** In worker, read `context_files` and prepend to task or system prompt.

---

## 🟠 High-Impact Gaps

### 5. No Partial Result Streaming
**Location:** `orchestrator.py::_spawn_subagent_thread/process()`  
**Issue:** Parent waits for full completion. For 120s timeouts, parent is blind for
2 minutes. No progress visibility.  
**Fix:** Stream `OrchestratorEvent`s back via queue/queue for thread, or temp file
polling for process.

### 6. No Retry in Composable Patterns
**Location:** `orchestrator.py::run_map_reduce(), run_chain()`  
**Issue:**
- `run_map_reduce`: Failed mappers are logged but not retried
- `run_chain`: Stops on first failure with no retry option
- `run_vote`: No tie-breaker when consensus is split  
**Fix:** Add `retry_failed=True` param to map-reduce, `continue_on_error` to chain.

### 7. No Resource Limits on Subagents
**Location:** `orchestrator.py::_spawn_subagent_process()`  
**Issue:** `mp.Process` has no CPU, memory, or FD limits. A runaway subagent can
OOM the host.  
**Fix:** Use `resource` module (Unix) or `psutil` to set `RLIMIT_AS`, `RLIMIT_CPU`.

### 8. Worktree Cleanup Race Condition
**Location:** `orchestrator.py::run() finally block`  
**Issue:** Worktree is deleted immediately after subagent finishes, but process may
still be flushing files. `WISP_KEEP_WORKTREES` exists but is manual.  
**Fix:** Add small delay (0.5s) before cleanup, or check for open file handles.

### 9. No Dynamic Load Balancing
**Location:** `orchestrator.py::run_parallel()`  
**Issue:** `max_concurrent` is static (default 4). Doesn't adapt to system load,
token budget, or queue depth.  
**Fix:** Monitor CPU/memory/token usage and adjust semaphore dynamically.

---

## 🟡 Medium-Impact Gaps

### 10. No Inter-Subagent Communication
**Location:** All composable patterns  
**Issue:** Parallel subagents are isolated. Can't share intermediate findings,
coordinate, or avoid duplicate work.  
**Fix:** Add shared `MessageBus` or `SharedContext` object passed to all subagents
in a parallel run.

### 11. Missing Health Checks / Heartbeat
**Location:** `orchestrator.py::_spawn_subagent_thread()`  
**Issue:** Thread subagents use `asyncio.wait_for` + `asyncio.to_thread`. If the
thread hangs (infinite loop in C extension), timeout won't fire until wall-clock
expires.  
**Fix:** Add heartbeat: subagent must touch a file/socket every N seconds.

### 12. No Subagent Result Persistence
**Location:** `orchestrator.py`  
**Issue:** Results are returned to parent and discarded. No searchable history,
no learning from past runs.  
**Fix:** Persist to SQLite/JSONL with task hash, result, timestamp, success rate.

### 13. Telemetry Not Auto-Aggregated
**Location:** `orchestrator.py::get_telemetry_summary()`  
**Issue:** Method exists but is never called automatically. No alerting on high
failure rates or latency spikes.  
**Fix:** Auto-log summary after each `run_parallel()`, warn if failure rate > 30%.

### 14. Process IPC Uses Temp Files (Not Pipes)
**Location:** `orchestrator.py::_spawn_subagent_process()`  
**Issue:** Result is written to temp JSON file. If disk is full or path is long
(>255 chars on some systems), IPC fails.  
**Fix:** Use `multiprocessing.Pipe` or shared memory for small results.

---

## 🟢 Low-Impact / Nice-to-Have

### 15. No Subagent Output Compression
**Location:** `orchestrator.py::run()`  
**Issue:** Large outputs (e.g., codebase analysis) are truncated at `max_output_chars`.
No summarization before truncation.  
**Fix:** If output > threshold, auto-summarize via LLM or extract key sections.

### 16. Missing Subagent Cost Estimation
**Location:** `orchestrator.py`  
**Issue:** No pre-flight cost estimate. User doesn't know if a task will cost $0.01
or $1.00 before running.  
**Fix:** Estimate tokens from task length + expected tool calls, show preview.

### 17. No Subagent Warm-Up / Pool
**Location:** `orchestrator.py::run()`  
**Issue:** Each subagent creates a fresh `WispAgentCore`, loads model, initializes
MCP/LSP. High latency for short tasks.  
**Fix:** Maintain a pool of warm agent instances, reuse across contracts.

### 18. Role Configs Not Validated at Runtime
**Location:** `orchestrator.py::_default_system_prompt()`  
**Issue:** `ROLE_CONFIGS` is imported but if a contract has an unknown role, it
falls back silently to generic prompt.  
**Fix:** Log warning when role is unknown, suggest closest match.

---

## Summary Table

| # | Gap | Severity | Effort | File |
|---|-----|----------|--------|------|
| 1 | No depth tracking in process | 🔴 Critical | Small | `orchestrator.py` |
| 2 | No result caching | 🔴 Critical | Small | `orchestrator.py` |
| 3 | Schema validation uses jsonschema | 🔴 Critical | Small | `orchestrator.py` |
| 4 | Context files not in process | 🔴 Critical | Small | `orchestrator.py` |
| 5 | No partial streaming | 🟠 High | Medium | `orchestrator.py` |
| 6 | No retry in patterns | 🟠 High | Medium | `orchestrator.py` |
| 7 | No resource limits | 🟠 High | Medium | `orchestrator.py` |
| 8 | Worktree cleanup race | 🟠 High | Small | `orchestrator.py` |
| 9 | No dynamic load balancing | 🟠 High | Medium | `orchestrator.py` |
| 10 | No inter-subagent comms | 🟡 Medium | Large | New module |
| 11 | No heartbeat | 🟡 Medium | Medium | `orchestrator.py` |
| 12 | No result persistence | 🟡 Medium | Medium | New module |
| 13 | Telemetry not auto-aggregated | 🟡 Medium | Small | `orchestrator.py` |
| 14 | IPC uses temp files | 🟡 Medium | Medium | `orchestrator.py` |
| 15 | No output compression | 🟢 Low | Small | `orchestrator.py` |
| 16 | No cost estimation | 🟢 Low | Small | `orchestrator.py` |
| 17 | No agent pool | 🟢 Low | Large | New module |
| 18 | Role configs not validated | 🟢 Low | Small | `orchestrator.py` |

---

## Recommended Priority Order

1. **Fix #1-4** (Critical) — These are correctness bugs that can cause crashes or
   silent failures. Small effort, high impact.
2. **Fix #5-9** (High) — These improve reliability and efficiency. Medium effort.
3. **Fix #10-14** (Medium) — These enable advanced use cases. Larger effort.
4. **Fix #15-18** (Low) — Nice-to-have optimizations. Can be deferred.
