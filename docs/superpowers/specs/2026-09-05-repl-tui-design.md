# REPL TUI Design — streaming blocks, alt-screen pager, raw-mode gates

- Status: proposed (brainstormed 2026-09-05, companion server mockups deferred to implementation)
- Scope: all five spec systems, sequenced as vertical slices in the existing layer
- Anchor: `wisp/cli/repl.py` (`ReplRunner` + `CLIEventRenderer`), pure fns in `wisp/transport/renderer.py`
- Non-goals: replacing Textual `tui.py`, changing the `Transport` ABC, touching provider/core layers

## 1. Screen model & state machine (approved)

- `ScreenModel` dataclass owns viewport state: `mode` (`SCROLLBACK` | `ALTSCREEN` | `DEGRADED`), append-only block list (cap 500, prune oldest), collapsed-set of block IDs, telemetry snapshot, at most one pending gate.
- Pure `render(model, width, output_mode) -> list[str]` in `renderer.py`; same `BoxChars`/`OutputMode` handling as existing code (accessible/ascii free).
- Inputs become events (`Key`, `Resize`, `Tick`, `GateResult`, `SigInt`) folded via `reduce(model, event) -> (model, effects[])`. Runner executes effects (kill subprocess, open pager, exit); reduce is side-effect-free and TTY-less testable.
- Alt-screen (`\e[?1049h`) only for: topology inspect, multi-file diff pager, config menu. `DEGRADED` chosen once at startup via `isatty()`; irreversible; NDJSON to stdout, no ANSI/spinner/cursor moves.

## 2. Block hierarchy & widgets

- Block types: `Plan` (checklist with states), `Thought` (collapsed default: `▸ Thinking: … [1.2s]`; `Tab`/`Space` toggles newest collapsible), `Tool` (2-row envelope: row 1 glyph `⚡/🔍/⌨` + id + concise params; row 2 braille spinner `⠋` → `✔/✖` + duration + one-line summary), `Diff` (inline unified diff ≤60 lines, `+` green / `-` red, 2–3 context lines via existing diff viewer; above threshold → aggregate `Modified 3 files (+42, −12)` + `v` pager offer), `Log`, `Gate` (approval prompt row).
- Rendering rules: atomic appends only (never rewrite scrollback except the `\r`-repainted telemetry line); every block carries a stable ID for collapse/pager targeting; all strings width-truncated with `display_width()`.

## 3. Gates, keyboard matrix, signals

- termios raw mode (`ICANON`/`ECHO` off) scoped narrowly to pending-gate prompts; saved attrs restored immediately after; readline input path untouched.
- Matrices: tool gate `y/n/a/e/?`, diff gate `y/n/v/r`, subagent `Enter/s/i`. `?` renders the legend as a plain block (accessible-safe). Session `a` (always-allow) reuses existing `ApprovalSessionState` memory.
- SIGINT: first press cancels generation/child subprocess → prompt; second within 1s → `TerminalGuard` teardown (leave alt-screen, `\e[?25h` show cursor, restore termios) + exit 130. `TerminalGuard` also covers `atexit`/exception paths so the terminal is never left raw.

## 4. Telemetry bar

- One `\r`-repainted status line (zero scrollback pollution): `STATE │ TOKENS in/out/cache% │ COST │ branch cwd-root`. Refresh on 2Hz `Tick` + block append. Degraded mode emits NDJSON `telemetry` events instead.
- Sources: existing runtime telemetry (`record_turn`), existing git context; no new tracking infra.

## 5. Degradation, async I/O, testing

- Non-TTY/CI: plain-text NDJSON per block/gate/telemetry event; spinners/cursor code compiled out by mode branch (not runtime flags).
- Subprocess pipes drained by a single reader thread into `queue.Queue` (mirrors `cli.py` approval-reader `select` pattern); reduce loop never blocks on I/O.
- Tests mirror source (`tests/test_terminal_*.py`): reduce/render unit tests with no TTY, ANSI golden tests for escape sequences, one pty smoke test for the raw-mode gate + reset guarantee.

## 6. Sequencing (vertical slices)

1. Streaming blocks + collapsible thought + tool envelopes on scrollback.
2. Diff aggregate rule + Textual alt-screen pager (Textual already a dependency).
3. Raw-mode gate matrix + SIGINT two-stage + `TerminalGuard`.
4. Telemetry line + NDJSON degraded mode + golden/pty tests.

Each slice shippable against the existing suite; no slice changes the `Transport` ABC.
