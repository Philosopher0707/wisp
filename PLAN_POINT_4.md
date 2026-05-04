# Implementation Plan: Point 4 — Persistent Agent Memory

**Status:** Ready for Implementation  
**Scope:** Automatic session summarization + persistence. Load previous session context into new conversations.  
**Dependencies:** None (pure Python, no ML models).  
**Estimated Effort:** ~700 new lines, ~80 modified lines.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Phase 1: Extractive Summarizer — `wisp/summarizer.py`](#3-phase-1-extractive-summarizer--wispsummarizerpy)
4. [Phase 2: Agent Memory Store — `wisp/agent_memory.py`](#4-phase-2-agent-memory-store--wispagent_memorypy)
5. [Phase 3: Session Integration — `wisp/session.py`](#5-phase-3-session-integration--wispsessionpy)
6. [Phase 4: Agent Integration — `wisp/agent.py`](#6-phase-4-agent-integration--wispagentpy)
7. [Phase 5: CLI — `wisp/__main__.py`](#7-phase-5-cli--wisp__main__py)
8. [Phase 6: Testing](#8-phase-6-testing)
9. [File Change Summary](#9-file-change-summary)
10. [Open Questions](#10-open-questions)

---

## 1. Overview

### Goal
Automatically summarize completed Wisp sessions, persist the summaries to disk, and inject relevant past context into new sessions so the agent remembers:
- What was worked on
- Key technical decisions made
- User preferences expressed
- Open tasks / TODOs left behind

### Distinction from Existing `wisp/memory.py`
| Feature | `wisp/memory.py` (existing) | `wisp/agent_memory.py` (new) |
|---------|----------------------------|------------------------------|
| Trigger | Manual (`remember` tool) | Automatic (on session end) |
| Content | Atomic facts | Session narrative + decisions + tasks |
| Scope | Global + workspace key-value | Per-session structured summary |
| Summarization | None (raw facts) | Extractive NLP heuristics |
| User control | `wisp memory` CLI | Transparent, no user action needed |

### Non-Goals
- Not a vector database (no embeddings, no semantic search across memories).
- Not a full conversation log (sessions are already saved; this is a *summary*).
- Not a replacement for `wisp/memory.py` (complements it).

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Session Lifecycle                             │
│                                                                       │
│   ┌──────────┐     ┌─────────────┐     ┌─────────────────────────┐   │
│   │  Start   │────▶│ Load recent │────▶│ Inject into system      │   │
│   │  session │     │ summaries   │     │ prompt (same workspace) │   │
│   └──────────┘     └─────────────┘     └─────────────────────────┘   │
│                                                                       │
│   ┌──────────┐     ┌─────────────┐     ┌─────────────────────────┐   │
│   │  End     │────▶│ Summarize   │────▶│ Append to               │   │
│   │  session │     │ messages    │     │ ~/.config/wisp/agent_memory/ │   │
│   └──────────┘     └─────────────┘     │ sessions.jsonl          │   │
│                                        └─────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Phase 1: Extractive Summarizer — `wisp/summarizer.py`

**Purpose:** Lightweight, dependency-free extractive summarization of conversation messages.

### API Design

```python
# wisp/summarizer.py

from dataclasses import dataclass
from typing import Optional

@dataclass
class SessionSummary:
    summary: str                    # 1–3 sentence narrative
    key_decisions: list[str]        # "Use X for Y", "Decided on Z"
    user_preferences: list[str]     # "Prefers Hindi-English", "Likes detailed explanations"
    open_tasks: list[str]          # "Implement semantic search", "Fix Docker crash"
    files_touched: list[str]       # Extracted from tool calls
    timestamp: str
    workspace: str
    session_id: str

class ExtractiveSummarizer:
    """Summarize a conversation using heuristics — no ML model required."""

    def summarize(self, messages: list[dict], session_id: str, workspace: str) -> SessionSummary:
        """Main entry point."""
        ...

    # ── Internal heuristics ──
    def _extract_summary_sentences(self, messages: list[dict]) -> list[str]: ...
    def _extract_decisions(self, messages: list[dict]) -> list[str]: ...
    def _extract_preferences(self, messages: list[dict]) -> list[str]: ...
    def _extract_tasks(self, messages: list[dict]) -> list[str]: ...
    def _extract_files(self, messages: list[dict]) -> list[str]: ...
    def _score_sentences(self, sentences: list[str]) -> list[tuple[str, float]]: ...
```

### Heuristic Rules

#### A. Summary Sentences
1. Collect all `assistant` message contents.
2. Split into sentences (simple regex: `r'(?<=[.!?])\s+'`).
3. Score each sentence:
   - **Position bonus:** First sentence of first assistant message: +2.0
   - **Position bonus:** First sentence of any assistant message: +1.0
   - **Action verb bonus:** Contains "implemented", "added", "created", "fixed", "refactored": +1.5
   - **Number bonus:** Contains digits ("10 features", "299 tests"): +0.5
   - **Length penalty:** < 20 chars or > 200 chars: −1.0
4. Pick top 3 sentences by score.
5. Join into a coherent paragraph (deduplicate overlapping content).

#### B. Key Decisions
Scan all messages for decision patterns (case-insensitive):
- `"decided to"`, `"decided on"`, `"we decided"`, `"let's use"`, `"going with"`, `"chose"`, `"will use"`, `"settled on"`, `"opted for"`
- Extract the full sentence containing the pattern.
- Deduplicate exact matches.
- Max 5 decisions.

#### C. User Preferences
Scan `user` messages for preference patterns:
- `"I prefer"`, `"I like"`, `"I want"`, `"I need"`, `"I don't want"`, `"I dislike"`, `"please use"`, `"always"`, `"never"`
- Extract the full sentence.
- Also capture explicit corrections: `"No, do X instead"`, `"Actually, Y"`, `"Correction:"`
- Max 5 preferences.

#### D. Open Tasks
Scan all messages for task patterns:
- `"TODO"`, `"FIXME"`, `"HACK"`, `"next time"`, `"later"`, `"still need to"`, `"pending"`, `"not yet"`, `"up next"`, `"future"`, `"plan to"`
- Extract the full sentence or following clause.
- Max 5 tasks.

#### E. Files Touched
Scan `assistant` messages and `tool_calls` for file paths:
- Regex: `r'[\w\-./]+\.(py|js|ts|rs|go|java|md|json|yaml|yml|toml)'`
- Collect unique filenames.
- Max 10 files.

---

## 4. Phase 2: Agent Memory Store — `wisp/agent_memory.py`

**Purpose:** Persist and retrieve session summaries.

### Storage

```
~/.config/wisp/agent_memory/
  sessions.jsonl          # One JSON object per line
```

### Data Model (per line in JSONL)

```json
{
  "session_id": "20260503-202859-367602-repl-session",
  "timestamp": "2026-05-03T07:00:00Z",
  "workspace": "/Users/philosopher/Documents/wisp",
  "summary": "Implemented 10 features including project context, code index, and MCP client. Fixed Docker crash. 299 tests pass.",
  "key_decisions": [
    "Use Dice coefficient for fuzzy matching (no dependency)",
    "Tree-sitter optional with regex fallback"
  ],
  "user_preferences": [
    "Prefers Hindi-English mix",
    "Likes detailed explanations"
  ],
  "open_tasks": [
    "Implement semantic search",
    "Add auto-test on change"
  ],
  "files_touched": [
    "wisp/agent.py",
    "wisp/tools.py",
    "wisp/mcp.py"
  ]
}
```

### API Design

```python
# wisp/agent_memory.py

from pathlib import Path
from typing import Optional
from wisp.summarizer import SessionSummary

AGENT_MEMORY_DIR = WISP_CONFIG_DIR / "agent_memory"
SESSIONS_FILE = AGENT_MEMORY_DIR / "sessions.jsonl"
_MAX_SUMMARIES = 50  # Keep last N summaries to prevent bloat

class AgentMemory:
    """Store and retrieve session summaries."""

    def __init__(self):
        AGENT_MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    def save(self, summary: SessionSummary) -> None:
        """Append a summary to sessions.jsonl."""
        ...

    def load_recent(self, workspace: Optional[str] = None, limit: int = 3) -> list[SessionSummary]:
        """Load recent summaries, optionally filtered by workspace."""
        ...

    def load_all(self) -> list[SessionSummary]:
        """Load all summaries."""
        ...

    def clear(self) -> None:
        """Delete all summaries."""
        ...

    def format_for_prompt(self, summaries: list[SessionSummary]) -> str:
        """Format summaries into a system prompt block."""
        ...

    def _rotate(self) -> None:
        """If file exceeds _MAX_SUMMARIES, keep only the most recent."""
        ...
```

### Prompt Formatting

```python
def format_for_prompt(self, summaries: list[SessionSummary]) -> str:
    if not summaries:
        return ""
    lines = ["## Previous Session Context"]
    for s in summaries:
        lines.append(f"\n### Session {s.session_id[:20]}... ({s.timestamp[:10]})")
        lines.append(f"**Summary:** {s.summary}")
        if s.key_decisions:
            lines.append("**Decisions:**")
            for d in s.key_decisions:
                lines.append(f"  - {d}")
        if s.open_tasks:
            lines.append("**Open tasks:**")
            for t in s.open_tasks:
                lines.append(f"  - {t}")
        if s.user_preferences:
            lines.append("**User preferences:**")
            for p in s.user_preferences:
                lines.append(f"  - {p}")
    return "\n".join(lines)
```

---

## 5. Phase 3: Session Integration — `wisp/session.py`

**Changes:** Add `summarize()` method to the `Session` class.

```python
# wisp/session.py

def summarize(self) -> Optional[SessionSummary]:
    """Generate a summary of this session's conversation."""
    from wisp.summarizer import ExtractiveSummarizer
    if not self.messages:
        return None
    summarizer = ExtractiveSummarizer()
    return summarizer.summarize(
        messages=self.messages,
        session_id=self.id,
        workspace=self.workspace,
    )
```

**Changes:** ~15 lines.

---

## 6. Phase 4: Agent Integration — `wisp/agent.py`

### A. On Session Start

In `run()` and `repl()`, after loading/creating the session:

```python
# Load previous session summaries for this workspace
from wisp.agent_memory import AgentMemory
self.agent_memory = AgentMemory()
recent_summaries = self.agent_memory.load_recent(
    workspace=self.config.workspace or ".",
    limit=3,
)
self._recent_summaries = recent_summaries
```

### B. System Prompt Injection

In `_build_system_prompt()`, after the existing blocks:

```python
# Inject previous session summaries
if hasattr(self, "_recent_summaries") and self._recent_summaries:
    from wisp.agent_memory import AgentMemory
    block = AgentMemory().format_for_prompt(self._recent_summaries)
    if block:
        system += f"\n\n{block}"
```

### C. On Session End

In `run()` cleanup and `repl()` exit path:

```python
# Summarize and save session
if self.session and hasattr(self, "agent_memory"):
    summary = self.session.summarize()
    if summary:
        self.agent_memory.save(summary)
        logger.info("Saved session summary: %s", summary.summary[:80])
```

**Changes:** ~50 lines across `run()`, `repl()`, `_build_system_prompt()`.

---

## 7. Phase 5: CLI — `wisp/__main__.py`

Add `memory` subcommand (extend existing `cmd_config` or add new):

```bash
wisp memory list              # Show all session summaries
wisp memory show <id>       # Show full summary for a session
wisp memory clear           # Clear all agent memory
wisp memory stats           # Show count, oldest, newest
```

**Implementation:**

```python
def cmd_memory(subcommand: str = "list", session_id: Optional[str] = None):
    from wisp.agent_memory import AgentMemory
    mem = AgentMemory()

    if subcommand == "list":
        summaries = mem.load_all()
        if not summaries:
            print("No session summaries stored.")
            return
        print(f"{'Session ID':<30} {'Date':<12} {'Workspace':<30} {'Summary'}")
        for s in summaries:
            ws = s.workspace[:28]
            print(f"{s.session_id:<30} {s.timestamp[:10]:<12} {ws:<30} {s.summary[:50]}...")

    elif subcommand == "show" and session_id:
        summaries = mem.load_all()
        for s in summaries:
            if s.session_id.startswith(session_id):
                print(json.dumps(s.__dict__, indent=2, default=str))
                return
        print(f"Session '{session_id}' not found.")

    elif subcommand == "clear":
        mem.clear()
        print("✓ Agent memory cleared.")

    elif subcommand == "stats":
        summaries = mem.load_all()
        print(f"Total summaries: {len(summaries)}")
        if summaries:
            print(f"Oldest: {summaries[0].timestamp[:19]}")
            print(f"Newest: {summaries[-1].timestamp[:19]}")
```

**Changes:** ~50 lines.

---

## 8. Phase 6: Testing

### `tests/test_summarizer.py` (~120 lines)

- Test `_extract_decisions` with known patterns.
- Test `_extract_preferences` with known patterns.
- Test `_extract_tasks` with known patterns.
- Test `_extract_files` with mock tool calls.
- Test full `summarize()` with synthetic messages.

### `tests/test_agent_memory.py` (~100 lines)

- Test `save()` appends to JSONL.
- Test `load_recent()` respects workspace filter and limit.
- Test `load_recent()` returns newest first.
- Test `_rotate()` drops old entries.
- Test `format_for_prompt()` produces non-empty string.

### `tests/test_session_summarize.py` (~50 lines, optional)

- Test `Session.summarize()` returns `None` for empty messages.
- Test `Session.summarize()` returns `SessionSummary` for populated messages.

---

## 9. File Change Summary

| File | Action | Lines | Notes |
|------|--------|-------|-------|
| `wisp/summarizer.py` | **New** | ~250 | Extractive summarization engine |
| `wisp/agent_memory.py` | **New** | ~200 | JSONL persistence + prompt formatting |
| `wisp/session.py` | Modify | ~15 | Add `summarize()` method |
| `wisp/agent.py` | Modify | ~50 | Load on start, inject into prompt, save on end |
| `wisp/__main__.py` | Modify | ~50 | `memory` subcommand |
| `tests/test_summarizer.py` | **New** | ~120 | Heuristic extraction tests |
| `tests/test_agent_memory.py` | **New** | ~100 | Persistence tests |
| **Total** | | **~670 new, ~115 modified** | |

---

## 10. Open Questions

1. **Should we deduplicate summaries across sessions?**
   - If two consecutive sessions have identical open tasks, should we only show the latest?
   - **Recommendation:** No — show all. The user may have made progress between sessions. Agent can infer state from conversation.

2. **Should summaries be workspace-scoped or global?**
   - **Recommendation:** Load recent summaries for the *same workspace* only. Cross-workspace context is usually noise. But keep all summaries in one file for `wisp memory list`.

3. **How many summaries to inject?**
   - Default: 3 most recent for the same workspace.
   - **Configurable:** Add `config.memory_context_limit` (default 3).

4. **Should the user be able to edit summaries?**
   - **Recommendation:** Phase 2. For now, summaries are read-only. User can `wisp memory clear` if needed.

5. **Interaction with existing `wisp/memory.py`?**
   - Both blocks appear in the system prompt:
     1. `## Learned Preferences` (from `memory.py` — manual facts)
     2. `## Previous Session Context` (from `agent_memory.py` — auto summaries)
   - They serve different purposes and should coexist.

---

*End of Plan — Ready for implementation approval.*
