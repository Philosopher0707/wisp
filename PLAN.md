# Wisp Enhancement Plan — 10 Features for Agent Effectiveness

**Date:** 2026-05-03
**Status:** Planning Document
**Scope:** Features to significantly improve the Wisp coding agent's effectiveness

---

## Table of Contents

1. [Semantic Code Search (Embeddings-based)](#1-semantic-code-search)
2. [Auto-Test on Change](#2-auto-test-on-change)
3. [Git-Aware Context](#3-git-aware-context)
4. [Persistent Agent Memory](#4-persistent-agent-memory)
5. [Multi-Model Fallback Chain](#5-multi-model-fallback-chain)
6. [Screenshot / Visual Understanding](#6-screenshot--visual-understanding)
7. [Real-time Health Monitoring](#7-real-time-health-monitoring)
8. [Structured Planning / Task Decomposition](#8-structured-planning)
9. [now 9-better-error-diagnosis)
10. [Collaborative Editing](#10-collaborative-editing)

---

## 1. Semantic Code Search (Embeddings-based)

### Goal
Replace regex-based `search_symbols` with semantic search using vector embeddings. Search by *intent* ("auth validation logic") not just by name ("validate_token").

### Architecture
```
User query → Embed query (384-dim) → Cosine similarity → Rank results
                    ↑
Codebase: Function bodies → Embed each function → FAISS index / simple numpy
```

### New Files

| File | Purpose | Size Estimate |
|------|---------|---------------|
| `wisp/semantic_search.py` | Core embedding + search logic | ~400 lines |
| `wisp/embeddings.py` | Model loading, text chunking, batch encoding | ~300 lines |
| `wisp/code_chunker.py` | Extract meaningful code chunks (functions, classes, docstrings) | ~250 lines |
| `wisp/vector_store.py` | In-memory vector index with persistence | ~200 lines |
| `tests/test_semantic_search.py` | Tests for semantic search | ~150 lines |
| `tests/test_embeddings.py` | Tests for embedding model | ~100 lines |

### Modified Files

| File | Changes |
|------|---------|
| `wisp/tools.py` | Add `semantic_search` tool schema + implementation |
| `wisp/agent.py` | Inject semantic search summary into system prompt |
| `wisp/__main__.py` | Add `wisp index` command to rebuild embeddings index |
| `pyproject.toml` | Add optional deps: `sentence-transformers`, `faiss-cpu` |

### Dependencies
```
sentence-transformers>=3.0  # For all-MiniLM-L6-v2 model
faiss-cpu>=1.8               # For fast similarity search (optional)
numpy>=1.24                  # For vector operations
```

### Data Flow
1. **Index building:** `build_semantic_index(workspace)`
   - Walk source files
   - Extract functions/classes via tree-sitter/AST
   - Chunk into ~512 token segments
   - Embed each chunk → 384-dim vector
   - Store in `.wisp/semantic_index/` (numpy arrays + metadata JSON)

2. **Query:** `semantic_search(query, top_k=10)`
   - Embed query string
   - Cosine similarity against all indexed vectors
   - Return top-k with file path, line number, snippet, similarity score

3. **Persistence:** Index saved to `.wisp/semantic_index/`
   - `vectors.npy` — float32 array (n_chunks × 384)
   - `metadata.json` — list of {file, line, name, kind, text}
   - `config.json` — model version, timestamp

### API Design
```python
# wisp/semantic_search.py

class SemanticIndex:
    def __init__(self, workspace: str, model_name: str = "all-MiniLM-L6-v2"):
        self.workspace = workspace
        self.model = load_model(model_name)
        self.vectors: np.ndarray | None = None
        self.metadata: list[dict] = []

    def build(self) -> None:
        """Scan codebase and build embedding index."""
        chunks = extract_code_chunks(self.workspace)
        texts = [c.text for c in chunks]
        self.vectors = self.model.encode(texts, show_progress_bar=True)
        self.metadata = [c.to_dict() for c in chunks]
        self._save()

    def search(self, query: str, top_k: int = 10) -> list[SearchResult]:
        """Find most semantically similar code chunks."""
        query_vec = self.model.encode([query])
        similarities = cosine_similarity(query_vec, self.vectors)[0]
        top_indices = np.argsort(similarities)[-top_k:][::-1]
        return [
            SearchResult(
                score=float(similarities[i]),
                file=self.metadata[i]["file"],
                line=self.metadata[i]["line"],
                name=self.metadata[i]["name"],
                text=self.metadata[i]["text"][:200],
            )
            for i in top_indices
        ]

    def _save(self) -> None:
        """Persist index to .wisp/semantic_index/"""
        ...

    def _load(self) -> bool:
        """Load persisted index if available and fresh."""
        ...
```

### Tool Schema
```json
{
  "type": "function",
  "function": {
    "name": "semantic_search",
    "description": "Search codebase by meaning/intent using AI embeddings. Finds code related to concepts even if names don't match.",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {"type": "string", "description": "What you're looking for in natural language"},
        "top_k": {"type": "number", "description": "Number of results", "default": 10}
      },
      "required": ["query"]
    }
  }
}
```

### System Prompt Addition
```markdown
## Semantic Search
- Use `semantic_search()` to find code by intent/concept
- Example: semantic_search("how auth tokens are validated") finds validate_token, check_permissions, etc.
- Index covers {N} functions/classes across {M} files
```

### Performance Budget
- **Index build:** ~2-5 min for 10K functions (one-time)
- **Search:** <100ms for 10K vectors
- **Memory:** ~15MB per 1000 functions (384-dim float32)
- **Disk:** ~20MB per 1000 functions (vectors + metadata)

---

## 2. Auto-Test on Change

### Goal
Automatically run relevant tests when files change. Smart test selection based on import graph.

### Architecture
```
File save → Detect changed files → Build import graph → Find affected tests
                ↓                                    ↓
         File watcher (watchdog)              pytest --co + AST analysis
                ↓                                    ↓
         Trigger test run                      Run only matching tests
```

### New Files

| File | Purpose | Size Estimate |
|------|---------|---------------|
| `wisp/test_runner.py` | Smart test discovery, selection, execution | ~400 lines |
| `wisp/import_graph.py` | Build module dependency graph from AST | ~300 lines |
| `wisp/file_watcher.py` | Watchdog-based file change detection | ~200 lines |
| `wisp/test_reporter.py` | Format test results for LLM consumption | ~150 lines |
| `tests/test_test_runner.py` | Tests for test runner | ~100 lines |

### Modified Files

| File | Changes |
|------|---------|
| `wisp/agent.py` | Add `auto_test` flag; run tests after tool calls if enabled |
| `wisp/tools.py` | Add `run_tests` tool for manual trigger |
| `wisp/__main__.py` | Add `--auto-test` CLI flag; `wisp test` command |
| `wisp/config.py` | Add `auto_test`, `test_framework`, `test_command` settings |

### Dependencies
```
watchdog>=3.0      # File system events
pytest>=7.0        # Test collection (already have)
```

### Data Flow

1. **Import Graph Building:**
   ```python
   # wisp/import_graph.py
   def build_import_graph(workspace: str) -> dict[str, set[str]]:
       """Map each source file to the set of files it imports."""
       graph = {}
       for pyfile in Path(workspace).rglob("*.py"):
           imports = extract_imports(pyfile)  # AST-based
           graph[str(pyfile)] = imports
       return graph
   ```

2. **Test Discovery:**
   ```python
   # wisp/test_runner.py
   def discover_tests(workspace: str) -> list[TestDef]:
       """Use pytest --collect-only to find all tests."""
       ...

   def map_tests_to_files(tests: list[TestDef]) -> dict[str, list[str]]:
       """Map each test file to the source files it tests (via imports)."""
       ...
   ```

3. **Smart Selection:**
   ```python
   def select_tests(changed_files: list[str], graph: dict) -> list[str]:
       """Find tests affected by changed files."""
       affected = set()
       for changed in changed_files:
           # Direct: tests that import changed file
           for test, imports in graph.items():
               if changed in imports:
                   affected.add(test)
           # Transitive: tests that import files which import changed file
           # (2-level BFS)
       return list(affected)
   ```

4. **Auto-Run Integration:**
   ```python
   # In agent.py after edit_file/write_file:
   if self.config.auto_test:
       affected = select_tests([changed_file], self._import_graph)
       if affected:
           result = run_tests(affected)
           self.messages.append({"role": "system", "content": f"Tests: {result}"})
   ```

### CLI Additions
```bash
wisp --auto-test "refactor auth"     # Enable auto-test for this run
wisp repl --auto-test                # REPL with auto-test
wisp test                          # Run all tests
wisp test --changed                  # Run tests affected by uncommitted changes
wisp test --watch                    # Watch mode (run on file change)
```

### System Prompt Addition
```markdown
## Auto-Test
- Tests run automatically after file edits (if enabled)
- Use `run_tests()` to manually trigger test suite
- Test results are injected into conversation context
```

---

## 3. Git-Aware Context

### Goal
Inject git state into system prompt: uncommitted changes, recent commits, branch info. Warn about editing files with pending changes.

### Architecture
```
git status → Parse diff → Format context → Inject into system prompt
     ↓
git log --oneline -5 → Recent commits
     ↓
git branch → Current branch
```

### New Files

| File | Purpose | Size Estimate |
|------|---------|---------------|
| `wisp/git_context.py` | Git state extraction, diff parsing | ~300 lines |
| `wisp/diff_parser.py` | Parse git diff into structured format | ~200 lines |

### Modified Files

| File | Changes |
|------|---------|
| `wisp/agent.py` | Call `git_context.format_context()` in `_build_system_prompt()` |
| `wisp/tools.py` | Add `git_status`, `git_diff` tools |
| `wisp/__main__.py` | Add `wisp git` subcommands |

### No New Dependencies
Uses `git` CLI via `subprocess` (already available).

### Data Flow

1. **Git State Extraction:**
   ```python
   # wisp/git_context.py
   @dataclass
   class GitState:
       branch: str
       is_dirty: bool
       untracked_files: list[str]
       modified_files: list[str]
       staged_files: list[str]
       recent_commits: list[str]  # Last 5
       ahead_behind: str  # "+2 -1" etc.

   def get_git_state(workspace: str) -> GitState | None:
       """Extract git state via git CLI."""
       ...
   ```

2. **Diff Parsing:**
   ```python
   def get_file_diff(filepath: str) -> str:
       """Get git diff for a specific file."""
       ...

   def has_uncommitted_changes(filepath: str) -> bool:
       """Check if file has pending changes."""
       ...
   ```

3. **System Prompt Injection:**
   ```markdown
   ## Git Context
   - Branch: feature/auth-refactor
   - Uncommitted: 3 files modified, 1 untracked
   - Recent commits:
     - abc1234 feat: add JWT validation
     - def5678 fix: token expiry check
   - ⚠️ config.py has uncommitted changes
   ```

4. **Edit Guard:**
   ```python
   # In tool_edit_file / tool_write_file:
   if git_context.has_uncommitted_changes(filepath):
       logger.warning("Editing file with uncommitted changes: %s", filepath)
       # Still allow but warn
   ```

### Tool Schemas
```json
{
  "name": "git_status",
  "description": "Show git status: branch, uncommitted files, recent commits"
}
{
  "name": "git_diff",
  "description": "Show git diff for a file or entire workspace",
  "parameters": {
    "path": {"type": "string", "description": "File to diff (omit for all)"}
  }
}
```

---

## 4. Persistent Agent Memory

### Goal
*My* memory (the assistant's) across conversations — not just Wisp's LLM memory. Remember session history, user preferences, project state.

### Architecture
```
Conversation ends → Summarize key facts → Store in ~/.config/wisp/agent_memory/
New conversation → Load summary → Inject into context
```

### New Files

| File | Purpose | Size Estimate |
|------|---------|---------------|
| `wisp/agent_memory.py` | Conversation summarization, persistence | ~300 lines |
| `wisp/summarizer.py` | Lightweight local summarization (extractive) | ~150 lines |

### Modified Files

| File | Changes |
|------|---------|
| `wisp/agent.py` | Save conversation summary on session end; load on start |
| `wisp/session.py` | Add `summarize()` method |

### No New Dependencies
Uses extractive summarization (key sentence extraction) — no ML model needed.

### Data Model
```python
# ~/.config/wisp/agent_memory/sessions.jsonl
{
  "session_id": "20260503-abc123",
  "timestamp": "2026-05-03T07:00:00Z",
  "workspace": "/Users/philosopher/Documents/wisp",
  "summary": "Implemented 10 features: project context, code index, structured results, fuzzy edit, config schema, markdown parser, MCP client, tree-sitter index, cross-session memory, VS Code MCP server. Fixed Docker crash. 299 tests pass.",
  "key_decisions": [
    "Use Dice coefficient for fuzzy matching (no dependency)",
    "Tree-sitter optional with regex fallback"
  ],
  "user_preferences": [
    "Prefers Hindi-English mix",
    "Likes detailed explanations",
    "Uses VS Code as editor"
  ],
  "open_tasks": [
    "Implement semantic search",
    "Add auto-test on change"
  ]
}
```

### Summarization Strategy
1. **Extract key sentences:** Use TF-IDF + position scoring
2. **Identify decisions:** Look for "we decided", "let's use", "going with"
3. **Track preferences:** User corrections, explicit preferences
4. **Note open tasks:** TODOs, "next time", "later"

### System Prompt Injection
```markdown
## Previous Session Summary
- Last worked on: Wisp codebase (10 features implemented)
- Key decisions: Tree-sitter optional, Dice coefficient for fuzzy match
- Open tasks: Semantic search, auto-test, git-aware context
- User prefers Hindi-English communication
```

---

## 5. Multi-Model Fallback Chain

### Goal
Smart model routing: fast/cheap for simple tasks, powerful for complex reasoning. Track cost and performance.

### Architecture
```
User prompt → Classify complexity → Route to appropriate model
                  ↓
    Simple query → Local model (llama3.2:3b, fast)
    Complex reasoning → Cloud model (deepseek-v4, powerful)
    Code generation → Code-specialized model (deepseek-coder)
```

### New Files

| File | Purpose | Size Estimate |
|------|---------|---------------|
| `wisp/model_router.py` | Complexity classification, model selection | ~250 lines |
| `wisp/model_registry.py` | Model configs, capabilities, cost tracking | ~200 lines |
| `wisp/usage_tracker.py` | Token counting, cost estimation, latency tracking | ~150 lines |

### Modified Files

| File | Changes |
|------|---------|
| `wisp/ollama_client.py` | Support multiple model endpoints |
| `wisp/agent.py` | Use `ModelRouter` instead of single model |
| `wisp/config.py` | Add `models` list with priority/cost settings |

### Dependencies
```
# No new dependencies — uses existing Ollama client
```

### Model Registry
```python
# wisp/model_registry.py

MODELS = {
    "llama3.2:3b": {
        "context": 128000,
        "speed": "fast",
        "cost_per_1k": 0.0,  # Local = free
        "strengths": ["simple_qa", "summarization"],
        "endpoint": "http://localhost:11434",
    },
    "deepseek-coder:6.7b": {
        "context": 64000,
        "speed": "medium",
        "cost_per_1k": 0.0,
        "strengths": ["code_generation", "refactoring"],
        "endpoint": "http://localhost:11434",
    },
    "deepseek-v4-flash:cloud": {
        "context": 1048576,
        "speed": "slow",
        "cost_per_1k": 0.001,  # Hypothetical
        "strengths": ["complex_reasoning", "architecture"],
        "endpoint": "http://localhost:11434",
    },
}
```

### Complexity Classifier
```python
def classify_complexity(prompt: str, history: list) -> str:
    """Classify prompt complexity for model routing."""
    # Heuristics:
    # - Length > 500 chars → complex
    # - Contains "design", "architecture", "refactor" → complex
    # - Contains "what", "how", "explain" → simple
    # - Has tool calls in history → medium
    # - Multiple files mentioned → complex
    ...
```

### Usage Tracking
```python
# wisp/usage_tracker.py
@dataclass
class UsageRecord:
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    timestamp: datetime

class UsageTracker:
    def __init__(self):
        self.records: list[UsageRecord] = []
    
    def log(self, record: UsageRecord):
        self.records.append(record)
        # Persist to ~/.config/wisp/usage.jsonl
    
    def summary(self, hours: int = 24) -> dict:
        """Return usage summary for last N hours."""
        ...
```

### CLI Additions
```bash
wisp --model llama3.2:3b "simple query"     # Force specific model
wisp --smart-model "complex task"            # Auto-select
wisp usage                                   # Show usage stats
wisp usage --reset                           # Clear tracking
```

---

## 6. Screenshot / Visual Understanding

### Goal
Capture VS Code / browser screenshots for UI-related tasks. Show visual state to the LLM.

### Architecture
```
User asks about UI → Take screenshot → Convert to text description (via vision model)
                        ↓
                   Or: Show diff visualization
```

### New Files

| File | Purpose | Size Estimate |
|------|---------|---------------|
| `wisp/screenshot.py` | Platform-specific screenshot capture | ~200 lines |
| `wisp/vision.py` | Vision model integration (optional) | ~150 lines |

### Modified Files

| File | Changes |
|------|---------|
| `wisp/tools.py` | Add `take_screenshot` tool |
| `wisp/mcp_servers/vscode_server.py` | Add `vscode_screenshot` MCP tool |

### Platform Support
- **macOS:** `screencapture` CLI or `CGDisplayCreateImage`
- **Linux:** `gnome-screenshot` or `import` (ImageMagick)
- **Windows:** `PIL.ImageGrab` or `win32api`

### Implementation
```python
# wisp/screenshot.py

def take_screenshot() -> Path:
    """Capture screen and save to temp file."""
    if sys.platform == "darwin":
        path = "/tmp/wisp_screenshot.png"
        subprocess.run(["screencapture", "-x", path], check=True)
        return Path(path)
    elif sys.platform == "linux":
        ...
    else:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        path = Path("/tmp/wisp_screenshot.png")
        img.save(path)
        return path
```

### Vision Model (Optional)
If a vision-capable model is available:
```python
def describe_screenshot(image_path: Path) -> str:
    """Use vision model to describe screenshot content."""
    # Encode image to base64
    # Send to multimodal model (GPT-4V, Claude 3, etc.)
    # Return text description
```

### Tool Schema
```json
{
  "name": "take_screenshot",
  "description": "Capture a screenshot of the current screen. Use to understand UI state, verify visual changes, or debug layout issues.",
  "parameters": {}
}
```

---

## 7. Real-time Health Monitoring

### Goal
Monitor system health: memory, CPU, disk. Auto-detect service failures (Ollama, Docker, MCP). Alert on hangs.

### Architecture
```
Background thread → Poll every 30s → Check services → Alert if degraded
                        ↓
                   Memory > 80%? → Warn
                   Ollama down? → Auto-restart attempt
                   Operation > 30s? → Alert possible hang
```

### New Files

| File | Purpose | Size Estimate |
|------|---------|---------------|
| `wisp/health_monitor.py` | Health checks, alerting, auto-restart | ~300 lines |
| `wisp/system_info.py` | Cross-platform system metrics | ~200 lines |

### Modified Files

| File | Changes |
|------|---------|
| `wisp/agent.py` | Start health monitor on init; check before operations |
| `wisp/ollama_client.py` | Report health status to monitor |
| `wisp/mcp.py` | Report MCP connection health |

### No New Dependencies
Uses `psutil` (optional) or platform-specific CLIs (`vm_stat`, `free`, `df`).

### Health Checks
```python
# wisp/health_monitor.py

class HealthMonitor:
    def __init__(self):
        self.checks: list[HealthCheck] = []
        self.alerts: list[Alert] = []
    
    def register(self, check: HealthCheck):
        self.checks.append(check)
    
    async def run(self):
        while True:
            for check in self.checks:
                status = await check.run()
                if status.level != "ok":
                    self.alerts.append(Alert(
                        source=check.name,
                        level=status.level,  # warning, critical
                        message=status.message,
                    ))
            await asyncio.sleep(30)

# Built-in checks
ollama_check = HealthCheck(
    name="ollama",
    check=lambda: client.check_health(),
    auto_restart=True,
)

memory_check = HealthCheck(
    name="memory",
    check=lambda: get_memory_percent() < 80,
    alert_threshold=90,
)

disk_check = HealthCheck(
    name="disk",
    check=lambda: get_disk_free_gb() > 5,
)
```

### Alert Integration
```python
# In agent.py before heavy operations:
if health_monitor.has_critical_alerts():
    logger.warning("System under stress — operations may be slow")
    # Or: ask user if they want to continue
```

### CLI Additions
```bash
wisp health              # Show current health status
wisp health --watch      # Continuous monitoring
```

---

## 8. Structured Planning / Task Decomposition

### Goal
When user says "implement X", auto-break into subtasks, estimate complexity, track progress.

### Architecture
```
User request → LLM generates plan → Store plan → Execute step-by-step
                  ↓
            Task tree with dependencies
                  ↓
            Progress tracking + user updates
```

### New Files

| File | Purpose | Size Estimate |
|------|---------|---------------|
| `wisp/planner.py` | Task decomposition, dependency analysis | ~400 lines |
| `wisp/task_tree.py` | Tree data structure for tasks | ~200 lines |
| `wisp/progress.py` | Progress tracking, reporting | ~150 lines |

### Modified Files

| File | Changes |
|------|---------|
| `wisp/agent.py` | Check for active plan; execute next step |
| `wisp/tools.py` | Add `plan_task`, `mark_step_done` tools |

### Plan Format
```python
# wisp/planner.py

@dataclass
class Task:
    id: str
    description: str
    estimated_complexity: str  # low, medium, high
    dependencies: list[str]  # Task IDs that must complete first
    files_to_touch: list[str]
    status: str  # pending, in_progress, done, blocked
    notes: str

class Plan:
    def __init__(self, goal: str):
        self.goal = goal
        self.tasks: list[Task] = []
        self.created_at = datetime.now()
    
    def next_task(self) -> Task | None:
        """Return next ready task (all dependencies done)."""
        ...
    
    def progress(self) -> tuple[int, int]:
        """Return (done_count, total_count)."""
        ...
```

### LLM Prompt for Planning
```markdown
Break down this task into subtasks. For each subtask:
1. Description (1 sentence)
2. Complexity (low/medium/high)
3. Dependencies (which other subtasks must finish first)
4. Files likely to change

Task: {user_request}
```

### System Prompt Addition
```markdown
## Active Plan: {goal}
Progress: {done}/{total} tasks complete
Next: {next_task_description}
```

### CLI Additions
```bash
wisp plan "implement semantic search"     # Generate and show plan
wisp plan --execute "refactor auth"         # Generate and start executing
wisp progress                              # Show current plan progress
wisp plan --abort                          # Cancel current plan
```

---

## 9. Better Error Diagnosis

### Goal
When tests fail or code crashes, automatically diagnose root cause and suggest fix.

### Architecture
```
Test fails → Capture output → Parse stack trace → Identify failing line
                ↓
         Correlate with recent changes
                ↓
         Suggest fix based on error type
```

### New Files

| File | Purpose | Size Estimate |
|------|---------|---------------|
| `wisp/error_diagnosis.py` | Stack trace parsing, error classification | ~300 lines |
| `wisp/fix_suggester.py` | Pattern-based fix suggestions | ~250 lines |

### Modified Files

| File | Changes |
|------|---------|
| `wisp/test_runner.py` | (from Feature 2) Add diagnosis hook |
| `wisp/agent.py` | Auto-diagnose on test failure |

### Error Classification
```python
# wisp/error_diagnosis.py

ERROR_PATTERNS = {
    "ImportError": {
        "pattern": r"ImportError: cannot import name '(\w+)'",
        "suggest": lambda m: f"Check if '{m.group(1)}' exists and is exported in the target module.",
    },
    "AttributeError": {
        "pattern": r"AttributeError: '(\w+)' object has no attribute '(\w+)'",
        "suggest": lambda m: f"Check spelling of '{m.group(2)}' or verify object type.",
    },
    "SyntaxError": {
        "pattern": r"SyntaxError: (.+)",
        "suggest": lambda m: "Check for missing brackets, quotes, or indentation.",
    },
    "AssertionError": {
        "pattern": r"AssertionError",
        "suggest": lambda m: "Test expectation doesn't match actual result. Check test logic and implementation.",
    },
    "IndentationError": {
        "pattern": r"IndentationError: (.+)",
        "suggest": lambda m: "Fix indentation — likely mixed tabs/spaces or wrong level.",
    },
}

def diagnose(error_output: str, changed_files: list[str]) -> Diagnosis:
    """Analyze error output and suggest fixes."""
    for error_type, config in ERROR_PATTERNS.items():
        if match := re.search(config["pattern"], error_output):
            suggestion = config["suggest"](match)
            return Diagnosis(
                error_type=error_type,
                suggestion=suggestion,
                likely_cause=identify_likely_cause(error_output, changed_files),
            )
    return Diagnosis(error_type="Unknown", suggestion="Review error output manually.")
```

### Integration
```python
# In test_runner.py after failure:
diagnosis = diagnose(test_output, recently_changed_files)
self.messages.append({
    "role": "system",
    "content": f"Test failed with {diagnosis.error_type}. Suggestion: {diagnosis.suggestion}"
})
```

---

## 10. Collaborative Editing

### Goal
Support multiple agents/humans editing same codebase. Lock awareness, conflict prediction, change notifications.

### Architecture
```
Agent A edits file X → Write lock file → Agent B checks lock before editing
                ↓
         Lock expires after timeout (prevents stale locks)
                ↓
         On conflict: merge or alert user
```

### New Files

| File | Purpose | Size Estimate |
|------|---------|---------------|
| `wisp/file_lock.py` | Advisory file locking with timeouts | ~200 lines |
| `wisp/change_tracker.py` | Track who changed what, when | ~150 lines |

### Modified Files

| File | Changes |
|------|---------|
| `wisp/tools.py` | Check locks before edit_file/write_file |
| `wisp/agent.py` | Register change tracker; check conflicts |

### Lock File Format
```
.wisp/locks/
  config.py.lock → {"agent": "wisp-abc123", "since": "2026-05-03T07:00:00Z", "expires": "2026-05-03T07:05:00Z"}
```

### Implementation
```python
# wisp/file_lock.py

class FileLock:
    def __init__(self, workspace: str, agent_id: str):
        self.lock_dir = Path(workspace) / ".wisp" / "locks"
        self.agent_id = agent_id
    
    def acquire(self, filepath: str, timeout_sec: int = 300) -> bool:
        """Try to acquire lock on file."""
        lock_file = self.lock_dir / f"{Path(filepath).name}.lock"
        if lock_file.exists():
            lock = json.loads(lock_file.read_text())
            if datetime.fromisoformat(lock["expires"]) > datetime.now():
                return False  # Lock held by another agent
        lock_file.write_text(json.dumps({
            "agent": self.agent_id,
            "since": datetime.now().isoformat(),
            "expires": (datetime.now() + timedelta(seconds=timeout_sec)).isoformat(),
        }))
        return True
    
    def release(self, filepath: str):
        lock_file = self.lock_dir / f"{Path(filepath).name}.lock"
        lock_file.unlink(missing_ok=True)
```

### Conflict Detection
```python
def detect_conflict(filepath: str, my_changes: str, other_lock: dict) -> Conflict | None:
    """Detect if another agent has modified the same file."""
    # Check if file was modified since lock was acquired
    # If yes, alert user about potential conflict
    ...
```

---

## Implementation Priority

### Phase 1: Foundation (Week 1)
| # | Feature | Effort | Impact | Files |
|---|---------|--------|--------|-------|
| 1 | **Git-Aware Context** | Low | High | `git_context.py`, `agent.py` |
| 2 | **Persistent Agent Memory** | Low | High | `agent_memory.py`, `agent.py` |
| 3 | **Better Error Diagnosis** | Low | Medium | `error_diagnosis.py`, `test_runner.py` |

### Phase 2: Intelligence (Week 2)
| # | Feature | Effort | Impact | Files |
|---|---------|--------|--------|-------|
| 4 | **Semantic Code Search** | Medium | Very High | `semantic_search.py`, `embeddings.py`, `vector_store.py` |
| 5 | **Auto-Test on Change** | Medium | High | `test_runner.py`, `import_graph.py`, `file_watcher.py` |
| 6 | **Structured Planning** | Medium | Medium | `planner.py`, `task_tree.py` |

### Phase 3: Scale (Week 3)
| # | Feature | Effort | Impact | Files |
|---|---------|--------|--------|-------|
| 7 | **Multi-Model Fallback** | Medium | Medium | `model_router.py`, `usage_tracker.py` |
| 8 | **Health Monitoring** | Low | Medium | `health_monitor.py`, `system_info.py` |
| 9 | **Collaborative Editing** | Medium | Low | `file_lock.py`, `change_tracker.py` |
| 10 | **Screenshot / Vision** | Medium | Low | `screenshot.py`, `vision.py` |

---

## Dependency Graph

```
Git Context ──┬──► Agent Memory (uses git for session tracking)
              └──► Error Diagnosis (uses git for change correlation)

Semantic Search ──┬──► Auto-Test (uses code index for test mapping)
                  └──► Planner (uses search for task analysis)

Test Runner ──► Error Diagnosis (test output parsing)

Health Monitor ──► Multi-Model (checks model availability)
                 ──► MCP (checks server health)
```

---

## Configuration Additions

```python
# wisp/config.py — new settings

SETTINGS_SCHEMA.update({
    "semantic_search_enabled": {
        "type": bool, "default": True,
        "description": "Enable AI-powered semantic code search"
    },
    "semantic_search_model": {
        "type": str, "default": "all-MiniLM-L6-v2",
        "description": "Embedding model for semantic search"
    },
    "auto_test": {
        "type": bool, "default": False,
        "description": "Auto-run tests after file edits"
    },
    "test_framework": {
        "type": str, "default": "pytest",
        "description": "Test framework: pytest, unittest, jest, etc."
    },
    "git_context_enabled": {
        "type": bool, "default": True,
        "description": "Inject git status into system prompt"
    },
    "agent_memory_enabled": {
        "type": bool, "default": True,
        "description": "Persist conversation summaries across sessions"
    },
    "health_monitor_enabled": {
        "type": bool, "default": True,
        "description": "Monitor system and service health"
    },
    "multi_model_enabled": {
        "type": bool, "default": False,
        "description": "Enable smart model routing"
    },
    "models": {
        "type": list, "default": [],
        "description": "List of model configs for multi-model routing"
    },
})
```

---

## Testing Strategy

| Feature | Test Approach | Coverage Target |
|---------|--------------|-----------------|
| Semantic Search | Mock embedding model; test similarity ranking | 90% |
| Auto-Test | Mock file system; test selection logic | 85% |
| Git Context | Mock git CLI; test parsing | 90% |
| Agent Memory | Mock sessions; test summarization | 80% |
| Multi-Model | Mock model endpoints; test routing | 85% |
| Health Monitor | Mock system metrics; test alerting | 80% |
| Planner | Mock LLM; test plan generation | 75% |
| Error Diagnosis | Sample error outputs; test classification | 85% |
| File Lock | Mock file system; test concurrency | 90% |
| Screenshot | Mock platform; test file creation | 70% |

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Semantic search too slow for large codebases | Use FAISS; lazy indexing; incremental updates |
| Auto-test runs too frequently | Debounce (5s delay); only run on save, not every keystroke |
| Agent memory grows too large | Summarize aggressively; TTL on old memories |
| Multi-model adds latency | Pre-warm connections; cache model availability |
| Health monitor noise | Configurable thresholds; snooze alerts |
| File locks become stale | Auto-expire after 5 min; heartbeat renewal |

---

## Success Metrics

| Metric | Baseline | Target |
|--------|----------|--------|
| Time to find relevant code | 30s (grep) | 5s (semantic) |
| Test feedback loop | Manual (2 min) | Auto (10s) |
| Context lost between sessions | 100% | 10% |
| Model cost per task | $0.01 (fixed) | $0.005 (smart routing) |
| System downtime awareness | None | <30s detection |
| Plan adherence | N/A | 80% tasks completed as planned |

---

*Document version: 1.0*
*Total estimated lines of new code: ~4,500*
*Total estimated lines modified: ~800*
*Estimated implementation time: 3 weeks (1 developer)*
