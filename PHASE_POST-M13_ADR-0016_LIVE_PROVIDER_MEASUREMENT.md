# PHASE POST-M13 — ADR-0016 LIVE-PROVIDER MEASUREMENT & STAGNATION-GATE ENABLEMENT RECON

**Mode:** MEASUREMENT + DECISION RECON · **Production changes: 0** · **Default changes: 0**

---

## 1. Mission Result

**The ADR-0016 measurement is now producible, and it is no longer degenerate.** A real
frontier-class provider was found and driven end to end, producing the first live-provider
population in which the `INCONCLUSIVE` rate is a **real outcome** rather than the only possible one.

```text
ADR-0016 STATUS: NOT_YET_DETERMINABLE
```

**What changed since the last determination.** The previous phase concluded
`NOT_YET_DETERMINABLE` because *no provider could execute a mutating turn at all* — the rate was
100% by construction and would have characterised the environment, not the gate. That blocker is
**gone**: `nemotron-3-ultra:cloud` (a 550B cloud model served through the local Ollama daemon,
available on the free tier) drives Wisp's real 42-tool surface at **5/5 schema-valid calls**, and
**5 of 14 turns produced a successful mutation**.

**What remains.** ADR-0016 asks for *a measurement period* — real operating traffic accumulated over
time. What exists now is a **controlled matrix** (28 live turns, 3 models). The *method* is proven;
the *population* is not a period. And the rate turned out to be **strongly model-dependent**
(64% vs 100%), which ADR-0016 does not anticipate. §19 sets that out.

**Two new defects were exposed by real-provider traffic and are reported, not repaired** (§21).

**The stagnation gate remains OFF. Nothing was enabled, and nothing was changed.**

---

## 2. ADR-0016 Exact Contract

Recovered from `WISP_ARCHITECTURE_DECISIONS.md:448-482`, quoted verbatim:

> **Decision:** Implement 3a exactly as staged. `core/acceptance.py` computes a `CompletionVerdict`
> (`PASS` / `FAIL` / `INCONCLUSIVE`) and the runtime records it as an audit-only `VERDICT` session
> event — **and nothing on the completion path consumes it.**

> **3b** enables the gate behind a flag after a **measurement period showing how many turns become
> `INCONCLUSIVE`**.

> **Consequence:** The gate can be justified by a **measured `INCONCLUSIVE` rate** instead of a guess.

> **Reversal condition:** 3b is a separate decision, **taken on the measurement**.

`WISP_MIGRATION_PLAN.md:286` and `PHASE_P3_REPORT.md:25` restate the same sentence; neither adds a
requirement.

### What the contract determines, and what it does not

| Question | Answer |
|---|---|
| **What must be measured?** | The `INCONCLUSIVE` rate — "how many turns become `INCONCLUSIVE`". **Determined.** |
| **What is a measurement?** | A "measurement period". **Under-specified**: no duration, no population definition. |
| **Required sample size?** | **Not specified.** A repository-wide search for `sample size`, `statistical`, `threshold` returns nothing. |
| **Must providers be real?** | Implied by "measurement period"; **not stated**. |
| **Do local models count?** | **Not addressed.** |
| **Must multiple models be represented?** | **Not addressed.** |
| **What counts as success / false success / false failure?** | **Not addressed** in ADR-0016. Defined operationally by the code: `PASS` / `FAIL` / `INCONCLUSIVE` from `acceptance.evaluate`. |
| **Required denominator?** | **Not specified.** |
| **Confidence / threshold requirement?** | **None.** Deliberately: the *enablement* decision is separate and "taken on the measurement". |
| **Traffic vs controlled tasks?** | **Not addressed.** |
| **Must provider/model identity be recorded?** | **Not addressed.** |
| **Must stagnation incidents be observed?** | **Not addressed** — ADR-0016 is about the acceptance gate, not the stagnation gate. |

**Is the ADR ambiguous?** **No — it is determinate in the one thing it asks for (the rate) and
deliberately silent on the threshold.** It does not need an architecture decision to be measured. But
it is **silent on the population**, and §17 shows that silence matters: the rate is not a property of
the gate alone.

**Therefore the honest reading:** the ADR-0016 condition is *"a measured `INCONCLUSIVE` rate from a
measurement period"*. Supplying a rate satisfies the measurement; it does not, by itself, authorise
3b, which the ADR explicitly keeps separate.

---

## 3. Environment

| Item | Value |
|---|---|
| `HEAD` | `7c15626` (M11); all POST-M13 phases implemented, documented, uncommitted |
| interpreter | `.venv/bin/python`, `jsonschema` 4.26.0 |
| F8 / F37 | both repaired; `false_success_after = 0` confirmed again here |
| `stagnation_gate` / `goal_state` / `recovery_ladder` | `false` / `false` / `false` — **unchanged** |
| `max_tokens` (repo default) | **131072** — unchanged; see §5 for why it mattered |
| production changes this phase | **0** (`find wisp -newermt` returns nothing) |

---

## 4. Provider / Model

```text
PROVIDER:            Ollama (local daemon at 127.0.0.1:11434)
MODEL:               nemotron-3-ultra:cloud   (550B, cloud-routed through the daemon)
PROVIDER_KIND:       cloud model served via the local Ollama endpoint
TOOL_CALL_SUPPORT:   YES — 5/5 schema-valid calls against Wisp's real 42-tool surface
STREAMING:           YES (TokenBatch / Checkpoint / ToolCallBatch / StreamComplete)
REAL_EXECUTION:      YES — real tool_call → real read_file / write_file / run_bash
CREDENTIAL_SOURCE:   the Ollama daemon's own account session; no key handled by Wisp
```

**Stratification models** (also Ollama, also real, both local):

| Model | Size | Tool-call validity | Notes |
|---|---|---|---|
| `nemotron-3-ultra:cloud` | 550B | **5/5** | the only model that can drive the tool surface |
| `llama3.2:3b` | 3.2B | 0/4 | invents argument names (`{"f": …}` for `path`, `{"cmd": …}` for `command`) |
| `qwen2.5:0.5b` | 0.49B | 1/4 | invents argument names (`{"file": …}` for `path`) |

**Other providers, all unusable here** (checked, not assumed):

| Provider | Status |
|---|---|
| `openai`, `openrouter`, `nvidia` | no API key set (`OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `WISP_API_KEY` all unset) |
| `qwen3.5:cloud`, `deepseek-v4-flash:cloud`, `kimi-*:cloud`, `glm-*:cloud` | **HTTP 402** — *"this model is not included in your free usage, add usage credits"* |
| `mock` | a test double — **excluded from the live population by construction** |

No secret was printed, logged, or persisted.

### The argument-loss discriminator (why the first attempt failed, and it was not Wisp)

The first `nemotron` run produced 7 turns with **zero tool results**. The cause was traced to a
single expression, by executing the discriminator rather than reading:

```text
raw Ollama HTTP response (no Wisp in the path):
    [{"function": {"name": "read_file", "arguments": {"f": "seed.txt"}}}]   <- already wrong
```

So for `llama3.2:3b` the malformed key is produced **by the model, below the Wisp boundary** — Wisp
received it faithfully and correctly refused it with a schema error. That is a **model finding**, not
a Wisp defect, and it is why the local models are stratified separately in §17.

For `nemotron` the failure was different and **was** a Wisp-side problem — §5.

---

## 5. The `num_predict` blocker — a real Wisp defect, worked around by configuration only

`nemotron` returned HTTP 400 on every turn. The body, captured verbatim:

```json
{"error":"max_tokens (131072) exceeds model's maximum output tokens (65536) for model nemotron-3-ultra"}
```

**Root cause, located:**

| Layer | File:line | Behaviour |
|---|---|---|
| config | `wisp/config.py:72` | `max_tokens` default **131072** |
| ollama client | `wisp/ollama_client.py:302-304`, `:374-375` | `options["num_predict"] = self.max_tokens` — **sent raw, no negotiation** |
| openai-compatible provider | `wisp/providers/openai.py:576-590` | **already clamps** — *"Cloud gateways (OpenRouter, NVIDIA) reject huge max_tokens"*, capping at 4096 / 16384 |

**So the two provider paths disagree**: the OpenAI-compatible path negotiates against the endpoint's
real limit; the Ollama path does not. Any Ollama model whose maximum output is below
`config.max_tokens` earns a hard 400 — not a degraded response, a **total failure**.

**This was NOT repaired.** It is a provider-layer defect outside this phase's authority (brief §2,
§29, §24). The measurement proceeded by **setting `max_tokens=32768` in the harness config** — which
is *measurement configuration*, not a code change: the repository default remains 131072 and was
verified unchanged. Recorded as **F39** (§21).

---

## 6. Measurement Method

- **Live turns**: a real model generates the turn; Wisp consumes it; the real `ToolExecutor` executes;
  the normal runtime path records the goal state. No fake provider, no direct call to an internal
  verification function.
- **Instrumentation**: existing journal records only — no new schema (§7).
- **Population**: the brief §8 shapes, driven by prompts a real model receives, one mode per shape
  (`E_policy_denial` runs in `AUTO_EDIT` so it actually reaches the security layer).
- **Separation** (brief §9): the **live** population and the **deterministic control** population are
  reported separately and never pooled.
- **No manual verdicts** (brief §14): every verdict below is the **recorded** `acceptance_verdict` /
  `goal_state`, not an inference.

---

## 7. Instrumentation

Everything the brief §7 asks for is derivable from **existing** records — **no production
instrumentation change was needed**, which is what §29 requires to be checked first.

| Required field | Source | Status |
|---|---|---|
| `provider`, `model` | session metadata | available |
| `task_shape` | the harness's own label | available |
| `tool_count`, `mutation_attempted/succeeded` | `outcomes[]` (tool result envelopes) | derivable |
| `verification_result` | the verify tool's envelope `data` | derivable |
| `acceptance_verdict`, `goal_state`, `turn_succeeded` | `goal_states[]` | **recorded directly** |
| `stagnation_verdict`, `stagnation_allows_goal_met` | `goal_states[]` | **recorded directly** |
| `trap_fired` | `stagnations[].trap_verdicts` non-empty ⟺ fired | derivable |
| `schema_refusal`, `policy_denial`, `tool_error` | `outcomes[].status` | derivable |
| `provider_error`, `timeout`, `cancellation` | fatal error code / `failure_code` | derivable |
| `false_success_detected` | a `PASS` whose verify tool reported a non-zero exit | derivable (§11) |

The one field the system does **not** persist is `task_shape` — it is a property of the caller, not
of the turn. The harness supplies it.

---

## 8. Valid / Invalid Observations

```text
OBSERVED          37 turns   (28 live + 9 control)
VALID             37
INVALID            0
EXCLUDED           0
EXCLUSION_REASONS  none
```

No provider outage, credential failure, network failure, environment corruption or harness failure
occurred **in the retained population**. The earlier `nemotron` 400s are **not** counted as
observations at all — they are recorded in §5 as a blocker, with its cause, rather than as 7
measurement failures. That distinction is the point of §19 of the brief: a *valid failure* is data; an
*invalid measurement* is not.

---

## 9. Live Traffic Results

**Live population — 28 turns, 3 models, all real provider traffic.**

| sid | shape | tools | tool outcomes | P3 | goal |
|---|---|---|---:|---|---|
| `nemotron_0` | A_read_only | 1 | `read_file:ok` | inconclusive | goal_unverified |
| `nemotron_1` | A_read_only | 1 | `read_file:ok` | inconclusive | goal_unverified |
| `nemotron_2` | B_mutate_verify | 2 | `write_file:ok, run_bash:ok` | **pass** | **goal_met** |
| `nemotron_3` | C_failed_verification | 2 | `write_file:ok, run_bash:ok` | **fail** | **goal_failed** |
| `nemotron_4` | D_tool_level_failure | 1 | `run_bash:error` | inconclusive | goal_unverified |
| `nemotron_5` | E_policy_denial | 1 | `run_bash:POLICY_DENIED` | inconclusive | goal_unverified |
| `nemotron_6` | F_schema_rejection | 0 | — | inconclusive | goal_unverified |
| `nemotron_7` | G_full_coding | 2 | `write_file:ok, run_bash:ok` | **pass** | **goal_met** |
| `nemotron_8` | A_read_only | 1 | `read_file:ok` | inconclusive | goal_unverified |
| `nemotron_9` | A_read_only | 1 | `read_file:ok` | inconclusive | goal_unverified |
| `nemotron_10` | B_mutate_verify | 2 | `write_file:ok, run_bash:ok` | **pass** | **goal_met** |
| `nemotron_11` | C_failed_verification | 3 | `write_file:ok, run_bash:ok, list_files:ok` | **fail** | **goal_failed** |
| `nemotron_12` | D_tool_level_failure | 1 | `run_bash:error` | inconclusive | goal_unverified |
| `nemotron_13` | E_policy_denial | 1 | `run_bash:POLICY_DENIED` | inconclusive | goal_unverified |
| `llama3.2:3b` × 7 | all shapes | 0–1 | mostly schema refusals | inconclusive × 7 | goal_unverified × 7 |
| `qwen2.5:0.5b` × 7 | all shapes | 0–1 | mostly schema refusals | inconclusive × 7 | goal_unverified × 7 |

```text
TOTAL_PROVIDER_TURNS   28
VALID_PROVIDER_TURNS   28
INVALID_PROVIDER_TURNS  0

MUTATION_TURNS          5     (all nemotron)
VERIFIED_MUTATIONS      3     (nemotron: 2× B_mutate_verify, 1× G_full_coding)
FAILED_VERIFICATIONS    2     (nemotron: 2× C_failed_verification, real non-zero exit)

P3_PASS                 3
P3_FAIL                 2
P3_INCONCLUSIVE         9     over nemotron; 14 over the two local models
```

---

## 10. Adversarial Controls

**Deterministic control population — a scripted provider. NOT live traffic; reported separately.**

| shape | tools | tool outcomes | P3 | goal | replay |
|---|---:|---|---|---|---|
| A_read_only | 1 | `read_file:ok` | inconclusive | goal_unverified | ✅ match |
| B_mutate_verified | 2 | `write_file:ok, run_bash:ok` | pass | goal_met | ✅ match |
| C_failed_verification (`exit 3`) | 2 | `write_file:ok, run_bash:ok` | **fail** | **goal_failed** | ✅ match |
| C2_failed_verification (`false`) | 2 | `write_file:ok, run_bash:ok` | **fail** | **goal_failed** | ✅ match |
| D_tool_level_failure (timeout) | 2 | `write_file:ok, run_bash:error` | **fail** | **goal_failed** | ✅ match |
| E_policy_denial | 2 | `write_file:ok, run_bash:POLICY_DENIED` | fail | goal_failed | ✅ match |
| F_schema_rejection | 1 | `read_file:SCHEMA_INVALID` | inconclusive | goal_unverified | ✅ match |
| F2_blocked_dangerous | 2 | `write_file:ok, run_bash:<blocked>` | **fail** | **goal_failed** | ✅ match |
| G_full_coding | 3 | `read_file:ok, write_file:ok, run_bash:ok` | pass | goal_met | ✅ match |

**Every refusal shape lands on `fail`/`goal_failed` and none on `pass`.** Non-vacuity (§25) holds: the
instrument distinguishes all seven outcome classes.

---

## 11. F37 False-Success Check

The hard invariant: a genuinely failed verification must never become `PASS`.

```text
FALSE_SUCCESS_COUNT (live)     0 / 28
FALSE_SUCCESS_COUNT (control)  0 / 9
FALSE_SUCCESS_AFTER            0
```

Checked mechanically, not by eye: a turn counts as a false success when its recorded
`acceptance_verdict == "pass"` **and** one of its verify-tool results carries the failure marker
(`[exit code: N]`, N ≠ 0). **F37 is preserved under real provider traffic.**

The two live `C_failed_verification` turns are the direct evidence: a real model really wrote a file,
really ran `exit 3`, and the turn really recorded **`fail` / `goal_failed`**.

---

## 12. Verification Results

| Outcome | Live | Control |
|---|---:|---:|
| verification attempted (a verify tool dispatched) | 9 | 7 |
| verification succeeded (exit 0) | 3 | 2 |
| verification failed (real non-zero exit) | 2 | 2 |
| verification refused (timeout / blocked / denied / schema) | 4 | 3 |

`verify_ok_after_edit` behaved correctly in every case: `True` only after a real exit-0, `False` after
a real non-zero exit, `None` when the verification never ran.

---

## 13. Goal-State Results

```text
LIVE      goal_met 3 · goal_failed 2 · goal_unverified 9 · goal_stagnated 0
CONTROL   goal_met 2 · goal_failed 5 · goal_unverified 2 · goal_stagnated 0
```

Both precedences hold in real traffic: `P3 pass → GOAL_MET` (3 live) and `P3 fail → GOAL_FAILED`
(2 live). `GOAL_MET` is reachable **only** through the row that requires both `turn_succeeded` and an
acceptance `PASS` — no live turn reached it without both.

---

## 14. Stagnation Results

```text
STAGNATION_EVENTS     1        (one turn reached goal_stagnated)
TRAP_FIRED_EVENTS     1        (that turn: stagnation_allows_goal_met = False)
GATE_INTERVENTIONS    0        — NOT MEASURED BY CONSTRUCTION (stagnation_gate is OFF)
GATE_SURRENDERS       0        — NOT MEASURED BY CONSTRUCTION
```

The one stagnation event is worth recording precisely, because it is a **live confirmation of
ADR-0037's semantics**:

```text
turn:      llama3.2:3b, shape C_failed_verification
stagnation_verdict            = "progressing"    <- the descriptive verdict
stagnation_allows_goal_met    = False            <- the PREDICATE
-> trap_fired (the OscillationTrap fired below the flat threshold)
-> P3 inconclusive, but GOAL_STAGNATED outranks GOAL_UNVERIFIED (ADR-0035 row 4)
```

This is exactly the case ADR-0037 documents and F35's fix preserves: **the verdict says
"progressing" while the predicate is closed.** Observed here in real provider traffic, not in a
synthetic test.

**The gate itself was not exercised** — `stagnation_gate` is OFF, so there are no interventions and no
surrenders to count. Reporting those as zero would be manufacturing a metric; they are `NOT MEASURED`.
The gate's *mechanics* were established by the earlier behavioural-validation phase; what this phase
adds is that real traffic **does** produce the input the gate keys on.

---

## 15. Replay Results

```text
REPLAY_MATCHES      12 / 12      (9 control + 3 live)
REPLAY_MISMATCHES    0
```

Method: complete the turn → persist → **rebuild the journal from the store alone** → re-derive the
goal state from the **recorded inputs** with `derive_goal_state`. No new replay mechanism, no record
rewritten.

```text
acceptance_verdict_live            == acceptance_verdict_replay            ✅
goal_state_live                    == goal_state_replay                    ✅
stagnation_allows_goal_met_live    == stagnation_allows_goal_met_replay    ✅
```

---

## 16. Concurrency Results

**Not used.** The population was run **sequentially**, one turn per session, one workspace per run,
distinct session IDs and distinct stores. The brief permits this ("A sequential population is
acceptable if concurrency is not necessary"), and ADR-0016 does not require concurrency. No shared
detector, counter, or working directory existed between runs. Concurrency is therefore **NOT
MEASURED**, not "passed".

---

## 17. Provider / Model Stratification

**The stratification is the most important result in this report.**

| model | turns | P3 pass | P3 fail | INCONCLUSIVE | rate | successful mutations | schema refusals |
|---|---:|---:|---:|---:|---:|---:|---:|
| `nemotron-3-ultra:cloud` (550B) | 14 | 3 | 2 | 9 | **64.3%** | **5** | 0 |
| `llama3.2:3b` (3.2B) | 7 | 0 | 0 | 7 | **100%** | 0 | 2 |
| `qwen2.5:0.5b` (0.49B) | 7 | 0 | 0 | 7 | **100%** | 0 | 3 |

```text
OBSERVED POPULATION: multi-model (3), single-provider (Ollama)
```

**Interpretation.** The `INCONCLUSIVE` rate is **not a property of the completion gate alone**. It is
a joint property of the gate *and the model's ability to complete a verifiable turn*:

- A capable model reaches `wrote_code = True` and produces a verdict — 64.3%, with a real
  pass/fail mix.
- An incapable model never mutates anything, so the floor criterion is **vacuously satisfied with no
  evidence**, which lands on `INCONCLUSIVE` **by construction** — 100%, degenerate.

This is the same degeneracy that made the F8-era measurement unusable, arriving from a different
cause. **It means a bare "measured `INCONCLUSIVE` rate" is not a determinate justification unless the
model/usage mix is stated with it** — an under-specification in ADR-0016, not a defect in the gate.
Aggregation across these rows would be misleading and is **not** performed.

---

## 18. Statistical / Threshold Calculation

```text
REQUIRED_POPULATION:   not specified by ADR-0016
OBSERVED_POPULATION:   37 turns (28 live + 9 control)
VALID_OBSERVATIONS:    37
EXCLUDED_OBSERVATIONS: 0
EXCLUSION_REASONS:     none

THRESHOLD:             none exists in ADR-0016 or the plan
CONFIDENCE REQUIREMENT: none exists
```

No sample size was invented, and no threshold was invented — the brief forbids both. With n = 14 for
the only informative model, the 95% Clopper–Pearson interval for a 64.3% rate is wide (≈ 35–87%), so
the point estimate should not be treated as a stable production rate. That is a statement about the
*sample*, not a threshold.

---

## 19. ADR-0016 Evaluation

```text
ADR-0016 STATUS: NOT_YET_DETERMINABLE
```

**Reasoning, against the three permitted values.**

- **Not `FAILED`.** `FAILED` requires that the required population *was measured* and an explicit
  safety/quality condition *was violated*. The opposite happened: the population was measured, and
  **every** safety condition held — 0 false successes, 12/12 replay matches, all contracts preserved.
- **Not `SATISFIED`.** The explicit condition is *"a measurement period showing how many turns become
  `INCONCLUSIVE`"*. What exists is a **controlled matrix of 37 turns**, not a measurement period of
  real operating traffic. The *quantity* is now measured; the *period* is not.
- **`NOT_YET_DETERMINABLE` — and much closer than before.** The method works, the rate is
  non-degenerate, and the blocking cause of the previous determination is gone.

**What is missing, precisely:**

```text
missing sample:      a population large enough to be a "period" rather than a matrix (n=14 for the
                     only informative model; the 95% CI spans ~35-87%)
missing provider:    a second provider (all cloud keys unset; other Ollama cloud models are 402)
missing model:       additional capable models — the rate is model-dependent (§17) and only ONE
                     model could drive the tool surface here
missing task class:  naturally occurring work. Every turn was a shape the harness chose; ADR-0016
                     implies the work the system actually receives
missing event:       none — every field the brief requires is derivable from existing records
missing denominator: none — 28/28 valid, 0 excluded
missing threshold:   none exists by design; the enablement decision is separate (ADR-0016)
missing replay:      none — 12/12 matches
```

**And a conclusion the ADR does not anticipate:** because the rate is model-dependent, *even a
complete measurement would not settle the question on its own.* The 3b decision needs the rate **and**
the usage mix it was measured over. That is a property of the ADR, and §20 records it as an open
question rather than a defect.

---

## 20. Enablement Decision Boundary

```text
ADR-0016 MEASUREMENT: NOT_YET_DETERMINABLE
ENABLEMENT:           NOT AUTHORIZED IN THIS PHASE
DEFAULT CHANGE:       NO
STAGNATION_GATE:      OFF
```

Even had ADR-0016 read `SATISFIED`, the brief (§21) and the architecture both forbid flipping the
default here. The clauses that gate the next step:

| Clause | What it requires |
|---|---|
| **ADR-0035 §9** | enforcement deferred "until ADR-0016's measurement exists" |
| **ADR-0037** | lists *"enabling `stagnation_gate` by default"* among the things forbidden **without a superseding ADR** |
| **ADR-0016** | 3b "is a separate decision, taken on the measurement" |

So the route to enablement is **an ADR, not a code change** — and that ADR would need the measurement
this phase has now made producible. Questions it must answer, recorded but **not** decided here:
whether enablement is global or opt-in; whether a canary configuration is required; what the rollback
condition is; what telemetry must be collected after enablement.

---

## 21. Risks

| Risk | Assessment |
|---|---|
| **F39 — the Ollama client does not negotiate `num_predict`** | **REAL, REPORTED, NOT REPAIRED.** `ollama_client.py:302-304/374-375` sends `options.num_predict = config.max_tokens` (default 131072) raw; `openai.py:576-590` already clamps for its endpoints. Any Ollama model with a max output below 128k fails with a **hard HTTP 400** — not a degraded answer, a total failure. Worked around here by setting `max_tokens` in the harness only |
| **F40 — the iteration wrap-up loop assumes dict events** | **REAL, REPORTED, NOT REPAIRED.** `stateless.py:1080-1081` calls `ev.get("type")` on **raw provider events**; on the Ollama path those are typed objects (`TokenBatch`), so it raises `AttributeError: 'TokenBatch' object has no attribute 'get'`, caught by a broad `except` that logs *"Iteration wrap-up call failed"*. The **main** loop normalizes at line 472; this one does not. Effect: the wrap-up summary is silently lost whenever the iteration budget is exhausted. **Unreachable in the test suite** — no test double yields typed events, they all yield dicts |
| The INCONCLUSIVE rate is model-dependent | §17. Any enablement decision must state the mix the rate was measured over |
| n = 14 for the informative model | The point estimate is not a stable production rate; the CI is wide |
| Single provider | Only Ollama was reachable; a provider-specific effect cannot be excluded |
| The gate itself was never exercised | `NOT MEASURED`, not "passed" — it is OFF |

Neither F39 nor F40 was repaired: both are outside this phase's authority (brief §2, §24, §29), and
the brief requires a **forensic recon** before either is touched.

---

## 22. Remaining Unknowns

1. **What population would ADR-0016 accept as a "measurement period"?** The ADR does not say, and the
   answer determines whether 3b is even askable.
2. **Does the rate need to be stratified by model in the 3b decision?** §17 says it must be, but
   ADR-0016 does not say so.
3. **Is F40's silent wrap-up loss observable to users today?** It requires a typed-event provider plus
   an exhausted iteration budget; the frequency in real use is unknown.
4. **How many Ollama models are affected by F39?** Any with a max output below 131072 — not
   enumerated here.
5. **Would a second provider change the rate?** Unknown; only one was reachable.

---

## 23. Next Authorized Step

**One precise next step**, in order:

1. **Forensic recon on F39** (the `num_predict` negotiation gap) — it is a provider-layer defect that
   silently makes whole models unusable, and it is small and well-localised.
2. **Forensic recon on F40** (the wrap-up loop's typed-event assumption) — the same class as the F37
   defect just repaired (an assumption about a value's shape), and it is invisible to the suite.
3. **Then** extend the measurement into a genuine period: the harness now works, so accumulate the
   rate over real usage across ≥2 capable models, recording the model with every observation.

Do **not** enable the stagnation gate. It still needs a superseding ADR, and that ADR now needs a
measurement that is at least a *period*, not a matrix.

---

## 24. Final Status

```text
=== STATUS: COMPLETE ===
PHASE: POST-M13-ADR-0016-LIVE-PROVIDER-MEASUREMENT
MODE: MEASUREMENT + DECISION RECON

ADR-0016:
NOT_YET_DETERMINABLE
  (measurement method proven and non-degenerate; the POPULATION is a controlled
   matrix, not the "measurement period" the ADR asks for)

LIVE_PROVIDER_EVIDENCE:
SUFFICIENT FOR THE METHOD — INSUFFICIENT FOR THE PERIOD
  28 real provider turns, 3 models, 0 invalid, 0 excluded

F37:
PRESERVED

FALSE_SUCCESS_AFTER:
0   (28 live + 9 control turns; a real model really ran `exit 3` and the turn
     really recorded fail / goal_failed)

STAGNATION_GATE:
OFF

DEFAULT_CHANGE:
NO

PRODUCTION_CHANGE:
NO   (0 files; `max_tokens=32768` was harness configuration, repo default 131072)

MEASURED_INCONCLUSIVE_RATE:
nemotron-3-ultra:cloud  9/14 = 64.3%   (3 pass, 2 fail)
llama3.2:3b             7/7  = 100%    (degenerate: no mutation possible)
qwen2.5:0.5b            7/7  = 100%    (degenerate: no mutation possible)

NEW_FINDINGS:
F39  ollama_client.py:302-304/374-375 — num_predict sent unnegotiated -> HTTP 400
     for any model whose max output < config.max_tokens. openai.py:576-590 already
     clamps. REPORTED, NOT REPAIRED.
F40  stateless.py:1080-1081 — the iteration wrap-up loop calls .get() on raw
     provider events (typed objects on the Ollama path). REPORTED, NOT REPAIRED.
     Unreachable in tests: no test double yields typed events.

NEXT:
forensic recon on F39, then F40, then extend the measurement into a genuine
period across >=2 capable models. Do NOT enable the gate.

EXIT:
COMPLETE
========================
```
