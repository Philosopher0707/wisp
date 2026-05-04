# Future Expansion Reminders

This document tracks planned features for cross-session memory and context persistence in Wisp.

---

## 1. Auto-Summarize: Inject Previous Session Context

**Goal:** When starting a new session, automatically summarize recent related sessions and inject that summary into the system prompt so the agent retains context across conversations.

**How it would work:**

1. **Trigger:** On `Session.create()`, after the user types their first prompt, scan recent sessions (last N sessions or sessions with similar title slugs).

2. **Summarize:** For each relevant session, extract the final assistant message or run a cheap summarization pass (e.g., using a lightweight local model or simple heuristics like last tool call results + final response).

3. **Inject:** Append a `## Previous Context` block to the system prompt:
   ```markdown
   ## Previous Context
   - Session "refactor-auth": Migrated auth module to async/await. Tests passing.
   - Session "add-tests": Added 12 unit tests for auth module. Coverage at 87%.
   ```

4. **Config toggle:** Add `WISP_AUTO_SUMMARIZE=true/false` and `WISP_SUMMARIZE_MAX_SESSIONS=3` to config.

**Files to touch:**
- `wisp/session.py` — Add `summarize()` method or standalone helper
- `wisp/agent.py` — Call summarizer in `_build_system_prompt()` or `Session.create()`
- `wisp/config.py` — Add `auto_summarize`, `summarize_max_sessions` settings

**Open questions:**
- How to determine "related" sessions? Title similarity? Workspace overlap? Manual tagging?
- Summarization cost — should it be async/cached? Should it use the same model or a cheaper one?

---

## 2. Shared Memory File: Persistent Key-Value Facts

**Goal:** Maintain a global `memory.json` file that stores key facts, decisions, and preferences the agent learns across all sessions, so they don't need to be repeated.

**How it would work:**

1. **Storage:** `~/.config/wisp/memory.json` — a simple JSON object:
   ```json
   {
     "project_preferences": {
       "/Users/me/project-a": {
         "test_framework": "pytest",
         "style_guide": "black + ruff",
         "preferred_model": "deepseek-v4-flash:cloud"
       }
     },
     "learned_facts": [
       "User prefers descriptive variable names over short ones",
       "User wants type hints in all Python functions",
       "Project uses async/await pattern for I/O bound code"
     ],
     "last_updated": "2026-04-30T18:00:00Z"
   }
   ```

2. **Auto-learn:** After each tool call or assistant response, the model could optionally emit a `memory_update` tool call (or we parse the response for "remember that..." patterns) to append facts.

3. **Inject:** In `_build_system_prompt()`, load the memory for the current workspace and append:
   ```markdown
   ## Learned Preferences
   - Test framework: pytest
   - Style: black + ruff
   - User prefers descriptive variable names over short ones
   ```

4. **Manual edit:** Users can edit `memory.json` directly or use commands:
   ```bash
   wisp memory add "Use pydantic for all data models"
   wisp memory list
   wisp memory remove "Use pydantic..."
   ```

**Files to touch:**
- `wisp/memory.py` — New module: `MemoryManager` class with `load()`, `save()`, `add_fact()`, `get_facts(workspace)`
- `wisp/agent.py` — Inject memory block into system prompt; handle `memory_update` tool if implemented
- `wisp/config.py` — Add `memory_enabled`, `memory_max_facts` settings
- `wisp/__main__.py` — Add `wisp memory` subcommands

**Open questions:**
- Should memory be global or per-workspace? (Probably both: global facts + workspace overrides)
- How to prevent memory bloat? Max facts? TTL? Relevance scoring?
- Should the model itself manage memory, or should it be user-driven?

---

## Implementation Priority

| Feature | Complexity | User Value | Suggested Order |
|---------|-----------|------------|-----------------|
| Shared memory file | Low-Medium | High | **1st** — Simple JSON, immediate value |
| Auto-summarize | Medium | Medium | **2nd** — Requires summarization logic + relevance detection |

---

## Related Code Pointers

- Session persistence: `wisp/session.py` — `SessionManager.save/load`
- System prompt builder: `wisp/agent.py` — `_build_system_prompt()`
- Config resolution: `wisp/config.py` — `WispConfig` class
- Tool execution: `wisp/tools.py` — `execute_tool()` dispatch

---

*Last updated: 2026-04-30*
