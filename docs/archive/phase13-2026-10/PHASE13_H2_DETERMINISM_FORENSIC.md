# PHASE 13-H2 — Determinism, Terminality & Context-Accounting Forensic Harness

AUDIT ONLY. Zero production files modified (`git diff` clean apart from one
pre-existing foreign 1-line change in `wisp/tools/registry.py`, untouched).
Harness: `tests/reliability/test_13h2_determinism.py` — **39/39 pass in ~9.5s**,
ruff F-clean. Regression matrix: **184 passed, 0 failed**. All live-unknowables
are `null` in `phase13_h2_metrics.json`.

## 1. Executive Summary

H1's P1 is **reproduced deterministically**: a tool round followed by a thinking
round that stalls ends the turn with **zero terminal events** (no `done`, no
`error`) while the stall notice and thinking preview render normally — the exact
incident signature. Two new defects pinned: (a) **bookkeeping-set mismatch (P1)** —
`"complete" ∈ TERMINAL_TYPES` but `∉ _BOOKKEEPING_TYPES`, so a bare terminal marker
counts as "meaningful" and an empty stream ends `done(reason=natural)` with **empty
content** (silent empty success); (b) **wrap-up object-blindness (P2)** — the
budget wrap-up consumes raw provider events via `ev.get`, so object-shaped streams
raise `AttributeError` inside the wrap-up (honest E5101 error+done, observed live
in logs). Pruner ceiling proven non-constant: `pruned(N) = 500·N + 2` for N≥500,
breaks above N=400, verified through N=2000. Meter confirmed as raw-history
`chars//4`; synthetic 817×4.8KB reads reproduce the incident shape (991,198 /
387% vs 993,280 / 388%). `max_reflections` proven dead (2 refs, 0 call sites).
Silent ends are recorded repo-complete (`was_last_turn_complete == True`).

## 2. Scope / Audit-Only Boundary

Allowed: new test file, metrics, report, test-only pokes (breaker `_state`,
env knobs, monkeypatched pruner faults). None of §1's MUST-NOT list was touched:
no READY_TO_ANSWER, no retry/G1B/G1C/auth/executor/workspace/prompt changes, no
detector, no `max_reflections` wiring, no pruner redesign, no salvage. Defects
recorded, none fixed.

## 3. Test Environment

`.venv` python 3.12, pytest 9.0.2, `-p no:cacheprovider`; hermetic HOME via
`tests/reliability/conftest.py`. Real `WispAgentCore.turn` + real
`AgentRuntime.run_turn`; fakes only at provider boundary (`MockProvider`,
dict-shaped `_DictProvider` mirroring `providers/openai.py:280-356`,
`_RecordingMock` payload tap). Real `read_file`/`write_file` on `tmp_path`.
No network, no sleeps for ordering (only condition-wait on first event in the
cancel test and sub-second stall/timeout deadlines as the subject itself).

## 4. Exit-Path Matrix

18 paths enumerated (subagent traces, §2 of build). Terminal behavior pinned live
except where noted:

| Exit path | done | error | answer | stats* | observable reason |
|---|---|---|---|---|---|
| normal content-only | 1 | 0 | yes | yes | `done(natural)` |
| G1B bare return (stall/fail, no tools) | 0 | 0 | no | yes | **none — the defect** |
| stream exception (non-transient) | 0 | 1 | no | yes | `Provider stream failed` |
| protocol-integrity violation | 0 | 1 | no | yes | static row (unreachable via public path — intake mints IDs) |
| runtime exception | 0 | 1 | no | no | `Turn aborted` |
| cancelled | 0 | 0 | no | no | task cancellation |
| turn timeout | 1 | 1 | no | yes | `Turn timed out after Ns` |
| iteration wrap-up ok (dict provider) | 1 | 0 | yes | yes | budget notice + summary |
| iteration wrap-up fail (object provider) | 1 | 1 | no | yes | `Max iterations reached` |
| verification rejection | — | — | — | — | non-exit: `Verification loop` nudge, exactly 2 then finish (5 writes measured) |
| steering drain | — | — | — | — | non-exit: `steering_*` + extra round |
| circuit-open round | 0 | 1† | no | yes | `circuit_open` status (†round-level error, turn still silent) |
| empty dict stream | 0 | 1 | no | yes | `no usable response` |
| empty object stream | 1 | 0 | **empty** | yes | `done(natural)` — NEW P1 §5 |
| malformed (unknown event, no marker) | 0 | 1 | no | yes | `without its terminal marker` |
| immediate transient, attempts=1 | 0 | 1 | no | yes | `after 1 attempt` |

\*stats = `on_turn_stats` fires (repl.py:653-654 runs on any non-exception return).

## 5. G1B Bare-Return Reproduction

`TestBareReturn`: tool round → final round emits thinking ("craft final answer"
analogue) then hangs; 0.2s chunk deadline fires `chunk_stall`. Captured: thinking
produced ✓, `chunk_stall` reported ✓, tools executed ✓, `done` absent ✓, `error`
absent ✓, `ProgressTracker` stats computable (1 run / 1 ok) ✓. **H1-H1 confirmed
as deterministic mechanism.** (Live incident terminal remains `null` — mechanism,
not history, is proven.)

## 6. Final Content vs Thinking

C1 thinking-only+fail: thinking live, no content, error diag, no done. C2
thinking+content+fail: **partial content streams live but never completes** —
rendered-but-undelivered. C3 thinking+content+tools: tools run, turn continues to
done — content non-terminal. C4 clean: single done. C5 content+tool same
generation: tool executes after valid text — **model prose never terminates**.
No heuristic added (§22 respected).

## 7. Terminal Event Uniqueness

D1 success: exactly 1 done, 0 error. D2/D4/D6: at most 1 done everywhere observed;
error+done pairs occur only on timeout and wrap-up-fail. D3 cancel: zero events.
D5 stall: **zero final events** (`done`/`error` both absent; only non-final
`provider_status`). D7: persist precedes terminal observation at runtime layer
(see §17).

## 8. Terminal Reason Mapping

`provider_failed=true ∧ turn_succeeded=true` **confirmed live** (N test):
`runtime.py:463` sets success on any generator exhaustion including the silent
path, and `_persist_turn_state` writes repo-DONE unconditionally on it. Mapping:
§4 table = code path → event → runtime → renderer → user-visible. Contradictions
filed: (i) silent end recorded complete; (ii) empty object stream recorded
`done(natural)` with empty content; (iii) `render_done_reason` handles
`max_reflections`, nothing emits it.

## 9. Pruner Ceiling Results

Sweep (10KB synthetic reads; N, raw B, pruned B): 10/100512/34446 · 50/502592/59006
· 100/1005192/89706 · 200/2010492/151206 · 400/4021092/200002 · 500/5026392/250002
· 817/8213193/408502 · 1000/10052892/500002 · 1500/15079892/750002 ·
2000/20106892/1000002. **Law: pruned(N) = 500·N + 2 for N ≥ 500.** Holds ≤400,
breaks ≥401 (`500·401 = 200500 > 200000`). H1's ~408KB probe confirmed byte-exact
(408502). The configured "total ceiling" is a per-tool floor beyond N=400.

## 10. Pruner Failure-Open Results

`prune_messages` raising (pre-flight + safety-net, stateless.py:345-349,867-870)
and `enforce_byte_ceiling` raising both: turn completes normally with **raw
history on the wire** (recorded payload ≥ raw bytes). Fails open by design
(never break the turn on accounting); consequence quantified: at incident scale
the fallback ships ~8.2MB instead of ~0.4MB (~20×). Unchanged.

## 11. History vs Provider Payload

N, est tokens, % of 256k, pruned B: 10/12128/5%/18839 · 100/121298/47%/74009 ·
500/606598/237%/250002 · 817/991198/387%/408502 · 1000/1213223/474%/500002.
Meter == `chars//4` exactly (asserted). Labels: RETAINED_HISTORY (meter),
PROVIDER_PAYLOAD (pruned, always smaller), CONTEXT_LIMIT (256k). Meter cannot
report payload size → UNKNOWN in production; separated only in harness.

## 12. Per-Round Accounting

4-round scripted turn: 4 provider calls, payload strictly growing first→last,
3/3 tool results, 1 done. Transport POST count unobservable from core events →
`null` (counting would need transport-layer patching; deferred to H3).

## 13. 817-Tool Structural Reproduction

50 iterations × (49×16 + 1×33) = **817/817 calls/results**, 51 provider calls
(50 + wrap-up attempt), widest round (33) fully executed → **no host per-round
cap at 33**. Loop reaches budget naturally; object-shaped wrap-up fails honestly
(E5101+done, `AttributeError: 'TokenBatch' object has no attribute 'get'`
observed in logs). 24 unique paths → duplicates dominate, matching a reread-heavy
incident. Payload grows monotonically. Structural only; no timing asserts.

## 14. `max_reflections` Enforcement

Codesearch: only `wisp/config.py` (schema/init/validation) and
`wisp/transport/renderer.py` (dead reason strings). **Validated + rendered,
never read, never enforced** — dead knob. Behavioral: 6 identical read rounds
run to normal completion with no reflection signal. NOT wired (per scope).

## 15. No-Progress Signal Inventory

From the 6-identical-round turn, extractable now: same tool+args streak (6/6),
same-path repetition, `files_changed == []`, zero mutations, round count. All
available from `(events, session)`, none persisted first-class, none consumed.
No detector built (per scope).

## 16. Final-Answer Delivery Boundaries

Renderer-level pins: thinking alone writes **nothing** (buffered,
generated-not-delivered); stall/error flushes the 122-line preview verbatim
(`122 lines … /thinking to expand` asserted); content streams live
pre-terminal; done flushes the answer. Generated-but-undelivered boundaries:
(i) thinking buffer pre-boundary, (ii) live content of a failed round
(rendered, never completed), (iii) wrap-up dropping non-content events,
(iv) bare return skipping the DONE flush.

## 17. Success-Honesty Results

Runtime-level: silent-end turn → no `done` in stream, yet
`was_last_turn_complete == True` and session persisted (user message only —
**the thinking fragment is not even persisted**). Clean turn → done + complete.
`turn_succeeded` means "generator exhausted", not "answer delivered". Documented,
unmodified.

## 18. G1A–G1E Regression Results

184 passed, 0 failed: `tests/reliability/` (incl. 39 new), graph terminality,
salvage gate, retry integrity, 13h/13h0 forensics, journal recovery, security
policy. No G1A/B/C/D/E or authorization regression. Audit-only confirmed.

## 19. Findings

1. H1 P1 stands, now deterministic (P1). 2. Empty-stream silent success (P1,
new). 3. Wrap-up object-blindness (P2, new). 4. Pruner ceiling linear past
N=400 (P2 observability/bound honesty; P1-adjacent at incident scale via
fail-open 20×). 5. Meter/payload conflation (P3 UX, P2 for ops). 6. Success
flag dishonest on silent ends (P1 family, same fix as #1). 7. `max_reflections`
dead (P3). No safety/authority findings (read-only repros, gates inline).

## 20. Severity

H1 P1 **retained**. New P1: silent empty `done(natural)`. New P2: wrap-up
blindness, pruner-ceiling honesty. P3: meter labeling, dead knob. No P0.

## 21. Exact Remediation Candidates

Ranked by correctness/risk (G1B/G1E/security interplay in parens):
1. **A — terminal-event closure** (every exhaust path yields exactly one
terminal classification; fixes §5 + §17-core; low risk; touches only exit
edges, G1B semantics kept). 2. **C — failure-aware final-content handling**
(define deliver-partial vs drop for C2; interacts with G1B honesty + G1D
salvage; needs care). 3. **G — round-level observability** (persist terminal
reason + numeric accounting; near-zero risk; prerequisite evidence for B/D).
4. **E — real context accounting** (three separate meter values; UI-only +
payload tap; cheap). 5. **F — hard pruner ceiling** (constant bound w.r.t. N;
watch G1E payload-retry interplay). 6. **B — host answer-candidate state**
(biggest design surface; do after A+C+G). 7. **D — no-progress guard**
(novelty-based; needs G's accounting first; keep out of H3 unless trivial).

## 22. Unknowns

Live incident terminal event (no log existed — mechanism proven, history not);
per-round transport POST counts; unique/duplicate file split of the incident;
model id + true window (payload-vs-window overflow unjudgeable); provider-side
latency breakdown of the 1505s.

## 23. Recommendation for 13-H3

Order: A → C → G → E, then F, then B, then D. H3 scope: implement A+C with the
H2 harness as the failing-first gate (bare-return test must observe a terminal
event; empty-object test must not observe empty `done`), add payload/reason
persistence (G), keep everything else pinned.

```text
VERDICT: PASS WITH KNOWN UNKNOWNs
NEXT PHASE: 13-H3 — terminal-event closure (A) + failure-aware final-content (C), gated by the H2 harness, then G/E observability.
```
