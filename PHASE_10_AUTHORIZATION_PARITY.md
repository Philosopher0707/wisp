# Phase 10 — Authorization Parity (G1)

**Repository:** `/Users/philosopher/Documents/wisp`
**Status:** measured and pinned; **one decision open**
**Related:** `PHASE_10_PROTECTED_PATH_GUARD.md` (the instance), `PHASE_10_AUTHORITY_CLOSURE_AUDIT.md` §3.5

---

## 1. What was already known, and what was not

Phase 10 named G1 as remaining debt in one sentence: *"two authorization
implementations remain; REST uses the weaker one."* That was an observation.
This document is the measurement — and the measurement changed the picture.

**The two models:**

| Model | Layers | Used by |
|---|---|---|
| `auth/decision.authorize()` | L0 policy bundle · L1 principal capabilities · L2 workspace trust · L3 risk vs sensitivity · **L4 arguments** · L5 approval | the agent (`ToolExecutor.execute`) |
| `infra/security.SecurityPolicy.check()` | workspace trust · pluggable **mode engine** · **policy hooks** · approval | the REST gate · `ApprovalGate` |

**They are not two implementations of one concept.** They cover *different rule
sets*: `authorize()` owns capability/sensitivity/argument narrowing;
`SecurityPolicy` owns the mode engine and the policy-hook mechanism (which can
rewrite arguments). Neither is a subset of the other.

**The actual asymmetry is compositional, and the agent does not have it.**
`ToolExecutor` consults **both**:

```
tool_executor.py:691   policy_hard_deny(func_name, effective_mode)   # mode rules
tool_executor.py:707   authorize(...)                                # authority layers
                       -> then pre-tool hooks, plan guard, dangerous-command block
                       -> then the approval gate (which wraps SecurityPolicy)
```

The REST gate consults **only** `SecurityPolicy`. So every rule that lives
exclusively in `authorize()` is invisible to REST. That is the mechanism behind
the protected-path bypass — and it is a *class*, not a one-off.

---

## 2. The measurement

Every REST route's `(action_name, args)` was driven through both models in all
four modes and the verdicts compared.

**36 (route, mode) pairs → 9 divergent.** Three were the protected-path
instance (now closed at the REST adapter). The remaining **6** are one shape:

| action | `authorize()` | `SecurityPolicy` | REST gate today |
|---|---|---|---|
| `hooks.create` in `auto_edit` | ALLOW **+ approval** | ALLOW | **ALLOW** |
| `hooks.create` in `ask_all` | ALLOW **+ approval** | ALLOW | **ALLOW** |
| `mcp.add_server` in `auto_edit` | ALLOW **+ approval** | ALLOW | **ALLOW** |
| `mcp.add_server` in `ask_all` | ALLOW **+ approval** | ALLOW | **ALLOW** |
| `plugins.install` in `auto_edit` | ALLOW **+ approval** | ALLOW | **ALLOW** |
| `plugins.install` in `ask_all` | ALLOW **+ approval** | ALLOW | **ALLOW** |

The file and shell surfaces (`/api/files*`, `/api/bash`) are at **full parity**
in every mode — verified by `test_parity_routes_have_no_divergence_at_all`.

---

## 3. Why the six matter

`require_tool_allowed`'s own docstring states the contract:

> *"REST has no human to approve, so approval-required verdicts deny — same as
> ApprovalGate with no handler."*

**It cannot honour that**, because `SecurityPolicy` never reports
`approval_required` for these actions. The contract is declared and unreachable.

Two consequences:

1. **The default mode is affected.** `permission_mode` defaults to
   `AUTO_EDIT` (`config.py:674`). So out of the box, REST permits registering a
   hook, an MCP server, or a plugin **without the approval the agent path
   requires for the same operation**.
2. **The desktop client does not set a mode**, so it runs at whatever the user
   configured — i.e. also `auto_edit` by default.

**The "no approver" premise is a design statement, not a capability limit.** The
server already has an approval channel: `WebSocketTransport` implements
"bidirectional approval flow (Issue 8)". The REST routes simply do not use it.

---

## 4. Options

| Option | Effect | Cost |
|---|---|---|
| **A. Accept** — REST callers are human-driven; the request *is* the approval | No change. The divergence is documented as intended | The gate's docstring becomes false, and must be corrected. An agent-driven REST caller gets no approval gate |
| **B. Honour the contract** — the REST gate also consults `authorize()` and denies approval-required | Parity with the agent in every mode | **In `auto_edit`/`ask_all` the shipped desktop client would receive 403** on `/api/hooks`, `/api/mcp/servers`, `/api/plugins/install`. Same blast radius that option B of Target C was weighed against — but this time it is *measured*, not assumed |
| **C. Route approvals through the existing channel** — an approval-required REST action asks over WebSocket and waits | The complete fix: parity *and* the client keeps working | A real feature: request correlation, timeout, and a client-side confirmation UI. Out of scope for a remediation pass |

**Recommendation: B now, C as the real fix** — the same shape as the Target C
recommendation, and for the same reason: B is the only option that strictly
improves the position without inventing mechanism, and C is what the
architecture actually wants.

**But B is not implementable without a decision**, because it changes
default-mode behaviour of a shipped client. That is why it is not applied here.

**Option A is defensible** — if REST is documented as a *human* surface, the
approval question is answered by the caller's identity. Choosing A means
correcting the docstring, not leaving it aspirational.

---

## 5. What was implemented

The divergence is now **measured and ratcheted** rather than latent:
`tests/test_authorization_parity.py` (18 tests).

| Test | Enforces |
|---|---|
| `test_authorization_parity_is_pinned` | a **new** divergence fails, naming the (route, mode, verdicts) |
| `test_no_known_divergence_has_been_silently_fixed` | a divergence that disappears fails — the recorded decision would be stale |
| `test_parity_routes_have_no_divergence_at_all` | the file/shell surfaces must stay at full parity |
| `test_the_divergence_is_only_the_approval_layer` | if a divergence appears that is not approval-shaped (e.g. one layer denies while the other allows), it is treated as a worse class |
| `test_the_rest_gate_documents_the_approval_contract` | if the docstring changes, the table is re-examined |
| `test_the_agent_consults_both_models` | if the agent stops composing both layers, the table stops describing reality |
| `test_default_mode_is_the_affected_one` | if the default mode changes, the blast radius is re-evaluated |

This is the same move as the Target C boundary work: **make the current state
explicit and impossible to change unnoticed, so the decision is a single
reviewable step rather than an open-ended question.**

---

## 6. What a "one authority" answer would actually look like

Not "delete `SecurityPolicy`". The honest target:

> **One composition point** — a single function that consults the mode rules,
> the authority layers, and the approval requirement, in a defined order, and
> returns one verdict. Both `ToolExecutor` and the REST gate call it.

`ToolExecutor` already *is* that composition, inline, across ~40 lines. The
extraction is mechanical: lift lines 691–722 into a function that takes
`(action_name, args, workspace, mode, policy)` and returns a verdict, then have
the REST gate call it with a `local_principal`.

That would give G1 a real answer and delete the parity table entirely — the
divergence would be structurally impossible rather than ratcheted. It is a
larger change than this pass, and it still needs the approval question answered
first (does an approval-required verdict deny, or wait?).

---

## 7. Honest note

G1 was recorded in the audit as a one-line debt item with the word "weaker". The
measurement shows the relationship is not weaker/stronger but
**different-and-incompletely-composed** — and that the agent already composes
both, which is what makes REST's single consultation a defect rather than a
design.

That reframing came from the same habit as the previous five: drive both paths
with the same input and compare. The parity table exists so the next person does
not have to re-derive it.
