# Wisp REPL Interface Design

Status: proposed — v1 targets the CLI transport (`wisp/transport/cli.py`,
`renderer.py`, `progress.py`, `spinner.py`). The Textual TUI is out of scope
here and will consume these contracts later.

## 1. Principles (earned from live evidence this session)

1. **Honesty over decoration.** Every state the user can see must be true:
   never show progress that isn't happening (the old retroactive
   "Auto-delegating…" was a bug, not a style choice). Never hide failure —
   empty streams surface as visible errors after one retry.
2. **Bounded everything.** Every wait has a deadline and a visible clock:
   provider stall ≤90s → retry; classify ≤10s → skip; child budgets clamp to
   the turn clock. Silence past a bound is a defect.
3. **One gutter.** All chrome indents two spaces; long-form model prose runs
   at column 0 so reading width wins. Nothing else moves.
4. **Progressive disclosure.** Thinking collapses to a one-line preview
   (`/thinking` expands); tool output caps at 30 wrapped lines; diffs cap at
   50 with an honest `… (+N more lines)` tail.
5. **Mode parity.** Every glyph has an ASCII twin and an accessible word form;
   minimal mode keeps only outcome lines. A widget that renders in one mode
   and garbage in another is broken.

## 2. Turn anatomy (the core object)

Annotated ground truth — what a turn must look like when everything fires:

```
➜ research caching strategies use subagents        ← prompt echo (tty only)
  ℹ Auto-delegating to subagents...                ← verdict BEFORE launch
  🧬 [researcher] Research caching strategies…     ← child starts (live)
  ✓ [researcher] 47.8s · 2 files                   ← child ends (live)
  ⠋ read_file settings.json                        ← spinner: tool running
  ✓ read_file · 12ms                               ← fast tool: inline result
  {"model": "llama3", "retries": 3}
─── Diff — …app.py                                 ← edit tools: diff card
   1 def greet(name):
 2 -    return "hello"
 2 +    return f"hello, {name}!"
                                                   ← blank line before prose
Here's what I found: …

  ✓ understand ✓ plan ❯ execute ○ verify           ← phase bar on transition
  Turn 1 · 2 tools · 1 files · 47.9s               ← stats always last
  Files: app.py                                    ← ticker only when non-empty
──────────────────────────────────── … ──────────  ← turn separator
```

Rules pinned by tests already in the suite: buffers flush before any system
line; response gets a blank line after tool blocks; subagent lines interleave
in real time; stats indent matches the gutter.

## 3. Visual system

| Event class | Unicode | ASCII | Accessible | Minimal |
|---|---|---|---|---|
| Info | `ℹ` dim | `i:` | `[INFO]` | hidden unless error |
| Warning | `⚠` yellow | `!:` | `[WARN]` | shown |
| Error | double-border box `✗` | `+--+` box | `[ERROR]` block | one line |
| Tool ok | `✓` green | `x`→`[OK]` | `[PASS]` | header only |
| Tool fail | `✗` red | `[X]` | `[FAIL]` | header only |
| Child start | `🧬` accent | `>` | `[SUBAGENT] Started.` | hidden |
| Child done | `✓ [role]` | `+` | `[SUBAGENT] Done.` | hidden |
| Child retry/fail | `↻` / `✗` warn/err | `~` / `x` | words | hidden |
| Phase bar | `❯ ✓ ○` | `> x o` | `[PHASE] > …` | hidden |
| Thinking preview | `🧠 "…" (N lines)` | same minus emoji | `[Thinking] quote` | hidden |

Color semantics are stable across all modes: green=safe, red=failed,
yellow=degraded-but-working, cyan=structural (hunks/headers), dim=chrome.

## 4. Latency contract (new — the dead-air rule)

The user must never stare at a silent prompt after Enter:

| Window | What renders | Bound |
|---|---|---|
| Submit → first event | dim elapsed ticker on the prompt line: `(2.1s)` grows in place | first-token deadline 90s → abort+retry |
| Delegation analysis | `ℹ Analyzing whether to delegate…` if classify exceeds 1s | classify 10s cap |
| Each tool call | spinner with truncated label (≤ term_width−12) | tool_timeout config |
| Child running | 🧬 line already rendered at start; nothing more needed | role budget ×1.5 |
| Empty stream | visible error after ONE retry | guard shipped |

Acceptance: instrumented run shows zero multi-second gaps with no rendered
state change.

## 5. Interaction model

- **Input**: `➜ ` primary; trailing `\` or blank-line-Enter enters multiline
  (`... ` continuation, double-Enter ends). Readline history/editing on ttys;
  raw ESC-sequence stripping elsewhere. Piped stdin: each line is a prompt.
- **Approvals** (non-FULL modes): blank line, `⚠️ tool(args)` preview,
  options `y Y a n N d c`; `c` cancels the whole turn (unwinds honestly);
  any other key denies. FULL mode auto-approves silently by design.
- **Interrupt**: Ctrl+C during a turn cancels the turn's tasks, flushes
  buffers, prints resume hint; Ctrl+C at prompt exits cleanly.
- **Steering** (proposed): mid-turn injection exists in the engine
  (`steering_inject/paused/resumed` events render today) but has no entry
  point. Proposal: type-ahead buffer — text typed during a turn is captured
  and injected at the next tool boundary; Esc clears it. No signal hacks.

## 6. Commands surface

Existing: `/help /approve /bash /clear /compact /continue /drop /exit /grep
/init /ls …`. Additions this design commits to: `/model` (switch model
mid-session, re-renders banner meta), `/sessions` (list + switch saved
sessions), `/tokens` (context usage report). Everything else stays.

## 7. Stats & meters

Stats line gains an optional context meter when estimates are available:
`Turn 1 · 2 tools · 1 files · 47.9s · ctx 18k (12%)`. Hidden in minimal
mode; estimate source is the existing `_estimate_tokens` path — clearly
labeled as estimate, never presented as billing truth.

## 8. Known rough edges queued (evidence-backed)

| # | Item | Class |
|---|---|---|
| R1 | Dead-air ticker between Enter and first event | latency contract |
| R2 | Spinner label truncation for long bash commands | overflow |
| R3 | Strip JSON envelope keys from single-value tool results | cosmetics |
| R4 | Context meter in stats | metering |
| R5 | `/model`, `/sessions`, `/tokens` commands | surface area |
| R6 | Type-ahead steering capture | interaction |
| R7 | Accessible-mode diff already plain — extend `[Diff]` label prefix | a11y parity |

## 9. Milestones

All four milestones shipped (this session, gated by pytest+mypy+ruff):

- ~~**M1 — Latency contract**~~ ✅ wait clock + spinner truncation +
  classify indicator; acceptance tests drive a slow provider through the
  real REPL loop and assert a state render inside each window.
- ~~**M2 — Meters & commands**~~ ✅ context meter in stats; envelope
  unwrap for spawn results; /model /sessions /tokens surface verified.
- ~~**M3 — Steering**~~ ✅ type-ahead lines inject at tool boundaries via
  runtime inbox → engine drain; either steered or replayed, never both;
  rendered per mode with the ↻/~ /[STEER] glyphs.
- ~~**M4 — Parity sweep**~~ ✅ four-mode golden transcript snapshots under
  tests/goldens/transcripts/ are now the executable spec — visual changes
  fail CI with a byte diff; regenerate deliberately with UPDATE_GOLDENS=1.

Each milestone ships gated (pytest+mypy+ruff) as themed commits; golden
transcript fixtures become the regression harness for future UI work.
