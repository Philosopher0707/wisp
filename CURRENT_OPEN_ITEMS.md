# CURRENT_OPEN_ITEMS.md — the current state of every item the corpus has left open

> **DERIVED DOCUMENT — REGENERATE, DO NOT EDIT IN PLACE.**
> Regenerate with `env -u PYTHONPATH .venv/bin/python scripts/derive_current_open_items.py`.
> This page states each item's **current** state, not its history: it cites where the state
> is recorded, and it **introduces no decision**. Where two sources disagree, both are cited
> and §Findings records the disagreement — never a resolution.
>
> **The state vocabulary is the ledger's** (`WISP_MIGRATION_STATUS.md:41`), stated in §(a),
> with every other source's word mapped onto it. A reason is not a state — see §(b).
>
> **Sibling registers:** `CURRENT_AUTHORITIES.md` (what each authority's current state is),
> `CURRENT_FINDINGS.md` (every recorded finding and its status), `CURRENT_FLAGS.md` (every
> rollback flag and its default). All four are derived; none may decide.
>
> Generated 2026-10-08 at `52e90f6` · **102 items** · **23 open**, 79 closed (kept, in §The closed items).

---

## The register — items not yet closed

| id | title | state | blocked_by | tripwire | source |
|---|---|---|---|---|---|
| **R10** | `useApi.ts:368` sends no `Authorization` header | `PARTIAL` | — | — | CONTEXT.md:2466 — “✅ **FIXED** (§0f) — the functional half. **What remains is a decision**” |
| **R3** | Full provider-listing delegation | `NOT STARTED` | the 3 deltas (auth/timeout/degradation) must converge | tests/test_provider_listing_equivalence.py | CONTEXT.md:2473 — “Unsafe until the 3 deltas … converge; `test_provider_listing_equivalence.py` fails at that point” |
| **R7** | `capability_filter.py` untracked but imported | `NOT STARTED` | the file is the user's untracked WIP (§8) | — | CONTEXT.md:2477 — “See §8” |
| **R8** | 3 untracked test files abort collection | `NOT STARTED` | the files are the user's WIP | — | CONTEXT.md:2478 — “User's WIP” |
| **M1** | P3 stage 3b — enable the acceptance gate | `IN PROGRESS` | ADR-0051 R4 requires **≥ 2 capable models** and this host serves exactly **1** of 13 — an environment fact, not a code change | tests/reliability/test_acceptance_gate_enablement.py | CONTEXT.md:2495 — “**`IN_PROGRESS`** — the precondition is satisfied **and** the mechanism is built and driven; what remains is the population” |
| **M5** | Foreground-turn `RunRecord` lifecycle | `NOT STARTED` | — | — | CONTEXT.md:2499 — “**OPEN** — proven end-to-end for background runs only.” |
| **M7** | `change_tracker.py` not wired into evidence | `NOT STARTED` | deferred with stage 3b | — | CONTEXT.md:2501 — “**OPEN** — deferred with 3b.” |
| **P3 · item 6** | The completion rule requires non-invalidated evidence | `NOT STARTED` | it is stage 3b, which is M1 | — | WISP_MIGRATION_STATUS.md:512 — “`NOT DONE` — **that is stage 3b** — the plan's staging; `turn_succeeded` is unchanged” |
| **P3 · item 7** | Wire `change_tracker.py` into evidence | `NOT STARTED` | deferred with 3b (M7) | — | WISP_MIGRATION_STATUS.md:513 — “`NOT DONE` — deferred with 3b” |
| **P8 · item 5** | Serve the symbol-level repo map | `NOT STARTED` | `tiktoken` is installed, so the budget is now measurable — the item itself is not implemented | — | WISP_MIGRATION_STATUS.md:864 — “`NOT DONE` — the plan requires **measure first**” |
| **P8 · item 6** | Token-based compaction | `NOT STARTED` | it changes when context is destroyed, on the least observable path | — | WISP_MIGRATION_STATUS.md:865 — “`NOT DONE`” |
| **P8 · item 7** | Memory origin | `PARTIAL` | — | — | WISP_MIGRATION_STATUS.md:866 — “`PARTIAL` — `Provenance` supplies the field; `memory.py` is not yet wired to use it” |
| **P9 · item 2** | Structured `child_goal` | `NOT STARTED` | deferred (§11.4) | — | WISP_MIGRATION_STATUS.md:933 — “`NOT DONE` — deferred” |
| **P9 · item 3** | Mandatory `result_schema` | `NOT STARTED` | deferred | — | WISP_MIGRATION_STATUS.md:934 — “`NOT DONE` — deferred” |
| **P9 · item 4** | Transactional effects | `NOT STARTED` | deferred | — | WISP_MIGRATION_STATUS.md:935 — “`NOT DONE` — deferred” |
| **P9 · item 5** | Typed failure replacing prose markers | `NOT STARTED` | deferred | — | WISP_MIGRATION_STATUS.md:936 — “`NOT DONE` — deferred” |
| **ADR-0053 R1** | No declared turn population exists, so the `GOAL_MET` rate ADR-0051 R2 specifies cannot be produced | `NOT STARTED` | the population is the missing input (= M1's population) | — | WISP_ARCHITECTURE_DECISIONS.md:5169 — “**No declared turn population exists**” |
| **ADR-0053 R2** | A declared failure does not produce a replan | `NOT STARTED` | making it repairable means moving the probe into the engine — its own decision | — | WISP_ARCHITECTURE_DECISIONS.md:5171 — “It is recorded (`GOAL_FAILED`) rather than repaired within the turn” |
| **ADR-0054 R1** | The population is short by one capable model | `NOT STARTED` | an *environment* fact, not a design one | — | WISP_ARCHITECTURE_DECISIONS.md:5321 — “This is the only thing between the contract and the enablement decision” |
| **ADR-0061 R1** | The round-trip is pinned against a stub channel, not a live client | `NOT STARTED` | no real desktop/TUI/VS Code client runs on this host | tests/test_ws_control_plane.py | WISP_ARCHITECTURE_DECISIONS.md:6588 — “the *frame shape* and the *no-client behaviour* are driven and the end-to-end render is not” |
| **PHASE_OBJECTIVE_FLAG_COMPOSITION R1** | `CriteriaDerivation.strict` records `True` when the declaration path pre-empted it | `NOT STARTED` | making the boolean explicit is a record change and its own decision | — | PHASE_OBJECTIVE_FLAG_COMPOSITION.md:178 — “the boolean cannot distinguish *“strict acted”* from *“strict had nothing to act on”*” |
| **PHASE_OBJECTIVE_FLAG_COMPOSITION R2** | Whether objectives *must* declare | `NOT STARTED` | a policy with its own evidence; ADR-0056's named reversal condition | — | PHASE_OBJECTIVE_FLAG_COMPOSITION.md:174 — “That is a policy with its own evidence” |
| **PHASE_GATE_ENABLEMENT R1** | The gate was never exercised, so its interventions and surrenders are NOT MEASURED | `NOT STARTED` | the gate is not enabled (= M1) | — | PHASE_GATE_ENABLEMENT.md:204 — “Reporting those as zero would be manufacturing a metric” |

---

## The closed items — kept, with their closure's source

A page that deleted its closed items could not be checked for a regression: an item that
reopens would simply vanish from the open table with nothing to compare against.

| id | title | state | blocked_by | tripwire | source |
|---|---|---|---|---|---|
| **R1** | REST gate — finish option B | `COMPLETE` | — | — | CONTEXT.md:2459 — “✅ **DONE** (§0)” |
| **R2** | Correct the “breaks the client” claim | `COMPLETE` | — | — | CONTEXT.md:2460 — “✅ **DONE** (§5)” |
| **G0** | REST bypass of the protected-path guard | `COMPLETE` | — | — | CONTEXT.md:2461 — “✅ **DONE** (§0b)” |
| **E** | The M4 governance layer is not wired to the runtime | `COMPLETE` | — | tests/test_m4_governance_wiring.py | CONTEXT.md:2462 — “✅ **CLOSED 2026-09-25.** The key-trust decision is **ADR-0058** … and the wiring landed” |
| **G1** | Authorization parity — the agent composes both models; REST consulted only `SecurityPolicy` | `COMPLETE` | — | tests/test_authorization_parity.py | CONTEXT.md:2463 — “✅ **CLOSED.** **ADR-0055** drove the real paths: **0 path divergences of 36**” |
| **R1b** | `POST /api/hooks` still accepts an unvalidated `command` | `COMPLETE` | — | tests/reliability/test_external_input_path.py | CONTEXT.md:2464 — “✅ **CLOSED 2026-09-25 (ADR-0061 R6 / G3).**” |
| **W1** | The agent path's WebSocket approval prompt had never rendered | `COMPLETE` | — | tests/reliability/test_external_input_path.py | CONTEXT.md:2464 — “✅ **CLOSED 2026-09-25 (ADR-0061).**” |
| **F1** | `metadata["_budget"]` write-only | `COMPLETE` | — | — | CONTEXT.md:2467 — “✅ **FIXED** (§0e.2)” |
| **F2** | `_SENSITIVE_ENV_KEYS` has no consumer | `COMPLETE` | — | — | CONTEXT.md:2468 — “✅ **FIXED** (§0e.1) — deleted as superseded” |
| **F3** | `execute_tool(security_policy=…)` — no caller passes it | `COMPLETE` | — | wisp/tools/registry.py | CONTEXT.md:2469 — “✅ **CLOSED 2026-09-27.**” — the owed annotation **landed**: `registry.py:911` now *prohibits* wiring this entrypoint without the checks `ToolRegistry.execute` supplies. Prose-only, proved by both instruments — **ADR-0065 R3** satisfied |
| **F4** | `spawn_with_guards` is a dead duplicate | `COMPLETE` | — | tests/test_unwired_controls_inventory.py | CONTEXT.md:2470 — “**Accepted** — deletion candidate”; *Reason:* accepted, and pinned as deliberately dead — `tests/test_unwired_controls_inventory.py:283` asserts the variant is still uncalled (*“it is no longer dead code”*), so *“deletion candidate”* is superseded by the instrument and the tripwire cell is corrected — **ADR-0065 R1** |
| **F5** | event-replay `TOOL_CALL` — the referent is unidentifiable | `COMPLETE` | — | — | CONTEXT.md:2471 — “**Unresolved, no action** — recorded as unidentified rather than guessed at”; *Reason:* resolved as *no action* — recorded unidentified rather than guessed at, which is §12's own disposition — **ADR-0065 R1** |
| **G2** | The `run_bash` verb scan is a separate mechanism from the predicate | `COMPLETE` | — | — | CONTEXT.md:2472 — “**Accepted** — a shell command's target is not determinable from its text”; *Reason:* accepted — the same ground ADR-0061 used to close R1b, and the reason a runnable check, a blocklist and an allow-list are each rejected — **ADR-0065 R1** |
| **R4** | `_is_transient` is a separate predicate | `COMPLETE` | — | — | CONTEXT.md:2474 — “**Not debt** — different axis (retryability, not outcome class)” |
| **R5** | Two `RunStatus` enums remain | `COMPLETE` | — | — | CONTEXT.md:2475 — “**Resolved as a non-issue by the migration.** … **No shim needed** — ADR-0003” |
| **R6** | `.venv` missing deps | `COMPLETE` | — | — | CONTEXT.md:2469 — “✅ **CLOSED 2026-09-27.**”; *Reason:* all five deps installed at their `uv.lock` pins, plus `wcwidth`, so nothing is absent — the obstacle was TLS, not network (F-T9) |
| **R9** | `wisp/core/graph/__init__.py` modified, uncommitted | `COMPLETE` | — | — | CONTEXT.md:2479 — “User's pre-existing edit”; *Reason:* decided by **ADR-0060 R5**, which holds that this file *“needs **no edit**”* and that the dead part is a retained reference implementation, quoting this file's own uncommitted docstring as the reason; independently driven, the diff is a module docstring only — docstring-stripped AST identical and recursive `co_code` identical, prose-only — **ADR-0065 R1** |
| **M2** | Journal-first reconstruction | `COMPLETE` | — | tests/test_session_reconstruction.py | CONTEXT.md:2493 — “✅ **COMPLETE** … **Five consumers still read the blob** (a tripwire asserts it)” |
| **M3** | Killpoint integration | `COMPLETE` | — | tests/reliability/test_killpoints.py | CONTEXT.md:2497 — “✅ **COMPLETE** — `test_kp_session_midtool_then_killed`. One window covered.” |
| **M4** | ADR-0004 revisited | `COMPLETE` | — | — | CONTEXT.md:2498 — “✅ **COMPLETE** — **ADR-0027**. Found a live defect” |
| **M6** | `PolicyDecisionEnvelope` producer-less and consumer-less | `COMPLETE` | — | tests/test_contracts_policy.py | CONTEXT.md:2469 — “✅ **CLOSED 2026-09-27.**” — measured, **four of six** `wisp/contracts/` modules have no production importer, so it is *not* the last; that is the M1a freeze, which the spec defines as additive (*“Pure addition: no existing producer or consumer changes behavior”*) — a disposition, and **not** a deletion candidate |
| **M8** | `multi_agent/dag.py` not retired into `wisp/graph/` | `COMPLETE` | — (re-scoped by ADR-0060: the divergence is the *boundary*, not a blocker; the removal is **not owed**) | tests/reliability/test_dag_retirement_contract.py | CONTEXT.md:2502 — “✅ **COMPLETE.** *Reason:* surveyed and decided 2026-09-25 — **`DEPRECATE`, not remove** — and **re-scoped by ADR-0060**” |
| **M9** | The execution view | `COMPLETE` | — | — | CONTEXT.md:2488 — “✅ **COMPLETE** — ADR-0029.” |
| **M10** | The materialized graph is a lower bound on iterations | `COMPLETE` | — (by design: iteration boundaries are not observable) | — | CONTEXT.md:2503 — “**OPEN — by design.**”; *Reason:* by design — iteration boundaries are not observable, which is a disposition and not an obstacle — **ADR-0065 R1** |
| **M11** | The graph does not drive execution | `COMPLETE` | — | tests/reliability/test_layer_b_boundary.py | CONTEXT.md:2489 — “✅ **COMPLETE.** *Reason:* **decided, not deferred** (ADR-0060; ADR-0062 R3). ADR-0033 … **ADR-0060 closes the second half**” |
| **M12** | The failure path | `COMPLETE` | — | — | CONTEXT.md:2490 — “✅ **COMPLETE** — ADR-0032.” |
| **M13** | The stagnation detector is not constructed by the turn loop | `COMPLETE` | — | tests/test_stagnation_live_wiring.py | CONTEXT.md:2491 — “✅ **COMPLETE** — ADR-0034. … **Enforcement deferred**: routing and goal-met gating are tripwired.” |
| **M14** | The context trust boundary | `COMPLETE` | — | tests/test_prompt_section_trust.py | CONTEXT.md:2492 — “✅ **COMPLETE** — ADR-0031. … T2 fencing remains, deliberately staged.” |
| **M15** | The subagent spawn site | `COMPLETE` | — | tests/test_child_principal_wired.py | CONTEXT.md:2493 — “✅ **COMPLETE** — ADR-0030.” |
| **M16** | The `ESCALATION` record's loss is not fully addressed | `COMPLETE` | — | — | CONTEXT.md:2494 — “✅ **COMPLETE** — ADR-0028.” |
| **Layer C** | `wisp/core/graph/` — named *disowned* and consumed by the live path | `COMPLETE` | — | tests/reliability/test_layer_c_disposition.py | CONTEXT.md:2504 — “✅ **COMPLETE.** *Reason:* decided 2026-09-25 by **ADR-0060 R5** (ADR-0062 R3). The live symbols **moved**” |
| **P0 · item 6** | A normal turn creates a `RunRecord` row | `COMPLETE` | — | — | WISP_MIGRATION_STATUS.md:293 — “`DEFERRED to P1`”; P1 is `COMPLETE` (`:193`) |
| **P1 · item 3** | Journal replaces the snapshot as the primary record | `COMPLETE` | — | — | WISP_MIGRATION_STATUS.md:380 — “`DEFERRED to P2`”; P2 is `COMPLETE` (`:194`) |
| **P3 · item 5** | Structural independence (L1/L2) | `COMPLETE` | — | — | WISP_MIGRATION_STATUS.md:511 — “closed 2026-09-27” — L3 is *preferred, not required* by the plan, so its absence is not owed; L1/L2 are done and pinned |
| **P4 · item 1** | Reuse `wisp/graph/`'s store | `COMPLETE` | — | — | WISP_MIGRATION_STATUS.md:551 — “closed 2026-09-27” — the store is *deliberately not* shared; a second DB would fragment the durable record (ADR-0019) — a disposition, not an omission |
| **P5 · item 5** | Extend the executor to accept a mid-run node | `COMPLETE` | — (ADR-0060 measured it inexpressible and rejected Position B) | — | WISP_MIGRATION_STATUS.md:635 — “`NOT DONE` — **deferred** — §7.5”; §7.5's target was **rejected** by ADR-0060 |
| **P7 · item 1** | Wire `OscillationTrap` to the live loop | `COMPLETE` | — | tests/test_stagnation_live_wiring.py | WISP_MIGRATION_STATUS.md:747 — “`PARTIAL` … the live turn loop does not construct a detector (M13)”; M13 is `COMPLETE` |
| **P8 · item 3** | Graph context section scoped to the current node | `COMPLETE` | — | tests/reliability/test_layer_b_boundary.py | WISP_MIGRATION_STATUS.md:862 — “closed 2026-09-27 by ADR-0060 R2” — no current node exists *by decision*, so the section has no referent and is not owed |
| **P8 · item 4** | Populate plan context (`PlanState`, `## PLAN MODE ACTIVE`) | `COMPLETE` | — | tests/test_prompt_section_trust.py | WISP_MIGRATION_STATUS.md:863 — “closed 2026-09-27 by ADR-0063 R2” — the slot is `OPERATOR`-tagged, so "populate" is answered **remove**; the section survives only for the operator-endorsed path |
| **P9 · item 1** | Wire `derive_subagent` | `COMPLETE` | — | tests/test_child_principal_wired.py | WISP_MIGRATION_STATUS.md:932 — “`PARTIAL` … **the spawn site is M15**”; M15 is `COMPLETE` |
| **P9 · item 8** | Retire `dag.py` into `wisp/graph/` | `COMPLETE` | — (M8; re-scoped by ADR-0060) | — | WISP_MIGRATION_STATUS.md:939 — “`NOT DONE` — already item **M8**”; M8 is now `COMPLETE` by decision |
| **ADR-0053 R3** | `command_succeeds` on the turn path runs the declared command after `done`, once per declared turn | `COMPLETE` | — (a stated cost; it is why the flag defaults OFF) | — | WISP_ARCHITECTURE_DECISIONS.md:5174 — “That cost is real and is the reason the flag defaults OFF.”; *Reason:* a stated cost, not a defect — it is why the flag defaults OFF — **ADR-0065 R1** |
| **ADR-0054 R2** | The declared command runs once per declared turn (at the gate; reused at the verdict site) | `COMPLETE` | — (a stated cost) | — | WISP_ARCHITECTURE_DECISIONS.md:5323 — “that is a real cost, and it is why the flag defaults OFF”; *Reason:* a stated cost, not a defect — **ADR-0065 R1** |
| **ADR-0054 R3** | The gate shares the stagnation gate's budget | `COMPLETE` | — (the intended reading of a turn-level bound, stated rather than discovered) | — | WISP_ARCHITECTURE_DECISIONS.md:5325 — “That is the intended reading of a turn-level bound”; *Reason:* the intended reading, stated rather than discovered — **ADR-0065 R1** |
| **ADR-0054 R4** | `stagnation_gate` is untouched | `COMPLETE` | — | tests/reliability/test_acceptance_gate_enablement.py | WISP_ARCHITECTURE_DECISIONS.md:5327 — “CLOSED 2026-09-27” — both gates are wired and default OFF *independently*, which is the disposition ADR-0037 requires, not a gap |
| **ADR-0057 R1** | The agent path's WebSocket approval prompt has never rendered | `COMPLETE` | — | tests/reliability/test_external_input_path.py | WISP_ARCHITECTURE_DECISIONS.md:5826 — “`approve()` sends a frame no client reads”; closed by **ADR-0061** |
| **ADR-0057 R2** | `resolve_approval` still ignores the client's `id` when the transport resolves it | `COMPLETE` | — | tests/reliability/test_external_input_path.py | WISP_ARCHITECTURE_DECISIONS.md:5831 — “CLOSED 2026-09-27 by ADR-0066 R5” — the single-pending fallback is kept as a stated back-compat shim, unreachable for a correct client |
| **ADR-0057 R3** | G3 remains open (R9) | `COMPLETE` | — | tests/reliability/test_external_input_path.py | WISP_ARCHITECTURE_DECISIONS.md:5834 — “**G3 remains open** (R9)”; closed by **ADR-0061** (`CONTEXT.md` §12 row R1b) |
| **ADR-0057 R4** | A multi-client deployment asks every registered channel and takes the first response | `COMPLETE` | — | — | WISP_ARCHITECTURE_DECISIONS.md:5834 — “A multi-client deployment asks every registered channel” — **CLOSED 2026-09-27 by ADR-0066 R6**: per-client routing is *not owed*, having no client identity to route by |
| **ADR-0058 R1** | The multi-device ceremony — a control plane, a key-distribution server and a registration UI | `COMPLETE` | — | — | WISP_ARCHITECTURE_DECISIONS.md:5998 — “CLOSED 2026-09-27” — the M4 spec (§5) deferred the ceremony rather than omitting it, so it is **not owed** |
| **ADR-0058 R2** | `WISP_POLICY_CACHE` and `load_managed` are not engaged | `COMPLETE` | — (this decision wires the `local-only` path only) | — | WISP_ARCHITECTURE_DECISIONS.md:6000 — “The managed/disconnected modes have a network story and a cache”; *Reason:* by design — ADR-0058 wires the `local-only` path only, and the same disposition closed ADR-0058 R4 (*“by design”*) — **ADR-0065 R1** |
| **ADR-0058 R3** | REST does not receive L0 | `COMPLETE` | — | tests/reliability/test_rest_authorization_composition.py | WISP_ARCHITECTURE_DECISIONS.md:6002 — “`SecurityPolicy.check()` has **no organization layer**”; closed by **ADR-0059** |
| **ADR-0058 R4** | Private-key custody is not implemented by Wisp | `COMPLETE` | — (by design, R7) | — | WISP_ARCHITECTURE_DECISIONS.md:6007 — “(R7), by design” |
| **ADR-0059 R1** | A bundle's `approve` level is inert on REST | `COMPLETE` | — | tests/reliability/test_external_input_path.py | WISP_ARCHITECTURE_DECISIONS.md:6163 — “CLOSED 2026-09-27.” — closed **in effect** (ADR-0061, re-derived over the **ten** pinned pairs: all satisfy `allowed and approval_required` and REST asks) and **in mechanism** (ADR-0066 R1 decided the trigger is a *set*, not a read of `approval_required`) |
| **ADR-0059 R2** | REST's consult is conditional on a bundle, so L1/L2/L3 are not consulted without one | `COMPLETE` | — | tests/reliability/test_rest_authorization_composition.py | WISP_ARCHITECTURE_DECISIONS.md:6167 — “CLOSED 2026-09-27 by ADR-0068” — L2 is now applied unconditionally and last; L1/L3 stay bundle-gated and are not gaps. Driven: quarantined + no bundle → 403 by the workspace trust layer |
| **ADR-0059 R3** | REST still reimplements L4 (the protected-path predicate) | `COMPLETE` | — | tests/test_protected_path_guard.py | WISP_ARCHITECTURE_DECISIONS.md:6173 — “REST no longer reimplements L4 — CLOSED 2026-09-27.”; *Reason:* the scan is now the canonical `wisp.pathsec.touches_protected_path`, called by both `auth/decision`'s L4 and the REST gate, each keeping its own refusal message — a landed change |
| **ADR-0059 R4** | Two bundle sources — the env-var path and the publish route's held bundle | `COMPLETE` | — | — | WISP_ARCHITECTURE_DECISIONS.md:6176 — “CLOSED 2026-09-27.” — *Named, not merged* is the disposition: merging is a distribution decision and no deployment here has two sources to merge |
| **ADR-0059 R5** | The three REST-only names have an enforced rule and still have no agent operation | `COMPLETE` | — | — | WISP_ARCHITECTURE_DECISIONS.md:6179 — “That is the honest end state” |
| **ADR-0061 R2** | `receive_message`'s own `tool_approval` branch still ignores `msg["id"]` | `COMPLETE` | — | tests/reliability/test_external_input_path.py | WISP_ARCHITECTURE_DECISIONS.md:6595 — “CLOSED 2026-09-27 by ADR-0066 R5” — the same stated back-compat shim as ADR-0057 R2 |
| **ADR-0061 R3** | A multi-client deployment still asks every registered channel | `SUPERSEDED` | — (ADR-0057 residual 4, unchanged) | — | WISP_ARCHITECTURE_DECISIONS.md:6596 — “unchanged”; *Reason:* **superseded by `ADR-0057 R4`** — the same item under a second id, as this row's own `blocked_by` concedes — **ADR-0065 R5** |
| **ADR-0061 R4** | The bundle half of ADR-0059 residual 1 is un-measurable here | `COMPLETE` | — | tests/test_policy_bundle.py | WISP_ARCHITECTURE_DECISIONS.md:6598 — “The bundle half of ADR-0059 residual 1 is now measurable — CLOSED 2026-09-27.”; *Reason:* `cryptography` is installed, so the `approve` level's effect on the REST verdict can be driven — residual 1's own divergence stays tracked by ADR-0059 R1 |
| **PHASE_AUTHORIZATION_PARITY R1** | The approval authority is split three ways | `COMPLETE` | — | tests/reliability/test_rest_approval.py | PHASE_AUTHORIZATION_PARITY.md:119 — “CLOSED 2026-09-27 by ADR-0066 R1” — the three are three *questions*, not three copies; unifying is rejected on ADR-0055's measured ground |
| **PHASE_AUTHORIZATION_PARITY R2** | Three action names are in none of the three approval sets | `SUPERSEDED` | — | tests/reliability/test_rest_approval.py | PHASE_AUTHORIZATION_PARITY.md:121 — “SUPERSEDED 2026-09-27.”; *Reason:* **superseded by ADR-0057**, which created `REST_APPROVAL_ACTIONS` for exactly these three names — recorded by ADR-0066 R2, source not rewritten |
| **PHASE_AUTHORIZATION_PARITY R3** | REST cannot ask a human — a REST caller gets the no-approver fall-through | `COMPLETE` | — | tests/reliability/test_rest_approval.py | PHASE_AUTHORIZATION_PARITY.md:131 — “**Not repaired here** — … Option C is the fix”; landed as **ADR-0057** (`PHASE_REST_APPROVAL.md`) |
| **PHASE_AUTHORIZATION_PARITY R4** | Five further gated routes are not in the parity table | `COMPLETE` | — | tests/reliability/test_rest_approval.py | PHASE_AUTHORIZATION_PARITY.md:132 — “MEASURED 2026-09-27.” — all five pass the policy gate and none asked a human; **ADR-0066 R3** gated the two executing verbs and R4 kept the rest out by decision |
| **PHASE_M4_WIRING R1** | REST does not receive L0 | `COMPLETE` | — | tests/reliability/test_rest_authorization_composition.py | PHASE_M4_WIRING.md:186 — “its own decision, pinned so it cannot drift silently”; closed by **ADR-0059** |
| **PHASE_M4_WIRING R2** | `acp_session.py:208` — an ACP-only deployment would need the same load | `COMPLETE` | — | tests/test_acp_session.py | PHASE_M4_WIRING.md:187 — “CLOSED 2026-09-27 by ADR-0067” — the fallback is ungoverned *by construction* and now warns; loading it here is the second load site ADR-0058 R1 forbids |
| **PHASE_M4_WIRING R3** | The M4 policy suite cannot run here | `COMPLETE` | — | tests/test_policy_bundle.py | PHASE_M4_WIRING.md:188 — “The M4 policy suite could not run here” (F88) — **CLOSED 2026-09-27**; *Reason:* the six files report 43 passed, 0 failed, 0 errors — a landed change |
| **PHASE_M4_WIRING R4** | F89 — the falsy-`expires_at` message | `COMPLETE` | — | tests/reliability/test_key_trust_workflow.py | PHASE_M4_WIRING.md:189 — “the falsy-`expires_at` message”; closed by corpus integrity III (`CONTEXT.md` §0: 2.1 **CLOSED**) |
| **PHASE_M4_WIRING R5** | `WISP_POLICY_CACHE` / `load_managed` are not engaged | `SUPERSEDED` | — (its cited `ADR-0058 R6` does not exist; see **ADR-0065** residual 2) | — | PHASE_M4_WIRING.md:190 — “the managed/disconnected modes are not engaged (ADR-0058 R6)”; *Reason:* **superseded by `ADR-0058 R2`** — the same item under a second id, and its cited residual `ADR-0058 R6` dangles (ADR-0058's residual list has R1–R4) — **ADR-0065 R5** |
| **PHASE_KEY_TRUST_WORKFLOW R6** | The happy path of the key-trust workflow is not exercised by any test here | `COMPLETE` | — | tests/test_policy_bundle.py | PHASE_KEY_TRUST_WORKFLOW.md:235 — “The happy path of the key-trust workflow is now exercised — CLOSED 2026-09-27.”; *Reason:* `test_sign_verify_round_trip` drives a real Ed25519 keypair through sign and verify — a landed change |
| **PHASE_DAG_RETIREMENT R1** | `TaskDAG.validate()` mis-reports an unknown dependency as a cycle | `COMPLETE` | — | tests/reliability/test_dag_retirement_contract.py | PHASE_DAG_RETIREMENT.md:125 — “`TaskDAG.validate()`'s unknown-dep-as-cycle mis-report — **REPAIRED 2026-09-27** (§3)”; *Reason:* the Kahn in-degree now counts only edges whose source exists, so the false second message is gone and the verdict is unchanged — a landed change |
| **PHASE_DAG_RETIREMENT R2** | `dag_to_graph` has no production caller | `COMPLETE` | — (compat is test-only) | tests/reliability/test_dag_retirement_contract.py | PHASE_DAG_RETIREMENT.md:125 — “`dag_to_graph`'s lack of a production caller”; *Reason:* compat is test-only — a disposition, and the contract test pins it — **ADR-0065 R1** |
| **PHASE_LAYER_B_BOUNDARY R1** | `wisp/graph/api.py` has no importer — Layer B's typed SDK is reachable from nothing | `COMPLETE` | — | wisp/__init__.py | PHASE_LAYER_B_BOUNDARY.md:386 — “CORRECTED 2026-09-27” — measured, **six** import sites in four files, incl. `wisp/__init__.py:48`; it is the package's **public surface**. `GraphHandle` has no consumer — public-and-unconsumed, not dead. The premise was wrong |
| **PHASE_LAYER_B_BOUNDARY R2** | `test_the_orchestrator_still_imports_dag` is a bare string scan | `COMPLETE` | — | tests/reliability/test_dag_retirement_contract.py | PHASE_LAYER_B_BOUNDARY.md:392 — “was a bare string scan” — **REPAIRED 2026-09-27**; both tripwires in that class now parse the AST — a landed change |
| **PHASE_EXTERNAL_INPUT_PATH R1** | `vscode-extension/` is not a “shipped client” in ADR-0057's sense | `COMPLETE` | — | tests/reliability/test_external_input_path.py | PHASE_EXTERNAL_INPUT_PATH.md:222 — “ANSWERED 2026-09-27: it is.” — ADR-0061 names **three** clients that branch on the frame, the VS Code extension among them, correcting ADR-0057's *“both”* |
| **PHASE_OUTCOME_CLASSIFICATION_VIOLATION R1** | The M4 count guard is still RED | `COMPLETE` | — | tests/test_m4_governance_wiring.py | PHASE_OUTCOME_CLASSIFICATION_VIOLATION.md:239 — “it is the one that *is* a contract update”; closed by corpus integrity II (2.1 **CLOSED**) |
| **PHASE_OUTCOME_CLASSIFICATION_VIOLATION R2** | `httpx` is still absent, so two guards remain un-runnable | `COMPLETE` | — | tests/test_protected_path_guard.py | PHASE_OUTCOME_CLASSIFICATION_VIOLATION.md:240 — “`httpx` is now installed — CLOSED 2026-09-27.”; *Reason:* both named guards run and pass, so their counts are quotable — a landed change |
| **PHASE_GATE_ENABLEMENT R2** | The projection is proven over the guard's state space, not over all inputs | `COMPLETE` | — | tests/reliability/test_acceptance_gate_enablement.py | PHASE_GATE_ENABLEMENT.md:206 — “a reversal condition, not an open item (2026-09-27)” — the guard test re-derives the equivalence, so this is a tripwire the register already has a column for |

---

## (a) The state vocabulary — the ledger's, stated once

`WISP_MIGRATION_STATUS.md:41` states the vocabulary:

> `NOT STARTED` · `IN PROGRESS` · `COMPLETE` · `BLOCKED` · `PARTIAL` · `SUPERSEDED`

| word | what it claims |
|---|---|
| `NOT STARTED` | Nothing has been done. The item stands as written. |
| `IN PROGRESS` | Work is under way and the item is not finished. The `blocked_by` cell
  carries whatever the source names as the remaining obstacle. |
| `PARTIAL` | Some named part landed and another did not. The source says which is which. |
| `BLOCKED` | The item cannot proceed. **No item is currently in this state** (ADR-0062 R3.2):
  every source that could has preferred `IN PROGRESS` or `NOT STARTED` plus a stated `blocked_by`. |
| `COMPLETE` | The item is finished. For an item closed by a *decision* rather than a change,
  the decision is cited in the source cell. |
| `SUPERSEDED` | A later decision replaced the item's question. |

**Other sources' words, mapped.** Four sources name states in their own vocabulary:

| the source writes | this page | where |
|---|---|---|
| `OPEN` | `NOT STARTED` | `CONTEXT.md` §12 |
| `✅ DONE` · `FIXED` · `CLOSED` | `COMPLETE` | `CONTEXT.md` §12 |
| `DECIDED` | — (a *reason*; ADR-0062 R3) | `CONTEXT.md` §12 **until ADR-0062**, now written *Reason:* after `COMPLETE` |
| `Accepted (low)` · `Accepted` · `Unresolved, no action` | `COMPLETE` | `CONTEXT.md` §12 **until ADR-0065**, which ruled a recorded disposition is a closure and is written *Reason:* after `COMPLETE` — except where the disposition names work still owed, which is `PARTIAL` (ADR-0065 R3) |
| `DEFERRED` · `NOT DONE` | `NOT STARTED` | `WISP_MIGRATION_STATUS.md` per-item tables |
| `BLOCKED_ON_PRECONDITION` | — | **not used anywhere in this corpus** (§(b)) |

**§12 is not rewritten by this mapping.** A disposition word stays in `CONTEXT.md` §12 as the
record of what was decided; this page records the *state* that follows from it. So §12's `M10`
row still reads *“OPEN — by design.”* while this page carries `M10` as `COMPLETE`, and §12's
`F4` row still reads *“deletion candidate”* while the instrument pins it as deliberately dead.
That is the same split ADR-0062 R2 chose over renaming the id namespaces — the historical
record is not amended. The disposition is quoted in each row's source cell, so the two can be
**compared** rather than assumed to agree; a divergence here is not drift, it is the mapping.

---

## (b) The reasons are not states

**The rule, stated once.** A *reason* explains why an item is in a state; it is not itself a
state. The ledger has one state vocabulary and one column for reasons — `blocked_by` — and
coining a state word for a reason leaves two vocabularies in the ledger, which is how `OPEN`
and `NOT STARTED` came to coexist for the same state.

**Every place a reason was recorded as a state.** One, measured — and **repaired by ADR-0062 R3**:

- **`DECIDED`** — `CONTEXT.md` §12's table used it as a state word for **`M8`**, **`M11`** and
  **`Layer C`**. It names *why* the item is finished (an ADR decided it) rather than *that* it
  is. ADR-0062 R3 decided it is a reason: each of the three now reads `COMPLETE`, followed by
  *Reason:* and the deciding ADR — quoted in the source cells above.
- **`BLOCKED`** is a state the ledger defines and **no item is currently in** (ADR-0062 R3.2).
  Every item that is blocked is recorded as `IN PROGRESS` or `NOT STARTED` with a stated
  obstacle. A defined word with no members is a vocabulary, not a defect, and the word is kept so
  a future blocked item is representable.

**The brief's own vocabulary, checked.** The mission brief lists `OPEN · IN_PROGRESS ·
BLOCKED · DECIDED · CLOSED · SUPERSEDED` as *"the ledger's own vocabulary"*. It is not: the
ledger's is `NOT STARTED · IN PROGRESS · COMPLETE · BLOCKED · PARTIAL · SUPERSEDED`. Three of
the brief's six (`OPEN`, `DECIDED`, `CLOSED`) are absent from it, and two of the ledger's
(`NOT STARTED`, `PARTIAL`) are absent from the brief's. Recorded in §Findings.

---

## (c) The open count, by state

Measured 2026-10-08 at `52e90f6` over the 102 rows below. A count is canonical
only if it is measured after the LAST change to any member (**F85**), which is why the
generator recomputes it rather than the page stating it.

| state | count |
|---|---|
| `NOT STARTED` | 20 |
| `IN PROGRESS` | 1 |
| `PARTIAL` | 2 |
| `BLOCKED` | 0 |
| `COMPLETE` | 76 |
| `SUPERSEDED` | 3 |
| **total** | **102** |

**Open** — everything not in §The closed items — **23** of 102.

**How many are the host rather than the architecture.** Seven of the open rows name a
declared-and-absent dependency, a resource limit, or the user's own uncommitted work: `R7`,
`R8`, `M1` (its population), `ADR-0053 R1`, `ADR-0054 R1`, `ADR-0059 R1`, `ADR-0061 R1`. The
rest are architectural. **This list shrinks as rows close** — `R9` and `ADR-0058 R2` went with
ADR-0065, then `R6` and `ADR-0061 R4` once the five absent dependencies were installed.

---

## §Findings — disagreements between sources, and what could not be pinned

This page may **record** a disagreement; it may not resolve one.

### `CONTEXT.md` §12 against §0.0 — the cross-check the brief asks for

**No disagreement found, and that is a measured result.** §12's rows for `E`, `G1`, `R1b` and
`W1` all read `CLOSED`, and §0.0's narrative for each records the same closure with the same
ADR. The one historical disagreement the corpus records is **F98** — §12's `G1` row said
`OPEN` until 2026-09-25 while §0.0.14 recorded it closed — and it was repaired at
`PHASE_REST_AUTHORIZATION_COMPOSITION.md`. This page was derived **after** that repair, so it
cannot see the pre-repair state; it records that the check was run and found nothing, rather
than claiming the sources cannot disagree.

### The same id in two namespaces

- **`F1`–`F5`.** `CONTEXT.md` §12 uses `F1`–`F5` as **open-item** ids (Phase 10's defect
  ledger). `WISP_MIGRATION_STATUS.md` §23 and `CURRENT_FINDINGS.md` use `F1`–`F104` as
  **finding** ids. `§12`'s `F1` (`metadata["_budget"]` write-only) and the findings log's
  `F1` (`test_canonical_execution_state.py` already exists) are different things with the same
  name. **Decided by ADR-0062 R2: tolerated, not renamed** — a citation writes `ITEM-F1` for
  §12's rows and `FIND-F1` for a finding, and §12 states the rule once.
- **`M4`** — and, measured, **`M1`–`M7`**. `CONTEXT.md` §12's `M4` is *ADR-0004 revisited* (a
  migration item, also `WISP_MIGRATION_STATUS.md:204`). `PHASE_10_M4_GOVERNANCE_UNWIRED.md` and
  `AGENTS.md`'s module map use *M4* for the enterprise track's **governance layer**, which §12
  carries as row **`E`** — and the enterprise track's `M1`–`M7` collide with the migration's
  `M1`–`M7` the same way. This page used to name `WISP_MIGRATION_STATUS.md:2041` as a third
  `M4`; **that line has never named `M4`** (corpus governance II §5). **Annotated once at §12,**
  per ADR-0062 R2. **Finding F99** records the same collision from the other side.

### Claims that cannot be pinned

- **The brief's state vocabulary is not the ledger's.** Recorded in §(b). The register uses
  the ledger's, because the brief itself says *"the ledger's vocabulary governs"*.
- **`BLOCKED` has no members** — no item is currently in this state (ADR-0062 R3.2). Kept,
  not removed, and no `BLOCKED` row is invented, because that would be coining a state.
- **No ADR before 0053 has a named-residual section.** The ADR log's residual lists begin at
  ADR-0053; ADR-0001–0052's residuals, where they exist, are in their phase reports instead.
  So *"every ADR's named residuals"* resolves to **six** ADRs, not 61 — a scope the brief's
  wording does not state. Measured: `### Residuals` headings in
  `WISP_ARCHITECTURE_DECISIONS.md` occur at ADR-0053, 0054, 0057, 0058, 0059 and 0061.

### Quotations that were never verbatim

- **`ITEM-F3`.** Its source cell quoted *"Accepted (low) — annotate so nobody wires them without
  the missing checks"*. The `CONTEXT.md` §12 row has read *"Accepted (low) — the executor
  authorises per call; annotate so nobody wires …"* since it was first written (`98bb8f9`): the
  register dropped a clause **with no elision mark**. The source was **not** amended, so this is a
  transcription defect in this page, not a drifted status. **The elision is now marked (`…`); no
  word was added or changed.** Found by the source-quote check (`PHASE_REGISTER_SOURCE_PINS.md` §4).

### What this page did not do

- **No new decision, and no new item id.** Every id is the source's own name.
- **No source was edited by this page.** §12's `DECIDED` word and the id collisions were
  repaired at their source, by edits ADR-0062 R2/R3 authorise; this page records the result.
- **No closed item was deleted.** They are kept in §The closed items so a regression is
  visible as a move between the two tables.
