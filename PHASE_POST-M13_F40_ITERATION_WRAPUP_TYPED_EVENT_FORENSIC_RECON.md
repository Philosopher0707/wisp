# PHASE POST-M13 — F40 ITERATION WRAP-UP TYPED-EVENT FORENSIC RECON

**Status:** FORENSIC RECON COMPLETE · **Production changes:** 0 · **Test changes:** 0
**Date:** 2026-09-25 · **HEAD:** `7c15626` (main) · **Phase:** PM-19

---

## 1. Mission Result

```text
F40 = F40_CONFIRMED_PRODUCTION_DEFECT
```

Reproduced twice: **against the real Ollama daemon** (live provider, real turn) and
**deterministically** with a shipped production provider (`MockProvider`). Both produce the
same defect at the same line.

The defect is **two-part**, and the first part **masks** the second:

| | Mechanism | Consequence |
|---|---|---|
| **F40-1** | The wrap-up loop calls `.get()` on **raw provider events**, which are typed dataclasses on the Ollama path | `AttributeError` on the *first* event → the loop aborts → the summary is **entirely lost** |
| **F40-2** | Even with the representation fixed, the wrap-up recognises only the terminal spelling `"done"`, while a typed `StreamComplete` normalises to `"complete"` | `wrapped_up` stays `False` → a **spurious** `Max iterations reached` error |

Because F40-1 aborts the loop before the terminal event is ever reached, F40-2 has **never been
observable**. A repair that only fixes the representation would leave F40-2 live — proven in §11.3.

A **co-existing, separately-numbered contract gap** was also found and is reported but not merged
into the classification: `wisp/providers/protocol.py` **declares** that providers yield
`Generator[dict[str, Any]]` and *"standardized event dictionaries"*, while `OllamaClient` and
`MockProvider` yield typed dataclasses. See §12.4 and §16.

**Two additional instances of the same consumer assumption** were found (§7): `core/compaction.py`
(latent — requires `compaction_model`) and `graph/planner.py` (latent — requires a provider without
`generate`). Neither is a separate root cause; both are the same missing adapter.

---

## 2. Repository Provenance

```text
HEAD              7c15626  "docs(m11): record the M11 phase; findings F29-F31; ..."
branch            main
commits this phase 0
staged            0
modified tracked  44
untracked         70
```

The tree carries substantial **pre-existing WIP** (unrelated to this phase). Nothing was staged,
committed, stashed, reset, checked out or cleaned. The tree was read only.

### 2.1 Phase work already present

| Artefact | Present? | Evidence |
|---|---|---|
| **F37** evidence-adapter repair | yes, uncommitted | `stateless.py` differs from HEAD in `_refusal_result_event`, `_tool_result_output`, `_turn_inner`, `turn` |
| **F39** diagnostic | yes, uncommitted | `ollama_client.py`, `providers/openai.py`, `tests/test_ollama_configuration_incompatibility.py` |
| **F40** production or test work | **NO** | see §2.2 |

### 2.2 F40 is present in HEAD — it is not a regression

The wrap-up consumer region is **byte-identical to HEAD**:

```text
wrap-up consumer at HEAD line 947, now line 1081
region HEAD 945..959 == region now 1079..1093  ->  True
```

And the F37 diff's hunks (HEAD lines 85, 113, 204, 213, 303, 317, 334, 367, 790, 853, 858, 2311)
**do not overlap** that region; `git diff -- wisp/core/stateless.py` contains **zero** occurrences of
`etype = ev.get` / `Iteration wrap-up` / `wrapped_up`.

> **OBSERVED FACT.** F40 is a long-standing defect that ships in `HEAD`. No recent phase introduced
> it, and no phase has touched it.

---

## 3. F40 Question

The suspected defect was stated as *"the iteration wrap-up loop assumes provider events are dicts."*
That is a hypothesis. The forensic question is:

> **What assumption does the iteration wrap-up loop make about the type, shape, provenance or
> lifecycle of events, and is that assumption guaranteed by the production event pipeline?**

| Q | Answer | Evidence |
|---|---|---|
| Q1 What does it consume? | The provider event stream of a **final tool-less round** | `stateless.py:1080` |
| Q2 What type does it expect? | `dict` — it calls `.get()` | `stateless.py:1081-1083` |
| Q3 What fields? | `type`, `text`, `content` | `stateless.py:1081-1086` |
| Q4 Who produces it? | The configured provider, **un-normalised** | `_stream_events_async` yields `event` verbatim (`stateless.py:1130-1135`) |
| Q5 Where transformed? | **Nowhere on this path.** The main loop normalises; the wrap-up does not | `stateless.py:472` vs `:1081` |
| Q6 Does production preserve the type? | **No.** Ollama yields typed dataclasses | real daemon, §11.1 |
| Q7 Do tests use the same path? | **No.** The feature's contract test uses dicts | §10 |
| Q8 Where does the shape diverge? | At the provider → consumer boundary; only `_guarded_provider_stream` adapts | §5 |
| Q9 Who owns interpretation? | `WispAgentCore._normalize_event` — but its **invocation** is bundled inside the stall guard | §13 |
| Q10 What should the contract be? | §14 (reconstructed) — a decision, §16 |

**The assumption is not guaranteed.** It is guaranteed *only* for consumers that route through
`_guarded_provider_stream`.

---

## 4. Production Trace

```text
OllamaClient.generate_stream_events          wisp/ollama_client.py:433
  ├─ EventBatcher.add_content(...)  →  TokenBatch(phase="content", text, batch_index)
  ├─                              →  ToolCallBatch(phase="tool_calls", calls)
  ├─ batcher.checkpoint(...)      →  Checkpoint(...)
  ├─                              →  StreamComplete(phase="complete", final_content, ...)
  └─                              →  StreamError(phase="error", ...)
        │  (frozen dataclasses — wisp/stream_events.py:11-77)
        ▼
OllamaProvider.generate_stream_events_async  wisp/providers/ollama.py:103
  └─ returns the client's generator verbatim (a `return`, not a `yield`)
        │
        ▼
WispAgentCore._stream_events_async           wisp/core/stateless.py:1100
  └─ `yield event`  — NO NORMALISATION          (stateless.py:1130-1135)
        │
        ├───────────────►  MAIN LOOP            stateless.py:466
        │                  _guarded_provider_stream(open, self._normalize_event, ...)
        │                    └─ guarded_provider_stream() calls normalize_event per event
        │                  then `normalized = self._normalize_event(event)` again (:472)
        │                  ✔ works — dict from here on
        │
        └───────────────►  WRAP-UP LOOP         stateless.py:1080   ◄── F40
                           `ev.get("type", "")`   (:1081)
                           → AttributeError on the FIRST typed event
                           → caught by `except Exception` (:1089)
                           → `logger.exception("Iteration wrap-up call failed")`
                           → `wrapped_up` stays False
                           → `error_event("Max iterations reached")` (:1092)
```

**The asymmetry is one call.** The main loop reaches the provider through
`_guarded_provider_stream`, which injects `self._normalize_event` (`stateless.py:2233`). The wrap-up
loop calls `_stream_events_async` **directly** (`stateless.py:1080`) and therefore never meets the
normaliser.

---

## 5. Type Provenance

### 5.1 Source-level (deterministic, AST scan of every `yield` in `generate_stream_events`)

| Provider | What it yields | Shape |
|---|---|---|
| `OpenAIProvider` | `dict` literal ×11 | **dict** |
| `OllamaProvider` | delegates (`return self._client.generate_stream_events(...)`) | inherits the client's |
| `OllamaClient` | `TokenBatch`, `ToolCallBatch`, `Checkpoint`, `StreamComplete`, `StreamError` | **typed** |
| `MockProvider` | `TokenBatch`, `ToolCallBatch`, `StreamComplete` | **typed** |
| `Provider` (protocol default bridge) | passes the queue payload through | delegates |

### 5.2 Boundary table

| Boundary | Declared type | Production (Ollama) | Production (OpenAI) | Test (contract test) | Conversion |
|---|---|---|---|---|---|
| Producer | `dict[str, Any]` | `TokenBatch` etc. | `dict` | `dict` | none |
| Queue | `dict` | typed object | `dict` | `dict` | none |
| `_stream_events_async` | `dict` | typed object | `dict` | `dict` | **none** |
| `_guarded_provider_stream` | `dict` | **`dict`** | `dict` | `dict` | `_normalize_event` ✔ |
| Main loop | `dict` | `dict` | `dict` | `dict` | ✔ |
| **Wrap-up** | `dict` (assumed) | **typed object** | `dict` | `dict` | **none** ✘ |
| Persistence / replay | `dict` | n/a — never persisted | `dict` | `dict` | `normalize_event(...).to_dict()` |

> **OBSERVED FACT.** The consumer does **not** receive the same semantic object in production and in
> the tests. The test double is a dict; the Ollama provider is a dataclass.

### 5.3 `_normalize_event` — the adapter that exists

`stateless.py:2241-2286` handles **both** shapes explicitly: `isinstance(event, dict)` → copy;
otherwise read `.type` / `.phase` and whitelist `safe_fields` (`text`, `name`, `arguments`, `result`,
`message`, …). Its existence is evidence that **Wisp already knows providers may yield typed
objects** — the adapter is the designated boundary. The wrap-up simply does not use it.

---

## 6. Producer Inventory

| Producer | Kind | Representation | Reached by |
|---|---|---|---|
| `OllamaClient.generate_stream_events` | PRODUCTION | **typed** | `OllamaProvider` |
| `OllamaProvider._generate_stream_events_direct` | PRODUCTION (fallback) | `dict` | no-client path |
| `OpenAIProvider.generate_stream_events` | PRODUCTION | `dict` | OpenAI |
| `MockProvider.generate_stream_events` | PRODUCTION (test provider) | **typed** | tests, dev |
| `_NullProvider` (`composition.py:512`) | PRODUCTION | `dict` (`{"type":"error"}`) | unconfigured |
| `Provider.generate_stream_events_async` | PRODUCTION (default bridge) | passes through | providers without native async |
| `wisp/stream_parser.py` | PRODUCTION (helper) | constructs **typed** | `ollama_client.py:33` only |
| test doubles (`_DictProvider`, scripted cores) | FIXTURE | `dict` | tests |

> **OBSERVED FACT.** Two production providers emit the same logical event in **two different
> representations**. Nothing converts at the provider boundary; the only conversion is inside
> `_guarded_provider_stream`.

**No journal-replay producer exists for these events** (§8).

---

## 7. Consumer Inventory

| # | Consumer | Access pattern | Safe? |
|---|---|---|---|
| 1 | Main loop `_turn_inner` | `normalized.get(...)` — **after** the guard | ✔ |
| 2 | **Wrap-up loop** `stateless.py:1081-1086` | `ev.get("type")` on the **raw** stream | ✘ **F40** |
| 3 | `core/compaction.py:146-149` | `event.get("type")` on the **raw** stream | ✘ latent |
| 4 | `graph/planner.py:120-123` | `if isinstance(c, dict)` — silently **drops** typed events | ✘ latent |
| 5 | `runtime.py` persist loop | `event.get(...)` — the engine already yields flat dicts | ✔ |
| 6 | `transport/*` (renderer, CLI) | `dict` only | ✔ |

### 7.1 Consumer 3 — `core/compaction.py` (latent, config-gated)

```python
for event in provider.generate_stream_events(...):   # :141  raw stream
    if event.get("type") == "content":               # :146  ← AttributeError on typed
        summary_parts.append(event.get("text", ""))
    elif event.get("type") == "error":
        raise RuntimeError(event.get("message", "Compaction failed"))
except Exception as e:
    summary_parts.append(f"[ERROR: {e}]")            # :151  the AttributeError becomes the "summary"
```

Measured: `Compactor._llm_summarize(...) -> None` — the guard at `compaction.py:161`
(`summary.startswith("[ERROR")`) catches it and returns `None`, so the caller **silently falls back
to truncation**. Impact is a **capability loss, not corruption**: on the Ollama path, LLM compaction
never works and always degrades to truncation.

**Latent:** `compaction_model` defaults to `""` (`config.py:849-850`), and
`compaction.py:100` requires it to be truthy. So this path is unreachable until a user sets
`compaction_model`.

### 7.2 Consumer 4 — `graph/planner.py` (latent, provider-gated)

```python
chunks = list(provider.generate_stream_events(PLANNER_SYSTEM, messages))
text = "".join(c.get("text", c.get("content", ""))
               for c in chunks if isinstance(c, dict))     # :121-123
```

The `isinstance(c, dict)` guard **filters typed events out**, so `text == ""` — a **silent-empty**,
not a crash. `len(text) == 0` then fails `extract_json_from_markdown` →
`PlanError("INVALID_PLANNER_OUTPUT")`.

**Latent:** the branch is taken only when `hasattr(provider, "generate")` is False (`:113`).
Measured: `OpenAIProvider` → `hasattr(generate) == False` → takes the stream branch, but yields
dicts, so it is safe today. `OllamaProvider` and `MockProvider` define `generate`, so they take the
non-stream branch. This is a latent trap for any future provider that lacks `generate` and yields
typed events.

---

## 8. Serialization / Replay

**No typed event ever crosses a serialization or persistence boundary.**

| Check | Result |
|---|---|
| `TokenBatch`/`StreamComplete`/`ToolCallBatch`/`StreamError` in `runtime.py`, `session.py`, `session_repo.py`, `infra/store.py` | **0 matches** |
| What the runtime persists | `normalize_event(raw_event).to_dict()` (`runtime.py:790-794`) — **normalised dicts only** |
| Replay reconstruction | operates on persisted dicts → dict-shaped → **safe** |

> **DERIVED METRIC — REPLAY IMPACT: NONE.** The replay path cannot encounter F40 because the events
> that reach persistence have already been normalised by the main loop. There is **no asymmetry**
> between live and replay for this defect, and no replay evidence is required.

`stream_parser.py` is imported only by `ollama_client.py:33`, so it is a producer-side helper, not a
second consumer path.

---

## 9. Iteration Lifecycle

```text
iteration N starts
  → _guarded_provider_stream (normalised)  ✔
  → tool calls dispatched / results appended
  → round completes
iteration budget exhausts
  → budget_notice appended to messages
  → system(budget_notice, level="warning") yielded        (:1069)
  → *** wrap-up: _stream_events_async(system_prompt, msgs, tools=None) ***  (:1080)
       F40: aborts on the first typed event
  → `wrapped_up` False
  → error_event("Max iterations reached", code=CODE_ITERATION_BUDGET)  (:1092)
  → done_event                                             (:1098)
```

| Lifecycle question | Finding |
|---|---|
| When is the wrap-up stream created? | After the iteration loop exits, on budget exhaustion only |
| When consumed? | Immediately, inside the same `try` |
| Can the producer terminate before creating it? | Yes — and that is handled: `test_wrapup_failure_falls_back_to_error` covers a genuine provider raise |
| Does cancellation change the shape? | No — cancellation ends the turn earlier; the wrap-up is not reached |
| Do final events differ from intermediate? | **Yes — that is the defect.** The wrap-up asks for `tools=None`, which routes to the *same* provider method, so the representation is identical; only the *consumer* differs |
| Is replay a different type? | No (§8) |

> **INTERPRETATION.** This is **not** a lifecycle-ordering defect. The ordering is correct; the
> consumer reads the right stream at the right time. The defect is purely one of **representation**
> plus **vocabulary**.

---

## 10. Test Fidelity

### 10.1 The decisive finding

**Two tests pin opposite outcomes for the same production code path, and both pass.**

| Test | Provider representation | Asserts | Result | Faithful to the Ollama path? |
|---|---|---|---|---|
| `test_core_stateless.py::TestMaxIterationsWrapUp::test_final_summary_replaces_error` | **dict** | `SUMMARY_MARKER` delivered, **no** error | **pass** | **NO** |
| `test_core_stateless.py::…::test_wrapup_failure_falls_back_to_error` | dict + genuine `raise` | error emitted | pass | partial |
| `test_13h2_determinism.py::test_d6_wrapup_failure_error_plus_done_once_each` | **typed (`MockProvider`)** | error emitted — *"honest error"* | **pass** | representation **YES**, expectation **NO** |
| `test_13h5_success_derivation.py::test_exhaustion_not_success` | typed (`MockProvider`) | error + not-success | pass | consistent with the defect |
| `test_mock_provider.py` | typed | provider yields typed events | pass | pins the typed representation |

The feature's **contract test** (`test_final_summary_replaces_error`, docstring: *"must synthesize an
answer from the gathered context instead of dying with a bare 'Max iterations reached' error that
throws away minutes of tool work (live-evidenced)"*) validates the feature **with a representation the
real Ollama provider never produces.**

The test that *does* use the production representation **names the failure as expected**:

```python
# MockProvider yields objects; the wrap-up path consumes RAW events
# (stateless.py:826-834, ev.get) so it cannot see them: honest error.
assert _types(evs).count("error") == 1
assert any("Max iterations reached" in e.get("message", "") ...)
```

### 10.2 Why the suite cannot see it

The wrap-up's `except Exception` (`stateless.py:1089`) **collapses two distinct causes into one
observable**:

```text
cause A: the provider genuinely failed        → error emitted
cause B: the consumer cannot read the events  → error emitted
```

`test_wrapup_failure_falls_back_to_error` (cause A) and `test_13h2::test_d6` (cause B) assert the
**same** observable for **different** causes. **No test distinguishes them**, so the defect is
invisible to a green suite.

### 10.3 Classification of F40-related tests

| Test | Class |
|---|---|
| `test_core_stateless.py::TestMaxIterationsWrapUp::*` | REAL COMPONENT (dict-shaped fixture) |
| `test_13h2_determinism.py::test_d6_*` | REAL COMPONENT (typed fixture) — **pins the defect** |
| `test_13h5_success_derivation.py::test_exhaustion_not_success` | REAL COMPONENT (typed fixture) |
| `test_runtime_injected_context.py::test_budget_notice_persisted_in_transcript` | REAL COMPONENT — reaches the wrap-up but asserts only the notice + `done`, so it is silent on the summary |
| `test_mock_provider.py` | MOCKED COMPONENT (provider-level) |

> **No test drives the wrap-up with the production representation and asserts the intended
> behaviour.** That is the coverage gap, and it is the same class as F41's fixture that did not
> reproduce the production control flow.

---

## 11. Reproduction

### 11.1 Live — the real Ollama daemon

Real provider (`llama3.2:3b` via the local daemon), real `AgentRuntime.run_turn`, `max_iterations=1`,
instrumented at the two consumers:

```text
MAIN   : {'ToolCallBatch': 1, 'StreamComplete': 1}   ← typed; normalised by the guard  ✔
WRAPUP : {'TokenBatch': 1}                            ← typed; NOT normalised            ✘

Iteration wrap-up call failed
AttributeError: 'TokenBatch' object has no attribute 'get'

turn event types : ['error', 'tool_result', 'system', 'error', 'done']
content delivered: []
error messages   : ["Blocked: Schema validation failed for tool 'read_file': 'pat…",
                    "Max iterations reached"]
```

The first error is the known `llama3.2:3b` `path`-argument capability issue recorded during
ADR-0016 — unrelated to F40. The second is F40.

### 11.2 Deterministic — a shipped production provider

Real `WispAgentCore.turn` + real `MockProvider`, `max_iterations=1`, capturing the swallowed
exception via `logger.exception` (called inside the `except` block, so `sys.exc_info()` is live):

```text
provider            : MockProvider (a shipped production provider)
emitted types       : ['tool_call', 'tool_result', 'system', 'error', 'done']
content delivered   : []
'SUMMARY-OF-FINDINGS-TEXT' delivered?  False
'Max iterations reached' emitted?      True
wrap-up calls that raised: 1
   log: 'Iteration wrap-up call failed'
   swallowed exception: AttributeError: 'TokenBatch' object has no attribute 'get'
```

The provider **did** produce `SUMMARY-OF-FINDINGS-TEXT`. It was **never delivered**.

### 11.3 Control — same core, dict-shaped provider

```text
emitted types       : ['tool_call', 'tool_result', 'system', 'content', 'done']
content delivered   : ['SUMMARY-OF-FINDINGS-TEXT']
'SUMMARY-OF-FINDINGS-TEXT' delivered?  True
'Max iterations reached' emitted?      False
wrap-up calls that raised: 0
```

**The only variable is the event representation.** The wrap-up logic itself is correct.

### 11.4 The masking probe — proving F40-2 is real and hidden

Driving the wrap-up with the **normalised** shape of a typed terminal event:

| wrap-up terminal `type` | content delivered | error emitted |
|---|---|---|
| `"done"` | `['SUMMARY_MARKER']` | **none** ✔ |
| `"complete"` (= `StreamComplete.phase`, `stream_events.py:61`) | `['SUMMARY_MARKER']` | **`Max iterations reached`** ✘ |

> **DECISIVE.** Normalising the representation is **necessary but not sufficient**. Even with
> normalisation, a typed `StreamComplete` normalises to `"complete"`, which the wrap-up's
> `elif etype == "done"` (`stateless.py:1086`) does not recognise — so `wrapped_up` stays `False` and
> the spurious error is still emitted. **A repair that only adds normalisation would leave a
> half-fixed defect.**

---

## 12. Failure Classification

### 12.1 Primary

```text
F40 = EVENT TYPE CONTRACT VIOLATION  (a CONSUMER AUTHORITY DEFECT)
```

The wrap-up interprets an event whose representation contract is owned elsewhere. It is a
**consumer authority defect**: the adapter (`_normalize_event`) is the designated owner of the
typed→canonical mapping, and the wrap-up bypasses the only code path that invokes it.

### 12.2 Separated sub-defects

| ID | Site | Mechanism | Reachability |
|---|---|---|---|
| **F40-1** | `stateless.py:1081` | `.get()` on a typed event | **live on Ollama** |
| **F40-2** | `stateless.py:1086` | terminal vocabulary `{done}` ⊅ `{complete}` | **masked by F40-1**; proven in §11.4 |
| **F40-3** | `compaction.py:146-149` | same `.get()` assumption | latent (`compaction_model` empty by default) |
| **F40-4** | `planner.py:121-123` | `isinstance(c, dict)` silently drops typed events | latent (needs a provider without `generate`) |

### 12.3 The three terminal vocabularies

| Owner | Terminal set | Line |
|---|---|---|
| `provider_stream.TERMINAL_TYPES` | `{done, complete, stream_complete}` | `provider_stream.py:51` |
| Main loop | `{complete, done}` | `stateless.py:735` |
| **Wrap-up** | **`{done}`** | `stateless.py:1086` |
| `_BOOKKEEPING_TYPES` (a fourth, for the guard) | `{done, stream_complete, checkpoint, usage, stream_stats}` | `stateless.py:2212` |

Three consumers, four vocabularies, no single authority. F40-2 is the concrete consequence.

### 12.4 The co-existing contract gap (separate finding)

```text
DECLARED (protocol.py:36)   ->  Generator[dict[str, Any], None, None]
DOCSTRING (protocol.py:39)  ->  "Yields standardized event dictionaries:"
SHIPPED (ollama_client.py)  ->  TokenBatch / ToolCallBatch / Checkpoint / StreamComplete / StreamError
SHIPPED (mock.py)           ->  TokenBatch / ToolCallBatch / StreamComplete
```

> **OBSERVED FACT.** The declared provider contract and two shipped providers **contradict each
> other.** The declaration is not enforced anywhere.

This is **not** the same defect as F40-1/F40-2, and it is not caused by them. It is the reason the
repair is a decision rather than a patch (§16).

### 12.5 Not the cause

- **Not** a lifecycle defect — the ordering is correct (§9).
- **Not** a serialization defect — nothing is serialized on this path (§8).
- **Not** a fixture-only defect — reproduced live against the real daemon (§11.1). The fixture
  *hides* it; the fixture is not the *cause*.
- **Not** the F37 defect — F37 was an evidence-adapter fold; this is a consumer representation
  assumption. Different authority, different mechanism. **The F37 pattern must not be copied.**

---

## 13. Authority Map

| Concern | Current owner | Evidence | Correct owner candidate |
|---|---|---|---|
| Event construction | the provider | `ollama_client.py:623-695`, `mock.py:105-136`, `openai.py` | provider (unchanged) |
| Event representation **contract** | `providers/protocol.py` — declares `dict` | `protocol.py:36,39-45` | the protocol — **currently contradicted, unenforced** |
| Typed→canonical normalisation | `WispAgentCore._normalize_event` | `stateless.py:2241` | core (**keep**) |
| **Invocation** of normalisation | `_guarded_provider_stream` | `stateless.py:2227-2233` | **bundled with stall recovery — the defect's shape** |
| Terminal-type vocabulary | four places disagree | §12.3 | **one canonical set** |
| Wrap-up interpretation | `_turn_inner` | `stateless.py:1080-1088` | core, on **normalised** events |
| Compaction interpretation | `Compactor._llm_summarize` | `compaction.py:141-151` | same |
| Planner interpretation | `plan()` | `planner.py:120-123` | same |
| Persistence / replay | runtime (normalised dicts) | `runtime.py:790-794` | **unchanged** |

### 13.1 The architectural shape of the defect

`_guarded_provider_stream` bundles **two responsibilities**:

1. **stall + empty-stream recovery** (`max_attempts`, deadlines, `TERMINAL_TYPES`);
2. **normalisation** (`normalize_event`).

The wrap-up has a legitimate reason to skip (1) — it must not retry an empty final round — but by
calling `_stream_events_async` directly it also skips (2). **The bundle is why the bypass exists.**
Any repair that only patches the consumer leaves the bundle in place and the trap open for the next
consumer.

---

## 14. Reconstructed Contract

**CURRENT CONTRACT (implicit — never written down in one place):**

```text
event name        : `type` (canonical)  /  `phase` on typed events (normalised to `type`)
required fields   : `type`
content fields    : `text` (dict providers), `text` (typed TokenBatch)
terminal spellings: `done` (dict) | `complete` (normalised StreamComplete) | `stream_complete` (bookkeeping)
producer          : the configured provider  — MAY yield dict OR typed dataclass
normalisation     : REQUIRED before interpretation; implemented by `_normalize_event`;
                    INVOKED only by `_guarded_provider_stream`
consumer obligation: NOT STATED ANYWHERE
serialization     : none on the stream path; the runtime persists normalised dicts
replay semantics  : dict-shaped; unaffected by this defect
failure semantics : a consumer exception inside the wrap-up is SWALLOWED and re-reported
                    as "the provider failed"  ← collapses cause A and cause B
```

```text
CONTRACT VIOLATION: YES
  - protocol.py declares `dict`; OllamaClient and MockProvider yield typed dataclasses.
  - the consumer obligation ("normalise before interpreting") is unwritten, so three of
    four consumers do not honour it.
  - the terminal vocabulary has three competing spellings.
```

---

## 15. Candidate Repair

**Described only. Not implemented. No file was modified.**

The repair cannot be written before §16 is decided, because the two directions are materially
different. Both are recorded here as candidates.

### Candidate A — "providers yield canonical dicts" (enforce the declaration)

Change `OllamaClient` and `MockProvider` to yield dicts. Blast radius: `ollama_client.py`,
`mock.py`, and the tests that pin the typed representation (`test_mock_provider.py`,
`test_salvage_gate.py`), plus `EventBatcher`/`stream_parser`'s consumers. It would **discard** the
typed identity that `Checkpoint`/`StreamComplete` carry (validation hash, `done_reason`,
`total_tokens`) unless the dicts were made equivalent. **Not a restoration — a redesign.**

### Candidate B — "typed-or-dict, one mandated adapter" (ratify the de facto design)

Keep the providers as they are; make normalisation **mandatory at every consumer** and **one
terminal vocabulary**.

- `stateless.py` wrap-up: normalise each event, and accept the canonical terminal set.
- `compaction.py`: normalise each event.
- `planner.py`: normalise each event instead of `isinstance(c, dict)`.
- Widen `protocol.py`'s declaration to admit both shapes, stating that normalisation is the
  consumer's obligation.

**Blast radius: 3 consumers + 1 declaration + tests. No provider change. No default change. No
persistence change. No replay change.**

### 15.1 The trap both candidates must avoid

**Adding normalisation alone is insufficient** (§11.4). Any candidate must also fix the terminal
vocabulary, or the spurious `Max iterations reached` error survives. This is the single most
important repair constraint and it is only visible because F40-1 masks F40-2.

### 15.2 The second-order candidate

Splitting the normaliser out of `_guarded_provider_stream` so it **cannot** be bypassed (e.g. a
`_normalized_stream()` helper that the guard also uses) is the structural fix for §13.1. It is a
strictly larger change than Candidate B's per-consumer edits and changes an internal boundary —
therefore part of the decision, not a mechanical detail.

---

## 16. ADR Requirement

```text
ADR_REQUIRED: YES
```

### Reasoning

The brief's test is whether the repair changes an authority boundary, event contract, persistence
contract or cross-mode semantics.

1. **A contract is contradicted.** `protocol.py` declares `dict`; `OllamaClient` and `MockProvider`
   yield typed dataclasses. Whichever direction the repair takes, **one of the two must change.**
   Choosing which is authoritative is a contract decision, not a mechanical fix.
2. **The candidates are materially different.** Candidate A changes two providers and the tests that
   pin them; Candidate B changes three consumers and one declaration. Different blast radius,
   different risk, different reversibility.
3. **The terminal vocabulary has no owner** (§12.3 — four spellings across three consumers).
   Choosing the canonical set is a contract decision.
4. **The consumer obligation is unwritten** (§14). Making normalisation mandatory — and deciding
   whether it is enforced structurally or by convention — assigns ownership.
5. **F40-2 is masked by F40-1**, so a "mechanical" repair derived from the visible symptom would be
   wrong (§11.4). The correct scope is not derivable from the symptom.

`REQUIRES_ARCHITECTURE_DECISION` — and, as with ADR-0038, the decision should be expected to be
**larger than its implementation**: Candidate B is ~3 consumer edits plus a declaration, with no
flag, no default change and no migration.

**Not** escalated on the strength of F40-3/F40-4 alone: those are the same missing adapter and follow
automatically from the decision.

---

## 17. Regression Baseline

No production or test file was modified by this phase, so the baseline is by definition unchanged.
Measured anyway, to distinguish pre-existing from new:

| Suite | Result | Classification |
|---|---|---|
| the four wrap-up / failure-signal tests | **4 passed** | — |
| `test_core_stateless`, `test_failure_signal_classification`, `test_13h5_success_derivation`, `test_13h2_determinism`, `test_runtime_injected_context`, `test_mock_provider`, `test_salvage_gate` | **151 passed** | — |
| the canonical migration set (29 files) | **848 passed, 1 failed** | **pre-existing F38** (`test_node_identity.py::…::test_a_parallel_round_is_journaled_as_one_exchange_per_call`) |

```text
NEW FAILURES:         0
PRE-EXISTING:         1  (F38 — matches the recorded 849/848/1 exactly)
ENVIRONMENTAL:        0
PRODUCTION CHANGES:   0
TEST CHANGES:         0
```

The four wrap-up tests passing is itself the evidence for §10: **the suite is green on both sides of
the defect.**

---

## 18. Final Status

```text
=== STATUS: FORENSIC RECON COMPLETE ===

F40:
CONFIRMED_PRODUCTION_DEFECT

ROOT CAUSE:
The iteration wrap-up loop (stateless.py:1080-1088) consumes the RAW provider stream and
calls `.get()` on it; the only normaliser (`_normalize_event`) is invoked exclusively by
`_guarded_provider_stream`, which the wrap-up bypasses — so on any provider that yields
typed events (OllamaClient, MockProvider) the first event raises AttributeError, which is
swallowed and re-reported as "Max iterations reached", discarding the model's summary.
A second, masked defect in the same expression recognises only the terminal spelling
`done`, not the `complete` that a normalised StreamComplete produces.

PRODUCTION/TEST DIVERGENCE:
YES. The feature's contract test (test_core_stateless.py::test_final_summary_replaces_error)
uses a dict-shaped provider — a representation the real Ollama provider never produces.
The test that does use the production representation (test_13h2_determinism.py::test_d6)
asserts the failure as expected behaviour. Both pass; no test distinguishes "the provider
failed" from "the consumer cannot read the provider's events".

AUTHORITY GAP:
`_normalize_event` owns the typed→canonical mapping but its INVOCATION is bundled inside
the stall guard, so normalisation is optional at any consumer that bypasses the guard.
The provider event-representation contract is declared as `dict` (protocol.py:36) and
contradicted by two shipped providers. The terminal vocabulary has four spellings and no owner.

INSTANCES:
F40-1 stateless.py:1081   .get() on a typed event          LIVE (Ollama)
F40-2 stateless.py:1086   terminal vocabulary {done}       MASKED by F40-1; proven
F40-3 compaction.py:146   same .get() assumption           latent (compaction_model unset)
F40-4 planner.py:121      isinstance(c, dict) drops typed  latent (needs provider w/o `generate`)

REPLAY IMPACT:
NONE — typed events never cross persistence; the runtime persists normalised dicts.

ADR_REQUIRED:
YES

PRODUCTION CHANGES:
0

TEST CHANGES:
0

NEXT:
ADR — ratify the provider event-representation contract and the canonical terminal
vocabulary (Candidate A vs Candidate B, §15), then implement the consumers against it.
Do NOT copy the F37 pattern.
```

---

## 19. Evidence Artefacts

| Path | Contents |
|---|---|
| `.workbuddy-ai/memory/post-m13-f40-recon/f40_probes.py` | Probes A–F: type provenance (AST), the declared contract, the typed/dict wrap-up pair, the compaction consumer, the planner filter |

Live-daemon and terminal-vocabulary probes were run inline; their exact commands and outputs are
quoted verbatim in §11.
