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

**HEAD is `ade4dc6`** · branch `main`. The baseline is Phase 10's `83b10af`; §3 lists every commit
on top of it (**35**) and is the authority for the count.
**`ade4dc6` is the POST-M13 landing** — 108 files, +26,811/−397, covering ADR-0034 … ADR-0044
(M13, the POST-M13 chain, F8/F37, and the provider-event chain through ADR-0044). It excludes the
user's pre-existing WIP, listed in §8.
**The migration suites are 849 tests — 848 pass, 1 fails.** The failure is
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

**Ledger:** `WISP_MIGRATION_STATUS.md` (phase ledger, findings **F1–F44**, change log).
**Decisions:** `WISP_ARCHITECTURE_DECISIONS.md` (**ADR-0001 … ADR-0044**).

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
| **G1** | Authorization parity — the agent composes *both* decision models, REST consults one; 6 of 36 (route, mode) pairs diverge, in the **default** mode | ⚠️ **MEASURED + PINNED, needs a decision** (§0c) |
| **E** | The M4 organization policy layer is **never loaded** — a governance control that appears to exist and does not | ✅ false-assurance half **FIXED**; wiring needs a decision (§0d) |
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

| Check | Result |
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
| 2 | **G1** — authorization parity (§0c) | Option B gives parity but the client 403s on config routes in the default `auto_edit` mode. Recommended: B now, C (route approvals through the existing WebSocket channel) as the real fix |
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
| **10c. Parity** *(found while auditing the guard's cause)* | `PHASE_10_AUTHORIZATION_PARITY.md` | G1 measured: 9 of 36 (route, mode) pairs diverge; 6 remain, all the approval layer, in the default mode. Ratcheted; **one decision open** |
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
| `ade4dc6` | `feat:` land the POST-M13 execution-semantics work (ADR-0034 – ADR-0044) — 108 files, +26,811/−397; excludes the user's WIP (§8) — **`HEAD`** |

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
| **Editable install is a MetaPathFinder** | `__editable___wisp_0_1_0_finder` resolves `wisp` ahead of `sys.path`, so **`PYTHONPATH` cannot override which package is imported**. To compare against a baseline you must change the files in place. |
| **`git stash` is the wrong baseline tool here** | The tree has pre-existing uncommitted work in the same files. Stashing only your files reverts them to HEAD and discards it — producing false "regressions". Snapshot to a path outside the repo instead. Finding F12. |
| **BSD `grep` via Bash is unreliable here** | `--include` silently matches nothing (caused a false "tiktoken is unused"), and a plain `grep -n "a\|b" file` returned empty with exit 1 for a pattern that plainly exists. **Use the Grep tool, not the shell.** |
| **Long background runs get reaped** | The harness kills long pytest runs without writing a summary. Report per-subsystem results; **do not claim a full-suite pass.** |

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

The residual failures are dominated by **`httpx`** (11 starlette `TestClient` files), the 17 collection
`ERROR`s from foreign-session WIP files, and whatever the re-measurement finds. `jsonschema` is no longer
among them, and neither are the three files above.

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

---

## 11. Verification commands that actually work

```bash
# Gates (both must be green)
/opt/anaconda3/envs/litllm/bin/ruff check wisp/
uv run --no-project --with "mypy==2.3.1" mypy

# Phase 10 tests — the full set (245 passed)
env -u PYTHONPATH .venv/bin/python -m pytest \
  tests/test_outcome_classification_authority.py \
  tests/test_rest_gate_boundary.py \
  tests/test_protected_path_guard.py \
  tests/test_authorization_parity.py \
  tests/test_m4_governance_wiring.py \
  tests/test_unwired_controls_inventory.py \
  tests/test_server_policy_gate.py \
  tests/test_provider_listing_equivalence.py \
  tests/test_provider_model_authority.py \
  tests/test_layer_direction.py \
  tests/test_doc_drift.py -q -p no:cacheprovider

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

### Migration suites — 849 tests (848 pass, 1 fails)

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
  tests/reliability/test_verification_evidence_adapter.py -q
```

**Measured 2026-09-25, after the F37 evidence-adapter repair: 849 tests — 848 pass, 1 fails.** The failure is
`test_node_identity.py::TestANodeReferencesItsWorkUnit::test_a_parallel_round_is_journaled_as_one_exchange_per_call`
(**F38**): it pinned the exchange ordering that only existed because F8 refused every call pre-dispatch.
Now that calls genuinely dispatch, the grouping rule produces the batch shape it explicitly supports
(`call:c0+c1`). The production behaviour is *more* correct, not less — the test needs a one-line contract
update, which requires explicit authorization.

**This heading said "714 tests" until 2026-09-25** — that figure was the *pre-enforcement* count and the
command below it had never been extended with the POST-M13 files.

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
| **E** | **M4 governance layer not wired to the runtime** — `wisp/policy/` is never loaded; `ToolExecutor.policy` is `None` at both construction sites; `config.py` has no policy setting; `app.state.policy_pubkey` is set only by tests | **False-assurance half FIXED** (option C: CLI notice + doc qualifiers). **Wiring itself OPEN** (§0d) — depends on the **key-distribution ceremony** the M4 spec deferred. Pinned by `tests/test_m4_governance_wiring.py` (25 tests). |
| **G1** | **Authorization parity gap** — the agent composes *both* models (`policy_hard_deny` + `authorize()` + the approval gate); REST consults *only* `SecurityPolicy`. 6 of 36 (route, mode) pairs diverge, all the approval layer, in the **default** `auto_edit` mode. | **OPEN, measured** (§0c). Ratcheted by `tests/test_authorization_parity.py`. Options A/B/C in `PHASE_10_AUTHORIZATION_PARITY.md`; recommended **B now, C as the real fix**. |
| R1b | `POST /api/hooks` still accepts an unvalidated `command` | **OPEN — needs a decision.** The gate restricts *who* may register a hook, not *what* it runs. |
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
| R6 | `.venv` missing deps | Environment — **5** now: `jsonschema` is **fixed** (F8), so `numpy` joins the Phase 10 four. See §6. |
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
| **M1** | P3 stage 3b — enable the acceptance gate | **Blocked on an architecture decision, not on evidence or code.** F8 is fixed, tools genuinely execute, **F37 is fixed** (the contaminated row is gone; the matrix re-measured to 2 PASS / 2 FAIL / 4 INCONCLUSIVE), and a **live-provider** population now exists (28 real turns across 3 models, `FALSE_SUCCESS_AFTER = 0`). But **ADR-0016 is `NOT_YET_DETERMINABLE`**: the contract asks for a *measurement period*, and what exists is a controlled matrix — and the rate proved **model-dependent** (64.3% for the capable model, 100% degenerate for the two small ones). Enabling `stagnation_gate` by default is listed by **ADR-0037** among the things forbidden **without a superseding ADR**. So the next step is an ADR, not a code change. ADR-0016. |
| **M2** | Journal-first reconstruction | ✅ **COMPLETE** — `reconstruct()` + `reconstruction_source()`; the pre-P0 hazard and the gap hazard are both handled and pinned. **Five consumers still read the blob** (a tripwire asserts it). |
| **M3** | Killpoint integration | ✅ **COMPLETE** — `test_kp_session_midtool_then_killed`. One window covered. |
| **M4** | ADR-0004 revisited | ✅ **COMPLETE** — **ADR-0027**. Found a live defect (M2's journal-first could return a provider-invalid transcript). |
| **M5** | Foreground-turn `RunRecord` lifecycle | **OPEN** — proven end-to-end for background runs only. |
| **M6** | `PolicyDecisionEnvelope` producer-less and consumer-less | **OPEN** — the last unwired contract. |
| **M7** | `change_tracker.py` not wired into evidence | **OPEN** — deferred with 3b. |
| **M8** | `multi_agent/dag.py` not retired into `wisp/graph/` | **OPEN — the deferral's stated reason no longer holds.** It was deferred because `test_13j1_fanout_contract_repair.py` was *already red* for environmental reasons, so a regression would be indistinguishable. The F8 provisioning made it **green** (`test_13j1` 13→0, `test_13j` 5→0), so retiring `dag.py` can now be attempted and any regression **will** be attributable. Still on the live `fanout` path — treat it as a real change, not a cleanup. |
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
| `WISP_ARCHITECTURE_DECISIONS.md` | **ADR-0001 … ADR-0044** |
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
