# REPL TUI Design — streaming blocks, pager, raw-mode gates (v2)

- Status: revised after spec review iter-1 (2026-09-05); all iter-1 issues addressed inline
- Scope: scrollback blocks + approval gates + telemetry line + diff pager, sequenced as vertical slices in the existing layer
- Explicitly out of scope (follow-up spec): multi-agent topology inspect, config menu (no such REPL commands/views exist yet)
- Anchor: `wisp/cli/repl.py` (`ReplRunner`, `CLIEventRenderer`, `ReplLifecycle`), pure fns in `wisp/transport/renderer.py`
- Non-goals: replacing the Textual app (`wisp/tui/app.py`) or its `wisp/transport/tui.py` bridge, changing the `Transport` ABC or `ApprovalVerdict`, touching provider/core layers, new token/cost tracking infra

## 1. Screen model & state machine

- `ScreenModel` dataclass holds viewport *data only*: `viewport` (`SCROLLBACK` | `ALTSCREEN`), append-only block list (cap 500, prune oldest), collapsed-set of block IDs, telemetry snapshot, at most one pending gate. No formatting lives here.
- Display styling reuses the existing system verbatim: new `render_*` functions live in `renderer.py`, return `str` like `render_tool_call`/`render_thinking_block` (`renderer.py:78-133`), and read the global mode via `BoxChars()`/`status_symbols()`/`is_accessible()` (`terminal_width.py`). No parallel mode enum; `DEGRADED`/pipe behavior follows the existing detector (`WISP_OUTPUT_MODE`, `WISP_ACCESSIBLE`, `NO_COLOR`, non-TTY, `TERM=dumb`, `terminal_width.py:38-59`).
- Events: `Key`, `Resize`, `Tick`, `GateResult { verdict: str }`, `SigInt`, folded via `reduce(model, event) -> (model, list[Effect])` with `Effect = CancelTurn | OpenPager | ExitLoop | NoOp`. `Tick` reuses the existing wait-clock thread; `Resize` defers to the prompt_toolkit/SIGWINCH redraw (`repl.py:536-542`); cancel target is `_turn_task.cancel()` (the runner owns no child handle).
- Integration: `CLIEventRenderer.render_event` keeps its streaming calls and additionally appends block data to `ScreenModel`. Alt-screen (`\e[?1049h`) engages only for the diff pager. Machine output follows `repl-aesthetics.md`: `--json` emits WS-identical NDJSON on **stdout** (the automation contract); all interactive chrome (status, tool lines, stats) goes to **stderr**. The branch point is runtime `get_output_mode()`; non-TTY pipe/`NO_COLOR`/`TERM=dumb` auto-select the non-animated branch via the existing detector.

## 2. Block hierarchy & widgets

- Block types: `Plan`, `Thought` (collapsed default `▸ Thinking: … [1.2s]`; `Space` toggles newest collapsible — `Tab` stays with completion), `Tool` (2-row envelope: row 1 glyph+id+params; row 2 spinner→outcome + duration + summary), `Diff` (inline iff added+removed ≤ 60 lines via `wisp/ui/diff_viewer.py`; else aggregate `Modified 3 files (+42, −12)` + `v` pager offer), `Log`, `Gate`. (Sweep note for planning: aesthetics §5/§9 uses a related cap of 50 — unify to one cap.)
- Render rule: append-only, except the spinner-owned line and the telemetry line, both finalized in place via `\r…\e[K` (+newline) exactly like `Spinner.succeed/fail` (`spinner.py:121-151`). Block IDs are `blk-{seq}`; prune drops oldest blocks and their collapsed flags (documented, acceptable).
- Glyphs per mode (via existing helpers, never raw unicode): `⚡`→`[!]`/`[TOOL]`/`""`, `▸`→`>`/`[THINKING]`/`""`, braille→`...`/`[busy]`/`""`, `✔/✖`→`✓/✗`/`[PASS]/[FAIL]`/`""`.

## 3. Gates, keyboard, signals

- The shipped approval contract is frozen: keys `y/Y/v/a/n/N/d/c` map through `prompt_for_approval` to the 8 `ApprovalVerdict`s (`cli/approval.py:89-118`); `Y`≠`a` (`APPROVE_ALWAYS` vs `AUTO_ALL`) preserved; unknown/empty/EOF fail closed to `REJECT`; `c` raises `ApprovalCancelled` (recorded denial, not task-cancel).
- Spec input matrix expressed as contextual aliases of existing verdicts, no new verdicts: subagent spawn uses `y` (approve) / `n` (skip) / `v` (inspect context slice as a non-mutating view). The `v` alias resolves at the existing `VIEW` render branch (context slice instead of diff for spawn gates) rather than through `prompt_for_approval`'s `is_file_edit` gate; `?` prints the key legend as a plain block. `e` (edit args) and `r` (reject-with-critique) are dropped — no verdict mapping exists; recorded as future work.
- termios raw mode (`ICANON`/`ECHO` off) applies only while a gate is pending, coordinated with the prompt_toolkit session (`repl.py:195-247`), `TypeAheadBuffer.pause/resume`, and `_approval_lock` stdin exclusivity (`transport/cli.py:786-805`); POSIX `select` fallback per `cli.py:872-912`; non-POSIX/non-tty falls back to plain-line prompting. `TerminalGuard` (armed in `ReplLifecycle`) owns termios save/restore + alt-screen exit + `\e[?25h`, covering `atexit`/exception paths.
- SIGINT behavior unchanged from `repl.py:520-542`: first press cancels `_turn_task`; second raises `KeyboardInterrupt` into `_show_exit`/`shutdown` with history+session persist. No custom exit-130 path.

## 4. Telemetry bar

- One `\r`-repainted status line showing only fields that exist today (via `render_turn_stats` + git segment): turn/tools/files/elapsed/ctx-estimate + branch. Cache-hit % and session cost are explicitly out of scope (provider accounting does not emit them; `telemetry.record_turn` carries latency+tokens only).
- Single-`\r`-writer rule: telemetry pauses while `ACTIVE_SPINNER` is held (existing pause protocol) and refreshes at ≤10Hz per `repl-aesthetics.md` single-status-row rules. Degraded mode emits NDJSON `telemetry` events instead.

## 5. Testing

- `tests/test_terminal_*.py` mirroring source: `reduce`/`render_*` unit tests with no TTY; ANSI golden tests for `\r…\e[K` sequences and all four output modes; one pty smoke test proving the raw-mode gate restores termios and the cursor on both SIGINT stages.

## 6. Sequencing (vertical slices)

1. Streaming blocks + collapsible thought + tool envelopes on scrollback.
2. Diff aggregate rule + lazy-imported Textual alt-screen pager (startup budget per `entry.py:735`; collaborates with the tui bridge, does not replace it).
3. Raw-mode gate matrix (aliases only) + `TerminalGuard` + SIGINT conformance tests.
4. Telemetry line + NDJSON degraded branch + golden/pty tests.

Each slice shippable against the existing suite; no slice changes the `Transport` ABC or `ApprovalVerdict`.
