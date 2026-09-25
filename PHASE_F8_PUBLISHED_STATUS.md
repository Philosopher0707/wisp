# PHASE — F8 PUBLISHED STATUS (Deliverable 3)

**Deliverable:** **ADR-0052** — a capability failure is published as a failure of the **host**, not as a
denial; the published denial taxonomy is unchanged.
**Baseline:** `40cfa52` (no drift). **Predecessor:** `PHASE_F8_ERROR_CLASSIFICATION.md` §4, which named
the split and left it open.

---

## 1. What was produced

| Artefact | What it is |
|---|---|
| **ADR-0052** in `WISP_ARCHITECTURE_DECISIONS.md` (51 → 52) + its index row | The decision, the six surfaces, five rejected alternatives, the named residual. |
| `wisp/core/events.py` | `CAPABILITY_FAILURE_STATUS`, `CAPABILITY_KIND_KEY`, `capability_failure_result()` — the system-failure envelope. **No denial status added.** |
| `wisp/core/stateless.py` | `_is_capability_failure()` (the shared predicate), both dry-run stamping sites, and `_refusal_result_event`'s capability branch. |
| `tests/reliability/test_f8_published_status.py` | **15 tests** in 4 classes, driving the whole path; **3/3 non-vacuity probes caught**. |

---

## 2. The six surfaces, and their state

| Surface | Location | Before | After |
|---|---|---|---|
| `_DENIAL_STATUSES` | `core/events.py:312` | 5 members | **unchanged** (asserted) |
| `OUTCOME_BY_STATUS` | `core/events.py:346` | `SCHEMA_INVALID → INVALID` | **unchanged**; the capability path does not use it |
| `TERMINAL_OUTCOME_CLASSES` | `core/events.py:358` | `INVALID` non-retryable | **unchanged**; the capability envelope is `ERROR`, not terminal |
| prompt DENIALS-ARE-FINAL | `context_assembler.py:157` | 5 statuses | **unchanged** (asserted — this is why the ADR is not a behavioural change) |
| `DENIAL_SCHEMA_INVALID` | `core/recovery.py:48, :154` | consumed by M12 | **unchanged**; the capability path never reaches it |
| the two dry-run stamping sites | `core/stateless.py` (batch + single-call) | `_denial = "SCHEMA_INVALID"` unconditionally | the shared predicate decides; `_capability` for a host failure |

**Two of the six are touched, and neither is a published vocabulary.** That is the whole reason
Option B was chosen.

---

## 3. The decision

> **R1.** A `ValidationFailure` whose `kind` is `CAPABILITY_MISSING` is published with status **`error`**
> — never a denial status. **R2.** The `kind` travels in the envelope's `data`. **R3.** The published
> denial taxonomy and the prompt are **unchanged**. **R4.** Both stamping sites apply **one shared
> predicate**, which defaults to a data failure. **R5.** A genuine schema rejection is **byte-identical**.
> **R6.** `authorized` is `None` (no decision was made), not `False`. **R7.** The class is
> `OutcomeClass.ERROR`, deliberately not terminal.

**Why not Option A (a new denial status).** It is additive, but it extends a taxonomy consumed by the
M12 classifier **and** requires the prompt's DENIALS-ARE-FINAL list to name the new member — **a
behavioural change** (what the model is told), which needs the flag-and-measure treatment. It would also
need a new `OutcomeClass`, because none of the eight means *"the host is broken"*: mapping it to
`INVALID` keeps the wrong claim, and mapping it to `ERROR` abandons the denial framing anyway — i.e.
converges on Option B with more surfaces moved.

**Why not the third option** (reuse `SCHEMA_INVALID`, add a note to the message): **that is the shape F8
was** — a system failure wearing a data failure's name. A message note changes neither
`OUTCOME_BY_STATUS`, nor the M12 classification, nor what the prompt says.

---

## 4. The implementation

```
stateless._is_capability_failure(failure) -> bool      # reads `.kind`, never prose; defaults to data
  used at both dry-run sites:
      if _is_capability_failure(_schema_error):  tc["_capability"] = True
      else:                                      tc["_denial"] = "SCHEMA_INVALID"

stateless._refusal_result_event(tc, workspace)
      if tc.get("_capability"):  ev = capability_failure_result(name, reason, kind, ...)
      else:                      ev = denial_result(name, status, _denial_display(...), ...)

events.capability_failure_result(name, reason, kind, ...)
      {"status": "error", "capability": "tool_argument_validation", "kind": <kind>,
       "authorized": None, "executed": False, "retryable": False, "reason": ..., "hint": ...}
```

The audit line (`AuditLog.log_blocked`) was also corrected: it used to record
`f"{status}: {reason}"`, which for the capability case would have written `POLICY_DENIED:` about a
denial that never happened. It now records the failure's `kind`.

---

## 5. Non-vacuity

Three probes, each restoring the pre-ADR behaviour in one place; the tree is restored
**byte-identically** (sha256 verified) and `__pycache__` purged on both sides:

| Probe | Break | Result |
|---|---|---|
| **NV1** | `_is_capability_failure` always returns `False` | **CAUGHT** — 5 failed |
| **NV2** | the envelope's status reverted to `SCHEMA_INVALID` | **CAUGHT** — 3 failed |
| **NV3** | `"CAPABILITY_MISSING"` added to `_DENIAL_STATUSES` | **CAUGHT** — 2 failed |
| CONTROL | — | green (15 passed) |

`files left byte-identical: 2/2`.

### 5.1 NV1 did not falsify on the first attempt — and it was the test that was wrong

The first run reported **NV1 MISSED**. The cause: the test's `_publish` helper took a `capability: bool`
**argument** and set `_capability` itself — so it replicated the *rule* instead of exercising it, and
breaking `_is_capability_failure` changed nothing. **The test was not testing what it claimed.**

This is the same class as `PHASE_STRUCTURED_CRITERIA.md` §5's P2 (a red baseline keyed on the wrong
criteria id, making the promotion assertion vacuous), and the same lesson: **a probe that does not
falsify means the test is not testing what you think.** Found by running the probe, not by reading it.

Fixed by having `_publish` apply the **real** predicate, exactly as the stamping sites do. NV1 then
caught 5 failures. The docstring records the defect so the helper is not "simplified" back.

---

## 6. Regression

```
env -u PYTHONPATH .venv/bin/python -m pytest <41 files> -q -p no:cacheprovider --tb=no -rf
```

| | |
|---|---|
| **Result** | **1234 tests — 1233 passed, 1 failed** (130.75 s) |
| **The one failure** | `test_node_identity.py::…::test_a_parallel_round_is_journaled_as_one_exchange_per_call` — **F38**, pre-existing |
| **New failures** | **none** — the failure set is identical in both directions |
| **Now-passing** | none |
| **Delta** | **+15** — all in `test_f8_published_status.py` |

**The F8 sibling suites are green:** `test_f8_error_classification.py` +
`test_f8_tool_execution_restored.py` + `test_verification_evidence_adapter.py` → **45 passed**. The
produced-side fix ADR-0052 builds on is untouched.

**Gates, measured not asserted** (F71): `ruff check wisp/` → **11 errors**, unchanged by this phase; the
new test file is clean.

The full suite was **not** run: F36 says it cannot run in one process on this host. The method is weaker
than a two-run intersection and is stated as such.

---

## 7. The residual, named

The M12 classifier now sees **no denial** for a capability failure, so a turn whose only problem is a
broken validator is classified as an ordinary error — `FailureClass.IMPLEMENTATION` by ADR-0032's
precedence — rather than by a denial rule. **That is the honest classification** (nothing was denied),
and it is recorded rather than smoothed over. If a future ADR wants a dedicated failure class for
*"the host is broken"*, that is a taxonomy decision and this ADR does not pre-empt it.

**Not changed, and why:** `is_denial_text` is unaffected (`CAPABILITY_MISSING` is not a denial token and
the envelope carries no denial status); `recovery.py` is untouched; `_DENIAL_STATUSES` is asserted
unchanged.

## 8. Honest limits

- **The capability path is unreachable in this environment**, because `jsonschema` is now provisioned. It
  is exercised by hiding the module from `sys.modules` — the technique the F8-provisioning phase
  established — which reproduces the *import* failure but not, say, a partially-installed `jsonschema`
  whose own import fails deep inside. The `except ImportError` clause covers both; only the first is
  measured.
- **The two stamping sites are pinned structurally**, not driven end-to-end: the test asserts both call
  the predicate (a source count) and drives `_refusal_result_event` directly. Driving a real turn
  through both sites would be stronger; the AST/source pin catches the regression that matters (a site
  that stops consulting the predicate).
- **`retryable: False` is a payload field, not an enforced policy.** Nothing currently blocks a retry of
  a capability failure; the envelope and the failure's text both *state* that it will fail identically.
  Enforcing it would be a recovery-policy decision.
- **The envelope's class is `ERROR`, which is not terminal** — so a consumer that gates on
  `TERMINAL_OUTCOME_CLASSES` will now permit a retry it previously refused. That is intentional (the
  failure is recoverable by fixing the environment) and is stated here because it is a real change in
  what an automated consumer may do.
