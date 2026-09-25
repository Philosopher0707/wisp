# PHASE — GATE ENABLEMENT (Deliverable 1)

**Deliverable:** **ADR-0051** — the acceptance gate's enablement contract is a **non-redundancy
precondition**, not a rate; and the `INCONCLUSIVE` rate is **not a function of the gate**.
**Baseline:** `40cfa52` (= `HEAD` after `3f9e639`; no drift — the working tree is exactly the 29 WIP
entries in `CONTEXT.md` §8). **Predecessors:** `PHASE_STRUCTURED_CRITERIA.md` (ADR-0050),
`PHASE_PRECEDENCE_CORRECTION.md` (ADR-0049).

---

## 0. Baseline check — no drift

| Check | Result |
|---|---|
| `git rev-parse HEAD` | `40cfa52` — the docs handoff that points at `3f9e639`, i.e. the baseline the brief names |
| branch / remote | `main`; `origin/main` = `af3a89a` (not pushed) |
| `git status --short` | **29** entries — exactly `CONTEXT.md` §8's WIP list, and nothing else |
| environment | `.venv` python 3.12.8, pytest 9.0.2, `jsonschema` 4.26.0; Ollama reachable; `ruff`/`mypy` at their pinned paths |

**No drift. Proceeded.**

---

## 1. What was produced

| Artefact | What it is |
|---|---|
| **ADR-0051** in `WISP_ARCHITECTURE_DECISIONS.md` (50 → 51) + its index row | Amends ADR-0016's **3b condition**. R1–R9, the two-conjunct contract, the measured tables, ten rejected alternatives. |
| `scripts/gate_enablement_measurement.py` | **The instrument.** Three deterministic offline measurements: the projection, the producer search, the population rates. |
| `scripts/gate_enablement_population.json` | The 28 raw per-turn records, consolidated — so the rate is re-derivable **from the repository**. |
| `tests/reliability/test_gate_enablement_contract.py` | **12 tests.** The projection (R1's subject), the three non-violations (R8), and a **tripwire** that fails the moment an unwired flag is added. |
| `.workbuddy-ai/memory/post-m13-gate-enablement/` | The working evidence (outputs, the non-vacuity proof). Untracked by design; the *instrument* is tracked. |

---

## 2. The decision

> **R1 — the non-redundancy precondition.** The gate may be enabled only when the turn path's
> **required-criteria set contains at least one required criterion not derivable from
> `VerificationFloorGuard`'s own state.** Until then the gate is not enabled, **and the flag is not added
> to `config.py`** — a flag whose gate cannot fire is a written-but-unwired control, which this
> repository's own audit named as its dominant pathology. **Measured today: UNMET.**

R2 — the measure is **changed** to the `GOAL_MET` rate on a **declared**-objective population, with the
false-completion rate pinned at 0. R3 — the population must be declared and **never pooled**. R4 — ≥2
capable models, ≥10 declared objectives, ≥30 turns, 0 excluded. R5 — `FALSE_SUCCESS_AFTER = 0`. R6 —
replay 100%. R7 — `acceptance_gate` / `WISP_ACCEPTANCE_GATE`, default **OFF**, read once. R8 — the three
non-violations, asserted. R9 — rollback.

**Why the `INCONCLUSIVE` rate was rejected as the measure**, in two independent parts:

1. **It is not a property of the gate.** Re-derived from the primary records:

| provider / model | turns | PASS | FAIL | INCONCLUSIVE | rate | false successes |
|---|---:|---:|---:|---:|---:|---:|
| `nemotron-3-ultra:cloud` (550B) | 14 | 3 | 2 | 9 | **64.3%** | 0 |
| `llama3.2:3b` (3.2B) | 7 | 0 | 0 | 7 | **100%** | 0 |
| `qwen2.5:0.5b` (0.49B) | 7 | 0 | 0 | 7 | **100%** | 0 |
| **pooled** | 28 | 3 | 2 | 23 | **82.1%** | **0** |

The pooled figure describes no population that exists; the stratification *is* the result.
2. **It is not a function of the gate.** The gate **consumes** `floor_guard_verdict`; it computes no
verdict. Its only effect is to add provider rounds, so enabling it **cannot raise** the rate — it can only
let a failing turn be repaired into a passing one. A measure an intervention cannot move adversely is not
a safety measure *for that intervention*.

---

## 3. The corpus, and the measurement

### 3.1 The projection — the decisive new evidence

The turn path's verdict is not an independent evaluation:

```
runtime.py:1189-1190   _acceptance = floor_guard_verdict(_guard_for_goal).verdict
verification.py:236    floor_guard_criteria()   <- the ONE producer on the turn path
verification.py:252    check = (not guard.wrote_code) or guard.resolved()
stateless.py:911       guard.rejection()        <- ALREADY wired at the pre-`done` gate
```

Driven over the reachable guard state space — `enabled × wrote_code × verify_ok_after_edit × nudges_used ×
turns_used` = **192 states**:

| Claim | Measured |
|---|---|
| `verdict == FAIL` ⟺ the guard's own blocking condition | **0 disagreements / 192** |
| `verdict != PASS` while the guard is **not** blocking | **144** — of which **96** have the guard **disabled**, **48** are read-only turns |
| `verdict == FAIL` after the guard has already surrendered | **8** |

The three consequences, named:

| Gate design | Measured consequence |
|---|---|
| keyed on `FAIL` | **redundant** — the identical condition `rejection()` already tests, with the identical nudge and budget |
| keyed on `!= PASS` | **harmful** — withholds `done` on 144/192 states, including every read-only turn and every turn of a *disabled* guard |
| `FAIL` after surrender | a **second budget** on the same condition, after the first was spent |

### 3.2 The producer search — which producers are on the turn path

| Producer | On the turn path? |
|---|---|
| `wisp/core/verification.py` — `floor_guard_criteria()` | **yes — the only one** |
| `wisp/core/convergence.py` — the objective-derived criteria (5 sites) | no — they reach `converge_on_objective`, where `ConvergenceController` evaluates them itself (ADR-0045) |

**The objective-level loop is already the gate on the real criteria.** The turn-level gate would be a
second, redundant one.

### 3.3 The population

Re-derived by the committed instrument from `scripts/gate_enablement_population.json` — see §2's table.
`FALSE_SUCCESS_AFTER = 0` over all 28 turns, checked mechanically (a `PASS` whose verify tool carried a
non-zero exit marker).

---

## 4. Non-vacuity

Four probes, each breaking one property; the tree is restored **byte-identically** (sha256 verified) and
`__pycache__` purged on both sides:

| Probe | Break | Result |
|---|---|---|
| **NV1** the projection | `check=lambda _payloads: True` | **CAUGHT** — 3 failed |
| **NV2** precedence row count | delete `PRECEDENCE`'s row 7 | **CAUGHT** — 1 failed |
| **NV3** the ratified row-4 cell | drop `and acceptance != "pass"` | **CAUGHT** — 1 failed |
| **NV4** the unwired-flag tripwire | add `WISP_ACCEPTANCE_GATE` to `config.py` | **CAUGHT** — 1 error |
| CONTROL, before and after | — | green (12 passed) |

`files left byte-identical: 3/3`.

---

## 5. Regression

```
env -u PYTHONPATH .venv/bin/python -m pytest <39 files> -q -p no:cacheprovider --tb=no -rf
```

| | |
|---|---|
| **Result** | **1209 tests — 1208 passed, 1 failed** (137.30 s) |
| **The one failure** | `test_node_identity.py::…::test_a_parallel_round_is_journaled_as_one_exchange_per_call` — **F38**, pre-existing |
| **New failures** | **none** — the failure set is identical in both directions |
| **Now-passing** | none |
| **Delta** | **+12** — all in `test_gate_enablement_contract.py` |

The full suite was **not** run: F36 says it cannot run in one process on this host. The method is weaker
than a two-run intersection and is stated as such.

**Gates, measured not asserted** (F71): `ruff check wisp/` → **11 errors**, unchanged by this phase; the
new test file is clean. `mypy` was **not** re-run for this deliverable — it is 1844 errors at HEAD and this
change touches no `wisp/` source file, so the count cannot have moved; stating that is weaker than
measuring it and is recorded as such.

---

## 6. Findings — reported, not repaired

### F75 — the previous mission's instrument was never committed

`PHASE_STRUCTURED_CRITERIA.md` §7 states: *"the instrument is committed so it can be re-run."* **Measured,
it is not.**

```
.gitignore:98                      .workbuddy-ai/
git ls-files .workbuddy-ai/        0 files
git ls-files | grep -i corpus      (no instrument — only a report and a test)
```

So the ADR-0050 corpus (`corpus.py`, `corpus-output.txt`) exists **only** in the gitignored agent
workspace, and the instruction to re-run it cannot be followed from a clone. The same is true of every
prior mission's instruments under `.workbuddy-ai/memory/`.

**Not repaired** (that is ADR-0050's record, and repairing it is not this deliverable's scope). **What
this deliverable does instead is not repeat it:** its instrument is `scripts/gate_enablement_measurement.py`
— tracked, and `scripts/` is the repository's committed convention (`scripts/next_*.py` are tracked). The
general lesson is recorded in `CONTEXT.md` §0.0.9: **an instrument that cannot be committed is not a
re-runnable measurement.**

### F76 — the brief's own framing assumed the rate was the question

The brief's "what good output looks like" sketches a decision that picks a ceiling on the `INCONCLUSIVE`
rate for a declared population. **Driven, that framing does not survive contact with the code**: the gate
cannot move the rate (§2), and on today's criteria set it has nothing to gate on (§3.1). The decision is
therefore a **precondition**, not a number. This is recorded because the brief asks for exactly this
treatment of its own claims — the previous brief's numbering claim was inverted, and this one's premise
was incomplete in a different way.

---

## 7. What the ADR does **not** decide

- **Enabling the gate.** R1 is unmet, so there is nothing to enable.
- **The shape of the criteria source that would satisfy R1.** That is the change that makes the gate
  meaningful, and it is its own ADR. The obvious candidate is putting ADR-0048/0050's derived criteria on
  the turn path; the guard test will fail when that happens, which is the intended signal.
- **`stagnation_gate`** (ADR-0037 forbids enabling it without a superseding ADR) and **`recovery_ladder`**
  (ADR-0026/0035). Different concerns, different predicates, different records.
- **The F38 test**, `productive_continuations` tuning, and ADR-0050's two-flag composition.

## 8. Honest limits

- **The gate was never exercised.** It is not enabled, so there are no interventions or surrenders to
  count. Reporting those as zero would be manufacturing a metric; they are **NOT MEASURED**.
- **The projection is proven over the guard's state space, not over all inputs.** The state space is the
  guard's fields; a future guard field could in principle break the equivalence, which is why the guard
  test re-derives it rather than asserting the count.
- **The population is the prior mission's, re-derived.** No new live turns were taken. The *instrument* is
  new and committed; the *data* is the recorded JSON, consolidated into `scripts/`. `n = 14` for the only
  informative model, and the point estimate is not a stable production rate.
- **`mypy` was not re-run** (§5). The claim "the count cannot have moved" is an argument, not a
  measurement.
- **R4's numbers are a judgement.** ≥2 models / ≥10 objectives / ≥30 turns is chosen so no single turn
  moves the rate by more than ~3 points. It is a decision, and it is re-statable — not a measurement.
