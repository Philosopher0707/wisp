# Wisp 🤖

**A local-first coding agent powered by Ollama — with an experimental Android app for remote control.**

> **What is Wisp?** A single Python CLI that runs an AI coding agent on your
> machine (or any server with Ollama). It reads your codebase, edits files, runs
> tests, and remembers context across sessions. The TUI and WebSocket server are
> optional front-ends — the core is the CLI agent. The Android app is
> experimental and not actively maintained.

```
wisp "refactor the auth module to use async/await"
```

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Android](https://img.shields.io/badge/Android-API%2026%2B-brightgreen)](android/)

---

## Why Wisp?

Warp (github.com/warpdotdev/warp) open-sourced its client under AGPL, but its agent orchestration platform **Oz** remains a commercial cloud product using GPT/Claude. Wisp fills the gap: a **local/cloud-agnostic** agent that works with whatever models your Ollama instance provides — no per-request API fees, no vendor lock-in.

| Feature | Warp Oz | Wisp |
|---------|---------|------|
| Model inference | GPT models (paid API) | Ollama (cloud or local) |
| Code generation | ✅ | ✅ |
| File read/write/edit | ✅ | ✅ |
| Bash execution | ✅ | ✅ |
| Skill support | ✅ | ✅ (same format) |
| **Android remote control** | ❌ | 🧪 experimental |
| **Session compaction** | ❌ | ✅ |
| **Cross-session memory** | ❌ | ✅ |
| Data leaves your machine | To OpenAI/Anthropic | To your Ollama host |
| Per-request cost | Credits / subscription | Free |
| Open source | Server is closed | ✅ MIT |

---

## What's New

### 🎉 Session Compaction
Sessions auto-compact when they grow too long — old messages get summarized into a structured memory block, preserving recent context. Keeps conversations flowing without hitting context limits.

### 🧠 Active Memory (`remember` + `recall`)
- **`remember`** — Store facts across sessions (preferences, decisions, conventions)
- **`recall`** — Actively search memory and past session summaries for relevant context

Memory facts include timestamps in the system prompt so the model knows recency.
Important facts survive ~30 days longer than normal facts during LRU eviction,
but are no longer immortal — stale important facts can still be evicted.

### 📱 Native Android App
Control your coding agent from anywhere. Features:
- **Real-time chat** with thinking stream
- **Tool approve/deny** cards
- **File browser** — view workspace contents
- **Auto-reconnect** with exponential backoff
- **Markdown rendering** for assistant responses
- **Connection status** indicator

---

## Quick Start

### CLI (Local)

```bash
# Install
cd wisp && pip install -e .

# Check Ollama
ollama ls

# Run
wisp "list all files and describe the project structure"

# Interactive REPL
wisp repl

# Continue a session
wisp -S 20260504-120000-abc123 "next task"
```

### Cloud Server + Android

```bash
# Deploy to VPS (Hetzner/DigitalOcean/etc)
git clone https://github.com/your-username/wisp.git
cd wisp
export WISP_API_KEY="your-secure-key"
docker-compose up -d

# Pull a model
docker exec -it wisp-ollama ollama pull deepseek-v4-flash:cloud
```

Then install the Android APK and connect to `wss://your-domain.com`.

**Full guides:**
- [Cloud Deployment Guide](CLOUD_DEPLOYMENT_GUIDE.md) — VPS setup, TLS, Docker
- [Android Usage Guide](ANDROID_USAGE_GUIDE.md) — Build, install, configure

---

## Architecture

```
┌─────────────────┐      WebSocket/HTTPS      ┌─────────────────────────────┐
│   Android App   │  ◄──────────────────────►  │   Cloud VPS                 │
│  (Jetpack       │                           │  ┌─────────────────────┐    │
│   Compose)      │                           │  │  Wisp Server        │    │
│                 │                           │  │  (FastAPI + Agent)  │    │
│  • Chat UI      │                           │  └──────────┬──────────┘    │
│  • File tree    │                           │             │               │
│  • Tool         │                           │  ┌──────────▼──────────┐    │
│    approvals    │                           │  │  Ollama / External  │    │
│  • Settings     │                           │  │  LLM API            │    │
└─────────────────┘                           │  └─────────────────────┘    │
                                              └─────────────────────────────┘
```

---

## Commands

| Command | Description |
|---------|-------------|
| `wisp "prompt"` | Run with a prompt |
| `wisp repl` | Interactive REPL mode |
| `wisp -S <id> "prompt"` | Continue session |
| `wisp --model <name> "prompt"` | Use specific model |
| `wisp --skill <name> "prompt"` | Load a skill |
| `wisp compact <id>` | Compact session history |
| `wisp session list` | List saved sessions |
| `wisp session show <id>` | Show session details |
| `wisp memory list` | View remembered facts |
| `wisp check` | Verify Ollama connectivity |
| `wisp models` | List available models |
| `wisp server` | Start cloud server |

### REPL Slash Commands

| Command | Description |
|---------|-------------|
| `/help` | Show commands |
| `/clear` | Clear conversation |
| `/model <name>` | Switch model |
| `/skill <name>` | Load skill |
| `/compact` | Compact session now |
| `/tokens` | Show context usage |
| `/approve` | Toggle auto-approve |
| `/bash <cmd>` | Run shell command |
| `/save` | Force-save session |
| `/exit` | Quit REPL |

---

## Tools Available to the Agent

| Tool | Purpose |
|------|---------|
| `read_file` | Read file contents (with offset/limit) |
| `write_file` | Create or overwrite a file |
| `edit_file` | Targeted text replacement (surgical edits) |
| `edit_file_multi` | Multiple precise edits in a single call |
| `run_bash` | Execute shell commands (dangerous commands blocked) |
| `list_files` | Explore directory structure |
| `web_fetch` | Fetch content from URLs |
| `web_search` | Search the web for current information |
| `search_symbols` | Search code by regex for functions, classes, structs |
| `search_codebase` | Semantic vector search over the codebase |
| `remember` | Store a fact in cross-session memory |
| `recall` | Search memory and past summaries |
| `spawn` | Launch a subagent with a contract for scoped work |
| `fanout` | Delegate a task to multiple subagents in parallel |
| `plan_task` | Create a structured plan with subtasks |
| `run_tests` | Run tests for changed files or full suite |
| `diagnose` | Diagnose errors from test output or tracebacks |
| `git_status` | Show git status |
| `git_diff` | Show uncommitted changes |
| `git_commit` | Stage files and commit |
| `gh_pr_create` | Create a GitHub pull request |
| `lsp_diagnostics` | Run language server diagnostics on a file |
| `lsp_definition` | Go to definition of a symbol |
| `lsp_references` | Find all references to a symbol |

---

## Configuration

Settings are resolved: **env vars > config file > defaults**

```bash
# ~/.config/wisp/config.json
{
  "model": "deepseek-v4-flash:cloud",
  "auto_approve": false,
  "show_thinking": true,
  "auto_compact": true,
  "compact_threshold_msgs": 40,
  "compact_threshold_tokens": 75,
  "max_context_tokens": 256000
}
```

Or use env vars: `WISP_MODEL`, `WISP_AUTO_APPROVE`, `WISP_AUTO_COMPACT`, etc.

### Terminal Output Modes

Wisp supports multiple output styles for different terminals, screen readers, and accessibility needs:

| Mode | Env / How | What it looks like |
|------|-----------|-------------------|
| **Unicode** *(default)* | none / `WISP_OUTPUT_MODE=unicode` | Fancy boxes `┌─┐│`, colored icons `✓` `✗`, emojis `🧠` |
| **ASCII** | `WISP_OUTPUT_MODE=ascii` or `TERM=dumb` | Simple ASCII `+--+|`, no emojis |
| **Accessible** | `WISP_ACCESSIBLE=1` or `WISP_OUTPUT_MODE=accessible` | `[PASS]` / `[FAIL]` labels, full content shown (nothing collapsed), clear borders `[-]` |
| **Minimal** | `WISP_OUTPUT_MODE=minimal` | No borders at all. Plain text. |
| **High-contrast** | `WISP_HIGH_CONTRAST=1` | Colorblind-safe palette (blue instead of green for success) |

Set at runtime:
```bash
WISP_OUTPUT_MODE=accessible wisp repl    # screen-reader friendly
WISP_HIGH_CONTRAST=1 wisp repl           # colorblind-safe colors
NO_COLOR=1 wisp repl                    # no colors (implies ascii mode)
```

**Key features:**
- **Display-width aware**: CJK characters and emoji are measured correctly so wrapping, box alignment, and dot-filling work without tearing (uses `wcwidth`).
- **Automatic detection**: If `NO_COLOR` is set or stdout is not a TTY, Wisp falls back to ASCII mode automatically.
- **Accessible**: Thinking content is never collapsed in accessible mode — everything is rendered. Status icons become `[PASS]` / `[FAIL]` text instead of `✓` / `✗`.
- **Unicode width-safe**: Middle-dot padding in tool result headers uses display-width calculations, so lines align perfectly even with emoji.

---

## Android App

### Build

```bash
cd android
./gradlew assembleDebug
# APK: app/build/outputs/apk/debug/app-debug.apk
```

### Features
- **Jetpack Compose** UI with Material 3
- **WebSocket** real-time connection to server
- **Auto-reconnect** with exponential backoff (max 30s, 10 attempts)
- **Tool approval/denial** cards
- **Markdown rendering** for assistant messages
- **File browser** — navigate workspace, view file contents
- **EncryptedSharedPreferences** for API key (AES256-GCM, keyed from Android Keystore)
- **DataStore** for non-sensitive settings (server URL, model)
- **Connection status** badge in top bar

### Configure

| Setting | Example |
|---------|---------|
| Server URL | `wss://wisp.yourdomain.com` |
| API Key | `wisp-a1b2c3d4...` |
| Model | `deepseek-v4-flash:cloud` |

---

## CLI UX

The CLI transport (`CLITransportV2`) renders agent activity as a **live dashboard** rather than a plain log stream:

- **Phase bar** — Shows `understand → plan → execute → verify` phases, highlighting the current one
- **Tool spinners** — Inline `⠋` spinner with live elapsed time during tool execution; replaced by `✓`/`✗` on completion
- **File change ticker** — Accumulated changed files shown after each turn
- **Turn stats** — `Turn 3 · 5 tools (4 ok, 1 failed) · 2 files · 12.3s` summary line
- **Thinking collapsed** — Thinking output shown as a compact summary line (`🧠 Thinking... (12 lines — /thinking to expand)`) by default; use `--show-thinking` or `/thinking` to expand

All rendering is output-mode-aware (unicode, ascii, accessible, minimal) and display-width-aware (CJK, emoji).

## Project Structure

```
wisp/
├── wisp/                    # Core agent
│   ├── __main__.py          # CLI entry point
│   ├── agent.py             # Deprecated compat shim
│   ├── core/                # Event-driven stateless engine
│   │   ├── engine.py        # WispAgentCore (async turn() loop)
│   │   ├── events.py        # AgentEvent + 12 event types
│   │   ├── runtime.py       # AgentRuntime (session mgmt + locks)
│   │   └── ...
│   ├── transport/           # I/O layer (Transport ABC)
│   │   ├── base.py          # Transport abstract base class
│   │   ├── cli_v2.py        # CLI transport (live dashboard)
│   │   ├── server.py        # WebSocket server transport
│   │   ├── headless.py      # Headless transport (CI/API)
│   │   ├── tui.py           # Textual TUI transport
│   │   ├── renderer.py      # Terminal rendering utilities
│   │   ├── progress.py      # ProgressTracker (phase detection)
│   │   ├── spinner.py       # Terminal inline spinner
│   │   └── ...
│   ├── tools/               # Tool schemas + implementations
│   │   ├── registry.py      # TOOL_SCHEMAS + TOOL_IMPLS
│   │   └── ...
│   ├── multi_agent/         # Subagent orchestration
│   ├── infra/               # Security, telemetry, extensions
│   ├── config.py            # Settings schema + resolution
│   ├── commands.py          # REPL slash commands
│   └── ...
├── android/                 # Android app
│   └── app/src/main/java/   # Jetpack Compose UI
├── tests/                   # 35+ test files
├── skills/                  # Warp-compatible skill files
├── docker-compose.yml       # One-command cloud deploy
├── CLOUD_DEPLOYMENT_GUIDE.md
└── ANDROID_USAGE_GUIDE.md
```

---

## Security

Wisp has undergone a comprehensive security audit (52 findings, 4 severity levels). Production deployments benefit from a defense-in-depth model across transport, API, multi-agent, and infrastructure layers.

### Hardening Summary

| Layer | Control |
|-------|---------|
| **Transport** | WebSocket message size capped (256 KiB text / 10 MB images); all interactive transports default to `auto_approve=False` |
| **API** | Rate limiting on all state-changing routes via SQLite-backed per-IP tracking (30 req / 60 s) |
| **API** | Security headers: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Content-Security-Policy`, `Referrer-Policy`; opt-in HSTS via `WISP_ENABLE_HSTS=true` |
| **Auth** | API key rotation with persisted grace period (default 24h, stored in `~/.config/wisp/auth_keys.json` with `600` permissions) |
| **Auth** | Audit trail: append-only hash-chained log with automatic PII redaction at `~/.config/wisp/audit.jsonl` |
| **Routes** | Input validation: git refs allow-listed, hook names regex-restricted, plugin paths workspace-bound, workspace root bounded by `WISP_ALLOWED_WORKSPACE_ROOTS` |
| **Routes** | Error sanitization: raw `str(e)` replaced with generic messages on all production routes |
| **LLM Provider** | Ollama URL validated against `WISP_ALLOWED_OLLAMA_HOSTS`; cloud metadata endpoints (169.254.169.254) blocked in production mode via `WISP_PRODUCTION_MODE=true` |
| **Headless API** | `/api/prompt` defaults `auto_approve=False`; operators may set `WISP_HEADLESS_AUTO_APPROVE=true` for backward-compatible CI integrations |
| **Multi-agent** | Subagent recursion capped at `MAX_SUBAGENT_DEPTH=2`; `auto_approve=False` by default |
| **Schema** | Tool arguments pre-validated against JSON schemas before execution; malicious regex patterns gracefully handled |
| **Subagent** | Sensitive tool arguments (api_key, token, password, etc.) redacted before approval requests reach the client |

### Configuration

```bash
# Production-hardened environment
export WISP_API_KEY="sk-change-me-here"    # Required in production; set before start
export WISP_PRODUCTION_MODE="true"           # Blocks internal IPs and metadata services
export WISP_ALLOWED_OLLAMA_HOSTS="localhost,127.0.0.1,my-llm.internal"
export WISP_ALLOWED_WORKSPACE_ROOTS="/var/wisp-workspaces"  # Prevents workspace escape
export WISP_ENABLE_HSTS="true"               # Enforce HTTPS via strict-transport-security
export WISP_WORKSPACE_MUTABLE="true"         # Set to "false" to lock workspace path
```

### Security Audit Report

Full audit report: [SECURITY_AUDIT_2025-05-21.md](SECURITY_AUDIT_2025-05-21.md)

### Legacy Controls

- 🔒 **Dangerous commands blocked** — `rm -rf /`, `mkfs`, `eval`, `bash -c`, encoded payloads, etc. blocked at API + agent + sandbox layers
- 🔒 **Path sandboxing** — File access restricted to `WISP_WORKSPACE`; symlinks that escape the workspace are rejected
- 🔒 **Bash timeout** — Commands killed after 60 seconds
- 🔒 **CORS restricted** — Same-origin by default, configurable via `WISP_CORS_ORIGINS`
- 🔒 **TLS recommended** — Use `wss://` in production (Let's Encrypt / Cloudflare)
- 🔒 **Docker sandbox recommended** — `NoopSandbox` (host execution) is the fallback when Docker is unavailable

---

## SDK Architecture

Wisp is now built as a layered SDK:

```
┌─────────────────────────────────────────┐
│  Transports (I/O layer)                 │
│  - CLITransportV2   → live dashboard    │
│  - ServerTransport  → WebSocket         │
│  - HeadlessTransport → CI/API           │
│  - TUITransport     → Textual TUI       │
├─────────────────────────────────────────┤
│  Core (pure logic, zero I/O)            │
│  - WispAgentCore     → event-driven     │
│  - AgentEvent        → structured events│
│  - AgentRuntime      → session mgmt     │
├─────────────────────────────────────────┤
│  CLI Dashboard (rendering layer)        │
│  - ProgressTracker   → phase detection  │
│  - Spinner           → inline progress  │
│  - Renderer          → terminal output  │
├─────────────────────────────────────────┤
│  Config & Tools                         │
│  - WispConfig, TOOL_SCHEMAS, etc.       │
└─────────────────────────────────────────┘
```

### High-level API (sync)

```python
from wisp import Wisp

agent = Wisp(model="llama3.2", workspace=".")
for event in agent.run("refactor auth.py"):
    print(f"[{event.type}] {event.text}")
```

### Low-level API (async)

```python
from wisp import WispAgentCore, CLITransport

core = WispAgentCore()
transport = CLITransport(core)
transport.repl()
```

### Custom transport

```python
from wisp import WispAgentCore

core = WispAgentCore()
async for event in core.run("prompt"):
    # Handle events however you want
    print(event.to_dict())
```

See `examples/` for more: `sdk_basic.py`, `custom_transport.py`, `webhook_server.py`.

## License

MIT — free for any use, including commercial.

---

*Built with Python, FastAPI, Jetpack Compose, and Ollama.*
