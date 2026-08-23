# REPL Operating Contracts

The standard each layer of the interactive loop must meet. Every contract
here traces to a real failure we shipped or caught by probing; none are
aspirational. Status column in the breakdown table reflects the code as of
the last audit round.

## Component breakdown

| # | Layer | Entry points | Owns | Standard |
|---|-------|--------------|------|----------|
| 1 | Input acquisition | `_input_line`, `_input_multiline` (cli.py) | tty vs piped reads, EOF, ANSI stripping, backslash continuation | C1 |
| 2 | Classification & routing | `_run_repl` main loop (entry.py) + `dispatch` (commands.py) | deciding what a line *is* before anything runs | C2 |
| 3 | Command execution | `@register` registry, `AgentAdapter` facade | 24 slash commands, alias resolution, containment | C3 |
| 4 | Turn execution | `_run_turn` (entry) → `AgentRuntime.run_turn` → `WispAgentCore.turn` | the bounded provider↔tool loop, cancellation | C4 |
| 5 | Event transport & rendering | `CLITransport._render_event`, renderer.py pure fns | every event → visible, mode-aware output; buffers, spinner | C5 |
| 6 | State & persistence | session dict, store saves at boundaries | one live session pointer, honest save reporting | C6 |
| 7 | Lifecycle & signals | SIGINT arming, exit paths, cleanup order | interrupts, force-quit, shutdown sequence | C7 |

---

## C1 — Input acquisition

1. **EOF is `None`.** Every read path maps exhausted stdin to `None` — never
   `""`, never an exception past the reader. A piped script terminates the
   REPL gracefully when it ends. (Regression: piped EOF once spun at 100% CPU.)
2. **Ctrl+C means what the help text says.** Single-line prompt: exit
   gracefully. Multiline: clear current input and re-prompt (`""` sentinel).
   The toggle message and the behavior may never diverge.
3. **No read path blocks cancellation irrecoverably.** Interactive approval
   reads poll stdin with a stop-event so a cancelled turn cannot leave an
   orphaned reader swallowing later input.
4. **Docstrings describe implemented behavior only.** Claimed features that
   don't exist (e.g. "bracketed paste detection") are removed on sight.

## C2 — Classification & routing

Every input line gets **exactly one disposition**, decided at one choke point
before any work runs:

| Class | Detection | Disposition |
|-------|-----------|-------------|
| empty | strip → `""` | re-prompt, no output |
| exit word / `/exit` | literal match / ExitREPL | graceful exit with resume hint |
| `/multiline [single\|multi]` | prefix intercept, arg validated | mode switch + confirmation; garbage args rejected visibly, never accepted silently |
| other `/command` | starts with `/` | dispatch (C3); unknown command = visible error + hint, **never** sent to the model as a turn |
| plain text | everything else | exactly one turn |

A command's string return value **is** a follow-up prompt and runs as one
(REPL and single-shot mode alike). No disposition falls through two handlers.

## C3 — Command execution

1. **Aliases are unique, enforced at import time.** Registering a key owned
   by another command raises `ValueError` — silent last-write-wins stole
   `/compact`'s alias `c` for months.
2. **Handlers never raise past `dispatch`.** Any handler exception is caught,
   logged, and shown as `✗ Command failed: …`; the REPL survives every
   possible command crash.
3. **Return contract:** `True` = consumed · `False` = not a command ·
   `str` = follow-up prompt · `ExitREPL` = quit. Nothing else.
4. **Commands see live state only through `AgentAdapter`** — never stale
   closures. Facade attributes (`session`, `messages`, `runtime`) are read
   at call time.

## C4 — Turn execution

1. **The live session pointer is read at call time** (`adapter.session`
   inside `_turn()`), because commands like `/new` swap it mid-REPL.
2. **Every turn is bounded**: `turn_timeout` wall clock (default 1800s,
   schema range 10–7200) outside, `max_iterations` (default 50) inside;
   per-tool 300s, bash 60s, provider read-gap 60–120s, subagent role
   timeouts 90–180s nest beneath. Fallbacks must match schema defaults.
3. **Cancellation persists.** Ctrl+C or approval `[c]` raises
   `CancelledError`; the runtime's `finally` still appends messages and
   saves; the user sees an interrupt banner plus a resume command naming
   the *current* session.
4. **Known trade-off:** human approval time counts against `turn_timeout`.
   Bounded turns are the point; revisit only with an explicit pause design.

## C5 — Event transport & rendering

1. **No renderer may raise past `_render_event`.** A render bug aborts at
   most one line, never the turn's remaining output.
2. **String ops only after coercion.** Structured payloads (spawn/fanout/MCP)
   carry dict `data`; coerce via `_coerce_tool_data` before `.split()`,
   wrapping, or previewing. (Regression: dict data crashed rendering and
   silently dropped the rest of the turn.)
3. **Every event type has a mode-aware rendering decision** across unicode /
   ascii / accessible / minimal — minimal may return `""`, but that choice
   must be explicit in the renderer.
4. **Delegated work is visible and counted.** Subagent lifecycle streams live
   (`EventType.SUBAGENT`), and files children report count toward turn stats
   like files the parent wrote itself.
5. **Honest numbers.** Stats lines (tools, files, elapsed) reflect real
   outcomes or say nothing — no placeholders, no grammar slips ("1 lines").

## C6 — State & persistence

1. **Save at every boundary**: `/new` (old session), forced best-effort save
   on *every* exit path (Ctrl+C, EOF, error, clean quit), and the runtime's
   post-turn `finally`.
2. **Honesty about saving.** Exit messaging reports failure ("Could not save
   session") instead of unconditional success claims.
3. **Resume hints always name the live session id**, looked up at print time.

## C7 — Lifecycle & signals

1. **Cooperative SIGINT is re-armed after every turn.** First press:
   interrupt the turn, stay alive. Second press within a turn: default
   handler, force-quit. The arming state machine resets per turn.
2. **Cleanup order is fixed**: cancel in-flight task → let cancellations
   settle → restore signal handler → force-save → close loop → report.
3. **Reserved-but-unwired surfaces are labeled.** Steering pause exists in
   the event vocabulary and server transport but has no CLI trigger; treat
   as reserved until wired, not as working.

---

## Regression coverage map

| Contract | Tests |
|----------|-------|
| C1 EOF | `tests/test_repl_operation.py::TestPipedEofTermination` |
| C1 multiline Ctrl+C | `::TestMultilineInterruptContract` |
| C2 routing, /new, /multiline | `::TestNewSessionSync`, `::TestMultilineCommand`, `test_single_prompt_follow_up` |
| C3 returns & containment | `tests/test_commands.py`, probe-backed dispatch tests |
| C3 alias uniqueness | `tests/test_commands.py::TestRegistryIntegrity` |
| C4 cancellation | `tests/test_repl_operation.py::TestApprovalCancel` |
| C5 subagent streaming | `tests/test_subagent_events.py` |
| C5 structured results | `tests/test_transport_cli.py::TestStructuredToolResultRender` |
| C5 stats honesty | `tests/test_progress.py::TestDelegationFileTracking` |
