# PHASE POST-M13 — AUTHORITY RECONNAISSANCE

**Phase type:** forensic reconnaissance. **No production code was modified.**
**Date:** 2026-09-24
**Method:** CLAIM → TRACE → REPRODUCE → IDENTIFY AUTHORITY GAP → BOUND THE FIX

---

## 0. Method and integrity notes

Two evidence-integrity traps were hit during this recon and are recorded because they changed
intermediate conclusions. Both are instances of a known repo hazard.

### 0.1 `grep` with `\|` alternation returns **false negatives** here

`LC_ALL=C grep -n "13j1\|fanout" <file>` printed nothing and exited 1. Re-run as
`LC_ALL=C grep -n -e "13j1" -e "fanout" <file>`, the same file yielded **18 matching lines**.
An early conclusion — *"the fanout suite is green, so M8's stated blocker is stale"* — was **wrong**
and was retracted. `-e` / `-E` flags are the reliable form in this environment.
(The same trap produced an early false *"`multi_agent/dag.py` is unimported"* reading.)

### 0.2 The Grep tool skips dot-directories

A search for a placeholder in `.workbuddy-ai/` returns nothing via the Grep tool (ripgrep ignores
dot-dirs by default). Memory-log searches must use the shell.

**Consequence for this document:** every negative claim below ("X is never called") was re-verified
with the Grep tool *and*, where the target was under a dot-directory, with the shell. Negative
claims are the fragile kind; treat them as "no caller found by two methods" rather than "provably none".

---

## 1. Executive verdict

```
CURRENT MIGRATION STATE:
The Persistent Graph Loop migration (P0-P9) is fully traversed. All five M-items that M9 recorded
as blocked are complete (M11, M12, M13, M14, M15), as are the durability items (M2, M3, M4, M16).
HEAD = 7c15626 (docs(m11)). M13 is implemented, verified and documented but NOT committed.

M13 STATUS:
COMPLETE and intact. All six invariants verified in source (section 3). Zero regression.

NEXT CANDIDATE:
The **verdict-consumer edge of the live turn loop**. Four independently-built, independently-tested
authority mechanisms (P3 CompletionVerdict, P6 RecoveryLadder, P7 route_to_recovery/may_report_goal_met,
M12 classify_failure_signal) each have NO production consumer. They dead-end at one place.

DECISION:
INVESTIGATE
```

The decisive fact of this recon is **convergence**, not any single unwired module. The migration has
been closing mechanisms one at a time; the remaining work is not another mechanism. It is **one
missing consumer** that four mechanisms are already waiting on.

---

## 2. Baseline captured before any change

Per §10 of the brief. No `git stash`, no `git reset --hard`, no `git checkout --` was used.

```
$ git log -1 --oneline
7c15626 docs(m11): record the M11 phase; findings F29-F31; repair the commit table

$ git status --short | wc -l
79 entries (39 tracked-modified + 40 untracked)
```

Snapshotted to `.workbuddy-ai/memory/post-m13/` (agent workspace; untracked by design):

| File | Content |
|---|---|
| `status-before.txt` | `git status --short` |
| `head.txt` | `git log -1 --oneline` |
| `log20.txt` | `git log --oneline --decorate -20` |
| `diffstat-before.txt` | `git diff --stat` |
| `diff-before.patch` | full `git diff` (131,995 bytes) |

The working tree carries substantial pre-existing WIP (Phase 10 / Phase 13 docs, `wisp-desktop/`,
`wisp/server/routes/`, `wisp/pathsec.py`, `wisp/provider_catalog.py`, `tests/conftest.py`, …).
**This recon did not touch any of it.** The only files written by this phase are this document and
the recon artifacts under `.workbuddy-ai/memory/post-m13/`.

---

## 3. M13 invariant verification (brief §2)

All six required invariants were verified **in source**, not merely in the report.

| # | Invariant | Status | Evidence |
|---|---|---|---|
| 1 | Empty `ProgressSignal` ⇒ `UNKNOWN`, never `STAGNATING` | **INTACT** | `is_empty` at `stagnation.py:79-91` excludes `work_units`; `observe()` returns `UNKNOWN` for it **without appending, without feeding the trap, without counting flat** (`stagnation.py:235-241`) |
| 2 | Stagnation identity is `action_key(tool,args)`, NOT `tool_call_id` | **INTACT** | `work_units: frozenset[str]` (`stagnation.py:76`); the field docstring states *"NOT `TaskNode.work_unit` … a repeated call gets a **fresh** one"* (`stagnation.py:72-75`) |
| 3 | A refused call still carries the canonical identity via `_refusal_result_event` | **INTACT** | helper at `stateless.py:2273`; all 10 refusal sites route through it (`stateless.py:466,492,511,530,547,576,597,621,640,656`) |
| 4 | Detector constructed **per turn** | **INTACT** | constructed inside `run_turn`'s per-turn block (`runtime.py:680-707`); the comment states per-turn-not-per-session and why |
| 5 | `STAGNATION` is journal-only, written only when the verdict is reached | **INTACT** | kind at `session.py:51`; record ctor `session.py:242`; `apply` case `session.py:532` |
| 6 | Enforcement stays DEFERRED | **INTACT — tripwired** | `test_the_ladder_is_not_consulted_yet` (AST on `.decide`, `test_stagnation_live_wiring.py:399-413`); `test_goal_met_is_not_yet_gated_on_the_live_path` (AST on `may_report_goal_met`, `:416-428`) |

**M13 has not regressed.** The detector records; it does not act. Both enforcement paths remain
AST-pinned shut, exactly as the brief requires.

---

## 4. Current-state matrix (brief §3)

Traced into source and tests. `LIVE?` means *consumed by a production entry point*, not *importable*.

| Phase | Mechanism | Live? | Tests | Remaining work | Dependency |
|---|---|---|---|---|---|
| **M1** | P3 acceptance gate (`core/acceptance.py`) | **recorded only** | `test_acceptance_verdict.py`, `test_verdict_layer_recorded.py` | stage 3b: gate completion | **F8 tool path + a measurement** |
| **M5** | foreground `RunRecord` lifecycle (`runs/`) | **background only** | `test_durable_layer_reachable.py`, `test_runs_*.py` | foreground per-turn record | consumer unclear |
| **M6** | `PolicyDecisionEnvelope` (`contracts/policy.py`) | **NO** | `test_contracts_policy.py` | producer + consumer | product decision |
| **M7** | `change_tracker.py` evidence | **NO — never fed** | `test_change_tracker.py` (unit only) | — (premise unsound) | duplicate change records |
| **M8** | retire `multi_agent/dag.py` | **YES — live** | fanout suites | retirement | **18 fanout failures** |
| **M10** | materialized graph lower bound | by design | — | none (documented limit) | — |
| **M12** | failure taxonomy | **partial** | `test_failure_signal_classification.py` | adapter has no consumer | ladder enforcement |
| **M13** | stagnation | **recorded only** | `test_stagnation_live_wiring.py` (27) | enforcement deferred | **verdict consumer** |
| **M14** | prompt section classification | **YES** | `test_prompt_section_trust.py` | — | — |
| **M15** | child principal | **YES** | `test_child_principal_wired.py` | — | — |
| **M16** | escalation durability | **YES** | `test_escalation_durability.py` | — | — |

M14/M15/M16 verified live by their consumers:
`SECTION_TRUST` (`context_assembler.py:62,90,95,113`) · `child_principal` (`auth/principal.py:70` →
`multi_agent/_runner.py:496,669,806`) · `_journal_turn_events` (`runtime.py:1160` → called at
`runtime.py:842,1116`).

---

## 5. Authority map and the missing edge (brief §4, §6)

### 5.1 The four dead verdicts

Each of these has a production implementation, tests, and an architecture decision — and **no
production consumer**.

**P3 — the acceptance verdict**
```
core/acceptance.py::evaluate
  → floor_guard_verdict()            core/verification.py:293-301
  → recorded as an audit VERDICT     core/runtime.py:1047-1049
  → ??? nothing reads it
```
`acceptance.py:20` states it directly: *"`CompletionVerdict` therefore computes and exposes a verdict
that nothing consumes yet — deliberately."* `route_for()` (`acceptance.py:64`) appears only in
`acceptance.py` and its own test. ADR-0016 (`WISP_ARCHITECTURE_DECISIONS.md:448-481`) records the
staging and the reversal condition: *"3b is a separate decision, taken on the measurement."*

**P6 — the recovery ladder**
```
RecoveryLadder.decide()              core/recovery.py:512
  → instantiated ONLY in tests       test_recovery_ladder.py, test_failure_signal_classification.py:298,306,311
  → ??? no production instantiation
```

**P7 — stagnation routing and the completion guard**
```
route_to_recovery()                  core/stagnation.py:331
  → called ONLY in tests             test_stagnation_detection.py:196,207,224,236,243,251
may_report_goal_met()                core/stagnation.py:294
  → no production caller; AST-tripwired shut
```

**M12 — the failure taxonomy adapter**
```
classify_failure_signal()            core/recovery.py:181
  → referenced by: its definition, its own test (test_failure_signal_classification.py:86-87), prose docs
  → ??? no production caller
```
Consequently `CODE_FAILURE_CLASS` (`recovery.py:165`) is reachable **only** through that uncalled
adapter. By contrast the F28 fix *is* live: `_ENGINE_DENIAL_PREFIXES` (`core/events.py:352`) is
consumed at `core/events.py:419`, and `TRANSIENT_MARKERS` (`recovery.py:151`) is aliased and used at
`subagent_orchestrator.py:938-952`. So M12 is **half-wired**: the denial predicate is live, the
taxonomy bridge is not.

### 5.2 The single missing edge

```
  P3 CompletionVerdict ─┐
  P6 RecoveryLadder ────┤
  P7 route_to_recovery ─┼──▶  all tested, all reachable from their packages
  P7 goal-met guard ────┤              │
  M12 taxonomy ─────────┘              ▼
                            ╔═══════════════════════════════════╗
                            ║ MISSING EDGE: the live turn loop   ║
                            ║ consumes NO verdict at all          ║
                            ╚═══════════════════════════════════╝
                                          │
                                          ▼
                       completion is decided by terminal evidence alone
                       (`turn_succeeded`, 13-H5) — ADR-0016
```

The completion decision **exists and is live**. What is missing is that none of the four verdict
mechanisms feeds it. M13 is the fourth instance of this pattern; it is not a new class of problem.

**This is the answer to the brief's core question.** The next bounded target is not a mechanism —
it is the one consumer those mechanisms are waiting on.

---

## 6. Candidate detail

### M1 — acceptance gate / P3 stage 3b

**Verdict: `DEFER`** (blocked on an external dependency, then on a measurement).

The brief warned: *"the current repository dependency set appears to contain `jsonschema`. Do NOT
assume this means the blocker is gone."* It is not gone. The manifest contains it; the environment
does not.

```
$ python -c "import jsonschema"
ModuleNotFoundError: No module named 'jsonschema'
```

`jsonschema` is declared at `pyproject.toml:22` and used in exactly one place:
`stateless.py:2215-2216` (and the `write_file` salvage retry at `:2221-2223`).

**Reproduced (RED-first probe, no file written).** A valid argument dict against a valid schema:

```
jsonschema import FAILED: ModuleNotFoundError No module named 'jsonschema'
RESULT: "Schema validation failed for tool 'read_file': No module named 'jsonschema'"
        -> caller treats this as a hard denial (F8)
```

The mechanism is `stateless.py:2214-2227`: the bare `except Exception` catches the
`ModuleNotFoundError` and returns it as a schema-validation failure string, which the caller treats as
a hard `SCHEMA_INVALID` denial. **A missing dependency becomes a total tool outage, with the failure
misdirected at the tool.** F8 stands, unchanged.

Because no tool can execute, the P3 `INCONCLUSIVE` rate cannot be measured on real traffic, and
ADR-0016 makes stage 3b a decision *taken on that measurement*. The chain is
`3b ← measurement ← working tool path ← jsonschema ← no network`.

**Answering the brief's four M1 questions:** dependency installed? **no.** importable? **no.**
runtime path usable? **no.** schema validation reachable? **no.** acceptance mechanism correct?
**yes — and therefore doubly blocked**, because the mechanism is sound and unmeasurable.

### M5 — foreground `RunRecord` lifecycle

**Verdict: `INVESTIGATE`** (the mechanism is real; the consumer is not).

The durable run layer is wired for **background** runs and nothing else:

| Consumer | Evidence |
|---|---|
| `CompositionRoot` creates and injects the store | `composition.py:83,150,212` |
| `ToolExecutor` receives and forwards it | `tool_executor.py:430,481,1774` |
| `TaskManager` | `task/manager.py:20-21` |
| `BackgroundAgentManager` create/transition/lease/recover | `background.py:88-233`, `recover()` at `:213` |

There is **no `run_store` reference anywhere in `core/runtime.py` or `core/stateless.py`** — the
foreground turn path never touches it. The reachability test's own docstring scopes the claim to
background runs: *"`CompositionRoot` constructs `BackgroundAgentManager` WITH a run store, so a launch
writes `background_runs` rows"* (`test_durable_layer_reachable.py:19-20`).

Two notes:
- `wisp/config.py:272` documents the flag as *"persist a durable RunRecord per turn"*; per-turn
  persistence is not implemented for foreground turns. The flag is read (`config.py:897-898`,
  `composition.py:247`) but its documented promise is partly unmet.
- **The plan names a test that does not exist.** `WISP_MIGRATION_PLAN.md:90` lists
  `test_durable_run_layer_reachable.py`; the actual file is `test_durable_layer_reachable.py`.

**Why not IMPLEMENT:** the missing edge is the *consumer*. A foreground `RunRecord` would be a second
durable record of a turn whose transcript the session journal already durably records (P0/P1) — a
candidate instance of the repo's own rule, *"two producers of one structure is a defect, even when
they agree today"*. The contract must be stated before the code exists.

### M6 — `PolicyDecisionEnvelope`

**Verdict: `DEFER`** (no producer/consumer contract; requires a product decision).

The module's own docstring (`contracts/policy.py:1-3`) is decisive:

> *"Adds no authority: serializes the two existing decision types (`core/contracts.ApprovalDecision`,
> `infra/policy_engine.PolicyDecision`) into one wire form."*

Referenced by exactly three things: its definition (`contracts/policy.py:12`), its re-export
(`contracts/__init__.py:11,24`), and its own test (`tests/test_contracts_policy.py`). F16
(`WISP_MIGRATION_STATUS.md:1635`) recorded this and it is still true.

The brief says: *"Do not wire it merely because it exists. If it has no clear producer/consumer
contract, classify it as deferred."* It has none. Note also that its `principal_id` / `correlation_id`
are marked *"reserved, supplier TBD Phase 1"* (`contracts/policy.py:19-20`); the ADR says the supplier
*"is now named"* (`docs/adr/2026-09-04-local-identity.md:33-35`), so the identity half is answerable —
but the **authority** half is not: F6 records two disjoint decision models
(`auth/decision.authorize()`, 6 layers; `infra/security.SecurityPolicy.check()`, 4 layers). Choosing
which one the wire form represents is a product decision, not a wiring task.

### M7 — `change_tracker.py` into evidence

**Verdict: `DEFER`** — and the brief's premise does not survive contact with the repository.

The brief asks whether change evidence can be connected to turn/verification/journal/completion
"without creating a second evidence authority". It cannot, because there is currently **no change
evidence at all**, and there are already **three** change records.

1. **`ChangeTracker` is never fed.** `filesystem.py:138-140` does
   `tracker = _change_tracker_ctx.get(); if tracker: tracker.record_write(...)`. The context var is set
   only by `set_collaboration_tools()` (`tools/_utils.py:29`) — and that function **is never called**
   anywhere in the repository (only defined and re-exported at `tools/__init__.py:22`). So `tracker`
   is always `None`, and `record_write` never runs in the live path.

2. **Its only reader is broken.** `ChangeTracker.__init__` starts with an empty in-memory list
   (`change_tracker.py:46`) and there is no load or persistence path (only `to_json`). The single
   consumer, `cmd_changes` (`__main__.py:779-785`), constructs a **fresh** instance and prints
   `ct.summary()` — which, with no persistence, is **always `"No changes made."`**. This is a live
   defect of the same family as F32/F33: the structure cannot report its own state.

3. **Two other change records already exist.** `workspace.ChangeSet`/`Change` is a richer vocabulary
   (`c.path`, `c.op`, `c.rename_from`, artifacts; `workspace.py:171-193,296-299,457,514-553`), and
   `runtime._touched_files` is a session-scoped, bounded set (`runtime.py:474,757,1296-1308,1330`;
   consumed by `core/context/compactor.py:38,61,82`). A fourth — `EditRecord`
   (`runs/compensation.py:10`) — exists for compensation.

**Wiring `change_tracker` into evidence would create a fourth change record on top of a structure that
is never populated and whose reader is broken.** The correct next step is a *decision about which
change record is authoritative* — an architecture decision, not an implementation.

### M8 — retire `multi_agent/dag.py`

**Verdict: `DEFER`** (blocker verified, not assumed).

The brief requires establishing liveness first. **`dag.py` is live** — do not delete it:

| Consumer | Evidence |
|---|---|
| `tools/orchestration.py` imports `TaskDAG, TaskNode` | `tools/orchestration.py:166` |
| and calls `orch.run_dag(dag, ...)` | `tools/orchestration.py:214` |
| `DAGScheduler` | `multi_agent/subagent_orchestrator.py:1307,1353` |
| tool route `orchestrate_dag` | `tool_executor.py:1935-1936` |
| risk classification | `core/contracts.py:304` |

And the blocker is **real and quantified**. Fanout-family failures in the stable baseline, and
unchanged in this phase's fresh run:

| Test file | Baseline failures | This run |
|---|---|---|
| `tests/reliability/test_13j1_fanout_contract_repair.py` | 13 | 13 |
| `tests/reliability/test_13j_fanout_contract.py` | 5 | 5 |
| `test_fanout_resilience` / `test_graph_optimizer_fanout` / `test_spawn_fanout` | 0 | 0 |

**18 red**, so a retirement regression would be indistinguishable from the existing noise. CONTEXT.md
attributes this to *"environmental reasons"*; **redness is verified, the cause is not** — note that
`test_13j1_fanout_contract_repair.py` imports no `httpx` and no `TestClient`, so the usual
environmental cause does not apply to it. Determining the cause is prerequisite work.

### M10 — materialized graph lower bound

**Verdict: `DEFER` — by design, not a task.** Documented only at `CONTEXT.md:107,929` (there is no
ledger entry): one node per closed tool exchange plus one terminal; iteration boundaries are not
observable. This is a stated property of the design, not unfinished work.

---

## 7. Dependency graph

```
NEXT CANDIDATE: the verdict consumer in the live turn loop
  │
  ├─ required mechanism  ── P3 CompletionVerdict      EXISTS  core/acceptance.py:179
  │                          P6 RecoveryLadder        EXISTS  core/recovery.py:512
  │                          P7 StagnationDetector    EXISTS  core/stagnation.py:191  (LIVE, recording)
  │                          M12 FailureClass         EXISTS  core/recovery.py:46
  │
  ├─ required producer   ── all four produce today, and are tested
  │                          P3 → recorded at runtime.py:1047-1049
  │                          P7 → recorded as STAGNATION (session.py:51)
  │
  ├─ required storage    ── the session journal (journal-only records, ADR-0028 shape)
  │                          EXISTS and replays
  │
  └─ required consumer   ── ✗ MISSING
                             the turn loop reads no verdict; completion is derived
                             from terminal evidence alone (turn_succeeded, 13-H5)

AUTHORITY BOUNDARY: the completion decision in AgentRuntime.run_turn
  └─ blocked secondarily by: F8 (no working tool path → P3 stage 3b unmeasurable)
```

---

## 8. Rejected candidates

| Candidate | Why not selected |
|---|---|
| **M1 (acceptance gate 3b)** | Dependency genuinely external and uninstallable (no network). Reproduced: a *valid* args dict is refused because `jsonschema` is missing. ADR-0016 additionally makes 3b a decision *on a measurement* that this environment cannot produce. **DEFER.** |
| **M5 (foreground `RunRecord`)** | Mechanism exists and is live for background runs, but the **consumer is unclear**; a foreground record risks duplicating the session journal (discipline #9). Needs the contract stated first. **INVESTIGATE.** |
| **M6 (`PolicyDecisionEnvelope`)** | Producer-less **and** consumer-less by its own docstring ("Adds no authority"). Two disjoint decision models (F6) make "what authority it represents" a product decision. **DEFER.** |
| **M7 (`change_tracker` evidence)** | Premise unsound: the tracker is **never fed** (`set_collaboration_tools` has no caller) and its only reader reads an empty fresh instance. Three change records already exist. Would create a fourth. **DEFER.** |
| **M8 (retire `dag.py`)** | `dag.py` is **live** on the `orchestrate_dag` path; 18 fanout failures at baseline make a regression indistinguishable. **DEFER.** |
| **M10 (graph lower bound)** | By design; a documented property, not work. **DEFER.** |
| **M13 enforcement** | Explicitly out of scope for this phase, and it is one *instance* of the selected candidate rather than a competitor. |
| **F8 repair (make the tool path degrade gracefully)** | Tempting — it would unblock M1 and every tool-dependent test. But it changes validation semantics (fail-open when a validator is absent), which is an authority question, and the brief forbids changing authority semantics in a recon phase. Recorded as the **highest-value unblocking work**, not selected. |

---

## 9. Test baseline (brief §11)

Re-run against the **current** tree — the 714 / 129 figures were not assumed.

```
$ env -u PYTHONPATH .venv/bin/python -m pytest tests/ -q -p no:cacheprovider \
    --no-header --continue-on-collection-errors --tb=no \
  | grep -E "^(FAILED|ERROR)" | LC_ALL=C sort > .workbuddy-ai/memory/post-m13/fullrun1.txt
FULLRUN1 lines: 129
```

Set comparison against `.workbuddy-ai/memory/baseline-failures-stable.txt` (independent Python set
diff, not `comm` — F31):

```
baseline=129  current=129
NEW failures  = 0
FIXED (gone)  = 0
set-identical: True
errors: 17  failed: 112
```

| Suite | Command | Result |
|---|---|---|
| Full suite | `pytest tests/` | **129, set-identical, 0 new, 0 fixed** |
| Migration suites (24 files) | the §11 list + `test_node_identity.py`, `test_stagnation_live_wiring.py` | **714 passed** in 49.33s |
| M11–M16 (7 files) | `test_node_identity`, `test_failure_signal_classification`, `test_stagnation_detection`, `test_stagnation_live_wiring`, `test_prompt_section_trust`, `test_child_principal_wired`, `test_escalation_durability` | **222 passed** in 4.09s |

**The migration's stable result holds on the current tree.** The 714/129 figures are re-confirmed, not
quoted.

---

## 10. Defects and documentation errors found during this recon

Recorded but **not fixed** (recon phase).

| # | Finding | Evidence |
|---|---|---|
| **R1** | **`cmd_changes` can never report a change.** It constructs a fresh `ChangeTracker` and prints `summary()`; `ChangeTracker` has no persistence, so the output is always `"No changes made."` | `__main__.py:779-785`; `change_tracker.py:43-47` |
| **R2** | **`ChangeTracker` is never fed.** `set_collaboration_tools()` has no caller, so `_change_tracker_ctx` is never set and the filesystem tools' recording branch never runs | `tools/_utils.py:29`; `tools/__init__.py:22`; `filesystem.py:138-140` |
| **R3** | **`classify_failure_signal` has no production consumer**, so `CODE_FAILURE_CLASS` is reachable only through an uncalled function. The F28 denial fix *is* live — the docs conflate the two | `recovery.py:181,165,213-214`; `core/events.py:352,419`; `CONTEXT.md:86`; `AGENTS.md:129` |
| **R4** | **The plan names a test that does not exist**: `test_durable_run_layer_reachable.py`. The actual file is `test_durable_layer_reachable.py` | `WISP_MIGRATION_PLAN.md:90` |
| **R5** | **M12's report records known-unfinished work** that is not reflected in the phase's DONE status: *"`_ENGINE_DENIAL_PREFIXES` would be better; it is not done."* | `PHASE_M12_REPORT.md:156` |
| **R6** | **CONTEXT.md attributes the 18 fanout failures to "environmental reasons"**; redness is verified, the cause is not — and the main 13j1 file imports no `httpx`/`TestClient`, so the usual environmental cause does not apply | `CONTEXT.md:928`; baseline lines 24-41 |

---

## 11. POST-M13 VERDICT

```
POST-M13 VERDICT

CURRENT HEAD:
7c15626 (docs(m11): record the M11 phase; findings F29-F31; repair the commit table)

M13:
COMPLETE — implemented, verified, documented, NOT committed. No regression.

M13 EMPTY-OBSERVATION SAFETY:
INTACT. ProgressSignal.is_empty excludes work_units (stagnation.py:79-91);
observe() returns UNKNOWN for an empty observation without appending it,
counting it flat, or feeding the trap (stagnation.py:235-241). F32 not regressed.

M13 ACTION IDENTITY:
INTACT. work_units is keyed by action_key(tool,args), explicitly NOT
TaskNode.work_unit / tool_call_id (stagnation.py:72-76). Refusals carry the
identity through _refusal_result_event (stateless.py:2273, 10 call sites). F33 not regressed.

M13 ENFORCEMENT:
DEFERRED

M1:
DEFER. jsonschema declared (pyproject.toml:22) but NOT importable; reproduced that a
VALID args dict is refused as "Schema validation failed ... No module named 'jsonschema'"
(stateless.py:2214-2227, F8). No working tool path => the ADR-0016 measurement cannot be
taken => stage 3b has no basis for a decision. The acceptance mechanism itself is sound.

M5:
INVESTIGATE. Durable run layer is live for background runs only
(background.py:88-233, recover() at :213; composition.py:83,150,212; task/manager.py:20-21).
Zero run_store references in core/runtime.py or core/stateless.py. Consumer unclear;
risk of duplicating the session journal.

M6:
DEFER. Producer-less and consumer-less; its own docstring says it "Adds no authority"
(contracts/policy.py:1-3). Choosing which of the two disjoint decision models (F6) it
represents is a product decision.

M7:
DEFER — premise unsound. ChangeTracker is never fed (set_collaboration_tools has no
caller), its only reader reads an empty fresh instance (__main__.py:779-785), and three
other change records already exist (workspace.ChangeSet, runtime._touched_files,
runs.compensation.EditRecord). Wiring it would create a fourth.

M8:
DEFER. dag.py is LIVE (tools/orchestration.py:166,214; subagent_orchestrator.py:1307;
tool_executor.py:1935). 18 fanout-family failures at baseline (13 + 5), unchanged this run.

M10:
DEFER — by design. One node per closed tool exchange plus one terminal; iteration
boundaries are not observable. A stated property, not unfinished work.

NEXT PHASE:
INVESTIGATE

WHY:
Four independently-built and independently-tested authority mechanisms — P3
CompletionVerdict, P6 RecoveryLadder, P7 route_to_recovery/may_report_goal_met, and M12
classify_failure_signal — each have NO production consumer. They dead-end at one place:
the live turn loop consumes no verdict at all, and completion is derived from terminal
evidence alone (turn_succeeded, 13-H5; ADR-0016). M13 is the fourth instance of this
pattern, not a new problem. The boundary is real and the mechanisms are ready, but the
contract is not proven: three overlapping verdict vocabularies (P3 PASS/FAIL/INCONCLUSIVE,
P7 STAGNATING/UNKNOWN/PROGRESSING + may_report_goal_met, P6 rungs) have no stated
precedence. Per the brief's own discipline — IMPLEMENT ONLY AFTER THE CONTRACT IS PROVEN —
the next phase must bound that contract, not build the consumer.

DEPENDENCIES:
- Contract decision: which single verdict is authoritative for completion, and its precedence
  against the other three. No external dependency.
- Secondary and blocking for enablement only: F8 (jsonschema absent, uninstallable — no network).
  This gates P3 stage 3b's measurement, not the contract work.
- Non-blocking: the 18 fanout failures (M8) and the F17 flake.

RISK:
Medium. The contract work is read-only and low-risk. The risk sits in what follows it:
ADR-0016 rates the completion gate "High — the highest in the plan", because a stricter gate
can make previously-successful turns report INCONCLUSIVE. Any phase that enables gating must
keep the existing flag as the rollback switch and must not weaken the M13 tripwires, which
currently hold both enforcement paths shut.

PRODUCTION CHANGES:
NONE. No file under wisp/ or tests/ was modified. One inline RED-first probe was executed
(via heredoc, no file written) to reproduce F8; its output is quoted in section 6.
Files written by this phase:
  - PHASE_POST_M13_AUTHORITY_RECON.md                        (this document)
  - .workbuddy-ai/memory/post-m13/{status-before,head,log20,diffstat-before}.txt
  - .workbuddy-ai/memory/post-m13/diff-before.patch
  - .workbuddy-ai/memory/post-m13/{fullrun1,msuite,m11-16}.txt

TEST BASELINE:
Full suite: 129 failures, set-identical to baseline-failures-stable.txt, 0 new, 0 fixed
(17 errors + 112 failed). Migration suites: 714 passed. M11-M16: 222 passed.

MIGRATION REGRESSION:
NONE. Zero new failures, zero fixed. M13's own invariants re-verified in source; both
enforcement tripwires still green.

RECOMMENDED NEXT COMMAND:
env -u PYTHONPATH .venv/bin/python -m pytest \
  tests/test_acceptance_verdict.py tests/test_verdict_layer_recorded.py \
  tests/test_recovery_ladder.py tests/test_failure_signal_classification.py \
  tests/test_stagnation_detection.py tests/test_stagnation_live_wiring.py -q

...then open PHASE_M13_REPORT.md section 7 items 8-9, ADR-0016, and ADR-0032 side by side and
write the precedence contract for the completion verdict. Do not construct the consumer first.
```

---

## 12. What this phase deliberately did not do

- Did not implement M13 enforcement (routing to the recovery ladder, gating goal completion).
- Did not modify any production code, any test, or the graph.
- Did not wire `PolicyDecisionEnvelope`, `change_tracker`, or a foreground `RunRecord`.
- Did not delete or modify `multi_agent/dag.py`.
- Did not weaken any tripwire to make the repository look green.
- Did not use `git stash`, `git reset --hard`, or `git checkout --`.
- Did not fix the six defects in section 10 — they are recorded, not repaired.
