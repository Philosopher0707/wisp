# Phase 7 — Architectural Invariants

**Repository:** `/Users/philosopher/Documents/wisp`
**Scope:** only invariants supported by the actual architecture and its remediation.

---

## Enforcement legend

The brief requires distinguishing *documented* from *actually enforced*. The scale used, weakest to strongest:

| Level | Meaning |
|---|---|
| **DOC** | Stated in prose or a docstring. Nothing detects violation. |
| **TESTED** | An executable test fails if violated. Detected on test run. |
| **STATIC** | Fails at import/instantiation, or a type checker/linter rejects it. Detected without running. |
| **RUNTIME** | Fails at execution time in production, not only in tests. |

**The Phase 3 finding, restated as a rule:** every concept that drifted in this codebase had **DOC**-only enforcement. Every concept that held had **STATIC** or **RUNTIME** enforcement. Invariants below therefore name their enforcement level explicitly — an invariant at DOC level is a known liability, not a guarantee.

---

## Group 1 — Authority (one owner per shared concept)

### INV-1 — Run state has exactly one canonical representation

**Statement.** `wisp.runs.record.RunState` is the sole canonical run-state vocabulary. Every subsystem with a local vocabulary adapts through `coerce_state()`; none re-derives the mapping.

**Enforcement: STATIC + TESTED**
- `tests/test_canonical_execution_state.py::test_run_state_enum_is_defined_exactly_once` — AST scan; fails if a second `RunState` class appears anywhere in `wisp/`.
- `::test_legacy_alias_map_has_exactly_one_definition` — AST scan; fails if a second `LEGACY_STATE_ALIASES` appears.
- `::test_runstatus_duplication_does_not_grow` — **ratchet**: two `RunStatus` enums remain (`contracts/run.py`, `graph/types.py`); a third fails the test.
- `::test_every_runstatus_value_coerces_without_error` — every value of both remaining enums must resolve to a canonical `RunState`.
- `::test_all_terminal_success_spellings_converge` — `"succeeded"` and `"completed"` resolve to the same state.

**Known gap (deliberate, tracked).** The two `RunStatus` enums are *not* removed. They are safe because all their values coerce, but they are still two type definitions for one concept. Tightening the ratchet to a single entry is sequenced work.

---

### INV-2 — Containment semantics live in exactly one implementation

**Statement.** `wisp.pathsec.resolve_contained` is the sole implementation of "prove this path is inside this root". All callers delegate.

**Enforcement: TESTED + STATIC**
- `tests/test_canonical_path_containment.py` — a differential corpus over **six** entry points; each must deny every escape case independently.
- `::test_control_characters_are_rejected_everywhere` — the regression pin for the divergence that existed.
- `::test_symlink_escapes_are_rejected_everywhere`
- `::test_no_module_reimplements_realpath_prefix_containment` — AST/textual scan for the signature pattern, with an explicit allowlist. **This test found a fourth re-implementation (`graph/cli.py`) that a manual audit had missed.**

**Note.** The structural scan is allowlist-based, so it is a *ratchet*, not a proof. A re-implementation that avoids the textual signature would pass.

---

### INV-3 — Provider semantics are defined by the protocol, not by shared modules

**Statement.** `Provider.list_models()` is the only authority for provider-specific model enumeration. No module outside `providers/` imports a concrete provider class.

**Enforcement: STATIC (partial) + TESTED (partial) + DOC**
- **STATIC:** `Provider` is an ABC — a missing method fails at instantiation. `ProviderFactory` asserts registry coverage (`providers/factory.py:36`).
- **TESTED (added in round 2):** `tests/test_provider_model_authority.py` — AST pin that `provider_catalog` does not access a provider's private model table, an offline-fallback equality test against the provider's own public list, a degradation-contract test, and a ratchet on concrete-provider imports outside `wisp/providers/`.
- **DOC / NOT ENFORCED:** the catalog still **reimplements** each provider's HTTP model listing, so two implementations remain and nothing asserts they agree on live data. The dependency inversion is gone; the duplication is not.

**Status: partially enforced.** The private-attribute reach that made this a hard inversion is removed (the fallback now goes through the factory and the public `available_models`). Full delegation is sequenced with named equivalence questions.

---

### INV-4 — Background-agent admission is a single rule at every entry point

**Statement.** `BackgroundAgentManager._admit()` is the one admission decision; `launch()` and `send()` both consult it.

**Enforcement: TESTED**
- `tests/test_background_admission.py::test_both_entry_points_consult_admit` — AST scan of both methods.
- `::test_admission_rule_is_defined_once` — AST scan; fails if admission logic is split into a second method.
- `::test_send_refuses_at_the_bound` / `::test_send_allowed_when_a_slot_is_free` — behavioural, both directions.

---

### INV-5 — The verification gate and the shell output format are pinned together

**Statement.** `tools/bash.py::_format_bash_output` emits `[exit code: N]` **only** on a non-zero exit; `core/verification.py` treats the absence of that marker as success. Both sides change together.

**Enforcement: TESTED**
- `tests/test_verification_contract.py` drives the **real formatter** into the **real guard** — the coupling is executable, not documented.
- `::test_success_output_carries_no_prefix` fails if the formatter changes, which is the silent-inversion guard.
- `::test_literal_nonzero_prefix_still_fails` ensures the parse-based fix did not weaken the gate.

---

## Group 2 — Boundaries (dependency direction)

### INV-6 — The core does not depend on presentation layers

**Statement.** `wisp/core/` must not import from `wisp/cli/`, `wisp/transport/`, `wisp/server/`, `wisp/repl/`, or `wisp/tui/`.

**Enforcement: TESTED (module level) + ratcheted (deferred)**
- **FIXED in round 2:** the module-level inversion is gone. `core/approval_gate.py` no longer imports `ApprovalCancelled`/`ApprovalTimeout` from `wisp.cli.approval`; those verdict types moved to `wisp/exceptions.py` (below both layers), with `cli/approval.py` re-exporting for identity.
- **TESTED:** `tests/test_layer_direction.py::test_core_has_no_module_level_imports_from_presentation` — AST scan of every `wisp/core/**/*.py`.
- **RATCHETED:** `::test_core_deferred_presentation_imports_are_ratcheted` pins the one remaining case — `core/doctor.py`'s deferred, diagnostic introspection of the renderer. A deferred import creates no module-load dependency, and doctor inspects other layers by design.
- **TESTED:** `::test_approval_verdict_types_live_below_both_layers`, `::test_cli_reexport_preserves_identity_for_existing_importers`, `::test_verdict_semantics_preserved`.

**Status: enforced at module level; one deferred diagnostic import retained by design.**

---

### INV-7 — The graph layer asserts nothing about authority

**Statement.** `wisp/graph/` performs no `subprocess`, socket, or direct `ToolExecutor` call; all model-adjacent effects route through the injected runner, and every tool call passes `authorize()`.

**Enforcement: RUNTIME + TESTED**
- **RUNTIME:** `authorize()` is consulted on every tool call regardless of caller.
- **TESTED:** verified by grep in this audit and by the existing graph test suites (489 tests in the graph fortress).

This is the strongest boundary in the codebase: the invariant is structurally true (no such call sites exist) *and* independently enforced downstream.

---

### INV-8 — Model-initiated tool calls pass through one choke point

**Statement.** `ToolExecutor.execute` → `authorize()` is the only path from model output to a side effect. With no executor wired, the fallback is risk-gated to reads.

**Enforcement: RUNTIME + TESTED**
- Single delegation point (`core/stateless.py:1840`); READ-only risk gate on the fallback (`:1846-1887`).
- Subagents share the executor; graph nodes route through the runner.

**Scope note (important).** This invariant is true **for the model path**. It is **not** true for the REST control plane: 35 of 41 mutating routes carry no policy gate. The invariant should be read as stated — model-initiated — and the REST boundary documented separately.

---

## Group 3 — Canonicalization discipline

### INV-9 — A concept with one owner has one executable guard

**Statement.** Any concept declared canonical must have at least TESTED enforcement; DOC-only canonicalization is not canonicalization.

**Enforcement: PROCESS (this document + the report's "Remaining Debt" section).**

**Evidence this rule is load-bearing:** the three concepts that drifted (execution state, error classification, model listing) were all DOC-only. The four that held (provider contract via ABC, provider registry via factory assertion, graph via fingerprint, containment via the canonical helper's 3-of-5 adoption) all had executable guards.

---

### INV-10 — Duplicate definitions do not grow

**Statement.** Where duplication is knowingly tolerated pending removal, a ratchet test pins the current count and fails on growth.

**Enforcement: TESTED**
- `RunStatus` enums: pinned at two (`test_runstatus_duplication_does_not_grow`).
- Containment re-implementations: pinned by allowlist.

**Rationale:** a ratchet is honest about debt while preventing accretion. It is explicitly *not* a claim that the debt is gone.

---

## Invariants NOT claimed

Recording these so they are not mistaken for guarantees.

| Not an invariant | Why |
|---|---|
| "ToolExecutor is the only action path" (unqualified) | False for the REST control plane — 35/41 mutating routes bypass the policy layer. True only for model-initiated calls (INV-8). |
| "The session dict has one writer" | Two writers (runtime and core) with no enforcement. |
| "Configuration is frozen after startup" | `WispConfig` is immutable by convention (`replace()` returns a new instance) but read from the environment at multiple construction sites. |
| "Hook directories are unwritable" | Three guards protect it from the *agent*; `POST /api/hooks` writes it over HTTP. |
| "Error classification is single-sourced" | Three duplicate `is_error` predicates still exist (F2/C2) — **not fixed in this pass**. |
| "The REST surface enforces the policy layer uniformly" | It does not (F2). |

---

## Summary

| Invariant | Level | Status |
|---|---|---|
| INV-1 Run state has one authority | STATIC + TESTED | **Enforced** (2 enums remain, ratcheted) |
| INV-2 Containment has one implementation | TESTED + STATIC | **Enforced** (allowlist-based) |
| INV-3 Provider semantics behind the protocol | STATIC + TESTED partial | **Partially enforced** — inversion removed, delegation pending |
| INV-4 Admission is one rule | TESTED | **Enforced** |
| INV-5 Verification/format coupling pinned | TESTED | **Enforced** |
| INV-6 Core does not import presentation | TESTED (module level) | **Enforced** at module level; 1 deferred diagnostic import ratcheted |
| INV-7 Graph asserts no authority | RUNTIME + TESTED | **Enforced** |
| INV-8 Model calls pass one choke point | RUNTIME + TESTED | **Enforced** (model path only) |
| INV-9 Canonical ⇒ executable guard | PROCESS | Adopted |
| INV-10 Duplication is ratcheted | TESTED | **Enforced** |

**Six enforced, two partially enforced, one procedural, zero unenforced.** Both remaining partials are named in the report's Remaining Debt with their specific open questions rather than smoothed over.
