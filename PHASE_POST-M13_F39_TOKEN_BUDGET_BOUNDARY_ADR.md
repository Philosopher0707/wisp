# PHASE POST-M13 — F39 TOKEN-BUDGET BOUNDARY ARCHITECTURE DECISION

**Mode:** ARCHITECTURE DECISION ONLY
**Production changes: 0** · **Test changes: 0** · **Default changes: 0** · **Dependency changes: 0**

---

## 1. Mission Result

**RATIFIED — as ADR-0038.**

> **The configured output budget is sent verbatim; a provider's refusal of it is a configuration
> incompatibility, not a capability to be guessed.**

The decision **declines to invent the boundary's data** and instead **names its owner**. It creates no
abstraction, adds no behaviour to the running system, and changes no default.

**The most consequential finding of this phase is that the ratified policy requires no behavioural
change at all.** R1–R4 and R7–R10 describe behaviour that *already holds*. The only gap is **R5/R6** —
the refusal has no name and no diagnostic. So the entire implementation boundary is **diagnostic**,
and the behavioural sub-phase is **empty**.

The four policy questions, answered:

| Question | Answer |
|---|---|
| **Q1** — what happens when the budget is too large? | **Send verbatim; classify the refusal.** Not clamp, not omit, not negotiate |
| **Q2** — who owns the boundary? | **The provider implementation** — the single owner of adaptation. `WispConfig` owns the requested budget and never adapts it |
| **Q3** — what does `max_tokens` mean? | **A — an exact requested provider output budget** |
| **Q4** — unknown capability? | **One rule, no cases:** send verbatim; on refusal, classify and surface |

---

## 2. Established Facts

Re-verified against current source before deciding. **All hold.**

```text
config.max_tokens          config.py:70-75   default 131072
                           declared         "Max tokens per response
                                             (set to null/None for no limit)"
                           pinned by        tests/test_config.py:34
                           fingerprint()    NOT included (config.py:1188-1198)

translation                ollama_client.py:302-304 and :374-375
                           options["num_predict"] = self.max_tokens
                           guarded by `if self.max_tokens is not None` — ONE check, no transform

Ollama num_predict         a requested output-token budget, enforced per request

observed model             nemotron-3-ultra:cloud, maximum output 65536
                           65536 accepted · 65535 accepted · 32768 accepted · 131072 -> HTTP 400
                           the boundary is INCLUSIVE

metadata                   /api/show -> model_info{".context_length": 262144}
                           NO key for predict | output | max
                           context_length (262144) > the rejected value (131072)

retry                      _post_stream:696 retries on >= 500 ONLY
                           one logical call -> exactly ONE POST
```

**`context_length` is NOT the output limit**, and for the observed model it is **larger** than the
rejected value — so it can never be used as a proxy.

---

## 3. Current Authority Map

| Decision | Owner today | Notes |
|---|---|---|
| the requested output budget | **`WispConfig.max_tokens`** | authoritative; per-call mutable (`semantic_compressor.py:685` sets 512) |
| the model's output capacity | **the endpoint** | authoritative *externally*; Wisp holds no representation |
| the provider's affordability cap | **`providers/openai.py:576-590`** | endpoint-keyed, hardcoded: OpenRouter 4096, NVIDIA 16384 |
| translation `max_tokens → num_predict` | `ollama_client.py:302-304`, `:374-375` | **raw pass-through** |
| translation `max_tokens → max_tokens` (OpenAI) | `openai.py:576-590` | the affordability clamp |
| **the boundary between budget and capacity** | **NOBODY** | **the gap this ADR closes** |
| request rejection | the endpoint | 400 |
| retry | `_post_stream:696`, `_post_with_retry:250` | 400 is **not** retried |
| error classification | `OllamaError("Ollama HTTP error: {e}")` | generic; the actionable body is only logged |

**One owner exists for the budget, one for the endpoint's own limit, and none for the boundary.** That
is the authority gap, and it is an **absence**, not a conflict — which is why this ADR *assigns*
rather than *reconciles*.

**The `max_tokens` name collision** (recorded so a fix is not aimed at the wrong one): three unrelated
settings share the name — `config.max_tokens` (the provider budget, F39), `context_pruner
.max_tokens_per_historical_result`, and `ResourceBudget.max_tokens` (subagent/graph). Only the first
is in scope.

---

## 4. Problem Statement

> **Who owns the boundary between a provider-independent requested output budget and a
> provider/model-specific capability that Wisp cannot currently observe?**

The problem is **not** "what number should 131072 be clamped to". That framing presumes a number
exists to clamp to, and the forensic phase proved it does not: the only discoverable token value is
the **context window**, and for the observed model it is *larger* than the rejected value. Any clamp
built on it would leave the failure in place while silently raising the budget everywhere the request
currently succeeds.

So the decision must resolve **ownership** in the absence of data — and the honest answer to "what
should we adapt to?" when nothing is observable is **"nothing, and say so"**.

---

## 5. Decision Questions

### Q1 — what happens when the configured budget is too large?

**SEND VERBATIM; CLASSIFY THE REFUSAL.**

| Candidate | Verdict |
|---|---|
| `CLAMP` | **Rejected** — requires a capacity value that does not exist (R3) |
| `OMIT` | **Rejected as an automatic behaviour** — silently makes the setting advisory (R1, R2). It survives as the *user's explicit* mode, `None` |
| `REFUSE` | **Adopted, in its honest form** — Wisp does not refuse the request, but it **names** the refusal when the provider issues one (R5) |
| `NEGOTIATE` | **Rejected** — see below |
| `LEAVE PROVIDER-DEFINED` | **Rejected as a default** — that is `None`, and it must remain the user's choice (R2) |

**"NEGOTIATE" defined precisely, and rejected.** Negotiation could only mean: send the budget, parse
the provider's error string for a limit, retry with that value. That is rejected because it (a) makes
an **undocumented error string** a capability source, (b) turns a **permanent 400 into a retry**,
(c) creates a **second authority** over capacity — the provider's error format, and (d) makes the
effective budget depend on **how a failure happened to be phrased**. R6 forbids deriving capability
from a response; R7 forbids retrying this class.

### Q2 — who owns the boundary?

**THE PROVIDER IMPLEMENTATION — one owner (R10).**

```text
WispConfig            owns the REQUESTED budget      (and never adapts it)
Provider implementation  owns ADAPTATION            (and is the single policy owner)
```

**No new abstraction is introduced.** The owner already exists; what was missing was the assignment
and the policy. Today, with no typed capability source, the provider's policy is **"send verbatim"** —
a *defined* policy, not an absence of one. That is the point: a future provider gaining a capability
inherits an owner instead of improvising one.

### Q3 — what does `max_tokens` mean after the decision?

**A — AN EXACT REQUESTED PROVIDER OUTPUT BUDGET.**

| Candidate | Verdict |
|---|---|
| **A** exact requested budget | **Adopted** — matches the declaration (`config.py:73`), the code, and R1 |
| B desired upper bound subject to capability | **Rejected** — silently redefines a user-facing setting; the user's configured request and their effective request diverge with no signal |
| C global logical budget providers may translate | **Rejected** — that is the abstraction this ADR declines to build, and it would make every provider a translator |
| D other | none needed |

### Q4 — what happens when provider capability is unknown?

**ONE RULE, NO CASES.** Send verbatim; on refusal, classify and surface.

```text
local provider        ─┐
cloud provider         │
offline mode           ├──  ALL TAKE R4:  send verbatim
known model            │                    -> accepted, or
unknown model         ─┘                    -> classified + surfaced
```

The **only** thing that varies is what the diagnostic *can include* — the limit, when the response
states it. **No separate semantics are created for any axis**, because separate semantics are how a
system acquires a second policy nobody decided to have.

---

## 6. Option Analysis

### Option A — Preserve exact budget / fail at provider *(current)*
Preserves semantics exactly, and permits runtime failure. **Adopted as the behaviour**, but
*incomplete*: it fails without a name. A and G together are the decision.

### Option B — Static global cap
Requires someone to choose `GLOBAL_CAP`. **No value is defensible**: low enough to be safe is
arbitrary, high enough to be harmless does not fix the failure. It silently rewrites the user's
configured budget, and it cannot guarantee compatibility because capacity is unknown. **Rejected** (R3).

### Option C — Model-aware capability contract
`min(max_tokens, provider.model_max_output)` is the ideal shape — **and the value does not exist.**
Ollama publishes no such field. It becomes reachable only if a provider gains a typed source, which is
exactly the condition R8 anticipates. **Deferred, not built.**

### Option D — Capability discovery + cache
Requires a discovery source (none), and would add: a request, a cache, a lifetime, an invalidation
rule, a staleness policy, a fail-open/fail-closed decision, and a new persistent authority — all for a
fact the endpoint does not publish. **Rejected.**

### Option E — Omit `num_predict`
**Already exists, as `max_tokens = None`, and works** — verified against the model that rejects
131072. The decision **keeps it as the explicit user-controlled escape hatch** and **forbids it as an
automatic fallback** (R2). Making it automatic would convert an exact budget into an advisory one
without a decision.

### Option F — Provider-error renegotiation
Rejected on four grounds, above (Q1). It is the option that *looks* most helpful and is the most
dangerous: it would make Wisp's effective budget a function of a provider's error prose.

### Option G — Surface the error only *(adopted, alongside A)*
**This is the entire implementation.** It is semantic-preserving, independent of every behavioural
policy, and compatible with all of them — including R8, if it ever activates. **R5 + R6.**

---

## 7. OpenAI Clamp Analysis

**Measured** (`openai.py:576-590`, same config `max_tokens=131072`):

| provider | `api_base` | sent `max_tokens` |
|---|---|---:|
| `openrouter` | `https://openrouter.ai/api/v1` | **4096** |
| `nvidia` | `https://integrate.api.nvidia.com/v1` | **16384** |
| `openai` | `https://api.openai.com/v1` | **131072 — unclamped** |

**Decision: option D — reclassify, leave untouched.** Not folded in (R9), not removed.

The distinction this ADR exists to protect:

```text
model output capacity   a property of the MODEL     -> F39; Wisp cannot observe it
provider credit cap     a property of the ACCOUNT   -> the OpenAI clamp; Wisp hardcodes it
```

Its own comment states its purpose — *"cap to a generous but credit-safe value"* — and its rationale
contains a claim F39 **disproves**: *"Local Ollama ignores this field anyway."* Ollama enforces it and
rejects the request. Correcting that comment is a **documentation** change in the mechanical
sub-phase; the clamp itself is not a defect and is not in scope.

---

## 8. `None` Semantics

**Unchanged. An explicit user-selected "provider decides" mode. Never an automatic fallback.**

Verified: `max_tokens=None` → `options` sent as `{"temperature": 0.2}` (no `num_predict`) → **accepted**
by the model that rejects 131072.

R2 exists specifically to stop a future implementer from "fixing" F39 by silently omitting the
parameter on failure. That would make `max_tokens` advisory without anyone deciding it, and it would
do so **only on the failure path** — the least observable place to change a setting's meaning.

---

## 9. Retry Analysis

**Unchanged.** `_post_stream:696` retries on `>= 500` only; a 400 raises immediately. Measured:
**one logical call → one POST**. R7 fixes this so that no negotiation is added by accident.

Had Option F been adopted, it would have had to define: maximum renegotiation attempts; whether one
retry only; whether it shares the existing retry budget; whether it fires only when the response
exposes a limit; what happens when parsing fails; whether a second 400 terminates the turn. **None of
that is needed, because negotiation is rejected** — and that is a large part of why it was rejected:
it is the only option that would have required a new bounded-retry policy.

---

## 10. Offline Analysis

| Scenario | Behaviour under the ratified policy |
|---|---|
| local Ollama | send verbatim; accept, or classify + surface. **No new network** |
| cloud-routed Ollama | identical — the daemon's own egress is unchanged |
| no model metadata | **irrelevant** — capability is not sought (R3) |
| disconnected daemon | a different error class entirely (connection failure), already transient-retried |
| offline mode | **preserved** — no metadata service, no extra request, no cache |

**The decision requires no capability discovery, so it cannot break local-first operation.** That is a
direct consequence of declining Option D.

---

## 11. Measurement Impact

**ADR-0016 is not modified.** Its contract — *"a measurement period showing how many turns become
`INCONCLUSIVE`"* — is untouched, and this decision changes no verdict.

The ADR-0016 population that ran used a **declared harness override** (`max_tokens=32768`) to avoid
F39. That was legitimate configuration, and it was recorded. The obligation this ADR adds is that
future periods record it **per observation**:

```text
configured max_tokens          MUST be recorded
effective num_predict          MUST be recorded   (equal under R1/R4)
done_reason                    MUST be recorded   (truncation was never instrumented)
provider and model             MUST be recorded
```

**This does not amend ADR-0016** — it is an instrumentation obligation on future phases. If R8 ever
activates (a provider adapts the budget), the measurement contract would need an explicit
effective-vs-configured statement, and **that would be a follow-up ADR** rather than a silent edit.

---

## 12. Replay / Durability

**No new persistence.**

| Item | Kind | Persisted? |
|---|---|---|
| configured `max_tokens` | **configuration** | already a config field |
| effective `max_tokens` | derived execution fact | **equals the configured value** under R1/R4 — nothing new |
| provider capability | derived provider fact | **Wisp holds none** — nothing to persist |
| provider / model | configuration | already in `fingerprint()` and the session |
| negotiation result | — | **does not exist** under R7 |

Replay reconstructs **outcomes** from the journal; request parameters were never part of it, and this
ADR does not add them. Adding persistence "because it might be useful" is exactly what §14 of the brief
forbids.

**R8 carries a standing obligation:** if a provider ever adapts, the effective value becomes a derived
execution fact and **must** be recorded with the call. That is a follow-up attached to R8, not new
persistence now.

**Adjacent observation, not changed:** `max_tokens` is **not** in `WispConfig.fingerprint()`
(`config.py:1188-1198`), so a mid-session change may not invalidate a cached core. Recorded as a
follow-up question in the ADR; **out of scope here**.

---

## 13. Security

**F39 does not affect security, and this decision introduces no security authority.**

Verified by source: **0 matches** for `max_tokens` / `num_predict` in
`infra/security.py`, `core/approval_gate.py`, `tool_executor.py`, `core/verification.py`,
`core/goal.py`, `core/acceptance.py`, `graph/verifier.py`.

The boundary is strictly between **`WispConfig` and the provider client**. Authorization, approval,
the sandbox, `ToolExecutor`, verification, P3 and the goal state are untouched — the provider request
is constructed *after* all of them, and a rejection there fails the turn, it does not bypass anything.

---

## 14. Decision Matrix

| Option | Semantics | Correctness | Offline | Portability | Latency | Authority | Retry impact | Replay impact | Complexity | ADR |
|---|---|---|---|---|---|---|---|---|---|---|
| **A** verbatim *(current)* | exact, truthful | fails, unnamed | preserved | n/a (provider-agnostic) | none | **none — the gap** | none | none | none | **this ADR** |
| **B** static cap | **changed** — silent reduction | works only if the cap happens to be safe | preserved | good | none | **new** (who picks the cap) | none | **changed** (effective ≠ configured) | trivial | needed |
| **C** model-aware clamp | exact, subject to capability | **ideal — unimplementable** | preserved | needs a typed source per provider | none | provider impl | none | changed if it ever fires | low | covered by R8 |
| **D** discovery + cache | exact, subject to capability | **unimplementable** | preserved (same daemon) | per-provider | **+1 request** | **new, persistent** | none | changed | **high** | needed |
| **E** omit automatically | **advisory** — the setting stops binding | works everywhere | preserved | good | none | **new** (a fallback policy) | none | changed | trivial | needed |
| **F** error renegotiation | **changed, and format-dependent** | works where the prose is stable | preserved | fragile | **+1 round trip** | **second authority: the error format** | **changes: 400 becomes retryable** | changed | medium | needed |
| **G** surface only | **unchanged** | **does not fix; makes legible** | preserved | universal | none | **none added** | **none** | none | **low** | **this ADR** |

**Compatible with the existing architecture without new abstractions: A and G** (the ratified pair),
and **C** the moment a typed source exists (R8). **B, D, E and F each require a new abstraction or a
new authority**, and each changes either the meaning of a user-facing setting or the retry policy.

**No option is ranked and no score is assigned.** The selection criterion is stated in the ADR: which
option preserves truthful semantics without inventing data.

---

## 15. Normative Decision

The ADR's normative rules, reproduced verbatim from **ADR-0038**:

> **R1.** `max_tokens` is an **exact requested output budget**. Wisp SHALL send the configured value to
> the provider verbatim.
>
> **R2.** `max_tokens = None` SHALL omit the provider's output-budget parameter. This is an **explicit
> user-selected "provider decides" mode** and SHALL NOT be applied as an automatic fallback.
>
> **R3.** Wisp SHALL NOT derive a model's output capacity from `context_length`, from any other
> discoverable field, or from a hardcoded default.
>
> **R4.** When a provider cannot establish a model's output capacity from a **typed, non-error**
> source, Wisp SHALL send the configured budget unmodified and SHALL NOT clamp it.
>
> **R5.** When a provider rejects a request because the budget exceeds the model's capacity, Wisp SHALL
> classify it as a **configuration incompatibility** — permanent and non-retryable — distinct from a
> transient provider failure and from a model refusal.
>
> **R6.** The surfaced diagnostic SHALL name the configured budget and, when the provider's response
> states it, the provider-reported limit. Wisp SHALL NOT parse such a response to derive a capability.
>
> **R7.** Wisp SHALL NOT retry a configuration incompatibility. The existing retry classification and
> budget SHALL remain unchanged.
>
> **R8.** When a provider implementation can establish a model's output capacity from a typed,
> non-error source, that provider SHALL own the adaptation: it SHALL send `min(configured, capacity)`
> and SHALL record the **effective** value for that call.
>
> **R9.** Endpoint-specific affordability caps SHALL be classified as **affordability policy**. They
> SHALL NOT be generalised into the capacity boundary and SHALL NOT be used to infer capacity.
>
> **R10.** The provider implementation SHALL be the **single owner** of output-budget adaptation.
> `WispConfig` owns the requested budget and SHALL NOT adapt it.

Every branch has defined behaviour. No rule uses *"try to"*, *"where possible"*, *"reasonable"*, or
*"best effort"*.

---

## 16. ADR Requirement

```text
ADR_REQUIRED: YES  (established by the forensic recon)
ADR_WRITTEN:  YES
ADR_NUMBER:   ADR-0038
```

**Numbering protocol resolved before writing** (§21):

```text
PREFIX:              ADR-
NUMBERING_FORMAT:    ADR-NNNN  (zero-padded 4)
CURRENT_MAX_ADR:     0037
NEXT_ADR:            0038
COLLISION_CHECK:     PASS — no ADR-0038 or ADR-0039 anywhere in the repository
INDEX_CHECK:         the log carries 38 sections and 38 index rows, both including 0038
INDEX_UPDATE:        CONTEXT.md lines 62 and 1022 -> "ADR-0001 … ADR-0038"
```

Appended using the repository's **append-only** convention: a new `## ADR-0038 — …` section before the
`## Decision index`, plus its index row. **No existing ADR was modified, renumbered or reordered.**

---

## 17. Implementation Boundary

The ratified policy is satisfied by current behaviour **except R5 and R6**. The boundary is therefore
**entirely diagnostic**, and split as the brief requires:

```text
MECHANICAL SUB-PHASE                        (authorised; no further ADR)
  production files:   wisp/ollama_client.py     — the HTTPError branch of _post_stream
  behaviour:          classify a rejection whose body states a model output limit as a
                      CONFIGURATION INCOMPATIBILITY, and include the configured value and the
                      provider-reported limit in the surfaced error
  doc fix:            the false "Local Ollama ignores this field anyway" comment in
                      wisp/providers/openai.py
  provider abstractions: none
  configuration changes: none        dependency changes: none
  default behaviour:     unchanged   feature flags: none        migration: none
  persistence:           none        telemetry: none added
  tests:              one asserting the surfaced message names the configured value and the limit

BEHAVIOURAL SUB-PHASE
  EMPTY.
  R1-R4 and R7-R10 describe behaviour that ALREADY HOLDS. This ADR authorises no behavioural
  change — which is why it needs no flag, no default change and no migration.
```

**The two sub-phases are not mixed:** the mechanical sub-phase changes only how a failure is
*reported*; there is no behavioural sub-phase to mix it with.

---

## 18. Rejected Alternatives

| Alternative | Why rejected |
|---|---|
| Static global cap | The cap is invented (R3); it silently rewrites a configured budget (R1); no value is defensible |
| Model-aware clamp | The capacity is not discoverable; the nearby number is the context window, which is **larger** than the rejected value |
| Discovery + cache | No source exists; would add a request, a cache, an invalidation rule, a staleness policy, a fail-open/fail-closed decision and a new persistent authority |
| Automatic omit | Makes `max_tokens` advisory without a decision, and only on the failure path (R1, R2) |
| Error renegotiation | Makes an undocumented error string a capability source (R6); turns a permanent 400 into a retry (R7); creates a second authority; makes the effective budget depend on error prose |
| `context_length` as capacity | The near-miss trap: 262144 > 131072, so it leaves the rejection in place **and** raises the budget wherever it currently works |
| Folding the OpenAI clamp in | Conflates affordability with capacity (R9) — properties of different things |
| Changing the `max_tokens` default | Pinned by a test with a stated product rationale; the default is not the defect — the unnamed refusal is |
| Building a provider capability registry now | §24 of the brief forbids designing beyond the boundary; R8 assigns ownership without building the acquisition |

---

## 19. Risks

| Risk | Assessment |
|---|---|
| A user still hits the 400 | **By design.** The decision makes the failure *legible*, not impossible. Remedies (`max_tokens ≤ capacity`, or `null`) are the user's choice and are documented |
| The decision reads as "do nothing" | It **assigns the boundary's owner** (R8, R10), **names the error class** (R5) and **forbids the three tempting wrong fixes** (R3, R6, R9). Without it the next provider to gain a capability would improvise a policy |
| R8 becomes a licence to build a registry | R8 requires a typed source to **already exist**. It authorises adaptation, not acquisition |
| R6's diagnostic is mistaken for capability parsing | R6 permits *displaying* a stated limit and forbids *deriving* a capability from it. That distinction is the guard rail |
| `max_tokens` absent from `fingerprint()` | Recorded as a follow-up, **not changed** |
| The measurement's declared override is forgotten | §11 makes per-observation recording mandatory for future periods |

---

## 20. Rollback

**Trivially reversible, because the decision adds no behaviour.** Reverting the R5/R6 implementation
restores today's generic `OllamaError` exactly — no state, no migration, no flag.

R8–R10 are constraints on future work rather than behaviour, so abandoning them requires a
**superseding ADR** naming (a) the capability source it intends to use, (b) whether an error string is
an acceptable source, and (c) who then owns the boundary.

**Trigger to revisit:** a provider that **does** expose a typed output-capacity field (R8 then
activates without amending ADR-0038), or a pattern of users hitting the incompatibility — which would
make a *product* answer (a different default, or a guided remedy in the setup/doctor surface) the right
response rather than an architectural one.

---

## 21. Follow-up Work

1. **Mechanical sub-phase** (§17) — the only authorised implementation.
2. **F40** — the iteration wrap-up loop's typed-event assumption; same defect class as F37; needs its
   own forensic recon.
3. **`max_tokens` and `fingerprint()`** — should a mid-session change invalidate a cached core?
4. **`max_tokens = 0`** — the schema has no `min` (unlike `temperature`); accepted by config and by the
   endpoint, and would silently produce an empty response.
5. **Should the default remain 131072?** A product decision, not an architectural one.
6. **ADR-0016 measurement period** — now with `configured max_tokens`, `effective num_predict`,
   `done_reason`, provider and model recorded per observation.

**Explicitly not designed here** (per §24): capability registries, model catalogs, benchmarking,
token-budget optimizers, global schedulers, adaptive planning, graph-level budgets, subagent budgets.

---

## 22. Final Status

```text
=== STATUS: RATIFIED ===

F39:
CONFIRMED

ARCHITECTURE_DECISION:
RATIFIED

ADR:
ADR-0038

MAX_TOKENS_SEMANTICS:
An exact requested provider output budget, sent verbatim.

UNKNOWN_CAPABILITY:
One rule for every provider and mode — send verbatim; on refusal, classify as a
configuration incompatibility and surface it; never infer, never clamp.

RETRY_POLICY:
Unchanged — a refusal of this class is permanent and is not retried.

NONE_SEMANTICS:
Unchanged — an explicit user-selected "provider decides" mode, never an automatic
fallback.

OPENAI_CLAMP:
Reclassified as endpoint-specific affordability policy and left untouched — it is
NOT model output capacity and must not be folded into the boundary.

MEASUREMENT_IMPACT:
ADR-0016 is not modified; future periods must record configured max_tokens,
effective num_predict, done_reason, provider and model per observation.

PRODUCTION_CHANGES:
0

TEST_CHANGES:
0

DEFAULT_CHANGES:
0

NEXT:
implement the MECHANICAL SUB-PHASE only — classify a model-capacity rejection in
wisp/ollama_client.py's _post_stream as a configuration incompatibility and include
the configured value and the provider-reported limit in the surfaced error, plus the
openai.py comment correction, with one test. No behavioural change is authorised.

========================
```

### Final validation (§26)

```text
no production code changed ........................ YES (0 files in wisp/)
no tests changed .................................. YES (0 files in tests/)
no defaults changed ............................... YES (max_tokens = 131072)
no dependency changes ............................. YES
the ADR contains an unambiguous normative rule ..... YES (R1-R10, no vague wording)
unknown capability has defined semantics .......... YES (one rule, no cases)
None has defined semantics ........................ YES (explicit mode, R2)
retry behaviour has defined semantics ............. YES (R7, unchanged)
OpenAI's endpoint-specific clamp addressed ........ YES (reclassified, R9, left untouched)
context length explicitly rejected as a proxy ..... YES (R3)
replay implications explicit ...................... YES (no new persistence; R8 obligation)
ADR-0016 measurement implications explicit ........ YES (instrumentation only; no amendment)
implementation boundary separable from decision ... YES (mechanical / behavioural split)
no second authority accidentally created .......... YES (R10, single owner)
ADR numbering resolved before writing ............. YES (0038; collision check PASS)
append-only honoured .............................. YES (no existing ADR touched)
```
