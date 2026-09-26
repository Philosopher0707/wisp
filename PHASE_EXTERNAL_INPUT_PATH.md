# PHASE — THE EXTERNAL INPUT PATH (Deliverable 2, ADR-0061)

**Deliverable:** decide the boundary on the external input path — the agent path's WebSocket approval
frame (W1) and `POST /api/hooks`'s `command` (G3).
**Outcome:** **DECIDED — both closed.** W1 is **fixed** (the frame is now the one the clients read);
G3 is **closed by naming what the gate does not do**, not by inventing a check.
**Baseline:** `HEAD` = `995e2ee` (Deliverable 1 landed; no drift).
**Report of record; the decision is ADR-0061; the guards are
`tests/reliability/test_external_input_path.py` (16).**

---

## 1. W1 — the frame, driven

`w1_probe.py` drives `approve()` against a stub client that records frames, and reads the frame each
client *branches on* out of its source.

### Before the change

| | |
|---|---|
| the frame `approve()` sent | `approval_request` / `{approval_id, tool_call}` |
| desktop renderer (`useWebSocket.ts`) | branches on `tool_approval_request`, reads `call_id`, `name`, `arguments`, `reason` — **MISMATCH** |
| TUI (`ws_client.py`) | the same — **MISMATCH** |
| VS Code extension (`wispClient.ts`) | the same — **MISMATCH** |
| a client that never answers | `approve()` → **False** at the bound |
| no client at all | `approve()` → **False** |

**All three clients mismatched.** The prompt had never rendered; every request hit the 60 s bound and
denied.

### After the change

| | |
|---|---|
| the frame | `tool_approval_request` / `{call_id, name, arguments, reason}` |
| all three clients | **MATCH** |
| the round-trip (stub echoes `call_id`) | `approve()` → **True** |
| `call_id` vs the `_approvals` key | equal — resolution lands on its own entry |

### The 60 s wait was not performed, and why that is the honest method

`_APPROVAL_TIMEOUT` is asserted at **60.0**, and the *mechanism* is driven with the constant temporarily
lowered to 0.05 s: a non-responding client yields **False** at the bound. A 60 s sleep adds no
information beyond the constant itself. The condition is stated rather than left implicit (F94).

### Two probe defects, both found by running it

1. **The client-frame detector read the wrong type.** It collected every occurrence of either frame
   name and took `sorted(...)[0]`, so `vscode-extension/src/wispClient.ts` — which *branches* on
   `tool_approval_request` and then **re-emits** an event named `approval_request` — was reported as
   branching on `approval_request`. `approval_request` sorts first, so the probe silently inverted its
   own answer for that client. Repaired to read the type **out of the branch** (`case '…':` for TS,
   `msg_type == "…"` for Python). This is the F92 class: the guard's claim and its subject had drifted.
2. **A shell `grep` with `\|` alternation returned nothing** for a pattern that plainly exists —
   `CONTEXT.md` §6's documented trap. It nearly produced a false finding that ADR-0057's client
   citations do not resolve. Re-run with the Grep tool, they resolve exactly.

---

## 2. G3 — `command` is not validated

| | |
|---|---|
| `HookCreateRequest.command` | `Field(..., min_length=1)` — **no content check** |
| `HookCreateRequest.name` | validated by `_SAFE_HOOK_NAME_RE` (path-traversal allowlist) |
| a hostile command (`rm -rf / …; curl … \| sh`) | **accepted** — driven |
| a traversing name (`../escape`) | **refused** — driven |

The asymmetry is the decision. The reason is **G2's**: a shell command's target is not determinable
from its text. The route's docstring now states it:

> **The gate restricts WHO may register a hook. It does not restrict WHAT the hook runs.**

Rejected, with reasons: a *runnable check* (the target is not determinable), a *metacharacter
blocklist* (metacharacters are the feature; evadable and false-positive-prone), an *allow-list* (not a
validation but a new policy surface with no owner, which would still have to define "the same
command"). **G3 closes by naming what the gate does not do.**

---

## 3. Are these one decision or two?

**Two decisions, one shared principle — and not the principle the brief offered.**

The brief proposed *"the runtime does not execute what it has not validated"* as the candidate shared
boundary. **That principle is false for the hook path**: the runtime *does* execute a hook command it
has not validated, and §2 decides it must. So:

* **W1** is a **broken authorization path** — a human *is* supposed to be asked, the mechanism exists,
  and the frame prevented the question. The fix is a wire protocol.
* **G3** is an **absent content check**, decided to stay absent — the authorization for a hook already
  works; what cannot exist is a check on the command's text.

The principle they share: **the runtime's control over external input is authorization-based, and it is
not content-inspection-based.**

---

## 4. ADR-0059 residual 1 — driven

Residual 1 read: *"A bundle's `approve` level is inert on REST. … REST cannot, having no approver."*

Driven over the six pinned pairs (ADR-0055 §1.3) — `residual1_probe.py`:

| action | mode | `authorize()` | REST asks a human? |
|---|---|---|---|
| `hooks.create` | `auto_edit` / `ask_all` | ALLOW **+ approval** | **True** |
| `mcp.add_server` | `auto_edit` / `ask_all` | ALLOW **+ approval** | **True** |
| `plugins.install` | `auto_edit` / `ask_all` | ALLOW **+ approval** | **True** |

**6 of 6.** ADR-0057's trigger set *is* the six pairs, and it does **not** over-fire: `read_only`
denies outright, `full` does not ask.

**So the residual moves: its stated reason is no longer true.** REST has an approver and consults it in
exactly the six pairs. What does **not** move is the **mechanism** — REST reads a hand-written set, not
`authorize().approval_required`, deliberately (ADR-0057 R4: the three names have no agent operation, so
reading the agent's model would make REST's trigger a function of a model for actions only REST has).
**Closed in effect, un-composed in mechanism** — and a guard asserts `approval_required` does not appear
in `approval_bridge.py`, so composing it later is a deliberate change.

**The bundle half stands, cited not re-measured.** `{"write_file": "approve"}` needs a verifiable bundle
and `cryptography` is absent on this host (F88). The condition is named rather than left implicit (F94).

---

## 5. The four non-violations, asserted

| | assertion |
|---|---|
| **1** | `authorize()`'s first three parameters — `principal`, `tool_name`, `args` — from `inspect` **and** the AST |
| **2** | `SecurityPolicy.check()` is `(self, action, context)`, and no organization slot (ADR-0059's pin) |
| **3** | `ToolExecutor.execute`'s chain is `policy_hard_deny` → `authorize` → `_get_write_tools`, from the AST |
| **4** | `goal.PRECEDENCE` by content, `VerificationFloorGuard`'s four methods, `turn_succeeded` derived from `terminal_outcome` |

**Non-violation 1 was written wrong first and the failure is kept:** the pin asserted
`["principal", "action", "args"]`, but the parameter is `tool_name`. The *claim* was right and the
*pin* named the wrong thing — the guard caught it on its first run. A no-op assertion (`assert fields
or True`) was also removed: a check that can never fail is not a check.

---

## 6. Non-vacuity

`nonvacuity_probe.py` — break the property, confirm the guard fails, restore, confirm the bytes are
identical, re-run as a control.

```
mutation                                             file                           result   ok
----------------------------------------------------------------------------------------------------
the frame reverts to `approval_request`              wisp/transport/websocket.py    CAUGHT   yes
`call_id` is not the `_approvals` key                wisp/transport/websocket.py    CAUGHT   yes
the bound moves 60 -> 30 (SIZE-PRESERVING)           wisp/transport/websocket.py    CAUGHT   yes
the denial stops naming its reason                   wisp/transport/websocket.py    CAUGHT   yes
the hook-name allowlist is weakened                  wisp/server/routes/hooks.py    CAUGHT   yes
the docstring stops naming the boundary              wisp/server/routes/hooks.py    CAUGHT   yes
authorize()'s second parameter is renamed            wisp/auth/decision.py          CAUGHT   yes
the trigger starts reading `approval_required`       wisp/server/approval_bridge.py CAUGHT   yes

8/8 guards non-vacuous
```

`__pycache__` is purged on both sides of every mutation, because `60.0 → 30.0` is **size-preserving** —
the documented trap where a same-second restore leaves stale bytecode holding the mutated source.

---

## 7. The contract updates, with reasoning

Six tests pinned the **old** frame. Each is updated in the same change, and the reasoning is in the
test:

| file | test | why it changed |
|---|---|---|
| `test_websocket.py` | `test_approve_sends_request_and_waits_for_response` | asserted `approval_request` / `tool_call` — the shape no client reads |
| `test_websocket.py` | `test_approval_frame_structure` (renamed) | asserted the old type and `tool_call` |
| `test_websocket.py` | `test_concurrent_distinct_approvals_both_resolve_by_id` | resolved on `approval_id`; the key is now `call_id` |
| `test_websocket.py` | `test_approval_frame_carries_the_correlation_key` (renamed) | same |
| `test_websocket.py` | `test_unknown_id_with_two_pending_noops` | cleanup resolved on `approval_id` |
| `test_ws_control_plane.py` | `test_approval_frame_resolves_mid_turn` | waited for `approval_request` and answered with a hard-coded `id: "x"`, which resolved **only** via the single-pending fallback. It now waits for the clients' frame and echoes the frame's own `call_id`, so it exercises the real correlation |

**The gate-order corpus is NOT re-written RED-first**, and the reason is the ADR's: the gate *chain* is
unchanged (non-violation 3, asserted). This changes a transport frame and a docstring, not a gate.

---

## 8. Verification

| | |
|---|---|
| **The new guard** | `test_external_input_path.py` — **16 passed** |
| **Non-vacuity** | **8/8 CAUGHT**, tree restored byte-identical, control green |
| **The affected suites** (9 files) | **174 passed, 0 failed** — incl. `test_rest_approval`, `test_websocket`, `test_tui_ws_client`, `test_ws_control_plane`, `test_approval_loop`, `test_permission_mode`, `test_authorization_parity`, `test_rest_authorization_composition` |
| **Gates** | `ruff check wisp/` → **11 errors**, unchanged (F71). `mypy` not re-run |

### 8.1 The canonical block, re-measured

| | |
|---|---|
| **before this change** | **1471 tests — 1470 passed, 1 failed** (F38) |
| **after** | **1487 tests — 1486 passed, 1 failed** (F38) — **+16**, all in the new file |
| **new failures** | **none**; **now-passing**: none |

Both headings (`CONTEXT.md` §11 and `AGENTS.md`) are re-measured in the same change that adds the file
— F85. The full suite was **not** run (F36: it cannot run in one process on this host), so the method
is weaker than a two-run intersection and is stated as such.

---

## 9. Honest limits

- **The round-trip is pinned against a stub channel, not a live client.** No real desktop, TUI or
  VS Code client runs on this host, so the *frame shape* and the *no-client behaviour* are driven and
  the *render* is not. The route-level round-trip
  (`test_ws_control_plane.py::test_approval_frame_resolves_mid_turn`) is driven end to end through
  `agent_websocket`, which is the strongest available proxy — but it is a fake socket.
- **`receive_message`'s own `tool_approval` branch still ignores `msg["id"]`** (ADR-0057 residual 2,
  unchanged). The route intercepts first; that branch is the old-protocol fallback. Named, not silently
  fixed.
- **The bundle half of ADR-0059 residual 1 is un-measurable here** — `cryptography` is absent (F88), so
  `verify_bundle` returns False for everything.
- **`vscode-extension/` is not a "shipped client" in ADR-0057's sense**, and this report does not claim
  it is. It is named because it reads the same frame, which *strengthens* R1 (the client change is zero
  for it too) rather than changing the decision. Whether it ships is not this ADR's question.
- **`_validate_hook_name` was already correct and is not this ADR's work.** It is driven here only to
  pin the *asymmetry* that is the decision.
