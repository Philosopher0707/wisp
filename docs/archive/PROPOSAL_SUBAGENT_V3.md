# Proposal: Unified Subagent Architecture v3

## Status: Draft — Ready for Review

---

## 1. Problem Statement

Wisp currently has **three overlapping subagent systems** that confuse users and duplicate maintenance effort:

| System | File | Model | Isolation | Used By |
|--------|------|-------|-----------|---------|
| Legacy Subagent | `wisp/subagent.py` | Threaded, sync | Shared workspace | `spawn_subagent` tool |
| Parallel Runner | `wisp/subagent_runner.py` | Async, `asyncio` | Git worktree (optional) | `spawn_subagents()` API |
| Swarm Orchestrator | `wisp/multi_agent/orchestrator.py` | Async, `asyncio` | Shared workspace + file locks | `/swarm` CLI command |

### 1.1 Critical Issues

1. **Duplicated agent loop logic** — `wisp/subagent.py::_run_child()` reimplements tool execution, message handling, and iteration counting that already exists in `WispAgentCore`.
2. **Incompatible result types** — Three different `SubagentResult` dataclasses with overlapping but different fields.
3. **No progress visibility** — Legacy subagents run in a black-box thread. The parent has no idea if the subagent is thinking, calling tools, or stuck.
4. **System prompt ignored** — `subagent_runner.py` built custom system prompts but never passed them to `run_task()` (fixed in this session, but the architecture still encourages this class of bug).
5. **No structured output** — Subagents return raw text. There's no JSON schema validation, no retry on malformed output, no type safety.
6. **Fire-and-forget semantics** — Once spawned, a subagent cannot be paused, steered, or cancelled gracefully (only a timeout kills it).
7. **Workspace races** — Default shared workspace means two subagents can edit the same file simultaneously with no coordination.

---

## 2. Design Principles

| Principle | Rationale |
|-----------|-----------|
| **One unified API** | A single `SubagentOrchestrator` replaces all three systems. |
| **Use the full agent core** | Subagents run `_arun()` or `run_task()`, not a reimplemented loop. |
| **Event-driven observability** | Parents receive `OrchestratorEvent` streams from subagents in real-time. |
| **Structured contracts** | JSON schema validation, type-safe outputs, automatic retry on parse failure. |
| **Composable patterns** | Map-reduce, voting, sequential chains are first-class primitives. |
| **Isolation by default** | Git worktree isolation is the default; shared workspace is opt-in. |
| **Resource governance** | Token budgets, memory limits, and timeouts are enforced, not advisory. |

---

## 3. Proposed Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SubagentOrchestrator                           │
│  (unified entry point — replaces subagent.py, subagent_runner.py)    │
├─────────────────────────────────────────────────────────────────────┤
│  run(contract) → SubagentResult                                       │
│  run_parallel(contracts, max_concurrent=4) → list[SubagentResult]   │
│  run_map_reduce(task, items, mapper, reducer) → SubagentResult      │
│  run_vote(task, agents, consensus_threshold=0.6) → SubagentResult   │
│  run_chain(tasks, pass_context=True) → SubagentResult               │
└─────────────────────────────────────────────────────────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
        ┌──────────┐        ┌──────────┐          ┌──────────┐
        │  Single  │        │ Parallel │          │  Swarm   │
        │  Agent   │        │  Agents  │          │  Mode    │
        └──────────┘        └──────────┘          └──────────┘
              │                     │                     │
              └─────────────────────┴─────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
              ┌──────────┐                  ┌──────────┐
              │WispAgent   │                  │WispAgent │
              │.run_task()│                  │._arun() │
              │(simplified)│                  │(full)   │
              └──────────┘                  └──────────┘
                    │                               │
                    └───────────────┬───────────────┘
                                    ▼
                          ┌─────────────────┐
                          │  Event Stream   │
                          │  (progress)     │
                          └─────────────────┘
```

### 3.1 Unified Contract

```python
@dataclass
class SubagentContract:
    """Single source of truth for all subagent invocations."""

    # ── Identity ──
    name: str = "subagent"           # Human-readable identifier
    role: str = "generalist"         # Maps to ROLE_CONFIGS in roles.py

    # ── Task ──
    task: str = ""                   # The instruction (was "prompt" / "description")
    system_prompt: Optional[str] = None  # Override default role prompt

    # ── Tools & Capabilities ──
    tools: list[str] = field(default_factory=lambda: ["all"])
    allowed_skills: list[str] = field(default_factory=list)

    # ── Budgets ──
    max_iterations: int = 15
    timeout_seconds: float = 120.0
    max_tokens: Optional[int] = None   # Hard token budget (enforced by trim)
    max_output_chars: int = 8000

    # ── Output ──
    output_format: str = "text"      # text | json | markdown | report
    output_schema: Optional[dict] = None  # JSON schema for validation
    auto_retry_parse: bool = True    # Retry once if output_schema fails

    # ── Environment ──
    model: Optional[str] = None
    workspace: Optional[str] = None
    worktree_isolated: bool = True   # DEFAULT: isolated git worktree
    auto_approve: bool = True

    # ── Observability ──
    progress_callback: Optional[Callable[[OrchestratorEvent], None]] = None
```

### 3.2 Unified Result

`wisp/multi_agent/task.py::SubagentResult` is already designed for this. We deprecate the duplicate types in `subagent.py` and `subagent_runner.py`.

```python
@dataclass
class SubagentResult:
    task_id: str = ""
    success: bool = False
    output: str = ""
    error: Optional[str] = None
    files_changed: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    iterations_used: int = 0
    retry_count: int = 0
    timed_out: bool = False
    hit_iteration_limit: bool = False
    messages: list[dict] = field(default_factory=list)  # Audit trail
    tool_calls: list[dict] = field(default_factory=list)
    validated_output: Optional[Any] = None  # Parsed JSON if schema matched
```

---

## 4. API Design

### 4.1 Single Subagent

```python
from wisp.multi_agent import SubagentOrchestrator, SubagentContract

orch = SubagentOrchestrator(parent_agent=my_agent)

result = await orch.run(SubagentContract(
    name="security-audit",
    role="security-auditor",
    task="Audit src/auth.py for injection vulnerabilities",
    output_format="json",
    output_schema={
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "line": {"type": "integer"},
                        "severity": {"enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"]},
                        "description": {"type": "string"},
                    },
                    "required": ["file", "line", "severity", "description"],
                },
            },
            "summary": {"type": "string"},
        },
        "required": ["findings", "summary"],
    },
    worktree_isolated=True,
))

if result.success and result.validated_output:
    for finding in result.validated_output["findings"]:
        print(f"{finding['severity']}: {finding['file']}:{finding['line']}")
```

### 4.2 Parallel (Map)

```python
contracts = [
    SubagentContract(name=f"test-{f}", role="test-writer", task=f"Write tests for {f}")
    for f in ["src/auth.py", "src/api.py", "src/db.py"]
]

results = await orch.run_parallel(contracts, max_concurrent=3)
```

### 4.3 Map-Reduce

```python
# Split a large codebase review across N subagents, then synthesize
result = await orch.run_map_reduce(
    task="Review the codebase for performance issues",
    items=["src/auth.py", "src/api.py", "src/db.py", "src/cache.py"],
    mapper=lambda item: SubagentContract(
        name=f"review-{item}", role="code-reviewer", task=f"Review {item}"
    ),
    reducer="Synthesize the individual reviews into a prioritized action plan.",
)
```

### 4.4 Voting (Consensus)

```python
# Ask 3 independent subagents the same question; take majority answer
result = await orch.run_vote(
    task="Is this function vulnerable to SQL injection?\n\n```python\ndef query(user_input):\n    return f'SELECT * FROM users WHERE name = \"{user_input}\"'\n```",
    agents=[
        SubagentContract(name="sec-1", role="security-auditor"),
        SubagentContract(name="sec-2", role="security-auditor"),
        SubagentContract(name="sec-3", role="security-auditor"),
    ],
    consensus_threshold=0.6,  # At least 2/3 must agree
)
```

### 4.5 Chain (Sequential)

```python
# Step 1 writes code, step 2 reviews it, step 3 fixes issues
result = await orch.run_chain([
    SubagentContract(name="writer", role="coder", task="Implement JWT auth"),
    SubagentContract(name="reviewer", role="code-reviewer", task="Review the code"),
    SubagentContract(name="fixer", role="coder", task="Fix the issues raised"),
], pass_context=True)  # Each step sees previous steps' outputs
```

---

## 5. Implementation Plan

### Phase 1: Foundation (1–2 days)
- [ ] Move `SubagentContract` and `SubagentResult` fully into `wisp/multi_agent/task.py`
- [ ] Deprecate duplicate types in `wisp/subagent.py` and `wisp/subagent_runner.py` (keep aliases for backward compat)
- [ ] Create `wisp/multi_agent/orchestrator.py::SubagentOrchestrator` class
- [ ] Implement `run()` and `run_parallel()` using existing `SwarmOrchestrator` primitives

### Phase 2: Core Integration (2–3 days)
- [ ] Make `spawn_subagent` tool delegate to `SubagentOrchestrator.run()` instead of legacy `SubagentRunner`
- [ ] Wire `progress_callback` through to `OrchestratorEvent` emissions
- [ ] Add `output_schema` validation with `jsonschema` (optional dependency)
- [ ] Add `auto_retry_parse`: if schema fails, inject error into subagent context and retry once

### Phase 3: Composable Patterns (2–3 days)
- [ ] Implement `run_map_reduce()`
- [ ] Implement `run_vote()`
- [ ] Implement `run_chain()`
- [ ] Add tests for all patterns

### Phase 4: Resource Governance (2–3 days)
- [ ] Enforce `max_tokens` via `_trim_context_if_needed()` before each turn
- [ ] Track token usage across parallel subagents (global budget)
- [ ] Add memory limits (RSS monitoring via `psutil`, optional)
- [ ] Graceful degradation: if budget exceeded, return partial results

### Phase 5: Cleanup & Migration (1 day)
- [ ] Mark `wisp/subagent.py` and `wisp/subagent_runner.py` as deprecated with `warnings.warn`
- [ ] Update all internal call sites to use `SubagentOrchestrator`
- [ ] Update documentation and examples
- [ ] Remove in v2.0

---

## 6. Event Streaming Protocol

Subagents emit events that the parent can observe in real-time:

```python
class OrchestratorEvent:
    task_id: str
    event_type: str  # TASK_STARTED | TASK_PROGRESS | TASK_COMPLETED | TASK_FAILED
    payload: dict
```

**Example event flow:**

```
TASK_STARTED    {name: "security-audit", role: "security-auditor"}
TASK_PROGRESS   {iteration: 1, tool: "read_file", target: "src/auth.py"}
TASK_PROGRESS   {iteration: 2, tool: "search_symbols", target: "execute_query"}
TASK_COMPLETED  {output: "...", files_changed: [], duration_ms: 4500}
```

The CLI `/swarm` command already consumes these via `progress_callback`. The `spawn_subagent` tool should optionally stream them to the parent agent's transport layer.

---

## 7. Backward Compatibility

| Old API | New API | Compatibility |
|---------|---------|---------------|
| `SubagentRunner(parent).spawn(contract)` | `SubagentOrchestrator(parent).run(contract)` | Alias + deprecation warning |
| `SubagentRunner(config, workspace).run_parallel(specs)` | `SubagentOrchestrator(parent).run_parallel(contracts)` | Alias + deprecation warning |
| `WispAgentCore.spawn_subagents(specs)` | `SubagentOrchestrator(self).run_parallel(contracts)` | Redirect internally |
| `spawn_subagent` tool | Same tool name, delegates to new orchestrator | Transparent |

---

## 8. Open Questions

1. **Should subagents share the parent's LSP manager?** Sharing gives better code intelligence but risks state corruption. Isolation is safer but slower (cold LSP start per subagent).
2. **How do we handle subagent file edits in worktrees?** Currently the parent can't see worktree edits until they're merged. Should we auto-merge on success, or leave that to the parent?
3. **Should `run_map_reduce` support recursive splitting?** If a mapper's output is too large, should it auto-spawn another reduce layer?
4. **Token budget: per-subagent or global?** A global budget prevents runaway costs but is harder to reason about. Per-subagent is simpler but allows N×budget total spend.

---

## 9. Acceptance Criteria

- [ ] All 27 `test_subagent.py` tests pass with new orchestrator
- [ ] All `/swarm` CLI tests pass
- [ ] New patterns (map-reduce, vote, chain) have ≥90% test coverage
- [ ] No regressions in full test suite (currently 899 tests)
- [ ] Documentation updated with examples for each pattern
- [ ] Deprecation warnings emitted for old APIs
