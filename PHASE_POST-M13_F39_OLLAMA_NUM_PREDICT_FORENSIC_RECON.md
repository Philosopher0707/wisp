# PHASE POST-M13 — F39 OLLAMA `num_predict` NEGOTIATION FORENSIC RECON

**Mode:** READ-ONLY FORENSIC RECON
**Production changes: 0** · **Test changes: 0** · **Default changes: 0**

---

## 1. Mission Result

**F39 is CONFIRMED as a genuine defect — and it is not repairable mechanically.**

The value flow is established end to end, the rejection is reproduced exactly, and the decisive
question from the brief — *"does Wisp already have an explicit provider contract that says provider
clients must adapt provider-independent token settings to provider/model limits?"* — resolves to
**NO**.

Three facts settle it:

1. **The `Provider` protocol declares no output-limit contract.** It exposes `get_context_length()`
   and `get_model_info()`; neither concerns output limits, and `get_context_length()` returns the
   **context window**, which is a different quantity.
2. **The OpenAI "clamp" is not that contract.** It is a hardcoded `api_base` lookup for two hosts
   (4096 for OpenRouter, 16384 for NVIDIA) — and `api.openai.com` **passes 131072 through
   unclamped**, exactly as Ollama does. It is a point fix, not a policy.
3. **The model's output limit is not discoverable.** `/api/show` exposes `.context_length` and
   nothing else — and for this model the context length (**262144**) is *larger* than the rejected
   value (**131072**), so clamping to the discoverable number would make the failure **worse**.

Because every working repair changes either the semantics of `max_tokens` or the retry policy,
**the phase stops at the decision boundary**:

```text
=== STATUS: REQUIRES ARCHITECTURE DECISION ===
```

**Nothing was modified.** One thing worth recording up front, because it changes the urgency: the
documented escape hatch — `max_tokens: null` — **already works**, and was verified against the
rejected model.

---

## 2. F39 Statement

> `wisp/ollama_client.py` sends `config.max_tokens` directly as Ollama `options.num_predict`, while
> the repository default is `131072`; Ollama models with lower maximum output limits reject the
> request with HTTP 400.

**Verdict: CONFIRMED, with one correction and one addition.**

- **Correction:** the defect is **not** that the Ollama client fails to honour an existing contract.
  There is no such contract to honour (§7, §9).
- **Addition:** the defect is **not limited to cloud models**. It fires for *any* Ollama-served model
  whose maximum output is below `config.max_tokens`. Nothing about the defect is
  `nemotron`-specific — `nemotron-3-ultra` merely made it visible by having a limit (65536) below
  the default.

---

## 3. Baseline

| Item | Value |
|---|---|
| `HEAD` | `7c15626` (M11); all POST-M13 phases implemented, documented, uncommitted |
| `config.max_tokens` | **131072** — unchanged, and **pinned by `tests/test_config.py:34`** |
| `wisp/` modifications | 20 pre-existing WIP + the F37 repair; **unchanged by this phase** |
| Ollama daemon | reachable at `127.0.0.1:11434`; 13 models |
| subject model | `nemotron-3-ultra:cloud` (max output 65536) |
| production / test / default changes | **0 / 0 / 0** |

---

## 4. Exact Reproduction

**Probe A — request construction.** Captured by observing the session POST from a real
`OllamaClient`:

```json
{"model": "nemotron-3-ultra:cloud",
 "system": "...",
 "messages": [...],
 "options": {"temperature": 0.2, "num_predict": 131072},
 "stream": true}
```

**Probe B — endpoint rejection.** The response body, verbatim:

```json
{"error":"max_tokens (131072) exceeds model's maximum output tokens (65536) for model nemotron-3-ultra (ref: 5348158a-…)"}
```

```text
CONFIGURED_MAX_TOKENS: 131072
MODEL_MAX_OUTPUT:      65536
NUM_PREDICT_SENT:      131072
HTTP_STATUS:           400
RETRY_COUNT:           0
```

**Probe C — below-limit control.** The **same model**, the same request, only `num_predict` varied:

| `num_predict` | result |
|---:|---|
| 131072 | **REJECTED 400** |
| **65536** | **ACCEPTED** |
| 65535 | ACCEPTED |
| 32768 | ACCEPTED |

So the model's limit is **exactly 65536**, and the boundary is **inclusive** — `==` is accepted, `>`
is rejected. The rejection is a clean, deterministic function of the sent value, not a flake.

**Probe G — measurement configuration.** `WispConfig().max_tokens == 131072`. The prior
measurement's `32768` was a harness `config.replace(...)`, never a repository change.

---

## 5. Value-Flow Trace

```text
config.max_tokens = 131072                       config.py:70-75
        |                                        "Max tokens per response
        |                                         (set to null/None for no limit)"
        v
OllamaClient.__init__                            ollama_client.py:94
        self.max_tokens = config.max_tokens      <- NO transformation
        |
        v
generate / generate_stream_events                ollama_client.py:302-304 / 373-375
        options = {"temperature": self.temperature}
        if self.max_tokens is not None:
            options["num_predict"] = self.max_tokens   <- RAW PASS-THROUGH
        |
        v
payload["options"] = options                     ollama_client.py:326 / :396
        |
        v
POST /api/chat                                   _post_stream:678
        |
        v
Ollama: compare against the model's max output
        |
        +-- <= limit -> accepted
        +-- >  limit -> HTTP 400
```

**Every transformation:** exactly **one** — a `None` check. There is no clamping, no negotiation, no
discovery, no validation.

### The questions from §5, answered from source and probes

| Question | Answer | Evidence |
|---|---|---|
| Is `max_tokens` a global upper bound? | **No** — it is a *requested output budget*, per response | `config.py:73` |
| Is it provider-independent in intent? | **Yes** — it lives in `WispConfig`, not in a provider | `config.py:70-75` |
| Is `num_predict` equivalent to `max_tokens`? | **Semantically close, not identical** — see §7 | `ollama_client.py:302-304` |
| Can Ollama omit `num_predict`? | **Yes** — and that is the only universally-working configuration today | probe: `max_tokens=None` → accepted |
| Does Ollama expose the model maximum? | **No** — only `.context_length` | Probe E |
| Does the client already have metadata access? | **Yes** — `get_model_info()` / `get_context_length()` exist and are used | `ollama_client.py:183`, `:199` |
| Is there a model-info endpoint? | **Yes** — `/api/show` | `ollama_client.py:190` |
| Is the maximum stable per model? | **Yes, as observed** — 65536 accepted, 131072 rejected, repeatably | Probe C |
| Is it discoverable *before* generation? | **NO** — `/api/show` does not carry it | Probe E |
| Can the endpoint reject a value below the limit? | **Not observed** | Probe C |
| What if the limit is unknown? | **The request is sent anyway and the endpoint decides** | Case 4, §10 |

---

## 6. Ollama Contract

Established by probe, not inference:

| Property | Finding |
|---|---|
| `num_predict` meaning | an **output-token budget** for this request |
| unit | tokens |
| `-1` semantics | Ollama's documented "until context/stop" — **not used by Wisp** |
| omitted | Ollama applies its own default; **accepted by the model that rejects 131072** |
| `0` | **accepted by the endpoint** (and by Wisp's config validation) — would generate nothing |
| per-model maximum | **enforced**, at the request boundary, as a **400** |
| maximum discoverable via `/api/show`? | **NO** — only `.context_length` |
| error class | **400** — a client error, i.e. **permanent** |

**The trap that matters.** `/api/show` *does* return a number that looks like a token limit:

```text
nemotron-3-ultra:cloud   .context_length = 262144    max output = 65536
llama3.2:3b              .context_length = 131072    max output = ?
qwen2.5:0.5b             .context_length =  32768    max output = ?
```

`context_length` (262144) is **2× the rejected value** (131072). A fix that clamps to the
discoverable number would leave the request **still rejected** — and would additionally *raise* the
budget for any model where it currently happens to work. **The only available metadata is the wrong
metadata.**

---

## 7. Wisp Token Semantics

| Concept | Wisp meaning | Ollama meaning | Relationship |
|---|---|---|---|
| `max_tokens` | the **requested output budget** for a response; `None` = "no limit" (`config.py:73`); default 131072 | n/a — Ollama has no such field | Wisp-side, provider-independent |
| `num_predict` | n/a — Wisp has no such concept | the **requested output budget** for one call | `options["num_predict"] = max_tokens` (raw) |
| context window | `get_context_length()`, used for display/detection only | `.context_length` in `/api/show` | **a different quantity** |
| maximum output tokens | **Wisp has no representation of it** | enforced; **not exposed by any endpoint** | **the gap** |
| requested output tokens | `max_tokens` | `num_predict` | identity, modulo the `None` check |
| effective output tokens | `UNKNOWN — requires evidence` — Wisp never learns what was actually generated against the budget | decided by the endpoint | **not observable today** |

**Three distinct settings share the name `max_tokens`, and none of them is the same thing:**

| Setting | Where | What it governs |
|---|---|---|
| `config.max_tokens` | `config.py:70` | the **provider** output budget ← F39 |
| `max_tokens_per_historical_result` | `context_pruner.py:439` | context pruning |
| `ResourceBudget.max_tokens` | `multi_agent/resource_budget.py:21`, `graph/executor.py:1063` | a **subagent / graph run** budget |

Only the first is F39. The name collision is worth recording, because a fix aimed at "the token
limit" could easily be aimed at the wrong one.

---

## 8. OpenAI Provider Comparison

**Probe D — measured, not read.** Same config (`max_tokens=131072`), three endpoints:

| provider | `api_base` | sent `max_tokens` | clamped? |
|---|---|---:|---|
| `openrouter` | `https://openrouter.ai/api/v1` | **4096** | yes |
| `nvidia` | `https://integrate.api.nvidia.com/v1` | **16384** | yes |
| `openai` | `https://api.openai.com/v1` | **131072** | **NO** |

The implementation (`openai.py:576-590`) is explicit about its reasoning:

> *"Cloud gateways (OpenRouter, NVIDIA) reject huge max_tokens with 402 'can only afford N' — cap to a
> generous but credit-safe value. **Local Ollama ignores this field anyway.** OpenRouter's
> remaining-credit check is dynamic (e.g. 5403), so use a conservative 4096 for openrouter to stay
> under the typical free-tier balance; nvidia can use 16384."*

Three findings:

1. **The clamp is endpoint-keyed, not model-aware.** It knows nothing about a model's limit. It
   cannot — no endpoint exposes one.
2. **It is a credit-protection workaround, not a limit negotiation.** Its stated purpose is staying
   under a *balance*, not under a *maximum output*.
3. **Its comment contains a claim F39 disproves.** *"Local Ollama ignores this field anyway"* is
   **false**: Ollama enforces it, and rejects the request when it exceeds the model's limit. The
   same reasoning that produced the OpenAI clamp would have produced the Ollama one — the
   belief that Ollama ignores the field is *why* it was left raw.

**Therefore the two implementations are not intentionally different in policy; one of them rests on
an incorrect premise.** But that does not make the Ollama fix a copy of the OpenAI one: the OpenAI
clamp has a value to clamp to (a known balance/endpoint), and the Ollama case **has none**.

`nvidia.py:62` sets `self.max_tokens` and inherits `_build_payload`, so it does **not** duplicate the
clamp — one implementation, two endpoints. **No duplicate authority.** (`nvidia.py:73-76` does hold a
static per-model *context-length* table — a precedent for model metadata, but again of the wrong
quantity.)

---

## 9. Authority Map

| Decision | Current owner | Notes |
|---|---|---|
| the user's requested output budget | `config.max_tokens` | mutable per-call: `semantic_compressor.py:685` temporarily sets 512 |
| the provider's output limit | **the endpoint** | Wisp holds no representation |
| the **model's** output limit | **the endpoint** | **not exposed by any Wisp-reachable API** |
| translation `max_tokens → num_predict` | `ollama_client.py:302-304`, `:374-375` | **raw pass-through** |
| translation `max_tokens → max_tokens` (OpenAI) | `providers/openai.py:576-590` | endpoint-keyed static clamp |
| clamping | **split**: none (Ollama) / two hardcoded hosts (OpenAI) | **inconsistent, and not derived from a contract** |
| request rejection | the endpoint | 400, permanent |
| retry | `_post_stream:696` (5xx only), `_post_with_retry:250` (transient) | **a 400 is not retried** |
| error classification | `OllamaError("Ollama HTTP error: {e}")` | body logged at ERROR, **not surfaced to the caller** |

**Is there one authority, a duplicate, or none?**

```text
Wisp's token budget:      one owner  (config.max_tokens)
The model's limit:        NO owner   (Wisp has no representation)
The boundary between them: NO owner  (each client improvises, or does nothing)
```

**The architectural question the brief poses** — *who should own the boundary between Wisp's
provider-independent token budget and Ollama's provider/model-specific output limit?* — currently has
**no owner**. That is the gap, and assigning one is a decision, not a repair.

---

## 10. Failure Semantics

| Case | Current behaviour | Existing contract | Desired behaviour | ADR? |
|---|---|---|---|---|
| **1** `config < model max` | accepted, honoured | yes | unchanged | no |
| **2** `config == model max` | **accepted** (verified: 65536) | yes | unchanged | no |
| **3** `config > model max` | **HTTP 400, turn cannot run at all** | **none** | **undecided** — clamp, omit, or refuse? | **YES** |
| **4** model max unknown | sent anyway; the endpoint decides | none | **undecided** | **YES** |
| **5** metadata unavailable | `get_context_length()` falls back to 128000 — but that is *context*, not output | n/a | no change (wrong quantity) | no |
| **6** model disappears / changes | a different error (`list_models` empty, or a 404 on generate) | n/a | unchanged | no |
| **7** unexpected 400 | `OllamaError("Ollama HTTP error: 400 …")` — **generic**; the actionable body is only in the log | none | surface the reason | no (mechanical) |
| **8** `max_tokens = None` | `num_predict` **omitted** → **accepted by the rejecting model** | documented (`config.py:73`) | unchanged — **this is the escape hatch** | no |
| **9** `max_tokens = 0` | accepted by config **and** by the endpoint; `num_predict: 0` sent; would generate nothing | none | **undecided** — should config reject 0? | **YES** (minor) |

**Case 8 is the load-bearing one.** The documented setting `max_tokens: null` **already avoids F39
entirely**, verified against the model that rejects 131072. So a user-facing remedy exists today; what
does not exist is a *default* that works across models.

**Case 9 is a real, separate sharp edge:** the schema is `(int, type(None))` with **no minimum**
(contrast `temperature`, which declares `min`/`max` at `config.py:65-66`). `max_tokens=0` is therefore
accepted and produces an empty response with no diagnostic.

---

## 11. Capability Discovery

**Probe E — is the maximum output discoverable?**

```text
GET  /api/show  {"model": "nemotron-3-ultra:cloud"}
  -> top-level keys : capabilities, details, model_info, modified_at, thinking
  -> model_info     : {..., ".context_length": 262144}
  -> keys matching predict|output : NONE
  -> capabilities   : ["completion", "thinking", "tools"]
```

| Question | Answer |
|---|---|
| Does Ollama expose the max output? | **NO** |
| Does it expose *a* token number? | yes — `.context_length`, the **context window** |
| Is that number usable as a clamp? | **NO** — 262144 > the rejected 131072 |
| Does Wisp already fetch `/api/show`? | yes — `get_model_info()` (`ollama_client.py:183`) |
| Is that machinery reusable? | yes, for *context* — it is the right shape and the wrong field |
| Any other local endpoint? | none observed that carries the limit |

**So Option C (model-aware clamp) and Option D (capability discovery) are not implementable from
available metadata.** This is the finding that narrows the option space to policy choices.

---

## 12. Offline / Local-First Analysis

| Scenario | Impact |
|---|---|
| offline Ollama, local model | **unaffected by F39** *if* the model's limit ≥ `config.max_tokens`; otherwise the model is unusable |
| cloud-routed Ollama model | where F39 was found |
| model metadata unavailable | `/api/show` failing already degrades to a 128000 *context* default; it has no bearing on output limits |
| cold-start discovery | **no discovery exists to cold-start** |
| disconnected environments | **preserved by every option except D** — a static cap (B) needs no network; discovery (D) adds a request but only to the already-required Ollama connection |

**`OFFLINE_COMPATIBILITY: PRESERVED`** for the currently-viable options. Note that F39 itself is an
*offline* failure mode: it needs no network beyond the local daemon, and it makes a locally-served
model unusable with no configuration error at startup — the failure appears only when a turn runs.

---

## 13. Retry Analysis

**Probe F — measured.**

```text
one logical call with num_predict=131072
  -> POST attempts observed: 1
```

`_post_stream:696` retries **only on `>= 500`**; a 400 falls straight through to
`raise OllamaError` at `:701`. The non-streaming `_post_with_retry:250` retries on
`is_transient_status(...)` and `>= 500` — likewise not on 400.

```text
RETRY_COUNT: 0
```

**So the consequence is the good one:** one logical turn produces **one** rejected request, not N.
No retry amplification, no wasted quota, no latency penalty. A 400 is already classified as permanent
by behaviour — which is also why the failure is *immediate and total* rather than degraded.

---

## 14. Test Coverage

**Probe: is `num_predict` asserted anywhere?**

```text
grep -rn 'num_predict' tests/          -> NOTHING
grep -rn 'num_predict' wisp/ (excl. ollama_client) -> NOTHING
```

| Question | Finding |
|---|---|
| Is `num_predict` asserted? | **NO — never, in any test** |
| Are model-limit errors covered? | **NO** |
| Is the default covered? | **YES** — `tests/test_config.py:34`: `assert cfg.max_tokens == 131072  # Raised to match industry-grade coding agent limits` |
| Is the OpenAI clamp covered? | **partially** — `tests/test_openai_provider.py:20` sets `max_tokens = 4096`, but 4096 is below every clamp threshold, so **the clamp branch is never exercised** |
| Is Ollama request construction covered? | **NO** — `tests/test_ollama_client.py:13` uses `max_tokens = 4096`, far below every model limit |
| Is there a mock Ollama server? | **NO** — no `/api/chat` or `/api/show` fixture exists |

**The exact coverage gap:** the tests pick `4096`, a value below every model's limit, so the
pass-through is *exercised* but never *stressed*. And because `num_predict` is never asserted, the
translation itself is untested. **A test that asserted `options["num_predict"] == config.max_tokens`
would have documented the behaviour; a test that asserted it against a *model limit* would have caught
F39.** Neither exists.

---

## 15. ADR-0016 Measurement Impact

**Probe G** established the facts: repo default 131072, harness 32768, defaults unchanged.

| Question | Answer |
|---|---|
| Were the 7 F39-invalid attempts correctly excluded? | **YES.** The provider rejected the request before generating; there was no turn, no tool call, no verdict. That is an **invalid measurement**, not a valid failure — exactly the §19 distinction |
| Does the workaround make the 28-turn population valid? | **YES, as a declared configuration.** `max_tokens` was set explicitly in the harness and is recorded in the report |
| Does it introduce sampling bias? | **Structurally, no.** The P3 verdict is driven by `wrote_code` and `verify_ok_after_edit` — both decided by **tool results** (a file was written; a command exited non-zero), not by the length of generated text. `num_predict` bounds generated text only |
| Is the measured rate conditional on `max_tokens=32768`? | **Yes, strictly.** With the default 131072 every `nemotron` turn would have been rejected, and the rate would have been 100% degenerate — as it was for the two local models |
| Must future periods record effective token configuration? | **YES** — `num_predict` and `done_reason` should both be recorded |
| Must F39 be repaired before the next period? | **NO.** The workaround is legitimate configuration. But it must be **explicit**, never silent |

**Residual risk, stated rather than dismissed:** a tighter output budget *could* truncate a turn early
and cause it to skip a verification. Truncation was **not instrumented** in the population, so this is
bounded but not eliminated. `MEASUREMENT_IMPACT: CONDITIONAL`.

**The prior report is not invalidated and its numbers are not recalculated.**

---

## 16. Architectural Options

Each assessed on the properties the brief lists. **No option is ranked.**

### A — Blind pass-through *(current)*
```text
num_predict = max_tokens
```
Correctness: **fails** for any model below the default · User semantics: unchanged (but the turn dies) ·
Portability: none · Latency: none · Caching: n/a · Offline: fine · Failure mode: **total, at turn time** ·
Replay: n/a · Observability: **poor** (generic 400) · Authority: none · Testability: trivial ·
Back-compat: n/a · Complexity: none

### B — Static global clamp
```text
num_predict = min(max_tokens, GLOBAL_CAP)
```
Correctness: works only if `GLOBAL_CAP` ≤ every reachable model's limit — **unknowable, so it is a
guess** · User semantics: **CHANGED** — a configured 131072 silently becomes the cap · Portability:
good · Latency: none · Caching: none · Offline: fine · Failure mode: silently reduces the budget ·
Replay: **changed** (the effective value becomes config-dependent) · Observability: none added ·
Authority: **new** (whoever picks the cap) · Testability: easy · Back-compat: **breaks** for users who
deliberately set a high budget · Complexity: trivial

### C — Model-aware clamp
```text
num_predict = min(max_tokens, MODEL_LIMIT)
```
**Not implementable** — the model limit is not discoverable (§11). Correctness: would be ideal ·
everything else: **moot**

### D — Capability discovery
```text
discover limit -> clamp -> send
```
**Not implementable from available metadata.** Would additionally require a cache, an invalidation
rule, a fail-open/fail-closed policy, and a new request on the hot path · Offline: preserved (the
discovery target is the same daemon) · Authority: **new** · Complexity: **high**

### E — Omit `num_predict` when the value exceeds a known limit
**The condition cannot be evaluated** — no known limit exists. Variant "always omit" is viable and
equivalent to `max_tokens: null`; it makes `max_tokens` **unenforced** for Ollama · User semantics:
**CHANGED** (the setting becomes advisory) · Correctness: works for every model · Complexity: trivial

### F — Fail before the network request
```text
reject incompatible configuration locally
```
**Requires the limit to compare against** — unavailable. A weaker form ("refuse any value above
`X`") is option B with a different failure mode · Failure mode: **fail-closed**, at startup or first
turn · Authority: **new** · Complexity: low

### G — Provider capability abstraction
```text
move model/provider limits into a provider capability contract
```
Correctness: would be the general answer · **but the data does not exist to populate it** for Ollama ·
Authority: **new, and would become the owner of the boundary** · Complexity: **highest** ·
Back-compat: touches every provider · ADR: **required**

### H — Surface the constraint, do not guess it *(added: not in the brief's list, and the only option that changes no semantics)*
The 400 body **already contains** the model's limit (*"exceeds model's maximum output tokens (65536)"*).
Wisp could classify this 400 as a **configuration error** and surface the limit and the remedy
(`set max_tokens ≤ 65536, or null`).

Correctness: **does not fix the failure** — it explains it · User semantics: **unchanged** ·
Portability: good (parse-with-fallback) · Latency: none · Offline: fine · Failure mode: unchanged
(but **legible**) · Observability: **the whole point** · Authority: none · Testability: easy ·
Complexity: **low** · ADR: **no**

---

## 17. Contract Classification

```text
REPAIR_CLASS: ARCHITECTURE_CHANGE
```

**Why.** The brief's own test:

> *Does Wisp already have an explicit provider contract that says provider clients must adapt
> provider-independent token settings to provider/model limits?*

**NO.** Established from three independent directions:

1. The `Provider` protocol (`providers/protocol.py:15-170`) declares `generate_stream_events`,
   `generate_stream_events_async`, `health_check`, `list_models`, `get_model_info`,
   `get_context_length`, `generate_structured` — **nothing about output limits, budgets, or
   clamping.**
2. The only clamp in the tree (`openai.py:576-590`) is a hardcoded `api_base` lookup, and
   `api.openai.com` is **not** in it — so it is not even a rule about *that* provider.
3. Its stated rationale rests on a premise F39 **disproves**: *"Local Ollama ignores this field
   anyway."*

**But it is not purely architectural either.** Two sub-parts are mechanical:

- **H** (surface the constraint) changes no semantics and restores nothing — it is pure
  observability.
- **Case 7** (the generic `OllamaError` message) is the same gap.

**So:** the *diagnosability* half is `MECHANICAL_REPAIR`; the *behavioural* half is
`ARCHITECTURE_CHANGE`, because every working behaviour change alters `max_tokens` semantics, retry
semantics, or introduces a new authority. The brief requires exactly one classification; the
**governing** one is `ARCHITECTURE_CHANGE`, with the mechanical sub-part named in §19.

---

## 18. ADR Requirement

```text
ADR_REQUIRED: YES
```

Against §19's list, any working repair introduces at least one of:

| Trigger | Which options |
|---|---|
| new token-budget semantics | **B, E** (the effective budget stops equalling the configured one) |
| changed user-visible semantics | **B, E, F** |
| new fail-open / fail-closed policy | **D, F** |
| a new model-discovery authority | **C, D, G** |
| a new provider capability abstraction | **G** |
| new persistent provider metadata (a cache) | **D** |
| changed retry semantics | the retry-on-400 variant |

**And stop condition 2 of the brief is met directly:** *"fixing it requires changing `max_tokens`
semantics."* Every option that actually makes the request succeed either silently reduces the
configured budget (B, E) or refuses to run (F).

**Stop condition 9 is also worth recording, narrowly:** there is no *duplicate* authority today
(one clamp implementation, two endpoints — §8), but there are **three** places where a token limit is
decided (`config.py`, `ollama_client.py`, `openai.py`) with **no** place that owns the boundary
between them (§9).

**Per §24, this phase STOPS here.** No repair was designed past the boundary, and none was
implemented.

---

## 19. Minimal Repair Boundary

**Not defined — `ADR_REQUIRED: YES`.** Per §20, the boundary is specified *only* when the ADR
requirement is `NO`.

What **can** be stated without crossing the boundary, because it changes no semantics and no
authority:

```text
MECHANICAL SUB-PART (available now, no ADR):
  production files expected:  wisp/ollama_client.py  (the 400 classification only)
  functions expected:         _post_stream's HTTPError branch  -> classify a
                              "max_tokens … exceeds … maximum output tokens (N)"
                              body as a CONFIGURATION error carrying N
  tests expected:             one test asserting the surfaced message names the limit
  configuration changes:      none
  dependency changes:         none
  default changes:            none
  migration:                  none

BEHAVIOURAL PART:            NOT DEFINED — requires the decision in §18
```

The decision the ADR must make, in one line:

> **When Wisp's configured output budget exceeds a provider model's limit, should Wisp clamp, omit,
> refuse, or negotiate — and who owns that boundary?**

---

## 20. Risks

| Risk | Assessment |
|---|---|
| A naive "clamp to the model's context length" fix | **Would make it worse.** The discoverable number (262144) is *larger* than the rejected value (131072). This is the most likely wrong fix and it is available from existing code (`get_context_length()` is already wired) |
| A static cap chosen by guesswork | Silently reduces every user's budget; the "right" value is unknowable from inside Wisp |
| Repairing F39 as if it were mechanical | Would introduce token-budget semantics with no decision record |
| The `max_tokens` name collision | Three unrelated settings share the name (§7); a fix aimed at "the token limit" can miss |
| `max_tokens = 0` | Accepted by config and by the endpoint; produces an empty response with no diagnostic |
| The OpenAI clamp's false premise | *"Local Ollama ignores this field anyway"* is wrong; the same premise may appear elsewhere |
| Measurement contamination | Any future period run with a silent workaround repeats F39's blindness; the effective budget must be recorded |
| F40 | Still open, unrelated, and also a shape assumption — see the ADR-0016 report |

---

## 21. Remaining Unknowns

1. **What is the real distribution of model output limits across Ollama models?** Only one limit
   (65536) was observed directly; `llama3.2:3b` and `qwen2.5:0.5b` accepted 131072 and 32768, but
   their true maxima were never probed.
2. **Is the limit stable across an Ollama version or a cloud-model revision?** Observed stable
   within this session; not tested across upgrades.
3. **Does the limit vary by *route* (local vs cloud) for the same model name?** Unknown.
4. **Would a retry-on-400 negotiation be reliable?** The body carries the limit and is stable in
   form, but it is an undocumented error string.
5. **Why was 131072 chosen as the default?** `tests/test_config.py:34` says *"Raised to match
   industry-grade coding agent limits"* — a product rationale, not a provider one. Whether it should
   remain is a decision input.
6. **Was any ADR-0016 turn truncated?** Not instrumented; `MEASUREMENT_IMPACT` is `CONDITIONAL`
   partly because of this.

---

## 22. Recommended Next Phase

**A decision, not a repair.** In order:

1. **`architecture-decision-engineering` on the boundary ownership question** (§18). It must settle:
   clamp / omit / refuse / negotiate; who owns the boundary; and whether the OpenAI clamp is
   subsumed into it or left as an endpoint-specific exception. This is the blocking step.
2. **Then** a `reliability-phase-engineering` implementation of whatever the decision authorises —
   **plus** the mechanical sub-part in §19, which can ship independently and without an ADR because
   it changes no semantics.
3. **F40** remains open and is the same defect class as F37 (an assumption about a value's shape);
   it deserves its own recon.
4. **Then** the ADR-0016 measurement period, now with `num_predict` and `done_reason` recorded.

**Do not repair F39 in this phase, and do not enable the stagnation gate.**

---

## 23. Final Status

```text
=== STATUS: REQUIRES ARCHITECTURE DECISION ===

F39:
CONFIRMED

ROOT_CAUSE:
The Ollama client forwards config.max_tokens (default 131072) verbatim as
options.num_predict, and Ollama rejects any value above the model's maximum
output — which Wisp cannot discover, because /api/show exposes only
.context_length, a different and larger quantity.

REPAIR_CLASS:
ARCHITECTURE_CHANGE
  (diagnosability sub-part is MECHANICAL; the behavioural part changes
   max_tokens semantics and needs the decision)

ADR_REQUIRED:
YES
  stop condition 2 met: every working fix changes max_tokens semantics

PRODUCTION_CHANGE:
NO   (0 files; 0 tests; 0 defaults)

MEASUREMENT_IMPACT:
CONDITIONAL
  the 7 rejected attempts were correctly excluded as INVALID;
  the 28-turn population is valid as a DECLARED configuration (32768);
  the verdict is structurally robust (it depends on tool results, not text
  length) but truncation was not instrumented;
  the prior report is NOT invalidated and its numbers are NOT recalculated.

NEXT:
architecture-decision-engineering on the boundary-ownership question
( clamp / omit / refuse / negotiate, and who owns it ),
then a reliability phase for the authorised repair plus the mechanical
error-surfacing sub-part.
Do NOT repair F39 here. Do NOT enable the stagnation gate.

========================
```

### Required metrics

```text
REPRODUCED:                YES
CONFIGURED_MAX_TOKENS:     131072
MODEL_MAX_OUTPUT:          65536          (boundary verified: 65536 accepted, 131072 rejected)
NUM_PREDICT_SENT:          131072
HTTP_STATUS:               400
RETRY_COUNT:               0
BELOW_LIMIT_CONTROL:       PASS           (65536 / 65535 / 32768 accepted)
OPENAI_COMPARISON:         DIFFERENT      (clamps by endpoint, not model; api.openai.com unclamped)
MODEL_LIMIT_DISCOVERABLE:  NO             (/api/show exposes .context_length only)
OFFLINE_COMPATIBILITY:     PRESERVED
ADR_REQUIRED:              YES
PRODUCTION_CHANGES:        0
TEST_CHANGES:              0
DEFAULT_CHANGES:           0
MEASUREMENT_IMPACT:        CONDITIONAL
```
