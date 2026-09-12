# PHASE 13-H0 — CONTROL LOOP, STEERING & CAPABILITY-GATING FORENSIC AUDIT

Audit-only. No production code modified. Forensic tests:
`tests/test_13h0_forensics.py` (5 tests, deterministic, green).
Baseline: HEAD `6211574`, Python 3.11.15, default AUTO_EDIT.

## 1. Executive summary
The trace is lawful behavior at every layer — but three design gaps
made it nearly inevitable: (a) skills advertise with no filtering and
the `disable-model-invocation` opt-out flag is dead metadata (PROVEN);
(b) nothing classifies task mode, infers read-only intent, or
pre-validates necessity/permission before execution (PROVEN absent);
(c) prohibited tools stay advertised while the system prompt invites
them (13-H/13F.1-A). The fanout denial itself worked exactly as
designed (13F.1: REQUIRE_APPROVAL → prompt → genuine user `n` →
USER_DENIED → fallback). No replay, no bypass, no escalation. Top
severity: P1 (dead skill opt-out + unfiltered advertisement of
repo-mutating instructions); rest P2/P3.

## 2. Incident reconstruction
Prompt (overview + 4 bullets) → Thinking → `skill__setup-
matt-pocock-skills` ✓ (instructions injected, ≤50KB) → user-typed
steering echoes of the 4 bullets → Thinking → fanout proposal (4
tasks verbatim, background, max_concurrent=4) → approval prompt →
user `n` → USER_DENIED → next generation → `list_files` ✓. Cost: 1
wasted iteration + 1 approval interaction + denial context.

## 3. Control-loop call graph
REPL (`repl.py` run_turn + typeahead inbox) → runtime.run_turn →
core.turn (timeout) → `_turn_inner` iterations → provider round →
gate (`ApprovalGate`: policy → REQUIRE → handler) → executor
(authorize → guards → approval → dispatch) → history append →
steering drain → next iteration. Model-controlled: tool/skill/fanout
selection, task decomposition, recovery choice. Host-controlled:
policy, approval, execution, history shape. No host planner
decomposes bullets; no pre-execution necessity check exists anywhere.

## 4. Steering state machine
`inject_steering` (typeahead thread only) → inbox → drained at next
tool boundary → appended as user message + `steering_feedback` event.
Preserves: everything (prompt, history, results, objectives,
counters, budgets, mode, approval state). Resets/replays/
reconstructs: nothing (PROVEN H4 in 13-H + code reading). Cannot
self-trigger (notes are data). Not in retry accounting. The four
`↻ steering · - …` lines are user-typed bullet echoes (the ONLY
producer of steering events is user input — `repl.py:812`,
`entry.py:639`); the bare `↻ steering` is an empty/whitespace note.

## 5. Skill-selection path
Discovery scans workspace + global skill dirs (no flag filtering);
`SkillExtension.tools()` advertises every discovered skill as
`skill__<name>`; the model selects from descriptions; invocation
returns SKILL.md instructions as text (≤50KB into context). The setup
skill matched an architecture-analysis prompt on engineering-setup
wording. Its frontmatter `disable-model-invocation: true` is parsed
NOWHERE (grep-proven) — dead metadata. Invocation needs no repo
knowledge, performs no mutation itself, but injects mutation-
directing instructions (AGENTS.md writes) into a read-only task with
no capability check. Premature activation: YES (before any repo
inspection, unjustified by task).

## 6. Fanout-selection path
The 4 tasks are the model's verbatim decomposition of the 4 bullets
(PROVEN H0-3: executor receives exactly proposed args; host adds
nothing). No host component maps answer sections to executable tasks.
Fanout was "necessary" only in the model's judgment; sequential reads
would have sufficed. Fanout is EXEC-risk delegation (not inherently
mutating; children mode-filtered); AUTO_EDIT gates it via
REQUIRE_APPROVAL (13F.1 — approval, not prohibition). Children were
never spawned (denial precedes dispatch — PROVEN).

## 7. Approval path
Gate: policy REQUIRE → handler (memoized REPL prompt) → user `n` →
USER_DENIED, no execution. Genuine interactive denial (proven by text
shape: not timeout/policy wording; user was present and steering).
Approval occurs AFTER tool selection, INSIDE the gate which precedes
execution — there is no earlier planning-visible policy signal by
design (no preflight API exists; §12). The cycle was spent because
discovery happens at execution time, not because policy was hidden
from a planner (there is no planner on this path).

## 8. Read-only classification path
None exists. Fresh sessions inherit AUTO_EDIT (`WispConfig` +
`SecurityPolicy` defaults, PROVEN H0-4); no intent inference, no task
mode, no capability declaration anywhere on the REPL agent path.
Runtime state at fanout attempt: intent unknown, mode AUTO_EDIT,
capabilities ungated-proposal + per-call enforcement, strategy
model-chosen.

## 9. Strategy selection
100% model-generated on the agent path (no deterministic
strategy selector; the coding-graph gate declined or wasn't reached
for analysis). Inputs: system prompt + tool schemas + transcript.
The selector (the model) sees permission mode NOWHERE (prompt has no
mode section) but sees all tool schemas including prohibited ones.

## 10. Pre-execution validation
NO PRE-EXECUTION ACTION VALIDATION. No host layer checks necessity,
mode-compatibility, cheaper alternatives, or prior work before
execution. The gate checks permission only. Stated explicitly.

## 11. Failure recovery
Denial → structured USER_DENIED → next generation → model chose
`list_files` (fallback = model decision, not deterministic). Cost:
1 iteration, 1 provider round, denial bytes, approval UX. Classified
as capability-mismatch refusal surfaced honestly (not tool failure).

## 12. Exploration-state analysis
NO EXPLICIT EXPLORATION LEDGER (13-H §12; unchanged). Steering
therefore depends on transcript reconstruction — which is lossless
(append-only), so no replay defect; but no ledger means repeated
objectives (bullets as notes + bullets as tasks) are never
recognized as already-covered.

## 13. Duplicate-work analysis
Skill instructions (≤50KB) + 4 bullet echoes + fanout attempt +
denial: all unique-acquisition failures rather than exact duplicates,
except the bullets existing twice (prompt + steering notes). Useful
vs wasted: skill activation wasted (unjustified, context-heavy);
steering echoes user-caused; fanout attempt wasted-but-informative
(taught the constraint); list_files useful.

## 14. Context amplification
Measured structurally (13-H H7): denial + skill body + echoes all
persist and resend every subsequent round. The skill body dominates
(≤50KB vs ~hundreds of bytes elsewhere).

## 15. Authority map
Task mode: NONE (host default only). Skill selection: MODEL (should
be: host-filtered advertisement). Fanout selection: MODEL (correct —
strategy is model's). Approval requirement: POLICY (correct).
Approval result: USER (correct). Exploration strategy: MODEL
(correct). Steering content: USER (correct). Tool execution: HOST
(correct). Only skill advertisement is mis-assigned.

## 16. Security assessment
Invariant holds: model selection grants nothing (per-call gates;
skill invocation is read-like text delivery; instruction-mediated
mutations re-gate per call). The dead opt-out flag is a filtering
defect, not an escalation vector. No new permission/approval/budget/
workspace authority is obtainable through the traced path.

## 17. G1A–G1E compatibility
Unaffected (no prod change; read-only trace). G1E: denial consumed no
budget (consistent). 13F.1 contract behaved exactly as specified end
to end (REQUIRE → prompt → USER_DENIED → continue) — first live
validation of the remediation.

## 18. Phase 13-H dependency
13-H's mechanics (retry math, deadlines, breaker, search, pruning)
stand INDEPENDENT of H0 (verified separately). But 13-I
implementation must order H0 FIRST: H0 controls WHAT gets proposed
(advertisement filtering, prompt guidance, mode surfacing) while 13-H
controls cost AFTER proposal. Fixing resend math before stopping
unjustified proposals optimizes waste instead of preventing it.
Order: H0 → H → I.

## 19. Severity-ranked findings
P1-H01 dead `disable-model-invocation` + unfiltered skill
advertisement (repo-mutating instructions activatable in read-only
tasks; 50KB context injection). MUST FIX (honor the flag; consider
risk-classifying skill tools). P2-H02 no task-mode/intent/preflight
layer (approval discovered only at execution; wasted cycles).
SHOULD FIX (mode surfacing + necessity preflight). P2-H03 system
prompt invites prohibited tools with no mode section. SHOULD FIX
(prompt guidance, 13F.1-A §15a). P3-H04 steering echoes duplicate
prompt content; bare-`↻ steering` opaque. NICE TO HAVE. No P0.

## 20. Phase 13-I scope
(1) Honor skill opt-out + filter advertisement (P1). (2) Surface
permission mode + prohibited-tool list in system prompt (P2). (3) Then
13-H sequence (search build-or-warn → no-progress → resend
accounting). (4) Telemetry taxonomy. Necessity-preflight design after
(1)–(2) prove insufficient.

## Appendix — §24 answers
Q1: Model-selected from unfiltered advertisement (dead opt-out flag);
no repo/capability prerequisite exists. Q2: Model decomposition of
bullets into tasks; host has no such transformation. Q3: By design —
policy is enforced at execution time; no preflight API exists for
planning. Q4: Preserves everything; resets/replays/reconstructs
nothing; user-input-only trigger.
