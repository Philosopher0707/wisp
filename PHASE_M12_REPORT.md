# PHASE M12 REPORT — The Failure Path Reaches the Taxonomy

| Field | Value |
|---|---|
| Item | **M12** — the recovery ladder on the failure path |
| Predecessor | P6 (built the taxonomy and the ladder; deferred this) |
| Status | **`COMPLETE`** — the bridge exists and the refusal defect is closed; ladder *enforcement* stays deferred |
| Decision | `WISP_ARCHITECTURE_DECISIONS.md` **ADR-0032** |
| Files changed | 3 production, 2 test files |
| Tests added | `tests/test_failure_signal_classification.py` (**33**) |
| Rollback | none needed — the adapter is additive; the denial fix is a predicate |

---

## 1. The gap was not where ADR-0026 put it

ADR-0026 deferred M12 with a reason: rewiring the failure path *"alters behaviour on the least-covered
path, and the plan names the risk as 'ordering and budget interaction'."*

Reconnaissance found the ladder **cannot be consulted**, because nothing bridges the runtime's failure
signals to the taxonomy:

| Side | Has |
|---|---|
| the runtime | `(message, recoverable, code)` on an `error` event |
| the taxonomy | a `result` (→ `classify_result`) **or** semantic flags |

Neither of those is the other. `classify_failure` has **no `denial` parameter** — denial is detected only
via a *result* going through `classify_result`. So driving the taxonomy from real failures for the first
time was the actual work, and it found two defects.

## 2. Finding 1 — the engine's refusals were invisible (F28)

The engine refuses a tool call before dispatch — role restriction, schema, gate, extension — and emits
the refusal as an `error` event beginning `Blocked: …`.

`is_denial_text` checks the five canonical statuses and four prose markers:

```python
_PROSE_DENIAL_MARKERS = ('[denied', 'denied by', 'approval denied', 'not authorized')
```

**None of them matches `Blocked:`.** Verified — `is_denial_text` returns `False` for all six engine
refusal shapes, and `True` for all five canonical statuses.

### The cost is concrete

The orchestrator's retry loop says exactly what it means to do:

```python
# Don't retry authorization denials or cancellations (§17).
if self._is_denial(last_error) or "cancell" in last_error.lower():
    logger.warning("Subagent %s denied/cancelled — not retrying: %s", ...)
    return result
```

and `_is_denial` delegates to `is_denial_text`. So it **retried them** — up to `max_retries`, on a call
that would be refused identically. The ladder forbids retrying a `SECURITY` failure; the orchestrator's
own loop did it, because it could not see the refusal.

**This is F15's shape from the other side.** F15: the prose markers matched nothing real, so structured
denials were invisible. Fixed by adding the statuses. Now the statuses are checked — and the **engine's
own marker** was missing, so engine refusals stayed invisible.

## 3. Finding 2 — `OutcomeClass.TIMEOUT` does not mean a turn timeout

It is reachable only from `APPROVAL_TIMEOUT` — a *denial* status — which is why `classify_failure` maps it
to `SECURITY`. I initially read that as a bug ("a timeout is not a security failure"); checking the
vocabulary showed the mapping is **correct**, and that the name is the hazard. A caller routing the
engine's turn timeout (`CODE_TURN_TIMEOUT`) through it would get a security failure that forbids retry.
`test_a_turn_timeout_is_not_a_security_failure` pins it.

## 4. The decision (ADR-0032)

1. **`_ENGINE_DENIAL_PREFIXES`** in `core/events.py` — `("blocked:", "extension intercept failed:")`,
   matched with `startswith`. In the canonical module, not the caller: `is_denial_text` is the ONE
   authority for "is this text a denial", and a second matcher is what F15 *was*.
2. **A prefix, not a substring** — so *"the write was not blocked: it succeeded"* is not a refusal.
3. **`classify_failure_signal(message, recoverable, code)`** — the adapter. Precedence mirrors
   `classify_failure`: refusal → cancellation → error code → transport markers → `recoverable` →
   `IMPLEMENTATION`. It decides *which* taxonomy entry applies and never re-derives what an outcome means.
4. **`CODE_FAILURE_CLASS`** — the class per engine error code, **total by test**.
5. **`TRANSIENT_MARKERS` moves to `core/recovery.py`**; the orchestrator aliases it. The retry loop and
   the taxonomy must agree about what is transient.

### Two precedence choices worth stating

**The default is `IMPLEMENTATION`, deliberately not `SECURITY`.** `SECURITY`'s only legal rung is
escalation, so defaulting to it would escalate every novel failure to a human. An unrecognised failure
must not silently acquire the strongest prohibition.

**`CODE_TURN_TIMEOUT` is `ENVIRONMENT`, not `TRANSIENT`.** Retrying a turn that timed out because the
model is too slow is the orchestrator's own documented refusal (*"the model is too slow or unreachable —
not retrying"*), and `ENVIRONMENT` routes to `DIAGNOSTIC` for exactly that reason.

## 5. Completion criteria

- [x] The bridge from the runtime's failure signals to the taxonomy exists
- [x] Engine refusals are recognised as denials — **the RED-first test**
- [x] The retry loop's own expression now answers `True` for a refusal
- [x] The adapter is total over the real failure shapes, and ratcheted
- [x] Every engine error code has a classification — **ratcheted**
- [x] The turn-timeout naming trap is pinned
- [x] The transient vocabulary has one authority
- [x] **Zero new failures** — see §6
- [ ] `ruff` / `mypy` — not installed
- [ ] **Ladder enforcement** — deliberately deferred, see §7

## 6. Regression

Verified against the **stable baseline** (`.workbuddy-ai/memory/baseline-failures-stable.txt`).

| Comparison | Result |
|---|---|
| Count | **129** |
| New vs the stable baseline | **none** (`comm -13` empty) |
| Absent vs the stable baseline | **none** (`comm -23` empty) |

This phase changes a **predicate the retry path consults**, so the comparison is load-bearing rather than
a formality. The neighbouring suites were checked explicitly first — 14 files, 516 tests — and **one**
test needed updating: the P10 guard `test_subagent_denial_detector_delegates`.

### One guard improved, not weakened

That guard was `assert "_DENIAL_MARKERS" not in src` — a **whole-file grep**. My explanatory comment in
`subagent_orchestrator.py` names the removed list to say *why* it was removed, so the grep matched my
documentation and failed.

The guard now walks the **AST** for the name as an identifier. Comments are never in the AST, so a
documentation mention no longer trips it — and an executable reintroduction still does. Since "the
replacement is only an improvement if it still detects the thing", it ships with
`test_the_denial_marker_guard_is_not_vacuous`, which runs the detector against a synthetic offender.

A guard that forbids documenting the defect it guards against is one people delete.

`ruff` / `mypy`: not installed.

## 7. What is deferred, and why

**The ladder's decision is not enforced.** This phase makes a failure *classifiable* and a refusal
*visible* — the precondition ADR-0026 assumed already existed. Acting on the decision (re-running a turn
on a `RETRY` rung, parking it on `HUMAN`) changes the turn loop's control flow, which is the risk
ADR-0026 correctly named.

What is now in place for that step: the class is computable from a real failure, the legal rungs per class
are a table, `RecoveryLadder.decide()` is tested, and the budgets exist. The remaining decision is a
policy one — which rungs the turn loop may take — and it is now expressible.

## 8. Honest limits

- **The adapter is a judgement, written down.** Each precedence step is a decision with a reason; a
  reviewer could argue `recoverable=True` should not imply `TRANSIENT`. It is one function with the
  reasoning beside it.
- **`_KNOWN_NON_REFUSALS` is a test-local list.** It forces a decision on each new engine failure prefix
  rather than a silent default, but it lives in the test rather than beside the code. Moving it next to
  `_ENGINE_DENIAL_PREFIXES` would be better; it is not done.
- **The denial fix's effect is not measured end to end.** No subagent run was driven to observe the retry
  loop actually declining to retry a `Blocked:` failure. The claim rests on the predicate returning `True`
  for the exact expression the loop evaluates — which the test asserts — not on an observed run.
- **`CODE_TOOL_TIMEOUT` is classified but never emitted.** It is defined in `events.py` and no code path
  emits it — another orphan, recorded rather than removed, because removing a public constant is a
  different decision.
- **Nothing consumes the recorded class yet.** The adapter and the code table exist and are tested; the
  turn loop does not yet record or act on the class. That is §7.
