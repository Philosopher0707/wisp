# Phase 2 — Boundary Forensics

**Repository:** `/Users/philosopher/Documents/wisp` @ `main` `5ea0ed9`
**Method:** AST/grep sweep of every cross-layer import edge, plus per-concept ownership interrogation
**Status:** analysis only — no code modified

---

## Preamble: the headline result

I tested the brief's ten listed leakage patterns against the code. **Six do not occur.** The layering is genuinely cleaner than the brief assumes, and saying so is more useful than manufacturing violations to fix.

| Leakage pattern the brief asks about | Verdict | Evidence |
|---|---|---|
| runtime logic leaking into orchestration | **Not found** | `multi_agent/` does not implement turn logic; it calls the same `WispAgentCore` |
| provider-specific behavior leaking into generic abstractions | **FOUND — 5 files** | `__main__.py`, `cli/setup.py`, `cli/commands/model.py`, `repl/commands/provider.py`, `provider_catalog.py` branch on provider name (§B2) |
| graph semantics leaking into execution details | **Not found** | `graph/executor.py` has zero provider/tool/subprocess references |
| execution details leaking into graph representation | **Not found** | `graph/types.py` is pure data; `Graph.fingerprint()` hashes only security-relevant fields |
| configuration leaking into runtime state | **Partly** | `WispConfig` is read at multiple construction sites rather than injected once — pre-existing, documented in `docs/architecture-v2.md` |
| persistence leaking into domain semantics | **Not found** | `infra/store.py` returns plain dicts; no ORM; domain types are not persisted objects |
| transport/API concerns leaking into core logic | **FOUND — 2 sites** | `core/approval_gate.py:13`, `core/doctor.py:325` (§B1) |
| error translation occurring at multiple layers | **FOUND — 3 duplicate classifiers** | `transport/renderer.py:29`, `transport/cli.py:1462`, `benchmark/scoring.py:88` (§B3) |
| metadata interpreted differently by different components | **FOUND** | Execution state: 3 vocabularies (§B4) |
| duplicated normalization/parsing/validation | **Partly** | Containment (3 impls, 2 divergent); event normalization (3 paths) |
| test-only behavior not matching production semantics | **FOUND** | `test_provider_select.py:210` relies on the absence of a TTY (§B5) |

**Four real boundary defects.** Each is interrogated below in the brief's required format.

---

## B1 — Core depends upward on the CLI and transport layers

**Who owns this responsibility?** The exception types `ApprovalCancelled` / `ApprovalTimeout` are *domain* concepts — they describe the outcome of an approval decision, which is core semantics. They are currently **defined in `wisp/cli/approval.py`** and **imported by `wisp/core/approval_gate.py:13`**.

| Question | Answer |
|---|---|
| Who owns it? | Should be the core (or a neutral module). Currently the CLI. |
| Who may call it? | Any consumer of the approval gate — core, transports, server. |
| What crosses the boundary? | Two exception types. |
| What must NOT cross? | The core must not know the CLI exists. |
| Which module defines the contract? | **`wisp/cli/approval.py`** |
| Which module defines behavior? | **`wisp/core/approval_gate.py`** |
| Are they the same? | **No — inversion.** |

**Evidence:** `wisp/core/approval_gate.py:13` → `from wisp.cli.approval import ApprovalCancelled, ApprovalTimeout`. A second site: `wisp/core/doctor.py:325` → `from wisp.transport import renderer as _renderer` (deferred, but still upward).

**Architectural implication:** the core layer — the one everything else depends on — has a hard import edge into the presentation layer. Any CLI refactor can break the core, and the core cannot be reused without the CLI. The dependency direction is inverted for these symbols.

**Remediation shape:** move the two exception types to the core (or to the existing neutral `wisp/exceptions.py`) and have `cli/approval.py` re-export them. Note this is a **symbol relocation**, not a behavior change — the brief's Rule 1 is satisfied.

**Risk:** low. The types are plain exception classes; moving them changes no semantics. The real work is confirming no consumer imports them *from* `cli.approval` in a way that a re-export would break — a mechanical check.

---

## B2 — Provider-specific behavior lives outside the provider abstraction

This is the clearest architectural leakage in the codebase, and it is **structural, not incidental**.

**Two distinct defects:**

### B2a — Model listing is implemented in a shared catalog, not behind the provider protocol

The `Provider` protocol declares `list_models()` as an abstract method (`providers/protocol.py:154`). Each provider implements it. **But `provider_catalog._list_models_impl()` reimplements model listing independently**, branching on provider name and performing its own HTTP calls (`provider_catalog.py:82-111`: ollama `/api/tags`, openrouter `/models`, openai `/models`, nvidia `/models`).

So there are **two authorities for "what models does provider X serve"**, and they can disagree.

| Question | Answer |
|---|---|
| Who owns model listing? | Should be the provider (it is provider-specific knowledge). |
| Who currently defines behavior? | **Both** `Provider.list_models()` and `provider_catalog._list_models_impl()`. |
| Which defines the contract? | `providers/protocol.py:154`. |
| Are they the same? | **No.** The catalog does not delegate to the protocol. |

### B2b — The abstraction layer imports concrete providers (dependency inversion)

| Site | Import | Why it's a violation |
|---|---|---|
| `provider_catalog.py:105` | `from wisp.providers.nvidia import NVIDIAProvider` | A shared module reaching into a concrete provider's **private** `_MODEL_CONTEXT` class attribute as a fallback catalog |
| `cli/setup.py:92` | `from wisp.providers.ollama import OllamaProvider` | The CLI constructing a concrete provider instead of asking the factory |

The protocol + factory exist precisely so consumers never name a concrete provider. Two consumers do anyway.

**Provider-name branching across 5 non-provider files:** `__main__.py:317`, `cli/setup.py:173,217,236`, `cli/commands/model.py:69,75,129,143`, `repl/commands/provider.py:175,182,244,267`, `provider_catalog.py:66,68,86,91,93,96`.

**Nuance — not all of it is a defect.** Several sites are legitimate *presentation* decisions (`cloud = dim("(cloud)") if provider == "ollama" else ""` — labelling a local provider in the UI). The defect is not "CLI mentions a provider name"; it is:
1. the CLI **constructing** concrete providers (`setup.py:92`),
2. a shared catalog **reimplementing** provider-specific protocol methods,
3. a shared catalog **reaching into a concrete provider's private attributes**.

**Remediation shape:** make `provider_catalog` delegate to `Provider.list_models()` via the factory; move NVIDIA's static catalog behind the protocol (e.g. `get_model_info`/a declared class-level catalog on the protocol); have `cli/setup.py` go through the factory. Provider-name *labels* in the UI can stay.

**Risk: medium.** `provider_catalog` has a 300 s cache and an offline-fallback path (`_MODEL_CONTEXT`) that exists because NVIDIA's API can be unreachable — that behavior must be preserved, not deleted. This is the brief's Rule 3 exactly: identify semantic differences before removing a path.

---

## B3 — Error classification has three duplicate authorities

The same predicate — "is this tool result an error?" — is implemented three times:

| Implementation | Location |
|---|---|
| `result_is_error(result)` | `transport/renderer.py:29` |
| `_is_error_result(result)` | `transport/cli.py:1462` |
| `_is_error_result(result)` | `benchmark/scoring.py:88` |

Plus two more error *classifications* on a different axis: `subagent_orchestrator.py:939,948` (`_is_transient`, `_is_denial`) classify by **substring matching on error strings**, and `core/stateless.py:1899` `_normalize_tool_result()` shapes errors into a fourth representation.

| Question | Answer |
|---|---|
| Who owns "is this an error?" | Should be one predicate over the tool-result envelope. |
| Who currently defines it? | **Three modules, independently.** |
| What must NOT cross? | The *classification* rule must not be re-derived per consumer. |

**Why it matters even though it works today:** the three implementations must agree on the envelope shape. `_normalize_tool_result` defines that shape, and the other three consume it by convention. If the envelope gains a field or a status value, three consumers must be updated in lockstep with nothing detecting a miss — the same failure mode as F28.

**Remediation shape:** one `is_error_result()` over the canonical envelope, exported from the module that owns the envelope; the other two call it.

**Risk: low.**

---

## B4 — Execution state: three vocabularies, one explicit adapter, one contradiction

This is the most consequential boundary defect found in this phase (recorded as F28 in the normalization).

| Subsystem | Type | Terminal success value |
|---|---|---|
| `multi_agent/background.py:35` | `STATUS_COMPLETED` (module constants) | `"completed"` |
| `contracts/run.py:12` | `RunStatus(StrEnum)` | `COMPLETED = "completed"` |
| `graph/types.py:37` | `RunStatus(str, Enum)` | `SUCCEEDED = "succeeded"` |
| `runs/record.py:14` | `RunState(StrEnum)` | `SUCCEEDED = "succeeded"` |

**Two `RunStatus` enums with different member names and different values for the same concept**, plus a third vocabulary in `background.py`.

**The contradiction inside one module.** `background.py` translates its own vocabulary twice, inconsistently:

```python
# :79-84 — an explicit, documented adapter to the M3 RunState machine (correct)
_STATUS_TO_RUN_STATE = {STATUS_RUNNING: "running", STATUS_COMPLETED: "succeeded", ...}

# :601-605 — a second, inline mapping 500 lines later (presentation)
mark = {STATUS_COMPLETED: "completed", STATUS_FAILED: "FAILED", STATUS_CANCELLED: "cancelled"}
```

Mapping #1 is a legitimate labeled boundary adapter. Mapping #2 is presentation formatting for a notification line — but it also renders `FAILED` in caps while its siblings are lowercase, and it reintroduces the `completed` vocabulary at a second site. **There is no translation layer between the vocabularies** (verified: no `_STATUS_ALIAS` / `normalize_status` exists).

**Consumers are already split:**

| Compares `"completed"` | Compares `"succeeded"` |
|---|---|
| `tools/subagent_tools.py:41,85,108` | `coding.py:239` |
| `transport/renderer.py:498,505,587,603` | `graph/api.py:27` |
| `tui/widgets/agents/agent_grid.py:17` | `graph/cli.py:160,243,296,299` |
| `core/stateless.py:1347` | `graph/executor.py:238` |
| `supervisor.py:158` | `runs/record.py` (via `TERMINAL_STATES`) |

| Question | Answer |
|---|---|
| Who owns "what state is this run in?" | No single owner. |
| Which module defines the contract? | **Three modules define three contracts.** |
| Are they the same? | **No.** |

**Why this is the highest-value canonicalization target:** the failure mode is **silent**. A consumer comparing `status == "succeeded"` against a background agent that reports `"completed"` does not raise — it concludes "not terminal" and behaves differently. No test currently asserts the vocabularies agree.

---

## B5 — Test-only behavior diverges from production semantics

`tests/test_provider_select.py:210` exercises the "provider switch with no key" path by **deleting the env vars and relying on the absence of a TTY** so that `getpass` raises. In CI (no TTY) this passes; on a developer machine it hangs forever.

| Question | Answer |
|---|---|
| What does production do? | Prompts interactively via `getpass`. |
| What does the test assert? | That the prompt path yields "don't switch" — but it does so by accident of environment. |
| Are they the same? | **No — the test asserts an environment property, not the contract.** |

This is the brief's "test-only behavior that does not match production semantics" pattern, in its most literal form: the test passes for a reason unrelated to the behavior it claims to verify.

**Remediation shape:** mock `getpass` and assert the no-key outcome deterministically.

---

## B6 — Boundaries that are correctly drawn (do not touch)

Recording these so the remediation does not "fix" them.

| Boundary | Why it is correct |
|---|---|
| `core/` → `providers/` | The core imports only `providers/protocol.py`; no concrete provider anywhere in `core/`. The seam holds. |
| `graph/` → host | Zero `subprocess`, `execute_tool`, socket, or filesystem-mutation calls in `wisp/graph/`. All model-adjacent effects route through the injected runner. |
| `contracts/` → rest | Only one outward edge (`contracts/envelope.py:6` → `core.events.AgentEvent`), which is the intended wrap-the-internal-event relationship. |
| `infra/store.py` → domain | Returns plain dicts; no persistence types leak into domain semantics. |
| `tools/` → authority | Every tool is a pure function behind `ToolExecutor`; no tool reaches authority directly. |
| `providers/` → `provider_select` | The factory consumes the registry, not the reverse. Correct direction. |
| Graph representation vs execution | `types.py` is data; `executor.py` is behavior. Cleanly separated. |

---

## B7 — Cross-layer import edges, complete

| From | To | Count | Verdict |
|---|---|---|---|
| `core/` | `cli/` | 1 (`approval_gate.py:13`) | **DEFECT — inversion** |
| `core/` | `transport/` | 1 (`doctor.py:325`) | **DEFECT — upward leak** |
| `graph/` | `cli/` | 1 (`cli.py:374`) | **DEFECT — upward leak** (source of the `cli.dispatcher ↔ graph.cli` cycle) |
| `contracts/` | `core/` | 1 (`envelope.py:6`) | Acceptable by design |
| `cli/`, `repl/` | `providers/` (concrete) | 2 (`setup.py:92`, `provider_catalog.py:105`) | **DEFECT — inversion** |
| `server/` | `core/`, `infra/` | many | Correct direction |
| `tools/` | `infra/`, `core/` | many | Correct direction |

**Four upward edges, all in the same shape: a lower layer naming a higher one, or an abstraction naming a concrete implementation.** They are small in count and cheap to fix, which is a good sign for the codebase overall.

---

## Summary

| # | Boundary defect | Shape | Severity | Risk to fix |
|---|---|---|---|---|
| B1 | Core → CLI/transport exception & rendering types | Dependency inversion | S4 | Low (symbol relocation) |
| B2 | Provider-specific behavior outside the provider abstraction | Leakage + inversion | **S3** | Medium (preserve NVIDIA offline fallback) |
| B3 | Three duplicate error classifiers | Duplicated semantics | S4 | Low |
| B4 | Three execution-state vocabularies, one contradiction, no adapter | **Missing canonical contract** | **S3** | Medium (touches types consumers depend on) |
| B5 | Test asserts an environment property, not the contract | Test/production divergence | S3 | Low |
| — | Six of the brief's ten listed leakage patterns | **Do not occur** | — | Do not touch |

**The pattern across all four defects is the same:** *a concept that is genuinely shared was allowed to be re-derived locally.* Not sprawl — four narrow, well-defined seams where a shared concept has a local copy. That is why the remediation is surgical rather than architectural.
