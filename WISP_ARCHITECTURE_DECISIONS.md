# WISP — ARCHITECTURE DECISIONS

**Append-only decision log for the Persistent Graph Loop migration.**

Every decision that constrains future phases is recorded here with its evidence and its reversal
condition. Decisions are never edited — a superseded decision is marked `SUPERSEDED by ADR-NNNN` and a
new entry is appended.

**Status vocabulary:** `ACCEPTED` · `SUPERSEDED` · `PROVISIONAL`

---

## ADR-0001 — Evolve the existing four layers; do not replace them

**Status:** ACCEPTED
**Phase:** P0
**Context:** The Phase 0 audit found four disjoint execution/durability layers:

| Layer | Location | Has | Lacks |
|---|---|---|---|
| A — live turn loop | `core/stateless.py` (`WispAgentCore.turn`), `core/runtime.py` | dynamism, streaming, tools, approvals | graph, persisted node state, evidence |
| B — durable validated DAG | `wisp/graph/` | persistence, validation, provenance, join policies | frozen topology (static DAG) |
| C — experimental phase loop | `wisp/core/graph/` + `OscillationTrap` | a goal→phase loop | tests only; disowned |
| D — durable runtime layer | `wisp/runs/`, `wisp/contracts/`, `wisp/trace/` | run state machine, leases, idempotency, spans | never constructed in production |

**Decision:** The Persistent Graph Loop is **not** built from scratch. Layer A is evolved in place to
consume Layer D's durable primitives, and Layer B supplies graph semantics. Layer C is left disowned.

**Rationale:** Layer D is complete and tested — the cost of the target architecture is *wiring*, not
construction. A rewrite would discard the governance, containment, and authority work of Phases 1–13
and would violate the standing no-rewrite constraint.

**Reversal condition:** If Layer D's abstractions prove unable to carry per-node graph state, revisit
at P4 (task graph materialization).

---

## ADR-0002 — One rollback flag per concern, read via `getattr` with a safe default

**Status:** ACCEPTED
**Phase:** P0
**Context:** P0 must be reversible "by disabling one flag" (migration plan), but P0 spans four
independent concerns (run persistence, event fidelity, spans, replay). A single flag would make partial
rollback impossible; declaring new frozen-dataclass fields on `WispConfig` would break the many
`SimpleNamespace` test doubles.

**Decision:** Three flags — `durable_runs`, `session_event_fidelity`, `turn_spans` — each read at the
consumption site as `getattr(config, name, True)`, and each resolvable from the environment
(`WISP_DURABLE_RUNS`, `WISP_SESSION_EVENT_FIDELITY`, `WISP_TURN_SPANS`) via `get_setting`.

This follows the established `thin_tools` precedent (`core/stateless.py:1189`, `:1740`, `:1774`), which
is likewise **not** a declared `WispConfig` field and is read with `getattr`.

**Consequence:** Test doubles that predate the flags get the new behavior by default (safe, because
every new write is additive and best-effort) and can opt out by setting the attribute.

**Reversal condition:** None — this is a convention, not a mechanism.

---

## ADR-0003 — No `RunStatus`→`RunState` coercion shim is needed

**Status:** ACCEPTED
**Phase:** P0
**Context:** `WISP_MIGRATION_PLAN.md` P0 item 2 required canonicalizing `RunStatus`
(`wisp/graph/types.py:37`, 7 values) and `RunState` (`wisp/runs/record.py:17`, 8 values) *before*
wiring, on the theory that wiring an uncodified divergence would create a live defect.

**Evidence (executed against the repository):**

```
RunStatus: ['awaiting_approval','cancelled','failed','paused','queued','running','succeeded']
RunState : ['awaiting_approval','cancelled','failed','paused','planning','queued','running','succeeded']
subset? True | extra: ['planning']
all coerce OK: True
```

**Decision:** `RunStatus` is a **strict subset** of `RunState`. The single asymmetry (`PLANNING`) exists
only in `RunState` and is unreachable from any `RunStatus` producer. Every `RunStatus` value is a legal
`RunState` value and coerces through the existing `coerce_state()` without a shim.

**Consequence:** P0 item 2 is **complete with no work**. Wiring the durable layer cannot introduce a
state-vocabulary divergence. `coerce_state()` remains the single translation authority
(`runs/record.py:57`) and already fails loud on unknown values.

**Reversal condition:** If a future phase adds a `RunStatus` member absent from `RunState`, a shim
becomes mandatory. The `subset? True` assertion should be promoted to a ratchet test at that point.

---

## ADR-0004 — Persistence is best-effort and must never break a turn

**Status:** ACCEPTED
**Phase:** P0
**Context:** P0 adds SQLite writes to the hot turn path (`AgentRuntime.run_turn`). A raised exception
from the durable layer would abort a user's turn — turning an observability feature into an outage.

**Decision:** Every new durable write is wrapped in `try/except Exception` with a counter and a
`logger.warning`. This follows the precedent already set by `BackgroundAgentManager._persist_create`
(`background.py:179-182`) and `_persist_status` (`:208-211`), which maintain
`persist_skipped_total` as a durability canary.

**Consequence:** Durability failures are observable (`persist_skipped_total`, `persist_stats()`) but
never fatal. The canary is the honest signal; a non-zero count means state is being lost.

**Reversal condition:** Once a phase requires durable state as a *correctness* precondition (P2's
proposal boundary is the likely candidate), silent best-effort is no longer acceptable for that path
and must become fail-loud. Revisit at P2.

---

## ADR-0005 — Fail loud on unknown session event types during replay

**Status:** ACCEPTED
**Phase:** P0
**Context:** `Session.apply` (`core/session.py:84-117`) is a `match` on `SessionEventType` with **no
wildcard arm**. Adding a member to the enum therefore produces a silent no-op on replay: the event is
consumed, contributes nothing, and no diagnostic is emitted. This is the same failure class as the
unwired-layer pathology, one level down.

**Decision:** Add a `case _:` arm that logs a warning identifying the event type, and record the
unknown-event count on the `Session` so callers can observe replay fidelity.

**Rationale:** An event log that silently discards events it does not understand cannot be trusted as
an audit source — which is precisely what the migration needs it to be.

**Reversal condition:** None. Silently dropping durable events is never correct.

---

## ADR-0006 — Wire the durable layer at the composition root, not inside consumers

**Status:** ACCEPTED
**Phase:** P0
**Context:** `BackgroundAgentManager` accepts `run_store` and fully implements persistence. Two
production sites construct it without the argument (`composition.py:192`, `tool_executor.py:1666`).
The second site is a lazy fallback inside `_get_background_manager()`.

**Decision:** The **composition root** is the single place that constructs `SQLiteRunStore` and injects
it. `ToolExecutor._get_background_manager()` continues to exist as a fallback for direct
`ToolExecutor` users, and constructs a store only when it can reach one through its own dependencies;
otherwise it preserves today's in-memory behavior.

**Rationale:** Matches the existing `CompositionRoot` doctrine (`composition.py:1-11`: *"One place to
create and wire all services"*). Constructing the store inside the manager would scatter durable
ownership and make the rollback flag unreachable.

**Reversal condition:** None.

---

## ADR-0007 — `TOOL_CALL` events feed an audit trail, not the transcript

**Status:** ACCEPTED
**Phase:** P0
**Context:** Two event shapes could record a tool invocation: an
`ASSISTANT_MESSAGE` carrying a `tool_calls` list, or one `TOOL_CALL` event per call. The provider
protocol requires **exactly one** assistant message carrying all of an iteration's `tool_calls`
blocks, **immediately** followed by that iteration's replies (`runtime.py`, GH#6 regression guard).

If `Session.apply(TOOL_CALL)` appended a message per call, replay would produce a transcript that
strict providers reject with HTTP 400 — the exact defect the GH#6 guard exists to prevent.

**Decision:** Split the responsibilities by purpose.

| Concern | Event(s) | Lands in |
|---|---|---|
| Transcript reconstruction | `ASSISTANT_MESSAGE(tool_calls=…)` + `TOOL_RESULT(tool_call_id=…)` | `Session.messages` |
| Per-call audit / later graph reasoning | `TOOL_CALL(name, arguments)` | `Session.tool_calls` |

`TOOL_RESULT` gains an optional `tool_call_id` because the live transcript pairs replies to calls
**by id** (`_serialize_tool_exchanges`); a journaled reply without one cannot be replayed into a
provider-valid transcript.

**Consequence:** Replay reproduces `Session.messages` exactly (verified by round-trip test) *and*
rebuilds a tool-execution audit trail, without either corrupting the other.

**Reversal condition:** None. A second assistant message per call is never provider-valid.

---

## ADR-0008 — Journal events are derived from the message serializer, not reimplemented

**Status:** ACCEPTED
**Phase:** P0
**Context:** `_serialize_tool_exchanges` contains non-obvious pairing rules (GH#6: a gate-refused
call streams no call event while its denial reply still arrives, so positional pairing corrupts
mixed batches; identity comes from the reply's `tool_call_id`).

Journaling the same turn required the same pairing. A second implementation would be a **second
authority** for pairing — the exact defect class this migration exists to eliminate.

**Decision:** `_serialize_tool_exchanges` returns the `SessionEvent` list as well as writing the
messages, from **one walk**. `journal=False` returns an empty list and builds no event objects.

**Consequence:** The transcript and the event log cannot disagree about which reply answered which
call. The rollback flag cannot change the transcript.

**Reversal condition:** None.

---

## ADR-0009 — Do not widen a pinned internal signature to carry a new concern

**Status:** ACCEPTED
**Phase:** P0
**Context:** The first implementation of the journal write added a positional
`journal_events` parameter to `AgentRuntime._persist_turn_state`. Two tests broke:

```
tests/reliability/test_13h5_success_derivation.py::TestFlagCompatibility::
    test_flag_false_on_terminal_error
tests/reliability/test_13h5_success_derivation.py::TestFlagCompatibility::
    test_flag_true_on_clean_done
```

`TestFlagCompatibility._spy` wraps that method with the pinned 6-parameter signature
(`(session, sid, prompt, turn_succeeded, seq_num, terminal_error=None)`) to observe the
`turn_succeeded` flag. The extra positional argument made the wrapper raise.

**Decision:** Revert the signature. The journal write becomes its own off-loop step,
`_journal_turn_events(sid, events)`, called **before** `_persist_turn_state`.

**Rationale:** The test is a legitimate guard on success derivation, and the directive forbids
weakening tests to accommodate a change. Widening an internal contract is also the wrong shape
regardless: "persist terminal turn state" and "journal the turn body" are different concerns, and
they deserve different call sites. The fix also preserves the ordering guarantee — the journal write
completes before the terminal DONE, so no reader observes a DONE whose body is missing.

**Consequence:** A breaking signature change was caught by an existing guard and corrected at the
source rather than at the test.

**Reversal condition:** None.

---

## ADR-0010 — The append-only journal IS the idempotency record

**Status:** ACCEPTED
**Phase:** P1
**Context:** P1 requires durable idempotency so a crash between a tool's side
effect and its record cannot cause the effect to repeat. The infrastructure already existed and was
unused: an `idempotency` table (`infra/store.py:190-196`), `RunStore.idempotent_get/put`, and
`Scheduler.already_done/memoize/recall` — **none of which had a production caller** (only
`test_runs_scheduler.py` / `test_runs_store.py`).

The obvious move is to wire that table up. Three problems:

1. `idem_put` is **first-write-wins** (`ON CONFLICT DO NOTHING`), so it cannot hold both an *intent*
   marker and a later *result* under one key.
2. The table has **no TTL and no scope**, so keys accumulate forever and collide across sessions.
3. A row saying "this ran" is not enough. Exactly-once is unachievable for an arbitrary tool: if the
   process dies *after* the effect but *before* the record, the outcome is genuinely unknown. A table
   that pretends otherwise produces **false success**, which RULE 12 forbids.

**Decision:** Do not wire the idempotency table. Make the **event journal** the record, and use a
canonical action key (`wisp/core/action_key.py`) stamped on both sides:

| Journaled | Means | Written |
|---|---|---|
| `TOOL_CALL(action_key=K)` | intent — K was dispatched | **before** dispatch |
| `TOOL_RESULT(action_key=K)` | resolution — K's outcome is known | after the tool returns |

`Session.unresolved_actions()` then reports keys with a call and no *real* result. A `synthesized`
placeholder does **not** count as resolution: it records that the outcome was never learned, which is
exactly the state being reported.

**Consequence:** The record is bounded (it lives in the log that is written anyway), scoped (per
session by construction), and honest — it reports *ambiguity* instead of asserting an outcome.
Recovery surfaces the unresolved action; it must not silently repeat it.

**Reversal condition:** If a future phase requires cross-session or cross-process action dedup, the
`idempotency` table becomes appropriate — but it must then be scoped and TTL'd, and it must not be
used to claim an outcome that was never observed.

---

## ADR-0011 — Incremental journaling reuses the turn-end grouping rule

**Status:** ACCEPTED
**Phase:** P1
**Context:** P0 wrote the whole turn body once, in `run_turn`'s `finally` block. A `finally` never
runs on SIGKILL, so a killed turn left only its `user_message` on disk. P1 must journal each exchange
as it closes.

The trap: the exchange-grouping rule (GH#6) is non-obvious, and P0 already relied on it for the
provider transcript. A second copy of the rule for the journal would be a **second authority** — and
a drift between them would corrupt the *transcript*, not merely the log.

**Decision:** Extract the rule into `_group_exchanges(tool_sequence, closed_only=)` and route **both**
writers through it. The incremental writer calls it with `closed_only=True` on every `tool_result`;
the turn-end serializer calls it with the default. The incremental writer commits a prefix of the
turn-end event list, so the turn-end writer slices that prefix off.

This is sound only because grouping is **sequential** — later events never re-open an earlier
exchange — which is pinned by
`test_turn_journal_incremental.py::TestBoundaryAgreement::test_grouping_helper_is_sequential_and_prefix_stable`.

**Consequence:** Both writers agree on where an exchange ends by construction. The flag
`turn_journal` changes *when* events land, never *which* — pinned by
`test_flag_off_and_on_produce_the_same_final_journal`.

**Reversal condition:** None.

---

## ADR-0012 — P1 does not touch the hot path in `stateless.py`

**Status:** ACCEPTED
**Phase:** P1
**Context:** `WISP_MIGRATION_PLAN.md` listed `core/stateless.py` (`_turn_inner`) among P1's affected
components, on the premise that a `tool_call` event must be written before dispatch and the engine
was the only place that knew about dispatch.

**Evidence:** The engine **already yields the `tool_call` event before dispatching** —
`yield _flatten_event(tc_event)` (`stateless.py:535`), and the batch path's single-call form yields at
the end of its gate block; `self._execute_tool(...)` only runs later (`:812`). The runtime consumes
that generator, so when it handles the `tool_call` event, dispatch has not started — and the
journal write is awaited there.

**Decision:** Implement P1's journaling entirely in `core/runtime.py`. `stateless.py` is untouched.

**Rationale:** The engine is the hottest, most heavily guarded file in the codebase (13 gates, GH#6
regression guards, salvage logic). Not modifying it removes the single largest risk from P1.

**Consequence:** The plan's "Components Affected" list is wrong for P1; corrected in
`PHASE_P1_REPORT.md` §3.

**Reversal condition:** If a future phase needs the *result* journaled from inside the engine (e.g.
before a post-processing step that can fail), revisit.

---

## ADR-0013 — The verdict record rides in the existing hash-chained sink, in a structured envelope

**Status:** ACCEPTED
**Phase:** P2
**Context:** P2 must record the authorization verdict (`controlling_layer`) for
**both** allow and deny. The deny path already records it — interpolated into a denial message
(`tool_executor.py:722`). The allow path records nothing, so `allow` and `approval` are
indistinguishable from "no gate ran".

Three candidate homes:

| Option | Problem |
|---|---|
| Add a `controlling_layer` column to `audit_log` | `ImmutableAuditTrail.verify()` recomputes each row's hash from its own column values, so adding a field to the payload **invalidates the chain for every pre-existing row** — destroying tamper-evidence to gain a record |
| A new session event + a verdict channel from the executor | The executor is shared across sessions and does not know the session id; a shared mutable channel would be racy |
| The existing `record_decision(action, tool_name, workspace, allowed, reason, args_summary)` | No new column, no new channel — but `args_summary` is a TEXT blob, so is it "structured"? |

**Decision:** Use `ImmutableAuditTrail.record_decision`, the purpose-built decision recorder that
`AuditLog` already maps onto (`tools/audit.py:142`), and store the layer **twice**:

- `reason` → `"[Allowed by <layer> layer: ...]"`, deliberately **symmetric** with the existing
  `"[Denied by <layer> layer: ...]"` so one query covers both outcomes;
- `args_summary` → a JSON envelope `{"decision": "authorized", "layer": ..., "approval_required": ...,
  "obligations": [...], "args_keys": [...]}`, which is queryable and hash-covered.

**Rationale:** The record is the goal; a schema migration that breaks the tamper-evident chain of every
historical row is a worse trade than a structured envelope inside an existing column. Symmetry with the
deny format means the two outcomes are not two conventions.

**Consequence:** Every call now yields exactly one verdict row — on the allow path or the deny path,
never both (the recorder sits *after* the fork, pinned by
`test_proposal_boundary_no_bypass.py::test_verdict_record_follows_the_allow_deny_fork`).
The chain still verifies (pinned by `test_chain_verifies_after_mixed_allow_and_deny`).

**Reversal condition:** If audit records need to be queried by layer with an index, add the column and
accept a chain-format version bump for new rows.

---

## ADR-0014 — The safety net for a gate-touching change is written RED-first

**Status:** ACCEPTED
**Phase:** P2
**Context:** P2's stated risk is behavioural drift in `ToolExecutor`'s gate chain. The plan requires
`test_gate_order_unchanged.py` to be written **before** the change, and cites the Phase 10 precedent
(`CONTEXT.md:424`, "Behaviour verified IDENTICAL on a 29-item corpus").

**Decision:** `tests/test_gate_order_corpus.py` was written and made green against the **unmodified**
implementation, then the P2 insertion was made, then the corpus was re-run. It fingerprints each case
by its **structured** outcome — the machine-readable `status` plus the deciding layer parsed from the
structured `reason` — never by prose.

**Rationale:** A corpus written *after* a change records the change, not the baseline. Fingerprinting
on structured fields means a wording change cannot break the test while a *decision* change always
will.

**Consequence:** The corpus immediately earned its place — it revealed that a `read_only` denial is
decided by the **policy-engine gate, which runs before the `authorize()` consult**, so it names no
layer. That ordering is real, was previously undocumented, and is now pinned.

**Reversal condition:** None.

---

## ADR-0015 — The proposal boundary records; it does not decide

**Status:** ACCEPTED
**Phase:** P2 (completion)
**Context:** P2's objective — "make reasoning produce proposals that validation disposes, instead of
model output reaching effects directly" — can be read two ways:

1. **Inversion:** insert a proposal type *between* the model and the gates, so validation becomes an
   explicit stage the proposal passes through.
2. **Record:** the gates already *are* the validation stage; make their disposition observable by
   recording the proposal and the outcome.

The plan leans (1) — it lists `core/stateless.py` (`_turn_inner`) as an affected component and
proposes wrapping the dispatch at `stateless.py:812`.

**Evidence against (1):** the gates run **inside** `ToolExecutor.execute`, which is downstream of the
dispatch call. Wrapping the dispatch site would place the proposal boundary *before* validation
without any way to observe its disposition — the exact inversion that requires re-plumbing 19 gates.
And the migration's own constraint is explicit: *"The proposal layer adds a record, not a decision
procedure."*

**Decision:** Implement (2). `wisp/core/proposal.py` builds a `ToolRequest` for every call that
reaches dispatch and a `ToolResult` outcome for every proposal, including rejections. Both are
journaled as **audit-only** session events (`PROPOSAL`, `OUTCOME`) by the runtime, which already sees
the call before dispatch and the result after (the P1 finding).

**Why audit-only is load-bearing:** the transcript is rebuilt from
`ASSISTANT_MESSAGE(tool_calls=…)` + `TOOL_RESULT`. If `PROPOSAL` or `OUTCOME` also appended to
`messages`, replay would duplicate every tool reply. `Session.apply` therefore records them in
dedicated lists and never touches `messages`.

**Status derivation is delegated:** `classify_result()` in `core/events.py` is the ONE authority for
"what kind of outcome is this?". `proposal.py` maps its `OutcomeClass` onto the frozen
`ToolResult.STATUSES` rather than re-deriving any predicate — a second classifier is what let
benchmark error accounting miss every structured denial.

**Consequence:** The boundary is observable, replayable, and queryable, with **zero** changes to
`stateless.py` and one insertion in `tool_executor.py`. The mapping is lossy (`OutcomeClass` has 8
values, `STATUSES` has 4), so the precise class is always carried in `metadata["outcome_class"]` —
documented rather than hidden.

**Reversal condition:** If a future phase needs to *reject* a proposal before the gates run (e.g. a
plan-level veto), that is a real inversion and needs its own phase with its own RED-first corpus.

---

## ADR-0016 — P3 ships in two stages, and stage 3a does not gate

**Status:** ACCEPTED
**Phase:** P3
**Context:** P3 changes the **completion semantics of the product**. A stricter
gate can make previously-"successful" turns report `INCONCLUSIVE`. The plan rates it *"High — the
highest in the plan"* and prescribes shipping in two stages: **3a** introduces criteria and evidence
and *records* the verdict without gating; **3b** enables the gate behind a flag after a measurement
period showing how many turns become `INCONCLUSIVE`.

**Decision:** Implement 3a exactly as staged. `core/acceptance.py` computes a
`CompletionVerdict` (`PASS` / `FAIL` / `INCONCLUSIVE`) and the runtime records it as an audit-only
`VERDICT` session event — **and nothing on the completion path consumes it.**

`turn_succeeded` still derives from terminal evidence alone (13-H5), and
`VerificationFloorGuard` still owns the completion invariant. Pinned by
`TestStage3aDoesNotGate::test_completion_rule_is_unchanged`.

**Why the verdict vocabulary is new rather than reused.** Both existing vocabularies were examined and
neither can express the required verdict:

| Existing | Why it cannot |
|---|---|
| `VerificationFloorGuard.resolved()` | a boolean: `wrote_code and verify_ok_after_edit is True`. It cannot say "I could not tell", and it is the *actor's* own bookkeeping |
| `VerificationResult.decision` (`graph/verifier.py:19`) | `("ALLOW","REJECT","RETRY","ESCALATE")` — a **router's** vocabulary; every value presumes a verdict was reached |

Routing is therefore **derived** from the verdict (`route_for()`), keeping the graph vocabulary as an
output rather than a competitor: one authority for "what is true", another for "what to do about it".
`INCONCLUSIVE` routes to `RETRY`, never `ALLOW` — the whole point of distinguishing it is that it must
not be mistaken for success.

**Consequence:** The gate can be justified by a measured `INCONCLUSIVE` rate instead of a guess.

**Reversal condition:** 3b is a separate decision, taken on the measurement.

---

## ADR-0017 — The floor criterion is an implication, not a predicate

**Status:** ACCEPTED
**Phase:** P3
**Context:** Demoting `VerificationFloorGuard` to one deterministic criterion, the obvious projection
is `check=lambda _: guard.resolved()`. That reports **FAIL** for a turn that mutated nothing —
`resolved()` requires `wrote_code` — which is a claim the evidence does not support and which the
guard itself never makes: `rejection()` returns `None` when `wrote_code` is `False`.

**Decision:** Express the criterion as the implication it actually is:
`check=lambda _: (not guard.wrote_code) or guard.resolved()`.

**Consequence:** A turn that mutated nothing passes the check and then produces no evidence for it,
landing on **`INCONCLUSIVE`** — the honest answer, since there was nothing to verify. The four cases
are now distinct and each is pinned:

| Turn | Verdict |
|---|---|
| mutated nothing | `INCONCLUSIVE` (criterion vacuously holds, no evidence exists) |
| mutated, unverified | `FAIL` (deterministic check fails) |
| mutated, verified | `PASS` |
| mutated again after verifying | `FAIL` (the guard's invalidate-on-mutation rule) |

**Reversal condition:** None. A vacuous criterion is satisfied, not failed.

---

## ADR-0018 — The engine publishes its guard; the runtime only reads it

**Status:** ACCEPTED
**Phase:** P3
**Context:** Stage 3a must record a verdict against the floor guard, but the guard lives in
`core/stateless.py::_turn_inner` while the journal is written by `core/runtime.py`. `AGENTS.md` states
*"Stateless core — `WispAgentCore` has no mutable state."*

Three options:

| Option | Problem |
|---|---|
| Runtime re-derives `wrote_code` / `verify_ok_after_edit` from observed tool events | A **second implementation of the floor rule** — a second authority for "was this verified", the defect class this migration exists to remove |
| Engine yields the verdict as a new event type | Changes the event stream every transport consumes, for an audit record |
| Engine publishes the guard; runtime reads it | A narrow exception to the stateless rule |

**Decision:** Publish the guard (`self._last_guard = guard`) and have the runtime read it via
`getattr(core, "_last_guard", None)`. The exception is narrow and its limits are documented at the
assignment:

- It is **not session state** — a per-turn handle, overwritten each turn, read only by the runtime
  that just ran that turn.
- It is **race-free in practice**: cores are cached per (session, fingerprint), so sessions never
  share one, and concurrent turns on the *same* session are serialized by the session lock.
- It keeps **one** floor implementation.

**Consequence:** The acceptance model is reachable (RULE 11) without a second floor rule and without
touching the event stream.

**Reversal condition:** If a future phase needs the verdict per-node rather than per-turn, pass it
through the return channel instead of publishing it.

---

## Decision index

| ADR | Title | Phase | Status |
|---|---|---|---|
| 0001 | Evolve the existing four layers; do not replace them | P0 | ACCEPTED |
| 0002 | One rollback flag per concern, read via `getattr` with a safe default | P0 | ACCEPTED |
| 0003 | No `RunStatus`→`RunState` coercion shim is needed | P0 | ACCEPTED |
| 0004 | Persistence is best-effort and must never break a turn | P0 | ACCEPTED |
| 0005 | Fail loud on unknown session event types during replay | P0 | ACCEPTED |
| 0006 | Wire the durable layer at the composition root, not inside consumers | P0 | ACCEPTED |
| 0007 | `TOOL_CALL` events feed an audit trail, not the transcript | P0 | ACCEPTED |
| 0008 | Journal events are derived from the message serializer, not reimplemented | P0 | ACCEPTED |
| 0009 | Do not widen a pinned internal signature to carry a new concern | P0 | ACCEPTED |
| 0010 | The append-only journal IS the idempotency record | P1 | ACCEPTED |
| 0011 | Incremental journaling reuses the turn-end grouping rule | P1 | ACCEPTED |
| 0012 | P1 does not touch the hot path in `stateless.py` | P1 | ACCEPTED |
| 0013 | The verdict record rides in the existing hash-chained sink, in a structured envelope | P2 | ACCEPTED |
| 0014 | The safety net for a gate-touching change is written RED-first | P2 | ACCEPTED |
| 0015 | The proposal boundary records; it does not decide | P2 | ACCEPTED |
| 0016 | P3 ships in two stages, and stage 3a does not gate | P3 | ACCEPTED |
| 0017 | The floor criterion is an implication, not a predicate | P3 | ACCEPTED |
| 0018 | The engine publishes its guard; the runtime only reads it | P3 | ACCEPTED |
