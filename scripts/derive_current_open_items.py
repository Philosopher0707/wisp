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
     "CONTEXT.md:2265 — “✅ **DONE** (§0)”"),
    ("R2", "Correct the “breaks the client” claim", "COMPLETE", "—", "—",
     "CONTEXT.md:2266 — “✅ **DONE** (§5)”"),
    ("G0", "REST bypass of the protected-path guard", "COMPLETE", "—", "—",
     "CONTEXT.md:2267 — “✅ **DONE** (§0b)”"),
    ("E", "The M4 governance layer is not wired to the runtime", "COMPLETE", "—",
     "tests/test_m4_governance_wiring.py",
     "CONTEXT.md:2268 — “✅ **CLOSED 2026-09-25.** The key-trust decision is **ADR-0058** … and the wiring landed”"),
    ("G1", "Authorization parity — the agent composes both models; REST consulted only `SecurityPolicy`", "COMPLETE", "—",
     "tests/test_authorization_parity.py",
     "CONTEXT.md:2269 — “✅ **CLOSED.** **ADR-0055** drove the real paths: **0 path divergences of 36**”"),
    ("R1b", "`POST /api/hooks` still accepts an unvalidated `command`", "COMPLETE", "—",
     "tests/reliability/test_external_input_path.py",
     "CONTEXT.md:2270 — “✅ **CLOSED 2026-09-25 (ADR-0061 R6 / G3).**”"),
    ("W1", "The agent path's WebSocket approval prompt had never rendered", "COMPLETE", "—",
     "tests/reliability/test_external_input_path.py",
     "CONTEXT.md:2271 — “✅ **CLOSED 2026-09-25 (ADR-0061).**”"),
    ("R10", "`useApi.ts:368` sends no `Authorization` header", "PARTIAL", "—", "—",
     "CONTEXT.md:2272 — “✅ **FIXED** (§0f) — the functional half. **What remains is a decision**”"),
    ("F1", "`metadata[\"_budget\"]` write-only", "COMPLETE", "—", "—",
     "CONTEXT.md:2273 — “✅ **FIXED** (§0e.2)”"),
    ("F2", "`_SENSITIVE_ENV_KEYS` has no consumer", "COMPLETE", "—", "—",
     "CONTEXT.md:2274 — “✅ **FIXED** (§0e.1) — deleted as superseded”"),
    ("F3", "`execute_tool(security_policy=…)` — no caller passes it", "NOT STARTED", "—", "—",
     "CONTEXT.md:2275 — “**Accepted (low)** — … annotate so nobody wires them without the missing checks”"),
    ("F4", "`spawn_with_guards` is a dead duplicate", "NOT STARTED", "—", "—",
     "CONTEXT.md:2276 — “**Accepted** — deletion candidate”"),
    ("F5", "event-replay `TOOL_CALL` — the referent is unidentifiable", "NOT STARTED", "—", "—",
     "CONTEXT.md:2277 — “**Unresolved, no action** — recorded as unidentified rather than guessed at”"),
    ("G2", "The `run_bash` verb scan is a separate mechanism from the predicate", "NOT STARTED", "—", "—",
     "CONTEXT.md:2278 — “**Accepted** — a shell command's target is not determinable from its text”"),
    ("R3", "Full provider-listing delegation", "NOT STARTED",
     "the 3 deltas (auth/timeout/degradation) must converge",
     "tests/test_provider_listing_equivalence.py",
     "CONTEXT.md:2279 — “Unsafe until the 3 deltas … converge; `test_provider_listing_equivalence.py` fails at that point”"),
    ("R4", "`_is_transient` is a separate predicate", "COMPLETE", "—", "—",
     "CONTEXT.md:2280 — “**Not debt** — different axis (retryability, not outcome class)”"),
    ("R5", "Two `RunStatus` enums remain", "COMPLETE", "—", "—",
     "CONTEXT.md:2281 — “**Resolved as a non-issue by the migration.** … **No shim needed** — ADR-0003”"),
    ("R6", "`.venv` missing deps", "PARTIAL", "`httpx` and `cryptography` are absent from the venv **and** the uv cache", "—",
     "CONTEXT.md:2282 — “`jsonschema` is **fixed** (F8); `httpx`, `cryptography`, `numpy`, `tiktoken`, `aiohttp` remain absent”"),
    ("R7", "`capability_filter.py` untracked but imported", "NOT STARTED", "the file is the user's untracked WIP (§8)", "—",
     "CONTEXT.md:2283 — “See §8”"),
    ("R8", "3 untracked test files abort collection", "NOT STARTED", "the files are the user's WIP", "—",
     "CONTEXT.md:2284 — “User's WIP”"),
    ("R9", "`wisp/core/graph/__init__.py` modified, uncommitted", "NOT STARTED", "the edit is the user's pre-existing WIP", "—",
     "CONTEXT.md:2285 — “User's pre-existing edit”"),

    # ── CONTEXT.md §12 — "Migration open items — current" ────────────────────
    ("M1", "P3 stage 3b — enable the acceptance gate", "IN PROGRESS",
     "ADR-0051 R4 requires **≥ 2 capable models** and this host serves exactly **1** of 13 — an environment fact, not a code change",
     "tests/reliability/test_acceptance_gate_enablement.py",
     "CONTEXT.md:2301 — “**`IN_PROGRESS`** — the precondition is satisfied **and** the mechanism is built and driven; what remains is the population”"),
    ("M2", "Journal-first reconstruction", "COMPLETE", "—", "tests/test_session_reconstruction.py",
     "CONTEXT.md:2302 — “✅ **COMPLETE** … **Five consumers still read the blob** (a tripwire asserts it)”"),
    ("M3", "Killpoint integration", "COMPLETE", "—", "tests/reliability/test_killpoints.py",
     "CONTEXT.md:2303 — “✅ **COMPLETE** — `test_kp_session_midtool_then_killed`. One window covered.”"),
    ("M4", "ADR-0004 revisited", "COMPLETE", "—", "—",
     "CONTEXT.md:2304 — “✅ **COMPLETE** — **ADR-0027**. Found a live defect”"),
    ("M5", "Foreground-turn `RunRecord` lifecycle", "NOT STARTED", "—", "—",
     "CONTEXT.md:2305 — “**OPEN** — proven end-to-end for background runs only.”"),
    ("M6", "`PolicyDecisionEnvelope` producer-less and consumer-less", "NOT STARTED", "—", "—",
     "CONTEXT.md:2306 — “**OPEN** — the last unwired contract.”"),
    ("M7", "`change_tracker.py` not wired into evidence", "NOT STARTED", "deferred with stage 3b", "—",
     "CONTEXT.md:2307 — “**OPEN** — deferred with 3b.”"),
    ("M8", "`multi_agent/dag.py` not retired into `wisp/graph/`", "COMPLETE",
     "— (re-scoped by ADR-0060: the divergence is the *boundary*, not a blocker; the removal is **not owed**)",
     "tests/reliability/test_dag_retirement_contract.py",
     "CONTEXT.md:2308 — “✅ **COMPLETE.** *Reason:* surveyed and decided 2026-09-25 — **`DEPRECATE`, not remove** — and **re-scoped by ADR-0060**”"),
    ("M9", "The execution view", "COMPLETE", "—", "—",
     "CONTEXT.md:2294 — “✅ **COMPLETE** — ADR-0029.”"),
    ("M10", "The materialized graph is a lower bound on iterations", "NOT STARTED",
     "— (by design: iteration boundaries are not observable)", "—",
     "CONTEXT.md:2309 — “**OPEN — by design.**”"),
    ("M11", "The graph does not drive execution", "COMPLETE", "—",
     "tests/reliability/test_layer_b_boundary.py",
     "CONTEXT.md:2295 — “✅ **COMPLETE.** *Reason:* **decided, not deferred** (ADR-0060; ADR-0062 R3). ADR-0033 … **ADR-0060 closes the second half**”"),
    ("M12", "The failure path", "COMPLETE", "—", "—",
     "CONTEXT.md:2296 — “✅ **COMPLETE** — ADR-0032.”"),
    ("M13", "The stagnation detector is not constructed by the turn loop", "COMPLETE", "—",
     "tests/test_stagnation_live_wiring.py",
     "CONTEXT.md:2297 — “✅ **COMPLETE** — ADR-0034. … **Enforcement deferred**: routing and goal-met gating are tripwired.”"),
    ("M14", "The context trust boundary", "COMPLETE", "—", "tests/test_prompt_section_trust.py",
     "CONTEXT.md:2298 — “✅ **COMPLETE** — ADR-0031. … T2 fencing remains, deliberately staged.”"),
    ("M15", "The subagent spawn site", "COMPLETE", "—", "tests/test_child_principal_wired.py",
     "CONTEXT.md:2299 — “✅ **COMPLETE** — ADR-0030.”"),
    ("M16", "The `ESCALATION` record's loss is not fully addressed", "COMPLETE", "—", "—",
     "CONTEXT.md:2300 — “✅ **COMPLETE** — ADR-0028.”"),
    ("Layer C", "`wisp/core/graph/` — named *disowned* and consumed by the live path", "COMPLETE", "—",
     "tests/reliability/test_layer_c_disposition.py",
     "CONTEXT.md:2310 — “✅ **COMPLETE.** *Reason:* decided 2026-09-25 by **ADR-0060 R5** (ADR-0062 R3). The live symbols **moved**”"),

    # ── WISP_MIGRATION_STATUS.md — the phase ledger's non-COMPLETE rows ──────
    ("P0 · item 6", "A normal turn creates a `RunRecord` row", "COMPLETE", "—", "—",
     "WISP_MIGRATION_STATUS.md:293 — “`DEFERRED to P1`”; P1 is `COMPLETE` (`:193`)"),
    ("P1 · item 3", "Journal replaces the snapshot as the primary record", "COMPLETE", "—", "—",
     "WISP_MIGRATION_STATUS.md:380 — “`DEFERRED to P2`”; P2 is `COMPLETE` (`:194`)"),
    ("P3 · item 5", "Structural independence (L1/L2)", "PARTIAL", "—", "—",
     "WISP_MIGRATION_STATUS.md:511 — “`PARTIAL` … a second model (L3) is not implemented — the plan makes it preferred, not required”"),
    ("P3 · item 6", "The completion rule requires non-invalidated evidence", "NOT STARTED",
     "it is stage 3b, which is M1", "—",
     "WISP_MIGRATION_STATUS.md:512 — “`NOT DONE` — **that is stage 3b** — the plan's staging; `turn_succeeded` is unchanged”"),
    ("P3 · item 7", "Wire `change_tracker.py` into evidence", "NOT STARTED", "deferred with 3b (M7)", "—",
     "WISP_MIGRATION_STATUS.md:513 — “`NOT DONE` — deferred with 3b”"),
    ("P4 · item 1", "Reuse `wisp/graph/`'s store", "PARTIAL", "a second DB would fragment the durable record (ADR-0019)", "—",
     "WISP_MIGRATION_STATUS.md:551 — “`PARTIAL` … **the STORE is not**”"),
    ("P5 · item 5", "Extend the executor to accept a mid-run node", "COMPLETE",
     "— (ADR-0060 measured it inexpressible and rejected Position B)", "—",
     "WISP_MIGRATION_STATUS.md:635 — “`NOT DONE` — **deferred** — §7.5”; §7.5's target was **rejected** by ADR-0060"),
    ("P7 · item 1", "Wire `OscillationTrap` to the live loop", "COMPLETE", "—", "tests/test_stagnation_live_wiring.py",
     "WISP_MIGRATION_STATUS.md:747 — “`PARTIAL` … the live turn loop does not construct a detector (M13)”; M13 is `COMPLETE`"),
    ("P8 · item 3", "Graph context section scoped to the current node", "NOT STARTED",
     "no current node exists — nothing drives execution (M11, now decided as the permanent boundary)", "—",
     "WISP_MIGRATION_STATUS.md:862 — “`NOT DONE`”"),
    ("P8 · item 4", "Populate plan context (`PlanState`, `## PLAN MODE ACTIVE`)", "NOT STARTED",
     "a **product decision**, not a mechanical change", "—",
     "WISP_MIGRATION_STATUS.md:863 — “`NOT DONE` — the plan says *“populate or remove”*”"),
    ("P8 · item 5", "Serve the symbol-level repo map", "NOT STARTED",
     "`tiktoken` is absent, so the 1200-token budget cannot be measured faithfully", "—",
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
    ("ADR-0053 R3", "`command_succeeds` on the turn path runs the declared command after `done`, once per declared turn", "NOT STARTED",
     "— (a stated cost; it is why the flag defaults OFF)", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:5174 — “That cost is real and is the reason the flag defaults OFF.”"),
    ("ADR-0054 R1", "The population is short by one capable model", "NOT STARTED",
     "an *environment* fact, not a design one", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:5321 — “This is the only thing between the contract and the enablement decision”"),
    ("ADR-0054 R2", "The declared command runs once per declared turn (at the gate; reused at the verdict site)", "NOT STARTED",
     "— (a stated cost)", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:5323 — “that is a real cost, and it is why the flag defaults OFF”"),
    ("ADR-0054 R3", "The gate shares the stagnation gate's budget", "NOT STARTED",
     "— (the intended reading of a turn-level bound, stated rather than discovered)", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:5325 — “That is the intended reading of a turn-level bound”"),
    ("ADR-0054 R4", "`stagnation_gate` is untouched", "NOT STARTED",
     "ADR-0037 forbids enabling it without a superseding ADR", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:5327 — “The two gates are now both wired and both default OFF, independently.”"),
    ("ADR-0057 R1", "The agent path's WebSocket approval prompt has never rendered", "COMPLETE", "—",
     "tests/reliability/test_external_input_path.py",
     "WISP_ARCHITECTURE_DECISIONS.md:5826 — “`approve()` sends a frame no client reads”; closed by **ADR-0061**"),
    ("ADR-0057 R2", "`resolve_approval` still ignores the client's `id` when the transport resolves it", "NOT STARTED",
     "— (it is the agent path's resolver)", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:5830 — “the transport's path is weaker. Not changed here”"),
    ("ADR-0057 R3", "G3 remains open (R9)", "COMPLETE", "—", "tests/reliability/test_external_input_path.py",
     "WISP_ARCHITECTURE_DECISIONS.md:5834 — “**G3 remains open** (R9)”; closed by **ADR-0061** (`CONTEXT.md` §12 row R1b)"),
    ("ADR-0057 R4", "A multi-client deployment asks every registered channel and takes the first response", "NOT STARTED",
     "per-client routing is its own ADR", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:5835 — “a per-client routing decision is its own ADR”"),
    ("ADR-0058 R1", "The multi-device ceremony — a control plane, a key-distribution server and a registration UI", "NOT STARTED",
     "deferred by the M4 spec (§5), read as such", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:5998 — “Deferred by the spec, read as such here.”"),
    ("ADR-0058 R2", "`WISP_POLICY_CACHE` and `load_managed` are not engaged", "NOT STARTED",
     "— (this decision wires the `local-only` path only)", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:6000 — “The managed/disconnected modes have a network story and a cache”"),
    ("ADR-0058 R3", "REST does not receive L0", "COMPLETE", "—",
     "tests/reliability/test_rest_authorization_composition.py",
     "WISP_ARCHITECTURE_DECISIONS.md:6002 — “`SecurityPolicy.check()` has **no organization layer**”; closed by **ADR-0059**"),
    ("ADR-0058 R4", "Private-key custody is not implemented by Wisp", "COMPLETE", "— (by design, R7)", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:6007 — “(R7), by design”"),
    ("ADR-0059 R1", "A bundle's `approve` level is inert on REST", "PARTIAL",
     "the bundle half is un-measurable here — `cryptography` is absent (F88)", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:6163 — “REST cannot, having no approver”; **closed in effect** by ADR-0061 (6/6 pinned pairs), **un-composed in mechanism**"),
    ("ADR-0059 R2", "REST's consult is conditional on a bundle, so L1/L2/L3 are not consulted without one", "NOT STARTED",
     "— (workspace quarantine is the real pre-existing gap it exposes)", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:6167 — “**workspace quarantine** is a real pre-existing gap”"),
    ("ADR-0059 R3", "REST still reimplements L4 (the protected-path predicate)", "NOT STARTED",
     "removing it is a separate change; its message is pinned", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:6173 — “Pre-existing; kept because its message is pinned”"),
    ("ADR-0059 R4", "Two bundle sources — the env-var path and the publish route's held bundle", "NOT STARTED",
     "merging them is a distribution decision", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:6176 — “still has no decision reader. Named, not merged”"),
    ("ADR-0059 R5", "The three REST-only names have an enforced rule and still have no agent operation", "COMPLETE", "—", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:6179 — “That is the honest end state”"),
    ("ADR-0061 R1", "The round-trip is pinned against a stub channel, not a live client", "NOT STARTED",
     "no real desktop/TUI/VS Code client runs on this host", "tests/test_ws_control_plane.py",
     "WISP_ARCHITECTURE_DECISIONS.md:6588 — “the *frame shape* and the *no-client behaviour* are driven and the end-to-end render is not”"),
    ("ADR-0061 R2", "`receive_message`'s own `tool_approval` branch still ignores `msg[\"id\"]`", "NOT STARTED",
     "— (the route intercepts first; it is the old-protocol fallback)", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:6593 — “ADR-0057 residual 2, unchanged here”"),
    ("ADR-0061 R3", "A multi-client deployment still asks every registered channel", "NOT STARTED",
     "— (ADR-0057 residual 4, unchanged)", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:6596 — “unchanged”"),
    ("ADR-0061 R4", "The bundle half of ADR-0059 residual 1 is un-measurable here", "NOT STARTED",
     "`cryptography` is absent (F88)", "—",
     "WISP_ARCHITECTURE_DECISIONS.md:6598 — “un-measurable here”"),

    # ── The phase reports' residual sections ────────────────────────────────
    ("PHASE_AUTHORIZATION_PARITY R1", "The approval authority is split three ways", "NOT STARTED",
     "unifying it is its own ADR", "—",
     "PHASE_AUTHORIZATION_PARITY.md:120 — “Unifying it is its own ADR; this one touches none of the three.”"),
    ("PHASE_AUTHORIZATION_PARITY R2", "Three action names are in none of the three approval sets", "NOT STARTED",
     "adding rows is inert today and is not this ADR's change", "—",
     "PHASE_AUTHORIZATION_PARITY.md:122 — “no approval model governs them on either path”"),
    ("PHASE_AUTHORIZATION_PARITY R3", "REST cannot ask a human — a REST caller gets the no-approver fall-through", "COMPLETE", "—",
     "tests/reliability/test_rest_approval.py",
     "PHASE_AUTHORIZATION_PARITY.md:131 — “**Not repaired here** — … Option C is the fix”; landed as **ADR-0057** (`PHASE_REST_APPROVAL.md`)"),
    ("PHASE_AUTHORIZATION_PARITY R4", "Five further gated routes are not in the parity table", "NOT STARTED",
     "unmeasured here; `mcp.test_server` executes a server command", "—",
     "PHASE_AUTHORIZATION_PARITY.md:133 — “They are unmeasured here and are named so the next reader does not assume the table is exhaustive.”"),
    ("PHASE_M4_WIRING R1", "REST does not receive L0", "COMPLETE", "—",
     "tests/reliability/test_rest_authorization_composition.py",
     "PHASE_M4_WIRING.md:186 — “its own decision, pinned so it cannot drift silently”; closed by **ADR-0059**"),
    ("PHASE_M4_WIRING R2", "`acp_session.py:208` — an ACP-only deployment would need the same load", "NOT STARTED",
     "— (named, not done)", "—",
     "PHASE_M4_WIRING.md:187 — “named, not done”"),
    ("PHASE_M4_WIRING R3", "The M4 policy suite cannot run here", "NOT STARTED",
     "`cryptography` is absent (F88)", "—",
     "PHASE_M4_WIRING.md:188 — “(F88)”"),
    ("PHASE_M4_WIRING R4", "F89 — the falsy-`expires_at` message", "COMPLETE", "—",
     "tests/reliability/test_key_trust_workflow.py",
     "PHASE_M4_WIRING.md:189 — “the falsy-`expires_at` message”; closed by corpus integrity III (`CONTEXT.md` §0: 2.1 **CLOSED**)"),
    ("PHASE_M4_WIRING R5", "`WISP_POLICY_CACHE` / `load_managed` are not engaged", "NOT STARTED",
     "ADR-0058 R6", "—",
     "PHASE_M4_WIRING.md:190 — “the managed/disconnected modes are not engaged (ADR-0058 R6)”"),
    ("PHASE_KEY_TRUST_WORKFLOW R6", "The happy path of the key-trust workflow is not exercised by any test here", "NOT STARTED",
     "a real signature round-trip needs `cryptography` (F88)", "—",
     "PHASE_KEY_TRUST_WORKFLOW.md:235 — “pinned by signature and by pure function, not end to end”"),
    ("PHASE_DAG_RETIREMENT R1", "`TaskDAG.validate()` mis-reports an unknown dependency as a cycle", "NOT STARTED",
     "— (a defect in the deprecated entry point, pinned)", "tests/reliability/test_dag_retirement_contract.py",
     "PHASE_DAG_RETIREMENT.md:124 — “`TaskDAG.validate()`'s unknown-dep-as-cycle mis-report”"),
    ("PHASE_DAG_RETIREMENT R2", "`dag_to_graph` has no production caller", "NOT STARTED",
     "— (compat is test-only)", "tests/reliability/test_dag_retirement_contract.py",
     "PHASE_DAG_RETIREMENT.md:125 — “`dag_to_graph`'s lack of a production caller”"),
    ("PHASE_LAYER_B_BOUNDARY R1", "`wisp/graph/api.py` has no importer — Layer B's typed SDK is reachable from nothing", "NOT STARTED",
     "whether it is a public API or dead code is not this decision's question", "—",
     "PHASE_LAYER_B_BOUNDARY.md:387 — “**Named, not repaired**: it is Layer B's surface”"),
    ("PHASE_LAYER_B_BOUNDARY R2", "`test_the_orchestrator_still_imports_dag` is a bare string scan", "NOT STARTED",
     "— (the instrument-defect class; named, not repaired)", "—",
     "PHASE_LAYER_B_BOUNDARY.md:390 — “It asserts *presence* of an …”"),
    ("PHASE_EXTERNAL_INPUT_PATH R1", "`vscode-extension/` is not a “shipped client” in ADR-0057's sense", "NOT STARTED",
     "whether it ships is not this ADR's question", "—",
     "PHASE_EXTERNAL_INPUT_PATH.md:224 — “this report does not claim it is”"),
    ("PHASE_OBJECTIVE_FLAG_COMPOSITION R1", "`CriteriaDerivation.strict` records `True` when the declaration path pre-empted it", "NOT STARTED",
     "making the boolean explicit is a record change and its own decision", "—",
     "PHASE_OBJECTIVE_FLAG_COMPOSITION.md:178 — “the boolean cannot distinguish *“strict acted”* from *“strict had nothing to act on”*”"),
    ("PHASE_OBJECTIVE_FLAG_COMPOSITION R2", "Whether objectives *must* declare", "NOT STARTED",
     "a policy with its own evidence; ADR-0056's named reversal condition", "—",
     "PHASE_OBJECTIVE_FLAG_COMPOSITION.md:174 — “That is a policy with its own evidence”"),
    ("PHASE_OUTCOME_CLASSIFICATION_VIOLATION R1", "The M4 count guard is still RED", "COMPLETE", "—",
     "tests/test_m4_governance_wiring.py",
     "PHASE_OUTCOME_CLASSIFICATION_VIOLATION.md:239 — “it is the one that *is* a contract update”; closed by corpus integrity II (2.1 **CLOSED**)"),
    ("PHASE_OUTCOME_CLASSIFICATION_VIOLATION R2", "`httpx` is still absent, so two guards remain un-runnable", "NOT STARTED",
     "`httpx` is in neither the venv nor the uv cache", "—",
     "PHASE_OUTCOME_CLASSIFICATION_VIOLATION.md:241 — “their counts remain unquotable”"),
    ("PHASE_GATE_ENABLEMENT R1", "The gate was never exercised, so its interventions and surrenders are NOT MEASURED", "NOT STARTED",
     "the gate is not enabled (= M1)", "—",
     "PHASE_GATE_ENABLEMENT.md:204 — “Reporting those as zero would be manufacturing a metric”"),
    ("PHASE_GATE_ENABLEMENT R2", "The projection is proven over the guard's state space, not over all inputs", "NOT STARTED",
     "a future guard field could break the equivalence", "tests/reliability/test_acceptance_gate_enablement.py",
     "PHASE_GATE_ENABLEMENT.md:206 — “the guard test re-derives it rather than asserting the count”"),
]


def _head_sha() -> str:
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                          capture_output=True, text=True, check=True).stdout.strip()


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
    A(f"> Generated 2026-09-25 at `{_head_sha()}` · **{total} items** · "
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
    A("| `Accepted (low)` · `Accepted` · `Unresolved, no action` | `NOT STARTED` | `CONTEXT.md` §12 |")
    A("| `DEFERRED` · `NOT DONE` | `NOT STARTED` | `WISP_MIGRATION_STATUS.md` per-item tables |")
    A("| `BLOCKED_ON_PRECONDITION` | — | **not used anywhere in this corpus** (§(b)) |")
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
    A(f"Measured 2026-09-25 at `{_head_sha()}` over the {total} rows below. A count is canonical")
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
    A("**How many are the host rather than the architecture.** Eleven of the open rows name a")
    A("declared-and-absent dependency, a resource limit, or the user's own uncommitted work: `R6`,")
    A("`R7`, `R8`, `R9`, `M1` (its population), `ADR-0053 R1`, `ADR-0054 R1`, `ADR-0058 R2`,")
    A("`ADR-0059 R1`, `ADR-0061 R1`, `ADR-0061 R4`. The rest are architectural.")
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
