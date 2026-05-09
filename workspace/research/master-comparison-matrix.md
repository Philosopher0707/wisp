# AI Coding Tools Landscape: Master Comparison Matrix (Q2 2026)

> **Compiled**: May 9, 2026 | **Sources**: 50+ web sources, official docs, GitHub repos, independent reviews, community discussions
>
> **Purpose**: Definitive reference for all AI coding tools as of Q2 2026. Parts A-C cover every tool, every dimension, every UX pattern, and a gap analysis for Wisp.

---

## Table of Contents

1. [Part A: Master Comparison Matrix](#part-a-master-comparison-matrix)
   - [Section 1: Basics & Company](#section-1-basics--company)
   - [Section 2: Paradigm & Surface](#section-2-paradigm--surface)
   - [Section 3: AI Capabilities](#section-3-ai-capabilities)
   - [Section 4: Models & Providers](#section-4-models--providers)
   - [Section 5: Context & Indexing](#section-5-context--indexing)
   - [Section 6: Tools & Actions](#section-6-tools--actions)
   - [Section 7: Safety & Sandboxing](#section-7-safety--sandboxing)
   - [Section 8: UX & Customization](#section-8-ux--customization)
   - [Section 9: Collaboration](#section-9-collaboration)
   - [Section 10: CI/CD & Automation](#section-10-cicd--automation)
   - [Section 11: Extensibility](#section-11-extensibility)
   - [Section 12: Pricing](#section-12-pricing)
   - [Section 13: Benchmarks & Metrics](#section-13-benchmarks--metrics)
2. [Part B: UX/UI Pattern Catalog](#part-b-uxui-pattern-catalog)
3. [Part C: Feature Gap Summary for Wisp](#part-c-feature-gap-summary-for-wisp)

---

## Part A: Master Comparison Matrix

### Tools Covered (25)

| # | Tool | Abbreviation |
|---|------|-------------|
| 1 | Claude Code | CC |
| 2 | Codex CLI | CX |
| 3 | Codex App (Desktop) | CXA |
| 4 | Gemini CLI | GC |
| 5 | Cursor | CU |
| 6 | Windsurf | WS |
| 7 | Aider | AD |
| 8 | Augment Code / Auggie | AG |
| 9 | Cody (Sourcegraph) | CD |
| 10 | Continue | CN |
| 11 | GitHub Copilot | CP |
| 12 | PearAI | PR |
| 13 | Zed | ZD |
| 14 | Warp | WR |
| 15 | Devin | DV |
| 16 | Replit Agent | RP |
| 17 | Bolt.new | BL |
| 18 | Lovable | LV |
| 19 | v0 (Vercel) | V0 |
| 20 | Cline | CL |
| 21 | Qodo | QD |
| 22 | Kiro | KR |
| 23 | Open Interpreter | OI |
| 24 | Antigravity | AN |
| 25 | Droid (Factory) | DR |

---

### Section 1: Basics & Company

| Tool | Maker | Founded | HQ | Open Source | License | Primary Language | Install Method | Platform Support |
|------|-------|---------|-----|-------------|---------|-----------------|----------------|-----------------|
| **Claude Code** | Anthropic | 2021 | San Francisco, US | No | Proprietary | TypeScript | npm / brew / direct | macOS, Linux, Windows (WSL) |
| **Codex CLI** | OpenAI | 2015 | San Francisco, US | Yes | Apache 2.0 | Rust (96.3%) | npm / brew / cargo | macOS, Linux, Windows |
| **Codex App** | OpenAI | 2015 | San Francisco, US | No | Proprietary | -- | Direct download | macOS, Windows |
| **Gemini CLI** | Google | 1998 | Mountain View, US | Yes | Apache 2.0 | TypeScript (97.9%) | npm / brew / npx | macOS, Linux, Windows |
| **Cursor** | Cursor Inc (Anysphere) | 2022 | San Francisco, US | No | Proprietary | -- (VS Code fork) | Direct download | macOS, Linux, Windows |
| **Windsurf** | Cognition AI | 2021 (acquired 2025) | Mountain View, US | No | Proprietary | -- (VS Code fork) | Direct download | macOS, Linux, Windows |
| **Aider** | Paul Gauthier (indie) | 2023 | -- | Yes | Apache 2.0 | Python | pip | macOS, Linux, Windows |
| **Augment Code** | Augment Code Inc | 2022 | -- | No | Proprietary | -- | npm (Auggie), IDE plugins | macOS, Linux, Windows |
| **Cody** | Sourcegraph | 2013 | San Francisco, US | No | Proprietary | -- | IDE extensions | VS Code, JetBrains, Neovim, Emacs, Eclipse, Visual Studio |
| **Continue** | Continue Dev Inc | 2023 | -- | Yes | Apache 2.0 | TypeScript | VS Code / JetBrains marketplace, curl | VS Code, JetBrains |
| **GitHub Copilot** | Microsoft / GitHub | 2008/2018 | San Francisco, US | No | Proprietary | -- | IDE extensions | VS Code, JetBrains, Neovim, Xcode, Eclipse, Visual Studio |
| **PearAI** | PearAI Inc (YC) | 2024 | -- | Yes | Apache 2.0 / MIT | -- (VS Code fork) | Direct download | macOS, Linux, Windows |
| **Zed** | Zed Industries | 2021 | -- | Yes | GPL / AGPL | Rust | Direct download / brew | macOS, Linux, Windows |
| **Warp** | Warp Dev Inc | 2020 | New York, US | Yes (AGPL v3) | AGPL v3 | Rust | Direct download / brew | macOS, Linux, Windows |
| **Devin** | Cognition AI | 2023 | Mountain View, US | No | Proprietary | -- | Web app + Slack/Teams bot + CLI | Cloud (any browser) |
| **Replit Agent** | Replit Inc | 2016 | San Francisco, US | No | Proprietary | -- | Browser | Any browser |
| **Bolt.new** | StackBlitz | 2017 | -- | Yes (bolt.diy) | MIT (bolt.diy), Proprietary (cloud) | -- | Browser | Any browser |
| **Lovable** | Lovable Dev AB | 2023 | Stockholm, Sweden | No | Proprietary | -- | Browser / Desktop app | Any browser, macOS (desktop) |
| **v0** | Vercel | 2015 | San Francisco, US | No | Proprietary | -- | Browser, GitHub | Any browser |
| **Cline** | Cline Bot Inc | 2024 | -- | Yes | Apache 2.0 | TypeScript | VS Code marketplace | VS Code, JetBrains |
| **Qodo** | Qodo (formerly Codium) | 2022 | Tel Aviv, Israel | No | Proprietary | -- | IDE plugins + CLI | VS Code, JetBrains |
| **Kiro** | Amazon / AWS | 1994 | Seattle, US | No | Proprietary | -- | IDE download + CLI | macOS, Windows, Linux |
| **Open Interpreter** | Killian Lucas (indie) | 2023 | -- | Yes | AGPL v3 | Python | pip | macOS, Linux, Windows |
| **Antigravity** | Google | 1998 | Mountain View, US | No | Proprietary (free preview) | -- (VS Code fork + Windsurf heritage) | Direct download | macOS, Linux, Windows |
| **Droid** | Factory AI | 2023 | -- | No | Proprietary | TypeScript | npm / CLI | macOS, Linux, Windows |

---

### Section 2: Paradigm & Surface

| Tool | Paradigm | Interface Type | Primary Surface | Has CLI | Has IDE Integration | Has Desktop App | Has Web UI | Has Mobile |
|------|----------|---------------|-----------------|---------|---------------------|-----------------|------------|------------|
| **Claude Code** | CLI Agent | Terminal + IDE plugin | Terminal | Yes (primary) | VS Code, JetBrains (companion) | No | Claude.ai chat | No |
| **Codex CLI** | CLI Agent | Terminal | Terminal | Yes (primary) | Via ACP | Yes (Codex App) | No | No |
| **Codex App** | Desktop Agent | Desktop App | Desktop | Bundled | No | Yes (macOS, Windows) | No | No |
| **Gemini CLI** | CLI Agent | Terminal | Terminal | Yes (primary) | Powers Gemini Code Assist in VS Code | No | No | No |
| **Cursor** | AI IDE | IDE (VS Code fork) | Editor | Integrated terminal | Native (it IS the IDE) | Yes (desktop) | No | No |
| **Windsurf** | AI IDE | IDE (VS Code fork) | Editor | Integrated terminal | Native (it IS the IDE) | Yes (desktop) | No | No |
| **Aider** | CLI Agent | Terminal | Terminal | Yes (primary) | Via watch mode / comments | No | No | No |
| **Augment Code** | Hybrid | IDE plugin + CLI | Editor + Terminal | Yes (Auggie CLI) | VS Code, JetBrains | No | No | No |
| **Cody** | IDE Extension | IDE plugin | Editor | No agent CLI | VS Code, JetBrains, Neovim, Emacs, Eclipse, Visual Studio | No | Web demo | No |
| **Continue** | IDE Extension | IDE plugin | Editor | Via curl script | VS Code, JetBrains | No | No | No |
| **GitHub Copilot** | IDE Extension + CLI | IDE plugin + CLI | Editor | Yes (Copilot CLI) | VS Code, JetBrains, Neovim, Xcode, Eclipse, Visual Studio | No | GitHub.com | GitHub Mobile |
| **PearAI** | AI IDE | IDE (VS Code fork) | Editor | Integrated terminal | Native (it IS the IDE) | Yes (desktop) | No | No |
| **Zed** | AI Editor | Native Editor (Rust) | Editor | Bundled zed CLI | Native (it IS the editor) | Yes (desktop) | No | No |
| **Warp** | AI Terminal | Terminal (Rust) | Terminal | Native (it IS the terminal) | N/A (terminal-first) | Yes (desktop) | No | No |
| **Devin** | Autonomous Agent | Cloud sandbox + chat | Cloud dashboard + Slack/Teams | Yes (Devin CLI) | No (runs in cloud) | No | Yes (dashboard) | Slack/Teams mobile |
| **Replit Agent** | Browser IDE + Agent | Browser | Browser | No | Native (it IS the IDE) | No | Yes (primary) | Yes (mobile browser) |
| **Bolt.new** | Browser App Builder | Browser | Browser | No | No | No | Yes (primary) | No |
| **Lovable** | Browser App Builder | Browser | Browser | No | No | Yes (macOS) | Yes (primary) | No |
| **v0** | Browser UI Builder | Browser | Browser | No | No | No | Yes (primary) | No |
| **Cline** | IDE Agent | IDE extension | Editor | Yes (Cline CLI, headless) | VS Code, JetBrains | No | No | No |
| **Qodo** | Quality Platform | IDE plugin + CLI + Git bot | Editor + PRs | Yes (CLI) | VS Code, JetBrains | No | GitHub/GitLab PRs | No |
| **Kiro** | AI IDE + CLI | IDE (VS Code derived) + CLI | Editor + Terminal | Yes (Kiro CLI) | Native (it IS the IDE) | Yes (desktop) | No | No |
| **Open Interpreter** | Natural Language Shell | Terminal | Terminal | Yes (primary) | No | No | No | No |
| **Antigravity** | AI IDE + Manager | IDE (VS Code fork) + Manager dashboard | Editor + Manager surface | Integrated terminal | Native (it IS the IDE) | Yes (desktop) | No | No |
| **Droid** | CLI Agent | Terminal + SDK | Terminal | Yes (primary) | Via ACP | No | No | No |

---

### Section 3: AI Capabilities

| Tool | Chat | Autocomplete / Tab | Agent Mode | Inline Editing | Multi-file Editing | Sub-agents | Parallel Agents | Plan Mode | Cmd+K / Inline Cmd | Code Review |
|------|------|-------------------|------------|----------------|-------------------|------------|-----------------|-----------|-------------------|-------------|
| **Claude Code** | Yes | No | Yes (primary) | No (applies diffs) | Yes (core strength) | Yes (team/ subagents) | Yes (Agent Teams) | Yes (/think, clarify) | No | Yes (code review tool) |
| **Codex CLI** | Yes | No | Yes (core) | Yes (TUI) | Yes | Yes (subagents) | Yes (multi-agent v2) | Yes (Plan Mode) | No | Yes (/review) |
| **Codex App** | Yes | No | Yes (with computer use) | Yes | Yes | Yes | Yes (background) | Yes | No | Yes (GitHub review) |
| **Gemini CLI** | Yes | No | Yes | No | Yes | No | No | Yes (Plan Mode) | No | Yes (PR reviews) |
| **Cursor** | Yes | Yes (Tab + Supermaven) | Yes (Composer/Agent) | Yes (Cmd+K) | Yes (Composer) | No | No | Yes (Agent plan) | Yes (Cmd+K) | Yes |
| **Windsurf** | Yes | Yes (Supercomplete) | Yes (Cascade) | Yes | Yes (Cascade) | No | Yes (Simultaneous Cascades) | Yes (Cascade) | Yes (Cmd+I) | No |
| **Aider** | Yes (chat + code) | No | Yes (edit loop) | No (generates diffs) | Yes (repo map) | Weak/Strong model split | No | Yes (architect mode) | No | No |
| **Augment Code** | Yes | Yes | Yes (agent mode) | Yes | Yes | Yes (sub agents) | Yes (parallel agents) | Yes | No | No |
| **Cody** | Yes | Yes | No (see Amp) | No | Limited | No | No | No | No | No |
| **Continue** | Yes | Yes | Yes (Agent Mode) | Yes (Edit) | Yes (Agent mode multi-file) | No | No | No | No | No |
| **GitHub Copilot** | Yes | Yes (industry best) | Yes (Coding Agent) | Yes | Yes (Agent mode) | No | No | No | Yes (inline) | Yes (agentic code review) |
| **PearAI** | Yes (CMD+L) | Yes (Supermaven) | Yes (via Cline/Roo Code) | Yes (CMD+I) | Yes (agent integration) | Via Cline/Roo | No | No | Yes (CMD+I) | No |
| **Zed** | Yes | Yes (Zeta + Copilot) | Yes (Agentic Editing) | Yes (inline assist) | Yes | Yes (spawn_agent) | Yes (Parallel Agents, Threads sidebar) | No | No | No |
| **Warp** | Yes (Oz agent) | No (CLI suggestions) | Yes (Agent Mode) | No | Yes (agent multi-step) | No | Yes (Cloud agents) | No | No | No |
| **Devin** | Yes | No | Yes (full autonomous) | No (works in cloud) | Yes (core feature) | Yes (specialized sub-agents) | Yes (parallel instances) | Yes (interactive planning) | No | Yes (auto PR review) |
| **Replit Agent** | Yes | Yes | Yes (Agent modes) | Yes | Yes | Yes (auto task-split) | Yes (parallel agent builds) | Yes (Plan Mode) | No | No |
| **Bolt.new** | Yes | No | Yes (agentic build) | No | Yes | No | No | No | No | No |
| **Lovable** | Yes (Chat Mode Agent) | No | Yes (agentic chat) | Yes (Visual Edits) | Yes | No | No | Yes (Plan Mode) | Yes (Cmd+K palette) | No |
| **v0** | Yes | No | Yes (agentic build) | No | Yes | No | No | No | No | No |
| **Cline** | Yes | No | Yes (Plan + Act) | No (diff previews) | Yes (full codebase) | No | No | Yes (Plan Mode) | No | No |
| **Qodo** | Yes | No | Yes (multi-agent review) | No | Yes (PR review) | Yes (5 review agents) | Yes (parallel review agents) | No | No | Yes (primary feature) |
| **Kiro** | Yes | Yes | Yes (Autopilot Mode) | Yes | Yes (multi-root) | Yes | No | Yes (spec-driven) | No | No |
| **Open Interpreter** | Yes (chat) | No | Yes (autonomous exec) | No | Yes (filesystem access) | No | No | No | No | No |
| **Antigravity** | Yes | Yes | Yes (AgentKit 2.0) | Yes (Cmd+K) | Yes | Yes (Manager Agent pattern) | Yes (stable parallel exec) | Yes (artifacts) | Yes (Cmd+K) | No |
| **Droid** | Yes | No | Yes (full autonomy) | No | Yes | Yes (Code/Review/QA/Security Droids) | Yes (multi-model sampling) | Yes (spec mode) | No | Yes (Review Droid) |

---

### Section 4: Models & Providers

| Tool | Default Model(s) | Model Lock-in? | Multi-Model Support | BYOK (Bring Your Own Key) | Local Models (Ollama) | Max Context Window | Supported Providers |
|------|-----------------|----------------|---------------------|---------------------------|----------------------|--------------------|---------------------|
| **Claude Code** | Claude Opus 4.7 / Sonnet 4.6 | Yes (Claude only) | No (Claude models only) | No (requires Anthropic API or subscription) | No | 1M tokens (beta on Max plans) | Anthropic only |
| **Codex CLI** | GPT-5.5 / GPT-5.3-Codex | No | Yes (Claude, Gemini compatible via plugins) | Yes (OpenAI, others via config) | Yes (via OpenAI-compatible endpoints) | ~192K practical, 400K model max | OpenAI, any OpenAI-compatible API |
| **Codex App** | GPT-5.5 | No | Yes | Via ChatGPT subscription tiers | No | ~192K+ | OpenAI |
| **Gemini CLI** | Gemini 3.1 Pro / 3 Flash | No | No (Gemini only via CLI) | Yes (Gemini API Key) | No | 1M tokens (standard) | Google Gemini, Vertex AI |
| **Cursor** | Claude Sonnet + GPT-4o | No | Yes (Claude, GPT, Gemini, custom) | Yes (API keys) | No | Up to 200K (model dependent) | Anthropic, OpenAI, Google, Azure, OpenRouter, custom |
| **Windsurf** | SWE-1.6 / Adaptive Router | No | Yes (Claude, GPT, Gemini, Grok, Kimi, SWE) | No (bundled, quota-based) | No | Up to 200K | Anthropic, OpenAI, Google, xAI, custom models |
| **Aider** | Claude Sonnet 4.5/4.6 | No | Yes (any via LiteLLM) | Yes | Yes (Ollama, any local) | Up to 200K (model dependent) | Any provider via LiteLLM |
| **Augment Code** | Claude + GPT-4 mix | No | Yes (multi-model) | Yes | No | Not disclosed (~200K+) | Anthropic, OpenAI, custom |
| **Cody** | Claude Opus 4.7 / GPT-4o / Gemini | No | Yes (admin configurable) | Yes (Azure, Bedrock) | No | Up to 1M (Claude Sonnet 4) | Anthropic, OpenAI, Google, Mistral |
| **Continue** | Configurable | No | Yes (50+ models) | Yes (primary mode) | Yes (Ollama, LM Studio) | Model dependent | Any provider (50+) |
| **GitHub Copilot** | GPT-5.2 / Claude Sonnet | No | Yes (GPT, Claude, Gemini, Grok, Raptor) | No (GitHub-curated) | No | 128K-1M (model dependent) | Anthropic, OpenAI, Google, xAI |
| **PearAI** | Configurable | No | Yes (Claude, GPT, Gemini, DeepSeek) | Yes (free BYOK) | Yes (Ollama, LM Studio) | Model dependent | Anthropic, OpenAI, Google, DeepSeek, local |
| **Zed** | Configurable | No | Yes (Claude, GPT, Gemini, DeepSeek, +) | Yes | Yes (Ollama) | Model dependent | Anthropic, OpenAI, Google, Bedrock, Copilot, OpenRouter, Ollama, Vercel AI, xAI |
| **Warp** | GPT-4o / Claude | No (Oz routes dynamically) | Yes (agent supports multiple) | Yes (enterprise) | No | Model dependent | OpenAI, Anthropic, Google |
| **Devin** | Cognition-tuned (Claude + GPT) | Yes (Cognition-managed) | No (managed internally) | No | No | Not disclosed | Cognition (managed mix) |
| **Replit Agent** | Replit-tuned mix | No | Yes (Lite/Economy/Power tiers) | No (bundled) | No | Not disclosed | Replit-managed mix |
| **Bolt.new** | Claude + GPT + Gemini | No | Yes (model choice) | Yes (bolt.diy self-hosted) | Via bolt.diy | Model dependent | Anthropic, OpenAI, Google |
| **Lovable** | Gemini 3 Flash (default) | No | Yes (Claude Opus 4.7, GPT-5.5, Gemini 3.1 Pro, etc.) | No (bundled) | No | Model dependent | Anthropic, OpenAI, Google |
| **v0** | Vercel-managed | Yes (proprietary) | No | No | No | Not disclosed | Vercel-managed mix |
| **Cline** | Claude Sonnet 4.6 (recommended) | No | Yes (any model) | Yes (primary mode) | Yes (Ollama, LM Studio) | Model dependent | Anthropic, OpenAI, Google, Cerebras, Groq, Bedrock, Azure, Vertex, OpenRouter, any OpenAI-compatible |
| **Qodo** | Multi-model agent mix | No | Yes (via agents) | Yes (enterprise) | No | Not disclosed | Multi-provider |
| **Kiro** | Claude Sonnet 4.5 / Auto | No | Yes (Auto mode = mix of frontier) | Yes | No | Up to 1M | Anthropic, Amazon/managed |
| **Open Interpreter** | GPT-4o / Claude | No | Yes (any via config) | Yes | Yes (Ollama, LM Studio, Llamafile, Jan) | Model dependent | OpenAI, Anthropic, any local |
| **Antigravity** | Gemini 3.1 Pro / Flash | No | Yes (Claude, GPT, Gemini) | Yes (Pro/Enterprise) | No | Up to 1M | Google, Anthropic, OpenAI |
| **Droid** | Configurable mix | No | Yes (multi-model per subtask) | Yes (enterprise) | Yes | Model dependent | Multiple (configurable) |

---

### Section 5: Context & Indexing

| Tool | Max Context Window | Indexing Method | Context Files | Rules System | Repo Map / Codebase Understanding | Context Compaction | Custom System Prompt |
|------|--------------------|----------------|---------------|-------------|----------------------------------|--------------------|----------------------|
| **Claude Code** | 1M tokens (Max plans) | Hooks + manual context | CLAUDE.md, .claude/settings.json | Hooks (17 lifecycle events) | No AST-based map (relies on context window) | No (manual context mgmt) | Via CLAUDE.md |
| **Codex CLI** | ~192K practical | Diff-based "forgetting" + SQLite | config.toml, AGENTS.md | Rules + deny-read globs | Skills + @mentions for fuzzy file search | Yes (auto-summarizes near context limit) | Via config.toml |
| **Codex App** | ~192K+ | Same as CLI | AGENTS.md, Memory (preview) | Rules system | Memory + cross-session context | Yes | Via settings |
| **Gemini CLI** | 1M tokens (standard) | Google Search grounding | GEMINI.md | Custom context files | Repo-level understanding via context window | Yes (checkpointing) | Via GEMINI.md |
| **Cursor** | Up to 200K | Semantic repo indexing | .cursorrules, .cursor/rules/ | Rules system (.cursor/rules/) | Codebase-wide indexing with embeddings | No | Via .cursorrules |
| **Windsurf** | Up to 200K | Codemaps (repo structure index) | .windsurfrules | Memory & Rules | Codemaps + real-time awareness of edits, terminal, clipboard | No | Via .windsurfrules |
| **Aider** | Up to 200K | Repo Map (Tree-sitter AST analysis) | .aider.conf.yml, .aider.chat.history.md | No explicit rules system | Repo Map: Tree-sitter AST, ranked by reference frequency, SQLite cached | No (manual via /drop) | Via config |
| **Augment Code** | Not disclosed (~200K+) | Semantic codebase understanding engine | Steering files | Context Engine | Deep enterprise codebase understanding | Not disclosed | Via config |
| **Cody** | Up to 1M (Claude Sonnet 4) | SCIP code graph (Sourcegraph) | Context controls (@-mention repos/files/symbols) | Admin-defined context filters | Code graph: indexes every symbol/reference/dependency across hundreds of repos | No | Admin configurable |
| **Continue** | Model dependent | Local codebase indexing | .continue/rules/ | Custom Rules (.continue/rules/) | @codebase context retrieval | No | Via config.json |
| **GitHub Copilot** | 128K-1M | Workspace indexing | .github/copilot-instructions.md, instructions.md | Custom instructions / agents | Project-level context via agent mode | No | Via instructions.md |
| **PearAI** | Model dependent (BYOK) | Local indexing (Continue fork) | Config files | Via Continue rules fork | @codebase retrieval | No | Via config |
| **Zed** | Model dependent | LSP + AST | Config-based | No explicit rules | LSP semantic tokens + AST | No | Via config |
| **Warp** | Model dependent | Terminal context | Project config | No | Terminal session context only | No | Via settings |
| **Devin** | Not disclosed | Knowledge Base Memory | Org/team config | Enterprise config | Learns codebase conventions over time | No | Enterprise config |
| **Replit Agent** | Not disclosed | Multi-file intelligence | Project config | Rules system | Project-wide dependency/pattern analysis | No | Via project settings |
| **Bolt.new** | Model dependent | Project files | Project config | Via prompt | Workspace file awareness | No | Via prompt |
| **Lovable** | Model dependent | Cross-Project Referencing | Project settings | Via prompt | @mentions across workspace projects | No | Via settings |
| **v0** | Not disclosed | GitHub repo import | Repo + env config | Via prompt | Full repo understanding via import | No | Via prompt |
| **Cline** | Model dependent | Workspace snapshots | .clinerules | Plan/Act mode approval | Full codebase read/write with diff previews | No | Via .clinerules |
| **Qodo** | Not disclosed | Deep Codebase Context Engine | PR history + centralized rules | Centralized Rule System (auto-evolving) | Multi-repo codebase indexing, cross-module dependency detection | No | Admin configurable |
| **Kiro** | Up to 1M | Multi-root workspace | .kiro/specs/, .kiro/steering/ | Steering Files (.kiro/steering/) | Living specification documents + property-based testing | Via checkpoints (step rewind) | Via .kiro/steering/ |
| **Open Interpreter** | Adjustable (1000-3000 tokens rec. for local) | Filesystem access | Profiles (.py files) | User message templates | Direct filesystem awareness | Adjustable context_window | Via Python API / config |
| **Antigravity** | Up to 1M | Workspace + Browser agent | AGENTS.md | Rules (global/per-workspace) + Workflows | AgentKit 2.0 visual builder | Not disclosed | Via AGENTS.md |
| **Droid** | Not disclosed | HyperCode + ByteRank | config.yml / config.json | Specification Mode | Multi-resolution codebase representation | Not disclosed | Via config |

---

### Section 6: Tools & Actions

| Tool | File Ops (Read/Write) | Shell / Bash | Web Search | Web Fetch | Git Integration | MCP Support | Browser / Computer Use | Image Input | Voice Input | Terminal Multiplexing |
|------|-----------------------|-------------|------------|-----------|-----------------|-------------|------------------------|-------------|-------------|----------------------|
| **Claude Code** | Yes (full) | Yes | Via tool (optional) | Via tool | Yes (native, worktrees) | Yes (stdio + HTTP) | No (text only) | Yes (paste images) | No | No |
| **Codex CLI** | Yes (full) | Yes (sandboxed) | Yes (cached/live, first-party) | Yes | Yes (native) | Yes (full, 9000+ plugins) | Yes (in-app browser + Computer Use on macOS) | Yes (paste screenshots) | Yes (WebRTC Realtime Voice) | Yes (multiple terminal tabs) |
| **Codex App** | Yes (full) | Yes (multi-tab) | Yes | Yes | Yes (worktree + GitHub review) | Yes | Yes (Computer Use + in-app browser) | Yes (image gen too) | Yes (voice dictation) | Yes (multi-tab terminal) |
| **Gemini CLI** | Yes (full) | Yes | Yes (Google Search grounding) | Yes | Yes (PR reviews, issue triage) | Yes (stdio) | No | No | No | No |
| **Cursor** | Yes (full) | Yes (integrated terminal) | Yes (via @web) | Yes | Yes (via VS Code Git) | Yes (stdio + HTTP) | Yes (browser preview) | Yes | No | VS Code terminal |
| **Windsurf** | Yes (full) | Yes (up to 20 tool calls/prompt) | Yes (Web Search tool) | Yes | Yes (workspace Git) | Yes (stdio) | Yes (Browser + Previews + Deploys) | Yes | Yes (voice input) | Integrated terminal |
| **Aider** | Yes (edit loop) | Yes (auto-test/lint) | Yes (add URLs as context) | Yes | Yes (native, auto-commit, /undo) | No (not natively) | No | Yes (screenshots, web pages) | Yes (voice-to-code) | No |
| **Augment Code** | Yes (full) | Yes (agentic CLI) | No | No | Yes | Yes | No | No | No | No |
| **Cody** | Yes (limited) | No (no agent) | No | No | No | Via admin config | No | No | No | No |
| **Continue** | Yes (Agent Mode) | Yes | No | No | No (no git tools) | Yes | No | No | No | No |
| **GitHub Copilot** | Yes (full agent mode) | Yes (agent terminal) | Yes (agent mode) | Yes | Yes (deep: issues, PRs, repos, code review) | Yes (stdio + HTTP) | No (cloud agent runs remotely) | Yes | No | VS Code terminal |
| **PearAI** | Yes (full) | Yes | Yes (Perplexity integration) | Yes | Yes (VS Code Git + /commit) | Via integrations | No | No | No | VS Code terminal |
| **Zed** | Yes (full) | Yes (integrated terminal) | Via agent tools | Via agent tools | Yes (shared Git worktrees for collab) | Yes (MCP on remotes) | No | No | Yes (audio calls) | Integrated terminal |
| **Warp** | Yes (agent) | Yes (native, primary) | Yes (Oz agent) | Yes | Via CLI agent | Via integrations | No | Yes (image attachments) | Yes (voice) | Yes (it IS a terminal) |
| **Devin** | Yes (in cloud sandbox) | Yes (in cloud sandbox) | Yes (agent capability) | Yes (agent capability) | Yes (GitHub/GitLab PRs) | Via integrations | Yes (cloud browser) | No | No | Cloud sandbox |
| **Replit Agent** | Yes (full) | Yes | Yes | Yes | Yes | Via integrations | Yes (embedded browser) | Yes | No | Integrated shell |
| **Bolt.new** | Yes (full) | Yes (Node.js runtime) | No | No | Yes (export) | No | Yes (browser preview) | No | No | No |
| **Lovable** | Yes (full, Dev Mode) | Yes (edge functions) | Via connectors | Via connectors | Yes (GitHub sync) | Yes (MCP Chat Connectors) | Yes (Browser Testing) | Yes | Via connector | No |
| **v0** | Yes (full web) | No (frontend only) | No | No | Yes (deep: auto-branch, auto-commit, PRs, Git panel) | No | Yes (Vercel Sandbox VM) | Yes | No | No |
| **Cline** | Yes (full codebase) | Yes (terminal integration) | Yes | Yes | Yes (Git operations) | Yes (stdio + HTTP) | Yes (headless browser, Computer Use) | Yes | No | Integrated terminal |
| **Qodo** | Yes (PR files) | Yes (automation) | No | No | Yes (GitHub, GitLab, Bitbucket, Azure DevOps) | Via integrations | No | No | No | No |
| **Kiro** | Yes (full) | Yes | Yes | Yes | Yes (AI commit messages) | Yes (native, local + remote) | No | Yes (multimodal input) | No | Multi-root workspace |
| **Open Interpreter** | Yes (full filesystem) | Yes (native: Python, JS, Shell, R, etc.) | No | No | No | No | Yes (Local OS Mode: mouse, keyboard, screen) | Yes (Local Vision with Moondream) | No | No |
| **Antigravity** | Yes (full) | Yes (Terminal Execution Policy) | Yes | Yes | Yes | Yes (Pro, up to 10 connections) | Yes (embedded Chrome browser agent) | Yes | No | Integrated terminal |
| **Droid** | Yes (full) | Yes | Via agent tools | Via agent tools | Yes (Git AI: AI Blame, prompt saving) | Via integrations | No | No | No | No |

---

### Section 7: Safety & Sandboxing

| Tool | Sandboxing Level | Sandbox Technology | Default Safety State | Permissions System | Approval Modes | Hooks / Policy Engine | Network Control | Secret Protection | Audit Trail |
|------|-----------------|--------------------|---------------------|--------------------|----------------|---------------------|--------------------|------------------|------------|
| **Claude Code** | Application-level | Hooks system + permission prompts + opt-in Bubblewrap/Seatbelt | Disabled by default | Granular per-action prompts | Allow/deny per action + auto-approve toggles | 17 lifecycle hook events (programmable governance) | Via validating proxies (manual) | Limited (env vars inherited) | Via hook logging |
| **Codex CLI** | Kernel-level (gold standard) | Seatbelt (macOS), Landlock + seccomp + Bubblewrap (Linux), Windows Sandbox | **Enabled by default** (workspace-write) | Granular: on-request, untrusted, never | Suggest mode → Auto-Edit → Full-Auto, switchable mid-session | Auto Review agent (checks for exfiltration, credentials) | **Off by default**, must explicitly enable | Protected paths (.git/.agents/.codex read-only) + deny-read globs | Yes (OTel Telemetry, enterprise managed config) |
| **Codex App** | Kernel-level | Same as CLI + macOS Screen Recording/ Accessibility permissions | Enabled by default | Per-action + computer use permissions | Allow/deny per action | Same as CLI | Off by default | macOS permission gating | OTel + enterprise |
| **Gemini CLI** | Kernel-level (opt-in) | Docker/Podman (Linux), Seatbelt (macOS), 5 predefined profiles | Disabled by default (requires --sandbox flag) | Per-action prompts | Allow/deny per action | No (no programmable hooks) | Via opt-in Docker/Seatbelt | Limited | Via checkpointing |
| **Cursor** | Editor-level | Editor process model + permission prompts | N/A (inherently permissive) | Action-level prompts | Ask before commands/edits outside project | No hook system | Some config for tool access | Weak (CVEs: CVE-2026-22708 allowlist bypass) | No |
| **Windsurf** | Editor-level | Editor process + permission prompts | N/A (inherently permissive) | Per-action prompts | Allow/deny per action | No hooks | Some tool access config | Limited | Named checkpoints + reverts |
| **Aider** | None (local exec) | No sandbox | No sandbox | --yes flag for unattended | Per-command manual approval or auto | No hooks | No | Limited (env vars inherited) | Via Git commits |
| **Augment Code** | Application-level | Enterprise security controls | Enterprise configurable | Team-level permissions | Configurable | SSO, OIDC, SCIM, SOC 2 Type II, CMEK, ISO 42001 | SIEM integration | Data residency options, granular access controls | Comprehensive audit trails |
| **Cody** | Application-level (enterprise) | Self-hosted deployment + context filters | Enterprise configurable | Admin-defined (model whitelisting, repo access) | Admin-configured policies | SSO/SAML/SCIM, context filters | Self-hosted option | Zero data retention policy | Audit logs |
| **Continue** | None (local/API) | No sandbox | N/A | Per-API key | User-controlled | No hooks | Through chosen provider | Configurable via API keys | No |
| **GitHub Copilot** | Editor-level + Cloud | Editor process + GitHub Actions sandbox | Standard protections | Organization policies | Configurable per-org | Enterprise: org policies, IP indemnity | Via GitHub Actions | IP indemnity included | Enterprise audit |
| **PearAI** | None (local/BYOK) | No sandbox | N/A | BYOK-controlled | User-controlled | No hooks | User configurable | Zero data retention | No |
| **Zed** | Editor-level | Editor process + optional remote | N/A | Configurable | User-controlled per provider | ACP (Agent Client Protocol) for agent connections | Via provider config | User-controlled (BYOK) | No |
| **Warp** | Application-level | Local detection (no data leaves until Enter) + SOC 2 | Good defaults | Per-command approval | Approve all commands before execution | SOC 2 compliant, zero data retention with providers | User-controlled | No training on customer data | Session history |
| **Devin** | Cloud VM | Full cloud sandbox (isolated VM, not on your machine) | Always sandboxed | Task-level approval | Review/modify/approve plan before execution | Enterprise: VPC deployment, SAML SSO, SLAs | Isolated cloud VM | Not on your machine | Session replay |
| **Replit Agent** | Cloud sandbox + Browser | Replit cloud infrastructure | Cloud sandboxed | Per-task review | Review & approve before merge | Security Center 2.0, Auto Protect, Private Publishing | Cloud infrastructure | External Access Tokens for private apps | Security Center |
| **Bolt.new** | Browser sandbox | WebContainer (StackBlitz) | Browser sandboxed | N/A | User-initiated actions | N/A | Browser sandbox | Browser sandbox | No |
| **Lovable** | Cloud + Browser | Cloud sandbox | Sandboxed | Plan approval | Review plan before code | Security Scan, dependency scanning, secrets overview, publishing controls | Cloud sandbox | 2FA, SCIM provisioning (Enterprise) | Audit logs (Enterprise) |
| **v0** | Vercel Sandbox (lightweight VM) | Vercel Sandbox | Sandboxed by default | GitHub PR flow (never pushes to main) | Auto-branch, PR-based workflow | SOC 2 Type 2, SSO, Okta SCIM, access policies | Vercel Sandbox VM | Enterprise: Snowflake/AWS with access controls, no prompting credentials | Enterprise access policies |
| **Cline** | Application-level | Workspace snapshots + permission gating | Checkpoint-based safety | Per-step approval workflow | Plan/Act modes with step-by-step oversight | Zero-trust: code never touches Cline servers | User-controlled | Local/air-gapped model option | Visible token usage per step |
| **Qodo** | Application-level | Multi-agent review pipeline | Agent-based review | Per-finding review | Inline PR comments | Centralized Rule System | Through Git platform | PR-based, not local files | PR history awareness |
| **Kiro** | Application-level | Checkpointing (rewind any step) | Checkpoint-based | Per-task approval | Autopilot toggle | Agent Hooks (file saves, creation, prompt submit, spec task exec) | Through AWS IAM | AWS IAM Identity Center | Centralized billing in AWS Console |
| **Open Interpreter** | None (full local access) | No sandbox (this is the point and the risk) | No sandbox (full system access) | Optional approval prompts | Approve code before running | No hooks | No | No (full local access, high risk) | No |
| **Antigravity** | Application-level | Terminal Execution Policy (Off/Auto/Turbo) + Browser URL allowlist | Configurable | Artifact Review Policy | Always proceed / Agent decides / Always request review | Rules system + Workflows + AgentKit 2.0 fallback config | Browser URL allowlist | AGENTS.md agent definitions | Real-time debug view |
| **Droid** | Application-level | DroidShield (real-time static analysis) | Configurable autonomy | Granular: low (manual each action) to high (full auto) | Specification Mode approval | Custom Droids for security review | Auto-run for reversible commands | DroidShield catches pre-commit | Every action logged with reasoning |

---

### Section 8: UX & Customization

| Tool | Vim Mode | Themes | Keybindings | Customizability | Status Indicators | Diff Preview | Progress Display | Token Counter | Time Estimate | File Navigation |
|------|----------|--------|-------------|-----------------|-------------------|-------------|------------------|--------------|---------------|-----------------|
| **Claude Code** | Via terminal | Terminal themes | Terminal-native | CLAUDE.md + hooks | Streamed output | Inline SEARCH/REPLACE blocks in terminal | Streaming text | Via /cost or API console | No | N/A (terminal) |
| **Codex CLI** | Yes (/vim in TUI composer) | Terminal themes | Terminal-native + /vim modal | config.toml, custom agents, skills, plugins | Spinner + step counter | Inline diff in TUI | Spinner + streaming + step indicator | Yes (per-request tracking) | No | Fuzzy file search (@mentions) |
| **Codex App** | Via terminal | App themes | App defaults | Full config + Memory personalization | Rich visual status | Rich inline diff with accept/reject | Progress bars, step counters, spinners | Yes | No | File sidebar with rich previews (PDF, spreadsheets, slides) |
| **Gemini CLI** | Terminal only | Terminal themes | Terminal-native | GEMINI.md config files | Streaming text | Text-based diffs in terminal | Streaming text + checkpointing | No | No | N/A (terminal) |
| **Cursor** | Yes (VS Code) | Full VS Code themes + custom | Full VS Code keybindings | Full VS Code customization | Status bar, inline indicators | Rich inline diff (red/green) with per-hunk accept/reject | Agent progress panel | No | No | File tree, Cmd+P, symbol search, breadcrumbs, tabs |
| **Windsurf** | Yes (VS Code) | Full VS Code themes | Full VS Code keybindings | Full VS Code customization | Status bar, inline indicators | Rich inline diff with accept/reject | Cascade progress with todo lists | Yes (quota dashboard, per-prompt) | No | File tree, Cmd+P, symbol search, breadcrumbs, tabs |
| **Aider** | Terminal only | Terminal themes | Terminal-native | .aider.conf.yml | Streamed text | Text-based diff in terminal | Streaming text | Via --model-metadata | No | N/A (terminal) |
| **Augment Code** | Via IDE | IDE themes | IDE defaults | IDE customization + steering files | IDE status bar | IDE native diff | Streaming + visual progress in Auggie CLI | Via credits dashboard | No | IDE file tree |
| **Cody** | Via IDE | IDE themes | IDE defaults | IDE customization | IDE status bar | IDE native diff | Streaming chat | No | No | IDE file navigation |
| **Continue** | Via IDE | IDE themes | IDE keybindings | config.json + rules + custom slash commands | IDE status bar | Rich inline diff (accept/reject) in VS Code | Streaming chat | Per-API usage (own keys) | No | IDE file navigation |
| **GitHub Copilot** | Via IDE | IDE themes | IDE defaults | instructions.md + custom agents | IDE status bar | Inline ghost text for completions, rich diff for agent | Ghost text streaming | Via usage dashboard | No | IDE file navigation |
| **PearAI** | Yes (VS Code) | Full VS Code themes | Full VS Code keybindings | Full VS Code customization | IDE status bar | Rich inline diff (CMD+I) | Streaming chat | Via own API keys | No | File tree, Cmd+P, tabs |
| **Zed** | Yes (Vim mode) | Rich theming | Configurable | Open source full edit | GPU-rendered status | Split (side-by-side) diffs, inline assist | Parallel agent Threads sidebar | No | No | File tree, tabs |
| **Warp** | Terminal only | Terminal themes | Configurable | Full terminal customization + natural language detection | Agent task lists, inline indicators | Command output display | Task lists auto-updating, streaming output | No | No | Terminal workflow |
| **Devin** | N/A (web) | Web dash | N/A | Enterprise config | Session progress dashboard | PR-based diff on GitHub/GitLab | Interactive planning + session replay | No | ACU (Agent Compute Unit = ~15 min blocks) | Web dash session list |
| **Replit Agent** | N/A (browser) | Browser-based themes | Browser defaults | Project config | Agent mode, Kanban-style tracking | Inline diff in browser editor | Kanban-style task tracking | No | No | File tree, tabs |
| **Bolt.new** | N/A (browser) | Browser defaults | Browser defaults | Project config | Progress bar | Inline browser preview | Progress bar | Token counter (daily limits) | No | File tree |
| **Lovable** | N/A (browser) | Browser defaults | Cmd+K palette | Full project config + connectors | Progress bar | Visual Edits inline | Prompt Queue + progress tracking | Credit counter | No | Command palette |
| **v0** | N/A (browser) | VS Code-style editor themes | Browser defaults | Project + env config | Git panel | Inline editor diff | Auto-commit + PR creation progress | No | No | Full VS Code-style editor, GitHub repo file tree |
| **Cline** | Via IDE | IDE themes | IDE defaults | .clinerules + full OSS transparency | Checkpoint status | Diff preview (per-step approval) | Step-by-step streaming with approval gates | Yes (visible token usage per step) | No | IDE file tree |
| **Qodo** | Via IDE | IDE themes | IDE defaults | Centralized Rule System | PR comment inline status | Inline PR diff comments | Multi-agent review progress per agent | No | No | PR file tree |
| **Kiro** | Via IDE / terminal | IDE themes | IDE defaults | .kiro/steering/ + Agent Hooks | Checkpoint indicators | Code diffs with approve/step/edit | Spec-driven task progress (In Progress → Done) | Yes (per-prompt credit usage) | No | Multi-root workspace + file tree |
| **Open Interpreter** | Terminal only | Terminal | Terminal-native | Python API + templates | Streaming output | Terminal text output | Streaming text | No (adjustable context_window) | No | N/A (terminal + filesystem) |
| **Antigravity** | Via IDE | Full IDE themes | Expanded shortcuts (Ctrl+Enter for agents) | Visual Agent Builder + AGENTS.md + Rules + Workflows | Real-time debug view | Inline preview before applying changes | Artifacts (task lists, implementation plans, walkthroughs) | Yes (real-time quota dashboard with 80% alerts) | No | Redesigned sidebar + file tree |
| **Droid** | Terminal only | Terminal | Terminal-native | config.yml + SDK + Custom Droids | Streaming output | Text-based diff in terminal | Step-by-step autonomous execution | No | No | N/A (terminal) |

---

### Section 9: Collaboration

| Tool | Multiplayer / Real-time Collab | Session Sharing | Team Features | Pair Programming | Knowledge Sharing | Multi-repo Support | Org Management |
|------|-------------------------------|-----------------|---------------|------------------|-------------------|--------------------|-----------------|
| **Claude Code** | No | No | Agent Teams (parallel agents, same task) | No | No | Via Git worktrees | Enterprise deployment |
| **Codex CLI** | No | Resume/fork sessions | Managed config (enterprise), pooled credits | No | Memory (preview) | Via multi-root + SSH to devboxes | Enterprise: requirements.toml enforcement |
| **Codex App** | No | Resume/fork from any message | Team credits via ChatGPT plans | No | Memory + thread automations | Via multi-tab terminal + SSH | Enterprise |
| **Gemini CLI** | No | Checkpoint save/resume | Team via Gemini Code Assist | No | GEMINI.md project files | No | Enterprise ($45/user/mo) |
| **Cursor** | No | No | Teams plan ($40/user/mo) | No | .cursorrules sharing | Workspace-based | Business plan with admin |
| **Windsurf** | No | Spaces (bundle sessions, files, context) | Teams ($40/user/mo): admin dashboard, analytics, Devin Cloud | No | Spaces + Cascade memory | Workspace-based | Teams + Enterprise (SSO, RBAC, hybrid deploy) |
| **Aider** | No | .aider.chat.history.md (markdown file) | No | No | Repo Map caching | Via file system | No |
| **Augment Code** | Yes (session sharing) | Session sharing, export as markdown | Team plans with pooled credits | No | Context Engine across team | Multi-repo context engine | SSO, OIDC, SCIM, SOC 2, ISO 42001 |
| **Cody** | No | No | Enterprise only ($59/user/mo) | No | Code graph index shared across team | Yes (across hundreds of repos, core strength) | SSO, SAML, SCIM, self-hosted |
| **Continue** | No | No | Team plan ($20/seat/mo) | No | Shared custom agents + rules | Workspace-based | Custom enterprise pricing |
| **GitHub Copilot** | No | No | Business ($19/user/mo) + Enterprise ($39/user/mo) | No | GitHub.com integration | Organization-level | Org policies, IP indemnity, knowledge bases |
| **PearAI** | No | No | Enterprise (contact sales) | No | Open source transparency | VS Code workspace | Enterprise: SSO, team licenses |
| **Zed** | Yes (primary feature, built from ground up) | Channels & Calls + shared projects | Zed Guild community contributions | Yes (real-time pair programming) | Real-time following AI agents (same as human collab) | Multi-root + Dev Containers + SSH remoting | ACP Registry for community agents |
| **Warp** | No | Conversation history | Cloud agents for parallel team execution | No | Agent conversation sharing | Multi-repo via Cloud agents | SOC 2 compliant |
| **Devin** | No | Session replay | Team plan ($500/mo): 250 ACUs + pooled | No | Knowledge Base Memory | Enterprise: VPC deployment | SSO, SAML, SLAs |
| **Replit Agent** | Yes (task-based, multi-request) | Kanban-style task tracking | Pro/Enterprise: team collaboration | Yes (task-based) | Shared project + design context | Multi-file project intelligence | Enterprise: Security Center 2.0 |
| **Bolt.new** | No | No | No | No | No | No | No |
| **Lovable** | Yes (Multiplayer & Workspaces) | Cross-Project Referencing | Teams (up to 20 users, shared credit pool) | Yes (real-time collaboration) | Personal workspaces with unlimited collaborators | Workspace-based | Enterprise: SSO, SCIM, audit logs, 2FA |
| **v0** | Yes (collaborative editing) | Shared projects, Team/Business plan | Enterprise roles: Builder, Creator, Viewer | Yes (Git-based) | Team/Unlisted/Public visibility, Git panel for non-engineers | Full GitHub repo import | SSO, Okta SCIM, Access Groups |
| **Cline** | No | No | Enterprise: SSO, audit trails, self-hosted, VPC | No | Open source codebase | Workspace-based | Enterprise policies + global policies |
| **Qodo** | No | PR comment-based sharing | Team plans | No | Centralized Rule System + PR History Awareness | Multi-repo codebase indexing | SSO, SLAs |
| **Kiro** | No | Checkpoint-based resume | Team Plans (Pro, Pro+, Power): AWS IAM Identity Center, centralized billing | No | .kiro/specs/ (living docs as Markdown) | Multi-root workspace support (monorepos, submodules) | AWS IAM Identity Center |
| **Open Interpreter** | No | No | No | No | No | No | No |
| **Antigravity** | No (but Manager Surface orchestrates multiple agents) | Artifacts (verifiable deliverables) | Enterprise: team collaboration, quota dashboard, per-member breakdowns | No | AGENTS.md + Rules + Workflows | Yes (multiple workspaces simultaneously) | Enterprise: SAML/SSO, on-premises |
| **Droid** | No | Session SDK for resumption | Enterprise via Factory platform | No | Specification Mode + prompt saving | Multi-repo via HyperCode | Enterprise |

---

### Section 10: CI/CD & Automation

| Tool | Headless Mode | JSON / Structured Output | GitHub Actions | CI/CD Pipeline Support | Scripting / API | Scheduling | Background / Async Execution | Non-interactive Mode | Docker Support |
|------|--------------|--------------------------|----------------|-----------------------|-----------------|------------|------------------------------|---------------------|-----------------|
| **Claude Code** | Yes (--print flag) | Yes (JSON output mode) | Yes (third party) | Possible via scripts | No public API | No | Agent Teams (parallel, not async) | Yes (--print, -p) | Via hooks |
| **Codex CLI** | Yes (--print) | Yes (JSON, stream-json) | Yes (first-party, plugins) | Yes (native support) | config.toml + SDK | Thread automations (days/weeks) | Yes (cloud exec, async subagents) | Yes (--yolo, --print) | Yes (Dev Container reference impl) |
| **Codex App** | Yes | Yes | Yes | Yes | Via automations | Yes (thread automations) | Yes (background computer use, agents in background) | Yes | Yes |
| **Gemini CLI** | Yes (--output-format json / stream-json) | Yes (JSON, stream-json) | Yes (GitHub workflows) | Yes (non-interactive mode) | Shell scripting | No | No | Yes (--output-format) | Yes (opt-in --sandbox with Docker) |
| **Cursor** | Limited (no native headless) | No structured output | Via terminal scripts | Possible via terminal | No API | No | No | No | Dev Container support |
| **Windsurf** | No | No | Via terminal | Not designed for CI/CD | No API | No | No | No | Dev Container support |
| **Aider** | Yes (--yes flag) | No (text output) | Possible via scripts | Yes (dependency bumps, doc gen) | Python API | No | No | Yes (--yes) | No |
| **Augment Code** | Yes (--print, --quiet) | Pipes in/out | Yes (official actions: PR Description, PR Review) | Yes (service accounts for CI/CD) | Service accounts (non-human identities) | No | No | Yes (--print, --quiet) | Enterprise self-hosted |
| **Cody** | No | No | No | No | No | No | No | No | No |
| **Continue** | Limited (via CLI curl) | No | No | Not designed for CI/CD | No API | No | No | No | No |
| **GitHub Copilot** | Yes (cloud agent) | No (GitHub-focused) | Yes (Code review runs on GitHub Actions) | Yes (coding agent turns issues into PRs) | GitHub Actions + cloud agent | No | Yes (cloud agent: research, plan, write code offline) | Yes (via agent mode) | Codespaces + Dev Containers |
| **PearAI** | No | No | No | Not designed for CI/CD | No API | No | No | No | No |
| **Zed** | Yes (via ACP) | ACP protocol | Via ACP agents | Via ACP-connected agents | ACP SDK | No | No | Yes (ACP agent connections) | Dev Containers, SSH remoting |
| **Warp** | Yes (cloud agents) | No | Via cloud agents | Yes (cloud agents, scheduled agents) | Platform API | Yes (cron-like scheduling) | Yes (Cloud agents platform) | Yes (cloud execution) | Via cloud agents |
| **Devin** | Yes (cloud, fully autonomous) | PR-based output | Yes (via GitHub/GitLab integrations) | Designed for autonomous CI/CD | Slack/Teams bot + CLI | No | Already async (runs in cloud independently) | Always autonomous | Cloud VM (not your Docker) |
| **Replit Agent** | No | No | Via Git export | Limited | No API | No | Yes (Agent builds while you plan/design) | No | Cloud infrastructure |
| **Bolt.new** | No | No | No | No | No | No | No | No | No |
| **Lovable** | No | No | Via GitHub sync | Limited | API connectors | No | No | No | Lovable Cloud (managed) |
| **v0** | No | No | Auto-PR workflow | Yes (full git workflow: auto-branch, auto-commit, PR-to-main) | Vercel platform auto-deploy | No | No | No | Vercel Sandbox (lightweight VM) |
| **Cline** | Yes (Cline CLI, headless) | No | Yes (automation scripts) | Yes (Cline CLI for pipelines) | CLI + MCP | No | No | Yes (headless Cline CLI) | Enterprise self-hosted |
| **Qodo** | Yes (CLI, automation) | No structural output | Yes (GitHub Actions + GitLab CI + Bitbucket + Azure DevOps) | Yes (automated quality workflows) | CLI + PR-based API | No | Yes (runs asynchronously on PRs) | Yes (CLI) | Enterprise |
| **Kiro** | Yes (Kiro CLI) | No | Via AWS | AWS integration | AWS-console managed | Agent Hooks + spec tasks | No | Yes (CLI) | Via AWS |
| **Open Interpreter** | Yes (--yes or Python API) | No | Via Python scripting | Yes (Python API automation) | Python API | No | No | Yes (script mode) | No |
| **Antigravity** | No | No | Via terminal | Limited | No API | No | No (Manager Surface orchestrates parallel, not async) | No | No |
| **Droid** | Yes (full autonomy levels) | Yes (Zod schemas) | Yes (automated PR review via GitHub Actions) | Yes (SDK: @activade/droid-sdk) | TypeScript SDK + CLI | No | Yes (background execution with process management) | Yes (auto-run mode, --yes flag) | Via enterprise |

---

### Section 11: Extensibility

| Tool | Plugin System | Marketplace / Registry | MCP Integration | Custom Agents | API / SDK | Custom Commands / Slash | Extensions / Add-ons | App Connectors | Skills / Reusable Bundles |
|------|--------------|------------------------|-----------------|---------------|-----------|-------------------------|----------------------|----------------|--------------------------|
| **Claude Code** | No (hooks-based) | No marketplace | Full (stdio + HTTP) | Subagents | No public API | Custom slash commands | Hooks (17 events) | No | Yes (Skills) |
| **Codex CLI** | Yes (plugins) | Yes (curated marketplace, 9000+ MCP + 90 proprietary) | Full (MCP client + server mode) | Subagents + Custom agents | config.toml + MCP | Yes (Skills, Goals) | Plugins (installable bundles) | MCP connectors | Yes (Skills: open spec at agentskills.io) |
| **Codex App** | Yes (90+ plugins) | Yes (marketplace) | Full | Custom agents + Memory | Via automations | Skills + Goals | Atlassian Rovo, CircleCI, CodeRabbit, MS Suite, Neon, Render, SSH | Yes | Yes (Skills) |
| **Gemini CLI** | No (MCP-based) | No marketplace | Full (stdio) | No custom agents | No SDK | No | GEMINI.md context | MCP connectors | No |
| **Cursor** | No (VS Code extension market via fork) | VS Code marketplace (partial) | Full (stdio + HTTP) | No (no subagents) | No API | Custom commands | VS Code extensions (limited compatibility) | No | No |
| **Windsurf** | No (proprietary) | No marketplace | Full (stdio) | No (Cascade agent is monolithic) | No API | Cascade tool calling | MCP integrations | MCP connectors | No |
| **Aider** | No (model plugins via LiteLLM) | No marketplace | No (not natively) | Architect/Editor model split | Python API | /commands (built-in) | 100+ language support via LiteLLM | No | No |
| **Augment Code** | Via MCP | No marketplace | Full | Sub agents + Custom commands | Service accounts API | Custom commands / Custom Droids | MCP connectors | GitHub, Linear, Jira | No |
| **Cody** | No (enterprise extension only) | No | Via admin config | No | No | No | Sourcegraph extensions | No | No |
| **Continue** | No (OSS customization) | No marketplace | Full | Custom rules + slash commands | config.json | Custom slash commands, custom model config | 50+ model providers | No | No |
| **GitHub Copilot** | Yes (extensions preview) | GitHub Marketplace (existing) | Full (stdio + HTTP) | Custom agents (custom instructions) | GitHub Actions + API | Custom instructions | Extensions via GitHub Marketplace | MCP connectors | No |
| **PearAI** | Yes (VS Code extensions) | VS Code marketplace | Via integrations | Cline / Roo Code integration | No | Custom slash commands (/commit, /edit, /comment, /test) | Continue + Roo Code + Cline + Mem0 + Supermaven + Perplexity | No | No |
| **Zed** | Yes (ACP) | ACP Registry (community agents) | Full (MCP on remotes) | ACP agents (Claude Agent, Codex, OpenCode, Gemini CLI compatible) | ACP protocol + Zeta open model | No | Extensions via ACP | MCP + ACP connectors | No |
| **Warp** | Via integrations | No | Via integrations | Cloud agents | Platform API | Custom workflows | MCP connectors | Claude Code, Codex, Gemini CLI, OpenCode | No |
| **Devin** | No (managed platform) | No | Via 20+ integrations | Specialized sub-agents internally | Slack/Teams/CLI | Custom knowledge base | 20+ integrations (GitHub, GitLab, Linear, Jira, Slack, Teams, Datadog, Sentry, AWS, Azure, Snowflake) | Yes | No |
| **Replit Agent** | No (proprietary) | No | Via integrations | Parallel sub-agents | No | Agent mode selection (Lite/Economy/Power) | Connectors (BigQuery, Linear, Slack, Notion, Excel, Databricks) | Yes (Connectors) | No |
| **Bolt.new** | Yes (bolt.diy OSS) | No | No | No | bolt.diy API | No | StackBlitz WebContainer | Via bolt.diy | No |
| **Lovable** | Yes (Connectors) | No | Yes (MCP Chat Connectors: Notion, Linear, Jira, Miro, Sentry, PostHog, etc.) | Chat Mode Agent | API Connectors | Prompt Queue | 30+ App Connectors + 15+ Chat Connectors | Extensive (Stripe, Shopify, Slack, Twilio, Google Workspace, BigQuery, Snowflake, etc.) | No |
| **v0** | No (Vercel ecosystem) | No | No | No | Vercel platform | No | Vercel integrations | Snowflake, AWS databases (direct, access-controlled) | No |
| **Cline** | Yes (MCP server creation on the fly) | No | Full (stdio + HTTP, can create MCP servers dynamically) | No (model routing, not agent creation) | CLI + MCP | Custom MCP tools | MCP connectors (databases, APIs, Jira, AWS, PagerDuty) | Via MCP | No |
| **Qodo** | Via multi-agent architecture | No | Via integrations | 5 specialized review agents + judge agent | CLI + PR API | Custom rules via Centralized Rule System | IDE plugin + CLI + Git bot | Git platforms | No |
| **Kiro** | Agent Hooks | No | Full (native, local + remote) | Custom agents (backend specialist, frontend agent, etc.) | CLI | Agent Hooks (file save/create/prompt submit/spec task exec) | Open VSX plugins | AWS integrations | No |
| **Open Interpreter** | No | No | No | No | Python API | --profile flags, --local menu | No | No | No |
| **Antigravity** | Yes (AgentKit 2.0: visual drag-and-drop agent builder) | No | Yes (Pro, up to 10 connections) | Manager Agent pattern + visual builder | No | Workflows (saved /commands) | AgentKit 2.0 + Embedded Chrome browser | MCP connectors | No |
| **Droid** | Yes (Custom Droids + SDK) | No | Via integrations | Code/Review/QA/Security/Custom Droids | TypeScript SDK (@activade/droid-sdk) | Custom Droid creation | Git AI integration (AI Blame, prompt saving) | Via MCP | No |

---

### Section 12: Pricing

| Tool | Free Tier | Entry Paid | Mid Tier | High Tier | Enterprise | Pricing Model | Annual Discount | Student / OSS |
|------|-----------|------------|----------|-----------|------------|---------------|-----------------|----------------|
| **Claude Code** | Limited (free tier) | Pro $20/mo | Max 5x $100/mo | Max 20x $200/mo | Custom | Subscription + usage | Yes | No |
| **Codex CLI** | Yes (free tier) | ChatGPT Plus $20/mo | ChatGPT Pro $100/mo | ChatGPT Pro $200/mo | Custom (Enterprise) | Subscription (CLI is OSS, API costs separate) | Yes | No |
| **Codex App** | No (requires ChatGPT plan) | $20/mo (Plus) | $100/mo (Pro) | $200/mo (Pro top tier) | Custom | Subscription (included in ChatGPT) | Yes | No |
| **Gemini CLI** | Yes (free: 1,000 req/day, 60 req/min) | Standard ~$19/user/mo (1,500 req/day) | -- | Enterprise $45/user/mo (2,000 req/day) | Custom | Subscription + usage tiers | Yes | No |
| **Cursor** | Limited free | Pro $20/mo | Pro+ $60/mo | Ultra $200/mo | Business $40/user/mo | Subscription | Yes | No |
| **Windsurf** | Yes (Free: 25 Cascade/mo, tab autocomplete) | Pro $20/mo | -- | Max $200/mo | Teams $40/user/mo, Enterprise custom | Quota-based (tokens/day + week) | Yes | Student $10/mo |
| **Aider** | Yes (OSS, BYO API keys) | $0 (+ API costs) | -- | -- | -- | Free + API (BYOK) | N/A | N/A |
| **Augment Code** | No free tier (enterprise) | Indie $20/mo (40K credits) | Standard $60/mo/dev (130K credits) | Max $200/mo/dev (450K credits) | Enterprise custom | Credit-based (top-ups: $15/24K credits) | Volume-based | No |
| **Cody** | No (discontinued 2025) | N/A | N/A | Enterprise $59/user/mo | Custom | Annual contract, enterprise only | Annual only | No |
| **Continue** | Yes (OSS, BYO API keys) | Starter $3/M tokens (PAYG) | Team $20/seat/mo (+ $10 credits) | -- | Company: custom | PAYG + seat-based | No | OSS free |
| **GitHub Copilot** | Yes (Free: 2K completions, 50 chats/mo) | Pro $10/mo | Pro+ $39/mo | Business $19/user/mo | Enterprise $39/user/mo | Seat-based → Usage-based (June 2026) | No | OSS/student: free |
| **PearAI** | Yes (BYOK: Free) | Intern $0 | Junior $15/mo | 10X $10/mo (yearly) | Enterprise: custom | Subscription | Yes | No |
| **Zed** | Yes (OSS, BYO API keys) | $0 (+ API costs) | -- | -- | -- | Free + API (BYOK) | N/A | OSS free |
| **Warp** | Yes (free tier) | Warp Pro (pricing not disclosed) | -- | -- | Enterprise: custom | Subscription | Yes | No |
| **Devin** | No free tier | Core $20/mo (+ $2.25/ACU PAYG) | Team $500/mo (250 ACUs incl., + $2.00/additional ACU) | -- | Enterprise custom | Subscription + ACU usage | No | No |
| **Replit Agent** | Yes (free tier) | Core $25/mo | Pro (includes parallel agents) | -- | Enterprise custom | Subscription | Yes | Education: free |
| **Bolt.new** | Yes (150K-300K tokens/day, 1M/mo cap) | Pro $20/mo (10M tokens) | -- | -- | -- | Subscription + token limits | Yes | bolt.diy: self-hosted free |
| **Lovable** | Yes (5 credits/day) | Pro $25/mo (100+ credits) | Teams $50/mo (400+ credits, 20 seats) | -- | Enterprise custom | Credit-based + PAYG (Cloud, AI) | No | No |
| **v0** | Yes (200 credits/mo, ~7 msgs/day) | Premium $20/mo | Team $30/user/mo | Business $100/user/mo | Enterprise custom | Credit-based + seat-based | Yes | No |
| **Cline** | Yes (OSS, BYO API keys) | $0 (+ API: mixed ~$22/mo or $8-12 with auto-routing) | -- | -- | Enterprise custom | Free + API (BYOK) | N/A | OSS free |
| **Qodo** | Limited trial | Team: $19-39/user/mo (estimated) | -- | -- | Enterprise custom | Seat-based | Volume-based | OSS: free |
| **Kiro** | Limited trial | Pro pricing not publicly disclosed | Pro+ (est. $30-50/mo) | Power (est. $60-100/mo) | Enterprise custom | Credit-based + AWS billing | Via AWS | No |
| **Open Interpreter** | Yes (OSS, BYO API keys) | $0 (+ API costs) | -- | -- | -- | Free + API (BYOK) | N/A | OSS free |
| **Antigravity** | Yes (Free Preview: generous Gemini 3 rate limits) | Pro $20/mo or $200/yr | -- | -- | Enterprise custom | Subscription | Yes (yearly) | No |
| **Droid** | Via open source SDK | Not publicly disclosed | Enterprise via Factory platform | -- | Enterprise custom | Enterprise contract | Via Factory | OSS SDK |

---

### Section 13: Benchmarks & Metrics

| Tool | SWE-bench Verified | SWE-bench Lite | Terminal-Bench | GitHub Stars | Installs / Users | Community Rating | Known Weaknesses |
|------|--------------------|----------------|-----------------|-------------|-------------------|------------------|------------------|
| **Claude Code** | 80.9% | 46% "most loved" (Prag. Eng.) | Not ranked | -- | Millions (exact not disclosed) | 9.0/10 consensus | Anthropic-locked, rate limits, cost at scale |
| **Codex CLI** | ~75% (GPT-5 Codex) | Not disclosed | Not ranked | ~80,900 | 3M+ weekly active users (Codex App) | 8.5/10 | Weaker on frontend, less mature at architecture |
| **Codex App** | 88.7% (GPT-5.5 on app) | Not disclosed | Not ranked | -- | 3M+ weekly active | 8.5/10 | EU/UK unavailable for computer use, cloud-exec only |
| **Gemini CLI** | 80.6% (Gemini 3.1 Pro) | Not disclosed | Not ranked | ~103,000 | Not disclosed | 8.0/10 | "Coin toss" quality (sometimes great, sometimes needs heavy intervention) |
| **Cursor** | Not disclosed | Not disclosed | Not ranked | -- | Largest AI IDE user base | 9.2/10 | Pricing escalation, VS Code fork lock-in, extension breakage |
| **Windsurf** | Not disclosed | Not disclosed | Not ranked | -- | Millions | 8.5/10 | Quota backlash, agent trails Cursor for complex refactors |
| **Aider** | Not disclosed | Not disclosed | Not ranked | ~43,000 | 5.7M+ PyPI installs, 15B tokens/week | 8.0/10 | No IDE, no MCP native, terminal-only |
| **Augment Code** | Not disclosed | Not disclosed | Not ranked | -- | Enterprise | 7.9/10 | Enterprise-only, opaque pricing, newer entrant |
| **Cody** | Not disclosed | Not disclosed | Not ranked | -- | Enterprise | 8.1/10 | No agent mode, enterprise-only, discontinued free/pro |
| **Continue** | Not disclosed | Not disclosed | Not ranked | ~33,000 | 2.9M+ VS Code installs | 8.0/10 | No built-in models, setup friction, no CI/CD |
| **GitHub Copilot** | Not disclosed | Not disclosed | Not ranked | -- | 20M+ developers, 90% Fortune 100 | 8.8/10 | Multi-file trails Cursor, code review still maturing |
| **PearAI** | Not disclosed | Not disclosed | Not ranked | ~676 | Small but growing | 4.2/5 | Less polished UX, small team, slower velocity |
| **Zed** | Not disclosed | Not disclosed | Not ranked | ~82,000 | Not disclosed | 8.0/10 | Young extension ecosystem, no Composer-style multi-file editing |
| **Warp** | Not disclosed | Not disclosed | Not ranked | Not disclosed | Not disclosed | Not rated | Terminal-only, agent mode still evolving |
| **Devin** | Not disclosed | Not disclosed | Not ranked | -- | Enterprise (Nubank, etc.) | 4.2/5 | 25% tasks need human intervention, expensive at scale |
| **Replit Agent** | Not disclosed | Not disclosed | Not ranked | -- | 30M+ total users (platform) | 7.6/10 | Web-only, ecosystem lock-in |
| **Bolt.new** | Not disclosed | Not disclosed | Not ranked | -- | Not disclosed | 7.4/10 | Reliability issues (browser APIs), limited to prototyping |
| **Lovable** | Not disclosed | Not disclosed | Not ranked | -- | Not disclosed | 8.0/10 | Struggles with complex business logic, credit-burning loops |
| **v0** | Not disclosed | Not disclosed | Not ranked | -- | 4M+ users (pre-rebuild) | 7.8/10 | Frontend-only, no backend, Vercel-ecosystem lock-in |
| **Cline** | Not disclosed | Not disclosed | Not ranked | ~61,000 | 5M+ VS Code installs | 8.5/10 | No autocomplete, setup complexity (API keys) |
| **Qodo** | Not disclosed | Not disclosed | Not ranked | -- | 2M+ installs, 4M+ PRs reviewed/yr | 8.0/10 | Narrow focus (code review/testing), not a general-purpose agent |
| **Kiro** | Not disclosed | Not disclosed | Not ranked | ~3,617 | Not disclosed | 8.0/10 | Newer entrant, AWS ecosystem lock-in, property-based testing needs maturity |
| **Open Interpreter** | Not disclosed | Not disclosed | Not ranked | ~63,000 | Not disclosed | 7.5/10 | Local execution risks, less reliable than cloud models, no IDE |
| **Antigravity** | Not disclosed | Not disclosed | Not ranked | -- | Public preview | Not rated | Immature extensions, quota limits in preview, learning curve |
| **Droid** | 19.27% (Full), 31.67% (Lite pass@1) | 42.67% (pass@6) | **#1: 58.75%** | -- | Enterprise | 8.0/10 | Enterprise-only, Factory ecosystem lock-in |

---

## Part B: UX/UI Pattern Catalog

### 1. Chat Interfaces

| Pattern | Description | Tools Using It | UX Assessment |
|---------|-------------|----------------|---------------|
| **Sidebar Chat** | Chat lives in a dedicated sidebar panel alongside the editor | Cursor, Windsurf, Continue, Copilot, PearAI, Antigravity, Cody, Augment | Dominant pattern for IDE extensions. Good for persistent context while coding. Splits attention between chat and editor. |
| **Inline Chat** | Chat appears inline within the code editor, often triggered by selection or Cmd+K | Cursor (Cmd+K), Copilot (inline chat), PearAI (CMD+I), Windsurf (Cascade inline), Zed (inline assist) | Excellent for targeted code changes. Less context overhead. The "micro-chat" pattern. |
| **Floating / Pop-out Chat** | Chat can be detached into a floating window | Codex App (floating pop-out windows), Cursor (detachable panels), Windsurf | Useful for multi-monitor setups. Enables side-by-side code + chat on different displays. |
| **Terminal Chat** | Chat is the entire interface -- text stream in the terminal | Claude Code, Codex CLI, Gemini CLI, Aider, Open Interpreter, Droid, Cline CLI | Minimalist, keyboard-only. High power-user ceiling, steep learning curve. No visual diffing without terminal tricks. |
| **Agent Canvas / Manager** | Dedicated dashboard for monitoring/managing multiple agents | Antigravity (Manager Surface), Replit (Kanban), Windsurf 2.0 (Agent Command Center) | Emerging "supervisory" UX. The user is orchestrating a team, not having a conversation. |
| **Web Chat** | Chat is the primary interface in a browser, alongside a preview/editor pane | Replit, Bolt.new, Lovable, v0, Devin | Enables zero-setup access. Good for non-developers. Preview alongside chat is the key UX -- "see what you build as you describe it." |

**Key Trends:**
- The industry is moving from sidebar-chat to embedded/inline agent interactions
- "The chatbot is dead" as a primary interface -- agentic actions with visual feedback are replacing text conversations
- Multi-agent dashboards (Antigravity, Replit, Windsurf 2.0) represent the next evolution

### 2. Diff Displays

| Pattern | Description | Tools Using It | UX Assessment |
|---------|-------------|----------------|---------------|
| **Unified Diff (Text)** | Traditional unified diff format (+, -, @@) in terminal or text block | Claude Code, Gemini CLI, Aider, Droid, Open Interpreter, Cline CLI | Functional but low visual bandwidth. Works in terminal. Hard to scan for large changes. |
| **Side-by-Side Diff** | Two-pane view: original on left, modified on right | Zed (split diffs, shipped Feb 2026), some VS Code-based tools | Better for complex changes. More screen real estate needed. |
| **Inline Rich Diff (Accept/Reject Hunks)** | Changes shown inline in editor with green/red highlights and per-hunk accept/reject buttons | Cursor (gold standard), Windsurf, Copilot, PearAI, Continue, Kiro, Codex App | **The gold standard.** Fine-grained control. Visual scanning is fast. Trust-building. |
| **Inline Ghost Text** | Suggested code appears as faint/ghosted text inline; Tab to accept | Copilot (completions), Cursor (Tab), Windsurf (Supercomplete), Zed (Zeta) | Minimal cognitive interruption. Best for small completions. The "invisible" UI. |
| **Word-Level Highlighting** | Within a diff line, only the changed characters/words are highlighted | Cursor, Windsurf, Copilot | Critical for spotting subtle changes. Missing in basic diff tools. |
| **Per-Hunk Accept/Reject** | Individual code blocks (hunks) can be accepted or rejected independently | Cursor, Copilot, Windsurf, Codex App, PearAI, Continue, Kiro | Essential agentic UX primitive. Users want fine-grained control, not all-or-nothing. |
| **Changes Dashboard** | A panel listing ALL files changed in a conversation, expandable to see diffs | Roo Code (per-conversation), Nimbalyst (Changes Tab), Cursor (file list in Composer) | Critical for review-at-scale. When agents touch 15-30 files/session, you need bulk review with selective attention. |
| **Before/After Visual Comparison** | For design tools, show visual before/after of generated designs | Lovable (Visual Edits), Replit (design variants on canvas), v0 | Format-native diff. For UI work, seeing is more important than reading code. |

**Key Trends:**
- Per-hunk accept/reject is now a baseline expectation (Claude Code's all-or-nothing is a known UX gap -- issue #31395)
- Review-at-scale tooling (changes dashboards, session review state) is the new bottleneck
- Format-native diffs (visual for designs, formatted for markdown, rendered for diagrams) gaining adoption

### 3. Progress Indicators

| Pattern | Description | Tools Using It |
|---------|-------------|----------------|
| **Spinner / Throbber** | Animated spinner while waiting for AI response | Universal (nearly all tools) |
| **Streaming Text** | Response streams token-by-token as generated | All chat-based tools |
| **Step Counter** | "Step 3 of 7" or "Task 2/5 complete" | Codex CLI, Windsurf (Cascade todo), Devin, Kiro (spec-driven), Antigravity (artifacts) |
| **Progress Bar** | Visual bar filling as task completes | Codex App, Replit (Kanban progress), Lovable, Bolt.new |
| **Token Counter** | Real-time display of tokens consumed (current request and/or session total) | Cline (per-step), Windsurf (per-prompt), Codex CLI, Kiro (per-prompt), Claude Code (/cost) |
| **Time Estimate** | Estimated time to completion | Devin (ACU: ~15 min blocks) |
| **Todo List (Auto-Update)** | AI-generated checklist that updates as items complete | Windsurf (Cascade), Warp (Oz task lists), Kiro (spec tasks), Antigravity (artifacts) |
| **Checkpoint Indicator** | Shows that a checkpoint/snapshot was saved; can restore | Cline, Kiro, Gemini CLI, Replit |
| **ACU / Credit Consumption** | Task cost shown in compute units, not just time | Devin (ACU = Agent Compute Unit) |
| **Usage Dashboard (Real-time)** | Live dashboard of quota/token usage across all agents | Antigravity (80% alerts, per-member breakdowns), Windsurf (quota dashboard), GitHub Copilot (AI Credits preview, May 2026) |

**Key Trends:**
- Token/cost counters are becoming standard as pricing shifts to usage-based models
- Todo lists that auto-update build trust by showing what the agent thinks it's doing
- Real-time quota dashboards are a response to "bill shock" complaints (especially Windsurf's quota backlash)

### 4. Context Visualization

| Pattern | Description | Tools Using It |
|---------|-------------|----------------|
| **Token Usage Bar** | Visual bar showing context window fill level | Rare (some tools show token counts, not windows) |
| **File References** | List or tree of files included in context | Cursor (file list in Composer), Aider (tracked files), Claude Code (reads listing) |
| **Repo Map Visualization** | Interactive map of codebase structure and dependencies | Aider (Repo Map as text), Sourcegraph (code graph), Continue (@codebase), Droid (HyperCode) |
| **Codebase Knowledge Graph** | Visual graph of symbols, references, and dependencies | Sourcegraph (SCIP code graph), Cody (context controls) |
| **@Mention Context Attachments** | @-mention files, symbols, repos, previous conversations to attach to context | Windsurf (@-mention conversations), Lovable (@ cross-project), Cursor, Continue (50+ providers), Copilot |
| **Context Filters** | Admin-defined rules for what AI can/cannot access | Cody (admin-defined context filters), Augment (Context Engine), GitHub Copilot (org policies) |
| **Memory / Persistent Context** | AI remembers preferences, conventions, patterns across sessions | Codex App (Memory preview), Windsurf (Memories & Rules), Devin (Knowledge Base), Lovable (cross-project referencing) |
| **Reasoning Visualization** | AI shows its thinking process (e.g., Claude's thinking accordions) | Claude Code (thinking blocks), Cursor (agent reasoning), Antigravity (artifacts) |

**Key Trends:**
- Context visualization is underinvested across the industry -- most tools don't show what the model can "see"
- Persistent memory across sessions is emerging as a key differentiator (Codex, Windsurf, Devin)
- @mentions are the dominant context-attachment UX pattern

### 5. Input Methods

| Pattern | Description | Tools Using It |
|---------|-------------|----------------|
| **Natural Language (Chat)** | Plain English text input | Universal |
| **Slash Commands** | `/command` style structured inputs | Claude Code (/think, /cost), Aider (/code, /ask, /undo, /drop), PearAI (/commit, /edit, /comment, /test), Continue (custom slash), Codex CLI (/goal, /review, /vim) |
| **@ Mentions** | @file, @symbol, @repo, @conversation to attach context | Cursor, Windsurf, Codex CLI, Lovable, Continue, Copilot, Antigravity |
| **Cmd+K / Inline Command** | Select code + Cmd+K to describe a change inline | Cursor, Windsurf, PearAI (CMD+I), Copilot (inline), Antigravity |
| **Voice Input** | Speech-to-text for dictating instructions | Windsurf, Aider (voice-to-code), Codex CLI (WebRTC Realtime Voice), Warp, Zed (audio calls) |
| **Image Paste / Attach** | Paste screenshots or attach images for visual context | Claude Code, Cursor, Windsurf, Aider, Copilot, Codex CLI, Codex App, Kiro, Antigravity, Open Interpreter |
| **Image Generation** | Generate images (UI assets, mockups) within the tool | Codex App (gpt-image-2.0), Lovable (via AI connectors) |
| **Drag & Drop Files** | Drag files from filesystem into chat for context | Cursor, Windsurf, Copilot (IDE-dependent), Codex App |
| **File Upload** | Formal upload dialog for files | Codex App, web-based tools (Lovable, v0, Bolt.new, Replit) |
| **Prompt Queue** | Queue multiple prompts in sequence | Lovable (Prompt Queue: queue, reorder, repeat up to 50x), Codex CLI (Goals: persisted multi-turn) |
| **Mid-Turn Steering** | Send instructions while agent is working; it adapts in real-time | Codex CLI (mid-turn steering, unique feature) |
| **Visual Agent Builder** | Drag-and-drop GUI to assemble agents with tools | Antigravity (AgentKit 2.0, unique feature) |

**Key Trends:**
- Multimodal input is now standard (images, voice, text combined)
- Slash commands provide discoverability for power features
- Mid-turn steering (Codex CLI) is an emerging pattern -- the ability to redirect an agent mid-flight
- Visual agent building (Antigravity) could lower the barrier to custom agent creation

### 6. Notification Patterns

| Pattern | Description | Tools Using It |
|---------|-------------|----------------|
| **Toast / Popup** | Brief non-blocking notification in corner | Cursor, Windsurf, Copilot (IDE toasts), Codex App |
| **Status Bar Indicator** | Persistent indicator in editor status bar | Cursor (agent status), Windsurf, Copilot, Continue, PearAI, Augment |
| **Banner** | Top-of-editor banner for important notices | Some IDE tools (version updates, rate limits) |
| **Inline Status** | Status shown inline within the chat/agent panel | Cursor (agent step status), Cline (approval status per step), Windsurf (Cascade steps) |
| **Desktop Notification** | OS-level notification (macOS, Windows) | Codex App (thread automations completion), Warp (cloud agent completion) |
| **Slack / Teams Notification** | Send status to Slack/Teams | Devin (reports back with PRs), Warp (via integration) |
| **Quota Alert** | Warning when approaching usage limits | Antigravity (80% alerts), Windsurf (quota exhaustion warnings), Bolt.new (token limits) |

**Key Trends:**
- Notifications are increasingly about quota/cost, not just status (reflecting usage-based pricing)
- Async notifications (Slack/email/desktop) enable background agent workflows
- Inline status beats toasts for agentic workflows (you need to see what step you're on)

### 7. File Navigation

| Pattern | Description | Tools Using It |
|---------|-------------|----------------|
| **File Tree** | Hierarchical sidebar file browser | Universal in IDEs (Cursor, Windsurf, PearAI, Zed, Kiro, Antigravity); also in web tools (Replit, v0, Lovable) |
| **Quick Open (Cmd+P)** | Fuzzy file name search | VS Code-based tools (Cursor, Windsurf, PearAI, Antigravity), Zed |
| **Symbol Search (Cmd+Shift+O / Cmd+T)** | Search for symbols (functions, classes, types) within files or project-wide | Cursor, Windsurf, Zed (LSP semantic tokens), Cody (via code graph) |
| **Breadcrumbs** | Path breadcrumb navigation at top of editor | Cursor, Windsurf, PearAI, Zed |
| **Tabs** | Open file tabs at top of editor | Universal in IDEs |
| **Go-to-Definition / Find References** | LSP-powered navigation | All LSP-capable editors (Cursor, Windsurf, Zed, PearAI, Kiro) |
| **GitHub-Integrated File Navigation** | Browse repo files as if in IDE | v0 (VS Code-style editor + GitHub repo import), Devin |
| **Fuzzy File @Mentions** | Type @ to search and attach files to AI context | Codex CLI, Cursor, Windsurf, Continue |

**Key Trends:**
- File navigation is commoditized in IDEs -- differentiation comes from AI-aware navigation
- @mention-based file attachment is the new "file open" for agentic workflows
- Symbol search + AI context (e.g., Cody's code graph) is the enterprise differentiator

### 8. Approval UX

| Pattern | Description | Tools Using It |
|---------|-------------|----------------|
| **Permission Dialog** | Modal dialog asking permission for an action | Cursor, Windsurf, Copilot, Claude Code (per-action prompts) |
| **Approval Card** | Rich card showing action details, consequences, and approve/deny | Cursor (Composer approval cards), Codex CLI (plan mode approval), Antigravity (artifact review) |
| **Allow/Deny Buttons** | Simple binary approval | Universal |
| **Auto-Approve Toggle** | Toggle to auto-approve certain action types | Claude Code (permission toggles), Codex CLI (approval policies), Devin (autonomy levels), Droid (autonomy spectrum) |
| **Step-by-Step Approval** | Agent proposes each step; you approve before next step | Cline (Plan/Act mode review), Kiro (spec task approval), Windsurf (Cascade explains before acting) |
| **Plan-First Approval** | Agent shows full plan; you approve plan, then it executes all steps | Codex CLI (Plan Mode), Kiro (spec-driven), Devin (interactive planning), Lovable (Plan Mode), Replit (Plan Mode) |
| **Diff-First Approval** | Before/after diff shown before action executes | Cursor (gold standard), Windsurf, Codex App, Cline (diff preview), Kiro (code diffs) |
| **Per-Hunk Approval** | Accept/reject individual code changes within a larger edit | Cursor, Copilot, Windsurf, Codex App, Continue, Kiro |
| **Approval Fatigue Mitigation** | Grouped approvals, auto-approve categories, risk-based gating | Codex CLI (suggest → auto-edit → full-auto tiers), Antigravity (Terminal Execution Policy: Off/Auto/Turbo), Droid (autonomy spectrum) |

**Key Trends:**
- Plan-first approval (review plan, then execute) is becoming the dominant agentic pattern
- Diff-first review (Cursor's gold standard) is the baseline expectation for IDE agents
- Approval fatigue is a real problem -- tools are adding risk-based auto-approval tiers
- The "trust stack" pattern: Streaming → Reasoning → HITL Gates → Confidence Indicators

### 9. Session Management

| Pattern | Description | Tools Using It |
|---------|-------------|----------------|
| **Session List** | List of all past sessions with preview/name | Codex CLI (resume/fork any session), Claude Code (session resume), Windsurf (Spaces), Devin (session replay) |
| **Session Resume** | Pick up a session exactly where you left off | Codex CLI (resume any past session), Claude Code, Gemini CLI (checkpointing), Aider (.aider.chat.history.md), Augment (resumable sessions) |
| **Session Fork** | Create a branch of a session from any message | Codex CLI (fork from any message) |
| **Session Naming** | Name sessions for later reference | Codex CLI, Claude Code, Devin, Windsurf (Spaces) |
| **Session Search** | Search across all past sessions | Not widely implemented (Codex CLI likely has via config) |
| **Session Pin / Favorite** | Pin important sessions for quick access | Minimal implementation across tools |
| **Session Delete / Archive** | Remove old sessions | Most tools |
| **Session Export** | Export session as markdown, JSON, or shareable link | Augment (export as markdown), Cline (checkpoint comparison) |
| **Checkpoint within Session** | Snapshot within a single session for rewind | Cline (automatic workspace snapshots), Kiro (rewind any number of steps), Gemini CLI (checkpointing), Replit (checkpoints & rollback) |

**Key Trends:**
- Session resume and fork are table stakes for CLI agents in 2026
- Checkpointing within sessions is emerging as a power-user feature
- Session search and pinning are underinvested across the industry
- Fork-from-any-message (Codex CLI) is the most advanced session model

### 10. Error Patterns

| Pattern | Description | Tools Using It |
|---------|-------------|----------------|
| **Error Display (Inline)** | Error message shown inline in the editor or terminal | Universal |
| **Retry Button** | One-click retry of the failed action | Cursor, Windsurf, Copilot, Codex CLI, Lovable |
| **Error Explanation** | AI explains what went wrong in natural language | Cursor, Copilot, Codex CLI, Kiro (intelligent error diagnostics) |
| **Fix Suggestion** | AI suggests a specific fix for the error | Cursor, Windsurf, Copilot, Codex CLI, Kiro (auto-fix or surface options) |
| **Auto-Fix Loop** | AI detects error, fixes it, re-runs, loops until passing | Aider (auto-test loop), Windsurf (linter auto-fix), Kiro (property-based test shrinking + auto-fix), Copilot (cloud agent fixes review suggestions) |
| **Error with Context** | Error shown with relevant code context and stack trace | Kiro (reads syntax, type, semantic errors), Codex CLI, Cursor |
| **Error Grouping** | Group similar errors to reduce noise | Qodo (judge agent consolidates) |
| **Fallback Configuration** | Per-agent fallback: one agent's failure doesn't halt pipeline | Antigravity (AgentKit 2.0 fallback) |

**Key Trends:**
- Auto-fix loops (detect → fix → retest → loop) are becoming standard
- Error explanation + fix suggestion is expected, not just error display
- Per-agent fallback (Antigravity) is critical for multi-agent systems

---

## Part C: Feature Gap Summary for Wisp

### Methodology

Features are ranked by **market penetration** -- the approximate percentage of the 25 tools surveyed that have the feature. The ranking identifies what Wisp is missing vs the competitive landscape, sorted by what "everyone else has."

### Critical Gaps (80-100% of competitors have these)

| # | Feature | Competitors With It | Priority | Notes |
|---|---------|---------------------|----------|-------|
| 1 | **Multi-file editing** | 24/25 (96%) | CRITICAL | Only tool without it is arguably Open Interpreter (which has filesystem access anyway). This is the #1 baseline agent capability. |
| 2 | **Agent mode (autonomous task execution)** | 23/25 (92%) | CRITICAL | Copilot, Cursor, Windsurf, Claude Code, Codex CLI, Aider, Devin, Replit, Cline, Kiro, Antigravity, Droid, etc. all have agent mode. |
| 3 | **Chat interface** | 25/25 (100%) | CRITICAL | Every single tool has chat. Table stakes. |
| 4 | **Shell/bash execution** | 24/25 (96%) | CRITICAL | Only web-only builders (v0, Bolt) may be limited. Everyone else runs commands. |
| 5 | **File read/write operations** | 25/25 (100%) | CRITICAL | Universal baseline. |
| 6 | **Diff preview (visual)** | 22/25 (88%) | HIGH | All IDEs have rich diff previews. CLI tools have text diffs. Wisp needs at least one. |
| 7 | **Git integration** | 22/25 (88%) | HIGH | From basic (commit) to advanced (auto-commit, PR creation, worktrees). |
| 8 | **Plan mode / approval before execution** | 20/25 (80%) | HIGH | Cursor, Windsurf, Codex, Gemini, Aider, Devin, Replit, Lovable, Cline, Kiro, Antigravity, Droid all have plan-first workflows. |
| 9 | **Model selection / multi-model** | 20/25 (80%) | HIGH | Only Anthropic-locked tools are single-model. Multi-model is standard. |
| 10 | **Context files / rules system** | 20/25 (80%) | HIGH | CLAUDE.md, .cursorrules, GEMINI.md, AGENTS.md, .kiro/steering/ etc. |

### High Gaps (60-79% of competitors have these)

| # | Feature | Competitors With It | Priority | Notes |
|---|---------|---------------------|----------|-------|
| 11 | **Autocomplete / tab completion** | 15/25 (60%) | HIGH | Cursor, Windsurf, Copilot, Zed, PearAI, Continue, Kiro, etc. Key for daily IDE usage. |
| 12 | **Inline editing (Cmd+K style)** | 14/25 (56%) | HIGH | Select code, describe change, see inline diff. Cursor's signature feature. |
| 13 | **Sub-agents / specialized agents** | 14/25 (56%) | HIGH | Claude Code (Agent Teams), Codex CLI, Augment, Qodo (5 agents), Antigravity (Manager pattern), Droid (Code/Review/QA/Security Droids), Kiro (custom agents) |
| 14 | **MCP support** | 17/25 (68%) | HIGH | Becoming universal. Claude Code, Codex, Gemini, Cursor, Windsurf, Copilot, Cline, ZD, Kiro, Antigravity, Augment, Continue, Lovable all have it. |
| 15 | **Per-hunk accept/reject diffs** | 14/25 (56%) | HIGH | Cursor gold standard. Windsurf, Copilot, Codex App, PearAI, Continue, Kiro. Critical UX primitive. |
| 16 | **Checkpoints / undo within session** | 14/25 (56%) | HIGH | Cline (workspace snapshots), Kiro (rewind any steps), Replit, Gemini CLI, Windsurf (named checkpoints). |
| 17 | **Web search tool** | 15/25 (60%) | MEDIUM | Codex (cached/live), Claude Code, Gemini (Google grounding), Cursor, Windsurf, Copilot, Warp. |
| 18 | **Parallel agents** | 12/25 (48%) | MEDIUM | Claude Code (Agent Teams), Codex CLI (multi-agent v2), Zed (Threads sidebar), Antigravity, Replit (auto task-split), Devin, Windsurf (simultaneous Cascades), Qodo (5 parallel review agents), Droid |

### Medium Gaps (40-59% of competitors have these)

| # | Feature | Competitors With It | Priority | Notes |
|---|---------|---------------------|----------|-------|
| 19 | **BYOK (bring your own key)** | 13/25 (52%) | MEDIUM | Free/OSS tools primarily: Aider, Cline, Continue, PearAI, Zed, Open Interpreter, Augment. |
| 20 | **Local models (Ollama)** | 11/25 (44%) | MEDIUM | Cline, Aider, Continue, PearAI, Zed, Open Interpreter, Codex CLI, Droid. Privacy differentiator. |
| 21 | **Session resume / fork** | 11/25 (44%) | MEDIUM | Codex CLI (gold standard: resume/fork from any message), Claude Code, Gemini CLI, Aider, Augment. |
| 22 | **Token/cost counter** | 12/25 (48%) | MEDIUM | Cline (per-step), Windsurf (quota dashboard), Kiro (per-prompt), Codex CLI, Claude Code (/cost). |
| 23 | **Voice input** | 10/25 (40%) | LOW | Windsurf, Aider, Codex CLI (WebRTC), Warp, Zed, Copilot (limited). |
| 24 | **CI/CD / headless mode** | 13/25 (52%) | MEDIUM | Codex CLI (--print, --yolo), Aider (--yes), Gemini CLI (--output-format json), Cline CLI, Augment (--print). |
| 25 | **Slash commands** | 11/25 (44%) | MEDIUM | Claude Code, Aider, PearAI, Continue, Codex CLI, Antigravity. |

### Lower Gaps (0-39% of competitors have these)

| # | Feature | Competitors With It | Priority | Notes |
|---|---------|---------------------|----------|-------|
| 26 | **Multiplayer / real-time collaboration** | 5/25 (20%) | LOW | Zed (primary feature), Lovable (Multiplayer), Replit, v0 (collaborative), Augment (session sharing). |
| 27 | **Computer use / browser agent** | 7/25 (28%) | LOW | Codex App (gold standard), Cline, Antigravity, Devin, Open Interpreter (OS mode), Replit, Windsurf. |
| 28 | **Image input (paste screenshots)** | 12/25 (48%) | LOW | Many tools: Claude Code, Cursor, Windsurf, Aider, Copilot, Codex CLI, Kiro, Antigravity, Open Interpreter. |
| 29 | **Desktop app** | 10/25 (40%) | LOW | Cursor, Windsurf, PearAI, Zed, Warp, Kiro, Antigravity, Codex App, Lovable (macOS). |
| 30 | **Plugin marketplace** | 4/25 (16%) | LOW | Codex CLI/App (curated marketplace), GitHub Copilot (extensions preview), Zed (ACP Registry). |
| 31 | **Web UI / browser interface** | 7/25 (28%) | LOW | Devin, Replit, Bolt.new, Lovable, v0, Antigravity (via IDE), Warp (web-based). |
| 32 | **Property-based testing** | 1/25 (4%) | LOW | Kiro (unique feature: spec → property extraction → random test generation). |
| 33 | **Mid-turn steering** | 1/25 (4%) | LOW | Codex CLI (unique: send instructions while agent works). |
| 34 | **Visual agent builder (drag-and-drop)** | 1/25 (4%) | LOW | Antigravity (AgentKit 2.0: unique feature). |
| 35 | **AI Blame (attribution tracking)** | 1/25 (4%) | LOW | Droid (via Git AI integration: see if code was AI or human-written). |
| 36 | **Memory / persistent preferences** | 6/25 (24%) | LOW | Codex App (preview), Windsurf (Memories), Devin (Knowledge Base), Lovable (cross-project). |
| 37 | **Scheduling / cron agents** | 4/25 (16%) | LOW | Codex CLI/App (thread automations), Warp (scheduled agents), Copilot (cloud agent background). |
| 38 | **Sessions sharing / export** | 5/25 (20%) | LOW | Augment (export as markdown, session sharing), Codex CLI (resume/fork). |
| 39 | **Vim mode (in TUI)** | 5/25 (20%) | LOW | Codex CLI (/vim), Cursor (VS Code), Zed (native), Windsurf (VS Code). |
| 40 | **Kanban / task board view** | 4/25 (16%) | LOW | Replit (Kanban planning), Windsurf 2.0 (Agent Command Center), Cline CLI (Kanban sidebar). |

### Summary: Wisp's Top 10 Must-Have Features (by market penetration)

1. **Chat interface** -- 100% of competitors
2. **File read/write** -- 100% of competitors
3. **Multi-file editing** -- 96% of competitors
4. **Shell/bash execution** -- 96% of competitors
5. **Agent mode** -- 92% of competitors
6. **Diff preview (visual)** -- 88% of competitors
7. **Git integration** -- 88% of competitors
8. **Plan mode** -- 80% of competitors
9. **Multi-model support** -- 80% of competitors
10. **Context files / rules system** -- 80% of competitors

### Competitive White Space (Features Few Have That Wisp Could Own)

These are features with low market penetration (under 25%) that could differentiate Wisp:

1. **Property-based testing** (Kiro only) -- Spec → automatic property extraction → random test generation
2. **Mid-turn steering** (Codex CLI only) -- Redirect agents while they work
3. **Visual agent builder** (Antigravity only) -- Drag-and-drop agent creation
4. **AI Blame / attribution tracking** (Droid only) -- Provenance tracking for AI-generated code
5. **True kernel-level sandboxing for local CLI** (Codex CLI only) -- Safety differentiator
6. **Unified TUI + GUI hybrid** (No one does this well) -- Terminal power + visual diffing in one tool
7. **Fork-from-any-message session model** (Codex CLI only) -- Best-in-class session management

---

## Appendix: Key Sources

- [AI Code Review - Best AI Coding Assistants 2026](https://aicodereview.cc/blog/best-ai-coding-assistants/)
- [Tembo - Best Agentic AI Coding Tools 2026](https://www.tembo.io/blog/agentic-ai-coding-tools)
- [AI Agent Brief - Best AI Coding Assistants 2026](https://www.ai-agent-brief.com/ai-tool-hub/best-ai-coding-assistants-2026.html)
- [Automation Switch - 27 AI Coding Tools Scored](https://automationswitch.com/tool-comparisons/ai-coding-assistants)
- [Toolradar - Best AI Coding Tools 2026](https://www.toolradar.com/guides/best-ai-coding-tools)
- [TokenMix - Claude Code vs Codex CLI vs Gemini CLI](https://tokenmix.ai/blog/claude-code-vs-codex-cli-vs-gemini-cli)
- [Planu - AI Coding Tools Compared](https://planu.dev/en/blog/ai-coding-tools-compared)
- [CodeMySpec - CLI Agents Compared 2026](https://codemyspec.com/pages/cli-agents-compared-2026)
- [InventiveHQ - Gemini vs Claude vs Codex Comparison](https://inventivehq.com/blog/gemini-vs-claude-vs-codex-comparison)
- [AristoAiStack - AI Coding Agents 2026](https://aristoaistack.com/posts/ai-coding-agents-cursor-windsurf-claude-code-codex-2026/)
- [Aider GitHub](https://github.com/paul-gauthier/aider)
- [Cline Docs](https://docs.cline.bot/introduction/overview)
- [OpenAI Codex CLI Docs](https://developers.openai.com/codex/cli/features)
- [OpenAI Codex App Features](https://developers.openai.com/codex/app/features)
- [Gemini CLI GitHub](https://github.com/google-gemini/gemini-cli)
- [Zed AI Docs](https://zed.dev/ai)
- [Windsurf Docs](https://docs.windsurf.com/windsurf/cascade/cascade)
- [Replit Agent Docs](https://docs.replit.com/core-concepts/agent/)
- [Devin AI](https://www.devin.ai/)
- [Continue Dev GitHub](https://github.com/continuedev/continue)
- [Y Build - Lovable vs Bolt.new vs v0](https://ybuild.ai/en/blog/lovable-vs-bolt-vs-v0-ai-app-builder-comparison-2026)
- [GitHub Copilot Plans](https://github.com/features/copilot/plans)
- [Sourcegraph Cody](https://sourcegraph.com/get-cody)
- [Augment Code](https://www.augmentcode.com)
- [PearAI Docs](https://trypear.ai/docs)
- [Warp Agents](https://www.warp.dev/agents)
- [Kiro Dev](https://kiro.dev)
- [Qodo Docs](https://docs.qodo.ai)
- [Google Antigravity Blog](https://developers.googleblog.com/build-with-google-antigravity-our-new-agentic-development-platform)
- [Open Interpreter GitHub](https://github.com/OpenInterpreter/open-interpreter)
- [Droid (Factory)](https://factory.ai/news/code-droid-technical-report)
- [Agentic UX Primitives (Yaroslav Boiko, 2026)](https://yaroslavboiko.com/blog/agentic-ux-primitives/)
- [AI Catchup - Codex CLI vs Claude Code vs Cursor Architecture](https://aicatchup.com/comparisons/codex-cli-vs-claude-code-vs-cursor-architecture)
- [Pillar Security - AI Agent Sandboxing](https://www.pillar.security/blog/your-ai-agent-will-run-untrusted-code-now-what)
- [Agensi - AI Coding Tools Comparison 2026](https://www.agensi.io/learn/ai-coding-tools-comparison-2026)
- [Lushbinary - AI Coding Agents Comparison](https://lushbinary.com/blog/ai-coding-agents-comparison-cursor-windsurf-claude-copilot-kiro-2026/)
- [v0 by Vercel Blog](https://vercel.com/blog/introducing-the-new-v0)
- [Lovable 2.0 Blog](https://lovable.dev/blog/lovable-2-0)
- [OpenAI Codex App Update - 9to5Mac](https://9to5mac.com/2026/04/16/openais-codex-app-adds-three-key-features-for-expanding-beyond-agentic-coding/)
- [Nimbalyst - AI Diff Review](https://nimbalyst.com/blog/ai-diff-review-visual-agent-changes)
- [AgentMarketCap - Second-Generation AI CLI Tools](https://agentmarketcap.ai/blog/2026/04/05/agent-cli-tools-second-generation-claude-code-gemini-cli-codex-ux-philosophies)
- [GitHub Copilot Usage-Based Billing](https://github.blog/news-insights/company-news/github-copilot-is-moving-to-usage-based-billing/)
- [GitHub Copilot Agentic Code Review](https://github.blog/changelog/2026-03-05-copilot-code-review-now-runs-on-an-agentic-architecture/)
