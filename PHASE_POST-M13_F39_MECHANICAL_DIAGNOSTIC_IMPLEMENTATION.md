# PHASE POST-M13 — F39 MECHANICAL DIAGNOSTIC IMPLEMENTATION

**Mode:** AUTHORIZED IMPLEMENTATION (ADR-0038 mechanical sub-phase only)
**Production files changed: 2** · **Defaults changed: 0** · **Dependencies changed: 0**

---

## 1. Mission Result

```text
COMPLETE
```

ADR-0038's mechanical sub-phase is implemented and verified **against the real Ollama endpoint**, not
only against a fixture. A capacity rejection is now a named **configuration incompatibility** carrying
the configured budget and the provider-reported limit. **No behavioural change**: the request is still
sent verbatim, `None` still omits the budget, nothing is clamped, omitted, negotiated or retried, and
the parsed limit never becomes state.

```text
BEFORE   StreamError(error_type="OllamaError",
                     message="Ollama HTTP error: 400 Client Error: Bad Request ...")
AFTER    StreamError(error_type="OllamaConfigurationError",
                     message="… configured max_tokens (131072) exceeds this model's
                              maximum output tokens (65536). This is a CONFIGURATION
                              INCOMPATIBILITY, not a transient provider failure: the
                              request is not retried and was not modified. Lower
                              `max_tokens` to at most 65536 … The limit above is
                              reported by the provider for this message only — Wisp
                              does not record it or use it on later requests.")
```

**One finding was discovered on the way, and it changed the shape of the fix** — see §3 and §12.

---

## 2. Baseline

| Item | Value |
|---|---|
| starting commit | `7c15626` (M11); all POST-M13 phases uncommitted |
| working tree | 20 pre-existing modified files under `wisp/`; **neither file changed here carried pre-existing code WIP** (verified in §13) |
| `max_tokens` default | **131072**, unchanged |
| `stagnation_gate` | **false**, unchanged |
| pre-phase provider suites | 182 passed (measured with the new file excluded: 157) |
| canonical migration set | **849 tests — 848 pass, 1 fails** (F38, pre-existing) |

---

## 3. Source Forensics

Locations re-established from current source; the ADR's line numbers had moved.

| What | Where |
|---|---|
| `OllamaError` | `ollama_client.py:45` — a single generic `Exception` subclass; **no permanent/configuration representation existed** |
| streaming HTTP error handling | `_post_stream`, the `except requests.exceptions.HTTPError` branch |
| non-streaming HTTP error handling | `_post_with_retry`, the same branch |
| retry classification | `_post_stream` retries on `>= 500` only; `_post_with_retry` on `is_transient_status` (429/5xx) |
| `num_predict` construction | `ollama_client.py:302-304` (`generate`) and `:373-375` (`generate_stream_events`) |
| transient taxonomy | `wisp/core/transport.py:443` `is_transient_status`, `:451` `is_transient_error` — **only** transient helpers exist |
| stale OpenAI comment | `providers/openai.py:576-590` — *"Local Ollama ignores this field anyway."* |

### The finding that changed the fix — the error body was never readable

The first implementation classified correctly **in unit tests and never against the real daemon**. The
cause is a genuine, pre-existing defect:

```text
with self._session.post(..., stream=True) as resp:
    resp.raise_for_status()      <- raises INSIDE the with
                                 <- __exit__ closes the response
except HTTPError as e:
    e.response.text              <- a CLOSED streamed response has NO body
```

Measured directly: on a real streamed 400, `response.text` is `""` on the **first** read, and
`response.content` is `b""`. A streamed response is not buffered, and `Response.__exit__` closes it.

**So the existing 4xx body log in `_post_stream` has never logged a real body.** It only ever appeared
to work because the unit tests' double carries a `.text` attribute and raises from `post()` — so the
`with` block never runs and the closing never happens. That is the same class of defect as F37: a
fixture that does not reproduce the production control flow. Recorded as **F41**.

The consequence for this phase is direct: **R6 needs the body, so the body must be read while the
response is open.** §4 does exactly that, and the existing log line is pointed at the same captured
string — one read, one source, rather than two readers of one fact.

---

## 4. Changes

### `wisp/ollama_client.py` — +104 / −6

| # | Change |
|---|---|
| 1 | `import re` (stdlib) |
| 2 | `_CAPACITY_REJECTION_RE` — a narrow pattern anchored on the whole distinctive phrase |
| 3 | `OllamaConfigurationError(OllamaError)` with `kind = "CONFIGURATION_INCOMPATIBILITY"` |
| 4 | `_response_text(response)` — one defensive body reader, used by both paths |
| 5 | `_configuration_incompatibility(body, configured)` — the diagnostic builder |
| 6 | `_post_stream`: capture the body **inside** the `with`, before `raise_for_status()` |
| 7 | `_post_stream`: the 4xx log now reads the captured body |
| 8 | `_post_stream`: classify, immediately before the final `raise` |
| 9 | `_post_with_retry`: classify, immediately before the final `raise` |

**No other function changed.** Verified by AST comparison against `HEAD`: the only differing functions
are `_post_stream`, `_post_with_retry`, and the two new helpers; the only new module-level name is
`_CAPACITY_REJECTION_RE`; the only new class is `OllamaConfigurationError`.

The pattern, deliberately anchored on the phrase rather than a keyword:

```python
r"exceeds\s+model['\u2019]s\s+maximum\s+output\s+tokens\s*\((\d+)\)"
```

`maximum` alone, `token` alone, and `max_tokens is not a valid option` all fail to match — tested.

### `wisp/providers/openai.py` — +12 / −3, **comment only**

**AST-identical to `HEAD`.** The false premise is corrected, the ADR-0038 R9 distinction is recorded,
and **the clamp is untouched**:

```text
before   "… cap to a generous but credit-safe value. Local Ollama ignores this field anyway."
after    "… This is an AFFORDABILITY policy, NOT a model-capacity one (ADR-0038 R9) …
          An earlier version of this comment claimed 'Local Ollama ignores this field anyway.'
          That is false: Ollama enforces its own per-model output limit and rejects an
          over-budget request with a 400 rather than ignoring the value (F39)."
```

### `tests/test_ollama_configuration_incompatibility.py` — new, 349 lines, 25 tests

---

## 5. Diagnostic Contract

| | before | after |
|---|---|---|
| type | `OllamaError` | **`OllamaConfigurationError`** (`OllamaError` subclass — every existing handler still works) |
| `error_type` on the `StreamError` event | `"OllamaError"` | **`"OllamaConfigurationError"`** |
| message | `Ollama HTTP error: 400 Client Error: Bad Request for url: …` | configured value **+** provider limit **+** the classification **+** the remedy **+** an explicit non-claim |
| says it is configuration, not transient | no | **yes** |
| says it is not retried / not modified | no | **yes** |
| says Wisp does not record the limit | no | **yes** |

The diagnostic's last clause is deliberate and load-bearing for R6 — it states that the number is
**reported for this message only**, so a reader cannot infer that Wisp acquired a capability.

**Where it lands.** `generate_stream_events` converts every failure into
`StreamError(error_type=type(e).__name__, message=str(e))` — so the classification travels as the
**type name** and the diagnostic as the **message**, with no change needed to that conversion.

---

## 6. Retry Proof

```text
one logical call, provider returns 400 "exceeds model's maximum output tokens"
    -> POST attempts observed: 1
```

`test_one_logical_call_is_one_post` asserts exactly one POST. The classification is placed **after**
every retry branch in both methods, so a 400 (which no branch retries) is the only status that can
reach it — the retry loop itself is untouched. Verified separately: **a 503 still exhausts the full
retry budget (3 POSTs)**.

---

## 7. Request Preservation

Against the **real daemon**:

```text
max_tokens=131072  ->  sent num_predict = 131072   (verbatim, NOT clamped)
max_tokens=65536   ->  sent num_predict = 65536    -> ACCEPTED
max_tokens=65537   ->  sent num_predict = 65537    -> OllamaConfigurationError
max_tokens=131072  ->  sent num_predict = 131072   -> OllamaConfigurationError
```

`test_the_request_still_carries_the_configured_value_verbatim` pins the payload.

---

## 8. `None` Preservation

```text
max_tokens=None  ->  options sent = {"temperature": 0.2}   (num_predict ABSENT)
                 ->  ACCEPTED, no errors
```

Unchanged, and R2 is pinned from both directions: `None` omits the parameter
(`test_none_still_omits_num_predict`), and a rejection **never** causes an automatic switch to it
(`test_no_automatic_fallback_to_omitting_the_budget`). The helper also refuses to classify when
`configured is None` — with no budget sent there is no budget to exceed, so claiming the user's
configuration is at fault would be wrong.

---

## 9. OpenAI Comment Repair

Comment only. **AST-identical to `HEAD`**, and behaviour measured unchanged:

| provider | sent `max_tokens` | expected | |
|---|---:|---:|---|
| `openrouter` | 4096 | 4096 | MATCH |
| `nvidia` | 16384 | 16384 | MATCH |
| `openai` | 131072 | 131072 | MATCH |

---

## 10. Test Evidence

| suite | result |
|---|---|
| `tests/test_ollama_configuration_incompatibility.py` (new) | **25 passed** |
| targeted: new + `test_ollama_client` + `test_ollama_client_retry` + `test_ollama_provider` + `test_openai_provider` + `test_provider_conformance` + `test_provider_protocol` + `test_provider_factory` + `test_provider_selection_contract` + `test_mock_provider` | **182 passed, 0 failed** |
| adjacent: `test_verification_evidence_adapter` + `test_f8_tool_execution_restored` + `test_verification_contract` + `test_agent` + `test_harness_scaffolding` | **83 passed, 0 failed** |
| canonical migration set | **849 — 848 pass, 1 fails** (F38, pre-existing) |
| real endpoint (`.workbuddy-ai/memory/post-m13-f39-diagnostic/verify_against_real_endpoint.py`) | **all checks pass** |

**The real-endpoint verification is the load-bearing one.** The unit tests use a captured body; the
probe drives the actual daemon, which is what exposed F41.

---

## 11. Falsification Results

| # | Attempted falsification | Outcome |
|---|---|---|
| **F1** | a normal HTTP 400 becomes misclassified | **Could not falsify.** 7 unrelated bodies (empty, unparseable, unknown model, invalid options, `maximum context length`, `too many tokens`, `max_tokens is not a valid option`) all keep the generic error. A real **404** from a nonexistent model also stays `OllamaError` |
| **F2** | a capacity error gets retried | **Could not falsify.** Exactly **1 POST** |
| **F3** | `num_predict` is silently changed | **Could not falsify.** Sent verbatim at 131072, 65537 and 65536 |
| **F4** | the provider limit becomes persistent state | **Could not falsify.** No attribute is set on the client; nothing is written anywhere |
| **F5** | the provider limit becomes a future clamp | **Could not falsify.** A second request after a rejection still sends the configured 131072 |
| **F6** | `None` starts acting as an automatic fallback | **Could not falsify.** `num_predict` remains present on the failing request |
| **F7** | the OpenAI clamp changes | **Could not falsify.** AST-identical; all three endpoint values unchanged |
| **F8** | an unrelated provider error gets the label | **Could not falsify.** Same evidence as F1, plus the real 404 |

**No falsification succeeded.** The one thing that *did* fail initially was not a falsification of the
design but of my **test double** — see §3, F41.

---

## 12. ADR-0038 Invariant Matrix

| Rule | Status | Evidence |
|---|---|---|
| **R1** exact requested budget | **PASS** | verbatim at 131072 / 65537 / 65536, real endpoint |
| **R2** `None` is explicit provider-decides | **PASS** | `num_predict` absent; never applied as a fallback |
| **R3** no capability from `context_length` | **PASS** | no code path reads it; nothing was added |
| **R4** unknown capability sent verbatim | **PASS** | no clamp, no discovery, no metadata query |
| **R5** capacity rejection classified | **PASS** | `OllamaConfigurationError`, `kind = "CONFIGURATION_INCOMPATIBILITY"`, distinguishable from transient/refusal/connection/server |
| **R6** diagnostic includes requested + limit | **PASS** | both numbers present, real endpoint; explicitly disclaims recording |
| **R7** no retry | **PASS** | 1 POST; 503 still retries 3× |
| **R8** typed future capability stays provider-owned | **PASS** | nothing introduced; no abstraction added |
| **R9** OpenAI affordability ≠ capacity | **PASS** | AST-identical; comment now states the distinction |
| **R10** provider is sole adaptation owner | **PASS** | no new owner; `WispConfig` untouched |

---

## 13. Diff Scope

```text
production files changed   2   wisp/ollama_client.py, wisp/providers/openai.py
production lines           +116 / −9
test files added           1   tests/test_ollama_configuration_incompatibility.py (349 lines, 25 tests)
defaults changed           0   max_tokens = 131072
dependency files changed   0   pyproject.toml / uv.lock untouched
new dependency             0   (only stdlib `re`)
new config field           0        new feature flag   0
new persistence            0        new telemetry      0
```

`find wisp -newermt <phase start>` returns **exactly those two files**. **No pre-existing WIP** was
present in either: `openai.py` was AST-identical to `HEAD` beforehand, and `ollama_client.py`'s only
AST differences are the four functions above.

**One change beyond the literal minimum is declared, not slipped in:** the existing 4xx log line in
`_post_stream` now reads the captured body instead of `e.response.text`. It is *required*, not
cosmetic — a second reader of the same closed response would be a second source for one fact, and it
would remain silently empty. Its logged content is unchanged in intent (the first 500 characters of
the body); it simply starts working. Reported as **F41**.

---

## 14. Regression Results

```text
NEW FAILURES:         0
PRE-EXISTING FAILURES: 1   (test_node_identity.py::…test_a_parallel_round_is_journaled_as_one_exchange_per_call)
                           — F38, unchanged, present in the baseline
ENVIRONMENTAL:        0
```

The canonical set reads **849 / 848 pass / 1 fail**, identical to the count measured before this phase.
No test was weakened, updated or deleted; the new file is additive.

**Adjacent behaviours confirmed unchanged:** F37 (`false_success_after = 0`, the adapter suite green),
verification and the floor contract (green), stream handling (green), and the provider suites (green).

---

## 15. Final Status

```text
=== STATUS: COMPLETE ===

F39:
CONFIRMED + DIAGNOSTIC REPAIR COMPLETE

ADR-0038:
SATISFIED

BEHAVIORAL CHANGE:
NONE

PRODUCTION CHANGES:
2 files (+116 / −9)   ollama_client.py, providers/openai.py (comment only)

DEFAULT CHANGES:
0        DEPENDENCY CHANGES: 0        TEST CHANGES: 1 new file, 25 tests

NEW FINDING:
F41 — the 4xx body log in _post_stream had never logged a real body: a closed
      streamed response has no body, and the unit-test double (which raises from
      post(), never entering the `with`) hid it. Fixed as a required consequence
      of R6, since the diagnostic needs the same body.

NEXT:
F40 FORENSIC RECON — the iteration wrap-up loop's typed-event assumption.
      Its own phase: RECON → AUTHORITY → CONTRACT → DECISION → IMPLEMENT.
      Do NOT copy the F37 pattern into it.

========================
```

**Stopping here as instructed.** F40 is not started.
