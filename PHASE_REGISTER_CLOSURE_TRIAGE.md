# PHASE_REGISTER_CLOSURE_TRIAGE.md — can the open register be closed, and what would close it

> **A triage, not a closure.** This document changes no state word and closes no item. It
> classifies all 64 open rows of `CURRENT_OPEN_ITEMS.md` by *what would actually close them*,
> measures the instrument that would have to record it, and names the defects it found.
> The one decision it proposes is drafted in §6 and is **unratified**.

Generated 2026-09-27 at `bb7fd29` (branch `plan-cli`).

---

## §0 — The verdict, first

**The request was "go through the open register and close it all for good." Three quarters of it
cannot be closed, and attempting it would corrupt the corpus.**

Measured: of the 64 open rows, **26 are closeable** — 12 by an already-recorded decision or a
duplicate, 14 by changing the host rather than the code. **The remaining 38 are open because a
decision has not been made.** They are not awaiting work; they are awaiting *rulings*. Their own
`blocked_by` cells say so: *"unifying it is its own ADR"*, *"per-client routing is its own ADR"*,
*"deferred by the M4 spec"*, *"its own decision"*.

Closing those 38 without making the decisions would mean writing 38 closure citations into
`scripts/derive_current_open_items.py` for closures that never happened. That is not a shortcut —
it is the exact failure this corpus was built to prevent, and `_check()` would refuse most of it
anyway (§1).

**What "for good" can honestly mean here:** the 26 close, and the 38 get *classified* — each one
either routed to the decision that would close it, or recorded as genuinely blocked with a named
obstacle. That is a register that is *finished being vague*, which is the useful reading of the
request.

---

## §1 — What "closing" means here, measured

The register cannot be closed by editing it. `CURRENT_OPEN_ITEMS.md` carries the banner
*"DERIVED DOCUMENT — REGENERATE, DO NOT EDIT IN PLACE"* and the reason is structural, not stylistic.

**The instrument.** `scripts/derive_current_open_items.py` holds the 102 rows as a literal `ROWS`
list in the source (line 33 onward) and renders them. `ROWS` *is* the register; the markdown is a
projection of it. So a closure is an edit to `ROWS` — `state` → `COMPLETE`, and a `source` cell that
cites where the closure is recorded.

**The gate.** `_check()` (line 322) refuses to render unless all of these hold:

| check | what it forces |
|---|---|
| ids unique | no row can be split into two to look smaller |
| `state` in the ledger's six | no new state word can be coined |
| source file **exists**, cited line **in range** | a citation cannot point at nothing |
| no `\|` in any cell | the markdown table cannot be broken |
| tripwire path **exists** | a claimed guard must be real |
| every quoted source appears **verbatim on the cited line ±3** | **the load-bearing one** |
| ≥ 60 sources carry a checkable quote (`QUOTE_FLOOR`) | the quote check cannot go vacuous |

The sixth row is the whole story. `scripts/register_pins.py` parses `path:line — “the source's own
words”` and looks in a ±3-line window for those words. **A closure citation that does not exist at
its source fails the gate and the register refuses to generate.** That is why "close it all" cannot
be faked: the instrument was built to make exactly that impossible.

**The register is currently faithful.** Measured: regenerating at `bb7fd29` reproduces the committed
page byte-for-byte except the commit stamp (see **F-T6**). The 64/38 split is not drift.

---

## §2 — The six closure modes, derived from the 38 closed rows

Read off the closed table's `source` cells. A closure is earned in one of six ways:

| mode | how it reads in the source cell | example |
|---|---|---|
| **1. Landed change** | the work was done and measured | `E`, `G1`, `R1b`, `W1`, `M2`, `M3` |
| **2. Decision** | an ADR ruled | `M4`→ADR-0027, `M13`→ADR-0034, `M8`/`M11`/`Layer C`→ADR-0060 |
| **3. Re-scope** | the work is *not owed* | `M8` — *"the removal is **not owed**"*; `P5 · item 5` — the target was **rejected** |
| **4. Non-issue by analysis** | measured, and it is not debt | `R4` — *"**Not debt** — different axis"*; `R5` — *"Resolved as a non-issue by the migration"* |
| **5. Deletion** | the subject was removed | `F2` — *"deleted as superseded"* |
| **6. Superseded by a later item** | an earlier deferral completed later | `P0 · item 6` → deferred to `P1`, which is `COMPLETE` |

**Modes 3, 4 and 5 are the interesting ones**, because they close an item *without changing code*.
They are also the modes the open table under-uses — which is the substance of **F-T1**.

---

## §3 — The triage

26 of 64 classified; 38 remain, and §3.6 says why.

| class | rows | what closes it |
|---|---|---|
| **C1** | 10 | Decision already recorded — vocabulary repair |
| **C2** | 2 | Duplicate row — same item as another id |
| **C3** | 7 | Host: an absent dependency blocks it |
| **C4** | 3 | Host: the user's own uncommitted work |
| **C5** | 4 | Host: no capable-model population |
| **C6** | 38 | Its own decision — an ADR, or a landed change |

### C1 — Decision already recorded — vocabulary repair (10)

| id | state | title |
|---|---|---|
| **F3** | `NOT STARTED` | `execute_tool(security_policy=…)` — no caller passes it |
| **F4** | `NOT STARTED` | `spawn_with_guards` is a dead duplicate |
| **F5** | `NOT STARTED` | event-replay `TOOL_CALL` — the referent is unidentifiable |
| **G2** | `NOT STARTED` | The `run_bash` verb scan is a separate mechanism from the predicate |
| **M10** | `NOT STARTED` | The materialized graph is a lower bound on iterations |
| **ADR-0053 R3** | `NOT STARTED` | `command_succeeds` runs the declared command after `done`, once per declared turn |
| **ADR-0054 R2** | `NOT STARTED` | The declared command runs once per declared turn |
| **ADR-0054 R3** | `NOT STARTED` | The gate shares the stagnation gate's budget |
| **ADR-0058 R2** | `NOT STARTED` | `WISP_POLICY_CACHE` and `load_managed` are not engaged |
| **PHASE_DAG_RETIREMENT R2** | `NOT STARTED` | `dag_to_graph` has no production caller |

**Why these are closeable.** Each `blocked_by` states a *disposition*, not an obstacle: *"by design"*,
*"a stated cost"*, *"accepted"*, *"compat is test-only"*, *"no action"*. A disposition is a decision.
The corpus already closes items on exactly this ground — `ADR-0058 R4` closed as *"(R7), by design"*,
`R4` as *"Not debt"*, `R5` as *"Resolved as a non-issue"*. These ten are the same shape and are
carried as open instead. **F-T1** is the reason, and it needs a ruling before the states can move.

### C2 — Duplicate row — same item as another id (2)

| id | state | duplicate of | evidence |
|---|---|---|---|
| **ADR-0061 R3** | `NOT STARTED` | `ADR-0057 R4` | its own `blocked_by`: *"— (ADR-0057 residual 4, unchanged)"* |
| **PHASE_M4_WIRING R5** | `NOT STARTED` | `ADR-0058 R2` | same title; `blocked_by` cites a residual that does not exist (**F-T5**) |

Two ids, one item, counted twice against the open total. A duplicate closes as `SUPERSEDED`
pointing at the canonical id — the mode exists in the vocabulary and has no members yet.

### C3 — Host: an absent dependency blocks it (7)

| id | state | the dependency |
|---|---|---|
| **R6** | `PARTIAL` | `httpx`, `cryptography`, `numpy`, `tiktoken`, `aiohttp` |
| **P8 · item 5** | `NOT STARTED` | `tiktoken` |
| **PHASE_OUTCOME_CLASSIFICATION_VIOLATION R2** | `NOT STARTED` | `httpx` |
| **ADR-0059 R1** | `PARTIAL` | `cryptography` |
| **ADR-0061 R4** | `NOT STARTED` | `cryptography` |
| **PHASE_M4_WIRING R3** | `NOT STARTED` | `cryptography` |
| **PHASE_KEY_TRUST_WORKFLOW R6** | `NOT STARTED` | `cryptography` |

**Measured this session:** all five are absent from the venv (`.venv/bin/python -c "import …"`);
`jsonschema` is present (4.26.0, matching `CONTEXT.md` §12's *"`jsonschema` is **fixed** (F8)"*).
And **`pypi.org` answers HTTP 200** — so the obstacle `CONTEXT.md` records as *"Re-attempted offline
2026-09-25: both `httpx` and `cryptography` still fail"* is **an offline-host fact, not a permanent
one**. Installing `cryptography` alone unblocks 4 of these 7.

### C4 — Host: the user's own uncommitted work (3)

| id | state | what it is |
|---|---|---|
| **R7** | `NOT STARTED` | `wisp/capability_filter.py` untracked but imported |
| **R8** | `NOT STARTED` | untracked test files abort collection |
| **R9** | `NOT STARTED` | `wisp/core/graph/__init__.py` modified, uncommitted |

**R9 is measurably risk-free.** Its uncommitted diff is a module-docstring rewrite only. Proved with
both instruments — docstring-stripped AST equality **True**, recursive `co_code` equality **True** →
**prose-only, exit 0**. Closing R9 is a commit decision, not a code decision. R7 and R8 are commits
too, but they carry real behaviour and deserve their own review.

### C5 — Host: no capable-model population (4)

| id | state | obstacle |
|---|---|---|
| **M1** | `IN PROGRESS` | ADR-0051 R4 needs **≥ 2 capable models**; this host serves **1 of 13** |
| **ADR-0053 R1** | `NOT STARTED` | no declared turn population exists (= M1's population) |
| **ADR-0054 R1** | `NOT STARTED` | the population is short by one capable model |
| **PHASE_GATE_ENABLEMENT R1** | `NOT STARTED` | the gate was never exercised, so it is NOT MEASURED |

One root cause, four rows. `PHASE_GATE_ENABLEMENT R1` is explicit that the honest move is to leave
it open: *"Reporting those as zero would be manufacturing a metric."*

### C6 — Its own decision (38)

Not closeable without a ruling. Each `blocked_by` names the ruling it needs.

| id | state | title |
|---|---|---|
| **R10** | `PARTIAL` | `useApi.ts:368` sends no `Authorization` header |
| **R3** | `NOT STARTED` | Full provider-listing delegation |
| **M5** | `NOT STARTED` | Foreground-turn `RunRecord` lifecycle |
| **M6** | `NOT STARTED` | `PolicyDecisionEnvelope` producer-less and consumer-less |
| **M7** | `NOT STARTED` | `change_tracker.py` not wired into evidence |
| **P3 · item 5** | `PARTIAL` | Structural independence (L1/L2) |
| **P3 · item 6** | `NOT STARTED` | The completion rule requires non-invalidated evidence |
| **P3 · item 7** | `NOT STARTED` | Wire `change_tracker.py` into evidence |
| **P4 · item 1** | `PARTIAL` | Reuse `wisp/graph/`'s store |
| **P8 · item 3** | `NOT STARTED` | Graph context section scoped to the current node |
| **P8 · item 4** | `NOT STARTED` | Populate plan context (`PlanState`, `## PLAN MODE ACTIVE`) |
| **P8 · item 6** | `NOT STARTED` | Token-based compaction |
| **P8 · item 7** | `PARTIAL` | Memory origin |
| **P9 · item 2** | `NOT STARTED` | Structured `child_goal` |
| **P9 · item 3** | `NOT STARTED` | Mandatory `result_schema` |
| **P9 · item 4** | `NOT STARTED` | Transactional effects |
| **P9 · item 5** | `NOT STARTED` | Typed failure replacing prose markers |
| **ADR-0053 R2** | `NOT STARTED` | A declared failure does not produce a replan |
| **ADR-0054 R4** | `NOT STARTED` | `stagnation_gate` is untouched |
| **ADR-0057 R2** | `NOT STARTED` | `resolve_approval` still ignores the client's `id` |
| **ADR-0057 R4** | `NOT STARTED` | A multi-client deployment asks every registered channel |
| **ADR-0058 R1** | `NOT STARTED` | The multi-device ceremony |
| **ADR-0059 R2** | `NOT STARTED` | REST's consult is conditional on a bundle |
| **ADR-0059 R3** | `NOT STARTED` | REST still reimplements L4 |
| **ADR-0059 R4** | `NOT STARTED` | Two bundle sources |
| **ADR-0061 R1** | `NOT STARTED` | The round-trip is pinned against a stub channel |
| **ADR-0061 R2** | `NOT STARTED` | `receive_message`'s `tool_approval` branch ignores `msg["id"]` |
| **PHASE_AUTHORIZATION_PARITY R1** | `NOT STARTED` | The approval authority is split three ways |
| **PHASE_AUTHORIZATION_PARITY R2** | `NOT STARTED` | Three action names are in none of the three approval sets |
| **PHASE_AUTHORIZATION_PARITY R4** | `NOT STARTED` | Five further gated routes are not in the parity table |
| **PHASE_M4_WIRING R2** | `NOT STARTED` | `acp_session.py:208` — an ACP-only deployment would need the same load |
| **PHASE_DAG_RETIREMENT R1** | `NOT STARTED` | `TaskDAG.validate()` mis-reports an unknown dependency as a cycle |
| **PHASE_LAYER_B_BOUNDARY R1** | `NOT STARTED` | `wisp/graph/api.py` has no importer |
| **PHASE_LAYER_B_BOUNDARY R2** | `NOT STARTED` | `test_the_orchestrator_still_imports_dag` is a bare string scan |
| **PHASE_EXTERNAL_INPUT_PATH R1** | `NOT STARTED` | `vscode-extension/` is not a "shipped client" |
| **PHASE_OBJECTIVE_FLAG_COMPOSITION R1** | `NOT STARTED` | `CriteriaDerivation.strict` records `True` when pre-empted |
| **PHASE_OBJECTIVE_FLAG_COMPOSITION R2** | `NOT STARTED` | Whether objectives *must* declare |
| **PHASE_GATE_ENABLEMENT R2** | `NOT STARTED` | The projection is proven over the guard's state space |

**Three of these are defects, not decisions** and are worth separating out:
`PHASE_DAG_RETIREMENT R1` (a real mis-report, pinned), `PHASE_LAYER_B_BOUNDARY R2` (an instrument
that asserts presence by string scan), and `ADR-0059 R3` (a re-implemented predicate). Each is
closeable by *fixing* it — mode 1 — and needs no ADR, only the work.

---

## §4 — Findings

Numbered `F-T*` to avoid colliding with the corpus's own `F1`–`F104`. Each is measured, with the
line that carries it.

### F-T1 — §(a)'s mapping table contradicts ADR-0062 R3, and four rows are miscarried because of it

`CURRENT_OPEN_ITEMS.md` §(a) maps the sources' words onto the ledger's six:

> | `Accepted (low)` · `Accepted` · `Unresolved, no action` | `NOT STARTED` | `CONTEXT.md` §12 |

But `CONTEXT.md` §12's rows for `R4` and `R5` — *"**Not debt** — different axis"* and *"Resolved as a
non-issue by the migration"* — are carried `COMPLETE`. **Those are the same kind of statement as
`Accepted`.** A disposition that resolves an item is a closure in one row and an open state in the
next, and the difference is not the disposition's meaning; it is which mapping row the transcriber
reached for.

ADR-0062 R3 already ruled on this exact defect class, for `DECIDED`: *"It names **why** the item is
finished (an ADR decided it) rather than **that** it is"* — repaired to `COMPLETE` + *Reason:*. The
same reasoning applies verbatim to `Accepted`, which names why an item is finished (the residual was
accepted) rather than that it is.

**Affected rows: `F3`, `F4`, `F5`, `G2`** (mapping row 4), plus **`M10`, `ADR-0053 R3`,
`ADR-0054 R2`, `ADR-0054 R3`, `ADR-0058 R2`, `PHASE_DAG_RETIREMENT R2`** (the *"by design"* /
*"stated cost"* dispositions, closed on `ADR-0058 R4`'s precedent).

**This is why C1 needs a ruling and not a transcription.** The mapping table is the authority the
register cites for its own states; overriding it is an editorial decision about the vocabulary —
which is precisely what ADR-0062 R2/R3 were needed for. Drafted in §6.

### F-T2 — `F4`'s tripwire cell says `—`, and a tripwire exists

The register's `F4` row carries `tripwire = "—"`. Measured:

`tests/test_unwired_controls_inventory.py:283` — `test_the_dead_spawn_variant_is_still_dead_but_its_guards_live`,
asserting `"async def spawn_with_guards(" in src` and `src.count("spawn_with_guards") == 1`, with the
failure message *"`spawn_with_guards` now has a caller — it is no longer dead code"*.

So `F4` is **not a deletion candidate** — a test pins it as deliberately dead, and deleting it would
break that test. `CONTEXT.md` §12 calls it *"Accepted — deletion candidate"*; the instrument disagrees,
and the instrument is the more recent artefact. `F4` is mode 4 (non-issue), already guarded, and its
tripwire citation is missing. `_check()` would accept the corrected citation — the path exists.

### F-T3 — `F3`'s owed annotation is present but weaker than the source names

`CONTEXT.md` §12 gives `F3` the residual action *"annotate so nobody wires them without the missing
checks."* Measured: `wisp/tools/registry.py:911-912` reads *"For advanced features (security_policy,
lsp_manager, file_lock), uses the module-level TOOL_IMPLS directly."*

The annotation exists but **describes rather than prohibits** — it does not say "do not wire these
without truncation and security checks", which is the sentence the source asked for. So `F3` is
**`PARTIAL`** (annotation landed, prohibition did not), not `COMPLETE`. Recording it as `COMPLETE`
would be the transcriber inventing a closure; the honest state is `PARTIAL` with the gap named.

### F-T4 — Two rows are the same item counted twice

`ADR-0061 R3` is `ADR-0057 R4`. Its own `blocked_by` concedes it: *"— (ADR-0057 residual 4,
unchanged)"*. `PHASE_M4_WIRING R5` is `ADR-0058 R2`, on identical title. The open count of 64
therefore over-counts by two.

### F-T5 — A cross-reference to a residual that does not exist

ADR-0058's residual list (`WISP_ARCHITECTURE_DECISIONS.md:5996-6007`, the `### Residuals` section)
has **four** numbered entries — 1 `:5998`, 2 `:6000`, 3 `:6002`, 4 `:6007`. (The ADR's other numbered
list, `### Non-violations` at `:5984`, is a separate list and is not counted here.) Yet:

- the register's `PHASE_M4_WIRING R5` row cites `blocked_by = "ADR-0058 R6"` — **there is no R6**;
  the item it describes is ADR-0058 **R2**;
- ADR-0058's own reversal condition (`:6012`) reads *"then **R5** is wrong"* — **there is no R5**;
  the residual it means is **R1** (the multi-device ceremony).

Both are dangling. `_check()` does not catch this class — it validates that the *source* file and
line exist, not that a residual *id* cited in prose exists.

### F-T6 — The register's generation date is hardcoded while its commit stamp is live

`scripts/derive_current_open_items.py:404` renders:

```
A(f"> Generated 2026-09-25 at `{_head_sha()}` · **{total} items** · ")
```

The sha is read live from git; the date is a string literal. Regenerating **today, 2026-09-27**,
produces *"Generated 2026-09-25 at `bb7fd29`"* — a page asserting a generation date that is not
its generation date. Today the two happen to be one commit apart and the claim is merely stale; the
mechanism allows it to be arbitrarily wrong, and the page's whole value is that its stamps are true.

### F-T7 — The register is faithful to its instrument

Measured: regenerating at `bb7fd29` reproduces the committed page byte-for-byte, except the stamp in
F-T6. No row has drifted from `ROWS`, no count is stale. **The 64/38 split is trustworthy as of
`f251ed4`** — the triage above is therefore triaging a sound page, not a drifted one.

### F-T8 — `CONTEXT.md` §13 lists one report twice and omits two it cites

Measured, in the document index:

- **`PHASE_EXTERNAL_INPUT_PATH.md` appears twice** — line 2455 (*"was missing from this index until
  the corpus-governance mission"*, the `F114` correction) and line 2471 (a later, more detailed row).
  The correction added a row that a subsequent landing duplicated.
- **`PHASE_M4_WIRING.md` and `PHASE_KEY_TRUST_WORKFLOW.md` are absent from §13 entirely**, although
  §12's `E` row cites both by name. `F114`'s class — a record that exists and is not listed — twice
  more, in the same index `F114` was raised against.

Both are repaired in the change that carries this report (ADR-0062 R7's rule, applied to itself).
`_check()` cannot see this class: it validates a *row's* source pin, not the completeness of a
hand-maintained index.

### F-T9 — the corpus's diagnosis of the absent dependencies is wrong

`CONTEXT.md` §6.1 concludes *"Closing either needs network access, not a code change"*, on the
evidence that *"Re-attempted offline 2026-09-25: both `httpx` and `cryptography` still fail"*.

**Measured, network was never the obstacle.** `curl` reaches `pypi.org` (HTTP 200) while pip fails
with `SSLCertVerificationError: unable to get local issuer certificate`. The venv's OpenSSL default
verify path is `/Library/Frameworks/Python.framework/Versions/3.12/etc/openssl/cert.pem`, which
**does not exist** — the python.org installer's `Install Certificates.command` was never run.
`certifi` is installed and unused.

The consequence is the opposite of the record: the obstacle is **a one-variable environment fix, not
an unavailable resource**, and all five dependencies install at their exact `uv.lock` pins once
`SSL_CERT_FILE` names `certifi`'s bundle. §6.1's "not quotable" column therefore stood on a
misdiagnosis — an offline *attempt* was read as an offline *host*.

---

## §5 — What would actually close them

Three batches. Only the first needs a decision; the second needs the host; the third needs work.

| batch | rows | action | closes |
|---|---|---|---|
| **1** | C1 (10) + C2 (2) | ratify §6, then edit `ROWS` + regenerate | **12** |
| **2a** | C3 (7) | install the five deps (pypi reachable), then measure | up to **7** |
| **2b** | C4 (3) | commit the WIP — `R9` is proven prose-only and risk-free | **3** |
| **2c** | C5 (4) | serve a second capable model, then enable and measure | **4** |
| **3** | C6 (38) | 35 need their own ADR; **3 are defects** closeable by repair | 3 now, 35 by decision |

**Batch 2a is the highest-leverage single action in the corpus.** One `pip install` unblocks four
rows outright and makes three more measurable.

---

## §6 — ADR-0065, as ratified

Written to `WISP_ARCHITECTURE_DECISIONS.md` in the change that carries this report — the body after
ADR-0064, and its row in the Decision index. The text below is the **ratified** decision. Two
corrections were made against the draft, and both are recorded here rather than silently: the draft's
*Consequences* said *"Ten rows"* while listing nine, and `R9` closes under **ADR-0060 R5** rather than
under this ADR. §8 records the execution.

> ### ADR-0065 — A recorded disposition is a closure, and §(a)'s mapping row for it is corrected
>
> **Status:** proposed.
>
> **Context.** §(a) of `CURRENT_OPEN_ITEMS.md` maps `Accepted (low)` · `Accepted` · `Unresolved, no
> action` onto `NOT STARTED`. The same page carries `R4` (*"Not debt"*) and `R5` (*"Resolved as a
> non-issue"*) as `COMPLETE`, and `ADR-0058 R4` as `COMPLETE` on the ground *"by design"*. The two
> dispositions are the same kind of statement — a residual acknowledged and resolved without a code
> change — and are recorded in two different states. ADR-0062 R3 removed the same ambiguity for
> `DECIDED`, ruling that a decision names *why* an item is finished and is therefore recorded as
> `COMPLETE` + *Reason:*.
>
> **Decision.** A disposition recorded at an item's source — *Accepted*, *Unresolved, no action*,
> *by design*, *a stated cost*, *compat is test-only* — is a closure. Such an item is recorded
> `COMPLETE`, followed by *Reason:* and the disposition, quoted from its source. §(a)'s mapping row
> is corrected accordingly.
>
> **Consequences.** Nine rows move to `COMPLETE` with a cited *Reason:*: `F4`, `F5`, `G2`, `M10`,
> `ADR-0053 R3`, `ADR-0054 R2`, `ADR-0054 R3`, `ADR-0058 R2`, `PHASE_DAG_RETIREMENT R2`. **`F3` is
> excluded** and recorded `PARTIAL`: its disposition names a residual *action* (an annotation), and
> that annotation is descriptive rather than the prohibition the source specifies (F-T3). An item
> whose disposition names work still to be done is not closed by the disposition.
>
> **Not decided here.** Whether *"deferred"* is also a disposition. It is not: it names a future, and
> a future is not a closure. The `P9` rows and `M7` stay open.
>
> **Reversal condition.** A row closed under this ADR that is later found to require work reopens as
> `IN PROGRESS` with the work named — and the reopening is recorded as a finding, so that
> "accepted" and "wrongly accepted" stay distinguishable.

---

## §7 — What this document did not do

*As first written.* §8 records what a later change did with it.

- **It closed nothing.** No `ROWS` entry was edited, no state word changed, no source amended.
- **It ratified nothing.** §6 was a draft; `WISP_ARCHITECTURE_DECISIONS.md` was untouched.
- **It edited no register.** `CURRENT_OPEN_ITEMS.md` was regenerated once to test idempotence
  (F-T7) and then **restored to its committed bytes**; `git status` showed it unmodified.
- **It did not touch the user's WIP.** `wisp/capability_filter.py`, the untracked tests, and the
  `wisp/core/graph/__init__.py` edit are as they were found. `R7`/`R8` need a commit decision
  from their author, not an architect's guess.
- **It claims no measurement it did not take.** Every line citation above was read; every "absent"
  and "present" was executed; the prose-only verdict came from both instruments, not inspection.

---

## §8 — What was executed

Two authorisations were given: ratify ADR-0065, and attempt the host batch. Both were carried out.

### Batch 1 — the vocabulary repair

- **ADR-0065 is ratified and written**: the body after ADR-0064, plus its row in the Decision index —
  the first ADR added since ADR-0064.
- **§(a)'s mapping row is corrected** in the generator (ADR-0065 R2), and a new paragraph states that
  **§12 is not rewritten**: the disposition stays as the record of what was decided, while the
  register records the state that follows. That pre-empts the `F98`-class confusion of reading §12's
  *"OPEN — by design"* beside the register's `COMPLETE`.
- **Thirteen `ROWS` entries changed, and `_check()` accepted every one** — the pins are unchanged and
  each *Reason:* is appended after the closing quotation mark, as the closed rows already do.
  - nine to `COMPLETE` under **R1** — `F4`, `F5`, `G2`, `M10`, `ADR-0053 R3`, `ADR-0054 R2`,
    `ADR-0054 R3`, `ADR-0058 R2`, `PHASE_DAG_RETIREMENT R2`
  - one to `PARTIAL` under **R3** — `F3`
  - two to `SUPERSEDED` under **R5** — `ADR-0061 R3`, `PHASE_M4_WIRING R5`
  - one to `COMPLETE` under **ADR-0060 R5** — `R9`
- **Register: 64 open → 52; `COMPLETE` 38 → 48; `SUPERSEDED` takes its first two members.** Verified
  by regeneration, not asserted: `NOT STARTED=44, IN PROGRESS=1, PARTIAL=7, COMPLETE=48, SUPERSEDED=2`.
- **`F4`'s tripwire cell corrected** from `—` to `tests/test_unwired_controls_inventory.py` (F-T2).
- **F-T6 fixed** — the generation date is read from the clock. It had to be: the regeneration this
  ADR requires would otherwise have stamped *"Generated 2026-09-25"* on a page generated 2026-09-27.
  The two sibling generators still carry the literal.
- **A stale count repaired** — §(c)'s *"Eleven of the open rows …"* paragraph named `R9` and
  `ADR-0058 R2`, both of which this change closed. It reads nine, and says why.
- **F-T8 repaired** — `PHASE_EXTERNAL_INPUT_PATH.md` de-duplicated in `CONTEXT.md` §13, and
  `PHASE_M4_WIRING.md` and `PHASE_KEY_TRUST_WORKFLOW.md` added to it.
- **`CONTEXT.md` §13** carries this report (ADR-0062 R7) and the ADR range advances to
  **ADR-0001 … ADR-0065**.

### Batch 2 — the host, where the corpus's diagnosis was wrong (F-T9)

`CONTEXT.md` §6.1 records the obstacle as *"Closing either needs network access, not a code change"*.
Measured, network was never the obstacle — `curl` reached `pypi.org` (HTTP 200) while pip failed with
`SSLCertVerificationError`. With `SSL_CERT_FILE` pointed at `certifi`'s bundle, **all five installed
at their exact `uv.lock` pins**: `httpx 0.28.1`, `cryptography 50.0.1`, `numpy 2.4.6`,
`tiktoken 0.14.0`, `aiohttp 3.14.3`. pip then reported a **sixth** absent dependency never recorded
in §6.1 — `wcwidth>=0.2.5`, wisp's own declared requirement — now installed (`0.9.1`).

**What this does and does not unblock.** C3's seven rows are now *measurable*, not closed: closing
them means running the suites and quoting the counts, which is its own landing. `C4` is untouched —
`R7` and `R8` carry behaviour, and their author's commit decision is not this architect's to make.
`C5` still needs a second capable model.

### Nothing else moved

No `wisp/` production change; no flag, gate or authority touched. `CURRENT_FINDINGS.md` and
`CURRENT_FLAGS.md` were regenerated only as a safety check and **reverted** — their diffs were the
commit stamp alone.

---

## §9 — The three defect rows, repaired

§3.6 named three of the 38 as **defects rather than decisions** — closeable by repair, no ADR needed.
All three are repaired and closed: **52 open → 49**.

### R1 — `TaskDAG.validate()`'s unknown-dep-as-cycle mis-report

The Kahn in-degree counted *unknown* dependencies, so their dependents were stranded above degree 0
and reported as part of a cycle on top of the correct *unknown* error. The in-degree now counts only
edges whose source exists.

**The verdict is unchanged** — an unknown dependency is still rejected; only the false second message
is gone. Driven, both directions:

| | `unknown_dep` |
|---|---|
| old formula | `['Node 'a' depends on unknown 'nope'`, `Cycle detected involving: a']` |
| new code | `["Node 'a' depends on unknown 'nope'"]` |

And the case that keeps the filter honest: a graph with **both** an unknown dep and a real cycle
still reports both, so the filter is not too broad. The file's `DEFECT-PIN` became a `FIXED-PIN`, as
that pin's own docstring instructed — *"the defect is fixed; update this DEFECT-PIN"*.

### R2 — the two bare string-scan tripwires

`test_the_orchestrator_still_imports_dag` and `test_the_orchestrate_dag_tool_still_imports_dag`
asserted exact source lines — passing on a commented-out import, failing on an equivalent one
reordered or wrapped. Both now parse the AST, following the pattern the third test in the same class
already used and documented.

Non-vacuity driven: the helper returns `{'DAGScheduler'}` for the real module and **empty** for a
wrong suffix, a wrong name, and a wrong file. `tests/reliability/test_dag_retirement_contract.py`:
**12 passed**.

### R3 — REST's re-implementation of L4

The *scan* — not the predicate, which was already canonical — was written out twice, identically, in
`wisp/auth/decision.py`'s L4 and `wisp/server/deps.py`'s protected-path guard. It is now one
function, `wisp.pathsec.touches_protected_path`, called by both; each site keeps its own risk guard
and its own refusal message. The predicate and the key set were already single-sourced, so what could
still drift was the scan itself — and a second copy of a guard is how this authority's divergence
started in the first place.

**86 guard and parity tests pass**, including `test_rest_authorization_composition.py`'s byte-for-byte
differential against HEAD's gate, and `test_protected_path_guard.py`'s four authority invariants. The
one remaining copy of the scan is inside that differential test — a deliberate baseline of the *old*
gate, not a live guard.

### A correction to §3

I classified these three as *"closeable by repair"*. That was right for R1 and R2, and only **half
right for R3**: its source read *"kept because its message is pinned"*, which is the shape of a
**disposition** — and a disposition closes under ADR-0065 R1 with no repair at all. I repaired it
instead, which is strictly better (it removes a duplicated authority rather than accepting it) but is
a larger change than the row required. Recorded here rather than left implicit, because the
classification and the action disagree and a reader should know that.

### Sources amended, in the same change

A closure is recorded where the item lives, so `PHASE_DAG_RETIREMENT.md` §3 and §6,
`PHASE_LAYER_B_BOUNDARY.md`'s residual bullet, and ADR-0059's residual 3 each now state the repair.
Each register source cell was **re-pinned to the amended text in the same change** — which
`_check()`'s verbatim-quote rule enforces rather than trusts. Where an amended line moved, the pin
moved with it (`PHASE_DAG_RETIREMENT.md` `:124` → `:125`).

---

## §10 — The dependency cluster, closed by measuring

**F-T9** found that §6.1's "not quotable" column stood on a misdiagnosis. Installing the dependencies
made the seven C3 rows *measurable* — and the work they were waiting for **is** the measurement. So
here it is. **44 open, from 49.**

### The M4 policy suite is green

Six files, re-run against the recorded baseline:

| | recorded (`cryptography` absent) | measured now |
|---|---|---|
| `test_policy_bundle` · `test_policy_modes` · `test_policy_cli` · `test_policy_routes` · `test_policy_precedence` · `test_policy_enforcement` | **14 failed, 6 errors, 18 passed** | **43 passed, 0 failed, 0 errors** |

The total rises from 38 to 43 because five tests previously **errored at collection**; they now
collect and pass. Closes **`PHASE_M4_WIRING R3`**.

### The key-trust happy path is exercised

`tests/test_policy_bundle.py::test_sign_verify_round_trip` drives a real Ed25519 keypair —
`generate_keypair` → `sign_bundle` → `verify_bundle(...) is True` — with the tamper and wrong-key cases
rejected alongside it. `PHASE_KEY_TRUST_WORKFLOW R6` said the happy path was *"not exercised by any
test in this environment"*; it is now. **Closed.**

### Both guards the corpus called un-runnable now run

`tests/test_protected_path_guard.py` and `tests/test_server_policy_gate.py` both pass, so their counts
are quotable. Closes **`PHASE_OUTCOME_CLASSIFICATION_VIOLATION R2`**.

Also closed: **`R6`** (nothing is absent — all five at their `uv.lock` pins, plus a sixth the corpus
never recorded, `wcwidth`, wisp's own declared requirement) and **`ADR-0061 R4`** (the bundle half is
now measurable).

### Two rows did *not* close, deliberately

- **`P8 · item 5`** — `tiktoken` was the obstacle and it is gone, but the *item* is *"serve the
  symbol-level repo map"*, which is not implemented. **Removing a blocker is not doing the work.** Its
  `blocked_by` now says so instead of blaming a dependency.
- **`ADR-0059 R1`** — stays `PARTIAL`. Its bundle half became measurable, so the obstacle was
  **re-stated rather than the row closed**. Closing it on "it became measurable" would be closing an
  item on the removal of an excuse. The measurement is owed, and named.

### Sources amended, and a derived page corrected with them

Each closure is recorded at its source and re-pinned in the same change: `CONTEXT.md` §6.1's
misdiagnosis paragraph and §12's `R6` row, `PHASE_M4_WIRING.md:188`, `PHASE_KEY_TRUST_WORKFLOW.md:235`,
`PHASE_OUTCOME_CLASSIFICATION_VIOLATION.md:240` (pin moved from `:241`), and ADR-0061's residual 4.

The register's §(c) host-count paragraph was revised too. It **names rows**, so closing rows falsifies
it — it read *"nine … `R6` … `ADR-0061 R4`"* and now reads seven. A derived page that states a stale
list is worse than one that states none, and this is the second time in this change that a closure
invalidated prose the generator emits verbatim.

---

## §11 — Ten more closed, and the 27 that remain

Ten rows closed in this pass: **37 → 27**. Nine on existing decisions or fresh measurement, and `F3`
on the annotation it owed.

### The nine

| row | the ground |
|---|---|
| `P3 · item 5` | **re-scope** — L3 is *preferred, not required* by the plan, so its absence is not owed |
| `P4 · item 1` | **disposition** — the store is *deliberately not* shared; a second DB would fragment the durable record (ADR-0019) |
| `P8 · item 4` | **ADR-0063 R2** answers *"populate or remove"* — the slot is `OPERATOR`-tagged, so it is **remove** |
| `ADR-0054 R4` | **disposition** — both gates wired and both default OFF *independently*, which is what ADR-0037 requires |
| `ADR-0058 R1` | the M4 spec **deferred** the ceremony, so it is not owed |
| `ADR-0059 R4` | **disposition** — *named, not merged* |
| `PHASE_LAYER_B_BOUNDARY R1` | **measured correction** — the premise was false (below) |
| `PHASE_EXTERNAL_INPUT_PATH R1` | **answered** — it *is* a shipped client, by ADR-0061's own text |
| `PHASE_GATE_ENABLEMENT R2` | **a reversal condition, not an item** — the guard test re-derives it |
| `F3` | its owed annotation **landed**: `registry.py:911` now *prohibits* rather than describes. Prose-only, both instruments |

### The measured correction — `PHASE_LAYER_B_BOUNDARY R1`

The residual read *"`wisp/graph/api.py` has no importer — Layer B's typed SDK is reachable from
nothing"*. Measured: **six import sites in four files**, including `wisp/__init__.py:48`
(`from wisp.graph.api import GraphHandle, run_graph`). It is the **package's public surface**. What is
true is narrower and is what the source now says: `GraphHandle` has **no consumer** — re-exported and
never called. **Public-and-unconsumed, not dead**, and the premise is recorded as wrong rather than
quietly dropped.

### The environment fact, re-measured and unchanged

`scripts/acceptance_gate_population.py` — the committed instrument — reports **1 capable of 13**
(5 retired, 5 paywalled, 2 degenerate, 1 capable). So `M1`, `ADR-0053 R1`, `ADR-0054 R1`,
`PHASE_GATE_ENABLEMENT R1` and `P3 · item 6` are open **on an environment fact**, not on work.
Closing them would mean reporting a population this host does not serve.

### Why the other 27 are not closed

| group | rows | why |
|---|---|---|
| **the user's own WIP** | `R7`, `R8` | `CONTEXT.md` §8 says *"do NOT commit or delete"* — the resolution is the author's. Measured cause of `R8`: its three files import `wisp.multi_agent.delegation`, **removed by `11fc949`** (`refactor(delegation): remove prompt-interception auto-delegation`), so they test a module that deliberately no longer exists |
| **no second capable model** | `M1`, `ADR-0053 R1`, `ADR-0054 R1`, `PHASE_GATE_ENABLEMENT R1`, `P3 · item 6` | re-measured above |
| **no live client on this host** | `ADR-0061 R1` | its own recorded obstacle |
| **a measurement owed** | `ADR-0059 R1`, `P8 · item 5`, `P8 · item 6` | each has an instrument or a budget to run — not a decision to take |
| **a decision owed** | `R10`, `R3`, `M5`, `M6`, `ADR-0053 R2`, `ADR-0059 R2`, `PHASE_M4_WIRING R2`, `PHASE_OBJECTIVE_FLAG_COMPOSITION R1`, `R2` | each needs its own ruling |
| **blocked on `M1`** | `M7`, `P3 · item 7` | deferred *with* stage 3b, and stage 3b is the population |
| **one programme** | `P9 · item 2`, `3`, `4`, `5` | the structured-delegation contract; whether it is owed is the decision |

**None of the 27 was closed, and that is the result, not a shortfall.** Each needs a decision, a
measurement, the author's own action, or an environment this host does not have. Closing them would
have meant writing twenty-seven closure citations for closures that had not happened — which is what
`_check()`'s verbatim-quote rule exists to make fail rather than pass.

### Two of the 27 are cheap, and both are measured

- **`M6`** — `PolicyDecisionEnvelope` has **no production producer and no consumer**: it is defined in
  `wisp/contracts/policy.py`, exported from `wisp/contracts/__init__.py`, and referenced only by
  `tests/test_contracts_policy.py`. It is either wired or deleted, and both are small. It is **not** a
  disposition — the corpus calls it *"the last unwired contract"*, which is an unfinished wiring.
- **`P8 · item 5`** — its blocker is gone and `tiktoken` works (`cl100k_base` verified, 1000 chars →
  125 tokens). The plan asks only to **measure first**, so the next step is the measurement, not a
  decision.
