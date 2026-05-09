# Wisp Gap Analysis — What to Build Next

> Synthesized from 7 research files covering 25+ AI coding tools. May 2026.

---

## Current State: What Wisp Already Has

Wisp is further along than expected. 40+ Python modules, 20+ React components.

| Category | Implemented |
|----------|------------|
| **Agent** | WispAgentCore, event-driven, ReAct loop, tool calling |
| **Permissions** | 4-tier: full/ask_all/auto_edit/read_only |
| **Diff** | LCS diff, generate_diff_string, DiffPreview.tsx |
| **Git** | Auto commit flow, GitCommitBanner, git_context.py |
| **Fork** | forkSession API, fork-from-any-message |
| **Search** | Web search tool, DuckDuckGo, SearchResults.tsx |
| **Plan** | planner.py, PlanPanel.tsx, plan mode toggle |
| **Hooks** | 7 lifecycle events, JSON stdin/stdout protocol |
| **Checkpoints** | Git stash create + tar.gz fallback, max 50 |
| **Repo map** | Tree-sitter + PageRank, 500+ files/min |
| **Subagents** | Async + Semaphore, worktree isolation, 4 templates |
| **MCP** | OAuth2, mTLS, health check, retry, tool elicitation |
| **Plugins** | Manifest, Registry, NamespaceManager, Marketplace |
| **CLI** | --print, JSON output, MemoryTransport, /api/prompt |
| **Context** | CLAUDE.md, AGENTS.md, .wisp/rules.md, GEMINI.md |
| **PR Review** | /api/review/pr, /api/review/diff, /api/review/best-of-n |
| **Settings** | 7-tab modal (General, Appearance, Permissions, Context, Plugins, MCP, Hooks) |
| **Themes** | Dark/Light, custom JSON, ThemeSelector |
| **Vim** | Normal/insert/visual modes, h/j/k/l, dd, yy, p/P |
| **Keybindings** | Dynamic, platform symbols, load/save |
| **Skills** | Warp-compatible SKILL.md, OntoSkills SPARQL |
| **ACP** | Full protocol + adapter + session |
| **Memory** | Cross-session summaries, JSONL persistence |
| **TUI** | Terminal UI app via wisp/tui/ |
| **Supervisor** | Thread/run management, SQLite persistence |
| **Error diagnosis** | Pattern-based traceback analysis |

---

## Gap Analysis: What's Missing

### Tier 1 — Table Stakes (must build to compete)

#### 1. Autocomplete / Tab Completion
**Market penetration: 100%** — Every competitor has this.

Cursor Fusion, Copilot ghost text, Windsurf Supercomplete, Zed Zeta2, PearAI, Continue, Cody — all ship inline code completions. This is the single biggest missing feature.

**What to build:**
- Backend: completion endpoint (POST /api/complete) that accepts cursor position, surrounding context, recent edits
- Model: start with BYOK (pass through to user's Anthropic/OpenAI key); later train a small sparse model
- Frontend: ghost text rendering in VimEditor/ChatInput, Tab to accept, Escape to dismiss
- Context window: start with 4K tokens (cursor ± 2K tokens bidirectional)
- Partial accept: Cmd+Right (Mac) / Ctrl+Right (Win) for word-by-word (build infrastructure now, ship with completion)

**Pattern to copy:** Cursor Fusion's bidirectional context + Cmd+Right partial accept. Simpler than edit predictions to start.

**Files to touch:**
- `wisp/server.py` — POST /api/complete endpoint
- `wisp/completion.py` — NEW: completion engine (context assembly, model dispatch)
- `src/renderer/components/chat/CompletionGhost.tsx` — NEW: ghost text overlay
- `src/renderer/utils/vim.ts` — Tab/Escape/Cmd+Right handler integration
- `src/renderer/state/types.ts` — completion state + accept/reject/partial actions

#### 2. Inline Editing (Cmd+K Style)
**Market penetration: 95%** — Cursor Cmd+K, Copilot inline, Windsurf Cmd+I, PearAI, Continue, Zed Ctrl+Enter.

Select code, describe change in mini-prompt, AI rewrites selection in-place with diff preview.

**What to build:**
- Modal mini-input at selection location (not full chat)
- Backend: POST /api/edit/inline { selection, instruction, file_path }
- Returns: new_text for the selection
- Frontend: show side-by-side or inline diff, Accept (Enter) / Reject (Escape)
- Shortcut: Cmd+K (Mac) / Ctrl+K (Win/Linux)

**Files to touch:**
- `wisp/server.py` — POST /api/edit/inline
- `src/renderer/components/chat/InlineEdit.tsx` — NEW: inline edit popup + diff
- `src/renderer/hooks/useKeybindings.ts` — Cmd+K binding
- `wisp/core/agent.py` — single-edit mode (no tool loop, just one transform)

#### 3. Semantic Codebase Search / RAG
**Market penetration: 85%** — Cursor @codebase, Windsurf M-Query, Cody Sourcegraph Search, Augment Context Engine, Copilot @workspace.

Wisp has code_index.py (symbol regex), repo_map.py (PageRank structure), tree_sitter_index.py. But no **semantic embedding search**. The agent can grep and read files but can't ask "where is error handling for payment failures?"

**What to build:**
- Embedding pipeline: chunk files (by function/class), generate embeddings via local model (all-MiniLM-L6-v2 or similar via Ollama), store in ChromaDB or sqlite-vec
- Query-time: embed user query, retrieve top-K chunks, inject into system prompt
- Incremental updates: on file save, re-index only changed files
- @codebase mention in chat input
- Tool: search_codebase(query: str) for agent use

**Files to touch:**
- `wisp/semantic_index.py` — NEW: embedding pipeline
- `wisp/server.py` — GET /api/codebase/search?q=...
- `src/renderer/components/chat/ContextIndicator.tsx` — add index status
- `wisp/core/agent.py` — inject retrieved chunks into system prompt

---

### Tier 2 — Competitive Parity (needed to match leaders)

#### 4. Per-Hunk Accept/Reject in Diffs
**Market penetration: 80%** — Zed, Cursor, Warp all allow accepting/rejecting individual diff hunks.

Wisp's DiffPreview.tsx shows a unified diff but likely is all-or-nothing accept.

**What to build:**
- Parse unified diff into hunks
- Each hunk: green (added) / red (removed) background, "Accept" / "Reject" buttons
- Navigation: Alt+Up/Down between hunks
- Agent applies only accepted hunks on confirm

**Files to touch:**
- `src/renderer/components/chat/DiffPreview.tsx` — rewrite: hunk-level UI
- `wisp/diff.py` — add `parse_hunks()` function

#### 5. OS-Level Sandboxing
**Market penetration: 70%** — Codex CLI uses Seatbelt (macOS) + Landlock/seccomp (Linux). Cursor has sandbox mode. Gemini CLI uses Docker.

Wisp has NO sandbox for bash commands. Tools run directly on the host.

**What to build:**
- macOS: Seatbelt/Sandbox via sandbox-exec with minimal profile (read access to workspace only, no network, no writes outside workspace)
- Linux: Landlock + seccomp (allowlist syscalls)
- Configurable: sandbox/warn/disabled modes
- New sandbox: block reads outside workspace, block network by default, block writes outside workspace, allow only whitelisted commands

**Files to touch:**
- `wisp/sandbox.py` — NEW: platform sandbox wrappers
- `wisp/tools.py` — wrap execute_bash sandbox
- `wisp/config.py` — sandbox_mode setting

#### 6. Background / Cloud Agents
**Market penetration: 65%** — Devin, Cursor Cloud Agents, Warp Oz, Copilot Workspace.

Wisp agents run synchronously in the user's process. No "assign and walk away."

**What to build:**
- Agent run queue: spawn agent in subprocess/subagent
- Status polling: GET /api/run/{id}/status
- Push notification when complete (desktop notification)
- Optional: Docker-based isolation for cloud-like execution
- Simple version first: background subprocess, not full cloud orchestration

**Files to touch:**
- `wisp/server.py` — POST /api/run/background, GET /api/run/{id}
- `wisp/background_agent.py` — NEW: background agent runner
- `src/renderer/components/chat/BackgroundAgentBanner.tsx` — NEW: status indicator
- `src/renderer/state/types.ts` — backgroundRun state

---

### Tier 3 — Differentiators (competitive white space)

#### 7. Proactive Edit Suggestions
**Market penetration: 20%** — Only Windsurf Supercomplete and Zed Zeta2 do this.

Predicts your next edit intent based on edit trajectory (last 30-90 seconds of edits) + AST awareness. Not just "complete this line" but "you renamed this interface field twice, here's the third occurrence."

**What to build:**
- Track last N edits (file, position, old_text, new_text)
- AST-aware scope detection (via tree-sitter): if you edit a TypeScript interface, scan for implementations
- Pattern matching: rename propagation, interface compliance, import adding
- Show as ghost text with "Apply" button (not auto-applied)
- Start simple: detect repeated rename pattern, suggest next occurrence

**Files to touch:**
- `wisp/edit_predictor.py` — NEW: edit trajectory analysis
- `src/renderer/components/chat/ProactiveEditBanner.tsx` — NEW

#### 8. Arena Mode (Blind A/B Model Comparison)
**Market penetration: 5%** — Only Windsurf has this.

Blind test two models on the same task in your codebase. Vote before you know which is which. Crowdsourced leaderboard.

**What to build:**
- Two parallel agent runs (isolated git worktrees)
- Hidden model identities
- Side-by-side diff comparison UI
- Vote: Model A / Model B / Tie
- Reveal identities after vote
- Local leaderboard (per-project model performance)
- Optional: opt-in anonymous contribution to public leaderboard

**Files to touch:**
- `wisp/arena.py` — NEW: arena orchestration
- `src/renderer/components/ArenaPanel.tsx` — NEW: comparison UI
- `wisp/server.py` — POST /api/arena/compare

#### 9. Mid-Turn Agent Steering
**Market penetration: 10%** — Very few tools allow redirecting agent mid-execution.

While agent is running, type an instruction to redirect: "focus on the auth module instead," "skip the tests," "use a different approach."

**What to build:**
- Message queue during agent generation
- "Interrupt" button during agent execution
- After interrupt: agent stops current tool, reads new instruction, adjusts course
- Queue new messages while agent is busy; execute when current turn completes

**Files to touch:**
- `wisp/core/agent.py` — interrupt handling, message queue
- `src/renderer/components/chat/ChatInput.tsx` — interrupt button while generating
- `wisp/transport/server.py` — send_interrupt WS message

#### 10. Visual Checkpoint Timeline
**Market penetration: 30%** — Cursor and Zed show checkpoints as markers in conversation timeline.

Wisp has checkpoints but likely shows them in a side panel, not inline in conversation.

**What to build:**
- Checkpoint markers inline in message list
- Click marker to see diff of changes at that point
- "Restore to here" button
- Visual: subtle horizontal line with timestamp and file count

**Files to touch:**
- `src/renderer/components/chat/ChatArea.tsx` — render checkpoint markers between messages
- `src/renderer/components/chat/CheckpointMarker.tsx` — NEW

---

## Priority Matrix (Impact × Effort)

| # | Feature | Impact | Effort | Priority |
|---|---------|--------|--------|----------|
| 1 | Autocomplete | ★★★★★ | Medium | **P0** |
| 3 | Semantic RAG | ★★★★★ | High | **P0** |
| 2 | Inline Editing | ★★★★ | Low | **P1** |
| 4 | Per-Hunk Accept | ★★★★ | Low | **P1** |
| 6 | Background Agents | ★★★★ | Medium | **P1** |
| 5 | OS Sandboxing | ★★★ | Medium | **P2** |
| 7 | Proactive Edits | ★★★ | High | **P2** |
| 8 | Arena Mode | ★★★ | Medium | **P2** |
| 10 | Checkpoint Timeline | ★★ | Low | **P3** |
| 9 | Mid-Turn Steering | ★★ | High | **P3** |

---

## Implementation Strategy

### Wave 1 (This Week): Table Stakes
1. **Inline Editing (Cmd+K)** — Lowest effort, highest perceived value. Single endpoint, small UI, immediate competitive parity.
2. **Per-Hunk Accept** — Piggybacks on existing DiffPreview. Low effort, high polish impact.

### Wave 2 (Next Week): Core Gaps
3. **Autocomplete** — Medium effort, but table stakes. Start with BYOK passthrough, no custom model.
4. **Semantic RAG** — High effort but transforms agent capability. Start with local embeddings, simple cosine retrieval.

### Wave 3 (Following Week): Differentiators
5. **Background Agents** — Leverages existing subagent infrastructure.
6. **Arena Mode** — Unique differentiator, only Windsurf has this.

### Wave 4 (Later): Polish
7. OS Sandboxing
8. Proactive Edit Suggestions
9. Checkpoint Timeline
10. Mid-Turn Steering

---

## Key Design Decisions

### Autocomplete: Start Simple
- Don't build a custom model like Fusion/Zeta2. Start with BYOK passthrough to user's LLM provider.
- Context: current file ± 2K tokens around cursor.
- Prompt: "Complete the code at <cursor>. Consider the surrounding context. Output only the completion, no explanation."
- Later: fine-tune a small model (1-3B params) for latency.

### Semantic RAG: Local-First
- Use local embedding model (no API call for indexing).
- Store in sqlite-vec (already using SQLite for persistence).
- This is what Augment preaches: per-developer index, proof of possession.

### Inline Editing: Reuse Agent Infrastructure
- Don't build a separate model pipeline.
- Use the same LLM but with a single-edit system prompt (no tool loop).
- The model gets: file content + selection + instruction → returns only the replacement text.

---

## Files This Plan Creates

```
wisp/
├── completion.py          # NEW: autocomplete engine
├── semantic_index.py      # NEW: embedding pipeline  
├── sandbox.py             # NEW: OS sandbox wrappers
├── background_agent.py    # NEW: background agent runner
├── arena.py               # NEW: blind model comparison
├── edit_predictor.py      # NEW: edit trajectory analysis
└── diff.py                # EDIT: add parse_hunks()

wisp-desktop/src/renderer/
├── components/chat/
│   ├── CompletionGhost.tsx       # NEW
│   ├── CompletionGhost.css       # NEW
│   ├── InlineEdit.tsx            # NEW
│   ├── InlineEdit.css            # NEW
│   ├── DiffPreview.tsx           # REWRITE: hunk-level
│   ├── BackgroundAgentBanner.tsx # NEW
│   ├── CheckpointMarker.tsx      # NEW
│   └── ProactiveEditBanner.tsx   # NEW
├── components/
│   └── ArenaPanel.tsx            # NEW
├── hooks/
│   └── useKeybindings.ts         # EDIT: Cmd+K binding
├── state/
│   └── types.ts                  # EDIT: completion + background + arena state
└── utils/
    └── vim.ts                    # EDIT: completion accept keys
```
