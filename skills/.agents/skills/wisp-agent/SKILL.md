---
name: wisp-agent
description: Run Wisp — a local-first coding agent with Ollama cloud models, supporting REPL, sessions, skills, subagents, swarm mode, memory, and Android remote control. Use for code generation, refactoring, debugging, testing, and multi-agent orchestration.
---

# Wisp Agent

Wisp is a local-first coding agent powered by Ollama cloud models. It reads/writes files, runs bash commands, executes git operations, spawns subagents, manages sessions, and supports multi-agent swarms — all through your Ollama instance.

## When to use

- You want AI coding assistance without sending code to external APIs (OpenAI, Anthropic, Google)
- You have Ollama running with cloud-model access
- You need a coding agent that runs through your existing Ollama setup
- You want to use advanced features: sessions, skills, subagents, swarm mode, memory
- You need to delegate tasks to child agents or run multi-agent workflows

## Quick Start

```bash
# Single-shot
wisp "refactor the auth module to use async/await"

# Interactive REPL
wisp repl

# With a skill
wisp --skill diagnose "debug the failing test"

# Continue a session
wisp -S 20260504-120000-abc123 "next task"

# Swarm mode
wisp swarm "analyze codebase and generate a report"
```

## Available Models

Run `wisp models` or `ollama ls` to see yours.

| Model | Description |
|-------|-------------|
| `deepseek-v4-pro:cloud` | Default. Strong reasoning, good for code |
| `deepseek-v4-flash:cloud` | Faster, lighter version |
| `kimi-k2.6:cloud` | Latest Kimi, strongest reasoning |
| `kimi-k2.5:cloud` | Strong general-purpose |
| `glm-5.1:cloud` | Good general model |
| `gemini-3-flash-preview:cloud` | Fast, lightweight |
| `minimax-m2.7:cloud` | Lightweight option |

Override: `wisp --model kimi-k2.6:cloud "your prompt"`

## CLI Subcommands

| Subcommand | Description |
|------------|-------------|
| `wisp run <prompt>` | Single-shot agent |
| `wisp repl` | Interactive REPL |
| `wisp tui` | Full-screen terminal app |
| `wisp server` | Start cloud server for remote clients |
| `wisp session list` | List saved sessions |
| `wisp session show <id>` | Show session details |
| `wisp session delete <id>` | Delete a session |
| `wisp session trim <id> [n]` | Trim to last N exchanges |
| `wisp session compact <id> [n]` | Summarize old messages |
| `wisp skills` | List discovered skills |
| `wisp config [--set k=v]` | View/set configuration |
| `wisp check` | Verify Ollama connectivity |
| `wisp models` | List available models |
| `wisp swarm 'goal'` | Spawn multi-agent swarm |
| `wisp agents list` | List available agent roles |
| `wisp agents status` | Show running swarm status |

## REPL Slash Commands

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
| `/session` | Show session info |
| `/spawn <role> <task>` | Spawn subagent |
| `/swarm <goal>` | Spawn swarm |
| `/agents` | List agent roles |
| `/exit` | Quit REPL |

## Tools Available to the Agent

### File & Code
| Tool | Purpose |
|------|---------|
| `read_file` | Read file contents (with offset/limit) |
| `write_file` | Create or overwrite a file |
| `edit_file` | Targeted text replacement |
| `edit_file_multi` | Multiple surgical edits at once |
| `list_files` | Explore directory structure |
| `search_symbols` | Search code for functions, classes, structs |
| `search_codebase` | Semantic search over codebase |

### Execution & Shell
| Tool | Purpose |
|------|---------|
| `run_bash` | Execute shell commands (dangerous commands blocked) |
| `web_fetch` | Fetch content from URLs |
| `web_search` | Search the web |

### Git
| Tool | Purpose |
|------|---------|
| `git_status` | Show git status |
| `git_diff` | Show uncommitted changes |
| `git_branch` | List/create/switch branches |
| `git_commit` | Stage and commit |
| `git_push` | Push to remote |
| `gh_pr_create` | Create GitHub PR |

### Memory & Sessions
| Tool | Purpose |
|------|---------|
| `remember` | Store a fact in cross-session memory |
| `recall` | Search memory and past summaries |

### Multi-Agent
| Tool | Purpose |
|------|---------|
| `spawn_subagent` | Delegate scoped tasks to child agents |
| `plan_task` | Create structured plan with subtasks |
| `mark_step_done` | Mark plan step complete |
| `update_plan` | Update plan step status |

### LSP & Diagnostics
| Tool | Purpose |
|------|---------|
| `lsp_diagnostics` | Run language server diagnostics |
| `lsp_definition` | Go to definition |
| `lsp_references` | Find all references |
| `lsp_hover` | Get hover info |
| `lsp_symbols` | List file symbols |

### VS Code Integration
| Tool | Purpose |
|------|---------|
| `vscode_open_file` | Open file in VS Code |
| `vscode_run_command` | Run VS Code command |
| `vscode_show_message` | Show notification |
| `vscode_get_editor_state` | Get editor state |

### Debugging
| Tool | Purpose |
|------|---------|
| `diagnose` | Analyze errors and suggest fixes |

## Configuration

Settings resolved: **env vars > config file > defaults**

```bash
# ~/.config/wisp/config.json
{
  "model": "deepseek-v4-pro:cloud",
  "provider": "ollama",
  "ollama_url": "http://localhost:11434",
  "temperature": 0.2,
  "max_tokens": 8192,
  "max_iterations": 30,
  "max_reflections": 3,
  "auto_approve": true,
  "auto_compact": true,
  "compact_threshold_tokens": 75,
  "compact_keep_recent": 10,
  "max_context_tokens": 256000,
  "show_thinking": true,
  "show_tool_output": true,
  "skill_dirs": [".agents/skills", ".warp/skills", ".claude/skills"],
  "context_files": ["CLAUDE.md", "AGENTS.md", ".wisp/rules.md", "GEMINI.md"]
}
```

Or use env vars: `WISP_MODEL`, `WISP_PROVIDER`, `WISP_OLLAMA_URL`, `WISP_AUTO_APPROVE`, `WISP_AUTO_COMPACT`, etc.

## Examples

### Code review
```bash
wisp "Review the git diff and suggest improvements"
```

### Feature implementation
```bash
wisp "Add input validation to the login form in src/components/Login.tsx"
```

### Bug fix with diagnose skill
```bash
wisp --skill diagnose "Fix the null pointer error in src/handlers/user.go around line 45"
```

### Testing
```bash
wisp "Add unit tests for the calculate_total function in src/utils/pricing.ts"
```

### Multi-agent swarm
```bash
wisp swarm "Analyze this codebase for security issues, performance bottlenecks, and missing tests"
```

### With subagent delegation
```bash
wisp "Refactor the auth module. Spawn a subagent to handle the JWT token logic."
```

## Session Management

Sessions auto-save and can be continued:
- Auto-compact when too long (old messages summarized)
- Cross-session memory with `remember`/`recall`
- List: `wisp session list`
- Continue: `wisp -S <id> "next task"`

## Android Remote Control

Control Wisp from your phone:
1. Deploy server: `wisp server` (or Docker)
2. Install Android APK
3. Connect via WebSocket

See [ANDROID_USAGE_GUIDE.md](ANDROID_USAGE_GUIDE.md) and [CLOUD_DEPLOYMENT_GUIDE.md](CLOUD_DEPLOYMENT_GUIDE.md)
