"""Wisp — a local Ollama-powered coding agent, compatible with Warp's Skill ecosystem.

Wisp provides an open-source, local-first alternative to Warp's Oz cloud agent.
It uses Ollama for local model inference and supports Warp's Skill format
(SKILL.md files in .agents/skills/ directories).

## SDK Usage

```python
from wisp import Wisp

# High-level synchronous API
agent = Wisp(model="llama3.2", workspace=".")
for event in agent.run("refactor auth.py"):
    print(f"[{event.type}] {event.text}")

# Low-level async API (full control)
from wisp import WispAgentCore, CLITransport

core = WispAgentCore()
transport = CLITransport(core)
transport.repl()
```
"""

__version__ = "0.1.0"

# ── High-level API ───────────────────────────────────────────────────

from wisp.sdk import Wisp

# ── Core components ──────────────────────────────────────────────────

from wisp.core.agent import WispAgentCore
from wisp.core.events import AgentEvent

# ── Transports ────────────────────────────────────────────────────────

from wisp.transport.cli import CLITransport
from wisp.transport.server import ServerTransport

# ── Configuration ────────────────────────────────────────────────────

from wisp.config import WispConfig

__all__ = [
    "Wisp",
    "WispAgentCore",
    "AgentEvent",
    "CLITransport",
    "ServerTransport",
    "WispConfig",
]
