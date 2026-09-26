# PHASE — ACCEPTANCE GATE ENABLEMENT (Deliverable 1)

**Deliverable:** **ADR-0054** — the acceptance gate consumes `verdict_keys_on_declared` at the engine's
pre-`done` gate, bounded and defaulting OFF.
**Baseline:** `c03eee3` (§0 records `5898e0e`; the tip is its docs follow-up). **No drift** — the working
tree was exactly the 29 WIP entries in `CONTEXT.md` §8.
**Predecessor:** `PHASE_CRITERIA_SOURCE.md` (ADR-0053), whose §10 named the missing consumer.

---

## 0. Baseline check — no drift

| Check | Result |
|---|---|
| `git rev-parse --short HEAD` | `c03eee3`; `CONTEXT.md` §0 records `5898e0e` (the feature commit, per the convention) |
| `git status --short \| wc -l` | **29** — exactly §8's WIP list, nothing else |
| `origin/main` | `af3a89a` (not pushed) |

**No drift. Proceeded.**

---

## 1. What was produced

| Artefact | What it is |
|---|---|
| **ADR-0054** in `WISP_ARCHITECTURE_DECISIONS.md` (53 → 54) + its index row | The consumer, the enforcement mechanism, the measurement, eight rejected alternatives. |
| `wisp/core/turn_criteria.py` | `DeclaredCriteriaGate` (the read-only callable the engine asks), `declared_criteria_gate()` (the builder), `compose_declared_nudge()` (the intervention text), and probe reuse on the verdict path. |
| `wisp/core/stateless.py` | `declared_gate` on `turn()`/`_turn_inner()`, and the bounded gate block at the pre-`done` site. |
| `wisp/core/runtime.py` | The `acceptance_gate` flag read, the gate built **before** the turn, passed only when present, and the cached measurement reused at the verdict site. |
| `wisp/config.py` | `acceptance_gate` / `WISP_ACCEPTANCE_GATE`, default `False`. |
| `scripts/acceptance_gate_population.py` | **The instrument** — the model enumeration, committed so the residual re-runs. |
| `tests/reliability/test_acceptance_gate_enablement.py` | **25 tests**, 5 classes, one driving a **real turn**; **3/3 non-vacuity probes caught**. |
| `tests/reliability/test_gate_enablement_contract.py` | **The ADR-0051 tripwire fired and was replaced by its inverse** (§4). |
| `CURRENT_AUTHORITIES.md` | Re-pinned again (§5). |

---

## 2. The decision

> **R1 — the consumer.** The engine's pre-`done` gate asks a **read-only callable** the runtime builds.
> It receives no criteria, no specs, no probe — the ADR-0036 shape. **R2 — the condition.**
> `verdict_keys_on_declared` asked of a declared-only set; not redefined. **R3 — the probe.** Taken at
> the gate (the workspace is final there), cached, and **reused** at the verdict site — one probe per
> turn. **R4 — delay, never a veto.** ADR-0036's bounded replan model, **sharing the turn's extension
> budget**. **R5 — fail open.** **R6 — the flag.** `acceptance_gate` / `WISP_ACCEPTANCE_GATE`, default
> **OFF**, read once, **dependent** on `turn_criteria_source`. **R7 — the measurement** (§3). **R8 — the
> three non-violations**, asserted. **R9 — no new authority.**

**The question the brief said must be answered, answered.** *"Does the withholding happen before the
verdict is computed (moving the probe into the engine loop), or does 'enforcement' mean something else?"*
— **Before, and the probe goes to the gate rather than into the loop.** A gate that acts after `done`
withholds nothing, so "enable the gate" had to mean asking *before* `done`. The probe is not moved into
the loop body: it is taken by the runtime's callable **at the gate**, which is the moment the workspace
is final — and the measurement is cached so the verdict site does not run it again. **The engine gains a
predicate and a text, never a criteria source.**

**The engine is untouched in its authority.** It already received a read-only predicate for stagnation
(ADR-0036); it now receives a second one, from a different concern, at the same site, after both existing
gates so neither loses its behaviour and the turn is never double-nudged.

---

## 3. The measurement — the contract is **not** satisfied, and that is measured

ADR-0051 R4 requires **≥ 2 capable models**. `scripts/acceptance_gate_population.py` enumerates all 13
models the local daemon serves (2026-09-25):

```text
capable 1 · degenerate 2 · retired 5 · paywalled 5 · unclassified 0
```

| class | models |
|---|---|
| **capable (1)** | `nemotron-3-ultra:cloud` — recorded 5/5 schema-valid tool calls |
| degenerate (2) | `llama3.2:3b`, `qwen2.5:0.5b` — recorded as unable to drive the tool surface |
| retired (5) | `qwen3.5:cloud`, `deepseek-v4-flash:cloud`, `gemini-3-flash-preview:cloud`, `kimi-k2.5:cloud`, `glm-5.1:cloud` |
| paywalled (5) | `glm-5.2:cloud`, `kimi-k3:cloud`, `minimax-m2.7:cloud`, `deepseek-v4-pro:cloud`, `kimi-k2.6:cloud` |

**So the `GOAL_MET` rate ADR-0051 R2 specifies is not produced** — a rate over one model is exactly the
model-dependent artefact ADR-0051 §Problem already measured (64.3% vs 100%). **What would produce it:** a
second capable model that is neither retired nor paywalled, or a provider key.

**What IS produced — the mechanism, driven end to end through a real turn:**

| observation | result |
|---|---|
| a failing declaration with the gate ON | withholds `done` **exactly twice** (the shared budget), emits the nudge, **then finishes** |
| a satisfied declaration | no withholding |
| the flag OFF | no withholding; the failure is recorded as before |
| the source flag OFF, the gate flag ON | **inert** — the dependency holds |

That is the whole of "the gate has something to gate on, and acting on it is bounded and honest". Only the
*population* is missing.

---

## 4. The ADR-0051 tripwire fired — and was replaced by its inverse

`tests/reliability/test_gate_enablement_contract.py::TestNoUnwiredAcceptanceGateFlagWasAdded` asserted
`acceptance_gate` was **absent** from production. **It failed** when this ADR added the flag — the signal
its own docstring predicted.

It was **rewritten, not weakened** (the repo's rule for a guard that pinned the old state). The property
it now defends: the flag exists, **defaults OFF**, is read once, is **dependent** on the criteria source,
and its env var is named. Default-OFF is not tidiness — it is this ADR's measured conclusion, and the
class's docstring says so.

## 5. `CURRENT_AUTHORITIES.md` was re-pinned — and the guard's weakness recurred

`runtime.py` moved again, and the pin guard failed. **It caught one stale pin of five** — the same defect
ADR-0053 §6 recorded, one iteration later: it checks a pinned line is *non-blank*, not that it carries the
symbol. **Four of the five re-pins were again found by reading.** This is Deliverable 2's subject, and
this recurrence is its strongest evidence.

---

## 6. Non-vacuity

| Probe | Break | Result |
|---|---|---|
| **NV1** | the engine's gate never runs | **CAUGHT** — 1 failed |
| **NV2** | the gate never withholds | **CAUGHT** — 2 failed |
| **NV3** | the dependency on the source is dropped | **CAUGHT** — 1 failed |
| CONTROL | — | green (25 passed) |

`files left byte-identical: 2/2`.

---

## 7. Regression

```
env -u PYTHONPATH .venv/bin/python -m pytest <45 files> -q -p no:cacheprovider --tb=no -rf
```

| | |
|---|---|
| **Result** | **1314 tests — 1313 passed, 1 failed** (145.63 s) |
| **The one failure** | `test_node_identity.py::…::test_a_parallel_round_is_journaled_as_one_exchange_per_call` — **F38**, pre-existing |
| **New failures** | **none** — the failure set is identical in both directions |
| **Delta** | **+25** — all in `test_acceptance_gate_enablement.py` |

*(An intermediate run showed a second failure — the pin guard, §5 — fixed and superseded by the run
above. The intermediate is named rather than hidden.)*

**Gates, measured not asserted** (F71): `ruff check wisp/` → **11 errors**, unchanged. Three unused
imports in the new test file and two `F541`s in the new instrument were found by ruff and fixed, so this
phase adds none.

The full suite was **not** run: F36 says it cannot run in one process on this host. The method is weaker
than a two-run intersection and is stated as such.

---

## 8. The three non-violations, asserted

| Non-violation | How it is asserted |
|---|---|
| `turn_succeeded` unchanged | `test_turn_succeeded_is_still_terminal_evidence`, **and** `test_the_withheld_turn_still_ends_and_is_not_a_second_success_signal` — the withheld turn still ends with `done`, so the gate is not a `turn_gated` flag |
| `VerificationFloorGuard` unchanged | `test_the_floor_guards_own_semantics_are_unchanged` — `rejection()`'s three cases |
| `goal.PRECEDENCE` unchanged in content and count | `test_precedence_is_unchanged_in_content_and_count` — 8 rows, ratified cells driven |

## 9. Residuals, named

1. **The population is short by one capable model** — the only thing between the contract and the
   enablement decision, and an *environment* fact, not a design one.
2. **The declared command runs once per declared turn** (at the gate, reused at the verdict site). With
   `command_succeeds` that is a real cost, and it is why the flag defaults OFF.
3. **The gate shares the stagnation gate's budget**, so a stagnating turn can spend the declared gate's
   extensions. Intended, and stated rather than discovered.
4. **`stagnation_gate` is untouched** (ADR-0037). Both gates are wired, both default OFF, independently.

## 10. Honest limits

- **The withholding is demonstrated on one model's worth of nothing** — the end-to-end test uses a
  scripted provider, not a real model. It proves the *mechanism* (bounded, delay-not-veto, finishes), not
  the *rate*.
- **The gate is a delay, not a repair.** It gives the model two more rounds; whether a real model uses
  them to satisfy a declared criterion is exactly what the missing population would measure.
- **`DeclaredCriteriaGate` exposes two read-only attributes** (`last_measurement`, `evaluations`) beyond
  `__call__`, pinned by test. `evaluations` is observability only — nothing reads it in production.
- **`mypy` was not re-run.** Three `wisp/` files changed; the count is 1844 at HEAD and this phase does
  not claim it moved. Stating that is weaker than measuring it.

---

# §11 — Deliverable 2: the pin-guard fix

**Why.** ADR-0053 §6 recorded the sixth instrument-defect instance: the pin guard's name (*"every pin
names a real line"*) was stronger than its check, which asserted a pinned line was **non-blank**. It
caught **one of five** stale pins when `runtime.py` moved; four were found by reading. It recurred in
this mission's Deliverable 1.

**The fix — the assertion is now on the pinned CONTENT.**

```python
# before — the line exists and is not blank
assert lines[int(start) - 1].strip()

# after — the window around the pin contains an identifier the page's prose names
spans = [s for s in re.findall(r"`([^`]+)`", raw) if not PIN_RE.fullmatch(f"`{s}`")]
expected = {m for s in spans for m in IDENT_RE.findall(s)} - path_noise
window = " ".join(lines[max(0, lo - PIN_WINDOW):hi + PIN_WINDOW + 1])
assert any(name in window for name in expected)
```

The page writes `symbol` … (`path:line`), so the **other backticked spans on the line** are the
expectation set. Three boundaries, all stated in the guard's docstring rather than left implicit:

| Boundary | Rule |
|---|---|
| **The window** | `±2` lines, so a pin may legitimately drift a little when code moves around it. A drift of 6 lines **fails** (NV1). |
| **A range pin** | the whole span is the window, plus `±2`. Checking only the start of a range would call a range stale whose content is one line below it — which is exactly what `progress.py:50` was. |
| **A line with no other backticked span** | not content-checkable (a §3 matrix row names its result in plain prose). Those pins are counted separately, and `assert checkable >= 15` fails if the page's pin format drifts and the check goes vacuous (NV3). |

**The fix found nine stale pins the old guard had passed.** Eight were genuinely stale — all in
`wisp/core/convergence.py`, drifted since ADR-0050 — and every one landed on a **non-blank** line, which
is precisely why the old check passed them:

| page claim | old pin | real line |
|---|---|---|
| the attempt journal line `{"kind":"attempt"}` | `convergence.py:931-932` | **1323** |
| `verdict`/`unmet`/`evidence_ids` in the attempt journal | `convergence.py:937-938` | **1333-1334** |
| `progress`, `progress_signals` | `convergence.py:909-910`, `:944` | **1305-1306**, **1340-1341** |
| `measurement_observations` + `measurement_digest` over `WITNESS_FIELDS` | `convergence.py:796-809` | **1192-1205** |
| `AttemptRecord.rung`, `.directive`, `.failure_class` | `convergence.py:928-929`, `:942` | **1277-1278**, **1299** |
| the last `AttemptRecord.goal_state` | `convergence.py:885` | **1281** |

The ninth (`progress.py:50`) was a range artifact, not staleness: the class is at 50 and its members at
61-63, so the pin became `50-63` **and** the check learned to honour ranges.

**Non-vacuity** — the brief's probe plus two more, each restoring the tree byte-identically:

| probe | break | result |
|---|---|---|
| **NV1** | a pinned symbol drifts 6 lines | **CAUGHT** — 1 failed |
| **NV2** | a stale pin is restored in the page | **CAUGHT** — 1 failed |
| **NV3** | the page's pin format drifts (the floor) | **CAUGHT** — 3 failed |

### The probe was wrong first — the same class, one level up

**NV3 MISSED on its first run**, and the cause was the *probe*, not the guard: it called
`str.replace(old, new, 1)`, so only the **first** pin was reformatted and the other ~36 stayed valid —
the guard correctly passed. **A probe that does not falsify is a finding that the test is not testing
what you think** — and here the probe was the test. Fixed by giving `probe()` a `count` parameter
(`-1` = all), and the defect is recorded in that function's docstring so it is not "simplified" back.

That is the third time in two missions that a non-falsifying probe turned out to be the instrument's own
defect (ADR-0052's NV1, ADR-0053's NV1, and now this one). It is the discipline `CONTEXT.md` §10 now
names, applied to the tooling that enforces it.

**`CONTEXT.md` §10's sixth instance is marked CLOSED** with a citation to this section.
