# Wisp Codebase Architecture Analysis

**Skill:** improve-codebase-architecture  
**Date:** 2025-01-20  
**Scope:** Deep structural analysis with concrete refactoring recommendations

---

## Executive Summary

Wisp is a ~34K-line Python codebase with a **layered architecture** that has grown organically. It has clear separation between core engine, transport, and tools — but suffers from **god classes**, **circular dependencies**, **mixed concerns**, and **inconsistent abstraction layers**. The codebase is functional but has significant technical debt that will slow future development.

**Key Metrics:**
- 79 Python modules, ~34K lines
- 20 modules have zero test coverage (25%)
- 3 circular dependency pairs
- 8 files exceed 1000 lines ("god classes")
- `wisp.core.agent` imports 30 other modules

---

## 1. Architecture Layers

```
┌─────────────────────────────────────────┐
│  Transport Layer (CLI, Server, TUI)    │  ← I/O, user interaction
├─────────────────────────────────────────┤
│  Agent Layer (WispAgent, WispAgentCore)│  ← Orchestration, state
├─────────────────────────────────────────┤
│  Tool Layer (tools.py, MCP, LSP)     │  ← External interactions
├─────────────────────────────────────────┤
│  Context Layer (skills, memory, git)   │  ← Knowledge injection
├─────────────────────────────────────────┤
│  Infrastructure (config, session, etc) │  ← Persistence, config
└─────────────────────────────────────────┘
```

**Verdict:** Layer boundaries exist but are **leaky**. The core agent directly imports transport concerns, and tools mix business logic with I/O.

---

## 2. Critical Issues Found

### 2.1 God Classes — Single Responsibility Violations

| File | Lines | Classes | Functions | Problem |
|------|-------|---------|-----------|---------|
| `wisp/multi_agent/orchestrator.py` | 2,946 | 3 | 47 | Swarm + subagent + worker logic all in one file |
| `wisp/tools.py` | 2,069 | 1 | 31 | File ops, git, web, LSP, bash ALL in one module |
| `wisp/core/agent.py` | 1,844 | 1 | 36 | System prompt building, tool execution, session mgmt, compaction |
| `wisp/repo_map.py` | 1,840 | 4 | 48 | Indexing, formatting, dependency analysis, caching |
| `wisp/transport/cli.py` | 1,462 | 1 | 28 | Input handling, rendering, signal handling, transport logic |
| `wisp/server.py` | 2,075 | 3 | 22 | HTTP server, WebSocket, ACP, streaming, session mgmt |

**Impact:** These files are hard to test, hard to reason about, and change frequently (high churn risk).

### 2.2 Circular Dependencies

```
wisp.commands ↔ wisp.transport.cli
wisp.multi_agent.orchestrator ↔ wisp.multi_agent.orchestrator (self-import!)
```

**Root cause:** `commands.py` imports `cli.py` for `ExitREPL`, and `cli.py` imports `commands.py` for `dispatch`. The orchestrator self-imports via `from wisp.agent import WispAgent` inside methods.

**Impact:** Prevents clean module extraction, complicates testing, creates import-order fragility.

### 2.3 Mixed Concerns — I/O in Core Modules

While `wisp.core.agent` claims "zero I/O", several core-adjacent modules violate this:

| Module | I/O Found | Should Be |
|--------|-----------|-----------|
| `wisp/multi_agent/orchestrator.py` | `print()` for tokens | Return values, logging |
| `wisp/commands.py` | Direct `print()` to stdout | Return structured results |
| `wisp/transport/cli.py` | Signal handlers, stdin reads | Transport-only (OK here) |

### 2.4 Tight Coupling — The Agent Knows Too Much

`wisp.core.agent` imports **30 modules**:
- Skills, memory, git, project context, code index, tree-sitter, MCP, LSP
- It builds system prompts by directly calling ALL of these
- Any change in any context module requires understanding the agent

**This is the architectural bottleneck.** The agent is a "god object" that coordinates everything.

### 2.5 Test Coverage Gaps

20 modules (25%) have **zero tests**:

| Module | Risk Level | Why Untested |
|--------|-----------|--------------|
| `wisp.server` | 🔴 HIGH | 2,075 lines, HTTP/WebSocket/ACP — complex mocking |
| `wisp.hooks` | 🔴 HIGH | 34K lines of hook logic — no tests at all |
| `wisp.mcp` | 🟡 MEDIUM | 1,081 lines, MCP server integration |
| `wisp.sandbox` | 🟡 MEDIUM | Security-critical, no tests |
| `wisp.background_agent` | 🟡 MEDIUM | Async background processing |
| `wisp.arena` | 🟢 LOW | Experimental feature |
| `wisp.completion` | 🟢 LOW | Completion engine |
| `wisp.lsp.*` | 🟡 MEDIUM | LSP client/manager |
| `wisp.plugins.*` | 🟢 LOW | Plugin system |
| `wisp.semantic_index` | 🟡 MEDIUM | Semantic search |

---

## 3. Deepening Opportunities

### 3.1 Extract System Prompt Builder

**Current:** `WispAgentCore._build_system_prompt()` is ~200 lines that imports skills, ontology, project context, code index, memory, git, planner, repo map.

**Recommended:** Create `wisp.prompt_builder.SystemPromptBuilder`:

```python
class SystemPromptBuilder:
    def __init__(self, workspace, config):
        self.workspace = workspace
        self.config = config
        self._cache = {}
    
    def build(self, skill_name=None, query=None) -> str:
        # Delegates to specialized builders
        parts = [
            self._base_prompt(),
            self._workspace_context(),
            self._skills_block(),
            self._ontology_context(),
            self._project_context(),
            self._code_index(),
            self._memory(),
            self._git_context(),
            self._plan_context(),
            self._repo_map(),
            self._skill_mode(skill_name),  # MANDATORY block last
        ]
        return "\n\n".join(filter(None, parts))
```

**Benefits:**
- Agent drops from 30 imports to ~5
- Each context builder independently testable
- Cache strategy per-builder (not one giant cache)
- Skills can inject their own builders

### 3.2 Split tools.py into Domain Modules

**Current:** 2,069 lines, 31 functions — file ops, git, web, bash, LSP, memory

**Recommended:**

```
wisp/tools/
  __init__.py          # Registry, execute_tool()
  filesystem.py        # read_file, write_file, edit_file, list_files
  git.py              # git_status, git_diff, git_commit, etc.
  web.py              # web_fetch, web_search
  bash.py             # run_bash
  lsp.py              # lsp_diagnostics, lsp_definition, etc.
  memory.py           # remember, recall
  search.py           # search_symbols, search_codebase
```

**Benefits:**
- Each domain independently testable
- Clear ownership (git bugs → git.py)
- Lazy loading per domain
- Plugin architecture: new tool domains = new files

### 3.3 Break Up orchestrator.py

**Current:** 2,946 lines with SwarmOrchestrator + SubagentOrchestrator + worker process

**Recommended:**

```
wisp/multi_agent/
  orchestrator.py      # Abstract base + shared utilities
  swarm.py             # SwarmOrchestrator only
  subagent.py          # SubagentOrchestrator only
  worker.py            # _run_subagent_worker only
  pool.py              # Process/thread pool management
```

**Benefits:**
- Swarm and subagent can evolve independently
- Worker code isolated for security review
- Pool management extractable for reuse

### 3.4 Introduce Event Bus for Decoupling

**Current:** Agent directly calls memory, git, planner, etc.

**Recommended:** Use the existing `MessageBus` (from `wisp/multi_agent/bus.py`) as a proper event bus:

```python
# Instead of:
memory_block = format_memory_block(ws)
if memory_block:
    system += f"\n\n{memory_block}"

# Use events:
bus.emit(ContextRequest("memory", workspace=ws))
memory_block = bus.wait_for("memory", timeout=1.0)
```

**Benefits:**
- Context builders become async and parallel
- Agent doesn't know about individual modules
- New context sources = new event handlers (no agent changes)

### 3.5 Fix Circular Dependencies

**commands.py ↔ cli.py:**
- Extract `ExitREPL` exception to `wisp/exceptions.py`
- Have both modules import from there
- `cli.py` keeps `dispatch()` import, but `commands.py` drops `cli.py` import

**orchestrator.py self-import:**
- Move `WispAgent` import to runtime (already lazy in some places)
- Or extract shared types to `wisp/multi_agent/types.py`

### 3.6 Add Missing Tests for Critical Modules

Priority order:

1. **`wisp/server.py`** — HTTP endpoints, WebSocket, ACP protocol
2. **`wisp/hooks.py`** — Hook execution, error handling
3. **`wisp/mcp.py`** — MCP server lifecycle, tool registration
4. **`wisp/sandbox.py`** — Security boundaries, resource limits
5. **`wisp/semantic_index.py`** — Embedding, search, relevance

---

## 4. Consolidation Opportunities

### 4.1 Merge Redundant Index Systems

**Current:** Three separate indexing systems:
- `wisp/code_index.py` — regex-based symbol index
- `wisp/tree_sitter_index.py` — Tree-sitter AST index
- `wisp/semantic_index.py` — embedding-based semantic index
- `wisp/repo_map.py` — structural dependency map

**Problem:** They don't share caches, don't know about each other, and the agent queries them separately.

**Recommended:** Unified `wisp.index.CodebaseIndex`:

```python
class CodebaseIndex:
    def __init__(self, workspace):
        self.symbol_index = SymbolIndex()      # from code_index
        self.ast_index = ASTIndex()            # from tree_sitter_index
        self.semantic_index = SemanticIndex()  # from semantic_index
        self.dependency_map = DependencyMap()   # from repo_map
    
    def search(self, query, strategy="hybrid"):
        # Combines all indexes, deduplicates, ranks
        pass
```

### 4.2 Unify Memory Systems

**Current:** Three memory systems:
- `wisp/memory.py` — session memory block formatting
- `wisp/agent_memory.py` — cross-session summaries
- `wisp/multi_agent/bus.py` — event history (MessageBus)

**Recommended:** Single `wisp.memory.MemoryManager` with pluggable backends.

### 4.3 Merge Transport Implementations

**Current:**
- `wisp/transport/cli.py` — 1,462 lines
- `wisp/transport/server.py` — unknown size
- `wisp/server.py` — 2,075 lines (HTTP server)

**Problem:** `wisp/server.py` and `wisp/transport/server.py` likely overlap.

**Recommended:** Audit and consolidate. The transport layer should be:
- `wisp/transport/base.py` — abstract transport
- `wisp/transport/cli.py` — CLI transport only
- `wisp/transport/http.py` — HTTP/WebSocket transport

---

## 5. Testability Improvements

### 5.1 Extract Interfaces for External Dependencies

**Current:** Agent directly instantiates `OllamaClient`, `MCPManager`, etc.

**Recommended:** Protocol-based dependency injection:

```python
from typing import Protocol

class LLMClient(Protocol):
    def chat(self, messages, tools=None): ...
    def stream(self, messages): ...

class ToolExecutor(Protocol):
    def execute(self, name, args, workspace): ...

class WispAgentCore:
    def __init__(self, config, llm: LLMClient, tools: ToolExecutor):
        ...
```

**Benefits:**
- Mock LLM for tests (no Ollama needed)
- Mock tools for tests (no filesystem changes)
- Swap providers without changing agent

### 5.2 Make System Prompt Building Pure

**Current:** `_build_system_prompt()` reads files, queries git, builds indexes.

**Recommended:** Pass all context as parameters:

```python
def build_system_prompt(
    base_prompt: str,
    workspace_info: WorkspaceInfo,
    skills: list[Skill],
    code_index: CodeIndex,
    memory: MemoryBlock,
    git_context: GitContext,
    active_skill: Optional[str] = None,
) -> str:
    # Pure function — no I/O, no side effects
    ...
```

**Benefits:**
- 100% testable without filesystem
- Deterministic (same inputs → same output)
- Cacheable at any granularity

---

## 6. Concrete Refactoring Plan

### Phase 1: Low-Risk, High-Impact (Week 1)
1. **Extract `ExitREPL`** to `wisp/exceptions.py` — fixes circular dep
2. **Split `tools.py`** into `wisp/tools/` package
3. **Add tests** for `wisp/server.py` endpoints

### Phase 2: Structural (Week 2-3)
4. **Extract `SystemPromptBuilder`** from `wisp/core/agent.py`
5. **Break up `orchestrator.py`** into swarm/subagent/worker
6. **Unify index systems** behind `CodebaseIndex`

### Phase 3: Architectural (Week 4)
7. **Introduce event bus** for context building
8. **Add protocol-based DI** for LLM and tools
9. **Consolidate transports**

---

## 7. Files to Touch

| Priority | File | Action |
|----------|------|--------|
| 🔴 P0 | `wisp/tools.py` | Split into package |
| 🔴 P0 | `wisp/core/agent.py` | Extract prompt builder |
| 🔴 P0 | `wisp/multi_agent/orchestrator.py` | Split into 3 files |
| 🟡 P1 | `wisp/commands.py` | Fix circular dep with cli.py |
| 🟡 P1 | `wisp/server.py` | Add tests |
| 🟡 P1 | `wisp/hooks.py` | Add tests |
| 🟢 P2 | `wisp/repo_map.py` | Merge into unified index |
| 🟢 P2 | `wisp/memory.py` | Merge with agent_memory |
| 🟢 P2 | `wisp/transport/` | Consolidate server transports |

---

## 8. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Breaking skill loading | Medium | High | Keep `discover_skills()` API stable |
| Breaking transport | Low | High | CLI transport has good test coverage |
| Breaking subagent | Medium | High | 184 tests cover subagent gaps |
| Performance regression | Low | Medium | Benchmark prompt building before/after |
| Merge conflicts | High | Low | Small PRs, one refactor at a time |

---

## Summary

Wisp has a **solid foundation** with clear layer separation, but it has grown into a codebase where:
- 8 files are "god classes" that do too much
- 25% of modules are untested
- The core agent is a bottleneck (30 imports)
- Circular dependencies prevent clean extraction

**The highest-impact fix:** Extract `SystemPromptBuilder` from `wisp/core/agent.py`. This single refactor would:
- Reduce agent imports from 30 → ~5
- Make every context source independently testable
- Enable parallel context building
- Make the agent core actually "zero I/O" as documented

**Next step:** Start with Phase 1 (tools.py split + ExitREPL extraction) — low risk, immediate testability gains.
