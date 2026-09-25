# PHASE POST-M13 — LIVE COMPLETION ENFORCEMENT SEAM RECON

**Phase type:** read-only forensic architecture reconnaissance.
**Date:** 2026-09-24
**Predecessors:** `PHASE_POST_M13_AUTHORITY_RECON.md` · `PHASE_POST_M13_VERDICT_CONTRACT.md` · `PHASE_POST_M13_AUTHORITY_ADR.md` (ADR-0035) · `PHASE_POST_M13_AUTHORITY_IMPLEMENTATION.md`

**Primary question:** what is the minimum authority-preserving interface by which the runtime-owned
M13 stagnation predicate can influence the engine's pre-`done` gate?

**Answer in one line:** the *interface* is a small, precedent-following addition — but the *policy* it
would enforce is not specified anywhere, and ADR-0035 contains a self-conflict that decides whether the
seam delays or vetoes. The interface is answered here; the policy needs an amendment.

---

## 1. Baseline

| Property | Value |
|---|---|
| HEAD | `7c15626` — `docs(m11): record the M11 phase; findings F29-F31; repair the commit table` |
| Branch | `main` |
| Working tree | **dirty** — 87 entries; 39 tracked-modified + 48 untracked |
| Pre-existing modifications | the Phase 10/13 WIP plus this session's five post-M13 documents — **all treated as immutable** |
| Diff at baseline | 174,566 bytes |

Snapshot: `.workbuddy-ai/memory/post-m13-seam/{head,branch,status-before}.txt`, `diff-before.patch`.

**No production code, test, configuration, ADR or tripwire was modified.** The only artifact this phase
writes is this document.

---

## 2. Actual call path

Traced from source, not from the implementation report (whose line numbers have shifted).

```
AgentRuntime.run_turn                                  core/runtime.py
  │  builds: stagnation_detector (per turn)            runtime.py:708, from_config at :713-716
  │          call_args_by_id, terminal flags
  │
  ├─ async for raw_event in core.turn(                 runtime.py:750
  │        session, prompt, approval_handler=…, steering_drain=…)
  │
  │   ┌── WispAgentCore.turn                          stateless.py:204
  │   │     publishes ContextVars: turn_deadline :224, agent_depth :230, agent_branch :234
  │   │     wraps the loop in asyncio.timeout(turn_timeout)   :299
  │   │        │
  │   │        └─ WispAgentCore._turn_inner           stateless.py:315
  │   │              builds guard = VerificationFloorGuard(...)  :341-345
  │   │              publishes self._last_guard = guard          :367   ← engine → runtime
  │   │              for iteration in range(max_iterations):     :368
  │   │                 provider round → tool_calls → validate → authorize → execute
  │   │                 ── yields `tool_result` ────────────────────────► runtime observes it
  │   │                                                                   (updates the detector)
  │   │                 ...
  │   │                 if provider round incomplete: error + return     :752-760
  │   │                 ┌ PRE-DONE GATE ─────────────────────────────────┐
  │   │                 │ rejection = guard.rejection()      :766       │
  │   │                 │ if rejection: nudge; yield; continue :767-790  │
  │   │                 │ if guard.resolved(): capture skill  :793-804   │
  │   │                 │ yield done_event()                  :805       │
  │   │                 └────────────────────────────────────────────────┘
  │   │
  │   └─ except TimeoutError: error(fatal) + done      stateless.py:306-313
  │
  └─ consumes events; at loop end:
        turn_succeeded = saw_done and not saw_fatal_error    runtime.py:902
        recovery consumer (flag)                             runtime.py:1149
        goal-state derivation + recording (flag)             runtime.py:1198, 1209
```

### `done` has three emission sites, not one

| Site | Path | Emits a fatal error first? | Can it loop again? |
|---|---|---|---|
| `stateless.py:805` | the clean-completion gate | no | **yes** — `rejection()` does `continue` at `:790` |
| `stateless.py:313` | the `turn()` timeout wrapper | **yes** — `CODE_TURN_TIMEOUT`, `recoverable=False` | no — the timeout already fired |
| `stateless.py:964` | the iteration-budget wrap-up | **yes** — `CODE_ITERATION_BUDGET`, `recoverable=False` | no — the loop has ended |

**Only site 805 is a gate that can refuse to finish and go round again.** The other two are *failure*
paths that emit a fatal error and then a `done` — which is precisely why 13-H5's
`saw_done ∧ ¬saw_fatal_error` exists: a `done` alone does not mean success. They are already correctly
`GOAL_FAILED` under ADR-0035's precedence row 3, and a stagnation gate would not (and should not) touch
them.

**Edges annotated:**

| Edge | Data passed | Authority passed | Mutable state | Sync/async | Exception boundary |
|---|---|---|---|---|---|
| runtime → `core.turn` | `session` dict, `prompt`, 2 callables | none — the engine owns its own loop | the `session` dict | async generator | none (the runtime's `async for` is outside) |
| `turn` → `_turn_inner` | same + `messages`, `system_prompt`, `tools`, `max_iterations` | none | guard created inside | async generator | `asyncio.timeout` wraps it (`:299`) |
| `_turn_inner` → runtime | event dicts (`tool_result`, `system`, `done`, `error`) | none | `self._last_guard` published | async yields | `try/except` around provider work |
| `_turn_inner` → ContextVars | `turn_deadline`, `agent_depth`, `agent_branch` | none | ContextVar writes | — | — |

**The direction that exists today is engine → runtime** (`self._last_guard`, ADR-0018). The seam needs
the **reverse**: runtime → engine.

---

## 3. Authority ownership (from source)

| Mechanism | Owner | Current location | Current authority | Must remain unable to |
|---|---|---|---|---|
| `VerificationFloorGuard` | the **engine** (`_turn_inner`) | `stateless.py:341-345`; class `verification.py:120` | the verification floor: `rejection()` may withhold `done` (bounded); `resolved()` is the verified-completion predicate | decide goal state; decide recovery; be re-implemented elsewhere |
| P3 `CompletionVerdict` | derived from the guard, read by the **runtime** | `verification.py:293-301`; recorded `runtime.py` verdict block | an acceptance **input** — `PASS`/`FAIL`/`INCONCLUSIVE` | own completion; gate anything |
| M13 detector | the **runtime** | constructed `runtime.py:708,713-716`; class `stagnation.py:191` | `UNKNOWN`/`PROGRESSING`/`STAGNATING` for one turn | gate `done`; classify failures; be duplicated |
| `may_report_goal_met()` | **M13** (the detector) | `stagnation.py:294-305` | whether the goal may be called met | decide recovery; mutate anything |
| Goal arbiter | **new**, ADR-0035 | `goal.py::derive_goal_state`; called `runtime.py:1198` | the goal state | inspect files/git/tools; call a model; produce `turn_succeeded` |
| terminal evidence | the **runtime** | `runtime.py:867-902` | `turn_succeeded` | decide the goal state |
| `turn_succeeded` | the **runtime**, one producer | `runtime.py:902` | node status, spans, persistence | be produced anywhere else (pinned by an AST ratchet) |

**The engine imports `stagnation` nowhere.** Verified: zero occurrences in `stateless.py`. So today the
two authorities share no code path at all.

---

## 4. M13 lifecycle

| Property | Finding | Evidence |
|---|---|---|
| Constructed | **once per turn**, inside `run_turn`, before `core.turn` is called | `runtime.py:708`, `:713-716` |
| Lifetime | **one turn** — a fresh detector per turn, deliberately | `runtime.py:704-707` (comment: per turn, not per session) |
| Fed by | `tool_result` events the runtime observes as it consumes the engine's stream | `runtime.py` M13 observation block |
| `may_report_goal_met()` inputs | `enabled`, `consecutive_flat`, `min_consecutive`, `trap_fired` | `stagnation.py:294-305` |
| **Mutates?** | **No.** It reads three attributes and returns a bool. `verdict` (`:265-278`) is likewise a pure property | `stagnation.py:294-305, 265-278` |
| Idempotent? | **Yes** — safe to call any number of times | as above |
| Valid **before** `done`? | **Yes.** The runtime has already processed every prior `tool_result` by the time the engine reaches the gate: the engine yields an event (suspending), the runtime's loop body runs, then the engine resumes. So the detector reflects everything observed so far | the `async for` at `runtime.py:750` + the yield at the gate |
| Valid after `done`? | Yes, but meaningless — the turn is over | — |
| Disabled | `enabled=False` ⇒ `may_report_goal_met()` returns **`True`** | `stagnation.py:301-302` |
| `not may_report_goal_met()` means | `consecutive_flat >= min_consecutive` **or** `trap_fired` — i.e. "a run of non-progressing observations, or a detected cycle" | `stagnation.py:303-305` |

**It is *not* a hard completion rejection.** It is a *predicate about progress* that the goal arbiter
consumes as row 4. Nothing in M13 says "the turn must not end".

---

## 5. Boundary analysis

The predicate could physically enter at exactly one place: **between `stateless.py:790` and `:791`** —
after the floor guard has declined to reject, and before `guard.resolved()` / `done`. That is the only
site that (a) is on the clean-completion path, and (b) can still `continue` the loop.

Everything needed for the crossing already exists *except the object itself*:

- the engine has the detector's **address space** (same task, same call stack — the runtime drives the
  generator),
- the engine already holds two injected callables and three published ContextVars,
- the runtime already holds a per-turn detector and already reads one engine-published handle
  (`core._last_guard`).

**What is missing is only the runtime → engine direction.**

---

## 6. Existing channels

| Channel | Exists? | Fits? | Evidence |
|---|---|---|---|
| **Injected callable on `core.turn()`** | **YES** | **YES** — `approval_handler` and `steering_drain` are exactly this: runtime-owned behaviour the engine calls | `stateless.py:204, 317`; threaded to `_turn_inner` at `:300-304` |
| **ContextVar** | **YES** | partially — but the module is scoped to *tool execution* | `wisp/tools/context.py` header: "Per-execution context for the process-wide shared ToolExecutor"; setters listed are `agent_depth`, `agent_branch`, `turn_deadline`, `sub_event_queue`, `repeat_key` |
| **Mutable core attribute** | **YES** | the *reverse* of what is needed | `self._last_guard = guard` (`stateless.py:367`) — engine writes, runtime reads |
| **`session` dict** | YES | **NO** — it is the persisted transcript carrier; putting a callable in it risks serialization | `stateless.py:239`, `session.get(...)` reads |
| **Callback / `before_done` hook** | **NO** | — | searched: no `before_done`, `before_complete` or completion hook exists; the only gate is the inline `guard.rejection()` |
| **Event queue / tool execution context** | YES | **NO** — scoped to tool calls, and the gate is not a tool call | `wisp/tools/context.py` |

**Two channels genuinely fit: an injected callable (A) and a ContextVar (B).** The engine's use of
`_exec_ctx.turn_deadline.set(...)` with the comment *"Overwritten by every turn; only read while a turn
is live"* (`stateless.py:224`) is a live precedent for B's *mechanics* — but its home module is
explicitly the **tool** context, so B would require either an off-purpose addition there or a new module.

---

## 7. Candidate seams

| | Candidate | Verdict |
|---|---|---|
| **A** | **Pass a predicate explicitly through `core.turn()`** | **RECOMMENDED.** Exactly mirrors `approval_handler`/`steering_drain`. The engine calls a runtime-owned callable; it gains no authority, holds no state, and imports nothing new. `core.turn` has ~65 call sites and already accepts extra keyword args (`core.turn(session, "go", approval_handler=_allow)`), so an optional kwarg with a default is backward-compatible |
| **B** | Existing ContextVar | **Viable but second.** The mechanics are proven (`turn_deadline`), but the only ContextVar module is scoped to tool execution; adding a completion predicate there would be off-purpose, and a new module for one bool is more surface than one parameter |
| **C** | Existing callback/hook | **Not available.** No `before_done`/completion hook exists. Creating one *is* candidate A under a different name |
| **D** | A completion-policy object | **Rejected — over-built.** ADR-0035 needs one bool for one gate; an object introduces a type, a lifecycle and a place to put future policy, none of which is decided |
| **E** | Move M13 ownership into the core | **Rejected.** The detector is per-turn runtime state fed by the runtime's event stream; moving it would create a second detector during the transition and change M13's ownership without evidence the current one is wrong |
| **F** | Add a second detector in the core | **Rejected — STOP 2.** Two detectors is two authorities for `UNKNOWN`/`PROGRESSING`/`STAGNATING` |

---

## 8. Recommended seam

**Candidate A — one optional injected callable, mirroring `approval_handler`.**

```
AgentRuntime.run_turn
  │  owns: stagnation_detector  (per turn, unchanged)
  │
  │  passes a closure:  lambda: not stagnation_detector.may_report_goal_met()
  ▼
WispAgentCore.turn(..., completion_gate=None)
  ▼
WispAgentCore._turn_inner(..., completion_gate=None)
  ▼
at stateless.py:790/791 — beside guard.rejection(), before done
```

**But the interface is the *easy* half.** Three policy questions are unanswered by every existing
document, and one of them is a self-conflict inside ADR-0035. See §11 and §16.

---

## 9. Data contract

What crosses the boundary is the smallest thing that preserves authority:

| Option | Preserves authority? | Replayable? | Verdict |
|---|---|---|---|
| a **bool** (snapshot) | ✗ — a stale snapshot could diverge from the detector as the turn continues | — | **No** |
| an **enum** | ✗ — same staleness; and it duplicates the detector's vocabulary | — | **No** |
| a **callable returning bool** | **✓** — the runtime keeps ownership; the engine only asks. The value is always current | the *verdict* is separately durable | **RECOMMENDED** |
| a capability object | ✓ but over-built | — | No |
| a context object | ✓ but over-built | — | No |

**Contract:** `Callable[[], bool] | None`, evaluated **at the gate, on demand**, returning
`True` = "completion may proceed". `None` = "no completion authority beyond the floor guard" — today's
behaviour exactly.

The callable is a **closure over the per-turn detector**, so it is inherently per-turn: no cache, no
lifetime to manage, no leak between turns.

**What must NOT cross:** the detector itself. Handing the engine the object would let the engine call
`observe()`, which *mutates* — that would hand a second party write access to the one stagnation
authority. The callable exposes the read-only predicate only.

---

## 10. Lifetime and concurrency

| Concern | Analysis |
|---|---|
| Lifetime | **per turn** — the closure is created in `run_turn` and dies with it. Matches the detector's own lifetime (`runtime.py:704-707`) |
| Concurrent sessions | Safe. Each turn has its own detector and its own closure; nothing is shared |
| Nested turns | Safe — each `core.turn` call receives its own argument. A nested engine turn (none exist today) would receive `None` unless passed |
| **Subagents** | **A real gap.** `multi_agent/_runner.py:532` calls `core.turn(session_dict, contract.task)` **directly**, bypassing `AgentRuntime.run_turn`. Subagent turns therefore have no M13 detector and would pass `None` ⇒ no stagnation gate. That is *consistent* (no detector ⇒ no predicate) but must be stated, not discovered later |
| Background tasks | Same as subagents — they drive `core.turn` directly (`_runner.py:532`) |
| Async cancellation | Safe. The callable is synchronous and non-blocking; cancelling the turn discards it |
| Multiple `AgentRuntime` instances | Safe — no shared state; the closure is per instance per turn |
| Global state | **None introduced.** ContextVar (B) would have been the riskier choice here |

---

## 11. Failure semantics

| Failure | Required behaviour | Why |
|---|---|---|
| Predicate is `None` (no detector, or a subagent turn) | **permit `done`** | Today's behaviour. Fail-closed would make an observability gap into a stuck run — the exact failure ADR-0026 named |
| The callable **raises** | **permit `done`**, and log | The engine's gate is not the place to turn a broken predicate into a hung turn |
| Detector `enabled=False` | **permit `done`** — the predicate returns `True` | `stagnation.py:301-302`; this is the rollback switch |
| Stale predicate | **Not possible** — the callable reads live state at the moment of the call | §9 |
| Duplicate evaluation | **Harmless** — the predicate is pure and idempotent | `stagnation.py:294-305` |

**Can failure of the stagnation authority silently permit `done`?** **Yes — and it must.** But it is not
*silent*: the goal-state record already carries `stagnation_verdict`, and `not_evaluated` is the explicit
sentinel for "the detector was absent" (`runtime.py` goal record). The absence is durable and visible.

**This is a decision the amendment must ratify**, because ADR-0035 does not state it.

---

## 12. Replay implications

| | Live | Reconstructed |
|---|---|---|
| Enforcement input | the live callable over the per-turn detector | **not available** — no detector exists during replay |
| Durable counterpart | the goal-state record's `stagnation_verdict` | read from the journal |
| Authority result | the goal state | **re-derived** from the record's own inputs (`derive_goal_state`) |

**The seam does not make replay depend on live detector state**, because:

1. the goal state is a pure function of **journaled** inputs, and `stagnation_verdict` is one of them;
2. the *effect* of any enforcement is itself durable — if the gate withheld `done`, the turn's terminal
   outcome (and the recorded goal state) reflects that;
3. nothing in the arbiter reads the detector directly.

**The distinction the report must preserve:**

```
live enforcement input      = the callable  (transient, per turn)
replay input                = the recorded stagnation_verdict  (durable)
```

They agree because both come from the same detector in the same turn. **If enforcement ever used a
different source than the recorded verdict, replay would diverge — so the seam must use
`may_report_goal_met()` and nothing else.**

---

## 13. Test plan (design only — not implemented)

| # | Test | Asserts |
|---|---|---|
| **S1** | M13 non-stagnating ⇒ `done` emitted as today | the default path is unchanged |
| **S2** | M13 stagnating + P3 `PASS` ⇒ the gate refuses `done` | `GOAL_STAGNATED` is *enforced*, not merely recorded |
| **S3** | M13 `UNKNOWN` ⇒ does not block | `UNKNOWN` stays non-blocking (ADR-0035 row 5's neighbour) |
| **S4** | A predicate from run A cannot affect run B | no leakage between turns |
| **S5** | Two concurrent runs keep independent predicates | no global state |
| **S6** | Restart/replay reproduces the same goal state | replay determinism |
| **S7** | Predicate absent / raises ⇒ `done` permitted, absence recorded | the failure policy of §11 |
| **S8** | Terminal `CANCELLED` cannot be overridden by the gate | precedence rows 0–1 |
| **S9** | `VerificationFloorGuard` semantics unchanged | `rejection()`/`resolved()` behave identically; the floor guard's own budget still surrenders |
| **S10** | `turn_succeeded` still has exactly one producer | the AST ratchet still passes |
| **S11** *(repo-specific)* | The gate is bounded: a stagnating turn **eventually** emits `done` | ADR-0035's "surrenders honestly" — otherwise the turn hangs |
| **S12** *(repo-specific)* | `turn_succeeded = True` **coexists** with `GOAL_STAGNATED` | ADR-0035 line 1478 — the clause the seam currently contradicts |
| **S13** *(repo-specific)* | The two failure-path `done` sites (`:313`, `:964`) are unaffected | only the clean gate is guarded |
| **S14** *(repo-specific)* | A subagent turn (which bypasses `run_turn`) is unaffected | the §10 gap is documented behaviour, not an accident |

---

## 14. Implementation boundary (what the next phase may modify)

1. `core/stateless.py` — one optional keyword parameter on `turn()` and `_turn_inner`; one evaluation at
   the gate between `:790` and `:791`.
2. `core/runtime.py` — construct the closure and pass it at the `core.turn` call site (`:750`).
3. A new module for the intervention's text and bound, **if** the amendment puts them in M13's home.
4. Tests for S1–S14.

**Not authorised:** changing `VerificationFloorGuard` or its criterion; a second detector; any global
mutable state; changing `turn_succeeded`'s rule; changing ADR-0035's precedence; touching the two
failure-path `done` sites; F8; the graph; fanout.

---

## 15. Non-goals

Cancellation and timeout producers; `BUDGET_EXHAUSTED`/`TIMED_OUT` as states; subagent stagnation
enforcement; moving goal-state calculation into `core.turn()`; the recovery ladder's own semantics; M1,
M5–M8, M10; graph-driven execution; F8.

---

## 16. Why the seam is not yet implementable

The **interface** is answered (§8) and the **mechanics** are settled (§9–§12). Four **policy** questions
are not answered by any existing document, and one of them is a conflict *inside* ADR-0035:

### (a) ADR-0035 contradicts itself on whether the gate delays or vetoes — **STOP 6 territory**

| Line | Clause |
|---|---|
| `WISP_ARCHITECTURE_DECISIONS.md:1528` | the gate "may **WITHHOLD** `done` — bounded, and it surrenders honestly when no rung remains" |
| `WISP_ARCHITECTURE_DECISIONS.md:1478` | "**`turn_succeeded = True` may coexist with `GOAL_FAILED`, `GOAL_UNVERIFIED` or `GOAL_STAGNATED`.** That coexistence is the point" |

If the gate **vetoes**, `saw_done` is false, so `turn_succeeded` is false, so line 1478's coexistence is
**impossible**. If the gate **delays and then surrenders**, line 1478 holds but the intervention is a
nudge whose content is undefined (below). **These must be reconciled before a line of code is written**,
because they prescribe different behaviour for the same input.

### (b) What the intervention *is* — undefined, and the obvious shape is forbidden

A stagnation nudge would tell the model to change approach and go round again. But
`stagnation.py:335-338` states the plan's position plainly: *"detection must trigger a replan rather
than a retry: retrying the same action against the same state is the definition of the loop being
detected."* So the gate must not implement the retry shape — yet nothing defines the replan shape, its
text, or which module owns it (the floor guard's text lives in `verification.compose_nudge`, the
invariant's home module).

### (c) The bound — undefined

ADR-0035 says "bounded" but never states the bound. `VerificationFloorGuard` has its own
(`min_turns`, `max_nudges`); the detector has `min_consecutive` but **no nudge budget**. Adding a second
untuned knob compounds P7's acknowledged `min_consecutive` limit.

### (d) Failure policy — unstated

§11 recommends **fail-open with the absence recorded**, and gives the reasoning. ADR-0035 does not state
it, and it is an authority question, not a tuning one.

### Hard-stop check

| Stop | Triggered? |
|---|---|
| STOP 1 — change `VerificationFloorGuard`'s meaning | **No** |
| STOP 2 — a second M13 detector | **No** |
| STOP 3 — make M13 global mutable state | **No** |
| STOP 4 — replay depends on live detector state | **No** (§12) |
| STOP 5 — change `turn_succeeded` | **No** *if* the gate delays-and-surrenders; **yes** if it vetoes — which is (a) |
| STOP 6 — change ADR-0035's precedence | **Borderline — triggered by (a).** The precedence table itself is not changed, but two clauses of the same ADR cannot both hold |
| STOP 7 — a new authority not covered by ADR-0035 | **Triggered by (b)/(c).** The intervention's content and bound are new policy |

---

## FINAL STATUS

```
SEAM STATUS: REQUIRES ARCHITECTURE DECISION
```

The **interface** is proven and small — an optional injected callable through `core.turn()`, mirroring
`approval_handler`/`steering_drain`, carrying a `Callable[[], bool]` closure over the per-turn detector.
That answer is complete, precedent-following, backward-compatible, per-turn, concurrency-safe and
replay-neutral.

What is **not** settled is what the seam would *do*. ADR-0035's own text forbids the coexistence it
asserts (§16a), the intervention's shape is undefined and its obvious form is explicitly rejected by
M13's own documentation (§16b), the bound is unstated (§16c), and the failure policy is unstated (§16d).

**The exact amendment needed** — a short ADR-0035 amendment or a new ADR-0036 fixing four things:

1. whether the gate **delays-then-surrenders** (which preserves line 1478) or **vetoes** (which
   supersedes it) — and if it vetoes, what `turn_succeeded` means for a stagnating turn;
2. the intervention's **shape and home module** (a replan-shaped nudge, not a retry);
3. its **bound**, and how it interacts with the floor guard's existing budget;
4. its **failure policy** (recommended: fail-open, with `not_evaluated` recorded).

With those four fixed, implementation is mechanical: one parameter, one closure, one gate evaluation —
and S1–S14 become writable without further decisions.

**Do not implement around this.** The interface is ready; the policy is not, and inventing it would be
exactly the silent stretch the brief forbids.
