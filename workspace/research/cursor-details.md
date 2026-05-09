# Cursor IDE — Detailed Feature Reference

> Research compiled 2026-05-09. Sources: cursor.com, docs.cursor.com, cursor.com/changelog, cursor.com/blog, forum.cursor.com, community guides.

---

## 1. Tab Completion

### How it works
- Powered by **Fusion** — custom sparse language model trained to predict edits on billions of tokens
- Fires on **every keystroke and cursor movement** (400M+ requests/day, 1B+ edited chars/day)
- Context window: **13,000 tokens** (up from 5,500 in original model)
- p50 server latency: **260ms** (down from 475ms)
- Model is sparse (likely MoE-like) — only a subset of parameters activate per inference pass for sub-300ms latency
- **Bidirectional context** — reads both above and below cursor
- Considers recent edits, established file patterns, and codebase context
- Uses **Priompt** (Cursor's open-source prompt library) — treats prompt elements as prioritized JSX components; drops low-priority items via binary search when over budget

### Two simultaneous predictions
- **Edits near cursor** — multi-line changes, appears as gray "ghost text" inline
- **Cursor jumps** — predicts where you'll navigate next; Fusion made these "instant and accurate"

### Multi-line prediction
- Completes entire function bodies after signature is written
- Suggests coordinated edits across related code
- Adds missing import statements
- Fills in repetitive patterns (next list item, next test case)
- **Modifies existing code**, not just appends
- Edits are **10x longer change sequences** vs original model
- Edits are **+25% more difficult** vs original model (more complex transformations)

### Partial acceptance (word-by-word)
- `Cmd+Right Arrow` (Mac) / `Ctrl+Right Arrow` (Win/Linux) — accept one more word/token from ghost text
- Lets you take useful parts of a long suggestion, stop before unwanted content
- Works repeatedly — each press accepts one more token

### Jump-in-file
- After accepting a Tab suggestion, pressing `Tab` again predicts next editing location
- Cursor jumps to the predicted location automatically

### Cross-file edits (portal windows)
- When changes in one file need updates in another, a **portal window** appears at bottom of editor
- Shows the predicted change in the other file
- Accept/reject within the portal

### Confidence gating (RL-optimized)
- Model only shows suggestions when confidence exceeds internal threshold
- **Online RL** (policy gradient) formalized this:
  - Reward: +0.75 accept, -0.25 reject, 0 for showing nothing
  - Result: model learns to show only when P(accept) > 25%
  - Effect: **21% fewer suggestions, 28% higher accept rate**
- New checkpoints deploy **every 1.5-2 hours** based on real user feedback
- Multiple deploys per day — model improves within same day from user behavior

### Controls
- Toggle indicator in bottom-right corner: **Snooze temporarily**, **Disable globally**, **Disable per file extension**
- Remap accept key: search "Accept Cursor Tab Suggestions" in Keyboard Shortcuts
- Full accept: `Tab`
- Reject: `Escape` or keep typing

### Tab model architecture details
- Fusion is Cursor's 2nd-gen model (Jan 2025), replacing original model (Mar 2024)
- Context: 13K tokens bidrectional, server latency ~260ms p50
- Real-time RL runs multiple updates per day from on-policy user data

---

## 2. Agent / Composer

### Composer (the model)
- Cursor's custom agentic coding model, purpose-built for low-latency agentic coding
- **Composer 2** is current (2026): built on Kimi K2.5 (1.04T total / 32B active MoE)
- 4x faster than similarly intelligent models
- Most turns complete under 30 seconds
- Accessible via `Cmd+I` or through Cursor Chat dropdown (select "Composer")

### Agent Mode
- Toggle with `Cmd+.` (Mac) / `Ctrl+.` (Win/Linux)
- Follows **ReAct loop** (Reasoning + Action):
  1. Analyze — reads codebase to understand architecture
  2. Plan — determines which files need changes
  3. Execute — creates/modifies files, runs terminal commands
  4. Verify — runs tests or checks for errors
  5. Iterate — reads output, fixes errors, loops
- Automatically retrieves relevant context via `@Recommended`
- Runs terminal commands, performs semantic code search, edits files
- Stops after **25 tool calls** (requires Claude models)

### Four modes (Cursor 3.x)
| Mode | Purpose | Capabilities |
|------|---------|--------------|
| **Agent** (`Cmd+.`) | Build features, fix bugs, refactor | Autonomous multi-file edits, terminal, all tools |
| **Ask** (`Cmd+.`) | Understand code, explore | Read-only codebase search and explanation |
| **Plan** (`Shift+Tab`) | Break down complex features | Creates detailed Markdown plans with file paths and code references, asks clarifying questions, waits for approval |
| **Debug** (dropdown) | Find root causes of tricky bugs | Hypothesis generation, code instrumentation, log collection, targeted minimal fix |

### Gold-standard workflow: Ask -> Plan -> Agent
1. **Ask mode** — understand current architecture
2. **Plan mode** — create reviewable implementation plan (can edit before approving)
3. **Agent mode** — execute with auto-run enabled

### Best practices
- Start with plans — planning before coding is single highest-impact change
- Let agent find its own context — it has powerful grep and semantic search
- Start new conversations when switching tasks or agent gets confused
- Provide verifiable goals — typed languages, linters, tests give clear success signals
- Review carefully — AI code can look right while being subtly wrong

### Composer 1 vs Composer 1.5 vs Composer 2
| Capability | C1 | C1.5 | C2 |
|---|---|---|---|
| CursorBench | 38.0 | 44.2 | **61.3** |
| SWE-bench Multilingual | 56.9 | 65.9 | **73.7** |
| Terminal-Bench | 40.0 | 47.9 | **61.7** |
| API pricing | — | — | $0.50/M in, $2.50/M out |

---

## 3. Context System

### @-Mentions system
| Mention | What it does |
|---------|-------------|
| `@Files` | Reference a specific file |
| `@Folders` | Include an entire directory (use sparingly) |
| `@Codebase` | Scan entire project for context (token-expensive) |
| `@Web` | Fetch live web content |
| `@Docs` | Search third-party documentation (Next.js, Prisma, etc.) |
| `@Git` | Inject uncommitted changes as context |
| `@Linter Errors` | Let AI see and fix lint issues directly |
| `@PR` | Reference git diff with main branch for PR review |
| `@Branch` | Review all changes on current branch |
| `@Recommended` | Agent auto-gathers relevant context |
| `@rule-name` | Manually include a specific rule |

### Rules — Directory structure (modern system)
```
project/
├── .cursor/
│   └── rules/
│       ├── typescript-standards.mdc    # Rule with frontmatter
│       ├── react-patterns.mdc
│       ├── api-guidelines.md            # Plain .md also works
│       └── testing-conventions.mdc
├── .cursorrules                          # LEGACY — deprecated, being phased out
├── AGENTS.md                             # Simple markdown alternative
├── CLAUDE.md                             # Cross-compatible with Claude Code
└── .cursorignore                         # Block agent from indexing/reading files
```

### Rule anatomy (.mdc files)
```yaml
---
description: "React component patterns and conventions"
globs: "src/components/**/*.tsx"
alwaysApply: false
---
# Rule body — markdown instructions for the AI
```

### Rule activation modes (driven by frontmatter)
| Mode | `alwaysApply` | `globs` | `description` | Behavior |
|------|--------------|---------|--------------|----------|
| Always Apply | `true` | — | — | Included in every chat session |
| Auto Attached | `false` | provided | — | Activates when matching files are in context |
| Agent Requested | `false` | omitted | provided | AI reads description and pulls rule when relevant |
| Manual (@-mention) | `false` | omitted | omitted | Only applies when you `@rule-name` in chat |

### Rules hierarchy (precedence — earlier wins)
1. **Team Rules** — Team/Enterprise plans (highest priority)
2. **Project Rules** — `.cursor/rules/*.mdc` (version-controlled)
3. **User Rules** — Cursor Settings > Rules (global across all projects)
4. **Legacy Rules** — `.cursorrules` file (deprecated)
5. **AGENTS.md** — simple markdown in project root

### Skills — dynamic, on-demand knowledge
- Location: `.cursor/skills/<skill-name>/SKILL.md`
- Loaded dynamically — only when agent decides skill is relevant
- Markdown with YAML frontmatter (`name`, `description`)
- Invoked automatically or manually via `/skill-name`
- Can be pinned as quick-action pills (v3.3)

### MCP Servers (Model Context Protocol)
- Location: `mcp.json` at project root, or `~/.cursor/mcp.json` (user-level)
- Connect to Slack, Datadog, Sentry, databases, Figma, etc.
- Loaded on demand to reduce token usage
- Toggle on/off via Settings > Features > MCP
- Both HTTP (recommended) and stdio transports; OAuth supported

### Hooks — event-driven automation
- Location: `hooks/hooks.json` (in plugin or project)
- Events: `sessionStart`, `sessionEnd`, `stop`, `preCompact`
- Tool hooks: `preToolUse`, `postToolUse`, `postToolUseFailure`
- File hooks: `beforeReadFile`, `afterFileEdit`
- Shell hooks: `beforeShellExecution`, `afterShellExecution`
- MCP hooks: `beforeMCPExecution`, `afterMCPExecution`
- Subagent hooks: `subagentStart`, `subagentStop`
- User hooks: `beforeSubmitPrompt`, `afterAgentResponse`, `afterAgentThought`

### Subagents — specialized isolated assistants
- Location: `.cursor/agents/` (project) or `~/.cursor/agents/` (user)
- Context-isolated, parallel execution, specialized prompts & models
- Modes: **Foreground** (blocks until done) or **Background** (runs independently)
- Built-in subagents: `explore`, `bash`, `browser` — used automatically
- Can launch child subagents (v2.5+) — tree of coordinated work
- Can resume previous sessions via agent ID
- Custom model per subagent; `readonly: true` option for audit agents

### Context budget / long context handling
- Cursor middleware imposes **stricter context limits** than underlying LLM capabilities
- "Context is too large" error when over budget — more aggressive in recent versions
- Long context checkbox in settings for larger inputs (but may degrade output quality)
- **Max Mode**: extended context windows; burns quota faster
- Each chat/conversation has independent context budget
- Long chats degrade output — recommended to start fresh regularly

### .cursorignore
- Blocks agent from reading/indexing sensitive or junk files
- Example: `node_modules/`, `dist/`, `build/`, `.next/`, `coverage/`, `.env`, `*.pem`, `secrets/`
- Codebase indexing: Indexes new files by default (folders <10,000 files); manual "Compute Index" for larger
- Ignore files configurable in settings (in addition to .gitignore)
- Git graph relationships: indexes git history for file relationship understanding

---

## 4. Inline Editing (Cmd+K)

### Basic flow
1. Select code in editor
2. Press `Cmd+K` (Mac) / `Ctrl+K` (Win/Linux)
3. Type instruction (e.g., "Convert this to async function", "Add error handling for empty lists")
4. Press `Return` — Cursor rewrites the selection in-place

### Variations
- `Opt+Return` (Mac) / `Alt+Return` (Win/Linux) — enter **question mode** (no code change)
- `Cmd+Shift+Enter` — **full-file edits** from inline edit (v0.50+)
- `Cmd+L` — **send to Agent** for multi-file changes
- `Cmd+Shift+K` — **add selection to Edit** (explicit context shuttle)

### Diff preview
- Side-by-side diff panel: **left = original, right = AI suggestion**
- Themed diffs option in settings (background colors for inline diffs in editor)
- Toggle inline diffs on/off in settings (v2.5+)

### Accept / Reject
| Action | Key |
|--------|-----|
| Accept | `Enter` or `Tab` |
| Reject | `Escape` |
| Modify before accepting | Edit in-place within diff view, then accept |
| Accept all changes (Agent) | `Cmd+Enter` |
| Reject all changes (Agent) | `Cmd+Backspace` |
| Navigate next diff | `Option+Down` (Mac) / `Alt+Down` (Win/Linux) |
| Navigate previous diff | `Option+Up` (Mac) / `Alt+Up` (Win/Linux) |

### Critical gotchas
- Diff panel appears **only once** during generation. Dismissed = lost, must regenerate
- Scroll within diff viewer for changes beyond visible area
- Non-US keyboard layouts may require remapping Cmd+K

### Cmd+K vs Agent Mode — when to use each
| Use Cmd+K when... | Use Agent Mode when... |
|---|---|
| Single-file change | Multi-file changes |
| You can select precise code | Terminal commands needed |
| Transformation is well-defined | AI needs to read other files |
| Want fast response (1-3 sec) | Want autonomous write->test->fix loop |

### Apply model — how file editing works mechanically
- **Full-file rewrite model**, NOT diffs
- Fine-tuned from Deepseek Coder Instruct and Llama 3 families (Llama-3-70b-ft best performer)
- Uses **speculative edits**: existing source code as draft tokens, model only generates what changed
- ~9x speedup over vanilla inference, ~13x over standard Llama-3-70b
- Achieves ~1000 tokens/second (~3500 chars/second)
- Why not diffs: diffs give model fewer output tokens to think; models bad at counting line numbers; full files are in-distribution
- **Search/replace tool** for large files (Agent mode): finds location without reading entire file, changes only relevant portion — ~2x faster on large files

### Apply model limitations
- Unreliable on files >500 lines (some users report ~40% success rate)
- Known to fail on files >1,500-4,000 lines (silent failure, content truncation)
- May rewrite entire file (all green diff) instead of patching only changes
- Performance degrades with Gemini and o1 models
- Cursor is working on long-context training (up to 2500 lines), knowledge distillation to smaller models, on-policy RL

---

## 5. Bug Finding / Debug Mode

### Debug Mode — 5-step loop
1. **Explore & Hypothesize** — Agent explores files, builds context, generates multiple hypotheses
2. **Add Instrumentation** — Agent inserts log statements sending data to local debug server (Cursor extension)
3. **Reproduce the Bug** — Agent asks YOU to reproduce with specific steps (human in the loop)
4. **Analyze Logs** — Agent reviews collected runtime logs for root cause
5. **Targeted Fix & Cleanup** — Makes focused fix (often 2-3 lines), removes all instrumentation

### When to use Debug Mode
- Reproducible but not understandable from reading code
- Race conditions, timing-dependent issues
- Performance problems, memory leaks requiring runtime profiling
- Regressions (something used to work, now doesn't)
- Intermittent failures (e.g., "checkout fails 1 in 10 times")

### Terminal error detection
- When command fails in integrated terminal, press `Cmd+K` / `Ctrl+K`
- Cursor auto-detects error in terminal buffer, pre-fills context
- Offers targeted fix based on the actual error output
- Multi-step debugging: returns numbered sequences of runnable blocks
- Settings: toggle suggestions, auto-read error output, model selection, context lines for long build logs
- Works best on POSIX shells (bash, zsh, fish); PowerShell functional but may use Unix syntax

### Bugbot (automated PR review)
- Autonomous code review agent integrated with GitHub
- Finds: logic bugs, edge cases, security vulnerabilities, code quality issues
- Runs automatically on PR creation/update; manually via `cursor review` / `bugbot run` comment
- **Bugbot Autofix**: spawns cloud agents in VMs to automatically fix issues found
- Over 35% of Autofix changes merged into base PR
- Over 70% of flagged issues resolved before merge (up from 52% six months ago)
- During beta: reviewed 1M+ PRs, found 1.5M+ issues
- Used by Sentry, Discord, Rippling, Sierra, Maven, Decagon
- Pricing: $40/mo Bugbot Pro (200 PRs/mo); $40/user/mo for teams; 14-day free trial
- Setup: cursor.com/dashboard -> Integrations tab -> connect GitHub -> enable per repo

### Agent loop failures
- Pattern: makes change -> test fails -> reverts -> tries again -> loops
- Fix: press Escape, start new conversation with specific diagnosis
- Agent lost track after 3-4 correction attempts — context is polluted
- Fresh conversation with better prompt yields better results

---

## 6. Code Review

### Self-review
- Use `@Branch` to review all changes on current branch
- Ask: "review the changes on this branch", "what questions will reviewers have?"

### Agent review
- After Agent finishes task: click "Review -> Find Issues" for line-by-line analysis
- From Source Control tab: "Agent Review" to compare against main
- Inline comments in diff view

### PR review (v3.3 — May 2026)
- New **Reviews, Commits, and Changes tabs** — manage PRs from creation to merge in one place
- "Split Changes into PRs" — quick action using chat context to split work into logical PRs
- PR review experience unifies creation through merge workflow

### Security Review (Beta, Enterprise — May 2026)
- Security Reviewer: per-PR analysis
- Vulnerability Scanner: scheduled scans
- Integrates with existing SAST/SCA tools via MCP

### Bugbot PR review (see section 5)
- Automatic and on-demand PR review with fix suggestions

### Best-of-N review
- `/best-of-n` command runs the same task across multiple models in parallel
- Each model in isolated git worktree — compare all outputs side-by-side
- Pick the best result; Cursor may suggest which solution is strongest
- Best for: security-sensitive code, complex algorithms, UI components, refactoring

### AI commit messages
- Sparkle icon in commit message input auto-generates message from diff + repo history
- Agent Attribution: optionally adds "Made with Cursor" trailer; Enterprise admins can globally disable

### Cursor Blame (Enterprise only)
- Tracks which code is AI vs human: **Tab**, **Agent** (with model name), **Human**
- Line annotations: Command Palette -> "Cursor Blame: Toggle editor decorations" (author, commit, time, AI co-authorship)
- File blame view: right-click -> "Cursor Blame: Toggle file blame" (per-line attribution)
- Commit view: AI contribution breakdown by source, percentage, conversation summaries

---

## 7. UI/UX

### Core layout (VS Code foundation)
- **Editor** — central area, tabbed files
- **Sidebar** — left by default (File Explorer, Search, Source Control, Extensions)
- **Activity Bar** — thin vertical icon bar far left
- **Panel** — bottom (integrated terminal, debug console, output)
- **Status Bar** — bottommost (file/project info)

### Sidebar positioning
- `"workbench.sideBar.location": "left"` or `"right"` in settings.json
- Known bug: sidebar position can swap when switching between Editor/Agent mode; settings may not persist after restart (especially macOS)

### Chat panel positioning
- AI Chat can be **docked** on right, **floating** as separate panel, or in the **Agents Window**
- In Agent Tabs layout: multiple chats side-by-side or in grid
- Tiled layout (v3.1): Split view into panes, run multiple agents in parallel, compare outputs, drag agents into tiles
- Tab bar spans full width in maximized chat layouts
- "Scroll to bottom" button when agent panel content overflows

### Agents Window (Cursor 3.0, April 2026)
- Standalone interface or alongside IDE — `Cmd+Shift+P -> Agents Window`
- Run many agents in parallel across repos and environments
- Agent Tabs: multiple chats side-by-side or grid
- Workspaces: local folders, worktrees, cloud VMs, remote SSH

### Design Mode (Cursor 3.0+)
- Annotate and target UI elements directly in browser
- Shortcuts: `Cmd+Shift+D` toggle, `Shift+drag` select area, `Cmd+L` add element to chat, `Opt+click` add to input
- Keyboard navigation of element tree (v3.1): up/down/sideways to pick elements

### Panels — resize and rearrange
- Panels draggable and rearrangeable
- Terminal movable to bottom or side with adjustable height/width
- Limited freeform customization vs JetBrains — community requesting drag-and-drop flexible layouts

### Minimap (inherited from VS Code)
- Toggle: `View > Minimap` or `"editor.minimap.enabled": true/false`
- Full VS Code minimap settings (size, color, symbols, side, etc.)

### Breadcrumbs (inherited from VS Code)
- Toggle: `View > Breadcrumbs` or `"breadcrumbs.enabled": true/false`
- Shows file path hierarchy at top of editor

### Auto-scroll settings
- Auto-scroll Chat/Composer panel to bottom as new messages generate
- Collapse input box pills to save space
- Render pills instead of full code blocks (collapses Composer code blocks into pills)

### UI customization
- Themes available through Extensions marketplace
- Cursor-specific UI extends but does not replace VS Code theming
- Status bar indicator for model, Tab state, cursor position

---

## 8. Custom Model (Composer 2)

### Training pipeline
- Built on **Kimi K2.5** base (1.04T total / 32B active MoE model)
- **Phase 1 — Continued Pretraining**:
  - Large code-dominated data mix
  - Three sub-phases: bulk training at 32K seq len, long-context extension to 256K, short SFT on targeted coding tasks
  - NVIDIA B300s (Blackwell GPUs) with MXFP8 precision, AdamW optimizer
  - Multi-Token Prediction (MTP) layers trained from scratch for speculative decoding in production
- **Phase 2 — Large-Scale RL**:
  - Asynchronous RL using policy gradients (GRPO-like with modifications)
  - Rollouts sampled in realistic Cursor sessions with equivalent tools and environments
  - Single-epoch regime (no prompt trained on twice)
  - Full parameter updates via Adam
  - Modifications: no length standardization, no std deviation normalization of group advantages, k1=-log(r) for KL divergence (not biased k3), router replay for MoE layers

### Real-time RL (post-launch)
- Uses real user inference tokens as training signal
- Update frequency: as often as **every 5 hours**
- Billions of tokens from real user interactions
- Reward: edits persisted, follow-ups, user behavior
- Checkpoints validated against eval suites (CursorBench) before deployment
- On-policy: fast cycle keeps data fully or near-fully on-policy
- Composer 1.5 results: edit persistence +2.28%, dissatisfied follow-ups -3.13%, latency -10.3%

### Training innovations
- **Self-Summarization**: long rollouts chained via summaries for long-horizon tasks in limited context windows
- **Nonlinear Length Penalties**: concave-down penalty using hyperparameters k and q — efficient on easy tasks, deeper thinking on hard tasks
- **Behavioral Rewards**: auxiliary rewards for coding style, communication quality; penalties for poor tool-calling
- **Anyrun Environments**: sandboxed Firecracker VMs with full dev environments (including browsers), forking/snapshotting for mid-trajectory checkpointing

### Offline RL training infra
- Decoupled architecture across 3 GPU regions + 4 CPU regions
- Training workers run independently
- Inference workers (via Fireworks AI) generate rollouts
- Environment pods (via Anyrun) provide sandboxed VMs
- Weight sync every training step via delta-compressed S3 uploads
- Mid-rollout weight updates keep data near-on-policy

---

## 9. Settings

### General
| Setting | Description |
|---------|-------------|
| Import VS Code Settings | One-click migration of extensions, keybindings, settings, snippets from VS Code |
| AI Rules (User Rules) | Free-form text applying across all projects globally |
| Editor Settings | Standard VS Code–inherited editor behavior |
| Privacy Settings | Privacy Mode toggle |

### Models
- Model dropdown below AI input box — switch per conversation
- Supported providers: OpenAI (GPT models), Anthropic (Claude Opus 4.6, Sonnet 4.5), Google (Gemini 3 Pro — 1M+ context), Azure
- Custom API keys can be added
- Model recommendations:
  - Complex architecture, hard bugs: Claude Opus 4.6
  - Everyday coding, features: Claude Sonnet 4.5
  - Large context (10k+ lines): Gemini 3 Pro
  - Quick iterations, simple edits: GPT-5.2
- **Max Mode**: extended context; burns quota faster. Best for files >5,000 lines, cross-repo refactoring, complex dependency chains

### Features — Tab
- Enable/disable Cursor Tab autocomplete
- Advanced Features configuration for Tab

### Features — Chat & Composer
| Setting | Description |
|---------|-------------|
| Agent Stickiness | If ON, Normal vs Agent mode choice persists across new Composer conversations |
| Auto-Scroll to Bottom | Auto-scrolls Composer panel to bottom as messages generate |
| Auto-Apply to Files Outside Context | Allows Composer to auto-apply changes to files outside current context |
| Collapse Input Box Pills | Collapses pills/labels in Composer pane to save space |
| Render Pills Instead of Blocks | Collapses Composer code blocks into pills instead of full code blocks |
| Iterate on Lints [BETA] | If linter errors exist, Composer attempts to fix them iteratively |

### Features — Codebase Indexing
- Index new files by default (folders <10,000 files auto-indexed; manual "Compute Index" otherwise)
- Ignore files: add files to skip during indexing (beyond .gitignore)
- Git Graph Relationships: indexes git history for file relationships; code/commit messages local, metadata cloud-stored
- Clear Index Cache: Settings > Advanced > Clear Index Cache

### Features — Docs
- Configure documentation sources for `@Docs` context

### Features — Editor
| Setting | Description |
|---------|-------------|
| Chat/Edit Tooltip | Shows chat/edit tooltip near highlighted code |
| Auto Parse Links | Auto-resolves links and adds to context |
| Auto-Select for Cmd+K | Auto-selects regions for inline edit |
| Themed Diffs | Themed background colors for inline diffs |

### Features — Terminal
| Setting | Description |
|---------|-------------|
| Show Terminal Hover Hint | Shows hover hint with "Add to Chat" or "Debug with AI" commands |
| Use Preview Box | Preview box for Cmd+K output; if OFF, streams directly into terminal |

### YOLO Mode (Agent autonomy)
- Configures which terminal commands Agent runs without confirmation
- Allow list examples: `npm test`, `pnpm test`, `vitest`, `jest`, `pytest`, `tsc`, `npm run build`, `touch`, `mkdir`, `eslint`, `prettier`, `git status`, `git diff`
- Deny list examples: `rm -rf`, `sudo`, `ssh`, `curl`, `wget`, `git push`, `git commit`, `npm publish`, `deploy`

### Agent Auto-Run settings
- Sandbox mode, ask every time, or run everything
- On macOS: Seatbelt/sandbox-exec; Linux: Landlock + seccomp
- Reduces interruptions by 40% vs unsandboxed agents

### Performance settings
- `search.exclude`: exclude `node_modules`, `dist`, `build`, `.next`, `coverage`, `*.min.js`, lock files
- Multi-root workspaces: open specific packages instead of entire monorepos for faster indexing

### Workspace vs User settings
- **User settings**: `~/.cursor/settings.json` or Cursor Settings UI
- **Workspace settings**: `.vscode/settings.json` within project (VS Code standard)
- **Team settings** (Enterprise): centralized via admin dashboard

---

## 10. Terminal Integration

### AI terminal commands
- `Cmd+K` (Mac) / `Ctrl+K` (Win/Linux) at empty terminal prompt — describe command in natural language
- Cursor generates the shell command inline
- `Cmd+Enter` to run the generated command; `Escape` to accept without running

### Error detection in terminal
- Press `Cmd+K` after command failure — auto-detects error, pre-fills context
- Generates targeted fix based on buffer content
- Multi-step debugging: numbered sequence of runnable blocks
- Settings: toggle suggestions, auto-read error output, model selection, context lines configurable

### Agent terminal access
- Agent runs terminal commands autonomously (installing deps, running tests, builds)
- Terminal commands require approval by default (configurable in YOLO Mode)
- Each agent gets own terminal session
- "Add to Chat" hover hint on terminal for quick context injection
- "Debug with AI" hover hint for failed commands

### Terminal settings
- Show Terminal Hover Hint: on/off
- Use Preview Box: show Cmd+K output in preview box; if OFF, streams directly into terminal
- Works only in Cursor's integrated terminal (not external terminal emulators)
- Best on bash/zsh/fish; PowerShell functional but may use Unix syntax

### Cursor CLI
- Install: `curl https://cursor.com/install -fsS | bash`
- Brings Agent to terminal as standalone tool (Agent, Plan, Ask modes)
- Commands:
  ```bash
  cursor .                    # Open directory in Cursor
  cursor src/file.ts          # Open specific file
  cursor -g src/app.ts:42     # Jump to line 42
  cursor --diff file1 file2   # Compare two files
  cursor -a ../shared-lib     # Add folder to workspace
  ```

### Sandboxing
- macOS: Seatbelt/sandbox-exec
- Linux: Landlock + seccomp
- Windows: WSL2
- Blocks unauthorized file access and network activity by default
- Configurable: sandbox mode, ask every time, or run everything

---

## 11. Git Integration

### AI commit messages
- Stage changes -> click sparkle icon in commit input -> auto-generate from diff + repo history

### Merge conflict resolution
- When conflict occurs: "Resolve in Chat" button
- Agent analyzes both sides, proposes resolution

### PR review
- `@PR` (Diff with Main Branch) in Chat provides git diff for PR review
- Alternatively: pipe `git diff origin/master > diff.txt`, reference the file
- For remote PRs from others: use GitHub MCP server for deeper integration

### Cursor Blame (Enterprise)
- Line annotations: author, commit message, time, AI co-authorship (Tab vs Agent vs Human)
- File blame: per-line attribution via right-click
- Commit view: AI contribution breakdown by source, percentage, conversation summaries

### Source Control panel
- Standard VS Code Git UI (staging, unstaging, diff, branches)
- "Agent Review" option from Source Control tab (compare against main)

### Multi-workspace Git
- Each workspace has independent Agent and git context
- `/worktree` command creates isolated git worktrees with independent branches
- Parallel agents each get their own worktree — no cross-contamination

### Git graph relationships (indexing)
- Indexes git history to understand file relationships
- Code and commit messages stored locally
- Metadata (SHAs, changes, obfuscated filenames) stored in cloud

### Agent attribution in commits
- Optional "Made with Cursor" trailer on commits and PRs
- Enterprise admins can globally disable

---

## 12. Extensions & Plugins

### VS Code extension compatibility
- Cursor built on VS Code source — supports `.vsix` packaging format
- Uses **Open VSX registry** (not Microsoft Marketplace) for its own marketplace
- Most VS Code extensions work directly
- Some extensions may not be listed or have API divergence issues

### Installing missing VS Code extensions
- **Method A**: Download `.vsix` from VS Code Marketplace -> drag into Cursor Extensions pane
- **Method B**: Clone repo -> `npm install && npx vsce package` -> install via Command Palette "Extensions: Install from VSIX..."
- Note: manually installed extensions don't auto-update

### Cursor-native plugin system
- Distinct from VS Code extensions — bundles: Rules, Skills, Agents, Commands, MCP servers, Hooks
- Plugin manifest: `.cursor-plugin/plugin.json`
- Distributed as Git repos, reviewed before listing
- Marketplace partners: AWS, Amplitude, Figma, Linear, Stripe, Vercel
- Team Marketplace (Enterprise): admins create marketplace for plugins; Default Off/On/Required distribution
- Install: `/add-plugin`

---

## 13. Keyboard Shortcuts — Complete Reference

### Essential AI shortcuts (Cursor exclusive)
| Action | Mac | Win/Linux |
|--------|-----|-----------|
| Toggle Sidepanel / Open Composer | `Cmd+I` | `Ctrl+I` |
| Open Chat | `Cmd+L` | `Ctrl+L` |
| Inline Edit (selected code) | `Cmd+K` | `Ctrl+K` |
| Mode Menu | `Cmd+.` | `Ctrl+.` |
| Rotate between Agent modes | `Shift+Tab` | `Shift+Tab` |
| Loop between AI models | `Cmd+/` | `Ctrl+/` |
| Cursor Settings | `Cmd+Shift+J` | `Ctrl+Shift+J` |
| Toggle Voice Mode | `Cmd+Shift+Space` | `Ctrl+Shift+Space` |
| Toggle Design Mode | `Cmd+Shift+D` | `Ctrl+Shift+D` |
| Open Chat & Composer History | `Cmd+Alt+L` | `Ctrl+Alt+L` |

### Chat input shortcuts
| Action | Mac | Win/Linux |
|--------|-----|-----------|
| Submit | `Return` | `Enter` |
| Submit with Codebase Search | `Cmd+Return` | `Ctrl+Enter` |
| Force Send (while generating) | `Cmd+Return` | `Ctrl+Enter` |
| Queue Message | `Ctrl+Return` | `Ctrl+Enter` |
| Cancel Generation | `Cmd+Shift+Backspace` | `Ctrl+Shift+Backspace` |
| New Chat | `Cmd+N` or `Cmd+R` | `Ctrl+N` or `Ctrl+R` |
| New Chat Tab | `Cmd+T` | `Ctrl+T` |
| Previous Chat | `Cmd+[` | `Ctrl+[` |
| Next Chat | `Cmd+]` | `Ctrl+]` |
| Close Chat | `Cmd+W` | `Ctrl+W` |
| Unfocus Input Field | `Escape` | `Escape` |
| Model Toggle | `Cmd+Opt+/` | `Ctrl+Alt+/` |
| Quick Question (Inline) | `Cmd+Shift+L` | `Ctrl+Shift+L` |

### Code acceptance & diff review
| Action | Mac | Win/Linux |
|--------|-----|-----------|
| Accept Inline Autocomplete | `Tab` | `Tab` |
| Accept Next Word (partial) | `Cmd+Right` | `Ctrl+Right` |
| Accept All AI Changes | `Cmd+Enter` | `Ctrl+Enter` |
| Reject All AI Changes | `Cmd+Backspace` | `Ctrl+Backspace` |
| Next Diff | `Option+Down` | `Alt+Down` |
| Previous Diff | `Option+Up` | `Alt+Up` |

### Context & code selection
| Action | Mac | Win/Linux |
|--------|-----|-----------|
| @-Mentions dropdown | `@` (in chat input) | `@` |
| Slash Commands | `/` (in chat input) | `/` |
| Add Selection to Chat | `Cmd+Shift+L` | `Ctrl+Shift+L` |
| Add Selection to Edit (Cmd+K) | `Cmd+Shift+K` | `Ctrl+Shift+K` |
| Add Selection to New Chat | `Cmd+L` (with selection) | `Ctrl+L` |
| Toggle File Reading Strategies | `Cmd+M` | `Ctrl+M` |
| Clipboard as Context | `Cmd+V` (in chat) | `Ctrl+V` |
| Clipboard as Text Context | `Cmd+Shift+V` (in chat) | `Ctrl+Shift+V` |

### Terminal shortcuts
| Action | Mac | Win/Linux |
|--------|-----|-----------|
| Open Terminal AI Prompt | `Cmd+K` (in terminal) | `Ctrl+K` |
| Run Generated Command | `Cmd+Enter` | `Ctrl+Enter` |
| Accept Command | `Escape` | `Escape` |

### Core editor/navigation (VS Code foundation)
| Action | Mac | Win/Linux |
|--------|-----|-----------|
| Command Palette | `Cmd+Shift+P` | `Ctrl+Shift+P` |
| Quick Open File | `Cmd+P` | `Ctrl+P` |
| Global Text Search | `Cmd+Shift+F` | `Ctrl+Shift+F` |
| Toggle Terminal | `Ctrl+\`` | `Ctrl+\`` |
| Toggle Sidebar | `Cmd+B` | `Ctrl+B` |
| Go to Definition | `F12` | `F12` |
| Rename Symbol | `F2` | `F2` |
| Go to Line | `Ctrl+G` | `Ctrl+G` |
| Toggle Line Comment | `Cmd+/` | `Ctrl+/` |
| Save | `Cmd+S` | `Ctrl+S` |
| Open Settings | `Cmd+,` | `Ctrl+,` |

### Git & panels
| Action | Mac | Win/Linux |
|--------|-----|-----------|
| Open Source Control | `Cmd+Shift+G` | `Ctrl+Shift+G` |
| Open Problems Panel | `Cmd+Shift+M` | `Ctrl+Shift+M` |
| Open Extensions | `Cmd+Shift+X` | `Ctrl+Shift+X` |

### Voice input
| Action | Key |
|--------|-----|
| Toggle Voice Mode | `Cmd+Shift+Space` / `Ctrl+Shift+Space` |
| Record (press & hold) | `Ctrl+M` (v3.1+: batch STT for higher quality) |

### Customizing shortcuts
- `Cmd+R` then `Cmd+S` — open full keyboard shortcuts panel
- Or `Cmd+Shift+P` -> "Keyboard Shortcuts"
- All keybindings remappable (including AI features)

---

## 14. Privacy / Offline

### Privacy Mode
- Toggle: `Cmd+Shift+J` -> General -> Privacy Mode
- **Zero Data Retention (ZDR)**: code never stored by AI model providers, never used for training
- Legally binding ZDR agreements with all model providers (OpenAI, Anthropic, Google, xAI)
- Encryption: AES-256 at rest, TLS 1.2+ in transit
- Teams/Enterprise: admins can enforce org-wide (members cannot disable)

### Important caveats
- **If you use own API key**: ZDR does NOT apply — data follows your provider's privacy policy
- **Privacy Mode must be OFF** to use Cloud/Background Agents (repo temporarily stored on Cursor servers)
- Even with own API keys, requests still route through Cursor's backend for prompt building

### What data leaves the machine
- Code context (files/snippets from @mentions, open tabs, agent exploration)
- Prompt text (natural language instructions)
- Terminal output (when agent runs commands)
- Search results (matched code snippets from semantic search)

### What stays local
- Semantic search/indexing is built and queried locally
- Only matched snippets sent to server (not entire codebase)
- Embedding process: code chunks uploaded -> one-way mathematical vectors computed -> original plaintext discarded
- Only embeddings, obfuscated file paths, line numbers stored
- Actual code looked up locally when needed

### Offline mode
- **Not available** — Cursor has no true offline/air-gapped mode
- AI features require cloud-based model providers
- Even with local API keys, requests route through Cursor backend

### Additional security
- `.cursorignore` — block agent from reading sensitive files (`.env`, `*.pem`, `secrets/`)
- Terminal approval — every command requires manual approval by default
- Workspace Trust — restricted mode for untrusted repositories
- SSO & SCIM — SAML-based auth + automated provisioning (Enterprise)
- CMEK — Customer Managed Encryption Keys for embeddings and Cloud Agent data (Enterprise)
- MDM enforcement — prevent personal account login on corporate devices

---

## 15. Pricing Tiers

### Individual plans
| Plan | Monthly | Annual | Credits | Key Features |
|------|---------|--------|---------|-------------|
| **Hobby** | $0 | $0 | Limited | Limited Tab, limited Agent; 1-week Pro trial; no credit card |
| **Pro** | $20/mo | ~$192/yr ($16/mo) | $20 | Unlimited Tab, unlimited Auto mode, full Agent, Composer, Background Agents, custom API keys |
| **Pro+** | $60/mo | ~$576/yr ($48/mo) | $60 (3x) | Everything in Pro + 3x usage multiplier on all frontier models |
| **Ultra** | $200/mo | ~$1,920/yr ($160/mo) | $200 (10x) | Everything Pro+ + 20x multiplier, priority new feature access, parallel Background Agents |

### Business plans
| Plan | Price | Features |
|------|-------|----------|
| **Teams** | $40/user/mo (~$32 user/mo annual) | Teams 3+; centralized billing, shared rules/commands, SSO (SAML/OIDC), admin dashboard, usage analytics, org-wide privacy mode, RBAC |
| **Enterprise** | Custom | Pooled org-wide usage, invoice/PO billing, SCIM, audit logs, granular admin & model controls, dedicated support, CMEK, MDM enforcement, Security Review |

### Credit system
- **Auto mode is unlimited** on all paid plans — Cursor picks best model, zero credit cost
- Credits deplete only when manually selecting a specific frontier model
- Credit pools reset monthly; no rollover
- On-demand overages billed in arrears if pool exceeded (cap configurable)

### Bugbot pricing (separate)
- Bugbot Pro: $40/mo (up to 200 PRs/mo)
- Bugbot Teams: $40/user/mo (only PR authors count as seats)
- 14-day free trial

### Scale
- Cursor has 1M+ paying subscribers, $2B+ annualized revenue (early 2026)

---

## 16. Edge Cases & Limitations

### Monorepo handling
- Cursor sometimes bypasses correct folder structure (creates files at root instead of inside `apps/` or `packages/`)
- Emoji in `code-workspace` folder names can cause path resolution bugs
- Mitigations: scope edits tightly to specific folders; use `.cursorignore` and `.mdc` rules; let build tools handle correctness; commit before big AI edits
- Multi-root workspaces: open specific packages, not entire monorepo

### Large file limits
- Apply model unreliable on files >500 lines; fails entirely on files >1,500-4,000 lines
- May silently do nothing, truncate content, or rewrite entire file
- 6,000-line `server.js` can be ~60K tokens — confuses edit model
- Workarounds: use search/replace tool (Agent mode); extract section to temp file; modularize aggressively (files <500-700 lines); diff view approach

### Context limits
- "Context is too large" error more aggressive in recent versions
- Middleware imposes stricter limits than underlying LLM capabilities
- Context pollution near limit causes hallucinations
- Mitigations: keep chats short; long context checkbox (expect messier output); target specific modules; use Search panel to locate code first

### Error recovery
| Failure | Recovery |
|---------|----------|
| Apply silently fails | Use search/replace tool or extract section to temp file |
| Agent loops (same fix repeatedly) | Escape -> new conversation with specific diagnosis |
| Too many files changed | Restore checkpoint, add "Only modify X" constraints |
| Import errors after refactoring | Run `tsc`, let agent fix import paths only |
| Agent creates files in wrong folder | Add explicit `.cursorignore` and `.mdc` rules; avoid emoji in workspace names |
| Import path not found | Use relative paths instead of package aliases |
| Changes work locally, fail in CI | Capture error, inspect CI/CD environment |
| Generated code overwritten | Edit source templates, not generated files |

### Checkpoint limitations
- Session-local only — disappear on Cursor restart
- Composer/Agent changes only (not Tab completions)
- No terminal side effect rollback
- All-or-nothing restore (reverts *all* changes from that prompt including manual edits)
- Forum reports of restore failures — **never rely on checkpoints as sole safety net**

### Other known issues
- Sidebar position bug: swaps when switching modes; settings may not persist after restart (macOS)
- Non-US keyboard layouts: Cmd+K may need remapping
- Model-specific degradation: Gemini and o1 models perform worse for apply operations

---

## Additional: Parallel Agents & Advanced Workflows

### Parallel agents
- Agents Window: spawn multiple agents simultaneously across repos/worktrees/cloud/SSH
- Agent Tabs: view multiple chats side-by-side or grid
- Tiled layout (v3.1): split view into panes; drag agents into tiles; compare outputs
- `/multitask` (v3.2): break requests into chunks, tackle with fleet of parallel subagents
- Parallel Plan Execution (v3.3): "Build in Parallel" — agents identify independent tasks, run simultaneously
- Up to 8 agents in parallel (Cursor 2.0); practical ceiling ~4 for most laptops

### /worktree command
- Creates isolated git worktree with independent branch
- Shares `.git` history — no cloning, no duplicating, no cross-contamination
- Review diff, merge what you want, worktree cleaned up when done

### /best-of-n command
- Runs same task across multiple models simultaneously
- Each in isolated git worktree
- Compare all outputs side-by-side; pick best; Cursor may suggest strongest solution

### Cloud Agents (formerly Background Agents)
- Run in isolated cloud VMs, not on local machine
- Clone repo from GitHub/GitLab into fresh VM
- Work on separate branch, push changes directly
- Computer Use: control remote desktop and browser with mouse/keyboard
- Artifacts: screenshots, videos, logs attached to PRs
- Auto-fix CI failures on GitHub Actions (Teams)
- Launch from: Desktop, Web (cursor.com/agents), Slack (@cursor), GitHub (@cursor comment), Linear (@cursor), API
- Available on all paid plans; charged at API pricing; always Max Mode (no toggle)

### Cloud Agent Handoff (v3.2+)
- Start locally -> push to cloud to keep running while offline
- Pull cloud sessions back local for editing
- Move to cloud does NOT snapshot dirty files — commit or stash first

### Multi-root workspaces
- Single agent targets multiple folders (frontend, backend, shared libraries)
- No retargeting needed for cross-repo changes

### Context Usage Breakdown (v3.3)
- See stats on agent's context usage: rules, skills, MCPs, subagents consumption

### Productivity impact (reported)
- PR merge rate: +39% (company-level)
- Daily PR throughput: ~60% more merged PRs/week (power users)
- Autonomous task completion rate: +21% higher vs sequential

---

## Additional: ACP / JetBrains Integration

### Agent Client Protocol
- Open standard for connecting AI coding agents to IDEs (developed by JetBrains + Zed)
- Analogous to LSP — standardizes agent-IDE communication

### Cursor in JetBrains
- Cursor's agent runs as ACP server; JetBrains IDE is ACP client
- Install from JetBrains AI Chat -> "Add Agent from Registry" -> Cursor
- Requires paid Cursor plan; no JetBrains AI subscription needed
- Authenticate with existing Cursor account
- Full model selection, semantic search, file editing, terminal commands in JetBrains
- Modes: agent (full tools), plan (read-only), ask (Q&A)
- Same usage-based pricing as existing Cursor subscription

### ACP tech details (via CLI)
- Transport: stdio; Envelope: JSON-RPC 2.0; Framing: newline-delimited JSON
- Flow: initialize -> authenticate -> session/new -> session/prompt -> handle streaming notifications -> handle permission requests

### Cursor's ACP extension methods
| Method | Type | Purpose |
|--------|------|---------|
| `cursor/ask_question` | Blocking | Multi-choice user questions |
| `cursor/create_plan` | Blocking | Explicit plan approval request |
| `cursor/update_todos` | Notification | Todo list state updates |
| `cursor/task` | Notification | Subagent task notifications |
| `cursor/generate_image` | Notification | Generated image output |

---

## Additional: Slash Commands

| Command | Purpose |
|---------|---------|
| `/create-rule` | Create a new .cursor rule file |
| `/add-plugin` | Install plugin from marketplace |
| `/worktree` | Create isolated git worktree |
| `/best-of-n` | Run same task across multiple models |
| `/multitask` | Break task into parallel subagents |
| `/debug` | Root-cause analysis with instrumentation |
| `<skill-name>` | Invoke a skill (dynamic, defined in `.cursor/skills/`) |

---

## Additional: Checkpoints in Detail

### How they work
- Automatically created before every significant set of Agent changes
- Capture state of all modified files
- Visible as markers in chat/composer conversation timeline
- Restore: click "Restore" button next to checkpoint marker, or + button on hover

### Limits
- **Session-local** — disappear on Cursor restart (not persisted)
- **Agent/Composer only** — not Tab completions or manual edits
- **No terminal undo** — won't roll back `npm install`, db migrations
- **All-or-nothing** — restores *every* file from that prompt, including manual edits made afterward
- Forum report of complete restore failure (user lost 2,000 lines)

### Recommended safety strategy
1. **Git** = primary (commit before every Agent prompt, commit after success)
2. **Checkpoints** = quick in-session experimentation
3. **Local History** = `Cmd+Shift+P` -> "Local History: Find Entry to Restore" (inherited from VS Code)
4. **Timeline** = bottom of Explorer panel (file save history)
5. **Backups** = `~/.config/Cursor/Backups/` (crash recovery)
