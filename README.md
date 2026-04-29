# Wisp

**A local-first coding agent powered by Ollama — Warp-compatible, fully open-source.**

Wisp provides an open-source alternative to Warp's Oz cloud agent. It uses Ollama for model inference, supports Warp's Skill format, and integrates with Warp's "bring your own agent" ecosystem.

```
wisp "refactor the auth module to use async/await"
```

## Why Wisp?

Warp (github.com/warpdotdev/warp) open-sourced its client under AGPL, but its agent orchestration platform **Oz** remains a commercial cloud product using GPT/Claude. Wisp fills the gap: a local/cloud-agnostic agent that works with whatever models your Ollama instance provides — no per-request API fees, no vendor lock-in.

| Feature | Warp Oz | Wisp |
|---------|---------|------|
| Model inference | GPT models (paid API) | Ollama (cloud or local) |
| Code generation | ✅ | ✅ |
| File read/write/edit | ✅ | ✅ |
| Bash execution | ✅ | ✅ |
| Skill support | ✅ | ✅ (same format) |
| Data leaves your machine | To OpenAI/Anthropic | To your Ollama host |
| Per-request cost | Credits / subscription | Free (Ollama cloud or local) |
| Open source | Server is closed | ✅ MIT |

## Quick Start

```bash
# Install
cd wisp && pip install -e .

# Check Ollama
ollama ls   # should show models like deepseek-v4-flash:cloud

# Run
wisp "list all files and describe the project structure"

# With a specific model
wisp --model kimi-k2.5:cloud "add error handling to main.py"
```

## Commands

| Command | Description |
|---------|-------------|
| `wisp "prompt"` | Run with a prompt |
| `wisp run "prompt"` | Same, explicit |
| `wisp --model <name> "prompt"` | Use a specific model |
| `wisp --skill <name> "prompt"` | Load a skill |
| `wisp check` | Verify Ollama is running |
| `wisp models` | List available Ollama models |
| `wisp skills` | List discovered skills |
| `wisp config --set model=<name>` | Change default model |

## Available Models

Run `ollama ls` to see what's available on your system. Examples:
- `deepseek-v4-flash:cloud` — Fast, good for code (default)
- `kimi-k2.5:cloud` — Strong reasoning
- `kimi-k2.6:cloud` — Latest Kimi
- `glm-5.1:cloud` — General purpose
- `minimax-m2.7:cloud` — Lightweight

## Warp Integration

See [WARP_INTEGRATION.md](./WARP_INTEGRATION.md) for the full guide.

**Three tiers of integration:**
1. **Works now:** Run `wisp` in any Warp terminal tab
2. **Shared skills:** Same `.agents/skills/` and `.warp/skills/` directories
3. **Full toolbelt:** Submit a small PR to Warp to add `wisp` to their agent detection list

## Architecture

```
wisp/                    pip install -e .
├── wisp/
│   ├── __main__.py      CLI entry point
│   ├── config.py        ~/.config/wisp/config.json + env vars
│   ├── ollama_client.py Ollama API wrapper
│   ├── tools.py         5 tools: read/write/edit file, bash, list dirs
│   ├── skills.py        Warp-compatible SKILL.md discovery
│   └── agent.py         Plan → Act → Observe loop with tool calling
├── warp-integration/    Warp-specific integration files
│   ├── wisp-cli-agent.patch  PR to add Wisp to Warp's detection
│   └── wisp.tab.toml    Warp Tab Config
└── skills/              Shared skill files
```

## License

MIT — free for any use, including commercial.
