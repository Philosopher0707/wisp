# CLAUDE.md

Project instructions for Claude Code when working in this repository.

## Project overview

Wisp is a local-first Python coding agent with Ollama backend. Architecture: event-driven stateless core → transport layer (CLI/WebSocket/TUI/headless) → I/O. SDK-style with layered abstractions.

## Read these first (session records and standing rules)

Load these before changing anything; they hold state and hard-won lessons that the code alone does not show.

| File | What it is |
|---|---|
| `AGENTS_LEARNING.md` | Lessons learned so far, one `##` section each, with evidence. Search it before repeating an experiment. |
| `docs/sessions/2026-09-29-network-agent/CONTEXT.md` | Handoff for the 2026-09-29 session: current state, the unfinished repair of local `main`, boundaries, ranked open work. (Not the repo's `CONTEXT.md`, which is 253 KB and pinned by line number in the register scripts: do not edit it casually.) |
| `workflow.md` | Chronological log of that session (uncommitted working file). |
| `.agents/skills/verified-change-workflow/SKILL.md` | The method: measure, decide, apply; RED first; test through the production path; positive controls and mutation probes; baseline comparison; PR for the human to merge. |
| `WISP_ARCHITECTURE_DECISIONS.md` | ADR log (ADR-0069..0073 the network agent, ADR-0074 no approver / no yes). |
| `wisp_net/README.md` | The network platform, the evaluation harness and its baseline table. |
| `docs/fleet/README.md` | The fleet: `wisp.fleet.toml`, `wisp fleet status/doctor/workers`, MCP workers, the pre-push check. Which repos exist, and how wisp reaches them. |
| `docs/adr/2026-10-04-fleet-worker-contract.md` | Why workers are MCP servers that cannot approve; outcome of the plateform review. |
| `docs/reviews/2026-10-04-plateform-invariant-review.md` | Probe-backed review; the audit-chain fork on the live log and its fix. |

Standing boundaries (also in the assistant's memory as `boundaries.md`): scope is **simulated-first research**;
**open PRs and let the user merge**; never push the user's local-only commits, delete remote branches, spend
money beyond a key they name, or edit their uncommitted files; widening a permission mode or changing a pinned
policy is the user's decision, so state the trade-off and ask.

## Knowledge base (BM25, for now)

In **every project** keep a plain-file knowledge base: markdown files, one topic per `##` section, the search
words in the heading and first line, searched with **BM25**. No vector store or embeddings for now; lexical
ranking is exact about the vocabulary people search with and needs nothing installed. Reference implementation:
`wisp_net/state/knowledge.py` (Okapi BM25 over `wisp_net/knowledge/*.md`, chunked at `##` headings).

- `AGENTS_LEARNING.md` and the per-session `CONTEXT.md` are part of it. Search them before starting work.
- **Update it after every milestone or phase, before moving on**, not at the end: add the lesson, update the
  session state and open work, record decisions in the ADR log. Prefer editing an entry to adding a duplicate.
- A new project starts the same way: create `AGENTS_LEARNING.md`, a session `CONTEXT.md`, and a `knowledge/`
  folder for reference notes, then point `CLAUDE.md` at them.

## Build & test

```bash
pip install -e .                        # Install editable
python -m pytest tests/test_progress.py -v   # Single file
python -m pytest tests/test_transport_cli.py tests/test_progress.py tests/test_spinner.py tests/test_renderer.py -v  # Transport + UX
python -m pytest tests/ -v              # Full suite (may need explicit file list)

# Type checking (bare mypy uses the strict [tool.mypy] gate in pyproject.toml)
mypy
```

## Architecture layers

```
wisp/core/         Pure logic, zero I/O: WispAgentCore (turn loop), AgentEvent, AgentRuntime
wisp/transport/    I/O layer: Transport ABC + CLITransport, WebSocketTransport, HeadlessTransport, TUITransport
wisp/tools/        TOOL_SCHEMAS + TOOL_IMPLS in registry.py (~40 tools)
wisp/multi_agent/  Subagent orchestration: runner, orchestrator, worktree, delegation
wisp/infra/        Security, telemetry, extensions, store
```

## Key conventions

- **Events**: `AgentEvent` dataclass (frozen) with factory functions in `wisp/core/events.py`. Events flow: `engine.turn()` → `transport.send()` → `transport._render_event()`
- **Transports** implement `Transport` ABC (`base.py`): `send()`, `recv()`, `approve()`, `start()`, `stop()`
- **CLI rendering** uses mode-aware pure functions from `renderer.py` — all 4 output modes (unicode/ascii/accessible/minimal) handled via `BoxChars` and `OutputMode`
- **Testing**: pytest with a locally-defined `_MockRuntime` + `StringIO` (for transport tests). Stateless core tests use a real `WispAgentCore` with `MockProvider`
- **Config**: `WispConfig` dataclass in `wisp/config.py`. Resolution: env vars > config file > defaults
- **No comments** explaining WHAT code does — well-named identifiers handle that. Only WHY comments for non-obvious constraints

## CLI dashboard components (new)

- `wisp/transport/progress.py` — `ProgressTracker`: phase detection (understand→plan→execute→verify), tool counting, file tracking. Pure data, no I/O.
- `wisp/transport/spinner.py` — `Spinner`: inline terminal spinner with `\r` overwrites. Mode-aware frames.
- `wisp/transport/renderer.py` — `render_phase_bar()`, `render_turn_stats()`, `render_file_ticker()` added

## CLI event rendering flow

```
Tool Call → spinner.start(label)
Tool Result → spinner.succeed(label) or spinner.fail(label)
Phase change → render_phase_bar(new_phase)
Turn end → render_turn_stats(stats) + render_file_ticker(files) + separator
```
