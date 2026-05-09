# Zed Editor -- Comprehensive Feature Catalog

> Researched: May 9, 2026. Current stable version: ~v1.1.5 (1.0 shipped April 29, 2026).
> Sources: zed.dev official docs, blog, GitHub releases, community comparisons.

---

## Table of Contents

1. [Overview & Architecture](#1-overview--architecture)
2. [AI Capabilities](#2-ai-capabilities)
3. [UI/UX & Customization](#3-uiux--customization)
4. [Collaboration](#4-collaboration)
5. [Performance & Architecture (Deep Dive)](#5-performance--architecture-deep-dive)
6. [Extensibility & Plugins](#6-extensibility--plugins)
7. [Terminal](#7-terminal)
8. [Editing Model](#8-editing-model)
9. [Git Integration](#9-git-integration)
10. [Language Support (LSP & Tree-sitter)](#10-language-support-lsp--tree-sitter)
11. [Remote Development & Dev Containers](#11-remote-development--dev-containers)
12. [Debugger (DAP)](#12-debugger-dap)
13. [Unique Features (Nothing Else Has These)](#13-unique-features-nothing-else-has-these)
14. [Pricing & Plans](#14-pricing--plans)
15. [Roadmap](#15-roadmap)
16. [Limitations & Known Issues](#16-limitations--known-issues)

---

## 1. Overview & Architecture

**What Zed is**: An open-source, GPU-accelerated, multiplayer-capable code editor with deeply integrated AI. Written from scratch in **Rust** by the team that created **Atom, Electron, and Tree-sitter** (Nathan Sobo, Antonio Scandurra, Max Brunsfeld).

**Core architecture**:
- **Custom UI framework**: GPUI -- renders the entire UI as if it were a videogame, using the GPU for all rasterization
- **Buffer stack**: Rope (immutable SumTree) -> CRDT text layer -> Language layer (Tree-sitter + LSP) -> MultiBuffer (excerpt aggregation)
- **GPU backends**: Metal (macOS), DirectX 11 (Windows), wgpu/Vulkan (Linux)
- **Extension runtime**: WebAssembly (Wasmtime) with WASI and the WebAssembly Component Model
- **Collaboration**: CRDT-based replication layer for real-time sync
- **AI**: Native agentic AI with an open ACP protocol, plus built-in Zeta2 edit prediction model

**Platforms**: macOS, Linux, Windows (all production-ready as of 2026).

**License**: Apache 2.0 (open source).

---

## 2. AI Capabilities

This is Zed's most rapidly evolving area. AI is woven into the editor at multiple levels.

### 2.1 Agent Panel (Text Threads)

The central hub for agentic AI interaction. Open via `agent: new thread` or the sparkle icon in the status bar.

**Core workflow**: Describe a task in natural language. The agent discovers context, edits files, runs terminal commands, and stays on task until completion.

**Key sub-features**:

| Feature | Detail |
|---------|--------|
| **Agentic editing** | Agent can search codebase, edit files, run shell commands, spawn subagents |
| **Context injection via `@`** | `@file`, `@directory`, `@symbol`, `@thread`, `@rules`, `@diagnostics`, `@image` (clipboard paste for vision models) |
| **Slash commands** | `/selection`, `/terminal`, `/tab`, `/symbols`, `/prompt`, `/now`, `/file`, `/fetch`, `/diagnostics`, `/default` |
| **Profiles** | Built-in: Write (full), Ask (read-only), Minimal (no tools). Custom profiles can be created |
| **Tool permissions** | Per-tool: `confirm`, `allow`, `deny`. Supports regex/pattern-based overrides |
| **Checkpoints** | Auto-created on every edit; one-click rollback |
| **Change review** | Unified diff or single-file inline diff; accept/reject hunks or entire change sets |
| **Thread management** | Auto-generated editable titles, archiving, restoration, permanent deletion |
| **"New from Summary"** | Compact long threads approaching context limits |
| **Message queuing** | Queue messages while agent is generating; edit or cancel queued items |
| **Top-down streaming** | Responses stream from top with auto-scroll |
| **Agent Panel persistence** | Threads survive editor restarts |
| **Notifications** | Visual + sound when agent finishes generating |

### 2.2 Parallel Agents

A major 2026 feature (shipped April 2026). Run **multiple agent threads simultaneously**, each with:
- Its own agent model
- Its own context window
- Independent conversation history

**Threads Sidebar**: `cmd-alt-j` / `ctrl-alt-j`. Manages all active threads.

**Use cases**:
- Mix different agents per thread (Zed Agent, Claude Agent, Codex, OpenCode, Gemini CLI, etc.)
- Work across multiple projects simultaneously
- Isolate worktrees for safe parallel editing
- Use `spawn_agent` tool for subagent delegation within a thread

### 2.3 Inline Assistant (Transformations)

Triggered with `Ctrl+Enter` (`Cmd+Enter` on macOS). Prompt-driven code transformation.

**How it works**: Select code (or place cursor on a line) -> `Ctrl+Enter` -> write a prompt -> model replaces selection with transformed output.

**Features**:
- Works in: editor, rules library, channel notes, terminal panel
- **Multiple cursors**: Same prompt sent to each cursor position for parallel transformations
- **Multiple models**: Configure `inline_alternatives` in settings to cycle outputs from different models
- **Prefill prompts**: Custom keybindings can prefill specific prompts (e.g., `ctrl-shift-enter` always opens with "Build a snake game")
- **Context**: Supports `@`-mentions and image paste (same as Agent Panel)
- **`@thread`**: Reference a thread from Agent Panel to refine without re-explaining

### 2.4 Edit Predictions (Zeta2)

Zed's built-in, open-weight edit prediction model. Predicts your **next edit** (not just next token at end of line).

**How it's different from autocomplete**:
- Predicts rewrites at arbitrary cursor positions, not just line endings
- Sees your recent edit history (fine-grained stream, not compressed blob)
- Understands types and symbols via LSP context (new in Zeta2)
- Uses a "suffix-prefix-middle" (SPM) format with git-merge markers around the cursor region

**Zeta2 architecture**:
- Base: ByteDance Seed Coder (8B), fine-tuned via knowledge distillation
- Teacher model: Anthropic Sonnet 4.6 generates training data
- Training: ~100,000 examples from consensual, opt-in, open-source-only user edit traces
- Output: Rewrites the editable region with predicted edits applied
- ~30% improvement in acceptance rate over Zeta1

**Edit prediction providers** (configurable):
| Provider | Detail |
|----------|--------|
| **Zeta2** | Built-in, free tier: 2,000/month; Pro: unlimited. Default |
| **GitHub Copilot** | Next Edit Suggestions (NES) -- fully integrated since Feb 2026 |
| **Ollama** | Run Zeta2 locally |
| **Codestral** | Mistral's model |
| **Mercury Coder** | By Inception |
| **Sweep** | By Sweep AI |
| **Custom** | Any OpenAI-compatible server |

**Settings**:
- `edit_predictions_disabled_in`: glob-based exclusions (e.g., no predictions in `.env`, `*.pem`)
- `edit_predictions_disabled_in`: can target comments, strings, specific language contexts
- Per-language override: disable AI predictions in specific file types

### 2.5 ACP (Agent Client Protocol) & External Agents

An **open standard protocol** (Apache 2.0) created by Zed. Think LSP but for AI agents -- any agent can work in any ACP-compatible editor. Transport: JSON-RPC over subprocess stdio.

**Why it matters**: You can use Claude Agent, Gemini CLI, Codex, or any ACP agent **without giving Zed your API key**. Nothing touches Zed's servers.

**Supported external agents** (via ACP Registry):

| Agent | Auth Options | Notes |
|-------|-------------|-------|
| **Gemini CLI** | OAuth, API key, Vertex AI | Reference implementation |
| **Claude Agent** | API key, Claude Pro/Max | Reads CLAUDE.md. No history resumption/checkpointing |
| **Codex CLI** | ChatGPT login, CODEX_API_KEY, OPENAI_API_KEY | Reads ~/.codex/config.toml |
| **OpenCode** | API keys | Uses OpenCode Go provider |
| **GitHub Copilot CLI** | GitHub auth | |
| **Qwen Code** | API keys | |
| **Kimi CLI** | API keys | |
| **Mistral Vibe** | API keys | |
| **Goose** | API keys | From Square |
| **Cline** | API keys | |
| **Augment Code** | API keys | |
| **Qoder CLI** | API keys | |
| **Stakpak** | API keys | |
| **Blackbox AI** | API keys | |

**ACP Registry**: `zed: acp registry` -- centralized distribution. Install, manage, and remove agents. Built-in agents are now managed through the registry rather than hardcoded.

**Editors supporting ACP** (besides Zed): JetBrains IDEs (in progress), Neovim (via CodeCompanion/avante.nvim), Emacs (via agent-shell), Obsidian (via ACP plugin).

### 2.6 MCP (Model Context Protocol) Support

Zed supports connecting **MCP servers** to extend agent knowledge and tool access. MCP servers can run:
- Locally
- On remote servers (since January 2026, with `"remote": true` in config)
- Via extensions

### 2.7 Model Flexibility

Three tiers of model access:

| Tier | How | What |
|------|-----|------|
| **Zed-hosted** | Pro plan ($10/mo) | Claude Sonnet, Claude Opus, GPT-5 (mini/nano), Gemini 2.5 Pro/Flash, DeepSeek V4-Pro/Flash |
| **BYOK** | Your own API keys (free) | Anthropic, OpenAI, Google, OpenRouter, Vercel AI Gateway, any OpenAI-compatible endpoint |
| **Local** | Ollama or similar | Zeta2, any model you can run locally -- everything stays on your machine |

**Model favorites**: Pin favorite models; cycle with `alt-tab`.

**Thinking effort controls**: For thinking-enabled models (Claude extended thinking, etc.), configurable effort levels.

**BYOK context windows**: Up to 1M tokens for Claude Opus & Sonnet with your own key.

### 2.8 Rules Library

A full editor interface for writing and managing AI rules/prompts. Open via Agent Panel menu or `agent: open rules library`.

**Key features**:
- Create, duplicate, delete, set default rules
- `.rules` files at project root are auto-included in all Agent interactions
- Compatible formats: `.cursorrules`, `.windsurfrules`, `.clinerules`, `AGENTS.md`, `CLAUDE.md`
- Rules can use slash commands to dynamically inject context (e.g., `/file`, `/symbols`)
- Rules can nest other rules with `/prompt`
- Replaces the older "Prompt Library" (backwards compatible)

### 2.9 AI Commit Message Generation

Click the pencil icon in the commit editor or use `git: generate commit message` (`alt-tab` / `alt-l`). Generates a commit message based on staged changes.

---

## 3. UI/UX & Customization

### 3.1 Layout System

**Panel management**:
| Panel | Toggle Shortcut |
|-------|----------------|
| Project Panel | `cmd-shift-e` / `ctrl-shift-e` |
| Collaboration Panel | `cmd-shift-c` / `ctrl-shift-c` |
| Outline Panel | `cmd-shift-b` / `ctrl-shift-b` |
| Terminal Panel | `` ctrl-` `` |
| Threads Sidebar | `cmd-alt-j` / `ctrl-alt-j` |
| Agent Panel | Sparkle icon or `agent: new thread` |

**Dock positions**: Terminal dock (`bottom`, `left`, `right`); Project Panel dock (`left`, `right`).

**Panel Layout Switcher** (May 2026): Toggle between **classic** layout and **agentic** layout (Threads Sidebar + Agent Panel on left, Project/Git panels on right).

**Centered layout**: Configurable left/right padding ratios for a focused editing view.

**Bottom dock layout styles**: `contained`, `full`, `left_aligned`, `right_aligned`.

**Zen mode**: Hide status bar, breadcrumbs, and quick actions individually or all at once.

**Dock panel**: A unique concept -- a special pane that can be fetched/dismissed with `shift-escape` while keeping its contents persistent. Commonly used for terminals, docs, or diagnostics.

**Tab management**: Tabs across the top, splittable panes. No drag-tabs-to-new-window (current limitation).

### 3.2 Themes

**Built-in theme selector**: `Cmd+K Cmd+T` (`Ctrl+K Ctrl+T`) -- browse and preview in real time.

**Theme modes**: `system` (follow OS), `light`, `dark`. Separate light/dark pair configurable.

**Theme overrides**: Per-theme color customization in `settings.json`:
```json
{
  "theme_overrides": {
    "One Dark": {
      "editor.background": "#333",
      "syntax": { "comment": { "font_style": "italic" } }
    }
  }
}
```

**Custom local themes**: `.json` files in `~/.config/zed/themes/`. Extensions can also ship themes.

**Theme gallery**: [zed-themes.com](https://zed-themes.com) -- community site for previewing hundreds of themes.

**Syntax highlighting**: Theme-controlled via Tree-sitter captures (`@function`, `@keyword`, `@string`, `@type`, `@variable`, etc.) plus optional LSP semantic token layering.

### 3.3 Fonts

Three independent font settings:

| Setting | Purpose | Default |
|---------|---------|---------|
| `buffer_font_family` | Editor text | `.ZedMono` (Lilex) |
| `ui_font_family` | UI elements | `.ZedSans` (IBM Plex) |
| `terminal.font_family` | Terminal | Inherits editor |

**Per-setting overrides**:
- `buffer_font_size`, `ui_font_size`, `terminal.font_size`
- `buffer_font_features`: OpenType features (e.g., disable ligatures with `{"calt": false}`)
- `buffer_font_fallbacks`: Fallback font list (e.g., `["Nerd Font"]` for icons)
- `terminal.font_fallbacks`: Separate terminal fallback list

**Line height**: `"comfortable"` (1.618), `"standard"` (1.3), or custom value.

**Can you customize font per language?** Not directly, but you can use `languages.<lang>.font_size` to set per-language font size overrides. Full font family per language is not currently a documented setting.

**Scroll-wheel font size zooming**: `mouse_wheel_zoom` setting (new April 2026).

**Subpixel text rendering**: ClearType-style on Windows & Linux (since January 2026).

### 3.4 Keybindings

**Base keymap presets**: VS Code (default), Atom, Emacs (Beta), JetBrains, Sublime Text, TextMate, Cursor, or None.

**Keymap editor**: `Cmd+K Cmd+S` (`Ctrl+K Ctrl+S`) or `zed: open keymap`.

**Configuration**: `~/.config/zed/keymap.json` (macOS/Linux), `%APPDATA%\Zed\keymap.json` (Windows).

**Binding features**:
- Modifiers: `ctrl-`, `cmd-`, `alt-`, `shift-`, `fn-`
- Key sequences: `"cmd-k cmd-s"` (chords)
- Context-aware: bindings only in `Editor`, `Terminal`, `ProjectPanel`, or with conditions like `Editor && vim_mode == normal`
- Action arguments: pass parameters to actions (e.g., inline assist with prefilled prompt)

### 3.5 UI Element Toggles

Individually toggleable via settings:
- Tab bar
- Status bar (partial or full hide)
- Scrollbar
- Minimap
- Gutter (line numbers, git indicators, fold indicators)
- Breadcrumbs
- Inlay hints
- Inline diagnostics

### 3.6 Which-Key Modal

Introduced January 2026 (`"which_key": {"enabled": true}`). When enabled, shows available keybindings in a modal after a delay when you pause mid-sequence. Similar to Emacs/VSpaceCode/Spacemacs which-key functionality.

---

## 4. Collaboration

Zed's collaboration is **built in**, not extension-based. Uses CRDTs for conflict-free real-time sync.

### 4.1 Channels

**Persistent rooms** for ongoing team collaboration. Each channel maps to a project or workstream.

**Features**:
- **Subchannels**: Hierarchical organization under parent channels; permissions inherited
- **Persistent membership**: Invite team members via right-click -> "Manage members"
- **Channel Notes**: Shared Markdown file per channel for status, ideas, design discussions (accessible even without joining)
- **Voice chat**: Built-in microphone support; mute/unmute toggle; configurable `mute_on_join`
- **Public channels**: Guest users get read-only access; hosts can grant temporary write access
- **Livestreaming**: Public channels can be used for broadcasting
- **Ambient awareness**: See who's in which channel via avatars in the Collaboration Panel
- **Channel favorites**: Pin favorite channels in the collab panel (new April 2026)

### 4.2 Pair Programming & Real-Time Editing

**Shared Projects**:
- Share via the `Share` button
- Collaborators can open, edit, save files, run searches, and interact with language servers as if local
- Warning: sharing a project gives file-system access within that project

**Following**:
- Click a collaborator's avatar or `ctrl-alt-cmd-f` to follow
- Your pane tracks their cursor, scroll position, and file navigation
- Can follow in one pane while working independently in another
- Auto-follow on project join (you immediately follow the person who invited you)

**Cursor colors**: Each collaborator gets a distinct cursor color. Active project members in color, inactive in gray.

### 4.3 Screen Sharing

**Built in -- no Zoom/Meet needed**:
- Click the monitor icon in the title bar to share your entire screen
- Choose specific display via chevron menu
- **Auto-switching**: When following someone, Zed auto-switches between following their cursor (when in editor) and viewing their screen share (when they leave Zed, e.g., to a browser)
- Dedicated screen tab can be opened from the Collaboration Panel
- Security: collaborators see your entire screen -- stop sharing when done

### 4.4 Contacts & Accounts

- Add contacts via GitHub username
- Requires a Zed account (free)
- Collaboration Panel: `cmd-shift-c` / `ctrl-shift-c`

---

## 5. Performance & Architecture (Deep Dive)

### 5.1 GPU Rendering (GPUI)

Zed's custom UI framework renders everything like a videogame:

- **Target**: 120 FPS with ~8.33ms per frame budget
- **Custom shaders** per primitive: rectangles (SDF-based, including rounded corners), drop shadows (Evan Wallace's Gaussian approximation for rounded rects -- 4 samples on sliding axis, separable axis blurring), glyphs (GPU atlas from OS native rasterization), icons, images
- **Text**: Single instanced draw call per frame. Glyphs cached in GPU atlas. OS native shaping (CoreText on macOS, DirectWrite on Windows).

**2026 GPU backend change**: Linux switched from Blade to **wgpu** (PR merged Feb 13, 2026). Results:
- CPU draw: ~301us -> ~236us (-21%)
- GPU draw: ~12.3ms -> ~12.0ms
- Peak memory: ~540MB -> ~538MB
- NVIDIA/Wayland freezes: resolved
- Implementation: single global buffer per frame, `MemoryHints::MemoryUsage`, WGSL shaders, unified surface creation

**GPU device loss recovery**: Graceful recovery on Linux X11 (backward-compatible with wgpu migration).

### 5.2 Buffer Architecture (4-Layer Stack)

| Layer | Crate | Responsibility |
|-------|-------|---------------|
| **Rope** | `rope` | Immutable SumTree of fixed-size Chunks (CHUNK_BASE=64). O(log n) slicing, cloning |
| **text::Buffer** | `text` | CRDT collaborative editing, anchors (survive edits), undo/redo, Lamport timestamps |
| **language::Buffer** | `language` | Tree-sitter syntax trees, diagnostics, LSP integration, code actions |
| **MultiBuffer** | `multi_buffer` | Excerpt aggregation from multiple buffers into one virtual document |

Each layer exposes cheaply-clonable **snapshot** types for concurrent reads.

### 5.3 Memory Usage

**Benchmarks** (M4 Max / M3 Pro MacBook, 2025-2026):

| Metric | Zed | VS Code | Cursor |
|--------|-----|---------|--------|
| Cold Start | **~180-248ms** | 1.2-2.1s | 2.1-2.4s |
| RAM (Idle) | **~142-184MB** | 518-730MB | 612-920MB |
| Keystroke Latency | **~8ms** | ~18ms | ~22ms |
| Large File (50MB) | **Instant** | ~3.2s | ~3.5s |

**Memory optimization history**:
- CHUNK_BASE 16 -> 64: ~50% buffer memory reduction (138MB .tex file: 827MB -> 396MB)
- mimalloc allocator by default (aggressive memory retention; pools are kept even after file close; can be overridden)

**Startup memory**: Empty GPUI window uses ~10MB (Windows), vs ~100MB for comparable wgpu example.

### 5.4 LSP Performance Caveat

On massive monorepos (2M+ LOC TypeScript), VS Code Insiders actually **beats Zed** for LSP-heavy operations (find-references, rename) due to years of tsserver pipeline optimization. Zed wins on editor-surface operations (typing, scrolling, file open) but loses on extreme-scale language-server operations.

---

## 6. Extensibility & Plugins

### 6.1 WASM-based Extension System

**Architecture**: Extensions are written in **Rust**, compiled to **WebAssembly** (.wasm), and run in a sandboxed **Wasmtime** runtime. Uses the **WebAssembly Component Model** with **WIT** (Wasm Interface Type) as the IDL.

**Why WASM?**
- Sandboxed security (restricted capabilities)
- Cross-platform (single .wasm binary runs everywhere)
- Permission system for all capabilities

**Extension manifest** (`extension.toml`):
```toml
[extension]
name = "my-extension"
version = "1.0.0"
```

**Compilation target**: `wasm32-wasip2` (wasm32-wasip1 also supported).

**Build tool**: `zed-extension` CLI produces `archive.tar.gz` + `manifest.json`.

### 6.2 Extension Capabilities

| Capability | Description |
|------------|-------------|
| **Language servers** | Download, configure, launch LSP binaries |
| **Tree-sitter grammars** | Syntax highlighting via Wasm-compiled parsers |
| **Tree-sitter queries** | Highlights, indents, outline, injections, text objects, etc. |
| **Themes** | Custom color themes (JSON format) |
| **Slash commands** | Custom `/` commands for the Agent Panel's text threads |
| **Debug adapters** | DAP integration (provide DAP binary) |
| **Context servers** | MCP servers for AI context |
| **HTTP client** | Make HTTP requests from extensions |
| **Key-value store** | Persist extension data |
| **npm packages** | Install and use npm dependencies |

### 6.3 Extension API (`zed_extension_api`)

Extensions implement the `Extension` trait:
```rust
use zed_extension_api as zed;

struct MyExtension;

impl zed::Extension for MyExtension {
    fn new() -> Self { Self }
    fn language_server_command(
        &mut self,
        language_server_id: &zed::LanguageServerId,
        worktree: &zed::Worktree,
    ) -> zed::Result<zed::Command> {
        // Configure and launch LSP
    }
}
zed::register_extension!(MyExtension);
```

**`Worktree` resource** provides: root path, file reading, binary discovery (`which`), shell environment, etc.

### 6.4 Current Limitations

- **No UI extensibility**: Extensions cannot create new panels, modify editor UI, or add views
- **Extension registry is a Git repo** (`zed-industries/extensions`) -- team acknowledges this "doesn't scale" and will change
- **~800 extensions** available (vs 50,000+ for VS Code)
- **Not compatible** with VS Code extensions
- **License restrictions**: Registry requires approved open-source licenses (Apache-2.0, MIT, etc.)

---

## 7. Terminal

### 7.1 Built-in Terminal Emulator

**Multiple instances**: Toggle with `` ctrl-` ``, open new with `Ctrl+~`.

**Split terminals**: Horizontally with `Cmd+D` (macOS) / `Ctrl+Shift+5` (Linux/Windows).

**Dual placement**:
- **Terminal Panel** (docked bottom/left/right)
- **Center Pane** (as a regular editor tab)

### 7.2 Terminal Features

| Feature | Detail |
|---------|--------|
| **Custom shell** | Override default via `terminal.shell.program` |
| **Environment variables** | Passed through from editor |
| **Python venv auto-detection** | Automatically activates detected virtual environments |
| **Custom fonts** | Separate `terminal.font_family`, `terminal.font_size`, `terminal.font_fallbacks` |
| **Vi mode** | Terminal supports vi-style navigation |
| **Path hyperlinks** | Clickable `src/main.rs:42` style paths |
| **Copy on select** | Highlight to copy |
| **Alternate scroll mode** | Supported |
| **Login shell** | Terminal launches as login shell (`.zshrc`/`.bash_profile` sourced) |

### 7.3 AI in Terminal

**Inline Assistant**: Press `Ctrl+Enter` in the terminal to get AI help explaining errors, suggesting commands, or troubleshooting.

**AI agents**: In the Agent Panel, agents can run terminal commands as part of their workflow.

### 7.4 Known Terminal Limitation

The `terminal.shell` setting is used **globally** -- for interactive terminals, AI agents, and internal shell operations. If you set a non-Bash shell (Nushell, tmux, fish), AI agents break because Zed internally uses `${terminal.shell} -c 'command'`. A workaround wrapper script is the community fix; feature request exists for separate shell settings.

---

## 8. Editing Model

### 8.1 Multi-Buffer Editing

A truly distinctive feature. **Multibuffers** compose editable excerpts from multiple files into a single tab.

**How it works**:
- Project search results open as a multibuffer
- Diagnostics open as a multibuffer
- Find References open as a multibuffer
- You can edit excerpts directly; changes reflect in all open copies of that file
- Save once (`cmd-s` / `:w`) to save all modified files
- Click divider lines or use `editor: open excerpts` (`alt-enter`) to jump to original file
- Configurable: `double_click_in_multibuffer` can be set to `"open"` to open on double-click

**Multi-cursor with multibuffers**:
- `cmd-d` / `gl` (Vim): Select next match of word under cursor
- `cmd-shift-l` / `g a` (Vim): Select **all** matches across all excerpts
- Edit all matches simultaneously with multiple cursors

### 8.2 Vim Mode

Deep Vim emulation, enabled via welcome screen, `toggle vim mode`, or `"vim_mode": true`.

**Design philosophy**: Replicates Vim where it makes sense; uses Zed-native features (multiple cursors, Tree-sitter) where they're better.

**LSP navigation**:
| Binding | Action |
|---------|--------|
| `g d` | Go to definition |
| `g D` | Go to declaration |
| `g y` | Go to type definition |
| `g A` | Find all references |
| `g I` | Go to implementation |
| `c d` | Rename symbol |
| `] d` / `[ d` | Next/previous diagnostic |

**Tree-sitter motions**:
| Binding | Action |
|---------|--------|
| `] m` / `[ m` | Next/previous method/function |
| `] ]` / `[ [` | Next/previous section/class |
| `] c` / `[ c` | Next/previous git change |

**Text objects**:
| Object | Target |
|--------|--------|
| `a c` / `i c` | Class |
| `a f` / `i f` | Function |
| `i a` | Argument/parameter |
| `a t` / `i t` | HTML/XML tag |
| `i l` | Line |

**Multi-cursor in Vim**:
| Binding | Action |
|---------|--------|
| `g l` | Add next copy of word under cursor |
| `g L` | Add previous copy |
| `g a` | Select all copies |

**Surround** (vim-surround style):
| Operator | Action |
|----------|--------|
| `ys` | Add surround |
| `cs` | Change surround |
| `ds` | Delete surround |

**Ex commands**: `:w`, `:q`, `:wq`, `:vs`, `:sp`, `:tabedit`, `:%s/foo/bar/g`, etc.

**Context scoping**: Keybindings use `Editor && vim_mode == normal`, `vim_mode == visual`, `vim_mode == insert`, `vim_mode == operator`, `vim_mode == waiting`, etc.

### 8.3 Snippets

Reusable code templates with tab stops. Appear in the completion menu.

**Sort order** (`snippet_sort_order`):
| Value | Behavior |
|-------|----------|
| `"top"` | Snippets first |
| `"inline"` | Normal ordering |
| `"bottom"` | After other completions |
| `"none"` | Hidden entirely |

### 8.4 Autocomplete / Code Completions

**Two sources**:

1. **LSP completions**: Variable names, functions, symbols from language servers. Trigger with `ctrl-space`. Configurable `show_completions_on_input` (enable/disable auto-show).
2. **Edit predictions**: Zeta2, Copilot, etc. Accept with `tab`.

**Completion settings**:
- `completions.words`: `"enabled"`, `"fallback"` (word completions when no LSP), or `"disabled"`
- `completions.lsp_insert_mode`: `"replace_suffix"`, `"replace"`, `"insert"`, `"replace_subsequence"`
- `use_autoclose`: Auto-close brackets and quotes
- `use_auto_surround`: Surround selected text when typing brackets or quotes

**Vim-mode insert bindings**:
| Shortcut | Action |
|----------|--------|
| `ctrl-x ctrl-o` | Open completion menu |
| `ctrl-x ctrl-c` | Copilot suggestion |
| `ctrl-x ctrl-a` | Inline AI assistant |
| `ctrl-x ctrl-l` | Code actions menu |

### 8.5 Regex Search & Replace

**Engine**: Rust `regex` crate (different syntax from Vim).

**Syntax differences from Vim**:
| Feature | Vim | Zed |
|---------|-----|-----|
| Capture groups | `\(` `\)` | `(` `)` |
| Literal parens | `(` `)` | `\(` `\)` |
| Backreferences | `\1`, `\0` | `$1`, `$0` |
| Global matching | `/g` suffix | Global by default |
| Case-insensitive | `/i` suffix | `(?i)` prefix or toggle |

**Substitute command**: `:%s/foo/bar/g` -- Zed auto-converts Vim-style parens and backreferences.

**Settings**:
- `search.whole_word`, `search.case_sensitive`, `search.include_ignored`, `search.regex`, `search.center_on_match`
- `vim.use_regex_search`: Vim search uses regex by default
- `vim.use_smartcase_find`: Case-insensitive when target is all lowercase

### 8.6 Code Actions & Diagnostics

**Diagnostics display**:
- Underlined text in editor
- Scrollbar markers (color-coded)
- Inline diagnostics ("error lens" style, configurable with `diagnostics.inline.enabled`)
- Hover tooltips with full diagnostic messages
- Navigation: `editor: GoToDiagnostic` / `GoToPreviousDiagnostic` (F8 / Shift+F8 in base keymap)
- Filtering by severity: `off`, `error`, `warning`, `info`, `hint`, `null` (all)
- Diagnostic counts in project panel (file tree badges) and editor tabs

**Code actions**:
- Lightbulb menu on cursor (`editor: ToggleCodeActions` / `Ctrl+.`)
- Quick fixes: `editor: ConfirmCodeAction`
- Formatting: via LSP, external formatter, code actions, or chained formatters
- Format on save: `off`, `on`, or per-language

### 8.7 Other Editing Features

| Feature | Detail |
|---------|--------|
| **Auto-indent** | Tree-sitter driven (`indents.scm`) |
| **Rainbow brackets** | `colorize_brackets` -- each pair gets distinct color |
| **Bracket pair highlighting** | Current pair highlighted |
| **Breadcrumbs** | Syntax node path below tab bar; LSP document symbols optionally interleaved |
| **Outline view** | Modal (`cmd-shift-o`) or persistent panel (`cmd-shift-b`). Works with singletons and multibuffers |
| **Symbol prefix** | Each outline symbol shows type prefix (`struct`, `fn`, `mod`, `impl`) |
| **Auto-scroll outline** | Follows cursor to current symbol |
| **Folding** | Syntax-level fold points from Tree-sitter |
| **Block comment toggle** | `editor: toggle block comment` (new April 2026) |
| **Line endings** | `line_ending` setting + `.editorconfig` `end_of_line` support (new May 2026) |
| **Markdown editing** | `extend_list_on_newline`, `indent_list_on_tab` (since January 2026) |
| **Markdown preview** | Live preview with Mermaid diagrams, GFM alert callouts (`[!NOTE]` etc.), anchor links, footnotes, GIF support, compound emoji |
| **Selection rotation** | `RotateSelectionsForward` / `RotateSelectionsBackward` actions |
| **Drag-and-drop** | Move/copy files within project panel |
| **File creation** | New files and directories from project panel |

---

## 9. Git Integration

All native, no extensions needed.

### 9.1 Git Panel

**Visible state**:
- Working tree changes (modified, untracked)
- Staging area
- Branch info
- Git remote info
- Diff stats (since March 2026)

**Actions**:
- Stage/unstage files via checkboxes
- Quick commit: type message, hit `cmd-enter` (auto-stages all tracked changed files)
- Expand commit editor with `shift-escape`
- Fetch, push, pull buttons
- Undo commit: "Uncommit" button = `git reset HEAD~1 --soft`

### 9.2 Inline Git Blame

- Shows commit author + timestamp on current line
- Toggle: `editor: toggle git blame inline` (no default binding)
- Default enabled; configurable delay (`delay_ms: 600`)
- Hover opens tooltip with: full commit message (Markdown rendered), commit SHA (clickable permalink -- opens on Git host), author info
- Gutter blame alternative: `editor: toggle git blame` (`cmd-alt-g b` / `alt-g b`)
- Themed with `hint` color, overridable via `experimental.theme_overrides`

### 9.3 Diff View

**Access**: `git: diff` in Command Palette, or `ctrl-g d`.

**Two view styles**:
| Mode | Behavior |
|------|----------|
| **Split** (default) | Side-by-side: original vs modified |
| **Unified** | Inline additions/deletions in single pane |

**Features**:
- **Editable multibuffers**: Diffs are live, editable excerpts; make changes while reviewing
- **Word diff highlighting**: Changed words within lines highlighted (can disable globally or per-language)
- **Diff hunk navigation**: Expand/collapse hunks; keyboard navigation between hunks
- **Staging from diff**: Stage/unstage hunks or files inline

**Hunk shortcuts**:
| Action | macOS | Linux/Windows |
|--------|-------|---------------|
| Expand all hunks | `cmd-"` | `ctrl-"` |
| Toggle selected hunk | `cmd-'` | `ctrl-'` |
| Collapse all hunks | `Escape` | `Escape` |

**Staging shortcuts**:
| Action | macOS | Linux/Windows |
|--------|-------|---------------|
| Stage and next | `cmd-y` | `alt-y` |
| Unstage and next | `cmd-shift-y` | `alt-shift-y` |
| Stage all | `cmd-ctrl-y` | `ctrl-space` |

### 9.4 Branch Management

- **Create branch**: `git: branch`
- **Switch branch**: `git: switch` or `git: checkout branch`
- **Branch picker**: In Git Panel and status bar
- **Delete branch**: Via the branch switcher (confirms before delete; cannot delete current branch)
- **Git Graph View**: Replaced file history; lazy loading, search, resizable columns (new May 2026)

### 9.5 Worktree Support

- Open worktree picker: `git: worktree` (`cmd-ctrl-w` / `alt-ctrl-shift-w`)
- Create linked worktrees from current or default branch
- Switch to, open in new window, or delete existing worktrees
- New worktrees start in detached HEAD; use branch picker to create/checkout a branch

### 9.6 Stash Workflow

| Action | Command |
|--------|---------|
| Stash all | `git: stash all` |
| View stash list | `git: view stash` |
| Apply latest | `git: stash apply` |
| Pop latest | `git: stash pop` |

From stash diff view: `ctrl-space` (apply), `ctrl-shift-space` (pop), `ctrl-shift-backspace` (drop).

### 9.7 Other Git Features

| Feature | Detail |
|---------|--------|
| **AI commit messages** | `git: generate commit message` (`alt-tab` / `alt-l`) |
| **Fetch/Push/Pull** | Buttons in Git Panel or Command Palette |
| **Merge conflict resolution** | Inline buttons: accept incoming, current, or both |
| **Copy Permalink** | Create permanent links to code at specific lines/commits on your Git host |
| **Undo commit** | `git reset HEAD~1 --soft` via "Uncommit" button |
| **Create pull request** | `git: create pull request` command (since March 2026) |
| **Host detection** | Auto-detects GitHub, GitLab, Bitbucket, Gitea, Forgejo, SourceHut; self-hosted instances supported |

---

## 10. Language Support (LSP & Tree-sitter)

### 10.1 LSP Features

Full LSP support across **59 languages** (plus 17 with just Tree-sitter highlighting):

| Feature | Mechanism |
|---------|-----------|
| Code completion | LSP completion request |
| Diagnostics | Push + pull diagnostic variants |
| Go to definition/declaration/type def/implementation | LSP goto request |
| Find references | LSP references request (opens as multibuffer) |
| Rename | LSP rename request (with multibuffer preview) |
| Hover | LSP hover (type info, docs, links) |
| Code actions | LSP code action request (lightbulb menu) |
| Workspace symbols | LSP workspace/symbol (project-wide fuzzy search) |
| Inlay hints | LSP inlayHint (parameter names, inferred types) |
| Formatting | LSP formatting, range formatting, on-type formatting |
| Document symbols | LSP documentSymbol (for breadcrumbs + outline) |
| Code lens | LSP codeLens (new May 2026, disabled by default) |
| Semantic tokens | LSP semanticTokens (layered on Tree-sitter) |

### 10.2 Language Server Management

- **Auto-download**: First time opening a file, Zed downloads the language server
- **Auto-update**: Servers updated automatically
- **Prioritization**: Choose/disable servers per language with `language_servers` (e.g., `["intelephense", "!phpactor", "..."]`)
- **Toolchain discovery**: Python venv, Node.js node_modules, etc.
- **Binary overrides**: Custom paths, args, env vars for server binaries

### 10.3 Tree-sitter Query Files

All language-specific editor behavior is driven by Tree-sitter queries:

| Query File | Purpose |
|-----------|---------|
| `highlights.scm` | Syntax highlighting with fallback captures |
| `brackets.scm` | Bracket matching for rainbow brackets and pair highlighting |
| `outline.scm` | Symbol outline for breadcrumbs and outline modal |
| `indents.scm` | Auto-indentation for complex nested expressions |
| `injections.scm` | Multi-language documents (HTML+JS+CSS, Markdown code blocks) |
| `overrides.scm` | Scope-based setting overrides (word characters, completion triggers) |
| `textobjects.scm` | Vim text objects and Tree-sitter motions |
| `redactions.scm` | Privacy redactions during screen sharing |
| `runnables.scm` | Runnable code detection |

### 10.4 Semantic Token Modes

| Mode | Behavior |
|------|----------|
| `"off"` (default) | Tree-sitter highlighting only |
| `"combined"` | LSP semantic tokens overlaid on Tree-sitter |
| `"full"` | LSP tokens entirely replace Tree-sitter |

Customizable via `semantic_token_rules` with token type/modifier matching, theme style references, fallbacks, color overrides, and font styling.

### 10.5 Per-Language Configuration

All configurable under `languages.<LanguageName>` in `settings.json`:

```json
{
  "languages": {
    "Python": {
      "tab_size": 4,
      "formatter": "language_server",
      "format_on_save": "on",
      "enable_language_server": true
    },
    "JavaScript": {
      "tab_size": 2,
      "formatter": { "external": { "command": "prettier", "arguments": ["--stdin-filepath", "{buffer_path}"] } }
    }
  }
}
```

**Per-language overrides include**: `tab_size`, `hard_tabs`, `preferred_line_length`, `formatter` (LSP, external, code actions, or chained), `format_on_save`, `enable_language_server`, `soft_wrap`, `show_completions_on_input`, `colorize_brackets`, `semantic_tokens`, `inlay_hints`, `edit_predictions_disabled_in`, `word_characters`, `language_servers`, and more.

---

## 11. Remote Development & Dev Containers

### 11.1 SSH Remote Development

**Architecture**:

| Local Machine | Remote Server |
|--------------|---------------|
| UI / GPUI rendering | Source code |
| Tree-sitter parsing | Language servers (LSP) |
| Unsaved buffers | Terminal & tasks |
| Collaboration | AI / LLM features |
| Extensions (propagated) | Extensions (synced from local) |

**Features**:
- Connect via system `ssh` binary; inherits `~/.ssh/config`
- SSH ControlMaster multiplexing for Zed protocol, terminals, tasks
- **Snappy reconnects**: Language servers keep running on connection drops
- Port forwarding: `-L` / `-R` flags (configurable in settings)
- WSL support: open local folders in WSL, or WSL-resident folders
- Direct CLI: `zed ssh://[user@]host[:port]/path`
- Remote servers: macOS, Linux (x86_64/arm64). Local client: macOS, Linux, Windows.

### 11.2 Dev Containers

Support landed as MVP in v0.218 (December 2025). Opens projects inside Docker containers defined by `.devcontainer/devcontainer.json`.

**Current state (as of early 2026)**:
- Prompts when opening project with `.devcontainer/devcontainer.json`
- Uses devcontainer CLI reference implementation
- Reuses SSH remote architecture (spawns remote server inside container via `docker exec`)
- Native implementation replaced Node-based CLI (April 2026)
- Title bar indicator when in container
- MCP servers on remote supported (since January 2026)

**Current limitations**:
- No `forwardPorts` support (only `appPort`)
- No auto-rebuild on config changes
- Hard dependency on Docker in PATH (Podman works if aliased to `docker`)
- Extensions synced between host and container from same manifest

---

## 12. Debugger (DAP)

### 12.1 Debug Adapter Protocol Support

**Supported languages**: Rust, Go, Python, JavaScript/TypeScript, C/C++ (via CodeLLDB), and more via extensions.

**Zero-setup launch**: `debugger: start` (F4) opens contextual list of preconfigured debug tasks.

**Config files**: `.zed/debug.json` (project), `~/.config/zed/debug.json` (global), `.vscode/launch.json` (compatible).

### 12.2 Debugger Capabilities

| Capability | Detail |
|-----------|--------|
| **Launch & Attach** | Both modes supported |
| **Breakpoints** | Conditional, log points, hit counts, enable/disable |
| **Exception breakpoints** | Break on uncaught/caught exceptions |
| **Run to cursor** | Execute until current line |
| **Evaluate expression** | Evaluate selection or typed expression |
| **Inlay value hints** | `inlay_hints.show_value_hints` -- inline variable values during debugging |
| **Multiple sessions** | Debug multiple processes simultaneously |
| **Split pane aware** | Debug line tracked across split panes |
| **Remote debugging** | Supported |

### 12.3 Debugger Actions

`debugger::Start`, `debugger::Continue`, `debugger::StepInto`, `debugger::StepOver`, `debugger::StepOut`, `debugger::Stop`, `debugger::Restart`, `debugger::Rerun`, `debugger::RunToCursor`, `debugger::EvaluateSelectedText`, `debugger::ToggleBreakpoint`, `debugger::ClearAllBreakpoints`, `debugger::Pause`, `debugger::Detach`

### 12.4 Debugger Settings

- `debugger.dock`: `"left"`, `"right"`, `"bottom"`
- `debugger.stepping_granularity`: `"statement"`, `"line"`, `"instruction"`
- `debugger.save_breakpoints`: persist across sessions
- `debugger.timeout`: TCP adapter connection timeout
- `debugger.log_dap_communications`: log DAP messages for development
- `debugger.render_breakpoint_icons`: toggle breakpoint indicators

### 12.5 Debugger Extensions

Extensions can provide DAP servers by implementing: `get_dap_binary`, `dap_request_kind`, `dap_config_to_scenario`, and debug locators.

---

## 13. Unique Features (Nothing Else Has These)

### 13.1 GPUI -- Videogame-Style GPU Rendering

No other text editor renders its entire UI on the GPU with custom shaders per primitive. Every rectangle, shadow, glyph, and icon is drawn by dedicated shader code. This is the only editor that treats rendering like a game engine.

### 13.2 MultiBuffers

The ability to compose editable excerpts from **multiple arbitrary files** into a single virtual document, edit them all simultaneously with multiple cursors, and save them all at once. No other editor has this paradigm. VS Code's search results are not editable in the same way.

### 13.3 CRDT-Based Collaboration (Built In)

While VS Code has Live Share (an extension), and Google Docs uses CRDTs for documents, Zed is the only code editor with **native CRDT-based real-time collaboration** built into the core -- no plugins, no signup (besides a Zed account), no separate service.

### 13.4 ACP -- Agent Client Protocol

An open standard (like LSP but for AI agents) that lets **any** agent work in **any** editor. Zed created it and is actively evangelizing it across JetBrains, Neovim, Emacs, and others. No other editor has proposed or shipped an equivalent open protocol for agent interoperability.

### 13.5 Zeta2 -- Open-Weight Edit Prediction Model

A purpose-built, open-weight model trained via knowledge distillation from Sonnet 4.6, specifically to predict your **next edit** (not next token). Trained on real user edit traces, not static code. Open source (Apache 2.0). No other editor ships a custom-trained, open-weight prediction model.

### 13.6 Built By the Atom/Electron/Tree-sitter Team

The creators of three foundational technologies in modern code editing are building Zed. This institutional knowledge is visible in the deep Tree-sitter integration (syntax-aware selections, text objects, indentation, folding, breadcrumbs, outline), first-class LSP support, and overall architecture.

### 13.7 Dock Panel Concept

A fetchable/dismissible panel (`shift-escape`) that keeps state while hidden. Used for persistent terminal, docs, diagnostics. Unique UX pattern not found in other editors.

### 13.8 Channels (Slack-Like Team Rooms in an Editor)

Persistent team rooms with subchannels, channel notes (shared Markdown), voice chat, and ambient awareness of who's working on what. No other editor has this integrated. VS Code Live Share is session-based, not persistent.

---

## 14. Pricing & Plans

### 14.1 Free Plan -- $0 Forever

- Full editor with all core features
- **2,000 accepted edit predictions/month** (Zeta2 basic version)
- **BYOK**: Full AI features with your own API keys (Anthropic, OpenAI, Google, OpenRouter, etc.)
- **External agents via ACP**: Claude Code, Gemini CLI, Codex, and all ACP-registered agents work on free tier
- **Agent Panel** available with BYOK or external agents
- No hosted LLM models included
- Collaboration included

### 14.2 Pro Plan -- $10/month

- **Unlimited advanced Edit Predictions** (full Zeta2 with all LSP context features)
- **$5 monthly token credit** for Zed-hosted models
- Access to hosted: Claude Sonnet, Claude Opus, GPT-5 (mini/nano), Gemini 2.5 Pro/Flash, DeepSeek V4-Pro/Flash
- **Overage billing**: Additional usage at API list price + 10%, with **$10/month hard cap** (max $20/month total)
- **14-day free trial** with $20 in token credits
- Spend limits configurable in dashboard

### 14.3 Student Plan -- Free for 12 Months

- All Pro features at no cost
- **$10/month in token credits** (double Pro)
- Any enrolled university student worldwide
- Apply at zed.dev/education

### 14.4 Business Plan -- $30/seat/month

- Org-wide AI model policies
- Data governance controls
- Unified spend visibility
- Role-based access controls
- Unlimited edit predictions
- Order forms at 25+ seats; month-to-month available

### 14.5 Pricing History

Late 2025: Changed from prompt-based to **token-based pricing**. Pro dropped from $20/month to **$10/month** (50% reduction). Old "unlimited prompts" replaced with $5 token credits + usage-based billing.

### 14.6 Do You Need Pro?

**Skip Pro if**:
- You use BYOK with your own API keys
- You use Claude Code / Gemini CLI / Codex as external agents
- You already pay for GitHub Copilot ($10/month, fully integrated in Zed)
- You don't need unlimited edit predictions

**Pro is worth it if**:
- You want zero-config, hosted models out of the box
- You rely heavily on edit predictions
- You want the convenience of not managing API keys

---

## 15. Roadmap

### 15.1 In Progress (as of May 2026)

- **Zed for Business** -- centralized billing, admin controls
- **Instant Sharing** -- share links for live viewing/editing
- **Code History as Context** -- see how code evolved
- **Async Collaboration** -- work on branches asynchronously
- **Skills** -- agent leverages custom skills
- **Edit Tool Improvements** -- targeted replace/delete operations

### 15.2 Coming Next

- **Zed on the Web** -- open projects from any device
- **Plan Mode** -- agent plans before making changes
- **Notebooks** -- interactive data visualization (like Jupyter)
- **Hands-Free Coding** -- voice commands
- **UI for Terminal Agents** -- visual interface for agent operations
- **ForwardPorts** -- full port forwarding for dev containers

### 15.3 Zeta2 Roadmap

- **Jumps** -- when LSP reports error from a recent edit, suggest fix at error location
- **DPO (Direct Preference Optimization)** -- train model to do more of what users accept, less of what they dismiss
- **More efficient prompt formats** -- output only a subset of editable region to reduce token generation
- **Continuous improvement** -- next version experiments already running

### 15.4 Remote Development Follow-ups

- Remote extension API adjustments
- Remote-local Ollama for AI assistant
- Per-session port forwarding
- Improved remote-server binary management (eager updates, cleanup, configurable source)

---

## 16. Limitations & Known Issues

### 16.1 Ecosystem & Extensions

- **~800 extensions** (VS Code has 50,000+)
- **No VS Code extension compatibility**
- **No UI extensibility** -- extensions cannot create panels or modify the editor UI
- Extension registry is a Git repo (team acknowledges this doesn't scale)
- Weaker support for niche languages and enterprise tooling

### 16.2 Missing Features (vs VS Code)

- **No drag-tabs-to-new-window** support
- **No custom theme creation** in-editor (can write JSON files manually)
- **No built-in file icon themes** (minimalist by default)
- **No settings sync** between machines (manual config management)
- **No profile system** for different configurations (only AI profiles)

### 16.3 AI Limitations

- **Slash commands don't work in the editor** -- only in Agent Panel text threads (feature request exists but was closed due to age)
- External agents lack: past message editing, thread history resumption, checkpointing (Claude Agent), agent teams, hooks
- `terminal.shell` is global -- breaks AI agents with non-Bash shells
- No cross-file codebase indexing (Cursor-style `@codebase`)
- Multi-file task success rate ~63% (Cursor is ~80%)

### 16.4 Performance Limitations

- On 2M+ LOC TypeScript monorepos, VS Code Insiders beats Zed for LSP-heavy operations (find-references, rename) due to years of tsserver optimization
- mimalloc memory retention -- RAM not immediately released when files closed (can override)

### 16.5 Dev Container Limitations

- No `forwardPorts` (only `appPort`)
- No auto-rebuild on config changes
- Hard Docker dependency (Podman only if aliased)
- Extensions not managed per-container

### 16.6 Platform

- Windows as remote server not supported (local client only)
- Linux wgpu backend is relatively new (Feb 2026)

---

## Quick Reference: Performance Benchmarks

| Metric | Zed | VS Code | Cursor |
|--------|-----|---------|--------|
| Cold start | **180-248ms** | 1.2-2.1s | 2.1-2.4s |
| RAM (idle) | **142-184MB** | 518-730MB | 612-920MB |
| Keystroke latency | **~8ms** | ~18ms | ~22ms |
| 50MB file open | **Instant** | ~3.2s | ~3.5s |
| AI multi-file success | ~63% | ~56% | **~80%** |
| Extensions available | ~800 | **50,000+** | **50,000+** (VS Code compat) |
| Pro price | $10/mo | Free + Copilot $10/mo | $20/mo |

---

## Quick Reference: Key Bindings

| Action | macOS | Linux/Windows | Vim Mode |
|--------|-------|---------------|----------|
| Agent Panel (new thread) | `cmd-alt-j` (sidebar) | `ctrl-alt-j` | `:agent: new thread` |
| Inline Assistant | `Cmd+Enter` | `Ctrl+Enter` | `ctrl-x ctrl-a` |
| Command Palette | `Cmd+Shift+P` | `Ctrl+Shift+P` | `:` |
| Project Search | `Cmd+Shift+F` | `Ctrl+Shift+F` | `g/` |
| File Search | `Cmd+P` | `Ctrl+P` | `:e` or space |
| Theme Selector | `Cmd+K Cmd+T` | `Ctrl+K Ctrl+T` | |
| Keymap Editor | `Cmd+K Cmd+S` | `Ctrl+K Ctrl+S` | |
| Collaboration Panel | `Cmd+Shift+C` | `Ctrl+Shift+C` | |
| Outline Panel | `Cmd+Shift+B` | `Ctrl+Shift+B` | |
| Terminal Toggle | `` Ctrl+` `` | `` Ctrl+` `` | |
| New Terminal | `Ctrl+~` | `Ctrl+~` | |
| Debugger Start | `F4` | `F4` | |
| Git Diff | `Ctrl+G D` | `Ctrl+G D` | |
| Go to Definition | `F12` | `F12` | `g d` |
| Find References | `Shift+F12` | `Shift+F12` | `g A` |
| Rename Symbol | `F2` | `F2` | `c d` |
| Bookmarks | Configurable | Configurable | |
| Dock Fetch/Dismiss | `Shift+Escape` | `Shift+Escape` | |
| Multiple Cursors (all) | `Cmd+Shift+L` | `Ctrl+Shift+L` | `g a` |

---

*End of document. Compiled from zed.dev official documentation, blog posts, GitHub releases, and community comparisons. Last updated: May 9, 2026.*
