# PHASE POST-M13 — F40 PROVIDER EVENT CONTRACT & NORMALIZATION AUTHORITY ARCHITECTURE DECISION

**Status:** RATIFIED · **Production changes:** 0 · **Test changes:** 0 · **Default changes:** 0
**Date:** 2026-09-25 · **HEAD:** `7c15626` (main) · **Phase:** PM-20 · **ADR:** **0039**

---

## 1. Mission Result

```text
RATIFIED  →  ADR-0039
```

> **Providers MAY emit either a canonical dict or a typed stream event. The core owns exactly one
> canonicalization boundary for provider objects, that boundary is total and whitelist-based, and it
> must be obtainable *without* stall recovery. No core consumer may interpret a raw provider event.
> The terminal vocabulary has one authority.**

The decision is **not** "flatten the providers" and **not** "normalize at every consumer". It ratifies
the boundary the codebase already has in one place, makes it singular and total, and **separates it
from the stall guard** — which is the specific structural reason F40 exists.

---

## 2. F40 Facts

Established by the recon and **re-verified for this decision**. Only facts; no interpretation.

| # | Fact | Evidence |
|---|---|---|
| 1 | `protocol.py:36` declares `Generator[dict[str, Any], None, None]`; the docstring says *"standardized event dictionaries"* | `protocol.py:36-45` |
| 2 | `OllamaClient` yields 5 typed dataclasses; `MockProvider` yields 3; `OpenAIProvider` yields 11 dict literals; the Ollama fallback path and `_NullProvider` yield dicts | AST scan |
| 3 | **The typed Ollama path emits no `done` at all** — its only terminal is `StreamComplete(phase="complete")` | AST scan of `generate_stream_events` dict literals: `NONE` |
| 4 | The guard **consumes** terminal markers (`break` before `yield event`) and **retries** an attempt that produced nothing usable | `provider_stream.py:197-208`, `:285` |
| 5 | `_normalize_event` drops fields; **every dropped field has zero consumers** outside the producers | field-by-field measurement + consumer grep |
| 6 | **Two canonicalizers exist** with divergent whitelists (16 vs 14) and different return shapes | §6 below |
| 7 | Replay is unaffected — typed events never cross persistence | `runtime.py:790-798` |
| 8 | `_normalize_event` is total (no raising path for a provider event) | `stateless.py:2241-2286` |

### 2.1 New finding discovered during this decision — **F42**

`wisp.core.events.normalize_event` claims to accept *"Provider objects with type/phase + attributes"*
but its whitelist omits `calls`. Measured:

```text
ToolCallBatch(phase='tool_calls', calls=[{...}])
  -> events.normalize_event(...).to_dict()
  -> {'type': 'tool_calls', 'data': {}, 'timestamp': 0.0, 'schema_version': 1}
                                        ^^^^^^^^^^ the tool calls VANISH
```

`StreamComplete.done_reason` is dropped the same way. Both call sites (`runtime.py:793` else-branch,
`headless.py:48`) receive engine output, which is always a flat dict — so the divergence is **latent
today**, and **severe if reached**. Recorded as **F42**, separate from F40. It is the empirical proof
that the "consumer-local normalization" alternative (Option D) drifts.

---

## 3. Contract Decision — Q1–Q10

| Q | Answer |
|---|---|
| **Q1** Authoritative representation contract | A provider MAY emit **either** a canonical dict **or** a typed event. The contract is the **shape**, not the type: `type` (or `phase`) + whitelist-readable fields. (R1) |
| **Q2** One representation, or both allowed? | **Both allowed at the provider edge.** One representation is mandatory at the **consumer** edge. |
| **Q3** Where does typed→canonical belong? | At a **core** boundary, invoked once per event, before any consumer sees it. (R2, R4) |
| **Q4** Provider responsibility or consumer-boundary responsibility? | **Consumer-boundary.** A provider must not convert (that would create N authorities and discard its internal metadata). |
| **Q5** Must every consumer consume canonical events only? | **Yes** — an architectural invariant, not a convention. (R4) |
| **Q6** Canonical terminal vocabulary | `{done, complete}` as **synonyms**; `stream_complete` retained as an accepted legacy alias with no producer; `checkpoint`/`usage`/`stream_stats` are bookkeeping, **not** terminal. (R8, R9) |
| **Q7** Terminal vocabulary owner | **One**: `provider_stream.TERMINAL_TYPES`. Consumers read it, they do not re-spell it. (R7) |
| **Q8** How do the consumers interact with the canonical representation? | All obtain events from the canonical boundary. The guard additionally consumes terminal markers and adds recovery; the wrap-up, compaction and planner do not. (R5, R6) |
| **Q9** Stay coupled to `_guarded_provider_stream`? | **No.** Canonicalization must be independently obtainable. (R5) |
| **Q10** Smallest architecture closing F40-1…F40-4 with no new authority | One total canonicalizer (R2, R3) + one normalization-only boundary (R5) + one terminal vocabulary (R7) + the consumer invariant (R4). **No provider rewrite, no schema expansion, no new taxonomy, no persistence change.** |

---

## 4. Provider Representation

**Allowed:** a canonical `dict[str, Any]`, **or** a typed stream event carrying `type`/`phase` plus
whitelist-readable attributes.

**Not allowed:** a provider deciding terminal semantics, completion, or canonical shape; a provider
being *required* to convert.

**Why the typed form is legitimate** — the recon's measurement, re-confirmed:

| Typed event | Canonical `type` | Preserved | Dropped |
|---|---|---|---|
| `TokenBatch` | `content` | `text` | `batch_index`, `phase` |
| `ToolCallBatch` | `tool_calls` | `calls` | `phase` |
| `Checkpoint` | `checkpoint` | — | all 5 |
| `StreamComplete` | `complete` | `done_reason` | `final_content`, `final_thinking`, `tool_calls`, `total_tokens`, `validation_hash` |
| `StreamError` | `error` | `message` | `error_type`, `partial_content`, `partial_thinking` |

**Every dropped field has zero consumers.** So the typed form carries *internal* richness the canonical
form deliberately does not need — and Option A would have to reconstruct the typed form internally to
keep its own machinery working, for no consumer benefit.

---

## 5. Canonical Event Contract

```text
canonical event
  type        REQUIRED   always present ("unknown" if the provider shape is unrecognised)
  payload     OPTIONAL   drawn from the existing 16-field whitelist:
                         text, name, arguments, result, message, duration_ms, turns,
                         session_id, summary, reason, level, recoverable, tool_call_id,
                         id, calls, done_reason
  unknown     DROPPED    not namespaced, not preserved, not forbidden-by-error
```

The schema is **not expanded** by this decision. Fact 5 is the justification: nothing consumed is lost,
so a wider schema would add surface with no reader.

---

## 6. Normalization Authority

**One owner: `WispAgentCore._normalize_event`.**

| | `WispAgentCore._normalize_event` | `wisp.core.events.normalize_event` |
|---|---|---|
| Returns | flat dict | `AgentEvent` (payload in `.data`) |
| Whitelist | **16** | **14** |
| Extra fields | `calls`, `done_reason` | — |
| Provider-object handling | **the authority** | **must be subordinated** (R2) |
| Call sites | main loop, the guard (injected) | `runtime.py:793`, `headless.py:48` |

`events.normalize_event` remains a canonicalizer of `AgentEvent`/dict inputs; it **must not** be a
second canonicalizer of provider objects. That is the F42 fix, and it is structural rather than a
whitelist patch.

---

## 7. Terminal Vocabulary

| Spelling | Meaning | Producer | Status |
|---|---|---|---|
| `done` | provider declared the stream finished | `openai.py`, Ollama fallback, protocol bridge | canonical (synonym) |
| `complete` | provider declared the stream finished | normalized `StreamComplete.phase` — the **only** terminal on the typed Ollama path | canonical (synonym) |
| `stream_complete` | — | **none anywhere** | legacy alias, accepted, not canonical |
| `checkpoint` | stream-integrity marker | normalized typed `Checkpoint` | bookkeeping, **not terminal** |
| `usage` | token accounting | no stream producer | bookkeeping, **not terminal** |
| `stream_stats` | per-stream diagnostics | `openai.py` | bookkeeping, **not terminal** |

**`done` and `complete` are synonyms** — both mean *the provider declared the stream finished*. They are
not different lifecycle concepts; they are two spellings produced by different providers, and fact 3
shows the typed Ollama path produces **only** `complete`.

The two questions this ADR must make answerable:

```text
"the provider successfully completed the response"  ->  type ∈ {done, complete}
"the stream itself ended"                           ->  NOT a provider event.
                                                        The guard's `saw_terminal` bookkeeping;
                                                        the no-marker case is already an explicit
                                                        truncation error. SHALL NOT be merged.
```

---

## 8. Consumer Rule

> **No core consumer may interpret raw provider events.** (R4)

Architectural, not conventional — a convention is what the codebase already had, and it failed in
**three** places. Enforcement is **structural** (R5): the canonical boundary is a callable a consumer
must obtain events *from*, so bypassing it requires reaching past a boundary rather than forgetting a
step.

| Consumer | Today | After |
|---|---|---|
| main loop | compliant (via the guard) | compliant |
| **wrap-up** | **raw — F40-1/F40-2** | canonical |
| **compaction** | **raw — F40-3** | canonical |
| **planner** | **raw — F40-4** | canonical |
| runtime persistence | canonical dicts | unchanged |
| transport | canonical dicts | unchanged |
| future consumers | unspecified | must obtain from the boundary |

---

## 9. Wrap-Up Semantics

The wrap-up needs **normalization without retry and without terminal-marker consumption**:

- it must **see** the terminal marker to set `wrapped_up` — and the guard *consumes* markers
  (`provider_stream.py:197-208`);
- it must **not** re-issue an empty final round — and the guard *retries* one up to `max_attempts`.

So routing the wrap-up through `_guarded_provider_stream` is wrong on **both** counts, and this is the
measured reason the wrap-up calls `_stream_events_async` directly. **R5/R6 exist precisely to permit
`normalize` without `retry`.** The resolution is a normalization-only boundary, not a change to the
guard's recovery.

---

## 10. Compaction / Planner

One rule closes both; neither needs its own decision.

| Site | Current behaviour | After |
|---|---|---|
| `compaction.py:141-151` | typed → `AttributeError` → `"[ERROR: …]"` → `_llm_summarize` returns `None` → **silent fallback to truncation** | canonical events → LLM compaction works |
| `planner.py:120-123` | `isinstance(c, dict)` **filters typed events out** → empty text → `PlanError(INVALID_PLANNER_OUTPUT)` | canonical events → planner works |

Both are **latent**: compaction requires `compaction_model` (defaults to `""`); the planner's stream
branch requires a provider without `generate` (`OpenAIProvider` has none, but yields dicts).

---

## 11. Replay

**UNCHANGED.** Re-verified: typed events never cross persistence (`runtime.py:790-798` persists
canonical dicts; zero matches for the typed classes in `runtime.py`/`session.py`/`session_repo.py`/
`infra/store.py`).

```text
live provider event  ->  canonical dict  ->  persistence  ->  replay      (unchanged)
```

**No persistence migration. No replay migration.** This was the preferred outcome and it is available
without compromise, because canonicalization already produces the persisted shape.

---

## 12. Compatibility

| Provider | Output today | Change required |
|---|---|---|
| `OpenAIProvider` | dicts | **none** |
| `OllamaClient` / `OllamaProvider` | typed | **none** (consumed canonically) |
| Ollama fallback path | dicts | none |
| `MockProvider` | typed | none |
| `_NullProvider` | dict | none |
| protocol default bridge | passes through | none |
| future providers | unspecified | MAY emit either (R1) |

**No provider is rewritten. No test that pins typed output is invalidated. No OpenAI-only assumption
is introduced.**

---

## 13. Security

Canonicalization is a **whitelist projection**: it can only *remove* keys. It cannot execute
provider-controlled code, invoke a tool, mutate authorization/approval/security policy, bypass
`ToolExecutor`, or promote untrusted tool arguments into authority — tool arguments remain data that
the existing gates validate. **Normalization, terminal classification, the provider contract and
completion authority all remain host-owned**; no model or provider input influences them.

---

## 14. Decision Matrix

Tradeoffs only. **No scores, no ranking.**

| Dimension | A: dict providers | B: typed-or-dict + adapter | C: structural normalized stream | D: consumer-local |
|---|---|---|---|---|
| Contract clarity | one shape, easy to state | two shapes at the edge, one at the consumer | two shapes at the edge, one enforced at a boundary | two shapes everywhere |
| Existing compatibility | **breaks** typed providers + their tests | preserves all providers | preserves all providers | preserves providers |
| Typed metadata preservation | provider must reconstruct internally | kept where it is used (producer) | same as B | same as B |
| F40-1 closure | yes | yes | **yes, structurally** | yes |
| F40-2 closure | yes | **only if the terminal rule is also fixed** | yes | **only if each consumer fixes it** |
| F40-3 closure | yes | yes | yes | yes, separately |
| F40-4 closure | yes | yes | yes | yes, separately |
| Authority clarity | N canonicalizers (one per provider) | one canonicalizer, invocation optional | **one canonicalizer, invocation structural** | N canonicalizers (the F42 situation) |
| Bypass resistance | n/a | **weak** — a convention | **strong** — a boundary | none |
| Replay impact | none | none | none | none |
| Provider impact | 2 providers + their tests | none | none | none |
| Consumer impact | none | 3 consumers | 3 consumers + the guard | 3 consumers |
| Performance | conversion per provider | one whitelist pass per event | one pass per event, plus a generator frame | one pass per event |
| Reversibility | low (rewrites) | high | high | high |
| Complexity | medium (N implementations) | low | low–medium (one extra boundary) | low per site, high in aggregate |
| Future-provider safety | must implement conversion | may bypass by accident | **cannot bypass by accident** | will drift (F42) |

**Compatible with the existing architecture:** B and C. **Requires a new abstraction:** C (a
normalization-only boundary). **Requires rewriting existing code:** A. **Empirically shown to drift:**
D.

---

## 15. Rejected Alternatives

1. **Option A — providers MUST emit dicts.** Moves the canonicalizer into every provider, creating *N*
   authorities instead of one; rewrites two providers and invalidates the tests pinning their typed
   output; and buys nothing, because fact 5 shows the canonical projection is already the only thing
   consumed.
2. **Option D — consumer-local normalization.** Rejected **empirically**: `events.normalize_event` *is*
   this alternative already realised, and it has drifted — it silently drops `calls` (**F42**). N sites
   means N whitelists and N chances to drift.
3. **Route the wrap-up through `_guarded_provider_stream`.** Rejected on two measured grounds: the guard
   retries an empty attempt, and it consumes the terminal marker before yielding — which would make
   `wrapped_up` unreachable. This is the trap the decision exists to avoid.
4. **Patch only the wrap-up (the F37 pattern).** Rejected: leaves F40-2 live (proven — a normalized
   `StreamComplete` still fails a `{done}`-only check), leaves F40-3/F40-4 open, leaves the
   normalization/stall bundle in place, and leaves two canonicalizers drifting.
5. **Expand the canonical schema to carry the typed metadata.** Rejected: no consumer reads any dropped
   field, so this adds surface with no reader.

---

## 16. Implementation Boundary

**Mechanical**

1. A **normalization-only** stream boundary (normalize; forward, **including** terminal markers; no
   retry).
2. `_guarded_provider_stream` obtains its events from it and keeps its recovery unchanged.
3. The wrap-up, `compaction.py` and `planner.py` obtain events from it.
4. Each consumer reads the terminal predicate from `TERMINAL_TYPES` instead of re-spelling it.
5. `events.normalize_event`'s provider-object branch subordinated per R2 (**F42**).
6. `protocol.py`'s declaration widened to admit both forms (R1).

**Behavioural / architectural**

The wrap-up **delivers the summary it already produces** instead of discarding it, and stops emitting a
spurious `Max iterations reached`. That is the intended effect of closing a defect, not a new behaviour.

**No flag. No default change. No configuration. No new error type. No schema change.**

---

## 17. Migration

**NONE.**

| Kind | Required? |
|---|---|
| persistence migration | **no** |
| replay migration | **no** |
| journal rewrite | **no** |
| provider migration | **no** |
| configuration migration | **no** |
| default change | **no** |

---

## 18. Rollback

**Fully reversible and cheap.** The change adds a boundary and moves three consumers onto it. Reverting
restores the previous call sites. Nothing in the decision is observable in a journal, a persisted
record, or a configuration file — so there is no state to unwind.

---

## 19. ADR Number

```text
resolved before writing:
  highest existing ADR     0038
  sections before append   38
  index rows before append 38
  collision check           ADR-0039 / ADR-0040 -> no match anywhere
  number used              ADR-0039
  appended append-only     before "## Decision index"
  sections after append    39
  index rows after append  39
  max heading after append ## ADR-0039
  CONTEXT.md range         ADR-0001 … ADR-0039  (2 occurrences)
```

**No existing ADR was modified.**

---

## 20. Final Status

```text
=== STATUS: RATIFIED ===

F40:
CONFIRMED_PRODUCTION_DEFECT

ARCHITECTURE:
Providers MAY emit typed or dict events; the core owns ONE total, whitelist-based
canonicalization boundary, obtainable WITHOUT stall recovery; no consumer may interpret
raw provider events; one terminal vocabulary ({done, complete} synonyms).

NORMALIZATION_OWNER:
WispAgentCore._normalize_event   (R2 — one owner; events.normalize_event subordinated)

  [SUPERSEDED 2026-09-25 by ADR-0040 (PM-22): the ratified authority is
   `wisp.core.events.canonical_event`; `WispAgentCore._normalize_event` is a
   delegation facade. This block is the record of what ADR-0039 decided at the
   time and is left as written — see
   PHASE_POST-M13_F40_CANONICALIZATION_OWNERSHIP_RECONCILIATION.md.]

TERMINAL_AUTHORITY:
provider_stream.TERMINAL_TYPES   (R7 — consumers read it, never re-spell it)

REPLAY:
UNCHANGED  (no persistence migration, no replay migration)

PRODUCTION_CHANGES:
0

TEST_CHANGES:
0

DEFAULT_CHANGES:
0

NEW_FINDING:
F42 — events.normalize_event silently drops a ToolCallBatch's payload (latent)

NEXT:
IMPLEMENT ADR-0039 — the normalization-only boundary, the three consumers, the single
terminal predicate, and the F42 subordination. Do NOT copy the F37 pattern.
```

---

## 21. Evidence

| Artefact | Contents |
|---|---|
| `WISP_ARCHITECTURE_DECISIONS.md` | **ADR-0039**, appended append-only + index row |
| `PHASE_POST-M13_F40_ITERATION_WRAPUP_TYPED_EVENT_FORENSIC_RECON.md` | the recon this decision rests on |
| this report | the decision record, the option matrix, and the implementation boundary |

Measurements performed this phase (all read-only, no file modified): the field-by-field
normalization-loss table; the two whitelists; the empirical `ToolCallBatch → data: {}`; the producer
inventory per terminal spelling; the guard's marker-consumption and retry semantics; the terminal
spellings on each Ollama path.
