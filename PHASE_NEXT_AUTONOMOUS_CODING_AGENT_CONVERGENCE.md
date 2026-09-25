# PHASE NEXT — WISP AUTONOMOUS CODING AGENT CONVERGENCE

**Mission:** turn Wisp from a semantically closed agent runtime into a demonstrably
convergent autonomous coding agent.

**Method:** inspect → reconstruct → benchmark → identify missing capabilities → design →
implement → integrate → test → run realistic coding tasks → falsify → repair → document.

**Outcome:** one architectural capability was missing at the *objective* level; it is now
implemented, wired, tested and ratified as **ADR-0045**. The verdict is in §26 and is
derived from the behavioural evidence in §21, not from the presence of the mechanism.

---

## 1. Current capability baseline

| Dimension | State at the start of this mission |
|---|---|
| Package size | 374 Python modules under `wisp/`; 42-tool model surface |
| Test suites | migration suites 849 tests (848 pass, 1 known fail — F38); `tests/reliability/` 378 |
| Live provider | `nemotron-3-ultra:cloud` via a local Ollama daemon is the **only** free-tier model that works; every other `:cloud` model is HTTP 402 (not in free usage) or HTTP 410 (retired). Measured latency ≈ 2–17 s per call, ≈ 11 s for a trivial prompt |
| Turn loop | `WispAgentCore.turn()` — one prompt, up to `max_iterations` (default **50**) provider round-trips, then a bounded wrap-up |
| Completion | semantically closed per turn (ADR-0043/0044): terminal evidence → turn predicate → acceptance verdict → goal state → recovery record |
| Objective level | **empty** — see §4, §7, §8 |

The single most important baseline fact: **every `run_turn` caller in the repository
dispatches exactly one turn and returns.**

```
wisp/sdk.py:101            async for event in self._root.runtime.run_turn(...)
wisp/entry.py:551,802      async for event in root.runtime.run_turn(...)
wisp/headless.py:91        async for event in root.runtime.run_turn(...)
wisp/transport/websocket.py:326
wisp/server/routes/prompt.py:59
wisp/tui/screens/workspace.py:297
wisp/acp_session.py:102    def run_turn(self) -> Iterator[ContentBlock]
wisp/cli/repl.py:638       async for event in self.runtime.run_turn(...)
wisp/multi_agent/_runner.py:699   (one turn per subagent task)
wisp/benchmark/runner.py:156      (one turn per benchmark task)
```

The REPL's outer `while` is a **human** loop, not an autonomous one: it reads the next
line, and `pending` collects prompts the user *typed ahead*. `config.autonomous` is an
**approval** mode (auto-approve safe writes/bash) and nothing more — it starts no loop.

---

## 2. Existing architecture map

```
wisp/
├── core/                      the runtime spine
│   ├── stateless.py           WispAgentCore.turn / _turn_inner — the agent loop
│   ├── runtime.py             AgentRuntime.run_turn — session, journal, verdicts, records
│   ├── engine.py              WispAgentCore re-export
│   ├── events.py              AgentEvent, OutcomeClass, error codes, classify_result
│   ├── goal.py                derive_goal_state — the completion arbiter (ADR-0035)
│   ├── acceptance.py          Verdict, AcceptanceCriteria, Evidence, evaluate (P3 3a)
│   ├── verification.py        VerificationFloorGuard — the per-turn completion floor
│   ├── recovery.py            FailureClass (10), RecoveryRung (7), RecoveryLadder
│   ├── stagnation.py          M13 detector; monotonic latch (ADR-0037)
│   ├── task_graph.py          per-turn graph — RECORDED, not enforced
│   ├── convergence.py         ★ NEW — the objective-level loop (ADR-0045)
│   ├── mutator/search_replace.py   3-tier patch matching
│   ├── context/               boot, compactor, repomap
│   └── session.py, session_repo.py  journal + reconstruction
├── graph/                     the *other* executor (executor, planner, scheduler,
│                              validator, verifier, optimizer, store, coding_graphs)
├── multi_agent/               orchestrator, roles, dag, worktree manager, budgets
├── tools/                     42 tool implementations (registry.py)
├── providers/                 ollama, openai, openrouter, nvidia, mock, factory
├── benchmark/                 4 deterministic tasks + runner + SWE-bench surface
├── autonomous.py              ★ NEW — the wiring
├── autonomous_cli.py          ★ NEW — `wisp converge`
├── planner.py                 Plan/Task/PlanStore (write-only — F47)
├── repo_map.py                repository intelligence
├── context_assembler.py       system-prompt sections + trust classification
├── coding.py                  REPL strategy gate → graph templates
└── error_diagnosis.py         regex traceback classifier (exposed as the `diagnose` tool)
```

---

## 3. End-to-end execution trace

One turn, as the code actually runs it:

```
prompt
  └─ AgentRuntime.run_turn
       ├─ get_or_create_session; session lock
       ├─ core = _get_core(session)                    cached per (session, fingerprint)
       └─ WispAgentCore.turn
            ├─ _build_system_prompt(session, query)    ContextAssembler sections +
            │     default_system, role_extra, skills, project_context, memory,
            │     git_context, repo_map, tools block, lint, module summary,
            │     operating context, environment block
            └─ _turn_inner   (up to max_iterations = 50)
                 ├─ prune_messages                       bounded historical payloads
                 ├─ _guarded_provider_stream             canonicalization + stall recovery
                 │     └─ _normalized_provider_stream → canonical_event   (ADR-0040)
                 ├─ per tool call, IN THIS ORDER:
                 │     role filter → schema validation → approval gate →
                 │     extension intercept → ToolExecutor.authorize → execute
                 ├─ VerificationFloorGuard.note_tool_result   (F37 envelope unwrap)
                 ├─ finish? → guard.rejection()          bounded verification nudge
                 │            completion_gate()          bounded stagnation nudge
                 │            → done_event
                 └─ exhaustion → one wrap-up call → done | CODE_ITERATION_BUDGET
       └─ turn end (runtime):
            ├─ journal assistant/tool exchanges (incremental + turn-end)
            ├─ materialize the per-turn task graph          RECORDED
            ├─ record the P3 verdict (flag: record_verdict)
            ├─ recovery ladder decision (flag: recovery_ladder, default OFF)  RECORDED
            └─ derive + record the goal state (flag: goal_state)
```

**Where the trace ends.** The trace has no branch that asks *"is the user's objective
satisfied?"* — only *"did this turn finish?"*. That is the gap.

---

## 4. Goal model

`core/goal.py` holds a pure, total arbiter: six states, an explicit precedence table, no
severity score and no model judgement. `GOAL_MET` is reachable **only** through row 6,
which requires *both* `turn_succeeded` and an acceptance `PASS`.

What did not exist is anything that **supplies** the objective-level inputs:

- `acceptance.evaluate(criteria, evidence)` is pure and total, but the only producer of
  `AcceptanceCriteria` in the repository is `verification.floor_guard_criteria(guard)` —
  the **actor's own bookkeeping** (its own docstring says so). No objective→criteria
  derivation existed. → **F46**.
- No evidence producer existed for anything but the floor guard's own state.

So the goal model was correct and **unreachable for a real objective**. This is the first
half of the blocking gap, and it is why the mission's §23 ("no fake completion") was
already satisfied in *principle* — `GOAL_UNVERIFIED` was the only thing the system could
say about an objective — while being satisfied in a way that made the agent useless.

---

## 5. Repository intelligence model

| Component | What it does | Reachability |
|---|---|---|
| `repo_map.py` (1983 lines) | file discovery, per-language symbol extraction (tree-sitter with a regex fallback), dependency graph, importance, `format_for_llm(max_tokens)`, `get_relevant_files(query)`, `get_dependencies`/`get_dependents`, cached | **in the prompt** (`_build_repo_map`, token-bounded) and via `search_codebase` |
| `_build_module_summary` | a concise module structure overview | in the prompt |
| `_build_lint_context` | the workspace's lint/check configuration | in the prompt |
| `git_context.py` | branch, commit, status | in the prompt |
| `lsp/` + `lsp_*` tools | definition, references, hover, symbols, diagnostics | model-invoked |
| `semantic_index.py`, `code_index.py`, `tree_sitter_index.py`, `import_graph.py` | additional indexing | model-invoked / partial |
| `search_codebase`, `search_symbols` | retrieval | model-invoked |

**Assessment.** The intelligence is real and reasonably deep; what is bounded is how much
of it enters the prompt by default. That is the right trade (a repo map for a repository
this size would blow any context), and it means the agent must *retrieve*. Whether it
retrieves reliably is a **model** behaviour, and the benchmark says it does not: in
`fix-off-by-one` the agent spent calls on `.workbuddy-ai/…` and `/totals.py` — paths that
do not exist in its workspace — while `read_file` and `list_files` were returning correct
workspace-relative paths. The path guard correctly refused the escape (`Access denied:
/totals.py resolves to /totals.py, which is outside workspace …`), so the boundary held;
the *retrieval* did not.

---

## 6. Context architecture

One assembler, section-classified for trust (ADR-0031 / M14): `ContextAssembler` builds
the system prompt from `PromptContext`, with per-workspace memoisation keyed on the
workspace, the mtimes of `rules.md`/`conventions.md`/the memory file, the subagent prompt
hash and the allowed-tool hash. Per-turn blocks (operating context, environment) are
deliberately outside the static cache.

Bounding: `prune_messages` before every provider call, `compaction.py` + `maybe_compact`
across turns, `context_pruner`, `semantic_compressor`.

**The hole.** `PromptContext.from_legacy(workspace=…, default_system=…, role_extra=…,
skills_block=…, project_context=…, memory_block=…, git_context=…, repo_map=…)` — and
`_build_system_prompt` passes exactly those. It never passes `plan_mode`, `active_plan` or
`plan_context`, so `PlanState` is `None` and the plan block is empty. `ContextAssembler.
PlanState` has **no production constructor at all**. → **F47**.

---

## 7. Planning architecture

`planner.py` is a real planner: `Task` (dependencies, files, status, `is_ready`), `Plan`
(`next_task`, `progress`, `is_complete`, `start_task`/`complete_task`/`skip_task`),
`PlanStore` (atomic write, file lock, rotation), and `parse_plan_from_text` over a
documented format. Three tools expose it: `plan_task`, `update_plan`, `mark_step_done`.

**But nothing consumes it.** `Plan.next_task()` and `Task.is_ready()` are called from
`progress.py` and the `wisp plan` CLI only. The plan never enters a prompt (§6), never
gates completion, never drives execution. The model writes a plan, and then behaves
exactly as it would have without one. → **F47**.

This is the answer to §6 of the mission: **Wisp has a plan *store*, not a planning
*mechanism*.**

---

## 8. Graph / execution relationship

Two graph systems, and the answer is different for each.

| System | Role |
|---|---|
| `core/task_graph.py` | One node per closed tool exchange, materialized at turn end. Its own caller in `runtime.py` says *"RECORDED, not enforced: the message list remains authoritative"*. `TaskNode` carries no tool name, arguments or result (ADR-0029: *"the graph is a shape, not a payload"*). **A record.** |
| `wisp/graph/` (5635 lines) | A genuine executor: `planner.py` compiles an IR, `optimizer_passes.py` optimises it, `scheduler.py` + `executor.py` run it with concurrency, `validator.py`/`verifier.py` gate it, `store.py`/`trace.py` persist it. **Drives execution** — but its only caller is `coding.handle_prompt`, i.e. the **interactive REPL**. `run_headless`, the benchmark, the server, the SDK and ACP never reach it. |

So: the record does not drive, and the driver is not reachable from the non-interactive
entry points. Unifying them is a genuine architectural decision and is **not** taken here
(recorded as an open follow-up in ADR-0045).

---

## 9. Mutation architecture

- **Tools:** `write_file`, `edit_file`, `edit_file_multi`, `fs_mutate`.
- **Patch matching:** `core/mutator/search_replace.py` — three tiers (`exact`,
  `whitespace`, `similar`) with a reported `MatchTier`, so a fuzzy application is visible
  rather than silent.
- **Containment:** `pathsec.py` — observed working, in production, during the benchmark
  (`Access denied: /totals.py resolves to /totals.py, which is outside workspace …`).
- **Concurrency:** `file_lock.py` — a process-level `threading.Lock` plus an OS-level
  advisory lock, with owner metadata and a timeout.
- **Staleness:** the verification floor invalidates prior verification evidence on any
  mutation (`verify_ok_after_edit = None`), and `acceptance.invalidate()` generalises the
  same rule to evidence generally.

No transactional multi-file mutation and no merge/conflict resolution exist; two
concurrent writers to one file are serialised by the lock, not merged.

---

## 10. Verification architecture

One authority, in layers:

| Layer | Owner | Nature |
|---|---|---|
| the turn's completion floor | `VerificationFloorGuard` | per-turn; a mutation requires a *later* verification command to have exited 0 |
| the evidence contract | `verification._verify_result_is_success` | positional: `bash.py::_format_bash_output` emits `[exit code: N]` **only** on non-zero, so the prefix's absence *is* success. Both sides pinned by `tests/test_verification_contract.py` |
| the vacuous-green guard | `_run_tests_is_evidence` | a `0/0` run is not evidence |
| the verdict vocabulary | `acceptance.Verdict` (PASS/FAIL/**INCONCLUSIVE**) | pure, total, content-addressed evidence |
| the goal arbiter | `goal.derive_goal_state` | consumes the verdict; `GOAL_MET` needs both |

**Stage 3a**: `acceptance` records, it does not gate — ADR-0016 is `NOT_YET_DETERMINABLE`
because its contract names no sample size, threshold or provider requirement.

The layers are correctly separated (§9 of the mission: *command succeeded* ≠ *test passed*
≠ *implementation verified* ≠ *goal completed*). What was missing is the layer that
**produces** evidence for an objective. → F46.

---

## 11. Failure diagnosis

| Stage | Exists? |
|---|---|
| raw failure | yes — the `error` event carries `(message, recoverable, code)` |
| failure evidence | yes — `classify_result` → `OutcomeClass`; `classify_failure_signal` bridges the runtime signal |
| classification | **yes, and it is closed** — `FailureClass`, 10 classes, with a test pinning the count and a totality test over the engine's error codes |
| root-cause hypothesis | **no automatic stage.** `error_diagnosis.py` is a regex traceback classifier, exposed as the model-invoked `diagnose` tool; the model must choose to call it |
| candidate recovery | yes — `RecoveryLadder.decide`, legal-rung tables, budgets |
| verification of the recovery | yes — the floor guard, and now the acceptance probe |

So diagnosis is **model-driven with a classification substrate**. That is a real limitation
and it is named in §25; it is not the blocking gap, because classification + the ladder
already give the loop something better than "the command failed, try again".

---

## 12. Recovery

`core/recovery.py` is complete and, before this mission, **inert**:

- `FailureClass` — 10, closed, totality-tested.
- `RecoveryRung` — `RETRY < REPAIR < ROLLBACK < LOCAL_REPLAN < GLOBAL_REPLAN < DIAGNOSTIC < HUMAN`, cost-ordered as an `IntEnum` so "move down the ladder" is a comparison.
- `LEGAL_RUNGS` / `FORBIDDEN_RUNGS` — per class, total by construction, with `SECURITY`'s prohibition (`RETRY`/`REPAIR`/`ROLLBACK`/`LOCAL_REPLAN`/`GLOBAL_REPLAN`/`DIAGNOSTIC` all forbidden; only `HUMAN` legal) enforcing P6's no-retry rule **by class, not by matching statuses**.
- `BudgetGovernor` — one place that answers "how much is left".
- `RecoveryLadder.decide()` — evidence is **required**; `legal_rungs()` excludes anything already tried (R5, enforced structurally); an unsafe rollback **escalates**; exhaustion produces `HumanIntervention` as durable **state**, not a hang.

Before this mission it was called from exactly one place — `runtime.py:1216`, behind
`recovery_ladder` (default **OFF**) — and only to append a record. It is now driven by the
convergence controller.

---

## 13. Replanning

Before: none. `compose_replan_nudge` existed (M13's text for the completion gate), bounded
to `_MAX_STAGNATION_INTERVENTIONS = 2` per turn, behind `stagnation_gate` (default OFF).

After: the controller's `LOCAL_REPLAN` / `GLOBAL_REPLAN` / `DIAGNOSTIC` rungs are real
replans — a new session, a materially different directive, and the harness's measured
evidence. A rung cannot be repeated (§12's R5), so "replan" cannot degenerate into
"rephrase".

---

## 14. Stagnation and strategy change

M13's detector runs on the live turn path and **records**. ADR-0037 established that the
latch is monotonic — `trap_fired` never clears inside a turn — so the gate can withhold
`done` and nudge but **cannot change the goal state**. Enforcement stays behind
`stagnation_gate`, default OFF, pending ADR-0016's measurement.

The objective-level analogue is now in the controller: *the same criteria unmet and nothing
measurable changed* → `FailureClass.STAGNATION` → the ladder may only choose
`GLOBAL_REPLAN` or `DIAGNOSTIC` (`RETRY`/`REPAIR` are forbidden for that class). The
desired relationship in §12 of the mission is therefore realised at the level where it can
act:

```
no progress → stagnation detected → current strategy invalidated
            → new decomposition (GLOBAL_REPLAN) or information (DIAGNOSTIC) → new attempt
```

---

## 15. Subagents

`multi_agent/`: `subagent_orchestrator.py`, `roles.py`, `task.py` (contracts),
`dag.py` (`TaskDAG`, `DAGScheduler`, `DAGResult`), `worktree_manager.py` (isolation),
`resource_budget.py`, `capability_matcher.py`, `shared_context.py`, `telemetry.py`,
`context_partition.py`, `schema_validator.py`. The model reaches them through `spawn`,
`fanout`, `spawn_background`, `subagent_*` and `orchestrate_{chain,dag,map_reduce,vote}`.

The contracts the mission asks for mostly exist: a bounded goal (`contract.task`), an input
artifact (`shared_context`), tool permissions (role-restricted `allowed_tools`, enforced
before the approval gate), a budget (`ResourceBudget`), and a failure taxonomy (the same
`FailureClass`). A subagent authorises as a **narrowed child principal** (M15/ADR-0030).

Note `coding.decide_strategy` deliberately keeps orchestration requests on the single-agent
path: graph workers lack `spawn`, so routing them to a graph "always dies at the role gate".

---

## 16. Parallelism

`DAGScheduler` + `fanout` + `worktree_manager` provide branch parallelism with worktree
isolation. Budgets are per-branch. The historical fanout defects (`test_13j`, `test_13j1`)
are recorded as green now.

The **convergence loop is deliberately sequential**: attempts mutate the same workspace, so
parallelising them would trade repository correctness for latency, which §18 of the mission
forbids. Parallelism belongs *inside* an attempt, where it already exists.

---

## 17. Model routing

`provider_select.py` is a **manual** switch: `parse_target`, `build_provider`, `probe`,
`resolve_key`/`store_key`/`missing_key`, `apply_switch`, `persist`. `provider_catalog.py`
lists what is available. There is **no task-aware routing** — no cheap-model path for
extraction, no strong-model path for decomposition, no specialised verifier model. Routing
is host-controlled in the sense that the *user* controls it, which satisfies "the model must
not select its own authority" but not "task-aware routing".

This is a real gap (§15 of the mission) and is recorded in §25. It is not the blocking gap:
convergence is about whether the loop can close, not about which model closes it.

---

## 18. Memory

Two layers, both injected into the prompt:

- `memory.py` — workspace-scoped durable **facts**, with normalisation, dedupe, importance,
  touch-counts, a debounced atomic save and a migration path.
- `agent_memory.py` — per-session **summaries** (`AgentMemory`, `SESSIONS_FILE`), formatted
  by `format_for_prompt` and injected as the `memory_block`.

Both are attributable (workspace-scoped, timestamped) and reachable. The mission's
"actionable artifacts — hypothesis / evidence / decision / constraint" are **not** stored
as such; facts and summaries are. The convergence journal (§19) is the first
attempt/decision/evidence record.

---

## 19. Persistence / resume

| Layer | Mechanism |
|---|---|
| durable runs | `runs/` + `SQLiteRunStore`; durable admission, leases, idempotency, crash recovery (`durable_runs`, default ON) |
| session journal | append-only, hash-chained; incremental per-exchange flush (P1) and turn-end serialization sharing ONE grouping rule |
| reconstruction | `SessionRepository.reconstruct()` (M2); the journal is the idempotency record (ADR-0010) |
| killpoints | `tests/reliability/test_killpoints.py` — a real `SIGKILL` window (M3) |
| escalation | durable **state**, not a blocking call (M16) |
| **objective attempts** | ★ NEW — the convergence controller's append-only JSONL journal; `resume=True` re-runs **nothing** |

The controller's resume was tested with a torn final line (a crash artifact) and with a
completed attempt, asserting exactly one *new* turn is executed.

---

## 20. Benchmark suite

`wisp/benchmark/` already contained the right shape: `BenchmarkTask` with a `setup()` and a
`verify()` that **executes code** ("never by asking a model to judge another model"), plus a
SWE-bench `predictions.jsonl` surface, a `terminal_bench` adapter and a `docker_backend`.

Four deterministic tasks:

| id | difficulty | verification |
|---|---|---|
| `create-function` | easy | import `shout` from `strings_util`, assert `shout("wisp") == "WISP!"` |
| `fix-off-by-one` | easy | `sum_to(1)==1`, `sum_to(5)==15`, `sum_to(10)==55`, **and** the text no longer contains `range(1, n)` |
| `json-edit` | easy | `retries == 7` **and** the other keys unchanged |
| `subagent-delegate` | hard | `answer.txt == "7"` **and** a `spawn` tool call actually occurred |

Two drivers were added:

- `scripts/next_bench.py` — the existing one-turn-per-task flow, with per-task event capture
  and a machine-readable result file. (Needed because `wisp bench` passes the raw config
  dict into the core factory, which re-hydrates a `WispConfig` from it — so the placeholder
  `ollama_url` in `~/.config/wisp/config.json` overrode the env-resolved value.)
- `scripts/next_converge_bench.py` — the **same tasks through the convergence loop**, with
  the task's own verifier as the acceptance authority.

---

## 21. Real task results

Provider: `nemotron-3-ultra:cloud` (the only free-tier model that answers).
`max_tokens = 65536` (the F39/ADR-0038 boundary: 65536 accepted, 131072 rejected).

**Read §21.4 first if you read only one part.** The first two measurement rounds were
taken with a **broken instrument**, and finding that was the single most valuable outcome
of running the benchmark at all.

### 21.1 Round 1 — the single-turn benchmark, as it existed

`scripts/next_bench.py`, `max_iterations = 50`, total wall clock **926.4 s**.

| task | status | wall clock | tool calls | verifier said |
|---|---|---|---|---|
| `create-function` | **FAIL** | 129.9 s | 35 | `ImportError: cannot import name 'shout' from 'strings_util'` |
| `fix-off-by-one` | **FAIL** | 193.1 s | 50 | `AssertionError: sum_to(1) -> 0` |
| `json-edit` | **TIMEOUT** | 301.0 s | — | harness bound hit |
| `subagent-delegate` | **TIMEOUT** | 301.1 s | — | harness bound hit |

`0 passed / 2 failed / 2 timeout`. And `files_touched == []` for **all four** — verified
directly against the workspaces: `strings_util.py` had no `shout`, `totals.py` still had
`range(1, n)`, `settings.json` still had the old `retries`, `answer.txt` was never created.

**Zero mutations in 85 tool calls.** The obvious reading — "the model will not act" — is
what I first wrote down. It was wrong, and the falsification is in §21.3.

### 21.2 Round 2 — the convergence loop

`scripts/next_converge_bench.py`, `create-function`, `max_attempts = 2`, 240 s per attempt,
482.1 s total:

```
attempt 0 [INITIAL]   turn_succeeded=False  tools=356  changed_files=[]  verdict=fail
attempt 1 [REPAIR]    turn_succeeded=False  tools=377  changed_files=[]  verdict=fail
goal_state = goal_failed   converged = False
```

The loop behaved exactly as designed — it measured, classified (`implementation`), chose a
structurally new rung, re-attempted, and then reported `goal_failed` **honestly** rather
than claiming success. But it, too, measured a broken instrument (§21.3).

### 21.3 Falsification of round 1 — the instrument was broken, not the agent

`changed_files == []` cannot distinguish three very different causes: the agent never
called a mutating tool, it called one whose arguments were rejected, or it called one that
was refused. So the harness was instrumented to record the **tool-name histogram and the
outcome of every mutating call**. One attempt, 151.6 s, 73 tool calls:

```
read_file 2   edit_file 1   write_file 2   run_bash 3
edit_file_multi 1   git_status 1   list_files 1
```

Six mutating calls — and every one of them denied:

```
edit_file  {"path":"strings_util.py",
            "old_text":"def greet(name):\n    return \"hello\"",
            "new_text":"…def shout(name):\n    return name.upper() + \"!\""}
  -> {'status': 'error', 'data': '[Denied: edit_file requires a wired ToolExecutor
      (no approval/policy/audit on the fallback path)]'}

write_file {"path":"strings_util.py", "content":"…def shout(name):\n    return name.upper() + \"!\""}
  -> [Denied: write_file requires a wired ToolExecutor …]

edit_file_multi {…}  -> [Denied: edit_file_multi requires a wired ToolExecutor …]
write_file {"path":"./strings_util.py", …} -> [Denied: …]
```

**The model was solving the task correctly.** It wrote a valid `shout()` implementation —
`name.upper() + "!"`, exactly the requirement — and the runtime refused to apply it, six
times, silently. This is the **F54** defect:

`WispAgentCore._execute_tool` (`stateless.py:2043`) has a deliberate, correct safety
fallback: with no `tool_executor` there is no approval, policy or audit, so it allows
**READ-only** tools and denies everything else (`risk_for_tool`: `write_file`/`edit_file`/
`edit_file_multi` are `WRITE`; `run_bash`/`run_tests`/`exec_sandbox`/`spawn` are `EXEC`).
`CompositionRoot` wires an executor — so `wisp run`, the REPL, the server, the TUI, ACP
and `run_headless` are all fine. But `benchmark/runner.py::make_ollama_core_factory`
built a core **without one**, and it is the one place that builds a core by hand.

Consequences, all now fixed:

- `wisp bench` has been reporting FAIL for tasks **no agent could ever pass**, because
  every write, every `run_bash` and every `spawn` was refused. The published benchmark
  number was not a measurement of the agent.
- `spawn` was denied too, so `subagent-delegate` was unpassable by construction — and its
  `verify_events` gate ("no subagent spawn — model answered solo") would have reported the
  model's failure to delegate as a capability gap.
- The one-turn timeout in `subagent-delegate`/`json-edit` is consistent with an agent
  retrying a refused action until its iteration budget ran out.

The fix is one line in the factory (wire a `ToolExecutor`), plus a tripwire that fails if
any production module outside `composition.py` builds a `WispAgentCore` without one.

### 21.4 Round 3 — the product path, end to end

The same task, through the real entry point this mission added —
`wisp converge` → `CompositionRoot` → `AgentRuntime.run_turn` → a core with a wired
executor:

```
$ wisp converge "Add a function shout(name) to strings_util.py that returns the name
  uppercased with an exclamation mark appended, so shout('wisp') == 'WISP!'." \
  -w .workbuddy-ai/memory/next/cli-ws -m nemotron-3-ultra:cloud \
  --permission-mode full --max-attempts 2 --json

  attempt 1 [INITIAL]: turn succeeded in 48.0s, 9 tool call(s)
```

```json
{ "goal_state": "goal_met",
  "converged": true,
  "reason": "every required criterion has valid evidence produced by the harness",
  "attempts": [{ "index": 0, "rung": "INITIAL",
                 "turn_succeeded": true, "terminal_outcome": "succeeded",
                 "changed_files": ["strings_util.py"],
                 "tool_calls": 9,
                 "verdict": "pass", "goal_state": "goal_met",
                 "unmet": [],
                 "evidence_ids": ["symbol:shout:a83526904b002003"],
                 "duration_s": 48.045 }] }
```

and the repository, read back off disk:

```python
"""String helpers."""


def greet(name):
    return "hello"


def shout(name):
    return name.upper() + "!"
```

**48 seconds, 9 tool calls, one attempt, and the objective is satisfied.** Compare round 1:
35 tool calls, 130 seconds, no change. Same task, same model, same workspace shape. The
difference is the instrument.

Two things this run establishes that no test could:

1. **The criteria were derived by the host, not by the model.** The objective named a
   file and a definition, `derive_acceptance` produced `symbol:shout`, and the harness
   measured it by reading the file. The evidence id `symbol:shout:…` is a content digest
   of that measurement — the model's prose was never an input.
2. **`GOAL_MET` was earned through ADR-0035's row 6** — a successful turn *and* an
   acceptance `PASS` — so the completion chain held on a real objective, not only in
   tests.

Round 1's numbers are retained above because they are the evidence for F54, not because
they describe the agent.

### 21.5 Round 4 — the fixed benchmark

`scripts/next_bench.py` with the executor wired (`WISP_PERMISSION_MODE=full`), 4 tasks,
240 s per task, 457.1 s total. The **same suite, same model, same tasks** as round 1:

| task | round 1 | round 4 | tool calls (r1 → r4) | files changed |
|---|---|---|---|---|
| `create-function` | FAIL 129.9 s | **TIMEOUT** 241.0 s | 35 → — | — |
| `fix-off-by-one` | FAIL 193.1 s | **PASS** 105.4 s | 50 → **6** | `totals.py` |
| `json-edit` | TIMEOUT 301.0 s | **PASS** 36.8 s | — → **4** | `settings.json` |
| `subagent-delegate` | TIMEOUT 301.1 s | **PASS** 72.2 s | — → **5** | `answer.txt` |

`0 passed / 2 failed / 2 timeout` → **`3 passed / 0 failed / 1 timeout`**, with the tool
count collapsing from 35–50 to **4–6**.

`create-function` is the one non-pass, and it is a *provider* failure, not an agent one:
the run log for that task is a wall of `Server error 502, retrying in 1s…` and
`Provider stream no data for 90s (attempt 1/3) — retrying`. It had already begun working
the task correctly — it was running `python3 -c "from strings_util import shout; …"` to
check its own work — when the harness's 240 s bound expired. The identical objective
succeeded through `wisp converge` in §21.4.

**A detail worth reading twice.** All three passing runs are marked `surrendered=True`:
the turn's `VerificationFloorGuard` exhausted its nudge budget and let the turn finish
*unverified*. The objective was nevertheless met, because the **harness** measured the
repository afterwards and found the criteria satisfied. That is §9 of the mission's
requirement — *command succeeded* ≠ *test passed* ≠ *implementation verified* ≠ *goal
completed* — holding in production: the turn-level floor and the objective-level acceptance
are different authorities, and the one that counts for the objective is the one that
measured it.

The passing runs also show the agent verifying itself once it can: the `create-function`
log contains
`run_bash UNCONFINED: no sandbox provider — executing on host: python3 -c "from strings_util import shout; print(shout('wisp'))"`.
That is the F48 limitation in the open — with `--permission-mode full` and no sandbox
provider, `run_bash` runs unconfined on the host.

### 21.6 Attempted: an external provider (OpenRouter) — blocked on credentials

Every number above comes from `nemotron-3-ultra:cloud`, the only free-tier Ollama model
that answers — a real limitation (§25.8). A second provider was therefore attempted, to
check that convergence is not an artifact of one model. **It is blocked on the API key,
not on Wisp:**

| Probe | Result |
|---|---|
| `GET https://openrouter.ai/api/v1/models` (public) | **200**, 0.9 s, 460 models |
| `stealth/space-bunny-alpha` in that list | **present** — the model id is valid |
| `POST /chat/completions` with the supplied key | **401** `{"error":{"message":"User not found.","code":401}}` |
| `GET /key`, `GET /credits` with the same key | **401** `User not found.` — every authenticated route |
| `GET /key` with a raw (unprefixed) header | **401** `Missing Authentication header` |

So the endpoint is reachable, the model exists, and the key is rejected by OpenRouter
itself. Wisp's `openrouter` provider is wired (`WISP_PROVIDER=openrouter`,
`WISP_API_KEY=…`, `WISP_MODEL=…`) and was not exercised.

One incidental finding worth keeping: the venv has **no CA path**
(`ssl.get_default_verify_paths()` → `cafile: None`), so the first probe failed with
`CERTIFICATE_VERIFY_FAILED` even though the machine has egress. `certifi` is installed;
`SSL_CERT_FILE=<certifi>/cacert.pem` fixes it. This is recorded in the project memory but
it cost a probe here, and it will cost the next person one too.

### 21.7 Raw artifacts

| File | Contents |
|---|---|
| `.workbuddy-ai/memory/next/bench-run1.log` / `bench-results.json` | round 1 — the broken instrument |
| `.workbuddy-ai/memory/next/converge-run2.log` / `converge-run.json` | round 2 — the loop on the broken instrument |
| `.workbuddy-ai/memory/next/converge-run3.json` | round 2 — the tool histogram |
| `.workbuddy-ai/memory/next/converge-run4.json` | round 2 — **the mutation trace that found F54** |
| `.workbuddy-ai/memory/next/cli-converge2.log` | round 3 — the product path, converged |
| `.workbuddy-ai/memory/next/bench-fixed.log` / `bench-fixed.json` | round 4 — the fixed benchmark |
| `.workbuddy-ai/memory/next/regression-reliability.log` | `tests/reliability/` — 520 passed |

---

## 22. Failure cases

| # | Case | Observed |
|---|---|---|
| 1 | **Planning failure** — the initial plan is wrong | Not reachable as a *planning* failure: no plan drives execution (§7). The agent proceeds without one. |
| 2 | **Context failure** — required information missing | Observed in round 1: `fix-off-by-one` never located `totals.py` despite it being the only file in the workspace. **But see F54** — the same agent, with a working runtime, found and wrote `strings_util.py` in 9 tool calls (§21.4), so the round-1 confusion is not separable from the denied-mutation environment it was operating in. |
| 3 | **Mutation failure** | **Observed, and it was the blocking gap.** Six correct mutating calls refused in one attempt (§21.3). Fixed; the mutation now lands (§21.4). |
| 4 | **Test failure** | Observed: both verifiers reported the concrete failure, and in single-turn mode nothing acted on it. |
| 5 | **Diagnosis failure** | Observed by omission: no automatic diagnosis stage exists (§11). |
| 6 | **Recovery failure** | In round 2 the loop recovered *correctly* — `implementation` → `REPAIR` → re-attempt — but the re-attempt was also refused, so it could not succeed. Its terminal state was honest (`goal_failed`). |
| 7 | **Stagnation** | Reproduced deterministically in `test_stagnation_selects_a_strategy_changing_rung`: identical measurements ⇒ `STAGNATION` ⇒ `GLOBAL_REPLAN`, `RETRY`/`REPAIR` forbidden. |
| 8 | **Parallel conflict** | Not exercised; the loop is sequential and worktree isolation covers `fanout`. |
| 9 | **Provider failure mid-task** | Reproduced twice over. By test: `test_a_transient_provider_failure_retries` (`TRANSIENT` ⇒ `RETRY`) and `test_a_capacity_rejection_is_not_retried_forever`. Live: `Server error 502, retrying in 1s…` and `Provider stream no data for 90s (attempt 1/3) — retrying` appeared in the round-4 run. |
| 10 | **Interruption** | Reproduced in `test_a_torn_journal_line_does_not_break_resume`. |
| 11 | **Resume** | Reproduced in `test_resume_continues_without_re_running_completed_attempts` — exactly one *new* turn. |
| 12 | **False success** | Reproduced in `test_a_successful_turn_with_unmet_criteria_is_not_convergence` — a successful turn with unmet criteria is `GOAL_FAILED`, never `GOAL_MET`. Also observed live in round 2: the loop reported `goal_failed` rather than claiming success. |
| 13 | **Partial success** | Reproduced in `test_partial_success_is_not_convergence` — one criterion satisfied, one not. |
| 14 | **Hidden regression** | Reproduced in `test_hidden_regression_blocks_convergence` and `test_the_no_regression_criterion_is_gating`. |
| 15 | **A benchmark that cannot execute** | **Observed.** The instrument itself was the failure (§21.3). |

---

## 23. Falsification

The new suites are **51 tests**; the ones that matter are the ones that try to make the
loop lie.

| Property | Test | Result |
|---|---|---|
| A successful turn with unmet criteria is never `GOAL_MET` | `test_a_successful_turn_with_unmet_criteria_is_not_convergence` | holds |
| No criteria ⇒ `INCONCLUSIVE`, never `PASS` | `test_no_criteria_can_never_be_goal_met` | holds |
| Criteria with no evidence ⇒ `INCONCLUSIVE` | `test_criteria_without_evidence_are_inconclusive` | holds |
| A failed turn is never `GOAL_MET`, even with criteria satisfied | `test_a_failing_turn_can_never_be_goal_met` | holds |
| A denial escalates and runs exactly one turn | `test_a_denied_action_escalates_and_is_never_replanned` | holds |
| No rung is ever repeated | `test_the_ladder_never_repeats_a_rung` | holds |
| Attempts are bounded | `test_attempts_are_bounded_by_max_attempts`, `test_the_objective_budget_overrides_the_controller_default` | holds |
| Evidence names the harness as its producer | `test_evidence_is_produced_by_the_harness_not_the_actor` | holds |
| A vacuous green is not evidence | `test_a_vacuous_green_is_not_evidence` | holds |
| Rollback is skipped when it cannot be executed | `test_rollback_is_skipped_when_it_cannot_be_executed` | holds |
| The derivation refuses an unparseable objective | `test_derivation_refuses_an_unparseable_objective` | holds |
| The turn predicate is read in **both** event shapes (F40's defect class) | `test_typed_events_are_read_the_same_as_dict_events` | holds |
| The loop cannot reach the runtime | `test_the_loop_does_not_depend_on_the_runtime` | holds |
| The acceptance path never mentions a model | `test_the_acceptance_derivation_never_consults_a_model` | holds |

**Non-vacuity.** Two controls prove the assertions can fail:
`test_the_false_success_test_is_not_vacuous` shows the same shape *does* converge when the
criterion is satisfiable, so the false-success test fails for the reason it claims; and
`test_a_denial_without_the_no_retry_rule_would_loop` asserts `SECURITY`'s legality tables
directly, so the escalation comes from the taxonomy rather than from the controller
happening to stop.

**Five defects were found by running the mission's own benchmark and tests** — the first
two by the new suites, the last three only by execution:

- **F49** — a deterministic check treated an absent measurement as a `FAIL`, collapsing
  "no evidence" into "failing evidence".
- **F50** — stagnation was reported from an *empty* measurement and from an objective with
  *no* criteria.
- **F51** — derived "exit 0" is unsatisfiable on a red baseline; criteria are now
  baseline-relative.
- **F52** — `_git_baseline` committed into the **enclosing** repository (eleven
  `bench baseline` commits landed in this project; recovered with `git reset --mixed`).
- **F54** — **the blocking gap**: the mutating tool surface is unreachable on any path
  that builds a `WispAgentCore` by hand, so every write, `run_bash` and `spawn` was
  refused. Fixed; §21.3 is the trace that found it.
- **F55** — `wisp converge` re-declared the global `--model`/`--workspace` flags, which
  `extract_global_flags` strips before dispatch, so a run silently used the wrong model
  and the wrong workspace.

Plus one implementation bug the suite caught immediately: `Objective.max_attempts`
defaulted to `3`, silently shadowing `ConvergenceController(max_attempts=…)` — a bound that
was not a bound. It now defaults to `0` meaning "inherit".

---

## 24. Architectural decisions / ADRs

**ADR-0045 — Convergence is an objective-level loop that consumes the turn-level
authorities and re-implements none of them.** Rules R1–R12:

| # | Rule |
|---|---|
| R1 | Acceptance criteria are host-derived, never model-declared |
| R2 | Evidence is produced by the harness and names its producer |
| R3 | No new verification authority — `evaluate`, `derive_goal_state`, `classify_failure*` are consumed, not re-derived |
| R4 | A criterion that cannot be evaluated must not report `FAIL` |
| R5 | The next strategy is chosen by `RecoveryLadder`; R5's structural novelty is the ladder's own rule |
| R6 | A denial outranks the stagnation observation |
| R7 | Stagnation requires unmet criteria **and** a non-empty measurement |
| R8 | Each attempt gets a fresh session |
| R9 | Bounded; exhaustion is a state, never a hang and never a success |
| R10 | Rollback is opt-in and refuses rather than truncating |
| R11 | Attempts are journaled append-only; a resume re-runs nothing |
| R12 | The turn predicate is not re-derived — it delegates to `terminal_outcome_from_evidence` |

The decision log is now **ADR-0001 … ADR-0045**.

---

## 25. Remaining limitations

1. **The plan is still write-only (F47).** `ContextAssembler.PlanState` has no production
   constructor; `_build_system_prompt` never passes `plan=`. A plan the model wrote is never
   shown to it again. Fixing this is a separate decision (should the *model's* plan be
   injected, or should the loop derive work units?) and was not taken.
2. **The default permission mode blocks the agent's own verification (F48).**
   `permission_mode` defaults to `auto_edit`, in which `run_bash` is blocked and the prompt
   switches to `VERIFICATION_LOOP_RULES_NO_BASH`. Acceptance is unaffected (the harness
   measures), but the agent cannot run the project's test command by default.
3. **The two execution architectures are not unified (§8).** `wisp/graph/executor.py` drives
   execution but is REPL-only; the convergence loop drives execution but does not use the
   graph. Recorded as a follow-up question in ADR-0045, not decided.
4. **No automatic root-cause stage (§11).** Classification exists; the *hypothesis* is the
   model's, or nobody's.
5. **No task-aware model routing (§17).** Provider selection is manual.
6. **The loop is sequential.** Attempts share one workspace; parallelism inside an attempt is
   unchanged.
7. **A live recovery-to-success has not been observed.** The ladder is proven by 34 tests
   and its honest-exhaustion path is proven live (§21.2); the failure-then-success path is
   not yet captured on a live run.
8. **The result is model-dependent.** The only free-tier model available is weak — 356 and
   377 tool calls on two failed attempts — though it was sufficient once the runtime
   allowed it to act (§21.4).
9. **Benchmark workspaces are harness artifacts.** They are deeply nested under the repo,
   which plausibly contributed to the path confusion seen in round 1.
10. **Derived acceptance is a closed grammar.** It covers a declared verification command and
    an explicitly named definition in an explicitly named file. Everything else yields no
    criterion, and then `GOAL_UNVERIFIED` — honest, and less useful than a richer derivation
    would be.
11. **`acceptance` is still stage 3a** for the *turn* path. The objective path now gates
    through the controller; the turn path's gate remains behind ADR-0016's
    `NOT_YET_DETERMINABLE`.

---

## 26. Final convergence verdict

The mission's definition of done is behavioural: *given a real software-engineering
objective and a real repository, Wisp can autonomously move the repository toward the
requested state, recover when its first approach fails, verify its work independently, and
terminate only when the objective is actually supported by evidence.*

Taken one clause at a time, against the evidence in §21:

| Clause | Evidence | Verdict |
|---|---|---|
| move the repository toward the requested state | §21.4 — a real CLI invocation, a real repository, `shout()` written, read back off disk; §21.5 — **3 of 4 deterministic tasks pass**, 4–6 tool calls each, up from 0 of 4 | **demonstrated** |
| verify its work independently | §21.4 — evidence id `symbol:shout:…`, a content digest of a harness measurement; the model cannot write evidence (R2). §21.5 — all three passes are `surrendered=True` on the turn's floor yet pass the harness's criteria, so the two authorities are demonstrably separate | **demonstrated** |
| terminate only when the objective is supported by evidence | §21.4 `goal_met` through ADR-0035 row 6; §21.2 the same loop reporting `goal_failed` rather than claiming success | **demonstrated** |
| recover when its first approach fails | 34 tests covering RETRY / REPAIR / ROLLBACK / LOCAL_REPLAN / GLOBAL_REPLAN / DIAGNOSTIC, the no-repeat rule, denial escalation, and resume; §21.2 exercised the live path (`implementation` → `REPAIR`) and terminated honestly. **No live recovery-to-success has been observed** — on the working path the first attempt succeeded | **demonstrated in test, not yet observed live** |

The mission also asked for one thing specifically: to find and close the architectural
capability preventing convergence. It was found, and it was **not** the one this report
initially named.

Round 1's "zero mutations in 85 tool calls" was a *measurement of a broken instrument*.
The mutation trace (§21.3) showed the model writing a correct `shout()` implementation and
the runtime refusing it six times with
`[Denied: write_file requires a wired ToolExecutor (no approval/policy/audit on the
fallback path)]` — because `benchmark/runner.py` built a `WispAgentCore` without the
`tool_executor` that `CompositionRoot` wires. Every write, every `run_bash` and every
`spawn` was refused; `wisp bench` had been reporting FAIL for tasks no agent could pass.

So the blocking gap was **F54: the mutating tool surface is unreachable on any path that
constructs a `WispAgentCore` by hand, and it fails closed with a per-call denial that no
layer surfaces as evidence.** It is architectural — a security fallback that is correct in
isolation and catastrophic when a harness takes that path — and it is now fixed, with a
tripwire that fails if any production module outside `composition.py` builds an unwired
core.

What the loop added on top of that fix is what made the difference between 35 tool calls
with no result and 9 tool calls with the result: it *measured the repository* instead of
trusting the turn, and it said `goal_met` only when the measurement agreed.

### Residual, stated plainly

- **A live recovery-to-success has not been observed.** The ladder's behaviour is proven
  by test and its honest-exhaustion path is proven live (§21.2); a live run in which
  attempt 1 fails and attempt 2 succeeds has not been captured. That is the next
  measurement, not a claim.
- **The plan is still write-only** (F47). Nothing here changes that; the loop supplies the
  missing forward motion instead.
- **The default `permission_mode` blocks the agent's own verification** (F48).
- **The two execution architectures are still not unified** (§8).
- **The result is model-dependent**, and the only free-tier model available is weak — it
  was, however, sufficient, once the runtime let it act.
- **`graph/executor.py` remains REPL-only**, so the loop does not reuse the graph.

None of these is a capability whose absence prevents convergence; each is a limitation on
how well it converges.

```text
AUTONOMOUS CODING AGENT: CONVERGENT
```

with the residuals above recorded, and with the honest note that this verdict is
**newly earned**: the first two rounds of measurement said the opposite, and they were
wrong because the instrument was wrong. The falsification that overturned them is in
§21.3, and it is the reason this report does not end where it began.
