# Windsurf & Augment Code: Feature Catalog

## Part A: Windsurf (by Codeium)

### 1. Cascade Agent

- **Type**: Full agentic AI, chat-based, multi-file editing
- **Modes**: Code (all tools), Plan (structured planning first), Ask (read-only/search only)
- **Context Assembly Pipeline** (5 layers, in order):
  1. Rules (global → workspace `.windsurfrules`)
  2. Memories (persisted facts from prior sessions)
  3. Open Files (active file highest weight; other tabs included)
  4. Codebase Retrieval (M-Query semantic RAG against embedding index)
  5. Recent Actions (IDE telemetry: file saves, test runs, navigation history)
- **RAG Indexing**: Each file/function → 768-dim embeddings; **M-Query** (proprietary) improves precision over cosine similarity, reduces hallucinations vs naive RAG
- **Fast Context / SWE-grep Subagent**: Specialized retrieval agent, up to 20x faster than agentic search; two variants:
  - SWE-grep (high-intelligence, complex retrieval)
  - SWE-grep-mini (ultra-fast, 2,800+ tokens/sec)
  - Executes up to 8 parallel tool calls per turn over max 4 turns
  - Uses restricted tools (grep, read, glob) to prevent context pollution
- **Agent Tools**: Codebase Search, File Navigation, File Editing, Terminal Execution, Web Search, URL Read, View Chunk
- **Tool Limit**: Up to 25 tool calls per prompt; type `continue` if trajectory stops
- **Credit Cost**: Free tier includes SWE-1.5 model; premium models consume quota
- **Parallel Agents** (Wave 13): Up to 5 Cascade agents simultaneously, each in isolated git worktree; side-by-side diff review and merge workflow
- **Worktree Isolation**: Each agent runs on its own git branch in a separate working directory; zero branch conflicts during execution; conflicts deferred to deliberate merge step

### 2. Flow State

- **What it is**: Basic autocomplete mode; grey ghost-text appears as you type; press `Tab` to accept
- **Trigger**: Reactive -- waits for you to type, then completes obvious patterns
- **Best for**: Single-line completions, boilerplate, completing known patterns (function calls, variable names, common idioms)
- **Architecture**: Separate pipeline from Cascade, optimized for latency (<100ms); light context: cursor position, current file, nearby symbols, recent edits
- **vs Cascade**: Flow = tab autocomplete; Cascade = multi-file agentic refactors. Different pipelines, different latency budgets, different context depth

### 3. Supercomplete

- **What it is**: Proactive, intent-aware completion engine built on Cascade's context system; predicts intent before you type
- **Three signals** (that standard autocomplete ignores):
  1. **Edit Trajectory** -- tracks last 30-90 seconds of edits; if you renamed `userId` → `accountId` twice, it pre-stages the next occurrence
  2. **Cursor Intent** -- monitors cursor movement; navigating from function definition to call sites primes suggestions
  3. **AST-Aware Scope** -- parses lightweight AST; understands that TypeScript interface changes have downstream effects
- **Output**: Multi-line, diff-style previews across the file (not just single-line ghost text)
- **Latency**: 300-700ms (slower than Flow, deeper reasoning)
- **Pricing**: Included in Pro ($20/mo); free tier has usage limits

### 4. Codeium's Index (Context Engine)

- **Type**: Semantic RAG embedding index, not fine-tuning based
- **Embeddings**: 768-dimension vectors per file/function capturing semantic meaning
- **Indexing Pipeline**: Embedding Generation → Query-Time Retrieval → M-Query (proprietary similarity search)
- **What it indexes**: All files in workspace except those in `.codeiumignore` (uses `.gitignore` syntax); excludes `node_modules/`, `dist/`, `*.env` by default
- **Context limit**: ~200K tokens (varies by plan: Free = standard, Pro = expanded)
- **Update model**: Real-time as you edit; file saves trigger re-indexing of changed files only (incremental)
- **Plans**: Free = local indexing; Pro = expanded context lengths; Teams/Enterprise = remote repository indexing, Google Docs knowledge base

### 5. Windsurf Terminal (Cascade Terminal)

- **Dedicated Shell**: Wave 13+ introduced a dedicated `zsh` shell for Cascade, separate from your default terminal; reads your `.zshrc` for aliases/env vars
- **Execution Modes** (configurable in Windsurf Settings):
  | Level | Behavior |
  |---|---|
  | Disabled (Manual) | All commands require manual approval |
  | Allowlist Only | Only allow-listed commands auto-execute; rest require approval |
  | Auto (Semi-auto) | Cascade uses judgment to determine safe commands; risky commands still demand approval (premium models only) |
  | Turbo | All commands auto-execute immediately except deny-listed ones |
- **Allow/Deny Lists**: Configure per-command; deny list takes precedence; Teams/Enterprise admins can set org-wide max auto-execution level
- **Inline AI (Cmd+I / Ctrl+I)**: Opens inline chat in terminal; describe CLI command in natural language; AI generates proper syntax; accept/reject/follow-up
- **@-mention Terminals**: Chat with Cascade about active terminals
- **Send selection**: Select terminal output → `Cmd+L` / `Ctrl+L` to send as context to Cascade

### 6. Multi-Agent (Wave 13)

- **Parallel agents**: Up to 5 Cascade agents simultaneously
- **Isolation**: Each runs in isolated git worktree (separate branch, same repo)
- **Example workflow**:
  - Agent A: Refactor auth module
  - Agent B: Write unit tests for payment service
  - Agent C: Update API docs
  - Agent D: Migrate DB schemas
  - Agent E: Fix sprint backlog bugs
- **Review**: Multi-pane Cascade view shows all agents' progress; review diffs side-by-side; approve/merge only passing branches
- **Merge**: Manually merge approved branches (e.g., `git merge feat-user-profiles test-auth-module refactor-utils`)
- **Human role shifting**: From writer → reviewer (decompose tasks, delegate, review outputs, merge)
- **Practical ceiling**: 5-7 concurrent agents on a laptop before rate limits, merge complexity, and human review bandwidth become bottlenecks

### 7. Arena Mode

- **Concept**: Blind A/B model comparison on real coding tasks in your own codebase
- **Flow**:
  1. Submit a prompt in Arena Mode
  2. Windsurf silently routes to two different models (identities hidden)
  3. Both execute in isolated git worktrees
  4. Review results side-by-side, vote Model A / Model B / Tie
  5. Model identities revealed after vote
  6. Vote feeds into crowdsourced public leaderboard
- **Leaderboard**: windsurf.com/leaderboard; Elo rating system; personal + global leaderboards
- **Battle Groups** (randomized model pairs):
  | Group | Description |
  |---|---|
  | Frontier | Top reasoning models (GPT 5.2, Claude Opus/Sonnet 4.5, Gemini 3 Pro) |
  | Fast | Speed-optimized (SWE 1.5, Claude Haiku, GPT-5.3-Codex-Spark) |
  | Hybrid | Mix of frontier + fast |
- **Cost**: Two models = double credit consumption; flat $20/mo Pro quota covers usage
- **Limitations**: Git-initialized workspaces only; random matchups (cannot pick specific models); doubles compute time; self-selected user base biases results
- **Philosophy**: "Your codebase is the benchmark" -- addresses training data contamination in HumanEval/SWE-bench; blind testing eliminates brand loyalty and benchmark anchoring bias

### 8. Plan Mode

- **How it works**: Cascade explores codebase → asks clarifying questions → provides multiple options via interactive interface → outputs detailed plan as external Markdown file
- **Plan storage**: `~/.windsurf/plans`; can be @-mentioned later
- **Implementation**: Click "Implement" on plan file to switch to Code mode and begin work
- **Goal**: Reduce wasted tokens on misaligned outputs; surface assumptions before code touches files
- **Status**: Beta (as of Wave 13)

### 9. Cascade Memories

- **Creation**: Auto-generated by Cascade during conversations when it encounters useful context; also manually creatable ("create a memory of...")
- **Storage**: `~/.codeium/windsurf/memories/` (local machine only)
- **Scope**: Per-workspace -- not shared across workspaces
- **Sharing**: Not version-controlled, not team-shared; lives only on local machine
- **Retrieval**: Automatically included when Cascade deems them relevant
- **Credit cost**: None -- memories do not consume credits
- **Key caveat**: Windsurf recommends using Rules or AGENTS.md over Memories for anything you want reliably reused or team-shared

### 10. Rules System

- **Global Rules**: `~/.codeium/windsurf/memories/global_rules.md` -- applies to all workspaces, always on, max 6,000 characters
- **Workspace Rules**: `.windsurf/rules/*.md` -- one file per rule; max 12,000 characters per file
- **System Rules** (Enterprise): OS-specific (`/etc/windsurf/rules/`); deployed by IT, read-only
- **Activation Modes** (YAML frontmatter `trigger` field):
  | Trigger | Behavior |
  |---|---|
  | `always_on` | Full rule included in every system prompt |
  | `model_decision` | Only description shown; Cascade reads full rule when relevant |
  | `glob` | Applied when Cascade touches files matching `globs` pattern |
  | `manual` | Activated only by typing `@rule-name` in Cascade input box |
- **Auto-discovery**: Windsurf scans `.windsurf/rules` in current workspace, sub-directories, and parent directories up to git root
- **AGENTS.md alternative**: Root-level → always-on (version-controlled); subdirectory → auto-glob for that directory; no frontmatter required
- **Recommendation**: Use Rules/AGENTS.md for reliable reuse and team sharing; Memories for transient personal context

### 11. Web Search + MCP

- **Web Search Pipeline** (3-tool architecture):
  1. Web Search → LLM synthesizes intent into query, returns URL list
  2. URL Read → Local scrape; short pages ingested directly, long pages chunked with table-of-contents outline
  3. View Chunk → Cascade reads relevant chunks (coalesces adjacent ones)
- **Manual triggers**: `@web` (general search), `@docs` (documentation), `@codebase` (broader semantic search), `@filename.ts` (specific file)
- **Auto-detection**: Cascade searches automatically when it detects need for current info
- **Token efficiency**: Only retrieves necessary information to optimize credit usage
- **MCP Support**: Stdio & HTTP transport; one-click plugin store + manual `mcp_config.json` at `~/.codeium/windsurf/`; blue-checkmark "official" plugins; 5,000+ servers indexed by MCPFind
- **MCP servers**: Run per-workspace; same JSON config format as Cursor (portable between editors)

### 12. Pricing

| Plan | Price | What You Get |
|---|---|---|
| Free | $0/mo | 25 prompt credits/mo, unlimited Fast Tab, Command, App Previews, 1 App Deploy/day, SWE-1.5 free model |
| Pro | $15/mo (grandfathered) / **$20/mo** (new) | 500 credits/mo (replaced by daily+weekly quota system March 2026), premium models, 5 App Deploys/day, optional zero data retention |
| Teams | $30-40/user/mo | 500 credits/user, centralized billing, admin dashboard, priority support |
| Enterprise | $60/user/mo | 1,000 credits/user, SSO, RBAC, dedicated account mgmt, hybrid deployment |
| Student | **$10/mo** | Pro features |

- **Quota System** (March 2026): Credit system replaced by daily + weekly allowances; extra usage billed at API list prices; free models (SWE-1.5) do not count against quota; grandfathered $15/mo Pro indefinitely

### 13. Models Available

| Model | Tier | Notes |
|---|---|---|
| **SWE-1.5** | Free default | Windsurf's own agentic coding model; "near Claude 4.5-level at 13x speed"; succeeded by SWE-1.6 |
| **SWE-1.5 Fast** | Free (priority for paying) | Ultra-fast variant |
| **Claude 4 Opus** | BYOK only | Bring your own Anthropic API key; bypasses Windsurf credits entirely |
| **Claude 4 Opus (Thinking)** | BYOK | Extended reasoning variant |
| **Claude 4 Sonnet** | BYOK | BYOK; token-based billing (less predictable cost) |
| **Claude 4 Sonnet (Thinking)** | BYOK | Thinking variant |
| **GPT-5.x** | Available | Referenced as available via arena/battle groups, not as first-class premium option |

- **BYOK**: Bring Your Own Key for Anthropic models; token-based billing (input + output tokens) instead of flat credit consumption

### 14. IDE Integration

- **Type**: Standalone VS Code fork (not a plugin), by Codeium
- **Installers**: `.dmg` (macOS Intel + Apple Silicon), `.exe` (Windows), `.deb`/`.rpm` (Linux)
- **Extension marketplace**: Open VSX Registry (open-vsx.org), NOT Microsoft Marketplace; ~90%+ VS Code extensions work; incompatible: GitHub Copilot, Cursor-specific extensions, some debuggers, VS Code Remote-SSH
- **Coexistence**: Can run alongside VS Code and Cursor without conflicts; `windsurf` CLI command to launch
- **Migration**: Import settings, extensions, keybindings from VS Code or Cursor on first launch (or via Command Palette)
- **Recommended extensions**: ESLint, Prettier, GitLens, Error Lens, Python, Ruff, Pyright
- **Minimum OS**: macOS Yosemite 10.10+, Windows 10+, Ubuntu 20.04+

### 15. Unique Features

- **One-Click App Deploys**: Deploy from Cascade prompt; handles framework analysis, build, provides public URL at `.windsurf.build`
- **Live Preview with Select-and-Fix**: Click elements in preview, send back to Cascade as context
- **Arena Mode**: Only blind A/B model comparison in any commercial IDE
- **SWE-1.x Custom Models**: In-house models tuned for agentic workflows, optimized for speed
- **40+ IDE Plugins**: Including JetBrains, Vim, Neovim (through Codeium extension, not the fork)
- **Dedicated Cascade Terminal**: Separate zsh shell just for AI agent commands
- **5 Parallel Agents + Worktree Isolation**: First among major AI IDEs to ship production-grade multi-agent with git worktree isolation
- **Plan Tiers for Indexing**: Hierarchical context depth by plan (Free = standard, Pro = expanded, Enterprise = remote repos + docs)

---

## Part B: Augment Code

### 1. Context Engine

- **Core Thesis**: Context quality over token quantity; Context Engine = database (indexed, ranked, compressed), vs context window = RAM (ephemeral, finite, blind to relevance)
- **Scale claim**: 500M-token enterprise monorepos; 128K window captures only 0.025% of codebase; Augment targets 90%+ hit rates at ~1/10th token consumption
- **What it indexes**:
  - Source files (all languages)
  - Commit history (why changes happened, not just what)
  - Codebase patterns (how the team actually builds)
  - Dependency graphs (cross-repo, cross-service)
  - Documentation, runbooks, design decisions
  - External sources via MCP (Linear, Jira, Confluence, Notion)
- **Custom Embedding Models**: Rejects generic embedding APIs (OpenAI, Pinecone); owns the training pipeline for code-specific embeddings; trained for "helpfulness" not just "relevance" (e.g., knows LLM already knows PyTorch -- don't retrieve it)
- **Indexing Architecture** (5-component pipeline):
  | Component | Role |
  |---|---|
  | Source | Connects to GitHub, GitLab, BitBucket, websites |
  | Indexer | Discovers files, chunks, sends to Context Engine for embedding |
  | Store | Persists index state (local FS or S3) for incremental updates |
  | Context Engine | Semantic search backend; stores embeddings, handles queries |
  | Client | CLI (`ctxc`), MCP server, or custom application |
- **Incremental Updates**: Hash → Diff → Index only changed files; unchanged skipped; deleted removed
- **Cloud Infrastructure**: Google Cloud -- PubSub (job queuing, separate real-time vs bulk queues), BigTable (indexed storage), AI Hypercomputer (GPU inference), custom inference stack
- **Scaling**: Thousands of files/second; near-instant branch switches; 100+ file changes from git ops, search-and-replace, auto-formatting handled in seconds
- **RAM Optimization**: Overlapping indices between users on same tenant are shared; only divergent portions (different branches) duplicated; custom embedding search implementation (not third-party vector DB) enables sharing + Proof of Possession enforcement
- **Per-Developer Index**: Not one index per repo -- **one per developer**; prevents branch-bleed (retrieving from `main` while on feature branch → hallucinated functions)
- **Context Limit**: Up to 200K tokens per interaction
- **Codebase Scale**: 400,000+ files supported
- **MCP Exposure**: Context Engine available as MCP server for any AI coding tool (Claude Code, Cursor, Zed); local mode (real-time working dir) + remote mode (multi-repo default branches); ~40-70 credits per MCP query; 1,000 free queries during launch

### 2. Augment Agent (IDE)

- **Plan-Then-Execute**: Analyzes request against codebase → creates structured plan (actionable task list) → reviews plan with user → executes step-by-step
- **Modes**:
  | Mode | Description |
  |---|---|
  | Code | Full agent with all tools; plans and implements |
  | Ask | Read-only; retrieval and non-editing tools only; never modifies files |
  | Auto | Agent works independently without pausing for approval; plans, implements, iterates; can interrupt anytime to redirect |
- **Memories** (see section 4)
- **Prompt Enhancer** (sparkle button): Type quick/incomplete prompt → Augment expands with codebase references (file paths, naming conventions, error-handling patterns, test conventions); review enhanced prompt before sending
- **Checkpoints**: Automatically saved snapshots at each plan step; one-click rollback to any checkpoint; agent continues working while you review diffs; non-blocking -- revert independently of agent progress
- **Parallel Tool Calls**: Agent can make multiple tool calls in parallel within a single turn
- **Terminal Execution**: Runs commands directly (npm install, dev servers, git ops); sees output; reacts to errors
- **Multi-Model**: Switch between Claude (Opus 4.6/4.7, Sonnet 4.5), GPT (5.1), and other frontier models
- **Multi-Modal**: Accepts screenshots, Figma files, UI mockups as input
- **IDE Support**: VS Code + JetBrains (native plugins)

### 3. Auggie CLI

- **Install**: `npm install -g @augmentcode/auggie`; requires Node 22+
- **Modes**:
  | Mode | Flag | Description |
  |---|---|---|
  | Interactive | `auggie` (no flags) | Full-screen TUI; real-time streaming; visual progress; tool-call visibility; slash commands; ongoing conversation |
  | Print | `--print` / `-p` | Single-shot execution to stdout; exits immediately; no prompting |
  | Quiet | `--print --quiet` | Only final assistant message; structured output; no intermediate steps |
  | Compact | `--print --compact` | Compact streaming within print mode |
  | Ask | `--ask` / `-a` | Retrieval + non-editing tools only |
  | Headless | `--print` in CI | Drop into GitHub Actions, Jenkins, any CI system |
  | MCP Server | `--mcp` | Expose `codebase-retrieval` tool to external AI clients |
  | ACP | `--acp` | Run as ACP agent for compatible clients (Zed, Neovim, Emacs, JetBrains) |
- **Sub-Agents**: Delegate specialized tasks to focused agents in isolated contexts (security audits, test writing, data analysis)
- **Parallel Agents**: Run multiple agents simultaneously (e.g., refactor frontend while tests update in another session)
- **Sessions**: Resumable across terminal sessions; `--continue`, `--resume`, `session list/resume/delete/share`; export as markdown; shareable links
- **Automation**:
  - Unix-pipeline compatible: `cat build.log | auggie --print --quiet "Summarize"`
  - Queued instructions: `--queue` flag
  - Bounded runs: `--max-turns`
  - Official GitHub Actions: `augmentcode/describe-pr`, `augmentcode/review-pr`
  - `/github-workflow` wizard
- **Service Accounts**: Non-human identities with dedicated API tokens for CI/CD (Enterprise)
- **Custom Commands**: Reusable markdown templates in `.augment/commands/`
- **Tool Permissions**: Granular: `--remove-tool`, `--permission`, persistent tool management
- **Output**: `--output-format json` for structured automation; `--show-credits` summary
- **Prompt Enhancer**: `--enhance-prompt` flag
- **Multi-Model**: `--model` flag or `/model` slash command
- **Status**: Beta (as of 2025)

### 4. Augment's Memory System

- **Creation**: Agent proposes memory when it detects something worth persisting (long-term goal, debugging decision, relevant system detail)
- **Memory Review Flow**:
  ```
  Conversation → Agent proposes memory (draft)
  → Turn Summary shows "1 Pending Memory"
  → User clicks to review inside Chat
  → Options: Approve / Edit (curate before saving) / Discard
  → Agent loop continues with curated memory context
  ```
- **Philosophy**: Hidden, uncurated memories erode trust; Memory Review keeps curation in-chat where context lives; lightweight enough for flow, powerful enough for quality
- **Promotion**: Can promote high-quality memories to workspace Rules and share with team
- **Persistence**: Cross-session and cross-conversation
- **Before Memory Review**: Only way to audit memories was opening a raw memory file (now in-chat)

### 5. Multi-Repo Support

- **Context Engine MCP** (local mode): Auggie CLI as MCP server; indexes working directory in real-time; best for active development
- **Context Engine MCP** (remote mode): Augment-hosted at `api.augmentcode.com/mcp`; integrates with Augment GitHub App; indexes multiple repositories' default branches; best for cross-repo context
- **Context Connectors**: Index from GitHub, GitLab, Bitbucket, documentation sites, internal wikis, custom sources via extensible SDK
- **Auto-Sync**: CI/CD hooks push-update on commit
- **Dependency Graphs**: Cross-repo, cross-service dependency awareness; agent understands which repos/services are coupled

### 6. Integrations (Native OAuth)

| Integration | Capabilities |
|---|---|
| GitHub | Pull Issues, make code changes, open PRs, check CI status |
| Linear | Read, update, comment on, resolve Linear issues |
| Jira | View assigned tickets, create/update tickets, change statuses |
| Confluence | Query documentation, update pages, keep knowledge base current |
| Notion | Search & retrieve docs, meeting notes, specs (read-only currently) |
| Glean | Enterprise-only early access: search internal data sources |
| Sentry | Search issues/errors/traces/logs; create RCAs; AI-generated fixes |
| Stripe | Real-time payment events, refunds, subscriptions; OAuth MCP support |
| MCP | Extensible to 100+ additional tools |

- **Auto-detection**: Agent detects when an integration is relevant from conversation context
- **Explicit**: Mention service name in request to force usage

### 7. ACP Support

- **Protocol**: Agent Client Protocol (ACP) -- open protocol for any compatible client to communicate with Auggie
- **Supported clients**:
  | Client | Method |
  |---|---|
  | Zed | Install Auggie extension from Extensions panel (requires Zed v0.211.6+); or manual `agent_servers` config with `auggie --acp` |
  | Neovim | Via ACP-compatible plugins: Avante.nvim, Agentic.nvim, CodeCompanion.nvim |
  | Emacs | Via agent-shell.el or other ACP-compatible plugins |
  | JetBrains | Native Augment plugin already on Marketplace; ACP-based config documented with `auggie --acp` args |
  | Terminal | `auggie --acp` from any terminal |
- **Significance**: Augment is available everywhere -- not locked to VS Code

### 8. Enterprise Features

- **Deployment Options**: SaaS, VPC (AWS VPC; all API calls, model inference, vector indexing behind your subnets), On-Premise (containers in your data center), Air-Gapped (completely offline; defense/healthcare/classified)
- **Context Engine**: Full functionality across ALL deployment modes including air-gapped
- **Certifications**: SOC 2 Type II (attested); ISO/IEC 42001:2023 (first AI coding assistant to achieve this); GDPR; CCPA
- **Data Protection**: Customer-Managed Encryption Keys (CMEK) -- customer holds symmetric keys, revoking instantly cuts Augment access; non-extractable architecture; proof-of-possession API
- **Training Policy**: Zero training on customer proprietary data or code; legally enforceable; indemnification clause
- **Identity**: SSO/MFA; Okta, Azure AD, AWS SSO; role-scoped context engine with short-lived containers inheriting only granted permissions; multi-tenant isolation with namespace sharding
- **Audit**: Immutable, timestamped logging of all interactions; 72-hour internal / 5-day customer security incident notification; secrets scrubbing before reaching models; public Trust Center
- **Best fit**: Defense contractors, healthcare (HIPAA), financial services (multi-jurisdictional compliance), multi-cloud enterprises

### 9. Pricing

| Plan | Price | Monthly Credits | Auto Top-Up |
|---|---|---|---|
| Trial | **$0** (with valid credit card) | 30,000 | -- |
| Indie | **$20/mo** | 40,000 | $15 per 24k credits |
| Standard | **$60/mo** | 130,000 | $15 per 24k credits |
| Max | **$200/mo** | 450,000 | $15 per 24k credits |
| Enterprise | **Custom** | Custom | Custom |

- **Credit Consumption** (Sonnet 4.5 baseline):
  | Task Size | Credits |
  |---|---|
  | Small (bug fix, 1-3 files) | ~293 |
  | Medium (feature, 10+ files) | ~860 |
  | Complex (major feature, 20+ files) | ~4,261 |
  | Intent session (mixed model routing) | ~1,200-1,500 |
  | Context Engine MCP query | ~40-70 |
- **Credit Rules**: Pool at team level (Standard/Max: up to 20 users); monthly credits do NOT roll over; top-up credits roll over, expire after 12 months; no AI training on paid plans
- **History**: Switched to credit-based pricing October 20, 2025; migrated existing customers Oct 20-31, 2025
- **Completions Deprecation**: Code completions deprecated March 31, 2026 for Indie, Standard, Max, Legacy plans (Enterprise continues)
- **Who pays what**: Completions-only = ~$20/mo; daily agent user = $60-200/mo; power user (remote agents, CLI, agent-written code) = $200+/mo

### 10. Models

- **Multi-Model Access**: Claude (Opus 4.6/4.7, Sonnet 4.5), GPT (5.1, 5.2, 5.4), Gemini 3.x, Haiku 4.5
- **Model Routing by Role** (Intent product):
  | Role | Recommended Model |
  |---|---|
  | Coordinator | Sonnet 4.6 or Gemini 3.1 Pro |
  | Implementors (well-scoped) | Haiku 4.5 |
  | Implementors (ambiguous) | Sonnet 4.6 |
  | Verifier | GPT-5.2/5.4 or Sonnet 4.6 |
- **BYOA**: Bring Your Own Agent (Claude Code, Codex, OpenCode) without Augment subscription
- **BYOK**: Bring Your Own Key for specific providers

### Bonus: Intent (Standalone Product)

- **Status**: Public beta, macOS only, Apple Silicon
- **Architecture**: Coordinator-Implementor-Verifier (CIV) with Living Spec at center
- **Living Spec**: YAML-based specification as source of truth; agents read/write to it; edits propagate to all active agents; prevents spec rot
- **Two Control Loops**:
  - Inner: Each Implementor's ReAct-style reason-act-observe cycle
  - Outer: Plan → Execute → Verify → Replan across Coordinator and Verifier
- **Three Human Checkpoints**:
  1. Spec Review & Edit ("highest impact minutes in session")
  2. Task Decomposition Review
  3. Final Diff Review → auto-commit, PR, merge
- **Built-in Specialist Agents**: Investigate, Implement, Verify, Critique, Debug, Code Review
- **Custom Specialists**: Define via YAML-frontmatter Markdown in `.augment/agents/`
- **Built-in**: Browser, Terminal, Git (code, preview, commit, PR, merge)
- **Good Fit**: Multi-file features (3-15 files, 1-3 services); cross-service refactors; greenfield features; migrations
- **Poor Fit**: Single-file fixes; production hotfixes; exploratory prototyping; sprawling refactors (50+ files)
- **Pricing**: Regular Augment credits; ~1,200-1,500 credits/session; Indie plan ($20/mo, 40k credits) supports ~27-33 sessions/mo

---

## Comparative Highlights

| Dimension | Windsurf | Augment Code |
|---|---|---|
| **IDE Model** | VS Code fork (standalone app) | VS Code + JetBrains plugins + ACP for any editor |
| **Context Engine** | RAG with 768-dim embeddings, M-Query, 200K tokens | Custom embedding models, per-developer index, proof-of-possession, 200K tokens |
| **Multi-Agent** | 5 parallel Cascade agents, git worktree isolation | Auggie parallel agents + Intent CIV pipeline with worktree isolation |
| **Pricing (Indie/Pro)** | $20/mo | $20/mo |
| **Unique Moat** | Arena Mode (blind A/B), SWE custom models, one-click deploy | Per-developer indexing, CMEK/air-gapped, Context Engine as MCP for any tool |
| **Terminal** | Dedicated Cascade Terminal (zsh), 4 auto-exec levels, Cmd+I inline | Auggie CLI with full TUI, print/quiet/headless modes, service accounts |
| **Memory** | Auto/manual, per-workspace, local only, free | In-chat review/approve/edit/discard, promotable to Rules |
| **Rules** | `.windsurfrules` with YAML frontmatter triggers (always_on/glob/model_decision/manual) | AGENTS.md, custom commands in `.augment/commands/` |
| **Integrations** | MCP (5,000+ servers via MCPFind) | 8 native OAuth (GitHub, Linear, Jira, Confluence, Notion, Sentry, Stripe, Glean) + MCP |
| **Enterprise** | SSO, RBAC, hybrid deployment | SSO, RBAC, VPC, on-premise, air-gapped, CMEK, ISO 42001, SOC 2 Type II |
| **Free Tier** | 25 credits/mo + unlimited SWE-1.5 model | 30,000 trial credits (with credit card) |

---

## Sources

### Windsurf
- [Windsurf Cascade Official Page](https://windsurf.com/cascade)
- [Windsurf Docs - Context Awareness](https://docs.windsurf.com/context-awareness/overview)
- [Windsurf Docs - Fast Context](https://docs.windsurf.com/context-awareness/fast-context)
- [Windsurf Docs - Terminal](https://docs.windsurf.com/windsurf/terminal)
- [Windsurf Docs - Cascade Modes](https://docs.windsurf.com/windsurf/cascade/modes)
- [Windsurf Docs - Memories](https://docs.windsurf.com/windsurf/cascade/memories)
- [Windsurf Docs - Arena Mode](https://docs.windsurf.com/windsurf/cascade/arena)
- [Windsurf Docs - Models](https://docs.windsurf.com/windsurf/models)
- [Windsurf Docs - Quota](https://docs.windsurf.com/windsurf/accounts/quota)
- [Windsurf Pricing](https://windsurf.com/pricing)
- [Windsurf Leaderboard](https://windsurf.com/leaderboard)
- [Windsurf University - Rules & Memories](https://www.windsurf.com/university/general-education/intro-rules-memories)
- [Windsurf Getting Started](https://docs.windsurf.com/windsurf/getting-started)
- [Windsurf Web Search Technical Deep Dive](https://khou22.com/blog/2025-01-17-windsurf-web-search-tutorial)
- [Windsurf Supercomplete Guide](https://markaicode.com/windsurf-supercomplete-beyond-autocomplete-ai-coding/)
- [Windsurf Wave 13 Coverage](https://aiautomationglobal.com/blog/windsurf-wave-13-parallel-agents-arena-mode-ai-ide-2026)
- [Windsurf vs Cursor Comparison - DataCamp](https://www.datacamp.com/blog/windsurf-vs-cursor)
- [Windsurf Arena Mode - InfoQ](https://infoq.com/news/2026/02/windsurf-arena-mode/)
- [Windsurf Arena Mode - DEV Community](https://dev.to/alanwest/windsurfs-arena-mode-lets-you-blind-test-ai-models-i-tried-it-1hk4)
- [Multi-Agent IDE Convergence](https://agentmarketcap.ai/blog/2026/04/07/multi-agent-ide-convergence-parallel-agents-productivity)

### Augment Code
- [Augment Context Engine](https://augmentcode.com/context-engine)
- [Augment Context Engine Technical Blog](https://augmentcode.com/blog/a-real-time-index-for-your-codebase-secure-personal-scalable)
- [Augment Context Engine vs Windows](https://www.augmentcode.com/guides/context-engine-vs-context-windows)
- [Augment Context Connectors](https://docs.augmentcode.com/context-services/context-connectors/how-it-works)
- [Augment Context Engine MCP](https://docs.augmentcode.com/context-services/mcp/overview)
- [Augment Agent Blog](https://augmentcode.com/blog/meet-augment-agent)
- [Augment Agent Docs](https://docs.augmentcode.com/using-augment/agent)
- [Auggie CLI Product Page](https://www.augmentcode.com/product/CLI)
- [Auggie CLI Docs](https://docs.augmentcode.com/cli/overview)
- [Auggie CLI Flags](https://docs.augmentcode.com/cli/reference)
- [Auggie Interactive Mode](https://docs.augmentcode.com/cli/interactive)
- [Auggie Automation](https://docs.augmentcode.com/cli/automation/overview)
- [Augment Memory Review](https://www.augmentcode.com/changelog/memory-review)
- [Augment How We Built Memory Review](https://www.augmentcode.com/blog/how-we-built-memory-review)
- [Augment Agent Integrations](https://docs.augmentcode.com/setup-augment/agent-integrations)
- [Augment Intent Product](https://augmentcode.com/intent)
- [Augment Intent Blog](https://www.augmentcode.com/blog/intent-a-workspace-for-agent-orchestration)
- [Augment CIV Pattern](https://www.augmentcode.com/guides/coordinator-implementor-verifier)
- [Augment Intent Walkthrough](https://www.augmentcode.com/guides/intent-walkthrough-prompt-to-merge)
- [Augment Custom Specialist Agents](https://www.augmentcode.com/guides/how-to-define-custom-specialist-agents-in-intent)
- [Augment Security](https://www.augmentcode.com/security)
- [Augment CMEK](https://www.augmentcode.com/blog/customer-managed-keys-your-keys-your-rules)
- [Augment Pricing](https://www.augmentcode.com/pricing)
- [Augment Pricing Change Blog](https://www.augmentcode.com/blog/augment-codes-pricing-is-changing)
- [Augment ACP Blog](https://www.augmentcode.com/blog/auggie-acp-zed-neovim-emacs)
- [Augment ACP Clients](https://docs.augmentcode.com/cli/acp/clients)
- [Augment Code vs Amazon Q Enterprise](https://www.augmentcode.com/tools/augment-code-vs-amazon-q-enterprise-security-reviews)
- [Augment MCP Multi-Repo](https://www.augmentcode.com/guides/mcp-integration-streamlining-multi-repo-development)
- [Augment SOC2 Guide](https://www.augmentcode.com/tools/ai-coding-tools-soc2-compliance-enterprise-security-guide)
