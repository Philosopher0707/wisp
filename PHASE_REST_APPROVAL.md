# Phase — REST approval through the WebSocket channel

**Repository:** `/Users/philosopher/iCloud Drive (Archive)/Documents/wisp`
**Baseline:** `HEAD` = `0cd5613` (the ADR-0056 docs tip; §0 records `9d56aec`). Working tree = the 29 WIP
entries of `CONTEXT.md` §8 — **no drift**.
**Decision:** **ADR-0057** — a REST request for an executable-config action asks a human over the
WebSocket channel; **no client means deny**. Flag `WISP_REST_APPROVAL`, default **OFF**.
**Change:** a new `wisp/server/approval_bridge.py`, an async gate companion, the three routes, the WS
route's registration, and a config flag. **No client change. No gate chain change.**

---

## 1. The brief's premise, driven

The brief stated, as a premise:

> *"The WebSocket channel already implements bidirectional approval flow (`wisp/transport/websocket.py`,
> wired through `wisp/server/routes/agents.py`). The REST routes just don't use it."*

Reading the channel first — as the brief required — shows it is **half true**, and the false half decides
the shape of the ADR.

| direction | server emits / reads | both shipped clients emit / read | match |
|---|---|---|---|
| server → client | `approval_request` · `{approval_id, tool_call}` (`websocket.py:159-163`) | `tool_approval_request` · `{call_id, name, arguments, reason}` (`useWebSocket.ts:103`, `ws_client.py:127`) | **NO** |
| client → server | `tool_approval` · `{approved}` (`websocket.py:299-301`) | `tool_approval` · `{id, approved, reason?}` (`ApprovalPrompt.tsx:31`, `useKeybindings.ts:117`, `ws_client.py:157`) | yes |

So the **answer** direction works and is consistent across both clients; the **question** direction does
not. **No client recognises the frame the server sends**, which means the *agent* path's WebSocket
approval prompt has never rendered — `approve()` always timed out (60 s) and denied.

Two consequences:

1. *"Wire REST into the existing channel"* cannot be done as stated — there is no working question path.
2. The **clients' vocabulary is already a de-facto contract.** Adopting *it* needs **no client change**;
   adopting the server's current frame would need one in **both** clients.

---

## 2. The decision

> **R1** The frame is the **clients'** vocabulary: `tool_approval_request`, carrying `call_id`, `name`,
> `arguments`, `reason`. **R2** The correlation key is `call_id` — what the clients already echo as `id`,
> and what `agents.py:196-208` already resolves on. **R3** A new `ApprovalBridge` owns its **own**
> correlation map, so `WebSocketTransport`, `approve()` and the agent path are untouched. **R4** The
> trigger is an explicit set: `{hooks.create, mcp.add_server, plugins.install}` in
> `{auto_edit, ask_all}`; `full` does not ask, `read_only` denies outright. **R5** **No client ⇒ 403.**
> **R6** Timeout `REST_APPROVAL_TIMEOUT_S = 30.0`, bounded, deny on expiry. **R7** Policy first, then ask
> — an **async** companion so `require_tool_allowed` keeps its signature. **R8** The client change is
> **zero**, pinned by the guard. **R9** G3 deferred. **R10** The four non-violations asserted from the
> AST. **R11** The flag defaults OFF, read once.

### Rejected alternatives

| Alternative | Why rejected |
|---|---|
| Reuse the server's current frame | No client reads it (measured). Would need a client change in both clients |
| Route through `WebSocketTransport.approve()` | It resolves its connection from the turn's `ContextVar`/`_current_ws`, which a REST request lacks. Reaching in would couple the agent path to a REST-only feature |
| Hold until a client connects | Holds an HTTP connection open on a condition that may never change — the "must not hang" the brief forbids |
| Fall through with a warning | **Silently allows** an executable-config mutation — forbidden outright |
| Change `SecurityPolicy`'s block sets | The three names have no `TOOL_RISK_TABLE` row (ADR-0055 §1.2); a mode block-set is shared by REST *and* `ApprovalGate`. R4's explicit set is narrower and readable |
| Absorb G3 | A content restriction, not an authorization decision. Its own ADR |
| Reconcile the agent path's dead frame here | It would change a **live** path's behaviour (the prompt would start rendering where today it times out to deny). Named as residual 1; not made silently |

---

## 3. What landed

| File | Change |
|---|---|
| `wisp/server/approval_bridge.py` | **new** — the frame constants, the action/mode sets, `ApprovalBridge` (registry + round-trip + its own correlation map) |
| `wisp/server/deps.py` | `configured_permission_mode`, `rest_approval_enabled`, `approval_bridge`, **`require_rest_approval`** (async). `require_tool_allowed` is **unchanged** |
| `wisp/server/routes/{hooks,mcp,plugins}.py` | `await require_rest_approval(...)` after the policy gate; the comments now state the new behaviour |
| `wisp/server/routes/agents.py` | `_WsApprovalChannel`; register on connect (after auth); resolve a REST approval from `tool_approval`; unregister in `finally` |
| `wisp/composition.py` | `root.approval_bridge = ApprovalBridge()` |
| `wisp/config.py` | `rest_approval` / `WISP_REST_APPROVAL`, default **False** (+26/−0) |
| `WISP_ARCHITECTURE_DECISIONS.md` | **ADR-0057** + its index row |
| `tests/reliability/test_rest_approval.py` | **new** — 18 tests |
| `AGENTS.md` | the flag row, three module-map rows, the corrected WebSocket row, the test block |

### Two corrections to the corpus made along the way

- **`AGENTS.md`'s `wisp/transport/websocket.py` row said "bidirectional approval".** Measured, that is
  half true. The row now says which half.
- **`wisp/server/routes/{hooks,mcp,plugins}.py` said** *"the desktop client keeps working because the
  policy allows this action in full / auto_edit / ask_all"*. With the flag ON that is no longer the whole
  story, so the comments changed **as a stated consequence of the decision**, not as docstring drift.

---

## 4. The guard

`tests/reliability/test_rest_approval.py` — **18 tests**, driving the round-trip with a stub channel:

| Test | Property |
|---|---|
| `test_a_connected_client_can_approve` / `_can_deny` | the round-trip resolves, with a frame count floor |
| `test_no_client_denies_and_does_not_hang` | **R5** — the brief's explicit constraint |
| `test_a_silent_client_times_out_to_deny` | **R6** — bounded, deny on expiry |
| `test_a_disconnected_client_stops_being_asked` | the registry is honest |
| `test_the_correlation_key_resolves_the_right_request` | **R2** — two concurrent requests, only the named one resolves |
| `test_an_unknown_correlation_key_resolves_nothing` | a stray response is not a decision |
| `test_the_frame_is_the_one_both_clients_read` | **R1/R8** — both clients still read the frame, and the desktop client still sends the response |
| `test_the_timeout_is_bounded_and_named` | **R6** |
| `test_the_trigger_is_the_three_executable_config_actions` | **R4**, per action × mode |
| `test_an_unknown_mode_normalises_like_security_policy` | total function |
| `test_each_route_asks_after_the_policy_gate` | **R7**, from the **AST** — a string scan would read the comment that describes the order |
| `test_the_flag_off_preserves_todays_behaviour` | **R11** |
| `test_the_flag_on_without_a_bridge_denies` | a misconfiguration is not a silent allow |
| `test_the_agent_gate_chain_is_unchanged` | **R10.1**, AST |
| `test_the_two_decision_models_are_unchanged` | **R10.2** |
| `test_the_turn_paths_approval_model_is_unchanged` | **R10.3** |
| `test_the_turn_predicate_floor_guard_and_precedence_are_untouched` | **R10.4** — precedence by **content** |

**Non-vacuity: 5/5 caught**, each file restored byte-identical (sha256, `__pycache__` purged):

| # | Mutation | Test it must fail | Result |
|---|---|---|---|
| NV1 | no client stops denying | `test_no_client_denies_and_does_not_hang` | **CAUGHT** |
| NV2 | the frame stops being the clients' vocabulary | `test_the_frame_is_the_one_both_clients_read` | **CAUGHT** |
| NV3 | a route asks before the policy gate | `test_each_route_asks_after_the_policy_gate` | **CAUGHT** |
| NV4 | the flag's default flips to ON | `test_the_flag_off_preserves_todays_behaviour` | **CAUGHT** |
| NV5 | the correlation key is ignored | `test_the_correlation_key_resolves_the_right_request` | **CAUGHT** |

**One instrument note, recorded honestly.** `test_the_frame_is_the_one_both_clients_read` is a **text**
check, because the clients are TypeScript and there is no Python tree to parse. It is deliberately narrow
— the frame string **and** the response field — and it fails if either client stops reading them. A text
check over Python would be the defect `CONTEXT.md` §10 names; over TypeScript it is the only available
instrument, and it is stated rather than passed off as an AST check.

---

## 5. Verification

```
the new guard                              18 passed
non-vacuity                                5/5 CAUGHT, tree restored byte-identical
regression (52 files)                      1398 tests — 1397 passed, 1 failed (F38, pre-existing)
  incl. the WS suites                      test_websocket + test_server_approval_contract
                                           + test_transport_ws — all green
canonical block (44 files)                 1312 tests — 1311 passed, 1 failed (F38)
ruff (all changed + new files)             All checks passed
ruff check wisp/                           11 errors — unchanged (F71)
```

`mypy` was **not** re-run: seven `wisp/` files changed, the count is 1844 at HEAD, and this phase does
not claim it moved. **Stating that is weaker than measuring it.**

---

## 6. Findings

**F83 — a capability claim about a path, measured against the path.** ADR-0055 §Context and this
mission's brief both describe the WebSocket channel as implementing *"bidirectional approval flow"*. The
**answer** direction does; the **question** direction has never reached a client, because
`WebSocketTransport.approve()` emits `approval_request` while both shipped clients branch on
`tool_approval_request`. This is F78's shape — *a model is not a path* — one level down: **a claim about
a channel, never driven against the channel.** It has been carried since Phase 10, and it is the third
consecutive mission where the premise handed to the mission was the thing that failed.

**Consequence, recorded not repaired:** the agent path's WebSocket approval prompt has never rendered, so
`approve()` always timed out to deny. **Fixing it would change a live path's behaviour** and is therefore
its own ADR — this ADR names it as residual 1 and pins it with a guard, rather than making the change
silently inside a REST-only feature.

**F84 — the three routes' own comments described a design the flag now changes.** They said *"the desktop
client keeps working because the policy allows this action in full / auto_edit / ask_all"*. That remains
true with the flag OFF, which is the default — so the comments were not *wrong*, they were **incomplete
about a decision that had not been made yet**. They now state both readings. Recorded because the corpus
has previously found comments that assert a contract the code cannot honour (`require_tool_allowed`'s own,
ADR-0055 §4).

---

## 7. What this does not decide

- **G3** — `POST /api/hooks` accepting an unvalidated `command`. It is about *what a hook may run*; this
  ADR is about *who may register one*. Deferred as its own ADR (R9), as ADR-0055 §7 already assigned it.
- **The agent path's dead frame** (residual 1). Reconciling it onto R1's vocabulary would fix the agent
  path and change a live path's behaviour. Its own ADR.
- **`resolve_approval` ignoring the client's `id`** when the *transport* resolves it (residual 2). The
  bridge correlates correctly; the transport's path is weaker and is the agent path's resolver.
- **Multi-client routing** (residual 4): every registered channel is asked and the first response wins.
  Fine for the shipped single-client model.
- **Whether `full` should ask.** It does not, and never did — `full` relaxes approval. Changing that is a
  new decision about the mode vocabulary, not about REST.
