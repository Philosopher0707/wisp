# PearAI, Cody (Sourcegraph), Continue, GitHub Copilot -- Complete Feature Catalog

> Research date: 2026-05-09 | Sources cited inline

---

## Part A: PearAI (trypear.ai)

### 1. What Is PearAI?

- **VS Code fork** (Electron + TypeScript), NOT just an extension
- Founded 2024 by Nang Ang and Duke Pan (ex-Meta, Coinbase, Tesla)
- Backed by **Y Combinator** ($1.25M seed, Dec 2024)
- Licensed **Apache 2.0 / MIT** -- fully open source
- The AI layer is itself a **fork of Continue** (the open-source assistant)
- Positioned as the "open-source alternative to Cursor and Windsurf"
- GitHub: `trypear/pearai-master` (~737 stars), `trypear/pearai-app`, `trypear/pearai-submodule`
- Backend: Python FastAPI + Supabase database
- ~1,900+ Discord community members; ~40% of code from external contributors

### 2. AI Features

| Feature | Details |
|---------|---------|
| **AI Chat (Cmd+L)** | Contextual, codebase-aware chat built into the editor |
| **Inline Editing (Cmd+I)** | Targeted code changes without leaving the editor |
| **Autocomplete** | Fast inline suggestions via Supermaven (bundled, 1M-token context window) |
| **PearAI Agent** | Autonomous coding agent, powered by Roo Code / Cline integration; writes features, fixes bugs, refactors from natural language |
| **PearAI Router** | Automatically routes each task to the best-performing LLM each week |
| **PearAI Memory** | Persistent memory across sessions, built on Mem0; remembers coding preferences and project settings |
| **PearAI Inventory** | Indexes tech stack libraries/frameworks locally for smarter suggestions |
| **PearAI Creator (beta)** | Multi-file code generation and project scaffolding, powered by a forked Aider (`trypear/pearai-aider`) |
| **Multi-Model** | Claude Sonnet/Opus, GPT-4o, o1/o3-mini, Gemini, DeepSeek V3/R1, Llama, and more |
| **Local Models** | Full Ollama, LM Studio, and LocalAI integration for offline/privacy use |
| **Perplexity Integration** | Web search for up-to-date docs and context |
| **Full VS Code Extension Compatibility** | All extensions, themes, debuggers, language servers work |

### 3. Pricing

| Plan | Price | Details |
|------|-------|---------|
| **Free (BYO Key)** | $0/month | Full editor, bring your own API keys, local models, community support |
| **Pro (Maker)** | $15/month | PearAI Router + hosted servers, $15/mo in credits, zero data retention with Anthropic, direct support |
| **Enterprise** | Custom | Increased credits, centralized billing/dashboard, zero data retention, founder support |

- Credits never expire; top-ups available
- Cheaper than Cursor Pro ($20/mo) and Copilot Pro ($10/mo but Pro is simpler)

### 4. Unique Features & Differentiators

- **Open-source** (Apache 2.0) vs Cursor (proprietary), Windsurf (proprietary), Copilot (proprietary)
- **PearAI Router** -- no other tool auto-routes to the best model weekly
- **Curated multi-tool bundle** -- unifies Aider, Supermaven, Continue, Mem0, Perplexity in one interface
- **Local-first** -- codebase indexing is entirely local; zero data retention on PearAI servers
- **Full BYO-key free tier** -- zero cost if you bring your own LLM API keys
- **Proprietary fine-tuned model** -- reportedly outperforms major LLMs on coding benchmarks
- **Upcoming**: PearAI Launch (one-click deploy via Netlify)

### 5. Pros & Cons

**Pros:**
- Fully open source -- auditable, forkable, no lock-in
- Familiar VS Code interface; all extensions work
- Free BYO-key tier for power users
- Supports local models for privacy
- Active community
- Lower price than Cursor

**Cons:**
- Less polished UX than Cursor/Windsurf
- Smaller team -> slower feature velocity
- No enterprise SSO in base Pro plan
- Some features still in beta
- Had a controversial/rocky launch in 2024 (marketing overstated early capabilities)
- Desktop only, no mobile/web version

### Ratings (from review sites)

| Source | Rating |
|--------|--------|
| Ojambo (in-depth feature test, v1.8.9) | 9.5/10 |
| ToolChase | 4.2/5 |
| Zelili | 4.7/5 |
| AIDevStart | 8.8/10 |

---

## Part B: Cody by Sourcegraph (sourcegraph.com/cody)

### 1. Context Engine

- Cody's RAG architecture spans **3 layers**:
  1. **Local file context** -- immediate editor buffer, open files
  2. **Local repository context** -- current codebase, indexed locally
  3. **Remote repository context** -- via Sourcegraph Code Intelligence Platform: search, code graph (SCIP), embeddings
- **Sourcegraph Search is the primary context provider** (replaced embeddings in v5.3+ for Enterprise)
  - More secure: no code sent to third-party embedding APIs
  - Scales to larger repos and more repositories
  - Users select multiple repos as context sources from within the IDE
  - Equal or better retrieval quality vs embeddings
- **Multi-repo context**: Search up to 10 repositories simultaneously (Enterprise)
- **Context windows up to 1M tokens** (via Claude Sonnet 4)
- **Agentic Context Fetching** -- mini-agent system autonomously retrieves relevant context:
  1. Proactive context gathering
  2. Agentic context reflection (reviews gathered context)
  3. Iterative context improvement (multiple review loops)
  4. Enhanced response accuracy
- Reflection/review step defaults to **Gemini 2.5 Flash**, falling back to Claude Haiku or GPT-4.1 mini

### 2. Cody Chat

| Feature | Details |
|---------|---------|
| **Chat** | Contextual Q&A about codebase, debug help, code generation |
| **Chat history** | Persistent across sessions |
| **@-file mentions** | Add specific files to context |
| **@-symbol mentions** | Add specific symbols/functions/classes |
| **@-directory mentions** | Add entire directories |
| **Image upload** | Screenshots, diagrams for visual context |
| **LLM selection** | Hot-swap models mid-conversation |
| **Multi-repo context** | Pull context from remote repos (Enterprise) |
| **Prompt Library** | Pre-built and custom prompts |
| **Slash commands** (inline): Edit Code, Document Code, Generate Unit Tests, Explain Code, Smell Code |
| **SmartApply / Execute** | AI-driven code application with diff preview |
| **Deep Search (2026)** | Dedicated subagent for thorough multi-step codebase search; shows hover cards (definitions, references, type info) in sidebar |

### 3. Autocomplete

| Feature | Details |
|---------|---------|
| **Multi-line completions** | Full multi-line suggestions |
| **Cycle suggestions** | Cycle through multiple completion options |
| **Accept word-by-word** | Partial acceptance granularity |
| **Auto-edit suggestions** | Proactive edit suggestions beyond completions |
| **Latency** | Competitive; Supermaven-class speed in recent updates |
| **Single-line** | Traditional inline completions |

### 4. Cody Agent (Agentic Context Fetching)

- NOT the same as Cursor/Copilot Agent mode for autonomous code changes
- Cody's "agent" is about **agentic context retrieval** -- autonomously finding the right context
- Tools available:
  - **Code Search**: Semantic code search across repositories
  - **Codebase File**: Full file retrieval
  - **Terminal**: Shell command execution (user consent required; VS Code, JetBrains, VS only)
  - **Web Browser**: Live web search
  - **MCP**: Model Context Protocol for external services
  - **OpenCtx**: External context providers (issue trackers, etc.)
- For actual autonomous code **changes**, Sourcegraph created **Amp** (see below)

### 5. Amp -- Sourcegraph's Separate Agentic Product (2026)

Amp is Sourcegraph's **frontier coding agent**, distinct from Cody:

**Core Principles:**
1. Unconstrained token usage -- no artificial limits
2. Always uses the best models -- switches to state-of-the-art
3. Raw model power -- minimal guardrails
4. Built to evolve -- no backcompat, no legacy features

**Agent Modes:**

| Mode | Models | Use Case |
|------|--------|----------|
| `smart` | Claude Opus 4.7 (up to 300k context tokens) | Maximum capability & autonomy |
| `rush` | Fast models | Small, well-defined tasks |
| `deep` | GPT-5.5 (with extended thinking) | Complex reasoning |
| `large` | -- | Extended contexts |

**Key Features:**
- **Oracle** -- GPT-5.4 as "second opinion" model for reviews, debugging, refactoring analysis
- **Subagents** -- spawn independent agents for parallel work (e.g., "convert these 3 CSS files to Tailwind")
- **Librarian** -- subagent that searches all public GitHub code + private repos + Bitbucket Enterprise
- **Painter** -- Gemini 3 Pro Image for generating/editing images (mockups, icons, screenshots)
- **Code Review**: `amp review` for bugs, security, performance, style
- **Plugin System**: TypeScript plugins for events, custom tools, commands, UI
- **Toolboxes**: Simple scripts extend Amp without full MCP server
- **Agent Skills**: Packages of instructions + resources; MCP servers can be bundled inside
- **16+ built-in tools**: Bash, Read, edit_file, create_file, Grep (ripgrep), glob, list_directory, web_search, read_web_page, Task, oracle, todo_write/read, undo_edit, mermaid, codebase_search_agent
- **Thread Sharing**: Sync to ampcode.com; Public/Unlisted/Workspace/Group/Private visibility
- **AGENTS.md**: Auto-reads for codebase guidance; YAML frontmatter `globs` for file-specific rules

**IDE Support:** VS Code (+ forks: Cursor, Windsurf), JetBrains, Neovim, Zed; CLI-IDE bidirectional
**Pricing:** Pay-as-you-go with zero markup; $5 minimum credit; Enterprise: 50% premium, SSO, zero data retention

### 6. IDE Support

| IDE | Status | Key Features |
|-----|--------|-------------|
| **VS Code** | Gold Standard | Chat, autocomplete, inline edit, @-mentions (files/symbols/dirs), image upload, SmartApply, agentic context, Deep Search |
| **JetBrains** | GA (since June 2024) | Chat, autocomplete, inline edit, multi-repo, agentic context; hotkeys: `Tab`, `Opt+[`, `Opt+\` |
| **Neovim** | Experimental & unmaintained | `sg.nvim` plugin; Chat, autocomplete, Sourcegraph Search, LSP; `:CodyChat`, `:CodyAsk`, `:CodyTask` |
| **Emacs** | Experimental | `emacs-cody` (`cody.el`); autocomplete focus; chat is "stub only" -- not functional; ~76 stars, 4 contributors |
| **Visual Studio** | Experimental | Chat + autocomplete; feature gaps |
| **Web** | Fully supported | Browser-based via Sourcegraph web app |
| **CLI** | Supported | Command-line interface |

### 7. Enterprise (Cody Enterprise)

- **Cody Enterprise**: Unaffected by Free/Pro shutdown; fully supported
- **Code Search Enterprise**: $49/user/month -- Deep Search, Code Search, Symbol Search, Batch Changes, Code Insights, Code Navigation, Code Monitoring
- **Cody Enterprise**: Custom pricing (contact sales)
- **BYOK support**: Anthropic, OpenAI, Azure OpenAI, Amazon Bedrock
- **No training on customer code** (unless admin explicitly enables fine-tuning)
- **Zero retention** by LLM partners (Anthropic/OpenAI)
- **Single-tenant cloud** deployment
- **IP allowlisting**, RBAC, compliance controls
- **Audit logs**
- **Enterprise Starter**: Up to 50 devs, 100 repos, 5GB storage (max 10GB) -- but Cody-free as of July 2025

### 8. Pricing

| Plan | Status (2026) | Price | Details |
|------|--------------|-------|---------|
| **Cody Free** | Discontinued (July 2025) | -- | Signups stopped June 2025 |
| **Cody Pro** | Discontinued (July 2025) | -- | Signups stopped June 2025 |
| **Cody Enterprise** | Active | Custom | Contact sales; includes BYOK, multi-repo |
| **Enterprise Code Search** | Active | $49/user/month | Search platform |
| **Enterprise Starter** | Active (Cody-free) | Lower cost | Up to 50 devs, no Cody |

- Existing Free/Pro users got access until July 23, 2025
- Affected users received $10-$40 in free **Amp** credits

### 9. Models

- **Default**: Sourcegraph Model Provider (Cody Gateway via `cody-gateway.sourcegraph.com`)
- **Supported**: Claude 3 Opus, Claude Sonnet 4, GPT-4o, GPT-4.1 mini, Gemini 2.5 Flash, Mixtral 8x22B, Mistral
- **Multi-model**: Hot-swap per task; Pro+ can switch freely
- **BYOK**: Azure OpenAI, Amazon Bedrock, and other providers for Enterprise

### 10. Unique Features Summary

- **Code graph awareness** -- best-in-class for large/multi-repo codebases
- **Sourcegraph Search** as primary RAG -- scale, security, accuracy
- **Deep Search** (2026) -- dedicated subagent for codebase exploration
- **Batch Changes** -- automated large-scale code changes across repos
- **Code Insights** -- dashboards and analytics on codebase
- **Code Monitoring** -- alerts on codebase changes
- **Self-hosted deployment** -- full data residency (unique among major AI coding tools)
- **Amp** -- separate frontier agent product, unconstrained, always cutting-edge models

---

## Part C: Continue (continue.dev)

### 1. Architecture

- **Open-source** (Apache 2.0), created May 2023 by Continue Dev, Inc. (San Francisco, $5.1M raised)
- **33,000+ GitHub stars**, 400+ contributors, 2.5M+ VS Code Marketplace installs
- **IDE extensions**: VS Code, JetBrains (NOT a fork -- pure extension)
- **CLI**: `cn` command for terminal and CI/CD workflows
- **Model-agnostic**: Connects to any model via provider configuration, NOT locked to any vendor
- **Configuration**: YAML-based `config.yaml` (deprecated `config.json` in 2025)
- **Two deployment models**:
  - **Local**: `config.yaml` on your machine, `.env` for API keys, version-control friendly
  - **Hub (Mission Control)**: Web-based config management, auto-sync across IDE instances

### 2. Chat

| Feature | Details |
|---------|---------|
| **Sidebar Chat** | Ask questions, debug, generate code without leaving IDE |
| **Inline Chat** | Edit code at cursor with natural language |
| **/commands (slash commands)** | Powered by "Prompts" defined in `config.yaml`; can come from Hub or local files |
| **`/mcp`** | Added "Explore MCP Servers" option in v1.27.0 (Oct 2025) |
| **@-mentions** | Add files, symbols, directories to context |
| **Chat history** | Persistent across sessions |
| **Model switching** | Per-conversation model selection from config dropdown |
| **Custom system messages** | Per-model overrides: `baseSystemMessage`, `baseAgentSystemMessage`, `basePlanSystemMessage` |

### 3. Autocomplete

| Feature | Details |
|---------|---------|
| **Tab completions** | Inline suggestions as you type |
| **Multi-line** | Full multi-line completions |
| **Accept word-by-word** | Partial acceptance granularity |
| **Configurable** | Assigned via `roles: [autocomplete]` on a model |
| **Options** | `debounceDelay`, `maxPromptTokens`, `onlyMyCode`, `useCache`, `useImports`, `useRecentlyEdited`, custom Mustache templates, `transform` |
| **Multi-model** | Different model for autocomplete vs chat vs edit |

### 4. Agent Mode

| Feature | Details |
|---------|---------|
| **Agent Mode** | Multi-file changes with tool access; autonomous multi-step tasks |
| **MCP Tools** | Full Model Context Protocol support; `stdio`, `sse`, `streamable-http` transports |
| **MCP Interop** | Drop JSON configs from Claude Desktop, Cursor, or Cline directly into `.continue/mcpServers/` |
| **Tool Policies** | Per-tool: *Ask First* (default), *Automatic*, or *Excluded* |
| **Background Agents** | Triggered by events: PR reviews, docs updates, alert tickets |
| **Rules** | Project-level `.continue/rules/` markdown files; concatenated into system prompt for Agent/Chat/Edit |
| **MCP only in Agent mode** | MCP tools do NOT work in plain Chat mode |

### 5. Model Flexibility

Continue supports **50+ providers**, the widest range of any tool:

| Provider | Models |
|----------|--------|
| **Anthropic** | Claude Opus 4.7, Claude Sonnet 4, etc. |
| **OpenAI** | GPT-5.5, GPT-5.4, o1, o3, o4-mini, etc. |
| **Google** | Gemini 3.1 Pro, Gemini 2.5 Flash |
| **xAI** | Grok Code Fast 1 |
| **Mistral** | Devstral Medium/Small, Codestral |
| **Moonshot AI** | Kimi K2 |
| **Qwen** | Qwen Coder 3, Qwen2.5-Coder |
| **DeepSeek** | DeepSeek V3, R1 |
| **Ollama** | Qwen3 Coder 30B, Gemma 3 4B, Llama, etc. |
| **Azure** | Azure OpenAI models |
| **Amazon Bedrock** | All Bedrock models |
| **LM Studio** | Local models |
| **Custom endpoints** | OpenAI-compatible API endpoints |

**Model Roles** -- fine-grained control over which model does what:
- Chat, Edit, Apply, Autocomplete, Embedding, Reranker

### 6. IDE Support

| IDE | Status |
|-----|--------|
| **VS Code** | Full support, most features |
| **JetBrains** | Full support (IntelliJ, WebStorm, GoLand, PyCharm, etc.) |
| **CLI** | `cn` command for terminal and CI |

### 7. Configuration System

- **Config file**: `config.yaml` (replaced deprecated `config.json`)
- **Location**: `~/.continue/config.yaml` (global) or `.continue/config.yaml` (per-workspace)
- **Modular blocks**: Models, MCP Servers (tools), Rules, and Prompts
- **Hub references**: `uses: owner/item-name` syntax for community blocks
- **Secrets**: `${{ secrets.KEY_NAME }}` or `${{ inputs.NAME }}` notation
- **Permission levels**: Private, Organization, or Public
- **Specialized configs**: e.g., Next.js config with React rules, data-science config with Python tools
- **Source-controlled AI Checks**: `.continue/checks/` markdown files with YAML frontmatter; run as GitHub status checks in CI

### 8. Unique Features

- **Most open-source** -- Apache 2.0, full codebase on GitHub, community-driven
- **Model agnosticism** -- 50+ providers, local models, BYOK, no lock-in
- **Continue Hub (Mission Control)** -- share, discover, and remix configs, rules, and tools
- **AI Checks in CI** -- source-controlled markdown checks that run as GitHub status checks
- **Background Agents** -- event-triggered, not just on-demand
- **CLI-first options** -- `cn` for terminal and CI/CD pipelines
- **MCP interop** -- drop configs from other tools directly into Continue
- **No forced subscription** -- you can use it 100% free with your own keys forever

### 9. Pricing

| Plan | Price | Details |
|------|-------|---------|
| **Starter** | $3/million tokens (pay-as-you-go) | Create/run AI agents, connect integrations (Slack, Sentry, Snyk), buy credits for frontier models |
| **Team** | $20/seat/month (incl. $10 credits) | Centralized management, share private agents, control agent usage, Gmail/GitHub SSO |
| **Company (Enterprise)** | Custom | Custom SSO (SAML/OIDC), BYOK, invoicing, SLA |
| **Free (BYOK)** | $0 | IDE extensions free forever; bring your own API keys or run local models |

---

## Part D: GitHub Copilot (Deep Dive)

### 1. Copilot Chat -- Agent Modes (3 Core)

| Mode | Description |
|------|-------------|
| **Agent mode** | Autonomously accomplishes tasks -- determines files to edit, runs terminal commands, self-corrects errors, multi-file changes |
| **Plan mode** | Creates detailed implementation plans before any code changes; researches, asks clarifying questions, outputs structured plan |
| **Ask mode** | Read-only; optimized for answering questions about codebase, coding concepts, technology |

**Chat Participants (`@` mentions):**

| Participant | Description |
|-------------|-------------|
| `@workspace` | Codebase context (structure, interactions, design patterns) |
| `@github` | GitHub skills (PRs, issues, web search) |
| `@terminal` | Terminal shell context |
| `@vscode` | VS Code commands and features |
| `@azure` | Azure services (public preview) |

**Complete Slash Commands List:**

*Core:*
| Command | Description |
|---------|-------------|
| `/clear` | Start new chat session |
| `/compact` | Compact conversation context |
| `/fork` | Fork current session into new independent session |
| `/help` | Quick reference |
| `/debug` | Show Chat Debug view |
| `/troubleshoot` | AI analyzes agent debug logs |

*Code Understanding:*
| Command | Description |
|---------|-------------|
| `/explain` | Explain how code works |
| `/doc` | Generate documentation comments |

*Code Improvement:*
| Command | Description |
|---------|-------------|
| `/fix` | Propose fix for problems |
| `/fixTestFailure` | Find and fix failing test |
| `/optimize` | Analyze and improve performance |
| `/simplify` | Simplify code selection |

*Testing:*
| Command | Description |
|---------|-------------|
| `/tests` | Generate unit tests |
| `/setupTests` | Help setting up testing framework |

*Project Scaffolding:*
| Command | Description |
|---------|-------------|
| `/new` | Scaffold new workspace or file |
| `/newNotebook` | Scaffold Jupyter notebook |
| `/init` | Generate/update `copilot-instructions.md` or `AGENTS.md` |

*Debugging & Search:*
| Command | Description |
|---------|-------------|
| `/startDebugging` | Generate `launch.json` and start debugging |
| `/search` | Generate search query |
| `/plan` | Create detailed implementation plan |

*Configuration:*
| Command | Description |
|---------|-------------|
| `/agents` | Configure custom agents |
| `/hooks` | Configure hooks |
| `/instructions` | Configure custom instructions |
| `/prompts` | Configure reusable prompt files |
| `/skills` | Configure agent skills |
| `/create-prompt` | Generate prompt file with AI |
| `/create-instruction` | Generate instructions file with AI |
| `/create-skill` | Generate agent skill with AI |
| `/create-agent` | Generate custom agent with AI |
| `/create-hook` | Generate hook configuration with AI |

*Auto-Approve (YOLO):*
| Command | Description |
|---------|-------------|
| `/yolo` / `/autoApprove` | Enable global auto-approval of all tool calls |
| `/disableYolo` / `/disableAutoApprove` | Disable global auto-approval |

### 2. Agent Tools (`#` tools in Agent Mode)

**Read Tools (`#read`):**
- `#read/readFile` -- Read file content
- `#read/problems` -- Workspace issues from Problems panel
- `#read/terminalLastCommand` -- Last terminal command + output
- `#read/terminalSelection` -- Current terminal selection
- `#read/getNotebookSummary` -- Notebook cells and details
- `#read/readNotebookCellOutput` -- Notebook cell execution output

**Edit Tools (`#edit`):**
- `#edit/editFiles` -- Apply edits to files
- `#edit/createFile` -- Create new file
- `#edit/createDirectory` -- Create new directory
- `#edit/editNotebook` -- Edit a notebook

**Execute Tools (`#execute`):**
- `#execute/runInTerminal` -- Run shell command in terminal
- `#execute/getTerminalOutput` -- Get terminal command output
- `#execute/createAndRunTask` -- Create and run new task
- `#execute/runNotebookCell` -- Run notebook cell
- `#execute/testFailure` -- Get unit test failure info

**Search Tools (`#search`):**
- `#search/codebase` -- Semantic code search
- `#search/fileSearch` -- Find files by glob pattern
- `#search/textSearch` -- Find text in files
- `#search/listDirectory` -- List directory contents
- `#search/changes` -- Source control changes
- `#search/usages` -- Find references, implementations, definitions

**Web & GitHub:**
- `#web/fetch` -- Fetch URL content
- `#githubRepo` -- Semantic search a GitHub repo
- `#githubTextSearch` -- Text search a GitHub repo or org

**Agent & VS Code:**
- `#agent/runSubagent` -- Delegate task to isolated subagent
- `#vscode/runCommand` -- Run VS Code command
- `#vscode/installExtension` -- Install VS Code extension
- `#vscode/extensions` -- Search for VS Code extensions
- `#vscode/askQuestions` -- Agent asks clarifying questions
- `#vscode/getProjectSetupInfo` -- Project scaffolding instructions
- `#vscode/VSCodeAPI` -- VS Code extension development help
- `#browser` (tool set) -- Experimental integrated browser

**Context Variables (`#` mentions):**
- `#file`, `#selection`, `#editor`, `#block`, `#class`, `#function`, `#line`, `#path`, `#project`, `#sym`, `#comment`, `#todos`

### 3. Subagents (2025+)

- Delegate tasks to isolated agents with their own context window
- Operate independently without pausing for feedback
- Return results to main session
- **Automatic delegation** -- Copilot analyzes request and picks right subagent
- **Direct invocation** -- e.g., "Use the testing subagent to write unit tests"
- **Tool-based** -- e.g., `#runSubagent` tool

### 4. Copilot Edits (Multi-File Editing)

- Multi-file inline changes from a single natural-language prompt
- **Working set** -- specify which files are in scope
- **Review** -- diff preview before applying
- **Iteration** -- refine with follow-up prompts
- GA since early 2025
- Works alongside Agent mode

### 5. Copilot Code Review

- **Available on**: Pro, Pro+, Business, Enterprise (premium feature)
- **Environments**: GitHub.com, GitHub Mobile, VS Code, Visual Studio, Xcode, JetBrains
- **Agentic context gathering** -- full project context (GA)
- **Cloud agent integration** -- pass suggestions to Copilot cloud agent for auto PR creation (public preview)
- **Automatic reviews**: Configurable per-user, per-repo, per-org; on open, on push, on draft PRs
- **Custom instructions**: Repository-level and organization-level
- **Unlicensed users**: Business/Enterprise can extend code review to members without Copilot license (pay-per-use)
- **Coding guidelines** (Enterprise): Up to 6 natural-language guidelines per repo, path-pattern targeting, testing against code samples
- **Warning**: Starting June 1, 2026, code review runs consume GitHub Actions minutes

### 6. Copilot Extensions (DEPRECATED)

- **GA**: February 19, 2025 -- marketplace with Perplexity, Stack Overflow, Docker, Mermaid Chart, Arm
- **Sunset announced**: September 24, 2025
- **Full shutdown**: November 10, 2025
- **Replaced by**: Model Context Protocol (MCP) servers + GitHub MCP Registry
- **Still supported**: Client-side VS Code Copilot Extensions (NOT affected)
- The GitHub Marketplace now lists **AI models** rather than Copilot extensions

### 7. Copilot Autocomplete

| Feature | Details |
|---------|---------|
| **Ghost text** | Inline suggestions as you type |
| **Multi-line** | Full multi-line completions |
| **Cycling suggestions** | Navigate multiple completion options |
| **Partial acceptance** | Accept word-by-word or line-by-line |
| **Next Edit Suggestions** | Tab to accept predicted next edits (preview); far-away edit indicators, inline previews |

### 8. Copilot CLI (Terminal)

- **What**: Full agent in the terminal, NOT just command suggestions
- **Install**: `npm install -g @github/copilot`, Homebrew, WinGet, or install script
- **Included in**: Free, Pro, Pro+, Business, Enterprise plans
- **Key features**:

| Feature | Description |
|---------|-------------|
| `/plan` (Shift+Tab) | Structured planning mode; asks clarifying questions, builds plan before code |
| `/fleet` | Parallel subagents for same task across multiple models |
| `/model` | Switch models: Claude Sonnet 4.5 (default), GPT-5.1, GPT-5.1-Codex, GPT-5.5, Gemini 3 Pro; BYO provider via env vars |
| `/delegate` | Background task execution; creates branches, implements, opens PRs |
| `/resume` | Session persistence with auto-compaction |
| `/agent` | Custom agents & skills; built-in: Explore, Task, General purpose, Code review, Research, Rubber duck |
| `/context` | Visual token usage breakdown |
| `/compact` | Manual context compression |
| `/usage` | Session statistics |
| `/share` | Export to Markdown or GitHub Gists |
| `/experimental` | Access experimental features |
| `/changelog` | Track updates |
| `!command` | Run shell commands directly (no model) |
| `Ctrl+T` | Toggle reasoning visibility |
| `@filepath` | Add files to prompt context |

**Headless/Scriptable:**
- `-p` / `--prompt` for programmatic use
- Fine-grained tool approval: `--allow-tool`, `--deny-tool`, `--allow-all-tools`
- ACP (Agent Client Protocol) -- use CLI as agent in third-party tools

**Security:**
- Every file change/command requires user approval (unless opted in)
- Trusted directories scope read/modify/execute permissions
- Inherits org Copilot governance policies
- Fine-grained: deny `rm` or `git push` while allowing everything else

**Platforms:** macOS, Linux, Windows (PowerShell, WSL)

### 9. Copilot Customization

| Feature | Free | Pro/Pro+ | Business | Enterprise |
|---------|:----:|:--------:|:--------:|:----------:|
| Repository custom instructions | Yes | Yes | Yes | Yes |
| Personal custom instructions | Yes | Yes | Yes | Yes |
| Organization custom instructions | No | No | Yes | Yes |
| Prompt files | Yes | Yes | Yes | Yes |
| MCP support | No | Yes | Yes | Yes |
| Organization-wide policies | No | No | Yes | Yes |
| Content exclusion | No | No | Yes | Yes |
| Copilot Memory (public preview) | No | Yes | No | No |
| Block suggestions matching public code | Yes | Yes | Yes | Yes |

**Custom Instruction Files:**

| File | Scope |
|------|-------|
| `.github/copilot-instructions.md` | Repository-wide, all files |
| `.github/instructions/*.instructions.md` | Path-specific, with `applyTo` frontmatter |
| Organization settings | All repos in org |
| Personal settings | Your conversations only |

**Best practices (code review instructions):**
- Keep under 4,000 characters per file
- Use short imperative directives, bullet points
- Provide concrete examples (correct & incorrect)
- Use `applyTo` frontmatter for language/directory targeting
- Use `excludeAgent` frontmatter to exclude specific agents
- Do NOT attempt to: change UX/formatting, block PRs, follow external links, use vague directives

### 10. Copilot Workspace

- **What**: Copilot-native, browser-based development environment
- **Differs from regular Copilot**: Task-driven, autonomous, end-to-end (issue-to-PR), NOT inline IDE assist
- **Workflow**: Spec -> Brainstorm -> Plan -> Implement -> Validate -> PR

| Feature | Description |
|---------|-------------|
| **Task-driven** | Start from Issue, PR, template repo, or natural language task |
| **Spec/Brainstorming** | Thought partner -- explore how codebase works, solution ideas |
| **Plan Generation** | Auto-determines files to change; fully editable plan |
| **Implementation** | Streams multi-file code changes; refine via natural language or direct code editing |
| **Auto-Validation** | Auto-runs build & test after implementation; attempts repair on failure |
| **Follow Ups** | Detects dependent changes (renamed functions, changed params, modified classes); auto-edits affected files |
| **File-Specific Plans** | View/edit plan per file during implementation |
| **Go to Definition** | Built-in code navigation |
| **Built-in Terminal + Codespaces** | Build, test, run inside workspace before PR |
| **Autofix Integration** | GHAS users: triage and apply Autofix suggestions with build/test |
| **Enterprise Support** | Enterprise Managed Users; full admin controls |

### 11. "Project Padawan" (Fully Autonomous SWE Agent)

- Announced February 2025
- Goal: Assign entire issues to Copilot; it completes the task autonomously; you review later
- Codename for the direction toward fully autonomous coding agents

### 12. Vision for Copilot

- Generate UI from screenshots (preview)
- Image upload support in chat

### 13. Pricing

| Plan | Price (USD/month) | Premium Requests | Best For |
|------|-------------------|------------------|----------|
| **Copilot Free** | $0 | 50/month | Testing the waters |
| **Copilot Pro** | $10 | 300/month | Individual developers |
| **Copilot Pro+** | $39 | 1,500/month | AI power users |
| **Copilot Business** | $19/seat | 300/user/month | Organizations |
| **Copilot Enterprise** | $39/seat | 1,000/user/month | Enterprises (GHEC) |

- **Additional premium requests**: $0.04 each across all paid plans
- **Major change coming**: Starting June 1, 2026, moving from request-based to usage-based billing

### 14. Enterprise Features

- **IP indemnification** -- GitHub assumes legal risk for Copilot-generated code
- **Data privacy** -- Zero data retention by LLM partners; no training on customer code
- **Audit logs** -- Full visibility into Copilot usage
- **Content exclusion** -- Exclude specific files/repos from Copilot context
- **Organization-wide policies** -- Enforce rules across teams
- **Codebase indexing** -- Tailored suggestions based on org's codebase
- **Early access** -- New features and models first
- **Admin controls** -- Enable/disable features per group or user
- **SSO / SAML** -- Enterprise identity integration

### 15. IDE Support

| IDE | Support Level |
|-----|-------------|
| VS Code | Full (gold standard) |
| Visual Studio | Full |
| JetBrains (all IDEs) | Full |
| Eclipse | Supported |
| Xcode | Supported |
| Vim / Neovim | Supported |
| Azure Data Studio | Supported |
| GitHub.com | Full (web) |
| GitHub Mobile | Full |
| GitHub CLI | Full (terminal) |
| Windows Terminal | Supported |
| SQL Server Management Studio | Supported |

---

## Quick Comparison Matrix

| Dimension | PearAI | Cody | Continue | Copilot |
|-----------|--------|------|----------|---------|
| **Type** | VS Code fork | IDE extension | IDE extension + CLI | IDE extension + CLI |
| **Open Source** | Yes (Apache 2.0) | No | Yes (Apache 2.0) | No |
| **Lowest Price** | Free (BYOK) | Custom Enterprise | Free (BYOK) | Free (limited) |
| **Pro Price** | $15/mo | N/A (Enterprise only) | $20/seat/mo | $10/mo |
| **Enterprise** | Custom | $49-$59/seat/mo | Custom | $39/seat/mo |
| **Multi-Model** | Yes (Router) | Yes (hot-swap) | Yes (50+ providers) | Yes (Pro/Pro+) |
| **Local Models** | Yes (Ollama, LM Studio) | No | Yes (Ollama, LM Studio) | Limited (CLI BYO) |
| **Agent Mode** | Yes (Roo Code/Cline) | Separate (Amp) | Yes (MCP tools) | Yes (built-in) |
| **Autocomplete** | Supermaven (1M ctx) | Yes (multi-line) | Yes (configurable) | Yes (ghost text) |
| **Multi-File Edit** | Yes (Creator/Agent) | Yes (Inline Edit) | Yes (Agent mode) | Yes (Edits + Agent) |
| **Code Review** | No | Yes (Amp) | Yes (CI checks) | Yes (built-in) |
| **Context Engine** | Local index + Inventory | Sourcegraph Search + Code Graph | Provider-based (configurable) | @workspace + codebase indexing |
| **Unique Strength** | Open-source + curation | Multi-repo search at scale | Model freedom + CI | GitHub ecosystem depth |
| **IDEs** | Standalone editor | VS Code, JetBrains, Neovim, Emacs | VS Code, JetBrains | VS Code, JetBrains, Visual Studio, Eclipse, Xcode, Vim |
| **Self-Hosted** | No | Yes (Enterprise) | N/A (open source) | No |
| **CLI** | No (VS Code terminal) | Yes | Yes (`cn`) | Yes (full agent) |
