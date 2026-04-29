---
name: wisp-agent
description: Run Wisp — a local-first coding agent using Ollama cloud models for code generation, file editing, and bash execution.
---

# Wisp Agent

Wisp is a coding agent that uses Ollama for model inference. It can read/write files, run bash commands, and execute git operations — all through Ollama's cloud model API, no GPU needed.

## When to use
- You want AI coding assistance without sending code to external APIs (OpenAI, Anthropic, Google)
- You have Ollama running with cloud-model access
- You want an open-source alternative to cloud-based coding agents
- You need a coding agent that runs through your existing Ollama setup

## Instructions

1. Ensure Ollama is running: `ollama serve`
2. Run Wisp: `wisp "<your prompt>"`
3. Wisp will think, use tools, and respond iteratively

## Available models (run `ollama ls` to see yours)

Current available models:
- `deepseek-v4-flash:cloud` — Fast, good for code (default)
- `kimi-k2.5:cloud` — Strong general-purpose reasoning
- `kimi-k2.6:cloud` — Latest Kimi, stronger reasoning
- `glm-5.1:cloud` — Good general model
- `minimax-m2.7:cloud` — Lightweight option

Override with: `wisp --model kimi-k2.5:cloud "your prompt"`

## Examples

### Code review
```
wisp "Review the git diff and suggest improvements"
```

### Feature implementation
```
wisp "Add input validation to the login form in src/components/Login.tsx"
```

### Bug fix
```
wisp "Fix the null pointer error in src/handlers/user.go around line 45"
```

### Testing
```
wisp "Add unit tests for the calculate_total function in src/utils/pricing.ts"
```

## Configuration

Wisp reads from `~/.config/wisp/config.json` or environment variables:

| Setting        | Env Var           | Default                |
|----------------|-------------------|------------------------|
| Ollama URL     | WISP_OLLAMA_URL   | http://localhost:11434 |
| Model          | WISP_MODEL        | deepseek-v4-flash:cloud |
| Temperature    | WISP_TEMPERATURE  | 0.2                    |
| Max tokens     | WISP_MAX_TOKENS   | 4096                   |
| Auto approve   | WISP_AUTO_APPROVE | false                  |
