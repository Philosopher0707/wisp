# PHASE 13F.1-A — REPEATED POLICY-DENIAL LOOP DIAGNOSIS

Audit-only. No production code modified. Forensic tests:
`tests/test_13f1a_forensics.py` (7 tests, deterministic mocks, green).
Baseline: HEAD `6211574` (phase13-stack commit), Python 3.11.15.

## 1. Exact live reproduction (as reported)
A `Failed validating 'required' in schema:` error (preview-truncated,
`… +16 more`), followed by six identical `✗ run_bash / Policy denied:
run_bash — AUTO_EDIT mode blocks run_bash` boxes.

## 2. Schema-error sequence
"Failed validating 'required'…" is jsonschema-library phrasing; it
appears NOWHERE verbatim in the codebase. The only in-repo jsonschema
call is arg validation (`stateless._validate_tool_args`), which wraps
it as `Schema validation failed for tool …`. PROVEN ordering (test
A3): the policy pre-check runs BEFORE schema validation, so an
argless PROHIBITED call yields POLICY_DENIED, never a schema error.
Therefore the live schema error came from a DIFFERENT call/layer than
the six run_bash denials — most plausibly an allowed tool with
malformed args, or an MCP/external validator. Precise first-line
origin: UNRESOLVED from code alone (paste may start mid-block; the
REPL preview truncates long lines). It is a separate preceding event,
not the loop's engine.

## 3. Model-generation sequence (proven, test A1)
Mock provider proposing run_bash every round for 4 rounds → exactly 4
POLICY_DENIED results, 4 distinct non-empty IDs, provider consulted
exactly 5 times (4 rounds + wrap-up). Denials == generations: no host
reissue, no provider/transport retry inflates the count.

## 4. Tool-proposal sequence
Generation N proposes run_bash → denial N with that generation's ID →
history append → generation N+1 proposes run_bash again. Each cycle is
one full provider round-trip.

## 5. Policy evaluations
One per proposal, stateless, no memory: the gate re-evaluates every
proposal independently and denies identically each time. Denials write
no "do not ask again" state (memo is per-turn and only suppresses
re-PROMPTS for identical REQUIRE_APPROVAL calls, not DENY decisions).

## 6. Denial results
Structured envelopes: `{status: POLICY_DENIED, authorized: false,
executed: false, retryable: false, reason}` with correct per-generation
IDs (test A6 asserts all fields on persisted history).

## 7. Tool-schema visibility
PROVEN (test A5 + code): `_get_tool_schemas` advertises ALL tools with
no mode filtering — `run_bash`, `fanout`, `git_push` remain visible in
AUTO_EDIT. Only subagent-role `allowed_tools` and the opt-in
thin-harness posture filter advertisement. Enforcement is
execution-side only. Additionally the system prompt actively invites
`run_bash` ("run bash commands", "check it with run_bash", "run the
test suite with run_bash" — `context_assembler.py`) with no
mode-conditional guidance: the model is told to use the tool it will
be denied.

## 8. History/message representation
Each denial persists as its own JSON tool message paired to its
assistant block (test A6). The transcript DOES distinguish six
denials (six envelope messages, six boxes) — observability at the
event level is intact; only aggregate summaries ("N tools") compress
them.

## 9. Retry-budget interaction
Denials consume nothing: they return before dispatch, instantiate no
task, touch no G1E counter (turns of pure denial complete with zero
retry events — tests A1/A2). Model regeneration is a new proposal,
not a logical retry. Correct per the 13F.1 contract; consequence: no
budget bounds the repetition.

## 10. Provider/transport interaction
No provider-level retry: POLICY_DENIED is not a provider error, so
the guarded stream never retries it (provider call count ==
generations, test A1). No transport reissue.

## 11. Termination conditions
PROVEN (test A2, max_iterations=3 → exactly 3 denials + wrap-up +
done): the iteration budget ends the loop. Also effective:
turn_timeout exception, genuine user cancellation (S10), mode switch.
There is NO denial-aware terminator — with default max_iterations=50
the loop can run ~50 denied rounds per turn.

## 12. Repeated-denial classification
Model behavior (A: proven) meeting host-control absence: the host
re-presents the prohibited tool every round and treats each denial
exactly like a failure (no status branching anywhere post-result —
verified by code reading of the turn loop). Not host retry (B),
not provider retry (C).

## 13. Severity: P2
No bypass, no escalation (P0/P1 excluded by evidence). Poorly bounded
(up to ~50 wasted provider rounds by default) with API-cost and
attention-noise impact — P2 per rubric, bordering P3 for short
budgets. Six denials in the live run is consistent with a fixated
model inside one turn budget, not an infinite loop.

## 14. Root cause
Three jointly-sufficient causes, no single defect: (1) execution-side-
only enforcement — prohibited tools stay advertised (code design);
(2) turn loop has no denial-state transition — POLICY_DENIED flows
through the identical continue path as any result (code design,
intentional per 13F.1 §8); (3) model-side fixation — system prompt
invites run_bash unconditionally and the envelope's `retryable: false`
is advisory text the model may ignore (model behavior, UNKNOWN
sensitivity to structured vs plain denial without live-model E2E).

## 15. Minimum remediation (NOT implemented)
Smallest combination honoring the audit: (a) mode-conditional system-
prompt guidance (tell the model which tools the mode prohibits —
instruction, not mechanism); (b) consider hiding hard-DENY tools from
advertised schemas per mode (mechanism; needs headless/autonomous
impact check — background children rely on full schemas); (c) R5
revisited ONLY as a bounded "N identical denials → summarize and
stop proposing that tool" (not a turn-stop). Recommend (a) first;
(b)/(c) need their own scoped phases.

## 16. Unresolved questions
(a) Exact first-line origin of the live schema error (mid-paste vs
external validator). (b) Whether live-model behavior differs between
plain-text and structured denial (UNKNOWN by construction — mocks
cannot model semantics). (c) Whether R5 should be reconsidered —
yes, as a scoped proposal, not as 3-denials-stop: the evidence shows
a bounded-but-wasteful loop the budget only accidentally terminates.

## Appendix — §20 answers
1. Six model generations each independently proposed run_bash; no
   host/provider reissue (A1). 2. Six generations (denials ==
   generations, distinct IDs). 3. No — separate preceding event from
   another call/layer; policy precedes schema validation (A3). 4. Yes
   — no mode filtering at advertisement (A5). 5. Yes — full envelope
   JSON per denial with paired IDs (A6). 6. Yes — identical continue
   path, no status branching (code reading). 7. No — zero budget
   contact (A1/A2). 8. No — max_iterations ends it (A2); ~50 rounds
   by default. 9. Iteration budget, turn timeout, user cancel. 10. Yes
   — git_push and fanout paths verified (A4; fanout declined =
   USER_DENIED). 11. Model behavior inside host-designed absence of a
   denial transition. 12. Reliability/UX (P2), not security. 13. Mode-
   conditional prompt guidance first (§15). 14. Reconsidered as a
   scoped proposal, not implemented here per the hard rule.
