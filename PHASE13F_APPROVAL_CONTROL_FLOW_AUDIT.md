# PHASE 13F — APPROVAL, DENIAL & CONTROL-FLOW INTEGRITY FORENSIC AUDIT

Audit-only. No production code modified. Forensic tests:
`tests/test_13f_forensics.py` (18 tests, deterministic mocks).
Baseline: HEAD `a6fb8e0`, Python 3.11.15, default AUTO_EDIT mode.
Working tree at audit time also holds uncommitted G1C/D/E deltas and
the tool-identity provenance deltas; findings below do not depend on
them except where flagged [TREE].

Conventions: OBSERVED = seen in transcript/code; PROVEN = pinned by a
forensic test; INFERRED = reasoned, marked; UNSPECIFIED = architecture
is silent.

## 1. Executive summary
The `fanout` block worked as built, and the continuation was the
architecture operating as designed — per-call gating with no
turn-level authority revocation. No privilege-escalation path was
found (no P0). The real findings: (a) PROVEN — user approval at the
gate OVERRIDES a policy denial, so an AUTO_EDIT "block" is really
"ask"; (b) PROVEN — gate and M2-authorize disagree on fanout
(deny vs allow-with-obligation); (c) PROVEN — denial reaches the model
as unstructured text with no machine-readable denial semantics, and
renders identically to ordinary failure (✗); (d) PROVEN — executor
denials are never written to the audit trail (`log_blocked` has zero
production callers); (e) policy block sets omit `spawn_background`
and the thin-harness mutators while blocking `spawn`/`fanout`
(T9 conflation, availability-direction). Highest severity: P1
(ambiguous boundary, §30). No P0.

## 2. Exact reproduction
Turn-level repro (F7, PROVEN): MockProvider proposes `fanout` →
CLI-shaped handler declines → history receives
`[Blocked: AUTO_EDIT mode blocks fanout]` → next iteration proposes
`read_file` → executes → turn completes with `done`. Prompts: 1.
Denials: 1. Mutations after denial: 0. Matches the live transcript
(prompt → ✗ Blocked → thinking → reads).

## 3. Exact call graph
`CLITransport.approve` prompt ← `ApprovalGate.check_decision` ←
`stateless._turn_inner` gate-check ← provider `tool_call` event.
`SecurityPolicy.check` → engine rule `mode.auto_edit_block`
(`policy_engine.py:228`, set at `:259`) denies `fanout` →
handler declines → `(False, "AUTO_EDIT mode blocks fanout")` →
`_refusal_result_event` (plain-string result) → yielded `error_event`
("Blocked: …") + history tool message → loop `continue`s →
next iteration → next provider generation. No turn-stop, no budget,
no mode degradation anywhere on this path (OBSERVED in code).

## 4. Approval state machine
PROMPT → {y: APPROVE(once) | Y: ALLOW-TOOL(session set) |
a: POLICY=AUTO | n: REJECT(once) | N: DENY-TOOL(session set) |
d: POLICY=BLOCK | c: ApprovalCancelled→recorded denial |
timeout/empty/garbage: REJECT (fail-closed)} (PROVEN F-mapping +
`cli.py:836-883`). Scope: per (tool,args) memo per turn; Y/N per
tool NAME (not args); a/d session policy. Transport state is
per-transport-instance (process lifetime), not per-session [TREE-note
carried from prior phase].

## 5. Policy state machine
Mode → rule priority (FULL 0 > READ_ONLY 10 > ASK_ALL 20/21 >
AUTO_EDIT 30 > catch-all 1000). AUTO_EDIT denies exactly
`{run_bash, git_branch, git_commit, git_push, gh_pr_create, spawn,
fanout}`; everything else allowed (PROVEN F5 matrix). M2 `authorize()`
independently maps EXEC/NETWORK/PRIVILEGED → approval obligation in
AUTO_EDIT (never denial). The two layers can and do disagree (F4).

## 6. Tool lifecycle state machine
PROPOSED → GATE(policy allow→pass; policy deny→prompt) →
{APPROVED | DENIED | CANCELLED(via ApprovalCancelled) |
TIMEOUT(→deny)} → EXECUTOR(authorize deny→Denied result;
guards→Blocked; approval→prompt-or-memo; dispatch) →
{SUCCESS | ERROR | TIMEOUT | CANCELLED-propagates} → history →
MODEL_CONTINUATION (unconditional). Denial is never terminal for the
turn (PROVEN F7). Cancellation (real CancelledError/KeyboardInterrupt)
propagates untouched at every catch site (OBSERVED).

## 7. Model continuation state machine
Every iteration: model may propose ANY tool; each proposal is
independently gated. Denial changes nothing about the next
iteration's authority. Continuation is HOST-loop-driven but
MODEL-content-directed: the host decides THAT generation continues;
the model decides WHAT to propose. There is no host "denial budget",
no read-only degradation, no mutation quarantine (all UNSPECIFIED =
absent, not merely undocumented).

## 8. Authority matrix
| Event | Model may continue? | Reads | Mutating | Network | Shell | Fanout | Why |
|---|---|---|---|---|---|---|---|
| approval granted | yes | yes | yes(prompted once) | yes | yes | yes | per-call allow |
| approval denied | yes | yes | re-prompt per turn | yes | no(policy) | re-prompt per turn | denial is per-call, non-terminal (PROVEN) |
| policy denied | yes | yes | re-prompt per turn | yes | no(policy) | re-prompt per turn | same as above; prompt precedes the block |
| approval timeout | yes (deny) | yes | re-prompt | yes | no | re-prompt | timeout≡deny, fail-closed (OBSERVED `cli.py:841`) |
| tool exception | yes | yes | yes | yes | gated | gated | ordinary error result |
| tool timeout | yes | yes | yes | yes | gated | gated | structured error, ID kept |
| cancellation (real) | no | no | no | no | no | no | propagates, turn ends |
| budget exhausted | summary only | no | no | no | no | no | wrap-up generation, no tools |

## 9. Denial → alternative-action matrix
fanout→read_file: allowed, legitimate (PROVEN live shape). fanout→
search/edit/bash/network/another-fanout: each independently gated;
bash stays policy-blocked (PROVEN F10). edit→read: allowed,
legitimate. edit(denied)→bash: blocked (PROVEN). bash(denied)→edit:
re-prompts (user decides). network→alternate-network: both
NETWORK-risk, both prompt — no silent route. No case found where a
denied capability is reachable WITHOUT a fresh approval prompt or a
policy allow — EXCEPT the F3 override (policy deny + user approve =
run), which is user-authorized by construction.

## 10. AUTO_EDIT/fanout analysis
`fanout` is EXEC-risk (delegation), blocked by mode rule alongside
`run_bash`/git-writes. INFERRED intent: deny autonomous
concurrency+mutation without a human. But: (a) `spawn_background`
(EXEC, same delegation class) is NOT in the block set — PROVEN F5;
(b) children inherit mode and get blocked tools filtered from schemas
(`filter_allowed_for_mode`), so a read-only fanout COULD be safe —
over-blocking, availability-direction (P3); (c) the "block" is
overridable by one `y` at the gate (F3) while the message says
"blocks" — the word is inaccurate when a human is present (P2/UX).
Verdict: conflation is REAL but fail-closed in direction; no
escalation demonstrated. Intent: UNSPECIFIED (no comment/design doc
found).

## 11. Tool-result protocol analysis
Denial enters history as bare string `[Blocked: …]` (PROVEN F2) —
no `status` envelope, no `authorized:false`, no `executed:false`, no
`retryable:false`, no `continuation` hint. The identity-provenance
machinery (IDs, pairing, preflight) is orthogonal and intact [TREE].
Loss: control information the model needs to distinguish "refused"
from "failed". Severity P2 (T10).

## 12. Retry interaction
Policy/approval denial triggers NO executor retry, consumes NO G1E
budget (pre-execution), is invisible to provider/graph retry layers
(OBSERVED). Model regeneration = new proposal, re-gated every turn
(PROVEN F8: same-turn memo suppresses re-prompt; next turn prompts
again). Indefinite re-prompting across turns is possible (annoyance,
not escalation — each round trips a human). Subagent layer:
denied/cancelled results never retry (OBSERVED
`subagent_orchestrator.py:1102,1558,1594` + `_DENIAL_MARKERS`).

## 13. Graph interaction
Approval nodes: explicit-`True`-only grant; denial → CANCELLED;
audited (`graph.approval_denied/granted`); denied sinks → run
CANCELLED, never SUCCESS (OBSERVED `graph/executor.py:430-500`).
No denial→SUCCESS laundering found; G1C terminality untouched
(no prod change; G1C suite green).

## 14. Subagent interaction
Parent denial happens before dispatch — children never queued
(PROVEN F6 pattern at executor level). No approval handler exists in
child context, so gated tools fail closed there. Denial markers stop
orchestrator retries (OBSERVED). No parent→child escape found.

## 15. Concurrency/approval race analysis
Verdicts attach per (tool,args)→result-ID correctly under
concurrency (PROVEN F9: approve-A/deny-B, execution exactly once,
IDs paired). CLI serializes live prompts under `_approval_lock`
(OBSERVED). Approval identity binds (name,args) per turn + result
`tool_call_id` per call; no `tool_call_id` on the prompt object
itself — confusion would require prompt interleaving, which the lock
prevents (INFERRED sound; live-race test out of scope).

## 16. Resume analysis
Denials persist as ordinary tool text; no PENDING/approval state is
persisted (transport + runtime approval states are in-memory).
PROVEN F11: resume continues without resurrecting the denied call.
DENIED→PENDING impossible by construction (no such transition
exists). Restart clears Y/a (re-prompt = fail-closed direction).

## 17. Audit trail analysis
Policy allow+deny → `SecurityPolicy._audit` (OBSERVED). Graph
approval verdicts → audited (OBSERVED). Approvals-on-execution →
`log_auto/explicit_approved` (OBSERVED). GAP (PROVEN by zero-caller
grep): `AuditLog.log_blocked` exists but has NO production callers —
executor-level denials/blocks (user `n`, declines, guard blocks) are
never written; only tests call it. Continuation decisions are never
recorded anywhere. Severity P2 (forensic completeness).

## 18. Threat model findings
T1 accidental continuation: PROVEN present, harmless (reads only in
the observed run). T2 retry laundering: possible as re-prompt
annoyance; no auto-retry, no budget evasion — P3. T3 substitution:
no silent path found — every alternative re-prompts or is
policy-blocked — NO FINDING. T4 semantic bypass: manual sequential
equivalent of denied fanout is user-visible reads/approved writes —
legitimate, NO FINDING. T5 approval confusion: memo keyed
(name,args); Y per tool-name is explicit persistence — NO FINDING
beyond the documented coarseness. T6 concurrent confusion: PROVEN
absent at executor level. T7 resume escalation: PROVEN absent. T8
subagent escape: none found. T9 conflation: PROVEN present,
fail-closed direction — P2/P3. T10 deception: PROVEN — denial ≡
failure in UI and model context — P2.

## 19. Severity classification
No P0. P1 (1): approval-override/policy-disagreement boundary —
a "policy block" the user can unknowingly lift with one `y`, with the
model unable to tell policy-denial from user-denial afterward.
P2 (3): unstructured denial semantics; audit gap on denials;
misleading "blocks" wording + refusal-text ambiguity. P3 (2):
AUTO_EDIT over-blocking read-only fanout; cross-turn re-prompt
annoyance. All else: no finding.

## 20. Existing tests reviewed
`test_q22_audit_trail` (log_blocked contract, caller absent),
`test_runtime_tool_history` (persist pairing), `test_toolchain_e2e`
(approval wiring), `test_no_bypass`, approval/risk unit tests,
G1B/C/D/E suites (green, §28 intact).

## 21. New forensic tests
18 tests in `tests/test_13f_forensics.py`, all passing: policy matrix
(8), refusal shape, override proof, M2 disagreement, executor
no-execution-on-decline, turn-level live repro, cross-turn re-gating,
concurrency isolation, substitution boundary, resume.

## 22. Metrics
See `phase13f_metrics.json`. Headline: denial→continuation rate 1.0
(by design, per-call gating); denial→mutation-without-fresh-approval
rate 0.0 (PROVEN across matrix); denial→equivalent-capability-silent
rate 0.0; repeated-attempt prompts 1/turn (unbounded turns);
approval-override-of-policy rate 1.0 when user approves (by design).

## 23. Recommended remediation (NOT implemented)
R1 (P1): decide and document whether gate approval may override
policy denial; if yes, reword "blocks"→"requires approval" and emit
structured `POLICY_OVERRIDDEN_BY_APPROVAL` history markers; if no,
make gate denials non-overridable. R2 (P2): structured denial
envelope `{status, authorized, executed, retryable}` for refusals.
R3 (P2): call `log_blocked` on every executor denial path. R4
(P2/P3): reconcile `spawn_background`/thin-tool vs `spawn`/`fanout`
block sets, or document the distinction. R5 (P3): optional denial
counter → escalate repeated identical denials to turn-stop. All R1–R5
are small, localized, independently testable; regression risk low
(R2 changes model-visible text — golden tests may need updates).

## 24. Explicit unresolved questions
(a) Intended semantic of AUTO_EDIT vs orchestration tools —
UNSPECIFIED in code/docs. (b) Whether turn-level denial budgets are
desired — UNSPECIFIED. (c) Whether Y-persistence should be
per-session rather than per-transport — flagged, not decided.
(d) Live concurrent-prompt race — mitigated by lock, not live-tested.

## 25. Final verdict
Expected behavior with accurately-scoped defects: the fanout block
held, continuation used only retained (read) authority, and no
denial→mutation composition exists without fresh user approval.
The P1 is the boundary's AMBIGUITY (overridable "blocks"), not a
bypass. Remediation is clarification + structured semantics + audit
coverage — no emergency, no architecture redesign.

## Appendix — §31 answers
1. Per-call gating has no turn-level revocation; the loop continued
   to the next iteration. 2. Intentional architecture, UNSPECIFIED
   for the denial case specifically. 3. Policy-denied (AUTO_EDIT
   rule); user input (decline/timeout) merely declined to override.
   4. Debatable — the prompt offered an override the policy wording
   disclaims; see R1. 5. Full proposal authority; per-call
   enforcement unchanged. 6. No (PROVEN matrix). 7. Only as
   per-turn re-prompts, never silently. 8. Only user-authorized
   equivalents. 9. No (children never queued; no handler in child
   context). 10. No (PROVEN F11). 11. No (PROVEN F9 + prompt lock).
   12. Yes, fail-closed direction (spawn_background/thin tools
   omitted; read-only fanout over-blocked). 13. Mere error text
   (PROVEN F2). 14. Arguably nowhere: reads-after-denial is
   legitimate task continuation; the defect is semantic, not
   terminal. 15. R1 (decision + wording + marker). 16. Code: R2/R3
   (small); R1/R4 are policy/UX clarification first.
