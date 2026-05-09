# Warp Terminal: Comprehensive Feature Catalog

> Research date: 2026-05-09
> Product: Warp (warp.dev) -- the "Agentic Development Environment"
> Status: Open-source (AGPL v3) as of April 29, 2026

---

## Table of Contents

1. [What Makes Warp Fundamentally Different](#1-what-makes-warp-fundamentally-different)
2. [AI Features](#2-ai-features)
3. [Block-Based Architecture](#3-block-based-architecture)
4. [Agent Mode & Oz Agents](#4-agent-mode--oz-agents)
5. [Cloud Agents & Orchestration (Oz Platform)](#5-cloud-agents--orchestration-oz-platform)
6. [Modern Input Editor](#6-modern-input-editor)
7. [Smart Completions & Autosuggestions](#7-smart-completions--autosuggestions)
8. [IDE-Like Code Editor](#8-ide-like-code-editor)
9. [UI/UX Features](#9-uiux-features)
10. [Theming, Transparency & Appearance](#10-theming-transparency--appearance)
11. [Warp Drive](#11-warp-drive)
12. [Workflows](#12-workflows)
13. [Notebooks](#13-notebooks)
14. [Collaboration & Team Features](#14-collaboration--team-features)
15. [Session Sharing](#15-session-sharing)
16. [Split Panes & Synced Inputs](#16-split-panes--synced-inputs)
17. [Command History & Search](#17-command-history--search)
18. [Markdown Viewer](#18-markdown-viewer)
19. [Launch Configurations & Tab Configs](#19-launch-configurations--tab-configs)
20. [Session Restoration](#20-session-restoration)
21. [Integrations](#21-integrations)
22. [Security & Privacy](#22-security--privacy)
23. [Performance & Rendering Engine](#23-performance--rendering-engine)
24. [Platforms & Shell Compatibility](#24-platforms--shell-compatibility)
25. [Pricing Plans](#25-pricing-plans)
26. [Codebase Architecture](#26-codebase-architecture)
27. [Recent Changelog (2026)](#27-recent-changelog-2026)
28. [Comparison with Other Terminals](#28-comparison-with-other-terminals)
29. [Sources](#29-sources)

---

## 1. What Makes Warp Fundamentally Different

Warp is not a traditional terminal emulator. It is a full "Agentic Development Environment" (ADE) built from scratch in Rust. The foundational differences:

### 1.1 The Terminal App Itself Owns the Input Editor

In every traditional terminal (iTerm2, Terminal.app, Kitty, Alacritty, etc.), when you type a command, the shell process manages the input buffer -- character by character -- over a PTY (pseudo-terminal). The terminal app is only a dumb rendering surface for the shell's cooked/canonical input mode. This is why traditional terminals can't do IDE-like editing: the terminal doesn't see words or lines, it just sees a stream of bytes.

**Warp moves the command editor out of the shell and into the terminal app itself.** Warp's input area is a full Rust text editor (SumTree-based, co-developed with Nathan Sobo, the creator of the Atom editor). It intercepts what you type, gives you a full IDE experience (syntax highlighting, multi-cursor, error underlining, etc.), and only hands off the final command to the shell for execution.

This architectural decision is the foundation that enables essentially every other feature Warp has.

### 1.2 Block-Based Output

Instead of a continuous, undifferentiated scrollback buffer, Warp groups each command and its output into a discrete, selectable, actionable "Block". This is the single most frequently cited "killer feature" of Warp. Each block can be independently: copied, collapsed, bookmarked, shared via permalink, filtered, and used as AI context.

### 1.3 GPU-Accelerated Rust Engine

Warp renders on the GPU using Metal (macOS) or wgpu (Linux/BSD/Windows). It has its own custom Rust UI framework (WarpUI). Rendering is ~1.9ms per frame, sustaining 144+ FPS on 4K displays.

---

## 2. AI Features

Warp has AI integrated at every level -- it's not a sidebar chatbot bolted onto a terminal.

### 2.1 Natural Language to Command (Warp AI)

- Type plain English in the command input and Warp auto-detects it vs. shell commands using a **local, on-device classifier model** (no data leaves your machine until you explicitly hit Enter)
- Type `#` followed by a space to manually invoke AI command mode
- Examples: "show me all running Docker containers" generates `docker ps`; "find large files over 100MB" generates the appropriate `find` command
- The generated command is placed directly into your input line -- no copy/paste needed

### 2.2 AI Command Suggestions

- `#` + natural language description triggers command suggestion as you type
- Suggests commands with context awareness of your environment
- Run the suggestion with a single keystroke

### 2.3 Error Explanation

- Right-click any command output block and select "Ask Warp AI"
- Warp AI explains what the error means in plain English, identifies missing dependencies, and suggests fixes
- Select any text in terminal output and feed it directly into Warp AI for debugging
- Works on compiler errors, build failures, git errors, network errors, etc.

### 2.4 Active AI Recommendations

Active AI is a suite of proactive features (Settings > Agents > Warp Agent > Active AI):

- **Prompt Suggestions** -- Contextual banners that suggest what to ask Agent Mode when it detects an error or common scenario. Accept with `CMD+ENTER` (macOS) or `CTRL+SHIFT+ENTER` (Linux/Windows). The suggestion itself does not count toward AI limits; accepted prompts run in Agent Mode and do count.
- **Next Command Prediction** -- AI suggests the most relevant next command based on your active session, command history (enriched with git branch, exit code, and directory metadata). Accept with `RIGHT ARROW` or `CTRL-F`. **Unlimited across all plans, including Free.**
- **Suggested Code Diffs** -- Automatically surfaces potential fixes for command-line errors (compiler errors, merge conflicts, etc.). Accept with `CMD+ENTER` (macOS) or `CTRL+ENTER` (Windows/Linux). Does not count toward AI request limits, though monthly volumes scale by plan.

### 2.5 AI Autofill

- In Warp Drive, AI can automatically name and describe Workflows and Notebooks
- Reduces manual metadata entry when saving reusable commands

### 2.6 Chat with Warp AI

- A dedicated conversation view for asking questions, debugging, and getting guidance
- Supports model selection, voice input, image attachments, and conversation history
- Can be forked (`/fork`, `/fork-and-compact`, `/fork from`) to explore different directions

### 2.7 Model Support

Supported AI models across plans:
- OpenAI: GPT-5.2, GPT-4o
- Anthropic: Claude Sonnet 4.5, Claude Opus 4.5
- Google: Gemini 3 Pro
- BYOK (Bring Your Own API Key) on Build plan and above -- use your own OpenAI, Anthropic, or Google keys
- BYOLLM (Bring Your Own LLM) on Enterprise -- any model you host

### 2.8 AI Request Limits (Credits)

| Plan | Monthly Credits |
|------|----------------|
| Free | 150 (first 2 months), then 60/month |
| Build ($18/mo) | 1,500 |
| Max ($180/mo) | 18,000 |
| Business ($45/user/mo) | 1,500 per user |
| Enterprise (custom) | Negotiable |

"Next Command" predictions are unlimited across all plans.

---

## 3. Block-Based Architecture

### 3.1 What Is a Block?

A block is a discrete, self-contained unit that groups together: the prompt, the command entered, and all of that command's output (stdout + stderr). Each command you run creates its own block.

### 3.2 Block-Level Actions

Every block has a context menu (three dots) supporting:
- **Copy** -- Copy the entire block's output (`CMD+SHIFT+C`)
- **Copy Command** -- Copy only the command text
- **Copy Output** -- Copy only the output text
- **Filter** -- Search within a specific block's output using `CMD+F`
- **Collapse/Expand** -- Collapse output you no longer need to reclaim visual space
- **Bookmark** -- `CMD+B` (macOS) or `CTRL+SHIFT+B` (Windows/Linux) to mark important blocks
- **Share** -- Create web permalink or embed code (see Block Sharing below)
- **Ask Warp AI** -- Send the block as context to the AI for explanation or debugging
- **Re-run Command** -- Re-execute the exact command

### 3.3 Block Navigation

- Jump between blocks: `CMD+UP/DOWN` (macOS) or `CTRL+UP/DOWN` (Windows/Linux)
- Navigate bookmarked blocks: `OPTION+UP/DOWN` (macOS) or `ALT+UP/DOWN` (Windows/Linux)
- Bookmark indicators appear along the scrollbar with hover previews

### 3.4 Block Sharing (Permalinks & Embeds)

- Create a **web permalink** to share a block -- preserves colors, formatting, text-wrapping
- Links generate rich Open Graph/Twitter previews in Slack, Notion, Telegram, etc.
- Generate an **HTML iframe embed** to place interactive terminal blocks on web pages
- Shared blocks are accessible to anyone with the link, stored indefinitely until manually unshared
- Access: Click block menu > Share (`CMD+SHIFT+S`), title the block, choose "Create link" or "Get embed"
- Manage shared blocks at Settings > Shared blocks

### 3.5 Block Dividers

- Visual dividers can be toggled between blocks
- Configurable in Settings > Appearance

### 3.6 Block Visibility Separation

- Terminal command/output blocks are visually separated from AI agent conversation blocks
- Agent mode shows its own conversation blocks in a dedicated view, keeping your terminal history clean

### 3.7 Blocks as Agent Context

- Attach any block(s) to an agent query via the sparkles icon, `CMD+UP` (macOS), or `CTRL+UP` (Windows/Linux)
- Selected blocks become "pending context" until you submit a query; then they're "attached"
- Inside agent conversations, shell commands you run are automatically included as context for the next query
- This is how Warp provides rich, accurate terminal-state context to AI

---

## 4. Agent Mode & Oz Agents

### 4.1 What Is Agent Mode?

Agent Mode is Warp's feature that embeds an LLM directly into the terminal experience. It can execute multi-step workflows using natural language, understand your terminal environment, and self-correct when things go wrong. Launched June 17, 2024, massively expanded with Oz agents in 2025-2026.

### 4.2 Core Agent Capabilities

- **Write and edit code** across multiple files
- **Debug and fix errors** -- analyze stack traces, interpret error output, apply fixes
- **Self-correcting** -- if an agent runs an invalid command or causes an error, it automatically adjusts and retries until the task is completed
- **Run shell commands** and use output to guide next steps
- **Learn CLI tools** by reading `--help` or public documentation
- **Full Terminal Use** -- agents can operate inside interactive terminal applications (database shells, debuggers, REPLs, text editors, dev servers) and see live terminal buffer output
- **Task Lists** -- automatically track complex workflows with real-time progress updates
- **Ask clarifying questions** during Agent Mode (since April 2026)
- **Suggest follow-ups** when done (since April 2026)
- **Work with universal tools** -- any CLI tool that has `--help` or public docs: GitHub CLI, AWS/GCP, Kubernetes, Datadog, internal CLIs

### 4.3 Agent Context Sources

Agents pull context from multiple sources:
- **Blocks** -- terminal output blocks as context
- **Codebase Context** -- Git-tracked files indexed locally
- **Rules** -- global and project-level behavior guidelines from `AGENTS.md`
- **MCP Servers** -- external tools via Model Context Protocol (GitHub, Linear, databases, etc.)
- **Warp Drive** -- saved workflows, notebooks, prompts, and environment variables
- **Web Search** -- agents can search the web for up-to-date information
- **Skills** -- reusable instruction sets auto-discovered from projects
- **@-context menu** -- search across all available context sources

### 4.4 Agent Autonomy Profiles

Granular control over agent behavior via Settings > Agents > Profiles > Permissions:

| Action | Autonomy Levels |
|--------|----------------|
| Reading files | Let agent decide / Always prompt / Always allow / Never |
| Creating plans | Let agent decide / Always prompt / Always allow / Never |
| Executing commands | Let agent decide / Always prompt / Always allow / Never |
| Calling MCP servers | Let agent decide / Always prompt / Always allow / Never |

Built-in profiles:
- **Default** -- balanced permissions prompting for major actions
- **YOLO mode** -- loose permissions for fast iteration
- **Prod mode** -- restrictive for high-risk production environments

### 4.5 Terminal Mode vs. Agent Conversation View

- **Terminal Mode (default)** -- Clean, traditional terminal input. Agent controls appear only when needed.
- **Agent Conversation View (Oz)** -- Dedicated, expanded conversation UI with model selector, voice input, image attachments, and conversation history management.
- Conversations can be **forked** (`/fork`, `/fork-and-compact`, `/fork from`) to branch off and explore different approaches.

### 4.6 Third-Party CLI Agent Support

Warp supports running third-party CLI agents (Claude Code, OpenAI Codex, Gemini CLI, OpenCode, Cursor CLI, Copilot CLI) with:
- **New Rich Input** -- image paste support, multi-line editing
- **Code Review** -- send comments and diff hunks as context to CLI tools
- **Notifications UI** -- support for Claude Code and OpenCode notifications
- **File artifacts** -- download and filter file outputs from agent conversations

### 4.7 Interactive Code Review

A PR-style review workflow directly in Warp for agent-generated changes:
- **Inline code diffs** -- visual diffs you can inspect, modify, or reject
- **Review panel** -- summarizes all files and diffs touched by an agent
- **Approve, revert, or edit hunks** inline
- **Leave inline comments** on specific lines or blocks
- **Batch comments** -- add multiple comments, submit all feedback in a single pass
- **Agent applies feedback** and returns an updated diff
- Works with Warp's native agent AND third-party CLI agents
- Requires a Git-indexed directory

---

## 5. Cloud Agents & Orchestration (Oz Platform)

### 5.1 What Is Oz?

Oz is Warp's orchestration platform for cloud agents. It lets you deploy agents that run in the cloud, on schedules, or in reaction to events. Central dashboard at `oz.warp.dev`.

### 5.2 What Cloud Agents Do

- **Run in isolated cloud containers** (Docker environments)
- **React to events** -- Slack messages, GitHub issues/PRs, Linear tickets, CI steps, webhooks, cron timers, manual runs
- **Run on schedules** (cron-based)
- **Parallel execution** -- run many agents concurrently, shard repo-wide tasks
- **Persistent records** -- every run produces a transcript, status, metadata, and outputs
- **Team-wide observability** -- see what ran, when, what it did

### 5.3 Orchestration Flow

1. Trigger fires (schedule, Slack, GitHub, CI, webhook, API, manual)
2. Orchestrator creates a task, tracks its lifecycle
3. Agent executes on a host, optionally inside a reproducible Docker Environment
4. Persistent record with status, metadata, transcript, and outputs

### 5.4 Deployment Patterns

| Pattern | Description |
|---------|-------------|
| **CLI-only (BYO orchestrator)** | Run `oz agent run` from CI, scripts, or dev boxes. Your system orchestrates, Warp adds visibility. |
| **Oz-hosted + Oz orchestration** | Oz runs agents on Warp-managed infra inside Docker environments. Minimal setup, reproducible. |
| **Self-hosted execution** (Enterprise) | Agents run on your own infrastructure while Oz handles orchestration and observability. Two sub-modes: Managed (Oz orchestrates via `oz-agent-worker` daemon) and Unmanaged (you orchestrate, Warp tracks). |

### 5.5 Multi-Agent & Parallel Patterns

- **Fan-out / Sharding** -- Split a repo-wide task by directory/module. Launch multiple `oz agent run-cloud` instances, each handling a shard. Aggregate results (PRs, plans) afterward.
- **Multi-model comparison** -- Launch N runs with the same prompt but different models. Compare outputs, pick the best, or merge.
- **Parallel cloud coding agents** -- "Multithread complex development tasks" by running many agents concurrently.

### 5.6 Cloud Agent Specs by Plan

| Plan | Concurrent Agents | CPU / RAM |
|------|------------------|-----------|
| Build | 20 concurrent | 4 vCPU / 8 GiB |
| Max | 40 concurrent | 8 vCPU / 16 GiB |
| Enterprise | Custom | Custom |

---

## 6. Modern Input Editor

### 6.1 Fundamental Architecture

Warp's input editor is a full text editor built from scratch in Rust. It uses a SumTree data structure (a rope variant) and was co-developed with Nathan Sobo, creator of the Atom text editor. The editor sits inside the terminal app, bypassing the shell's character-by-character PTY input mode.

### 6.2 Features

- **Click anywhere to position cursor** -- not limited to the end of the line
- **Multi-cursor editing** -- place multiple cursors and type/edit simultaneously (like VS Code)
- **Text selections** -- click and drag to select, double-click for word selection
- **Copy/Cut/Paste** with standard shortcuts
- **Multi-line editing** -- soft wrapping without manual escaping
- **Autocomplete quotes, parentheses, and brackets** -- like an IDE
- **Undo/Redo** -- `CMD+Z` / `CMD+SHIFT+Z` (macOS)
- **IDE-standard keyboard shortcuts** for text navigation and manipulation
- **Vim keybindings** -- full modal editing (see section below)

### 6.3 Vim Mode

Toggle via Command Palette ("Vim Keybindings") or Settings > Features > Text Editing.

Supported:
- **Movement**: `h/j/k/l`, `w/b/e`, `gg/G`, `0/$`, `^`, `%`
- **Editing**: `d/c/s/x/y/p`, `u/Ctrl-r` (undo/redo), `~` (toggle case), `.` (repeat)
- **Text Objects**: `i`/`a` + `w`, `"`, `'`, `(`, `{`, `[`
- **Search**: `t/T/f/F` (character search), `/` and `?` open Warp's command search
- **Mode Switching**: `i/I/a/A/o/O` (insert), `v/V` (visual mode), `ESC` or custom key to exit insert
- **Registers**: Named (`a`-`z`), system clipboard (`+`, `*`), unnamed (`"`)

### 6.4 Syntax & Error Highlighting

- **Syntax Highlighting** -- Colors sub-commands, options/flags, arguments, and variables distinctly. Enabled by default. Togglable via Command Palette or Settings.
- **Error Underlining** -- Dashed red underline on invalid commands (binary doesn't exist). Enabled by default. Togglable.
- **Command Corrections** -- Auto-detects typos and missing parameters, suggests the correct command.
- Note: Newly installed apps/aliases require a new Warp session (new window/tab/pane) to be recognized.

### 6.5 Input Position

Three modes configurable in Settings > Appearance > Input:
- **Pinned to Bottom** -- Input area fixed at the bottom (traditional terminal feel) -- default
- **Pinned to Top** -- Input area fixed at the top
- **Waterfall** -- Input area scrolls naturally with output

---

## 7. Smart Completions & Autosuggestions

### 7.1 Command Completions

- **400+ CLI tools** with built-in completion specs out of the box
- Includes: `cargo`, `docker`, `terraform`, `git`, `npm`, `aws ec2`, `docker-compose`, `yarn`, `claude`, `codex`, `gcloud`, `kubectl`, `timedatectl`, and hundreds more
- **Fuzzy matching** -- `TAB` navigates through fuzzy-matched suggestions for commands, subcommands, options, flags, and path parameters
- **On-demand or automatic** -- Choose whether completions menu opens manually or automatically as you type
- Tab key behavior configurable -- use Tab for completions (arrow keys for suggestions) or vice versa

### 7.2 Autosuggestions

Fish-style ghost-text suggestions based on shell history:

- Accept full suggestion: `RIGHT ARROW` or `CTRL-F`
- Partial completion (word by word): `CTRL-RIGHT` (macOS) or `CTRL-SHIFT-RIGHT` (Windows/Linux)
- When cursor at end of buffer: `CTRL-E` + `RIGHT`
- Togglable via Command Palette

### 7.3 Command Inspector (Command X-Ray)

Surfaces inline documentation for flags and sub-commands as you type. Shows what each flag does without needing to consult `man` pages separately. Works inside the input editor.

---

## 8. IDE-Like Code Editor

Warp includes a **native tabbed code editor** -- not a full IDE replacement, but capable enough for quick edits, code review, and landing agent-generated changes without leaving the terminal.

### 8.1 Editor Features

- **Syntax highlighting** for dozens of languages: Rust, Go, Python, TypeScript/JS, C++, Shell, Java, C#, HTML/CSS, JSON, HCL/Terraform, Lua, Ruby, PHP, TOML, Swift, Kotlin, SQL, Elixir, Dockerfile, and more
- **Tabbed file viewer** -- group multiple files into a single tabbed viewer; reorder, close, drag tabs between panes
- **Split pane or new tab** layout options for opening files
- **Vim keybindings** support in the editor
- **Shared buffers** -- same file open in multiple tabs/panes stays in sync automatically, including disk changes
- **Find and Replace** -- regex support, case sensitivity, smart case preservation
- **Go to Line** dialog (`CTRL-G`) with line:column support
- **Global search** across files (`CMD-F` / `CTRL-SHIFT-F`)
- **File Tree / Project Explorer** -- browse, open, create, and manage files within repos

### 8.2 LSP (Language Server Protocol) Support

Built-in LSP integration (since March 2026):

| Language | Server | Features |
|----------|--------|----------|
| Rust | `rust-analyzer` | Hover, go-to-def, references, diagnostics, format-on-save |
| Go | `gopls` | Same as above |
| Python | `pyright` | Same as above |
| TypeScript/JS | `typescript-language-server` | Same as above |
| C/C++ | `clangd` | Same as above |

- Hover info -- type signatures, documentation, Markdown content
- Go-to-definition -- `CMD+CLICK` or `CTRL+CLICK` any symbol
- Find references -- `CMD+CLICK` on a definition to see all references
- Inline diagnostics -- errors/warnings as dashed underlines, real-time updates as you type
- Format on save -- auto-formatting via the language server
- Right-click context menu with LSP actions
- Automatic server lifecycle management (start on `cd`, stop when idle)

Limitations: LSP only works in local sessions (not SSH/WSL yet), one server per language, scoped to Git repository roots.

### 8.3 Coding Agent Integration

- Prompt-driven coding in natural language -- "Add a retry mechanism to this API call"
- Codebase Context indexing for accurate, context-aware responses
- Project Rules & Commands via `AGENTS.md`
- Multi-file and repo-wide changes
- Agent steering -- refine prompts, interrupt/retry, attach files/diffs/selections as context
- Code snippet references -- attach exact code as context to keep tokens lean

---

## 9. UI/UX Features

### 9.1 Command Palette

- `CMD+P` (macOS) or `CTRL+SHIFT+P` (Windows/Linux)
- Searchable access to every feature, setting, keyboard shortcut, and command
- Quickly toggle features like "Syntax Highlighting", "Vim Keybindings", "Autosuggestions"
- Used to launch Launch Configurations, Workflows, and Notebooks
- Fuzzy search with bolding of matched text

### 9.2 Tabs & Windows

- **Tabbed interface** -- multiple tabs in a single window
- **Vertical tabs** -- sidebar layout for tab organization (since April 2026)
- New tab placement configurable (`after_current_tab` default)
- Close tab: `CMD+W`
- New tab: `CMD+T`
- Switch tabs: `CMD+SHIFT+[` / `CMD+SHIFT+]`
- Tabs can be dragged to reorder
- Tab color customization

### 9.3 Global Hotkey (Quake/Visor Mode)

- Dedicated "dropdown" terminal window, toggleable via global shortcut
- Configurable pin position: top (like Guake), bottom (like iTerm2 hotkey window), left, or right
- Auto-hide when unfocused
- Dimensions configurable by percentage of screen
- Mutually exclusive with "Toggle all windows" shortcut

### 9.4 Toolbar Customization

- **Right-click toolbar > "Rearrange toolbar items"** to customize layout (since April 2026)
- Drag-and-drop context chips
- Toolbar items include: new tab, split pane, Warp Drive, AI toggles, etc.

### 9.5 Prompt Chips (Context Chips)

Native Warp prompt can show context chips: current working directory, Git branch, Kubernetes context, Python virtual environment, date/time, etc. Right-click prompt > Edit prompt to customize.

### 9.6 Link Detection

- URLs and file paths in terminal output are automatically detected and made clickable
- Clicking a file path opens it in Warp's code editor at the referenced line
- Agent references link to exact file locations

### 9.7 Pane Dimming

- Inactive split panes are visually dimmed to focus attention on the active pane
- Configurable in Settings

### 9.8 Infinite Scrollback

- Full scrollback buffer, not limited by terminal dimensions
- Scrollbar with block indicators and bookmark markers
- `CMD+UP/DOWN` to jump between blocks

---

## 10. Theming, Transparency & Appearance

### 10.1 Built-in Themes

Extensive theme library:
- Warp Dark, Warp Light
- Dracula, Fancy Dracula
- Solarized Dark, Solarized Light
- Gruvbox Dark, Gruvbox Light
- Jellyfish, Koi, Leafy, Marble
- Pink City, Snowy, Dark City, Red Rock
- Cyber Wave, Willow Dream, Phenomenon, Solar Flare, Adeberry

### 10.2 Theme Creator

- Auto-generate a theme from any background image
- Upload image, select background color, Warp generates a matching theme

### 10.3 OS Theme Sync

- Toggle "Sync with OS" to automatically switch between light and dark themes based on system settings
- Independently select which theme is used for light vs. dark OS mode

### 10.4 Custom Themes (YAML)

```toml
[appearance.themes]
theme = { custom = { name = "My Theme", path = "~/.warp/themes/my-theme.yaml" } }
```

### 10.5 Transparency & Blur

| Setting | Description |
|---------|-------------|
| **Window Opacity** | Slider 1-100 (100 = fully opaque) |
| **Window Blurring** | macOS: blur radius slider. Windows: Acrylic toggle. Linux: not supported. |

Platform notes:
- macOS: Large blur radii may impact performance, especially on Retina
- Windows: Opacity requires Vulkan or OpenGL backend (not DirectX 12 or Nvidia with "Auto"/"Prefer layered" present mode)

### 10.6 Fonts & Text

- Custom font selection (name, size, weight)
- Font ligatures support
- Line height ratio configurable
- Enforce minimum contrast setting

### 10.7 Cursor

- Style: bar, block, or underline
- Blinking: enabled or disabled

### 10.8 App Icon (macOS)

- Choose from 17 custom app icons

### 10.9 Spacing

- "Normal" or "Compact" spacing modes
- Block dividers toggle

---

## 11. Warp Drive

### 11.1 What Is Warp Drive?

Warp Drive is a cloud-synced, organizational workspace built into the Warp terminal. It stores and syncs your workflows, notebooks, prompts, and environment variables across all your devices and with your team.

### 11.2 What It Stores

Four types of objects:
- **Workflows** -- Parameterized, reusable commands (templates)
- **Notebooks** -- Interactive documentation (markdown + runnable shell blocks)
- **Prompts** -- Saved AI prompts for reuse
- **Environment Variables** -- Shared environment configuration

### 11.3 Storage & Sync

- All objects stored securely in the cloud, encrypted at rest (AES 256+)
- Changes sync immediately across all devices and team members
- Offline mode: edit personal objects (not synced until online), team objects become read-only

### 11.4 Workspaces

- **Personal workspace** -- Private to you
- **Team workspaces** -- Shared with your Warp team
- Objects moved from personal to team are shared with everyone
- WARNING: You cannot move objects back from team to personal (only copy/recreate)

### 11.5 Access

- Toggle sidebar: `CMD+\` (macOS) or `CTRL+SHIFT+\` (Windows/Linux)
- Also accessible from status bar or Command Palette

### 11.6 Sharing

Three levels:
- **Teams** -- All team members get full access
- **Direct Sharing** -- Share with individuals by email
- **Link-based Sharing** -- Public links accessible to anyone with the link

### 11.7 Permissions Model

| Action | Can View | Can Edit | Full Access |
|--------|----------|----------|-------------|
| Read/Execute workflows | Yes | Yes | Yes |
| Edit contents | No | Yes | Yes |
| Delete permanently | No | No | Yes |
| Modify permissions | No | No | Yes |

### 11.8 AI & Agent Integration

- Warp Drive serves as context for AI agents (Claude Code, Codex, Gemini CLI, OpenCode, etc.)
- Agents pull from your Workflows, Notebooks, Prompts, and Env Vars to generate more accurate responses
- Enabled by default (Settings > AI > Knowledge)

### 11.9 Pricing

- Free for up to 3 team members
- Build/Max/Business: unlimited objects, unlimited team members
- No hard storage limits

---

## 12. Workflows

### 12.1 What Are Workflows?

Workflows are **templatized, parameterized commands** that you name, save, and execute on-demand. More powerful than shell aliases because they support: descriptions, multiple arguments, argument types (enum/text), rich metadata, searchability, and team sharing.

### 12.2 Parameter Syntax

Arguments are defined with `{{double_curly_braces}}` in the command:

```yaml
command: |-
    brew tap beeftornado/rmtree
    brew rmtree {{package_name}}
```

### 12.3 Argument Types

- **Text** -- Free-form text input (default)
- **Enum** -- Predefined list of acceptable options (static list or dynamically generated via a shell command)

### 12.4 Enum Use Cases

| Use Case | Example |
|-----------|---------|
| Manage profiles | Store lists of profile names, DB URLs, environments |
| Get GraphQL Schema | Switch between dev/staging/prod environments |
| HTTP request data | Preset headers, query values, body data for API calls |

### 12.5 Aliases

Personal shortcuts with pre-filled default values and optional environment variables, synced across your Warp devices.

### 12.6 How to Execute

- **Warp Drive** -- click the workflow
- **Command Palette** (`CMD+P` / `CTRL+P`)
- **Command Search** (`CTRL+SHIFT+R` or `CTRL+R`)
- Use `SHIFT+TAB` to cycle through arguments when filling them in

### 12.7 Workflow Repository

- Public GitHub repo: [github.com/warpdotdev/workflows](https://github.com/warpdotdev/workflows) -- 149+ forks, 70+ contributors
- Community commands: [commands.dev](https://www.commands.dev)
- Local workflows directory: `~/.warp/workflows/`

### 12.8 Full YAML Template Format

```yaml
---
name: Uninstall a Homebrew package and all of its dependencies
command: |-
    brew tap beeftornado/rmtree
    brew rmtree {{package_name}}
tags:
  - homebrew
description: Uses rmtree to remove a Homebrew package and all of its dependencies
arguments:
  - name: package_name
    description: The name of the package that should be removed
    default_value: ~
source_url: "https://stackoverflow.com/questions/7323261/..."
author: Ory Band
shells: []
```

---

## 13. Notebooks

### 13.1 What Are Notebooks?

Notebooks are interactive, runnable documentation documents inside Warp Drive. They combine markdown text, syntax-highlighted code blocks, and runnable shell snippets into a single document. Similar in concept to Jupyter notebooks but for terminal/CLI workflows.

### 13.2 Key Features

- **Combine** markdown text, code blocks (multiple languages), and runnable shell snippets
- **Execute** command blocks directly into your terminal session with a click or `CMD+ENTER` / `CTRL+ENTER`
- **Parameterized arguments** in commands using `{{double_curly_braces}}`
- **Syntax highlighting** for code blocks across many languages
- **Embed existing Workflows** into notebooks
- **Mermaid diagram rendering** in markdown (since April 2026)
- **Markdown table rendering** (since April 2026)
- Accessible via Command Palette without leaving the terminal
- Export to Markdown (.md) format
- Multiple entry points: Warp Drive sidebar, Command Palette, or by opening `.md` files

### 13.3 Collaboration on Notebooks

- Only one editor at a time -- others open in Viewing mode
- Real-time sync across team members

---

## 14. Collaboration & Team Features

### 14.1 Teams

- Teams are groups of Warp users sharing a dedicated Warp Drive workspace
- Workspaces sync immediately -- all members see latest versions of Workflows, Notebooks, Prompts, Env Vars
- Admin (team creator) vs. Member roles with distinct capabilities
- Domain restriction -- limit team invites to specific email domains
- Team discoverability -- colleagues from the same email domain can discover each other's teams

### 14.2 Sharing

- **Team-wide** -- all members get access
- **Direct sharing** -- share with individuals by email
- **Link-based** -- public links with view or edit access levels

### 14.3 Team Security

- **SAML-based SSO** (Business plan and above)
- **Zero Data Retention** -- automatically enforced team-wide on Business plan
- **Admin dashboard** with multi-admin controls (Business plan)

---

## 15. Session Sharing

### 15.1 What Is Session Sharing?

Real-time multiplayer for the terminal. Stream your terminal session to teammates via a web link.

### 15.2 Features

- **Stream** your terminal in real time over the web
- **Hand over controls** -- teammates can actively type and control the shared session (not just watch)
- **Permissions controls** -- manage who can view vs. who can edit/control
- **Independent scrolling** -- each participant scrolls independently
- **Supports real-time vim and emacs co-editing**
- Use cases: team onboarding, pair programming, incident response

### 15.3 Limitations

- Secret Redaction is NOT applied during Session Sharing
- Free plan: up to 5 shared sessions

---

## 16. Split Panes & Synced Inputs

### 16.1 Split Panes

- Split right: `CMD+D` (macOS) or `CTRL+SHIFT+D` (Windows/Linux)
- Split down: `SHIFT+CMD+D` (macOS) or `CTRL+SHIFT+E` (Windows/Linux)
- Navigate: `CMD+[` / `CMD+]` (macOS) or `CTRL+SHIFT+{` / `CTRL+SHIFT+}` (Windows/Linux)
- Close pane: `CMD+SHIFT+W` (macOS) or `CTRL+SHIFT+W` (Windows/Linux)

### 16.2 Synced Inputs (Broadcast Input)

Warp's equivalent of iTerm2's "Broadcast Input" -- type in one pane, send to multiple panes simultaneously. Shipped May 2023.

Key difference from iTerm2:
- iTerm2 broadcasts raw keystrokes/bytes to all PTYs
- Warp syncs the **input buffer text** -- when you start syncing, all inputs are forced to the same starting state (text from focused pane is copied to others). This is safer for the "run the same command on multiple servers" use case.

### 16.3 Sync Options

- All panes in current tab
- All panes across all tabs (including future tabs)
- Can stop syncing for current tab or all panes/tabs

### 16.4 How to Access

- Via Command Palette (search "sync" / "synchronize")
- Indicator icon showing which tabs are synced

### 16.5 Known Limitations

- Only the focused terminal shows the cursor and UI elements
- Does NOT show all cursors flashing simultaneously (unlike iTerm2)
- Syncing resets other panes to match the focused pane's content when entering the mode
- Cannot add/remove individual panes from an active sync group
- Cross-window syncing not yet fully supported

---

## 17. Command History & Search

### 17.1 Command History

- `UP ARROW` in input editor -- prefix search based on what you've typed
- Per-session history isolation -- split panes have independent histories until closed

### 17.2 Command Search (`CTRL+R`)

- Fuzzy search with bolded matching text
- Shows: recent commands, named workflows, and rich metadata
- Rich history details include:
  - **Exit code** -- tell which commands succeeded or failed
  - **Working directory** at time of execution
  - **Git branch** (if applicable)
  - **Execution time** (relative, with absolute timestamp on hover)
  - **Workflow name** (if part of a saved workflow)

### 17.3 Timestamps

- Shown in Command Search (`CTRL+R`) and in the floating window for UP-arrow history
- Displayed as relative time (e.g., "2 hours ago") with full absolute timestamp available on hover
- Added August 2023 (GitHub issue #3485)

---

## 18. Markdown Viewer

### 18.1 Features

- Open any `.md` or `.markdown` file in a split pane alongside your terminal
- Toggle between editor and viewer modes
- `cat`, `glow`, and `less` commands trigger a banner to open `.md` files in the viewer
- Can be set as the default viewer for Markdown files (Settings > Features > General)

### 18.2 Runnable Shell Commands

- Shell commands inside triple-backtick code blocks (` ``` `) are interactive
- Click `>_` run icon or use `CMD+ENTER` (macOS) / `CTRL+ENTER` (Windows/Linux) to insert into terminal
- Supported language tags for shell blocks: `sh`, `shell`, `bash`, `fish`, `zsh`, `warp-runnable-command`

### 18.3 Keyboard Navigation

- `CMD+UP/DOWN` or `CTRL+UP/DOWN` -- move between shell blocks in the Markdown
- `CMD+L` / `CTRL+SHIFT+L` -- switch focus back to the terminal

---

## 19. Launch Configurations & Tab Configs

### 19.1 Tab Configs (New, Recommended)

- Save and share complete workspace setups (replacing legacy Launch Configurations)
- Save windows, tabs, panes, working directories, and colors
- Team-shareable for standardized dev environments

### 19.2 Launch Configurations (Legacy)

YAML-based saved window/tab/pane configurations. Still functional but superseded by Tab Configs.

Storage paths:
- macOS: `~/.warp/launch_configurations/`
- Windows: `%APPDATA%\warp\Warp\data\launch_configurations\`
- Linux: `~/.local/share/warp-terminal/launch_configurations/`

Ways to launch:
- Command Palette > type "Launch Configuration"
- Right-click the new Tab `+` button > choose configuration
- Menu bar (macOS): File > Launch Configurations
- Single-window configs: `CMD+ENTER` (macOS) or `CTRL+ENTER` (Linux/Windows) to launch in active window

### 19.3 YAML Example

```yaml
name: Example Panes
windows:
  - tabs:
      - title: Tab 1
        layout:
          split_direction: vertical
          panes:
            - cwd: /Users/warp-user/Documents
            - cwd: /Users/warp-user/Downloads
        color: blue
```

---

## 20. Session Restoration

- Enabled by default -- automatically restores windows, tabs, panes, and recent blocks on relaunch
- Saved to local SQLite database, overwritten on each quit
- Disable: Settings > Features > toggle off "Restore windows, tabs, and panes on startup"
- Clear session data by deleting the SQLite file (paths differ per OS)

---

## 21. Integrations

### 21.1 IDE & Editor Integrations

| Integration | How It Works |
|-------------|-------------|
| **VS Code** | `SHIFT+CMD+C` (macOS) / `CTRL+SHIFT+C` (Windows/Linux) opens Warp from VS Code |
| **JetBrains IDEs** | Configurable via Preferences > External Tools |
| **Zed** | Supported |
| **Cursor** | Supported |
| **Raycast** | Extension for opening new windows, tabs, or Launch Configurations |
| **Alfred** | Workflow support |
| **macOS Finder** | Right-click text file > Open in Warp's code editor |

### 21.2 Docker

- **Docker Extension** -- Browse running containers and open any container in a Warpified subshell with one click
- No need to manually type `docker exec` or container IDs
- Supported shells inside containers: bash, zsh, fish
- Currently macOS-only for the Docker extension GUI

### 21.3 SSH

Two mechanisms:

**SSH Extension (recommended, modern):**
- Installs lightweight remote server binary under `~/.warp/remote-server` on remote host (with explicit consent)
- Provides: real file tree (Project Explorer), reliable completions and autosuggestions, native code diffs, full input editor, blocks, command history and search
- Supports macOS and Linux remote hosts; bash and zsh shells
- Windows remote hosts not yet supported

**Legacy tmux-based (being deprecated):**
- Uses tmux Control Mode for command multiplexing
- Provides blocks, completions, input editor, and history search

### 21.4 Subshell "Warpification"

Warp auto-detects these commands and offers to "Warpify" the subshell (enable full IDE experience inside):

- `poetry shell`
- `eb ssh` (AWS Elastic Beanstalk)
- `gcloud compute ssh`
- `docker exec`
- `bash`, `zsh`, `fish`

Custom commands can be added under Settings > Subshells. Commands can be blocklisted.

### 21.5 Cloud Providers

- **gcloud compute ssh** -- Warpified SSH into GCP VMs
- **eb ssh** -- Warpified SSH into AWS Elastic Beanstalk
- **Kubernetes (kubectl)** -- extensive knowledge base coverage in Terminus docs
- Additional cloud integrations via MCP servers

### 21.6 MCP (Model Context Protocol)

- One-click MCP server installation
- Automatic detection of global/project-scoped MCP servers configured with `claude` or `codex` (since March 2026)
- File-based MCP server config at `~/.agents/.mcp.json` (global) or `.agents/.mcp.json` (project-local) (since April 2026)
- Connect external tools: GitHub, Linear, databases, custom APIs

---

## 22. Security & Privacy

### 22.1 Secret Redaction

- Detects and redacts secrets, passwords, API keys, IP addresses, and PII in terminal output
- **Disabled by default** -- must be manually enabled: Settings > Privacy > Secret Redaction
- Secrets are redacted before being sent to Warp's servers or any LLM provider
- Warp Drive prevents saving secrets in plain text
- Visual display: strikethrough (default) or asterisks (`********`)
- Click to reveal/copy the actual secret
- Custom regex patterns can be added for your own secret types
- NOT applied during Session Sharing

Built-in regex patterns cover: AWS access keys, GitHub PATs, OpenAI keys, Anthropic keys, Google API keys, Stripe keys, JWTs, Slack tokens, IP addresses, MAC addresses, phone numbers, Firebase auth domains, and more.

### 22.2 Telemetry Control

- Opt out of telemetry: Settings > Privacy > toggle off "Help improve Warp"
- Monitor telemetry in real-time via built-in Network Log
- Standard telemetry includes high-level product usage metrics only -- never user-generated content
- Free plan: telemetry must be enabled to use AI features
- Paid plans: can opt out of telemetry and still use AI

### 22.3 AI Data Handling

- AI input/output passed through to LLM APIs; Warp does not store it (for paid plans)
- OpenAI and Anthropic do not train models on Warp user data
- Zero Data Retention (ZDR) available for Enterprise -- OpenAI and Anthropic never retain your data
- ZDR automatically enforced team-wide on Business plan

### 22.4 Security Certifications

- SOC 2 Type 2 certified
- Data stored on Google Cloud Platform (GCP) in the United States
- AES 256+ encryption at rest; TLS 1.3 in transit
- Authentication: username/password, Google SSO, or GitHub SSO
- Enterprise: SAML/SSO (Okta etc.), domain verification, enforced privacy settings

### 22.5 Third-Party Services

- Sentry -- crash reporting (opt-out available)
- Rudderstack -- app analytics (opt-out available)

---

## 23. Performance & Rendering Engine

### 23.1 Architecture

- **Language**: Rust (~98% of codebase, 1M+ lines)
- **UI Framework**: Custom WarpUI (Entity-Component-Handle pattern, Flutter-inspired element layout)
- **GPU Rendering**: Metal (macOS) or wgpu (Windows, Linux, BSD), with WGSL shaders (~200 lines of shader code)
- **Grid Model**: Circular buffer grid (forked from Alacritty's model, then heavily customized)
- **Input Editor**: SumTree data structure (rope variant)

### 23.2 Block Architecture (Performance)

To support blocks (separate grids per command), Warp creates **separate grids per command** rather than a single global grid. Each block has its own: prompt grid, command input grid, and command output grid. This allows independent scrolling, collapsing, and memory management per block.

### 23.3 Circular Buffer Grid Optimizations

- Rows stored in a vector but may be "rotated" (screen index != vector index)
- Scrolling is O(1) metadata-only operation
- Rows allocated in chunks of 1,000 to amortize resize costs
- Occasional "re-zeroing" when the grid needs to grow

### 23.4 Benchmark Results

Official benchmarks (average ms, lower is better):

| Benchmark | Warp | Alacritty | Terminal.app | iTerm2 | WezTerm |
|-----------|------|-----------|-------------|--------|---------|
| dense_cells | 43.88 | 7.25 | 24.91 | 144.84 | 28.15 |
| scrolling | 30.06 | 31.75 | 283.34 | 1257.57 | 687.77 |
| scrolling_fullscreen | 37.40 | 37.36 | 307.03 | 1565.17 | 1205.00 |
| unicode | 66.47 | 16.78 | 34.45 | 93.01 | 1279.25 |

Key takeaways:
- Warp is ~42x faster than iTerm2 on scrolling tests
- Competitive with Alacritty on scrolling (30ms vs 32ms)
- Alacritty leads on dense_cells (7ms vs 44ms) and unicode (17ms vs 66ms)
- Average redraw time: ~1.9ms per frame, sustaining 144+ FPS on 4K

### 23.5 Real-World Resource Usage (8 tabs, 4 hours)

| Terminal | Idle (1 tab) | Under Load (8 tabs) |
|----------|-------------|---------------------|
| Kitty | 35-45 MB | 110 MB |
| iTerm2 | 80-168 MB | 290 MB |
| Warp | 210-410 MB | 380 MB |

Warp's higher memory usage stems from its rich UI, block model, and AI features -- it's doing more than a raw terminal emulator.

### 23.6 Input Latency

| Terminal | Latency |
|----------|---------|
| Kitty | 3-5ms |
| Alacritty | 3-5ms |
| Warp | 8-17ms |
| iTerm2 | 12-25ms |

Warp is perceptible to very fast typists but adequate for most users.

### 23.7 Cold Start

- Warp: ~1.2 seconds
- Kitty / Alacritty: near-instant
- iTerm2: ~0.5-1 second

---

## 24. Platforms & Shell Compatibility

### 24.1 Platforms

- **macOS**: x64 (Intel) and ARM64 (Apple Silicon) -- native, full-featured
- **Windows**: x64 and ARM64
- **Linux**: Debian/Ubuntu (.deb), Red Hat/Fedora (.rpm), openSUSE (.rpm)
- **Web**: WASM target (for web-compiled terminals)

Note: Linux version historically lagged behind macOS; Windows is newer. The macOS experience is the most mature and polished.

### 24.2 Shell Compatibility

| Shell | Status |
|-------|--------|
| zsh | Full support (default) |
| bash | Full support |
| fish | Full support |
| PowerShell | Supported |
| WSL | Supported |
| Git Bash | Supported |

### 24.3 Prompt Plugin Compatibility

| Plugin | Status |
|--------|--------|
| Powerlevel10k | Supported (v1.19.0+, requires Meslo Nerd Font) |
| Starship | Supported (some caveats) |
| Spaceship | Supported |
| oh-my-zsh | Supported |
| prezto | Supported |
| oh-my-bash | Not supported |
| bash-it | Not supported |
| tide (fish) | Not supported |
| oh-my-fish | Not supported |

### 24.4 Shell Integration Warning

iTerm2's shell integration breaks Warp's custom prompt. Disable it conditionally:

```bash
if [[ $TERM_PROGRAM != "WarpTerminal" ]]; then
    test -e "${HOME}/.iterm2_shell_integration.zsh" && source "${HOME}/.iterm2_shell_integration.zsh"
fi
```

---

## 25. Pricing Plans

As of October 30, 2025 (legacy Pro, Turbo, Lightspeed, and old Business plans retired).

### 25.1 Plan Comparison

| | Free | Build | Max | Business | Enterprise |
|---|---|---|---|---|---|
| **Price** | $0/mo | $18/mo | $180/mo | $45/user/mo | Custom |
| **Monthly AI Credits** | 150* / 60 | 1,500 | 18,000 | 1,500/user | Custom |
| **Frontier Models** | Limited | GPT-5.2, Claude Sonnet 4.5, Opus 4.5, Gemini 3 Pro | Same | Same | Same |
| **BYOK** | No | Yes | Yes | Yes | Yes |
| **BYOLLM** | No | No | No | No | Yes |
| **Codebase Indexing** | 1 codebase, 10K files | 40 codebases, 100K files each | Same | Same | Custom |
| **Cloud Agents (concurrent)** | No | 20 (4 vCPU/8 GiB) | 40 (8 vCPU/16 GiB) | Same as Build | Custom |
| **Warp Drive objects** | 10 workflows, 3 notebooks | Unlimited | Unlimited | Unlimited | Unlimited |
| **Session Sharing** | 5 sessions | Unlimited | Unlimited | Unlimited | Unlimited |
| **Cloud Conversations** | 30 stored | Unlimited | Unlimited | Unlimited | Unlimited |
| **SAML SSO** | No | No | No | Yes | Yes |
| **Zero Data Retention** | Manual config | Manual config | Manual config | Auto-enforced team-wide | Auto-enforced |
| **Seats** | 1 | 1 | 1 | Up to 50 | Custom |
| **Add-on Credits** | No | Yes (roll over 12 months) | Yes | Shared across team | Custom |
| **Self-hosted Agents** | No | No | No | No | Yes |
| **Dedicated Account Manager** | No | No | No | No | Yes |
| **Support** | Community | Private email | Private email | Admin dashboard | White-glove |

*150 credits/month for first 2 months, then 60/month.

### 25.2 Notable Pricing Details

- "Next Command" predictions are unlimited on ALL plans (including Free)
- Add-on credits roll over for 12 months
- Old Pro plan (~$10/mo, 500 requests) migrated to Build ($18/mo, 1,500 credits) in December 2025
- Old Team plan (~$18/mo per user) migrated to Business ($45/user/mo) in December 2025

---

## 26. Codebase Architecture

### 26.1 Overview

- **Repository**: [github.com/warpdotdev/warp](https://github.com/warpdotdev/Warp)
- **License**: AGPL v3 (app), MIT (WarpUI framework)
- **Stars**: ~48,000+
- **Forks**: ~3,100+
- **Language**: ~98% Rust, 1M+ lines
- **Crates**: 60+ Cargo workspace member crates

### 26.2 Key Crates

| Crate | Purpose |
|-------|---------|
| `warpui` / `warpui_core` | Custom UI framework (MIT-licensed) |
| `warp_core` | Core utilities, platform abstractions, feature flags |
| `editor` | Built-in code editor (tabs, LSP, find/replace) |
| `ipc` | Inter-process communication |
| `graphql` | GraphQL client/schema (backend API) |
| `persistence` | SQLite via Diesel ORM (local storage) |

### 26.3 Main Application Modules (`app/`)

| Module | Responsibility |
|--------|---------------|
| `terminal/` | Terminal emulation, shell management, block grid |
| `ai/` | Agent Mode, AI integration, codebase indexing |
| `drive/` | Cloud sync, Warp Drive features |
| `auth/` | Authentication, user management |
| `settings/` | Settings, preferences |
| `workspace/` | Workspace/session management, tabs, panes, splits |

### 26.4 Key Architectural Patterns

- **Entity-Component-Handle** (WarpUI): Views reference each other via handles, not direct ownership, avoiding cycles
- **Rotated Circular Buffer**: Terminal grids optimized for O(1) scrolling
- **Cross-Platform**: Native macOS/Windows/Linux + WASM target
- **Modular Workspace**: Workspaces contain configurations with terminals, notebooks, editor panes, file viewers
- **Tabs within Tabs**: File tabs inside workspace tabs
- **Feature Flag System**: Runtime-checked flags in `warp_core/src/features.rs`

### 26.5 Key Dependencies

- Tokio (async runtime)
- Alacritty (original grid source, heavily modified)
- Diesel (SQLite ORM)
- Hyper (HTTP)
- NuShell (shell integration)
- Core-Foundation / FontKit (macOS platform support)

### 26.6 Build & Development

- Bootstrap: `./script/bootstrap`
- Run: `./script/run`
- Test: `cargo nextest`
- Presubmit: `./script/presubmit` (fmt, clippy, tests)
- PRs must pass `cargo fmt` and `cargo clippy` cleanly

---

## 27. Recent Changelog (2026)

### April 22, 2026 (v0.2026.04.22)
- Toolbar chip rearrangement (right-click > Rearrange toolbar items)
- Mermaid diagram rendering in markdown notebooks
- Image paste in rich input editor for CLI agents
- Windows: "Start Warp at login" toggle + 408 new PowerShell cmdlet completions
- `/fork` opens in new pane (Enter) or new tab (Cmd+Enter)
- Per-query image limit raised to 20 (from 5); per-conversation to 200 (from 20)
- File artifacts in agent conversations with download and filtering
- Settings reorganized: Agents, Code, Cloud Platform subpages

### April 15, 2026
- Agent Mode "Last seen by agent at" indicator for long-running commands
- Vast expansion of shell completions (timedatectl, aws ec2, docker-compose, yarn, claude, codex, etc.)
- Code review comments can route to any terminal in the tab
- Skills searchable from @-context menu
- File-based MCP server config (`~/.agents/.mcp.json` global, `.agents/.mcp.json` project-local)

### April 8, 2026
- Vertical tabs (sidebar layout)
- Tab configs (save and share workspace setups)
- New Rich Input for third-party CLI agents (Claude Code, Codex, Gemini CLI, OpenCode)
- Revamped notifications UI (Claude Code, OpenCode support)
- Markdown table rendering in notebooks

### April 1, 2026
- [macOS] Right-click text files in Finder > open in Warp's code editor
- Send code review comments and diff hunks as context to third-party CLI tools
- Oz agents can ask clarifying questions during Agent Mode
- Agent suggests follow-ups when done

### March 25, 2026
- `/pr-comments` skill (fetch GitHub PR comments)
- Kitty Keyboard Protocol support
- Customizable toolbelt (drag-and-drop context chips)
- Syntax highlighting for Dockerfiles
- Settings to always show/hide agent thinking blocks

### March 18, 2026
- Automatic MCP server detection (global/project-scoped)
- Go to Line dialog (`CTRL-G`) with line:column support
- LSP (Language Server Protocol) support in native code editor
- `oz agent run` prints run ID with link to Oz dashboard

### February 10, 2026 -- Oz Launch
- Oz orchestration platform for cloud agents
- Agent Modality (terminal mode + dedicated conversation view)
- Cloud-Synced Conversations (persist across devices, share via link)
- Skills (reusable instruction sets auto-discovered from projects)
- Computer Use (agents interact with desktop environments in sandboxed cloud containers)

### January 2026
- Global search across files (`CMD-F` / `CTRL-SHIFT-F`)
- `/init` generates `AGENTS.md` instead of `WARP.md`
- Expanded web search support for additional models
- Kitty keyboard enhancement protocol full support
- New Max plan with 12x monthly credits

### Major 2025 Milestones
- Open-sourced (April 29, 2026 -- though some sources say earlier in 2025)
- New pricing structure (October 30, 2025)
- Agents 3.0 (multi-agent, full terminal use, code review integration)
- GPT-5.2 support

---

## 28. Comparison with Other Terminals

### 28.1 Philosophical Differences

| | Warp | iTerm2 | Kitty | Alacritty | WezTerm |
|---|---|---|---|---|---|
| **Philosophy** | AI-powered ADE | Feature-rich veteran | Speed purist | Minimal speed | All-in-one |
| **AI** | Deeply integrated | None | None | None | None |
| **Input Model** | Custom IDE editor | Shell PTY-driven | Shell PTY-driven | Shell PTY-driven | Shell PTY-driven |
| **Output Model** | Block-based | Stream-based | Stream-based | Stream-based | Stream-based |
| **Config** | GUI + settings.toml | Full GUI | Config files only | Config files only | Lua config |
| **Platforms** | macOS, Win, Linux | macOS only | macOS, Linux | macOS, Linux, Win | macOS, Linux, Win |
| **License** | AGPL v3 / MIT | GPL v2 | GPL v3 | Apache 2.0 | MIT |

### 28.2 When to Choose Each Terminal (per 2026 reviews)

**Choose Warp if**: You want AI-assisted command line, collaborative features, modern editing, and don't mind higher RAM usage. Best for teams and those who find traditional terminals intimidating.

**Choose iTerm2 if**: You need absolute stability, tmux integration, legacy plugin support, or GUI-based config. Safest for enterprise macOS environments. But pace of innovation is slowing.

**Choose Kitty if**: You want maximum speed, minimal resource usage, and are comfortable with config-file-only management. Best for terminal power users.

**Choose Alacritty if**: You want the absolute floor of input latency. Minimal features, maximum speed.

**Choose WezTerm if**: You want an all-in-one (multiplexer built in, Lua scripting, GPU rendering) with cross-platform consistency.

### 28.3 Representative Quote (from a 2026 review)

> "On a fresh M3 MacBook Pro in 2026, our default install is WezTerm, plus Warp for AI-heavy days, plus Alacritty for the absolute floor of input latency. iTerm2 is not on the install list at all."

---

## 29. Sources

- [Warp Official Site](https://www.warp.dev)
- [Warp AI / Agent Mode](https://www.warp.dev/ai)
- [Warp Documentation](https://docs.warp.dev)
- [Warp GitHub Repository](https://github.com/warpdotdev/warp) -- open-source, AGPL v3
- [Warp Workflows Repository](https://github.com/warpdotdev/workflows)
- [Warp Changelog](https://docs.warp.dev/changelog)
- [Warp Pricing](https://www.warp.dev/pricing)
- [Warp Drive](https://www.warp.dev/warp-drive)
- [Warp Session Sharing](https://www.warp.dev/session-sharing)
- [Warp All Features](https://warp.dev/all-features)
- [Warp vs iTerm2 Comparison](https://www.warp.dev/compare-terminal-tools/iterm2-vs-warp)
- [How Warp Works (Blog)](https://www.warp.dev/blog/how-warp-works)
- [The Block Model Behind Warp's ADE (Blog)](https://www.warp.dev/blog/block-model-behind-warps-agentic-development-environment)
- [Warp Agent Mode Blog Post](https://warp.dev/blog/agent-mode)
- [Warp Terminal Guide (AI:Productivity)](https://aiproductivity.ai/blog/warp-terminal-guide)
- [Warp Review 2026 (Doolpa)](https://doolpa.com/article/warp)
- [Warp vs iTerm2 vs Kitty 2026 (DevToolReviews)](https://www.devtoolreviews.com/reviews/warp-vs-iterm2-vs-kitty-best-terminal-emulator-2026)
- [Best Terminal Emulators 2026 Comparison](https://www.devtoolreviews.com/reviews/best-terminal-emulators-2026)
- [Warp vs iTerm2 vs Alacritty vs WezTerm: 90 Days Benchmark](https://novvista.com/warp-vs-iterm2-vs-alacritty-vs-wezterm-90-days-of-daily-use-latency-memory-and-real-productivity-numbers/)
- [Warp Review (AgentRank)](https://www.agentrank.tech/blog/warp-terminal-review-ai-powered-terminal)
- [Warp Launch Log 2 (Blog)](https://warp.dev/blog/launch-log-2)
- [Warp Agents 3.0 (Blog)](https://www.warp.dev/blog/agents-3-full-terminal-use-plan-code-review-integration)
- [Warp Free Tier Review (XDA Developers)](https://www.xda-developers.com/warps-free-tier-finally-got-good-enough-to-make-me-delete-iterm2/)

---

*This document was compiled from Warp's official documentation, blog posts, changelog, GitHub repository, and independent third-party reviews, all accessed May 2026.*
