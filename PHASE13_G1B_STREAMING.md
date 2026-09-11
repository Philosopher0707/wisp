# PHASE 13 G1B — PROVIDER STREAMING HONESTY

## Current Defect
G0-NEW-3: mid-stream `ConnectionError` after partial output produced
events `(content, done)` with zero error/status signal. Root causes found
while tracing (three, not one):
1. Guard discarded mid-stream transient errors when payload preceded them
   (`provider_stream.py`: `transient_error` set + `break`, then bare
   `return` under `got_meaningful`).
2. Bare terminal markers counted as meaningful (typed `StreamComplete`
   with empty content set `got_meaningful`), blessing vacuous success.
3. The turn loop emitted `done` unconditionally on tool-less finish —
   even after the guard's honest terminal errors. (Empty-mock turns
   produced bare `done`.)
Additionally the OpenAI adapter forged `done` on bare socket EOF
(unconditional terminal yields after the read loop).

## Completion Contract
`done` (turn) / terminal-marker-consumed (guard) now means: payload
events arrived AND the provider's terminal marker was observed AND no
error/stall intervened. Attempt states: COMPLETE (payload + terminal),
PARTIAL_ERROR (payload + mid-stream error, error yielded, no retry),
TRUNCATED (payload + EOF w/o terminal), TIMED_OUT (stall rules unchanged:
pre-first-byte retries, mid-stream chunk_stall, both pre-existing),
CANCELLED (propagates, pre-existing), MALFORMED (skipped-or-error at
adapter; terminal-less end escalates via TRUNCATED), EMPTY (bare marker →
existing empty-retry → honest terminal error). Retry counts, backoffs,
and policies are byte-identical (verified by diff).

## Provider Matrix
| Provider | Start | Chunks | Terminal (success) | Error | Notes |
|---|---|---|---|---|---|
| OpenAI (+OpenRouter/NVIDIA by inheritance) | HTTP 200 SSE | `data:` JSON deltas | finish chunk or `[DONE]` line (NEW: gated; was unconditional) | `{"type":"error","status"}` or raised transient | malformed lines skipped; cut tool-args accumulate-but-dropped (no finish → no emit) |
| Ollama native client | NDJSON | message/tool lines | `done:true` → StreamComplete | StreamError (immediate surface) | dead self-hash check untouched (P3) |
| Ollama direct fallback | HTTP 200 NDJSON | message/tool_call/content | `{"type":"done"}` on `done:true` | `{"type":"error"}` wrapper | EOF w/o done → no terminal (verified) |
| Mock | scripted | TokenBatch/ToolCallBatch | StreamComplete (always) | n/a | exhausted scripts emit `[mock: no more responses]` (test-only) |

EOF is NEVER success unless the provider's terminal marker was observed.
`done_reason=="length"` stays provider-defined terminal (warning path
unchanged). HTTP 200 starts nothing by itself.

## State Machine
NOT_STARTED → STREAMING → COMPLETE | PARTIAL_ERROR | TRUNCATED |
TIMED_OUT | CANCELLED | MALFORMED(via adapter skip→TRUNCATED/EMPTY) |
EMPTY. Forbidden transitions removed: ERROR→COMPLETE (guard returns after
yielding error), EOF_WITHOUT_TERMINAL→COMPLETE (truncated error),
BARE_MARKER→COMPLETE (not meaningful). Post-terminal bytes/timeouts
cannot retroactively fail a stream (terminal `break`s the read loop —
structural §10 ordering).

## Partial Output
Retained in-flow (content events already yielded; transcript keeps the
prefix — §3 diagnostic requirement). NEVER equated with complete:
every non-complete end yields `error` (code E1102, recoverable, hint) or
the pre-existing `chunk_stall` status, and the turn emits no `done`.
No parallel result model: existing `error`/`provider_status` event types.

## Error Propagation
transport (raise) → adapter (error event or raise) → guard (error event,
no swallow) → turn loop (`provider_failed` flag per iteration) → no
`done`; headless `ok=false` via existing error collection; CLI renders
the error event. Searched: no remaining `return content, True` /
`done=True`-default / `finally`-completion / swallowed-generator paths on
the provider→turn chain (the turn-tail wrap-up `.get` bug is pre-existing,
adjacent, and recorded below — not completion semantics).

## Timeout
Before first chunk: existing empty-retry → terminal error, no done
(pinned by new test). Mid-stream: `chunk_stall` + no done (behavior
change intended by §10; stall test still green). After terminal:
structurally impossible to observe (break). All with pre-existing
deadlines/backoffs untouched.

## Cancellation
Unchanged mechanics (cooperative, re-raised through guard, no auto-retry,
graph untouched). Pinned: mid-stream cancel → CancelledError, no `done`.

## Malformed Data
Adapter skip-and-continue preserved (OpenAI bad JSON lines; Ollama
non-dict/NDJSON tolerance); terminal-less ends escalate to TRUNCATED;
malformed terminal payloads cannot occur (done carries reason only).
`{"_raw"}` salvage path untouched (P1-1 gate owns it; authorize still
sees final args per G0 verification).

## Duplicate Chunks
Pass-through at every layer (no chunk identity ⇒ no safe dedup).
Pinned by contract tests (Ollama duplicate lines surface twice +
terminal). Deduplication recorded as deferred (requires protocol-level
identity; speculative logic refused per §13).

## Retry Boundary
RETRY SEMANTICS UNCHANGED. No new retry, no removed retry, no backoff
touch, no counter change (diff-verified). The only deltas are
classification (what counts as meaningful/terminal/failed) and the
turn-level `done` suppression on failed round-trips.

## Security
Partial output cannot become successful output through streaming
semantics anymore (the representation fix). Authorization path untouched
and re-pinned: salvaged-shape args through `ToolExecutor.execute` still
consult the approval handler; denial blocks the effect
(`test_partial_tool_call_denied_stays_denied`). No auth logic modified.

## Performance
Mock-provider turns, before→after: small 79.0→98.3ms (first-token
78→97ms — thread-spawn noise, run variance), medium 74.5→75.4ms, large
182.4→184.4ms total. Per-event overhead is a set-membership + flag
(noise-level). Correctness dominates; no optimization needed.

## Remaining Unknowns
- Turn-tail wrap-up `ev.get` crash on typed events (pre-existing,
  max-iterations path only; yields Max-iterations error + done — adjacent,
  not fixed: touches exhaustion semantics).
- Durable persistence of error markers (transcript keeps partial content,
  not the error flag — G2 audit dependency per §16 allowance).
- Duplicate-chunk identity (deferred, §13).
- `_raw` salvage execution on truncated tool args (P1-1 gate; now always
  accompanied by an explicit in-flow error — the §15 representation
  requirement — but execution itself is unchanged).
- Live-provider verification (all evidence is harness-scripted; no
  network fault injection against real endpoints in G1B).

## Tests
- `pytest tests/reliability/test_stream_contract.py` — 15 passed (NEW:
  per-provider terminal/error/truncation/malformed/duplicate + turn
  timeout/cancel/tool-then-error + denial regression)
- `pytest tests/reliability/test_provider_faults.py` — 13 passed
  (mid-error + truncation assertions strengthened to fixed contract)
- Provider suites (mock/openai/openrouter/conformance/factory/headless)
  — 118 passed; chunk_stall + core_stateless — 22 passed
- `pytest tests/reliability/` — 46 passed
- Full suite — 5446 passed / 17 failed (G0-known set) / 2 xfailed
- `ruff check` touched files — clean
