# Phase 10 — Authority Closure Audit

**Repository:** `/Users/philosopher/Documents/wisp`
**Baseline:** Phase 9 committed at `83b10af`
**Scope:** the four subsystems Phase 9 named as unable to answer *"where is the authority, and what prevents a second one?"*
**Status:** audit + implementation; all four targets **CLOSED**, plus one
additional area (the protected-path guard, §3.5) found and closed en route

---

## 0. Executive summary

| Target | Phase 9 verdict | Phase 10 verdict |
|---|---|---|
| **A — model listing** | none / NOTHING | **CLOSED (semantic).** One declared contract; provider-specific transport explicitly *distinguished*, documented, and tested. Transport not unified — deliberately. |
| **B — error classification** | none / NOTHING | **CLOSED.** One taxonomy, one classifier, one denial detector. **Two real defects found and fixed** en route. |
| **C — REST policy boundary** | none / NOTHING | **CLOSED** (user decision: option B). Every route reaching host execution or altering executable config now carries the policy gate; the boundary is pinned by exact-set and functional tests. |
| **D — core → presentation** | none / NOTHING | **CLOSED.** Exactly **one** edge exists; it is classified, isolated, guarded, and pinned. |
| **C+ — protected-path guard** *(found during C)* | not assessed | **CLOSED.** One predicate; all three authorization paths consult it. A verified REST escalation path is shut. |
| **E — M4 governance wiring** *(found during G1)* | not assessed | **OPEN — documented and pinned.** The organization policy bundle is never loaded into the runtime. The subsystem is complete and tested; the seam to the decision point is empty. |

**Two corrections to the Phase 9 report.** Its closing-question table (§"answered per subsystem") was written *before* rounds 2–4 added enforcement, and it now understates reality: it says error classification and core→presentation have "NOTHING", when `test_tool_result_error_authority.py` and `test_layer_direction.py` had already been added. Phase 10 corrects the table rather than leaving a stale baseline.

**A correction to this document.** An earlier draft of §3.3 held Target C open on the claim that gating three routes *"breaks the shipped desktop client."* **That was false** — it came from reading `require_tool_allowed`'s docstring instead of driving the policy. Measured, those actions are ALLOW in `full`/`auto_edit`/`ask_all` and DENY only in `read_only`. There was no client-breaking cost. Recorded rather than quietly edited: a decision taken on a false cost estimate is a guess with a rationale.

**Headline finding.** The most consequential Phase 10 discovery is in Target B: `tool_executor._record_metrics` decided success by substring-matching `'"status": "ok"'` in the result. Every successful **plain-text** result — the shape most tools return (`read_file`, `run_bash`, `git_diff`) — failed that test, so the *live* metrics path counted successful calls as errors and deflated the reported success rate. A second, adjacent site (the fetch breaker) made the same class of decision on a **truncated** slice of text.

**Second headline — a verified escalation path, found while closing R1b.** The
`.wisp/hooks` protected-path guard was enforced on the agent tool path and
**absent from REST**: `POST /api/files` wrote a hook that the equivalent
`write_file` *tool call* was refused. Adding the policy gate did not close it —
the gate consults `SecurityPolicy`, which scans no arguments. Root cause is the
engagement's recurring theme in its purest form: **two authorization
implementations**, and REST uses the weaker one. Fixed by extracting one
canonical predicate (`wisp/pathsec.is_protected_path`) and routing all three
paths through it. See §3.5 and `PHASE_10_PROTECTED_PATH_GUARD.md`.

**Third headline — a governance layer that is not connected to anything.**
Following G1 one step further (*if the agent has an L0 policy layer, what does
REST do with it?*) showed that **neither** path has one, because **nothing loads
a policy bundle**. `wisp/policy/` — signed bundles, narrow-only merge,
provenance, offline continuity, a CLI, four server routes, and full test
coverage — is never reached by the runtime. `authorize()`'s organization layer
works correctly and is always passed `None`. An operator can publish a bundle,
explain a denial with it, and dry-run it; none of that affects what the agent
may do. **This is the most consequential finding of the engagement**: the
earlier ones were controls that were too weak, this is a control that appears to
exist and does not. See **`PHASE_10_M4_GOVERNANCE_UNWIRED.md`**.

---

## 1. Target A — Model listing authority

### 1.1 The mapping (Rule 2, steps 1–4)

| # | Implementation | What it is | Consumers |
|---|---|---|---|
| 1 | `providers/protocol.py::Provider.list_models()` | The declared protocol method; implemented per provider (ollama, openai, openrouter; NVIDIA inherits OpenAI) | `provider_select.build_provider` consumers, `openai.py:410` |
| 2 | `provider_catalog.list_models()` → `_list_models_impl()` | An independent implementation: its own per-provider HTTP | `server/routes/models.py`, `repl/completion.py:55`, `repl/commands/provider.py:160` |
| 3 | `provider_catalog` offline fallback | `build_provider(...).available_models` (public) — Phase 9 removed the private `_MODEL_CONTEXT` reach | internal to (2) |

### 1.2 Semantic differences (step 3) — measured, not assumed

Phase 9 answered the three equivalence questions; Phase 10 re-verified them as preconditions:

| Delta | Implementation (1) | Implementation (2) |
|---|---|---|
| **Auth** | `Authorization: Bearer` + OpenRouter's `HTTP-Referer`/`X-Title`; Ollama sends **no** bearer | `Authorization: Bearer <cfg.api_key>` only |
| **Timeout** | OpenRouter 15 s; OpenAI `HARDENED_TIMEOUT` (→10 s) | flat 5.0 s |
| **Degradation** | provider-internal `try/except → []` | `_authed_get` absorbs failures; `/api/models` adds its own guard |

### 1.3 Is canonicalisation safe? (step 5) — **No, not as a transport merge**

Unifying the transport would silently change all three. Phase 9 concluded this; Phase 10 confirms it and **stops**.

### 1.4 The target contract (steps 6–7)

**The authority that matters is semantic, not transport.** `provider_catalog.list_models` is now declared as the canonical model-listing contract, with its input, output, guarantees, and the three transport deltas written into the docstring at the point of authority.

**Semantic authority (singular):** `provider_catalog.list_models`
**Provider-specific transport (legitimately distinct):** `_list_models_impl` + `_authed_get`
**Executable boundary:** `tests/test_provider_listing_equivalence.py` — 19 tests

The desired shape — *one semantic authority → explicit adapters → consumers* — is what exists. What is explicitly **not** claimed is one HTTP implementation.

### 1.5 Tests proving the boundary

| Test | Proves |
|---|---|
| `test_the_contract_is_declared_where_the_authority_lives` | the contract names its own boundary and the three deltas |
| `test_contract_yields_empty_when_the_fetch_fails` | the `[]`-on-failure guarantee, per provider |
| `test_the_transport_helper_is_the_failure_absorber` | the guarantee rests on one identified property |
| `test_caching_is_part_of_the_contract` | cache hit + `force=True` bypass |
| `test_provider_specific_transport_is_confined_to_the_catalog` | the distinct transport cannot spread to a second module |
| `test_catalog_does_not_import_concrete_providers` | the Phase 9 inversion cannot return |
| `test_openrouter_provider_adds_attribution_headers` … | the three deltas still exist → delegation still unsafe |

**Correction made during Phase 10.** My first draft of the contract claimed *"never raises"*. A test I wrote immediately disproved it: `_list_models_impl` propagates if the transport helper raises — it simply never happens, because `_authed_get` absorbs failures. The docstring now states the real guarantee (*"returns [] for an unreachable provider; a programming error in a provider branch still propagates; this is not a blanket exception swallow"*) and a test pins the mechanism. An over-claim caught by its own test.

---

## 2. Target B — Error classification authority

### 2.1 Forensic inventory (complete)

Every implementation deciding an outcome class for a tool/result:

| # | Site | Mechanism | Classified as | Status |
|---|---|---|---|---|
| 1 | `transport/renderer.py::result_is_error` | dict/JSON/text markers | binary error | **now delegates** |
| 2 | `transport/cli.py::_is_error_result` | delegates to (1) | binary error | already delegated |
| 3 | `benchmark/scoring.py::_is_error_result` | `status == "error"` | binary error | **fixed in Phase 9** |
| 4 | `tool_executor.py::_record_metrics` | `'"status": "ok"' in result` | success/error | **FIXED — defect** |
| 5 | `tool_executor.py::_note_fetch_outcome` | `'"status": "error"' not in result[:200]` | success/error | **FIXED — defect** |
| 6 | `multi_agent/subagent_orchestrator.py::_is_denial` | prose markers | denial | **FIXED — defect** |
| 7 | `multi_agent/subagent_orchestrator.py::_is_transient` | transport markers (429, rate limit) | retryability | **kept — different axis** |
| 8 | `core/contracts.py::is_cancellation` | `isinstance` on exceptions | cancellation | kept — exception axis, not result |
| 9 | `core/transport.py::is_transient_status/_error` | HTTP status / exception | retryability | kept — transport axis |
| 10 | `core/events.py::AgentEvent.is_final` | event type | event terminality | kept — event axis |

**Not conflated:** (7), (8), (9), (10) answer *different questions* (retryability, exception identity, transport health, event terminality). Verified by inspecting each; the brief's warning about assuming different names mean different semantics cuts both ways.

### 2.2 Defects found

**D1 — the live metrics path counted successful calls as errors.**

```python
ok = ((isinstance(result, str) and '"status": "ok"' in result)
      or (isinstance(result, dict) and result.get("status") == "ok"))
```

Measured on a 7-case corpus: **3 diverged from the canonical classifier.** A successful plain-text result (`read_file` contents, `run_bash` output) → `ok=False` → `tool_errors_total += 1`. That deflates `(1 - errors/calls) * 100` at `wisp/metrics.py:102`. Also failed on compact JSON (`{"status":"ok"}` without spaces).

**D2 — the denial detector could not see a structured denial.**

`_DENIAL_MARKERS = ("[denied", "denied by", "approval denied", "not authorized")`. Tested against every canonical status: **all five matched nothing.**

| status | matched by the old markers |
|---|---|
| `POLICY_DENIED` | **nothing** |
| `USER_DENIED` | **nothing** |
| `APPROVAL_TIMEOUT` | **nothing** |
| `CANCELLED` | **nothing** |
| `SCHEMA_INVALID` | **nothing** |

The comment beside it reads *"denials must never auto-retry (§26)"* — the rule the detector could not enforce for structured denials.

**D3 — the fetch breaker decided on truncated text.**

`'"status": "error"' not in result_str[:200]` and `'"status": "ok"' in result_str[:300]` — whitespace- and truncation-sensitive, and blind to plain-text errors (which read as success and reset the breaker).

### 2.3 The established authority

`wisp.core.events` — the module that already owned the status taxonomy.

| Concept | Authority | Representation | Translation | Consumers | Enforcement |
|---|---|---|---|---|---|
| outcome class | `core/events.py` | `OutcomeClass` (StrEnum, 8 values) | `OUTCOME_BY_STATUS`, `_ERROR_TEXT_MARKERS` | all | **STATIC + TESTED** |
| classification | `core/events.py::classify_result` | — | `classify_status`, `classify_text` | renderer, benchmark, tool_executor | **TESTED** |
| binary view | `is_error_outcome` | — | — | renderer (`result_is_error`), tool_executor | **TESTED** |
| retry-terminal view | `is_terminal_outcome`, `TERMINAL_OUTCOME_CLASSES` | — | — | subagent orchestrator | **TESTED** |
| denial detection | `is_denial_text`, `is_denial_outcome` | — | canonical tokens **then** prose | subagent orchestrator | **TESTED** |

### 2.4 Verification of the migration

`result_is_error` was checked against its **exact former implementation** on a 29-item corpus (dicts, JSON strings, legacy markers, `None`, ints, lists, malformed JSON, whitespace): **IDENTICAL, 0 mismatches.** The taxonomy adds precision (policy_denial / timeout / cancellation become distinct) without changing the binary answer.

### 2.5 Tests preventing narrower duplicates

`tests/test_outcome_classification_authority.py` — 67 tests, including:

| Test | Proves |
|---|---|
| `test_migrated_predicate_is_equivalent_to_the_original` | 29-case behavioural equivalence |
| `test_every_denial_status_is_classified` | no status can be invisible |
| `test_structured_denial_statuses_are_detected_in_text` | the D2 regression |
| `test_a_transient_transport_error_is_not_a_denial` | the axes stay distinct |
| `test_no_module_reimplements_tool_result_status_classification` | **AST scan** for `.get("status") == "ok"/"error"` outside the authority |
| `test_the_taxonomy_is_defined_once` | `OutcomeClass`/`OUTCOME_BY_STATUS` defined exactly once |
| `test_tool_executor_metrics_delegates` / `test_fetch_breaker_delegates` | AST call-graph pins on the two migrated sites |
| `test_denial_result_builds_a_classifiable_envelope` | builder and classifier agree |

The AST scan found the fourth classifier (`_record_metrics`) that manual reading had missed — the third consecutive phase where a mechanical guard outperformed the audit.

---

## 3. Target C — REST policy boundary

### 3.1 The decision matrix

41 mutating routes. `gated` = consults `require_tool_allowed` (the tool-policy layer). `client` = called by the shipped desktop client.

| Route | Mutates | Reaches exec / alters exec config | Auth | Policy gate | Client | Required policy |
|---|---|---|---|---|---|---|
| `POST /api/bash` | yes | **executes a command** | key | **yes** | yes | ✅ gated |
| `POST /api/files` | yes | file mutation | key | **yes** | yes | ✅ gated |
| `POST /api/files/edit` | yes | file mutation | key | **yes** | — | ✅ gated |
| `POST /api/files/binary` | yes | file mutation | key | **yes** | — | ✅ gated |
| `POST /api/files/rename` | yes | file mutation | key | **yes** | — | ✅ gated |
| `DELETE /api/files` | yes | file mutation | key | **yes** | yes | ✅ gated |
| `POST /api/hooks` | yes | **writes a shell-executed hook** | key | **yes** | **yes** | ✅ gated (decision) |
| `POST /api/hooks/{name}/test` | yes | **executes a hook** | key | **yes** | — | ✅ gated (decision) |
| `POST /api/mcp/servers` | yes | **registers a spawned command** | key | **yes** | **yes** | ✅ gated (decision) |
| `POST /api/mcp/servers/{name}/test` | yes | spawns a server | key | **yes** | — | ✅ gated (decision) |
| `POST /api/plugins/install` | yes | **activates plugin code** | key | **yes** | **yes** | ✅ gated (decision) |
| `POST /api/plugins/{name}/toggle` | yes | activates/toggles code | key | **yes** | — | ✅ gated (decision) |
| `DELETE /api/mcp/servers/{name}` | yes | removes a server | key | **yes** | yes | ✅ gated (symmetry) |
| `DELETE /api/plugins/{name}` | yes | removes plugin code | key | **yes** | — | ✅ gated (symmetry) |
| `POST /api/policy/publish` | yes | governance bundle | key | no | — | ⚠️ decision |
| `POST /api/policy/revoke` | yes | governance bundle | key | no | — | ⚠️ decision |
| `POST /api/workspace` | yes | workspace root | key | no | yes | ⚠️ decision |
| `POST /api/context` | yes | persistent instructions | key | no | yes | ⚠️ decision |
| `POST /api/git/commit` | yes | commits workspace | key | no | yes | ⚠️ decision |
| `POST /api/models/select` | yes | active provider/model | key | no | — | config only |
| `DELETE /api/hooks/{name}` | yes | removes a hook | key | no | yes | config only |
| `POST /api/run/background` | yes | starts an agent run | key | no | — | config only |
| `DELETE /api/run/{run_id}` | yes | cancels a run | key | no | — | config only |
| `POST /api/swarm/run` | yes | starts a swarm | key | no | — | config only |
| `POST /api/arena/{compare,vote}` | yes | model comparison | key | no | yes | config only |
| `POST /api/codebase/index` | yes | builds an index | key | no | — | config only |
| `POST /api/complete`, `/api/diff`, `/api/edit/inline`, `/api/prompt`, `/api/search`, `/api/jsonrpc`, `/api/review/*` | yes | no | key | no | mixed | read/compute |
| `POST /api/agents/background/{id}/{cancel,send}` | yes | controls an agent | key | no | — | config only |
| `POST /api/sessions/fork`, `PATCH`/`DELETE /api/sessions/{id}` | yes | session lifecycle | key | no | yes | config only |
| `GET /api/health` | no | no | **none** | no | yes | ✅ intentional (liveness) |

**Counts:** 41 mutating · **14 gated** (was 6) · 15 client-consumed · 5 reach host execution · 6 alter executable config. **Every route that reaches host execution or alters executable configuration now carries the policy gate**; the remaining ⚠️ rows mutate state but do not run code.

### 3.2 The six questions

1. **Externally reachable?** Only loopback by default; non-loopback binds refuse to start without `WISP_API_KEY` (`server/main.py:160-197`). One route is unauthenticated by design: `GET /api/health`.
2. **Which mutate state?** 41.
3. **Which execute code or alter executable configuration?** 5 execute, 6 alter config — the ⚠️ rows.
4. **Which are intentionally unauthenticated?** `GET /api/health` only.
5. **Which authorisation model exists?** Two layers: **authentication** (`verify_api_key`, all routes except health) and the **tool-policy layer** (`require_tool_allowed`, 6 routes). The second exists to substitute for a human approver on tool-equivalent operations.
6. **Which routes does the shipped client consume?** 15, listed above — extracted from `wisp-desktop/out/renderer/assets/index-*.js`.
7. **Intended distinction between authentication, authorisation, and command validation?** Authentication is enforced. Authorisation is enforced on tool-equivalent routes. **Command validation is not implemented on the hook route** — the `command` field is `Field(..., min_length=1)` with no content check.

### 3.3 The decision — and a correction to this document

**Three routes reached host execution, were ungated, and were consumed by the shipped desktop client:**

| Route | What it enables |
|---|---|
| `POST /api/hooks` | persists a command that later runs via `subprocess.run(shell=True)` on the next tool call, **with no approval** |
| `POST /api/mcp/servers` | registers a command that is spawned |
| `POST /api/plugins/install` | activates plugin code |

#### ⚠️ Correction — an earlier draft of this section was wrong

This section originally stated that gating these routes **"breaks the shipped desktop client."** **That claim was false.** It was derived from `require_tool_allowed`'s docstring rather than from the actual policy verdicts, and it was never tested.

Measured afterwards by driving the real policy:

| action | `full` | `auto_edit` | `ask_all` | `read_only` |
|---|---|---|---|---|
| `hooks.create` | ALLOW | ALLOW | ALLOW | **DENY** |
| `mcp.add_server` | ALLOW | ALLOW | ALLOW | **DENY** |
| `plugins.install` | ALLOW | ALLOW | ALLOW | **DENY** |

The policy is **mode-based**: action names that are not defined rules default to allow, except in `read_only`. So gating these routes **does not break normal use** — it denies only `read_only`, which is precisely the intent. There was never a client-breaking cost, and option B was safe all along.

This is the fifth time in this engagement that a conclusion drawn from *reading* dissolved under *execution* (see the handoff's §10). It is recorded here rather than quietly edited because the reasoning error is more instructive than the corrected table.

#### The decision

**Option B — gate the routes, and update the client to surface the refusal reason.** Implemented:

| Change | File |
|---|---|
| Gate `create_hook` + `test_hook` | `wisp/server/routes/hooks.py` |
| Gate `add_mcp_server` + `test_mcp_server` + `delete_mcp_server` | `wisp/server/routes/mcp.py` |
| Gate `install_plugin` + `toggle_plugin` + `delete_plugin` | `wisp/server/routes/plugins.py` |
| Surface the server's `detail` on any non-OK response | `wisp-desktop/src/renderer/hooks/useApi.ts` |

**Why the siblings were included.** Gating only the create route leaves an equivalent-authority bypass: `POST /api/hooks/{name}/test` *executes* the hook, and `POST /api/mcp/servers/{name}/test` *spawns* the server. Gating only `plugins/install` leaves `toggle` to switch code on. And the teardown routes (`DELETE`) were gated for symmetry: without it, a `read_only` session could destroy executable configuration it cannot create — an asymmetry with no security benefit.

**Why the client still works.** The gate fails closed with 403 only when the session policy denies. In `full`/`auto_edit`/`ask_all` these actions are allowed, so the shipped client's normal path is unchanged. The client change is not a compatibility shim — it is an error-reporting improvement: `API 403: Forbidden` now reads `API 403: Forbidden — Blocked by server policy (…); no approver is present over REST`.

### 3.4 What Phase 10 implemented here

**An executable invariant that did not exist.** Before this phase, a route that reached host execution could be ungated as long as it was *declared* unresolved — an escape hatch. That hatch is now closed: `tests/test_rest_gate_boundary.py` asserts that **every** host-execution route carries the gate, and that `UNRESOLVED_UNGATED` stays empty.

**Tests (16 in the boundary file, 10 added to the policy-gate file):**
- the gate boundary is pinned exactly (`KNOWN_GATED`), so any drift is deliberate;
- every host-execution route is gated — not merely declared;
- the three decision-set routes are gated;
- the client dependency is recorded executably, so if it ever lapses the audit is flagged;
- **functional**: each new gate denies in `read_only` and passes in `full`, driven through the real FastAPI routes.

**One pre-existing defect found and *not* fixed** (reported, not silently patched): the desktop client's real typecheck is `tsc -b`, not the `tsc --noEmit` the `typecheck` script runs — the latter is vacuous because the root `tsconfig.json` is project-references with `files: []`. `tsc -b` is **red with 32 errors across 12 files**, all pre-existing in `HEAD`. One is functional: `useApi.ts:368` calls `makeAuthHeaders()` with no argument, so the checkpoint-diff request sends **no `Authorization` header**. Out of scope here; flagged for a decision.

### 3.5 Target C+ — the protected-path guard (found while closing R1b)

Asking whether R1b survived Target C turned up a **verified privilege-escalation
path**, and it was not in the hook route.

`.wisp/hooks/` holds commands Wisp executes itself, before later tool calls,
with the full process environment. `docs/THREAT-MODEL.md` names the guard on
that directory as the mitigation for "malicious hook persistence". The guard
was enforced on the **agent** path and absent from **REST**:

| Path | `.wisp/hooks/x.json`, mode `full` |
|---|---|
| agent tool `write_file` | refused — "hook-directory mutation refused" |
| REST `POST /api/files` | **written** |

Adding the policy gate did not close it, because the gate consults
`SecurityPolicy`, which is mode-based and scans no arguments. The **files**
route — the mirror of `write_file` — was the gap, not the hook route.

Two further defects surfaced in the same guard: `new_path` was never scanned
(renaming *into* the hook directory passed), and the agent-side check was a
bare substring test (so `.wisp/hooksfoo/` was wrongly refused).

**Root cause is the finding of this whole engagement, in its purest form: two
authorization implementations.** `auth/decision.authorize()` is a 6-layer model
with an argument scan; `infra/security.SecurityPolicy.check()` is a 4-layer
model without one. Agent uses the first, REST the second — so every
argument-level rule the first has, the second silently lacks.

**Fixed** by extracting one canonical predicate (`wisp/pathsec.is_protected_path`)
and routing all three paths through it. 26 new tests, including three
structural ones that make a second guard fail loudly.

Full record: **`PHASE_10_PROTECTED_PATH_GUARD.md`** (evidence, deliberate
deltas, remaining debt G1–G4).

**G1 was then measured** (`PHASE_10_AUTHORIZATION_PARITY.md`). The two models are
not duplicates — they are *different rule sets*, and the agent composes **both**
while REST consults **one**. That is the defect. 36 (route, mode) pairs were
compared: **9 diverge**, 3 of which were this guard. The remaining 6 are the
**approval layer, in the default `auto_edit` mode** — REST permits registering a
hook / MCP server / plugin without the approval the agent requires, while the
gate's own docstring says it should deny. Pinned by
`tests/test_authorization_parity.py`; the fix is a decision (options A/B/C in
that document), because it changes the shipped client's default-mode behaviour.

---

## 4. Target D — Core → presentation direction

### 4.1 Complete edge audit

Every `wisp/core/**` import of a presentation layer, classified:

| File:line | Target | Kind | Classification |
|---|---|---|---|
| `wisp/core/doctor.py:325` | `wisp.transport` | **deferred** (inside a function, inside `try`) | **intentional introspection** |

**Total: 1 edge.** Zero module-level. Zero type-only. Zero runtime-import inversions.

Phase 9 removed the only genuine inversion (`core/approval_gate.py` → `wisp.cli.approval`, fixed by moving the verdict types to `wisp/exceptions.py`).

### 4.2 Classification of the remaining edge

`doctor.py` inspects the renderer to check that the rendering symbols exist and that the module is mode-aware (`BoxChars`, `display_width`, `OutputMode`). That is a **diagnostics module inspecting the system**, not core logic performing presentation. It is:

- **deferred** → creates no module-load dependency;
- **guarded** by `try/except` → a missing renderer cannot break a diagnostic run;
- **isolated** to one function;
- **tested** so it cannot become module-level, cannot multiply, and cannot lose its guard.

### 4.3 Tests

`tests/test_layer_direction.py` — 11 tests: no module-level inversions; no type-only inversions; **every** edge must appear in `CLASSIFIED_EDGES`; the introspection edge stays isolated and guarded; the relocated verdict types keep their identity and semantics.

---

## 5. Before/After authority map

| Concept | Before | After | Enforcement |
|---|---|---|---|
| Outcome class | none — 3+ predicates, one blind to structured denials | `core/events.py::classify_result` + `OutcomeClass` | **TESTED + STATIC** (AST scan, equivalence corpus) |
| Denial detection | prose markers only (`_DENIAL_MARKERS`) | `is_denial_text` — taxonomy first, prose fallback | **TESTED** |
| Tool metrics success | substring `'"status": "ok"'` | `is_error_outcome` | **TESTED** (AST call-graph pin) |
| Fetch-breaker outcome | truncated substring | `is_error_outcome` | **TESTED** |
| Model listing | two implementations, no declared contract | one **semantic** contract; transport explicitly distinguished | **TESTED** (19 contract/precondition tests) |
| REST gate boundary | 6 gated / 41 mutating; the executable-config routes open | 14 gated; every host-execution and executable-config route carries the policy gate | **TESTED** (16 boundary + 10 functional gate tests) |
| REST refusal reporting | `API 403: Forbidden`, reason discarded | `describeApiError` surfaces the server's `detail` | **TESTED** by typecheck; no client unit test (see debt) |
| Core → presentation | 1 genuine inversion + 1 diagnostic | 0 inversions; 1 classified diagnostic edge | **TESTED** (11 tests) |

## 6. Boundary changes

| Boundary | Before | After | Reason |
|---|---|---|---|
| Tool-result classification | 3 independent predicates | 1 authority + delegating consumers | binary answers disagreed; one missed every structured denial |
| Provider listing | implicit, undeclared | declared semantic contract with transport deltas named | makes "one authority, distinct transport" explicit and testable |
| REST policy | 6 routes gated; executable-config routes authenticated only | 14 routes gated — the authority class is now uniform | a route that runs code should not be authorised by an API key alone when its tool-equivalent peers are not |
| Desktop client error surface | status line only | status line + server `detail` | a policy refusal must be legible to the operator, not just to a log |
| Core → presentation | 2 edges, 1 a genuine inversion | 1 edge, classified and guarded | inversion removed; the diagnostic edge documented as intentional |

## 7. Findings affected

| Finding | Status |
|---|---|
| Target A (model listing) | **PARTIALLY FIXED** — semantic authority established; transport deliberately not unified |
| Target B (error classification) | **FIXED** — plus 2 previously-unknown defects (D1 metrics, D2 denial detection, D3 fetch breaker) |
| Target C (REST policy) | **FIXED** — user chose option B; every host-execution / executable-config route gated, boundary pinned, client surfaces the refusal reason |
| Target D (core→presentation) | **FIXED** — 0 inversions; remaining edge classified |
| Phase 9 F1 (`POST /api/hooks`) | **PARTIALLY FIXED** — the route now carries the policy gate; the `command` field is still unvalidated (R1b) |
| Phase 9 F17/F18 (sandbox) | **NO CHANGE REQUIRED** — remained invalidated; not reopened |
| Phase 9 F23/F24 (git hygiene) | **NO CHANGE REQUIRED** — not performed, per Rule 1 |
| Phase 9 F8 (environment) | **NO CHANGE REQUIRED** — separated from architecture, per Rule 1 |
| D4 (client typecheck vacuous + red) | **REPORTED, NOT FIXED** — found during Target C; out of scope |
| **G0 (REST bypass of the protected-path guard)** | **FIXED** — found while closing R1b; one canonical predicate, all three paths routed through it |
| G1 (two authorization implementations remain) | **REPORTED, NOT FIXED** — the structural cause of G0; needs its own decision |
| G2 (bash verb scan is a separate mechanism) | **ACCEPTED** — a shell command's target is not determinable from its text |
| G3 (`/api/hooks` command content unvalidated) | **ACCEPTED** — unchanged from R1b; hooks exist to run arbitrary commands |
| **E (M4 governance unwired)** | **DOCUMENTED AND PINNED; the false-assurance half is FIXED** — found during G1. The CLI and the three docs no longer imply enforcement (option C). **Wiring itself remains open** (option B). |
| **F (unwired-controls inventory)** | **RE-VERIFIED AND PINNED; the two live items are FIXED** — the prior audit's list of 12 was half-remediated and never maintained: **7 wired · 1 deleted · 3 unwired · 1 unidentified**. See `PHASE_10_UNWIRED_CONTROLS_INVENTORY.md`. |

## 8. New architectural invariants

| Invariant | Documented | Tested | Static | Runtime |
|---|---|---|---|---|
| Outcome class has one authority | ✅ docstring in `events.py` | ✅ 67 tests | ✅ AST scan | — |
| No module re-derives tool-result status | ✅ | ✅ | ✅ AST | — |
| Every denial status is classified and terminal | ✅ | ✅ | ✅ | — |
| Model listing has one semantic contract | ✅ docstring at the authority | ✅ 19 tests | — | — |
| Provider transport divergence is detectable | ✅ | ✅ precondition tests | — | — |
| Core has no module-level/type-only presentation edge | ✅ | ✅ 11 tests | ✅ AST | — |
| Every core→presentation edge is classified | ✅ | ✅ | ✅ | — |
| The REST gate boundary cannot drift silently | ✅ | ✅ 16 tests | ✅ AST | — |
| Every host-execution route is accounted for | ✅ | ✅ | ✅ AST | — |
| **Every host-execution / executable-config route is gated** | ✅ §3.3 + §9 B5 | ✅ 16 + 10 functional tests | ✅ AST | ✅ 403 driven end-to-end |
| **A REST refusal names its controlling layer** | ✅ `require_tool_allowed` docstring | ✅ via the functional tests | — | ✅ `detail` surfaced to the client |
| **The protected-path guard has one authority** | ✅ `wisp/pathsec` docstring + §3.5 | ✅ 26 tests | ✅ AST comparison scan + allowlist ratchet | ✅ 403 on all three paths |
| **Every authorization path consults it** | ✅ | ✅ functional per path | ✅ `auth/` and `server/deps.py` must not compare against the fragment | — |

## 9. Boundary specification for changed subsystems

The brief requires, for every changed subsystem: inbound and outbound boundary, ownership, allowed and forbidden dependencies, and the authorisation / error / persistence boundaries. Written here rather than inferred from the code.

### B1 — Outcome classification (`wisp/core/events.py`)

| Aspect | Specification |
|---|---|
| **Inbound** | Any tool result, as `dict` (the structured envelope), `str` (JSON envelope or legacy text), or anything else (treated as success — the original predicate's behaviour). |
| **Outbound** | `OutcomeClass`; `bool` from `is_error_outcome` / `is_terminal_outcome` / `is_denial_text` / `is_denial_outcome`. |
| **Ownership** | `core/events.py` owns both the taxonomy (`DENIAL_*`) and its classification. No other module may define either. |
| **Allowed dependencies** | stdlib only. Verified: the module imports `time`, `dataclasses`, `enum`, `typing`, plus a deferred `wisp.infra.tracing`. |
| **Forbidden dependencies** | Must not import `transport/`, `cli/`, `server/`, `repl/`, `tui/`, `multi_agent/`, or `benchmark/`. Enforced by the layer-direction test. |
| **Authorisation boundary** | None — classification reads values, grants nothing. |
| **Error boundary** | Pure. Never raises on any input (verified on a corpus including `None`, `int`, `list`, malformed JSON). The one raising function, `denial_result`, validates its own `status` argument. |
| **Persistence boundary** | None. The classified values are serialized by consumers, not here. |

**Consumers and how they may interpret it:** `transport/renderer.result_is_error` (binary view, public API preserved), `tool_executor` (metrics + fetch breaker), `benchmark/scoring` (via `result_is_error`), `multi_agent/subagent_orchestrator` (denial detection). Consumers read a class; they do not re-derive one.

### B2 — Provider model listing (`wisp/provider_catalog.py`)

| Aspect | Specification |
|---|---|
| **Inbound** | `provider_name: str` (a `KNOWN_PROVIDERS` key), `cfg: WispConfig`, `force: bool`. |
| **Outbound** | `list[str]` of model ids. `[]` means **cannot verify**, never "no models". |
| **Ownership** | `provider_catalog.list_models` owns the **semantic** contract. `_list_models_impl` + `_authed_get` own the **transport**, and are private to this module. |
| **Allowed dependencies** | `wisp.config`, `wisp.provider_select` (the factory), `requests`, stdlib. |
| **Forbidden dependencies** | Must not import a **concrete** provider (`providers.ollama`, `.openai`, `.nvidia`, `.openrouter`). Enforced by test. Must not be imported by `providers/`. |
| **Authorisation boundary** | Reads credentials from `cfg`; sends them only to the provider's own endpoint. Does not decide access. |
| **Error boundary** | Network/auth failure → `[]` (absorbed by `_authed_get`). A programming error in a provider branch propagates — this is **not** a blanket swallow, and the docstring says so. |
| **Persistence boundary** | An in-process TTL cache keyed by (provider, endpoint, key fingerprint). Never written to disk; never holds a credential beyond the key fingerprint. |

### B3 — Tool metrics and fetch breaker (`wisp/tool_executor.py`)

| Aspect | Specification |
|---|---|
| **Inbound** | `func_name: str`, `duration_ms: float`, `result: str \| dict` (the raw tool result). |
| **Outbound** | `metrics.record_tool(..., success=bool)`; fetch-breaker state transitions. |
| **Ownership** | `ToolExecutor` owns both; neither is a public API. |
| **Allowed dependencies** | `wisp.core.events` (the classifier), `wisp.metrics`. |
| **Forbidden dependencies** | Must not re-derive outcome from result text — pinned by an AST scan forbidding `.get("status") == "ok"/"error"` in these functions. |
| **Authorisation boundary** | None — this runs *after* `authorize()` has already permitted the call. |
| **Error boundary** | Both are best-effort: `_record_metrics` returns early without metrics; the breaker degrades to its previous state. Neither may raise into the tool path. |
| **Persistence boundary** | In-memory only. The breaker is bounded (`_FETCH_BREAK_MAX_KEYS = 512`, TTL 300 s, purged on write). |

### B4 — Subagent denial detection (`wisp/multi_agent/subagent_orchestrator.py`)

| Aspect | Specification |
|---|---|
| **Inbound** | `error: str \| None` — free text from a subagent result. |
| **Outbound** | `bool` — is this a denial (terminal for retry). |
| **Ownership** | `core/events.is_denial_text` owns the rule; the orchestrator only consumes it. |
| **Allowed dependencies** | `wisp.core.events`. |
| **Forbidden dependencies** | Must not keep its own marker list — pinned by a test asserting `_DENIAL_MARKERS` is gone. |
| **Authorisation boundary** | **This is adjacent to one.** The decision it feeds is "do not retry an authorisation denial", which is a policy expression, not an authority grant. It cannot permit anything; it can only refuse to retry. |
| **Error boundary** | Pure and total: `None`/`""` → `False`. |
| **Persistence boundary** | None. |

**Deliberately excluded from the authority:** `_is_transient` (retryability of a *transport* failure), `core/contracts.is_cancellation` (exception identity), `core/transport.is_transient_status` (HTTP status), `AgentEvent.is_final` (event terminality). Four different axes; conflating them would be the opposite error.

### B5 — REST policy gate (`wisp/server/deps.py::require_tool_allowed`)

| Aspect | Specification |
|---|---|
| **Inbound** | `(request, action_name, args, workspace)` — the FastAPI request (for the policy source), a stable action name, the arguments, and the workspace root. |
| **Outbound** | `None` on allow; raises `HTTPException(403)` with the denying layer named in `detail`. |
| **Ownership** | `wisp/server/deps.py` owns the gate. `wisp/infra/security.py::SecurityPolicy` owns the *verdict*; `wisp/pathsec.py` owns the *protected-path* rule. The gate only adapts REST to them. |
| **Allowed dependencies** | `wisp.infra.security`, `wisp.pathsec`, `wisp.core.contracts` (`risk_for_tool`). |
| **Forbidden dependencies** | Routes must not re-derive authorisation from `verify_api_key` alone for any action that reaches host execution or alters executable config — pinned by `test_host_execution_routes_are_gated`. And the gate must not compare against the protected fragment itself — pinned by `test_authorization_modules_do_not_compare_against_the_fragment`. |
| **Authorisation boundary** | **This *is* the boundary.** Two layers compose: `verify_api_key` answers *who*, `require_tool_allowed` answers *may this session do this*. Approval-required verdicts deny, because REST has no approver. A **third, unconditional** check precedes both: the protected-path guard, which denies even in `full` mode. |
| **Error boundary** | Fail-closed and total: any policy exception denies rather than permits. The 403 names the controlling layer so the client can surface it. |
| **Persistence boundary** | None — a pure decision. |
| **Coverage invariant** | Every mutating route that reaches host execution or alters executable configuration is gated. `UNRESOLVED_UNGATED` must stay empty. |
| **Known limit** | The gate controls **who may configure** executable behaviour, not **what** that configuration contains. `POST /api/hooks` still accepts an unvalidated `command` (R1b). |

---

## 10. Remaining debt

| # | Item | Why it remains |
|---|---|---|
| **R1** | ~~The REST gate decision (Target C)~~ | **CLOSED.** The user chose option B; implemented in §3.3. The boundary is now gated and pinned. |
| **R1b** | `POST /api/hooks` still accepts an unvalidated `command` | The gate now restricts *who* may register a hook, but not *what* the hook may run. Content validation remains unapplied — it was option C, and option B was chosen instead. **Still open.** |
| R1c | 27 mutating routes remain ungated | Deliberate: they mutate state but do not reach host execution or alter executable config. The invariant covers the authority class that matters; gating session/context/config routes is a separate question. |
| R3 | Full provider-listing delegation | Unsafe until the three deltas converge; the equivalence tests will fail at that point and signal it |
| R4 | `_is_transient` remains a separate predicate | **Not debt** — a different axis (retryability, not outcome class), verified by inspection |
| R5 | Two `RunStatus` enums remain | Phase 9 ratchet; values all coerce |
| R6 | `.venv` missing 4 declared dependencies | Environment, not architecture (Rule 1) |
| R7 | `capability_filter.py` untracked but imported by tracked code | Pre-existing; guarded by a default-off flag |
| R8 | Three untracked test files abort collection | User's WIP |
| R9 | `wisp/core/graph/__init__.py` modified but uncommitted | User's pre-existing edit |
| **G1** | **Two authorization implementations remain** — `auth/decision.authorize()` (6-layer) and `infra/security.SecurityPolicy.check()` (4-layer). REST uses the weaker one. | **Measured and pinned** (`PHASE_10_AUTHORIZATION_PARITY.md`). They are not duplicates but *different rule sets*, and the agent composes **both** while REST consults **one** — which is what makes it a defect. 9 of 36 (route, mode) pairs diverge; 6 remain, all the approval layer, in the **default** `auto_edit` mode. Ratcheted by `tests/test_authorization_parity.py`. **One decision open** (options A/B/C in that document). |
| G2 | The `run_bash` verb scan in `tools/_utils` is a separate mechanism from the predicate | Defensible — a shell command's target is not determinable from its text. Now defence-in-depth rather than the only guard. |
| G3 | `POST /api/hooks` command content still unvalidated | Unchanged from R1b. Hooks exist precisely to run arbitrary commands; the gate controls *who*, not *what*. |
| G4 | The protected-fragment allowlist has six entries | Three are path *constructors* rather than guards. The ratchet makes any seventh a deliberate decision. |
| R10 | Desktop client `tsc -b` is red (32 pre-existing errors) | Client work; one is functional (`useApi.ts:368` sends no auth header) |
| **E** | **The M4 organization policy layer is not wired to the runtime** — `wisp/policy/` is never loaded; `ToolExecutor.policy` is `None` at both construction sites; `config.py` has no policy setting; `app.state.policy_pubkey` is set only by tests | **The false-assurance half is FIXED** — a `NOT ENFORCED` notice now precedes every evaluating `wisp policy` command, `explain_denial` states a rule rather than a result, and `README`/`AGENTS.md`/`docs/SECURITY.md` carry qualifiers. **Wiring remains OPEN** — it depends on the **key-distribution ceremony** the M4 spec deferred (§5), so it is a decision. Pinned by `tests/test_m4_governance_wiring.py` (25 tests, incl. a tripwire). See `PHASE_10_M4_GOVERNANCE_UNWIRED.md`. |
| **F1** | ~~`metadata["_budget"]` is write-only~~ | ✅ **FIXED** — the runner now applies a DAG node's declared budget narrow-only (`_runner._budget_from_contract`). This completes `docs/audit-2026-08-24.md` item 11, whose other two prescribed fixes (blocked descendants, dependency injection) had already landed. `max_tool_calls` had no other source: the contract has no such field. |
| **F2** | ~~`_SENSITIVE_ENV_KEYS` has no consumer~~ | ✅ **FIXED** — deleted as a superseded duplicate. The live scrubbers are `_utils_env.py`'s `scrub_sensitive_env` (allow-list), `credential_free_env` (deny-list + pattern), and `minimal_process_env`; the pattern covers every key the static list named. |
| **F3** | `execute_tool(security_policy=...)` — no caller passes it; `ToolRegistry.execute` is production-unused and lacks truncation/security | **ACCEPTED (low)** — the executor authorises per call; these are defence-in-depth that never runs. **Worth annotating** so nobody wires them without the missing checks |
| **F4** | `spawn_with_guards` is a dead duplicate (its guards are live at `:723`/`:1737`) | **ACCEPTED** — deletion candidate, not a defect |
| **F5** | event-replay `TOOL_CALL` — the prior audit's referent could not be identified; both candidates are wired | **UNRESOLVED (no action)** — recorded as unidentified rather than guessed at |

## 11. Final gate — the four questions

> **Where is the authority? Who may interpret it? What prevents a second one? What test fails if someone creates one?**

| Area | Authority | Who may interpret | What prevents a second | What test fails |
|---|---|---|---|---|
| **Model listing** | `provider_catalog.list_models` (semantic) | any consumer of the contract; transport is private to the catalog | declared contract + confinement test + precondition tests that fire when the deltas converge | `test_provider_specific_transport_is_confined_to_the_catalog`, `test_catalog_does_not_import_concrete_providers`, `test_the_contract_is_declared_where_the_authority_lives` |
| **Error classification** | `core/events.py` (`OutcomeClass`, `classify_result`) | all consumers, by delegation only | **AST scan** forbidding `.get("status") == "ok"/"error"` outside the authority; single-definition pin | `test_no_module_reimplements_tool_result_status_classification`, `test_the_taxonomy_is_defined_once` |
| **REST policy** | `require_tool_allowed` at the route (the tool-policy layer) | every mutating route that reaches host execution or alters executable config | `KNOWN_GATED` exact-set pin; an empty `UNRESOLVED_UNGATED`; the host-execution ⇒ gated invariant | `test_the_gate_boundary_is_unchanged`, `test_host_execution_routes_are_gated`, `test_no_host_execution_route_remains_unresolved`, `test_the_decision_set_is_gated` |
| **Core → presentation** | no presentation dependency exists except one classified diagnostic edge | core may introspect, never depend | AST scan forbidding module-level and type-only edges; every edge must be classified | `test_core_has_no_module_level_imports_from_presentation`, `test_core_has_no_type_only_presentation_edges`, `test_every_core_to_presentation_edge_is_classified` |
| **Protected paths** (`C+`) | `pathsec.is_protected_path` | every authorization path — agent, tools, REST | **AST comparison scan** over `auth/` and `server/deps.py`; single-definition pin; identity check on the tools alias; mention ratchet | `test_authorization_modules_do_not_compare_against_the_fragment`, `test_fragment_list_has_one_definition`, `test_utils_alias_is_the_canonical_object`, `test_no_unexpected_module_mentions_the_fragment` |

**All five areas are now closed.**

REST policy was the one open question at the end of the first pass. It is closed not by inventing a policy but by the user making the product decision — **gate the executable-config routes, and update the client to surface the refusal** — and by that decision being encoded as an executable invariant rather than prose. The escape hatch (a host-execution route may be ungated if *declared* unresolved) is removed: `UNRESOLVED_UNGATED` must stay empty.

The correction in §3.3 matters to this closure. The area was held open partly on a claim — *"gating breaks the shipped client"* — that turned out to be false. A decision made on a false cost estimate is not a decision; it is a guess with a rationale. The real cost was zero for every mode except `read_only`, which is the intended behaviour.

**One area was not on the original list at all.** The protected-path guard (§3.5) was
found by asking whether R1b survived Target C — and the answer was that a *different*
route, `/api/files`, bypassed the guard entirely. It is included here because the
same four questions apply and now have answers. Its structural cause (G1, two
authorization implementations) remains open and is the most valuable thing left
to fix.

*Phase 10 audit complete. Implementation record in `PHASE_10_AUTHORITY_CLOSURE_IMPLEMENTATION.md`. Guard record in `PHASE_10_PROTECTED_PATH_GUARD.md`.*
