# Wisp REPL Aesthetics — Rendering Specification v3 (Industry Alignment)

Status: **v3**. Supersedes the visual sections of v2; `docs/repl-design.md`
principles and latency contract remain authoritative. v3 aligns Wisp's
terminal behavior with the conventions of best-in-class CLI tooling —
cargo, rustc, npm, gh, docker compose, terraform — while keeping the
agent-specific requirements (streaming prose, child orchestration,
approvals) first-class.

What changed from v2, in one line each:

| Change | Standard followed |
|---|---|
| stdout/stderr split: prose+data vs chrome | Unix stream discipline |
| One live status row instead of appended ticks | cargo / docker compose |
| Structured errors `error[CODE]: …` + `help:` | rustc / deno diagnostics |
| Semantic palette via roles + degradation ladder | NO_COLOR / POSIX terminals |
| Markdown rendering contract for answers | claude code / aider norms |
| Canonical truncation phrasing everywhere | gh CLI copy conventions |
| Machine modes (`--json`, exit codes) | every professional CLI |

---

## 0. Streams & machine modes

The single most important professionalism rule — **the Unix split**:

| Stream | Carries | Never carries |
|---|---|---|
| **stdout** | model prose (final answer text), diff bodies, command data output (`/bash`, `/grep`), JSON mode events | spinners, heartbeats, tool headers, warnings, stats, banner |
| **stderr** | ALL chrome: status rows, tool lines, child lines, system/warnings, errors, stats footer | answer prose |

Consequences, free of charge once implemented:

```console
$ wisp -p "explain CD28 co-stimulation" > answer.md     # clean markdown
$ wisp -p "…" 2>/dev/null                                # prose only, no chrome
$ wisp -p "…" --json > events.ndjson                     # automation
```

Machine modes:

- `--json`: one event per line (NDJSON) on stdout, schema identical to the
  WebSocket observability feed (`type`, `timestamp`, payload fields) — one
  contract, two transports.
- Exit codes: `0` turn(s) completed · `1` turn failed · `2` usage/config
  error · `130` cancelled by SIGINT (shell convention).
- `-q/--quiet`: suppress banner and all dim chrome; failures still print.
- Auto-degradation (no flags needed): non-TTY → no animation, minimal
  chrome; `CI=true` → quiet defaults; `TERM=dumb` → plain ASCII.

---

## 1. Color engine

Palette is defined as **semantic roles**, never literal colors — themes can
re-map roles without touching renderers:

| Role | Default (dark bg) | Meaning |
|---|---|---|
| `accent` | cyan 6 | agent acting: tool names, prompt echo, phase marker |
| `ok` | green 2 | success |
| `err` | red 1 | failure |
| `warn` | yellow 3 | degraded-but-working |
| `dim` | bright-black 8 | chrome, meta, durations |
| `prose` | default fg | THE STAR — never tinted |

Detection ladder (checked in order): `NO_COLOR` → none · `FORCE_COLOR` →
force · `TERM=dumb` → none · non-TTY stdout → none · truecolor capable →
24-bit · else 256 · else 16 · else none. Already partially in `colors.py`;
v3 adds the 16-color fallback mapping so themes survive old terminals.

**Never color-only**: every signal pairs a glyph AND a word AND a color —
colorblind-safe by construction, greyscale-safe by construction.

---

## 2. Motion: the single status row

v2 appended heartbeat lines; that spams scrollback on long children and
reads amateur next to cargo/compose. v3 adopts the **one live status row**
convention:

```
⠋ spawn · researcher ⏳ 45s · iter 2                    ← updates IN PLACE
```

Rules:

1. While work runs and nothing else has printed, ONE status row owns the
   bottom edge: spinner frame + primary action + elapsed clock + optional
   child summary. Redraws ≥4 Hz are pointless — update ≤10 Hz.
2. When an event produces a permanent line (child started/done/retry,
   warning, result card), the status row is finalized *above* it and a new
   status row begins below. Scrollback therefore contains exactly the
   meaningful lines — start, retries, outcome — never tick spam.
3. On completion the status row collapses into the outcome lines:
   `✓ [researcher] done in 88s` replaces `⠋ spawn · researcher ⏳ 88s`.
   The elapsed number survives inside the outcome — nothing lost.
4. `--no-motion` (and auto in non-TTY): no spinner frames, no `\r`
   rewriting; state changes print as ordinary lines. Screen readers and
   `tee`-piped sessions get linear truth.

Heartbeat cadence rules carry over (first visible state change ≤5s, then
≤20s refresh of the row's clock) but now they UPDATE the row rather than
append lines.

---

## 3. Diagnostics: rustc-style structured errors

All failures — provider, tool, approval, config — share one format:

```
error[E2103]: web_fetch timed out after 30s
  → https://openreview.net/notes/… (attempt 1/2)
help: raise `tool_timeout` in .wisp/config.toml, or check the URL
```

```
error[E1102]: provider stream stalled for 90s
  → model stealth/ox-alpha @ openrouter (attempt 2/3)
help: transient upstream issue; retrying automatically
```

Format law: `error[CODE]: message` on one line; zero or more `→` context
lines (indented 2); exactly one `help:` line when a hint exists. No
tracebacks on screen unless `--debug` (they go to the log file; the file
path is the last line of a fatal box).

Code registry (ranges reserved, registry lives in `wisp/core/events.py`):

| Range | Domain | Examples |
|---|---|---|
| E11xx | provider/stream | stall, auth, rate-limit, truncation |
| E21xx | tool execution | timeout, danger-block, schema-invalid |
| E31xx | approval/permission | denied, cancelled-by-user |
| E41xx | session/state | corrupt store, disk-full |
| E51xx | child/orchestration | budget exhausted, deadlock guard |

Recoverable ≠ silent: recoverable errors render as `warn[W…]` inline;
only turn-fatal conditions use the `error[...]` block. The v2 double-border
error box is retired — rustc-style lines are calmer and grep-able.

---

## 4. Markdown rendering contract (answers)

Model prose renders with a fixed subset — deterministic across turns:

| Markdown | Terminal treatment |
|---|---|
| `# Heading` | bold, blank line above, no glyph prefix |
| `**bold**` | bold |
| `` `code` `` | tinted accent, no background |
| ``` fenced block ``` | thin border `╭─╮│╰─╯`, lang tag dimmed top-right, syntax highlighting when pygments has the lexer |
| `- list` | `•` at col 0, wrapped continuation indent 2 |
| `1. list` | numbers preserved, same indent rule |
| `[text](url)` | text underlined; url omitted unless it differs materially from text, then ` (url)` dimmed |
| `---` hr | full-width dim `───` rule |
| tables | aligned columns, header underlined with `─` |

Streaming interplay: deltas paint raw until a block boundary arrives
(double-newline or fence close); completed blocks then re-render styled.
In practice this means lists/headings snap into style a beat later than
plain sentences — acceptable, standard among AI CLIs, and invisible at
reading speed.

---

## 5. Diffs — git convention

Unified coloring exactly as `git diff`: `-` red, `+` green, `@@` hunk
headers cyan, context dim. Header line: `diff app.py · +2 −1`. Cap 50
lines with canonical tail (§7). Word-diff within changed lines only when
width allows; accessible mode keeps the existing `[Diff]` label + plain
symbols.

---

## 6. Banner & prompt restraint

```
◆ wisp 0.4.1 · stealth/ox-alpha · ~/Documents/wisp · session a3f9
You › █
```

One line, dim, version-first — no ASCII art, no box. `--quiet` drops it.
Long sessions re-surface it via `/status`. Continuation input uses `⋯ `.
Everything else about interaction (multiline, Esc, type-ahead steering)
follows repl-design.md §5 unchanged.

---

## 7. Canonical vocabulary (copy deck)

One phrasing per concept, used identically in every renderer:

| Concept | Exact string |
|---|---|
| truncated output | `… +{N} lines ({cmd}: /tool {K})` |
| truncated args | `…` |
| elapsed on outcomes | `{name} · {S}s` |
| child done | `✓ [{role}] done in {S}s[ · {M} file{s}]` |
| heartbeat row | `⠋ {action} · {summary} ⏳ {S}s` |
| suppressed dupes | `⚠ ×{N} {first-message}` |
| cancel confirm | `cancelled · partial work kept` |
| compaction | `ℹ context compacted · {N} msgs summarized` |

Consistent copy is what makes a tool feel engineered rather than assembled.

---

## 8. Event→render contract (unchanged core, updated motion)

The §3 table from v2 stands, with these amendments:

- heartbeats/system-ticks → **status-row updates** (§2), not appended lines;
- fatal `error` → structured block (§3), not bordered box;
- content → unchanged (live delta painting, boundary no-dup);
- everything else (thinking preview, tool fast/slow paths, subagent
  lifecycle five kinds, phase bar transitions, steering, background
  notices, stats footer) — unchanged budgets and sources.

Universal `_flush_pending()` gate before any non-content event: unchanged,
still the one structural refactor proposed.

---

## 9. Density budgets ("how much") — carried forward, amended

All v2 §5 caps stand: thinking 60 chars/1 line; args width−12; result card
8 lines/400 chars; diffs 50; child task 60; warnings deduped per unique
text; error context ≤6 lines; whole-turn card budget 200 lines then
header-only mode. Amendments:

| Element | v3 change |
|---|---|
| Heartbeat lines | removed from scrollback (status row instead) — cap now moot |
| Status row | max 1; must exist whenever >2s since last printed line |
| Stats footer | adds error-code count when any fired: `· 2 warn` |

---

## 10. Silence budget — unchanged, extended

v2 §8 list stands verbatim (no hook noise, no retry chatter beyond one ⚠,
no mid-stream token estimates, no empty-result bodies, deduped warnings,
single-line compaction notice). Extensions:

- no spinner-frame logging ever reaches files/scrollback;
- `--json` mode emits engine events ONLY — no human-chrome synthetics;
- banner suppressed on non-TTY entirely.

---

## 11. Implementation status

| Rule | State |
|---|---|
| Live content deltas, boundary no-dup, accessible label | ✅ shipped |
| Heartbeat cadence (as status-row clock) | ◐ logic shipped, row-update pending |
| Spinner EOL clear, bounded cards, `/log`, `/thinking` | ✅ shipped |
| NO_COLOR + non-TTY detection | ✅ shipped |
| stdout/stderr split | ❌ new — largest item; touches transport write sites |
| Status-row renderer (single live row, finalize-on-event) | ❌ new — extends Spinner |
| Structured errors + code registry | ❌ new — events factory + ERROR branch |
| Markdown answer renderer (§4 subset) | ❌ new — pure function in renderer.py |
| Git-standard diff coloring | ◐ partial |
| Copy deck enforcement (§7 strings) | ❌ new — mechanical sweep |
| `--json` NDJSON + exit codes | ❌ new — entry.py wiring |
| Warning dedup, cadence decay, 200-line budget, minute-line | ❌ carried from v2 |

Ordering suggestion: streams split + status row first (they define the
canvas), then error format, then markdown, then the small sweeps. Each is
a renderer-scoped change testable against the four-mode matrix; only the
streams split touches call sites broadly.
