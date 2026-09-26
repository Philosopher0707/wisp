# PHASE — THE CRITERIA SOURCE ON THE TURN PATH (Deliverable 1)

**Deliverable:** **ADR-0053** — the turn path's required-criteria set carries the objective's declared
criteria; the gate keys on a `FAIL` the floor guard does not enforce.
**Baseline:** `3ed402a` (§0 records `805eca8`; the tip is its docs follow-up). **No drift** — the working
tree was exactly the 29 WIP entries in `CONTEXT.md` §8.
**Predecessor:** `PHASE_GATE_ENABLEMENT.md` (ADR-0051), whose §7 named this change as its own ADR.

---

## 0. Baseline check — no drift

| Check | Result |
|---|---|
| `git rev-parse --short HEAD` | `3ed402a`; `CONTEXT.md` §0 records `805eca8` (the feature commit, per the convention) |
| `git status --short \| wc -l` | **29** — exactly §8's WIP list, nothing else |
| `origin/main` | `af3a89a` (not pushed) |

**No drift. Proceeded.**

---

## 1. What was produced

| Artefact | What it is |
|---|---|
| **ADR-0053** in `WISP_ARCHITECTURE_DECISIONS.md` (52 → 53) + its index row | The mechanism, the gate's condition, the routing, the two-flag composition, the measurement, nine rejected alternatives. |
| `wisp/core/turn_criteria.py` | **The one producer** of the turn's criteria set. Re-implements nothing: it consumes `floor_guard_criteria`/`floor_guard_evidence`, `explain_acceptance(..., use_declaration=True)`, `CommandProbe.measure` and `acceptance.evaluate`. |
| `wisp/core/runtime.py` | The flag read (once, at `run_turn`'s entry) and the verdict site's declared branch. |
| `wisp/config.py` | `turn_criteria_source` / `WISP_TURN_CRITERIA_SOURCE`, default `False` — the flag spec, the field, the `get_setting` read. |
| `scripts/turn_criteria_measurement.py` | **The instrument** — committed, so the measurement re-runs from the repo (F75's lesson). |
| `tests/reliability/test_criteria_source_on_turn_path.py` | **25 tests**, 6 classes; **3/3 non-vacuity probes caught**. |
| `CURRENT_AUTHORITIES.md` | **Re-pinned** — `runtime.py`'s line numbers moved and its guard caught it. |

---

## 2. The decision

> **R1 — the source.** `floor_guard_criteria(guard) ∪ explain_acceptance(prompt, workspace,
> use_declaration=True).criteria` — **the same derivation the objective-level loop uses**, so the two
> levels cannot disagree about what a declaration means. **R2 — where it reaches the turn:**
> `AgentRuntime.run_turn`'s verdict site, via one new module. **R3 — the evidence:**
> `CommandProbe(declaration.specs).measure(workspace)` — the ONE probe, bounded by `spec.timeout_s`.
> **R4 — the gate's condition:** `verdict == FAIL` **and every criterion it names is a non-floor one**.
> **R5 — the routing:** through neither the `done` gate nor the ladder; through `derive_goal_state`.
> **R6 — a rejected declaration is loud** (propagates; never a floor-only fallback). **R7 — the flag:**
> `turn_criteria_source` / `WISP_TURN_CRITERIA_SOURCE`, default **OFF**, read **once**. **R8 — the
> two-flag composition: independent** of `WISP_CRITERIA_STRUCTURED_DECLARATION`. **R9 — no new
> authority.** **R10 — the three non-violations**, asserted.

**Alternatives rejected** (with reasons in the ADR): a new parameter on `core.turn()` (widens a signature
ADR-0009 pins, for a concern the engine does not act on — the verdict is computed *after* `done`); a new
field on a turn-request object (no such object; the prompt **is** the objective); resolving it inside
`stateless.py` (no workspace probe there, and it would give the engine a second completion authority);
gating on `!= PASS` (144/192 states); gating on any `FAIL` (that *is* the floor guard's condition);
falling back on a rejected declaration (ADR-0050 R4's forbidden downgrade); coupling the flags (two read
sites for one concern); making the engine withhold `done` (moves a subprocess into the loop — the
residual); consulting the prose grammar (ADR-0048 R7's negation-blind inference).

---

## 3. The measurement — the gate has something to gate on

Driven over six cases (`scripts/turn_criteria_measurement.py`; OFF is today's behaviour):

| case | OFF verdict | OFF goal | ON verdict | ON goal | gate? |
|---|---|---|---|---|---|
| plain prompt, no mutation | `inconclusive` | `goal_unverified` | `inconclusive` | `goal_unverified` | False |
| plain prompt, verified mutation | `pass` | `goal_met` | `pass` | `goal_met` | False |
| declared symbol **present**, no mutation | `inconclusive` | `goal_unverified` | `inconclusive` | `goal_unverified` | False |
| **declared symbol ABSENT, no mutation** | `inconclusive` | `goal_unverified` | **`fail`** | **`goal_failed`** | **True** |
| **declared symbol ABSENT, verified mutation** | **`pass`** | **`goal_met`** | **`fail`** | **`goal_failed`** | **True** |
| declared symbol ABSENT, FAILED verification | `fail` | `goal_failed` | `fail` | `goal_failed` | **False** |

**Row 5 is the point of the whole ADR.** A mutation-verified turn satisfies the floor guard *completely* —
`rejection()` is `None`, the floor-only verdict is `PASS`, the floor-only goal is `GOAL_MET` — while the
declared criterion fails. **The two conditions are not the same condition.**

**Row 6 is the control that keeps R4 honest:** when the `FAIL` is the floor guard's own,
`verdict_keys_on_declared` returns **False**, so a gate keyed on it cannot duplicate `rejection()`.

The criteria sets: OFF → `['floor:verification']`; ON → `['floor:verification', 'declared:symbol0']`.

### 3.1 The measure, and why the objective-level measure does not transfer unchanged

ADR-0051 R2 chose the `GOAL_MET` rate on a declared-objective population, with `FALSE_SUCCESS_AFTER = 0`.
At the turn level **the form transfers and the population does not**:

- The objective-level rate is over *attempts within a run*, between which the controller picks a rung. The
  turn level has no rung — a declared failure is terminal for the turn (R5) — so the rate is over *turns*
  whose prompts carried a declaration.
- `FALSE_SUCCESS_AFTER = 0` **does** transfer unchanged, and is the invariant that matters.

**Produced:** the verdict/goal table above. **Not produced:** a `GOAL_MET` rate over a declared turn
population, because **no such population exists** — the declaration flag is OFF in every production
caller and the corpus holds 13 objectives. What would produce it: turns whose prompts carry declarations,
accumulated with the flag on. Stated as a residual, not papered over.

---

## 4. Non-vacuity

Three probes, each restoring the pre-ADR behaviour in one place; the tree is restored
**byte-identically** (sha256 verified) and `__pycache__` purged on both sides:

| Probe | Break | Result |
|---|---|---|
| **NV1** | the union is removed (return `base`) | **CAUGHT** — 4 failed |
| **NV2** | the gate's condition widens to any `FAIL` | **CAUGHT** — 1 failed |
| **NV3** | the flag is ignored (OFF also declares) | **CAUGHT** — 5 failed |
| CONTROL | — | green (25 passed) |

`file left byte-identical: yes`.

---

## 5. Regression

```
env -u PYTHONPATH .venv/bin/python -m pytest <43 files> -q -p no:cacheprovider --tb=no -rf
```

| | |
|---|---|
| **Result** | **1289 tests — 1288 passed, 1 failed** (141.76 s) |
| **The one failure** | `test_node_identity.py::…::test_a_parallel_round_is_journaled_as_one_exchange_per_call` — **F38**, pre-existing |
| **New failures** | **none** — the failure set is identical in both directions |
| **Delta** | **+25** — all in `test_criteria_source_on_turn_path.py` |

*(An intermediate run showed a **second** failure — the derived-document pin guard, §6 — which is fixed
and the final run above is the record. The intermediate is named rather than hidden.)*

**Gates, measured not asserted** (F71): `ruff check wisp/` → **11 errors**, unchanged; both new files are
clean. Two `F541`s in the new instrument were found by ruff and fixed, so this phase adds none.

The full suite was **not** run: F36 says it cannot run in one process on this host. The method is weaker
than a two-run intersection and is stated as such.

---

## 6. `CURRENT_AUTHORITIES.md` was re-pinned — and its guard is weaker than it reads

`wisp/core/runtime.py` moved, so `CURRENT_AUTHORITIES.md`'s pins went stale and
`test_current_authorities_pins.py::TestEveryPinResolves::test_every_pin_names_a_real_line` **failed** —
correctly. The pins were re-pinned (its documented regeneration step 2) and every one was verified to
carry its symbol by reading the line back:

```
 964: turn_succeeded = _goal_outcome is TerminalOutcome.SUCCEEDED
1302: "terminal_outcome": _goal_outcome.value,
1317: "turn_succeeded": bool(turn_succeeded),
1303: "acceptance_verdict": str(
1299: journal_events.append(SessionEvent.goal_state_event(
```

**Finding — the pin guard checks for a BLANK line, not for the right line.** It reported exactly **one**
stale pin (`:1271`, whose line had become blank) and passed **four others** that had also moved, because
they landed on non-blank lines. So the guard's name (*"every pin names a real line"*) is stronger than
its check: it catches a pin pointing at whitespace, not a pin pointing at the wrong code. Four of the
five re-pins were found by reading, not by the guard.

This is the **sixth instance** of the instrument-defect class Deliverable 2 names — *a check that passes
for a reason unrelated to its claim*. **Reported, not repaired**: widening the guard to check the
symbol at the pin (not just that a line exists) is its own change, and this phase's authority is the
criteria source. Recorded in `CONTEXT.md` §10 with the other five.

---

## 7. The three non-violations, asserted

| Non-violation | How it is asserted |
|---|---|
| `turn_succeeded` unchanged | `test_turn_succeeded_is_still_terminal_evidence` — it is still a projection of `terminal_outcome_from_evidence` |
| `VerificationFloorGuard` unchanged | `test_the_floor_guards_own_semantics_are_unchanged` — `rejection()`'s three cases, and `FLOOR_CRITERION_ID` is still `"floor:verification"` |
| `goal.PRECEDENCE` unchanged in content and count | `test_precedence_is_unchanged_in_content_and_count` — 8 rows, and three ratified cells driven |

Plus `test_the_floor_path_and_the_new_source_agree_when_nothing_is_declared`: over the whole
`(enabled × wrote_code × verify)` guard space, a prompt with **no** declaration produces the **same**
verdict from the new source as from `floor_guard_verdict` — the strongest available form of "every caller
that does not set the flag sees today's behaviour".

---

## 8. The two-flag composition, decided

**Independent.** `WISP_TURN_CRITERIA_SOURCE` does **not** require
`WISP_CRITERIA_STRUCTURED_DECLARATION`. That flag gates the *objective-level* derivation
(`explain_acceptance(use_declaration=)` inside the convergence loop); the turn path makes its own call.
Coupling them would make the turn path's behaviour depend on a flag read at a different composition
point — two read sites for one concern, which is the disagreement ADR-0002 forbids. Pinned by
`test_it_is_not_coupled_to_the_objective_level_flag`, which asserts `turn_criteria.py` mentions neither
the flag nor its name. This answers ADR-0050's follow-up question 1 **for the turn path**.

---

## 9. Residuals, named

1. **No declared turn population exists**, so ADR-0051 R2's `GOAL_MET` rate cannot be produced yet. §3.1
   states the form of the measure; the population is the missing input.
2. **A declared failure does not produce a replan.** It is recorded (`GOAL_FAILED`), not repaired within
   the turn, because the verdict is computed *after* `done`. Making it repairable means moving the probe
   into the engine loop — a separate decision with a real cost (a subprocess per iteration).
3. **`command_succeeds` on the turn path runs the declared command after `done`**, once per declared turn,
   bounded by `spec.timeout_s`. That cost is why the flag defaults OFF.
4. **The `done`-withholding gate is unchanged**, so this ADR does not make the acceptance gate *enforce*
   anything. It satisfies R1's **precondition**, which is what makes ADR-0051's contract askable — not
   satisfied.

## 10. Honest limits

- **The gate's condition is defined and driven, but not consumed by any withholding path.** R5 states why
  (the verdict postdates `done`). A reader looking for "the gate withholds `done` on a declared failure"
  will not find it, and that is the decision, not an omission.
- **`verdict_keys_on_declared` is a *predicate*, and nothing calls it in production.** It is the ADR's
  named condition and its guard's subject; ADR-0051's enablement decision is what would consume it. It is
  not a written-but-unwired *control* — it is a definition the enablement contract refers to — but that
  distinction is worth stating rather than assuming.
- **The measurement is six cases, not a population.** It is enough to separate the two conditions (two of
  six discriminate) and it is committed; a population would be better and does not exist.
- **`mypy` was not re-run.** This change touches three `wisp/` files; the count is 1844 at HEAD and this
  phase does not claim it moved. Stating that is weaker than measuring it.
