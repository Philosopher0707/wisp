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

## ADR-0029 — The graph is a shape, not a payload; the transcript projects from the journal

**Status:** ACCEPTED
**Phase:** M9
**Supersedes:** the M9 statement in `WISP_MIGRATION_STATUS.md` ("the message list as a projection of the
graph")

**Context.** M9 was recorded as the migration's keystone — *"the message list is not yet a projection of
the graph"* — with five items (M11–M15) blocked on it. The target architecture states the model:

```
Immutable Event Journal  ──materialize──►  Graph State  ──project──►  Execution View
```

and says the message list is a view "projected from **both**". Reconnaissance checked the strong reading
against the repository and found it is not expressible, and that the weaker reading is currently broken.

**Finding 1 — the graph cannot project the transcript.** `TaskNode` carries `node_id`, `kind`,
`iteration`, `status`, `ready`, `deps`, `detail`, `superseded_by`. No tool name, no arguments, no result,
no assistant text. Verified behaviourally as well as structurally: a real turn's graph serialises with no
message content in it. To make the graph the *source* of the transcript, the transcript would have to be
copied into the nodes — a second copy that can disagree with the first, which is the defect class this
migration exists to remove, and precisely what P2 warned about when it made `PROPOSAL`/`OUTCOME`
audit-only ("a second path into `messages` would duplicate every tool reply on replay").

**Finding 2 — the nodes are a count, not an identity.** `build_turn_graph(run_id, n)` generates
`turn:0 … turn:n-1` from `_work_units = len(exchanges) + 1`. A node does not reference the exchange it
stands for. So the graph is a *lower bound on iterations* with a linear chain, not a structure over the
work. **This, not payload, is the real precondition for M11**: for the graph to drive execution a node
must be an *identified* unit of work.

**Finding 3 — the projection that does exist is not faithful.** `runtime.py` states the invariant at the
construction site: *"replay replaces the live transcript, so the log has to reproduce `messages`
exactly."* It did not. `Session.apply`'s `TOOL_RESULT` case built
`{role, content, name, tool_call_id}` while `_exchange_parts` builds `{role, tool_call_id, content}` — an
extra `name` key on every replayed tool reply. Two consequences, both verified:

| Consequence | Evidence |
|---|---|
| the journal and the **blob** disagreed on the same session | `blob["messages"] != replay.messages`; so `reconstruct()` returned different messages depending on which source it chose |
| a consumer branched on the divergence | `context_pruner._get_tool_name_for_result` reads `name` first; live took the `tool_call_id` fallback, replay short-circuited |

The divergence never reached a provider — `providers/openai.py` normalises tool messages to
`{role, tool_call_id, content}` — so it was latent rather than broken. Latent is the point: it is a second
producer of the same structure, and the next consumer to read `name` inherits a difference that no test
covered.

**Decision.**

1. **The graph stays a shape.** It is not given transcript payload. The ratchet
   `test_task_nodes_carry_no_transcript_payload` fails if a payload field appears on `TaskNode`, so the
   decision cannot be reversed by a convenience change.
2. **The transcript projects from the journal**, which is what `WISP_TARGET_ARCHITECTURE.md` already says
   (*"the journal is the source of truth for what happened"*). The graph contributes status and
   structure, not content.
3. **The projection must be faithful, and faithfulness is now an invariant with a test.**
   `Session.apply` produces the live shape exactly; `test_the_journal_reproduces_the_transcript_exactly`
   compares a real turn's live transcript against its replay, and
   `test_a_tool_reply_carries_only_the_protocol_keys` asserts the shape directly.

**Why the live shape wins rather than the replayed one.** The live shape is what the engine has always
produced and what the blob stores, so aligning the replay changes one branch in `Session.apply` instead
of changing what every consumer of `messages` sees. `name` is also redundant in the message — it is in
the assistant's `tool_calls` block, in `Session.tool_calls`, and resolvable from `tool_call_id` — and
adding it to the live path would put a key into `messages` that every provider adapter strips again.

**Consequence:** M9 is closed as *"the execution view is faithful and the graph is a shape"*, not as an
inversion. M11–M15 remain open, but their prerequisite is now stated correctly: **node identity**, not
message-list derivation. M12, M14 and M15 were recorded as blocked on M9 and are in fact independent of
it.

**Reversal condition:** If a future phase needs the graph to drive execution (M11), the change is to give
nodes identity and a reference to their work unit — **not** payload. If a proposal to put transcript
content into `TaskNode` appears, it needs a superseding ADR that explains how the second copy is kept
from diverging.

---

## ADR-0030 — A subagent's identity travels with the call, not the executor

**Status:** ACCEPTED
**Phase:** M15
**Supersedes:** P9's `ToolExecutor(principal=…)` as the *only* way to supply a subagent identity

**Context.** The plan calls narrowing a subagent's authority *"the single most important fix in the
delegation layer."* P9 built the plumbing — `auth.principal.child_principal()` and
`ToolExecutor(principal=…)` — and recorded the spawn site as the remaining gap (M15), with a **tripwire
test** asserting it was still unwired.

The gap, verified: `_runner._run_agent` builds the child core with
`tool_executor=self._tool_executor` — the **parent's** executor, whose `principal` is `None`. So
`ToolExecutor.execute` fell back to `local_principal(...)`: a `HUMAN` principal with
`capabilities=None`, i.e. **unbounded**. Every child's tool call was authorized as the local human.

The child's *tool list* was already narrowed (`session_dict["allowed_tools"]`, enforced by the core as a
schema filter). What was missing is the **authorization identity**: L1 of `authorize()` denies a tool the
principal lacks, and the principal was always the unbounded human.

**Why not a per-child executor.** The obvious fix — construct a `ToolExecutor` per child with
`principal=…` — is wrong here, and reading the constructor is what shows it: `ToolExecutor.__init__`
creates **two** `ThreadPoolExecutor`s (`_tool_pool`, `_network_pool`) whose shutdown the composition root
owns. A per-child executor would create two pools per subagent that nothing ever closes, and `fanout`
spawns many. The identity therefore travels with the **call**, not the object.

**Decision.**

1. **`ToolExecutor.execute(..., principal=None)`** — the identity to authorize as for this call.
   `None` preserves the previous behaviour exactly, so the change is additive for every existing caller.
2. **One precedence authority.** `ToolExecutor._effective_principal` resolves per-call principal >
   executor principal > unbounded local human, delegating the last two steps to the new
   `auth.principal.executor_principal()`. The subagent runner uses the **same** function to find the
   parent it derives a child from, so a child's `parent_principal_id` cannot name a principal its
   parent's own calls never authorize as.
3. **`child_principal(parent, contract, capabilities=…)`** — an explicit override for a caller that has
   already resolved the contract's tools. `_effective_child_tools` turns `"all"` and the permission mode
   into a concrete list *before* the principal is derived; `child_principal` correctly refuses to
   **guess** at `"all"`, and here there is nothing to guess. The override narrows only —
   `derive_subagent` still refuses a widening.
4. **Both child paths stamp it.** `_run_agent` (stateless core) and `_run_via_runtime` (`AgentRuntime`)
   build *separate* session dicts. Wiring one and not the other is the half-fix this migration keeps
   finding, so a test asserts both.

**Consequence — what actually changed.** The child inherits the parent's **permission mode** but not the
parent's **contract**, and the principal layer is now the gate that enforces the contract:

| Gate | Question | Runs |
|---|---|---|
| policy engine | is this tool permitted **in this mode**? | first — and names no controlling layer (F15) |
| `authorize()` L1 | does this **principal** have this capability? | second |

So a child declared `read_file` that calls `write_file` in `auto_edit` is now denied
`[Denied by principal layer: principal … lacks capability write_file]`. Before M15 it was permitted,
because the principal was the unbounded human. `run_bash` is *not* the test case: the policy gate denies
it in `auto_edit` before `authorize()` runs, which is pinned by its own test so the ordering is not
mistaken for redundancy.

**Reversal condition:** If a future phase needs a *different* executor per child (e.g. per-child sandbox
configuration), that executor must take its thread pools from a shared, owned pool — otherwise this ADR's
reason for existing is lost and the leak returns.

---

## ADR-0031 — Prompt sections are classified, and untrusted content is not in instruction position

**Status:** ACCEPTED
**Phase:** M14
**Closes:** P8's deferred item 1 ("trust tags on every context item")

**Context.** P8 built the trust boundary — `wisp/core/context_trust.py` with `TrustTag`, the T1–T4 rules,
fencing, and a structured `dropped` list — and shipped it **tagging-only, with no production caller**.
`context_trust` was imported by its own test and nothing else; no code anywhere built a `ContextItem`.

So the boundary was a *written-but-unwired control*, the pattern `docs/audit-2026-08-24.md` names as
dominant in this codebase. Classifying the sections found the reason that matters:

**T1 was violated, live.** `config.load_context_files()` reads workspace files — `CLAUDE.md`,
`.wisp/rules.md`, `~/.config/wisp/CLAUDE.md` — and `ContextAssembler` appended their content at priority
**−1**, i.e. *before* `default_system`. Verified:

| Observation | Before M14 |
|---|---|
| a workspace file containing `IGNORE ALL PREVIOUS INSTRUCTIONS…` | appears at **offset 23** |
| `## SYSTEM RULES` | appears at **offset 84** |
| fenced or labelled? | **no** — `"<<UNTRUSTED" not in prompt` |

A repository whose `CLAUDE.md` contains an instruction put that instruction **ahead of the rules that
forbid it**. T1 exists for exactly this, and the boundary that would have caught it had no caller.

**Decision.**

1. **`SECTION_TRUST`** — one table classifying every section the assembler can append, by name.
   `INSTRUCTION_PRIORITY = 0` names the tiers that carry instructions.
2. **`untrusted_sections_in_instruction_position(sections)`** — T1 as a predicate. **It fails closed**:
   a section nobody classified counts as untrusted, because "unclassified" has no answer to *"may
   repository content sit here?"* and assuming safety is the wrong default for a trust boundary.
3. **`context_files` moves from priority −1 to 1** — out of instruction position, still near the top.
   This is the T1 fix and it is a **move, not a demotion**: the operator's conventions stay high priority.
4. **The classification is total, and enforced as such.** `test_every_appended_section_is_classified`
   reads the `sections.append((...))` calls by AST and fails on a name missing from `SECTION_TRUST`, so a
   new section cannot arrive without someone answering the trust question.

**Classify conservatively: if a section's content can originate in the workspace, it is `REPOSITORY`.**
That is why `git_context` is untrusted — a commit message is text an author wrote and it reaches the
prompt — and why `memory_block` is, since memory is workspace-scoped. `recent_summaries` is
`TOOL_OUTPUT`, because compaction summaries are built from a conversation that includes tool output, and
the least-trusted contributor decides.

**Why not fence as well (T2).** Fencing changes more of the prompt for every turn, and the staging this
migration has used throughout applies: record first, then enforce. The classification is the
**precondition** for fencing — you cannot fence what you have not classified — so T2 is left as a
separate, observable change rather than bundled with a security fix that needed to land.

**Consequence:** the prompt is well-formed by T1, and the property is assertable rather than assumed. A
new section, or a section moved into an instruction tier, now fails a test instead of shipping.

**Reversal condition:** If a section is deliberately given instruction position despite being untrusted
— for example an operator-authored file that is *intended* to carry instructions — then it needs a tag
that says so and a rule for how it was authenticated. Widening `TRUSTED_TAGS` to make the check pass is
not that; it is the defect this ADR closes.

---

## ADR-0032 — The failure path reaches the taxonomy through an adapter, and the engine's refusals are denials

**Status:** ACCEPTED
**Phase:** M12
**Supersedes:** ADR-0026's placement of the gap (the ladder's absence is a *missing bridge*, not a missing wiring)

**Context.** ADR-0026 deferred M12 with a reason: *"rewiring [the failure path] alters behaviour on the
failure path — the least-covered path — and the plan names the risk as 'ordering and budget
interaction'."* Reconnaissance found the gap is not where that ADR put it. The ladder cannot be consulted
because **nothing bridges the runtime's failure signals to the taxonomy**:

| Side | Has |
|---|---|
| the runtime | `(message, recoverable, code)` on an `error` event |
| the taxonomy | a `result` (→ `classify_result`) **or** semantic flags |

Neither of those is the other. Driving the taxonomy from real failures for the first time found two
defects.

**Finding 1 — the engine's own refusals were invisible to the denial predicate.** The engine refuses a
tool call before dispatch (role restriction, schema, gate, extension) and emits the refusal as an `error`
event whose text begins `Blocked: …`. `is_denial_text` checks the five canonical statuses and four prose
markers — `('[denied', 'denied by', 'approval denied', 'not authorized')` — and **none of them matches
`Blocked:`**. Verified: `is_denial_text` returns `False` for all six engine refusal shapes.

The cost is concrete. The orchestrator's retry loop says

```python
# Don't retry authorization denials or cancellations (§17).
if self._is_denial(last_error) or "cancell" in last_error.lower():
```

and `_is_denial` delegates to `is_denial_text`. So it **retries them**, up to `max_retries`, on a call
that will be refused identically. The ladder forbids retrying a `SECURITY` failure; the orchestrator's own
loop did it because it could not see the refusal.

This is F15's shape from the other side. F15: the prose markers matched nothing real, so structured
denials were invisible. Now the statuses are checked, and the **engine's** marker was missing.

**Finding 2 — `OutcomeClass.TIMEOUT` does not mean a turn timeout.** It is reachable only from
`APPROVAL_TIMEOUT`, a *denial* status, which is why `classify_failure` maps it to `SECURITY`. The name
invites a caller to route the engine's turn timeout (`CODE_TURN_TIMEOUT`) through it and get a security
failure that forbids retry. Pinned by `test_a_turn_timeout_is_not_a_security_failure`.

**Decision.**

1. **`_ENGINE_DENIAL_PREFIXES` in `core/events.py`** — `("blocked:", "extension intercept failed:")`,
   matched with `startswith`. In the canonical module, not the caller: `is_denial_text` is the ONE
   authority for "is this text a denial", and a second matcher is what F15 was.
2. **A prefix, not a substring.** `_PROSE_DENIAL_MARKERS` stay substrings; these do not, so *"the write
   was not blocked: it succeeded"* is not read as a refusal.
3. **`classify_failure_signal(message, recoverable, code)`** in `core/recovery.py` — the adapter. It
   decides *which* taxonomy entry applies and never re-derives what an outcome means. Precedence mirrors
   `classify_failure`: refusal → cancellation → error code → transport markers → `recoverable` →
   `IMPLEMENTATION`.
4. **`CODE_FAILURE_CLASS`** — the class for each engine error code, **total by test** so a new code cannot
   silently take the default.
5. **`TRANSIENT_MARKERS` moves to `core/recovery.py`**, and `SubagentOrchestrator._TRANSIENT_MARKERS`
   aliases it. The retry loop and the taxonomy must agree about what is transient; two lists would drift.

**The default is `IMPLEMENTATION`, deliberately not `SECURITY`.** An unrecognised failure must not
silently acquire the strongest prohibition — `SECURITY`'s only legal rung is escalation, so defaulting to
it would escalate every novel failure to a human.

**Why `CODE_TURN_TIMEOUT` is `ENVIRONMENT` and not `TRANSIENT`.** Retrying a turn that timed out because
the model is too slow is the orchestrator's own documented refusal (*"the model is too slow or
unreachable — not retrying"*), and `ENVIRONMENT` routes to `DIAGNOSTIC` for exactly that reason.

**Consequence:** the ladder can now be driven from a real failure, and the retry loop stops retrying
engine refusals. The *enforcement* of the ladder's decision remains deferred — this phase makes the
failure classifiable and the refusal visible, which is the precondition the previous ADR assumed existed.

**Reversal condition:** If a refusal shape appears that is *not* a denial (a `Blocked:` message the agent
may legitimately retry), it belongs in `_KNOWN_NON_REFUSALS` with a reason — not by removing the prefix
rule, which would make every engine refusal invisible again.

---

## ADR-0033 — A node references its work unit; the payload ratchet classifies fields, not names

**Status:** ACCEPTED
**Phase:** M11
**Supersedes:** ADR-0029's payload ratchet mechanism (the property is kept; the blacklist is replaced)
**Amends:** ADR-0029 §"what this does to M11" — M11 is *node identity*, not the graph driving execution

**Context.** M11 was recorded as *"the graph does not drive execution"* (P5 item 5) and, after M9,
re-scoped to its precondition: **node identity**. M9 §17.6 verified the defect:

> *"`build_turn_graph(run_id, n)` generates `turn:0…turn:n-1` from `len(exchanges) + 1`; a node never
> references its work unit."*

Reproduced before changing anything — one real turn, two exchanges, ids `c0` and `c1`:

| Observation | Before M11 |
|---|---|
| node ids | `turn:0`, `turn:1`, `turn:2` |
| `c0` / `c1` present anywhere in the persisted graph | **no** |
| `detail` on every node | `""` |

`WISP_TARGET_ARCHITECTURE.md` §14 says why this is not cosmetic: *"given the journal, the system can
reconstruct the state as of any recorded transition, and **re-executing from that point is
idempotent**."* Re-executing a work unit idempotently requires naming it. An index cannot.

**Finding F29 — the ratchet forbade the fix.** `tests/test_execution_view_projection.py` guarded the
property M9 established (the graph must not become a second copy of the transcript) with a **name
blacklist**:

```python
payload_fields = {"tool", "tool_name", "arguments", "args", "result",
                  "content", "text", "messages", "transcript",
                  "tool_call_id", "output"}
```

`tool_call_id` is in that set — and it is the only thing that can reference a work unit. **M9's guard
forbade what M9's own report says M11 requires.** The two artifacts of one phase contradict each other,
and the contradiction is the finding.

**Finding F30 — a name blacklist is evadable by naming.** The same guard passes for a payload field
called `body`, `payload`, `blob` or `body_text`. It is a list of words, not a property, so it cannot
close the defect class it exists to close — it only catches the spellings its author thought of.

**Decision.**

1. **`TaskNode.work_unit: str`** — the identity of the work unit the node records. An *identity*, not
   content: `call:<protocol id>` for a closed tool exchange (ids joined by `+` for a batch), `output`
   for the terminal node. **`node_id` stays `turn:i`** and that is deliberate — it is the graph's
   *structural* key, which edges, `deps`, transitions and supersession all address, so it must stay
   stable and unique within the graph. Deriving it from a provider-supplied id would put the topology
   at the mercy of transcript data (and of `_exchange_parts`'s random fallback for id-less traffic).
   Two fields, two jobs.
2. **`build_turn_graph(run_id, work_units)`** — it takes the identities, not a count. A node that
   records nothing is therefore **not constructible from the turn's path**, which is the defect stated
   as a type. A bare `str` is refused explicitly: `str` *is* a `Sequence[str]`, so
   `build_turn_graph(r, "abc")` would otherwise build three nodes named after the characters.
3. **`turn_work_units(exchange_call_ids)`** — the ONE authority for "what are a turn's work units".
4. **The identity comes from the authority that mints it.** `_serialize_tool_exchanges` now returns
   `(events, exchange_call_ids)`, read back from the blocks `_exchange_parts` just built. It is not
   recomputed, and that is a correctness requirement rather than a tidiness one: an exchange whose
   events carry no id gets a fresh `uuid4` (`_exchange_parts`), so a second pass would mint a
   *different* id and the node would reference a work unit the transcript never recorded.
5. **The ratchet classifies fields, not names.** `NODE_FIELD_KINDS` gives every `TaskNode` field a kind
   — `STRUCTURAL`, `REFERENCE`, or `PAYLOAD` — and `node_field_violations()` reports unclassified,
   stale and payload fields. `PAYLOAD` is a declared kind with **no member**: the prohibition is
   expressible and enforced, and a new field is a test failure until it is classified deliberately.

**Why the reference is not a payload.** A payload is content the graph could reconstruct a message
from; a reference is an opaque handle that names a work unit and carries none of it. The test that
separates them is behavioural, not definitional: the serialized graph of a real turn contains no tool
name, no argument value and no result text (`test_the_graph_still_supplies_no_message_content`), and
every exchange reference resolves to a `tool_call_id` the journal actually recorded
(`test_the_reference_resolves_to_the_journal`).

**Consequence:** a node can now be traced to the work it records, which is the precondition
idempotent replay needs. The graph still **does not drive execution** — M11's original wording — and
that remains open; ADR-0029 already recorded that the strong reading (the graph as the source of the
transcript) is the wrong target, not merely unimplemented.

**Reversal condition:** if a node's `work_unit` ever needs to carry content — a tool name for a UI, say
— then the reference/payload boundary is wrong and this ADR is wrong with it. The test that would fail
is `test_the_graph_still_supplies_no_message_content`.

---

## ADR-0034 — The progress signal is built from the work, and an empty observation is not evidence

**Status:** ACCEPTED
**Phase:** M13
**Amends:** ADR-0029 §"what this does to M13" — the identity stagnation needs is the *action* identity, not M11's node identity

**Context.** P7 built the detector, the signal, the routing and the goal-met guard, and made
`config.graph_oscillation_guard` readable — but **nothing on the live turn path constructs a
`StagnationDetector`**. That is M13. M9 re-scoped it to *"meaningful progress signals"*.

Wiring it found two defects that the mechanism could not have shown on its own.

**Finding F32 — an empty observation was read as "no progress", and that declared every multi-turn
session stagnant.** `ProgressSignal.from_verdict_and_graph` takes a P3 verdict and a P4 graph, and
**both are opt-in records that default off**. So on a default configuration the turn-end signal is the
*empty* observation on every turn. Verified:

| Observation | Result |
|---|---|
| turn 1 | `UNKNOWN` |
| turn 2 | `trap_fired = True`, `may_report_goal_met() = False` |
| turn 3+ | **`STAGNATING`** |

A productive session was declared stagnating by its third turn, and blocked from reporting its goal met
from its second. That is the plan's *"flagging productive work as stagnated"* risk arriving in its worst
form — not from a tuned threshold, but from a mechanism that cannot represent *"I don't know"*. The
signal type had no way to say "this observation carries nothing", so absence of information was read as
absence of progress.

**Finding F33 — the identity stagnation needs is the *action* identity, and the runtime could not always
see it.** A repeated identical call gets a **fresh** `tool_call_id`, so M11's `TaskNode.work_unit` makes
every repeat look like new work — the wrong identity for this question. The right one is
`action_key(tool, args)` (P1's canonical digest, already on the journal). And the engine emits **no
`tool_call` event for a call it refuses before dispatch** — verified: a scripted `read_file` round
produced `error`, `tool_result`, `content`, `done` and nothing else — so the arguments never reach the
runtime, and a signal built from the reply alone cannot distinguish `read_file(a.txt)` from
`read_file(b.txt)`. Left alone, that flags a productive three-file read as stagnation.

**Decision.**

1. **`ProgressSignal.work_units`** — the action identities observed (`action_key` digests). A new one is
   progress; the same one again is not.
2. **`ProgressSignal.is_empty`**, and `observe()` returns `UNKNOWN` for an empty observation without
   appending it, counting it flat, or feeding it to the trap. *"We learned nothing" is not "nothing
   changed".*
3. **`with_work()`** — the live-path fold-in, mirroring `with_artifact`: immutable, and accumulated
   rather than differential, because progress is "we have learned something new at some point" and a
   delta comparison would call an A→B→A oscillation progress on every step.
4. **The detector is constructed per turn** in `run_turn`, gated by
   `config.graph_oscillation_guard`. Per **turn**, not per session: the question is "is this turn going
   round in circles?", and a detector spanning turns would compare one user request against the next.
5. **The identity travels with the refusal.** `_refusal_result_event` — the one helper every refusal
   goes through — stamps `action_key`, computed by the same function the journal uses. A refused call
   emits no call event, so this is the only place its arguments can be observed. For an *allowed* call
   the runtime already read the call event; the two cover disjoint cases and are not two copies.
6. **The verdict is recorded, not enforced** — a new `STAGNATION` audit event, journal-only (ADR-0028's
   shape), written **only when the verdict was reached**, so a normal turn adds no record to any
   caller's log. That is what lets it run under the flag's declared default (`True`) instead of being
   opt-in like `record_verdict`/`task_graph`.

**Consequence:** the detector runs on the live path and reports. Routing a stagnation to the recovery
ladder — the plan's item 3 — and gating completion on `may_report_goal_met()` — item 4 — remain
**deferred**, both pinned by tripwires. Acting on the verdict changes the turn's control flow, which is
the risk ADR-0026 named and the same deferral M12 made.

**Reversal condition:** if the record proves noisy on real turns, the fix is `min_consecutive` — which
P7 recorded as reasoned rather than tuned, and which this phase did not change. Lowering it below 2, or
removing the empty-observation guard, would re-open F32.

---

## ADR-0035 — Completion and recovery are two authorities; completion is evaluated first, and stagnation vetoes goal-met

**Status:** ACCEPTED
**Phase:** POST-M13 (contract)

**Supersedes:** the *deferrals* recorded in ADR-0026 (M12's remainder) and ADR-0034's items 3–4, by
specifying the contract they were waiting on. It supersedes neither their mechanisms nor their
reasoning. It **complements** ADR-0016: that ADR staged the *enablement* of the acceptance gate; this one
fixes the *semantics* the gate would enforce.

### Context

`PHASE_POST_M13_VERDICT_CONTRACT.md` established the gap. Four mechanisms exist, are tested, and are
reachable from their packages; none is consumed by a live authority:

| Mechanism | Built by | Consumer |
|---|---|---|
| `RecoveryLadder.decide()` (`core/recovery.py:512`) | P6 / ADR-0026 | **none** |
| `route_to_recovery()` (`core/stagnation.py:331`) | P7 | **none** (tests only) |
| `may_report_goal_met()` (`core/stagnation.py:294`) | P7 | **none** |
| `classify_failure_signal()` (`core/recovery.py:181`) | M12 / ADR-0032 | **none** |

Three further facts constrain any decision here:

1. **P3 is not an independent authority.** `floor_guard_verdict()` (`core/verification.py:293-301`)
   builds its criteria *and* evidence from `VerificationFloorGuard` itself, using ADR-0017's implication
   `(not guard.wrote_code) or guard.resolved()`. `CompletionVerdict` is therefore the live completion
   authority **restated three-valued** — which is what ADR-0018 intended when it chose to publish the
   guard rather than let the runtime re-derive it.

2. **There is exactly one explicit precedence statement in the repository**, at
   `WISP_TARGET_ARCHITECTURE.md:391-405` (§16). It governs a `GoalState` vocabulary that appears in **no
   `.py` file**, it is **cited by no ADR** (only §14 and §5 of that document are ever cited), and it
   **cannot express `INCONCLUSIVE`** — the value ADR-0016 and ADR-0017 make central. It also diverges
   from the implementation: §5.1 shows `STAGNATED → ESCALATING`, while `legal_rungs` excludes `HUMAN`
   as a choice for `FailureClass.STAGNATION` (`recovery.py:502-503`).

3. **The two questions are already separate in the code.** `may_report_goal_met()` answers *"may the goal
   be called met?"*; `route_to_recovery()` answers *"what should happen next?"*. Same detector, two
   outputs, two questions. This is the strongest existing evidence for a two-track model, and the
   decision below is a ratification of it rather than a redesign.

**What is undecided today:** what happens when the acceptance verdict says `PASS` and the stagnation
predicate says *not met*; whether `INCONCLUSIVE` blocks, permits, or is orthogonal to completion;
whether terminal evidence may declare success when acceptance is inconclusive; and what a goal-level
completion state even is, given that `GOAL_MET` is unimplemented.

### Decision

**One authority per question, two questions, one ordered arbiter, and no shared verdict.**

1. **Two tracks, two authorities.** The **completion authority** decides *"is the work complete?"*. The
   **recovery authority** decides *"what should happen next?"*. Neither may answer the other's question.

2. **The completion authority is the existing one, extended.** At turn level it remains terminal
   evidence: `turn_succeeded = saw_done and not saw_fatal_error` (`core/runtime.py:878`, 13-H5) —
   **unchanged**. At goal level it is a **new, derived state** (`GOAL_*`, below), computed from three
   journaled inputs: the terminal outcome, the P3 acceptance verdict, and the P7 stagnation predicate.
   The goal state is a **pure function** of those inputs — derived, never independently assigned.

3. **P3's vocabulary is the acceptance input, not a second authority.** `PASS` means *the verification
   floor is satisfied and every required criterion has valid evidence*; it does **not** mean the goal is
   complete. `FAIL` means a deterministic criterion failed. `INCONCLUSIVE` means criteria exist but no
   valid evidence does — **absence of evidence, not evidence of failure**.

4. **Stagnation is a completion input via a predicate, and a recovery input via a routing.** The
   detector's two outputs are consumed by the two different tracks. `may_report_goal_met()` is an input
   to the completion authority; `route_to_recovery()` is an input to the recovery authority. They never
   compete, because they answer different questions.

5. **Completion is evaluated first; recovery is evaluated after.** The completion authority sits at the
   engine's pre-`done` gate (`core/stateless.py:761-806`), where `guard.rejection()` already withholds
   `done`. The recovery authority sits at the runtime turn boundary
   (`core/runtime.py:867-878`), where the failure class is known. **A recovery decision therefore cannot
   prevent the current turn's completion — it runs later — and it cannot declare one.**

6. **`INCONCLUSIVE` is not a failure and not a pass.** It yields a distinct goal state
   (`GOAL_UNVERIFIED`). It never becomes `PASS`, and it never becomes `FAILED`.

7. **`UNKNOWN` remains non-blocking.** It is not a verdict: it means "no verdict yet" (no baseline, an
   empty observation, or a flat run shorter than `min_consecutive`). Non-blocking is **not** the same as
   "becomes success" — `UNKNOWN` is never converted into `PASS`.

8. **Classification is not authority.** `classify_failure_signal()` selects a `FailureClass` and
   "never re-derives what an outcome means" (ADR-0032). It feeds the recovery track only. It cannot
   affect completion, and it is not a hidden authority.

9. **The goal state is recorded, then enforced.** The goal state is **recorded audit-only first**, under
   the P3 stage-3a pattern, and enforcement stays behind the per-concern flags until ADR-0016's
   measurement exists. This ADR fixes *semantics*; it does not enable enforcement.

#### The goal taxonomy (the minimum authoritative set)

Six states. Five are §16's, minus the two that are *reasons* in the current implementation, plus the one
§16 cannot express:

| State | Requires | Authority |
|---|---|---|
| `GOAL_MET` | `turn_succeeded` ∧ P3 `PASS` ∧ ¬stagnating ∧ ¬escalated ∧ ¬cancelled | completion |
| `GOAL_UNVERIFIED` | ¬`GOAL_MET` ∧ ¬failed ∧ ¬stagnated ∧ (P3 `INCONCLUSIVE` ∨ terminal outcome `INCOMPLETE`) | completion |
| `GOAL_STAGNATED` | `turn_succeeded` ∧ `may_report_goal_met()` is `False` | completion |
| `GOAL_FAILED` | P3 `FAIL` ∨ fatal terminal error (`CODE_TURN_TIMEOUT` / `CODE_PROVIDER_STREAM` / `CODE_ITERATION_BUDGET` / protocol-integrity), carrying `reason` | completion |
| `ESCALATED_TO_HUMAN` | the ladder is exhausted (`RecoveryLadder.escalate`) | recovery |
| `CANCELLED` | operator cancellation | external |

**`BUDGET_EXHAUSTED` and `TIMED_OUT` are `reason`s within `GOAL_FAILED`, not separate states.** The
current implementation cannot distinguish them at the goal level — budget exhaustion and timeouts reach
the runtime as fatal `error` events with codes (`stateless.py:308-309`, `:959-960`) — and the
`BudgetGovernor` is not consulted on the live path. Promoting them to states would require the governor
to be wired, which is not this decision. The distinction is preserved in the `reason`, for the operator.

**`GOAL_UNVERIFIED` is required, not invented.** Without it, `INCONCLUSIVE` would have to be recorded as
either `GOAL_MET` (violating invariant 1) or `GOAL_FAILED` (a failure claim the evidence does not
support — the exact error `evaluate` refuses at `acceptance.py:227-228`).

#### Precedence — the ordered arbiter

When more than one condition holds, the **highest** row wins. This is a total order over the *goal
state*, not a severity score over mechanisms.

| # | Condition | Result | Source |
|---|---|---|---|
| 0 | A terminal state is already recorded | **frozen** — never rewritten | ADR-0020 |
| 1 | Operator cancellation | `CANCELLED` | §16, adopted verbatim (*"`CANCELLED` beats everything except an already-recorded terminal state"*) |
| 2 | The ladder is exhausted | `ESCALATED_TO_HUMAN` | **Decided here.** Escalation is a *request for human authority*; suppressing it would silently convert "a human is required" into "failed" — the same error class as converting `INCONCLUSIVE` into `PASS`. `ESCALATION` is the only state-bearing record (ADR-0028), so it is state, not a note |
| 3 | P3 `FAIL` ∨ fatal terminal error | `GOAL_FAILED` | **Decided here.** The stronger, evidence-backed claim |
| 4 | `may_report_goal_met()` is `False` | `GOAL_STAGNATED` | **Adopted from the plan**, item 4: *"A stagnated goal must never reach `GOAL_MET`"* |
| 5 | P3 `INCONCLUSIVE` ∨ terminal outcome `INCOMPLETE` | `GOAL_UNVERIFIED` | **Decided here.** Invariant 1; and 13-H5's *"bare exhaustion … never counts as success"* |
| 6 | `turn_succeeded` ∧ P3 `PASS` | `GOAL_MET` | §16's `GOAL_MET` definition: *"all acceptance criteria verified"* |

**§16's `GOAL_MET` beats `BUDGET_EXHAUSTED` is preserved by construction, not by a comparison.** A
budget-exhausted turn emits a fatal `CODE_ITERATION_BUDGET` error, so `turn_succeeded` is `False`, so
row 6 cannot be reached. The rule holds vacuously — which is the honest way to hold it, since the
implementation has no path where both conditions are true simultaneously.

#### Goal vs turn — the fundamental distinction

| | Turn | Goal |
|---|---|---|
| Question | *Did this turn finish cleanly?* | *Is the work complete?* |
| Rule | `saw_done ∧ ¬saw_fatal_error` (`runtime.py:878`) | the six-state derivation above |
| Consumer | graph node status (`runtime.py:998-1018`), spans (`:1224`), persistence (`:1274`) | **new** — the completion authority |
| Status | **implemented, unchanged** | **not implemented** — this ADR defines it |

**`turn_succeeded = True` may coexist with `GOAL_FAILED`, `GOAL_UNVERIFIED` or `GOAL_STAGNATED`.** That
coexistence is the point: a turn can finish cleanly and still not complete the goal. Today the two are
conflated because only the turn level exists; separating them is this ADR's substance, and it means
**terminal `done` can no longer imply goal success** (invariant 8).

### Authority table

| Mechanism | Role | Authority over | Cannot decide |
|---|---|---|---|
| `VerificationFloorGuard.rejection()` | completion gate (negative) | whether `done` may be emitted at all | goal state; recovery rung; whether the goal is verified |
| `VerificationFloorGuard.resolved()` | verified-completion predicate | whether the turn earned its completion (`wrote_code ∧ verify_ok_after_edit is True`) | goal state; recovery |
| P3 `CompletionVerdict` / `evaluate()` | acceptance verdict (input) | `PASS`/`FAIL`/`INCONCLUSIVE` against criteria + evidence | the goal state; recovery; whether to gate |
| `route_for()` | derived mapping | verdict → the graph router's vocabulary | nothing — it is derived and currently unread |
| `StagnationDetector.observe()` / `.verdict` | progress verdict | `UNKNOWN`/`PROGRESSING`/`STAGNATING` for one turn | completion; recovery routing |
| `may_report_goal_met()` | completion input (predicate) | whether the goal may be called met | recovery; failure classification |
| `route_to_recovery()` | recovery input (routing) | converting a progress verdict into a `FailureClass` | completion; the rung itself |
| M12 `classify_failure_signal()` | classification | which `FailureClass` applies | completion; whether to recover; the rung |
| `RecoveryLadder.decide()` / `.escalate()` | recovery authority | the next rung; escalation | completion; failure; the goal state; erasing evidence |
| `RecoveryLadder.terminal_outcome` | recovery outcome | `ESCALATED_TO_HUMAN` / `IN_PROGRESS` | completion |
| terminal evidence | turn-completion authority | `turn_succeeded` | goal state; recovery; verification |
| `turn_succeeded` | turn-level completion | node status, spans, persistence | goal state |

### Precedence matrix

Every combination has a deterministic answer. `T` = terminal outcome, `A` = P3, `S` = P7 stagnation.

| Condition A | Condition B | Required decision | Basis |
|---|---|---|---|
| P3 `PASS` | P7 `STAGNATING` | `GOAL_STAGNATED` — the turn may still be `turn_succeeded` | plan item 4; arbiter row 4 |
| P3 `FAIL` | recovery requested | `GOAL_FAILED`; the recovery request is **also** honoured — it is the *next-step* decision and cannot demote the failure | arbiter row 3; two tracks |
| P3 `INCONCLUSIVE` | recovery requested | `GOAL_UNVERIFIED`; recovery request honoured for the next step | arbiter row 5 |
| P3 `PASS` | `T` = success | `GOAL_MET` (if ¬stagnating, ¬escalated, ¬cancelled) | arbiter row 6 |
| P3 `FAIL` | `T` = success | `GOAL_FAILED` — terminal success **cannot** override an authoritative failure verdict | arbiter row 3 > 6; invariant 7 |
| P3 `INCONCLUSIVE` | `T` = success | `GOAL_UNVERIFIED` | arbiter row 5; invariant 1 |
| P7 `STAGNATING` | `T` = success | `GOAL_STAGNATED` | arbiter row 4 |
| P7 `UNKNOWN` | `T` = success | not blocking → falls through to the acceptance rule | `stagnation.py:252-262, 301-305`; invariant 3 |
| `SECURITY` failure | retry candidate | **retry forbidden by class**; the ladder escalates | `recovery.py:110, 132-135`; `LEGAL_RUNGS[SECURITY] = {HUMAN}` |
| cancellation | recovery request | `CANCELLED`; recovery does not run | §16 adopted; arbiter row 1 |
| timeout | recovery request | `GOAL_FAILED` (reason `timeout`); recovery may choose a rung for the **next** attempt, never for this turn's state | `stateless.py:308-309`; ADR-0032 (`CODE_TURN_TIMEOUT` is `ENVIRONMENT` → `DIAGNOSTIC`, deliberately **not** `TRANSIENT`) |
| budget exhausted | recovery request | `GOAL_FAILED` (reason `budget`); recovery may choose a rung for the next attempt | `stateless.py:959-960`; arbiter row 3 |

### State transition model

```
turn begins
  → observe one closed exchange at a time  (M13: action_key + outcome)
  → terminal outcome:  SUCCEEDED | FAILED | INCOMPLETE
  → COMPLETION EVALUATION          [pre-`done` gate, core/stateless.py:761-806]
        inputs:  terminal outcome, P3 verdict, may_report_goal_met()
        output:  GOAL_* state
        effect:  may WITHHOLD `done` — bounded, and it surrenders honestly when
                 no rung remains, exactly as `guard.rejection()` does today
  → RECOVERY EVALUATION            [turn boundary, core/runtime.py:867-878]
        inputs:  FailureClass (M12), route_to_recovery() (P7)
        output:  a RecoveryDecision rung, or ESCALATED_TO_HUMAN
        effect:  governs the NEXT step only
  → next state
```

**Ordering is the answer to the timing question.** Completion is evaluated first because it gates the
turn's end; recovery is evaluated second because it needs the failure class, which only exists once the
terminal outcome is known. They cannot deadlock: they are at different points, and the later one cannot
retroactively change the earlier one's answer.

**The completion gate must be bounded.** Withholding `done` is only legitimate while a rung remains.
`VerificationFloorGuard.rejection()` already models this — *"floor exhaustion surrenders honestly"*
(`verification.py:176-179`). The goal gate must inherit that property, or a stagnating turn would never
end.

### Durability / replay contract

> If a run is reconstructed from its journal, the goal state and the recovery decision must be
> reproducible **exactly**, or the reconstruction must fail loud rather than guess.

The goal state is a pure function of three inputs. **Two of them are not durably recorded today**, which
is a blocking gap for enforcement:

| Input | Recorded today? | Evidence | Required |
|---|---|---|---|
| Terminal outcome (`done` / fatal `error` + code) | **yes** — `session_events` (F4) | `runtime.py:867-878` | keep |
| P3 acceptance verdict | **opt-in** — `VERDICT`, `record_verdict` defaults `False` | `session.py:35,204`; `config.py:912-913` | **must become mandatory whenever the goal state is authoritative** |
| P7 stagnation predicate | **partial** — `STAGNATION` written *only when the verdict is reached* | `session.py:51`; `runtime.py:1054-1070` | **must become a positive record per evaluated turn**, else replay cannot distinguish "not stagnating" from "not evaluated" |
| M12 `FailureClass` | **no** — `RECOVERY` has no production producer | `session.py:230` (constructor); no caller | **required before the recovery track is authoritative** |
| Recovery decision | **no** — same | `session.py:230` | required |
| Escalation | **no** — `escalation_event` has no production producer, despite `ESCALATION` being the only state-bearing kind (ADR-0028) | `session.py:245`; `session.py:73-75` | required |
| Cancellation / budget | yes — via terminal `error` events with codes | `stateless.py:308-309, 959-960` | keep |

**The goal state is derived, not separately stored.** A separately-recorded state could drift from its
inputs; a derived one cannot. This follows ADR-0029 ("the graph is a shape … the transcript projects
from the journal") and the repo's one-authority principle.

**`ESCALATION` keeps ADR-0028's treatment**: state-bearing, fail-loud, because a `HumanIntervention` *is*
the parked run's state rather than a description of it. The other records stay best-effort audit.

### Rejected alternatives

| Alternative | Why rejected |
|---|---|
| **A unified verdict** (normalize all signals into one, then one consumer) | No normalizer exists, and building one would create a fifth vocabulary and a second place that decides what an outcome means — the defect class this migration exists to remove. ADR-0032 already rejected it at the classification boundary |
| **Adopt §16 wholesale** | Its vocabulary is unimplemented, unratified, cannot express `INCONCLUSIVE`, and diverges from the implementation on stagnation routing (§5.1 vs `recovery.py:502-503`). Copying it would ratify semantics the repository does not have |
| **Treat P3 as an independent authority** | Contradicted by source: `floor_guard_verdict` derives its criteria and evidence *from* the guard. ADR-0018 chose the published-guard read precisely to avoid a second authority for "was this verified" |
| **Treat stagnation as generic failure** | `FailureClass.STAGNATION` is `# working, not progressing` (`recovery.py:63`). A stagnation is not a failure; it is the absence of progress, and it routes to a replan rather than a retry |
| **Let recovery imply completion** | A `RecoveryDecision` carries no completion claim. Allowing one would make "we are retrying" indistinguishable from "we succeeded" — invariant 4 |
| **Let a terminal `done` imply goal completion** | The status quo, and precisely the conflation this ADR removes. `done` becomes at most `GOAL_UNVERIFIED`; `GOAL_MET` requires P3 `PASS` (invariant 8) |
| **Promote `BUDGET_EXHAUSTED`/`TIMED_OUT` to goal states** | Would require wiring `BudgetGovernor` onto the live path, which is not this decision. They are preserved as `reason`s within `GOAL_FAILED` |
| **Make escalation orthogonal to the goal state** | `ESCALATION` is state-bearing by ADR-0028 — the repo already treats it as state, not a flag |
| **Put the recovery consumer before the completion gate** | Recovery needs the failure class, which requires the terminal outcome; placing it earlier would mean classifying a failure that has not happened yet |

### Implementation boundary

**What the next implementation phase may change** — and nothing more:

1. Add the goal-state derivation and its **audit-only** record (the P3 stage-3a pattern).
2. Make the acceptance verdict and the stagnation verdict **mandatory** whenever the goal state is
   authoritative (§ durability), so replay can reproduce it.
3. Add producers for `RECOVERY` and `ESCALATION` journal records.
4. Wire the **completion consumer** at the pre-`done` gate (`stateless.py:761-806`), bounded, with the
   floor guard's honest-surrender property.
5. Wire the **recovery consumer** at the turn boundary (`runtime.py:867-878`).
6. Add the recovery flag under the name ADR-0026 reserved — `recovery_ladder` / `WISP_RECOVERY_LADDER`.
   **That flag does not exist in code today**; only the plan and ADR-0026's reversal condition name it.
7. **Replace the two M13 tripwires with their inverses in the same commit** that wires the consumers —
   the P9/M15 and M11/M13 precedent. They must not be weakened beforehand.

### Non-goals

Explicitly **not** authorised by this ADR: changing `turn_succeeded`'s definition; changing
`VerificationFloorGuard` or its criterion; changing `evaluate`'s rules; repairing F8 / `jsonschema`;
refactoring fanout or `multi_agent/dag.py`; M1/M5/M6/M7/M10 implementation; graph-driven execution; any
new execution engine; and **enabling enforcement before ADR-0016's measurement exists**.

### Rationale

The migration's recurring defect is a mechanism that exists and is never consulted. The remedy is not a
fifth mechanism — it is one authority per question, and an order for the cases where the inputs
disagree. Every clause above either **ratifies what the code already does** (the two-track split, the
three-valued verdict, classification-is-not-a-verdict, `UNKNOWN` is not blocking) or **adopts a rule the
plan already states** (stagnation vetoes goal-met) or **decides the one thing nothing had decided**
(what `INCONCLUSIVE` yields at goal level, and that escalation outranks failure). No new mechanism is
introduced.

### Consequence

A future implementer can answer *"which state wins in every conflict?"* from the precedence matrix
without inventing semantics. The completion track keeps its existing owner; the recovery track gains one;
and the two never arbitrate against each other because they answer different questions at different
points in the turn.

Two consequences are worth stating plainly:

- **Terminal `done` stops being sufficient for goal success.** That is a semantic change, and it is the
  decision. It is why enforcement is staged: the goal state is recorded first and enforced only after
  ADR-0016's measurement.
- **Two durability gaps must be closed before enforcement is honest** — the acceptance verdict is opt-in
  (`record_verdict=False`), and the stagnation record is written only when the verdict is reached. Under
  the current defaults, a reconstructed run could not reproduce its own goal state.

### Reversal condition

If the goal state proves noisy on real turns, the rollback is per-concern and already named: the
recovery flag (to be added), `graph_oscillation_guard`, and `record_verdict`. The goal state must
**degrade to audit-only** rather than change behaviour — the P3 stage-3a state it starts from. Removing
`GOAL_UNVERIFIED` (collapsing it into `GOAL_FAILED`) would re-open the exact conflation ADR-0017 was
written to prevent, and must not be done without superseding this ADR.

---

## ADR-0036 — Stagnation may withhold `done` for a bounded replan; it never vetoes the turn

**Status:** ACCEPTED
**Phase:** POST-M13 (completion enforcement policy)

**Amends ADR-0035 — by *reconciling* it, not by superseding it.** ADR-0035's two apparently conflicting
clauses (the gate "may WITHHOLD `done`" at `:1528`; "`turn_succeeded = True` may coexist with
`GOAL_STAGNATED`" at `:1478`) are **both true** under the decision below, so neither is withdrawn. This
ADR also adds one required key to the goal-state record (§6), closing a **live/replay divergence found
while writing it**. ADR-0035's goal taxonomy, its precedence table and its two-authority split are
**unchanged**.

### Context

`PHASE_POST_M13_LIVE_COMPLETION_SEAM_RECON.md` settled the *interface* and left four *policy* questions
open. Re-reading the source for this ADR found a third thing: **the live arbiter and the recorded evidence
already disagree in one case** (§6), which is the defect class this migration exists to remove.

**The interface is settled and is not reopened.** An optional injected callable on `core.turn()` /
`_turn_inner`, mirroring `approval_handler` and `steering_drain` (`stateless.py:204`, `:315-318`,
threaded at `:303`), carrying `Callable[[], bool] | None` — a **closure over the per-turn detector**,
evaluated on demand at the clean completion gate. The engine gains no authority, holds no state and is
handed no detector object (`observe()` mutates; handing it over would give a second party write access to
the one stagnation authority).

**The four open questions** were: (A) does the gate delay or veto; (B) what is the intervention, and who
owns it; (C) what is the bound; (D) what happens when the gate is absent or raises. This ADR answers all
four, and only those.

**What was already decided and is therefore not re-litigated:** completion is evaluated before recovery
(ADR-0035 §5); `GOAL_STAGNATED` outranks `GOAL_UNVERIFIED` and `GOAL_MET` (row 4); `may_report_goal_met()`
is row 4's input, named in the ratified table; `turn_succeeded` is 13-H5's rule and stays unchanged.

### Decision

**One decision, in one sentence:** *stagnation may withhold `done` for a bounded number of replan
interventions, and then it surrenders — it never vetoes the turn.*

#### 1. Axis A — the gate delays, it does not veto

`DECISION A = BOUNDED DELAY / REPLAN.`

When the gate withholds `done`, it **continues the loop with a replan intervention**. It never ends the
turn early, and it never withholds indefinitely. After the bound (§4) is exhausted it **falls through to
`done`** — the honest surrender `VerificationFloorGuard.rejection()` already models
(`verification.py:176-179`, *"floor exhaustion surrenders honestly"*).

**This is the only reading under which ADR-0035 is self-consistent**, and the evidence is in ADR-0035
itself rather than in preference:

1. **ADR-0035 already requires the bound.** `:1542-1545`: *"The completion gate must be bounded.
   Withholding `done` is only legitimate while a rung remains. … The goal gate must inherit that property,
   or a stagnating turn would never end."* A veto is unbounded by construction.
2. **ADR-0035 already rejects the veto's consequence.** Its rejected-alternatives table rejects
   *"Treat stagnation as generic failure"*. A veto produces exactly that: the loop runs to
   `max_iterations`, the wrap-up at `stateless.py:957-964` emits `CODE_ITERATION_BUDGET` with
   `recoverable=False`, so `saw_fatal_error` is true, so `turn_succeeded` is false, so arbiter row 3
   yields `GOAL_FAILED`. **Stagnation would become failure** — the rejected alternative.
3. **A veto has no third implementation.** The two available shapes are both already forbidden:

   | Veto shape | Mechanism | Result | Forbidden by |
   |---|---|---|---|
   | **A2a — refuse and loop** | the gate never permits `done` | the loop exhausts; `CODE_ITERATION_BUDGET` → `turn_succeeded=False` → `GOAL_FAILED` | ADR-0035's rejected alternative "treat stagnation as generic failure" |
   | **A2b — refuse and end** | return without emitting `done` | `TerminalOutcome.INCOMPLETE`, **no terminal event at all** | 13-H1 (a silent end is invisible); and it changes what `turn_succeeded` means for a stagnating turn, which ADR-0035's non-goals forbid |

4. **Bounded delay makes both clauses true at once.** The gate *did* withhold (line 1528), and it *then*
   surrendered, emitting `done` with the predicate still closed — so `turn_succeeded = True` and the goal
   state is `GOAL_STAGNATED` (row 4). Line 1478's coexistence is not merely preserved; it is the *point at
   which* the surrender becomes observable.

**What this ADR therefore does *not* say:** it does not say a stagnating turn must not finish. It says a
stagnating turn must not finish *as though nothing had happened*, and that the harness may spend a bounded
number of rounds trying to unstick it first.

#### 2. Axis B — the intervention is a replan, never a retry

The intervention is a **continuation message** appended to `messages` and yielded as a
`system(..., level="warning")` event — **the floor guard's existing path, reused verbatim**
(`stateless.py:788-790`).

**Shape: a replan-shaped intervention, never a retry.** `FailureClass.STAGNATION`'s legal rungs are
`{GLOBAL_REPLAN, DIAGNOSTIC, HUMAN}` (`recovery.py:107-109`) and `RETRY` is *structurally forbidden*
(`recovery.py:131`), because — in P7's words — *"retrying the same action against the same state is the
definition of the loop being detected"* (`stagnation.py:335-338`). The gate's intervention is the
in-turn expression of that same rule, so it must have the same shape: it states that progress has
stalled and instructs a change of strategy. It must **not** instruct a retry, and it must not name a
specific next action.

**Content: generic, and deliberately so.** The nudge does not name the repeated action. The model's own
transcript already contains it, and naming it would require the boundary to carry more than a predicate —
which is exactly the widening the recon refused. The nudge therefore communicates the **condition**
(progress has stalled on a repeated approach) and the **instruction** (change strategy before attempting
the same action again), and nothing else.

**Home: `stagnation.py`.** The text is composed there — M13's home module — and emitted by the engine,
mirroring `verification.compose_nudge` and the engine's import of it at `stateless.py:332-336`. That is
the repository's existing convention, stated in the source as GH#27: *"Nudge text derives from the
invariant's home module … so the intervention can never drift from the gate."* The engine gains a **text**
dependency, never an authority: it still decides nothing about stagnation, and it is still handed only a
predicate.

**Ownership.** The *decision* to withhold belongs to M13, exercised through the runtime's predicate. The
*text* belongs to `stagnation.py`. The *emission* and the *count* belong to the engine, beside the floor
guard it already owns. No new owner is created.

**It may modify:** the conversation's continuation state (one appended message, one `system` event) and
its own per-turn counter. **It must not:** execute a tool; mutate the detector; declare success or
failure; alter `VerificationFloorGuard` or its criterion; change `turn_succeeded`; write the journal;
change the goal state; or run when the predicate is open.

#### 3. The gate consults the predicate the arbiter consumes

The gate asks exactly `may_report_goal_met()` — **the same input arbiter row 4 consumes**
(`runtime.py:1138-1140`). One source, three uses: the arbiter derives from it, the gate gates on it, and
(§6) the record carries it.

This is what makes the gate's withholding *explicable*: "the turn was held because the goal may not be
reported met" is a true statement, because it is the same predicate the recorded state was derived from.
It is **not** required for replay — replay reproduces the goal state from the record, and the gate's
effect is already durable in the terminal outcome (§7) — but a gate consulting a different predicate than
the arbiter would make the two disagree about whether the goal may be called met, which is the
"two producers of one structure" defect (discipline #9).

#### 4. Axis C — the bound

**Maximum interventions: 2 per turn.** Owned by the **engine**, as a local counter in `_turn_inner`
beside `guard.nudges_used`; **reset** per turn, because `_turn_inner` runs once per turn and the counter
is a local, not a field.

The number is `VerificationFloorGuard.max_nudges`'s own default (`verification.py:124`), and it is
inherited the same way that guard's bound is — as a **constructor default on the gate mechanism, not a new
config key** (only `grind_min_turns` is configurable there). The bound is a property of the gate, not an
operator knob.

**Why a dedicated integer rather than an existing one.** The recon's rule was "prefer an existing bounded
mechanism if it can express the semantics without changing its meaning". No existing bound can:

| Candidate | Why it cannot be reused |
|---|---|
| `VerificationFloorGuard`'s `max_nudges` | answers a different question (verification floors). Sharing it would let stagnation spend the verification budget — a change to that guard's meaning (STOP 1) |
| `max_iterations` | **the loop's own bound is the wrong surrender point.** Running to it produces the wrap-up's `CODE_ITERATION_BUDGET` fatal error, i.e. `GOAL_FAILED` — the A2a outcome this ADR rejects. The gate must surrender *before* it, not at it |
| the detector's `min_consecutive` | a detection threshold, not a budget; and reusing it would make the intervention count a function of sensitivity |

**Two conditions, not one.** The gate withholds only when **all three** hold:

```
predicate() is False                      # the goal may not be reported met
and  interventions_used < 2               # the bound has not been spent
and  iteration + 1 < max_iterations       # a further round actually exists
```

The third condition is **not** a budget and **not** a new integer — it is a guard on the existing loop
bound, and it exists because the loop's last iteration cannot be withheld: the `for iteration in
range(max_iterations)` (`stateless.py:368`) would end, the wrap-up at `:957-964` would run, and the
surrender would become a `CODE_ITERATION_BUDGET` failure instead of an honest one. **A bound that could
turn its own surrender into a budget failure is not a bound.**

**At exhaustion:** the gate stops withholding. No message, no event, no counter — it falls through to
`done`, and the goal state is derived as usual (row 4 → `GOAL_STAGNATED` while the predicate is closed).

**Termination.** Both counters are monotone and independent — the floor guard's `nudges_used` advances on
a full nudge, the stagnation counter advances on every withhold, and the iteration index always advances.
So the gate cannot spin, and two gates at one site cannot deadlock.

#### 5. Axis D — the gate fails open, and the absence is already durable

`DECISION D = FAIL OPEN.` Every unavailable case permits `done`:

| Case | Behaviour | Basis |
|---|---|---|
| `completion_gate is None` (no detector — a subagent turn, or the flag off) | permit `done` | today's behaviour, exactly; a `None` gate is indistinguishable from the pre-seam engine |
| the callable **raises** | permit `done`, log at `debug` | the engine's gate is not the place to convert a broken predicate into a hung turn — ADR-0026's named failure |
| detector `enabled=False` (`graph_oscillation_guard` off) | permit `done` — the predicate returns `True` | `stagnation.py:301-302`; this is the rollback switch |
| stale / duplicate evaluation | not possible / harmless — the callable reads live state and the predicate is pure and idempotent | `stagnation.py:294-305` |

**Fail-open cannot manufacture a false `GOAL_MET`.** The goal state is derived from the **recorded
predicate**, not from the gate's behaviour. If the gate is absent or broken the turn finishes, and the
goal state is still `GOAL_STAGNATED` when stagnation was observed. Enforcement and recording are
independent, which is why this is safe rather than merely convenient.

**And it is not silent.** The absence is already durable: the record carries `not_evaluated` when no
detector exists (`runtime.py:1194-1197`). A turn whose gate failed is a turn whose record says why.

#### 6. The record must carry the predicate — a live divergence, found here

**The defect.** The arbiter's row-4 input and the recorded evidence are computed from **two different
things**:

| Fact | Source | Line |
|---|---|---|
| the arbiter's `stagnating` | `not stagnation_detector.may_report_goal_met()` — **includes `trap_fired`** | `runtime.py:1138-1140` |
| the record's `stagnation_verdict` | `stagnation_detector.verdict` — the **property**, which ignores `trap_fired` | `runtime.py:1194-1197` |

`may_report_goal_met()` returns `False` on `consecutive_flat >= min_consecutive` **or** `trap_fired`
(`stagnation.py:303-305`); `verdict` returns `STAGNATING` on the first term **only**
(`stagnation.py:276-278`). So when the reused `OscillationTrap` fires but the flat run is below the
threshold, the live derivation yields `GOAL_STAGNATED` while the record says `"progressing"` — **the
record contradicts its own conclusion**, and the existing replay test reconstructs from the record
(`test_post_m13_authority_implementation.py:350`, `stagnating=record["stagnation_verdict"] ==
"stagnating"`), so **live and replay disagree**. That is ADR-0035's replay invariant, violated by the
implementation that ratified it.

**The decision: record the arbitration input.** The goal-state record gains
`"stagnation_allows_goal_met": bool` — the value of `may_report_goal_met()` at turn end, i.e. exactly what
row 4 consumed — and replay reads that field instead of re-deriving from the verdict string.

**Why this shape and not the alternatives.** Aligning the *live* input to `verdict` would remove the
divergence too, but it would contradict ADR-0035's ratified row 4, which names `may_report_goal_met()`,
and it would discard the trap's evidence — `may_report_goal_met()`'s whole purpose. The defect is in the
record, so the record is what changes.

**One key is sufficient.** The pair (`stagnation_verdict`, `stagnation_allows_goal_met`) explains its own
conclusion: `verdict == "stagnating"` ⇔ the flat run closed the predicate; `verdict != "stagnating"` ∧
predicate closed ⇔ the trap fired. No further field is needed, and none is added.

**A ratified consequence.** `trap_fired` is a latch (`stagnation.py:288` — `bool(self.trap_verdicts)`), so
a stagnation that came from the trap **cannot be cleared by any intervention**. In that case the gate
withholds, the model replans, the predicate stays closed, and the bound is spent on an outcome that was
already determined. This is **bounded and conservative** — it can never turn a `GOAL_MET` into anything
worse than `GOAL_STAGNATED`, it costs at most two rounds, and §4's third condition keeps it from becoming
a budget failure. It is recorded here rather than "fixed" because changing it means changing
`may_report_goal_met()`, i.e. M13's semantics, which is not this decision. If it proves wasteful in
practice, the refinement is a **runtime-side** policy change (the closure is the runtime's) *plus* a
change to what is recorded — never one without the other, or §6's divergence returns.

#### 7. The bound is not journaled — stated, not assumed

The recon asked what the journal must record. Decided:

| Fact | Recorded? | Authoritative for replay? |
|---|---|---|
| stagnation observed | **yes** — `stagnation_verdict` + `stagnation_allows_goal_met` (§6) | **yes** — it is row 4's input |
| the final stagnation verdict | **yes** — same field (it *is* the turn-end verdict) | **yes** |
| intervention count | **no** | no |
| intervention exhausted | **no** — derivable from the count and the bound, so recording it would be a second producer of one fact | no |

The goal state is fully determined by the terminal outcome, the acceptance verdict and the predicate;
the intervention count adds nothing to it. Journaling the count would require either a stateful gate
closure — breaking the purity/idempotency the recon ratified — or a **second** engine→runtime publication
beside `_last_guard`, and this ADR is not willing to pay either for a fact the authority does not use.
The already-required observability list (terminal outcome, acceptance verdict, stagnation verdict, failure
class, recovery decision, escalation, goal state) is complete without it. If enforcement ever needs to be
*audited* rather than explained, that is a separate decision about the channel, and it is named here so it
is not rediscovered as a gap.

### Authority table

| Mechanism | Role | Authority over | Cannot decide |
|---|---|---|---|
| `VerificationFloorGuard.rejection()` | completion gate (negative), **unchanged** | whether `done` may be emitted for an unverified mutated turn; its own bound | the goal state; stagnation; recovery |
| the completion gate (new) | completion gate (negative), **bounded** | whether `done` is withheld **once more** on a closed stagnation predicate | the goal state; the predicate's value; recovery; anything at all when the predicate is open |
| `StagnationDetector.may_report_goal_met()` | the predicate | whether the goal may be reported met | whether `done` is withheld; recovery; failure classification |
| `stagnation.py` (nudge text) | the intervention's wording | the text only | whether to intervene |
| the engine's per-turn counter | the bound | how many interventions remain | the predicate; the text; the goal state |
| `derive_goal_state()` | the goal arbiter, **unchanged** | the six goal states | whether `done` is withheld; recovery |
| `turn_succeeded` | turn-level completion, **unchanged** | node status, spans, persistence | the goal state; whether `done` was withheld |
| `RecoveryLadder.decide()` | the recovery authority, **unchanged** | the next rung; escalation | completion; the goal state; whether `done` is withheld |

### Truth table — can a stagnating turn succeed?

Ratified here, not inferred afterwards. `T` = terminal evidence, `P` = the predicate at turn end.

| M13 | Intervention | Final terminal evidence | `turn_succeeded` | Goal state |
|---|---|---|---|---|
| `UNKNOWN` | none | success | **True** | `GOAL_MET` if P3 `PASS`; else `GOAL_UNVERIFIED` (rows 6/5) |
| `PROGRESSING` | none | success | **True** | `GOAL_MET` if P3 `PASS`; else `GOAL_UNVERIFIED` |
| closed (flat run) → replan clears it | 1 or 2 issued | success | **True** | `GOAL_MET` if P3 `PASS` — the predicate cleared, so row 4 no longer fires |
| closed (trap latched) → replan cannot clear it | 1 or 2 issued | success | **True** | `GOAL_STAGNATED` (row 4) — §6's ratified consequence |
| closed, bound exhausted | 2 issued, then surrender | success | **True** | `GOAL_STAGNATED` (row 4) |
| closed | none — no further iteration existed (§4) | success | **True** | `GOAL_STAGNATED` (row 4) |
| closed | any | failure (fatal) | **False** | `GOAL_FAILED` (row 3) |
| closed | any | cancelled | **False** | `CANCELLED` (row 1) |

**`turn_succeeded = True` with `GOAL_STAGNATED` is the expected outcome of the ordinary case, not an
edge case** — which is ADR-0035 line 1478 holding in the field.

### Conflict matrix — total, no unresolved combination

`P` = stagnation predicate, `P3` = acceptance verdict, `T` = terminal outcome, `C` = cancellation,
`R` = recovery decision, `B` = intervention budget. "Gate" is the action at the pre-`done` gate.

| # | P | P3 | T | C | R | B | Gate | Goal state | `turn_succeeded` |
|---|---|---|---|---|---|---|---|---|---|
| 1 | open | `PASS` | success | – | none | – | proceed | `GOAL_MET` | True |
| 2 | open | `INCONCLUSIVE` | success | – | none | – | proceed | `GOAL_UNVERIFIED` | True |
| 3 | open | `FAIL` | success | – | none | – | proceed | `GOAL_FAILED` | True |
| 4 | open | any | success | – | none | – | proceed | `GOAL_UNVERIFIED` | True |
| 5 | closed | `PASS` | success | – | none | remaining | **withhold + replan** | — (turn continues) | — |
| 6 | closed | `PASS` | success | – | none | exhausted | proceed (surrender) | `GOAL_STAGNATED` | True |
| 7 | closed | `PASS` | success | – | none | remaining, **last iteration** | proceed (surrender) | `GOAL_STAGNATED` | True |
| 8 | closed | any | success | – | none | any | proceed | `GOAL_STAGNATED` | True |
| 9 | closed | any | failure | – | rung chosen | – (gate never reached) | — | `GOAL_FAILED` | False |
| 10 | closed | any | failure | – | ladder exhausted | — | — | `ESCALATED_TO_HUMAN` | False |
| 11 | closed | any | incomplete | – | any | any | — | `GOAL_STAGNATED` (row 4 > row 5) | False |
| 12 | any | any | cancelled | **yes** | not consulted | — | — | `CANCELLED` (row 1) | False |
| 13 | any | any | any | – | **already terminal** | — | — | frozen (row 0) | — |
| 14 | closed | any | failure, class `SECURITY` | – | `HUMAN` forced | — | — | `ESCALATED_TO_HUMAN` | False |
| 15 | closed | any | failure, class `STAGNATION` | – | `GLOBAL_REPLAN`/`DIAGNOSTIC` | — | — | `GOAL_FAILED` | False |

**Notes on the rows that need them.**

- **Rows 6/7/8:** `GOAL_STAGNATED` requires the predicate to be closed *at terminal evaluation*. A
  stagnation that was resolved during the turn is **not** `GOAL_STAGNATED` (row 5 → clears → row 1). This
  is the distinction the recon asked for, and it is already expressible: the predicate describes the
  current state, not a latch on the turn — except for the trap, which §6 ratifies.
- **Rows 9/10/15:** recovery runs **after** the gate and **only for `turn_succeeded == False`**
  (`runtime.py:1149`). Row 15 shows the two tracks meeting without competing: the *rung* is recovery's
  (`GLOBAL_REPLAN`, per `recovery.py:107-109`), the *state* is completion's (`GOAL_FAILED`, row 3 — the
  terminal error outranks stagnation). The recovery decision governs the next step and does not demote
  or promote the goal state.
- **Row 11:** reachable — an incomplete provider round yields a `recoverable=True` error and returns
  without `done` (`stateless.py:752-760`), so `T = INCOMPLETE`. Row 4 still outranks row 5.
- **Row 12:** precedence implemented and tested; **no live producer exists today** (the runtime passes
  `cancelled=False`, `runtime.py:1203`). The gate's only obligation is that it must never withhold after
  a terminal outcome is recorded — and it sits *before* `done`, so a cancellation delivered as a terminal
  event bypasses it. Cancellation is not overridable by a replan.
- **Row 13:** ADR-0020. A later observation, a duplicate event and a replay all return the frozen state
  (`goal.py:133-134`).

**Two precedence questions the recon raised, answered explicitly.**

- **`M13 STAGNATING` + timeout** → the timeout pre-empts the loop (`asyncio.timeout`, `stateless.py:299`),
  so the gate is never reached: `GOAL_FAILED`, reason `timeout` (row 9). The gate **does** consume
  wall-clock, so it can contribute to a timeout; that is honest — the turn genuinely ran out of time — and
  §4's bound keeps the added time to at most two rounds.
- **`M13 STAGNATING` + recovery escalation** → `ESCALATED_TO_HUMAN` (row 10): escalation outranks
  stagnation, because suppressing a request for human authority would convert "a human is required" into
  "failed" (ADR-0035 row 2). A **successful** stagnating turn never reaches the ladder at all
  (`runtime.py:1149`), so it cannot escalate — which is what keeps recovery from changing this turn's
  completion answer, and is therefore **intentional, not a gap**.

### State transition model

```
turn begins
  → observe one closed exchange at a time            (M13: action_key + outcome)
  → terminal outcome: SUCCEEDED | FAILED | INCOMPLETE
  → COMPLETION EVALUATION                            [pre-`done` gate, stateless.py:766-806]
        gate 1: guard.rejection()                    UNCHANGED (verification floor)
        gate 2: completion_gate()                    NEW, bounded (§4)
                  False + budget + a round remains
                    → append replan nudge; yield system(warning); continue
                  otherwise
                    → fall through to `done`         (honest surrender)
  → terminal evidence: turn_succeeded = saw_done ∧ ¬saw_fatal_error    UNCHANGED
  → RECOVERY EVALUATION                              [turn boundary, runtime.py:1149]
        only when ¬turn_succeeded; governs the NEXT step only
  → GOAL-STATE DERIVATION + RECORD                   [runtime.py:1198-1224]
        inputs: terminal outcome, P3 verdict, the predicate (§6)
        output: one of the six GOAL_* states
  → next state
```

**Ordering is unchanged from ADR-0035 and is what keeps the tracks from arbitrating against each other.**
The gate runs first because it gates the turn's end; recovery runs second because it needs the failure
class, which needs the terminal outcome. The derivation runs last only because row 2's escalation fact
must exist first. **The gate is the only place the new decision acts; the record is where its consequence
becomes visible.**

### Durability / replay contract

```
LIVE:    the per-turn detector → predicate → the gate's withhold/surrender decision
REPLAY:  the recorded predicate → derive_goal_state        (the gate's decision is not replayed)
```

The seam does **not** make replay depend on live detector state. The gate's decision is *transient* — it
affects the turn's control flow, and its **consequence is durable**: whether the turn took extra rounds,
and how it ended, are captured by the terminal outcome. What replay needs is the arbitration **input**,
and §6 makes that durable.

| Input | Recorded today? | Required |
|---|---|---|
| terminal outcome | **yes** — `session_events` | keep |
| P3 acceptance verdict | **yes**, whenever the goal state is authoritative — closed by the implementation phase (`runtime.py:1126-1135`, recorded at `:1216`) | keep |
| the stagnation predicate | **partially** — `stagnation_verdict` is recorded but is *not* row 4's input | **add `stagnation_allows_goal_met`** (§6) — the blocking fix |
| intervention count / exhaustion | **no** — and not required (§7) | none |

**`LIVE_DERIVATION(inputs) == REPLAY_DERIVATION(same inputs)`** holds once §6 lands, because the goal
state is a pure function of three recorded facts and the arbiter reads nothing else. A reconstruction
that cannot read them fails loud (`goal_state_from_record`, `goal.py:189-205`) rather than guessing.

### Rejected alternatives

| Alternative | Why rejected |
|---|---|
| **A hard veto** (A2a/A2b) | Its only two implementations both produce outcomes ADR-0035 already forbids: `GOAL_FAILED` via `CODE_ITERATION_BUDGET`, or a silent end with no terminal event. It also makes ADR-0035 line 1478 unreachable. §1 |
| **A second stagnation detector in the engine** | Two authorities for `UNKNOWN`/`PROGRESSING`/`STAGNATING` — STOP 2 |
| **Moving M13 into `stateless.py`** | The detector is per-turn runtime state fed by the runtime's event stream; moving it creates a second detector during the transition, with no evidence the current ownership is wrong |
| **Handing the engine the detector object** | `observe()` mutates. The engine would gain *write* access to the one stagnation authority. Only the read-only predicate crosses |
| **A global mutable M13 predicate** | Leaks between turns, sessions and concurrent runs; also makes replay depend on live state (STOP 3/4) |
| **A ContextVar for one boolean** | The mechanism is proven (`turn_deadline`) but its only home is `wisp/tools/context.py`, scoped to *tool execution*. An explicit parameter is smaller and needs no new module |
| **A completion-policy object** | Over-built for one gate with one boolean; it would introduce a type and a lifecycle for undecided future policy |
| **Retrying the same action as the intervention** | `FORBIDDEN_RUNGS[STAGNATION]` contains `RETRY` (`recovery.py:131`); retrying the same action against the same state *is* the loop being detected |
| **Making stagnation `GOAL_FAILED`** | `FailureClass.STAGNATION` is `# working, not progressing` (`recovery.py:63`); ADR-0035's rejected-alternatives table already refuses this |
| **Making stagnation `ESCALATED_TO_HUMAN` automatically** | Suppressing *or* forcing escalation removes the ladder's authority. Escalation is a rung the ladder chooses (row 10), not a consequence of a progress signal |
| **Changing `turn_succeeded` to make the seam convenient** | ADR-0035's non-goals; 13-H5's rule is unchanged, and §1's whole argument is that it must not be |
| **Changing `VerificationFloorGuard`'s semantics** | STOP 1. The new gate sits *beside* it and is independent |
| **A full planner** | Out of scope; the intervention is a message, and the model's own transcript carries what it needs to replan |
| **Graph-native completion enforcement** | The graph is a shape, not a payload (ADR-0029); completion is not the graph's question |
| **Delivering the intervention through `steering_drain`** | Steering is drained at **tool boundaries** (`stateless.py:909-911`), so a final round with no tool call would never receive it — and steering cannot withhold `done`, which is the entire point. Delivery reuses the floor guard's path instead |
| **The runtime re-invoking `core.turn` on stagnation** | Would make the intervention a *second turn*: a second node, a second terminal outcome, and two goal states for one turn. The completion gate is where a turn's end is decided |
| **Aligning the live arbiter to `verdict` instead of fixing the record** | Contradicts ADR-0035's ratified row 4, which names `may_report_goal_met()`, and discards the trap's evidence. §6 |

### Implementation boundary

**What the next implementation phase may change — and nothing more:**

1. `core/stateless.py` — one optional keyword parameter on `turn()` (`:204`) and `_turn_inner`
   (`:315-318`), threaded at `:303`; one evaluation at the gate between `:790` and `:791`; one local
   counter; the third condition of §4.
2. `core/runtime.py` — construct the closure over the per-turn detector and pass it at the `core.turn`
   call site (`:750-752`), behind the flag; **add `stagnation_allows_goal_met` to the goal record**
   (`:1212-1224`) and use it in place of the verdict string.
3. `core/stagnation.py` — the intervention's text, as a composer beside `route_to_recovery`, mirroring
   `verification.compose_nudge`.
4. `config.py` — one new flag, `stagnation_gate` / `WISP_STAGNATION_GATE`, **default OFF**, per
   ADR-0002's one-flag-per-concern rule. Two levels of rollback: the flag stops *enforcement* while
   keeping the record; `graph_oscillation_guard` stops the *detector*, hence both.
5. Tests for S1–S14 of the seam recon, plus the trap-fired replay case §6 requires.

**Not authorised:** changing `VerificationFloorGuard` or its criterion; a second detector; any global
mutable state; changing `turn_succeeded`; changing ADR-0035's precedence; touching the two failure-path
`done` sites (`:313`, `:964`); F8; the graph; fanout; M1/M5–M8/M10.

### Non-goals

Enabling the gate by default; a cancellation producer; `BUDGET_EXHAUSTED`/`TIMED_OUT` as goal states;
subagent/background stagnation enforcement (no detector ⇒ no intervention authority, and extending M13
there is its own decision); journaling the intervention count; moving goal-state derivation into
`core.turn()`; changing M13's detection semantics or its `min_consecutive` tuning; the recovery ladder's
own semantics; F8.

### Rationale

Every prior phase of this migration found the same shape: a mechanism that exists, is tested, and is
never consulted. The temptation here is to answer that by making the gate *strong* — and a strong gate is
precisely the mistake. A veto would convert a progress signal into a failure, which is a category error
the repository had already refused in ADR-0035's own rejected alternatives; and it would silently change
what `turn_succeeded` means, which ADR-0035's non-goals forbid.

The bounded delay is the smaller, more honest intervention: the harness says *"I think we are going in
circles"*, once or twice, and then lets the turn finish and records the truth. Nothing is suppressed,
nothing is promoted, and the decision is visible in the record. It also happens to be the only reading
under which ADR-0035 is self-consistent — which is the strongest available evidence that it is the
intended one.

### Consequence

- A stagnating turn still finishes. It finishes **later, after being asked to replan**, and it is recorded
  as `GOAL_STAGNATED` rather than as success — so ADR-0035's central separation survives contact with
  enforcement.
- The engine gains a *predicate* and a *text*, never an authority. The stagnation authority remains
  single and remains the runtime's.
- **One live defect is closed:** the record and the replay path now carry the predicate the arbiter
  actually consumed (§6), so a reconstructed run reproduces its own goal state in the trap case as well.
- Enforcement is off by default and reversible per concern, so ADR-0016's measurement can be taken before
  anything is enabled.

### Reversal condition

`stagnation_gate` off restores today's behaviour exactly (the gate is `None`, which is the pre-seam
engine). `graph_oscillation_guard` off additionally disables the detector, so the predicate opens and
both the gate and row 4 go quiet. Neither requires a code change, and neither changes `turn_succeeded`.
Reversing §6 would require re-introducing the live/replay divergence it closes, and must not be done
without superseding this ADR.

---

## ADR-0037 — The stagnation latch is monotonic; the predicate closes on the trap, and `min_consecutive` gates the verdict

**Status:** ACCEPTED
**Phase:** POST-M13 (amendment)
**Amends ADR-0036** by completing it — widening its §6 clause from *trap-sourced* stagnation to **all
live-path stagnation**, and correcting its truth table. It **supersedes nothing**: ADR-0034's, ADR-0035's
and ADR-0036's decisions all stand, and this ADR changes no behaviour. It also resolves the three items the
recon found UNDECIDED.
**Evidence:** `PHASE_POST_M13_STAGNATION_REOPENING_RECON.md` (the read-only forensic recon) and
`PHASE_POST_M13_STAGNATION_LATCH_ADR_AMENDMENT.md` (this phase).

### Context

The recon proved, from source and live execution, that ADR-0036's truth table contains a row with no
reachable configuration:

| ADR-0036 row | Reachable? | Why |
|---|---|---|
| "closed (flat run) → replan clears it → `GOAL_MET`" | **no** | flat ⟹ the digest repeats ⟹ the trap fires ⟹ the predicate is `not trap_fired` |
| "closed (trap latched) → replan cannot clear it → `GOAL_STAGNATED`" | **yes — and it is the only row** | measured on the live path |

Three facts compose to make the first row unreachable, and each is a ratified decision rather than an
accident:

1. **ADR-0034 decision 3** builds the signal *"accumulated rather than differential"*, so `a` and `w` are
   monotone sets and any growth in either is progress.
2. `ProgressSignal.from_verdict_and_graph` — the only constructor that can set `failing_criteria`, the only
   digest field that could change *without* progress — has **no production caller**. So the digest's only
   variable fields are two growth sets, and `flat ⟺ digest unchanged`.
3. `OscillationTrap` fires on a repeated digest, and `trap_verdicts` is only ever appended
   (`stagnation.py:250`; `OscillationTrap._hashes` likewise, `graph/loop.py:120`). So the trap fires at the
   **first** flat observation and never clears within the detector's lifetime.

Measured across `min_consecutive ∈ {1, 2, 3, 5, 50}`: the predicate closes on one flat observation in
every case. Therefore, on the live path, **`predicate closed ⟺ trap_fired`** — and ADR-0036 §6's clause,
which ratified non-clearability for *trap-sourced* stagnation, already covers every case that occurs.

**What was genuinely undecided** was not whether the latch holds but **what it means**: whether the
monotonicity is the intended safety property, whether `min_consecutive` is meant to gate the predicate,
and whether the audit record is complete. This ADR decides those.

### Decision

**The stagnation predicate is monotonic for the detector's lifetime. Stagnation is not reopened, and
`GOAL_MET` cannot be restored by an intervention.**

Stated as a rule, so a future phase can implement or test it mechanically:

```text
IF:      trap_fired == true
THEN:    may_report_goal_met() remains false
         until the detector's lifetime ends

PROGRESS AFTER THE TRAP:  does not reopen the predicate, even when `is_progress_from()` is true
                          and `consecutive_flat` resets to 0

BOUNDARY:                 a new turn constructs a new detector (runtime.py:731), so the latch is
                          per turn — never per session, never global

REPLAN:                   may continue execution and may improve the work,
                          but cannot restore GOAL_MET for that turn

AUTHORITY:                M13 (`core/stagnation.py`) owns the predicate and therefore owns this
                          transition — by NOT making it

EVIDENCE:                 none is required, because no transition occurs
PERSISTENCE:              unchanged — `stagnation_allows_goal_met` in the goal record
REPLAY:                   unchanged — the recorded predicate is row 4's input
```

**Why monotonic, from the contract rather than from preference.** Four reasons, each with its basis:

1. **The contract already says so for the only case that occurs.** ADR-0036 §6: a trap-sourced stagnation
   *"cannot be cleared by any intervention"*, ratified as *"bounded and conservative"*. The recon showed the
   trap case is universal, so this ADR **completes** that clause rather than changing it. Reopening would
   require overriding a ratified clause.
2. **The available evidence is novelty, not recovery.** `is_progress_from()` returns true when a *new*
   action or a *new* artifact appears — a single new tool call clears it. Reopening on that would make the
   latch near-vacuous. A stricter rule (*"verified recovery"*) would need data the detector does not own:
   `core/stagnation.py` imports only `dataclasses`, `enum`, `typing`, `wisp.core.graph.loop` and
   `wisp.core.recovery` — no `wisp.core.verification`, and it reads no guard state (import-graph check).
   Its `criteria_satisfied` / `failing_criteria` / `completed_nodes` fields, which *could* carry P3 or
   graph evidence, are settable only by `from_verdict_and_graph` — the opt-in signal source ADR-0034
   removed. A stricter rule would therefore reverse ADR-0034's central decision.
3. **The safety asymmetry is the ratified bias.** A false `GOAL_STAGNATED` under-claims success; a false
   `GOAL_MET` is a correctness failure. ADR-0035 adopted row 4 from the plan's *"a stagnated goal must
   never reach `GOAL_MET`"*, and row 4 deliberately outranks row 6. Monotonicity is the strongest form of
   that property.
4. **It costs nothing the contract requires.** No new owner, no new transition, no new durable fact, no
   new bound, no production change. Reopening would add a transition whose evidence, owner and replay
   contract would all have to be decided — for a turn that, by ADR-0035's own taxonomy, is already
   recorded conservatively as `GOAL_STAGNATED`.

**`min_consecutive` is the VERDICT threshold, not the predicate's.** It gates `observe()`'s return value
and the `verdict` property; it does **not** gate `may_report_goal_met()`. This is **derived, not chosen**:
any predicate that depends on `consecutive_flat` alone is non-monotonic — it reopens the moment progress
resets the counter — so monotonicity and *"`min_consecutive` gates the predicate"* are mutually exclusive.
Monotonicity therefore forces the verdict-only reading. The module docstring's claim that *"N consecutive
non-progressing evaluations are required before stagnation is declared"* is accurate for the **verdict**
and must be read as scoped to it.

**The audit contract.** The `STAGNATION` event records the **verdict**; the **predicate** is recorded in
the goal record as `stagnation_allows_goal_met`. These are two different facts with two different owners of
meaning, and neither substitutes for the other:

| Fact | Recorded where | Authoritative for |
|---|---|---|
| the verdict (`UNKNOWN`/`PROGRESSING`/`STAGNATING`) | `STAGNATION` event (gated on the verdict) | explaining the detector's own conclusion |
| the predicate the arbiter consumed | `GOAL_STATE.stagnation_allows_goal_met` | **replay and the goal state** |

**Consequence, stated rather than hidden:** when the trap closes the predicate below the threshold, the
turn records **zero** `STAGNATION` events (measured), so the *observations* that closed the predicate are
not durable. The predicate itself is. That is accepted here because ADR-0035's replay contract requires the
decision to be **reproducible**, not the evidence to be **explainable**, and because the goal record always
carries the predicate. This limitation is revisited if enforcement is ever enabled by default, or if an
operator needs to explain a `GOAL_STAGNATED` from the journal alone.

### Authority

| Mechanism | Role | Authority over | Cannot decide |
|---|---|---|---|
| `StagnationDetector` | detection | the predicate and the verdict, **including whether the predicate may reopen — and it may not** | completion; recovery; `turn_succeeded` |
| `is_progress_from()` | progress classification | whether an observation is progress | the predicate — it is an *input* to `consecutive_flat`, not to the predicate |
| `trap_fired` | the latch | whether the predicate may be true | anything else |
| `min_consecutive` | the verdict threshold | `observe()`'s return value and `verdict` | the predicate |
| `may_report_goal_met()` | completion input (predicate) | whether the goal may be reported met | whether `done` is withheld; recovery |
| the completion gate | enforcement (behind `stagnation_gate`) | whether `done` is withheld **once more** | the goal state; the predicate |
| `derive_goal_state()` | the goal arbiter | the six goal states | the predicate; whether `done` was withheld |
| `RecoveryLadder` | recovery | the next rung | completion; the goal state |

**No reopening authority is created, and none exists.** This resolves the one competition the recon found —
`is_progress_from` versus `trap_fired`, both inside the detector — explicitly, in favour of `trap_fired`,
with the reason recorded above. It was previously an unstated preference; it is now a rule.

### State transition

```
observation (a new tool_result)
      |
      v
digest == previous digest ?
   |                    |
  yes                   no            <-- "no" <=> genuine growth in a or w (ADR-0034 decision 3)
   |                    |
   v                    v
consecutive_flat += 1   consecutive_flat = 0
trap.observe -> 'repeat'
trap_verdicts.append    <-- MONOTONIC: never cleared, never truncated
   |                    |
   v                    v
predicate = False       predicate = not trap_fired   -> still False once the latch has fired
```

**The only allowed transition out of a closed predicate is the end of the detector's lifetime** — the next
turn's `run_turn`, which constructs a fresh detector. There is no in-turn transition from closed to open.

### Bounds

| Bound | Value | Owner | Gates the predicate? |
|---|---|---|---|
| the latch | 1 firing, permanent for the turn | the detector | **yes — it is the predicate** |
| verdict threshold (`min_consecutive`) | 2 | the detector | no |
| intervention budget | 2 per turn | the engine | no |
| iteration budget | `max_iterations` (50) | the engine loop | no |
| time budget | `turn_timeout` (1800 s) | `asyncio.timeout` | no |

**No new bound is introduced.** Monotonicity removes a potential cycle rather than adding one, so the
intervention budget remains the binding constraint on the gate and the iteration and time budgets remain
the outer limits. The last iteration still cannot be withheld (ADR-0036 §4).

### Replay

**Unchanged, and no new durable fact is required.** Row 4 consumes the predicate's value at turn end, and
that value is already recorded as `stagnation_allows_goal_met`, taken from the same computation the arbiter
used (ADR-0036 §6 / F35). Monotonicity *removes* a transition, so it cannot add a durable input.

```text
reconstruct(terminal_outcome, acceptance_verdict, stagnation_allows_goal_met, turn_succeeded)
    == the live goal state
```

Replay does **not** need: the intervention count (ADR-0036 §7, re-ratified), a replan event (the nudge is
already a `[SYSTEM]` transcript message and is not an authority input), or the progress evidence (the
predicate is the input; the observations only explain it). **`stagnation_allows_goal_met` remains
sufficient.**

### Audit

The `STAGNATION` event is the **verdict's** record; the goal record is the **predicate's**. Neither is
extended, and no new event kind is introduced. The known limitation — a trap-closed turn records the
predicate but not the observations — is stated in the Decision above with its revisit condition.

### Interaction with existing ADRs

| ADR | Interaction |
|---|---|
| **ADR-0034** | **Compatible, and load-bearing.** Its decision 3 (*accumulated rather than differential*) is *why* the digest is monotone, and its removal of the opt-in signal source is why no stricter reopening evidence is available without reversing it. Unchanged |
| **ADR-0035** | **Compatible; no amendment required.** Row 4 names `may_report_goal_met()`'s *value* as its input, not its history, so a monotonic predicate satisfies it exactly. Its rationale (*"a stagnated goal must never reach `GOAL_MET`"*) is *strengthened* by monotonicity, and its precedence (row 4 over row 6) is untouched |
| **ADR-0036** | **Amended, not superseded.** §6's clause is widened in scope from *trap-sourced* to *all live-path stagnation*; its truth table's flat-run row is corrected to **unreachable**; §3's predicate-only seam, §4's bound and last-iteration rule, §5's fail-open and §7's journaling decision all stand |
| **ADR-0029 / ADR-0033** | Untouched |
| **ADR-0002** | Honoured: no flag is added, and `stagnation_gate` and `graph_oscillation_guard` remain the two rollback levels |

### Rejected alternatives

| Alternative | Factual reason |
|---|---|
| **Option A — M13 owns reopening** | Requires overriding ADR-0036 §6's ratified clause. Reopening on `is_progress_from` reopens on *novelty*, making the latch near-vacuous; a stricter rule needs verification or P3 data the detector does not import and cannot read, i.e. reversing ADR-0034. It also changes M13's remit from *"is progress stalled"* to *"has recovery occurred"* |
| **Option B — the runtime owns reopening** | Conflicts with ADR-0036 §3 (the engine receives only a read-only predicate): a runtime-derived predicate makes the runtime a second opinion on stagnation — the shape ADR-0035 and ADR-0036 removed |
| **Option C — recovery owns reopening** | Conflicts with ADR-0035 §5 and ADR-0036's ordering: completion is evaluated before recovery, and the ladder runs at the turn boundary and only when `not turn_succeeded` (`runtime.py:1149`). It would make a detection fact into a recovery decision, and require a producer for `RECOVERY` records, which today has none |
| **Make `min_consecutive` gate the predicate** | Not available alongside monotonicity: a predicate depending on `consecutive_flat` alone reopens when progress resets it, so this option *is* a reopening design and would need an owner, evidence and replay contract |
| **Record the observations in the trap-closed case** | Adds persistence for explainability, which ADR-0035's replay contract does not require — the predicate is already durable. Recorded as a limitation with a revisit condition instead |
| **Do nothing** | Leaves a ratified ADR asserting an unreachable row, and leaves the effective detection threshold undocumented — the two facts that made this phase necessary |

### Implementation boundary

**Authorised in a future implementation phase — documentation only:**

1. `core/stagnation.py` — **docstrings only**, no behaviour: state in `may_report_goal_met()` that it is
   `False` from the first flat observation and is monotonic for the detector's lifetime; scope the module
   docstring's *"N consecutive"* mitigation to the verdict; align `trap_fired`'s *"not sufficient on its
   own"* with the predicate's actual rule.
2. `AGENTS.md` — its completion-gate section already states that the gate cannot change the goal state;
   verify it matches this ADR's wording.

**Forbidden without a superseding ADR:** clearing or truncating `trap_fired`; changing
`may_report_goal_met()`'s rule; changing `is_progress_from()`; changing `OscillationTrap`; making
`min_consecutive` gate the predicate; adding a reopening mechanism, a second detector or a second
completion predicate; adding persistence or a new event kind for reopening; changing ADR-0035's
precedence; changing recovery semantics; changing `turn_succeeded`; enabling `stagnation_gate` by default;
touching F8, the graph or fanout.

### Reversal condition

Reversed only by a superseding ADR that names a reopening **owner**, a reopening **evidence rule** that the
detector can actually observe without importing the verification or acceptance authorities, and a
**replay contract** for the transition. The trigger to revisit: enforcement enabled by default, or a
measured rate of `GOAL_STAGNATED` on turns that later produced verified work — i.e. evidence that the
monotonic predicate is producing false stagnation rather than conservative under-claiming.

---

## ADR-0038 — The configured output budget is sent verbatim; a provider's refusal of it is a configuration incompatibility, not a capability to be guessed

**Status:** ACCEPTED
**Phase:** POST-M13 (F39)
**Date:** 2026-09-25
**Predecessors:** `PHASE_POST-M13_F39_OLLAMA_NUM_PREDICT_FORENSIC_RECON.md` (the read-only recon that
classified this `ARCHITECTURE_CHANGE` / `ADR_REQUIRED: YES`) and
`PHASE_POST-M13_ADR-0016_LIVE_PROVIDER_MEASUREMENT.md` (which surfaced F39).
**Supersedes nothing.** It adds no behaviour to the running system; it fixes the *ownership* of a
boundary that currently has none, and constrains future provider work.
**Evidence:** `.workbuddy-ai/memory/post-m13-f39-recon/f39_probes.py` (probes A–G, all executed).

### Context

`config.max_tokens` is a **provider-independent** setting — it lives in `WispConfig` (`config.py:70-75`),
declared *"Max tokens per response (set to null/None for no limit)"*, default **131072**, pinned by
`tests/test_config.py:34`.

`OllamaClient` forwards it verbatim as `options.num_predict` (`ollama_client.py:302-304`, `:374-375`) —
one `None` check, no transformation.

`nemotron-3-ultra:cloud` has a maximum output of **65536**. Measured, boundary inclusive:

| `num_predict` | result |
|---:|---|
| 65536 | accepted |
| 65535 | accepted |
| 32768 | accepted |
| **131072** | **HTTP 400** — *"max_tokens (131072) exceeds model's maximum output tokens (65536)"* |

So **any** Ollama-served model whose maximum output is below the default is unusable out of the box,
and the failure is total rather than degraded.

### Problem

Wisp holds **no representation** of a model's output capacity, and **cannot obtain one**:

```text
/api/show  ->  model_info {".context_length": 262144}
           ->  no key for predict | output | max
```

For the observed model the only discoverable token number (**262144**) is **larger** than the rejected
value (**131072**). It is the **context window**, a different quantity — and using it as a capacity
proxy would leave the rejection in place while *raising* the budget wherever the request currently
succeeds.

### Current authority gap

| Decision | Owner today |
|---|---|
| the requested output budget | `WispConfig.max_tokens` — authoritative |
| the model's output capacity | **the endpoint** — authoritative externally |
| Wisp's representation of that capacity | **none** |
| the boundary policy | **none** |

Three places touch a token limit — `config.py`, `ollama_client.py`, `openai.py` — and **none owns the
boundary between them**. The one clamp in the tree (`openai.py:576-590`) is a hardcoded `api_base`
lookup for OpenRouter (4096) and NVIDIA (16384) that **excludes `api.openai.com`** (measured: it sends
131072 unclamped), and whose own comment — *"Local Ollama ignores this field anyway"* — is **false**.

### Decision

**Wisp declines to invent the boundary's data, and instead names its owner.**

The configured budget is **sent verbatim**. A provider's refusal of it is classified as a
**configuration incompatibility** and surfaced — never silently adapted, never guessed, never
retried. Adaptation is owned by the **provider implementation**, and is authorised **only** from a
typed, non-error source; where no such source exists, the provider's policy is *"send verbatim"*,
which is a defined policy rather than an absence of one.

**No new abstraction is created.** No capability registry, no model catalog, no benchmarking, no
caching, no negotiation protocol. The decision is a boundary assignment plus a diagnostic obligation.

### Normative rules

> **R1.** `max_tokens` is an **exact requested output budget**. Wisp SHALL send the configured value to
> the provider verbatim.
>
> **R2.** `max_tokens = None` SHALL omit the provider's output-budget parameter. This is an **explicit
> user-selected "provider decides" mode** and SHALL NOT be applied as an automatic fallback.
>
> **R3.** Wisp SHALL NOT derive a model's output capacity from `context_length`, from any other
> discoverable field, or from a hardcoded default.
>
> **R4.** When a provider cannot establish a model's output capacity from a **typed, non-error**
> source, Wisp SHALL send the configured budget unmodified and SHALL NOT clamp it.
>
> **R5.** When a provider rejects a request because the budget exceeds the model's capacity, Wisp SHALL
> classify it as a **configuration incompatibility** — permanent and non-retryable — distinct from a
> transient provider failure and from a model refusal.
>
> **R6.** The surfaced diagnostic SHALL name the configured budget and, when the provider's response
> states it, the provider-reported limit. Wisp SHALL NOT parse such a response to derive a capability.
>
> **R7.** Wisp SHALL NOT retry a configuration incompatibility. The existing retry classification and
> budget SHALL remain unchanged.
>
> **R8.** When a provider implementation can establish a model's output capacity from a typed,
> non-error source, that provider SHALL own the adaptation: it SHALL send
> `min(configured, capacity)` and SHALL record the **effective** value for that call.
>
> **R9.** Endpoint-specific affordability caps SHALL be classified as **affordability policy**.
> They SHALL NOT be generalised into the capacity boundary and SHALL NOT be used to infer capacity.
>
> **R10.** The provider implementation SHALL be the **single owner** of output-budget adaptation.
> `WispConfig` owns the requested budget and SHALL NOT adapt it.

### `max_tokens` semantics

**An exact requested provider output budget** (Q3 option A). Unchanged from today's declaration and
from today's behaviour. Every alternative meaning — "a desired upper bound subject to provider
capability", "a global logical budget providers may translate" — would redefine a user-facing setting
**silently**, which R1 and R4 forbid.

### Unknown-capability semantics

**One rule, no cases** (Q4). Whether the provider is local or cloud, online or offline, the model known
or unknown: **send verbatim; on rejection, classify and surface.** The only thing that varies is what
the diagnostic *can include* — the limit, when the response states it. No separate semantics are
created for any axis, because separate semantics are how a system acquires a second policy it never
decided to have.

### Retry semantics

**Unchanged.** A 400 is permanent and is **not** retried — verified: `_post_stream` retries on `>= 500`
only (`ollama_client.py:696`), and one logical call produced **exactly one** POST. R7 fixes this
explicitly so that no future "negotiation" is added by accident.

### `None` semantics

**Unchanged, and load-bearing.** `max_tokens = None` omits `num_predict` entirely; the previously
failing model **accepts** it. It is the **documented, user-controlled escape hatch** — not an automatic
fallback. R2 exists to prevent a future implementer from "fixing" F39 by silently omitting the
parameter whenever a request fails, which would make the setting advisory without anyone deciding that.

### OpenAI clamp treatment

**Q8 option D — reclassified, not folded in, not removed.** The existing clamp stays exactly as it is;
this ADR names what it actually is: an **endpoint-specific affordability policy** (staying under a
credit balance), not model output capacity. The distinction is the point:

```text
model output capacity   -> a property of the model      (F39; Wisp cannot observe it)
provider credit cap     -> a property of the account    (the OpenAI clamp; Wisp hardcodes it)
```

Folding them together would make a credit policy look like a capability, and R9 forbids it. The clamp's
false claim about Ollama is corrected as a comment in the mechanical sub-phase (§ Implementation
boundary) — a documentation fix, not a behavioural one.

### Offline semantics

**Preserved, and no new dependency is introduced.** The decision requires **no** metadata service, no
extra request, and no network beyond the provider connection already required. Capability is simply
*not* sought. Local Ollama, cloud-routed Ollama, a disconnected daemon and an unknown model all take
the R4 path: send, and surface a refusal if one comes.

### Replay semantics

**No new persistence, and no change to replay.**

| Item | Kind | Persisted? |
|---|---|---|
| configured `max_tokens` | **configuration** | already exists as a config field |
| effective `max_tokens` | derived execution fact | **equals the configured value** under R1/R4 — nothing new |
| provider capability | derived provider fact | **Wisp does not hold one** — nothing to persist |
| provider / model | configuration | already in `fingerprint()` and the session |
| negotiation result | — | **does not exist** under R7 |

Replay reconstructs outcomes from the journal; request parameters are not part of it, and this ADR does
not add them. **R8 carries a standing obligation:** if a provider ever adapts, the effective value
becomes a derived execution fact and MUST be recorded with the call — a follow-up, not new persistence
now.

### Measurement impact

**ADR-0016 is not modified by this ADR.** Its contract is *"a measurement period showing how many turns
become `INCONCLUSIVE`"*, and this decision changes no verdict.

Future measurement periods SHALL record, per observation:

```text
configured max_tokens
effective num_predict          (equal to the configured value under R1/R4)
done_reason                    (truncation was never instrumented)
provider and model
```

The first two matter because the ADR-0016 population that *did* run used a declared harness override
(`max_tokens=32768`) to avoid F39; a silent override is the failure mode this obligation closes. This
does **not** amend ADR-0016 — it is an instrumentation obligation on future phases. If a future
implementation ever adapts the budget (R8), the measurement contract would need an explicit statement
about effective-vs-configured, and **that** would be a follow-up ADR.

### Implementation boundary

The ratified policy is satisfied by current behaviour **except for R5 and R6**. That makes the whole
implementation boundary **diagnostic**:

```text
MECHANICAL SUB-PHASE   (authorised; no ADR further required)
  production files:    wisp/ollama_client.py   — the HTTPError branch of _post_stream
  behaviour:           classify a rejection whose body states a model output limit as a
                       CONFIGURATION INCOMPATIBILITY, and include the configured value and
                       the provider-reported limit in the surfaced error
  doc fix:             the false "Local Ollama ignores this field anyway" comment in
                       wisp/providers/openai.py
  tests:               one asserting the surfaced message names the configured value and the limit
  configuration:       none        defaults:  none        dependencies: none
  migration:           none        feature flags: none

BEHAVIOURAL SUB-PHASE
  EMPTY. R1–R4 and R7–R10 describe behaviour that already holds. This ADR authorises no
  behavioural change, which is why it requires no flag and no default change.
```

### Non-goals

Explicitly **not** authorised: a universal provider capability registry; a model catalog; automatic
model benchmarking; dynamic token-budget optimizers; global token schedulers; adaptive agent planning;
graph-level budgets; subagent resource budgets; any change to `max_tokens`'s default; any change to
`WispConfig.fingerprint()`; any renegotiation protocol; any caching of provider facts.

### Rejected alternatives

| Alternative | Why rejected |
|---|---|
| **Static global cap** — `min(max_tokens, GLOBAL_CAP)` | The cap would be **invented** (R3). It silently rewrites a user's configured budget (R1), and no value is defensible: any cap low enough to be safe is arbitrary, and any cap high enough to be harmless does not fix the failure |
| **Model-aware clamp** — `min(max_tokens, capacity)` | **Not implementable.** The capacity is not discoverable; the only nearby number is the context window, which is larger than the rejected value |
| **Capability discovery + cache** | Needs a source that does not exist, and would add a request, a cache, an invalidation rule, a staleness policy and a fail-open/fail-closed decision — a new authority, for a fact the endpoint does not publish |
| **Omit `num_predict` automatically on failure** | Makes `max_tokens` **advisory** without anyone deciding that (R1, R2). `None` is the explicit form of that choice and already exists |
| **Renegotiate from the error body** — parse *"maximum output tokens (65536)"*, retry with it | Makes an **undocumented error string** a capability source (R6), turns a permanent 400 into a retry (R7), and creates a second authority over capacity — the provider's error format. It would also make the effective budget depend on how a failure happened to be phrased |
| **Use `context_length` as capacity** | The near-miss trap: **262144 > 131072**, so it would leave the rejection in place *and* raise the budget wherever it currently works |
| **Fold the OpenAI clamp into the new boundary** | Conflates affordability with capacity (R9). They are properties of different things |
| **Change the `max_tokens` default** | It is pinned by a test with a stated product rationale, and the default is not the defect — the defect is that a refusal has no name |

### Risks

| Risk | Assessment |
|---|---|
| A user still hits the 400 | **Yes — by design.** The decision makes the failure *legible*, not impossible. The remedy is `max_tokens ≤ capacity`, or `null`; both are user choices, and both are documented |
| The decision looks like "do nothing" | It is not: it **assigns the boundary's owner** (R8, R10), **names the error class** (R5) and **forbids the three tempting wrong fixes** (R3, R6, R9). Without it, the next provider to gain a capability would improvise a policy |
| R8 becomes a licence to build a registry | R8 requires a **typed, non-error** source to exist *first*. It authorises adaptation, not acquisition |
| The diagnostic parses a provider string | R6 permits *displaying* a limit the provider states, and forbids *deriving* a capability from it. The distinction is the whole guard rail |
| `max_tokens` is absent from `fingerprint()` | Recorded as a follow-up, **not** changed here. A config change mid-session may not invalidate a cached core |

### Rollback / reversal

**Trivially reversible, because the decision adds no behaviour.** Reverting the R5/R6 implementation
restores today's generic `OllamaError` exactly; no state, no migration, no flag.

R8–R10 are constraints on future work rather than behaviour, so abandoning them requires a
**superseding ADR** that names (a) the capability source it intends to use, (b) why an error string is
or is not an acceptable source, and (c) who then owns the boundary. The trigger to revisit: a provider
that **does** expose a typed output-capacity field — at which point R8 activates without amending this
ADR — or a pattern of users hitting the incompatibility, which would make a *product* answer
(different default, or a guided remedy) the right response rather than an architectural one.

### Follow-up questions

1. **Should `max_tokens` participate in `WispConfig.fingerprint()`?** It does not today
   (`config.py:1188-1198`), so a mid-session change may not invalidate a cached core. Out of scope here.
2. **Should config validation reject `max_tokens = 0`?** The schema is `(int, type(None))` with no
   `min` (contrast `temperature`, which declares `min`/`max`); `0` is accepted by config **and** by the
   endpoint, and would silently produce an empty response.
3. **Should the `max_tokens` default remain 131072?** Its rationale (`tests/test_config.py:34`) is a
   product one, not a provider one. A product decision, not an architectural one.
4. **Is the incompatibility rate worth measuring?** No instrumentation exists; if the pattern is
   common, the remedy belongs in the setup/doctor surface rather than in the provider.

---

## ADR-0039 — Providers may emit typed or dict events; the core owns one canonicalization boundary, and that boundary is separate from the stall guard

**Status:** ACCEPTED
**Phase:** POST-M13 (F40)
**Date:** 2026-09-25
**Predecessors:** `PHASE_POST-M13_F40_ITERATION_WRAPUP_TYPED_EVENT_FORENSIC_RECON.md` (the read-only recon
that classified F40 `CONFIRMED_PRODUCTION_DEFECT` / `ADR_REQUIRED: YES`) and
`PHASE_POST-M13_ADR-0016_LIVE_PROVIDER_MEASUREMENT.md` (which exposed F40 in real-provider traffic).

### Context

Wisp has two shipped provider families that answer the same protocol method with **different Python
types**. `OpenAIProvider.generate_stream_events` yields dict literals (11 of them). `OllamaClient`
yields frozen dataclasses — `TokenBatch`, `ToolCallBatch`, `Checkpoint`, `StreamComplete`,
`StreamError` — and `OllamaProvider` returns that generator unchanged. `MockProvider` also yields
dataclasses.

`wisp/providers/protocol.py` declares the opposite:

```python
def generate_stream_events(...) -> Generator[dict[str, Any], None, None]:
    """Yields standardized event dictionaries: ..."""
```

The declaration is not enforced, and the divergence is not cosmetic. Four consumers read provider
events; only one of them is protected.

### Problem

A consumer that reads a **raw** provider stream and assumes dicts breaks on every typed provider. The
only canonicalizer invocation in the codebase lives *inside* `_guarded_provider_stream`, so any consumer
that bypasses the guard also bypasses canonicalization — and there is a legitimate reason to bypass it.

F40 is the live instance: the iteration wrap-up loop (`stateless.py:1080-1088`) calls
`_stream_events_async` directly and does `ev.get("type", "")`, which raises
`AttributeError: 'TokenBatch' object has no attribute 'get'` on the first event. The exception is
swallowed, `wrapped_up` stays `False`, and the model's summary is discarded in favour of a bare
`Max iterations reached`.

### Established facts

All measured during F40 recon; re-verified for this decision.

1. **The contradiction is real.** `protocol.py:36` declares `dict`; `OllamaClient` and `MockProvider`
   yield dataclasses; `OpenAIProvider` and the Ollama *fallback* path (`_generate_stream_events_direct`)
   yield dicts; `_NullProvider` yields a dict.
2. **The typed Ollama path emits no `done` at all.** Its only terminal is
   `StreamComplete(phase="complete")`. `{"type": "done"}` occurs only on the Ollama *fallback* path and
   in `openai.py`. So the terminal spelling depends on which provider path runs, and F40-2 is
   structural rather than incidental.
3. **The terminal vocabulary has four spellings and no owner** (§Terminal vocabulary).
4. **The guard consumes terminal markers.** `provider_stream.py:197-208` sets `saw_terminal` and
   `break`s *before* `yield event`, so a consumer routed through the guard never observes a terminal
   event. The guard also **retries** an attempt that produced nothing usable (`max_attempts`).
   Consequently the wrap-up's `elif etype == "done"` is *designed* for an un-guarded stream — and
   routing the wrap-up through the guard would be wrong on two counts.
5. **Canonicalization is already lossy, and the loss is safe.** `_normalize_event` is a 16-field
   whitelist. Measured per class, it drops: `TokenBatch.batch_index`/`phase`;
   `ToolCallBatch.phase`; **all five** `Checkpoint` fields; `StreamComplete.final_content`/
   `final_thinking`/`tool_calls`/`total_tokens`/`validation_hash`/`phase`;
   `StreamError.error_type`/`partial_content`/`partial_thinking`. **Every dropped field has zero
   consumers** outside the producers. The projection is minimal but *adequate*.
6. **There are two canonicalizers, and they have drifted.** `WispAgentCore._normalize_event` (16 fields,
   returns a flat dict) and `wisp.core.events.normalize_event` (14 fields, returns an `AgentEvent` with
   the payload in `.data`). The second lacks `calls` and `done_reason`; measured, it maps a
   `ToolCallBatch` to `{"type": "tool_calls", "data": {}}` — **the tool calls vanish silently**. Both of
   its call sites (`runtime.py:793` else-branch, `headless.py:48`) receive engine output, which is
   always a flat dict, so the divergence is **latent today**. Recorded separately as **F42**.
7. **Replay is unaffected.** Typed events never cross persistence; `runtime.py:790-798` persists
   canonical dicts.
8. **`_normalize_event` is total.** Dict → copy; object → `hasattr`/`getattr` whitelist; otherwise
   `type = "unknown"`. It has no raising path for a provider event.

### Current authority gap

| Concern | Current owner | Evidence |
|---|---|---|
| Event representation **contract** | `providers/protocol.py` — declares `dict` | contradicted by two shipped providers, unenforced |
| Canonicalization of provider objects | **two** owners: `WispAgentCore._normalize_event` and `events.normalize_event` | divergent whitelists; the second is destructive (F42) |
| **Invocation** of canonicalization | `_guarded_provider_stream` | bundled with stall recovery, so bypassing recovery bypasses the adapter |
| Terminal vocabulary | four spellings, no owner | §Terminal vocabulary |
| Consumer obligation | unwritten | three of four consumers do not honour it |

### Decision

> **Providers MAY emit either a canonical dict or a typed stream event. The core owns exactly one
> canonicalization boundary for provider objects, that boundary is total and whitelist-based, and it
> must be obtainable *without* stall recovery. No core consumer may interpret a raw provider event.
> The terminal vocabulary has one authority.**

Typed events are **not** an accident and **not** a defect: they are a provider's richer *internal*
representation (batches, checkpoints, validation hashes), and the canonical projection is the only part
any consumer actually reads. The fix is therefore **not** to flatten the providers; it is to make the
existing canonical boundary **mandatory, singular, and separable**.

### Normative rules

> **R1.** A provider MAY yield either a canonical `dict[str, Any]` or a typed stream event. Both are
> **valid provider output**; neither SHALL be treated as a defect. The `Provider` protocol SHALL declare
> both forms, and SHALL state that canonicalization is the consumer's obligation.
>
> **R2.** Exactly **one** canonicalizer for provider objects SHALL exist: `WispAgentCore._normalize_event`.
> No second implementation SHALL canonicalize provider objects. `events.normalize_event` SHALL remain a
> canonicalizer of `AgentEvent`/dict inputs only.
>
> **R3.** Canonicalization SHALL be **total**: for any provider event it SHALL return a canonical dict
> and SHALL NOT raise.
>
> **R4.** No core consumer SHALL interpret a **raw** provider event. A consumer that needs provider
> events SHALL obtain them from the canonical boundary.
>
> **R5.** The canonical boundary SHALL be obtainable **without stall recovery**. Normalization and
> stall/empty-stream recovery are two responsibilities and SHALL NOT be bundled such that normalization
> is unreachable without retry semantics.
>
> **R6.** The canonical boundary SHALL **forward terminal markers** to a consumer that requests them.
> The stall guard SHALL continue to consume them (its `saw_terminal` contract is unchanged).
>
> **R7.** The terminal vocabulary SHALL have **one authority**: `provider_stream.TERMINAL_TYPES`.
> Consumers SHALL NOT re-spell it.
>
> **R8.** `done` and `complete` are **synonyms**: both mean *the provider declared the stream finished*.
> `stream_complete` SHALL remain **accepted** as a legacy alias although no producer emits it.
>
> **R9.** `checkpoint`, `usage` and `stream_stats` are **bookkeeping, not terminal**. They SHALL NOT be
> treated as stream completion.
>
> **R10.** A canonicalization failure is an **internal invariant violation**, never a provider failure.
> Because R3 makes canonicalization total, this is unreachable by construction — so a consumer's error
> path can only report genuine provider failure.
>
> **R11.** Canonicalization SHALL be a **whitelist projection**: it may remove data, never add, never
> execute, never mutate authority, never reinterpret tool arguments.
>
> **R12.** Persistence and replay SHALL continue to store canonical dicts. **No migration is authorised
> by this decision.**

### Provider contract

A provider SHALL emit **one of**: a canonical dict; or a typed stream event carrying `type` or `phase`
plus whitelist-readable attributes. A provider SHALL NOT be required to convert. A provider SHALL NOT
decide terminal semantics, completion, or canonical shape (R7, R11).

### Normalization contract

Input: any provider event. Output: a flat canonical dict with `type` always present and payload fields
drawn from the existing 16-field whitelist. Unknown fields are **dropped** — the whitelist is
deliberately narrow (its docstring: *"avoid leaking internal state or circular references"*), and fact 5
shows nothing consumed is lost. The schema is **not** expanded by this decision: no consumer needs a
dropped field, so adding one would be speculative.

### Canonical terminal vocabulary

| Spelling | Meaning | Producer | Canonical? |
|---|---|---|---|
| `done` | provider declared the stream finished | `openai.py`, Ollama fallback, protocol bridge | **yes — synonym** |
| `complete` | provider declared the stream finished | normalized `StreamComplete.phase` (Ollama typed path) | **yes — synonym** |
| `stream_complete` | *none* | **no producer** | legacy alias, accepted, not canonical |
| `checkpoint` | stream-integrity marker | normalized typed `Checkpoint` | **no — bookkeeping** |
| `usage` | token accounting | no stream producer | **no — bookkeeping** |
| `stream_stats` | per-stream diagnostics | `openai.py` | **no — bookkeeping** |

Answering the two questions this ADR exists to make answerable:

- *"the provider successfully completed the response"* → **`type ∈ {done, complete}`** (R8).
- *"the stream itself ended"* → **not a provider event.** Stream end is the guard's `saw_terminal`
  bookkeeping; the transport-level "no terminal marker" case is already surfaced as an explicit
  truncation error by the guard. These concepts SHALL NOT be merged.

### Consumer invariant

> **No core consumer may interpret raw provider events.** This is an **architectural invariant**, not a
> coding convention, because a convention is exactly what the codebase already had and it failed in
> three places. It is enforced by R5: the canonical boundary is a **callable a consumer must obtain
> events from**, so bypassing it requires deliberately reaching past the boundary rather than merely
> forgetting a step.

Consumers: main loop (already compliant), wrap-up, compaction, planner (all three to be moved),
runtime persistence and transport (already receive canonical dicts).

### Wrap-up semantics

The wrap-up needs **normalization without retry and without terminal-marker consumption** — it must see
the marker to set `wrapped_up`, and it must not re-issue an empty final round. The decision therefore
requires the boundary to be **separable** (R5, R6), not to route the wrap-up through the guard. This is
the explicit resolution of the tension the recon identified.

### Compaction / planner

Both consume the same canonical boundary and are closed by the same rule. `compaction.py` currently
turns a typed event into `"[ERROR: …]"` and silently degrades to truncation (latent: `compaction_model`
defaults to `""`). `planner.py` currently filters typed events out with `isinstance(c, dict)` and
produces empty text (latent: reachable only for a provider without `generate`). Neither is a separate
architecture question; neither needs its own decision.

### Failure semantics

The recon showed a provider failure and a representation failure collapsing into one observable. The
resolution is **not** a new error taxonomy — it is R3 + R4: with canonicalization mandatory and total,
the collapse cannot occur, because the only remaining cause of that error path is a genuine provider
failure. No new error type is authorised.

### Replay implications

**UNCHANGED.** Canonicalization already produces the persisted shape, and typed events never reach
persistence (fact 7). **No persistence migration. No replay migration.**

### Compatibility

| Provider | Today | After |
|---|---|---|
| `OpenAIProvider` | dicts | unchanged — already canonical |
| `OllamaProvider` / `OllamaClient` | typed | unchanged output; consumed canonically |
| `MockProvider` | typed | unchanged output |
| Ollama fallback path | dicts | unchanged |
| `_NullProvider` | dict | unchanged |
| protocol default bridge | passes through | unchanged |
| future providers | unspecified | MAY emit either form (R1) |

No provider is rewritten. No test that pins typed output is invalidated. No OpenAI-only assumption is
introduced.

### Security

Canonicalization is a **whitelist projection**: it can only remove keys. It cannot execute provider-
controlled code, invoke a tool, mutate authorization, approval or security policy, bypass
`ToolExecutor`, or promote untrusted tool arguments into authority. Tool arguments remain data that the
existing gates validate. Model- and provider-controlled input SHALL NOT influence normalization,
terminal classification, the provider contract, or completion authority — all remain host-owned.

### Implementation boundary

**Mechanical:** (i) a normalization-only stream boundary; (ii) the guard obtains its events from it and
keeps its recovery; (iii) the wrap-up, compaction and planner obtain events from it; (iv) the terminal
predicate read from `TERMINAL_TYPES` (R7) at each consumer; (v) `events.normalize_event`'s provider-object
branch subordinated to R2 (F42); (vi) the protocol declaration widened to admit both forms (R1).

**Behavioural:** the wrap-up delivers the summary it already produces instead of discarding it, and
stops emitting a spurious `Max iterations reached`. That is the **intended effect of closing a defect**,
not a new behaviour. **No flag, no default change, no configuration, no migration.**

### Non-goals

Not a provider rewrite. Not a canonical-schema expansion. Not a new error taxonomy. Not a change to
retry, stall, cancellation, tool-call, verification, goal-state or graph semantics. Not a persistence
change. Not a second detector or a new authority.

### Rejected alternatives

1. **Option A — providers MUST emit dicts.** Rejected. It moves the canonicalizer into every provider,
   creating *N* authorities instead of one (violates the stated criterion and the repo's discipline
   that two producers of one structure is a defect). It rewrites two providers and invalidates the tests
   that pin their typed output. And it buys nothing: fact 5 shows the canonical projection is already
   the only thing consumed, so the typed form would have to be reconstructed internally anyway.
2. **Option D — consumer-local normalization.** Rejected, and *empirically* so: `events.normalize_event`
   is precisely this alternative already realised, and it has **drifted** — it silently drops `calls`
   (F42). N sites means N whitelists and N opportunities to drift.
3. **Route the wrap-up through `_guarded_provider_stream`.** Rejected on two measured grounds: the guard
   retries an empty attempt up to `max_attempts` (wrong for a final tool-less round), and it **consumes**
   the terminal marker before yielding, which would make `wrapped_up` unreachable. This is the specific
   trap the decision exists to avoid.
4. **Patch only the wrap-up (the F37 pattern).** Rejected: it leaves F40-2 live (proven — a normalized
   `StreamComplete` still fails a `{done}`-only check), leaves F40-3/F40-4 open, leaves the
   normalization/stall bundle in place, and leaves two canonicalizers drifting.
5. **Expand the canonical schema to carry the typed metadata.** Rejected: no consumer reads any dropped
   field (fact 5), so this would add surface for no reader.

### Risks

- **R1's latitude could be read as "anything goes".** Mitigated by R3/R4: the latitude is at the
  *provider* edge only; the canonical boundary is strict and singular.
- **F42 is latent, not fixed here.** Its call sites receive dicts today; a future consumer passing a
  typed event to `events.normalize_event` would silently lose tool calls. R2 closes it structurally, but
  the implementation phase must not treat it as already closed.
- **The `stream_complete` alias has no producer.** Retaining it (R8) preserves compatibility but leaves a
  vocabulary entry that no test can exercise end-to-end; it should be covered by a unit-level assertion
  rather than an integration one.
- **`_normalize_event`'s whitelist is load-bearing and undocumented as a contract.** Fact 5 makes it a
  *contract* by this ADR; a future field addition must be justified by a consumer.

### Rollback / reversal

Fully reversible and cheaply so: the change adds a boundary and moves three consumers onto it. Reverting
restores the previous call sites and changes no persisted data, no configuration and no default. Nothing
in this decision is observable in a journal.

### Follow-up questions

1. Should the canonical `type` for a normalized `StreamComplete` be `"done"` (aligning with the dict
   providers) rather than `"complete"`? R8 makes both acceptable; a single *emitted* spelling would be
   simpler, but changing it touches the guard's `TERMINAL_TYPES` and the main loop's suppression filter
   (`stateless.py:735`), so it is left as an explicit follow-up rather than decided here.
2. Should `events.normalize_event`'s provider-object branch be **deleted** (making R2 structural) or
   retained as a delegating shim? Deletion is cleaner; retention is safer for external callers.
3. Is a static check worth adding so a future consumer cannot read a raw stream without going through
   the boundary? The invariant is architectural; enforcement beyond the API boundary is not yet decided.
4. `stateless.py:735` suppresses terminal events from the main loop, which the guard has already
   consumed — is that filter still load-bearing, or is it now redundant?

---

## ADR-0040 — The canonicalization authority is `events.canonical_event`; `_normalize_event` is a delegation facade

**Status:** ACCEPTED
**Phase:** POST-M13 (F40, reconciliation)
**Date:** 2026-09-25
**Predecessors:** `PHASE_POST-M13_F40_PROVIDER_EVENT_CONTRACT_ADR.md` (ADR-0039) and
`PHASE_POST-M13_F40_PROVIDER_EVENT_NORMALIZATION_IMPLEMENTATION.md` (which recorded the ownership
boundary its implementation actually established as its **only** declared deviation), reconciled by
`PHASE_POST-M13_F40_CANONICALIZATION_OWNERSHIP_RECONCILIATION.md`.

### Context

ADR-0039 R2 named the canonicalizer's subject as `WispAgentCore._normalize_event`. The implementation
found that the single implementation has to live in a module that **every** consumer can reach —
including `core/compaction.py` and `graph/planner.py`, which are outside the core and cannot hold a
per-turn `WispAgentCore`. It therefore put the whitelist and the projection in
`wisp.core.events.canonical_event`, with `_normalize_event` delegating.

That was declared as a deviation, not silently absorbed. This ADR decides whether the deviation is
the better architecture or an accident to be reverted.

### Problem

Which component **owns** provider-event canonicalization:

- `WispAgentCore._normalize_event` (ADR-0039's literal wording), or
- `wisp.core.events.canonical_event` (what the implementation established)?

The question is not *"which name is more prominent"*. It is *"which boundary is the cleanest single
authority, and does the record match it?"*

### Established facts

Re-verified for this decision; all eight hold.

| | Fact | Evidence |
|---|---|---|
| A | `events.canonical_event` owns the whitelist and the provider-object projection | `events.py:160` (`CANONICAL_EVENT_FIELDS`), `:180` (`canonical_event`) |
| B | `WispAgentCore._normalize_event` delegates | `stateless.py` — `return canonical_event(event)` |
| C | `events.normalize_event` delegates provider objects and owns no whitelist | its body has no `safe_fields` assignment and calls `canonical_event(event)` |
| D | Exactly one `CANONICAL_EVENT_FIELDS` definition | AST scan of every `wisp/**.py`: 1 definition, 2 readers (both in `events.py`) |
| E | F42 stays closed | `ToolCallBatch.calls` and `StreamComplete.done_reason` both survive |
| F | Every ADR-0039 consumer obtains canonical events | main loop (guard), wrap-up, compaction, planner |
| G | No second provider-object whitelist exists | AST scan for any collection with ≥4 canonical field names: **exactly one**; and exactly **one** function mapping a provider object to a canonical dict |
| H | Replay/persistence unchanged | no typed event class referenced in `runtime.py` |

### Dependency direction

| Module | `wisp.*` imports |
|---|---|
| `wisp/core/events.py` | **none — a leaf module** |
| `wisp/core/stateless.py` | **14**, including `wisp.core.events` |

`wisp.core.events` is already the low-level event-representation module: **21 production modules**
depend on it (`provider_stream`, `runtime`, `approval_gate`, `proposal`, `recovery`, `compaction`,
`planner`, `tool_executor`, `sdk`, the transports, …). Putting the canonical schema there makes the
dependency direction *downward into a leaf*; putting it in `stateless.py` inverts that.

### Decision

> **`wisp.core.events.canonical_event` is the formally ratified single provider-event canonicalization
> authority. `WispAgentCore._normalize_event` is retained as a compatibility/delegation facade, and
> `events.normalize_event` as a compatibility entry point with no independent provider-object
> canonicalization authority.**

Authority is defined by **ownership of the canonical schema and the provider projection**, not by which
historical facade invokes it. The implementation-discovered boundary is ratified; ADR-0039 R2's
*subject* is amended, and nothing else in ADR-0039 changes.

### Normative rules

> **R1.** The canonicalization authority SHALL be `wisp.core.events.canonical_event`. It SHALL own the
> canonical field whitelist (`CANONICAL_EVENT_FIELDS`), the provider-object interpretation, the
> typed→canonical mapping, the totality contract and the canonical output shape.
> *(Amends ADR-0039 R2's subject. R2's operative requirement — one implementation, no second
> whitelist — is unchanged.)*
>
> **R2.** `WispAgentCore._normalize_event` SHALL remain as a **compatibility/delegation facade**. It
> SHALL own no whitelist and no provider interpretation, and SHALL NOT be able to diverge from
> `canonical_event`.
>
> **R3.** `wisp.core.events.normalize_event` SHALL remain a compatibility entry point for
> `AgentEvent`/canonical-dict inputs, and SHALL **delegate** provider objects to `canonical_event`. It
> SHALL own no independent provider-object canonicalization authority.
> *(Supersedes ADR-0039 R2's clause that it "SHALL remain a canonicalizer of `AgentEvent`/dict inputs
> only". Delegating is strictly safer than rejecting: rejecting would make
> `normalize_event(provider_obj)` return `type="unknown"` and silently discard data — the F42 defect
> class. This resolves the single deviation the implementation phase declared.)*
>
> **R4.** Exactly one `CANONICAL_EVENT_FIELDS` definition SHALL exist. A second SHALL NOT be
> introduced under any name.
>
> **R5.** Authority over canonicalization SHALL be determined by **ownership of the canonical schema
> and the provider projection**, not by which facade invokes it. A delegating function is not a second
> authority.
>
> **R6.** `wisp.core.events` SHALL remain a **leaf module** — it SHALL NOT import from any other
> `wisp.*` module — so the canonical schema stays provider-neutral and low-level.
>
> **R7.** A new provider SHALL NOT implement provider-specific canonicalization, and a new consumer
> SHALL NOT implement consumer-specific canonicalization. Both SHALL go through `canonical_event`.
>
> **R8.** Everything else in ADR-0039 SHALL stand unchanged: typed-or-dict provider compatibility
> (R1), the canonical schema's contents (no expansion), `TERMINAL_TYPES` as the terminal authority
> (R7/R8), the normalization-only stream and its separation from stall recovery (R5/R6), F40's closure,
> F42's closure, the replay contract (R12) and the security contract (R11).

### Authority definition

| Term | Meaning | Component |
|---|---|---|
| **Authority** | owns the canonical field whitelist, provider-object interpretation, typed→canonical mapping, the totality contract, and the canonical output shape | `events.canonical_event` |
| **Facade** | a compatibility entry point that delegates, owns no whitelist, owns no provider interpretation, and cannot diverge | `WispAgentCore._normalize_event` |
| **Compatibility entry point** | the pre-existing event-normalization path, retained for its callers; provider objects delegate | `events.normalize_event` |
| **Consumer contract** | consumers receive canonical events only and never interpret raw provider events | the four consumers (ADR-0039 R4) |
| **Terminal authority** | which event types mean "the provider declared the stream finished" | `provider_stream.TERMINAL_TYPES` (unchanged) |

### F42 implication

F42 was two whitelists drifting apart. The ratified architecture is **one whitelist, one projection,
two delegating entry points**:

```text
CANONICAL_EVENT_FIELDS
        │
        ▼
canonical_event            <- the ONE implementation
        ▲
        │
   both callers
   (_normalize_event, normalize_event)
```

That is now the **intended invariant**, not a side effect: R4 makes a second whitelist a violation
rather than a drift risk, and R5 removes the ambiguity that let a "second canonicalizer" be created
without anyone noticing.

### Rejected alternatives

1. **Option A — retain `WispAgentCore._normalize_event` as the authority.** Rejected on four grounds.
   It **inverts the dependency direction** (the canonical schema would live in a module with 14 `wisp.*`
   imports and be consumed by `compaction`/`planner`, which are outside the core). It **couples generic
   event representation to agent-core state** — `stateless.py` is a ~2,400-line runtime module, and the
   canonical schema is provider-neutral data shape. It **requires changing already-tested code** for no
   consumer-visible benefit: no caller observes which module holds the whitelist. And "the original ADR
   said so" is not a reason — the original wording assumed a single consumer and a single core, which
   the F40 implementation disproved.
2. **Delete `_normalize_event` and have consumers call `canonical_event` directly.** Rejected: it
   churns the guard's injected-normalizer seam and this phase's own tests for no architectural gain.
   A delegating facade is not a second authority (R5), and removing it would be cosmetic churn of the
   kind §20 of the reconciliation brief forbids.
3. **Leave the deviation unratified as an implementation detail.** Rejected: an ownership boundary
   that exists only in code is exactly what produced F42 — the second whitelist was never *declared*,
   so nothing could detect it.

### Risks

- **Facade ambiguity.** A reader may take `_normalize_event` for the authority because it is the name
  the guard and the main loop call. Mitigated by R5's definition and by both functions' docstrings.
- **A future "helpful" second whitelist.** R4 forbids it and the AST assertion in the phase's test
  suite catches it.
- **`events.py` growing into a grab-bag.** R6 keeps it a leaf; the risk is scope, not direction, and
  the schema is deliberately not expanded (ADR-0039's measurement).

### Rollback / reversal

Trivially reversible and cheap: reverting means moving one whitelist and one projection function back
into `stateless.py` and turning `canonical_event` into the delegate. Nothing persisted, no
configuration, no default, no provider change.

### Follow-up questions

1. Should `events.normalize_event` be deprecated in favour of `canonical_event` for new callers, or
   kept indefinitely as the `AgentEvent` path? It is used by `runtime.py` (persistence) and
   `transport/headless.py`.
2. Is a static check worth adding so R4 and R7 cannot be violated without a test failing? The phase's
   suite asserts both; a lint rule would catch it earlier.
3. `events.py` now holds both `AgentEvent` and the canonical projection. If it grows further, the
   canonical schema may deserve its own leaf module — but that is a packaging question, not an
   authority one, and R6 already fixes the dependency direction.

---

## ADR-0041 — Recovery classification is semantic, and terminal detection precedes payload classification

**Status:** ACCEPTED
**Phase:** POST-M13 (F43)
**Date:** 2026-09-25
**Predecessors:** `PHASE_POST-M13_F43_F44_RECOVERY_COMPLETION_CONVERGENCE.md`; ADR-0039 (terminal
vocabulary authority), ADR-0040 (canonicalization authority).

### Context

F43: the same semantic condition — *a stream that produced no response* — entered **different recovery
paths depending on which provider emitted it**, and the typed path took the **worse** one.

```text
bare typed StreamComplete (phase="complete")  -> 1 provider call, NO error, empty "success"
bare dict {"type": "done"}                    -> 3 provider calls, then an honest error
```

The cause was structural, not a typo. `guarded_provider_stream` classified events in this order:

```python
if ntype not in bookkeeping:        # (1) vocabulary lookup
    got_meaningful = True
if ntype in TERMINAL_TYPES:         # (2) terminal detection
    saw_terminal = True
    if _terminal_has_payload(normalized):
        got_meaningful = True
    break
```

`WispAgentCore._BOOKKEEPING_TYPES` was `{"done", "stream_complete", "checkpoint", "usage",
"stream_stats"}` — it **re-spelled two terminal types and omitted the third** (`complete`). So a bare
`done` was correctly "not meaningful" at step (1), while a bare `complete` was wrongly "meaningful",
and the terminal branch's payload check at step (2) could only ever *add* meaningfulness, never remove
it.

The repository already knew. Three tests recorded it as a defect and deliberately pinned the symptom:

- `test_13h2_determinism.py::test_empty_object_stream_treated_as_natural_done` — *"counts as
  'meaningful' and the turn ends done(natural) with EMPTY content — **a silent empty success.
  Recorded, not fixed.**"*
- `test_13h5_success_derivation.py::test_natural_empty_success_preserved` — *"bookkeeping-set
  mismatch"*
- `test_13h4_success_semantics.py::test_f2_empty_object_natural_done` — *"# bookkeeping-set mismatch
  (H2)"*

and `test_13h_forensics.py` stated the intended contract in a comment:

> *"Production bookkeeping set: terminal markers must be included or a bare marker counts as
> meaningful."*

### Problem

Who owns the classification of a provider event as *terminal*, as *payload-carrying*, and as
*bookkeeping*, and how must those classifications be ordered?

The invariant that must hold: **provider representation must not determine recovery semantics.** A
typed `StreamComplete` and a canonical `{"type": "complete"}` must take the same path; a canonical
`{"type": "done"}` and its typed equivalent likewise.

### Decision

> **The guard SHALL detect terminal FIRST, and SHALL decide a terminal's meaningfulness solely from
> its payload. The non-terminal, payload-less vocabulary SHALL be listed once, and SHALL contain no
> terminal spelling — so there is nothing to drift.**

Terminal entries in the bookkeeping list were a *workaround* for classifying bookkeeping before
terminal. Ordering the terminal check first **removes the need for them entirely**, which eliminates
the duplication rather than deriving around it.

### Normative rules

> **R1.** `guarded_provider_stream` SHALL classify `ntype in TERMINAL_TYPES` **before** any
> non-terminal classification.
>
> **R2.** A terminal event's contribution to `got_meaningful` SHALL be decided **solely** by
> `_terminal_has_payload`. No vocabulary list SHALL be able to mark a terminal as meaningful.
>
> **R3.** `provider_stream.NON_PAYLOAD_TYPES` SHALL be the single list of non-terminal, payload-less
> event types (`checkpoint`, `usage`, `stream_stats`). It SHALL NOT contain any type that appears in
> `TERMINAL_TYPES`.
>
> **R4.** `TERMINAL_TYPES` (ADR-0039 R7) SHALL remain the sole terminal authority. This ADR adds no
> second terminal vocabulary and removes the one that existed.
>
> **R5.** A stream that produced only bare terminal markers SHALL be treated as **empty** — retried
> within the existing budget and, if still empty, surfaced as an explicit error. It SHALL NOT be
> reported as a successful empty response.
>
> **R6.** Equivalent semantic events from a typed provider and from a canonical dict SHALL produce
> **identical** recovery behaviour — same attempt count, same terminal classification, same outcome.
>
> **R7.** Non-terminal bookkeeping (`checkpoint`, `usage`, `stream_stats`) SHALL remain forwarded to
> the consumer and SHALL NOT be counted as meaningful — the existing recovery model is preserved, not
> simplified away.

### Rejected alternatives

1. **Derive the set: `BOOKKEEPING_TYPES = TERMINAL_TYPES | NON_PAYLOAD_TYPES`.** This makes the
   *current* behaviour correct with a one-line change, but it **keeps the hazard**: the ordering still
   lets a vocabulary list decide meaningfulness, so any *future* terminal type added to
   `TERMINAL_TYPES` would silently be mis-classified until someone remembered to add it to the derived
   set — exactly the drift F43 was. Reordering removes the requirement instead of automating it.
2. **`_BOOKKEEPING_TYPES = TERMINAL_TYPES`.** Semantically wrong: `checkpoint`, `usage` and
   `stream_stats` would stop being bookkeeping and would start counting as meaningful, so a stream
   carrying only a checkpoint would be treated as a response.
3. **Remove the bookkeeping concept and treat every non-terminal event as meaningful.** Wrong: a
   provider that emits only `stream_stats` (an HTTP-200-with-zero-deltas throttle signature) would be
   recorded as a successful response. `test_13h_forensics.py` exists precisely to prevent that.
4. **Fix only the typed path (special-case `StreamComplete`).** Rejected: a provider-specific branch
   is what F43 already was, inverted.

### Risks

- **A behaviour change on the typed path is intended and observable**: a bare `StreamComplete` now
  retries and can surface an error where it previously produced a silent empty success. That is the
  fix, and the three tests that pinned the symptom must be rewritten (§9 of the phase report).
- **`NON_PAYLOAD_TYPES` could be extended carelessly.** R3 forbids terminal spellings in it, and the
  phase's tests assert the disjointness.
- **The `done`-with-payload case** (`{"type":"done","text":"…"}`) must keep counting as meaningful —
  R2's payload check preserves it.

### Rollback / reversal

Trivial: restore the previous ordering and the old set. Nothing persisted, no configuration, no
provider change. The rewritten tests would have to be reverted with it.

### Follow-up questions

1. Should the guard also derive meaningfulness from payload for **non-terminal** types (an
   `error`-free `usage`-only stream is handled, but an unknown type carrying no payload still counts
   as meaningful)? Out of scope here: it would widen the change beyond F43's evidence.
2. Is `_terminal_has_payload`'s field list (`text`, `content`, `final_content`, `tool_calls`,
   `calls`) still the right set now that canonicalization drops `final_content`? It is harmless (the
   canonical `text`/`calls` carry the payload) but the dead key could be pruned.

---

## ADR-0042 — The completion chain: five distinct authorities, none derived from a lower one

**Status:** ACCEPTED
**Phase:** POST-M13 (F44)
**Date:** 2026-09-25
**Predecessors:** `PHASE_POST-M13_F43_F44_RECOVERY_COMPLETION_CONVERGENCE.md`; ADR-0035 (completion and
recovery are two authorities; the `derive_goal_state` arbiter), ADR-0039/0040 (event canonicalization).

### Context

F44 appeared when F40 was repaired: an iteration-budget-exhausted turn whose wrap-up succeeded now
ends `… content, done`, so `was_last_turn_complete` reads `True` where the typed path previously read
`False`.

Measurement showed the previous `False` was **not** the correct behaviour but an artifact: the spurious
`Max iterations reached` error (F40-1) was the last persisted event. The **dict** provider path —
untouched throughout — already produced `True` for the identical script.

The open question is therefore semantic, not a regression:

> What should completion mean when an iteration budget is exhausted but the final wrap-up produces a
> terminal event?

### Problem

The system carries five states that all sound like "it finished":

```text
provider terminal event   ("the provider ended the stream")
turn state                (turn_succeeded)
journal state             (was_last_turn_complete)
verification state        (acceptance_verdict / P3)
goal state                (derive_goal_state)
```

Nothing in the code says how they relate, so a reader is invited to collapse them — and a change to
one can silently look like a change to another.

### Established facts (measured)

Ten scenarios, each recording terminal event / `turn_succeeded` / `was_last_turn_complete` /
acceptance / goal state / terminal outcome:

| scenario | terminal | `turn_succeeded` | `was_last_turn_complete` | acceptance | goal state | outcome |
|---|---|---|---|---|---|---|
| ordinary success | `done` | **True** | True | inconclusive | **goal_unverified** | succeeded |
| provider terminal (typed) | `done` | True | True | inconclusive | goal_unverified | succeeded |
| exhaustion, wrap-up **fails** | `done` | **False** | **False** | inconclusive | **goal_failed** | failed |
| exhaustion, wrap-up **succeeds** | `done` | **True** | True | inconclusive | **goal_unverified** | succeeded |
| …then the **next turn** | `done` | True | True | inconclusive | goal_unverified | succeeded |
| verification **failure** | `done` | **True** | True | **fail** | **goal_failed** | succeeded |
| incomplete evidence | `done` | True | True | **fail** | **goal_failed** | succeeded |
| provider failure | **error** | False | False | inconclusive | goal_failed | failed |
| cancellation | **none** | False | False | inconclusive | goal_unverified | **incomplete** |

Two rows carry the whole decision:

- **row 1**: `turn_succeeded == True` with `goal_state == goal_unverified`. A turn can succeed without
  the goal being met.
- **row 6**: `turn_succeeded == True` with `goal_state == goal_failed`. A turn can succeed **while the
  goal fails** — the verification verdict is what decides, not the turn.

**The authorities do not collapse.** The `False`→`True` change on row 4 was the *correct* behaviour
being restored, not a new authority being created.

### Decision

> **The five states are distinct authorities with a fixed one-way relation. No higher state may be
> inferred from a lower one. An iteration-budget-exhausted turn that wraps up successfully is a
> COMPLETED TURN with an UNVERIFIED GOAL — neither a success nor a failure at the goal level.**

No new completion authority is introduced. No code change is required; what was missing was the
statement of the relation and the tests that hold it apart.

### Normative rules

> **R1.** A provider terminal event means **only** that the provider declared the stream finished. It
> SHALL NOT imply turn success, verification, or goal state.
>
> **R2.** `turn_succeeded` SHALL be derived from terminal evidence alone —
> `saw_done and not saw_fatal_error` (`runtime.py:938`). It SHALL NOT consult verification or goal
> state.
>
> **R3.** `was_last_turn_complete` SHALL mean **"the last persisted event is a DONE event"** — an
> *interrupted-turn / replay* signal, not a completion verdict. It SHALL NOT be read as, renamed to,
> or promoted into a success signal.
>
> **R4.** `acceptance_verdict` (P3) SHALL be the sole input that distinguishes `GOAL_MET` from
> `GOAL_FAILED`. `turn_succeeded` alone SHALL NOT reach `GOAL_MET` (ADR-0035 row 6 requires both).
>
> **R5.** **Iteration-budget exhaustion SHALL NOT be interpreted as goal failure.** An exhausted turn
> whose wrap-up succeeded is `goal_unverified` (no evidence), not `goal_failed`. It becomes
> `goal_failed` only if a **fatal** error ended the turn — which is what an exhausted turn whose
> wrap-up *also* failed produces.
>
> **R6.** A successful wrap-up SHALL NOT be interpreted as goal success. It supplies *content*, not
> *evidence*.
>
> **R7.** A successfully-wrapped-up exhausted turn SHALL be treated by the **next** turn as a
> **completed** turn: no incomplete-turn replay. Replay is for turns that did not reach their terminal
> event.
>
> **R8.** Verification evidence SHALL NOT be obscured by a successful wrap-up: an exhausted turn with
> a failing verification SHALL still reach `GOAL_FAILED`.
>
> **R9.** A terminal event SHALL NOT overwrite a recovery or escalation state; cancellation remains
> its own outcome (`incomplete`).

### The authority chain

```text
OBSERVATION      provider event (typed OR dict)
                       │  canonical_event  (ADR-0040)
                       ▼
CLASSIFICATION   canonical event type ∈ {content, tool_calls, terminal, bookkeeping, error}
                       │  guard: terminal-first, payload decides  (ADR-0041)
                       ▼
STREAM STATE     saw_terminal · got_meaningful · retry budget → complete | empty | truncated
                       │
                       ▼
TURN STATE       turn_succeeded = saw_done ∧ ¬saw_fatal_error        (runtime.py:938)
                       │
                       ▼
VERIFICATION     acceptance_verdict (P3) — VerificationFloorGuard's floor is its input
                       │
                       ▼
GOAL STATE       derive_goal_state(turn_succeeded, acceptance_verdict, …)   (ADR-0035)
                       │
                       ▼
ROUTING/RECOVERY terminal_outcome · escalation · incomplete-turn replay
```

Every arrow is one-way. `provider said done` never becomes `the goal is met`.

### Rejected alternatives

1. **Introduce a `turn_completed` flag distinct from `was_last_turn_complete`.** Rejected: it would be
   a second authority over the same journal fact, and the existing signal already means the right
   thing once it is documented.
2. **Make iteration exhaustion produce `GOAL_FAILED`.** Rejected: exhaustion is an absence of evidence,
   not evidence of failure. ADR-0035 maps INCONCLUSIVE to `GOAL_UNVERIFIED`, never FAILED — and
   "the budget ran out" is exactly inconclusive.
3. **Make a successful wrap-up produce `GOAL_MET`.** Rejected: it would let the model's own summary
   certify the work — model-controlled completion, which the architecture forbids.
4. **Make exhaustion suppress the terminal event so `was_last_turn_complete` stays `False`.** Rejected:
   that fabricates an interrupted turn where none occurred, and would make the next turn replay a
   turn that had already completed.
5. **Rename `was_last_turn_complete`.** Rejected under the "clarify the documented role rather than
   perform a cosmetic rename" rule: it is referenced by the runtime, five test files and the migration
   records, and its name is accurate for what it is — a *turn* signal, not a goal one.

### Risks

- **The name invites misreading.** Mitigated by R3 and by the tests in this phase that assert
  `turn_succeeded` and `goal_state` separately.
- **An exhausted turn counts as `succeeded` at turn level.** That is intended (the turn reached its
  terminal event and delivered its summary) and is why R5/R6 keep it out of the goal state.
- **`goal_unverified` for an ordinary successful turn** (row 1) may look surprising. It is ADR-0035's
  ratified design: with no verification evidence the goal is unverified, never met.

### Rollback / reversal

None needed — this ADR changes no behaviour. If a future decision changes the mapping, the tests in
`test_post_m13_f43_f44_authority_convergence.py` are the contract that must be updated with it.

### Follow-up questions

1. Should an exhausted turn record *why* it exhausted (budget vs. wrap-up failure) as a structured
   journal field, so the distinction in rows 3 and 4 is visible without inferring it from the error
   presence? It is currently derivable but not stated.
2. `terminal_outcome` is `succeeded` for row 6 (`goal_failed` with a failed verification). Is
   `succeeded` the right word at the *turn* level when the goal failed? It is accurate
   (turn-level ≠ goal-level), but the vocabulary is close enough to invite the collapse this ADR
   forbids.

---

## ADR-0043 — Meaningfulness is payload-based for every provider event; the classifier owns no vocabulary but the terminal authority

**Status:** ACCEPTED
**Phase:** POST-M13 (final execution-semantics closure)
**Date:** 2026-09-25
**Predecessors:** `PHASE_POST-M13_FINAL_EXECUTION_SEMANTICS_CLOSURE.md`; **supersedes ADR-0041 R3 and R7**
(the `NON_PAYLOAD_TYPES` list). ADR-0039 R7 (`TERMINAL_TYPES` is the sole terminal authority) stands.

### Context

ADR-0041 fixed F43 by ordering the terminal check first and reducing the bookkeeping set to the
non-terminal, payload-less types. That closed the reported defect — but it **kept the mechanism**: a
*vocabulary list* still participated in a *semantic* decision.

The closure audit found the mechanism still live, through a second door:

```text
bare terminal alone            -> 3 calls, error                    (correct)
checkpoint + bare terminal     -> 3 calls, error                    (correct)
UNKNOWN payload-less + terminal-> 1 call,  NO error, empty "success" (WRONG)
UNKNOWN with payload + terminal-> 1 call,  no error                 (correct)
content + bare terminal        -> 1 call,  no error                 (correct)
```

An event type the list does not mention is treated as meaningful, so it can **bless an otherwise empty
attempt** — F43's exact defect class, reached by a different spelling. Any future provider dialect
introduces a new spelling, so the list can never be complete.

### Problem

What makes a provider event count as *response output*, and may a vocabulary list participate in that
decision?

### Decision

> **An event counts as response output if and only if it CARRIES PAYLOAD. The decision is semantic for
> every event type, and the classifier owns no vocabulary except `TERMINAL_TYPES`.**

The `bookkeeping_types` parameter is **removed** from `guarded_provider_stream`, and
`NON_PAYLOAD_TYPES` / `_BOOKKEEPING_TYPES` are **deleted**. There is nothing left to keep in sync, so
the class of defect cannot recur by adding a dialect.

### Normative rules

> **R1.** An event SHALL contribute to `got_meaningful` if and only if it carries payload. This SHALL
> hold for terminal and non-terminal events alike, and SHALL NOT depend on the event's `type` string.
>
> **R2.** `TERMINAL_TYPES` SHALL remain the sole terminal authority (ADR-0039 R7). Terminal detection
> SHALL run before the meaningfulness decision so that `saw_terminal` is recorded regardless of payload.
>
> **R3.** The recovery classifier SHALL own **no** list of non-terminal event types.
> `bookkeeping_types`, `NON_PAYLOAD_TYPES` and `_BOOKKEEPING_TYPES` SHALL NOT be reintroduced under any
> name.
>
> **R4.** `_terminal_has_payload` is renamed `_event_has_payload`: it is no longer terminal-specific,
> and its name SHALL NOT imply otherwise.
>
> **R5.** An event that carries no payload SHALL NOT rescue an attempt that produced no response.
> Adding an unrecognised event type to a provider's output SHALL NOT change whether an empty attempt is
> retried.
>
> **R6.** Payload keys SHALL be the existing set (`text`, `content`, `final_content`, `tool_calls`,
> `calls`). `final_content` SHALL be retained: a canonical **dict** passes through `canonical_event`
> unchanged, so a dict-shaped provider MAY supply it, and dropping the key would make such a provider's
> payload invisible — turning a real response into a silent empty attempt.
>
> **R7.** Non-terminal events SHALL continue to be forwarded to the consumer whether or not they carry
> payload. The consumer's view of the stream is unchanged by this decision.

### Behaviour change

Measured, the decision is **identical for every event type in use** and differs in exactly one case —
the one that is the defect:

| stream | before | after |
|---|---|---|
| bare terminal | 3 calls, error | 3 calls, error |
| `checkpoint`/`usage`/`stream_stats` only | 3 calls, error | 3 calls, error |
| `checkpoint` + bare terminal | 3 calls, error | 3 calls, error |
| unknown payload-less + terminal | **1 call, no error (blessed)** | **3 calls, error** |
| unknown with payload + terminal | 1 call, no error | 1 call, no error |
| content + bare terminal | 1 call, no error | 1 call, no error |

### Rejected alternatives

1. **Add the missing type to the list** (the F43-shaped fix). Rejected: the list can never enumerate
   every dialect, and the audit found the hole precisely by using a name the list did not contain.
2. **Keep the list and add an "unknown ⇒ not meaningful" rule.** Rejected: it keeps two mechanisms
   (a list *and* a payload rule) where one suffices, and "unknown" is not a property the classifier
   can determine — every type is unknown until it is listed.
3. **Keep `bookkeeping_types` as an accepted-but-ignored parameter.** Rejected: a dead parameter is a
   hook for the next reader to reintroduce the list, which is the failure mode this ADR exists to
   close.
4. **Treat every non-terminal event as meaningful** (drop the payload requirement). Rejected: a
   `stream_stats`-only stream — the HTTP-200-with-zero-deltas throttle signature — would be recorded as
   a response.

### Risks

- **A provider whose payload uses a key outside the set** would have its response treated as empty and
  the attempt retried. Mitigated by R6 keeping `final_content`, and by the fact that the retry is
  bounded and surfaces an explicit error rather than a silent success — the failure is visible, not
  fabricated.
- **The signature change** touches five test call sites that passed the tuple positionally.

### Rollback / reversal

Restore the parameter and the set. No persisted state, no configuration.

### Follow-up questions

1. Should the payload key set be a named constant shared with `canonical_event`'s whitelist, so a
   provider adding a payload key cannot have it ignored in one place and honoured in the other? It is
   currently duplicated in intent (the whitelist and the payload keys overlap but are not the same
   list, and should not be: a whitelist *projects*, a payload check *detects*).

---

## ADR-0044 — One turn-level predicate: `terminal_outcome` is the authority and `turn_succeeded` derives from it

**Status:** ACCEPTED
**Phase:** POST-M13 (final execution-semantics closure)
**Date:** 2026-09-25
**Predecessors:** `PHASE_POST-M13_FINAL_EXECUTION_SEMANTICS_CLOSURE.md`; ADR-0035 (the arbiter),
ADR-0042 (the completion chain).

### Context

The closure audit searched for facts with more than one independent implementation and found one at the
turn level. `turn_succeeded` and `terminal_outcome` are the **same predicate**, computed twice, in the
same function, and then both handed to the arbiter:

```text
runtime.py:938   turn_succeeded  = saw_done and not saw_fatal_error
runtime.py:1157  _goal_outcome   = terminal_outcome_from_evidence(saw_done, saw_fatal_error)
                                  -> SUCCEEDED iff saw_done and not saw_fatal_error
goal.py:144/148  derive_goal_state reads `outcome`
goal.py:150      derive_goal_state reads `turn_succeeded`
```

`goal.py` documents the *intent* — *"the same two facts the turn-level rule uses, so the two levels can
never disagree about what happened"* — but nothing enforces it. Two implementations of one predicate
drift; and because both are inputs to the same arbitration, a drift would produce a **contradictory
goal state** (row 3 keyed on `outcome`, row 6 keyed on `turn_succeeded`).

The audit also found a cross-layer **name collision**: `RecoveryLadder.terminal_outcome` returns
`"ESCALATED_TO_HUMAN"` / `"IN_PROGRESS"` — a *recovery* state whose escalated value is the uppercase
form of the `GoalState` member `escalated_to_human`.

### Problem

Which component owns "the turn's terminal evidence", and how is it related to `turn_succeeded`?

### Decision

> **`terminal_outcome` (derived by `terminal_outcome_from_evidence`) is the single authority for the
> turn's terminal evidence. `turn_succeeded` is a DERIVED PROJECTION of it — computed from it, never
> computed independently.**

The predicate is evaluated **once per turn** and both the turn-level flag and the goal record read that
one value.

### Normative rules

> **R1.** `terminal_outcome_from_evidence(saw_done, saw_fatal_error)` SHALL be the only implementation
> of the turn's terminal-evidence predicate.
>
> **R2.** `turn_succeeded` SHALL be derived from that outcome (`== SUCCEEDED`) and SHALL NOT be
> computed by a second predicate, in the runtime or anywhere else.
>
> **R3.** The value SHALL be computed **once per turn** on the turn's normal path and reused for both
> the turn-level flag and the goal record. A second call on the same path is a duplication even when it
> agrees. The one permitted second call site is the **abort path** — the turn body raised before its
> evidence was final, so the once-per-turn computation never ran and the always-executing `finally`
> must still produce a value. That is the same function applied on the path where it did not run, never
> a second predicate.
>
> **R4.** `derive_goal_state` SHALL continue to accept both `terminal_outcome` and `turn_succeeded`, and
> the caller SHALL pass values derived from the same single computation, so the two can never disagree.
>
> **R5.** `terminal_outcome` at the **turn** level means: *what the turn's terminal evidence said*
> (`succeeded` | `failed` | `incomplete`). It SHALL NOT be read as, or promoted into, a goal verdict.
> The goal verdict is `goal_state` (ADR-0035 / ADR-0042).
>
> **R6.** The recovery ladder's own state SHALL NOT be named `terminal_outcome`. It SHALL be named
> `ladder_state`, so a recovery state cannot be mistaken for the turn-level outcome or for a
> `GoalState` — the collision is exactly the cross-layer collapse ADR-0042 prohibits.

### The turn-level authority, stated

| Fact | Authority | Consumers |
|---|---|---|
| the turn's terminal evidence | `goal.terminal_outcome_from_evidence` | the goal record's `terminal_outcome`; `turn_succeeded` |
| the turn-level success flag | **derived** from the above | the recovery ladder, `derive_goal_state` row 6 |
| the goal verdict | `derive_goal_state` | the goal record's `goal_state` |
| the ladder's state | `RecoveryLadder.ladder_state` | recovery tests/telemetry only |

### Rejected alternatives

1. **Leave both and document the intent.** Rejected: the docstring already claims the guarantee, and
   the audit exists precisely because a claim without enforcement is how the F43/F44 class recurs.
2. **Remove `turn_succeeded` and have consumers read `terminal_outcome == SUCCEEDED`.** Rejected: it
   churns the recovery ladder, the goal arbiter and their tests for a rename, and `turn_succeeded` is a
   legible name for a legible fact. Deriving it is the smaller, safer fix.
3. **Remove `terminal_outcome` and derive it from `turn_succeeded`.** Rejected: it inverts the
   dependency — `terminal_outcome` carries three values, `turn_succeeded` only two, so the outcome is
   strictly more informative and must be the source.
4. **Rename the goal record's `terminal_outcome`.** Rejected: it is a durable, replay-read field; the
   audit's job is to remove ambiguity, not to rewrite persisted contracts. Its meaning is now stated
   (R5), and the ladder's collision is renamed instead (R6).

### Risks

- **`terminal_outcome = "succeeded"` for a turn whose goal failed** (a failed verification) is accurate
  at turn level but close to the collapse ADR-0042 forbids. R5 states the boundary; the phase's tests
  assert `turn_succeeded` and `goal_state` separately.
- **The ladder rename touches six test assertions.**

### Rollback / reversal

Restore the second predicate and the ladder's property name. No persisted state changes: the goal
record's field names and values are untouched.

### Follow-up questions

1. Should `turn_succeeded` become a read-only property on a turn-result object rather than a local
   variable threaded through the persist path? That is a structural refactor of `run_turn`, not a
   semantic question, and is out of scope here.

---

## ADR-0045 — Convergence is an objective-level loop that consumes the turn-level authorities and re-implements none of them

**Status:** ACCEPTED (NEXT mission). Adds the only mechanism the turn-level closure left
missing; changes no existing contract.

### Context

ADR-0042 fixed the *turn*'s completion chain: provider terminal → stream state →
`turn_succeeded` → acceptance verdict → goal state → recovery. ADR-0044 collapsed its
duplicated predicate. ADR-0043 removed the last vocabulary list from a semantic decision.
The execution semantics were declared CLOSED.

Closed, but **per-turn**. A survey of the live code found the objective level empty:

| Component | State before this ADR |
|---|---|
| `run_turn` callers (SDK, CLI, REPL, headless, WebSocket, ACP, server route, TUI, benchmark) | **every one dispatches exactly one turn and returns** |
| `core/acceptance.py` | pure and total, but nothing constructs criteria from a user objective; the only producer is `verification.floor_guard_criteria`, i.e. the *actor's own* bookkeeping |
| `core/goal.py` | derives a state from facts it is handed; nothing supplies objective-level facts |
| `core/recovery.py::RecoveryLadder` | complete (10 classes, 7 rungs, legality tables, budgets, R5) and called from exactly one place — to **record** a decision, behind a flag defaulting OFF |
| `planner.py::PlanStore` | persists plans; `ContextAssembler`'s `PlanState` is constructed by **no production caller**, and `_build_system_prompt` never passes `plan=` — the plan is write-only |
| `core/task_graph.py` | journals a per-turn graph; `runtime.py` states plainly *"RECORDED, not enforced"* |
| `graph/executor.py` | a real executor, reachable only from the interactive REPL via `coding.handle_prompt`, not from headless/benchmark/server |
| `stagnation` (M13) | observes and records; the gate is default OFF and, per ADR-0037, cannot change the goal state |

So a turn that failed simply ended. The agent's convergence rested entirely on one turn's
internal iteration budget (`max_iterations`, default 50). Measured on the deterministic
benchmark against the only free-tier provider model available, three consecutive tasks
produced **FAIL (35 tool calls), FAIL (50 tool calls), TIMEOUT (300 s)** — with no retry,
no strategy change, and no objective-level record.

### Decision

**Add one mechanism — the loop — and let it consume every existing authority.**

`core/convergence.py::ConvergenceController` drives attempts until the objective is
*proven by evidence the harness produced*, or honestly is not. `wisp/autonomous.py` wires
it to the runtime; `wisp converge` exposes it. The rules:

**R1 — Acceptance criteria are host-derived, never model-declared.**
`derive_acceptance()` reads two sources, both machine-checkable: the workspace's own
declared verification commands (`environment.collect_environment`), and an explicitly
named definition in an explicitly named file, matched by a **closed, conservative
grammar**. An objective it cannot parse confidently yields **no criterion**. A model-
declared criterion would let the judged write the exam, which is the false-success shape
ADR-0037/0042 exist to prevent.

**R2 — Evidence is produced by the harness and names its producer.**
`CommandProbe` / the benchmark's `VerifyProbe` write `Evidence` with
`producer="convergence.command_probe"` (or `"benchmark.verify"`). The model has no
channel to write an evidence record. The model's prose is not an input to any decision in
this module.

**R3 — No new verification authority.** The controller calls
`acceptance.evaluate()`, `goal.derive_goal_state()`, `goal.terminal_outcome_from_evidence()`
and `recovery.classify_failure*()`. It re-derives none of them. `GOAL_MET` remains
reachable only through ADR-0035's row 6, which requires *both* a successful turn and an
acceptance `PASS`.

**R4 — A criterion that cannot be evaluated must not report `FAIL`.**
`command_succeeds`/`symbol_defined` follow `floor_guard_criteria`'s established
implication shape: an absent measurement returns `True` so that `evaluate`'s rule 3
turns it into `INCONCLUSIVE`. A measurement that *exists* and says the command did not
succeed (non-zero exit, timeout, unrunnable) **is** a failure. Collapsing "no evidence"
into "failing evidence" is the collapse `Verdict` exists to prevent.

**R5 — The next strategy is chosen by `RecoveryLadder`, not by this module.**
The controller observes; the ladder decides. `legal_rungs()` already excludes anything in
its history, so P6's **R5** ("a rung that would repeat an already-failed rung is illegal")
is enforced structurally rather than remembered. The rung vocabulary is `recovery.py`'s;
a second strategy vocabulary would be the duplicated-authority defect this repository
keeps removing. When the chosen rung is not executable in this configuration (ROLLBACK
without a snapshot) the ladder is asked again — the first decision is already in its
history, so the second cannot repeat it.

**R6 — A denial outranks the stagnation observation.** Precedence follows P6 exactly: an
engine-reported failure is classified *before* `repeated`, so a second denied call reaches
`SECURITY` (legal rungs: `{HUMAN}`) instead of being re-planned as stagnation. Reading it
the other way is how the no-retry rule leaks.

**R7 — Stagnation requires unmet criteria *and* a non-empty measurement.**
The controller's observation is "the same criteria are unmet and nothing measurable
changed". It does not fire when there are no criteria (an unexamined objective has not
been shown to stagnate) and it does not fire on an empty measurement (which is identical
to every other empty measurement). The *naming* is `classify_failure`'s; the class is
`STAGNATION` rather than `REPEATED` because STAGNATION's legal rungs
(`GLOBAL_REPLAN`, `DIAGNOSTIC`) are the strategy-changing ones and it forbids
`RETRY`/`REPAIR`.

**R8 — Each attempt gets a fresh session.** What crosses an attempt boundary is the
measured evidence — a few lines of facts — not the transcript. Carrying the transcript
forward grows without bound and feeds the model its own failed reasoning as if it were
established fact.

**R9 — Bounded, and exhaustion is a state.** Attempts are bounded by the objective's or
the controller's budget; the ladder's own budgets apply underneath. Exhaustion yields
`ESCALATED_TO_HUMAN` (via the ladder), `GOAL_STAGNATED` or `GOAL_UNVERIFIED` — never
`GOAL_MET`, and never a hang.

**R10 — Rollback is opt-in and refuses rather than truncating.** It is the one rung that
destroys work, so `Objective.allow_rollback` defaults `False`; `WorkspaceSnapshot` refuses
a workspace larger than its bounds instead of silently truncating, because a partial
snapshot makes a restore look successful while leaving files behind.

**R11 — Attempts are journaled append-only, and a resume re-runs nothing.**
A torn final line is ignored rather than failing the resume; a completed attempt is never
re-executed, which is what makes a restart after a crash safe.

**R12 — The turn predicate is not re-derived.** `wisp/autonomous.py::observe_turn` reads
the two facts ADR-0044's predicate uses and delegates to
`terminal_outcome_from_evidence`; `turn_succeeded` is then a projection of that outcome.
A second implementation of the rule is the defect ADR-0044 removed.

**R13 — The acceptance conditions are stated to the agent on every attempt.**
`AttemptRequest.criteria` carries the **required** criteria (advisory ones are recorded,
not demanded) and `compose_attempt_prompt` prints them. An agent cannot converge on a
target it has not been told: withholding the conditions until after a failure makes the
first attempt a guess at what "done" means. This hands over no authority — the conditions
are the harness's, and the agent still cannot write the evidence that satisfies them.

### Consequences

- `GOAL_MET` at the objective level now means "the harness measured the repository and
  every required criterion held", and is unclaimable by any amount of model prose.
- A capability that observes but does not act (`RecoveryLadder`, the stagnation detector,
  `acceptance.evaluate`) now has a caller that acts on it.
- The default `permission_mode` is `auto_edit`, in which `run_bash` is blocked. That does
  **not** weaken acceptance — the harness measures, not the agent — but it does mean the
  agent cannot run the project's tests itself in the default mode. Recorded as a
  limitation, not changed here.
- **F54, found by the loop's own benchmark and fixed here.** `_execute_tool`
  (`stateless.py:2043`) has a deliberate and *correct* safety fallback: with no
  `tool_executor` there is no approval, policy or audit, so it permits `READ` tools only
  and refuses everything else (`[Denied: <tool> requires a wired ToolExecutor …]`).
  `CompositionRoot` wires an executor; **`benchmark/runner.py::make_ollama_core_factory`
  did not** — and it is the one place that builds a core by hand. So `wisp bench` refused
  every `write_file`, `edit_file`, `run_bash`, `run_tests` and `spawn`, and had been
  reporting FAIL for tasks no agent could pass; `subagent-delegate` was unpassable by
  construction. The mutation trace is unambiguous: the model wrote a correct `shout()`
  implementation and the runtime refused it six times. The fix is one line plus two
  tripwires. With it wired, the same objective through `wisp converge` reaches `goal_met`
  in 48 s / 9 tool calls, against 130 s / 35 tool calls / no change before.
  **This is the architectural capability that was preventing convergence**, and it was
  not visible from any unit test — every test builds its core through `CompositionRoot`
  or injects a fixture executor, so the hand-built path was never exercised.
- **F52, found while running the benchmark and fixed here.** `benchmark/runner.py::
  _git_baseline` used `git rev-parse --git-dir`, which succeeds from any directory
  *inside* a repository — so a nested workspace skipped `git init` and then ran
  `git add -A` and `git commit` against the **enclosing** repository. Eleven
  `bench baseline` commits landed in this project before it was noticed; the branch was
  restored to `b8dc4ac` with `git reset --mixed` (working tree untouched, the user's
  pre-existing WIP verified intact). The check is now `--show-toplevel`. A benchmark
  harness that mutates the caller's repository is a mutation-safety defect, and the
  tripwire is `tests/test_bench_predictions.py::TestBaselineNeverTouchesTheEnclosingRepository`.

### Alternatives rejected

| Alternative | Why not |
|---|---|
| Put the loop inside `AgentRuntime.run_turn` | `run_turn` is the single-turn authority every transport depends on; giving it an objective lifetime makes one object own two. |
| Let the model declare its acceptance criteria | The judged writing the exam. Directly contradicts §23 of the mission and ADR-0037's spirit. |
| Reuse `graph/executor.py` as the driver | It executes an LLM-node DAG, not attempts against repository evidence, and it is not reachable from headless/benchmark/server. A separate decision would be needed to unify them; none is taken here. |
| Make the plan the driver | The plan is model-authored prose. Grounding completion in it would let the model's own words become the standard it is judged against. |
| Retry with a reworded prompt on failure | That is the "slightly different prompt" §12 forbids. The rung vocabulary forces a different *approach*, and R5 makes repetition structurally impossible. |

### Follow-up questions

1. Should `coding.handle_prompt`'s graph path and this controller converge on one
   execution model? That is a genuine architectural decision and is **not** taken here.
2. Should the `stagnation_gate` (ADR-0036/0037, default OFF) now be enabled, given that an
   objective-level loop exists that can act on stagnation? ADR-0016's measurement
   precondition is still `NOT_YET_DETERMINABLE`; unchanged.

---

## ADR-0046 — Objective-relative progress is a second input to the recovery decision, not a re-classification of the failure

**Status:** ACCEPTED (progress-aware recovery mission). Adds one table and one pure function;
changes no existing contract and no existing caller's behaviour.

### Context

ADR-0045 gave the objective level a loop. A live experiment then produced the case the
taxonomy cannot express:

```text
attempt 0 — 1800 s, 13 tests collected, 1 still failing
    CODE_TURN_TIMEOUT  →  FailureClass.ENVIRONMENT  →  LEGAL_RUNGS {DIAGNOSTIC, HUMAN}
                                                              │
                                                    "Do not edit any file"
```

`ENVIRONMENT`'s legal set is right for what it was written for — *"the model is too slow or
unreachable — not retrying"* (`recovery.py`). It is wrong for a turn that was cut off
**mid-implementation**, because that turn's work is real and its only sensible recovery is to
continue it. Both are the same failure: the host stopped the turn. The distinction is not
*what failed*; it is *whether the attempt moved the objective* — a fact the taxonomy does not
carry and must not be asked to.

Two designs were available: re-classify the timeout, or add a second input.

### Decision

**Add objective-relative progress as a second, orthogonal input to `RecoveryLadder.decide`, and
let a meaningful-progress observation WIDEN the class's legal rung set.**

**R1 — Progress is a separate fact from failure.** `FailureClass` continues to answer *what
failed*, and `CODE_TURN_TIMEOUT` continues to be `ENVIRONMENT`. Nothing re-classifies.

**R2 — Progress is host-owned and evidence-based.** `core/progress.py::evaluate_progress`
compares two `CommandProbe` measurements — the payloads `core/convergence.py` already takes —
and reports which of the objective's own numbers moved. It reads no model text. The verdict is
total: `NO_PROGRESS` / `MEANINGFUL_PROGRESS` / `PROGRESS_UNDETERMINABLE`.

**R3 — Activity is not progress.** `files_changed > 0`, `tool_calls > 0` and a model's claim
are *not* sufficient. File changes are recorded as `SUPPORTING` evidence that can never on its
own produce `MEANINGFUL_PROGRESS`. Only a movement in the objective's own measurement can: a
falling failure count, a rising pass count, a non-zero exit becoming zero, or a named symbol
becoming defined.

**R4 — A regression is not progress.** A measurement that moved backwards forces `NO_PROGRESS`
even alongside an authoritative improvement. Fixing one thing while breaking another is not
progress toward the objective, and there is no "negative progress" state to invent.

**R5 — A measurement whose declared inputs moved supplies no signal at all.** The
`inputs_digest` check runs first and disqualifies the whole criterion, yielding
`PROGRESS_UNDETERMINABLE`. This is ADR-0045 R12's tamper-evidence rule extended from
*convergence* to *progress*: a green suite reached by editing the contract must not be able to
look like progress either. **Demonstrated load-bearing by mutation probe** — with the check
disabled, the live tampering scenario is reported as `meaningful_progress`.

**R6 — Only `MEANINGFUL_PROGRESS` widens, and only where a class permits it.**
`PROGRESS_CONTINUATION_RUNGS` is total and empty for nine of the ten classes; `ENVIRONMENT`
gains exactly `REPAIR`. `SECURITY` cannot widen (a denial is a denial), and `REPEATED` and
`STAGNATION` cannot widen because both are *defined* as the absence of progress — widening them
would let this table contradict the classes that name it. `FORBIDDEN_RUNGS` is checked first, so
no widening can reintroduce a forbidden rung.

**R7 — `PROGRESS_UNDETERMINABLE` is conservative.** An unmeasurable attempt — no prior
measurement, or an untrustworthy one — recovers exactly as it did before this ADR. Treating
"cannot tell" as progress would make every unmeasurable objective continue forever.

**R8 — The continuation is a different strategy, not the same one re-worded.**
`directive_for(rung, progress=…)` selects a continuation wording that says the measured thing:
the objective moved, the work is real, continue from the current repository state, do not
restart, do not redo, do not edit the tests. The rung vocabulary is unchanged.

**R9 — The widening is bounded by everything that already bounded recovery.** R5's no-repeat
rule, `max_attempts` and the ladder's own budgets are untouched. A second progress-producing
`ENVIRONMENT` failure finds `REPAIR` already tried and falls to `DIAGNOSTIC`; a third escalates.

**R10 — Progress is durable and replayable, and a resume re-derives it from durable facts.**
`AttemptRecord` journals the verdict, the signals and the measurement's raw payloads, and the
pre-work baseline is journaled as the journal's first record (`kind: "baseline"`). A resumed run
prefers the **journaled** baseline, because re-measuring a workspace the interrupted run already
mutated would change both the derived criteria and every subsequent progress verdict. A legacy
journal with no baseline record still resumes.

**R11 — Progress can never override acceptance or the goal state.** The verdict still comes from
`acceptance.evaluate`; the state still comes from `goal.derive_goal_state`; `GOAL_MET` still
needs both a successful turn and a `PASS`. Progress changes *which rung may be tried*, and
nothing else.

### Alternatives rejected

- **Re-classify a progressing timeout as `IMPLEMENTATION`.** It would be a lie about what
  failed, and it would let a timeout reach `GLOBAL_REPLAN`/`ROLLBACK`, which a turn that was
  merely cut off has not earned.
- **A new `FailureClass`.** The taxonomy is closed at ten and its classes answer "what failed".
  The new fact is orthogonal to that question, so a class would force every consumer to learn a
  distinction that is not about failure.
- **A `CONTINUE` rung.** `REPAIR` already means "repair that specific failure rather than
  re-doing the whole task", which is exactly continuing a partially-completed objective. A new
  member of a 7-rung `IntEnum` would renumber a cost-ordered ladder that several invariants
  compare.
- **Reading only the failure count.** `environment._detect_verification_commands` hardcodes
  `python -m pytest tests/ -x -q`, and `-x` stops at the first failure, so on a red suite the
  failure count is pinned at 1 and the pass count is the only thing that moves. Reading only
  failures would report "no progress" for a project that had just implemented half its
  functions.
- **Widening `TRANSIENT`/`TOOL`/`VERIFICATION` too.** They already admit `REPAIR` without
  progress, so an entry would change nothing; the table stays empty for them so that "this class
  gains something from progress" is a fact a reader can see.

### Consequences

- The missing capability — *continue work that was cut off* — is reachable, through the same
  ladder, with the same legality rules and the same bounds.
- `RecoveryLadder.decide` and `legal_rungs` gained a keyword-only `progress` parameter
  defaulting to `None`; every existing caller's candidate set is provably identical (pinned by
  test).
- The failure taxonomy, the goal-state precedence, the acceptance verdict vocabulary and the
  turn predicate are all untouched.
- **A limit this ADR does not remove, recorded rather than hidden.** ADR-0035 row 3 makes a
  fatal terminal error `GOAL_FAILED`, and row 6 requires `turn_succeeded` for `GOAL_MET`. So a
  continuation attempt that satisfies every criterion but is *itself* cut off before emitting
  `done` is reported `GOAL_FAILED` even though the harness measures the objective as met. That
  is a false **negative**; it is not caused by progress awareness; and removing it would mean
  changing ADR-0035's precedence, which needs its own evidence and its own decision. Recorded in
  `PHASE_PROGRESS_AWARE_RECOVERY.md` §14.

---

## ADR-0047 — A failed turn is not a failed objective, and R5's unit is the strategy, not the rung

**Status:** ACCEPTED (multi-turn productive recovery mission). Amends ADR-0035's precedence rows
3–6 and ADR-0046's use of R5. Changes no other contract; every caller that does not pass
`progress` is unaffected.

### Context

Two questions were left open by ADR-0045 and ADR-0046, and the live experiments that closed
those decisions produced the evidence for both:

**F60.** `derive_goal_state`'s row 3 read *"P3 FAIL **or fatal terminal error**"*. So a turn that
timed out outranked an independent acceptance `PASS`, and a repository that satisfied **every**
objective criterion was reported `GOAL_FAILED`. Measured live: five runs reached `exit 0`,
recorded verdict `pass` on every attempt, and terminated `goal_failed` — and each spent a whole
extra attempt re-attempting an objective that was already met. A false **negative**, the mirror
of F37.

**F61.** `RecoveryLadder`'s R5 forbade *a rung* from repeating. That unit was right while the
only thing a rung could carry was a failure. ADR-0046 gave rungs a second possible payload — a
**success that has not finished** — and then the rule became actively harmful. Measured live:
`positive10`'s attempt 1 completed **14 of the 17 outstanding files** and moved the objective
from 1 to 44 passing checks, *more work than the first attempt*; it needed one more
continuation, and R5 refused it. The ladder fell to `DIAGNOSTIC`, whose directive is *"Do not
edit any file in this attempt"* — so the run was **guaranteed** to fail while every attempt had
made measurable progress.

### Decision

**F60 — the objective's evidence decides where it is decisive; the execution outcome decides
only where it is not.**

**R1 — The fatal-terminal-error clause is qualified, not removed.** Row 4 is now *"fatal
terminal error, **and no P3 PASS**"*. A fatal error with no `PASS` is still `GOAL_FAILED`, and
still outranks stagnation. Only one input combination moved: *fatal error + `PASS`*,
`GOAL_FAILED` → `GOAL_MET`.

**R2 — `turn_succeeded` is not an arbitration input.** It is still recorded on every attempt,
and `terminal_outcome` is still the turn-level authority (ADR-0044). It was never *sufficient*
for `GOAL_MET` (row 6 still needs a `PASS`); F60 establishes that it is not *necessary* either.

**R3 — Why a conjunction and not a reordering.** Three preserved rules must hold at once, and
they are not totally orderable: a fatal error must outrank stagnation (a *heuristic* must not
soften a *fact*); stagnation must outrank `PASS` (the completion gate withholds goal-met); and
`PASS` must outrank a fatal error (R1). As priorities that is `fatal > stagnation > PASS >
fatal` — a cycle, so **no ordering of rows can express it**. The cycle is broken where it is
semantically broken: the fatal clause is the only one whose meaning depends on the verdict,
because it is the only one that is a statement about the *attempt* rather than about the
*objective*.

**R4 — An authorization event is terminal for the run.** A denial or cancellation is excluded
from completion *before* the verdict is consulted, and escalates through the ladder's own
`SECURITY` row. Absorbing a denial into `GOAL_MET` would launder a security event; this
preserves the existing security ordering rather than adding a rule.

**R5 — The criteria are now the sole gate, and that is a consequence to be managed.** Before
this ADR a timeout accidentally masked weak criteria. `criteria_for`'s *guards-only* case (a red
baseline, no promotion, no symbol criterion) is a legitimate **no-regression objective** — "do
not make it worse" — and `GOAL_MET` on it is honest. But an objective that plainly requires a
green suite must state that, so `_WANTS_FIX_RE` was widened to cover three phrasings (a repair
verb, "X passes", "the suite passes") instead of one. The criteria must be as strong as the
objective.

**F61 — the unit of non-repetition is the strategy against materially unchanged state.**

**R6 — `MEANINGFUL_PROGRESS` is the witness that the state changed.** It is computed by
`core/progress.py` from the objective's own measurement, so "the state materially changed" is
host-owned, deterministic, and never the model's word. A rung may be re-chosen only when it is
true; otherwise R5 is exactly what it was.

**R7 — A dedicated budget bounds it.** `RecoveryBudget.productive_continuations` (default 2)
counts re-choices across the whole objective, separately from the rung's own budget. This is the
invariant: **productive recovery can continue, and productive recovery still terminates.** A
rung with no budget entry of its own (`REPAIR`) is bounded by this and nothing else, which is
why the two bounds are charged separately rather than one standing in for the other.

**R8 — A regression, a tampered input, and an unmeasurable attempt cannot unlock it.** A
regression forces `NO_PROGRESS`; a moved verification-input digest forces
`PROGRESS_UNDETERMINABLE`; `None` and an unknown string fail closed. All three therefore leave
R5 untouched.

**R9 — `SECURITY`, `REPEATED` and `STAGNATION` can never be widened or repeated.** The
continuation table stays empty for them and `FORBIDDEN_RUNGS` is still checked first, so no
progress observation can reach a forbidden rung.

**R10 — No new strategy-identity mechanism.** The journal already carries the strategy
fingerprint — rung, directive, evidence lines, measurement digest, session, files changed — and
R6 is what makes "the same strategy" decidable. Adding a second identity would be a second
authority for a question the progress verdict already answers.

**R11 — The bound is visible and durable.** `BudgetGovernor.snapshot()` reports it, and a
resumed run replays the decisions that spent it (the ladder's history is rebuilt by
`_resume_recovery`), so a restart cannot hand back a budget the interrupted run had used.

**R12 — Failure history is not erased by progress.** The journal is append-only; every attempt
is recorded with its own outcome, failure class and progress verdict, and a continuation adds a
record rather than rewriting one.

**R13 — One authority, unchanged.** `derive_goal_state` is still the only answer to "what is
the objective state", and it still takes no model input. The run-level aggregation is stated
once: *a run's state is the ladder's escalation if it surrendered, otherwise the last attempt's
derived state.*

### Alternatives rejected

- **Design A — preserve strict R5.** Rejected: it is *measured* to guarantee failure for an
  objective that needs two continuations, even when every attempt is productive.
- **Design B — allow productive continuation with no new budget.** Rejected: "productive" would
  then be the only bound, and a task the agent can advance one unit at a time would never
  terminate. §5's invariant is explicit.
- **Design E — a phase ladder (`INITIAL → RECOVERY → CONTINUATION → …`).** Rejected: the
  existing rung vocabulary already names the strategy (`REPAIR` *is* "continue the work"), and a
  phase axis would be a second vocabulary for the same question. The distinction that matters is
  not *which phase* but *whether the state changed*, and R6 answers that directly.
- **A new `GoalState` for "objective met, turn incomplete".** Rejected: ADR-0035 fixes six
  states, the information is already durable on the attempt (`turn_succeeded`,
  `terminal_outcome`, `failure_code`), and `GoalState` should answer one question. Adding a
  seventh state would make every consumer learn a distinction that belongs to the record.
- **Re-classifying a progressing timeout.** Already rejected in ADR-0046 and still rejected: a
  timeout is an `ENVIRONMENT` failure either way.

### Consequences

- The live trajectory the previous mission could not reach — *work genuinely left over, and a
  continuation that finishes it* — becomes reachable, and the mechanism that reaches it is the
  one already in place.
- The objective state is now a function of the objective's evidence, which makes the acceptance
  criteria load-bearing in a way they were not before. R5 is the mitigation, and the residual is
  recorded in `PHASE_MULTI_TURN_PRODUCTIVE_RECOVERY.md` §18.
- **F63, found while writing the F61 tests and fixed here.** `Measurement.digest` hashed the
  whole payload, including `output_tail` — which ends with the command's *elapsed time*. Two
  probes of the same unchanged workspace digested differently, so
  `repeated = digest == stagnation_witness` was a coin flip: a stagnant run could classify as
  `IMPLEMENTATION` and take `REPAIR` instead of `GLOBAL_REPLAN`, and ADR-0046 R10's replay
  determinism did not hold. The witness is now a digest over the state-bearing fields only
  (`WITNESS_FIELDS`), and the same projection is used for evidence identity. The prose excerpt is
  still recorded — it is evidence; it is simply not an identifier.
- **F62, found by the same tests and fixed here.** `_resume_recovery` indexed `attempts[-1]` on
  an empty list, so `resume=True` with a missing or empty journal raised `IndexError` —
  `wisp converge --resume` on a fresh run crashed instead of starting. A resume with no durable
  history is a fresh run.

---

## ADR-0048 — The acceptance criteria are host-derived from the objective's *stated* conditions; silence is not consent, and an undetermined requirement is `INCONCLUSIVE`

**Status:** ACCEPTED (criteria-authority mission). Amends ADR-0045 R1's *source* list by naming what
the host may infer from silence; authorises one additive record and one flag-gated behaviour. Changes
nothing when the flag is off. Supersedes no ADR.

### Context

ADR-0047 R5 made the acceptance criteria the **sole gate** on `GOAL_MET`: before it, a turn timeout
accidentally masked weak criteria, and after it a `PASS` completes the run. R5's mitigation was to widen
`_WANTS_FIX_RE` from one phrasing to three, so that *"an objective that plainly requires a green suite
must state that"*.

That mitigation was measured in this phase, and it does not close the class. The derivation has three
failure modes, and **all three reproduce on the real `derive_acceptance`** — see the phase report's
`three_modes.py` output, reproduced in §Behaviour change below.

**What the authority question actually is.** `derive_acceptance` reads two sources: the workspace's
declared verification commands (`environment.collect_environment`) and a closed grammar over the
objective text. The first answers *"what can be checked here?"*; the second is the host's attempt to
answer *"what does this objective require?"* — and the second is a **judgement about meaning**, made by
three regexes with no negation awareness, no confidence signal, and no record of what it concluded.

So the single most load-bearing input to the completion authority is a regex's opinion, and that opinion
is currently **unobservable**: nothing records whether the absolute criterion was promoted or left
advisory, or why.

### Problem

Three shapes, each measured end to end through `derive_acceptance` → `criteria_for` →
`acceptance.evaluate` → `goal.derive_goal_state`:

**MODE A — a false `GOAL_MET`.** The repository's **own benchmark task** `FIX_BUG`:

> *"totals.py defines `sum_to(n)` which should sum integers 1..n inclusive, but it is off by one:
> `sum_to(5)` returns 10 instead of 15. **Fix the bug** in totals.py."*

`_WANTS_FIX_RE` returns **no match** — there is no suite word within 40 characters of the repair verb.
On a red baseline the absolute criterion is therefore **advisory** and only the two guards are required:

```
advisory  verify:cmd0                      `python -m pytest tests/ -x -q` exits 0
REQUIRED  verify:cmd0:no_regression        ... reports no more failures than the baseline (1)
REQUIRED  verify:cmd0:inputs_unchanged     ... its inputs are unchanged
```

An attempt that changes **nothing** satisfies both guards. Measured verdict: **`pass`** →
**`goal_met`**, with the bug unfixed and the suite still failing. This is the F37 shape (a false
success) arriving through the criteria rather than through the evidence adapter, and ADR-0047 R5 named
the risk without closing it.

**MODE B — a false exhaustion.** The regex has **no negation awareness**:

> *"**Do not** make the tests pass by editing them; instead add a missing type annotation to
> models.py."*

`_WANTS_FIX_RE` matches `'make the tests'`. The absolute criterion is promoted to **required** on a red
baseline, the objective never asked for a green suite, and the run ends `fail` / `goal_failed`. The
prohibition was read as a requirement. (Other measured false positives: *"Make the linter pass"*,
*"CI will pass without any test changes"*.)

**MODE C — "I cannot tell".** An objective on a workspace that declares no verification commands yields
**no criteria at all**:

```
criteria -> (none)
verdict  -> inconclusive   reason=['NO_REQUIRED_CRITERIA']
goal     -> goal_unverified
```

This branch is **already honest** — `acceptance.evaluate` rule 1 returns `INCONCLUSIVE` for "no required
criteria", and `derive_goal_state` maps it to `GOAL_UNVERIFIED`, never to `MET`. Its cost is that
`GOAL_MET` is **unreachable**, so the objective can never complete. That is a consequence to be managed,
not a defect.

**The distinction the code cannot currently make.** MODE A and MODE C produce *opposite* outcomes from
the *same* input state — the host could not determine what the objective requires. MODE A treats that as
"nothing is required, so a no-op passes"; MODE C treats it as "nothing is required, so nothing is
verified". **Both cannot be right.** The corpus already decided which: ADR-0035 invariant 1, ADR-0042 and
ADR-0045 R4 all say that absence of evidence is not evidence, and `INCONCLUSIVE` must not be collapsed
into either a pass or a failure. MODE C is the established behaviour; MODE A is the collapse.

### Decision

> **The objective is the authority over what is required. The host owns the *derivation* and the
> *validation*, never the *invention*. Where the objective is silent, the host may infer "no
> regression" and may not infer "green". Where the host cannot tell whether the objective is silent or
> simply unparsed, it must say so, and the answer is `INCONCLUSIVE`.**

**R1 — Three derivation outcomes, not two.** Each command spec is classified:

| Outcome | Condition | Consequence |
|---|---|---|
| `STATED` | `_WANTS_FIX_RE` matches the objective | the absolute criterion is promoted to required (today's `promote_absolute=True`) |
| `UNSTATED` | no match, **and** the objective yields at least one other machine-checkable requirement (a `SymbolSpec`) | guards-only is correct: the objective asked for something checkable, and a green suite is not part of it. ADR-0047 R5's no-regression objective stands, and `GOAL_MET` on it means **"nothing got worse"** |
| `UNDETERMINED` | no match, **and** nothing else machine-checkable is named | the host has **no basis**; it may neither promote nor silently degrade |

**R2 — `UNDETERMINED` is `INCONCLUSIVE`, and it never promotes.** An `UNDETERMINED` spec whose
absolute criterion ended up **advisory** contributes a **required criterion the harness cannot
evidence** — `verify:cmdN:requirement_declared` — whose description states the question the host
declined to answer. `acceptance.evaluate`'s existing **rule 3** ("a required criterion with no valid
evidence → `INCONCLUSIVE`") turns it into `INCONCLUSIVE` with that criterion named in `unmet_criteria`.
**No new verdict vocabulary, no new rule, and no `FAIL`**: the absence of a determination is not a
determination of failure. This is the mechanism the corpus already uses for exactly this shape.

The **advisory** qualifier is the rule, not an optimisation. On a green baseline `criteria_for` requires
the absolute criterion anyway, so the derivation decided nothing and there is nothing for strict mode to
withhold. `UNDETERMINED` **and** `advisory` together are the entire blast radius, and that is exactly the
case where a no-op would otherwise pass — which is what keeps the flag from breaking objectives it has no
business touching.

**R3 — The derivation is recorded, and durably.** `explain_acceptance()` returns a `CriteriaDerivation`
carrying, per command spec, the outcome and the **matched span** (or the empty string), and the
convergence loop journals it **once, beside the baseline**, as a `{"kind": "derivation"}` line. A reader —
or a replay — can therefore answer *"did the host think this objective required a green suite, and on what
words?"* without re-running a regex. **A promotion that cannot cite the objective's own words is an
inference the record shows to be unfounded.** The record is read by nothing on the decision path; it is a
record, and that is the point — the answer stops being invisible.

**R4 — `derive_acceptance`'s signature is not widened** (ADR-0009). It becomes a thin caller of
`explain_acceptance(..., strict=False)` and keeps returning `(criteria, specs)`. `criteria_for` is
**untouched**, so its ~40 call sites are unaffected. The record is reached by calling the new function.

**R5 — Strict derivation is behind a flag, and the flag defaults to today.** `WISP_CRITERIA_STRICT_DERIVATION`
(default **OFF**). When off, `STATED` and `UNSTATED` behave exactly as today and `UNDETERMINED` is
recorded but not acted on. When on, `UNDETERMINED` yields the R2 criterion. The flag is read at the
composition point (`wisp/autonomous.py`), not inside the pure function.

**R6 — A structured-criteria path is viable, and it is not the model writing the exam.** ADR-0045 R1
forbids *model-declared* criteria, and that stands. The distinction that makes a structured path legal:

| | Who writes the criterion | Who validates | Who measures |
|---|---|---|---|
| **forbidden** — model-declared | the **model** | nobody | the harness |
| **permitted** — objective-declared | the **user's objective** | the **host**, against the measurable surface | the harness |

A declaration carried by the objective, **validated by the host** against the workspace's verification
commands and the symbol grammar, and **failing closed** on anything the harness cannot measure, keeps the
exam with the user and the grading with the host. The model gains **no** channel: it may not add, weaken,
reinterpret, or satisfy a declaration, and R2's criterion is unevidenceable by construction.

**The trade, named.** A declaration is a **new input surface** and therefore a new way to be wrong — a
malformed or unmeasurable declaration must be **rejected loudly**, never silently downgraded, because a
silent downgrade *is* MODE A. It also moves work onto the caller: an objective that states its acceptance
conditions is more verbose than one that does not. The benefit is the property MODE A lacks — a wrong
answer becomes **visible** (the host rejected the declaration) instead of **silent** (a no-op passed).
This ADR **decides that the path is viable and fixes its boundary**; it does **not** implement it, and
the follow-up question below records that.

**R7 — Negation is not handled, and the record must not pretend otherwise.** `_WANTS_FIX_RE` reads
*"Do not make the tests pass"* as a requirement (MODE B). R3's matched span makes this **visible** — a
reader sees the promotion cited `'make the tests'` inside a prohibition — but R1–R6 do not fix it.
Natural-language negation is not a closed grammar, and widening the regex again would be the same
mitigation R5 already tried and this ADR measures as insufficient. **The residual is stated, not
hidden.**

### Behaviour change

Measured on the real chain (`three_modes.py`, and the 55 tests in
`tests/reliability/test_criteria_derivation_authority.py`), **flag OFF** (today) and **flag ON**:

| Mode | Objective | Flag OFF | Flag ON |
|---|---|---|---|
| A | `FIX_BUG` — *"Fix the bug in totals.py"*, red baseline, nothing changed | `pass` → **`goal_met`** | `inconclusive` → **`goal_unverified`** |
| B | *"Do not make the tests pass…"* | `fail` → `goal_failed` | `fail` → `goal_failed` **(unchanged — R7)** |
| C | an objective with no criteria and no toolchain | `inconclusive` → `goal_unverified` | **identical** |

The only behaviour that moves is **MODE A**, and it moves from a false success to an honest
`INCONCLUSIVE`. Measured blast radius, by R2's advisory qualifier:

| Objective | Baseline | `verify:cmd0` | Strict withholds? |
|---|---|---|---|
| `STATED` | red | required (promoted) | no — the objective said so |
| `STATED` | green | required | no |
| `UNSTATED` | red | advisory + `symbol:*` required | no — the objective asked for something checkable |
| `UNDETERMINED` | **red** | **advisory** | **yes — this is MODE A** |
| `UNDETERMINED` | green | required | no — the derivation decided nothing |
| `UNDETERMINED` | no baseline | required | no |

With the flag off, **nothing** moves — the new record is journalled and read by nothing.

### Alternatives rejected

- **Widen `_WANTS_FIX_RE` again** (the ADR-0047 R5 shape). Rejected: **measured insufficient.** `FIX_BUG`
  carries no suite word to match, and MODE B shows a fourth phrasing would also match a *prohibition*.
  A regex over prose is the wrong instrument for a question about meaning, and every widening trades a
  false negative for a false positive.
- **Let the model declare the criteria.** Rejected: ADR-0045 R1 — the judged writing the exam. R6's
  objective-declared path is the legal alternative and is a different thing.
- **Make `UNDETERMINED` a `FAIL`.** Rejected: it asserts a failure the evidence does not support, which is
  the error `acceptance.evaluate` exists to refuse (rule 3's comment, ADR-0035 invariant 1). A timeout was
  once read as a failure for exactly this reason (F60).
- **Make `UNDETERMINED` promote conservatively.** Rejected: it *is* MODE B — inventing a requirement the
  user did not state, and failing an objective that never asked for it.
- **Record the derivation as prose in the attempt prompt only.** Rejected: prose cannot be asserted on,
  counted, or replayed — F21's finding, and the same distinction as F7's `controlling_layer`.
- **Widen `derive_acceptance` to return the record.** Rejected: ADR-0009 forbids widening a pinned
  internal signature to carry a new concern, and `derive_acceptance` has three callers plus a test surface.

### Risks

- **The strict mode makes some objectives uncompletable.** `UNDETERMINED` is terminal until the objective
  is clarified. This is deliberate and is MODE C's existing behaviour generalised — but it *is* a real
  cost, and it is why the flag defaults off.
- **The classification is still a heuristic.** `UNSTATED` is inferred from the presence of a `SymbolSpec`,
  which is a proxy for "the objective stated something checkable". An objective that names a symbol *and*
  requires a green suite without saying so lands in `UNSTATED` and keeps MODE A's shape.
- **`UNDETERMINED` can be reached by a well-formed objective** whose wording the grammar does not know.
  The record makes it visible; it does not make it right.

### Rollback / reversal

Revert the flag to OFF: `WISP_CRITERIA_STRICT_DERIVATION` defaults to `false`, so the behavioural half is
inert by construction. The record (`explain_acceptance`, `CriteriaDerivation`, `DerivationReason`) is
**additive and read by nothing on the decision path**, so it may remain with no effect.

**This ADR is reversed if** a measurement shows that a `UNDETERMINED` classification is reached for an
objective whose requirement *was* determinable from its own words — i.e. if the classifier's
false-`UNDETERMINED` rate makes more objectives uncompletable than MODE A's false-`GOAL_MET` rate makes
them wrongly complete. That trade is not currently measured, and **is the residual this ADR leaves**:
the classifier's error rates on real objectives are unknown, and MODE A remains reachable with the flag
off.

### Follow-up questions

1. **Implement the objective-declared structured path (R6).** The contract is named here; the grammar,
   the validation surface, and the rejection behaviour are not. That is a separate decision, and it is
   the one that would make a *wrong* answer visible rather than silent.
2. **Measure the classifier's error rates.** The reversal condition above needs numbers this ADR does
   not have. The three-mode probe is the instrument; it needs a corpus of real objectives.
3. **Should `UNDETERMINED` be reachable at all for an objective that names a file?** `FIX_BUG` names
   `totals.py` and no symbol, so it is `UNDETERMINED` under R1. A third outcome keyed on "the objective
   names a path and a defect" is conceivable and is **not** taken here.
4. **Does `GOAL_MET` on a guards-only set need to be *reported* differently?** ADR-0047 R5 calls it an
   honest no-regression objective. It is honest about what it checked and silent about what it did not;
   whether the operator should be told which one they got is an interface question.

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
| 0029 | The graph is a shape, not a payload; the transcript projects from the journal | M9 | ACCEPTED (supersedes the M9 statement) |
| 0030 | A subagent's identity travels with the call, not the executor | M15 | ACCEPTED |
| 0031 | Prompt sections are classified; untrusted content is not in instruction position | M14 | ACCEPTED |
| 0032 | The failure path reaches the taxonomy through an adapter; engine refusals are denials | M12 | ACCEPTED |
| 0033 | A node references its work unit; the payload ratchet classifies fields, not names | M11 | ACCEPTED (replaces ADR-0029's blacklist mechanism) |
| 0034 | The progress signal is built from the work; an empty observation is not evidence | M13 | ACCEPTED (amends ADR-0029's M13 re-scope) |
| 0035 | Completion and recovery are two authorities; completion is evaluated first, and stagnation vetoes goal-met | POST-M13 | ACCEPTED |
| 0036 | Stagnation may withhold `done` for a bounded replan; it never vetoes the turn | POST-M13 | ACCEPTED (amends ADR-0035 by reconciling its two clauses; adds the predicate to the goal record) |
| 0037 | The stagnation latch is monotonic; the predicate closes on the trap, and `min_consecutive` gates the verdict | POST-M13 | ACCEPTED (completes ADR-0036: widens its §6 scope to all live-path stagnation and corrects its truth table) |
| 0038 | The configured output budget is sent verbatim; a provider's refusal of it is a configuration incompatibility, not a capability to be guessed | POST-M13 (F39) | ACCEPTED (assigns the boundary's owner; authorises no behavioural change) |
| 0039 | Providers may emit typed or dict events; the core owns one total canonicalization boundary, and it is separate from the stall guard | POST-M13 (F40) | ACCEPTED (names one canonicalization owner; forbids raw-event interpretation by consumers) |
| 0040 | The canonicalization authority is `events.canonical_event`; `WispAgentCore._normalize_event` is a delegation facade | POST-M13 (F40) | ACCEPTED (amends ADR-0039 R2's subject; ratifies the ownership the implementation established, and supersedes R2's "`AgentEvent`/dict inputs only" clause) |
| 0041 | Recovery classification is semantic, and terminal detection precedes payload classification | POST-M13 (F43) | ACCEPTED (eliminates the last re-spelling of terminal vocabulary; amends no earlier decision) |
| 0042 | The completion chain has five distinct authorities; exhaustion is neither goal success nor goal failure | POST-M13 (F44) | ACCEPTED (defines the relation ADR-0035 left implicit; changes no behaviour) |
| 0043 | Meaningfulness is payload-based for every provider event; the classifier owns no vocabulary but the terminal authority | POST-M13 (closure) | ACCEPTED (supersedes ADR-0041 R3/R7 — removes the last vocabulary list from a semantic decision) |
| 0044 | `terminal_outcome` is the turn-level authority and `turn_succeeded` derives from it; the recovery ladder's state is renamed | POST-M13 (closure) | ACCEPTED (removes the second implementation of the turn predicate and a cross-layer name collision) |
| 0045 | Convergence is an objective-level loop that consumes the turn-level authorities and re-implements none of them | NEXT | ACCEPTED (adds the loop the turn-level closure left missing; changes no existing contract) |
| 0046 | Objective-relative progress is a second input to the recovery decision, not a re-classification of the failure | NEXT (progress-aware recovery) | ACCEPTED (widens one class's legal rungs on measurable progress; amends no earlier decision, and changes no caller that does not pass `progress`) |
| 0047 | A failed turn is not a failed objective, and R5's unit is the strategy, not the rung | NEXT (multi-turn productive recovery) | ACCEPTED (amends ADR-0035 rows 3–6 — one input combination moves; refines ADR-0046's use of R5 under a new dedicated budget) |
| 0048 | The acceptance criteria are host-derived from the objective's *stated* conditions; silence is not consent, and an undetermined requirement is `INCONCLUSIVE` | NEXT (criteria authority) | ACCEPTED (names what the host may infer from silence; authorises one additive record and one flag-gated behaviour defaulting to today; declares the objective-declared structured path viable without violating ADR-0045 R1) |
