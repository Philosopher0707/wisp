# PHASE_DECISION_BRIEFS.md — four decisions for the 44 open rows

> **Proposal. Nothing here is ratified.** No ADR in this document is written to
> `WISP_ARCHITECTURE_DECISIONS.md`, no state word is changed, and no register row moves. §3 is a
> complete draft at ADR resolution; §4–§6 are **briefs**, not drafts, and each says what it still
> needs before it can be one.

Generated 2026-09-27 at `bb7fd29`.

---

## §1 — Why four decisions and not 37 rows

Of the 44 open rows, seven are host conditions (`R7`, `R8`, `M1`, `ADR-0053 R1`, `ADR-0054 R1`,
`ADR-0059 R1`, `ADR-0061 R1`) and three are waiting on a measurement. The remaining 34 are
**decisions**, and they cluster: several row names share one question. §3's draft alone would close
**six** rows.

| decision | draft | rows it would close |
|---|---|---|
| the approval authority and the executable-config family | **§3 — complete draft** | `PHASE_AUTHORIZATION_PARITY R1`, `R2`, `R4`, `ADR-0057 R2`, `ADR-0057 R4`, `ADR-0061 R2` |
| per-client routing of an approval | folded into §3 as R4/R5 | (above) |
| the deferred structured-delegation set | §4 — brief | `P9 · item 2`, `3`, `4`, `5`, `M7`, `P3 · item 7`, `ADR-0058 R1` |
| the context subsystem's open set | §5 — brief | `P8 · item 3`, `4`, `5`, `6`, `7`, `P4 · item 1` |
| the remaining singletons | §6 — brief | `R10`, `R3`, `M5`, `M6`, `P3 · item 5`, `P3 · item 6`, `ADR-0053 R2`, `ADR-0054 R4`, `PHASE_M4_WIRING R2`, `PHASE_LAYER_B_BOUNDARY R1`, `PHASE_EXTERNAL_INPUT_PATH R1`, `PHASE_OBJECTIVE_FLAG_COMPOSITION R1`, `R2`, `PHASE_GATE_ENABLEMENT R2` |

**I spent the measurement budget on §3 rather than spreading it.** It is the only one of the four
with a security consequence, and it turned out to have a live one (§2). §4–§6 are honest briefs with
their measurement still owed, not thin drafts pretending to be decisions.

---

## §2 — The measurement §3 rests on

Every gate call in `wisp/server/routes/`, enumerated. Two gates exist and they are separate: the
**policy** gate (`require_tool_allowed` — API key, `authorize()`, the mode engine, the protected-path
guard) and the **human** gate (`require_rest_approval` — asks a client over the WebSocket, ADR-0057).

| route | action name | policy gate | human gate |
|---|---|---|---|
| `POST /api/hooks` | `hooks.create` | `hooks.py:110` | **`hooks.py:112`** |
| `POST /api/hooks/{name}/test` | `hooks.test` | `hooks.py:158` | — |
| `POST /api/mcp/servers` | `mcp.add_server` | `mcp.py:95` | **`mcp.py:97`** |
| `POST /api/mcp/servers/{name}/test` | `mcp.test_server` | `mcp.py:145` | — |
| `DELETE /api/mcp/servers/{name}` | `mcp.remove_server` | `mcp.py:161` | — |
| `POST /api/plugins` | `plugins.install` | `plugins.py:81` | **`plugins.py:83`** |
| `POST /api/plugins/{name}/toggle` | `plugins.toggle` | `plugins.py:127` | — |
| `DELETE /api/plugins/{name}` | `plugins.uninstall` | `plugins.py:144` | — |
| `POST /api/files` (+4 edit/delete) | `write_file` / `edit_file` | `files.py:113…163` | — (mode-based) |
| `POST /api/bash` | `run_bash` | `bash.py:35` | — (mode-based) |

**Fourteen policy-gated call sites. Three human-gated.** The three are exactly
`REST_APPROVAL_ACTIONS` (`approval_bridge.py:57`).

### The finding

**The set gates *persisting* an executable, not *executing* one.**

`REST_APPROVAL_ACTIONS`' own rationale (`approval_bridge.py:54-56`) is: *"These persist something Wisp
later **executes** — a hook command, an MCP server, a plugin — which is why they are the ones that
ask."*

But the route family has **test** verbs that execute immediately, and they are not in the set. And the
code says so itself, in two places, in the same words:

- `hooks.py:156-157` — *"Testing a hook **EXECUTES its command** — the same authority class as
  creating one. Gating only `create_hook` would leave this as a bypass."*
- `mcp.py:142-144` — *"Health-checking an MCP server **SPAWNS** it — the same authority class as
  registering one. Gating only `add_mcp_server` would leave this as a **spawn-by-other-means
  bypass**."*

Both authors closed the **policy** half of that bypass and left the **approval** half. So with
`WISP_REST_APPROVAL` ON in the default `auto_edit` mode — the configuration an operator turns on
precisely to get this control — **creating a hook asks a human and running it does not**, and
**registering an MCP server asks a human and spawning it does not.**

The removal verbs (`mcp.remove_server`, `plugins.uninstall`) and the toggle are also outside the set,
which is defensible for removal and less so for `plugins.uninstall` — it deletes what an approval
authorised. That asymmetry is stated, not decided.

`PHASE_AUTHORIZATION_PARITY R4` named this family as *"unmeasured here … so the next reader does not
assume the table is exhaustive"*. The table above is that measurement.

---

## §3 — ADR-0066 (ratified, and implemented in the change that ratified it)

Written to `WISP_ARCHITECTURE_DECISIONS.md` — body after ADR-0065, plus its row in the Decision
index — with **R3 implemented in the same change** rather than deferred. §8 records the execution.
The text below is the ratified decision; two things changed against the draft and both are noted
there: R3 is implemented, and a guard was repaired because R3's own comment tripped it.

> ### ADR-0066 — The approval authority stays three questions, and the executable-config set covers executing
>
> **Status:** proposed.
> **Phase:** the approval authority (`PHASE_AUTHORIZATION_PARITY.md`, ADR-0057, ADR-0061)
> **Evidence:** the gate enumeration in §2; `wisp/server/approval_bridge.py`; `wisp/transport/websocket.py:213`;
> `wisp/server/routes/{hooks,mcp,plugins}.py`
>
> **Context.** `PHASE_AUTHORIZATION_PARITY`'s residual 1 calls the approval authority *"split three
> ways"* and defers unifying it to its own ADR. Residual 2 says three action names are *"in none of
> the three approval sets"*. Residual 4 names five further gated routes as unmeasured. Driven (§2),
> residual 2 is **superseded** and residual 4 is **measured**, and the measurement exposes a gap in
> the set the earlier residuals were written before.
>
> **R1 — The three mechanisms are three questions, and they stay three.** Measured, they are not
> three copies of one rule:
> - `auth/decision.py`'s L5 answers *"does this permission mode require approval for this risk
>   class?"* — the agent path's question;
> - `SecurityPolicy.check()` answers *"does the loaded policy or mode require approval?"* — both paths;
> - `REST_APPROVAL_ACTIONS` answers *"does this REST-only action persist something executed?"* — and
>   its three names have **no agent operation at all** (ADR-0055's Option B, re-measured: they have no
>   `TOOL_RISK_TABLE` row and are not agent tools).
>
> Unifying them is **rejected**, for ADR-0055's own reason: the only unification available is to give
> the three names a risk-table row, which would make REST stricter than the agent on routes where the
> agent denies nothing — with no counterpart to be at parity with. A shared vocabulary is not the
> same question.
>
> **R2 — `PHASE_AUTHORIZATION_PARITY R2` is superseded, not repaired.** It reads *"`hooks.create`,
> `mcp.add_server`, `plugins.install` … no approval model governs them on either path"*. That was true
> when written and is false now: those three **are** `REST_APPROVAL_ACTIONS`, which ADR-0057 created.
> The residual is recorded as superseded and its source is **not rewritten** — it is the historical
> record of what was true before ADR-0057.
>
> **R3 — The executable-config set covers executing, not only persisting.** `hooks.test` and
> `mcp.test_server` join `REST_APPROVAL_ACTIONS`. The ground is the routes' own comments (§2): each
> declares itself *"the same authority class as creating one"*, and each is a live bypass of the
> human gate while it is outside the set. The set's stated rationale — *"these persist something Wisp
> later executes"* — is **narrower than its purpose**: a route that executes immediately is at least
> as deserving as one that persists for later execution. The asymmetry the flag currently ships with
> is that a human is asked to authorise a capability and not to exercise it.
>
> **R4 — Removal is outside the set, and that is decided rather than left implicit.** `mcp.remove_server`
> and `plugins.uninstall` narrow rather than widen what is installed, and a removal that needs an
> approval is an approval an operator may not be present to give. `plugins.uninstall` is the harder
> case — it deletes what an approval authorised — and is **named here as the cost**: an operator who
> wants removal gated wants it for a different reason than this set exists for.
>
> **R5 — An approval is correlated by its id, and the single-pending fallback is a stated shim.**
> `resolve_approval` (`websocket.py:213`) and `receive_message`'s `tool_approval` branch both fall
> back to *"resolve the single unresolved entry"* when the id is unknown or absent. That is
> **back-compatibility for a client that does not echo the id**, and it is safe only because a second
> concurrent approval makes the fallback a no-op (`len(pending) == 1`). It is kept, with its condition
> stated: the fallback is correct while it is unreachable for a correct client — which ADR-0061 R2
> made true, since `call_id` **is** the `_approvals` key. It is removed when no shipped client sends
> an un-echoed id.
>
> **R6 — A multi-client deployment asks every channel and takes the first response; per-client routing
> is a separate decision, and is not owed.** Routing an approval to the client that issued the request
> presupposes a client identity the transport does not carry — `_approvals` is keyed by `call_id`, not
> by client. Adding one is a change to the frame vocabulary ADR-0061 just fixed, and it is only
> meaningful in a deployment with more than one client, which this host does not have (ADR-0061 R1).
> The broadcast is the model until a multi-client deployment exists to route *for*.
>
> **Consequences.** Six rows close: `PHASE_AUTHORIZATION_PARITY R1` (R1), `R2` (R2), `R4` (R3/R4 —
> measured), `ADR-0057 R2` (R5), `ADR-0057 R4` (R6), `ADR-0061 R2` (R5). **R3 is a live behaviour
> change** — two routes begin asking a human — and it is behind `WISP_REST_APPROVAL`, which defaults
> OFF, so the default behaviour is unchanged.
>
> **Rejected.**
> - *Unifying the three mechanisms* (R1) — see above.
> - *Adding a risk-table row for the three REST-only names* — ADR-0055 §Why-not-B, re-measured.
> - *Per-client routing now* (R6) — no client identity to route by, and no multi-client deployment to
>   route for. Building the mechanism before its precondition is the pattern this corpus keeps
>   diagnosing.
> - *Gating removal* (R4) — stated as a cost, not adopted.
>
> **Residuals, named.**
> 1. **`write_file`, `edit_file` and `run_bash` are outside the set** and are governed by the mode
>    engine instead. They are agent tools with an agent counterpart, so they belong to `SecurityPolicy`'s
>    question — but the consequence is that the two gates cover disjoint route sets, and a reader
>    checking "is this route gated?" must know which question applies.
> 2. **`plugins.toggle` is neither installing nor removing** and is in neither set. Named, not decided.
> 3. **The four agents' approval sets are still hand-written.** R3 adds two names to a literal
>    `frozenset`. A route added without a corresponding entry is silent — no test enumerates the
>    routes and asserts each is classified. That guard is the thing that would make this set
>    self-maintaining, and it is not built here.
>
> **Reversal trigger.** A multi-client deployment (R6 reverses), or a shipped client that sends an
> un-echoed id (R5's shim stops being unreachable and becomes load-bearing).

---

## §4 — Brief: the deferred structured-delegation set

**Rows:** `P9 · item 2` (structured `child_goal`), `P9 · item 3` (mandatory `result_schema`),
`P9 · item 4` (transactional effects), `P9 · item 5` (typed failure replacing prose markers),
`M7` / `P3 · item 7` (wire `change_tracker.py` into evidence), `ADR-0058 R1` (the multi-device
ceremony).

**What is known.** Every one carries `blocked_by` = *"deferred"*, and ADR-0065 R4 ruled that
*"deferred" is not a disposition* — a future is not a closure. So these stay open until something
decides them. They are not one decision either: `P9` items 2–5 are one work programme (the structured
delegation contract), `M7` is a wiring task deferred *with* stage 3b, and `ADR-0058 R1` is a
spec-deferred ceremony.

**What it needs before it is a draft.** The `P9` set's own plan (`WISP_SUBAGENT_ARCHITECTURE.md`)
states a contract that the current `spawn`/`fanout` surface does not implement. The decision is
whether that contract is **owed** (and then it is a work item, not a decision) or **re-scoped as not
owed** (the ADR-0060 R2 pattern: *"the removal is not owed"*). Choosing requires reading the plan's
P9 section against the current subagent surface and measuring the gap — the same shape as ADR-0060,
which decided an inexpressible position by driving it.

**Do not draft this as a batch deferral.** Recording six rows as "deferred" a second time is the
non-decision ADR-0065 R4 exists to refuse.

---

## §5 — Brief: the context subsystem's open set

**Rows:** `P8 · item 3` (graph context scoped to the current node), `P8 · item 4` (populate plan
context), `P8 · item 5` (serve the symbol-level repo map), `P8 · item 6` (token-based compaction),
`P8 · item 7` (memory origin), `P4 · item 1` (reuse `wisp/graph/`'s store).

**What is known.**
- `P8 · item 3` is **already decided**: *"no current node exists — nothing drives execution (M11, now
  decided as the permanent boundary)"*. ADR-0060 R2 made the boundary permanent, so a context section
  scoped to a current node has no referent **by decision**. This one is closeable now, under
  ADR-0060, without a new ADR — the same way `R9` closed under ADR-0060 R5. **It should not wait for
  this brief.**
- `P8 · item 4` is recorded as *"a **product decision**, not a mechanical change"* — the plan says
  *"populate or remove"*. That is a genuine decision and it is small.
- `P8 · item 5` was blocked on `tiktoken`; it is installed now, so the 1200-token budget **can** be
  measured faithfully. The plan requires *"measure first"* — so the next step is the measurement, not
  a decision.
- `P8 · item 6` (token-based compaction) *"changes when context is destroyed, on the least observable
  path"* — the highest-risk item in the set and the one least suited to a paper decision.
- `P8 · item 7` is `PARTIAL` — `Provenance` supplies the field, `memory.py` is not wired to use it.

**What it needs.** `P8 · item 5`'s measurement first (cheap now, and it feeds item 6). Then items 4
and 7 are decidable; items 3 closes under ADR-0060 immediately; item 6 wants a measurement, not an ADR.

**The cheap win:** `P8 · item 3` can close in the next batch under ADR-0060 R2 with no new decision.

---

## §6 — Brief: the remaining singletons

Thirteen rows that share no neighbour, so each needs its own ruling. Listed with what the ruling is:

| row | the ruling it needs |
|---|---|
| `R10` | whether to canonicalize the 26 re-implementations now the ratchet records them; the 32 pre-existing renderer errors and the vacuous `typecheck` script are named |
| `R3` | full provider-listing delegation — blocked until the 3 deltas (auth/timeout/degradation) converge; `test_provider_listing_equivalence.py` fails at that point and signals it |
| `M5` | foreground-turn `RunRecord` lifecycle — proven end-to-end for background runs only |
| `M6` | `PolicyDecisionEnvelope` — producer-less and consumer-less; "the last unwired contract" |
| `P3 · item 5` | structural independence L1/L2 — `PARTIAL`; L3 is preferred, not required |
| `P3 · item 6` | the completion rule requires non-invalidated evidence — it *is* stage 3b (= `M1`) |
| `ADR-0053 R2` | a declared failure does not produce a replan — making it repairable means moving the probe into the engine |
| `ADR-0054 R4` | `stagnation_gate` untouched — ADR-0037 forbids enabling it without a superseding ADR |
| `PHASE_M4_WIRING R2` | `acp_session.py:208` — an ACP-only deployment would need the same load; named, not done |
| `PHASE_LAYER_B_BOUNDARY R1` | `wisp/graph/api.py` has no importer — public API or dead code? |
| `PHASE_EXTERNAL_INPUT_PATH R1` | is `vscode-extension/` a "shipped client" in ADR-0057's sense? |
| `PHASE_OBJECTIVE_FLAG_COMPOSITION R1` | `CriteriaDerivation.strict` records `True` when pre-empted — a record change |
| `PHASE_OBJECTIVE_FLAG_COMPOSITION R2` | whether objectives *must* declare — a policy with its own evidence |
| `PHASE_GATE_ENABLEMENT R2` | the projection is proven over the guard's state space, not all inputs |

**Three of these are cheap and worth doing first**, because they need a reading rather than a
programme: `PHASE_LAYER_B_BOUNDARY R1` (grep the tree for an importer — if none, it is dead code and
ADR-0060's disposition pattern applies), `PHASE_EXTERNAL_INPUT_PATH R1` (a definitional question
ADR-0057 already framed), and `PHASE_M4_WIRING R2` (the same load at a second site — a work item, not
a decision).

---

## §7 — What this document did not do

- **It ratified nothing.** No ADR entered `WISP_ARCHITECTURE_DECISIONS.md`.
- **It closed nothing.** No state word changed; `CURRENT_OPEN_ITEMS.md` is untouched by this file.
- **It did not edit a source.** Every quotation above is read from the file it names.
- **It did not draft §4–§6 as ADRs**, and says why in each. A thin draft that looks like a decision
  is worse than a brief that says what is missing — the corpus has a name for the former.
- **It corrected one of its own errors.** I first read the gate list with `require_tool_allowed(request`
  and concluded `hooks.test` was ungated. It is gated — the call passes `http_request`
  (`hooks.py:158`). The §2 table is the re-measured version. Recorded because the wrong conclusion was
  one step from being written into an ADR.

---

## §8 — What was executed

ADR-0066 was ratified and its R3 implemented in the same change. **37 open, from 44.**

### The decision

Written into `WISP_ARCHITECTURE_DECISIONS.md` (body after ADR-0065, plus an index row), and `CONTEXT.md`
§13's ADR range advances to **ADR-0001 … ADR-0066**.

### R3, implemented rather than deferred

Two names joined `REST_APPROVAL_ACTIONS`, and both routes now call `require_rest_approval` **after**
their policy gate:

| | before | after |
|---|---|---|
| `REST_APPROVAL_ACTIONS` | 3 names | **5** — `hooks.test`, `mcp.test_server` added |
| `require_rest_approval` call sites | 3 | **5** |
| pinned (action, mode) pairs | 6 | **10** |

**Why it was safe to do in the same change, measured not assumed:** `risk_for_tool` fails closed to
`ToolRisk.EXEC` for a name with no table row (`contracts.py:316`), and `authorize()` already returned
`approval_required=True` for `EXEC` in `auto_edit` and `ask_all`. **The authority already said these two
routes require approval; the REST gate simply did not ask.** All ten pinned pairs satisfy
`allowed and approval_required`. Behind `WISP_REST_APPROVAL`, default **OFF**, so the default is
unchanged; a REST caller with no connected client is denied, not hung.

### A guard was repaired, because R3 tripped it

`test_external_input_path.py` asserted the trigger is a set with `assert "approval_required" not in src`
— a **bare string scan over the whole module**. It failed on *this ADR's own comment* naming the
attribute, while a real read of it, split across lines or reached via `getattr`, would have passed.
It is now AST-based and scans for a read.

Non-vacuity driven: the new scan finds **6 reads in `auth/decision.py`** and **2 in `deps.py`**, and
correctly **none** in `approval_bridge.py`. So it bites when it should.

That is the third instance of this instrument class in one session — with `PHASE_DAG_RETIREMENT R1` and
`PHASE_LAYER_B_BOUNDARY R2`. **It was found by a change, not by review**, which is the argument for
making these scans parse rather than search.

### Sources amended, and re-pinned in the same change

`PHASE_AUTHORIZATION_PARITY.md` residuals 1, 2 and 4; ADR-0057's residuals 2 and 4; ADR-0061's
residual 2; and `WISP_MIGRATION_STATUS.md:862` (`P8 · item 3`). Every register source cell moved with
its line — `ADR-0057 R2` `:5830` → `:5831`, `ADR-0061 R2` `:6593` → `:6595`, and the two
`PHASE_AUTHORIZATION_PARITY` pins to the lines that now carry the closure.

### Seven rows closed

`PHASE_AUTHORIZATION_PARITY R1` (R1) · `R2` → **`SUPERSEDED`** (ADR-0057 created the set; ADR-0066 R2
records it) · `R4` (R3/R4, now measured) · `ADR-0057 R2` (R5) · `ADR-0057 R4` (R6) · `ADR-0061 R2` (R5)
· `P8 · item 3` — the last one under **ADR-0060 R2**, a pre-existing decision, exactly as §5 predicted:
no current node exists *by decision*, so the section has no referent and is not owed.

### Verified

Register regenerated twice — byte-identical. All three generators pass. **78 tests** across the five
affected suites. The §(c) host-count paragraph needed **no** change this time: none of the seven was in
its list, which is the first batch where that has been true.

### Still open, deliberately

`ADR-0059 R1` remains `PARTIAL` with its pinned-pair count moved six → ten. Its "closed in effect"
reading was measured over six pairs and is now over ten; **the measurement is owed again**, and
ADR-0066 R3's coherence argument is why it is expected to hold rather than why it may be assumed.
That is the one row this change made *less* settled, and it is named rather than closed.

---

## §9 — The decision queue: the 27 rows that remain, with a recommendation for each

Every remaining row needs one of four things, and **none of them is more work of the kind that closed
the other 75.** Three groups cannot be closed by anyone on this host; the rest are rulings. My
recommendation is stated for each so they can be approved in one pass rather than argued one at a time.

### Group 1 — cannot be closed here (7). No recommendation; these are facts.

| row | why |
|---|---|
| `R7`, `R8` | **your WIP.** `CONTEXT.md` §8 says *"do NOT commit or delete"*. Measured cause of `R8`: its three files import `wisp.multi_agent.delegation`, **removed by `11fc949`**, so they test a module that deliberately no longer exists. Only you can say whether they are stale or mid-work |
| `M1`, `ADR-0053 R1`, `ADR-0054 R1`, `PHASE_GATE_ENABLEMENT R1`, `P3 · item 6` | re-measured 2026-09-27 with the committed instrument: **1 capable model of 13** (5 retired, 5 paywalled, 2 degenerate). ADR-0051 R4 needs ≥ 2. Closing these means reporting a population this host does not serve |
| `ADR-0061 R1` | no real desktop/TUI/VS Code client runs here |

### Group 2 — a measurement owed (3). I can run these; they need no ruling.

| row | the measurement |
|---|---|
| `ADR-0059 R1` | whether a bundle's `approve` level leaves the REST verdict identical, re-derived over the **ten** pinned pairs (it was six) |
| `P8 · item 5` | the 1200-token budget on the symbol-level repo map. `tiktoken` is installed and verified (`cl100k_base`, 1000 chars → 125 tokens); the plan asks only to *measure first* |
| `P8 · item 6` | when token-based compaction would destroy context — the least observable path, and the one that most needs a measurement before a decision |

### Group 3 — a ruling owed (17). **My recommendation in bold.**

| row | the ruling, and what I would decide |
|---|---|
| `M6` | *Wire or delete `PolicyDecisionEnvelope`?* Measured: a complete, fully-tested frozen dataclass — 4 conversion classmethods, `from_dict` with unknown-field rejection — with **no production producer and no consumer**, and three fields commented *"reserved, supplier TBD Phase 1"*. **Delete it, and declare a wire form when its producer exists.** It is speculative generality with a supplier that never arrived; keeping it costs a contract nobody can use, and `F2`'s precedent (*"deleted as superseded"*) is the nearest one. *Rejected:* keeping it as a declared-but-unwired contract — the corpus calls it *"the last unwired contract"*, which is an unfinished wiring, not an accepted state |
| `PHASE_M4_WIRING R2` | *Does the ACP fallback load the organization policy?* Measured: `acp_session.py`'s `_get_tool_executor` prefers the composition root's executor and otherwise builds `ToolExecutor(self.config, …)` **with no `policy=`**. But `composition.py:167` says the policy is *"loaded ONCE, here — the single load site"* (ADR-0058). So this is **not a patch — it is a conflict**: wiring the fallback creates the second load site ADR-0058 forbids. **Decide that a root-less ACP deployment is ungoverned *by construction*, and make it loud** — a warning at construction, not a silent gap. *Rejected:* a second load site; *rejected:* leaving it silent |
| `M5` | *Foreground-turn `RunRecord` lifecycle.* Proven end-to-end for background runs only. **Close the gap: give the foreground turn the same lifecycle** — the asymmetry is the defect, and background-only evidence is the weaker half |
| `M7`, `P3 · item 7` | *Wire `change_tracker.py` into evidence.* Both deferred *with* stage 3b, and stage 3b is `M1` — so they are **blocked on the model population**, not deferred. **Re-state their obstacle to say so** rather than leaving "deferred" standing; they cannot close before `M1` does |
| `P9 · item 2`, `3`, `4`, `5` | *Is the structured-delegation contract owed?* Four items of one programme. **Decide the programme, not the items:** read `WISP_SUBAGENT_ARCHITECTURE.md`'s P9 section against the current `spawn`/`fanout` surface and rule either *owed* (then they are work, not decisions) or *re-scoped as not owed* — ADR-0060 R2's pattern. **I would re-scope items 3–5 (mandatory `result_schema`, transactional effects, typed failure) as not owed** — each is a contract for a delegation surface that ADR-0060 R2 declined to grow — and treat item 2 (structured `child_goal`) as owed |
| `R3` | *Full provider-listing delegation.* Blocked until the three deltas (auth/timeout/degradation) converge; `test_provider_listing_equivalence.py` fails at that point and signals it. **Leave open with the tripwire as the trigger** — this is the one row whose obstacle is a *test that will tell you*, which is the best kind |
| `R10` | *Canonicalize the 26 re-implementations?* The functional half is fixed; what remains is 32 pre-existing renderer errors, a vacuous `typecheck` script, and the 26 re-implementations the ratchet records. **Fix the vacuous script first** — a check that cannot fail is worse than no check, and it is the same defect class as the three bare-string-scan guards this session found |
| `ADR-0053 R2` | *Should a declared failure replan?* Making it repairable means moving the probe into the engine — its own decision. **Leave open and name it as the engine change it is**; it is not a register edit |
| `ADR-0059 R2` | *L1/L2/L3 are not consulted without a bundle — and the real gap is workspace quarantine.* A quarantined workspace denies non-read tools on the agent path and **does not on REST unless a bundle is loaded**. **Decide that quarantine must be enforced on REST independently of a bundle** — it is a security control whose enforcement should not depend on an unrelated configuration being present. This is the highest-value ruling in this queue |
| `PHASE_OBJECTIVE_FLAG_COMPOSITION R1` | *`CriteriaDerivation.strict` records `True` when the declaration path pre-empted it.* A record that cannot distinguish *"strict acted"* from *"strict had nothing to act on"*. **Make the boolean tri-state** — the record should say which happened |
| `PHASE_OBJECTIVE_FLAG_COMPOSITION R2` | *Must objectives declare?* ADR-0056's named reversal condition, and a policy with its own evidence. **Leave open** — it is explicitly the condition under which ADR-0056 reverses, so closing it would close the reversal condition itself |

### What I would do next, in order

1. **Run the three measurements** (Group 2) — no ruling needed, and two of them feed decisions.
2. **`ADR-0059 R2`** — the quarantine ruling. It is the only security-relevant item left.
3. **`M6`** and **`PHASE_M4_WIRING R2`** — both small, both decided above, both implementable in one change.
4. **The `P9` programme** — one reading, four rows.
5. **`R10`'s vacuous `typecheck` script** — a check that cannot fail.

**I did not close any of the 27, and I am not going to guess at them.** Three groups of reasons are in
front of you, each row has a recommendation, and every recommendation is grounded in something
measured this session rather than in a preference.
