# PHASE 13-H — PROVIDER DEGRADATION & AMPLIFICATION FORENSIC AUDIT

Audit-only. No production code modified. Forensic tests:
`tests/test_13h_forensics.py` (7 tests, deterministic, green).
Baseline: HEAD `6211574`, Python 3.11.15.

## 1. Executive summary
Both incidents are bounded, honest, and architecturally explicable —
no runaway, no bypass, no silent corruption. Incident A (~83 tools,
~8m48s, HTTP 500): ordinary per-iteration cost × no denial/stall of
the loop, terminated by honest provider failure + budgets. Incident B
(steering + rereads): user typeahead notes appended at tool
boundaries; no state is rebuilt, replayed, or reset — rereads are
model behavior, structurally incentivized by context pruning that
condenses older reads. Sharpest findings: (a) nothing ever builds the
semantic index, so `search_codebase` false-negatives by construction
(P1 — drove the manual-read fallback); (b) prohibited tools stay
advertised AND invited by the system prompt (13F.1-A); (c) retries
resend full context every attempt with no Retry-After handling;
(d) a true global turn deadline EXISTS (30-min asyncio timeout) —
nested layers cannot outlive it. Highest severity: P1 (search).

## 2. Incident A reconstruction
Read-only survey → N model generations, each 1 provider round (≤3
stream attempts × ≤3 transport POSTs, full-context resend each) +
tool time + growing-context model latency → transport timeouts retried
honestly → empty streams retried (≤3) → HTTP 500s counted per round →
breaker opens after 5 consecutive failed rounds → honest terminal
failure, no mutation. ~528s / 83 tools ≈ 6.4s per tool-round: fully
explained by model latency on ~67k context + reads; no exotic cause
required. Counts (live, estimated): generations ≈83, tool executions
83, provider rounds ≈83–90, stream attempts ≤~100, transport POSTs
≤~150, logical (G1E) retries 0.

## 3. Incident B reconstruction
`↻ steering` = user typeahead lines drained at tool boundaries
(`repl.py` → `inject_steering` → `_turn_inner` append). Proven (test
H4): the note appends exactly once; nothing re-executes, resets, or
replays; the turn continues on budget. Repeated reads of
providers.rs/workers.rs/eval files are therefore MODEL re-requests,
not host replay. Contributing structure (test H7): the pruner
condenses tool payloads beyond the retention window, so a model whose
working set exceeds the window must re-read full content — duplicate
bytes with a legitimate cause. Causal link to the live duplicates:
INFERRED (consistent, not proven without the live transcript).

## 4. Execution call graph
REPL → runtime.run_turn → core.turn (30-min timeout) → per-iteration:
prune → `_guarded_provider_stream` [breaker → guard(≤3) →
`generate_stream_events` → hardened_post(≤3)] → gate → executor →
history append → steering drain → next iteration. No cycles between
layers; retries nest strictly downward.

## 5. Retry graph
| Layer | Owner | Trigger | Max | Backoff | Counts against |
|---|---|---|---|---|---|
| iteration repair | stateless | transient EXCEPTION | 2 | 1s,2s | iteration budget |
| stream attempts | guard | empty/429/5xx pre-output | 3 | 0–4.5s+jitter | nothing further |
| transport POST | hardened_post | write-timeout/reset/RPRE | 3 | exp+jitter | nothing further |
| turn iterations | turn loop | any non-terminal outcome | 50 | — | turn budget |
| turn timeout | asyncio.timeout | wall-clock | 1800s | — | absolute |
Worst case per round: 9 POSTs. Per turn: 50 rounds → ≤450 POSTs, hard-
capped by the 1800s timeout. G1E logical budget is orthogonal (task
retries only).

## 6. Wall-clock accounting
T_total ≈ Σ(model latency × generations) + Σ(tool time) +
Σ(backoffs ≤ ~5s/round) + Σ(transport time). Backoff is negligible
(seconds per round); model latency on large context dominates (the
8m48s). No discrepancy class found; per-attempt timestamps exist only
in logs (not asserted here).

## 7. Deadline analysis
A TRUE global per-turn deadline EXISTS: `asyncio.timeout(turn_timeout)`
(default 1800s) wraps the whole turn; CancelledError propagates
through guard, provider, and executor untouched. Retries do NOT
inherit remaining time explicitly — they don't need to: cancellation
kills sleeps, sockets (via generator close), and threads abandonment.
Steering cannot reset it; provider/transport retries cannot outlive
it. Answers: §28-Q20 YES; Q21 NO.

## 8. Provider/transport forensics
Connect 15s / read 120s (write folded into read 60s per comment);
stream first-token 90s / chunk 90s; 429 + ≥500 retry as transient;
NO Retry-After handling (grep-proven absent); backoff linear-ish +
jitter, no 429-specific shaping. One generation CAN cause up to 9
complete full-context POSTs. With ~67k context each resend is the
dominant cost driver under degradation.

## 9. Circuit breaker
Always constructed (defaults 5/2/30s); wraps the whole provider round
OUTSIDE guard retries (`breaker.stream(_call_provider)`): counts
round outcomes AFTER nested retries exhaust (5 consecutive failed
rounds to OPEN, not 5 attempts). Recovery 30s → half-open. Honest
`circuit_open` UX events. Trips after, not before, nested retries —
correct placement for thundering-herd avoidance, slow to react to a
single bad round (by design).

## 10. Streaming (G1B intact)
Terminal = payload + marker; bare marker/early-EOF/mid-stream error =
explicit error events, never silent success (honesty preserved under
all nested paths — no code path contradicts it). Stream failure does
NOT cause a new provider request by itself (returns); only the outer
empty-attempt retry does, resending full context, consuming neither
logical nor G1E budget (invisible except logs/telemetry counters).

## 11. Steering state machine
`inject_steering` (any thread) → inbox → drained at next tool
boundary → appended as user message + feedback event → next generation
sees it. No prompt/history/tool/task reconstruction; no counter or
budget touched; cannot recurse (notes are data, not triggers); no
depth/count cap (bounded only by turn budget). Host-generated steering
does not exist — all steering is user typeahead.

## 12. Exploration ledger
NO DURABLE EXPLORATION LEDGER for the model's working set. The only
read-tracking (`_touched_files`) feeds post-turn memory summaries
(trimmed to 10 files), never the next generation's decisions. The
agent relies entirely on conversational context (+ pruner summaries).

## 13. Duplicate-work analysis (Incident B pattern)
Taxonomy applied to the reported files: exact rerereads of same
file/range after steering intervals. Legitimate-reread causes
present: pruner condensation (proven H7) + steering refocus. No
evidence of host-driven replay (H4). Duplicate/total ratio:
UNMEASURABLE without the live transcript — marked unknown, not
estimated.

## 14. Context amplification
Measured structurally: 8 × 500-line tool results condense on prune
(H7: bytes shrink, count preserved). Per-round provider payload grows
with unique work; retries resend the full payload (H1: 3 attempts, 3
full resends). Steering does NOT reinsert old results. Causal chain
(§14) holds EXCEPT the final arrow is model-driven reread, not host
reconstruction: UNIQUE → GROWTH → (prune condenses) → MODEL REREAD →
DUPLICATE → LARGER REQUEST. Partially demonstrated, live portion
inferred.

## 15. Exploration progress
Bounds: max_iterations (50/turn), turn_timeout (1800s), context prune
ceilings (8KB/tool, 200KB payload, last-3-full). NO per-task tool cap,
NO steering cap, NO no-progress detector, NO repeat-tool/query
detector, NO cost/token budget. All existing budgets are turn-scoped
and never reset mid-turn (nothing resets them — verified).

## 16. No-progress detection
Does not exist. Same-state/same-question/same-tool cycles are
indistinguishable from productive work at every layer. Incident B
permits arbitrarily many (bounded only by §15 budgets). Recorded as
P2 gap (no-progress blindness), not implemented per phase rules.

## 17. Search false negative
`tool_search_codebase` → embedding search over `.wisp/
semantic_index.db`. PROVEN (test H5): on an empty index it returns []
and the tool reports "No semantically relevant code found" —
indistinguishable from a genuine negative, with no index-state caveat.
PROVEN: nothing in production ever builds the index (no callers of
index_all/index_file outside the module). Unless an external process
built it, EVERY search false-negatives by construction — directly
explaining fallback to exhaustive manual reads. Severity P1
(advertised capability structurally unbuilt; misleading negative).
Also requires Ollama + numpy at query time (failure → "error" text).

## 18. Fanout/AUTO_EDIT
Covered in 13F/13F.1: EXEC-risk delegation primitive, conservative
over-block, now REQUIRE_APPROVAL with child mode-filtering. Blocking
a read-only survey's fanout is availability-direction (P3), unchanged
by this audit; it plausibly pushed Incident A toward sequential reads
(contributory, not causal).

## 19. Tool-summary accounting
"83 tools (83 ok, 1 failed)" counts `tool_result` EVENTS only
(ProgressTracker: succeeded/failed via shared predicate; denials
count as failed). NOT counted: generations, provider/stream/
transport attempts, retries, steering ops, duplicates. The UI
compresses five quantities into one — P3 observability note with a
concrete taxonomy proposal (§22 metrics).

## 20. G1A–G1E regression assessment
G1A journal: untouched by these paths. G1B honesty: intact under
nesting (§10). G1C terminality: graph layer uninvolved in both
incidents. G1D salvage: read-only incidents, no salvage. G1E retry
bound: holds AND is correctly distinguished — G1E bounds LOGICAL
task retries; it never claimed to bound physical attempts or wall-
clock; those are bounded by stream/transport limits + iteration
budget + the 1800s timeout. No invalidation. Critical distinction
verified, not just asserted.

## 21. Security/authority assessment
No amplification path touches authority: approvals and policy evaluate
per proposal with no state that steering/retry/pruning resets;
budgets only shrink authority (time/iterations), never expand it;
workspace containment is per-call. No escalation vector found.

## 22. Work amplification graph
Logical Task(1) → Agent Loop(≤50 iter) → per-iter: Tools(1–N results)
+ Steering(0–N appends, no rebuild) + Model(1 generation) → Provider
round(1) → Stream attempts(≤3) → Transport POSTs(≤3 each, full-context
resend) → Backoff(≤~5s). Measured multiplicities in tests: H1 3/3,
H2 3/1, H3 5 tools/6 generations. Worst-case bounds in §5.

## 23. Stopping boundaries
Incident A should have stopped — and DID — at honest provider failure
+ turn completion; no earlier principled boundary exists for "this
survey is too expensive" (NO ENFORCED cost/work STOP BOUNDARY —
stated explicitly). Per-round wastage (9 full-context POSTs) has no
dedicated bound either; only the 1800s cap. Incident B should have
recognized no-progress at the second identical post-steering reread —
NO SUCH MECHANISM EXISTS (stated explicitly). Missing boundaries:
(1) per-turn unique-work/duplicate-work signal, (2) no-progress
detector, (3) cost/token budget. None implemented (out of scope).

## 24. Severity-ranked findings
P1-H1 search index never built → authoritative false negatives →
manual-read fallback. MUST FIX (build-or-warn: index on demand or
label negatives "index empty"). P2-H2 no-progress blindness (loops
bounded only accidentally by budgets). SHOULD FIX. P2-H3 full-context
resend ×9/round, no Retry-After (cost under degradation). SHOULD FIX
(Retry-After + backoff shaping; resend is inherent to stateless HTTP).
P2-H4 advertised-but-prohibited tools + inviting prompt (13F.1-A
cause #1/#3). SHOULD FIX (prompt guidance; schema-hiding optional).
P3-H5 summary compresses 5 quantities. NICE TO HAVE (telemetry
taxonomy). P3-H6 read-only fanout over-block pushes sequential reads.
NICE TO HAVE. No P0.

## 25. Unknowns / Phase 13-I proposal
Unknowns: live per-round latency split; live duplicate ratio; live-
model sensitivity to denial/negative phrasing; Ollama index-build
cost at scale. 13-I sequence: (1) search build-or-warn (P1);
(2) no-progress detector scoped to exact/overlap rereads (P2);
(3) Retry-After + resend accounting (P2); (4) prompt guidance +
optional mode-filtered schemas (P2); (5) telemetry taxonomy (P3).
Each independently testable; no redox of G1A–G1E required.
