# PHASE_M4_WIRING — the M4 policy layer is wired, behind one env var, default OFF

**Baseline:** `HEAD` = `db29a11` (§0 records `08dd57e`), tree = 29, ADR count 57 → **58** (ADR-0058, D1).
**Deliverable:** 2 of 2. **Type:** implementation.
**Report:** this file. Decision: **ADR-0058** (`PHASE_KEY_TRUST_WORKFLOW.md`). Guard:
`tests/reliability/test_m4_policy_wiring.py` (10 tests). Updated in place:
`PHASE_10_M4_GOVERNANCE_UNWIRED.md`.

---

## 1. The change

| file | change | default |
|---|---|---|
| `wisp/config.py` | two **string** settings, `policy_bundle` / `policy_pubkey` (`WISP_POLICY_BUNDLE` / `WISP_POLICY_PUBKEY`) | both `""` — **no behaviour change** |
| `wisp/composition.py` | `load_organization_policy(config)` — the single load site (ADR-0006); the construction site passes `policy=load_organization_policy(self.config)` | `None` when unset; **raises** when a named bundle is unverifiable (R3) |
| `wisp/server/deps.py::request_policy` | **not changed** — see §4 | — |
| `tests/test_m4_governance_wiring.py` | four tripwires inverted, two over-broad scans repaired | — |
| `PHASE_10_M4_GOVERNANCE_UNWIRED.md` | `Status:` moved, §6's recipe marked done, §7's tripwires marked as replaced, §11 added | — |

**No new flag.** ADR-0002's one-flag-per-concern rule is not a licence to proliferate flags, and §6's
*"empty by default"* already describes a switch: **`WISP_POLICY_BUNDLE` non-empty is the trigger**
(ADR-0058 R1). `policy_pubkey` is a companion value, not a second switch.

---

## 2. The four tripwires, inverted — not weakened, not deleted

The P9/M15 and M11/M13 precedent: replace the tripwire with its inverse in the same change, so the code
and the document cannot drift apart.

| was | is now | the property it keeps |
|---|---|---|
| `test_no_tool_executor_is_constructed_with_a_policy` | `test_the_composition_root_passes_the_organization_policy` | **exactly one** site passes `policy=`, and it is the composition root. The other two are named in the docstring and must not |
| `test_the_runtime_never_imports_the_policy_package` | `test_only_the_composition_root_imports_the_policy_package` | exactly **one** runtime importer — a second load site fails |
| `test_config_has_no_policy_bundle_setting` | `test_config_reads_the_policy_settings` | both settings **default to empty**, so an unconfigured runtime loads nothing |
| `test_the_loader_entry_points_have_no_runtime_caller` | *(same test, inverted assertion)* | the only runtime caller is the composition root; the CLI floor stays |

**Four tests are unchanged and still green**, which is itself evidence the wiring is scoped:
`test_the_agent_passes_its_none_policy_through` (`policy: Any = None` survives),
`test_the_publish_route_holds_a_bundle_that_nothing_reads` (§4), and
`test_the_m4_spec_still_has_no_wiring_section` (the spec is not edited).

---

## 3. Two more tripwires fired — **for the wrong reason** — and were repaired

Both are the class the brief names: *a guard that pins a state rather than a property is a nuisance;
write it so it fails on a real violation, not on the next legitimate addition.* Both were found by
**running**, not by reading.

**3.1 `test_the_loader_entry_points_have_no_runtime_caller`** — fixed in D1's follow-up (`1f5f6fc`).
It pinned the exact **set** `{wisp/policy/cli.py, tests/test_policy_modes.py}`, so D1's own guard —
which drives `load_local` — made it fire, though a *test* calling the loader is not *"the layer may be
wired"* and its docstring already says *"reachable only from the CLI and **tests**"*. Stated as a rule
now (the CLI, and tests, nothing else) with a floor.

**3.2 `test_the_distribution_surface_is_unconfigured_in_production`** — found when the canonical block
went red on D2's first full run. It scanned for the **bare name** `policy_pubkey=` anywhere under
`wisp/` and `tests/`, which conflates two different things: the **route's** `app.state.policy_pubkey`
(what the tripwire is about — `wisp/server/routes/policy.py:22` reads it, `tests/test_policy_routes.py:33`
sets it) and ADR-0058's **config setting** `WispConfig.policy_pubkey`. D2's guard passes the setting as a
keyword argument, so the scan matched it and the tripwire fired. Scoped to `state.policy_pubkey` — which
is what its sibling `test_the_publish_route_holds_a_bundle_that_nothing_reads` already does with
`state.policy_bundle`.

Both were probed **in both directions**:

```
3.1  a runtime CALL in wisp/composition.py    -> CAUGHT   ·  a new TEST caller          -> MISSED (correct)
3.2  a PRODUCTION setter in routes/policy.py  -> CAUGHT   ·  the config setting alone   -> MISSED (correct)
```

Files restored byte-identical after each probe.

---

## 4. REST does **not** receive L0 — and §6's step 3 says why it should not

§6's recipe step 3 is *"`server/deps.py::request_policy` — load the same bundle so REST sees L0 too"*.
**Driven, that is not implementable as written.** `require_tool_allowed` consumes
`SecurityPolicy.check(action, context)`; **`SecurityPolicy` has no organization slot** —
`dir(SecurityPolicy)` contains no policy-shaped attribute, and `check` takes no bundle. L0 lives inside
`authorize()`, which REST does **not** call for these actions (ADR-0055's measurement).

So a bundle loaded into `request_policy` would be **dead data** — a new instance of the exact pattern
`PHASE_10_M4_GOVERNANCE_UNWIRED.md` §4 diagnoses. Wiring L0 into REST means adding an `authorize()` call
to the REST gate, which changes the gate ADR-0055 measured and pinned; that is **its own decision**.

**Stated as a residual, and pinned so it cannot drift silently:**
`test_rest_does_not_receive_l0_because_security_policy_has_no_slot` asserts `SecurityPolicy` has no
policy-shaped attribute **and** that `check`'s parameters are still `(self, action, context)`. If either
changes, the test fails and the decision is revisited.

---

## 5. Which sites receive the bundle

| site | receives it? | why |
|---|---|---|
| `wisp/composition.py:142` | **YES** | ADR-0006's single construction site; the runtime |
| `wisp/acp_session.py:208` | no | the fallback is reached only when there is **no** composition root — an ACP-only deployment has no configured runtime to load from. **Named as a residual** |
| `wisp/benchmark/runner.py:82` | no | a benchmark **harness**, not the runtime; giving it a policy would change what it measures |

`test_the_composition_root_passes_the_organization_policy` pins the set as exactly
`{"wisp/composition.py"}`, so a fourth site or a second importer fails.

---

## 6. Non-violations, re-asserted in the wired state

`test_key_trust_workflow.py` asserts the three before the wiring exists; `test_m4_policy_wiring.py`
re-asserts them **after** — the pair is the point, because the change that could have violated them is
this one.

1. `authorize()` — parameter list and `AuthorizationDecision`'s fields.
2. `wisp/policy/` — `load_local`'s signature, the package's `__all__` (with a floor), and the offline
   promise. The wiring adds a **caller**; it does not change the module.
3. `ToolExecutor.__init__`'s `policy` still defaults to `None`, and the executor still hands
   `effective_policy=self.policy` to `authorize()`.

---

## 7. Non-vacuity

**7/7 CAUGHT, 4 files restored byte-identical (sha256).**
`.workbuddy-ai/memory/post-m13-gate-enablement/m4_wiring_nonvacuity.py`

| probe | what it breaks | result |
|---|---|---|
| NVA | *unset* stops short-circuiting (R2) | **CAUGHT** |
| NVB | the loader's failure is swallowed (R3) | **CAUGHT** (3 tests) |
| NVC | the construction site stops passing `policy=` | **CAUGHT** |
| NVD | config's policy default stops being empty | **CAUGHT** |
| NVE | a **second** runtime module imports the policy package | **CAUGHT** |
| NVF | a **second** runtime caller of the loader | **CAUGHT** |
| NVG | `SecurityPolicy.check` gains a policy parameter | **CAUGHT** |

**The real path is driven where it can be.** The no-op and refusal cases construct a real
`CompositionRoot` (0.85 s) and run the real loader; the *engages* case stubs the **acquisition** step
only — a verifiable bundle needs `cryptography`, absent here (F88) — and says so in the test.

---

## 8. Findings

**F88 — the M4 policy suite is red in this environment** (D1 §6): 14 failed, 6 errors, 13 passed across
the five `test_policy_*.py` files, none of them in the canonical block. `cryptography` is declared but
absent.

**F89 — a bundle that omits `expires_at` raises `min() iterable argument is empty`** (D1 §6). Fails
closed; the message is wrong. Named, not fixed.

**F92 — two M4 tripwires scanned for a bare name and pinned an exact set**, so a legitimate addition
fired them for the wrong reason (§3). One in D1, one in D2. Both repaired, both probed in both
directions.

**F93 — the canonical block's heading was 1390 and the block now measures 1437** — a **+47** change with
**+3 files**, re-measured in this same change per F85. The block also now **includes**
`tests/test_m4_governance_wiring.py`, which was in no running block: the wiring's own guard was
un-runnable, which is how its two over-broad scans survived (F88's shape, one layer up).

---

## 9. Verification

| check | result |
|---|---|
| the new wiring guard | **10 passed** |
| the M4 guard (25 tests, 4 inverted + 2 repaired) | **25 passed** |
| the key-trust guard | 12 passed |
| non-vacuity (D2) | **7/7 CAUGHT**, 4 files restored byte-identical |
| the canonical block (50 files, both blocks identical) | **1437 tests — 1436 passed, 1 failed (F38)** |
| the two tripwire repairs, probed both ways | **CAUGHT / MISSED** as designed; files restored |
| `ruff` on the changed files | clean; `ruff check wisp/` unchanged at **11** (F71) |
| `mypy` | not re-run — stated as an argument, not a measurement |

**Rollback:** unset `WISP_POLICY_BUNDLE` — or revert the two `wisp/` changes; the guard's
`test_unset_is_a_no_op_on_the_real_composition_root` is the observation that shows the default is
intact.

---

## 10. Residuals, open

1. **REST does not receive L0** (§4) — its own decision, pinned so it cannot drift silently.
2. **`acp_session.py:208`** — an ACP-only deployment would need the same load; named, not done.
3. **The M4 policy suite cannot run here** (F88).
4. **F89** — the falsy-`expires_at` message.
5. **`WISP_POLICY_CACHE` / `load_managed`** — the managed/disconnected modes are not engaged (ADR-0058
   R6).
