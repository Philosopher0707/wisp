# Wisp 🤖

**A local-first coding agent powered by Ollama — now with a native Android app for remote control.**

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
| **Android remote control** | ❌ | ✅ |
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
| `run_bash` | Execute shell commands (dangerous commands blocked) |
| `list_files` | Explore directory structure |
| `web_fetch` | Fetch content from URLs |
| `search_symbols` | Search code for functions, classes, structs |
| `remember` | Store a fact in cross-session memory |
| `recall` | Search memory and past summaries |
| `spawn_subagent` | Delegate scoped tasks to child agents |
| `git_status` | Show git status |
| `git_diff` | Show uncommitted changes |

---

## Configuration

Settings are resolved: **env vars > config file > defaults**

```bash
# ~/.config/wisp/config.json
{
  "model": "deepseek-v4-flash:cloud",
  "auto_approve": true,
  "show_thinking": true,
  "auto_compact": true,
  "compact_threshold_msgs": 40,
  "compact_threshold_tokens": 75,
  "max_context_tokens": 256000
}
```

Or use env vars: `WISP_MODEL`, `WISP_AUTO_APPROVE`, `WISP_AUTO_COMPACT`, etc.

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
- **DataStore** encrypted preferences for settings
- **Connection status** badge in top bar

### Configure

| Setting | Example |
|---------|---------|
| Server URL | `wss://wisp.yourdomain.com` |
| API Key | `wisp-a1b2c3d4...` |
| Model | `deepseek-v4-flash:cloud` |

---

## Project Structure

```
wisp/
├── wisp/                    # Core agent
│   ├── __main__.py          # CLI entry point
│   ├── agent.py             # Plan → Act → Observe loop
│   ├── server.py            # FastAPI + WebSocket cloud server
│   ├── session.py           # Session persistence + compaction
│   ├── memory.py            # Cross-session memory (remember/recall)
│   ├── agent_memory.py      # Session summary storage
│   ├── summarizer.py        # Extractive summarization
│   ├── tools.py             # 15 tools for the agent
│   ├── ollama_client.py     # Ollama API wrapper
│   ├── config.py            # Settings schema + resolution
│   ├── commands.py          # REPL slash commands
│   └── ...
├── android/                 # Android app
│   └── app/src/main/java/   # Jetpack Compose UI
├── tests/                   # 30+ test files
├── skills/                  # Warp-compatible skill files
├── docker-compose.yml       # One-command cloud deploy
├── CLOUD_DEPLOYMENT_GUIDE.md
└── ANDROID_USAGE_GUIDE.md
```

---

## Security

- 🔒 **Dangerous commands blocked** — `rm -rf /`, `mkfs`, `eval`, `bash -c`, encoded payloads, etc. blocked at API + agent + sandbox layers
- 🔒 **Path sandboxing** — File access restricted to `WISP_WORKSPACE`; symlinks that escape the workspace are rejected
- 🔒 **Bash timeout** — Commands killed after 60 seconds
- 🔒 **CORS restricted** — Same-origin by default, configurable via `WISP_CORS_ORIGINS`
- 🔒 **API key required** — Pre-shared key for all client connections
- 🔒 **Headless mode hardened** — `/api/prompt` defaults to `auto_edit` permission mode and does NOT auto-approve destructive tools. Set `WISP_HEADLESS_AUTO_APPROVE=1` only in isolated CI environments
- 🔒 **TLS recommended** — Use `wss://` in production (Let's Encrypt / Cloudflare)
- 🔒 **Docker sandbox recommended** — `NoopSandbox` (host execution) is the fallback when Docker is unavailable; it now warns and still blocks dangerous commands

---

## SDK Architecture

Wisp is now built as a layered SDK:

```
┌─────────────────────────────────────────┐
│  Transports (I/O layer)                 │
│  - CLITransport      → terminal         │
│  - ServerTransport   → WebSocket        │
├─────────────────────────────────────────┤
│  Core (pure logic, zero I/O)            │
│  - WispAgentCore     → event-driven     │
│  - AgentEvent        → structured events│
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
