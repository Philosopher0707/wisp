# Wisp + Warp Integration Guide

This document explains how Wisp integrates with [Warp](https://warp.dev), the agentic development environment. There are **three tiers** of integration, from simple to fully immersive.

---

## Tier 1: Run Wisp in Warp (Works Now)

**What you get:** Wisp runs as a normal CLI command in Warp's terminal. You type prompts, Wisp responds. It works the same as in any terminal.

**How:**
```bash
# In any Warp terminal tab:
wisp "add error handling to main.py"
wisp --model qwen2.5-coder:7b "refactor the auth module"
wisp --skill code-review "review the latest changes"
```

**Limitation:** Warp's agent toolbelt (rich input editor, code review panel, agent notifications) won't activate because Warp doesn't recognize `wisp` as a known agent binary.

**Verdict:** ✅ Works. Good for getting started.

---

## Tier 2: Skill Compatibility (Works Now)

**What you get:** Wisp and Warp share the same skill format. Skills you write for Warp's built-in agent also work with Wisp, and vice versa.

**How it works:**
```
.your-project/
├── .agents/
│   └── skills/
│       └── code-review/
│           └── SKILL.md     ← Warp AND Wisp both read this
```

Wisp discovers skills from the same directories Warp uses:

| Directory | Wisp | Warp |
|-----------|------|------|
| `.agents/skills/` | ✅ | ✅ |
| `.warp/skills/` | ✅ | ✅ |
| `.claude/skills/` | ✅ | ✅ |
| `.codex/skills/` | ✅ | ✅ |
| `.gemini/skills/` | ✅ | ✅ |
| `~/.agents/skills/` | ✅ | ✅ |

```bash
# Use a skill with Wisp:
wisp --skill code-review "review the git diff"

# Warp's built-in agent + Wisp, same skills:
# /code-review (in Warp Oz)
# wisp --skill code-review (in Wisp)
```

**Verdict:** ✅ Works now. Skills are the shared language.

---

## Tier 3: Full Warp Agent Toolbelt (Requires PR)

**What you get:** When you run `wisp` in Warp, the agent toolbelt activates — rich input editor (`Ctrl+G`), code review panel, vertical tab metadata, agent icon, and session sharing.

**How to enable:**

1. **Submit a PR** to Warp's open-source repo adding Wisp to the `CLIAgent` enum
2. **Or** use a workaround: alias `wisp` to `pi` (Warp's Pi agent) — hacky but works

### The PR changes (one file)

The detection code is in `app/src/terminal/cli_agent.rs`. The patch adds a `Wisp` variant:

```rust
/// Add to the CLIAgent enum:
Wisp,

/// Add to command_prefix():
CLIAgent::Wisp => "wisp",

/// Add to display_name():
CLIAgent::Wisp => "Wisp",

/// Add to icon():
CLIAgent::Wisp => Some(Icon::WispLogo),

/// Add brand_color() — Wisp blue:
const WISP_BLUE: ColorU = ColorU { r: 99, g: 102, b: 241, a: 255 };
CLIAgent::Wisp => Some(WISP_BLUE),

/// Add to supported_skill_providers():
CLIAgent::Wisp => &[SkillProvider::Agents, SkillProvider::Claude],
```

A full patch file is included at `warp-integration/wisp-cli-agent.patch`.

### Workaround (no PR needed)

If you don't want to wait for the PR, alias Wisp as a known agent:

```bash
# Add to ~/.zshrc:
alias pi='wisp'

# Or create a symlink:
ln -s $(which wisp) /usr/local/bin/pi
```

Now Warp detects `pi` and activates the toolbelt. Wisp runs underneath.

**Verdict:** 🚧 Requires a small PR to Warp's codebase. Tracked as [warpdotdev/Warp#8579](https://github.com/warpdotdev/warp/issues/8579).

---

## Tier 4: Tab Config (Bonus)

Save a Warp Tab Config so you can launch Wisp with one click:

Create `~/.warp/tab_configs/wisp.toml`:

```toml
[[tabs]]
name = "Wisp"
directory = "/path/to/your/project"
command = "wisp --model qwen2.5-coder:7b"
```

Then in Warp: hover the tab → three dots → Launch from Config → select "Wisp".

---

## Quick Setup

```bash
# 1. Install Wisp
pip install -e /path/to/wisp

# 2. Create shared skills for both Warp and Wisp
mkdir -p .agents/skills/code-review
cat > .agents/skills/code-review/SKILL.md << 'EOF'
---
name: code-review
description: Review code changes in the current project
---
# Code Review
Analyze the git diff and suggest improvements.
Focus on: error handling, edge cases, performance.
EOF

# 3. Use either tool:
#    Warp: /code-review
#    Wisp: wisp --skill code-review "review changes"
```

---

## Architecture Summary

```
┌──────────────────────────────────────────────┐
│                 Warp Terminal                 │
│  ┌────────────────────────────────────────┐   │
│  │  wisp "refactor main.py"              │   │
│  │  ┌──────────────────────────────────┐  │   │
│  │  │  Warp detects "wisp" command     │  │   │
│  │  │  ├─ Tier 1: No match → runs CLI  │  │   │
│  │  │  ├─ Tier 3: Match → toolbelt ON  │  │   │
│  │  │  └─ Skill: same SKILL.md files   │  │   │
│  │  └──────────────────────────────────┘  │   │
│  └────────────────────────────────────────┘   │
│                       │                       │
│                       ▼                       │
│  ┌────────────────────────────────────────┐   │
│  │  Wisp (subprocess)                     │   │
│  │  ├─ Ollama ← local model inference     │   │
│  │  ├─ Tools: read/write/bash/list        │   │
│  │  └─ Skills: loaded from same dirs      │   │
│  └────────────────────────────────────────┘   │
└──────────────────────────────────────────────┘
```
