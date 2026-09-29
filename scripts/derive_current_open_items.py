#!/usr/bin/env python3
"""Derive `CURRENT_OPEN_ITEMS.md` from the corpus.

**Why committed.** Same reason as `derive_current_findings.py`: `CONTEXT.md` §0.0.9's
finding **F75** — *"an instrument that cannot be committed is not a re-runnable
measurement."* The page declares itself derived; a hand regeneration is the drift the page
exists to prevent.

**The sources, and why there are four.** An open item in this corpus is named in any of:
`CONTEXT.md` §12 (the live open-items table), `CONTEXT.md` §0.0.x, `WISP_MIGRATION_STATUS.md`
(the phase ledger and its per-phase deferred items), each ADR's `### Residuals` section, and
each phase report's residual section. Nothing compares them — which is why §12's `G1` row said
`OPEN` for three ADRs while `§0.0.14` recorded it closed (**F98**).

Run: `env -u PYTHONPATH .venv/bin/python scripts/derive_current_open_items.py`
"""
from __future__ import annotations

import datetime
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT = REPO / "CURRENT_OPEN_ITEMS.md"

#: The ledger's vocabulary, verbatim (`WISP_MIGRATION_STATUS.md:41`). The register uses these
#: six and only these six. §(a) maps every other source's word onto them.
VOCABULARY = ("NOT STARTED", "IN PROGRESS", "PARTIAL", "BLOCKED", "COMPLETE", "SUPERSEDED")

#: States that mean the item is finished. Everything else is an open item.
CLOSED_STATES = frozenset({"COMPLETE", "SUPERSEDED"})

#: `(id, title, state, blocked_by, tripwire, source)`.
#:
#: `id` is the item's name **as its source uses it**. `id` is not unique across sources —
#: §12's `F1`–`F5` are open items, and the findings log's `F1`–`F104` are findings. The
#: `source` cell disambiguates, and §Findings records the collision.
ROWS: list[tuple[str, str, str, str, str, str]] = [
    # ── CONTEXT.md §12 — "Open items" (the legacy / non-migration table) ─────
    ("R1", "REST gate — finish option B", "COMPLETE", "—", "—",
     "CONTEXT.md:2459 — “✅ **DONE** (§0)”"),
    ("R2", "Correct the “breaks the client” claim", "COMPLETE", "—", "—",
     "CONTEXT.md:2460 — “✅ **DONE** (§5)”"),
    ("G0", "REST bypass of the protected-path guard", "COMPLETE", "—", "—",
     "CONTEXT.md:2461 — “✅ **DONE** (§0b)”"),
    ("E", "The M4 governance layer is not wired to the runtime", "COMPLETE", "—",
     "tests/test_m4_governance_wiring.py",
     "CONTEXT.md:2462 — “✅ **CLOSED 2026-09-25.** The key-trust decision is **ADR-0058** … and the wiring landed”"),
    ("G1", "Authorization parity — the agent composes both models; REST consulted only `SecurityPolicy`", "COMPLETE", "—",
     "tests/test_authorization_parity.py",
     "CONTEXT.md:2463 — “✅ **CLOSED.** **ADR-0055** drove the real paths: **0 path divergences of 36**”"),
    ("R1b", "`POST /api/hooks` still accepts an unvalidated `command`", "COMPLETE", "—",
     "tests/reliability/test_external_input_path.py",
     "CONTEXT.md:2464 — “✅ **CLOSED 2026-09-25 (ADR-0061 R6 / G3).**”"),
    ("W1", "The agent path's WebSocket approval prompt had never rendered", "COMPLETE", "—",
     "tests/reliability/test_external_input_path.py",
     "CONTEXT.md:2464 — “✅ **CLOSED 2026-09-25 (ADR-0061).**”"),
    ("R10", "`useApi.ts:368` sends no `Authorization` header", "PARTIAL", "—", "—",
     "CONTEXT.md:2466 — “✅ **FIXED** (§0f) — the functional half. **What remains is a decision**”"),
    ("F1", "`metadata[\"_budget\"]` write-only", "COMPLETE", "—", "—",
     "CONTEXT.md:2467 — “✅ **FIXED** (§0e.2)”"),
    ("F2", "`_SENSITIVE_ENV_KEYS` has no consumer", "COMPLETE", "—", "—",
     "CONTEXT.md:2468 — “✅ **FIXED** (§0e.1) — deleted as superseded”"),
    ("F3", "`execute_tool(security_policy=…)` — no caller passes it", "COMPLETE", "—", "wisp/tools/registry.py",
     "CONTEXT.md:2469 — “✅ **CLOSED 2026-09-27.**” — the owed annotation **landed**: `registry.py:911` now *prohibits* wiring this entrypoint without the checks `ToolRegistry.execute` supplies. Prose-only, proved by both instruments — **ADR-0065 R3** satisfied"),
    ("F4", "`spawn_with_guards` is a dead duplicate", "COMPLETE", "—", "tests/test_unwired_controls_inventory.py",
     "CONTEXT.md:2470 — “**Accepted** — deletion candidate”; *Reason:* accepted, and pinned as deliberately dead — `tests/test_unwired_controls_inventory.py:283` asserts the variant is still uncalled (*“it is no longer dead code”*), so *“deletion candidate”* is superseded by the instrument and the tripwire cell is corrected — **ADR-0065 R1**"),
    ("F5", "event-replay `TOOL_CALL` — the referent is unidentifiable", "COMPLETE", "—", "—",
     "CONTEXT.md:2471 — “**Unresolved, no action** — recorded as unidentified rather than guessed at”; *Reason:* resolved as *no action* — recorded unidentified rather than guessed at, which is §12's own disposition — **ADR-0065 R1**"),
    ("G2", "The `run_bash` verb scan is a separate mechanism from the predicate", "COMPLETE", "—", "—",
     "CONTEXT.md:2472 — “**Accepted** — a shell command's target is not determinable from its text”; *Reason:* accepted — the same ground ADR-0061 used to close R1b, and the reason a runnable check, a blocklist and an allow-list are each rejected — **ADR-0065 R1**"),
    ("R3", "Full provider-listing delegation", "NOT STARTED",
     "the 3 deltas (auth/timeout/degradation) must converge",
     "tests/test_provider_listing_equivalence.py",
     "CONTEXT.md:2473 — “Unsafe until the 3 deltas … converge; `test_provider_listing_equivalence.py` fails at that point”"),
    ("R4", "`_is_transient` is a separate predicate", "COMPLETE", "—", "—",
     "CONTEXT.md:2474 — “**Not debt** — different axis (retryability, not outcome class)”"),
    ("R5", "Two `RunStatus` enums remain", "COMPLETE", "—", "—",
     "CONTEXT.md:2475 — “**Resolved as a non-issue by the migration.** … **No shim needed** — ADR-0003”"),
    ("R6", "`.venv` missing deps", "COMPLETE", "—", "—",
     "CONTEXT.md:2469 — “✅ **CLOSED 2026-09-27.**”; *Reason:* all five deps installed at their `uv.lock` pins, plus `wcwidth`, so nothing is absent — the obstacle was TLS, not network (F-T9)"),
    ("R7", "`capability_filter.py` untracked but imported", "NOT STARTED", "the file is the user's untracked WIP (§8)", "—",
     "CONTEXT.md:2477 — “See §8”"),
    ("R8", "3 untracked test files abort collection", "NOT STARTED", "the files are the user's WIP", "—",
     "CONTEXT.md:2478 — “User's WIP”"),
    ("R9", "`wisp/core/graph/__init__.py` modified, uncommitted", "COMPLETE", "—", "—",
     "CONTEXT.md:2479 — “User's pre-existing edit”; *Reason:* decided by **ADR-0060 R5**, which holds that this file *“needs **no edit**”* and that the dead part is a retained reference implementation, quoting this file's own uncommitted docstring as the reason; independently driven, the diff is a module docstring only — docstring-stripped AST identical and recursive `co_code` identical, prose-only — **ADR-0065 R1**"),

    # ── CONTEXT.md §12 — "Migration open items — current" ────────────────────
    ("M1", "P3 stage 3b — enable the acceptance gate", "IN PROGRESS",
     "ADR-0051 R4 requires **≥ 2 capable models** and this host serves exactly **1** of 13 — an environment fact, not a code change",
     "tests/reliability/test_acceptance_gate_enablement.py",
     "CONTEXT.md:2495 — “**`IN_PROGRESS`** — the precondition is satisfied **and** the mechanism is built and driven; what remains is the population”"),
    ("M2", "Journal-first reconstruction", "COMPLETE", "—", "tests/test_session_reconstruction.py",
     "CONTEXT.md:2493 — “✅ **COMPLETE** … **Five consumers still read the blob** (a tripwire asserts it)”"),
    ("M3", "Killpoint integration", "COMPLETE", "—", "tests/reliability/test_killpoints.py",
     "CONTEXT.md:2497 — “✅ **COMPLETE** — `test_kp_session_midtool_then_killed`. One window covered.”"),
    ("M4", "ADR-0004 revisited", "COMPLETE", "—", "—",
     "CONTEXT.md:2498 — “✅ **COMPLETE** — **ADR-0027**. Found a live defect”"),
    ("M5", "Foreground-turn `RunRecord` lifecycle", "NOT STARTED", "—", "—",
     "CONTEXT.md:2499 — “**OPEN** — proven end-to-end for background runs only.”"),
    ("M6", "`PolicyDecisionEnvelope` producer-less and consumer-less", "COMPLETE",
     "—", "tests/test_contracts_policy.py",
     "CONTEXT.md:2469 — “✅ **CLOSED 2026-09-27.**” — measured, **four of six** `wisp/contracts/` modules have no production importer, so it is *not* the last; that is the M1a freeze, which the spec defines as additive (*“Pure addition: no existing producer or consumer changes behavior”*) — a disposition, and **not** a deletion candidate"),
    ("M7", "`change_tracker.py` not wired into evidence", "NOT STARTED", "deferred with stage 3b", "—",
     "CONTEXT.md:2501 — “**OPEN** — deferred with 3b.”"),
    ("M8", "`multi_agent/dag.py` not retired into `wisp/graph/`", "COMPLETE",
     "— (re-scoped by ADR-0060: the divergence is the *boundary*, not a blocker; the removal is **not owed**)",
     "tests/reliability/test_dag_retirement_contract.py",
     "CONTEXT.md:2502 — “✅ **COMPLETE.** *Reason:* surveyed and decided 2026-09-25 — **`DEPRECATE`, not remove** — and **re-scoped by ADR-0060**”"),
    ("M9", "The execution view", "COMPLETE", "—", "—",
     "CONTEXT.md:2488 — “✅ **COMPLETE** — ADR-0029.”"),
    ("M10", "The materialized graph is a lower bound on iterations", "COMPLETE",
     "— (by design: iteration boundaries are not observable)", "—",
     "CONTEXT.md:2503 — “**OPEN — by design.**”; *Reason:* by design — iteration boundaries are not observable, which is a disposition and not an obstacle — **ADR-0065 R1**"),
    ("M11", "The graph does not drive execution", "COMPLETE", "—",
     "tests/reliability/test_layer_b_boundary.py",
     "CONTEXT.md:2489 — “✅ **COMPLETE.** *Reason:* **decided, not deferred** (ADR-0060; ADR-0062 R3). ADR-0033 … **ADR-0060 closes the second half**”"),
    ("M12", "The failure path", "COMPLETE", "—", "—",
     "CONTEXT.md:2490 — “✅ **COMPLETE** — ADR-0032.”"),
    ("M13", "The stagnation detector is not constructed by the turn loop", "COMPLETE", "—",
     "tests/test_stagnation_live_wiring.py",
     "CONTEXT.md:2491 — “✅ **COMPLETE** — ADR-0034. … **Enforcement deferred**: routing and goal-met gating are tripwired.”"),
    ("M14", "The context trust boundary", "COMPLETE", "—", "tests/test_prompt_section_trust.py",
     "CONTEXT.md:2492 — “✅ **COMPLETE** — ADR-0031. … T2 fencing remains, deliberately staged.”"),
    ("M15", "The subagent spawn site", "COMPLETE", "—", "tests/test_child_principal_wired.py",
     "CONTEXT.md:2493 — “✅ **COMPLETE** — ADR-0030.”"),
    ("M16", "The `ESCALATION` record's loss is not fully addressed", "COMPLETE", "—", "—",
     "CONTEXT.md:2494 — “✅ **COMPLETE** — ADR-0028.”"),
    ("Layer C", "`wisp/core/graph/` — named *disowned* and consumed by the live path", "COMPLETE", "—",
     "tests/reliability/test_layer_c_disposition.py",
     "CONTEXT.md:2504 — “✅ **COMPLETE.** *Reason:* decided 2026-09-25 by **ADR-0060 R5** (ADR-0062 R3). The live symbols **moved**”"),

    # ── WISP_MIGRATION_STATUS.md — the phase ledger's non-COMPLETE rows ──────
    ("P0 · item 6", "A normal turn creates a `RunRecord` row", "COMPLETE", "—", "—",
     "WISP_MIGRATION_STATUS.md:293 — “`DEFERRED to P1`”; P1 is `COMPLETE` (`:193`)"),
    ("P1 · item 3", "Journal replaces the snapshot as the primary record", "COMPLETE", "—", "—",
     "WISP_MIGRATION_STATUS.md:380 — “`DEFERRED to P2`”; P2 is `COMPLETE` (`:194`)"),
    ("P3 · item 5", "Structural independence (L1/L2)", "COMPLETE", "—", "—",
     "WISP_MIGRATION_STATUS.md:511 — “closed 2026-09-27” — L3 is *preferred, not required* by the plan, so its absence is not owed; L1/L2 are done and pinned"),
    ("P3 · item 6", "The completion rule requires non-invalidated evidence", "NOT STARTED",
     "it is stage 3b, which is M1", "—",
     "WISP_MIGRATION_STATUS.md:512 — “`NOT DONE` — **that is stage 3b** — the plan's staging; `turn_succeeded` is unchanged”"),
    ("P3 · item 7", "Wire `change_tracker.py` into evidence", "NOT STARTED", "deferred with 3b (M7)", "—",
     "WISP_MIGRATION_STATUS.md:513 — “`NOT DONE` — deferred with 3b”"),
    ("P4 · item 1", "Reuse `wisp/graph/`'s store", "COMPLETE", "—", "—",
     "WISP_MIGRATION_STATUS.md:551 — “closed 2026-09-27” — the store is *deliberately not* shared; a second DB would fragment the durable record (ADR-0019) — a disposition, not an omission"),
    ("P5 · item 5", "Extend the executor to accept a mid-run node", "COMPLETE",
     "— (ADR-0060 measured it inexpressible and rejected Position B)", "—",
     "WISP_MIGRATION_STATUS.md:635 — “`NOT DONE` — **deferred** — §7.5”; §7.5's target was **rejected** by ADR-0060"),
    ("P7 · item 1", "Wire `OscillationTrap` to the live loop", "COMPLETE", "—", "tests/test_stagnation_live_wiring.py",
     "WISP_MIGRATION_STATUS.md:747 — “`PARTIAL` … the live turn loop does not construct a detector (M13)”; M13 is `COMPLETE`"),
    ("P8 · item 3", "Graph context section scoped to the current node", "COMPLETE",
     "—", "tests/reliability/test_layer_b_boundary.py",
     "WISP_MIGRATION_STATUS.md:862 — “closed 2026-09-27 by ADR-0060 R2” — no current node exists *by decision*, so the section has no referent and is not owed"),
    ("P8 · item 4", "Populate plan context (`PlanState`, `## PLAN MODE ACTIVE`)", "COMPLETE",
     "—", "tests/test_prompt_section_trust.py",
     "WISP_MIGRATION_STATUS.md:863 — “closed 2026-09-27 by ADR-0063 R2” — the slot is `OPERATOR`-tagged, so \"populate\" is answered **remove**; the section survives only for the operator-endorsed path"),
    ("P8 · item 5", "Serve the symbol-level repo map", "NOT STARTED",
     "`tiktoken` is installed, so the budget is now measurable — the item itself is not implemented", "—",
     "WISP_MIGRATION_STATUS.md:864 — “`NOT DONE` — the plan requires **measure first**”"),
    ("P8 · item 6", "Token-based compaction", "NOT STARTED",
     "it changes when context is destroyed, on the least observable path", "—",
     "WISP_MIGRATION_STATUS.md:865 — “`NOT DONE`”"),
    ("P8 · item 7", "Memory origin", "PARTIAL", "—", "—",
     "WISP_MIGRATION_STATUS.md:866 — “`PARTIAL` — `Provenance` supplies the field; `memory.py` is not yet wired to use it”"),
    ("P9 · item 1", "Wire `derive_subagent`", "COMPLETE", "—", "tests/test_child_principal_wired.py",
     "WISP_MIGRATION_STATUS.md:932 — “`PARTIAL` … **the spawn site is M15**”; M15 is `COMPLETE`"),
    ("P9 · item 2", "Structured `child_goal`", "NOT STARTED", "deferred (§11.4)", "—",
     "WISP_MIGRATION_STATUS.md:933 — “`NOT DONE` — deferred”"),
    ("P9 · item 3", "Mandatory `result_schema`", "NOT STARTED", "deferred", "—",
     "WISP_MIGRATION_STATUS.md:934 — “`NOT DONE` — deferred”"),
    ("P9 · item 4", "Transactional effects", "NOT STARTED", "deferred", "—",
     "WISP_MIGRATION_STATUS.md:935 — “`NOT DONE` — deferred”"),
    ("P9 · item 5", "Typed failure replacing prose markers", "NOT STARTED", "deferred", "—",
     "WISP_MIGRATION_STATUS.md:936 — “`NOT DONE` — deferred”"),
    ("P9 · item 8", "Retire `dag.py` into `wisp/graph/`", "COMPLETE", "— (M8; re-scoped by ADR-0060)", "—",
     "WISP_MIGRATION_STATUS.md:939 — “`NOT DONE` — already item **M8**”; M8 is now `COMPLETE` by decision"),

    # ── The ADRs' named residuals ───────────────────────────────────────────
    ("ADR-0053 R1", "No declared turn population exists, so the `GOAL_MET` rate ADR-0051 R2 specifies cannot be produced", "NOT STARTED",
     "the population is the missing input (= M1's population)", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:5169 — “**No declared turn population exists**”"),
    ("ADR-0053 R2", "A declared failure does not produce a replan", "NOT STARTED",
     "making it repairable means moving the probe into the engine — its own decision", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:5171 — “It is recorded (`GOAL_FAILED`) rather than repaired within the turn”"),
    ("ADR-0053 R3", "`command_succeeds` on the turn path runs the declared command after `done`, once per declared turn", "COMPLETE",
     "— (a stated cost; it is why the flag defaults OFF)", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:5174 — “That cost is real and is the reason the flag defaults OFF.”; *Reason:* a stated cost, not a defect — it is why the flag defaults OFF — **ADR-0065 R1**"),
    ("ADR-0054 R1", "The population is short by one capable model", "NOT STARTED",
     "an *environment* fact, not a design one", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:5321 — “This is the only thing between the contract and the enablement decision”"),
    ("ADR-0054 R2", "The declared command runs once per declared turn (at the gate; reused at the verdict site)", "COMPLETE",
     "— (a stated cost)", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:5323 — “that is a real cost, and it is why the flag defaults OFF”; *Reason:* a stated cost, not a defect — **ADR-0065 R1**"),
    ("ADR-0054 R3", "The gate shares the stagnation gate's budget", "COMPLETE",
     "— (the intended reading of a turn-level bound, stated rather than discovered)", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:5325 — “That is the intended reading of a turn-level bound”; *Reason:* the intended reading, stated rather than discovered — **ADR-0065 R1**"),
    ("ADR-0054 R4", "`stagnation_gate` is untouched", "COMPLETE",
     "—", "tests/reliability/test_acceptance_gate_enablement.py",
     "WISP_ARCHITECTURE_DECISIONS.md:5327 — “CLOSED 2026-09-27” — both gates are wired and default OFF *independently*, which is the disposition ADR-0037 requires, not a gap"),
    ("ADR-0057 R1", "The agent path's WebSocket approval prompt has never rendered", "COMPLETE", "—",
     "tests/reliability/test_external_input_path.py",
     "WISP_ARCHITECTURE_DECISIONS.md:5826 — “`approve()` sends a frame no client reads”; closed by **ADR-0061**"),
    ("ADR-0057 R2", "`resolve_approval` still ignores the client's `id` when the transport resolves it", "COMPLETE",
     "—", "tests/reliability/test_external_input_path.py",
     "WISP_ARCHITECTURE_DECISIONS.md:5831 — “CLOSED 2026-09-27 by ADR-0066 R5” — the single-pending fallback is kept as a stated back-compat shim, unreachable for a correct client"),
    ("ADR-0057 R3", "G3 remains open (R9)", "COMPLETE", "—", "tests/reliability/test_external_input_path.py",
     "WISP_ARCHITECTURE_DECISIONS.md:5834 — “**G3 remains open** (R9)”; closed by **ADR-0061** (`CONTEXT.md` §12 row R1b)"),
    ("ADR-0057 R4", "A multi-client deployment asks every registered channel and takes the first response", "COMPLETE",
     "—", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:5834 — “A multi-client deployment asks every registered channel” — **CLOSED 2026-09-27 by ADR-0066 R6**: per-client routing is *not owed*, having no client identity to route by"),
    ("ADR-0058 R1", "The multi-device ceremony — a control plane, a key-distribution server and a registration UI", "COMPLETE",
     "—", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:5998 — “CLOSED 2026-09-27” — the M4 spec (§5) deferred the ceremony rather than omitting it, so it is **not owed**"),
    ("ADR-0058 R2", "`WISP_POLICY_CACHE` and `load_managed` are not engaged", "COMPLETE",
     "— (this decision wires the `local-only` path only)", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:6000 — “The managed/disconnected modes have a network story and a cache”; *Reason:* by design — ADR-0058 wires the `local-only` path only, and the same disposition closed ADR-0058 R4 (*“by design”*) — **ADR-0065 R1**"),
    ("ADR-0058 R3", "REST does not receive L0", "COMPLETE", "—",
     "tests/reliability/test_rest_authorization_composition.py",
     "WISP_ARCHITECTURE_DECISIONS.md:6002 — “`SecurityPolicy.check()` has **no organization layer**”; closed by **ADR-0059**"),
    ("ADR-0058 R4", "Private-key custody is not implemented by Wisp", "COMPLETE", "— (by design, R7)", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:6007 — “(R7), by design”"),
    ("ADR-0059 R1", "A bundle's `approve` level is inert on REST", "COMPLETE",
     "—", "tests/reliability/test_external_input_path.py",
     "WISP_ARCHITECTURE_DECISIONS.md:6163 — “CLOSED 2026-09-27.” — closed **in effect** (ADR-0061, re-derived over the **ten** pinned pairs: all satisfy `allowed and approval_required` and REST asks) and **in mechanism** (ADR-0066 R1 decided the trigger is a *set*, not a read of `approval_required`)"),
    ("ADR-0059 R2", "REST's consult is conditional on a bundle, so L1/L2/L3 are not consulted without one", "COMPLETE",
     "—", "tests/reliability/test_rest_authorization_composition.py",
     "WISP_ARCHITECTURE_DECISIONS.md:6167 — “CLOSED 2026-09-27 by ADR-0068” — L2 is now applied unconditionally and last; L1/L3 stay bundle-gated and are not gaps. Driven: quarantined + no bundle → 403 by the workspace trust layer"),
    ("ADR-0059 R3", "REST still reimplements L4 (the protected-path predicate)", "COMPLETE",
     "—", "tests/test_protected_path_guard.py",
     "WISP_ARCHITECTURE_DECISIONS.md:6173 — “REST no longer reimplements L4 — CLOSED 2026-09-27.”; *Reason:* the scan is now the canonical `wisp.pathsec.touches_protected_path`, called by both `auth/decision`'s L4 and the REST gate, each keeping its own refusal message — a landed change"),
    ("ADR-0059 R4", "Two bundle sources — the env-var path and the publish route's held bundle", "COMPLETE",
     "—", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:6176 — “CLOSED 2026-09-27.” — *Named, not merged* is the disposition: merging is a distribution decision and no deployment here has two sources to merge"),
    ("ADR-0059 R5", "The three REST-only names have an enforced rule and still have no agent operation", "COMPLETE", "—", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:6179 — “That is the honest end state”"),
    ("ADR-0061 R1", "The round-trip is pinned against a stub channel, not a live client", "NOT STARTED",
     "no real desktop/TUI/VS Code client runs on this host", "tests/test_ws_control_plane.py",
     "WISP_ARCHITECTURE_DECISIONS.md:6588 — “the *frame shape* and the *no-client behaviour* are driven and the end-to-end render is not”"),
    ("ADR-0061 R2", "`receive_message`'s own `tool_approval` branch still ignores `msg[\"id\"]`", "COMPLETE",
     "—", "tests/reliability/test_external_input_path.py",
     "WISP_ARCHITECTURE_DECISIONS.md:6595 — “CLOSED 2026-09-27 by ADR-0066 R5” — the same stated back-compat shim as ADR-0057 R2"),
    ("ADR-0061 R3", "A multi-client deployment still asks every registered channel", "SUPERSEDED",
     "— (ADR-0057 residual 4, unchanged)", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:6596 — “unchanged”; *Reason:* **superseded by `ADR-0057 R4`** — the same item under a second id, as this row's own `blocked_by` concedes — **ADR-0065 R5**"),
    ("ADR-0061 R4", "The bundle half of ADR-0059 residual 1 is un-measurable here", "COMPLETE",
     "—", "tests/test_policy_bundle.py",
     "WISP_ARCHITECTURE_DECISIONS.md:6598 — “The bundle half of ADR-0059 residual 1 is now measurable — CLOSED 2026-09-27.”; *Reason:* `cryptography` is installed, so the `approve` level's effect on the REST verdict can be driven — residual 1's own divergence stays tracked by ADR-0059 R1"),

    # ── The phase reports' residual sections ────────────────────────────────
    ("PHASE_AUTHORIZATION_PARITY R1", "The approval authority is split three ways", "COMPLETE",
     "—", "tests/reliability/test_rest_approval.py",
     "PHASE_AUTHORIZATION_PARITY.md:119 — “CLOSED 2026-09-27 by ADR-0066 R1” — the three are three *questions*, not three copies; unifying is rejected on ADR-0055's measured ground"),
    ("PHASE_AUTHORIZATION_PARITY R2", "Three action names are in none of the three approval sets", "SUPERSEDED",
     "—", "tests/reliability/test_rest_approval.py",
     "PHASE_AUTHORIZATION_PARITY.md:121 — “SUPERSEDED 2026-09-27.”; *Reason:* **superseded by ADR-0057**, which created `REST_APPROVAL_ACTIONS` for exactly these three names — recorded by ADR-0066 R2, source not rewritten"),
    ("PHASE_AUTHORIZATION_PARITY R3", "REST cannot ask a human — a REST caller gets the no-approver fall-through", "COMPLETE", "—",
     "tests/reliability/test_rest_approval.py",
     "PHASE_AUTHORIZATION_PARITY.md:131 — “**Not repaired here** — … Option C is the fix”; landed as **ADR-0057** (`PHASE_REST_APPROVAL.md`)"),
    ("PHASE_AUTHORIZATION_PARITY R4", "Five further gated routes are not in the parity table", "COMPLETE",
     "—", "tests/reliability/test_rest_approval.py",
     "PHASE_AUTHORIZATION_PARITY.md:132 — “MEASURED 2026-09-27.” — all five pass the policy gate and none asked a human; **ADR-0066 R3** gated the two executing verbs and R4 kept the rest out by decision"),
    ("PHASE_M4_WIRING R1", "REST does not receive L0", "COMPLETE", "—",
     "tests/reliability/test_rest_authorization_composition.py",
     "PHASE_M4_WIRING.md:186 — “its own decision, pinned so it cannot drift silently”; closed by **ADR-0059**"),
    ("PHASE_M4_WIRING R2", "`acp_session.py:208` — an ACP-only deployment would need the same load", "COMPLETE",
     "—", "tests/test_acp_session.py",
     "PHASE_M4_WIRING.md:187 — “CLOSED 2026-09-27 by ADR-0067” — the fallback is ungoverned *by construction* and now warns; loading it here is the second load site ADR-0058 R1 forbids"),
    ("PHASE_M4_WIRING R3", "The M4 policy suite cannot run here", "COMPLETE",
     "—", "tests/test_policy_bundle.py",
     "PHASE_M4_WIRING.md:188 — “The M4 policy suite could not run here” (F88) — **CLOSED 2026-09-27**; *Reason:* the six files report 43 passed, 0 failed, 0 errors — a landed change"),
    ("PHASE_M4_WIRING R4", "F89 — the falsy-`expires_at` message", "COMPLETE", "—",
     "tests/reliability/test_key_trust_workflow.py",
     "PHASE_M4_WIRING.md:189 — “the falsy-`expires_at` message”; closed by corpus integrity III (`CONTEXT.md` §0: 2.1 **CLOSED**)"),
    ("PHASE_M4_WIRING R5", "`WISP_POLICY_CACHE` / `load_managed` are not engaged", "SUPERSEDED",
     "— (its cited `ADR-0058 R6` does not exist; see **ADR-0065** residual 2)", "—",
     "PHASE_M4_WIRING.md:190 — “the managed/disconnected modes are not engaged (ADR-0058 R6)”; *Reason:* **superseded by `ADR-0058 R2`** — the same item under a second id, and its cited residual `ADR-0058 R6` dangles (ADR-0058's residual list has R1–R4) — **ADR-0065 R5**"),
    ("PHASE_KEY_TRUST_WORKFLOW R6", "The happy path of the key-trust workflow is not exercised by any test here", "COMPLETE",
     "—", "tests/test_policy_bundle.py",
     "PHASE_KEY_TRUST_WORKFLOW.md:235 — “The happy path of the key-trust workflow is now exercised — CLOSED 2026-09-27.”; *Reason:* `test_sign_verify_round_trip` drives a real Ed25519 keypair through sign and verify — a landed change"),
    ("PHASE_DAG_RETIREMENT R1", "`TaskDAG.validate()` mis-reports an unknown dependency as a cycle", "COMPLETE",
     "—", "tests/reliability/test_dag_retirement_contract.py",
     "PHASE_DAG_RETIREMENT.md:125 — “`TaskDAG.validate()`'s unknown-dep-as-cycle mis-report — **REPAIRED 2026-09-27** (§3)”; *Reason:* the Kahn in-degree now counts only edges whose source exists, so the false second message is gone and the verdict is unchanged — a landed change"),
    ("PHASE_DAG_RETIREMENT R2", "`dag_to_graph` has no production caller", "COMPLETE",
     "— (compat is test-only)", "tests/reliability/test_dag_retirement_contract.py",
     "PHASE_DAG_RETIREMENT.md:125 — “`dag_to_graph`'s lack of a production caller”; *Reason:* compat is test-only — a disposition, and the contract test pins it — **ADR-0065 R1**"),
    ("PHASE_LAYER_B_BOUNDARY R1", "`wisp/graph/api.py` has no importer — Layer B's typed SDK is reachable from nothing", "COMPLETE",
     "—", "wisp/__init__.py",
     "PHASE_LAYER_B_BOUNDARY.md:386 — “CORRECTED 2026-09-27” — measured, **six** import sites in four files, incl. `wisp/__init__.py:48`; it is the package's **public surface**. `GraphHandle` has no consumer — public-and-unconsumed, not dead. The premise was wrong"),
    ("PHASE_LAYER_B_BOUNDARY R2", "`test_the_orchestrator_still_imports_dag` is a bare string scan", "COMPLETE",
     "—", "tests/reliability/test_dag_retirement_contract.py",
     "PHASE_LAYER_B_BOUNDARY.md:392 — “was a bare string scan” — **REPAIRED 2026-09-27**; both tripwires in that class now parse the AST — a landed change"),
    ("PHASE_EXTERNAL_INPUT_PATH R1", "`vscode-extension/` is not a “shipped client” in ADR-0057's sense", "COMPLETE",
     "—", "tests/reliability/test_external_input_path.py",
     "PHASE_EXTERNAL_INPUT_PATH.md:222 — “ANSWERED 2026-09-27: it is.” — ADR-0061 names **three** clients that branch on the frame, the VS Code extension among them, correcting ADR-0057's *“both”*"),
    ("PHASE_OBJECTIVE_FLAG_COMPOSITION R1", "`CriteriaDerivation.strict` records `True` when the declaration path pre-empted it", "NOT STARTED",
     "making the boolean explicit is a record change and its own decision", "—",
     "PHASE_OBJECTIVE_FLAG_COMPOSITION.md:178 — “the boolean cannot distinguish *“strict acted”* from *“strict had nothing to act on”*”"),
    ("PHASE_OBJECTIVE_FLAG_COMPOSITION R2", "Whether objectives *must* declare", "NOT STARTED",
     "a policy with its own evidence; ADR-0056's named reversal condition", "—",
     "PHASE_OBJECTIVE_FLAG_COMPOSITION.md:174 — “That is a policy with its own evidence”"),
    ("PHASE_OUTCOME_CLASSIFICATION_VIOLATION R1", "The M4 count guard is still RED", "COMPLETE", "—",
     "tests/test_m4_governance_wiring.py",
     "PHASE_OUTCOME_CLASSIFICATION_VIOLATION.md:239 — “it is the one that *is* a contract update”; closed by corpus integrity II (2.1 **CLOSED**)"),
    ("PHASE_OUTCOME_CLASSIFICATION_VIOLATION R2", "`httpx` is still absent, so two guards remain un-runnable", "COMPLETE",
     "—", "tests/test_protected_path_guard.py",
     "PHASE_OUTCOME_CLASSIFICATION_VIOLATION.md:240 — “`httpx` is now installed — CLOSED 2026-09-27.”; *Reason:* both named guards run and pass, so their counts are quotable — a landed change"),
    ("PHASE_GATE_ENABLEMENT R1", "The gate was never exercised, so its interventions and surrenders are NOT MEASURED", "NOT STARTED",
     "the gate is not enabled (= M1)", "—",
     "PHASE_GATE_ENABLEMENT.md:204 — “Reporting those as zero would be manufacturing a metric”"),
    ("PHASE_GATE_ENABLEMENT R2", "The projection is proven over the guard's state space, not over all inputs", "COMPLETE",
     "—", "tests/reliability/test_acceptance_gate_enablement.py",
     "PHASE_GATE_ENABLEMENT.md:206 — “a reversal condition, not an open item (2026-09-27)” — the guard test re-derives the equivalence, so this is a tripwire the register already has a column for"),
]


def _head_sha() -> str:
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                          capture_output=True, text=True, check=True).stdout.strip()


def _today() -> str:
    """The generation date, read from the clock rather than written as a literal.

    **ADR-0065 residual 1.** The commit stamp on the same line is read live from git; a hardcoded
    date beside it is the one field on that line that can assert a generation date which is not the
    page's own — regenerating on any later day printed *"Generated 2026-09-25"* regardless. A
    derived page's whole value is that its stamps are true, so the date is measured too.
    """
    return datetime.date.today().isoformat()


def _check() -> list[str]:
    """Sources resolve; tripwires resolve; ids are unique; states are in the vocabulary."""
    broken: list[str] = []
    seen: set[str] = set()
    for iid, _t, state, _b, trip, src in ROWS:
        if iid in seen:
            broken.append(f"{iid}: duplicate id")
        seen.add(iid)
        if state not in VOCABULARY:
            broken.append(f"{iid}: state {state!r} is not in the ledger's vocabulary")
        head = src.split(" — ", 1)[0]
        path, _, num = head.rpartition(":")
        if not (REPO / path).exists():
            broken.append(f"{iid}: source file {path} does not exist")
        else:
            lines = (REPO / path).read_text(encoding="utf-8").splitlines()
            try:
                n = int(num)
            except ValueError:
                broken.append(f"{iid}: source line {num!r} is not a number")
            else:
                if not (1 <= n <= len(lines)):
                    broken.append(f"{iid}: {path}:{n} out of range ({len(lines)} lines)")
        if "|" in src or "|" in _t or "|" in _b:
            broken.append(f"{iid}: a cell contains `|`, which splits the markdown table")
        if trip != "—" and not (REPO / trip.split("::", 1)[0]).exists():
            broken.append(f"{iid}: tripwire {trip} does not exist")
    return broken + _source_quote_problems()


#: A check that found nothing to check has not run (F81). 101 of the 102 sources quote their
#: line today; the floor sits well below that so a legitimate row without a quote cannot trip it.
QUOTE_FLOOR = 60


def _source_quote_problems() -> list[str]:
    """The quoted words must be on the cited line, ±3 (`scripts/register_pins.py`).

    Range was all `_check` verified until the register-source-pins mission, so a pin could drift
    onto unrelated text and pass — chiefly every `CONTEXT.md` §12 row, after sections were
    inserted above it (`PHASE_REGISTER_SOURCE_PINS.md` §2).
    """
    sys.path.insert(0, str(REPO / "scripts"))
    from register_pins import source_quote_problems

    problems, checkable = source_quote_problems([(r[0], r[5]) for r in ROWS], REPO)
    if checkable < QUOTE_FLOOR:
        problems.append(f"only {checkable} sources carry a checkable quote (floor "
                        f"{QUOTE_FLOOR}) — the quote grammar drifted and the check went vacuous")
    return problems


def _counts() -> dict[str, int]:
    out = {w: 0 for w in VOCABULARY}
    for _i, _t, state, _b, _tr, _s in ROWS:
        out[state] += 1
    return out


def render() -> str:
    counts = _counts()
    total = len(ROWS)
    open_rows = [r for r in ROWS if r[2] not in CLOSED_STATES]
    closed_rows = [r for r in ROWS if r[2] in CLOSED_STATES]
    L: list[str] = []
    A = L.append

    A("# CURRENT_OPEN_ITEMS.md — the current state of every item the corpus has left open")
    A("")
    A("> **DERIVED DOCUMENT — REGENERATE, DO NOT EDIT IN PLACE.**")
    A("> Regenerate with `env -u PYTHONPATH .venv/bin/python scripts/derive_current_open_items.py`.")
    A("> This page states each item's **current** state, not its history: it cites where the state")
    A("> is recorded, and it **introduces no decision**. Where two sources disagree, both are cited")
    A("> and §Findings records the disagreement — never a resolution.")
    A(">")
    A("> **The state vocabulary is the ledger's** (`WISP_MIGRATION_STATUS.md:41`), stated in §(a),")
    A("> with every other source's word mapped onto it. A reason is not a state — see §(b).")
    A(">")
    A("> **Sibling registers:** `CURRENT_AUTHORITIES.md` (what each authority's current state is),")
    A("> `CURRENT_FINDINGS.md` (every recorded finding and its status), `CURRENT_FLAGS.md` (every")
    A("> rollback flag and its default). All four are derived; none may decide.")
    A(">")
    A(f"> Generated {_today()} at `{_head_sha()}` · **{total} items** · "
      f"**{len(open_rows)} open**, {len(closed_rows)} closed (kept, in §The closed items).")
    A("")
    A("---")
    A("")
    A("## The register — items not yet closed")
    A("")
    A("| id | title | state | blocked_by | tripwire | source |")
    A("|---|---|---|---|---|---|")
    for iid, title, state, blocked, trip, src in open_rows:
        A(f"| **{iid}** | {title} | `{state}` | {blocked} | {trip} | {src} |")
    A("")
    A("---")
    A("")
    A("## The closed items — kept, with their closure's source")
    A("")
    A("A page that deleted its closed items could not be checked for a regression: an item that")
    A("reopens would simply vanish from the open table with nothing to compare against.")
    A("")
    A("| id | title | state | blocked_by | tripwire | source |")
    A("|---|---|---|---|---|---|")
    for iid, title, state, blocked, trip, src in closed_rows:
        A(f"| **{iid}** | {title} | `{state}` | {blocked} | {trip} | {src} |")
    A("")
    A("---")
    A("")
    A("## (a) The state vocabulary — the ledger's, stated once")
    A("")
    A("`WISP_MIGRATION_STATUS.md:41` states the vocabulary:")
    A("")
    A("> `NOT STARTED` · `IN PROGRESS` · `COMPLETE` · `BLOCKED` · `PARTIAL` · `SUPERSEDED`")
    A("")
    A("| word | what it claims |")
    A("|---|---|")
    A("| `NOT STARTED` | Nothing has been done. The item stands as written. |")
    A("| `IN PROGRESS` | Work is under way and the item is not finished. The `blocked_by` cell")
    A("  carries whatever the source names as the remaining obstacle. |")
    A("| `PARTIAL` | Some named part landed and another did not. The source says which is which. |")
    A("| `BLOCKED` | The item cannot proceed. **No item is currently in this state** (ADR-0062 R3.2):")
    A("  every source that could has preferred `IN PROGRESS` or `NOT STARTED` plus a stated `blocked_by`. |")
    A("| `COMPLETE` | The item is finished. For an item closed by a *decision* rather than a change,")
    A("  the decision is cited in the source cell. |")
    A("| `SUPERSEDED` | A later decision replaced the item's question. |")
    A("")
    A("**Other sources' words, mapped.** Four sources name states in their own vocabulary:")
    A("")
    A("| the source writes | this page | where |")
    A("|---|---|---|")
    A("| `OPEN` | `NOT STARTED` | `CONTEXT.md` §12 |")
    A("| `✅ DONE` · `FIXED` · `CLOSED` | `COMPLETE` | `CONTEXT.md` §12 |")
    A("| `DECIDED` | — (a *reason*; ADR-0062 R3) | `CONTEXT.md` §12 **until ADR-0062**, now written *Reason:* after `COMPLETE` |")
    A("| `Accepted (low)` · `Accepted` · `Unresolved, no action` | `COMPLETE` | `CONTEXT.md` §12 **until ADR-0065**, which ruled a recorded disposition is a closure and is written *Reason:* after `COMPLETE` — except where the disposition names work still owed, which is `PARTIAL` (ADR-0065 R3) |")
    A("| `DEFERRED` · `NOT DONE` | `NOT STARTED` | `WISP_MIGRATION_STATUS.md` per-item tables |")
    A("| `BLOCKED_ON_PRECONDITION` | — | **not used anywhere in this corpus** (§(b)) |")
    A("")
    A("**§12 is not rewritten by this mapping.** A disposition word stays in `CONTEXT.md` §12 as the")
    A("record of what was decided; this page records the *state* that follows from it. So §12's `M10`")
    A("row still reads *“OPEN — by design.”* while this page carries `M10` as `COMPLETE`, and §12's")
    A("`F4` row still reads *“deletion candidate”* while the instrument pins it as deliberately dead.")
    A("That is the same split ADR-0062 R2 chose over renaming the id namespaces — the historical")
    A("record is not amended. The disposition is quoted in each row's source cell, so the two can be")
    A("**compared** rather than assumed to agree; a divergence here is not drift, it is the mapping.")
    A("")
    A("---")
    A("")
    A("## (b) The reasons are not states")
    A("")
    A("**The rule, stated once.** A *reason* explains why an item is in a state; it is not itself a")
    A("state. The ledger has one state vocabulary and one column for reasons — `blocked_by` — and")
    A("coining a state word for a reason leaves two vocabularies in the ledger, which is how `OPEN`")
    A("and `NOT STARTED` came to coexist for the same state.")
    A("")
    A("**Every place a reason was recorded as a state.** One, measured — and **repaired by ADR-0062 R3**:")
    A("")
    A("- **`DECIDED`** — `CONTEXT.md` §12's table used it as a state word for **`M8`**, **`M11`** and")
    A("  **`Layer C`**. It names *why* the item is finished (an ADR decided it) rather than *that* it")
    A("  is. ADR-0062 R3 decided it is a reason: each of the three now reads `COMPLETE`, followed by")
    A("  *Reason:* and the deciding ADR — quoted in the source cells above.")
    A("- **`BLOCKED`** is a state the ledger defines and **no item is currently in** (ADR-0062 R3.2).")
    A("  Every item that is blocked is recorded as `IN PROGRESS` or `NOT STARTED` with a stated")
    A("  obstacle. A defined word with no members is a vocabulary, not a defect, and the word is kept so")
    A("  a future blocked item is representable.")
    A("")
    A("**The brief's own vocabulary, checked.** The mission brief lists `OPEN · IN_PROGRESS ·")
    A("BLOCKED · DECIDED · CLOSED · SUPERSEDED` as *\"the ledger's own vocabulary\"*. It is not: the")
    A("ledger's is `NOT STARTED · IN PROGRESS · COMPLETE · BLOCKED · PARTIAL · SUPERSEDED`. Three of")
    A("the brief's six (`OPEN`, `DECIDED`, `CLOSED`) are absent from it, and two of the ledger's")
    A("(`NOT STARTED`, `PARTIAL`) are absent from the brief's. Recorded in §Findings.")
    A("")
    A("---")
    A("")
    A("## (c) The open count, by state")
    A("")
    A(f"Measured {_today()} at `{_head_sha()}` over the {total} rows below. A count is canonical")
    A("only if it is measured after the LAST change to any member (**F85**), which is why the")
    A("generator recomputes it rather than the page stating it.")
    A("")
    A("| state | count |")
    A("|---|---|")
    for word in VOCABULARY:
        A(f"| `{word}` | {counts[word]} |")
    A(f"| **total** | **{total}** |")
    A("")
    A(f"**Open** — everything not in §The closed items — **{len(open_rows)}** of {total}.")
    A("")
    A("**How many are the host rather than the architecture.** Seven of the open rows name a")
    A("declared-and-absent dependency, a resource limit, or the user's own uncommitted work: `R7`,")
    A("`R8`, `M1` (its population), `ADR-0053 R1`, `ADR-0054 R1`, `ADR-0059 R1`, `ADR-0061 R1`. The")
    A("rest are architectural. **This list shrinks as rows close** — `R9` and `ADR-0058 R2` went with")
    A("ADR-0065, then `R6` and `ADR-0061 R4` once the five absent dependencies were installed.")
    A("")
    A("---")
    A("")
    A("## §Findings — disagreements between sources, and what could not be pinned")
    A("")
    A("This page may **record** a disagreement; it may not resolve one.")
    A("")
    A("### `CONTEXT.md` §12 against §0.0 — the cross-check the brief asks for")
    A("")
    A("**No disagreement found, and that is a measured result.** §12's rows for `E`, `G1`, `R1b` and")
    A("`W1` all read `CLOSED`, and §0.0's narrative for each records the same closure with the same")
    A("ADR. The one historical disagreement the corpus records is **F98** — §12's `G1` row said")
    A("`OPEN` until 2026-09-25 while §0.0.14 recorded it closed — and it was repaired at")
    A("`PHASE_REST_AUTHORIZATION_COMPOSITION.md`. This page was derived **after** that repair, so it")
    A("cannot see the pre-repair state; it records that the check was run and found nothing, rather")
    A("than claiming the sources cannot disagree.")
    A("")
    A("### The same id in two namespaces")
    A("")
    A("- **`F1`–`F5`.** `CONTEXT.md` §12 uses `F1`–`F5` as **open-item** ids (Phase 10's defect")
    A("  ledger). `WISP_MIGRATION_STATUS.md` §23 and `CURRENT_FINDINGS.md` use `F1`–`F104` as")
    A("  **finding** ids. `§12`'s `F1` (`metadata[\"_budget\"]` write-only) and the findings log's")
    A("  `F1` (`test_canonical_execution_state.py` already exists) are different things with the same")
    A("  name. **Decided by ADR-0062 R2: tolerated, not renamed** — a citation writes `ITEM-F1` for")
    A("  §12's rows and `FIND-F1` for a finding, and §12 states the rule once.")
    A("- **`M4`** — and, measured, **`M1`–`M7`**. `CONTEXT.md` §12's `M4` is *ADR-0004 revisited* (a")
    A("  migration item, also `WISP_MIGRATION_STATUS.md:204`). `PHASE_10_M4_GOVERNANCE_UNWIRED.md` and")
    A("  `AGENTS.md`'s module map use *M4* for the enterprise track's **governance layer**, which §12")
    A("  carries as row **`E`** — and the enterprise track's `M1`–`M7` collide with the migration's")
    A("  `M1`–`M7` the same way. This page used to name `WISP_MIGRATION_STATUS.md:2041` as a third")
    A("  `M4`; **that line has never named `M4`** (corpus governance II §5). **Annotated once at §12,**")
    A("  per ADR-0062 R2. **Finding F99** records the same collision from the other side.")
    A("")
    A("### Claims that cannot be pinned")
    A("")
    A("- **The brief's state vocabulary is not the ledger's.** Recorded in §(b). The register uses")
    A("  the ledger's, because the brief itself says *\"the ledger's vocabulary governs\"*.")
    A("- **`BLOCKED` has no members** — no item is currently in this state (ADR-0062 R3.2). Kept,")
    A("  not removed, and no `BLOCKED` row is invented, because that would be coining a state.")
    A("- **No ADR before 0053 has a named-residual section.** The ADR log's residual lists begin at")
    A("  ADR-0053; ADR-0001–0052's residuals, where they exist, are in their phase reports instead.")
    A("  So *\"every ADR's named residuals\"* resolves to **six** ADRs, not 61 — a scope the brief's")
    A("  wording does not state. Measured: `### Residuals` headings in")
    A("  `WISP_ARCHITECTURE_DECISIONS.md` occur at ADR-0053, 0054, 0057, 0058, 0059 and 0061.")
    A("")
    A("### Quotations that were never verbatim")
    A("")
    A("- **`ITEM-F3`.** Its source cell quoted *\"Accepted (low) — annotate so nobody wires them without")
    A("  the missing checks\"*. The `CONTEXT.md` §12 row has read *\"Accepted (low) — the executor")
    A("  authorises per call; annotate so nobody wires …\"* since it was first written (`98bb8f9`): the")
    A("  register dropped a clause **with no elision mark**. The source was **not** amended, so this is a")
    A("  transcription defect in this page, not a drifted status. **The elision is now marked (`…`); no")
    A("  word was added or changed.** Found by the source-quote check (`PHASE_REGISTER_SOURCE_PINS.md` §4).")
    A("")
    A("### What this page did not do")
    A("")
    A("- **No new decision, and no new item id.** Every id is the source's own name.")
    A("- **No source was edited by this page.** §12's `DECIDED` word and the id collisions were")
    A("  repaired at their source, by edits ADR-0062 R2/R3 authorise; this page records the result.")
    A("- **No closed item was deleted.** They are kept in §The closed items so a regression is")
    A("  visible as a move between the two tables.")
    A("")
    return "\n".join(L)


def main() -> int:
    problems = _check()
    if problems:
        print("DERIVATION REFUSED — the data table is not sound:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 2
    OUT.write_text(render(), encoding="utf-8")
    counts = _counts()
    print(f"wrote {OUT.name}: {len(ROWS)} rows; "
          + ", ".join(f"{k}={v}" for k, v in counts.items() if v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
