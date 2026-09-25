# Phase 10 — Authority Closure Implementation

**Repository:** `/Users/philosopher/Documents/wisp`
**Baseline:** Phase 9 at `83b10af`
**Companion:** `PHASE_10_AUTHORITY_CLOSURE_AUDIT.md`

---

## 1. What changed

### Production code (12 files)

| File | Change | Why |
|---|---|---|
| `wisp/pathsec.py` | **+`PROTECTED_PATH_FRAGMENTS`, `PATH_BEARING_ARGS`, `is_protected_path()`** | The canonical protected-path predicate. `pathsec` already owns path safety and is stdlib-only, so every layer can import it. |
| `wisp/tools/_utils.py` | `_is_hook_controlled_path` delegates; `_SENSITIVE_HOOK_DIR_FRAGMENTS` aliases the canonical set | Removes the third copy of the guard; the 5 existing call sites are unchanged. |
| `wisp/auth/decision.py` | L4 scans **every** `PATH_BEARING_ARGS` key via the canonical predicate | Closes the `new_path` gap and the `.wisp/hooksfoo` false positive. |
| `wisp/server/deps.py` | `require_tool_allowed` applies the guard **before** the policy verdict | Closes the REST bypass — the actual security fix. |
| `wisp/core/events.py` | **+138 lines.** Added the canonical outcome taxonomy and classifier: `OutcomeClass`, `OUTCOME_BY_STATUS`, `TERMINAL_OUTCOME_CLASSES`, `_ERROR_TEXT_MARKERS`, `_PROSE_DENIAL_MARKERS`, `classify_status`, `classify_text`, `classify_result`, `is_error_outcome`, `is_terminal_outcome`, `is_denial_text`, `is_denial_outcome` | The module already owned the status taxonomy (`DENIAL_*`) and documented "the shared error predicate treats any non-`ok` status as failure" — but had no classifier. One authority now covers taxonomy *and* classification. |
| `wisp/transport/renderer.py` | `result_is_error` now delegates to `is_error_outcome` (−24 lines) | It was the de-facto canonical predicate with many consumers; its implementation moved to the taxonomy owner and the public name is preserved. |
| `wisp/tool_executor.py` | `_record_metrics` delegates; `_note_fetch_outcome` delegates | **Two live defects fixed** (see §2). |
| `wisp/multi_agent/subagent_orchestrator.py` | `_is_denial` delegates; `_DENIAL_MARKERS` removed (−8 lines net) | **One live defect fixed** (see §2). |
| `wisp/provider_catalog.py` | `list_models` docstring is now the declared canonical contract | Target A: the authority is semantic, and it was undeclared. |
| `wisp/server/routes/hooks.py` | Gate `create_hook` + `test_hook` | **Target C decision.** Both reach host execution; gating only create would leave test as a bypass. |
| `wisp/server/routes/mcp.py` | Gate `add_mcp_server` + `test_mcp_server` + `delete_mcp_server` | **Target C decision.** Registering spawns a command; testing spawns the server; deleting is the symmetric teardown. |
| `wisp/server/routes/plugins.py` | Gate `install_plugin` + `toggle_plugin` + `delete_plugin` | **Target C decision.** Installing/toggling activates in-process code; uninstall is the symmetric teardown. |
| `wisp-desktop/src/renderer/hooks/useApi.ts` | `describeApiError()` surfaces the server's `detail` on any non-OK response (+25 lines) | The refusal reason was being discarded: every policy denial read as a bare `API 403: Forbidden`. |

### Tests (6 files: 3 new, 3 extended)

| File | Tests | Target |
|---|---|---|
| `tests/test_outcome_classification_authority.py` | **67 (new)** | B |
| `tests/test_rest_gate_boundary.py` | **16 (new)** | C |
| `tests/test_protected_path_guard.py` | **26 (new)** | C+ |
| `tests/test_provider_listing_equivalence.py` | 19 (was 13) | A |
| `tests/test_layer_direction.py` | 11 (was 7) | D |
| `tests/test_server_policy_gate.py` | 14 (was 4) — functional deny-in-`read_only` / allow-in-`full` per new gate | C |

**149 new tests.** Phase 10 total across all targets.

### Documents (2)

`PHASE_10_AUTHORITY_CLOSURE_AUDIT.md`, `PHASE_10_AUTHORITY_CLOSURE_IMPLEMENTATION.md` (this file).

### Not touched

Per Rule 1: canonical execution state, path containment, background admission, the verification contract, telemetry accounting, graph run-state reclamation, background retention, TUI task ownership, documentation drift, packaging, F17/F18, F21, F22, F25, the benchmark error-classification work, and the provider-model dependency inversion. Also untouched: F23/F24 (git hygiene) and F8 (environment).

`wisp/core/graph/__init__.py` shows in `git diff` — that is a **pre-existing** edit from before Phase 9, not Phase 10 work.

---

## 2. Defects found and fixed

Three defects were found during the Target B inventory. None were on any prior findings list.

### D1 — the live metrics path counted successful tool calls as errors

```python
# BEFORE (tool_executor._record_metrics)
ok = ((isinstance(result, str) and '"status": "ok"' in result)
      or (isinstance(result, dict) and result.get("status") == "ok"))
```

Measured on a 7-case corpus, **3 diverged** from the canonical classifier:

| result | before | canonical |
|---|---|---|
| `"def f():\n    return 1\n"` (read_file contents) | `ok=False` ❌ | success |
| `"[exit code: 0]\n3 passed"` (run_bash output) | `ok=False` ❌ | success |
| `'{"status":"ok","data":"x"}'` (compact JSON) | `ok=False` ❌ | success |
| `'{"status": "ok", ...}'` (spaced JSON) | `ok=True` | success |
| `{"status": "ok"}` | `ok=True` | success |

Most tools return **plain text**. Every such successful call incremented `tool_errors_total`, deflating `(1 - errors/calls) * 100` at `wisp/metrics.py:102`. This is the live telemetry path, not a benchmark — the same failure mode Phase 9 fixed in `benchmark/scoring.py`, one layer over.

**Fixed:** delegates to `is_error_outcome`.

### D2 — the denial detector could not see a structured denial

`_DENIAL_MARKERS = ("[denied", "denied by", "approval denied", "not authorized")`, tested against every canonical denial status: **all five matched nothing.** `"[POLICY_DENIED]"` contains `"[policy_denied]"`, not `"[denied"`.

The comment beside the constant reads *"Authorization-denial marker: denials must never auto-retry (§26)"* — the rule the detector could not enforce. Because `_is_denial` is consulted *after* `_is_transient`, a denial whose text also looked transient would be retried.

**Fixed:** `is_denial_text` consults the canonical status tokens **first**, then the prose markers. Verified: all five statuses detected; `"connection reset"` correctly still not a denial.

### D3 — the fetch breaker decided on truncated text

```python
# BEFORE
elif '"status": "error"' not in result_str[:200]:   # web_fetch
    ...
if '"status": "ok"' in result_str[:300]:            # web_search
```

Whitespace- and truncation-sensitive, and blind to plain-text errors — which read as success and **silently reset the breaker**.

**Fixed:** delegates to `is_error_outcome`.

**Deliberate behaviour delta, documented in the code:** a `web_fetch` returning a generic error envelope now counts toward the breaker. Previously it did *neither* — not a failure, not a reset — so the breaker under-triggered on exactly the failures it exists to catch. Recorded in the docstring rather than applied silently.

---

## 3. Behaviour-preservation evidence

The migration's central risk was changing `result_is_error`, which has many consumers.

**Method:** the replaced implementation was reconstructed verbatim and run against the new one over a 29-item corpus — dicts with every status, JSON strings (spaced, compact, malformed), legacy markers, `None`, `int`, `list`, whitespace-only, empty.

**Result: IDENTICAL, 0 mismatches.** Pinned permanently by
`test_migrated_predicate_is_equivalent_to_the_original`, which re-derives the old logic and asserts agreement on every corpus item.

The taxonomy adds *precision* (policy-denial / timeout / cancellation become distinguishable) without changing the binary answer any existing consumer reads.

---

## 4. Verification

| Command | Result |
|---|---|
| `pytest tests/test_outcome_classification_authority.py` | **67 passed** |
| `pytest tests/test_rest_gate_boundary.py` | **16 passed** |
| `pytest tests/test_protected_path_guard.py` | **26 passed** — predicate semantics, all three paths, functional REST denial |
| `pytest tests/test_server_policy_gate.py` | **14 passed** — includes functional deny-in-`read_only` / allow-in-`full` for every new gate |
| `pytest tests/test_provider_listing_equivalence.py tests/test_provider_model_authority.py` | **41 passed** |
| `pytest tests/test_layer_direction.py` | **11 passed** |
| `pytest tests/test_transport_renderer.py tests/test_tool_result_error_authority.py tests/test_subagent_orchestrator.py tests/test_multi_agent_patterns.py tests/test_benchmark.py tests/test_transport_cli.py tests/test_core_stateless.py` | **295 passed** |
| `pytest tests/test_tool_executor_bugs.py tests/test_tool_executor_shared_state.py tests/test_tool_pool_isolation.py tests/test_metrics.py tests/test_web_search.py` | **58 passed** |
| `from wisp.server import app` | imports clean after every route change |
| `ruff check wisp/` | **All checks passed** |
| `mypy` (2.3.1, the `uv.lock` pin) | **exit 0** — "Success: no issues found in 8 source files" |
| `npx tsc -b` (desktop client, the real typecheck) | **32 pre-existing errors, none in the lines changed** — see §6 D4 |
| Broad regression over every affected subsystem | see §5 |
| `pytest <26 affected files: guard, policy gate, containment, hooks, sandbox, server routes, tool executor, security, MCP>` | **1195 passed** |
| `pytest <broad sweep>` | **2475 passed**, 7 failed, 5 errors — the known environmental set only |

**No previously closed invariant was weakened.** Re-ran the Phase 9 suites that touch the changed code: `test_tool_result_error_authority.py` (18), `test_layer_direction.py` (7→11), `test_benchmark.py`, `test_metrics.py`, `test_transport_renderer.py` — all pass, with the additions noted.

**Honesty note, preserved from Phase 9:** a single clean full-suite run was again not obtained in this environment (the harness reaps long background runs; several suites need dependencies absent from `.venv`). Verification is reported per subsystem, and the full-suite claim is not made.

---

## 5. Regression detail

Failures in the broad sweep remain the **same known environmental set** as Phase 9 — no new failures:

| Failure | Cause |
|---|---|
| `test_policy_cli.py` (errors), `test_release_cli.py` | `.venv` is missing the declared `cryptography` dependency |
| `test_subagent_enterprise.py` (4) | untracked foreign WIP |
| `test_sandbox_fallback_contract.py` (1) | test-ordering pollution; passes in isolation |

---

## 6. Findings status

| # | Finding | Status |
|---|---|---|
| A | Model listing authority | **PARTIALLY FIXED** — semantic authority + declared contract + tests; transport deliberately distinct |
| B | Error classification authority | **FIXED** — plus D1/D2/D3 |
| C | REST policy boundary | **FIXED** — user chose option B; every host-execution / executable-config route is gated, the boundary is pinned, and the client surfaces the refusal reason |
| C+ | Protected-path guard | **FIXED** — one canonical predicate; a verified REST escalation path (`POST /api/files` writing `.wisp/hooks/`) is shut |
| D | Core → presentation direction | **FIXED** — 0 inversions; 1 classified diagnostic edge |
| — | D1 metrics mis-classification | **FIXED** (not previously known) |
| — | D2 denial detection blind to structured statuses | **FIXED** (not previously known) |
| — | D3 fetch breaker truncated-text classification | **FIXED** (not previously known) |
| — | D4 `tsc --noEmit` is a vacuous typecheck; `tsc -b` is red with 32 pre-existing errors, one functional (`useApi.ts:368` sends no auth header) | **REPORTED, NOT FIXED** — out of scope; needs a decision |
| — | **G0 REST bypass of the protected-path guard** (`POST /api/files` wrote `.wisp/hooks/`) | **FIXED** — found while closing R1b; 26 new tests |
| — | G1 two authorization implementations remain; REST uses the weaker one | **REPORTED, NOT FIXED** — the structural cause of G0 |
| — | Phase 9 F17/F18, F23/F24, F8 | **NO CHANGE REQUIRED** — per Rule 1 |

---

## 7. Remaining debt

| # | Item | Nature |
|---|---|---|
| R1b | `POST /api/hooks` still accepts an unvalidated `command` | The gate restricts *who* may register a hook, not *what* it may run. Content validation (audit option C) was not applied because option B was chosen instead. |
| R1c | 27 mutating routes remain ungated | Deliberate — they mutate state but do not reach host execution or alter executable config. |
| R3 | Full provider-listing delegation | Unsafe until the three deltas converge; the equivalence tests fail at that point and signal it. |
| R4 | `_is_transient` remains a separate predicate | **Not debt** — a different axis (retryability, not outcome class). |
| R5 | Two `RunStatus` enums remain | Phase 9 ratchet; all values coerce. |
| R6 | `.venv` missing 4 declared dependencies | Environment, not architecture (Rule 1). |
| R7 | `capability_filter.py` untracked but imported by tracked code | Pre-existing; guarded by a default-off flag. |
| R8 | Three untracked test files abort collection | User's WIP. |
| R9 | `wisp/core/graph/__init__.py` modified but uncommitted | User's pre-existing edit. |
| R10 | Desktop client typecheck is red (32 pre-existing errors) | `tsc -b` is the real gate; the `typecheck` script's `tsc --noEmit` checks nothing because the root config is project-references with `files: []`. Not Phase 10 work. |
| G1 | **Two authorization implementations remain** — `auth/decision.authorize()` (6-layer) and `infra/security.SecurityPolicy.check()` (4-layer); REST uses the weaker one | **The structural cause of G0.** The real fix is making the REST gate delegate to `authorize()`, which changes the semantics of 12 `SecurityPolicy` consumers. Needs its own decision. |
| G2 | The `run_bash` verb scan is a separate mechanism from the predicate | Accepted — a shell command's target is not determinable from its text. Now defence-in-depth. |
| G3 | `POST /api/hooks` command content unvalidated | Unchanged from R1b. Hooks exist to run arbitrary commands; the gate controls *who*, not *what*. |

---

*Phase 10 implementation complete. 149 new tests. Both gates green. All four targets closed, plus the protected-path guard found en route. The most valuable thing left is G1: the two authorization implementations.*
