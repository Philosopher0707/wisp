# Phase 4 — Canonical Contract Freeze

**Repository:** `/Users/philosopher/Documents/wisp` @ `main` `5ea0ed9`
**Status:** analysis only — no code modified

---

## What "freeze" means here

Freezing a **semantic contract** means: the observable guarantees below become the ones the codebase is allowed to depend on, and anything that would change them is a breaking change requiring an explicit migration — not a local edit.

The brief warns: *do not freeze accidental implementation details.* So each contract below states only what consumers actually rely on, and each carries an explicit **"NOT frozen"** list of things that may change freely. Freezing too much would be as harmful as freezing nothing — it would lock in the very accidents we are removing.

---

## C1 — Execution state contract *(new — the primary freeze)*

**Owner:** `wisp/runs/record.py` (`RunState`)
**Rationale:** the only vocabulary with a declared transition table, a terminal set, and a validator. See Phase 3.

### Frozen surface

| Aspect | Guarantee |
|---|---|
| **Values** | The canonical state set is `RunState`: `queued, planning, running, awaiting_approval, paused, succeeded, failed, cancelled`. |
| **Terminal set** | `TERMINAL_STATES = {succeeded, failed, cancelled}`. Exactly these three; terminal is immutable. |
| **Transition legality** | Governed by `LEGAL_TRANSITIONS`; `is_legal(from, to)` is the single predicate. Any producer that moves a run must satisfy it. |
| **Terminal-success meaning** | `succeeded` is the **only** canonical spelling of "completed successfully". |
| **Cross-vocabulary adapters** | Every subsystem with a local vocabulary must expose **one** explicit, named, tested adapter into `RunState`. Adapters are unidirectional (local → canonical). |
| **Wire compatibility** | The *string values already emitted to consumers* are frozen: `background.py` continues to emit `"completed"` on its notification path, `graph/` continues to emit `"succeeded"`. Canonicalization is achieved by **adapter + test**, not by changing existing wire strings in place. |

### Invariants

1. No module defines a second enum whose member set overlaps `RunState` without declaring itself an adapter.
2. Every adapter maps terminal-success to `RunState.SUCCEEDED` — there is no adapter that maps a success state to a non-terminal one.
3. A producer's terminal state is reachable from its non-terminal states per `LEGAL_TRANSITIONS`.
4. `"completed"` and `"succeeded"` never appear as *comparison literals* in new code; they appear only inside declared adapters.

### Error semantics

Transitioning from a terminal state is a no-op with a logged warning — **never** an exception and never a silent state change. This preserves the existing "terminal precedence" behavior (`graph/executor.py:489-520`, `:233-242`).

### Metadata semantics

`RunStatus` in `contracts/run.py` and `graph/types.py` become **views** over the canonical state, not independent authorities. Where a consumer needs the canonical state it converts; where it needs the historical string it keeps the string.

### Allowed dependencies

`runs/record.py` must depend only on the stdlib. Adapters may import it. It must import nothing from `graph/`, `multi_agent/`, `contracts/`, or `server/`.

### Forbidden dependencies

- `runs/record.py` must **not** import any subsystem it is canonical for (no cycles).
- No consumer may import a *second* subsystem's state enum to compare against the canonical one; it must use an adapter.

### NOT frozen (free to change)

- The internal representation of `RunRecord`.
- Whether adapters are functions, classmethods, or dicts.
- The `Transition` record shape.
- Adding new states, provided `TERMINAL_STATES` and `LEGAL_TRANSITIONS` are updated together and all adapters are re-tested.

---

## C2 — Tool result envelope contract

**Owner:** `wisp/core/stateless.py::_normalize_tool_result` (`:1899`) — the module that defines the shape owns the predicate.
**Rationale:** three independent implementations of "is this an error?" exist (`transport/renderer.py:29`, `transport/cli.py:1462`, `benchmark/scoring.py:88`), and none of them owns the shape they classify.

### Frozen surface

| Aspect | Guarantee |
|---|---|
| **Shape** | Every tool result crossing into the message stream is normalized by `_normalize_tool_result` before any consumer sees it. |
| **Status values** | The status vocabulary emitted by normalization is the contract; consumers must not invent new values. |
| **Error predicate** | Exactly one exported predicate answers "is this an error?". The three existing implementations delegate to it. |
| **Denial vs failure** | Denials use the structured `DENIAL_*` envelope (`core/events.py:256-260`); execution failures keep their existing shape. These are **different axes** and must not be merged. |

### Invariants

1. A result is classified identically by every consumer — the predicate is single-sourced.
2. Adding a status value requires updating the predicate and every adapter in one change.
3. Denials are distinguishable from failures without string inspection.

### Error semantics

The `DENIAL_*` set (`POLICY_DENIED`, `USER_DENIED`, `APPROVAL_TIMEOUT`, `CANCELLED`, `SCHEMA_INVALID`) and the `CODE_*` set (`E1101` turn timeout, `E1102` provider stream, `E2103` tool timeout, `E5101` iteration budget) are both frozen as **taxonomies**, not merged. They answer different questions: *why was this refused* vs *what went wrong*.

### Allowed dependencies

The predicate may depend on the envelope shape only.

### Forbidden dependencies

The predicate must not depend on `transport/`, `benchmark/`, or `graph/`. Those are consumers.

### NOT frozen

- Human-readable error strings.
- Log formatting.
- `ToolError`'s internal structure.

---

## C3 — Provider protocol contract *(already stable — freeze as-is)*

**Owner:** `wisp/providers/protocol.py`
**Status:** already enforced by the ABC. **Do not change.**

### Frozen surface

| Aspect | Guarantee |
|---|---|
| **Interface** | `generate_stream_events`, `generate_stream_events_async`, `health_check`, `list_models`, `get_model_info`, optional `generate_structured`. A provider missing an abstract method fails at instantiation. |
| **Registry coverage** | `ProviderFactory` must register a class for **every** entry in `provider_select.KNOWN_PROVIDERS` — asserted at `factory.py:36`. |
| **Model listing** | `list_models()` is the **only** authority for provider-specific model enumeration. Shared modules must delegate to it, never reimplement it. |
| **Construction** | Consumers obtain providers from the factory, never by importing a concrete class. |

### Invariants

1. No module outside `providers/` imports a concrete provider class. *(Currently violated twice: `cli/setup.py:92`, `provider_catalog.py:105`.)*
2. No module outside `providers/` branches on provider name to *decide behavior* (labels are permitted; logic is not).
3. `providers/` never imports from `cli/`, `transport/`, `server/`, or `graph/`.

### Error semantics

Provider errors surface as events on the stream (`{"type": "error", ...}`), not exceptions crossing the boundary. The stream guard (`core/provider_stream.py`) owns retry/backoff. A provider must not retry internally.

### NOT frozen

- HTTP client choice, SSE parsing, timeouts.
- The number of providers.
- `get_model_info`'s payload beyond `id`.

---

## C4 — Containment contract

**Owner:** `wisp/pathsec.resolve_contained`
**Status:** canonical and already used by 3 of 5 consumers.

### Frozen surface

| Aspect | Guarantee |
|---|---|
| **Signature** | `resolve_contained(root, candidate, *, allow_absolute: bool) -> str`, raising `ValueError`. |
| **Rejections** | NUL bytes; control characters (except none — all rejected); `..` traversal; symlink escape; sibling-prefix collisions (`/ws2` vs `/ws`); absolute paths when `allow_absolute=False`. |
| **Root itself** | The root is allowed. |
| **Resolution** | `realpath` on both sides; separator-anchored prefix comparison. |

### Invariants

1. Every path-containment decision in the codebase routes through this function. *(Currently violated twice.)*
2. No consumer implements its own realpath+prefix comparison.
3. A differential test asserts all consumers agree on an adversarial corpus — **this becomes the executable guard.**

### Error semantics

`ValueError` is raised; callers map to their own error type (`ToolError`, `HTTPException`). The function never returns a sentinel and never logs.

### NOT frozen

- The error message text.
- Whether callers wrap or re-raise.

---

## C5 — Verification contract *(freeze the implicit one)*

**Owner:** currently undefined. Proposed: `core/verification.py` owns the *rule*; `tools/bash.py` owns the *format*.
**Rationale:** the completion gate depends on `bash.py` omitting the `[exit code:` prefix on success. This is correct today and completely undocumented — the failure mode is silent inversion.

### Frozen surface

| Aspect | Guarantee |
|---|---|
| **Success encoding** | A successful shell verification produces output that does **not** begin with `[exit code:`. |
| **Failure encoding** | A non-zero exit produces output beginning with `[exit code: N]\n`. |
| **Gate rule** | The completion gate treats "no failure prefix" as verification evidence; a red run never satisfies it. |
| **Bounding** | The gate self-limits (`min_turns` + `max_nudges`) and then permits an honest unverified finish. |

### Invariants

1. Changing the bash output format requires updating the gate in the same change — pinned by a test that drives the real formatter into the real guard (which is how the Phase 2 correction was made).
2. A verified turn never reports unverified, and vice versa.

### Error semantics

Exhausting the nudge budget is **not** an error — it is an honest unverified completion.

### NOT frozen

- Prefix wording (as long as the test pins both sides).
- Which tools count as verification evidence.

---

## C6 — Approval decision contract

**Owner:** `wisp/core/approval_gate.py` + `wisp/auth/decision.py`

### Frozen surface

| Aspect | Guarantee |
|---|---|
| **Layer attribution** | Every denial names its controlling layer (`auth/decision.py:26`): organization, principal, workspace, sensitivity, arguments, approval. |
| **Fail-closed** | No handler + forced → deny. Timeout → deny. Cancel → deny. |
| **Typed denials** | Denials carry a `DENIAL_*` status, never a bare boolean. |
| **REST** | With no human approver, approval-required verdicts deny (`server/deps.py:385-407`). |
| **Exception types** | `ApprovalCancelled` / `ApprovalTimeout` are **domain** types and must be owned by the core, not the CLI. |

### Invariants

1. Every effect passes `authorize()` on the model path.
2. The controlling layer is named in the denial.
3. The exception types do not live in a layer above their consumer. *(Currently violated: `core/approval_gate.py:13` imports them from `cli/`.)*

### NOT frozen

- Prompt wording, key bindings, timeout values.

---

## Contracts deliberately NOT frozen

The brief's Rule 5 warns against unjustified compatibility layers, and the inverse also applies: **do not freeze what should be free.**

| Not frozen | Why |
|---|---|
| The `session` dict shape | Shared-mutation contract is real but undocumented; freezing it now would entrench a design smell. Document it; freeze it only if it survives. |
| The three CLI dispatch layers | A documented in-progress migration. Freezing would block the strangler-fig. |
| `UnifiedStore` vs `GraphStore` split | Different bounded contexts, same DB file. Legitimate; not a duplicate. |
| The validation paths (jsonschema / pydantic / graph validator) | Each validates a different artifact at a different boundary. Treating them as duplication would be a category error. |
| `AgentEvent` vs `CanonicalEvent` | Correctly layered internal-vs-wire. Merge would be a regression. |
| `_CONTEXT_TTL`'s locking | Bounded to 3 keys; a race costs a cache miss. Not worth a contract. |

---

## Freeze summary

| Contract | Owner | Enforcement today | Enforcement after | Change risk |
|---|---|---|---|---|
| **C1** Execution state | `runs/record.py` | none | adapter test | **High** |
| **C2** Tool result envelope | `core/stateless.py` | convention ×3 | single predicate + test | Low |
| **C3** Provider protocol | `providers/protocol.py` | **ABC + factory assertion** | unchanged (extend to 2 violators) | Medium |
| **C4** Containment | `wisp/pathsec.py` | 3/5 consumers | differential test | Low |
| **C5** Verification | `core/verification.py` | implicit | test pinning both sides | Low |
| **C6** Approval decision | `core/approval_gate.py` | partial | move exception types down | Low |

**The through-line:** three of these six contracts have **no executable enforcement at all** today (C1, C2, C5), and those are precisely the three that have drifted. The Phase 3 correlation holds: enforcement, not documentation, is what keeps a contract intact.
