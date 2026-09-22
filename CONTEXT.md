# CONTEXT.md — Session Handoff

**Project:** `/Users/philosopher/iCloud Drive (Archive)/Documents/wisp` — "Wisp", a local-first Python CLI coding agent with an enterprise governance layer.
**Session span:** 2026-09-18 → 2026-09-22
**Purpose of this file:** hand off a long, multi-phase architectural engagement so a fresh context can continue without re-deriving anything.

> **The project moved.** It now lives under iCloud (`/Users/philosopher/iCloud Drive (Archive)/Documents/wisp`),
> not `~/Documents/wisp`. The `.venv` editable-install finder was repaired for the move, but its
> `MAPPING` still resolves `wisp` to the iCloud path — which is why `PYTHONPATH` **cannot** override
> which package is imported (see §6).

---

## 0. STATUS — Persistent Graph Loop migration, P0–P3a complete and committed

**Phase 10 is committed.** `HEAD` is **`b17a927`**, four commits on top of the Phase 10 baseline
`83b10af`. See §3 for the commit list.

**The Persistent Graph Loop migration is underway** — `WISP_MIGRATION_PLAN.md` defines phases P0–P9.

| Phase | Status | Report |
|---|---|---|
| P0 — wire the orphaned durable layer | `COMPLETE` | `PHASE_P0_REPORT.md` |
| P1 — journal turn transitions | `COMPLETE` | `PHASE_P1_REPORT.md` |
| P2 — introduce the proposal boundary | `COMPLETE` | `PHASE_P2_REPORT.md` |
| P3 — independent verification | `COMPLETE — stage 3a only` | `PHASE_P3_REPORT.md` |
| P4 — task graph from durable state | `COMPLETE` (item 5 deferred) | `PHASE_P4_REPORT.md` |
| P5 — runtime graph mutation | `COMPLETE` (item 5 deferred) | `PHASE_P5_REPORT.md` |
| P6 — recovery ladder | `READY TO START` | — |
| P7–P9 | `NOT STARTED` | — |

**Ledger:** `WISP_MIGRATION_STATUS.md` (phase ledger, findings log F1–F17, change log).
**Decisions:** `WISP_ARCHITECTURE_DECISIONS.md` (ADR-0001 … ADR-0023).

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

Every phase was verified by `diff -q` of the full-suite failure set before and after:
**131 (HEAD) → 128 for P0, P1, P2, P3, P4 and P5. Zero new failures at every step.**

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

`83b10af` was the Phase 9/10 baseline. Four commits sit on top of it:

| Commit | Scope |
|---|---|
| `cfe6f0f` | `docs:` the Persistent Graph Loop audit (9 documents), migration plan, ledger, decision log, P0–P2 reports — 14 files |
| `744d081` | `feat:` P0 + P1 + P2 implementation and tests — 17 files, 119 new tests |
| `385e552` | `docs:` P0–P2 wiring recorded in `AGENTS.md` + the ledger |
| `b17a927` | `feat(p3):` acceptance criteria, evidence, verdicts (stage 3a) — 11 files, 60 new tests |

> **Scope caveat.** `wisp/config.py`, `wisp/composition.py`, `wisp/core/runtime.py`,
> `wisp/tool_executor.py` and `AGENTS.md` carried **pre-existing uncommitted work** from before the
> migration. It is included in `744d081`/`385e552` because it is interleaved with the migration's
> changes in the same hunks; both commit bodies say so. It could not be separated without
> reverse-engineering changes the migration did not make.

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

### Phase 10's production changes (now committed in `744d081`/`385e552`)

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
| **`.venv` is missing 6 declared deps** | `aiohttp`, `tiktoken`, `prompt_toolkit`, `cryptography` (Phase 10) **+ `jsonschema`, `numpy`** (migration). `jsonschema` is the serious one — see the next row. |
| **⚠️ `jsonschema` missing ⇒ EVERY tool call is refused** | `_validate_tool_args` (`core/stateless.py:2186-2199`) imports `jsonschema` inside a `try` and converts the `ModuleNotFoundError` into a validation-failure **string**, which the caller treats as a hard `SCHEMA_INVALID` denial. **No tool executes at all in this environment.** A missing dependency silently becomes a total tool outage, with the failure misdirected at the tool. Finding F8. `pip install` cannot fix it — no network (SSL cert verification fails). |
| **Editable install is a MetaPathFinder** | `__editable___wisp_0_1_0_finder` resolves `wisp` ahead of `sys.path`, so **`PYTHONPATH` cannot override which package is imported**. To compare against a baseline you must change the files in place. |
| **`git stash` is the wrong baseline tool here** | The tree has pre-existing uncommitted work in the same files. Stashing only your files reverts them to HEAD and discards it — producing false "regressions". Snapshot to a path outside the repo instead. Finding F12. |
| **BSD `grep` via Bash is unreliable here** | `--include` silently matches nothing (caused a false "tiktoken is unused"), and a plain `grep -n "a\|b" file` returned empty with exit 1 for a pattern that plainly exists. **Use the Grep tool, not the shell.** |
| **Long background runs get reaped** | The harness kills long pytest runs without writing a summary. Report per-subsystem results; **do not claim a full-suite pass.** |

---

## 7. Known failures — the environmental set (NOT regressions)

**Measured on the full `tests/` tree with `--continue-on-collection-errors`:**

| Run | Failures + errors |
|---|---|
| `83b10af` (HEAD) | **131** |
| after P0 / P1 / P2 / P3 | **128** — failure sets `diff -q`-identical |

Phase 10's much smaller figure (7 failed / 5 errors, §0) was measured with foreign-WIP files
`--ignore`d; the numbers above are the full tree and are the ones to compare against.

**The residual 128 are dominated by two missing dependencies:** `jsonschema` (every tool-executing test
— `test_tools.py`, `test_salvage_gate.py`, `test_verification_loop.py`, `test_core_stateless.py`,
`test_policy_modes.py`, `test_runtime_tool_history.py`, `test_no_bypass.py`, …) and `httpx` (11 starlette
`TestClient` files, including `test_server_background_routes.py`). Plus `test_13j1_fanout_contract_repair.py`
(13), `test_policy_cli.py` (5), `test_13h2_determinism.py` (6, scripted-stream timing).

**One known-flaky test — the count varies ±1 run-to-run because of it:**

| Flaky test | Evidence |
|---|---|
| `tests/test_speculative_search.py::TestOracle::test_smallest_diff_wins_ties_broken_by_speed` | Passes 5/5 in isolation and 3/3 at file level; appeared once in a 129-failure run and was **absent from an immediate rerun of the identical tree**. Zero coupling to the migration. Finding F17. |

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

**On the full `tests/` tree the number is 128** (§7) — Phase 10's smaller figure was measured with
foreign-WIP files `--ignore`d. Compare like with like.

### Migration suites (P0–P3a) — 179 tests

```bash
env -u PYTHONPATH .venv/bin/python -m pytest \
  tests/test_durable_layer_reachable.py tests/test_turn_journal_incremental.py \
  tests/test_action_idempotency_key.py tests/test_proposal_boundary_records.py \
  tests/test_proposal_boundary_no_bypass.py tests/test_verdict_layer_recorded.py \
  tests/test_gate_order_corpus.py tests/test_acceptance_verdict.py -q
```

### Comparing against a baseline — do NOT use `git stash`

The tree has pre-existing uncommitted work in the same files. Copy the files you are about to edit to
a path **outside** the repo first, then compare. A stash-based baseline reverts them to HEAD, discards
that work, and makes unrelated failures look like yours. Finding F12.

---

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
| R6 | `.venv` missing deps | Environment — now **6** (`jsonschema` and `numpy` join the Phase 10 four). See §6. |
| R7 | `capability_filter.py` untracked but imported | See §8 |
| R8 | 3 untracked test files abort collection | User's WIP |
| R9 | `wisp/core/graph/__init__.py` modified, uncommitted | User's pre-existing edit |

### Migration open items (P0–P3a)

| # | Item | Nature |
|---|---|---|
| **M1** | **Stage 3b of the verification gate** — enable the acceptance gate behind a flag | **BLOCKED on a measurement**, which is itself blocked on a working tool path (`jsonschema`). The plan requires the `INCONCLUSIVE` rate be reported *before* enabling. ADR-0016. |
| **M2** | **Journal-first reconstruction with blob fallback** | **OPEN.** Five production consumers read the session *blob* (`__main__.py`, `supervisor.py`, `sdk.py`, `acp_session.py`, `server/routes/sessions.py`). Hazard: **pre-P0 sessions have no turn body in the log**, so a naive switch reconstructs *worse* than the blob. `PHASE_P1_REPORT.md` §7.1. |
| **M3** | **Killpoint integration** — `tests/reliability/test_killpoints.py` exists; the journal now supports it | **OPEN.** The *detection* primitive is implemented and tested (`Session.unresolved_actions()`); the harness integration is not. `PHASE_P1_REPORT.md` §7.2. |
| **M4** | **Revisit ADR-0004 for the proposal/verdict path** | **OPEN.** Durable writes are best-effort by design; once an authorization verdict or a completion verdict is recorded, silently losing one is closer to a **correctness** precondition than an observability one. |
| **M5** | **Foreground-turn `RunRecord` lifecycle** (P0 plan item 6, deferred) | **OPEN.** The *mechanism* is reachable and proven end-to-end for background runs; applying it to foreground turns is P1's stated remainder. |
| **M6** | `PolicyDecisionEnvelope` (`contracts/policy.py`) is still **producer-less and consumer-less** | **OPEN** — the last unwired contract. `ToolRequest`/`ToolResult` were wired in P2. |
| **M7** | `change_tracker.py` not yet wired into evidence (P3 plan item 7) | **OPEN** — deferred with 3b. |
| **M8** | `multi_agent/dag.py` not yet retired into `wisp/graph/` (P4 plan item 5) | **OPEN — deferred deliberately.** It is on the live `fanout` path, and `test_13j1_fanout_contract_repair.py` is already red for environmental reasons, so a regression caused by the retirement would be indistinguishable from one already there. Needs a green fanout suite first. |
| **M9** | **The message list is not yet a projection of the graph** (P4's stated risk mitigation) | **OPEN** — P5 work. P4 *enables* it by journalling both from one log; it does not implement it. |
| **M11** | **The graph does not drive execution** | **OPEN** — P5's item 5, deferred. The turn loop executes tools directly; the graph is a record, not a driver. Making it drive is a change of control and must land **with** M9. |
| **M10** | The materialized graph is a **lower bound** on iterations | **OPEN — by design.** The runtime materializes one node per closed tool exchange + one terminal node; iteration boundaries are not observable from the event stream, and inventing nodes would be a fabricated record. |

**Committed.** Phase 10 and migration P0–P3a are committed (§3). The remaining uncommitted files are
the user's pre-existing WIP (§8) plus foreign-session test files.

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
| `WISP_MIGRATION_STATUS.md` | **The ledger** — phase status, findings F1–F17, change log, regression summary |
| `WISP_ARCHITECTURE_DECISIONS.md` | **ADR-0001 … ADR-0018** |
| `PHASE_P0_REPORT.md` | Wire the orphaned durable layer |
| `PHASE_P1_REPORT.md` | Journal turn transitions |
| `PHASE_P2_REPORT.md` | Introduce the proposal boundary |
| `PHASE_P3_REPORT.md` | Independent verification (stage 3a) |
| `PHASE_P4_REPORT.md` | Materialize a task graph from durable state |
| `PHASE_P5_REPORT.md` | Runtime graph mutation |

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
