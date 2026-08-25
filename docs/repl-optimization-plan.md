# Wisp CLI/REPL Optimization Plan

Status: proposed — every item below is backed by a measurement taken on this
checkout, including the hypotheses that were measured and killed.

## Measured baseline (this machine, warm cache unless noted)

| Probe | Result | Verdict |
|---|---|---|
| `import wisp.entry` cold / warm | 0.64s / 0.19s | optimize |
| …of which `requests` stack (via tools.registry→web) | ~73ms | optimize |
| …of which `textual` (via transport/__init__→tui) | ~29ms | optimize |
| `display_width()` per 100KB string | 6.3ms | optimize |
| `wrap_text_wide()` on 2KB paragraph | 7.2ms | optimize |
| Full tool output wrapped BEFORE 30-line cap | O(n) per result | optimize |
| SQLite `save_session` per turn (400KB history) | 0.04–0.7ms | ruled out |
| Content-event render pipeline (500 events) | 1.1ms | ruled out |
| `_estimate_tokens` on 800KB history ×50 | 0.3ms | ruled out |
| Spinner/wait-clock/typeahead timer ticks | ≤8 wakeups/s total | ruled out |
| Readline history persistence | already exists | no action |

The dominant user-visible costs are (1) launch time and (2) rendering
latency spikes on large tool outputs. Provider round-trip time dominates
turns themselves and is already guarded (first-token deadline, retries).

## P1 — Lazy heavy imports (launch −50%)

`requests` and `textual` are imported on every CLI launch but used only by
web tools and `/tui` respectively.

- `wisp/transport/__init__.py`: replace eager `from .tui import TUITransport`
  with a PEP 562 module `__getattr__` lazy export.
- `wisp/entry.py`: move the TUI import inside `run_mode("tui")`.
- `wisp/tools/registry.py`: keep TOOL_SCHEMAS static; resolve web tool impls
  lazily (import `wisp.tools.web` inside the two handlers or via a thin
  deferring callable).

Gain: warm launch ~0.19s → ~0.09s (cold −100ms). Zero behavioral change.
Tests: web tools still execute after lazy resolution; `python -c
"import wisp.entry"` asserts `textual` and `requests` absent from
`sys.modules`.

## P2 — Truncate before wrap (bounded rendering)

`_render_tool_result` wraps the entire tool output, then keeps 30 lines. A
200KB minified-JSON result pays full-wrap cost to discard everything past
line 30 — repeated per tool call.

- Pre-cap input: `output_str[: _MAX_SHOW * inner_w * 3]` (×3 covers
  worst-case wide-char blowup) before `wrap_text_wide`, keeping the
  existing `… +N more lines` tail semantics.
- Visible output identical for anything under the cap; pathological inputs
  drop from unbounded to ~30-line cost.

Tests: 5MB single-line result renders in bounded time with exactly the
30-line + tail shape; normal-size outputs stay byte-identical against the
golden transcripts.

## P3 — ASCII fast path in `display_width`

99% of rendered text is ASCII, yet every call walks chars through wcwidth.

- Top of `display_width`: `if text.isascii(): return len(text)` — exact,
  since ASCII implies width-1 glyphs.

Gain: 6.3ms → ~0.05ms per 100KB; speeds up wrapping, padding, truncation
everywhere (renderer, terminal_width, diff title shortening, spinner).
Tests: parity assertions ASCII vs CJK vs emoji vs zero-width; goldens must
stay byte-identical.

## P4 — Pygments lexer cache

`render_diff_box` calls `get_lexer_by_name(language)` per diff. Wrap in
`functools.lru_cache(maxsize=32)`. Free win during multi-edit turns.

## Milestones

- **O1 = P1 + P3** (import laziness + width fast path): independent,
  immediately measurable, goldens prove no visual drift.
- **O2 = P2 + P4**: bounded rendering under adversarial outputs.

Each milestone lands gated (pytest + mypy strict + ruff) with before/after
numbers in the commit message. Success criteria: warm launch ≤0.10s, 5MB
tool result renders <50ms, zero golden diffs, full suite green.
