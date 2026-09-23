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

> **REACHED — see ADR-0027 (M4).** P2 landed, but the decisive change was **M2**, which promoted the
> journal from secondary to primary. The resolution is not fail-loud writes; it is a checkable
> invariant (`Session.gap_detected`) that makes silent loss detectable at the read.

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

## ADR-0019 — The task graph journals through `UnifiedStore`, not `GraphStore`

**Status:** ACCEPTED
**Phase:** P4
**Context:** P4 says to *"reuse `wisp/graph/`'s types, validator, store and scheduler."* The types and
legality rules are reused unchanged. The **store** is not, and the reason is structural:
`GraphStore.__init__` opens **its own SQLite database** (`graph/store.py:112-120`, `_conn()` at
`:122-129`), separate from `UnifiedStore`.

**Decision:** Reuse `wisp/graph/`'s **types** (`NodeType`, `NodeStatus`) and its **legality discipline**
(the `LEGAL_TRANSITIONS` shape from `runs/record.py`), but journal the graph through `UnifiedStore` as
`TASK_GRAPH` + `NODE_TRANSITION` session events.

**Rationale — two reasons, one of them the plan's own risk note:**

1. A second SQLite database would fragment the durable record that P0-P3 spent four phases
   consolidating into one append-only journal. "Which file holds the truth about this turn?" should
   have one answer.
2. P4's stated risk is *"divergence between the graph and the message list."* The plan's own mitigation
   is to make the message list a **projection of the graph** (`WISP_TARGET_ARCHITECTURE.md` C6). A graph
   that replays from the same journal as the transcript is what makes that possible; a graph in a
   separate database could not be projected from, only joined to.

**Consequence:** `Session.rebuild_task_graph()` reconstructs the graph from the log alone, so the
persisted graph is a **cache** and the journal is the truth. Pinned by
`test_session_rebuilds_its_graph_from_the_log`.

**Reversal condition:** If the graph ever needs to be queried independently of a session — cross-session
analytics, for instance — `GraphStore` becomes the right home, and the projection property is lost
deliberately rather than accidentally.

---

## ADR-0020 — `PENDING` may settle directly; terminal states stay frozen

**Status:** ACCEPTED
**Phase:** P4
**Context:** The first `LEGAL_NODE_TRANSITIONS` allowed only
`PENDING → RUNNING | SKIPPED | CANCELLED`. Materializing a graph **retroactively** from a completed
turn immediately failed with `illegal node transition: pending -> success`.

**Decision:** Allow `PENDING` to settle directly (`→ SUCCESS | FAILURE | TIMEOUT`), in addition to via
`RUNNING`. Terminal states keep **no** outgoing edges.

**Rationale:** This is not laxity. A graph materialized from a turn that already finished has no
observed `RUNNING` step to record, and a node whose work was instantaneous has none either. Refusing
the settlement would force a caller to write a transition that never happened — manufacturing history
to satisfy a state machine. The errors the machine still catches are the ones that matter: anything
leaving a **terminal** state, an unknown node, and a stale `from_status`.

**Consequence:** `success -> running` is still refused (pinned by
`test_illegal_transition_is_refused`); `pending -> success` is permitted.

**Reversal condition:** If a future phase needs to distinguish "ran and succeeded" from "settled
without running", add an explicit `RUNNING` requirement for the node kinds where that distinction is
load-bearing — not globally.

---

## ADR-0021 — The extended node vocabulary is a superset, and Layer B's `NodeStatus` is untouched

**Status:** ACCEPTED
**Phase:** P5
**Context:** P5 asks for seven new node states and notes that `SKIPPED` conflates
*"predecessor failed"* with *"dead branch"* — a real ambiguity. The plan names `graph/types.py` as the place
to add them.

**Two pieces of evidence made editing `NodeStatus` the wrong move:**

1. **`graph/scheduler.py::is_finished` lists terminal statuses *explicitly*.** A new terminal state
   there would make `is_finished` return `False` forever — a run that never completes. `_final`,
   the join policies and the retry semantics in `graph/executor.py` (1149 lines) carry the same
   hazard.
2. **The plan's own safety net for that change does not exist.** P5's completion criteria require
   `test_graph_fuzz.py`, `test_graph_races.py` and `test_graph_resume.py` to pass — **none of the three
   is in the repository.** And Layer B's executor has **zero** references from `core/runtime.py` or
   `core/stateless.py`: it is not on the live turn path at all.

**Decision:** Define `TaskNodeState` as a strict **superset** of `NodeStatus` in
`wisp/core/task_graph.py`, with every shared value an identical string, and leave `NodeStatus` alone.

**Precedent:** this is exactly the shape P0 verified for `RunState` (8) ⊃ `RunStatus` (7), which needed
no coercion shim (ADR-0003). ADR-0003's own recommendation was to promote that relationship to a
ratchet; `test_task_node_state_is_a_superset_of_node_status` does so here.

**Consequence:** The states are added where the live loop's graph lives. `coerce_node_state()` accepts
both vocabularies, so a P4 graph persisted with `NodeStatus` values still loads. Layer B is untouched
until its safety net is real.

**Reversal condition:** When `test_graph_fuzz.py`, `test_graph_races.py` and `test_graph_resume.py`
exist, adding the states to `NodeStatus` becomes a bounded change and the superset can be collapsed.

---

## ADR-0022 — `TERMINAL` means settled, not final

**Status:** ACCEPTED
**Phase:** P5
**Context:** P4's `TERMINAL_NODE_STATUSES` was documented as *"terminal states have no outgoing edges"*
and asserted as such. P5 requires that a settled node can still be **invalidated** — a stale `SUCCESS`
is precisely what invalidation exists to demote. So `SUCCESS` must have an outgoing edge, and the old
equation breaks.

**Decision:** Split the concept in two:

| Name | Means | Contains |
|---|---|---|
| `TERMINAL_NODE_STATES` | **settled** — releases downstream work | success, failure, timeout, cancelled, skipped, invalidated, superseded |
| `FINAL_NODE_STATES` | **no outgoing edges** — history, not rewritten | invalidated, superseded |

**Rationale:** One word was carrying two meanings, and P5 is what forced them apart. Keeping the
conflation would have made either invalidation impossible or readiness wrong.

**Consequence:** `test_terminal_states_have_no_outgoing_edges` was rewritten against
`FINAL_NODE_STATES`, and a new test asserts that a settled node *can* be invalidated.

**Reversal condition:** None.

---

## ADR-0023 — A node with no incoming edges is ready

**Status:** ACCEPTED
**Phase:** P5
**Context:** `graph/scheduler.py:41` leaves a non-entrypoint root with no incoming edges **pending**,
calling it a *"dead definition"*. That is correct for a **compiled** graph, where a root with no edges
is a mistake.

P5 introduces `create_node(graph, node, deps=[])` — a **runtime-created independent root**, which is
intentional: a parallel task. Under the compiled-graph rule it would stay pending forever.

The first implementation tried to refuse it at creation time. That was wrong: it rejected a legitimate
request to avoid a footgun the rule itself created.

**Decision:** `_compute_ready` treats any `PENDING` node with no incoming edges as ready, entrypoint or
not. The divergence from `graph/scheduler.py:41` is deliberate and documented in the function.

**Consequence:** `create_node(..., deps=[])` produces a runnable parallel task rather than a node that
hangs. `expand()` is unaffected (its caller supplies edges explicitly).

**Reversal condition:** If a future phase needs to distinguish "compiled root" from "runtime root", the
distinction belongs on the node (a `origin` field), not in the readiness rule.

---

## ADR-0024 — The denial rule is enforced by CLASS, not by matching statuses

**Status:** ACCEPTED
**Phase:** P6
**Context:** Denials must never auto-retry. This is not a new rule — `graph/subagent_orchestrator.py`
comments it, and **Phase 10 removed `_DENIAL_MARKERS`** precisely because it failed to enforce it: all
five canonical denial statuses matched **nothing** (`CONTEXT.md:456`). A prose-matching guard that
matches nothing is worse than no guard, because it reads as protection.

**Decision:** Make the rule structural.

- `classify_failure()` maps every denial status onto `FailureClass.SECURITY`, and **imports** the denial
  vocabulary from `core/events.py` rather than re-listing it. A local copy is the exact defect that was
  removed.
- `FORBIDDEN_RUNGS[SECURITY]` forbids every rung except escalation, so `is_legal_rung()` returns
  `False` for `RETRY` **by class**.
- Precedence is explicit: **denial outranks every other signal**, so a denied call that also looked
  transient is still `SECURITY` and the no-retry rule cannot leak.
- A test is parametrized over the **canonical** status set, so it cannot drift from the vocabulary it
  is supposed to cover.

**Consequence:** The rule is enforced by the type system and the taxonomy rather than by wording. An
AST test asserts no string literal `"POLICY_DENIED"` appears in `recovery.py`.

**Reversal condition:** None. Prose matching for security decisions is never correct.

---

## ADR-0025 — An unsafe rollback escalates instead of proceeding

**Status:** ACCEPTED
**Phase:** P6
**Context:** `runs/compensation.py` declares per-tool reversibility and its docstring says
*"No tool wiring"* — nothing called `reversibility()` or `rollback_preview()` until P6. Wiring it
raises an immediate question: what should the Rollback rung do for a tool declared `irreversible` (a
published `git push`) or `unknown` (any undeclared tool)?

**Decision:** **Escalate.** `plan_rollback()` refuses anything not declared `reversible`, and
`RecoveryLadder.decide()` converts that refusal into an escalation rather than proceeding.

**Rationale:** A recovery that makes things worse is the worst outcome available — worse than stopping,
because it destroys the evidence that would explain the original failure. Assuming an undeclared tool is
compensable is the specific assumption that produces that outcome. Refusal is the safe default.

**Consequence:** The compensation declarations finally have a production caller, and the failure mode
they were written to prevent is now structurally impossible rather than merely discouraged. Pinned by
`test_an_unsafe_rollback_escalates_instead` and `test_an_unknown_tool_is_refused`.

**Reversal condition:** If a tool is later proven compensable, declare it in `_REVERSIBILITY` — the
table is the single place that decides.

---

## ADR-0026 — The recovery ladder is a mechanism; the turn loop does not consult it yet

**Status:** ACCEPTED
**Phase:** P6
**Context:** P6's objective is to *"replace ad-hoc recovery with an explicit, budgeted, evidence-bearing
ladder."* The mechanisms exist independently today (stream attempts, transient retry, repair nudges,
the grind floor, cycle iterations) and the plan's rollback is a flag: off → today's mechanisms.

**Decision:** Ship the ladder as a complete, tested **mechanism** — taxonomy, rung table, budgets,
decisions, durable escalation, and the compensation wiring — and **do not** rewire the live turn loop's
recovery behaviour in this phase.

**Rationale:** Same reasoning as P5's item 5 (ADR not needed there — recorded as item M11). Rewiring the
turn loop's recovery changes behaviour on the *failure* path, which is the hardest path to test and the
one with the least existing coverage. The plan rates P6 "Medium" and names the risk as *"ordering and
budget interaction"* — precisely the things a live rewiring would disturb.

**Consequence:** `classify_failure()`, `plan_rollback()` and the ladder are reachable and tested, but
nothing on the live turn path calls the ladder. Recorded as item **M12**. Stated plainly in
`PHASE_P6_REPORT.md` §5 rather than implied.

**Reversal condition:** When the failure path has coverage comparable to the success path, wire the
ladder behind `WISP_RECOVERY_LADDER` and measure.

---

## ADR-0027 — Best-effort durability, revisited: the journal's *contiguity* is the precondition

**Status:** ACCEPTED — **supersedes the reversal condition of ADR-0004**
**Phase:** M4
**Context:** ADR-0004 made every durable write best-effort and stated its own reversal condition:

> *"Once a phase requires durable state as a **correctness** precondition (P2's proposal boundary is the
> likely candidate), silent best-effort is no longer acceptable for that path and must become fail-loud.
> Revisit at P2."*

P2 landed, and so did P3–P6. But **the decisive change was M2**, and it was not the one ADR-0004
anticipated. M2 promoted the journal from *secondary* to *primary* by making reconstruction
journal-first. That is what turns a permitted silent write failure from a lost observation into a
**silently truncated session**.

**The hole, verified.** With a `TOOL_RESULT` write lost (seq 2 absent, seqs `0, 1, 3`):

| Observation | Before M4 |
|---|---|
| replayed messages | `["user", "assistant", "assistant"]` — an assistant `tool_calls` block with **no tool reply** |
| `unknown_events` | **0** — it counts unrecognised event *kinds*, not missing ones |
| `reconstruction_source()` | **`"journal"`** — journal-first returns the broken transcript, authoritatively |

A strict provider rejects that message shape. So the failure mode M2 was designed to avoid — returning a
*worse* session than the blob — arrived by a second route, and nothing reported it.

**Decision.** Do **not** make the writes fail-loud. ADR-0004's core concern stands: a turn must not die
because a disk write failed, and making the journal fatal would convert an observability feature into an
outage — the exact outcome ADR-0004 was written to prevent.

Instead, make the **invariant checkable**:

1. `Session.gap_detected` — the journal's sequence is **contiguous** (`_journal_turn_events` stamps in
   order with no holes). A hole therefore means a write was lost. Contiguity is measured from the
   minimum present, so applying a single event is not a gap.
2. `reconstruction_source()` refuses a gapped journal and falls back to the blob.
3. `reconstruct()` reports `_gap` on the result.

**Why this is the right shape.** ADR-0004's own precedent is `persist_skipped_total` — a *canary*, not a
crash. The problem was never that the write could fail; it was that **nothing downstream could tell**.
`gap_detected` is that signal, and unlike a counter it is a property of the record itself, so it travels
with the data and cannot be forgotten by a caller that never read the counter.

**Consequence:** silent loss is still permitted at the write, and is now **detectable at the read**. The
classification, for the record:

| Record | Loss costs | Policy |
|---|---|---|
| Turn body (`ASSISTANT_MESSAGE` / `TOOL_CALL` / `TOOL_RESULT`) | the session — it is now the primary record | best-effort **write**, gap-checked **read** |
| `PROPOSAL` / `OUTCOME` (P2) | the authorization audit — compliance, not correctness | best-effort, canary |
| `VERDICT` (P3) | stage-3a measurement (nothing consumes it until 3b) | best-effort, canary |
| `TASK_GRAPH` / `NODE_TRANSITION` (P4) | graph↔transcript divergence | best-effort, canary |
| `RECOVERY` / `ESCALATION` (P6) | resumability — the escalation *is* the state | ~~best-effort, canary; open item M16~~ → **REACHED: ADR-0028.** The write is fail-loud and the read salvages rather than discards |

**Reversal condition:** If a future phase makes an *audit* record a legal obligation rather than a
diagnostic, that path needs fail-loud — and it will be a specific path, not the whole layer.

---

## ADR-0028 — A state-bearing record is not best-effort, in either direction

**Status:** ACCEPTED
**Phase:** M16
**Supersedes:** the `RECOVERY` / `ESCALATION` row of ADR-0027's classification

**Context.** ADR-0027 classified every durable record by what its loss costs, and left one row
explicitly unresolved:

> `RECOVERY` / `ESCALATION` (P6) — resumability, the escalation *is* the state — best-effort, canary;
> **open item M16**

Revisiting it found the policy wrong in **both** directions, and the **read** side was the worse of the
two.

**The read side — a live defect.** M4 made `reconstruction_source()` refuse a gapped journal and fall
back to the blob. That settles which *transcript* to trust, and says nothing about the journal-only
records, which are independent of the transcript's validity. Verified with a gapped journal whose
escalation had survived:

| Observation | Before M16 |
|---|---|
| the escalation in the journal | **present** (`esc-2`) |
| `gap_detected` | **True** — M4 detected the loss |
| `reconstruction_source()` | `"blob"` |
| the escalation in the result | **absent** — the blob has no `escalation` key |

So M4's fallback — added to *stop* a worse session being returned — silently discarded the state of a
parked run, and reported it only as `_gap`. A resume reading that result cannot tell why the run
stopped, or whether it should resume at all.

**The write side.** `AgentRuntime._journal_turn_events` swallowed every failure, on ADR-0004's rule that
a turn must never be broken by a failed write. That rule is right for a *record of what happened*,
whose loss is observable as a gap in the sequence. It is wrong for a **state transition**: if the write
that establishes the state is lost, continuing as though it landed is not a lost observation but a
**false record**.

**Decision.** Neither change makes a write fail-loud in general; ADR-0004 stands. Two targeted changes:

1. **The fallback salvages rather than discards.** `reconstruct()` carries the journal-only records under
   `_journal` on **both** paths, and names what a gap endangers in `_journal_records_at_risk`. Which
   *transcript* to trust and which *records* survived are two questions, and the code now treats them as
   two.
2. **A state-bearing batch is not swallowed.** `is_state_bearing(events)` is the single authority for
   which records are special; `_journal_turn_events` re-raises when a batch contains one, and stays
   best-effort otherwise.

**Why `ESCALATION` alone.** `HumanIntervention` is not a description of a parked run — it **is** the
parked run's state, and `resumable` reads it to decide whether to resume. `PROPOSAL`/`OUTCOME`,
`VERDICT`, `TASK_GRAPH`/`NODE_TRANSITION` and `RECOVERY` are records *about* a turn whose own behaviour
is unaffected by their loss. The set is pinned by a test parametrized over **every** other kind, so the
carve-out cannot widen by accident.

**Why a raise is safe here.** It is not silent. Inside the stream loop the turn's own handler turns it
into a recoverable error event the transport sees; on the turn-end path (a `finally`) it reaches the
caller of `run_turn`. Either way a caller learns the escalation was not durable — which is what it needs
to retry or tell an operator directly. And the turn is already stopping when an escalation is produced,
so this cannot abort work in progress.

**Consequence:** the escalation is durable in the sense that matters — a written escalation is readable,
and an unwritten one is reported. The `RECOVERY` rows stay best-effort, because the full ladder history
travels inside the intervention, so their loss is redundant rather than load-bearing.

**Reversal condition:** If escalation becomes answerable only through the journal (i.e. an operator
answers via an event rather than through the transport), the *answer* path inherits this ADR's
treatment and needs its own analysis.

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
| 0019 | The task graph journals through `UnifiedStore`, not `GraphStore` | P4 | ACCEPTED |
| 0020 | `PENDING` may settle directly; terminal states stay frozen | P4 | ACCEPTED |
| 0021 | The extended node vocabulary is a superset; Layer B's `NodeStatus` is untouched | P5 | ACCEPTED |
| 0022 | `TERMINAL` means settled, not final | P5 | ACCEPTED |
| 0023 | A node with no incoming edges is ready | P5 | ACCEPTED |
| 0024 | The denial rule is enforced by CLASS, not by matching statuses | P6 | ACCEPTED |
| 0025 | An unsafe rollback escalates instead of proceeding | P6 | ACCEPTED |
| 0026 | The recovery ladder is a mechanism; the turn loop does not consult it yet | P6 | ACCEPTED |
| 0027 | Best-effort durability, revisited: the journal's *contiguity* is the precondition | M4 | ACCEPTED (supersedes ADR-0004's reversal condition) |
| 0028 | A state-bearing record is not best-effort, in either direction | M16 | ACCEPTED (supersedes ADR-0027's `ESCALATION` row) |
