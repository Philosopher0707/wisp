# Phase — Authorization Parity (G1): the decision

**Repository:** `/Users/philosopher/iCloud Drive (Archive)/Documents/wisp`
**Baseline:** `HEAD` = `8de56d8` (docs tip; §0 records `17130c7`). Working tree = the 29 WIP entries
of `CONTEXT.md` §8 — **no drift**.
**Decision:** **ADR-0055** — the REST gate is at parity with the agent path; the recorded divergence
was between two *models*, not two *paths*. **Option A.**
**Change:** a docstring, a test file, and a committed instrument. **No production behaviour moves.**

---

## 1. What the brief claimed, and what the measurement found

The brief (and `PHASE_10_AUTHORIZATION_PARITY.md` §3, and the ratchet's own docstring) carried this
claim:

> *"out of the box, REST permits registering a hook, an MCP server, or a plugin **without the approval
> the agent path requires for the same operation**."*

**Driven, it is false.** It is a claim about **paths**, and it was derived from a comparison of two
**models**. The ratchet compares `auth/decision.authorize()` to `infra/security.SecurityPolicy.check()`
and treats the first as "the agent's verdict". A model is not a path.

Three measurements invert the premise.

### 1.1 Path parity holds — 0 divergences of 36

`scripts/authorization_parity_measurement.py` (committed, re-runnable) drives the two production
surfaces instead of the two models:

```
the agent   ToolExecutor.execute(tool, args, ws, approval_handler=None)
            — `None` because REST *has* no approver; that is REST's own condition
REST        require_tool_allowed(request, action, args, workspace)
```

| | rows | divergences |
|---|---|---|
| agent path vs REST gate (outcome) | 36 | **0** |
| `authorize()` vs `SecurityPolicy` (the ratchet's comparison) | 36 | **6** |

Where the two `DENY`s differ in mechanism (`DENY(guard)` vs `DENY(403)`) the outcome is identical, and
the outcome is what parity is about.

### 1.2 The three actions have no agent path at all

`hooks.create`, `mcp.add_server` and `plugins.install` are **REST-only action names**:

| test | `hooks.create` | `mcp.add_server` | `plugins.install` |
|---|---|---|---|
| in `TOOL_IMPLS` (42 names) | no | no | no |
| a plugin tool (`has_plugin_tool`) | no | no | no |
| a row in `TOOL_RISK_TABLE` (41 rows) | **no** | **no** | **no** |
| in `_get_write_tools()` | no | no | no |

`risk_for_tool` returns `ToolRisk.EXEC` for them only because it **fail-closes on an unknown name** —
the table itself has no row. Driving `ToolExecutor.execute` with those names yields `Unknown tool`.

**So there is no agent operation for them to be at parity *with*.** `authorize()`'s verdict on those
three names is a fact about `authorize()`.

### 1.3 The agent's approval model is not `authorize().approval_required`

`ToolExecutor.execute` consults `authorize()` and forks on **`not _decision.allowed` alone**
(`tool_executor.py:755`) — the decision's `approval_required` is **discarded**. The turn path's
approval requirement is a *third* set: `func_name in _get_write_tools(config)` (`:827`) combined with
`_needs_forced_approval` (`:1324`), both risk-table-derived.

`authorize().approval_required` **is** consumed — by a different agent surface,
`registry.execute_tool` (`tools/registry.py:951`), where it denies.

| Surface | Approval source |
|---|---|
| `ToolExecutor.execute` (the turn path) | `_get_write_tools` + `_needs_forced_approval` |
| `registry.execute_tool` (direct registry) | `authorize().approval_required` |
| `ApprovalGate`, `require_tool_allowed` (REST) | `SecurityPolicy.check().approval_required` |

**Three approval models; the ratchet was comparing two of them.**

---

## 2. The decision — Option A

> **R1** Parity is a property of the paths, and it holds (0/36). The guard re-drives it.
> **R2** The six pinned pairs are reclassified as a **model** divergence and stay pinned.
> **R3** **Option A**: accept, and correct the record.
> **R4** `require_tool_allowed`'s docstring states what its control actually is.
> **R5** The ratchet is updated, not weakened — and gains the property it lacked.
> **R6** Four residuals are named.
> **R7** The relationship to finding E is stated; E is not wired.
> **R8** The three non-violations are asserted by tests.
> **R9** Rollback: revert a documentation-and-test change.

### Why not B

Option B (the REST gate also consults `authorize()`, honouring its approval requirement) **is not a
parity fix**. Its only effect on these routes is to deny `hooks.create` / `mcp.add_server` /
`plugins.install` in `auto_edit` and `ask_all` — modes in which **the agent denies nothing**, because
it cannot run those names at all. B would make REST **stricter than the agent**, with no agent
counterpart: a divergence in the opposite direction, created by a change meant to remove one.

It would also 403 the shipped desktop client on three config routes, and the routes' own comments
state the intended design:

> *"The desktop client keeps working because the policy allows this action in full / auto_edit /
> ask_all and denies only in read_only."* — `wisp/server/routes/{hooks,mcp,plugins}.py`

### Why not C — for this decision

Option C (route approvals through the WebSocket channel) is the correct fix for a **different**
problem: REST cannot ask a human, so a REST caller gets the agent's *no-approver* behaviour rather
than its *approver* behaviour. That gap is real (§3, residual 3) and it is its own ADR. Adopting it
here would change the default-mode behaviour of a shipped client — a feature decision, not a parity
correction.

---

## 3. Residuals, named

1. **The approval authority is split three ways** (§1.3) — **CLOSED 2026-09-27 by ADR-0066 R1**, which
   measured that the three are three *questions*, not three copies, and rejected unifying them.
2. **Three action names are in none of the three approval sets** — **SUPERSEDED 2026-09-27.** `hooks.create`,
   `mcp.add_server` and `plugins.install` have no `TOOL_RISK_TABLE` row, so no risk-table model governs them.
   They **are** `REST_APPROVAL_ACTIONS`, which ADR-0057 created, so an approval model does govern them on REST.
   This residual is the record of what was true before ADR-0057 — not rewritten (ADR-0062 R2, ADR-0066 R2).
3. **REST cannot ask a human.** A REST caller gets the agent's no-approver behaviour, including its
   permissive fall-through for `auto_edit` writes. Driven with `approval_handler=None`, a `write_file`
   in `auto_edit` **runs**: the approval branch is entered, the handler is absent, `forced_approval` is
   `False`, and control falls through. The comment there reads *"auto_approve=True + no handler + not
   forced = pass through"*, but the enclosing guard is `not auto_approve`, so the comment does not
   describe the branch it sits in. **Not repaired here** — `ToolExecutor.execute`'s chain is one of the
   three non-violations (R8). Option C is the fix.
4. **Five further gated routes are not in the parity table** — **MEASURED 2026-09-27.** `hooks.test`,
   `mcp.test_server`, `mcp.remove_server`, `plugins.toggle` and `plugins.uninstall` all pass the policy
   gate and **none asked a human**. **ADR-0066 R3** moved the two *executing* verbs into the set; the
   removal and toggle verbs stay outside it, by ADR-0066 R4.

---

## 4. The change

| File | Change |
|---|---|
| `WISP_ARCHITECTURE_DECISIONS.md` | **ADR-0055** + its index row |
| `wisp/server/deps.py` | `require_tool_allowed`'s docstring: the approval clause is **kept** and **qualified**, and the control those routes actually have is named. **+13 / −0 — a pure docstring addition, no behaviour change.** |
| `tests/test_authorization_parity.py` | updated: `KNOWN_DIVERGENCES` → `KNOWN_MODEL_DIVERGENCES` with the reason corrected; **25 tests** (18 kept, 7 new) |
| `scripts/authorization_parity_measurement.py` | **new, committed** — drives both production paths (F75: an instrument that cannot be committed is not a re-runnable measurement) |
| `WISP_MIGRATION_STATUS.md` | M1's state `IN_PROGRESS` → **`PARTIAL`** (a reason is not a state) |
| `PHASE_AUTHORIZATION_PARITY.md` | this report |

### The guard's new properties

The ratchet keeps **every** property it had — a new divergence fails; a divergence that silently
disappears fails; the file/shell routes stay at model parity; a non-approval-shaped divergence is a
worse class; the agent still consults both models; the default mode is still the affected one — and
gains:

| New test | Property |
|---|---|
| `test_the_real_paths_agree_on_every_route_in_every_mode` | **the one it lacked**: drives both production paths and asserts outcome parity |
| `test_the_route_list_has_a_floor` | the collection is non-empty before it is iterated |
| `test_the_rest_only_actions_have_no_agent_path` | the premise of R2/R3 — **and the reversal condition**: if one gains an agent implementation, this fails |
| `test_the_turn_path_approval_set_is_not_authorize_approval_required` | a model is not a path |
| `test_authorize_is_unchanged` | R8.1 — signature, decision fields, and the `controlling_layer` vocabulary **observed** (≥2 layers reached, all within the pinned set) |
| `test_security_policy_check_is_unchanged` | R8.2 — signature and the **set relationships** (`_AUTO_EDIT_DENY_TOOLS ⊆ _AUTO_EDIT_BLOCK_TOOLS ⊆ _ASK_ALL_BLOCK_TOOLS`), which fail on a removal but not on a legitimate addition |
| `test_tool_executor_gate_chain_order_is_unchanged` | R8.3 — **parsed, not scanned**: the AST of `execute` gives the first call site of each gate, and their order is the property |

---

## 5. Verification

```
the updated ratchet                       25 passed
non-vacuity probes                        4/4 CAUGHT, tree restored byte-identical
regression (43 files)                     1262 tests — 1261 passed, 1 failed
                                          (F38, pre-existing: test_node_identity.py::…::
                                           test_a_parallel_round_is_journaled_as_one_exchange_per_call)
ruff check (changed + new files)          All checks passed
ruff check wisp/                          11 errors — unchanged (F71)
```

**The non-vacuity probes**, each breaking one claimed property:

| # | Mutation | Test it must fail | Result |
|---|---|---|---|
| NV1 | the REST gate stops denying (`if False:`) | `test_the_real_paths_agree_on_every_route_in_every_mode` | **CAUGHT** |
| NV2 | `hooks.create` gains a `TOOL_RISK_TABLE` row | `test_the_rest_only_actions_have_no_agent_path` | **CAUGHT** |
| NV3 | the approval gate leaves `ToolExecutor.execute` | `test_tool_executor_gate_chain_order_is_unchanged` | **CAUGHT** |
| NV4 | `authorize()` starts requiring approval for writes | `test_the_turn_path_approval_set_is_not_authorize_approval_required` | **CAUGHT** |

`__pycache__` was purged on both sides of every probe; each file was restored and verified
byte-identical by sha256.

`mypy` was **not** re-run. One `wisp/` file changed and the change is a docstring; the count is 1844 at
HEAD and this phase does not claim it moved. **Stating that is weaker than measuring it.**

---

## 6. Findings

**F78 — a model is not a path.** The parity ratchet compared two decision *models* and drew a
conclusion about two *paths*. Three phases of the finding rested on it, and the sentence drawn from it
was false in its subject: the agent has **no** `hooks.create` / `mcp.add_server` / `plugins.install`
operation, so REST's `ALLOW` matches the agent's behaviour rather than diverging from it. The class is
the instrument-defect class (`CONTEXT.md` §10) in a new guise: **the instrument measured something
adjacent to its claim.** The fix is the same discipline — drive the real path.

**F79 — a check that passes by finding nothing, again.** The ratchet had seven properties and **none**
compared the two paths it was named for. `test_the_agent_consults_both_models` asserted that
`tool_executor.py` contains the strings `policy_hard_deny(` and `authorize(` — a **string scan over a
Python tree**, which reads the comment that describes the order as readily as the order. The new guard
parses the AST.

**F80 — `tests/test_protected_path_guard.py` cannot run in this environment.** It imports
`fastapi.testclient`, which requires `httpx`; `httpx` is **not installed** in `.venv`
(`ModuleNotFoundError`). Phase 10's 26-test guard for the protected-path fix — the finding G1 is
adjacent to — is therefore **un-runnable here**, and the corpus has been quoting its "26 passed" from
a different environment. Not repaired: installing a dependency is an environment change, not this
phase's.

**F81 — a derived page carries a superseded disposition.** `CURRENT_AUTHORITIES.md` §4 states
*"enablement is ADR-0016's question and remains `NOT_YET_DETERMINABLE`"*. ADR-0051 replaced that
disposition, and ADR-0054/0055 have moved M1 to `PARTIAL`. The page's guard checks `path:line` pins, not
prose, so it cannot catch this. **Corrected in place** (§4 now cites ADR-0051's precondition and M1's
`PARTIAL`); recorded because a derived page that silently drifts is the thing §5 of that page exists to
prevent.

**The brief's own citations, checked.** The brief directed the reader to `CURRENT_AUTHORITIES.md`
*"§1.3, §1.5 (the two authorities whose composition G1 concerns)"*. **Those sections are the
`acceptance verdict` and the `goal state` authorities.** The page covers six authorities — turn
predicate, stream state, acceptance verdict, progress verdict, goal state, recovery ladder state — and
**none of them is the authorization authority**. So the ADR changes no stated authority and
`CURRENT_AUTHORITIES.md` needs **no regeneration** (F81's prose correction is not a regeneration).

---

## 7. What this does not decide

- **Not** whether REST *should* be able to register a hook with only an API key. The measurement says
  REST's behaviour matches the agent's; whether the agent's is right is G3's question (`POST /api/hooks`
  accepts an unvalidated `command`) and the capability asymmetry is a separate decision.
- **Not** the unification of the three approval models (residual 1).
- **Not** finding E. `ToolExecutor.policy` is `None` at both construction sites, so `authorize()`'s L0
  organization-policy slot is never filled. The two findings share a shape — *an authority that exists
  and is not consulted* — but they are not one fix, and wiring L0 would not have prevented G1: L0 sits
  inside `authorize()`, which REST does not call for these names anyway.
- **Not** the five further gated routes (residual 4).
