# Phase 3 — Canonical Authority Map

**Repository:** `/Users/philosopher/Documents/wisp` @ `main` `5ea0ed9`
**Method:** for each shared concept, locate every implementation and ask *"if two parts of Wisp disagree about this, which is authoritative?"*
**Status:** analysis only — no code modified

---

## Preamble: the answer for four of nine concepts is "already canonical"

The brief asks me to establish canonical authorities for nine concepts. Four already have exactly one, verified by import evidence. Reporting that is the honest outcome; declaring a new authority where one exists would create the duplication the brief is trying to remove.

| # | Concept | Verdict |
|---|---|---|
| 1 | Provider registry | **Already canonical** |
| 2 | Provider contract (interface) | **Already canonical** |
| 3 | Graph representation | **Already canonical** |
| 4 | Containment primitive | **Canonical exists; 2 consumers not migrated** |
| 5 | Model listing | **Two authorities — real duplication** |
| 6 | Execution state | **Three vocabularies — real ambiguity** |
| 7 | Error taxonomy | **Three duplicate classifiers — real duplication** |
| 8 | Event representation | **Layered by design; 3 normalization paths to converge** |
| 9 | Validation path | **Layered by concern; defensible as-is** |

---

## The canonical authority table

| Concept | Current authorities | Canonical authority | Consumers | Migration required |
|---|---|---|---|---|
| **Provider registry** — *what providers exist* | `provider_select.KNOWN_PROVIDERS` only | **`provider_select.KNOWN_PROVIDERS`** (confirmed: no second copy exists) | `providers/factory.py:118`, `repl/completion.py:40,68`, `repl/commands/provider.py:24,275,329,412`, `cli/` | **None.** Verified canonical. |
| **Provider contract** — *the interface* | `providers/protocol.py` `Provider` ABC | **`providers/protocol.py`** | All 5 provider implementations; `factory.py` | **None** for the protocol itself. Consumers that bypass it are covered by rows 3 and 5. |
| **Provider construction** | `ProviderFactory.create()` **and** direct concrete imports (`cli/setup.py:92` → `OllamaProvider`) | **`ProviderFactory.from_config()`** | `composition.py`, `entry.py`, subagent runner, `cli/setup.py` | **Yes — 1 site** (`cli/setup.py:92`). Low risk. |
| **Model listing** — *what models does provider X serve* | (a) `Provider.list_models()` per provider (the protocol method); (b) `provider_catalog._list_models_impl()` reimplemented HTTP per provider (`:82-111`) | **`Provider.list_models()`** — provider-specific knowledge belongs behind the protocol | `provider_catalog.list_models()`, `repl/completion.py:55`, `repl/commands/provider.py:160` | **Yes — the catalog must delegate to the protocol instead of reimplementing.** Must preserve the NVIDIA offline fallback (`provider_catalog.py:100-111`). |
| **Provider catalog metadata** — *NVIDIA's known model set* | `providers/nvidia.NVIDIAProvider._MODEL_CONTEXT` (**private**), read by `provider_catalog.py:105` | **`providers/nvidia`**, exposed via a **public** protocol surface (e.g. `get_model_info` / a declared catalog) | `provider_catalog.py` | **Yes — 1 site**, replacing a private-attribute reach with a public contract. |
| **Execution state** — *what state is this run in* | (a) `multi_agent/background.py` module constants → `"completed"`; (b) `contracts/run.py` `RunStatus` → `"completed"`; (c) `graph/types.py` `RunStatus` → `"succeeded"`; (d) `runs/record.py` `RunState` → `"succeeded"` | **`runs/record.py` `RunState`** — it is the only one with a declared transition table (`LEGAL_TRANSITIONS`) and terminal set, so it is the most complete statement of the lifecycle | `graph/*`, `runs/*`, `multi_agent/background.py`, `coding.py:239`, `supervisor.py:158`, `tools/subagent_tools.py`, `transport/renderer.py`, `tui/` | **Yes — the largest migration in this plan.** Requires an explicit adapter for each vocabulary; must not change the wire values that existing consumers compare against. |
| **Error classification** — *is this result an error* | (a) `transport/renderer.py:29` `result_is_error()`; (b) `transport/cli.py:1462` `_is_error_result()`; (c) `benchmark/scoring.py:88` `_is_error_result()` | **The module that owns the tool-result envelope** (`core/stateless.py:_normalize_tool_result`, `:1899`), exporting one predicate | The three call sites above | **Yes — 3 sites** collapse to one predicate. Low risk. |
| **Error taxonomy** — *how errors are named* | `core/events.py` `DENIAL_*` (5) + `CODE_*` (4); `tools/errors.py` `ToolError`; `exceptions.py`; `graph/types.py` `NodeFailure` | **`core/events.py`** for the event-level taxonomy (denials + codes); `tools/errors.py` remains the tool-layer exception. The two are different axes, not duplicates. | Many | **Partial — document the two axes; no merge.** `NodeFailure` is graph-local by design. |
| **Containment** — *prove a path is inside a root* | `pathsec.resolve_contained` (canonical, used by `tools/_utils`, `graph/artifacts`, `workspace`); **plus** `server/routes/files._resolve_path` and `sandbox.resolve_sandbox_cwd` (independent reimplementations) | **`wisp/pathsec.resolve_contained`** | 5 call sites | **Yes — 2 sites.** Both currently accept control characters that `pathsec` rejects. |
| **Graph representation** | `graph/types.py` only | **`graph/types.py`** (confirmed: `core/graph/` has zero production importers) | `graph/*` | **None** — plus delete the test-only `core/graph/` (F21). |
| **Event representation** | `core/events.AgentEvent` (internal) · flat `dict` (wire, yielded by `turn()`) · `contracts/envelope.CanonicalEvent` (versioned envelope) | **Layered, and correctly so.** `AgentEvent` is authoritative *internally*; `CanonicalEvent` is authoritative *at the versioned wire boundary*. | Core, transports, server, contracts | **No merge** — but converge the **3 normalization paths** (below). |
| **Normalization path** | (a) `core/events.normalize_event()`; (b) `core/stateless._flatten_event()`; (c) `contracts/adapters` flat↔nested | **`core/events.normalize_event()`** for provider→internal; **`contracts/adapters`** for internal→wire | Provider stream, `runtime.run_turn:443-451`, server | **Partial** — document which path owns which direction; the defensive re-normalization in `runtime.run_turn` suggests ambiguity exists today. |
| **Validation path** | `jsonschema` for tool args (`stateless.py:2111`) · `pydantic` for HTTP bodies · `graph/validator.py` for graphs · `registry.py` schemas | **Layered by concern — keep as-is.** Each validates a different artifact at a different boundary. | — | **None.** Treating these as duplication would be a mistake. |
| **Sandbox routing** | `tools/bash.py:78` uses `get_sandbox()` singleton; `tools/primitives.py:83` uses `get_router()` | **`sandbox/router.get_router()`** — it has TTL caching and explicit tier failover | `bash.py`, `primitives.py` | **Yes — 1 site** (`bash.py`). |
| **Persistence** | `infra/store.py` `UnifiedStore` (sessions, runs, events, memory, traces, tasks) **plus** `graph/store.py` `GraphStore` (graph_* tables) | **Deliberately split.** Different schemas, different owners, same DB file. | — | **None.** Not a duplicate authority — a bounded-context split. |
| **Tool registry** | `tools/registry.py` `TOOL_SCHEMAS` + `TOOL_IMPLS` | **`tools/registry.py`** | `stateless.py`, `tool_executor.py` | **None.** Verified 42 == 42. |

---

## Concepts where "who is authoritative?" had no answer

The brief asks me to establish an authority where none is clear. Three cases:

### 1. Execution state — **no authority exists; `runs/record.py` is the best candidate**

The reason this is a genuine ambiguity rather than layering: `graph/types.py` and `contracts/run.py` **both define a type named `RunStatus`** with **different values for the same terminal-success concept**. Two types with the same name and different meanings is the definition of an ambiguous authority.

`runs/record.py::RunState` is the right canonical choice because it is the only vocabulary with:
- a declared `LEGAL_TRANSITIONS` table (the lifecycle is *specified*, not implied),
- an explicit `TERMINAL_STATES` frozenset,
- a validation function (`is_legal`).

The other three are subsets with local naming. Canonicalizing means the others become **adapters into** it, not deletions — because their wire values are already compared by consumers.

### 2. "Is this result an error?" — **no authority; assign to the envelope owner**

Three independent implementations of the same predicate. There is no disagreement *today* because the envelope shape is stable. The authority should be the module that defines the envelope (`_normalize_tool_result`), so the predicate and the shape cannot drift apart.

### 3. Model listing — **two authorities; the protocol should win**

`Provider.list_models()` is the declared contract. `provider_catalog._list_models_impl()` is an undeclared second implementation. The protocol should win because the knowledge is provider-specific — and because the catalog currently reaches into a concrete provider's *private* attribute to obtain a fallback, which is only necessary because the catalog does not own that knowledge.

---

## Migration risk ranking

| Concept | Sites to migrate | Blast radius | Risk | Note |
|---|---|---|---|---|
| Execution state | ~10 consumer files | High | **High** | Silent-failure class; wire values are already compared |
| Model listing | 1 module + 1 provider | Medium | **Medium** | Must preserve the NVIDIA offline fallback |
| Provider construction | 1 site | Low | Low | Mechanical |
| Error classification | 3 sites | Low | Low | Predicate collapse |
| Containment | 2 sites | Medium | Low | Adds a check; cannot loosen |
| Sandbox routing | 1 site | Low | Low | Both already exist |
| Normalization | 2 modules | Medium | Low | Documentation + one delegation |

**Ordering follows from this table, and it matches Phase 1's sequence:** execution state first (highest ambiguity, highest risk, must precede dependent changes), then the provider cluster, then the low-risk collapses.

---

## The brief's closing question, answered per concept

> *"Where is the authority for this concept, and what prevents another part of Wisp from becoming an accidental second authority?"*

| Concept | Authority | What prevents a second authority |
|---|---|---|
| Provider registry | `provider_select.KNOWN_PROVIDERS` | **Nothing structural** — but the factory *validates* that its registrations cover every registry entry (`factory.py:36`), which is an executable check. **Good enough; this is the model to copy.** |
| Provider contract | `providers/protocol.py` | The ABC: a missing method fails at instantiation. **Statically enforced.** |
| Model listing | *none today* | **Nothing.** This is why a second implementation appeared. Fix = make the catalog delegate, so a second implementation has nowhere to live. |
| Execution state | *none today* | **Nothing.** Three types, one name collision, no cross-check. Fix = one canonical enum + explicit adapters + a test asserting producers agree. |
| Error classification | *none today* | **Nothing.** Fix = one exported predicate. |
| Containment | `pathsec` | **Partially** — 3 of 5 consumers use it; the 2 that don't are the ones that diverged. Fix = migrate them; the differential test then protects it. |
| Graph representation | `graph/types.py` | The `Graph.fingerprint()` hash over security-relevant fields, plus the validator. **Runtime enforced.** |
| Event representation | Layered | The `contracts` versioned envelope + adapters. **Documented, not enforced.** |
| Validation | Layered by concern | Different artifacts, different boundaries. **No enforcement needed.** |

**The honest pattern:** the concepts that have an executable guard (provider contract via ABC, provider registry via factory assertion, graph via fingerprint, containment via `pathsec`) have not drifted. The concepts that drifted — model listing, execution state, error classification — are exactly the ones with **no guard at all**. That correlation is the strongest argument for the brief's Rule 4 ("make boundaries executable"): every concept that drifted lacked one.
