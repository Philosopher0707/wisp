---
name: wisp-ollama
description: Run Wisp with Ollama cloud models for AI-powered coding tasks. No local GPU needed — uses Ollama's cloud model API.
---

# Wisp + Ollama

Use this skill when you want to run AI coding tasks using Wisp with Ollama — through Ollama's cloud model API, no local models needed.

## Prerequisites
- Ollama running: `ollama serve`

## Available models

Run `ollama ls` to see what's available. Common options:
- `deepseek-v4-flash:cloud` — fast code model (default)
- `kimi-k2.5:cloud` — strong general reasoning
- `kimi-k2.6:cloud` — latest Kimi model

## Instructions

1. Ensure Ollama is running
2. Run Wisp: `wisp "your coding task here"`
3. Use a specific model: `wisp --model kimi-k2.5:cloud "task"`

## When to use Warp's built-in agent vs Wisp

| Criteria | Use Warp Oz | Use Wisp |
|----------|-------------|----------|
| Cloud models (GPT/Claude) | ✅ | ❌ |
| Ollama cloud models | ❌ | ✅ |
| Works offline | ❌ | Via local models |
| No per-request API costs | ❌ | ✅ (free with Ollama cloud) |
| Agent toolbelt in Warp | ✅ | With PR |

## Example workflow

```bash
# 1. Start Ollama
ollama serve &

# 2. Run Wisp
cd ~/your-project
wisp "refactor the database layer to use async/await"

# 3. With a different model
wisp --model kimi-k2.5:cloud "review the latest git diff"
```
