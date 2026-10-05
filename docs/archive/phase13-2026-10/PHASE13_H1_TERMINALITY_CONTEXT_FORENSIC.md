# PHASE 13-H1 — Terminality, Context Explosion & Final-Answer Delivery Forensic Audit

AUDIT ONLY. No production file modified. Evidence: code refs + arithmetic + one
in-memory pruner probe (no live trace exists; live-unknowables are `null`).

## 1. Incident reconstruction

Turn 6 ran ~1505s, emitted 817 tool events (801 ok / 18 failed), changed 0 files,
printed a 122-line thinking preview (`"We need now craft final answer..."`), printed
`Turn 6 · 817 tools (801 ok, 18 failed) · 0 files · 25m 5s · ctx 970k (388%)`, and
returned to prompt with no final answer. User typed `tell me now`.

Loop (stateless.py): `turn()` wraps `_turn_inner()` in `asyncio.timeout(turn_timeout)`
(:211,282). `_turn_inner` runs `for iteration in range(max_iterations)` (:325); each
iteration pre-prunes, streams one provider round (:352), executes all pending tool
calls (:698-741), appends assistant+tool messages (:763-787). Exit paths: content-only
round → `done_event` (:687-688); non-complete round with no tool calls → bare `return`
(:643-648, **no done event**); `max_iterations` → tool-less wrap-up + `done` (:803-844);
timeout → error+done (:289-296).

## 2. Exact final 20-event sequence

Unknown — no session event log/telemetry dump was captured. Reconstructed
best-fit tail from code + transcript order (thinking preview, then stats, no answer,
no error box): `... tool_result ×N → thinking deltas ("craft final answer", 122 lines
buffered) → provider_status chunk_stall | error (sets provider_failed, :366-372,
yielded live) → has_tool_calls False → bare return (:643-648) → runtime finally
persists full-fidelity history (runtime.py:528) → `on_turn_stats` prints stats
(repl.py:653-654 runs even with no done) → prompt returns`. Items 2/13 carry this
as hypothesis H1 with discriminators, not fact.

## 3. Agent iteration accounting

`max_iterations` default 50 (config.py:637-639). 817 tools / ≤50 iterations ⇒
**≥16.3 tools/iteration average** — legal: one generation may carry many parallel
`tool_call`s, all executed in the iteration (:698-741). No host cap on calls/round
found. Exact iteration count: null (no log). 1505s/50 = 30.1s/iteration — consistent
with budget exhaustion, but equally consistent with fewer iterations + slow rounds.

## 4. Tool accounting

817 = `ProgressTracker.tools_run`, incremented per `tool_call` event (progress.py:121-126);
801/18 = per-`tool_result` success/fail classification via shared `result_is_error`
(:192-203). **Gap: 801+18=819 ≠ 817** — two calls never produced a classified result
(interrupted mid-execute → persisted placeholder `[no result recorded before turn
ended]`, runtime.py:112; or silent terminal path). Unique tools/files, duplicates,
reads/searches/fanout split: null (transcript shows dominant `read_file`; no log).
817 counts logical calls; provider/stream retries (≤3/round, stateless.py:1947) and
transport POST retries are separate layers, not tool events. Subagent-internal tools
do not surface as parent `tool_call`s — 817 is a parent-level floor, not total work.

## 5. Provider accounting

Per model generation: 1 `_guarded_provider_stream` (≤3 stream attempts, 90s
first-token + 90s chunk deadlines, :1912-1947) × transport POST retries (≤3,
transport.py:602-634). Rounds/generations/attempts for the incident: all null.
No payload-size logging exists at any dispatch site (pre-prune :344-349, safety-net
:868, wrap-up :821-825) — per-round bytes are unrecoverable post hoc.

## 6. Context accounting

`ctx 970k (388%)` = `render_turn_stats` (renderer.py:388-394): `ctx_tokens` from
`adapter._estimate_tokens(session messages)` (entry.py:179) = **chars//4 over RAW
persisted messages** (cli.py:579-581); `k = tokens/1024`; `pct = tokens/limit`.
970×1024 = **993,280 est. tokens**; 993280/256000 = **388.0% exact**. It is current
persisted-state size (full-fidelity history incl. all 817 tool results), not a
cumulative sum — though its value reflects cumulative retained work (~3.97 MB chars,
≈4.8 KB/result avg). It is **not** the provider payload (see §7/8).

## 7. Provider payload size

Unknown (null) for every round — never logged. Bounded analysis: per-round dispatch
is pruned (:344-349) under `max_total_bytes=200000` aspiration + 50KB/recent +
assistant `tool_calls` blocks (never pruned, :542-544) + system prompt. Probe: 817×
10KB read results → pruned **408,501 B, exceeding the 200KB ceiling**. So peak live
payload was large (~100-150k est. tokens) but far below 970k. Whether any request
exceeded the model window: **unknown** (model id + window not captured).

## 8. Pruner analysis

Trigger: every iteration pre-dispatch + safety-net in `_stream_events_async` + wrap-up
(:344-349, 868, 821-825). Keeps last 3 tool results full; historical read/list→2KB
condense; generic historical→8KB; total ceiling 200KB (context_pruner.py:70-99).
Failures: silent fallthrough to raw messages on exception (:347-349, 869-870) — **pruning
can fail open to unbounded payload**. Ceiling enforcement degrades to
`budget_per_tool = max(500, total/N)` (:594) — for N=817 the floor is 500 B × 817 =
~408KB: **the "ceiling" scales linearly past N>400, not constant**. Summaries are
per-message (~100-500 B markers) and do not themselves accumulate beyond that floor.
`prune_live_session` (runtime.py:371-376, context_manager.py) runs **pre-turn only**,
never intra-turn, and resolves tool names via `message.get("name")` which persisted
tool messages lack (runtime.py:113-117) — so history gets the generic 8KB path, not
the 2KB read_file path. Verdict: effective provider context is **linearly bounded in
tool count (≈500 B/tool + unpruned assistant blocks), not constant-bounded**.

## 9. Context-window discrepancy analysis

Header `256k` = `DEFAULT_MAX_CONTEXT_TOKENS = 256000` (config.py:26), the configured
`ctx_limit`. `970k` = measured `ctx_tokens` (§6). No discrepancy in code — one is the
ruler, one is the measurement. The only true discrepancy is interpretive: the UI
presents an **unpruned-history estimate** beside a **model-window limit**, inviting the
false inference that a 970k-token request was sent. It was not (§7).

## 10. Final-answer terminality analysis

Terminality = loop condition only: `not has_tool_calls` (+ verification pass + round
complete) → `done_event` (stateless.py:641-688). There is **no** stop-reason check, no
explicit final event, no parser, no provider marker, no answer state — `is_final` is
just `type in (done, error)` (events.py:88-90). **A generation containing valid final
text IS followed by more tool execution whenever the same generation also carries
tool calls** (content accumulates to `partial_content` while calls execute, :375-377,
690-741) — and a content-only round is still overridden by a verification rejection
(:654-672) or steering injection (:789-801). **NO ENFORCED FINAL-ANSWER TERMINALITY.**
`NO HOST-LEVEL READY_TO_ANSWER STATE` — "craft final answer" is model prose with zero
host effect (§4/§19 answer: **B/C boundary — reasoning followed by more tool calls or
a new round; text alone proves nothing**).

## 11. Model/provider final-generation analysis

Unknown which of: success / timeout / partial / malformed / empty / cancelled /
never-attempted (null). G1B integration: error or `chunk_stall` mid-round sets
`provider_failed`; with no tool calls the turn ends via **silent bare return** —
partial content retained only in diagnostics, `done=True` deliberately withheld
(:643-648). An incomplete response is therefore never marked successful; it is
instead **indistinguishable from success downstream** (runtime.py:463 sets
`turn_succeeded=True` on any exhaustion, §15).

## 12. Output-delivery analysis

Path: provider event → `_normalize_event` (:1951) → live `yield` (:576) → runtime
re-yield (runtime.py:427) → `renderer.render_event` (repl.py:639) → CLI buffers
(cli.py:1500-1522) → flush on boundary/done (:_flush_content). **Generated-but-
not-emitted is possible at two boundaries**: (a) G1B bare return — buffered thinking/
content has no terminal flush trigger except the already-rendered stall notice
(§16); DONE-branch flushing (:1563-1571) never runs; (b) iteration wrap-up ignores
non-content event types (:826-834) — a disobedient tool-calling wrap-up is silently
dropped, then reported only as a generic budget error. Failure boundary for H1: (a).

## 13. 122-line thinking analysis

Thinking is provider output, **buffered, never streamed live** (`_buffer_thinking`,
cli.py:1215-1218; rendered only at `_flush_thinking`, :1298-1346). With
`show_thinking=false` the full 122 lines collapse to one preview line
(`Thinking: "…" (122 lines — /thinking to expand)`, :1332-1335). 122 lines = a large
reasoning generation, possibly truncated by the stall it precedes — but the flush
itself is normal rendering, consumes nothing, and cannot alter terminal parsing
(thinking/content buffers are independent; content flushes thinking first, :1509-1510).
Thinking is not causal; it is the last visible artifact of a dying round.

## 14. Exploration amplification

tools/generation, tools/minute, unique/duplicate ratios: null without the log (817
tools / 1505s = **0.54 tools/s, 1.84 s/tool** — consistent with fast local reads, not
provider-bound). 0 files changed ⇒ read-only exploration; `VerificationFloorGuard`
never engaged (`wrote_code=False`, verification.py:101). Near-end novelty vs
repetition: unknown from transcript (only `✓ read_file` tails visible).

## 15. No-progress analysis

`NO NO-PROGRESS DETECTOR` confirmed and extended: `max_reflections` (default 3,
config.py:650-653) is **validated but never enforced** — zero call sites wire it;
`render_done_reason` handles a `max_reflections` reason (renderer.py:176-177,190)
that nothing emits (dead path). Classification of this incident's repetitiveness:
**unknown** (same-tool/same-path/same-range evidence requires the log). The
architecture permits unbounded duplicate reads; the pruner even incentivizes rereads
by condensing the history the model would otherwise reuse.

## 16. Deadline analysis

1505s < 1800s default, so the default watchdog did not fire. Candidates: **H1** G1B
bare return (any time, silent); **H2** iteration-budget wrap-up at ~50×30s (error or
summary + done); **H3** a configured `turn_timeout` <1800 (env `WISP_TURN_TIMEOUT`,
min 10s, config.py:640-643 — a 1500s setting ends the turn at exactly this wall
time with an `error[turn_timeout]` box). H3/H2 both emit visible terminal events the
incident report does not mention; H1 emits none — best fit, unproven.

## 17. G1A–G1E compatibility

G1A journal: intact (persist-everything finally, runtime.py:481-557). G1B streaming:
contract behaved as designed — non-complete marked, done withheld; the defect is the
**silent exit** it leaves, not a contract violation. G1C graph: uninvolved (read-only,
no delegation). G1D salvage: partial content retained in diagnostics only, never
surfaced — honest but invisible. G1E retry: stream/transport retries bounded
(3/3); no retry-storm evidence (1.84s/tool contradicts backoff-heavy looping).

## 18. Security/authority assessment

No bypass signal: 0 files changed, read-only tool dominance, approval/role gates
inline on every call path (stateless.py:429-534), workspace/graph/authorization
untouched by all terminal paths. Reliability/control-loop defect only. G1A–G1E: no
violation found (§17).

## 19. Root cause

The turn loop has **model-sovereign continuation**: the only thing that ends a turn
with an answer is the model emitting a tool-free, complete round. Host-side bounds
(iterations, timeouts, stall guards) all terminate the turn **without delivering an
answer** (silent return, budget error, or timeout error), and the context meter
measures **unpruned retained history**, so extreme-but-bounded provider traffic
(~16 calls/round × 50 rounds) presents as "970k context" while never tripping a
window error. The model's "craft final answer" reasoning is prose, not a state
transition — the subsequent round stalled/failed (H1) or exhausted budget (H2), and
the host had no READY_TO_ANSWER invariant to force delivery of what was already
reasoned. 817 tools = ~16 parallel reads/round × ~50 rounds of a model that never
chose to stop; 25m5s = that work's natural duration, not a deadline firing.

## 20. Severity

**P1** — a valid/completed research task consumed ~25 min / ~800+ tool calls and
failed to deliver its result, requiring user re-prompt. Not P0 (no incorrect
execution, loss, or safety breach). Not P2 (delivery actually failed, not merely
slow). The silent-terminal path additionally borders P3-observability, but the
delivery failure governs.

## 21. Exact stopping boundary

Unknown terminal event (null); stopping *boundary* with evidence: **the host control
loop has no boundary that converts final-answer intent into delivery**. Candidate
code sites, ranked: (1) stateless.py:643-648 bare `return` — add terminal signal;
(2) stateless.py:826-834 wrap-up filter — surface dropped tool-calls; (3) runtime.py:463
`turn_succeeded=True` on silent exhaustion — distinguish non-complete ends;
(4) entry.py:179 meter — label/measure pruned payload alongside history.

## 22. Recommended remediation (NOT implemented)

Host-enforced terminality: treat sustained final-intent + stalled round as deliver-
partial, not silent-return; always yield a terminal event (done or error) on every
exhaust path; wire `max_reflections` or an explicit no-progress detector; meter both
raw history and pruned payload; log per-round payload bytes + terminal reason.

## Proposed next phase

PHASE 13-H2 — determinism harness: per-round structured trace (iteration, payload
bytes est., stream attempts, terminal reason) persisted per turn + failing-first
tests pinning (a) bare return emits a terminal event, (b) pruned-bytes ceiling holds
for N≥1000 tools, (c) meter labels history vs payload. Then implement §22.

---

## Final summary

```text
ROOT CAUSE
Model-sovereign continuation with no host-level READY_TO_ANSWER invariant: the turn
ends with an answer only if the model emits a complete tool-free round. 817 reads
(~16/round x ~50 rounds) kept rounds tool-bearing; the final round went non-complete
after "craft final answer" reasoning (best fit: chunk stall -> G1B bare return,
stateless.py:643-648) and the host ended the turn with no terminal event and no answer.

FINAL ANSWER GENERATED?
null (reasoning preview is not a response; round payload unlogged)

FINAL ANSWER DELIVERED?
false (user had to re-prompt; stats line printed with no answer)

ACTUAL CONTEXT SIZE
~993k estimated tokens of retained persisted history (chars//4, unpruned), ~3.97 MB;
per-round provider payload was pruned (est. peak order ~100-150k tokens, unknown exact)
and never 970k in one request.

817 TOOLS EXPLAINED?
yes (structurally): <=50 iterations x uncapped parallel tool_calls/round (avg 16.3);
2-event gap (819 results vs 817 calls) consistent with result-less calls on the silent path.

25m5s TERMINATION REASON
null definitively; ranked: H1 silent G1B bare return (best fit) > H2 iteration-budget
wrap-up (~50x30s) > H3 configured turn_timeout<1800s. Default 1800s watchdog did not fire.

MISSING CONTROL BOUNDARY
Host-enforced final-answer terminality: FINAL INTENT + COMPLETE-ENOUGH ROUND =>
TURN TERMINATES WITH DELIVERY. Today: NO ENFORCED FINAL-ANSWER TERMINALITY,
NO HOST-LEVEL READY_TO_ANSWER STATE, silent non-complete return, unwired
max_reflections (no-progress detector absent).

SEVERITY
P1

NEXT PHASE
13-H2 determinism harness (per-round trace + terminal-event + pruner-ceiling + meter
tests), then remediate per section 22.
```
