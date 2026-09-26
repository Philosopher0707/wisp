# Phase — The REST gate's authorization composition (ADR-0059)

**Status:** `COMPLETE` — **`DECIDED + IMPLEMENTED`**. One new ADR, one production change, one inverted
pin, one contract update, one guard.
**Baseline:** `HEAD` = `5330a0f` (§0 recorded `aa9d8a8`, the handoff convention); tree = the 29 WIP
entries of §8; ADR count 58 → **59**.
**Predecessors:** `PHASE_M4_WIRING.md` §4 (which pinned this as its own decision),
`PHASE_AUTHORIZATION_PARITY.md` (ADR-0055), `PHASE_10_M4_GOVERNANCE_UNWIRED.md` §11 residual 1.
**Report scope:** the decision, the measurement, the change, the residual.

---

## 1. The decision, in one paragraph

> **The REST gate consults the M2 authority for its DENIAL verdict, and only for that.** ADR-0058
> wired an organization policy into the agent path; a bundle denying an agent tool name was then
> enforced there and **silently unenforced on REST** — driven, **11 of 36 (route, mode) pairs
> diverge**, five in the **default** `auto_edit` mode. So `require_tool_allowed` now calls
> `authorize()` with the **same `effective_policy` the composition root loaded** and refuses when it
> denies. It does **not** compose `authorize()`'s `approval_required`: approval stays with
> `SecurityPolicy.check()`. The reason is measured, not argued — **the two models agree on `allowed`
> in all 36 (route, mode) pairs** and disagree only on `approval_required`, in **exactly six** rows.
> The consult is **conditional on a bundle being loaded**, so with `WISP_POLICY_BUNDLE` unset the gate
> is today's code **byte-for-byte — status and detail**, proved on 40 rows.

---

## 2. Why the gap is real, and why ADR-0055 did not see it

ADR-0055 measured **0 path divergences of 36** and that measurement is still correct. It was taken
with **no organization policy loaded** — `ToolExecutor.policy` was `None` at every construction site,
so L0 was inert on both paths. ADR-0058 changed that condition and did not re-measure the paths.

**Driven** (`scripts/authorization_parity_measurement.py`'s method, extended with a loaded bundle):

| route | mode | agent (`approval_handler=None`) | REST (before) | diverges |
|---|---|---|---|---|
| `POST /api/files` | `auto_edit` | DENY (L0) | **ALLOW** | **yes** |
| `POST /api/files` | `full` | DENY (L0) | **ALLOW** | **yes** |
| `POST /api/files/edit` | `auto_edit` | DENY (L0) | **ALLOW** | **yes** |
| `POST /api/files/edit` | `full` | DENY (L0) | **ALLOW** | **yes** |
| `POST /api/bash` | `full` | DENY (L0) | **ALLOW** | **yes** |
| … plus the 6 remaining rows of the 11 | | | | |

A bundle an operator wrote to deny `write_file` was enforced on the agent path and **silently
unenforced on REST**. That is the false-assurance shape the M4 finding exists to name
(`PHASE_10_M4_GOVERNANCE_UNWIRED.md` §4), reintroduced by the landing that closed it.

**The counterfactual is in the guard.** `test_the_gap_is_real_when_no_composition_is_consulted`
drives the same rows with `organization_policy=None` and requires **≥ 8** of them to ignore the
denying bundle, with a **floor** so a shrunk row set cannot pass vacuously. The closure test then
drives the same rows and requires every one to refuse.

---

## 3. The measurement that decides the option

| question | answer, driven |
|---|---|
| do the two models ever disagree on `allowed`? | **No — 0 rows of 36** (no bundle) |
| where do they disagree? | **`approval_required` — 6 rows of 36** |
| which six? | **exactly** `hooks.create` / `mcp.add_server` / `plugins.install` × {`auto_edit`, `ask_all`} |
| on agent tool names? | the models **agree on both fields** in every mode |

The divergence between the models exists **exactly where the agent has no operation** — and nowhere
else. So *composing `allowed` composes the agreement*; composing `approval_required` composes the
divergence, and the divergence is not a gap — it is what keeps the shipped client working.

### The options, each driven

| option | what it is | measured effect | verdict |
|---|---|---|---|
| **A** | REST consults both, in the agent's order | **16 of 36 rows move with NO bundle** — incl. three config routes in the **default** mode, ALLOW → `403(approval)` | **REJECTED** — a client regression, not a parity fix |
| **B** | replace `SecurityPolicy.check()` with `authorize()` | discards the mode engine **and** the hooks layer; changes every gated route's semantics | **REJECTED** |
| **C** | consult `authorize()` for L0 only | needs a stable marker for "this denial came from L0" — which does not exist (L0's `controlling_layer` is the *provenance value*: `"local file"`, or a managed layer's name), or two calls compared | **ADOPTED, narrowed to the whole DENIAL verdict** — one call, one field, and it moves **zero** rows the mode engine already refuses |
| **D** | accept the divergence; change the docstring | the divergence is **real** (11 rows), in the default mode | **REJECTED — refuted by measurement** |

**D is the one the brief leaned toward, and its central row is false.** The brief's worked example
shows:

> *"`auto_edit` | `hooks.create` | no agent op | ALLOW | ALLOW | none — **L0 has no verdict on a
> non-agent name**"*

Driven: `authorize()` with `approval_matrix={"hooks.create": "deny"}` returns
`allowed=False, controlling_layer="local file"`. **The verdict exists; nothing consulted it.** So D is
not "accept a harmless divergence" — it is "leave the operator's stated rule unenforced".

---

## 4. The change

| file | change | default |
|---|---|---|
| `wisp/composition.py` | `self.organization_policy = load_organization_policy(self.config)` — held on the root so the second consumer reads the **same** instance; the construction site passes `policy=self.organization_policy` | `None`; no behaviour change |
| `wisp/server/deps.py` | `organization_policy(request)` + `_m2_denial(...)`; one insertion in `require_tool_allowed` between the path guard and the mode engine; the docstring now names three checks and their authorities | inert when no bundle is loaded |
| no new flag | `WISP_POLICY_BUNDLE` non-empty is the switch (ADR-0058 R1); ADR-0002's one-flag-per-concern rule is not a licence to proliferate flags | — |

**The change is an insertion, not a rewrite.** The diff to `require_tool_allowed`'s body adds ten
lines between the protected-path guard and `request_policy`; the guard block and the
`request_policy`/`check` block are byte-identical to HEAD. So HEAD's body is a **subsequence** of the
new body, and the conditional consult is the only new behaviour.

### R2, proved two ways

1. **Behaviourally.** `test_no_bundle_is_the_old_gate_byte_for_byte` reconstructs HEAD's body
   (guard → `SecurityPolicy.check()`) and drives it against the real new gate over **40 rows**
   (9 routes × 4 modes, plus a protected-path row): **40/40 identical, status *and* detail.**
2. **Structurally.** The consult's first act is `if policy is None: return None`, and
   `organization_policy` returns `None` when the root holds none — so the inserted block is skipped
   and the old body runs.

---

## 5. What the gate is, said plainly

`require_tool_allowed` composes **two authorities**, on their **denials**:

1. **Protected-path guard** — unchanged, first, its message pinned by
   `tests/test_protected_path_guard.py:330`.
2. **The M2 authority's denial verdict** — `authorize()` with the root's loaded policy. **Denials
   only.**
3. **The mode engine** — `SecurityPolicy.check()`, unchanged.

All three narrow, so the denied set is their **conjunction** and the order affects only the message.
The M2 consult precedes the mode engine so the **higher authority's** reason is the one reported: a
hard denial must not be presented as an approval requirement.

**And the approval half stays where it was.** `SecurityPolicy.check()` is the only source of an
approval requirement on REST. That is why a bundle's `approve` level is inert here (§7 residual 1).

---

## 6. The guards

**New — `tests/reliability/test_rest_authorization_composition.py` (16 tests).** Every one drives the
**real** `require_tool_allowed`:

- the row set has a **floor** (a shrunk set cannot pass vacuously);
- **the gap is real** without the consult, and **closes** with it — the pair is the point;
- a bundle naming a **REST-only** action is honoured (before, nothing consulted it);
- **R2's differential** (40 rows, status and detail);
- a bundle's **`approve` level is inert** — pinned as a *property*, so composing it must be deliberate;
- the **protected-path guard's message survives**;
- **`_m2_denial` reads `allowed` and never `approval_required`** — parsed, not scanned;
- the consult is **gated on a loaded policy**, and a root-less request does not raise;
- **one load site** (`root.organization_policy is root.tool_executor.policy`) and **no import of
  `wisp.policy`** in `deps.py`;
- **the same principal as the agent** — observed through the real call (see §6.1);
- the **four non-violations**.

**Inverted — `test_m4_policy_wiring.py`'s §4 pin.** It used to assert REST does **not** receive L0
*because* `SecurityPolicy` has no slot. Both assertions still hold — this ADR did **not** take that
route — so they now say *which route was not taken*, and the test adds the route that was. If a
`SecurityPolicy` slot ever appears, a **second** design has landed and the test fails so it is noticed.

**Contract update — `test_proposal_boundary_no_bypass.py`.** `AUTHORITY_CONSUMERS` gains
`wisp/server/deps.py`. The guard's own docstring asks for exactly this: *"confirm this is a deliberate
enforcement point, then add it"*. The reasoning is in the set's comment, and it states why this is a
second **reader** rather than a second **opinion** — one authority, one load site (ADR-0006), three
consumers. **Found by running the canonical block**, which is the first place a new `authorize()`
consumer shows up.

**Updated — `test_authorization_parity.py`.** Its module docstring gains the ADR-0059 section;
`test_the_agent_consults_both_models` is rewritten from a **string scan** (`"policy_hard_deny(" in
src` — finding F79's weakness) to an **AST** pin of both paths' call sites; and
`test_a_bundle_denial_reaches_both_paths` is added, driving the real gate against the real
`authorize()` with a bundle loaded.

### 6.1 The non-vacuity probe found a defect in *this* guard

Ten probes, one per rule. **NV4 MISSED on the first run**, and it was a real defect in the instrument:

| probe | what it exposed |
|---|---|
| **NV4** | `test_rest_authorizes_as_the_same_principal_as_the_agent` **computed the REST principal itself** and compared it with the agent's — so it passed even when `deps.py` was mutated to pass `None`. It asserted a property of a **reconstruction**, not of the production call. Rewritten to capture the argument the real `_m2_denial` passes, via a spy on `executor_principal`. |

That is the **fourth sub-case** the corpus named last mission — *the instrument's subject was the
wrong thing* — one level down: the subject was right, but the **observation point** was a copy of the
code rather than the code.

A second, smaller self-observation: the first draft routed `_verdict` through a helper that catches
`HTTPException` — but `_verdict` **already** catches it and returns a tuple, so the wrapper could
never see a failure. A wrapper that catches the same exception as the callable it wraps is
non-falsifying by construction. Recorded in the test.

**Result: 10/10 CAUGHT, every file restored byte-identical (sha256).**

---

## 7. Residuals, named

1. **A bundle's `approve` level is inert on REST.** Measured: `{"write_file": "approve"}` leaves the
   REST verdict identical to no bundle in every mode. Composing it is Option A, rejected above; the
   agent path honours it and REST cannot, having no approver. Pinned as a property.
2. **The consult is conditional on a bundle**, so L1/L2/L3 are not consulted without one. L1 is
   unbounded for the local human, L3 needs a `restricted` sensitivity REST never passes, L2 is the
   default trust — but **workspace quarantine is a real pre-existing gap**: a quarantined workspace
   denies non-read tools on the agent path and does not on REST **unless a bundle is loaded**. That
   divergence predates this ADR.
3. **REST still reimplements L4.** The protected-path guard is the same predicate as `authorize()`'s
   L4, written inline. Pre-existing; kept because its message is pinned.
4. **Two bundle sources.** The env-var path is consumed; the publish route's held
   `app.state.policy_bundle` still has no decision reader.
5. **The three REST-only names now have an enforced rule and still have no agent operation.** L0's
   verdict on them is REST's alone. That is the honest end state.
6. **The desktop client is unaffected** — that is a *result*, not a residual: because the approval
   half is not composed, no route's status changes except when a bundle denies it. No client change
   is in scope.

---

## 8. Findings

| # | finding |
|---|---|
| **F94** | *A measurement's condition is part of its result.* ADR-0055's "0 path divergences of 36" was quoted for three missions as a property of the **paths**. It was a property of the paths **with no policy loaded** — a condition ADR-0058 then changed, in the same repository, without re-measuring. The number was never wrong; its **scope** was. Every "N of M" in this corpus should be read with the state it was measured in, and a landing that changes that state owes a re-measurement. |
| **F95** | *A brief's worked example can be the thing that decides the wrong way.* The brief's Option D table asserted *"L0 has no verdict on a non-agent name"*. Driven, `authorize()` returns `DENY(controlling_layer="local file")` for exactly that case. The table was the argument for D, and the argument was false. **Ninth consecutive brief with a specific claim the measurement contradicted.** |
| **F96** | *A guard can pass by observing a copy of the code instead of the code.* The first `test_rest_authorizes_as_the_same_principal_as_the_agent` rebuilt the production call and compared the results, so mutating `deps.py` did not falsify it. The non-vacuity probe caught it (NV4). The lesson is a **third** instrument-defect sub-case variant: it is not enough for the instrument's *subject* to be right — its **observation point** must be the production path. |
| **F97** | *A live range claim can survive fourteen ADR landings.* `CONTEXT.md`'s §7 documents table still reads `WISP_ARCHITECTURE_DECISIONS.md | **ADR-0001 … ADR-0044**` while the log is at 59. Also `CONTEXT.md:178` said 0057. Both are the F81 class — prose the pin guard cannot see — and both are being fixed in Deliverable 2. |
| **F98** | *Two records of one finding's status disagreed, and the live one was the stale one.* `CONTEXT.md` §0.0's G1 row read **`CLOSED by ADR-0055`** while §12's **Open items** table still read **`OPEN, measured`** — stale since the ADR-0055 landing, i.e. through five missions, and contradicting a row in the same file. §12 is the table a reader is told to consult, so the stale copy was the load-bearing one. Corrected. This is F82's shape one level up: not two *blocks* that should be identical, but two **rows describing the same finding** that nothing compares. |
| **F99** | *A brief cited two ledger rows that do not exist.* *"Update `WISP_MIGRATION_STATUS.md`'s G1 and M4 rows"* — the file contains **no** `G1` row and no governance-layer row (grep: zero matches for `G1`, `authorization parity`, `policy bundle`, `governance layer`). Its `M4` row is `ADR-0004 revisited`, a different M4. The third consecutive mission with a wrong citation into this file. The live target is `CONTEXT.md` §12's **E** and **G1** rows, which were updated instead. |

---

## 9. Verification

| check | result |
|---|---|
| the new guard | **16 passed** |
| non-vacuity | **10/10 CAUGHT**, every file restored byte-identical (sha256) |
| the no-bundle differential | **40/40 identical — status and detail** |
| the M4 guard + the inverted pin + the parity ratchet | **89 passed** |
| regression (22 files, incl. the WS suites and the policy precedence file) | **354 passed, 0 failed** |
| the canonical block (51 files, both blocks identical) | **1453 tests — 1452 passed, 1 failed (F38)** |
| `ruff check wisp/` | **11 errors — unchanged** (F71); changed files clean |
| `mypy` | not re-run — stated as an argument, not a measurement |

**The canonical block grew by 16 (the new guard's tests) and both headings were re-measured in the
same change (F85).** The block also now includes `test_m4_governance_wiring.py` (added last mission,
F93) and the new composition guard, so the guards for the two M4 landings are both in a block that
runs.

**F38 is `tests/test_node_identity.py::TestANodeReferencesItsWorkUnit::test_a_parallel_round_is_journaled_as_one_exchange_per_call`**
— confirmed by driving the block and by `CONTEXT.md:100`, which names it. Not re-opened.
