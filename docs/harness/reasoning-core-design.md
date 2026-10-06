# Wisp reasoning core: design (draft for review)

Status: **design only, no code.** Written 2026-10-07 from a verified read of the tree (`feat/invariant-gates` @ `a5dd436`). Every fact in §2-§3
was checked against the code or a probe; §13 lists what was *not* verified. Decisions that are the owner's are in §12.

## 1. Summary

**What it is.** A pure, deterministic arbiter for the decisions Wisp makes *inside a turn*, between one model call and the next: is the last
step evidence of progress, is this claim supported, has this exact failure happened before, can the next request be paid for, may this turn
end. The model proposes; the core decides whether the evidence supports continuing, repairing, finishing or stopping, and records why.

**What it is not.** Not a planner that writes plans. Not a second goal, acceptance or recovery authority: the repository already has one
of each (§2) and the core *consumes* them. It never calls a model, and a model's prose is never an input to a decision (it is data to be
*checked*).

**The finding that shapes the design.** Most of a "reasoning core" already exists in pieces and is **not connected to the way wisp is used**:
the objective-level loop (`convergence.py`, `wisp converge`) runs only from its own command, and six turn-boundary authorities are OFF by
default. What is genuinely missing is smaller and sharper: **a record of what is actually known, and a check of what the model says against
it.** That is where this session's real failures came from (§3).

## 2. What exists (verified 2026-10-07)

| Layer | Module | Answers | Wired into a normal `wisp repl` turn? |
|---|---|---|---|
| L3 objective loop | `core/convergence.py` (2,044 lines), `autonomous.py`, `wisp converge` | "turn ended: is the objective met, what next?" | **No.** Only `wisp converge` runs it. Every `run_turn` caller (REPL, SDK, headless, server, TUI, benchmark) dispatches exactly one turn (ADR-0045 §1) |
| L2 turn boundary | `acceptance.py` (PASS / FAIL / INCONCLUSIVE with `Evidence`), `goal.py` (`GoalState`, `TerminalOutcome`), `recovery.py` (`FailureClass` ×10, `RecoveryRung` ×7, `RecoveryLadder`), `progress.py`, `stagnation.py`, `task_graph.py` | completion, failure naming, next rung, progress | **Mostly off.** `recovery_ladder`, `goal_state`, `stagnation_gate`, `turn_criteria_source`, `acceptance_gate`, `task_graph` default **OFF** (`CURRENT_FLAGS.md`); `goal_state` and `recovery_ladder` only *record* |
| L1 inside a turn | `verification.py` (`VerificationFloorGuard`), `announced_step.py` (wired at `stateless.py:892`), stagnation and declared-criteria nudges, the floor's short-repeat nudge (`stateless.py:788`; it de-duplicates identical *nudges*, it does not detect repeated failures) | may the turn end now? | Verification floor **ON**; announced-step ON; all of these act **only at the `done` boundary**, and the stagnation and declared-criteria nudges are OFF |
| Gates (new, unmerged) | `core/gates/` | is this call allowed; is this result safe to show | committed on `feat/invariant-gates`; its full-suite verification was still running when this was written |
| Outcome judging | `wisp/judge/` | did the run SOLVE / GAME / FAIL a task; was the claim honest | Offline tool, needs a live model |
| Closed vocabularies | `TerminalOutcome` {SUCCEEDED, FAILED, INCOMPLETE}; `GoalState` {GOAL_MET, GOAL_UNVERIFIED, GOAL_STAGNATED, GOAL_FAILED, ESCALATED_TO_HUMAN, CANCELLED}; `FailureClass` {TRANSIENT, TOOL, IMPLEMENTATION, VERIFICATION, DEPENDENCY, ENVIRONMENT, INVALID_ASSUMPTION, REPEATED, STAGNATION, SECURITY} | the words every decision must use | n/a |

Two consequences. (1) A new "fifth authority" would be wrong: the project has already paid, repeatedly, for the shape *"complete, tested, unreachable"*
(`convergence.py` calls it out by name). (2) The honest first move is **record and observe**, not enforce.

## 3. The gap, from Wisp's own failures

Everything here happened in a real session and has evidence in the repo (PR numbers are unmerged, `AGENTS_LEARNING.md` has the detail).

| # | What went wrong | Why no existing control caught it | Needs |
|---|---|---|---|
| F1 | `cmd \| tail` exit 0 read as success; a passing `echo` counted as "verified" (the floor accepted any passing `run_bash`) | the floor trusted the shell's status, not what ran | evidence that says *what* was observed (gates PR already fixed the verifier; the ledger generalises it) |
| F2 | A packaged app "worked" because startup checks passed; the feature routes were never called | no record separating "launched" from "the thing the user runs does what I claimed" | claims checked against observed facts |
| F3 | A control (the injection guard) was inert on the real event shape while its tests were green | tests built the shape the code expected | evidence provenance: a fact is OBSERVED only if the harness produced it from a real event |
| F4 | Final text claiming success with no run behind it | `announced_step` catches *announcing* the next step, nothing audits a *claim* | **claim audit** |
| F5 | The same failing command retried with variations, and a refused command rephrased | stagnation works on diff hashes at the turn boundary and is OFF; `FailureClass.REPEATED` exists but is consulted only at the boundary | tool-level repeat detection, bounded |
| F6 | `max_tokens=4096` sent to a key that could afford 83; the provider said so and the turn died on a billing error | affordability is a **static cap** (`providers/openai.py:587`, explicitly "an AFFORDABILITY policy"), never a decision informed by the provider's own answer | learned ceiling, one bounded retry, honest stop |
| F7 | Wisp reported state from memory (stale workspace notes, a doc naming a directory that does not exist) | nothing stamps a fact with how it was obtained | facts carry source and freshness |

A search of `wisp/` for success-claim handling and for expected-vs-observed step comparison found neither outside `convergence.py` (which deliberately ignores prose); the search used
keyword patterns, so treat "none found" as strong evidence, not proof.

## 4. Definition and layering

```
L3  objective loop      convergence.py / wisp converge            exists, separate entry
L2  turn boundary       acceptance, goal, recovery, progress      exists, mostly OFF
L1  INSIDE A TURN       reasoning core (this document)            <- the gap
    ledger -> claim audit -> decide -> record
L0  provider / tools    streaming, gates, executor
```

The core lives at L1 and has exactly four jobs: **remember** (evidence ledger), **check** (claims against the ledger), **decide** (a pure function
returning one of a closed set of actions), **record** (every decision with its evidence ids). L2 and L3 are *clients and consumers*: the core hands
them better evidence and reuses their vocabularies; it does not replace them.

### 4.1 The evidence ledger (`reasoning/ledger.py`)

A `Fact` has: `id`, `kind` (closed set: `TOOL_RESULT`, `GATE_DECISION`, `VERIFICATION_RUN`, `FILE_MUTATION`, `PROBE`, `PROVIDER_ERROR`), `status`
(`OBSERVED`, `INFERRED`, `ASSUMED`, `UNKNOWN`: the same four words as `CLAUDE.md` §1.2), `source` (a tool-call id or event id), `content_hash`, `step`, and
`stale_after` (the mutation index that invalidates it). `OBSERVED` can only be created from an engine event that carries a source id; there is no
constructor that takes model text (invariant RC8). Verification facts become stale on the next mutation: this generalises the floor's
`verify_ok_after_edit = None`.

### 4.2 Claim audit (`reasoning/claims.py`)

A closed set of claim kinds extracted from assistant text by conservative patterns: `TESTS_PASS`, `BUILD_OK`, `FIXED`, `FILE_CHANGED`, `COMMAND_RAN`,
`NOT_RUN` (an explicit disclaimer). Each audit result is `SUPPORTED`, `UNSUPPORTED` (no observed fact of the needed kind) or `CONTRADICTED` (an observed fact says
otherwise, e.g. the last runner failed). **Precision over recall**: a claim is flagged only when its kind is recognised *and* the ledger has no matching fact. The model's
disclaimers (`NOT_RUN`, "I did not run the tests") are honoured and never flagged.

### 4.3 Decide (`reasoning/decision.py`)

`decide(state, ledger, budgets) -> Decision(action, reason, evidence_ids, authority)` where `action` is one of `CONTINUE`, `NUDGE(kind)`,
`WITHHOLD_DONE(reason)`, `ANNOTATE_FINAL(claims)`, `RETRY_REQUEST(max_tokens)`, `ESCALATE(rung)`, `STOP(goal_state)`. Every `STOP` carries a member of `GoalState`; every
`ESCALATE` a member of `RecoveryRung`; every failure is named with `FailureClass`. Nothing outside the existing closed sets is invented.

### 4.4 Rules

| ID | Trigger (from the ledger, never from prose) | Action | Authority consulted |
|---|---|---|---|
| R1 | Final text carries a recognised success claim and the ledger has no observed verification after the last mutation (UNSUPPORTED), or the last runner FAILED (CONTRADICTED) | `ANNOTATE_FINAL` appends a one-line harness note; in enforce, `WITHHOLD_DONE` once, then end as `GOAL_UNVERIFIED` | verification floor (`verification.py`), `acceptance.Verdict` |
| R2 | The same failure signature (action key class + normalised error digest) twice in a turn | second: `NUDGE(change_hypothesis)` citing both evidence ids; third: `ESCALATE` via `RecoveryLadder` with `FailureClass.REPEATED` | `recovery.classify_failure`, `RecoveryLadder.decide` |
| R3 | A call refused by an invariant gate twice for the same rule | `NUDGE(refused_by_policy)` naming the rule and a narrower route; never a third attempt at the same shape | gates `Violation.rule` |
| R4 | A provider error states a numeric affordability limit (`can only afford N`) | learn a per-provider ceiling; if `N >= min_useful` one `RETRY_REQUEST(N - margin)`, else `STOP(ESCALATED_TO_HUMAN)` with the billing fact | `FailureClass.ENVIRONMENT`, `providers/openai.py` cap |
| R5 | `done` requested while a recognised obligation is open (existing floor, stagnation, declared criteria, announced step) | unchanged: the core *composes* their existing verdicts into one decision so a turn is never nudged twice | existing guards |
| R6 | A fact is used (in a claim or decision) after its `stale_after` | treat as `UNKNOWN`, never `OBSERVED` | ledger |

R1-R4 are new behaviour; R5-R6 are refactors that make the existing behaviour explicit and auditable.

## 5. Invariants (each will get a witness test before any code ships)

| ID | Invariant | Enforced in | Can be violated by |
|---|---|---|---|
| RC1 | A decision is a function of the ledger, the state and the config only: deterministic and replayable | `decide` is pure; no clock, randomness, environment, network, filesystem | reading time or env inside a rule |
| RC2 | The model's prose is never an input to a decision; it is data under audit | `claims.audit` returns facts about text; `decide` takes only `Claim` results | a rule branching on raw text |
| RC3 | No new authority: outcomes are `GoalState`, failures `FailureClass`, rungs `RecoveryRung` | closed-set assertions on `Decision` | an ad-hoc string outcome |
| RC4 | `observe` changes nothing observable except the journal and logs | the seam only appends records in observe | a nudge leaking out in observe |
| RC5 | Every intervention is budgeted: a turn can always end | per-rule budgets; a property test over random ledgers proves termination | an unbounded withhold loop |
| RC6 | The core can degrade but never fail a turn: an internal error is recorded as `CORE_ERROR` and the turn proceeds with the existing guards untouched | seam wraps `decide` | an exception reaching the engine; or silently disappearing without a record |
| RC7 | Claim audit favours precision: no recognised kind and no missing fact means no flag | pattern set + false-positive corpus | a loose pattern annotating a correct claim |
| RC8 | `OBSERVED` facts are created only from engine events with a source id | `Ledger.observe(event)` is the only constructor | model text reaching `observe` |
| RC9 | Affordability: at most one retry per request, never above the learned ceiling, and a limit below `min_useful` stops honestly | `plan_request` | a retry loop on 402 |
| RC10 | The core never calls a model | module import audit (as `tests/gates/test_purity.py`) | an LLM-based claim extractor |
| RC11 | Every decision is journaled with its evidence ids, and replaying the journaled ledger reproduces it | journal record + a replay test | a decision with no recorded evidence |
| RC12 | One call site per seam, pinned by an AST test | `tests/reasoning/test_seam.py` | a second path that bypasses the core |
| RC13 | A typo in the mode never makes the core more intrusive: unknown means `observe` | `parse_mode` | `enforce` on a typo |

## 6. Seams and flags

Four seams in `WispAgentCore._turn_inner`, each one call, each wrapped so RC6 holds:
1. **After each `tool_result`** (the place `scrub_secrets` and the verification guard already sit): `ledger.observe(event)`.
2. **Before each provider call**: `plan_request` (R4), and the stuck check (R2, R3).
3. **At the `done` gate**: the composed decision (R1, R5).
4. **Turn end**: the decision journal record.

One mode setting, `reasoning_core` = `off | observe | enforce`, default **`observe`**. It does **not** flip the six OFF flags (ADR-0002: one flag per concern; the matrix
in `CURRENT_FLAGS.md` stays authoritative). §8 proposes an optional profile that sets them together, as a separate decision.

## 7. How it uses what exists

- It consumes `acceptance.evaluate` and the floor's state for R1; it does not re-derive "is it verified".
- It calls `recovery.classify_failure` and `RecoveryLadder.decide` for R2/R4; the ladder, not the core, owns "what may be tried next".
- It reuses the `OscillationTrap` hashing idea for tool-level signatures, but keyed on `action_key` + error digest (the existing `action_key` is already the durable idempotency key).
- It reuses `invariant_gate` denials as facts (`GATE_DECISION`).
- Its output vocabulary is the existing one, so `goal.derive_goal_state`, the journal and the judge need no new words.

## 8. Wiring L2 and L3 into the normal path (separate decision)

Today `wisp repl` never reaches L3, and L2 mostly records nothing. An optional **profile** could set the related flags together:

| profile | sets | effect |
|---|---|---|
| `off` | nothing | today's behaviour |
| `observe` (default) | `reasoning_core=observe` | the core records decisions; nothing changes |
| `assist` | + `goal_state`, `recovery_ladder` (record only) | goal state and the chosen rung appear in the journal for every turn |
| `converge` | + `stagnation_gate`, `turn_criteria_source`, `acceptance_gate` | the gates that can withhold `done` |

Each flag stays individually overridable. The `converge` profile is **not** proposed by default: `PHASE_GATE_ENABLEMENT*` document the enablement decisions for those flags (not re-read in full for this draft), and flipping them is the owner's decision.

## 9. Evaluation: where the judge fits, where it does not

The judge (`wisp/judge/`) classifies a run mechanically (SOLVED, GAMED, FAILED, NO-OP, INFRA, BROKEN) and scores `claim_honest`. That is exactly the **outcome** the core is meant to improve.

| Question | Fits the judge? | Why |
|---|---|---|
| Does the core reduce false success claims and gaming over real tasks? | **Yes**: `claim_honest` rate and the `GAMED` rate are the primary metrics, `SOLVED` non-regression is the guard | it is mechanical and already independent of the model's prose |
| Did each *decision* inside a run behave as intended (the stuck rule fired once, the ceiling was learned, the claim was flagged)? | **No** | the judge sees the workspace before and after, not the steps |
| Can it gate CI? | **No** | it needs a live model (non-deterministic, costs money, and `INFRA` runs are excluded from the rate) |
| Does it carry steps, tokens, ending kind? | **No** (`Verdict` has `seconds`, no steps, tokens or ending) | extending it is possible but is a decision (D5) |
| Is its claim the model's text? | **No**: it parses wisp's `ok` flag, not the claim in prose | R1 needs the prose claim |

So evaluation has three layers, and only the first is new engineering:

1. **A deterministic fault-injection suite** (free, CI-able). Scripted provider "personas" drive the real engine over a real temp workspace: `ClaimsWithoutRunning`,
   `RepeatsTheSameFailure`, `GamesTheTest`, `RephrasesARefusedCommand`, `AnnouncesAndStops`, `HitsAnAffordabilityLimit`, and an `HonestSolver` as the control that the core must
   leave **untouched**. Verdicts reuse the judge's mechanical checks and add: ending kind, steps used, interventions fired, and whether the conversation is byte-identical in observe.
2. **Live judge runs**, paired off/on with `--repeat`, capped by money and a key *you* name (standing rule), reporting `claim_honest`, `GAMED`, `SOLVED`.
3. **Replay**: re-run `decide` over the journaled ledger and require the identical decision (RC11).

## 10. Phases (each shippable, observe-first, each ends with a mutation probe)

| Phase | Deliverable | Exit criterion |
|---|---|---|
| P0 | `ledger.py`, `claims.py` (pure) + false-positive corpus | RC7, RC8, RC10 witnessed; every mutation caught |
| P1 | `decision.py`, `plan_request`, the four seams in **observe** | RC1-RC6, RC9, RC11-RC13 witnessed; observe is byte-identical to off |
| P2 | The fault-injection suite and a **baseline with the core off** | numbers for how often each persona's failure reaches the user *today* |
| P3 | `enforce` for R1, R2, R4 (one at a time) | each rule shows a measured improvement on its persona with no change to `HonestSolver` |
| P4 | Optional profile (§8); journaling of `GoalState` per turn | the owner's decision |
| P5 | Optional: model-**declared** expectations; speculative search (`feat/speculative-lsp`) as a client of the ledger | separate design |

### P0 status (2026-10-07)

Built on `design/reasoning-core`: `wisp/core/reasoning/{ledger,claims}.py`, 243 tests in `tests/reasoning/` (ledger, 49-case positive corpus, 100+ case look-alike corpus, the verdict matrix, a purity audit by AST).
A mutation probe of 19 mutations over both modules ended with 0 survivors; its first run had 5 survivors/missing mutations, each a real test gap (a disclaimer in a separate sentence; `if`, `but`, `should` each tested only alongside another excluded word), now closed.
The corpus first run also found 3 genuine false positives (a claim about someone else's or last week's result: "The README says all tests pass") and 2 misses ("no errors" tripped the failure words); fixed by attribution words and by removing "no errors/issues" before the negation test.
Not verified: the false-positive rate on real transcripts. The corpora are synthetic; that measurement is the P2 baseline and needs the owner's consent to read session content.

### P1 status (2026-10-07)

Built: `decision.py` (R1-R4 as pure functions over `GoalState`/`FailureClass`/`RecoveryRung`; frozen `State`; budgets), `runtime.py` (`TurnReasoning`, journal), `shellwrites.py` (shell writes via the gates' parser), setting `reasoning_core` (default `observe`, a typo is `observe`), and four seams in `stateless.py`: after each tool result, on every refusal (`_refusal_result_event`, the one helper all refusals pass through), at the final answer, and on a provider exception.
Witnessed: 330 tests in `tests/reasoning/`; a real turn reaches every seam; observe output equals off output for the user; a broken ledger/audit/planner degrades to a `core_error` journal row (RC6); an AST test pins one call site per seam (RC12). 28-mutation probe over decision, runtime, ledger, shellwrites and the engine seams: 0 survivors except one equivalent mutant (`==` vs `>=` on the R2 nudge, since count 3 hits the escalate branch first).
Found on the way: refusals bypass the tool-result loop (they are appended to `tool_results_events_early`), so a seam placed only after `_execute_tool` never saw them; the refusal helper is now seam 1b. Re-anchored `scripts/derive_current_flags.py` (stateless.py read site 1367 -> 1384) and regenerated `CURRENT_FLAGS.md` and `register.md`.
Still unverified: that a real 402 reaches the provider-exception seam (it may arrive as an error event instead; P2 stub test); the journal is in memory and logged at debug, not yet persisted; `enforce` is decided but not applied (P3).

### P2 status (2026-10-07)

Built: `tests/reasoning/personas.py` (seven scripted personas driving the real engine: six failures plus the `HonestSolver` control), `test_personas.py`, and the baseline `docs/harness/reasoning-core-baseline.md` (table generated and pinned by a test).
Result: with the core off, all six failures reach the user today; observe changes nothing the user sees (whole event stream compared); the control is untouched; the journal records R1, R2, R3, R4 on the matching personas.
P2 found and fixed two P1 defects: a real 402 arrives as a provider `error` event (the seam only covered exceptions; now both reach one helper), and a failing shell command is an "ok" tool result with an exit marker in the text (R2 never saw repeated failures). 36-mutation probe: 1 survivor, equivalent (R2 `==` vs `>=`).
Not measured: real models or transcripts; live paired judge runs need a key and a cap you name.

### P3 status (2026-10-07)

R4 applied in `enforce` (commit 9592bf6): one retry at the provider's own affordable ceiling minus a margin, or an honest stop that names the limit; 13-mutation probe, 0 survivors.
R1 applied in `enforce`: a success claim the ledger cannot back is withheld once at the last completion gate (after the floor and the other gates, so none of them changes), then flagged in the answer's own text and the turn ends unverified; 12-mutation probe, 0 survivors after one added test (the RC4 guard).
Both are visible in the baseline table (`reasoning-core-baseline.md`): exactly the rows meant for R1 and R4 move from "reaches the user: yes" to "no" under enforce, nothing else moves, observe still equals off.
Remaining: R2/R3 (repeat/refusal nudges), a Linux (Docker) run of the new suites, the default (`observe` vs `enforce`, your decision), live paired judge runs (key and cap needed). Unverified: how often real models make these claims; whether flagging an answer annoys users in practice.

## 11. Risks

- **False positives in claim audit** would annotate correct claims. Mitigation: precision-first patterns, a corpus of real transcripts, observe mode first, and a measured rate before any `enforce`.
- **Redundancy with the OFF flags.** Mitigation: the core composes their verdicts (R5), never duplicates them, and never flips them.
- **Complexity in the hot path.** Mitigation: pure, constant-time per event, a pinned seam, and RC6 so a core bug cannot take a turn down.
- **A model that is already honest gains nothing.** That is expected and is the `HonestSolver` control: the core must be a no-op there.
- **Gaming the audit** (phrasing a claim to dodge the patterns). The audit is a floor, not a wall; the verification floor and the judge remain the authorities on truth.

## 12. Decisions for the owner

| # | Decision | My recommendation |
|---|---|---|
| D1 | Scope: L1 only, or also connect L2/L3 to the normal REPL path | **L1 only** now; the §8 profile later and separately |
| D2 | In `enforce`, R1 annotates only, or also withholds `done` once | annotate first; withhold once after the false-positive rate is measured |
| D3 | Evaluation money: a key and a ceiling for live judge runs (and which model) | the deterministic suite needs none; name a cap before any live run |
| D4 | Typo in the mode means `observe` (RC13) rather than `enforce` | `observe`: new behaviour must never switch on by accident |
| D5 | Extend the judge's `Verdict` with steps, tokens and ending kind, or keep a separate scorer | a separate scorer in the suite; do not widen a merged tool's contract for this |
| D6 | Order against the unmerged branches (`feat/invariant-gates`, `feat/modern-harness`, `feat/speculative-lsp`) | gates first (the core consumes them); speculative search later as a client |
| D7 | Name | keep "reasoning core"; the code lives in `wisp/core/reasoning/` |

## 13. What I did not verify

- **How often models actually make unsupported claims** in this setup: there is no baseline yet; P2 produces it. The design's value claims are hypotheses until then.
- **The false-positive rate** of the claim patterns on real transcripts (needs a corpus of sessions; the stores hold thousands, read-only access is available).
- **Whether the six OFF flags are off for a reason that still holds** (their enablement-decision documents were not re-read in full).
- **Whether a bounded affordability retry (R4) helps in practice:** the one observed case (83 affordable) stops honestly either way.
- The judge's task corpus size and how much noise a paired run needs (not measured).
