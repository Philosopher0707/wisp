# CONTEXT.md — Session Handoff

**Project:** `/Users/philosopher/iCloud Drive (Archive)/Documents/wisp` — "Wisp", a local-first Python CLI coding agent with an enterprise governance layer.
**Session span:** 2026-09-18 → 2026-09-22
**Purpose of this file:** hand off a long, multi-phase architectural engagement so a fresh context can continue without re-deriving anything.

> **The project moved.** It now lives under iCloud (`/Users/philosopher/iCloud Drive (Archive)/Documents/wisp`),
> not `~/Documents/wisp`. The `.venv` editable-install finder was repaired for the move, but its
> `MAPPING` still resolves `wisp` to the iCloud path — which is why `PYTHONPATH` **cannot** override
> which package is imported (see §6).

---

## 0. STATUS — Persistent Graph Loop migration: **the plan is fully traversed**

**HEAD is `2307925`** · branch `main`. The baseline is Phase 10's `83b10af`; §3 lists every commit
on top of it and is the authority for the count — **F87**: it did not, until 2026-09-25. **Nine**
"point the handoff" commits had never been listed, so the claim and the table disagreed; backfilled.
The only exception is the handoff commit that carries *this* line, which the next landing lists.
**`2307925` is corpus integrity III** — **no new ADR**. 2.1 **CLOSED** (**F89**'s message; the trailing
`or 0.0` was **unreachable**; 36/36 expiry inputs identical in outcome). 2.2: the dependency table is
consolidated as **§6.1**, but both offline installs **still fail** — **OPEN**. 2.3: §10's class gains
sub-cases **5** and **6**. 2.4: ADR-0057's residual 1 becomes §12 row **W1** (**F100**). Found **F97**
(three stale ADR-range claims) and **F99** (the brief's ledger rows do not exist). See
`PHASE_CORPUS_INTEGRITY_III.md`.
**`f35188a` is ADR-0059** — the REST gate now consults the M2 authority for its **denial** verdict.
It closes a divergence **ADR-0058 created**: ADR-0055's *0 path divergences of 36* was measured with
**no policy loaded** (**F94**), and once a bundle denies `write_file`/`edit_file`/`run_bash`, **11 of 36
(route, mode) pairs diverge** — five in the **default** mode. The brief's Option **D** is **refuted by
measurement** (**F95**). Conditional on a bundle, so unset means today's gate **byte-for-byte** (40/40,
status *and* detail). The §4 pin is inverted; the non-vacuity probe found a defect in the guard itself
(**F96**). See `PHASE_REST_AUTHORIZATION_COMPOSITION.md`.
**`aa9d8a8` is the M4 wiring** — the policy layer is **WIRED**, behind `WISP_POLICY_BUNDLE`, default
**OFF**. `config.py` reads two string settings; `composition.py` loads via `load_organization_policy`
and passes `policy=` at the single construction site (ADR-0006); a named-but-unverifiable bundle
**refuses to boot**. **§6's step 3 (REST) is deliberately NOT done** — `SecurityPolicy` has no
organization slot, so a bundle there would be dead data; pinned so it cannot drift. Four tripwires
inverted; **two more fired for the wrong reason and were repaired** (**F92** — the nuisance class).
The canonical block gains three files and is re-measured: **1437** (**F93**). See `PHASE_M4_WIRING.md`.
**`86ab7cb` is the key-trust decision** — **ADR-0058**, the brief's "0059" (**F90**: the previous
mission produced no ADR, so 0058 was free). The model is the **operator-supplied organization public
key**; **absence is a configuration, invalidity is a refusal**. **The brief's worked example was wrong
and the error decided the workflow** (**F91**): `WISP_POLICY_PUBKEY` is base64 **key material**, not a
path. Found **F88** (the M4 policy suite is red here — `cryptography` absent) and **F89** (a bundle
omitting `expires_at` raises `min() iterable argument is empty`). See `PHASE_KEY_TRUST_WORKFLOW.md`.
**`08dd57e` is corpus integrity II** — **no new ADR**. 2.1 **CLOSED** (the M4 guard's count is 3; the
third site is `wisp/benchmark/runner.py`, added by `8a7e9ab` and **authorised** by ADR-0045's F54 fix —
the brief and `PHASE_CORPUS_INTEGRITY.md` §2.1 both said `acp_session.py`/`cef3e90`, wrong on both
counts). 2.2 **OPEN** (`httpx` re-attempted, still absent from the cache and the host). See
`PHASE_CORPUS_INTEGRITY_II.md`.
**`6ec0f48` is the outcome-classification fix** — the migration's **own rule was being violated in a
production file**, and the guard that catches it had been RED since `ade4dc6`, invisible because its
block aborts at collection. `wisp/core/stateless.py`'s two direct `.get("status") == "ok"` comparisons
now delegate to `core.events.is_error_outcome`; 35-case differential on the production function
identical. **No ADR** — the rule is unchanged and the code is brought into compliance. The corpus's
"contract update" classification was **F86** (right for the M4 guard, wrong for this one). Found
**F85** (a count is canonical only if measured after the LAST change to any member). See
`PHASE_OUTCOME_CLASSIFICATION_VIOLATION.md`.
**`aa47ae0` is the corpus-integrity pass** — **no new ADR**. 2.1 **OPEN** (`httpx` is absent from the
cache *and from this host*, so the protected-path guard cannot be collected; the corpus's counts are
corrected instead — and the measurement found the block aborts on **two** files, not one, and that **two
Phase-10 guards are red at HEAD**). 2.2–2.4 closed. See `PHASE_CORPUS_INTEGRITY.md`.
**`e639115` is the REST-approval landing** — **ADR-0057**: a REST request for an executable-config action
asks a human over the WebSocket channel, in the **clients'** vocabulary (so the client change is **zero**);
**no client ⇒ 403**. Flag `WISP_REST_APPROVAL`, default **OFF**. Found **F83** (the channel's question
direction had never reached a client) and **F84**. See §0.0.16 below.
**`9d56aec` is the objective-path flag-composition landing** — **ADR-0056**: the two criteria flags are
**independent**, and the one interaction is **derivation order** (a declaration pre-empts `strict`, which
is then recorded and inert). **No production change** — the measured behaviour already is the decision.
Found **F82**. See §0.0.15 below.
**`7bb8f8a` is the authorization-parity landing** — **ADR-0055**: the REST gate is **at parity with the
agent path**; the recorded divergence was between two **models**, not two **paths** (0 path divergences
of 36). **Option A** — accept, and correct the record. **No production behaviour moves.** Found
**F78–F81**. See §0.0.14 below.
**`17130c7` is the pin-guard fix** (Deliverable 2) — the guard now asserts the pinned **content**, not
that a line exists; it found **nine** stale pins the old check had passed, and `CONTEXT.md` §10's sixth
instrument-defect instance is **CLOSED**. See `PHASE_GATE_ENABLEMENT_DECISION.md` §11.
**`74ac43b` is the acceptance-gate-enablement landing** — **ADR-0054**: the gate consumes
`verdict_keys_on_declared` at the engine's pre-`done` gate, bounded and **defaulting OFF** (ADR-0051
R2–R6's contract is **not** satisfied: 1 capable model of 13). See §0.0.13 below.
**`5898e0e` names the instrument-defect class once** (documentation only). See §10.
**`805eca8` is the F8-published-status landing** — **ADR-0052**: a capability failure is published as a
failure of the **host**, not a denial; the denial taxonomy and the prompt are **unchanged**. See §0.0.11
below.
**`1e83e34` is the M8 DAG-retirement landing** — `multi_agent/dag.py`'s ownership is declared and its
retirement is **decided as `DEPRECATE`**, blocked on a measured semantic divergence. **Prose-only** in
`wisp/` (docstring-stripped AST byte-identical). See §0.0.10 below.
**`3990313` is the gate-enablement landing** — 6 files, covering **ADR-0051** (the acceptance gate's
enablement contract is a **non-redundancy precondition**, not a rate) and its measurement. **No
production change** — no flag is added, nothing is enabled. See §0.0.9 below.
**`3f9e639` is the structured-criteria landing** — 7 files, +1,332/−30, covering **ADR-0050** (the
objective may declare its criteria; the host validates and **rejects rather than reinterprets**) and its
implementation. Flag `WISP_CRITERIA_STRUCTURED_DECLARATION`, default **OFF**. See §0.0.8 below.
**`3298894` is the precedence-correction landing** — 5 files, +873/−27, covering **ADR-0049**: the
canonical precedence table is `goal.PRECEDENCE` (eight rows, 0–7), older numbering is historical and is
resolved **by content**. **A record update — no code, no behaviour change**; the 48-combination
differential is identical before and after. See §0.0.7 below.
**`d7a55c2` is the F8 error-classification landing** — 6 files, +883/−11, covering F8's second half
(a missing validator is a system failure, not a schema verdict). See §0.0.5 below.
**`7023af0` is the criteria-derivation landing** — 6 files, +1,347/−11, covering **ADR-0048** (the
authority over *"does this objective require a green suite?"*). See §0.0.4 below.
**`802413a` is the current-authorities landing** — 3 files, +687, adding `CURRENT_AUTHORITIES.md`
and its guard. See §0.0.5 below.
**`b9af5f0` is the multi-turn productive recovery landing** — 12 files, +2,208/−93, covering
ADR-0047 and closing **F60** and **F61** (and fixing **F62**/**F63**). It excludes the user's
pre-existing WIP, listed in §8. See §0.0.2 below.
**`8a7e9ab` is the autonomous-convergence landing** — 21 files, +8,173/−14, covering ADR-0045
(the objective-level convergence loop) and ADR-0046 (progress-aware recovery). It excludes the
user's pre-existing WIP, listed in §8. See §0.0.0 and §0.0.1 below.
**`ade4dc6` is the POST-M13 landing** — 108 files, +26,811/−397, covering ADR-0034 … ADR-0044
(M13, the POST-M13 chain, F8/F37, and the provider-event chain through ADR-0044). It excludes the
user's pre-existing WIP, listed in §8.
**The canonical suite is 1453 tests — 1452 pass, 1 fails.** The failure is
`test_node_identity.py::TestANodeReferencesItsWorkUnit::test_a_parallel_round_is_journaled_as_one_exchange_per_call`,
finding **F38**: a test that had encoded the F8 environment as the contract (see §11).
**The full-suite failure set of 129 is STALE.** It was measured while `jsonschema` was missing, so it
conflated F8's effects with everything else — at least 24 of its entries were F8-caused. Re-measure before
quoting it; §11 says how.

| Phase | Status | Report |
|---|---|---|
| P0 — wire the orphaned durable layer | `COMPLETE` | `PHASE_P0_REPORT.md` |
| P1 — journal turn transitions | `COMPLETE` | `PHASE_P1_REPORT.md` |
| P2 — the proposal boundary | `COMPLETE` | `PHASE_P2_REPORT.md` |
| P3 — independent verification | `COMPLETE` — **stage 3a only** | `PHASE_P3_REPORT.md` |
| P4 — task graph from durable state | `COMPLETE` (item 5 deferred) | `PHASE_P4_REPORT.md` |
| P5 — runtime graph mutation | `COMPLETE` (item 5 deferred) | `PHASE_P5_REPORT.md` |
| P6 — recovery ladder | `COMPLETE` (live-loop wiring deferred) | `PHASE_P6_REPORT.md` |
| P7 — stagnation detection | `COMPLETE` (live-loop wiring deferred) | `PHASE_P7_REPORT.md` |
| P8 — context as a first-class subsystem | **`PARTIAL`** — trust boundary complete; items 3–6 deferred | `PHASE_P8_REPORT.md` |
| P9 — structured delegation | **`PARTIAL`** — two wiring fixes; five structural items deferred | `PHASE_P9_REPORT.md` |
| **M2** — journal-first reconstruction | `COMPLETE` (consumer adoption asserted) | `PHASE_M2_REPORT.md` |
| **M3** — killpoint integration | `COMPLETE` (one window) | `PHASE_M3_REPORT.md` |
| **M4** — ADR-0004 revisited | `COMPLETE` — **found a live defect** | `PHASE_M4_REPORT.md` |
| **M16** — the escalation is state, not audit | `COMPLETE` — **found a live read-side defect** | `PHASE_M16_REPORT.md` |
| **M9** — the execution view | `COMPLETE` — **the claim was the wrong target** | `PHASE_M9_REPORT.md` |
| **M15** — a subagent authorizes as a narrowed child | `COMPLETE` — **the obvious fix was a pool leak** | `PHASE_M15_REPORT.md` |
| **M14** — prompt sections classified (T1) | `COMPLETE` — **found a live T1 violation** | `PHASE_M14_REPORT.md` |
| **M12** — the failure path reaches the taxonomy | `COMPLETE` — **found the engine's refusals invisible** | `PHASE_M12_REPORT.md` |
| **M11** — a node references its work unit | `COMPLETE` — **the guard for the property forbade the fix** | `PHASE_M11_REPORT.md` |
| **M13** — the stagnation detector on the live loop | `COMPLETE` — **the signal declared every session stagnant** | `PHASE_M13_REPORT.md` |
| **POST-M13** — the authority recon | `COMPLETE` | `PHASE_POST_M13_AUTHORITY_RECON.md` |
| **POST-M13** — the verdict contract (**ADR-0035**) | `COMPLETE` | `PHASE_POST_M13_VERDICT_CONTRACT.md` |
| **POST-M13** — the authority ADR | `COMPLETE` | `PHASE_POST_M13_AUTHORITY_ADR.md` |
| **POST-M13** — the authority implementation | `COMPLETE` | `PHASE_POST_M13_AUTHORITY_IMPLEMENTATION.md` |
| **POST-M13** — the live completion seam recon | `COMPLETE` | `PHASE_POST_M13_LIVE_COMPLETION_SEAM_RECON.md` |
| **POST-M13** — completion enforcement policy (**ADR-0036**) | `COMPLETE` | `PHASE_POST_M13_COMPLETION_ENFORCEMENT_POLICY_ADR.md` |
| **POST-M13** — completion enforcement implementation | `COMPLETE` — **fixed F35** | `PHASE_POST_M13_COMPLETION_ENFORCEMENT_IMPLEMENTATION.md` |
| **POST-M13** — stagnation reopening recon | `COMPLETE` | `PHASE_POST_M13_STAGNATION_REOPENING_RECON.md` |
| **POST-M13** — stagnation latch amendment (**ADR-0037**) | `COMPLETE` — **completes ADR-0036** | `PHASE_POST_M13_STAGNATION_LATCH_ADR_AMENDMENT.md` |
| **POST-M13** — stagnation docstring alignment | `COMPLETE` — docstrings only | `PHASE_POST_M13_STAGNATION_DOC_ALIGNMENT.md` |
| **POST-M13** — stagnation gate validation | `COMPLETE` — **`ENABLEMENT_NOT_READY`** | `PHASE_POST_M13_STAGNATION_GATE_VALIDATION.md` |
| **POST-M13** — F8 tool-validation authority recon | `COMPLETE` — **`ADR_REQUIRED: NO`** | `PHASE_POST_M13_F8_TOOL_VALIDATION_AUTHORITY_RECON.md` |
| **POST-M13** — **F8 provisioning; tools really execute** | `COMPLETE` — **found F37, F38** | `PHASE_POST_M13_F8_PROVISIONING_AND_TOOL_EXECUTION_RESTORATION.md` |
| **POST-M13** — F37 verification-evidence authority recon | `COMPLETE` — **root-caused to one expression** | `PHASE_POST_M13_VERIFICATION_EVIDENCE_AUTHORITY_RECON.md` |
| **POST-M13** — **F37 evidence-adapter repair** | `COMPLETE` — **F37 FIXED**; 3 false-success shapes closed | `PHASE_POST_M13_VERIFICATION_EVIDENCE_ADAPTER_REPAIR.md` |
| **POST-M13** — ADR-0016 live-provider measurement | `COMPLETE` — **`NOT_YET_DETERMINABLE`**; found **F39, F40** | `PHASE_POST-M13_ADR-0016_LIVE_PROVIDER_MEASUREMENT.md` |
| **POST-M13** — F39 `num_predict` forensic recon | `COMPLETE` — **`REQUIRES ARCHITECTURE DECISION`** | `PHASE_POST-M13_F39_OLLAMA_NUM_PREDICT_FORENSIC_RECON.md` |
| **POST-M13** — F39 token-budget boundary (**ADR-0038**) | `COMPLETE` — **`RATIFIED`**; no behavioural change | `PHASE_POST-M13_F39_TOKEN_BUDGET_BOUNDARY_ADR.md` |
| **POST-M13** — **F39 mechanical diagnostic** | `COMPLETE` — **`ADR-0038 SATISFIED`**; found **F41** | `PHASE_POST-M13_F39_MECHANICAL_DIAGNOSTIC_IMPLEMENTATION.md` |
| **POST-M13** — **F40 typed-event forensic recon** | `COMPLETE` — **`F40_CONFIRMED_PRODUCTION_DEFECT`**; `ADR_REQUIRED: YES`; 2-part mechanism, 2 sibling sites | `PHASE_POST-M13_F40_ITERATION_WRAPUP_TYPED_EVENT_FORENSIC_RECON.md` |
| **POST-M13** — **F40 provider event contract (ADR-0039)** | `COMPLETE` — **`RATIFIED`**; one canonicalization owner; found **F42** | `PHASE_POST-M13_F40_PROVIDER_EVENT_CONTRACT_ADR.md` |
| **POST-M13** — **ADR-0039 normalization implementation** | `COMPLETE` — **F40-1…F40-4, F42 CLOSED**; found **F43, F44** | `PHASE_POST-M13_F40_PROVIDER_EVENT_NORMALIZATION_IMPLEMENTATION.md` |
| **POST-M13** — **canonicalization ownership (ADR-0040)** | `COMPLETE` — **`RATIFIED` (Option B)**; authority = `events.canonical_event`; 0 code changes | `PHASE_POST-M13_F40_CANONICALIZATION_OWNERSHIP_RECONCILIATION.md` |
| **POST-M13** — **F43/F44 convergence (ADR-0041/0042)** | `COMPLETE` — **F43 CLOSED**, **F44 SETTLED**; recovery classification is semantic + terminal-first | `PHASE_POST-M13_F43_F44_RECOVERY_COMPLETION_CONVERGENCE.md` |
| **POST-M13** — **final execution-semantics closure (ADR-0043/0044)** | `COMPLETE` — **`EXECUTION SEMANTICS: CLOSED`**; the last vocabulary list and the duplicated turn predicate removed | `PHASE_POST-M13_FINAL_EXECUTION_SEMANTICS_CLOSURE.md` |
| **NEXT** — autonomous coding agent convergence (**ADR-0045**) | `COMPLETE` — **`CONVERGENT`**; found **F49–F54** | `PHASE_NEXT_AUTONOMOUS_CODING_AGENT_CONVERGENCE.md` |
| **NEXT** — live recovery-to-success validation | `COMPLETE` — **`NOT DEMONSTRATED`**; found **F55–F58** | `PHASE_LIVE_RECOVERY_TO_SUCCESS_VALIDATION.md` |
| **NEXT** — progress-aware recovery (**ADR-0046**) | `COMPLETE` — **`DEMONSTRATED`**; found **F59** (fixed), **F60/F61** (open) | `PHASE_PROGRESS_AWARE_RECOVERY.md` |
| **NEXT** — multi-turn productive recovery (**ADR-0047**) | `COMPLETE` — **`F60 DEMONSTRATED`**, **`F61 DEMONSTRATED`**, **`CONVERGENCE AUTHORITY: CLOSED`**; **F60/F61 FIXED**, found **F62/F63** (fixed) | `PHASE_MULTI_TURN_PRODUCTIVE_RECOVERY.md` |
| **NEXT** — current authorities (documentation) | `COMPLETE` — `CURRENT_AUTHORITIES.md` + its guard; found **F64/F65** (the arbitration drift, the row-numbering ambiguity) | `PHASE_CURRENT_AUTHORITIES.md` |
| **NEXT** — criteria derivation authority (**ADR-0048**) | `COMPLETE` — **`RATIFIED`**; found **F66** (three measured failure modes, MODE A a false `GOAL_MET`), **F67** (the wiring had no test) | `PHASE_CRITERIA_DERIVATION_AUTHORITY.md` |
| **NEXT** — F8 error classification | `COMPLETE` — **`F8 SECONDARY_DEFECT REMOVED`**; found **F68** (inert retry), **F69** (two classifications for one condition) | `PHASE_F8_ERROR_CLASSIFICATION.md` |
| **NEXT** — precedence correction (**ADR-0049**) | `COMPLETE` — **`RECORD UPDATE`**; F-1/F-2 from the authorities page **DECIDED**; found the brief's **inverted numbering claim** | `PHASE_PRECEDENCE_CORRECTION.md` |
| **NEXT** — structured criteria (**ADR-0050**) | `COMPLETE` — **`RATIFIED + IMPLEMENTED`**; found **F72** (the error rate is a function of the workspace, not the objective) and **F73** (an over-broad tripwire) | `PHASE_STRUCTURED_CRITERIA.md` |
| gate enablement (**ADR-0051**) | `COMPLETE` — **`DECIDED`**; the gate has nothing to gate on; found **F75** (an uncommittable instrument), **F76** | `PHASE_GATE_ENABLEMENT.md` |
| M8 DAG retirement | `COMPLETE` — **`DEPRECATE` decided**, blocked on a measured semantic divergence; found **F77** | `PHASE_DAG_RETIREMENT.md` |
| F8 published status (**ADR-0052**) | `COMPLETE` — a host failure is not a denial | `PHASE_F8_PUBLISHED_STATUS.md` |
| criteria source on the turn path (**ADR-0053**) | `COMPLETE` — ADR-0051 R1 satisfied; found the pin guard's blank-line weakness (the 6th instrument defect) | `PHASE_CRITERIA_SOURCE.md` |
| acceptance gate enablement (**ADR-0054**) | `PARTIAL` — the mechanism is built and driven; the population is short by one capable model | `PHASE_GATE_ENABLEMENT_DECISION.md` |
| authorization parity (**ADR-0055**) | `COMPLETE` — **`DECIDED`**, Option A; found **F78** (a model is not a path), **F79**, **F80**, **F81** | `PHASE_AUTHORIZATION_PARITY.md` |
| objective-path flag composition (**ADR-0056**) | `COMPLETE` — **`DECIDED`**, independent; the interaction is derivation order | `PHASE_OBJECTIVE_FLAG_COMPOSITION.md` |
| REST approval through the WebSocket channel (**ADR-0057**) | `COMPLETE` — **`DECIDED`**, the clients' frame; no client ⇒ deny; flag default OFF; found **F83** (the channel was half-wired) | `PHASE_REST_APPROVAL.md` |
| corpus integrity pass | `COMPLETE` — 2.1 **OPEN** (no `httpx` anywhere on the host), 2.2–2.4 closed | `PHASE_CORPUS_INTEGRITY.md` |
| outcome-classification violation | `COMPLETE` — the **code** was wrong, the guard was right; a mechanical fix (**no ADR**); found **F85**, **F86** | `PHASE_OUTCOME_CLASSIFICATION_VIOLATION.md` |
| corpus integrity II | `COMPLETE` — 2.1 **CLOSED** (count 3, third site authorised by ADR-0045/F54), 2.2 **OPEN** (`httpx`) | `PHASE_CORPUS_INTEGRITY_II.md` |
| key-trust workflow (**ADR-0058**) | `COMPLETE` — **`DECIDED`**; the operator's public key; absence is a config, invalidity a refusal; found **F88** (the M4 policy suite is red here), **F89**, **F90**, **F91** | `PHASE_KEY_TRUST_WORKFLOW.md` |
| M4 wiring | `COMPLETE` — the layer is **WIRED**, default OFF; §6 step 3 (REST) deliberately not done; 4 tripwires inverted, **2 repaired** (**F92**); found **F93** | `PHASE_M4_WIRING.md` |
| REST authorization composition (**ADR-0059**) | `COMPLETE` — **`DECIDED + IMPLEMENTED`**; the gate consults `authorize()` for its **denial** verdict, conditional on a bundle; found **F94/F95/F96/F98/F99** | `PHASE_REST_AUTHORIZATION_COMPOSITION.md` |
| corpus integrity III | `COMPLETE` — 2.1/2.3/2.4 **closed**, 2.2's installs **OPEN**; found **F97**, **F100** | `PHASE_CORPUS_INTEGRITY_III.md` |

**Ledger:** `WISP_MIGRATION_STATUS.md` (phase ledger, findings **F1–F63**, change log — **mind the §0/§23 split**, and note it has **no G1 or governance-layer row**: `CONTEXT.md` §12 is the live open-items table, **F99**).
**Decisions:** `WISP_ARCHITECTURE_DECISIONS.md` (**ADR-0001 … ADR-0059**).

### 0.0.16 REST APPROVAL THROUGH THE WEBSOCKET CHANNEL (2026-09-25) — ADR-0057

**The brief's premise, driven, was half false — and the false half decided the shape.** The brief (and
ADR-0055 §Context) said the WebSocket channel *"already implements bidirectional approval flow"*. Reading
it, as the brief required:

| direction | server | both shipped clients | match |
|---|---|---|---|
| server → client | `approval_request` · `{approval_id, tool_call}` | `tool_approval_request` · `{call_id, name, arguments, reason}` | **NO** |
| client → server | `tool_approval` · `{approved}` | `tool_approval` · `{id, approved}` | yes |

**The answer direction works; the question direction never reached a client** — so the *agent* path's WS
approval prompt has never rendered, and `approve()` always timed out to deny. **F83**, F78's shape one
level down.

**The decision.** Adopt the **clients'** vocabulary (`tool_approval_request`, correlated on `call_id`) —
which makes the **client change zero**. A new `wisp/server/approval_bridge.py` owns its **own**
correlation map, so `WebSocketTransport`, `approve()` and the agent path are untouched. Trigger:
`{hooks.create, mcp.add_server, plugins.install}` in `{auto_edit, ask_all}` (`full` does not ask;
`read_only` denies outright). **No client ⇒ 403** — holding would hang and falling through would silently
allow, both forbidden. Timeout `REST_APPROVAL_TIMEOUT_S = 30.0`. Policy first, then ask, via a new
**async** companion so `require_tool_allowed` keeps its signature. **G3 deferred.** Flag
`rest_approval` / `WISP_REST_APPROVAL`, default **OFF** — with it off every caller sees today's code.
Guard `tests/reliability/test_rest_approval.py` (18 tests, **5/5** probes caught). **No client change, no
gate-chain change.** Residuals: the agent path's dead frame (a real fix, its own ADR), `resolve_approval`
ignoring `id` on the transport path, G3, and multi-client routing.

### 0.0.15 THE OBJECTIVE-PATH FLAG COMPOSITION (2026-09-25) — ADR-0056

**The last open ADR-0050 follow-up, decided.** ADR-0050 asked whether
`WISP_CRITERIA_STRICT_DERIVATION` and `WISP_CRITERIA_STRUCTURED_DECLARATION` compose on the objective
path. ADR-0053 R8 had answered the same question for the **turn** path — independent — because coupling
would create *two read sites for one concern*. **That reason does not transfer:** the objective path
reads both flags at **one** composition point (`autonomous.py:307-308`), as two parameters of
`explain_acceptance`.

**Driven, not read** (the 2×2 matrix on this repository's own MODE A objective, red baseline):

| objective | `strict` | `decl` | reason | `undetermined` | criteria |
|---|---|---|---|---|---|
| no declaration | T | F | `undetermined` | `['verify:cmd0']` | 4 |
| no declaration | T | T | `undetermined` | `['verify:cmd0']` | 4 |
| declared | F | T | `declared` | `[]` | 1 |
| declared | T | T | `declared` | `[]` | 1 |

**Independent.** `strict` **alone** withholds (rows 1–2 identical) — so the "dependent" reading is
**measured false**, and making `strict` conditional would silently disable ADR-0048's fix for the measured
false `GOAL_MET`. A declaration **pre-empts** `strict` (rows 3–4 identical): the early return at
`convergence.py:1029-1041` means `strict` is *recorded and inert*. **That interaction is derivation
order, not flag coupling.** The "composed" reading (a declaration required) is rejected — a policy no
measurement supports, and **not implemented**: `use_declaration=True` with no block is a no-op. No
parameter is added to `explain_acceptance` (ADR-0009). **No production change.** Guard:
`tests/reliability/test_objective_flag_composition.py` (7 tests, 4/4 non-vacuity probes caught).
Residual: `CriteriaDerivation.strict` records `True` when pre-empted.

### 0.0.14 AUTHORIZATION PARITY (2026-09-25) — ADR-0055: the divergence was between two models, not two paths

**G1 closed as a decision, and its three-phase-old claim was false.** `PHASE_10_AUTHORIZATION_PARITY.md`
concluded *"out of the box, REST permits registering a hook, an MCP server, or a plugin **without the
approval the agent path requires for the same operation**."* That is a claim about **paths**, derived
from a comparison of two **models** — the ratchet compares `authorize()` to `SecurityPolicy` and calls the
first "the agent's verdict".

**Driven** (`scripts/authorization_parity_measurement.py`, committed): the agent
(`ToolExecutor.execute` with `approval_handler=None` — REST's own condition) and
`require_tool_allowed` agree on **every route in every mode**: **0 path divergences of 36**, **6 model
divergences of 36**.

**Why the claim fails.** `hooks.create`, `mcp.add_server`, `plugins.install` are **REST-only action
names** — not in `TOOL_IMPLS` (42), not plugin tools, and with **no `TOOL_RISK_TABLE` row** (41 rows).
`risk_for_tool` returns `EXEC` only because it **fail-closes on an unknown name**; driving
`ToolExecutor.execute` with them answers `Unknown tool`. **The agent has no operation for them.** And the
agent's approval model is not `authorize().approval_required` at all — `ToolExecutor.execute` forks on
`not _decision.allowed` alone (`:755`) and takes its approval set from `_get_write_tools` (`:827`) plus
`_needs_forced_approval` (`:1324`). The discarded field **is** consumed, by the *direct registry* path
(`tools/registry.py:951`). **Three approval models; the ratchet compared two of them.**

**Option A — accept, and correct the record.** **B is rejected because it is not a parity fix**: its only
effect is to deny those three names in `auto_edit`/`ask_all` — modes where the agent denies *nothing* —
i.e. REST **stricter than the agent**, a divergence created by a change meant to remove one; it would
also 403 the shipped client on three config routes whose own comments state the intended design. **C** is
the right fix for a *different* problem (REST cannot ask a human) and is its own ADR. The docstring is
qualified (`+13/−0`, pure), the ratchet keeps all seven properties and **gains a real-path parity
guard** (25 tests, 4/4 probes caught). Four residuals named. **No production behaviour moves.**

**Findings:** **F78** a model is not a path; **F79** a check that passes by finding nothing (the ratchet
had seven properties and *none* compared the two paths it was named for; its "agent consults both" test
was a string scan over a Python tree); **F80** `tests/test_protected_path_guard.py` **cannot run in this
environment** (`fastapi.testclient` needs `httpx`, not installed) — Phase 10's 26-test guard for the
adjacent finding is un-runnable here; **F81** `CURRENT_AUTHORITIES.md` §4 carried a superseded
disposition (`NOT_YET_DETERMINABLE`), corrected.

### 0.0.13 ACCEPTANCE GATE ENABLEMENT (2026-09-25) — ADR-0054

ADR-0053 satisfied ADR-0051 R1's precondition and left one thing open (§10): *"`verdict_keys_on_declared`
is a predicate, and nothing calls it in production … ADR-0051's enablement decision is what would consume
it."* **ADR-0054 is that consumer.**

**The question, answered.** ADR-0053 §9's residual 2 said a declared failure is *recorded*, not repaired,
because the verdict is computed *after* `done`. A gate acting after `done` withholds nothing — so
enforcement had to mean asking **before** `done`. **It does:** the engine's pre-`done` gate asks a
**read-only callable** the runtime builds (`turn_criteria.DeclaredCriteriaGate`), receiving no criteria,
no specs and no probe — the ADR-0036 shape. The probe is taken **at the gate**, because that is the moment
the workspace is final, and it is **cached** so the verdict site reuses it: **one probe per declared
turn**. Withholding reuses ADR-0036's bound and **shares the turn's extension budget**, then surrenders
honestly — so the withheld turn still ends with `done` and `turn_succeeded` stays a projection of terminal
evidence. **The gate is an extension, not a success signal.**

**The flag.** `acceptance_gate` / `WISP_ACCEPTANCE_GATE`, default **OFF**, read once, **dependent** on
`turn_criteria_source` (with the source off there are no declared criteria, so the gate would withhold on
a verdict the record does not carry).

**The measurement: the contract is NOT satisfied, and that is measured.** ADR-0051 R4 requires **≥ 2
capable models**. `scripts/acceptance_gate_population.py` enumerates all **13** models the local daemon
serves: **capable 1** (`nemotron-3-ultra:cloud`) · **degenerate 2** · **retired 5** · **paywalled 5** ·
unclassified 0. So the `GOAL_MET` rate ADR-0051 R2 specifies is **not produced** — a rate over one model
is the model-dependent artefact ADR-0051 §Problem already measured. **What would produce it:** a second
capable model that is neither retired nor paywalled, or a provider key.

**What IS produced — the mechanism, driven end to end through a real turn:** a failing declaration with
the gate ON withholds `done` **exactly twice** (the shared budget), emits the `[DECLARED CRITERIA]` nudge,
**then finishes**; a satisfied declaration withholds nothing; the flag OFF withholds nothing; the gate is
**inert** without the criteria source.

**The ADR-0051 tripwire fired and was replaced by its inverse.** `test_gate_enablement_contract.py`'s
`TestNoUnwiredAcceptanceGateFlagWasAdded` asserted the flag was *absent* — and failed when it was added,
exactly as its docstring predicted. Rewritten, not weakened: it now defends that the flag exists,
**defaults OFF**, is read once, and is dependent.

**`CURRENT_AUTHORITIES.md` was re-pinned — and the pin guard's weakness recurred.** It caught **one** of
**five** stale pins again (the same defect ADR-0053 §6 recorded, one iteration later), so four were again
found by reading. That recurrence is **Deliverable 2's** subject.

**Produced:** ADR-0054 + its index row; `DeclaredCriteriaGate` / `declared_criteria_gate` /
`compose_declared_nudge` in `turn_criteria.py`; the `declared_gate` parameter and gate block in
`stateless.py`; the flag and wiring in `runtime.py` + `config.py`;
`scripts/acceptance_gate_population.py` (committed); `tests/reliability/test_acceptance_gate_enablement.py`
(25 tests, 3/3 non-vacuity probes caught); `PHASE_GATE_ENABLEMENT_DECISION.md`.

### 0.0.12 THE CRITERIA SOURCE ON THE TURN PATH (2026-09-25) — ADR-0053

ADR-0051 R1's precondition was *"the turn path's required-criteria set contains at least one required
criterion not derivable from `VerificationFloorGuard`'s own state"*, and ADR-0051 **measured** that it did
not — the turn's verdict was `floor_guard_verdict(guard)`, a pure projection, `verdict == FAIL` agreeing
with `guard.rejection()` **192/192** times. ADR-0051 §"what this does not decide" named the fix as its own
ADR. **ADR-0053 is that ADR.**

**The mechanism.** `wisp/core/turn_criteria.py` — the **one** producer of the turn's criteria set:

```
required = floor_guard_criteria(guard) ∪ explain_acceptance(prompt, ws, use_declaration=True).criteria
evidence = floor_guard_evidence(guard) + CommandProbe(declaration.specs).measure(ws)
verdict  = acceptance.evaluate(...)      <- unchanged
```

The declared half uses the **same derivation the objective-level loop uses**, so the two levels cannot
disagree about what a declaration means. Re-implements nothing.

**The gate's condition (R4)** — `verdict == FAIL` **and every criterion it names is non-floor**. Driven
over six cases (`scripts/turn_criteria_measurement.py`), **two discriminate**:

| case | OFF | ON | gate? |
|---|---|---|---|
| plain, no mutation | `inconclusive`/`goal_unverified` | same | False |
| plain, verified mutation | `pass`/`goal_met` | same | False |
| declared symbol **present**, no mutation | `inconclusive` | `inconclusive` | False |
| **declared symbol ABSENT, no mutation** | `inconclusive` | **`fail`/`goal_failed`** | **True** |
| **declared symbol ABSENT, verified mutation** | **`pass`/`goal_met`** | **`fail`/`goal_failed`** | **True** |
| declared symbol ABSENT, FAILED verification | `fail` | `fail` | **False** |

**Row 5 is the point.** A mutation-verified turn satisfies the floor guard *completely* — `rejection()`
is `None`, floor-only `PASS`/`GOAL_MET` — while the declared criterion fails. **Row 6 is the control**:
a `FAIL` the floor guard already enforces returns `False`, so a gate keyed on R4 cannot duplicate
`rejection()`.

**The routing (R5)** — through **neither** the `done` gate nor the ladder. The verdict is computed *after*
the engine emitted `done`, so ADR-0036's withholding gate cannot act on it; the consumption point is
`derive_goal_state`, so a declared failure lands `GOAL_FAILED` (row 3) where a floor-only verdict landed
`GOAL_UNVERIFIED` or `GOAL_MET`.

**The flag (R7/R8)** — `turn_criteria_source` / `WISP_TURN_CRITERIA_SOURCE`, default **OFF**, read once at
`run_turn`'s entry, **deliberately independent** of `WISP_CRITERIA_STRUCTURED_DECLARATION` (which gates
the objective-level derivation; coupling them would put two read sites on one concern). This answers
ADR-0050's follow-up question 1 **for the turn path**.

**A rejected declaration propagates** (R6 — ADR-0050 R4's loud rule); the parse and probe sit **outside**
the `except Exception` that guards "verdict unavailable", so a malformed head block cannot become a silent
floor-only run.

**This does not enable the acceptance gate.** `acceptance_gate` / `WISP_ACCEPTANCE_GATE` is still **not
added**; R1 is a *precondition*, and ADR-0051's R2–R6 measurement contract still gates enablement — it
needs a **declared turn population**, which does not exist. **M1's ledger state is `BLOCKED`** with that
reason recorded. *(ADR-0051's Consequence used to coin `BLOCKED_ON_PRECONDITION`; that is a **reason**, not
a state, and it is corrected in place — `WISP_MIGRATION_STATUS.md:41`'s vocabulary governs.)*

**`CURRENT_AUTHORITIES.md` was re-pinned** (`runtime.py` moved; its guard caught it). **Finding — the pin
guard checks for a BLANK line, not the right line:** it caught **one** of **five** stale pins and passed
four that had moved onto non-blank lines. The **sixth instance** of the instrument-defect class (§10);
reported, not repaired — widening that guard is its own change.

**Produced:** ADR-0053 + its index row; `wisp/core/turn_criteria.py`; the flag in `config.py`; the verdict
site in `runtime.py`; `scripts/turn_criteria_measurement.py` (committed); `tests/reliability/test_criteria_source_on_turn_path.py`
(25 tests, 3/3 non-vacuity probes caught); `PHASE_CRITERIA_SOURCE.md`.

### 0.0.11 F8 PUBLISHED STATUS (2026-09-25) — ADR-0052: a host failure is not a denial

`PHASE_F8_ERROR_CLASSIFICATION.md` §4 fixed the attribution **where the failure is produced**
(`ValidationFailure.kind == CAPABILITY_MISSING`) and named what it did not fix: the **published** status
was still `SCHEMA_INVALID` — a claim about the *arguments*, made about a host that never examined them.
It enumerated the six surfaces and said the choice needed an ADR. **ADR-0052 is that ADR.**

**The decision — Option B, a system-failure envelope.** A capability failure is published with status
`error`, carrying its `kind` in the envelope's `data` (plus
`capability: "tool_argument_validation"`), so a caller at the published boundary distinguishes a system
failure from a data failure **without parsing prose**. `authorized` is `None` — no decision was reached.

**Why not Option A (a new denial status).** It is additive but it extends a taxonomy the M12 classifier
consumes **and** requires the prompt's DENIALS-ARE-FINAL list to name the member — **a behavioural
change** needing the flag-and-measure treatment. It would also need a new `OutcomeClass`, because none
of the eight means *"the host is broken"*. **Why not the third option** (keep `SCHEMA_INVALID`, add a
note): that is the shape F8 *was*.

**The blast radius is two of the six surfaces, and neither is a published vocabulary.** `_DENIAL_STATUSES`,
`OUTCOME_BY_STATUS`, `TERMINAL_OUTCOME_CLASSES`, `recovery.py`'s `DENIAL_SCHEMA_INVALID` and the
prompt's final list are **all unchanged** — each asserted by a test.

**What landed:** `capability_failure_result()` + `CAPABILITY_FAILURE_STATUS` / `CAPABILITY_KIND_KEY`
(`core/events.py`); `_is_capability_failure()` and both dry-run stamping sites plus
`_refusal_result_event`'s capability branch (`core/stateless.py`); the audit line now records the
failure's `kind` rather than a denial status that never applied.

**Test:** `tests/reliability/test_f8_published_status.py` (15 tests, 4 classes, whole path: the produced
failure → the stamping rule → the published envelope → `classify_result`). **3/3 non-vacuity probes
caught.** **NV1 did not falsify on the first attempt** — the helper took a `capability: bool` argument
and set the flag itself, so breaking the predicate changed nothing; the test was not testing what it
claimed. Fixed by applying the **real** predicate; the defect is recorded in the helper's docstring.

**Residual, named:** the M12 classifier now sees no *denial* for a capability failure, so such a turn is
classified as an ordinary error (`FailureClass.IMPLEMENTATION` by ADR-0032's precedence). That is the
honest classification — nothing was denied.

### 0.0.10 DAG RETIREMENT (2026-09-25) — M8 surveyed and decided: **deprecate**, not remove

M8's premise was that `multi_agent/dag.py` is *"a duplicate of `wisp/graph/`'s executor — a strictly
weaker one"*. **Driven, the premise is half right, and the half that is wrong is the half that decides
the scope.**

**The duplicate is real.** All twelve behaviours `dag.py` provides have a `wisp/graph/` equivalent, and
the graph's is equal or superior in eleven — it adds durable runs, audit, artifacts, retries, joins,
bounded cycles, resource policy, and a structural + contract + governance + resource + security
validator. The two also **agree** on cycle detection and unknown-dependency detection.

**But the two disagree on what a valid DAG *is*.** Driven over a 9-case corpus:

| Case | `TaskDAG.validate()` | `validate_graph` |
|---|---|---|
| `single` / `chain` / `diamond` | valid | valid |
| `cycle2` / `self_cycle` / `cycle3` | INVALID | INVALID |
| `unknown_dep` | INVALID | INVALID |
| **`empty`** | **valid** | **INVALID** — *"graph has no nodes"* |
| **`disconnected`** (two independent roots) | **valid** | **INVALID** — *"unreachable nodes from 'a': b"* |

`wisp/graph/` requires a single reachable entrypoint; `TaskDAG` is a general partial order. A
disconnected DAG is a **legitimate `orchestrate_dag` input**. So a re-point is not a refactor — it would
**reject inputs the live tool accepts today**, and `GraphExecutor.run` would additionally create a
durable run per call and enforce a workspace-containment rule. **Choosing which definition wins is its
own decision**, and the brief forbids taking it silently inside a retirement.

**Two further measured facts.** `TaskDAG.validate()` **mis-reports an unknown dependency as a cycle**
(the unknown dep inflates the in-degree count and can never be dequeued) — a `DEFECT-PIN`, not repaired.
And `wisp/graph/compat.py::dag_to_graph` — the intended lowering — has **no production caller** (tests
only): another written-but-unwired instance.

**What landed:** `dag.py`'s module docstring declares the ownership boundary and names the blocker — a
**PROSE-ONLY** change (docstring-stripped AST byte-identical to HEAD's) — plus
`tests/reliability/test_dag_retirement_contract.py` (**10 tests**, 4 classes: the shared property, the
measured divergences, the defect pin, and **three tripwires on the residual**), the `AGENTS.md` module-map
row, and the `WISP_MIGRATION_STATUS.md` §6.4/item-5/M8 updates.

**The residual is open and tripwired, not closed.** The tripwires fail the moment either the divergence
closes or the execution path is re-pointed.

**Instrument defect, recorded:** the guard's `dag_to_graph`-caller check was written as a **bare string
scan** and failed on its first run — because this deliverable's own docstring *names* the symbol. It is
now `ast`-based (only an import or a call counts). *A string scan over a Python tree reads docstrings as
code.*

### 0.0.9 GATE ENABLEMENT (2026-09-25) — ADR-0051, decided; the gate has nothing to gate on

ADR-0016 staged P3 as **3a** (record the verdict) and **3b** (enable the gate *"after a measurement
period showing how many turns become `INCONCLUSIVE`"*). 3b has been open for the whole migration.
**ADR-0051 amends 3b's condition** — it does not supersede ADR-0016, whose staging and whose rejection
of both pre-existing vocabularies still stand.

**The measurement that reframes the question.** The turn path's acceptance verdict is **not an
independent evaluation**:

```
runtime.py:1189-1190   _acceptance = floor_guard_verdict(guard).verdict
verification.py:236    floor_guard_criteria()   <- the ONE producer on the turn path
verification.py:252    check = (not guard.wrote_code) or guard.resolved()
stateless.py:911       guard.rejection()        <- ALREADY wired at the pre-`done` gate
```

Driven over the **192-state** guard space (`scripts/gate_enablement_measurement.py`):

| Claim | Measured |
|---|---|
| `verdict == FAIL` ⟺ the guard's own blocking condition | **0 disagreements / 192** |
| `verdict != PASS` while the guard is *not* blocking | **144** — **96** with the guard *disabled*, **48** read-only turns |
| `verdict == FAIL` after the guard already surrendered | **8** |

So a gate keyed on this verdict is **redundant** (FAIL-keyed: the identical condition and nudge),
**harmful** (non-PASS-keyed: withholds `done` on every read-only turn and on every turn of a *disabled*
guard), or a **second budget** (the 8 surrendered states). **The gate has nothing to gate on.**

**The `INCONCLUSIVE` rate is not the measure, for two independent reasons.** It is **model-dependent**
(re-derived from the primary records: `nemotron-3-ultra:cloud` 9/14 = **64.3%**, `llama3.2:3b` 7/7 =
100%, `qwen2.5:0.5b` 7/7 = 100%; pooled 82.1%, which describes no population that exists) — and it is
**not a function of the gate**, which consumes the verdict and adds rounds, so enabling it *cannot raise*
the rate. The measure that can move is the `GOAL_MET` rate on a **declared**-objective population, with
the false-completion rate pinned at 0.

**The decision (R1–R9).** R1: a **non-redundancy precondition** — the turn path's required-criteria set
must contain ≥1 criterion not derivable from `VerificationFloorGuard`. R2: the measure is changed to the
declared-objective `GOAL_MET` rate. R3: the population must be declared and **never pooled**. R4: ≥2
capable models, ≥10 declared objectives, ≥30 turns, 0 excluded. R5: `FALSE_SUCCESS_AFTER = 0`. R6: replay
100%. R7: `acceptance_gate` / `WISP_ACCEPTANCE_GATE`, default **OFF**, read once — **and not added to
`config.py` while R1 is unmet**. R8: the three non-violations (`turn_succeeded`, `VerificationFloorGuard`,
`goal.PRECEDENCE`), asserted by a guard. R9: rollback.

**What is produced:** ADR-0051 + its index row; `scripts/gate_enablement_measurement.py` +
`scripts/gate_enablement_population.json` (**committed**, so the measurement re-runs from the repo);
`tests/reliability/test_gate_enablement_contract.py` (12 tests, 4/4 non-vacuity probes caught);
`PHASE_GATE_ENABLEMENT.md`.

**Finding F75 — the previous mission's instrument was never committed.** `PHASE_STRUCTURED_CRITERIA.md`
§7 says *"the instrument is committed so it can be re-run."* **It is not:** `.gitignore:98` excludes
`.workbuddy-ai/`, and `git ls-files .workbuddy-ai/` returns **0**. The ADR-0050 corpus exists only in the
agent workspace. This deliverable commits its own instrument, and the general lesson is recorded: **an
instrument that cannot be committed is not a re-runnable measurement.** (The `scripts/next_*.py`
instruments *are* tracked — `scripts/` is the committed convention.)

**No production change.** No flag is added, nothing is enabled, and the three authorities R8 names are
untouched.

### 0.0.8 STRUCTURED CRITERIA (2026-09-25) — ADR-0050, decided and implemented

ADR-0048 R6 decided the objective-declared structured path was **viable** and fixed its boundary, then
deferred *"the grammar, the validation surface, and the rejection behaviour"*. **ADR-0050 decides and
implements them.**

**The decision.** The declaration is a fenced block **at the head** of the objective, opened by
`--- criteria ---` and closed by `--- /criteria ---`, with one YAML-shaped line per criterion
(`<kind>: <spec>`), `<kind>` one of exactly two — `command_succeeds` or `symbol_defined`. The host
validates each spec against the measurable surface; a declaration naming an unmeasurable spec is
**rejected** with `CriteriaDeclarationRejected`, surfaced to the caller and **journalled**, and it **does
not fall back** to the prose grammar. `WISP_CRITERIA_STRUCTURED_DECLARATION` gates the path and defaults
**OFF**.

| Rule | Substance |
|---|---|
| R1 placement | at the **head** — a block quoted mid-prose is not a declaration |
| R2 grammar | closed: two kinds; comments and blanks allowed; an unknown kind and a malformed line are **rejected**, not skipped |
| R3 validation | `argv[0]` must resolve (workspace-relative executable, or on `PATH`); `<path>::<symbol>` must be **inside the workspace**, exist, and be an identifier. **Runnability, never outcome** |
| R4 rejection | loud, journalled, and the derivation **stops** — five shapes: unterminated, empty, unknown-kind, malformed-line, unmeasurable-spec |
| R5 precedence | `DECLARED` is a fourth derivation outcome and **precedes** the inference |
| R6 no model channel | the objective comes from the **caller**; the host validates; the harness measures — structural, not promised |
| R7 optional | no block → today's behaviour |
| R8 flag | `WISP_CRITERIA_STRUCTURED_DECLARATION`, default **OFF**, read once at the composition point |

A **hand-rolled** parser, not `pyyaml` — which **is** declared and available, so this is a choice: a
general YAML parser accepts sequences, maps, anchors and non-string scalars, and **every shape it accepts
is a shape the host must then interpret.**

**What closes, and what does not.** MODE A and MODE B both close **on the declared path only**. MODE A:
`DECLARED` makes the suite criterion **required**, so a no-op cannot pass. MODE B: closed **structurally**,
because the prose is not read at all. An objective that declares nothing keeps today's behaviour and
today's defects — **the declaration does not make the classifier better, it makes the *objective*
complete.** Both `DEFECT-PIN` classes still pass, docstrings updated to record the closure and point at the
declared-path tests; **not deleted**.

**The corpus, and the numbers ADR-0048's reversal condition needed.** Collected **by AST** — every string
literal the repo passes as an objective to the three call names, every module-level `*_OBJECTIVE`
constant, plus the benchmark prompts. **13 distinct objectives, 11 hand-labelled decidable**, measured on a
**red** baseline (the only baseline on which the promotion decision exists):

| | |
|---|---|
| required when it should be | **4** |
| required when it should **not** (MODE B) | **1** |
| **not** required when it should be (MODE A) | **1** |
| correctly not required | **5** |
| **accuracy** | **9/11 = 82%** |

**F72 — the error rate is not a single number.** The classifier's answer is a function of
`(objective, workspace)`, not of the objective alone: a symbol criterion is derivable only when the named
file **exists**, so three objectives move `UNDETERMINED` → `UNSTATED` against their own fixture workspace,
where `UNSTATED` is the **correct** answer. **Corrected for each objective's own workspace: 12/14 = 86%.**
ADR-0048's reversal condition is therefore **under-specified** — *"determinable from its own words"* is not
a property of the words.

**F73 — an over-broad tripwire, fixed.** The ADR-0045 R1 no-model-channel tripwire pinned parameter set
**equality**, so adding `use_declaration: bool` tripped it — though a boolean switch cannot carry criteria.
Rewritten as an allow-list, a probe showed a harmless `verbose: bool` **also** tripped it, which is worse
(it trains the reader to extend the list unthinkingly). Rewritten **again** to test the two things that
make a channel: a criteria-holding **annotation**, and a channel-shaped **name on a non-scalar**. T1/T2
caught, T3 green.

**Also found and fixed in-phase:** the declared-path test's red baseline was keyed `verify:cmd0` while a
declared spec's id is `declared:cmd0` — so `base is None` made the criterion required *regardless of the
promotion*, and the assertion was **vacuous**. Found by a probe, not by reading. In production the baseline
**does** cover the id, so the mutation would have reopened MODE A with a green suite.

**Report:** `PHASE_STRUCTURED_CRITERIA.md`. **Guard:** `tests/reliability/test_structured_criteria.py` (53),
which drives the real `converge_on_objective` rather than only the parser.

### 0.0.7 PRECEDENCE CORRECTION (2026-09-25) — ADR-0049

The two findings `CURRENT_AUTHORITIES.md` §5 recorded but was not authorised to decide. Both are now
**DECIDED**, as a **record update: no code, no behaviour change**.

**ADR-0049 R1** makes `goal.PRECEDENCE` (`wisp/core/goal.py:108-117`, **eight rows 0–7**) the
**canonical precedence table**, and declares ADR-0035 §Precedence's rows 0–6 and ADR-0047's renumbering
**historical**: any "row N" in either is resolved against the canonical table **by content**, not by
number. The table is restated in full in the ADR, verbatim, so a reader needs no other document.

**R2** establishes that `terminal_outcome == INCOMPLETE` is **not a row condition anywhere** — an
`INCOMPLETE` turn is routed by its verdict — and **ratifies** the two cells this moved
(`INCOMPLETE`+`PASS` → `GOAL_MET`, both `turn_succeeded` states) on ADR-0047's own principle: the
objective's evidence decides where it is decisive. **R3** **scopes** ADR-0047 R3 rather than changing it:
*"a fatal error must outrank stagnation"* holds for a fatal error **with no `PASS`** (the case R3's own
test exercises); a fatal error **with** a `PASS` is not fatal, so `fatal`+`PASS`+`stagnating` →
`GOAL_STAGNATED` is correct. **ADR-0047 is not reopened.**

**The brief's pinned claim about the numbering was INVERTED, and is corrected.** It said ADR-0035's row 4
and ADR-0047 R1's "row 4" name the same row by content (stagnation), that the canonical table expresses
it as row 4, and that ADR-0047's revised row 4 is canonical row 5. **Driven** over all 48 combinations:

| "row 4" in | Condition | Resolves to |
|---|---|---|
| ADR-0035 §Precedence | `may_report_goal_met() is False` (**stagnation**) | canonical row **5** |
| ADR-0047 R1 | *"fatal terminal error, and no P3 PASS"* (**the fatal clause**) | canonical row **4** |

Different content, and the mapping is the **reverse** of the claim — which matters because R1 of this very
ADR is the rule that resolves "row N". Had it been adopted, R1 would have mapped the fatal clause to
stagnation. The brief's R2 was also imprecise: it said `INCOMPLETE` is "handled by row 6"; only the `PASS`
case is (row 7 for `INCONCLUSIVE`/absent, row 3 for `FAIL`, row 5 for stagnation), so R2 states the
routing as a table.

**The driven mapping** (not read): ADR-0035's row 3 spans canonical 3, 4, 5, 6; row 4 → 5; row 5 → 6, 7;
row 6 → 6. And **ADR-0035's table is silent for 3 of 48 combinations** — F-2's non-totality, quantified
for the first time.

**No behaviour change, evidenced:** the 48-combination differential of `derive_goal_state` is
**identical before and after** the ADR —
`bb8b54e638a0d1304c5200ad28a0b2ab0bf9616baa1482646ddbbb0d37f1a139` (census: `goal_failed` 20,
`goal_met` 6, `goal_stagnated` 14, `goal_unverified` 8) — and `wisp/core/goal.py` is byte-identical.

**Guard:** `tests/reliability/test_precedence_canonical.py` (27) parses the ADR's restated table out of
the markdown and compares it to `goal.PRECEDENCE` row for row, so neither can drift from the other.
**Two probes did not falsify and are the finding:** one because a helper returned the whole ADR section
rather than the named subsection, one because a `+` regex group made an emptied disposition skip the
finding and the loop pass vacuously. Both were a check that can pass by finding nothing.

**A guard that had pinned the OLD state was rewritten, not weakened**:
`test_the_page_records_its_unpinnable_claims` asserted the literal phrase *"could not pin"* — the page's
state before this ADR — so it went red when the page was correctly regenerated. It now asserts the
property: every finding in §5 is marked `DECIDED` (with an ADR cited) or `OPEN`, and §5 does not
re-argue a decision.

**Report:** `PHASE_PRECEDENCE_CORRECTION.md`.

### 0.0.6 CRITERIA AUTHORITY & CORPUS NAVIGABILITY (2026-09-25) — three deliverables

**1. `CURRENT_AUTHORITIES.md` — the current-state-of-the-authorities page (no decision).**
The corpus is 48 ADRs; the *reasoning* is in them and the *answers* are scattered across six or more, so
anyone implementing the next decision re-derives the current state from ADR-0035 → 0036 → 0037 → 0042 →
0044 → 0047 — and that reconstruction is where drift re-enters. This is the one hazard the append-only
convention cannot protect against. One page (162 lines): six authorities, each with **owner / cannot-decide
/ current-ADRs / durable record fields**, 37 code pins plus an ADR section for every claim; the run-level
aggregation stated once; the precedence matrix as rows 0–7. Declared **regenerated, not edited**, and
**guarded** by `tests/reliability/test_current_authorities_pins.py` (28) — a page that can rot silently is
worse than no page.

**Two claims could not be pinned, and are findings rather than assertions:**

- **F64 — the arbitration drifted further than ADR-0047 states.** R1 says *"Only one input combination
  moved"*. A differential against `b9af5f0^` over all 48 input combinations finds **7 cells changed in three
  semantic classes**: the documented `fatal`+`PASS` → `GOAL_MET` and the documented `turn_succeeded` demotion
  — **plus two undocumented ones**: **`INCOMPLETE`+`PASS` moved `GOAL_UNVERIFIED` → `GOAL_MET`** (reachable:
  a turn that exhausts its budget without emitting `done` while the harness measures every criterion met now
  reports met), and **`fatal`+`PASS`+`stagnating` moved `GOAL_FAILED` → `GOAL_STAGNATED`** (the cell ADR-0047
  R3's *"a fatal error must outrank stagnation"* does not cover, because R3's rule is stated without a
  `PASS`). Not decided — the page states the code's behaviour and points at the finding.
- **F65 — the precedence table is total only in the code.** ADR-0035's arbiter has rows **0–6** and no
  fall-through, so `SUCCEEDED` with no verdict matches none of them; the implementation has always had a
  row 7. ADR-0047 revised the table **without restating it** and renumbered (*"Row 4 is now…"*) against a
  numbering that exists only in `goal.PRECEDENCE` — so **"row N" is ambiguous between two ADRs and the
  code**, and the brief for this deliverable inherited the ambiguity. No ADR contradicts another; this is an
  amendment whose numbering was not restated.

**2. ADR-0048 — the criteria-derivation authority (a decision).** ADR-0047 R5 made the acceptance criteria
the **sole gate** on `GOAL_MET`, and the input to that gate is *"does this objective's prose imply a green
suite?"* — answered by three regexes with no negation awareness, no confidence signal, and (before this)
**no record of what they concluded**. Measured, that mitigation does not close the class:

| Mode | Shape | Measured |
|---|---|---|
| **A** | **a false `GOAL_MET`** — the repo's **own benchmark task** `FIX_BUG` (*"Fix the bug in totals.py"*) matches no suite word, so on a red baseline the absolute criterion is advisory and **a no-op satisfies the guards** | `pass` → **`goal_met`**, bug unfixed, suite still failing |
| **B** | **a false exhaustion** — `_WANTS_FIX_RE` has **no negation awareness**, so *"**Do not** make the tests pass"* matches | `fail` → `goal_failed` on an objective that never asked |
| **C** | **"I cannot tell"** — no toolchain, no criteria | `inconclusive`/`NO_REQUIRED_CRITERIA` → `goal_unverified` (**already honest**) |

**A and C are opposite outcomes from the same input state, and both cannot be right.** The corpus had
already decided which: ADR-0035 invariant 1, ADR-0042 and ADR-0045 R4 all say `INCONCLUSIVE` must not be
collapsed. **C is the established behaviour; A is the collapse.**

**ADR-0048 R1–R7:** the objective is the authority over what is required; the host owns the derivation and
the validation, never the invention. Silence is not consent — the host may infer *"no regression"* and may
not infer *"green"*. Three outcomes (`STATED`/`UNSTATED`/`UNDETERMINED`); `UNDETERMINED` **never promotes**
and, where its absolute criterion is **advisory**, contributes a required **unevidenceable** criterion that
`evaluate`'s *existing* rule 3 turns into `INCONCLUSIVE` — no new verdict vocabulary, no new rule, no `FAIL`.
The derivation is **recorded and journalled** (`{"kind": "derivation"}`) so a promotion that cannot cite the
objective's own words is visible. `derive_acceptance`'s signature is **not widened** (ADR-0009) and
`criteria_for` is untouched. `WISP_CRITERIA_STRICT_DERIVATION` defaults **OFF** (read once, at the
composition point). The objective-declared structured path is **decided viable** with its R1 boundary named
— *not* the model writing the exam — and **not implemented**. **R7: negation is not handled**; the record
makes it visible, and the grammar was not widened again because widening it is the mitigation this phase
measures as insufficient.

**3. F8's second half.** `_validate_tool_args` wrapped `import jsonschema` and `jsonschema.validate` in one
`try`, so a missing validator and a genuine rejection produced the same string and the same denial status.
Fixed by **one added `except ImportError` clause, placed first** (`ModuleNotFoundError` is an `ImportError`;
`ValidationError` is not), returning a `ValidationFailure` — a `str` subclass carrying
`.kind ∈ {SCHEMA_INVALID, CAPABILITY_MISSING}`. The `write_file` retry block's own import was a **second
door** into the same defect and is closed by the same clause. **The published denial *status* is not
changed** — that needs a new member in a taxonomy consumed by the M12 classifier and quoted to the model,
so it is an ADR (the surfaces are enumerated in the report). **The attribution is now correct where the
failure is produced and still incorrect where it is published.**

**Report:** `PHASE_CURRENT_AUTHORITIES.md`, `PHASE_CRITERIA_DERIVATION_AUTHORITY.md`,
`PHASE_F8_ERROR_CLASSIFICATION.md`.

### 0.0.0 NEXT — autonomous coding agent convergence (2026-09-25)
The execution-semantics layer was CLOSED (ADR-0043/0044) but **per-turn**. A survey of the
live code found the objective level empty: every `run_turn` caller dispatches exactly one
turn and returns; `acceptance.evaluate` had no criteria producer for a user objective;
`RecoveryLadder` was called only to *record*; `PlanStore` was write-only (`_build_system_prompt`
never passes `plan=`); `core/task_graph` is *"RECORDED, not enforced"*; `graph/executor.py`
is reachable only from the interactive REPL.

**Added — the loop, and nothing that re-implements an existing authority:**

| File | What it is |
|---|---|
| `wisp/core/convergence.py` | `ConvergenceController` — the objective-level loop. Derives acceptance, measures with a harness probe, evaluates via `acceptance.evaluate`, derives state via `goal.derive_goal_state`, classifies via `recovery.classify_failure*`, and lets `RecoveryLadder.decide` choose a structurally-new rung. Bounded, journaled, resumable. |
| `wisp/autonomous.py` | The wiring: `observe_turn` (delegates the turn predicate to `terminal_outcome_from_evidence`), `compose_attempt_prompt`, workspace fingerprinting, `converge_on_objective`. |
| `wisp/autonomous_cli.py` + `wisp converge` | The CLI face. Exit code 0 only when the goal was met. |
| `scripts/next_bench.py`, `scripts/next_converge_bench.py` | Benchmarks: one turn per task, and the same tasks *through the loop*. |

**ADR-0045** ratifies it (R1–R12). Tests: `tests/reliability/test_next_convergence_controller.py`
(29) and `test_next_autonomous_wiring.py` (14).

**Report:** `PHASE_NEXT_AUTONOMOUS_CODING_AGENT_CONVERGENCE.md`.

### 0.0.2 MULTI-TURN PRODUCTIVE RECOVERY (2026-09-25)

Two questions left open by ADR-0045/0046 turned out to be **the same mistake in two places**: a
fact about the *attempt* used as a fact about the *objective*.

**F60 — completion authority.** `derive_goal_state` row 3 read *"P3 FAIL **or fatal terminal
error**"*, so a turn timeout outranked an independent acceptance `PASS` and a repository that
satisfied every criterion was reported `GOAL_FAILED`. Ratified, not accidental — it had its own
test. **F61 — multi-turn recovery.** R5's unit was *the rung*, right while a rung could only
carry a failure and wrong once ADR-0046 let one carry a *success that has not finished*; a
continuation that completed 14 of 17 outstanding files was refused the rung that would have
finished the job.

| File | What changed |
|---|---|
| `wisp/core/goal.py` | rows 3–6 restated; the fatal clause qualified by *"and no P3 PASS"*; `turn_succeeded` demoted from arbitration to a recorded fact |
| `wisp/core/recovery.py` | `RecoveryBudget.productive_continuations` (default 2); `_is_meaningful_progress`; `legal_rungs(..., exclude=)` with the refined R5; `decide` charges the productive budget on a re-choice |
| `wisp/core/convergence.py` | `WITNESS_FIELDS`/`witness_digest` (**F63**); the failure class computed for every attempt; `authorization_event` excluded from completion and escalated first; `passed = verdict PASS`; `_WANTS_FIX_RE` widened to three phrasings; `_resume_recovery` guards the empty case (**F62**) |

**ADR-0047** (R1–R13). Tests: `tests/reliability/test_multi_turn_productive_recovery.py` (50).
**Report:** `PHASE_MULTI_TURN_PRODUCTIVE_RECOVERY.md`. Live: `f60-live3` reached the §15 target
trajectory — `INITIAL → REPAIR → REPAIR`, three timeouts, passing `0→8→12→72`, `GOAL_MET` — and
`f61-live`/`f61-live2` show one continuation finishing genuinely-remaining work.

**The consequence to remember:** with F60, **the acceptance criteria are the sole gate**. A
timeout used to mask weak criteria; it no longer does. `_WANTS_FIX_RE` was widened for that
reason, and a *guards-only* criteria set (a red baseline, no promotion, no symbol criterion) is a
legitimate **no-regression objective** whose `GOAL_MET` means "nothing got worse".

### 0.0.1 PROGRESS-AWARE RECOVERY (2026-09-25)

The NEXT mission's live experiment exposed a dead end the taxonomy could not express:
`CODE_TURN_TIMEOUT → FailureClass.ENVIRONMENT → LEGAL_RUNGS {DIAGNOSTIC, HUMAN}`, where
`DIAGNOSTIC`'s directive is *"Do not edit any file in this attempt"* — so a turn cut off
**mid-implementation** was routed to a rung that **provably cannot** continue the work. The
class is correct (`ENVIRONMENT` is *"the model is too slow or unreachable — not retrying"*);
what was missing is a second, orthogonal fact: **did the attempt move the objective?**

**Added — one pure module, one table, one optional parameter:**

| File | What it is |
|---|---|
| `wisp/core/progress.py` | `evaluate_progress` — compares two `CommandProbe` measurements and reports which of the objective's own numbers moved. Host-owned, deterministic, reads no model text, no I/O, no model call. `NO_PROGRESS` / `MEANINGFUL_PROGRESS` / `PROGRESS_UNDETERMINABLE`. |
| `wisp/core/recovery.py` | `PROGRESS_CONTINUATION_RUNGS` (total; `ENVIRONMENT` gains exactly `REPAIR`; empty for `SECURITY`/`REPEATED`/`STAGNATION`), and a keyword-only `progress=` on `decide`/`legal_rungs`/`is_legal_rung` defaulting to `None`. |
| `wisp/core/convergence.py` | Progress per attempt; the continuation directive; the baseline journaled as the journal's first record; `read_journal_baseline`. |
| `scripts/next_progress_experiment.py` | The live driver (`--scenario`, `--modules`, `--per-module`). |

**ADR-0046** ratifies it (R1–R11). Tests: `tests/reliability/test_progress_aware_recovery.py`
(39). **Report:** `PHASE_PROGRESS_AWARE_RECOVERY.md` — verdict
**`PROGRESS-AWARE RECOVERY: DEMONSTRATED`**, with the boundary stated: in the five runs that
reached `GOAL_MET` attempt 0 had already satisfied the *objective*, so the continuation closed
the *turn*; the runs where the continuation wrote genuinely remaining work (**F61**:
`positive10` finished 14 of 17 outstanding files) did not converge because `R5` permits exactly
one continuation. **F60** (a `PASS`ing verdict on a timed-out turn is reported `goal_failed`)
and **F61** are open and recorded, not patched — both would change preserved contracts.


**The durability track is closed.** M2 (journal-first reconstruction), M3 (a real-SIGKILL kill point),
M4 (durability as a correctness precondition) and M16 (the escalation is state, not audit) are all
complete — and **M4 and M16 each found a live defect while closing it**, which is what revisiting a
decision is for. What remains is the *authority* track, below.

### 0.0.1 What is actually delivered, and what is not

The migration built **eight mechanisms**, each tested and reachable from its package:

`core/proposal.py` · `core/acceptance.py` · `core/task_graph.py` · `core/recovery.py` ·
`core/stagnation.py` · `core/context_trust.py` · `auth/principal.child_principal()` ·
`SessionRepository.reconstruct()`

**None is driven by the live turn loop — with one exception.** M13 constructs the stagnation detector on
the live turn path, so that mechanism is now driven (it observes and **records**; it does not act).
Everything else remains at the **mechanism** level, and the plan's own objective — *"the graph changes
during execution"* — is still met at the mechanism level rather than the integration level.

### 0.0.2 There is no single keystone — M9 was recorded as one, and that was wrong

M9 was described here as *"the message list as a projection of the graph"*, with five items blocked on it.
**M9 is now complete (ADR-0029), and the claim did not survive contact with the repository.** Two findings:

**The graph cannot project the transcript.** `TaskNode` carries no tool name, no arguments, no result, no
assistant text — the graph is a *shape*, not a payload. Making it the source of the message list would
mean copying the transcript into the nodes, i.e. a second copy that can disagree with the first. That is
the defect class this migration exists to remove, so the strong reading of M9 was **the wrong target**,
not merely unimplemented. The transcript projects from the **journal**; the graph contributes status.

**The projection that does exist was not faithful.** `Session.apply` added a `name` key to every replayed
tool reply that the live path never sets, while `runtime.py` states the invariant *"the log has to
reproduce `messages` exactly"*. The journal and the blob therefore disagreed on the same session, and
`context_pruner` branched on the difference. Fixed, and the equality is now asserted on a real turn.

**And "five items blocked on M9" was itself an overstatement.** Re-scoped:

| Item | What it actually needs |
|---|---|
| **M11** | ✅ **DONE** — node identity. `TaskNode.work_unit` names the work unit the node records; `build_turn_graph` takes the units, not a count; the M9 ratchet that **forbade the fix and was evadable by naming** (F29/F30) is replaced by field classification (ADR-0033). **The graph driving execution stays open**, pinned by a tripwire |
| **M12** | ✅ **DONE** — nothing from M9 was needed. `classify_failure_signal()` bridges the runtime's failure signals to the taxonomy, and **engine refusals are now recognised as denials** (they were being retried). Ladder *enforcement* stays deferred (ADR-0032) |
| **M13** | ✅ **DONE** — the detector runs on the live turn path (ADR-0034). Wiring it found that the turn-end signal is **empty on a default configuration**, which declared every multi-turn session stagnant (F32), and that the runtime cannot see a refused call's arguments (F33 — so the identity travels with the refusal). **Enforcement stays deferred**: routing to the ladder and gating completion are tripwired |
| **M14** | ✅ **DONE** — nothing from M9 was needed. The prompt sections are now classified and the **live T1 violation** (workspace-file content ahead of the system prompt) is fixed (ADR-0031) |
| **M15** | ✅ **DONE** — nothing from M9 was needed. The spawn site now passes a `child_principal`; the identity travels with the **call**, because a per-child executor would leak two thread pools each (ADR-0030) |

So **M12, M14 and M15 are independent** of the graph and of each other, and each is bounded — all three
done. M11 needed node identity and is done. **M13 is done too**, and it confirmed the pattern a third
time: M9's claim that M13 depended on M11 *narrowed again* — the identity stagnation needs is the
**action** identity (`action_key`, P1), not the protocol id (F33). **All five are complete.** The
remaining work is **larger** than "one keystone", not smaller, but it is several small pieces rather
than one large one.

### 0.0.3 Also open

| Item | Nature |
|---|---|
| **M1** | P3 stage 3b (enable the acceptance gate) — **F37 fixed, a live measurement exists, but ADR-0016 is `NOT_YET_DETERMINABLE` and enablement needs a superseding ADR** |
| **M8** | retire `multi_agent/dag.py` — **unblocked: the fanout suite is green now (F8 fixed)** |
| **M5** | foreground-turn `RunRecord` lifecycle |
| **M6** | `PolicyDecisionEnvelope` is still producer-less and consumer-less |
| **M7** | `change_tracker.py` not wired into evidence |
| **M10** | the materialized graph is a lower bound on iterations — **by design** |

### 0.0.4 The migration's central finding

Wisp does not need a Persistent Graph Loop **built**. Most of it already exists across **four layers
that do not talk to each other**:

| Layer | Location | Has | Lacks |
|---|---|---|---|
| A — live turn loop | `core/stateless.py`, `core/runtime.py` | dynamism, streaming, tools, approvals | graph, persisted node state, evidence |
| B — durable validated DAG | `wisp/graph/` | persistence, validation, provenance, join policies | frozen topology |
| C — experimental phase loop | `wisp/core/graph/` | a goal→phase loop | tests only; disowned |
| D — durable runtime layer | `wisp/runs/`, `wisp/contracts/`, `wisp/trace/` | run state machine, leases, idempotency, spans | never constructed in production |

The cost of the target architecture is therefore **wiring, not construction**. The recurring pathology
the migration has been removing is "complete, tested, and unreachable": **eight** such subsystems at
audit time, plus `ToolRequest`/`ToolResult` (fixed in P2) and `PolicyDecisionEnvelope` (still unwired).

### 0.0.5 Regression method — this tree makes the obvious baseline wrong

The tree carries **pre-existing uncommitted work** in the same files the migration edits. `git stash` of
only the touched files reverts them to **HEAD**, discarding that work — which made three unrelated
ratchet failures look like P0 regressions. **Snapshot the files you are about to edit to a path outside
the repo first, then compare against those copies.** Finding F12.

Every phase was verified against the **stable baseline** (the intersection of two runs — a single-run
count is not a baseline; see §7 and §11):
**129, failure set byte-identical in both directions, for P8, P9, M2, M3, M4, M16, M9, M15, M14, M12, M11 and M13**
— M11 and M13 were each additionally confirmed by a **two-run** pass on the final tree.
P0–P6 predate the method fix and were verified against single runs.

---

## 0.5. Phase 10 — the prior engagement (now committed)

`HEAD` at the end of Phase 10 was **`83b10af`**; its work is uncommitted no longer (see §3). It comprised 16 production files, 9 test files (222 new tests), 4 new documents.

### 0.0 What Phase 10 was, and what it found

Phase 10's brief asked one question of four subsystems: *"where is the authority, and what prevents a second one?"* All four were closed — and pursuing them surfaced **four findings that were on nobody's list**, two of which were fixed outright.

| # | Finding | Status |
|---|---|---|
| **C** | REST policy boundary — 35/41 mutating routes ungated | ✅ **FIXED** (user chose option B) |
| **C+** | The `.wisp/hooks` protected-path guard was absent from REST — `POST /api/files` wrote a hook the `write_file` *tool* was refused | ✅ **FIXED** (§0b) |
| **G1** | Authorization parity — **CLOSED by ADR-0055.** Driven, the agent path and the REST gate agree on **every** route in **every** mode (**0 path divergences of 36**); the 6 pinned pairs are a **model** divergence (`authorize()` vs `SecurityPolicy`) on three action names that are **REST-only** — the agent has no operation for them | ✅ **`DECIDED` — Option A** (§0.0.14) |
| **E** | The M4 organization policy layer is **never loaded** — a governance control that appears to exist and does not | ✅ **CLOSED** — the key-trust decision is **ADR-0058** and the wiring landed (`PHASE_M4_WIRING.md`); the layer is **WIRED**, default **OFF**. **REST receives L0** as of **ADR-0059** |
| **F** | The prior audit's 12 "written-but-unwired controls" were half-remediated and never maintained | ✅ **RE-VERIFIED**; #2 deleted, #7 fixed (§0e) |
| **R10** | The desktop client's checkpoint-diff request was **unauthenticated** — the one request that bypasses `apiFetch` built its headers from a *second* helper, called with no argument | ✅ **FIXED**; the 26-way duplication measured + ratcheted (§0f) |

### 0.0.1 The REST gate (finding C — complete)

The user approved **"Gate all three, update client"** (option B), and it is finished:

| Step | State |
|---|---|
| `wisp/server/routes/hooks.py` — gate `create_hook` + `test_hook` | ✅ **DONE** |
| `wisp/server/routes/mcp.py` — gate `add_mcp_server` + `test_mcp_server` + `delete_mcp_server` | ✅ **DONE** |
| `wisp/server/routes/plugins.py` — gate `install_plugin` + `toggle_plugin` + `delete_plugin` | ✅ **DONE** |
| `tests/test_rest_gate_boundary.py` — boundary re-pinned; `UNRESOLVED_UNGATED` now empty | ✅ **DONE** |
| `tests/test_server_policy_gate.py` — functional deny-in-`read_only` / allow-in-`full` per new gate | ✅ **DONE** |
| `wisp-desktop/src/renderer/hooks/useApi.ts` — surface the server's `detail` on non-OK | ✅ **DONE** |

**Gate boundary: 14 gated (was 6).** Every route that reaches host execution or alters executable configuration carries `require_tool_allowed`. Sibling routes (`*/test`, which spawn or execute the thing they test) and symmetric teardown routes (`DELETE`) were gated too, so there is no equivalent-authority bypass.

### 0.0.2 Current verification (all of it)

⚠️ **This table is a Phase-10-era snapshot, measured in a DIFFERENT environment. It is not
re-producible here, and the corpus must not quote it as current.** Corrected 2026-09-25 by the
corpus-integrity pass:

- **`ruff check wisp/` is NOT clean** — it reports **11 errors** at HEAD (F71). The row below was true
  in Phase 10's environment.
- **`mypy` is NOT exit 0** — the pinned 2.3.1 reports **1844 errors in 228 files** (F71).
- **"Phase 10 focused tests (11 files) → 245 passed" is not producible here.** The block **aborts at
  collection**: two of its files (`tests/test_protected_path_guard.py`, `tests/test_server_policy_gate.py`)
  import `fastapi.testclient`, which needs `httpx`, which is absent from `.venv` and from the uv cache
  (F80). With those two ignored the block reports **216 passed, 2 FAILED**, both pre-existing. §11 carries
  the repaired, runnable command.

| Check | Result (Phase 10's environment — see the correction above) |
|---|---|
| `ruff check wisp/` | **All checks passed** |
| `mypy` (2.3.1, the `uv.lock` pin) | **exit 0** |
| Phase 10 focused tests (11 files) | **245 passed** |
| Changed-subsystem regression (multi_agent / graph / subagent) | **270 passed** |
| Broad regression | **2544 passed, 27 failed, 5 errors** — the identical known environmental set (§7), all attributed. **Zero regressions.** |
| Desktop client guards (2026-09-21, §0f) | **9 passed** — `useApi.test.ts` (5) + `authHeaderAuthority.test.ts` (4) |
| Desktop typecheck (2026-09-21, §0f) | renderer `tsc -b tsconfig.web.json` = **32** (pre-existing set, no `TS2554`); `tsc -b` = **38** |

Re-verified 2026-09-21 at session start: `ruff` clean, 186 focused tests passed.

### 0.0.3 Remaining decisions — all measured, none guessed

| Order | Item | Why it needs you |
|---|---|---|
| 1 | **E** — wire the M4 policy layer (§0d) | Depends on the **key-distribution ceremony** the M4 spec deferred. Recommended: decide key trust, then wire |
| 2 | **G1** — authorization parity | **DECIDED (ADR-0055): Option A.** The recommendation of B rested on a premise the measurement inverted — B is *not* a parity fix (it would make REST stricter than the agent, on names the agent cannot run) and it would 403 the shipped client. C remains the fix for the *real* residual (REST cannot ask a human) and is its own ADR |
| 3 | **R1b** — `POST /api/hooks` command content | The gate restricts *who* may register a hook, not *what* it runs |
| 4 | **R10** — desktop client `tsc -b` | ✅ the **functional defect is FIXED** (§0f). What remains is the 32 pre-existing errors and the fact that the `typecheck` script checks nothing — a policy call |
| 5 | **Commit Phase 10** | Nothing is staged |

**Not committed.** Say the word and it commits the same way as Phase 9.

### 0b. The protected-path guard (finding C+ — fixed)

Asking whether R1b survived Target C turned up a **verified privilege-escalation
path**, in a different route than expected.

`.wisp/hooks/` holds commands Wisp executes itself with the full process
environment. `docs/THREAT-MODEL.md` names the guard on that directory as the
mitigation for "malicious hook persistence". The guard was enforced on the
**agent** path and absent from **REST**:

| Path | `.wisp/hooks/x.json`, mode `full` |
|---|---|
| agent tool `write_file` | refused |
| REST `POST /api/files` | **written** ← the defect |

The policy gate did not close it, because the gate consults `SecurityPolicy`,
which is mode-based and scans no arguments. `/api/bash` *was* covered, by a
third implementation (a shell verb scan).

Two more defects in the same guard: `new_path` was never scanned (renaming
*into* the hook dir passed), and the agent-side check was a bare substring test
(so `.wisp/hooksfoo/` was wrongly refused).

**Root cause: two authorization implementations.** `auth/decision.authorize()`
is 6-layer with an argument scan; `infra/security.SecurityPolicy.check()` is
4-layer without one. Agent uses the first, REST the second.

**Fixed** — one canonical predicate (`wisp/pathsec.is_protected_path`), all
three paths routed through it. 26 new tests. Full record:
`PHASE_10_PROTECTED_PATH_GUARD.md`.

**That work also exposed a test-isolation defect**, now fixed: `RATE_LIMITER` is
a *process-external* SQLite singleton (30 req / 60 s, keyed by client IP, in
`~/.config/wisp/`), so route tests shared one budget with each other **and with
previous runs**. Six pre-existing tests failed with `429` only when run
together. Fixed by a session-scoped autouse fixture in `tests/conftest.py`
alongside the existing auth neutralization. **The affected-subsystem sweep went
from 1195 → 1295 passed** — tests that had only been reachable in the right
order.

**The structural cause (G1) is measured but NOT fixed** — see §0c.

### 0c. G1 — measured, and the picture changed

G1 was recorded as *"two authorization implementations remain; REST uses the
weaker one."* That was an observation. Measuring it changed it:

**They are not two implementations of one concept.** `authorize()` owns
capability/sensitivity/argument narrowing; `SecurityPolicy` owns the mode engine
and the policy-hook mechanism. Neither is a subset of the other.

**The real asymmetry is compositional — and the agent does not have it.**
`ToolExecutor` consults **both** (`policy_hard_deny` at `:691`, then `authorize`
at `:707`, then the approval gate). The REST gate consults **only**
`SecurityPolicy`. Every rule living exclusively in `authorize()` is invisible to
REST — which is exactly how the protected-path bypass happened.

**Measured: 36 (route, mode) pairs → 9 divergent.** Three were the guard (closed).
The remaining **6** are the approval layer:

| action | `authorize()` | `SecurityPolicy` | REST gate |
|---|---|---|---|
| `hooks.create` / `mcp.add_server` / `plugins.install` in `auto_edit` and `ask_all` | ALLOW **+ approval** | ALLOW | **ALLOW** |

`require_tool_allowed`'s docstring says *"approval-required verdicts deny"* — it
**cannot honour that**, because `SecurityPolicy` never reports
`approval_required` for these actions.

**The default mode is affected.** `permission_mode` defaults to `AUTO_EDIT`
(`config.py:674`), and the desktop client sets no mode. So out of the box, REST
permits registering a hook / MCP server / plugin **without the approval the
agent path requires**.

An approval channel **does** exist — `WebSocketTransport` implements
bidirectional approval flow. The REST routes just don't use it.

**Options** (full matrix in `PHASE_10_AUTHORIZATION_PARITY.md`):
**A** accept (correct the docstring); **B** honour the contract — parity, but the
client 403s on those routes in `auto_edit`/`ask_all`; **C** route approvals
through the existing WebSocket channel — the complete fix, a real feature.
**Recommended: B now, C as the real fix.**

**Not implemented** — B changes default-mode behaviour of a shipped client, so it
is a decision, not a patch. **But it is now measured and ratcheted**:
`tests/test_authorization_parity.py` (18 tests) fails on any *new* divergence, on
a divergence that silently disappears, and on the file/shell surfaces losing
parity.

### 0d. E — the M4 governance layer is not wired to the runtime

**The most consequential finding of the engagement.** Found by following G1 one
step further: *if the agent has an L0 organization policy layer, what does the
REST gate do with it?* The answer was that **neither** path has one, because
nothing loads a bundle.

`wisp/policy/` — signed Ed25519 bundles, narrow-only merge, provenance,
offline continuity, a CLI (`wisp policy inspect|verify|explain|dry-run`), four
server routes, full test coverage — **is never reached by the runtime**.

Evidence:

| Check | Result |
|---|---|
| `ToolExecutor(...)` construction sites | **2** — `composition.py:134`, `acp_session.py:208`. **Neither passes `policy=`** |
| `wisp/config.py` | **no "policy" string at all** — no way to configure a bundle |
| callers of `load_local`/`load_managed`/`merge_all` | `policy/cli.py` + tests only |
| `POST /api/policy/publish` | verifies a signature, stores on `app.state.policy_bundle` — **nothing reads it for a decision** |
| `app.state.policy_pubkey` | set by `tests/test_policy_routes.py:33` **only** → real servers 503 on `/api/policy/*` |
| `ReproManifest.policy_bundle_id` | declared, serialized, **never populated** — a "which policy governed this run" field that is always empty |

**The mechanism works** — `authorize(run_bash, effective_policy=<deny bundle>)`
returns `allowed=False, layer=organization`. It is always passed `None`.

**Root cause:** the M4 spec has sections for modules, precedence, modes and tests
— and **no wiring section**. §5 "Deferred" does not list runtime integration
because it was never specified. This is not "ran out of time"; it is a subsystem
built to completion without the spec saying how it reaches the decision point.

**Why this one is different.** The earlier findings were controls that were *too
weak*. This is a control that **appears to exist and does not** — its failure
mode is false assurance. An operator can run `wisp policy dry-run`, see
`denied: run_bash (organization)`, and believe the fleet is governed.

The codebase's own prior audit already named this as the dominant pattern
(`docs/audit-2026-08-24.md:270`, *"written-but-unwired controls, ≥12
instances"*) — **but that audit predates M4** (08-24 vs 09-04) and does not list
it. M4 is a new instance of a pattern the codebase had already diagnosed.

**Not wired here, for a specific reason.** The naming convention already exists
(`WISP_POLICY_BUNDLE`, `WISP_POLICY_PUBKEY`, `WISP_POLICY_CACHE`), so wiring is
small — but `WISP_POLICY_PUBKEY` presupposes an operator *has* a trusted key,
and the M4 spec explicitly deferred the **"device registration + key distribution
ceremony (needs human workflow design)"**. Wiring first would make the env vars
live while leaving key trust unanswered.

**Pinned** by `tests/test_m4_governance_wiring.py` (25 tests) — including a
**tripwire** that fails the moment someone wires it, so the code and the
document cannot drift apart. Options A–D in
`PHASE_10_M4_GOVERNANCE_UNWIRED.md`; recommended **B (decide key trust, then
wire)**.

**Option C is DONE** — the false-assurance half is fixed: a `NOT ENFORCED`
notice now precedes every evaluating `wisp policy` command; `explain_denial`
says `"Rule: deny X"` rather than `"Denied X"` (it describes the bundle, not a
result); and `README.md`, `AGENTS.md`, `docs/SECURITY.md` carry qualifiers.
A side correction: the README's graph bullet claimed *"narrow-only over the
active policy"* — `validate_graph(graph)` reads the **graph's own** policy and
takes no external one, so the wording was wrong. **Wiring (B) is still open.**

### 0e. F — the unwired-controls inventory, re-verified

`docs/audit-2026-08-24.md:270` named *"written-but-unwired controls"* as **the
dominant pattern** in this codebase and listed 12 instances. M4 (§0d) was a
13th. So I re-verified the original 12.

**The list had half-decayed and was never maintained. Now: 7 wired · 1 deleted ·
3 unwired · 1 unidentified.** Two of the live items were fixed in this pass
(§0e.1, §0e.2).

| # | Control | Verified |
|---|---|---|
| 1 | `scrub_sensitive_env` | ✅ wired (`infra/hook_types.py:264`) |
| 2 | `_SENSITIVE_ENV_KEYS` | ✅ **DELETED** — superseded duplicate (§0e.1) |
| 3 | `DockerSandbox` | ✅ wired (`sandbox/__init__.py:388`) |
| 4 | `AuditLog.log_blocked` | ✅ wired (`tool_executor.py:1193`) |
| 5 | `_redact_sensitive_tool_args` | ✅ wired — real name `redact_sensitive_tool_args` |
| 6 | `spawn_with_guards` | ⚠️ dead duplicate — **guards live** at `:723`/`:1737` |
| 7 | DAG `metadata["_budget"]` | ✅ **FIXED** — now honored, narrow-only (§0e.2) |
| 8 | `execute_tool(security_policy=…)` | ⚠️ unwired — no caller passes it (low; annotate) |
| 9 | `ToolRegistry.execute` | ⚠️ production-unused (latent trap) |
| 10 | AUTO_EDIT default | ✅ **fixed** — schema *and* resolution both AUTO_EDIT |
| 11 | event-replay `TOOL_CALL` | ❓ **referent not identified** |
| 12 | chain patch apply | ✅ wired (`subagent_orchestrator.py:922`) |

**No remaining item is a live control that fails silently.**

#### 0e.1 #2 — the dead env list, deleted

`_SENSITIVE_ENV_KEYS` had no consumer and no dynamic access. Its job is done by
`tools/_utils_env.py`, which is stricter: `scrub_sensitive_env` (hooks) uses an
**allow-list**; `credential_free_env` (bash/sandbox/MCP) pairs a deny-list with
`_CREDENTIAL_ENV_PATTERN`, which matches every key the static list named. The
one deliberate difference is right way round: the static list called `HOME`/`USER`
sensitive, and the live code **keeps** them for the agent's bash tool while
excluding them from hooks. Deleted, with a note left in its place — wiring the
static list instead of the pattern would be a narrower control wearing a
similar name.

#### 0e.2 #7 — the DAG budget, now honored

`docs/audit-2026-08-24.md:112` (item 11) prescribed **three** fixes. Two had
landed; the third had not:

| Prescribed fix | State |
|---|---|
| Skip descendants of failed nodes | ✅ `dag.py::_block_descendants` |
| Inject dependency outputs | ✅ `metadata["_dep_results"]` |
| **Honor the metadata budget** | ❌ **was missing → now implemented** |

The orchestrator built a `ResourceBudget` from a node's `metadata["budget"]`,
attached it to `contract.metadata["_budget"]`, and both runner sites built their
**own** from contract fields — silently dropping it. It mattered most for
**`max_tool_calls`**: `ResourceBudget` has that field, the contract does not, so
the DAG declaration was the only way to bound tool calls per node.

Fixed with one helper, `_runner._budget_from_contract`, used by both sites:
contract limits are the floor, the declared budget narrows **only**. With no
declaration, behaviour is byte-for-byte unchanged.

**A claim I got wrong, caught by the guard.** I first recorded #11 as "in an
unreachable module" — `semantic_compressor.py` — on the strength of a sweep that
reported no importer. **False:** `infra/session_dto.py:67` imports
`SemanticCompressor` *inside a function*, and `session_dto` is imported by
`__main__.py:881`. My own pinning test failed on it before anyone read the claim.
Cause: BSD `grep` silently ignores `--include` after the path — **the third time
that exact mistake produced a false "unreferenced" verdict**.

Full record: `PHASE_10_UNWIRED_CONTROLS_INVENTORY.md`. Pinned by
`tests/test_unwired_controls_inventory.py` (15 tests) — a control that becomes
wired fails there, so the inventory cannot decay the way the audit's did.

### 0f. R10 — the client's auth header had 28 authors (functional half fixed)

`CONTEXT.md` listed R10 as *"32 pre-existing errors; one is functional:
`useApi.ts:368` sends no `Authorization` header."* The pre-existing errors are a
decision. **The functional defect is not** — it is a bug with one correct fix,
and it is fixed.

**The defect.** `getCheckpointDiff` is the one `useApi` method that cannot use
`apiFetch` (it reads a plain-text diff; `apiFetch` returns `resp.json()`), so it
calls `fetch` directly — and built its headers from a *second* helper, called
with **no argument**. `makeAuthHeaders(apiKey)` returns `{}` for a falsy key, so
the request shipped **unauthenticated**. There is no fallback: `authParams` is
the empty string by design (the key is never a query param, where it would leak
to logs).

**Why nothing caught it.** An AST scan of `wisp-desktop/src` found the header
constructed in **19 files / 28 sites** — 18 files / 27 sites in the renderer, of
which exactly **one** is canonical. So **26 re-implementations across 17 files**,
each written slightly differently (`: undefined`, `: {}`, spread). The canonical
builder is *one of* the 27, so the question *"does this module build the
header?"* answers **yes for every module — including the broken one**. The defect
was not an absence; it was a **duplicate that had drifted**. `src/main/backend.ts`
is a genuine exception (separate Electron bundle, cannot import the renderer
helper) and is pinned as-is.

**The compiler was already reporting it.** `tsc -b` builds *two* projects.
Measured on the renderer project: **33 errors with the defect** (including
`useApi.ts(380,48): error TS2554: Expected 1 arguments, but got 0`) and **32
without**. It was missed because the `typecheck` script runs `tsc --noEmit`, and
the root `tsconfig.json` is project-references with `"files": []` — so it
typechecks *nothing*. That is D4 (§4) with a second consequence.

**The 32-vs-38 discrepancy, resolved.** Both numbers are right:
`tsc -b tsconfig.web.json` = **32** (renderer only, 11 files); `tsc -b` (both
projects) = **38** (12 files, the extra 6 in `src/main/menu.ts`). The recorded 32
was a renderer-only count. No unexplained drift.

**Fixed:** one authority — `apiFetch` now calls `makeAuthHeaders(apiKey)` too;
`getCheckpointDiff` is handed the key and its dependency array corrected;
`makeAuthHeaders` carries the comment that names it the single authority.

**Guarded:** `useApi.test.ts` (5 tests, written RED-first — it failed with
`expected undefined to be 'Bearer secret-key-123'`) and
`authHeaderAuthority.test.ts` (4 tests) — an AST ratchet that fails when a new
file starts building the header, when a recorded one changes count, or when one
disappears, so this list cannot decay the way the audit's did.

**A test-infra fragility, found while verifying:** `npx vitest run` dies with
`Timeout waiting for worker to respond` — **not** a test failure, no test runs.
vitest's worker-start timeout is a hardcoded `START_TIMEOUT = 6e4`, and jsdom
environment setup measures **50.9–52.9 s** on this machine (load average
4.25–5.07). Worker startup sits ~9 s under a hard limit and crosses it under any
concurrency. **The desktop suite is flaky as a suite.** Verified by running one
file at a time.

Full record: `PHASE_10_CLIENT_AUTH_AUTHORITY.md`.

---

## 1. What Wisp is (verified facts)

| Fact | Value |
|---|---|
| Size | `wisp/` = **366 Python files, ~84K raw lines** |
| Tests | **~357 test files, ~5,374 test functions**, ~5,845 collected |
| Tools | **42** in `wisp/tools/registry.py` (`TOOL_SCHEMAS` == `TOOL_IMPLS` == 42) |
| Entry point | `wisp = wisp.__main__:main` |
| Python | `>=3.11` (CI runs 3.12); **POSIX only** |
| Architecture | Transport ABC → AgentRuntime (state) → `WispAgentCore.turn` → `ToolExecutor.execute` → `authorize()` → sandbox → host |
| Authority choke point | `core/stateless.py:1840` is the sole delegation to `ToolExecutor.execute`; with no executor wired the fallback is **risk-gated to reads only** |
| `authorize()` | 6-layer narrowing: policy → principal → workspace → sensitivity → args → approval, each naming its controlling layer |
| Server | 27 routers, ~70 routes, **41 mutating**; loopback-only by default, refuses to boot unauthenticated |

---

## 2. The engagement arc

| Phase | Deliverable | Outcome |
|---|---|---|
| **1. Reconnaissance** | `REPOSITORY_INTELLIGENCE_REPORT.md`, `repository_manifest.json` | 20-section model + machine manifest |
| **2. Verification** | (folded into the report) | Closed 10 open questions; **3 of my own Phase 1 claims were wrong** |
| **3. Judge** | `FINDINGS_ADJUDICATION.md` | 27 findings classified S1–S4; **1 genuine security defect**, 5 invalidated |
| **4. Remediation** | `PHASE_FINDINGS_NORMALIZATION.md`, `PHASE_BOUNDARY_FORENSIC.md`, `PHASE_CANONICAL_AUTHORITY_MAP.md`, `PHASE_CANONICAL_CONTRACT_FREEZE.md`, `PHASE_ARCHITECTURAL_INVARIANTS.md`, `PHASE_FINDINGS_REMEDIATION_REPORT.md` | Rounds 1–4: 18 fixed, 2 partial, 5 invalidated |
| **5. Commit** | `83b10af` | 54 files, +4,891/−210 |
| **10. Authority closure** | `PHASE_10_AUTHORITY_CLOSURE_AUDIT.md`, `PHASE_10_AUTHORITY_CLOSURE_IMPLEMENTATION.md` | 4 of 4 areas closed; REST needed a decision → **user chose option B, completed** |
| **10b. The guard** *(found while closing R1b)* | `PHASE_10_PROTECTED_PATH_GUARD.md` | A verified REST privilege-escalation path, closed; the guard canonicalized onto one predicate; a test-isolation defect fixed |
| **10c. Parity** *(found while auditing the guard's cause)* | `PHASE_10_AUTHORIZATION_PARITY.md`, `PHASE_AUTHORIZATION_PARITY.md` | G1 measured: 9 of 36 (route, mode) pairs diverge; 6 remain, all the approval layer, in the default mode. Ratcheted. **DECIDED — ADR-0055 (Option A)**: driven against the two real paths the divergence is **0 of 36**; the 6 are a *model* divergence on three **REST-only** action names |
| **10d. M4 governance** *(found while auditing parity)* | `PHASE_10_M4_GOVERNANCE_UNWIRED.md` | The organization policy layer is **never loaded**. False-assurance half fixed (option C); wiring **open** |
| **10e. The audit's own list** *(found while auditing M4's pattern)* | `PHASE_10_UNWIRED_CONTROLS_INVENTORY.md` | The prior audit's 12 unwired controls re-verified: **7 wired · 1 deleted · 3 unwired · 1 unidentified**; #2 and #7 fixed |

**The arc of Phase 10 is a chain.** Each question was answered, and the answer
showed the next assumption up was false:

```
"did R1b survive the REST gate?"      -> no, and a *different* route bypassed the guard entirely
"why did the guard miss REST?"        -> two authorization models, incompletely composed
"what does the other model do with
 the org policy bundle?"              -> neither has one; nothing loads a bundle at all
"the pattern has a name — is the
 codebase's own list still true?"     -> half of it had been fixed and nothing recorded which half
```

§10 records the same pattern from the other direction: **nine** times a claim
dissolved under execution, four of them mine.

---

## 3. Commits

`83b10af` was the Phase 9/10 baseline. Every commit on top of it, all on `main`.
**This table narrates the phase commits; `git log 83b10af..HEAD` is the authority for the exact
list.** A count written in prose goes stale on the next commit, so none is quoted:

| Commit | Scope |
|---|---|
| `cfe6f0f` | `docs:` the Persistent Graph Loop audit (9 documents), migration plan, ledger, ADRs, P0–P2 reports — 14 files |
| `744d081` | `feat:` P0 + P1 + P2 implementation and tests — 17 files, 119 tests |
| `385e552` | `docs:` P0–P2 wiring recorded in `AGENTS.md` + the ledger |
| `b17a927` | `feat(p3):` acceptance criteria, evidence, verdicts (stage 3a) — 60 tests |
| `98bb8f9` | `docs:` hand off the migration in `CONTEXT.md` |
| `e56fc6e` | `feat(p4):` materialize a task graph from durable state — 35 tests |
| `e2da7f0` | `feat(p5):` runtime graph mutation — 49 tests |
| `e47118a` | `docs:` correct `CONTEXT.md` — Phase 10's other changes are **not** committed |
| `e133a95` | `feat(p6):` recovery ladder — 69 tests |
| `3d39ff3` | `feat(p7):` stagnation detection — 44 tests |
| `8dcd372` | `docs(p7):` the baseline failure set is agent workspace data, not repo source |
| `e003814` | `feat(p8):` the context trust boundary — 54 tests |
| `e3e87d8` | `feat(p9):` wire the delegation authority; fix a latent `AttributeError` — 25 tests |
| `8e891e8` | `feat(m2):` journal-first reconstruction with blob fallback — 19 tests |
| `2c5bbbb` | `test(m3):` drive `unresolved_actions()` under a real SIGKILL |
| `02c756c` | `feat(m4):` revisit ADR-0004 — and close a live defect it led to — 24 tests |
| `d80f582` | `docs:` compaction handoff — refresh `CONTEXT.md`, add the workspace `MEMORY.md` |
| `a055b39` | `feat(m16):` the escalation is state, not audit — 40 tests, and **found a live read-side defect** |
| `0b1f4e8` | `docs(m16):` pin the commit hash and repair the handoff docs |
| `7cff74f` | `docs(m16):` ledger rows; keep agent workspace data untracked (+ `.gitignore`) |
| `73c2bbc` | `docs(m16):` record the third occurrence of the flaky test F17 |
| `1229b87` | `docs(m16):` correct the commit counts and complete this table |
| `ed9fee6` | `docs:` stop quoting a commit count that goes stale on every commit |
| `b39c120` | `docs:` complete the commit table and stop over-claiming it |
| `e52333f` | `feat(m9):` the execution view is faithful; the graph is a shape, not a payload — 11 tests |
| `034d4f0` | `docs(m9):` sort the findings log and complete the commit table |
| `bed9f7e` | `feat(m15):` a subagent authorizes as a narrowed child — 22 tests |
| `7c601c0` | `docs(m15):` record the M9 and M15 commits in the handoff table |
| `e502391` | `fix(m14):` classify prompt sections, and close a live T1 violation — 32 tests |
| `abbc4af` | `docs(m14):` record the M14 commit in the handoff table |
| `43015ea` | `fix(m12):` bridge the failure path to the taxonomy; engine refusals are denials — 33 tests |
| `e2b6f10` | `docs(m12):` record the M12 commit in the handoff table |
| `0fdcdea` | `feat(m11):` give graph nodes a work-unit identity; the ratchet classifies fields, not names — 24 tests |
| `7c15626` | `docs(m11):` record the M11 phase; findings F29–F31; repair the commit table |
| `ade4dc6` | `feat:` land the POST-M13 execution-semantics work (ADR-0034 – ADR-0044) — 108 files, +26,811/−397; excludes the user's WIP (§8) |
| `b8dc4ac` | `docs:` point the handoff at `ade4dc6` |
| `8a7e9ab` | `feat:` land the autonomous-convergence chain (ADR-0045 – ADR-0046) — 21 files, +8,173/−14; excludes the user's WIP (§8) |
| `c8b3cc8` | `docs:` point the handoff at `8a7e9ab` |
| `b9af5f0` | `feat:` close F60 and F61 (ADR-0047) — a failed turn is not a failed objective — 12 files, +2,208/−93; excludes the user's WIP (§8) |
| `af3a89a` | `docs:` point the handoff at `b9af5f0` |
| `802413a` | `docs:` add the current-state-of-the-authorities page, and guard it — 3 files, +687; found **F64/F65**; excludes the user's WIP (§8) |
| `7023af0` | `feat:` decide the criteria-derivation authority (ADR-0048) — 6 files, +1,347/−11; found **F66/F67**; excludes the user's WIP (§8) |
| `d7a55c2` | `fix:` F8's second half — a missing validator is a system failure, not a schema verdict — 6 files, +883/−11; found **F68/F69**; excludes the user's WIP (§8) |
| `dd21f6d` | `docs:` point the handoff at `d7a55c2`, record F64–F71, and correct two false claims |
| `3298894` | `docs:` decide the precedence numbering (**ADR-0049**) — the code did not move — 5 files, +873/−27; **no behaviour change**; corrects the brief's inverted numbering claim; excludes the user's WIP (§8) |
| `0bc4f22` | `docs:` point the handoff at `3298894`, and record F64/F65 as DECIDED |
| `3f9e639` | `feat:` let an objective declare its criteria (**ADR-0050**), and reject rather than reinterpret — 7 files, +1,332/−30; flag `WISP_CRITERIA_STRUCTURED_DECLARATION` default OFF; found **F72/F73**; excludes the user's WIP (§8) |
| `40cfa52` | `docs:` point the handoff at `3f9e639`, and record F72–F74 |
| `3990313` | `docs:` decide the acceptance gate's enablement contract (**ADR-0051**) — the gate has nothing to gate on — 6 files; **no production change**; the instrument is **committed** (`scripts/`), fixing the class F75 names; found **F75/F76**; excludes the user's WIP (§8) |
| `8a7db35` | `docs:` point the handoff at `3990313` |
| `1e83e34` | `docs(m8):` survey the `dag.py` retirement and decide **DEPRECATE**, not remove — blocked on a **measured semantic divergence** (which definition of a valid DAG wins); prose-only in `wisp/`; 10-test guard incl. 3 tripwires on the residual; excludes the user's WIP (§8) |
| `3c1b6dd` | `docs:` point the handoff at `1e83e34` |
| `805eca8` | `fix:` publish a capability failure as a **host** failure, not a denial (**ADR-0052**) — a system-failure envelope with the `kind` in `data`; the denial taxonomy and the prompt are unchanged; 15-test whole-path guard; found the helper defect that made NV1 non-falsifying; excludes the user's WIP (§8) |
| `3ed402a` | `docs:` point the handoff at `805eca8` |
| `800ada0` | `feat:` the turn path's criteria set carries the objective's declared criteria (**ADR-0053**) — satisfies ADR-0051 R1's precondition; the gate's condition is driven to differ from `rejection()` on 2 of 6 cases; flag `WISP_TURN_CRITERIA_SOURCE` default OFF; `CURRENT_AUTHORITIES.md` re-pinned (found the pin guard's blank-line weakness — a 6th instrument-defect instance); excludes the user's WIP (§8) |
| `5898e0e` | `docs:` name the instrument-defect class once, with its six instances — **documentation only**, no code changed; `CONTEXT.md` §10 + `AGENTS.md`; excludes the user's WIP (§8) |
| `c03eee3` | `docs:` point the handoff at `5898e0e` |
| `74ac43b` | `feat:` the acceptance gate consumes `verdict_keys_on_declared` (**ADR-0054**) — the engine's pre-`done` gate asks a read-only callable, bounded by ADR-0036's model and sharing the turn's budget; `WISP_ACCEPTANCE_GATE` default **OFF** (ADR-0051 R2–R6 **not** satisfied: **1** capable model of 13, measured by a committed instrument); the ADR-0051 tripwire fired and was replaced by its inverse; `CURRENT_AUTHORITIES.md` re-pinned; excludes the user's WIP (§8) |
| `17130c7` | `fix:` the pin guard asserts the pinned **content**, not that a line exists — found **nine** stale pins the old check passed; 3/3 non-vacuity probes caught (NV3 first MISSED because the *probe* replaced only the first occurrence); `CONTEXT.md` §10's sixth instrument-defect instance **CLOSED**; excludes the user's WIP (§8) |
| `8de56d8` | `docs:` point the handoff at `17130c7` |
| `7bb8f8a` | `docs:` the REST gate is **at parity with the agent path** (**ADR-0055**) — **0 path divergences of 36**, 6 **model** divergences; **Option A**; `require_tool_allowed`'s docstring qualified (**+13/−0**, pure); the ratchet keeps all seven properties and gains a **real-path parity guard** (25 tests); committed instrument `scripts/authorization_parity_measurement.py`; found **F78–F81**; `M1` → **`PARTIAL`**; excludes the user's WIP (§8) |
| `9d56aec` | `docs:` the two criteria flags are **independent** on the objective path (**ADR-0056**) — the interaction is **derivation order** (a declaration pre-empts `strict`); **no production change**; 7-test guard with an AST composition pin; found **F82** (the two canonical blocks listed different file sets — both now 45 files, **1312 tests**); excludes the user's WIP (§8) |
| `0cd5613` | `docs:` point the handoff at `9d56aec` |
| `e639115` | `feat:` a REST request for an executable-config action **asks a human** (**ADR-0057**) — the frame is the **clients'** vocabulary (client change **zero**), **no client ⇒ 403**, flag `WISP_REST_APPROVAL` default **OFF**; new `wisp/server/approval_bridge.py` + an async gate companion; 18-test guard, 5/5 probes caught; found **F83** (the WS approval channel was half-wired) and **F84**; excludes the user's WIP (§8) |
| `aa47ae0` | `docs:` **corpus integrity pass** — 2.1 **OPEN** (`httpx` absent from the cache and the host), 2.2–2.4 closed; the pin guard gains **four prose properties** and caught a real header drift on its first run; found **two Phase-10 guards red at HEAD** (neither F38, neither in the canonical block); excludes the user's WIP (§8) |
| `5c129d5` | `docs:` point the handoff at `aa47ae0` |
| `6ec0f48` | `fix:` `_tool_result_output`'s success test **delegates to the taxonomy** — the guard was RED since `ade4dc6` and the **code** was wrong, not the test; both branches call `core.events.is_error_outcome`, 35-case differential on the production function identical; **no ADR**; a **floor** added to the canonical guard; found **F85** (a count is canonical only if measured after the LAST change to any member) and **F86** (two unlike guards classified as one class); both outcome-classification files added to the canonical block; excludes the user's WIP (§8) |
| `08dd57e` | `docs:` **corpus integrity II** — 2.1 **CLOSED** (the M4 guard's count is 3; the third site is `wisp/benchmark/runner.py`, from `8a7e9ab`, **authorised** by ADR-0045's F54 fix — not `acp_session.py`/`cef3e90`), 2.2 **OPEN** (`httpx` re-attempted, still absent); **F87** — §3's own table was missing **nine** "point the handoff" commits; excludes the user's WIP (§8) |
| `db29a11` | `docs:` point the handoff at `08dd57e`, and backfill §3's commit table (**F87** — nine "point the handoff" commits had never been listed; the checker now reports *every commit is listed*) |
| `86ab7cb` | `docs:` decide the **key-trust workflow** (**ADR-0058** — the brief said 0059; **F90**) — the operator-supplied organization **public key**; absence is a configuration, invalidity is a refusal; expiry narrows, it does not refuse; the key is shared; offline by construction; the private key is never Wisp's. Rejects TOFU, a built-in root, and the registration ceremony (out of scope, not wrong). **The brief's worked example was wrong and decided the workflow** (**F91**): `WISP_POLICY_PUBKEY` is base64 key material, not a path. Found **F88** (the M4 policy suite is red in this environment — `cryptography` absent: 14 failed, 6 errors, 13 passed, none in the canonical block) and **F89** (a bundle omitting `expires_at` raises `min() iterable argument is empty`); 12-test guard, 9/9 probes caught, incl. **two defects in the guard itself**; excludes the user's WIP (§8) |
| `1f5f6fc` | `fix:` the **loader-caller tripwire** states its property, not a file list — D1's guard drives `load_local`, so the exact-set assertion fired **for the wrong reason**; repaired (the CLI, and tests, nothing else) and probed in both directions; excludes the user's WIP (§8) |
| `aa9d8a8` | `feat:` **wire the M4 policy layer** behind `WISP_POLICY_BUNDLE`, default **OFF** (**ADR-0058**) — `config.py` reads two string settings; `composition.py` loads via `load_organization_policy` and passes `policy=` at the single construction site; a named-but-unverifiable bundle refuses to boot. **§6's step 3 (REST) is deliberately not done** — `SecurityPolicy` has no organization slot, so a bundle there would be dead data; pinned. Four tripwires inverted, **two more fired for the wrong reason and were repaired** (**F92**); the canonical block gains three files and is re-measured (**F93**: 1390 → **1437**); 10-test guard, 7/7 probes caught; `PHASE_10_M4_GOVERNANCE_UNWIRED.md` updated in place; excludes the user's WIP (§8) |
| `5330a0f` | `docs:` point the handoff at `aa9d8a8` — the previous mission's handoff commit, which §0 said the next landing would list (**F87**'s stated exception); excludes the user's WIP (§8) |
| `f35188a` | `feat:` the **REST gate consults the M2 authority for its denial verdict** (**ADR-0059**) — closes the divergence **ADR-0058 created** (driven, **11 of 36** (route, mode) pairs; ADR-0055's 0-of-36 was measured with **no policy loaded**); rejects A (16 rows move with no bundle), B, and **D refuted by measurement**; **conditional on a bundle** ⇒ unset is today's gate **byte-for-byte** (40/40, status *and* detail, proved behaviourally *and* structurally); one load site (`root.organization_policy`), one principal; the §4 pin inverted; `AUTHORITY_CONSUMERS` gains `deps.py` (found by running the canonical block); 16-test guard, **10/10 probes caught** — the probe found a defect in the guard itself (**F96**); found **F94/F95/F98/F99**; canonical block 1437 → **1453**; excludes the user's WIP (§8) |
| `2307925` | `docs:` **corpus integrity III** — 2.1 **CLOSED** (F89's message; the trailing `or 0.0` was **unreachable**; 36/36 expiry inputs identical in outcome), 2.2 **OPEN** (both offline installs still fail; the table is consolidated as §6.1), 2.3 closed (§10 gains sub-cases 5 and 6), 2.4 closed (ADR-0057's residual 1 → §12 row **W1**, **F100**); found **F97** (three stale ADR-range claims) and **F99** (the brief's ledger rows do not exist); excludes the user's WIP (§8) — **`HEAD`** |

> **Scope caveat.** `wisp/config.py`, `wisp/composition.py`, `wisp/core/runtime.py`,
> `wisp/tool_executor.py`, `wisp/core/session.py`, `wisp/core/session_repo.py`, `wisp/auth/principal.py`,
> `wisp/auth/__init__.py`, `wisp/multi_agent/task.py` and `AGENTS.md` carried **pre-existing uncommitted
> work** from before the migration. It is included because it is interleaved with the migration's changes
> in the same hunks; the commit bodies say so. It could not be separated without reverse-engineering
> changes the migration did not make.
>
> **Resolved by `ade4dc6` (2026-09-25).** The whole POST-M13 chain is now committed, so the
> interleaving above no longer applies. What remains uncommitted is exactly §8's list — the user's
> own WIP — which was excluded deliberately and file-by-file.

### What `83b10af` itself contained (Phase 9/10)

**Canonicalizations:** run state (`runs/record.py::coerce_state`), path containment (`pathsec`, 4 call sites), error classification in the benchmark, background admission (`_admit`), approval verdict types moved `cli/` → `exceptions.py` (removed a core→cli inversion).

**State reclamation:** graph `_forget_run()`, background auto-prune, telemetry incremental byte accounting.

**Gates repaired:** `mypy` 16 errors → exit 0; `ruff` 1 error → clean; the `getpass` test that hung the suite forever now passes in 0.57s.

**Hygiene:** `setup.py` deleted, `testpaths = ["tests"]`, TUI tasks onto `OwnedTasks`, stale doc facts corrected.

**150 new tests across 14 files.** Cumulative: **18 FIXED · 2 PARTIAL · 5 INVALIDATED/CORRECTED**.

---

## 4. What remains uncommitted

The migration's own files are committed. What is still uncommitted is the **user's pre-existing WIP**
(§8) plus foreign-session test files — none of it the migration's.

### Phase 10's production changes — MOSTLY still uncommitted

⚠️ **Correction.** The migration commits touched only the files the migration needed. Phase 10's other
production changes are **still uncommitted** in the working tree — including `wisp/auth/decision.py`
(L4), `wisp/multi_agent/_runner.py` (F1), `wisp/multi_agent/subagent_orchestrator.py`, `README.md`,
`docs/SECURITY.md` and `tests/conftest.py`. Do not assume the table below is committed.

| File | Change |
|---|---|
| `wisp/multi_agent/_runner.py` | **F1** — `_budget_from_contract()`: a DAG node's declared budget is now applied narrow-only at both budget construction sites |
| `wisp/tools/_utils.py` | **F2** — deleted the superseded `_SENSITIVE_ENV_KEYS`; a note records why `_utils_env.py` is the authority |
| `wisp/policy/cli.py` | **Option C** — a `NOT ENFORCED` notice precedes every evaluating command (`inspect`/`verify`/`explain`/`dry-run`/`health`) |
| `wisp/policy/explain.py` | **Option C** — `explain_denial` says `"Rule: deny X"` (a statement about the bundle) instead of `"Denied X"` (a report of something that did not happen) |
| `wisp/pathsec.py` | **+`PROTECTED_PATH_FRAGMENTS`, `PATH_BEARING_ARGS`, `is_protected_path()`** — the canonical protected-path predicate |
| `wisp/tools/_utils.py` | `_is_hook_controlled_path` delegates; `_SENSITIVE_HOOK_DIR_FRAGMENTS` aliases the canonical set |
| `wisp/auth/decision.py` | L4 scans every `PATH_BEARING_ARGS` key via the canonical predicate. **P2** adds `_audit_authorization`: the verdict is now recorded for **allow and deny** |
| `wisp/server/deps.py` | `require_tool_allowed` applies the protected-path guard before the policy verdict |
| `wisp/core/events.py` | **+138 lines** — the canonical outcome authority: `OutcomeClass` (8 values), `OUTCOME_BY_STATUS`, `TERMINAL_OUTCOME_CLASSES`, `classify_status/_text/_result`, `is_error_outcome`, `is_terminal_outcome`, `is_denial_text`, `is_denial_outcome` |
| `wisp/transport/renderer.py` | `result_is_error` delegates (−24 lines). **Behaviour verified IDENTICAL on a 29-item corpus** |
| `wisp/tool_executor.py` | `_record_metrics` + `_note_fetch_outcome` delegate — **two live defects fixed**. **P2** adds `_audit_authorization`, one insertion after the allow/deny fork |
| `wisp/multi_agent/subagent_orchestrator.py` | `_is_denial` delegates; `_DENIAL_MARKERS` removed — **one live defect fixed** |
| `wisp/provider_catalog.py` | `list_models` docstring is now the declared canonical contract |
| `wisp/server/routes/hooks.py` | Target C — gate `create_hook` + `test_hook` |
| `wisp/server/routes/mcp.py` | Target C — gate `add_mcp_server` + `test_mcp_server` + `delete_mcp_server` |
| `wisp/server/routes/plugins.py` | Target C — gate `install_plugin` + `toggle_plugin` + `delete_plugin` |
| `wisp-desktop/src/renderer/hooks/useApi.ts` | `describeApiError()` surfaces the server's `detail` on non-OK (+25 lines); **2026-09-21 (§0f):** one header authority — `apiFetch` uses `makeAuthHeaders(apiKey)`, `getCheckpointDiff` is handed the key and reports via `describeApiError` |

### Files the migration added

`wisp/core/action_key.py` (P1), `wisp/core/proposal.py` (P2), `wisp/core/acceptance.py` (P3a), plus the
migration documents (§13) and eight test suites (§11).

### Tests (11 files, 231 tests)

⚠️ **A Phase-10-era inventory, not a re-measurable claim.** Per-file counts are as recorded then; the two
`fastapi.testclient` files cannot be collected here (F80), and `tests/test_m4_governance_wiring.py` now
**fails** (it asserts 2 `ToolExecutor` construction sites; there are 3). See §11 for the runnable block.

`tests/test_outcome_classification_authority.py` (67), `tests/test_rest_gate_boundary.py` (16), `tests/test_protected_path_guard.py` (26), `tests/test_authorization_parity.py` (18), `tests/test_m4_governance_wiring.py` (25), `tests/test_unwired_controls_inventory.py` (20), `tests/test_provider_listing_equivalence.py` (+6 → 19), `tests/test_layer_direction.py` (+4 → 11), `tests/test_server_policy_gate.py` (+10 → 14).

Plus two desktop-client guards (§0f): `wisp-desktop/src/renderer/hooks/useApi.test.ts` (5) and `authHeaderAuthority.test.ts` (4).

Plus `tests/conftest.py` — a new session-scoped autouse fixture neutralizing the
process-external rate-limit singleton (§0b §9 of the guard doc).

### Docs corrected for honesty (option C)

`README.md` (intro qualifier + a callout above the governance section + a
corrected graph bullet), `AGENTS.md` (policy-module row), `docs/SECURITY.md`
(policy-bundle bullet).

### The defect ledger — everything Phase 10 found and fixed

**None of these were on any prior list.** Seven were live defects; two were
security-relevant.

| # | Defect | Fixed |
|---|---|---|
| D1 | `tool_executor._record_metrics` decided success via `'"status": "ok"' in result`. Every successful **plain-text** result (most tools: `read_file`, `run_bash`, `git_diff`) failed it → the **live** metrics path counted successful calls as errors, deflating `(1-errors/calls)*100` at `metrics.py:102`. Also broke on compact JSON. | ✅ |
| D2 | `subagent_orchestrator._is_denial` matched prose only. **All five canonical denial statuses matched NOTHING** (`"[POLICY_DENIED]"` ≠ `"[denied"`). The comment beside it says "denials must never auto-retry" — the rule it could not enforce. | ✅ |
| D3 | `tool_executor._note_fetch_outcome` classified on `result_str[:200]` / `[:300]` substrings — whitespace/truncation sensitive, blind to plain-text errors. | ✅ (delta documented: a generic error envelope now counts toward the breaker) |
| D4 | The desktop client's real typecheck is `tsc -b`, not the `typecheck` script's `tsc --noEmit` — the latter is **vacuous** because the root `tsconfig.json` is project-references with `files: []`. `tsc -b` is red with **32 pre-existing errors across 12 files**. One is functional: `useApi.ts:368` calls `makeAuthHeaders()` with no argument, so the checkpoint-diff request sends **no `Authorization` header**. | ❌ **open (R10)** |
| **D5** | **The `.wisp/hooks` guard was absent from REST** — `POST /api/files` (and `/edit`, `/binary`, `/rename`, `DELETE`) wrote a hook the equivalent `write_file` *tool* was refused. Plus `new_path` unscanned and a boundary false positive. | ✅ (§0b) |
| **D6** | **The M4 organization policy layer is never loaded** — a governance control that appears to exist and does not. | ✅ false-assurance half (§0d); wiring open |
| **D7** | **The DAG node budget was built, attached, and silently dropped** — both runner sites constructed their own `ResourceBudget()` from contract fields. Worst for `max_tool_calls`, which the contract cannot express. | ✅ (§0e.2) |
| **D8** | **`_SENSITIVE_ENV_KEYS` was a superseded duplicate** with no consumer, encoding a stricter intent than the live pattern-based scrubber. | ✅ deleted (§0e.1) |
| **D9** | **The client's checkpoint-diff request was unauthenticated** — the one `useApi` method that cannot use `apiFetch` built its headers from a *second* helper called with no argument. The compiler flagged it (`TS2554`) all along, in a build the `typecheck` script never runs. Symptom was silent: the diff feature returned `''`. Root cause was not an absence but a **duplicate that had drifted** — the header has 28 construction sites across 19 files. | ✅ (§0f) |

**Two claims of mine were wrong and are recorded as such:** the Phase 1
"verification gate is exploitable" (it is correct), and the audit's "gating
breaks the shipped client" (§5). Two more were caught mid-draft by the guards I
wrote: the orphan scan's "dead modules", and the inventory's "unreachable
module" (§0e).

---

## 5. ✅ The wrong claim HAS BEEN CORRECTED

An earlier draft of the audit (§3.3) said gating the three routes **"breaks the shipped desktop client."** **That was wrong** — derived from `require_tool_allowed`'s docstring, not from the actual policy verdicts. Measured:

| action | full | auto_edit | ask_all | read_only |
|---|---|---|---|---|
| `hooks.create` | ALLOW | ALLOW | ALLOW | **DENY** |
| `mcp.add_server` | ALLOW | ALLOW | ALLOW | **DENY** |
| `plugins.install` | ALLOW | ALLOW | ALLOW | **DENY** |

**Gating does NOT break normal use.** It allows in `full`/`auto_edit`/`ask_all` and denies only in `read_only` — which is correct. The policy is **mode-based**: unknown action names default to allow except in `read_only`; `hooks.create` is not a defined rule anywhere in the source.

So option B was safe all along. **Corrected in** `PHASE_10_AUTHORITY_CLOSURE_AUDIT.md` §3.3 (with the reasoning error recorded, not quietly edited) and in the `PHASE_FINDINGS_REMEDIATION_REPORT.md` closing-question table.

This is the **fifth** instance of the §10 pattern: a conclusion drawn from *reading* that dissolved under *execution*.

---

## 6. Environment gotchas (cost me real time — do not rediscover)

| Gotcha | Detail |
|---|---|
| **Clear `PYTHONPATH`** | The WorkBuddy `sitecustomize.py` shim blocks pytest's temp `mkdir` → **false test failures**. Always `env -u PYTHONPATH`. |
| **Clear proxies** | Ambient `HTTP_PROXY`/`HTTPS_PROXY` leak into hermetic subprocess tests → spurious `502`. Use `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy`. |
| **`getpass` reads `/dev/tty`** | `< /dev/null` does NOT stop a credential-prompt hang. (Fixed in Phase 9, but the lesson stands.) |
| **BSD `grep`** | `--include` must come BEFORE the path or it silently matches nothing. This caused a **false** "tiktoken is unused" finding. |
| **zsh does not word-split** | Unquoted `$VAR` stays one word. Use `xargs` or `${=VAR}`. |
| **`grep -E "a\|b"`** | Fails silently in this shell. Use `grep -E "a|b"` or separate greps. |
| **Tooling locations** | `ruff`: `/opt/anaconda3/envs/litllm/bin/ruff` · `mypy`: `/Library/Frameworks/Python.framework/Versions/3.12/bin/mypy` (neither in `.venv`) · project venv: `.venv/bin/python` (3.12.8) |
| **mypy version** | `.venv` has none; `uv.lock` pins **2.3.1**. Use `uv run --no-project --with "mypy==2.3.1" mypy` for the true CI verdict. |
| **`.venv` still lacks some declared deps — `jsonschema` no longer does** | `aiohttp`, `tiktoken`, `prompt_toolkit`, `cryptography` (Phase 10) **+ `numpy`** remain absent. `jsonschema` was the serious one and is **fixed** — see the next row. |
| **✅ `jsonschema` — FIXED 2026-09-24 (this was F8)** | `_validate_tool_args` (`core/stateless.py:2285-2298`) imports `jsonschema` inside a `try` and converts the `ModuleNotFoundError` into a validation-failure **string**, so while it was missing **every tool call was refused before dispatch** and the failure was misdirected at the tool. It was **never a packaging problem**: the dependency was *declared* in `pyproject.toml` and *locked* in `uv.lock` — the venv simply lacked it. Provisioned **offline** from the uv cache at exactly the locked versions, with **0 production changes** and the lockfile/manifest byte-identical. **Tools really execute now** — a real read, a real write on disk, and an approved `run_bash` with captured stdout. **Install recipe:** `env -u PYTHONPATH UV_OFFLINE=1 ~/.local/bin/uv pip install --python .venv/bin/python --offline '<pkg>==<locked-version>'` (`uv` is **not on `PATH`** but lives at `~/.local/bin/uv`). **Two traps:** the interpreter has **no CA path** (`ssl.get_default_verify_paths()` → `cafile: None`), so `pip`/`urllib` fail TLS even though the machine has egress — `SSL_CERT_FILE=<certifi>/cacert.pem` fixes it; and **`.venv/bin/pip` has a broken pre-move shebang**, so use `python -m pip` or `uv`. The **error-classification** half of F8 is still unfixed — see F8's resolution row in the ledger. |
| **⚠️ CORRECTED 2026-09-25 — the editable install is a NO-OP, and the old claim was INVERTED** | This row used to read: *"`__editable___wisp_0_1_0_finder` resolves `wisp` ahead of `sys.path`, so **`PYTHONPATH` cannot override which package is imported**."* **Measured, that is backwards.** The finder's `MAPPING` resolves `wisp` to `/Users/philosopher/Documents/wisp/wisp` — the **pre-move** path — and **that directory does not exist**, so the finder returns nothing and `import wisp` from a foreign cwd raises `ModuleNotFoundError: No module named 'wisp'`. With `PYTHONPATH` pointed at the repo root it imports fine, so **`PYTHONPATH` CAN override**. What actually resolves `wisp` is the **cwd** (`./wisp`, because `python -m pytest` puts the cwd first). **The practical rule is unchanged** — change files in place to compare against a baseline — but for a different reason, and the real hazard is the one the old row did not name: **run from anywhere but the repo root and `wisp` resolves to nothing**, or to a foreign tree if one is on `PYTHONPATH`. Finding **F70**; reported, not repaired (repairing the venv is an environment change, and §9 keeps F8's env work separate from architecture). |
| **`git stash` is the wrong baseline tool here** | The tree has pre-existing uncommitted work in the same files. Stashing only your files reverts them to HEAD and discards it — producing false "regressions". Snapshot to a path outside the repo instead. Finding F12. |
| **BSD `grep` via Bash is unreliable here** | `--include` silently matches nothing (caused a false "tiktoken is unused"), and a plain `grep -n "a\|b" file` returned empty with exit 1 for a pattern that plainly exists. **Use the Grep tool, not the shell.** |
| **Long background runs get reaped** | The harness kills long pytest runs without writing a summary. Report per-subsystem results; **do not claim a full-suite pass.** |

### 6.1 The declared-and-absent dependencies (one table, four phases' worth of rediscovery)

A declared-and-absent dependency is an **environment state**, not a per-mission discovery. Each of these
was found in its own phase; this table is the one place they are stated together, with what each one
makes **un-quotable**.

| dependency | `uv.lock` pin | check it is absent | files that cannot run | counts NOT quotable |
|---|---|---|---|---|
| **`httpx`** | `0.28.1` | `env -u PYTHONPATH .venv/bin/python -c "import httpx"` | **9** `TestClient` files: `test_api_key_security`, `test_mcp_route_binding`, `test_policy_routes`, `test_protected_path_guard`, `test_security_integration`, `test_server_background_routes`, `test_server_blocking_routes`, `test_server_policy_gate`, `test_server_sessions_store` | **all of them.** Two (`test_protected_path_guard`, `test_server_policy_gate`) are in §11's Phase-10 block, which therefore **aborts at collection** — its old "(245 passed)" was never producible here (**216 passed, 2 failed** with both ignored). Finding **F80** |
| **`cryptography`** | `50.0.1` | `… -c "import cryptography"` | `test_policy_bundle.py`, `test_policy_modes.py`, `test_policy_cli.py`, `test_policy_routes.py` — anything calling `generate_keypair`/`sign_bundle`, because `verify_bundle` swallows the failed import and returns `False` for **everything** | **the M4 policy suite.** Driven per file: bundle **4 failed / 3 passed**, modes **10 failed**, cli **2 passed / 5 errors**, routes **1 error** (httpx), precedence **8 passed**, enforcement **5 passed** — **14 failed, 6 errors, 18 passed** across the six. None is in the canonical block. Finding **F88** |
| **`numpy`** | `2.4.6` | `… -c "import numpy"` | the numeric benchmarks; no test file | — (open; nothing quotes a number from it) |
| **`tiktoken`** | `0.14.0` | `… -c "import tiktoken"` | P8's token-budget measurement — it cannot be measured faithfully | **P8's token budgets.** An estimate is not a measurement |
| **`aiohttp`** | `3.14.3` | `… -c "import aiohttp"` | no test file imports it directly | — |
| **`jsonschema`** | `4.26.0` | — | **none** | — |

**`jsonschema` is the one that was fixed** (2026-09-24, offline from the uv cache) — that was **F8**, and
it was the serious one: while it was missing *every tool call was refused before dispatch*. Its row stays
here so the pattern is legible.

**The offline install recipe** (already in §6's table, repeated because this is where it is used):

```bash
env -u PYTHONPATH UV_OFFLINE=1 ~/.local/bin/uv pip install \
  --python .venv/bin/python --offline '<pkg>==<locked-version>'
```

**Re-attempted 2026-09-25** (the cache could have been populated since): **`httpx` and `cryptography`
both still fail** with *"was not found in the cache … the network was disabled"*. The uv cache exists
and is populated (`archive-v0`, `builds-v0`, `sdists-v9`, `simple-v24`, `wheels-v6`) and holds no
`httpx` or `cryptography` entry of any kind. **Closing either needs network access, not a code change.**
Until then the "not quotable" column stands.

---

## 7. Known failures — the environmental set (NOT regressions)

**Measured on the full `tests/` tree with `--continue-on-collection-errors`:**

| Run | Count | Note |
|---|---|---|
| `83b10af` (baseline) | **131** | |
| P0 – P6 (single runs each) | **128** | consistent across four measurements |
| P7 run 1 / run 2 | 129 / **130** | identical code — **the count is NOT stable** |
| P7 without its own test file | **129** | failure set byte-identical to run 1 → **P7 contributes zero** |
| P8, P9, M2, M3, M4 | **129** | each byte-identical to the stable baseline in **both** directions |

**⚠️ THE STABLE SET OF 129 IS STALE — do not quote it as current.** Every number in the table above was
measured while `jsonschema` was absent, which means it **conflated F8's effects with everything else**. The
F8 provisioning phase settled the question for one whole directory: **`tests/reliability/` went from 24
failures to 0**, including `test_13h2_determinism.py` (6→0, logged as "pre-existing" F10),
`test_13j1_fanout_contract_repair.py` (13→0) and `test_13j_fanout_contract.py` (5→0). Those attributions
were wrong. **The full-suite set must be re-measured before it is used as a baseline again.**

**What is known now, measured after provisioning and re-measured after F37 / F39:**

| Suite | Result |
|---|---|
| migration suites (canonical set, §11) | **849 tests — 848 pass, 1 fails** (F38) |
| `tests/reliability/` | **378 passed, 0 failed** — identical in one process and per file |
| the focused stagnation / post-M13 set | **220 passed** |

**Correction (2026-09-25).** This table previously read **385** for `tests/reliability/`, and the F37
phase's report read **386** for the same directory. Both were wrong: the per-file data recorded by
those phases **sums to 378**, and a fresh per-file run reproduces **378 exactly**, file for file, with
zero change. The number is **378**; the two earlier figures were counting errors, not measurements.

The residual failures are dominated by **`httpx`** (**9** starlette `TestClient` files — measured
2026-09-25 by AST-scanning `tests/**` for a `testclient` import; the line read "11" until then, which was
an estimate rather than a measurement, F80), the 17 collection `ERROR`s from foreign-session WIP files,
and whatever the re-measurement finds. `jsonschema` is no longer among them, and neither are the three
files above.

**Three known flaky / order-dependent tests — the count moves ±1 because of them:**

| Test | Behaviour | Finding |
|---|---|---|
| `tests/test_cli_surface_e2e.py::TestGroup3Headless::test_print_blackhole_server_falls_back` | depends on a **network timeout**; appears in one run, not the next | F20 |
| `tests/test_sandbox_fallback_contract.py::test_fallback_host_warns_at_tool_layer` | fails in the full run, **passes 5/5 in isolation** | §7 (Phase 10) |
| `tests/test_speculative_search.py::TestOracle::test_smallest_diff_wins_ties_broken_by_speed` | appeared once, **absent on an immediate rerun of the identical tree** | F17 |

---

## 8. User's pre-existing WIP — do NOT commit or delete

- `wisp/core/graph/__init__.py` — a modified docstring that predates this session (only unstaged tracked change)
- `wisp/multi_agent/_circuit_breaker.py` — untracked; I made a 1-line unused-import fix but the file is theirs
- `wisp/capability_filter.py` — untracked
- `PHASE13_*`, `phase13_*`, `FANOUT_*`, `tests/reliability/test_13i1_*`/`test_13i2_*`, the 4 uncollectable test files, `.agents/`, `.aionrs/`, `.workbuddy-ai/`

**Pre-existing latent break:** `wisp/core/stateless.py:291-292` and `:1177-1178` (tracked) do a guarded `from wisp.capability_filter import ...` — but `capability_filter.py` is **untracked**. Guarded by `capability_filtering`, default **False**, so a fresh clone works until `WISP_CAPABILITY_FILTERING=true`, then `ModuleNotFoundError`. Present in `HEAD~1` too.

---

## 9. Rules the user set (Phase 10 brief) — still binding

**RULE 1 — do NOT reopen closed work** unless new evidence proves it wrong: canonical execution state, path containment, background admission, verification contract, telemetry accounting, graph run-state reclamation, background retention, TUI task ownership, doc drift, packaging, **F17/F18 (sandbox routing — INVALIDATED, deliberate tested design)**, F21, F22, F25, the benchmark error-classification work, the provider-model inversion. **F23/F24 (git hygiene) must not be done as an architectural side effect. F8 (env deps) is separate from architecture.**

**RULE 2 — find the canonical authority first:** map implementations → map consumers → identify semantic differences → identify the authoritative behaviour → decide if canonicalization is safe → define the target contract → only then modify.

Also: *"Do not manufacture a closure merely to improve the Phase score. The goal is architectural truth."* And: *"Do not claim the full suite passes unless it actually does."*

---

## 10. Recurring lesson — worth preserving

**A tool that cannot see the whole picture reports ABSENCE as DEATH.** This happened **nine times**:

1. Phase 2 — the verification gate looked exploitable until I drove the real producer into it. **My finding was wrong.**
2. Phase 4 — `_CONTEXT_TTL` looked unbounded; it is keyed by `kind`, bounded to 3 entries. **My finding was wrong.**
3. Round 3 — the orphan scan called `resource_budget.py` dead; it is imported via **relative** imports my absolute-path scan could not see. **3 of 4 claims wrong.**
4. Round 4 — the sandbox routing divergence looked like a bug; two tests explicitly encode it as deliberate.
5. Phase 10 — the REST audit claimed gating breaks the client; the actual policy verdicts say otherwise (see §5).
6. Phase 10 — asking whether R1b survived Target C revealed a *different* route (`/api/files`) bypassing the hook-dir guard entirely (§0b). **The question was wrong; the answer was a real defect.**
7. Phase 10 — G1 reframed from "one is weaker" to "different rule sets, incompletely composed" once measured (§0c).
8. Phase 10 — following G1 one step further showed the thing both models disagree about is **never set at all** (§0d). **The seventh instance's premise was wrong too.**
9. Phase 10 — the unwired-controls sweep called `semantic_compressor.py` unreachable. **False** — a *deferred* import inside `session_dto.compact()` reaches it (§0e). **My own pinning test caught it.** Cause: BSD `grep` ignoring `--include` after the path — the third false "unreferenced" verdict from that one bug.

Instances 6–8 are one chain: each question was answered, and the answer exposed
that the next assumption up was false. The method that produced all three is the
same — **ask what the two paths do with the same input, then ask where that
input comes from.**

**Corollary, also proven four times: mechanical guards find what manual review misses** — and, increasingly, what *I* missed. The containment structural scan found a 4th re-implementation; the relative-import fix overturned 3 of 4 "dead module" claims; the doc-drift guard found `_MockIO` in `CLAUDE.md`; the inventory ratchet caught the `semantic_compressor` error before anyone read the claim.

**Practical rule:** drive the real path; write the guard; then report.

### The instrument is not exempt from the discipline

**A probe with a defect is invisible, because the probe is what catches the other defects.** Six
instances, and every one was found by *accident* — a probe that ran, a flake that was investigated, a
test that failed for a reason someone noticed. None was found by looking for it.

| # | Where | The instrument | The defect it hid |
|---|---|---|---|
| 1 | `PHASE_POST-M13_F39_MECHANICAL_DIAGNOSTIC_IMPLEMENTATION.md` (**F41**) | a test double that raised from `post()`, so the `with` block never ran | the 4xx error-body log had **never logged a body** |
| 2 | `PHASE_NEXT_AUTONOMOUS_CODING_AGENT_CONVERGENCE.md` (**F54**) | every test built its core through `CompositionRoot` or injected a fixture executor | `make_ollama_core_factory` was never exercised; `wisp bench` refused every mutation |
| 3 | `PHASE_STRUCTURED_CRITERIA.md` §5 | a size-preserving mutation left stale bytecode; a same-second restore reported a **false control failure** | the harness reported its own defect as the subject's |
| 4 | `PHASE_PRECEDENCE_CORRECTION.md` §5, §6 (**P5**, **Q1**) | a helper that returned the wrong section; a regex `+` group that let an emptied finding skip the check | two checks that could pass by **finding nothing** |
| 5 | `PHASE_DAG_RETIREMENT.md` §7.1 | a bare string scan over `wisp/**/*.py` | it counted a **docstring** as a caller |
| 6 | `PHASE_CRITERIA_SOURCE.md` §6 | `test_current_authorities_pins.py` checks a pinned line is **non-blank** | it caught **1 of 5** stale pins and passed four that had moved onto other, non-blank lines — **CLOSED** by `PHASE_GATE_ENABLEMENT_DECISION.md` §11 (the check now asserts the pinned **content**: the window around the pin must contain an identifier the page's prose names on that line, and the checkable-pin count has a floor) |

**The class, stated once:** *the instrument does not reproduce the production control flow, or does not
fail when the subject fails, and reports its own defect as a result about the subject.*

**The fourth sub-case — the instrument's SUBJECT was the wrong thing.** Added 2026-09-25. The first five
instances are all *a broken instrument*: it ran, and its result was not what its claim said. This one is
different and harder to see, because **the instrument was working perfectly and its result was sound — it
was sound about the wrong thing**, and it looked like a fact for three phases.

| Where | The instrument | What it was actually about |
|---|---|---|
| `PHASE_AUTHORIZATION_PARITY.md` §6 (**F78**) | `tests/test_authorization_parity.py` compared `authorize()` to `SecurityPolicy.check()` and called the first *"the agent's verdict"* | two **models**, not the two **paths** its name, its docstring and its table all described. Driven against the real paths: **0 divergences of 36**, where the table had claimed 6 — on three action names the agent **cannot execute at all** |
| `PHASE_OBJECTIVE_FLAG_COMPOSITION.md` §6 (**F82**) | two "canonical suite" command blocks, each quoting a count | **one of two** blocks, and they listed different file sets (43 vs 41) — so neither count described the intersection the method calls for |
| `PHASE_CORPUS_INTEGRITY.md` §2.1 (**F86**) | a table classifying **two** red Phase-10 guards as one class, on the evidence of **one** | *"contract updates — exactly F38's class"* is right for the M4 count guard (a legitimate site was added) and **wrong** for the outcome-classification guard, where the **code** violated the rule and the test was doing its job. F78's shape one level up: a claim about **two** things, derived from **one** |

**The tell is a table whose NAME and whose SUBJECT have drifted apart.** A ratchet called *parity between
the paths* that drives two functions; a block called *the* canonical suite when there are two. Neither is
visible by reading the instrument's output — only by asking **what it actually drives**. And the
consequence is worse than a broken probe: a broken probe gives a wrong answer about the right subject,
whereas this gives a **right answer about the wrong subject**, which is indistinguishable from a finding.

**The discipline it adds:** *before trusting a comparison, drive the two things its name says.* And when a
field is compared, check that something **reads** it — a computed value with no consumer is a fact about
the function that computes it, not about the system. (F83, the next mission, is the same shape applied to
a *claim* rather than an instrument: ADR-0055 §Context called the WebSocket channel *"bidirectional
approval"* without driving it; the question direction had never reached a client.)

**The fifth sub-case — a raise that does not discriminate.** Added 2026-09-25. `pytest.raises(X)` is
satisfied by **any** `X`, so a test whose property is *"this refuses, for this reason"* passes on a
refusal for a different reason. The instrument reports the right **type** and the wrong **fact**.

| Where | The instrument | The defect it hid |
|---|---|---|
| `PHASE_KEY_TRUST_WORKFLOW.md` §6 (**NV1**) | `pytest.raises(ValueError)` on an unverifiable bundle | with the signature check disabled, the loader reached `_bundle_to_effective` and raised **F89**'s unrelated `ValueError` — the same type, from the same call. The test passed while the property was broken. Fixed by pinning the **message** |
| `PHASE_CORPUS_INTEGRITY_III.md` §2.1 (the fix's own test) | the same shape, inverted | the F89 pin is only discriminating if the **pre-fix** message *fails* it — so that is asserted, by reproducing the old expression |

**The tell:** the assertion names a **type**, and that type has more than one producer. **The discipline:**
pin the message, or assert the *identity* of the failure — never the type alone.

**The sixth sub-case — a guard that pins a STATE rather than a PROPERTY.** Added 2026-09-25. A guard whose
subject is a *snapshot* fails on the next legitimate change, so it is either weakened or, worse, believed.
It is the **inverse** of a broken instrument: it works, it fails, and it fails **for the wrong reason**.

| Where | The instrument | What it actually pinned |
|---|---|---|
| `PHASE_M4_WIRING.md` §3 (**F92**, first) | `test_the_loader_entry_points_have_no_runtime_caller` asserted an exact **set** of callers | a *test* file driving the loader made it fire, although the tripwire's own docstring says *"the CLI and **tests**"*. Repaired to a rule (CLI + tests, nothing else) with a floor |
| `PHASE_M4_WIRING.md` §3 (**F92**, second) | `test_the_distribution_surface_is_unconfigured_in_production` scanned for the **bare name** `policy_pubkey=` | it matched the *config setting* ADR-0058 added, not the route attribute it is about. Repaired to `state.policy_pubkey` — what its sibling test already did |
| `PHASE_REST_AUTHORIZATION_COMPOSITION.md` §6.1 (**F96**) | `test_rest_authorizes_as_the_same_principal_as_the_agent` **recomputed** the REST principal and compared it with the agent's | a *copy* of the production call, not the production call — so mutating `deps.py` did not falsify it. The **observation point** must be the code under test |

**The tell:** the guard's failure message describes a *situation*, not a *violation*. **The discipline:**
write it so it fails on a real violation and stays silent on the next legitimate addition; and make sure
what it observes is the production path, not a reconstruction beside it.

**The discipline:**

- **A probe that does not falsify is not a pass — it is a finding that the test is not testing what you
  think.** Investigate the flake; do not retry it. Three times now: ADR-0052's NV1, `PHASE_STRUCTURED_CRITERIA`'s
  P2 (a baseline keyed to the wrong criteria id), and ADR-0053's NV1 — where the *helper* took the answer
  as a parameter and set the flag itself, so the predicate under test was never exercised.
- **A check that can pass by finding nothing has not run.** Every check whose subject is a collection —
  findings, pins, callers, imports — needs a **floor**: assert the collection is non-empty before
  iterating it.
- **A check over a Python tree must parse it, not scan it.** AST, not strings; a string scan reads
  docstrings and comments as code (instance 5).
- **A test double must reproduce the production control flow it replaces**, or say explicitly that it does
  not — and the test must fail if the production path changes in a way the double does not model.
- **A count is canonical only if it is measured after the LAST change to any member (F85).** F71 said
  *never quote a count from prose*; F82 said *only if there is ONE block*; F85 says the measurement must
  follow the last change, not the change that motivated measuring. Measured: the canonical block reported
  **1316** where both headings said **1312**, because one deliverable set the headings and the next added
  four tests to a block member. Measured again by the M4 wiring (**F93**): **1390 → 1437** on **+3 files**,
  re-measured in the same change that added them.
- **A guard that pins a STATE rather than a PROPERTY is a nuisance — and it fails for the wrong reason
  (F92).** Write it so it fails on a real violation, not on the next legitimate addition. Measured twice
  in one mission, on the M4 tripwires: one pinned an exact **set** of callers, so a *test* that drives the
  loader made it fire though its own docstring said tests were expected; the other scanned for a **bare
  name** (`policy_pubkey=`), which matched an unrelated, same-named **config setting** rather than the
  route attribute it was about. Both repaired, both probed **in both directions** — a real violation
  CAUGHT, the legitimate addition MISSED. **The tell:** a failure whose message does not describe the
  thing that changed.
- **The instrument is not exempt.** A check that has never been falsified has not been validated.

---

## 11. Verification commands that actually work

```bash
# Gates — ⚠️ NEITHER IS GREEN AT HEAD, corrected 2026-09-25 (F71)
/opt/anaconda3/envs/litllm/bin/ruff check wisp/          # 11 errors, all pre-existing
uv run --no-project --with "mypy==2.3.1" mypy wisp/      # 1844 errors in 228 files

# This block used to say "Gates (both must be green)". Neither is. Measured at d7a55c2: ruff reports
# 11 errors (incl. an F821 undefined name in wisp/auth/principal.py and wisp/context_assembler.py),
# and the pinned mypy reports 1844 errors in 228 files — not zero. Every phase that claimed a green
# lint/type criterion claimed something untrue. Both sets are UNCHANGED by the 2026-09-25 chain
# (verified by set diff: 0 new, 0 gone), so the chain neither caused nor repaired them. Finding F71.

# Phase 10 tests — MEASURED, and the block had to be repaired to run at all.
#
# The heading said "(245 passed)" until 2026-09-25. That number was NOT producible
# in this environment: the block ABORTS AT COLLECTION, because TWO of its files
# import `fastapi.testclient`, which needs `httpx`, which is not installed in .venv
# and is not in the uv cache (nor anywhere on this host) — finding F80.
#
#   ERROR tests/test_protected_path_guard.py  - starlette.testclient requires httpx
#   ERROR tests/test_server_policy_gate.py    - starlette.testclient requires httpx
#
# With those two ignored, the block now reports **218 passed, 0 failed.** Both of the
# guards it was reporting RED have been closed, each by the mission that owns it:
#
#   test_outcome_classification_authority.py::test_no_module_reimplements_tool_result_status_classification
#       `wisp/core/stateless.py:158,167` compared `.get("status")` to "ok"/"error"
#       directly; introduced by ade4dc6 (POST-M13 execution semantics).
#       **FIXED** (outcome-classification mission): the success test is delegated to
#       `core.events.is_error_outcome` in both branches — 35-case differential
#       identical on the production function. **The guard was RIGHT and the code was
#       wrong**, so calling this one a "contract update" was **F86**. It is in the
#       canonical block now, so it can no longer be red-and-unseen.
#   test_m4_governance_wiring.py::test_tool_executor_construction_sites_are_known
#       asserted 2 ToolExecutor construction sites; there are 3. **UPDATED** (corpus
#       integrity II) to 3, and the files are named so a swap cannot hide behind the
#       count. The third is `wisp/benchmark/runner.py`, added by 8a7e9ab and
#       **authorised** by ADR-0045's F54 fix — a genuine CONTRACT UPDATE, exactly as
#       PHASE_CORPUS_INTEGRITY.md §2.1 classified it. (`PHASE_CORPUS_INTEGRITY.md`
#       said the third was `wisp/acp_session.py`, from cef3e90; driven, that is wrong
#       on both counts — `acp_session.py` was one of the ORIGINAL two.)
#
# Neither was in the canonical block, which is why nothing caught them. The
# outcome-classification guard is in it now; the M4 count guard is not (it is a
# Phase-10 contract guard, and its own tripwire is what matters).
#
# RULE (F71, restated): do not quote a count that has not been produced in THIS
# environment. The two httpx-dependent files are listed but ignored until httpx can
# be installed. **Re-attempted by corpus integrity II:** `httpx` is still absent from
# the cache and from the host; the two files still ERROR at collection with
# `RuntimeError: The starlette.testclient requires httpx`. Finding F80 stays OPEN.
env -u PYTHONPATH .venv/bin/python -m pytest \
  tests/test_outcome_classification_authority.py \
  tests/test_rest_gate_boundary.py \
  tests/test_authorization_parity.py \
  tests/test_m4_governance_wiring.py \
  tests/test_unwired_controls_inventory.py \
  tests/test_provider_listing_equivalence.py \
  tests/test_provider_model_authority.py \
  tests/test_layer_direction.py \
  tests/test_doc_drift.py \
  --ignore=tests/test_protected_path_guard.py \
  --ignore=tests/test_server_policy_gate.py \
  -q -p no:cacheprovider            # 216 passed, 2 failed (both pre-existing, above)

# The policy tests need `cryptography`, which .venv lacks — supply it isolated:
env -u PYTHONPATH uv run --no-project --with "cryptography==50.0.1" \
  --with pytest --with pytest-asyncio --with fastapi --with httpx \
  --with pydantic --with pyyaml python -m pytest tests/test_policy_*.py \
  tests/test_m4_governance_wiring.py -q -p no:cacheprovider   # 68 passed

# Desktop client — the REAL typecheck (tsc --noEmit checks nothing here)
cd wisp-desktop && npx tsc -b

# Broad regression (expect the known environmental failures only)
env -u PYTHONPATH -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
  .venv/bin/python -m pytest tests/ -q -p no:cacheprovider --tb=no -rf \
  --ignore=tests/test_auto_delegate_defense.py \
  --ignore=tests/test_delegation_research_only.py \
  --ignore=tests/test_input_and_interrupts.py \
  --ignore=tests/test_subagent_enterprise.py
```

**Note on the numbers.** The broad sweep reports **27 failed, 5 errors** when
the policy files are included. All 27 are accounted for: **20** are the missing
`cryptography` (they pass 68/68 when it is supplied), and **7** are the known
environmental set in §7. Never quote "the suite passes" — quote the set.

**⚠️ These counts predate the F8 provisioning and are stale.** They were taken with
`jsonschema` absent, so they include F8's effects. The `tests/reliability/` measurement after
provisioning (24 failures → 0) shows the magnitude of the error. Re-measure before comparing.

### Canonical suites — 1453 tests (1452 pass, 1 fails)

```bash
env -u PYTHONPATH .venv/bin/python -m pytest \
  tests/test_durable_layer_reachable.py tests/test_turn_journal_incremental.py \
  tests/test_action_idempotency_key.py tests/test_proposal_boundary_records.py \
  tests/test_proposal_boundary_no_bypass.py tests/test_verdict_layer_recorded.py \
  tests/test_gate_order_corpus.py tests/test_acceptance_verdict.py \
  tests/test_task_graph_materialization.py tests/test_graph_mutation.py \
  tests/test_recovery_ladder.py tests/test_stagnation_detection.py \
  tests/test_context_trust.py tests/test_structured_delegation.py \
  tests/test_session_reconstruction.py tests/test_durability_preconditions.py \
  tests/test_escalation_durability.py tests/test_execution_view_projection.py \
  tests/test_child_principal_wired.py tests/test_prompt_section_trust.py \
  tests/test_failure_signal_classification.py tests/test_node_identity.py \
  tests/test_stagnation_live_wiring.py tests/reliability/test_killpoints.py \
  tests/reliability/test_post_m13_authority_implementation.py \
  tests/reliability/test_post_m13_completion_enforcement.py \
  tests/reliability/test_post_m13_stagnation_gate_validation.py \
  tests/reliability/test_f8_tool_execution_restored.py \
  tests/reliability/test_verification_evidence_adapter.py \
  tests/reliability/test_next_convergence_controller.py \
  tests/reliability/test_next_autonomous_wiring.py \
  tests/reliability/test_progress_aware_recovery.py \
  tests/reliability/test_multi_turn_productive_recovery.py \
  tests/reliability/test_current_authorities_pins.py \
  tests/reliability/test_criteria_derivation_authority.py \
  tests/reliability/test_f8_error_classification.py \
  tests/reliability/test_precedence_canonical.py \
  tests/reliability/test_structured_criteria.py \
  tests/reliability/test_gate_enablement_contract.py \
  tests/reliability/test_dag_retirement_contract.py \
  tests/reliability/test_f8_published_status.py \
  tests/reliability/test_criteria_source_on_turn_path.py \
  tests/reliability/test_acceptance_gate_enablement.py \
  tests/reliability/test_objective_flag_composition.py \
  tests/reliability/test_rest_approval.py \
  tests/test_outcome_classification_authority.py \
  tests/reliability/test_outcome_classification_delegation.py \
  tests/reliability/test_key_trust_workflow.py \
  tests/reliability/test_m4_policy_wiring.py \
  tests/reliability/test_rest_authorization_composition.py \
  tests/test_m4_governance_wiring.py -q
```

**Measured 2026-09-25, after the F8 error-classification landing: 1115 tests — 1114 pass, 1 fails.** The
failure is
`test_node_identity.py::TestANodeReferencesItsWorkUnit::test_a_parallel_round_is_journaled_as_one_exchange_per_call`
(**F38**): it pinned the exchange ordering that only existed because F8 refused every call pre-dispatch.
Now that calls genuinely dispatch, the grouping rule produces the batch shape it explicitly supports
(`call:c0+c1`). The production behaviour is *more* correct, not less — the test needs a one-line contract
update, which requires explicit authorization.

**The heading said "849 tests" until 2026-09-25, "714 tests" before that, "1115 tests" until the
gate-enablement mission, "1289" until the acceptance-gate-enablement mission, "1314" until its
Deliverable 2, "1317" until the authorization-parity mission, "1312" until the
outcome-classification mission, "1390" until corpus integrity II, and "1437" until the
REST-authorization-composition mission.** None was current for long: the command block had never been
extended with the POST-M13 files (714 → 849), the four NEXT-mission files and the three 2026-09-25
files (849 → 1115), or the guards the later missions added. Finding **F71**. **Do not quote a count
from prose** — run the block.

**And "1312" was stale within the mission that wrote it (F85).** The corpus-integrity pass measured the
block in **Deliverable 1** and set both headings; **Deliverable 2** then added **four** tests to
`tests/reliability/test_current_authorities_pins.py` — a block member — and the headings were never
re-measured. Measured now, the same 45 files collect **1316**, not 1312. F71 said *never quote a count
from prose*; F82 said *a count is canonical only if there is ONE block*; **F85 says a count is
canonical only if it is measured after the LAST change to any member** — and a heading written in
Deliverable 1 is stale the moment Deliverable 2 touches a file the block names.

**The two blocks were out of sync (F82).** Until the authorization-parity mission, `AGENTS.md`'s block
listed **two files this one did not** — `test_precedence_canonical.py` and `test_structured_criteria.py` —
so the two "canonical" counts described different suites and neither was the intersection. Both now list
the same **50 files**, and both headings carry the count measured from that block.

### The regression method — read this before changing anything

**A single-run failure count is NOT a baseline.** Two consecutive runs on identical code read **129** and
**130**. Use the **stable set**, which is the *intersection of two runs*.

**⚠️ The stored baseline is stale.** `.workbuddy-ai/memory/baseline-failures-stable.txt` holds **129**
entries measured **with `jsonschema` absent** — so it is not a baseline for the current tree, and at least
24 of its entries (the whole `tests/reliability/` set) were F8-caused. **Rebuild it before using it.**
Until then, compare against a *fresh* two-run intersection.

```bash
# LC_ALL=C is load-bearing: comm line-walks and expects a common collation, and a
# baseline sorted under a different locale reports identical sets as different (F31).
env -u PYTHONPATH .venv/bin/python -m pytest tests/ -q -p no:cacheprovider --no-header \
  --continue-on-collection-errors --tb=no 2>&1 \
  | grep -E "^(FAILED|ERROR)" | LC_ALL=C sort > /tmp/after.txt

LC_ALL=C comm -13 .workbuddy-ai/memory/baseline-failures-stable.txt /tmp/after.txt   # NEW failures
LC_ALL=C comm -23 .workbuddy-ai/memory/baseline-failures-stable.txt /tmp/after.txt   # now PASSING
```

**The full suite cannot run in one process on this host** (F36): chunk it per directory or per file, union
the results, and **say that the method was weaker than a two-run intersection**.

Both directions must be empty. The baseline lives **in the repo** because `/tmp` did not survive a
reboot and lost P0–P6's.

**Known flaky / order-dependent — the count moves ±1 because of these:**

| Test | Behaviour |
|---|---|
| `tests/test_cli_surface_e2e.py::TestGroup3Headless::test_print_blackhole_server_falls_back` | network timeout; appears in one run, not the next |
| `tests/test_sandbox_fallback_contract.py::test_fallback_host_warns_at_tool_layer` | fails in the full run, **passes 5/5 in isolation** |
| `tests/test_speculative_search.py::TestOracle::test_smallest_diff_wins_ties_broken_by_speed` | appeared once, absent on an immediate rerun (F17) |

### Comparing against a baseline — do NOT use `git stash`

The tree has **pre-existing uncommitted work in the same files**. Stashing only the files you touched
reverts them to HEAD, discards that work, and makes unrelated failures look like yours (F12). Snapshot
the files to a path **outside the repo** first, then compare.

## 12. Open items

| # | Item | Nature |
|---|---|---|
| R1 | ~~REST gate — finish option B~~ | ✅ **DONE** (§0) |
| R2 | ~~Correct the "breaks the client" claim~~ | ✅ **DONE** (§5) |
| **G0** | ~~REST bypass of the protected-path guard~~ | ✅ **DONE** (§0b) |
| **E** | **M4 governance layer not wired to the runtime** — `wisp/policy/` was never loaded; `ToolExecutor.policy` was `None` at **all three** construction sites (`composition.py:142`, `acp_session.py:208`, `benchmark/runner.py:82` — the third added by `8a7e9ab` and authorised by ADR-0045's F54 fix; corrected 2026-09-25, `PHASE_CORPUS_INTEGRITY_II.md` §1); `config.py` had no policy setting; `app.state.policy_pubkey` is set only by tests | ✅ **CLOSED 2026-09-25.** The key-trust decision is **ADR-0058** (`PHASE_KEY_TRUST_WORKFLOW.md`) and the wiring landed (`PHASE_M4_WIRING.md`): `config.py` reads `WISP_POLICY_BUNDLE`/`WISP_POLICY_PUBKEY`, `composition.py` loads and passes `policy=` at the single construction site, and a named-but-unverifiable bundle **refuses to boot**. **Default OFF** — unset means byte-for-byte today's behaviour. **Two things stay open and are named:** **REST now receives L0** — **ADR-0059** (`PHASE_REST_AUTHORIZATION_COMPOSITION.md`): `require_tool_allowed` consults `authorize()` with the root's loaded policy for its **denial** verdict only, closing a divergence **ADR-0058 created** (driven, 11 of 36 (route, mode) pairs; ADR-0055's 0-of-36 was measured with no policy loaded). `acp_session.py:208`/`benchmark/runner.py:82` still do not receive it (named). Pinned by `tests/test_m4_governance_wiring.py` (25 tests) + `tests/reliability/test_m4_policy_wiring.py` (10) + `tests/reliability/test_rest_authorization_composition.py` (16). |
| **G1** | **Authorization parity** — the agent composes *both* models (`policy_hard_deny` + `authorize()` + the approval gate); REST consulted *only* `SecurityPolicy`. 6 of 36 (route, mode) pairs diverge, all the approval layer, in the **default** `auto_edit` mode. | ✅ **CLOSED.** **ADR-0055** drove the real paths: **0 path divergences of 36**; the 6 are a **model** divergence on three REST-only names the agent has no operation for (Option A). **ADR-0059** then closed the **L0 gap ADR-0058 created** — REST now consults `authorize()` for its denial verdict (driven, 11 of 36 pairs had diverged once a bundle was loaded). This row said **OPEN** until 2026-09-25 — **stale since ADR-0055**, and contradicting §0.0's own row (F98). Ratcheted by `tests/test_authorization_parity.py` + `tests/reliability/test_rest_authorization_composition.py`. |
| R1b | `POST /api/hooks` still accepts an unvalidated `command` | **OPEN — needs a decision.** The gate restricts *who* may register a hook, not *what* it runs. |
| **W1** | **The agent path's WebSocket approval prompt has never rendered.** `WebSocketTransport.approve()` sends `approval_request` / `{approval_id, tool_call}` while **both shipped clients branch on `tool_approval_request`** reading `call_id` / `name` / `arguments` / `reason`. So the agent path's WS prompt always timed out (60 s) and denied. **ADR-0057 residual 1**; finding **F83**. | **OPEN — needs its own ADR.** Reconciling it onto ADR-0057 R1's vocabulary would *fix* it and would change a **live** path's behaviour, so ADR-0057 names it rather than making it silently. Pinned by `test_the_frame_is_the_one_both_clients_read`, which fails if either client changes. **This row exists because a residual named in an ADR is not automatically a live item** — §12 is the handoff's open-items authority and did not carry it (F100). |
| **R10** | ~~`useApi.ts:368` sends no `Authorization` header~~ | ✅ **FIXED** (§0f) — the functional half. **What remains is a decision:** the 32 pre-existing renderer errors (7 of them in `ErrorBoundary.test.tsx`, i.e. a test file being typechecked by the *build* config); the vacuous `typecheck` script; and whether to canonicalize the 26 re-implementations now that the ratchet records them |
| **F1** | ~~`metadata["_budget"]` write-only~~ | ✅ **FIXED** (§0e.2) — completes `docs/audit-2026-08-24.md` item 11 |
| **F2** | ~~`_SENSITIVE_ENV_KEYS` has no consumer~~ | ✅ **FIXED** (§0e.1) — deleted as superseded |
| **F3** | `execute_tool(security_policy=…)` — no caller passes it; `ToolRegistry.execute` is production-unused and lacks truncation/security | **Accepted (low)** — the executor authorises per call; annotate so nobody wires them without the missing checks |
| **F4** | `spawn_with_guards` is a dead duplicate (guards live at `:723`/`:1737`) | **Accepted** — deletion candidate |
| **F5** | event-replay `TOOL_CALL` — the prior audit's referent is unidentifiable; both candidates are wired | **Unresolved, no action** — recorded as unidentified rather than guessed at |
| G2 | The `run_bash` verb scan is a separate mechanism from the predicate | **Accepted** — a shell command's target is not determinable from its text |
| R3 | Full provider-listing delegation | Unsafe until the 3 deltas (auth/timeout/degradation) converge; `test_provider_listing_equivalence.py` fails at that point and signals it |
| R4 | `_is_transient` is a separate predicate | **Not debt** — different axis (retryability, not outcome class) |
| R5 | Two `RunStatus` enums remain | **Resolved as a non-issue by the migration.** `RunStatus` (7 values, `graph/types.py:37`) is a **strict subset** of `RunState` (8, `runs/record.py:17`); the only asymmetry is `PLANNING`, which exists solely in `RunState`. Every `RunStatus` value coerces through `coerce_state()`. **No shim needed** — ADR-0003. Promote the `subset? True` assertion to a ratchet if a future phase adds a member. |
| R6 | `.venv` missing deps | Environment — **one table now: §6.1.** `jsonschema` is **fixed** (F8); `httpx`, `cryptography`, `numpy`, `tiktoken`, `aiohttp` remain absent, each with its pin, its check, the files it blocks, and the counts it makes **un-quotable**. Re-attempted offline 2026-09-25: both `httpx` and `cryptography` still fail |
| R7 | `capability_filter.py` untracked but imported | See §8 |
| R8 | 3 untracked test files abort collection | User's WIP |
| R9 | `wisp/core/graph/__init__.py` modified, uncommitted | User's pre-existing edit |

### Migration open items — current

**There is no single keystone.** M9 was recorded as one and that was wrong (§0.0.2). **All five items
M9 was said to block are now complete** — M12, M14, M15, M11, M13.

| # | Item | Nature |
|---|---|---|
| **M9** | The execution view | ✅ **COMPLETE** — ADR-0029. **The strong reading was the wrong target** (the graph carries no payload), and the projection that exists was **not faithful** (now fixed and asserted). |
| **M11** | The graph does not drive execution | ✅ **COMPLETE as node identity** — ADR-0033. `TaskNode.work_unit` references the work unit (F29/F30: M9's own ratchet forbade the fix and was evadable by naming — replaced by field classification). **The graph driving execution is still open**, pinned by `test_the_graph_still_does_not_drive_execution`. |
| **M12** | The failure path | ✅ **COMPLETE** — ADR-0032. The ladder can be driven from a real failure now, and engine refusals are denials (F28: they were being retried). Enforcement deferred. |
| **M13** | The stagnation detector is not constructed by the turn loop | ✅ **COMPLETE** — ADR-0034. The detector now runs per turn on the live path, gated by `config.graph_oscillation_guard`. **Found two defects**: an empty signal (both inputs are opt-in, so it is empty by default) declared every multi-turn session stagnant (F32), and the runtime cannot see a refused call's arguments (F33). **Enforcement deferred**: routing and goal-met gating are tripwired. |
| **M14** | The context trust boundary | ✅ **COMPLETE** — ADR-0031. **Found a live T1 violation**: workspace-file content (`CLAUDE.md`) sat *before* the system prompt, unfenced. T2 fencing remains, deliberately staged. |
| **M15** | The subagent spawn site | ✅ **COMPLETE** — ADR-0030. The P9 tripwire fired and was replaced by its inverse; `execute(principal=…)` carries the child identity per call. |
| **M16** | The `ESCALATION` record's loss is not fully addressed | ✅ **COMPLETE** — ADR-0028. A state-bearing record is not best-effort in either direction; `reconstruct()` salvages the journal-only records on both paths (F24). |
| **M1** | P3 stage 3b — enable the acceptance gate | **`IN_PROGRESS`** — the precondition is satisfied **and** the mechanism is built and driven; what remains is the population. ADR-0051 R1's precondition was satisfied by **ADR-0053**; **ADR-0054** supplies the consumer ADR-0053 §10 named — the engine's pre-`done` gate asks a read-only callable and withholds `done` by ADR-0036's bounded model, **driven end to end through a real turn** (withheld exactly twice, then finished). The flag `acceptance_gate` / `WISP_ACCEPTANCE_GATE` is **added, default OFF, dependent on `turn_criteria_source`**. **The reason it is not ON:** ADR-0051 R4 requires **≥ 2 capable models** and this environment serves exactly **1** of 13 (`scripts/acceptance_gate_population.py`: capable 1 · degenerate 2 · retired 5 · paywalled 5). What would close it: a second capable model that is neither retired nor paywalled, or a provider key — **not a code change**. **ADR-0051** (amends ADR-0016) + **ADR-0053** + **ADR-0054**. |
| **M2** | Journal-first reconstruction | ✅ **COMPLETE** — `reconstruct()` + `reconstruction_source()`; the pre-P0 hazard and the gap hazard are both handled and pinned. **Five consumers still read the blob** (a tripwire asserts it). |
| **M3** | Killpoint integration | ✅ **COMPLETE** — `test_kp_session_midtool_then_killed`. One window covered. |
| **M4** | ADR-0004 revisited | ✅ **COMPLETE** — **ADR-0027**. Found a live defect (M2's journal-first could return a provider-invalid transcript). |
| **M5** | Foreground-turn `RunRecord` lifecycle | **OPEN** — proven end-to-end for background runs only. |
| **M6** | `PolicyDecisionEnvelope` producer-less and consumer-less | **OPEN** — the last unwired contract. |
| **M7** | `change_tracker.py` not wired into evidence | **OPEN** — deferred with 3b. |
| **M8** | `multi_agent/dag.py` not retired into `wisp/graph/` | **SURVEYED AND DECIDED 2026-09-25 — `DEPRECATE`, not remove.** The fanout suite is green (107 passed), so the retirement was attempted and **driven**. It is blocked on a **measured semantic divergence**: `wisp/graph/` requires a non-empty graph with every node reachable from the entrypoint, `TaskDAG` is a general partial order — so re-pointing `orchestrate_dag` onto `validate_graph` would **reject inputs it accepts today** (a behaviour change to a live, model-callable tool). Also measured: `TaskDAG.validate()` mis-reports an unknown dependency as a cycle, and `compat.dag_to_graph` is test-only. Choosing which definition of a valid DAG wins is its own decision. **The residual is open and tripwired** (3 tripwires). Guard: `tests/reliability/test_dag_retirement_contract.py`. Report: `PHASE_DAG_RETIREMENT.md`. |
| **M10** | The materialized graph is a **lower bound** on iterations | **OPEN — by design.** One node per closed tool exchange + one terminal; iteration boundaries are not observable. |

**Committed.** Phase 10 and migration P0–P9 + M2/M3/M4 are committed (§3). The remaining uncommitted
files are the user's pre-existing WIP (§8) plus foreign-session test files.

---

## 13. Document index

| File | Contents |
|---|---|
| `REPOSITORY_INTELLIGENCE_REPORT.md` | Phase 1+2: 20-section repo model, verified |
| `repository_manifest.json` | Machine-readable manifest + CI verdict |
| `FINDINGS_ADJUDICATION.md` | Phase 3: 27 findings, severity, ranked queue, "do not change" list |
| `PHASE_FINDINGS_NORMALIZATION.md` | Phase 0+1: normalization + dependency ordering |
| `PHASE_BOUNDARY_FORENSIC.md` | Phase 2: leakage audit, edge classification |
| `PHASE_CANONICAL_AUTHORITY_MAP.md` | Phase 3: authority table |
| `PHASE_CANONICAL_CONTRACT_FREEZE.md` | Phase 4: C1–C6 contracts |
| `PHASE_ARCHITECTURAL_INVARIANTS.md` | Phase 7: INV-1…INV-10 + enforcement levels |
| `PHASE_FINDINGS_REMEDIATION_REPORT.md` | Phase 9: rounds 1–4, verification, remaining debt |
| `PHASE_10_AUTHORITY_CLOSURE_AUDIT.md` | Phase 10: targets A–D + C+ (the guard), REST decision matrix, boundary specs |
| `PHASE_10_AUTHORITY_CLOSURE_IMPLEMENTATION.md` | Phase 10: changes, defects, verification |
| `PHASE_10_PROTECTED_PATH_GUARD.md` | **The REST escalation path: evidence, fix, deliberate deltas, debt G1–G4** |
| `PHASE_10_AUTHORIZATION_PARITY.md` | **G1 measured: the two authorization models, 9/36 divergent pairs, options A/B/C** |
| `PHASE_10_M4_GOVERNANCE_UNWIRED.md` | **E: the organization policy layer is never loaded — evidence, root cause, why wiring is a decision** |
| `PHASE_10_UNWIRED_CONTROLS_INVENTORY.md` | **F: the prior audit's 12 unwired controls, re-verified — 7 wired · 1 deleted · 3 unwired · 1 unidentified** |
| `PHASE_10_CLIENT_AUTH_AUTHORITY.md` | **R10: the client's auth header had 28 authors — the unauthenticated request, the 26-way duplication, the compiler that was already reporting it, and the flaky desktop suite** |
| `wisp-desktop/src/renderer/hooks/useApi.ts` | Phase 10 Target C — surfaces the server's refusal `detail` |

### Persistent Graph Loop migration (2026-09-22)

| File | Contents |
|---|---|
| `WISP_PERSISTENT_GRAPH_LOOP_ALIGNMENT_AUDIT.md` | **Phase 0 audit** — 32 sections, a 27-hop execution trace, a ~30-site mutation inventory, the authority table, a 22-concept mapping table, and Q1–Q14 |
| `WISP_TARGET_ARCHITECTURE.md` | The six-layer target (L0–L5), component boundaries, authority model, data/control flow, node state machine |
| `WISP_GRAPH_DOMAIN_MODEL.md` | Concept register (12 first-class, 2 field/derived, 3 excluded), 14 `NodeState` + 9 `GoalState` |
| `WISP_PROPOSAL_PROTOCOL.md` | The nine proposal types, including `NodeTransition` |
| `WISP_VERIFICATION_ARCHITECTURE.md` | Criteria kinds, PASS/FAIL/INCONCLUSIVE, evidence, L1/L2/L3 independence |
| `WISP_RECOVERY_ARCHITECTURE.md` | A 10-class failure taxonomy and a 7-rung recovery ladder |
| `WISP_CONTEXT_ARCHITECTURE.md` | Trust tags, `ContextRequest` → `Context`, graph context |
| `WISP_SUBAGENT_ARCHITECTURE.md` | Structured delegation, transactional effects, one-graph |
| `WISP_MIGRATION_PLAN.md` | **The plan of record** — phases P0–P9 with prerequisites, tests, risk, rollback |
| `WISP_MIGRATION_STATUS.md` | **The ledger** — phase status, findings **F1–F44**, change log, regression summary |
| `WISP_ARCHITECTURE_DECISIONS.md` | **ADR-0001 … ADR-0059** |
| `PHASE_P0_REPORT.md` | Wire the orphaned durable layer |
| `PHASE_P1_REPORT.md` | Journal turn transitions |
| `PHASE_P2_REPORT.md` | Introduce the proposal boundary |
| `PHASE_P3_REPORT.md` | Independent verification (stage 3a) |
| `PHASE_P4_REPORT.md` | Materialize a task graph from durable state |
| `PHASE_P5_REPORT.md` | Runtime graph mutation |
| `PHASE_P6_REPORT.md` | Recovery ladder |
| `PHASE_P7_REPORT.md` | Stagnation detection |
| `PHASE_P8_REPORT.md` | Context as a first-class subsystem |
| `PHASE_P9_REPORT.md` | Structured delegation — **and the migration's closing summary (§8)** |
| `PHASE_M2_REPORT.md` | Journal-first reconstruction with blob fallback |
| `PHASE_M3_REPORT.md` | Killpoint integration for the session journal |
| `PHASE_M4_REPORT.md` | Durability as a correctness precondition (ADR-0027) |
| `PHASE_M16_REPORT.md` | The escalation is state, not audit (ADR-0028) |
| `PHASE_M9_REPORT.md` | The execution view: faithful, and a shape not a payload (ADR-0029) |
| `PHASE_M15_REPORT.md` | A subagent authorizes as a narrowed child (ADR-0030) |
| `PHASE_M14_REPORT.md` | Prompt sections are classified, and T1 holds (ADR-0031) |
| `PHASE_M12_REPORT.md` | The failure path reaches the taxonomy (ADR-0032) |
| `PHASE_M11_REPORT.md` | A node references its work unit; the ratchet classifies fields, not names (ADR-0033) |
| `PHASE_M13_REPORT.md` | The stagnation detector on the live turn path (ADR-0034) |

**Guards added by the migration:**

| File | Protects |
|---|---|
| `tests/test_durable_layer_reachable.py` | every P0 path reaches a production entry point and writes its durable artifact |
| `tests/test_turn_journal_incremental.py` | exchanges are durable mid-turn; the flag changes *when*, never *which* |
| `tests/test_action_idempotency_key.py` | key stability; `unresolved_actions()` reports genuine ambiguity |
| `tests/test_proposal_boundary_records.py` | proposals + outcomes produced; the records never touch `messages` |
| `tests/test_proposal_boundary_no_bypass.py` | AST: authority consumers, `authorize()` consulted exactly once, no direct `TOOL_IMPLS` reach, both call edges reachable |
| `tests/test_verdict_layer_recorded.py` | the authorization verdict is recorded for allow **and** deny; the hash chain still verifies |
| `tests/test_gate_order_corpus.py` | **RED-first** corpus: every gate outcome byte-identical (write it before touching a gate) |
| `tests/test_acceptance_verdict.py` | the verdict algebra; the floor guard is retained, not replaced; stage 3a does not gate |
| `tests/test_task_graph_materialization.py` | readiness is **stored**, not recomputed; one transition API (AST, in-module and tree-wide); the graph is a projection of the log |
| `tests/test_graph_mutation.py` | the extended vocabulary is a superset (ratchet); expansion is acyclic by construction; invalidation cascades; supersession retains history; the growth budget is **enforced**; insertion order does not change the graph |
| `tests/test_failure_signal_classification.py` | **engine refusals are denials** (they were retried); the runtime→taxonomy adapter is total; every error code has a class; the `TIMEOUT` naming trap |
| `tests/test_prompt_section_trust.py` | every prompt section is classified (AST-ratcheted); **no untrusted section is in instruction position**; the predicate fails closed; the fix is a move, not a reshuffle |
| `tests/test_child_principal_wired.py` | a child is **denied at the authorization layer** for a tool its contract excludes; the identity travels with the **call** (ratchet: no per-child executor); **both** child paths stamp it; the parent is resolved by the shared rule |
| `tests/test_execution_view_projection.py` | a real turn's live transcript equals its replay; a tool reply carries only the protocol keys; `TaskNode` may not gain transcript payload (ratchet); the graph's nodes are a count, not an identity |
| `tests/test_escalation_durability.py` | a surviving escalation is **not** discarded by the blob fallback; the write policy is best-effort for a turn body and **fail-loud for a state-bearing batch**; the carve-out cannot widen |
| `tests/test_durability_preconditions.py` | a **gapped** journal is a provider-invalid transcript and is refused; contiguity semantics; **a real turn satisfies the invariant** (or the check would reject every session) |
| `tests/test_session_reconstruction.py` | a pre-P0 session is **not truncated** (the hazard); the journal carries the audit records the blob never had; both paths are shape-compatible with the blob; **a tripwire asserting the five consumers are still un-migrated** |
| `tests/test_durability_preconditions.py` | a **gapped** journal is a provider-invalid transcript and is refused; contiguity semantics; **a real turn satisfies the invariant** |
| `tests/test_session_reconstruction.py` | a pre-P0 session is **not truncated**; the journal carries the audit records the blob never had; shape-compatible on both paths; **a tripwire asserting the five consumers are still un-migrated** |
| `tests/test_structured_delegation.py` | a child gets exactly its declared tools; widening is refused; `["all"]` against an unbounded parent is refused rather than guessed; the executor authorizes as the principal it was given; **a tripwire asserting the spawn site is still unwired** |
| `tests/test_context_trust.py` | every item is tagged; T1–T4 enforced structurally; assembly is deterministic and order-independent; truncation is recorded **as data**; an injection payload cannot escape its fence |
| `tests/test_stagnation_detection.py` | the detector **reuses** `OscillationTrap` (AST-pinned); a productive task is never flagged; stagnation routes to a **replan, not a retry**; a stagnated goal cannot report `GOAL_MET`; `graph_oscillation_guard` is finally read |
| `tests/test_recovery_ladder.py` | the taxonomy is closed at 10; **denials never retry** (by class, over the canonical vocabulary); escalation is terminal and resumable; an unsafe rollback escalates; every rung cites evidence; budgets are enforced |


**Executable guards added across the engagement** (these are the real deliverable — a canonicalization without one is not a canonicalization):

| File | Protects |
|---|---|
| `tests/test_canonical_execution_state.py` | one run-state authority |
| `tests/test_canonical_path_containment.py` | one containment implementation |
| `tests/test_background_admission.py` / `_retention.py` | admission symmetry; retention bound |
| `tests/test_verification_contract.py` | the shell-format ↔ gate coupling |
| `tests/test_provider_model_authority.py` / `test_provider_listing_equivalence.py` | model-listing authority; the three transport deltas |
| `tests/test_tool_result_error_authority.py` | one outcome-classification authority |
| `tests/test_outcome_classification_authority.py` | the taxonomy + AST ban on re-deriving outcome |
| `tests/test_rest_gate_boundary.py` / `test_server_policy_gate.py` | the REST gate boundary + functional deny/allow |
| `tests/test_protected_path_guard.py` | one protected-path predicate across all three authorization paths |
| `tests/test_authorization_parity.py` | the known divergences between the two decision models, ratcheted |
| `tests/test_m4_governance_wiring.py` | that the M4 layer is **not** wired — a tripwire that fails the moment someone wires it |
| `tests/test_unwired_controls_inventory.py` | the 12-control inventory — fails if one is wired, or if the claim regresses |
| `tests/test_layer_direction.py` | core must not depend on presentation |
| `tests/test_module_orphans.py` | absolute **and** relative import reachability |
| `tests/test_doc_drift.py` | guidance docs must not cite deleted symbols |
| `tests/test_tui_task_ownership.py` | TUI tasks go through `OwnedTasks` |
| `tests/test_telemetry_accounting.py` / `test_graph_run_state_reclamation.py` | bounded per-run state |
| `wisp-desktop/.../useApi.test.ts` | the auth header on every request path — and its absence when no key is set |
| `wisp-desktop/.../authHeaderAuthority.test.ts` | one header authority; the 17-file / 26-site duplication, ratcheted |

**Three guards above are tripwires rather than invariants** — they assert the
*current* state is as documented and fail when it changes, so a decision cannot
drift from its record. That pattern came from the audit's own decay (§0e).

Memory: `.workbuddy-ai/memory/2026-09-18.md` (Phases 1–9) and
`.workbuddy-ai/memory/2026-09-20.md` (Phase 10 — all four findings, the
inventory work, and the environment lessons).
