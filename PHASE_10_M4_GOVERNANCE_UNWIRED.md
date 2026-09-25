# Phase 10 — The M4 Governance Layer Is Not Wired to the Runtime

**Repository:** `/Users/philosopher/Documents/wisp`
**Found while:** closing G1 (authorization parity) — checking whether the REST gate
applies the organization policy bundle the agent path has an L0 layer for
**Severity:** high for an enterprise feature; **not a vulnerability**
**Status:** documented and pinned; **wiring not implemented** (see §6)

---

## 1. The finding in one line

`wisp/policy/` — signed bundles, narrow-only merge, provenance, offline
continuity, a CLI, and four server routes — **is never loaded into the runtime**.
No tool call is ever denied by an organization policy.

An operator can publish a bundle, have it signature-verified, inspect it, explain
a denial with it, and dry-run it. **None of that affects what the agent is
allowed to do.** The subsystem works; it is not connected to the thing it
governs.

---

## 2. Evidence

**The mechanism works when supplied.** Driving the real `authorize()` with a
bundle that denies `run_bash`:

```
authorize(run_bash, effective_policy=<deny bundle>)
  -> allowed=False  layer=organization
     reason="Denied run_bash: approval level is 'deny', controlled by the
             organization policy layer"

authorize(run_bash, effective_policy=None)
  -> allowed=True
```

So L0 is correct and functional. The problem is that `None` is what it always
gets.

**The runtime never supplies one.** Three independent checks:

| Check | Result |
|---|---|
| Every `ToolExecutor(...)` construction | **2 sites** — `composition.py:134` and `acp_session.py:208`. **Neither passes `policy=`** |
| `ToolExecutor.__init__` | `self.policy = policy`; the parameter defaults to `None` (`tool_executor.py:429`) |
| `wisp/config.py` | **zero occurrences of "policy"** — there is no setting to point at a bundle |
| Callers of `load_local` / `load_managed` / `merge_all` / `EffectivePolicy(...)` | `wisp/policy/cli.py` and `tests/test_policy_modes.py`. **Nothing in the runtime** |
| `wisp.policy` importers | `wisp/__main__.py:493` (the `policy` subcommand), `wisp/server/routes/policy.py:40` (publish). **Not composition, not the executor, not the REPL** |

> **ANNOTATED 2026-09-25 (corpus integrity II) — the first row is now wrong twice, and the record
> keeps its number.** Driven, there are **three** `ToolExecutor(...)` construction sites, not two:
> `composition.py:142`, `acp_session.py:208` and **`benchmark/runner.py:82`**. Two corrections:
> the composition line number was stale (`:134` → `:142`), and the **third site was added by
> `8a7e9ab`** (the autonomous-convergence chain) — **after** this document was written, and
> **authorised** by ADR-0045's F54 fix, which wired an executor into
> `benchmark/runner.py::make_ollama_core_factory` because `_execute_tool`'s no-executor fallback
> permits `READ` tools only, so `wisp bench` was refusing every mutation and reporting FAIL for
> tasks no agent could pass. The invariant this table exists for — *no site passes `policy=`* —
> **still holds at all three**. The stale row is annotated rather than rewritten: a phase report
> records what was measured **then**, and deleting its number would falsify history. See
> `PHASE_CORPUS_INTEGRITY_II.md` §1.

**The server routes hold a bundle and stop.** `POST /api/policy/publish`
verifies a signature and stores the document on `request.app.state.policy_bundle`
(`routes/policy.py:51`). `GET /api/policy/current` serves it back.
`POST /api/policy/revoke` bumps the sequence. **Nothing reads
`app.state.policy_bundle` for a decision.** Its own docstring is candid about the
scope: *"this API is a distribution convenience"* — but the thing it distributes
*to* does not exist.

**And the distribution surface is itself unconfigured.** `app.state.policy_pubkey`
is set by `tests/test_policy_routes.py:33` and **by nothing else** — so in a real
server every `/api/policy/*` route returns `503 Policy distribution not
configured` (`routes/policy.py:24`).

---

## 3. What *is* wired (so the gap is specific)

| M4 surface | Wired? |
|---|---|
| `bundle.py` — sign / verify / canonical bytes | ✅ tested |
| `loader.py` — `load_local`, `load_managed`, merge, trim | ✅ tested |
| `explain.py` — `explain_denial`, `dry_run` | ✅ tested |
| `cli.py` — `wisp policy inspect/verify/explain/dry-run/health` | ✅ wired into `__main__.py` |
| `server/routes/policy.py` — publish / current / revoke / health | ✅ routed (but 503 without a pubkey) |
| **`authorize()`'s L0 layer** | ✅ implemented, ✅ tested — **but never receives a bundle** |

Every part is built and every part is tested. The seam between them is empty.

---

## 4. Why this is the most consequential finding so far

The earlier findings in this engagement were **controls that were too weak**:
a guard on two paths and not a third, an approval layer consulted by one model
and not the other. Those are defects of degree.

This is a defect of **kind**: a control that appears to exist and does not. Its
failure mode is **false assurance** — an operator reads the M4 docs, runs
`wisp policy dry-run`, sees `denied: run_bash (organization)`, and concludes
their fleet is governed. Nothing enforces it.

The codebase's own prior audit already named this as the dominant pattern —
`docs/audit-2026-08-24.md:270`, *"Written-but-unwired controls (the dominant
pattern, ≥12 instances)"*. **That audit predates M4** (2026-08-24 vs the M4 spec
of 2026-09-04), and its list does not include this. M4 is a **new instance of
the pattern the codebase had already diagnosed** — which is the more useful
observation: the pattern was known and recurred anyway.

---

## 5. Root cause

The M4 design spec (`docs/superpowers/specs/2026-09-04-m4-policy-design.md`) has
sections for modules, precedence, modes, and tests — and **no wiring section**.
Its §5 "Deferred" lists device registration, a Postgres control plane, and
encryption at rest; **runtime integration is not listed as deferred, because it
was never specified.**

So this is not "we ran out of time". It is: *a subsystem was specified, built,
and tested to completion without the specification ever saying how it reaches
the decision point.* The spec's §0 decision — *"local bundle files remain the
authority"* — presupposes a loader that reads them at startup. Nothing does.

---

## 6. Why wiring is not implemented here

The wiring itself is small and the naming convention already exists —
`wisp/policy/cli.py:26-36` uses **`WISP_POLICY_BUNDLE`**, **`WISP_POLICY_PUBKEY`**,
and **`WISP_POLICY_CACHE`**, so nothing has to be invented. A default-off wiring
would be roughly:

1. `config.py` — read `WISP_POLICY_BUNDLE` / `WISP_POLICY_PUBKEY` (empty by default)
2. `composition.py` — if both are set, `load_local(...)` and pass `policy=` to `ToolExecutor`
3. `server/deps.py::request_policy` — load the same bundle so REST sees L0 too
4. Tests — assert no-op when unset, denial when set

**It is not done, for a specific reason.** `WISP_POLICY_PUBKEY` presupposes that
an operator *has* a trusted public key — and the M4 spec explicitly deferred the
**"device registration + key distribution ceremony (needs human workflow
design)"** (§5). Wiring the bundle before that ceremony exists would make the
env vars live while leaving the question of *how a key is trusted* unanswered —
which is precisely the assumption the brief forbids encoding as architecture.

**It is a decision, and a genuinely unmade one.** It is also now the highest-value
open item in the repository.

---

## 7. What was implemented: the gap is now visible and cannot be forgotten

`tests/test_m4_governance_wiring.py` (14 tests) pins the current state.

| Test | Purpose |
|---|---|
| `test_l0_denies_when_a_bundle_is_supplied` | the mechanism is real, not theoretical |
| `test_l0_is_inert_without_a_bundle` | and inert by default |
| `test_no_tool_executor_is_constructed_with_a_policy` | **fails the moment someone wires it** — forcing the doc and the decision to be updated together |
| `test_config_has_no_policy_bundle_setting` | there is no way to configure one today |
| `test_the_runtime_never_imports_the_policy_package` | the seam is empty, not merely unpopulated |
| `test_the_publish_route_holds_a_bundle_that_nothing_reads` | the held bundle has no consumer |
| `test_the_distribution_surface_is_unconfigured_in_production` | `policy_pubkey` is set only by tests |
| `test_the_m4_spec_still_has_no_wiring_section` | the root cause is a spec gap; if a wiring section appears, re-read this document |

The third test is the important one: it is a **tripwire**, not an assertion that
the current state is right. Wiring the layer without updating this document
should be impossible to do accidentally.

---

## 8. Honest note

This was not found by looking for governance gaps. It came from following the
G1 question one step further — *if the agent has an L0 policy layer, what does
the REST gate do with it?* — and discovering that neither path has one, because
nothing loads a bundle.

That is the **seventh** instance of the engagement's recurring pattern, and the
second in a row where the honest answer to *"which of these two is wrong?"* was
**"both, because the thing they disagree about is never set."**

---

## 9. Recommendation

| Option | Effect | Cost |
|---|---|---|
| **A. Wire it, default-off** | The subsystem becomes functional; zero behaviour change when unset | Pre-empts the deferred key-distribution decision. Recommended only *after* that decision |
| **B. Decide the key-trust workflow first, then wire** | Correct order: `WISP_POLICY_PUBKEY` presupposes a ceremony that does not exist | A human workflow decision |
| **C. Mark it clearly as unenforced** | Docs and CLI stop implying enforcement | **✅ IMPLEMENTED** (see §10) |
| **D. Remove it** | Deletes a tested subsystem | Wasteful; M4 is the enterprise story |

**Recommendation: B, with C immediately.** C is done. B remains the open decision.

---

## 10. Option C, implemented

The harm of this finding is **false assurance**, and that harm is removable
without any decision about key trust. Done in this pass:

| Surface | Before | After |
|---|---|---|
| `wisp policy inspect/verify/explain/dry-run/health` | output read as an in-force verdict | a **NOT ENFORCED** notice is printed ahead of every one of them, naming this document |
| `wisp/policy/explain.py::explain_denial` | `"Denied run_bash: …"` — reports a denial that did not happen | `"Rule: deny run_bash — …"` — states the bundle's rule, which is true in both states |
| `README.md` (intro + governance section) | advertised signed governance policies as live | qualifier inline and a callout above the section; the graph "Governed" bullet corrected too |
| `AGENTS.md` policy-module row | listed the module neutrally | carries **⚠️ Not wired to the runtime** |
| `docs/SECURITY.md` | "Policy bundles Ed25519-signed, expiry trims authority (M4)" | adds **⚠️ Not enforced at runtime** |

**A correction made along the way.** The README's graph bullet said *"graph
policy is narrow-only over the active policy"*. `validate_graph(graph)` reads
`graph.policies` — the **graph's own** embedded policy — and takes no external
policy argument. There is no "active policy" for it to narrow over. Reworded to
what the code does (`allowed_nodes` / `allowed_tools` / `allowed_models`, with
`"all"` refused under a restrictive policy).

**Pinned** by seven further tests in `tests/test_m4_governance_wiring.py`
(25 total): every evaluating command emits the notice, transport commands do
not, the notice names this document, `explain_denial` states a rule rather than
a result, and the three docs each carry their qualifier. The CLI tests'
golden-output assertions still pass unchanged — the notice is additive.

**What C does not do:** it does not make the policy enforced. It removes the
*claim* that it is. B is still required.
