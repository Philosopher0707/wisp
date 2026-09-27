# PHASE_PLAN_CLI.md — the plan the operator cannot see

**Mission:** `PHASE_F47_PLANSTORE.md` §2's first recorded finding: *"`wisp plan`, `wisp progress`
and `wisp plan list` never see an agent's plan."*
**Baseline:** branch `plan-cli`, on `f47-planstore` (`3c573de`). `main` has not received the last
four landings. The tree carries the user's WIP from `CONTEXT.md` §8, untouched.
**Decision:** ADR-0064.

---

## §1 — In one page

**For the operator: `wisp plan`, `wisp progress`, `wisp plan list` and `wisp plan abort` now show and
act on the plan the agent wrote for this workspace.** Until now they printed *"No active plan."* /
*"No plans found."* after every agent plan in the default configuration. Driven, before → after:
*"No active plan."* → *"Plan: g"*, and *"No plans found."* → the plan's row. The same holds when the
workspace is reached through a symlink or a trailing slash. Under `WISP_WORKSPACE=.`, which already
worked, the output is byte-identical.

**Also changed:** planning in one project no longer deletes another project's plan. Rotation keeps the
ten newest plans **per workspace**, where it used to keep ten in total. Plans already on disk stay
readable, and none is rewritten until it is next saved.

**What did not change:** the plan tools' results, byte-for-byte, under every spelling (12/12 × 4
cases). The store is identical except each plan's `workspace` field, which is now the resolved
path.

**The decision** (§2, ADR-0064) is a fourth shape. The brief's three were tested:

- **Removal** is refuted: the commands have users.
- **Candidate 1** (the CLI alone) is refuted: ten plans elsewhere still delete the plan.
- **Candidate 2** (per-workspace rotation on the raw key) is incomplete: the agent's key is verbatim,
  so one directory has five keys.

What landed is **one resolver inside `PlanStore`**, used by lookup, listing, saving and rotation.

**Verification:**

- Guard `tests/test_plan_cli_sees_agent_plan.py` (12): **RED 7 failed / 4 passed**, each for the
  named reason; then 12 passed.
- **3/3** probes caught.
- **291/291** over 10 files, before and after.

**One more thing the change exposed.** `tests/test_main_cli.py::test_list_no_plan` had been reading
**the operator's real `~/.config/wisp/plans`**, because its patch missed `wisp.progress`'s own
`PlanStore` binding. The `"."` filter hid that. With the filter keyed on this repository, the test
failed on the plans this machine's store holds for this workspace. The test is now isolated. The
real store's contents were **not read**.

---

## §2 — Deliverable 1: the measurement, and the decision it picks

Every number below comes from `scripts/plan_cli_measurement.py`, run in this environment.

- Every child process has a **private `HOME`**, so the operator's real `~/.config/wisp/plans` (which
  exists on this machine) is never read or written.
- The agent's write is the production tool path: `WispConfig().workspace` (what the CLI and REPL
  put in `session["workspace"]`) → `registry.execute_tool("plan_task", …, workspace)` →
  `tool_plan_task` → `PlanStore.save`.
- The CLI runs as the real console entry point, `python -m wisp …`, in the workspace.

### 1. How the agent keys a plan (verified against the writer)

`tool_plan_task(goal, tasks, workspace)` → `parse_plan_from_text(…, workspace=workspace)` →
`Plan(workspace=workspace)` → `PlanStore.save`. **Nothing normalizes it.** The key is
`session["workspace"]` **verbatim**.

| how the operator starts wisp | stored key |
|---|---|
| default (cwd = the project) | `<project>`: absolute, resolved |
| `WISP_WORKSPACE=.` | **`"."`** |
| `WISP_WORKSPACE=sub/..` | **`"sub/.."`** |
| `WISP_WORKSPACE=<project>/` | **`"<project>/"`**: trailing slash kept |
| `WISP_WORKSPACE=<symlink to project>` | **`"<link>"`**: not resolved |
| default, started in `project/sub` | `<project>/sub` (a different directory, so correctly a different key) |

**`PHASE_F47_PLANSTORE.md` §2.1's *"absolute"* is true of the default only.** The key is whatever
string the session carries, so **one directory has as many keys as it has spellings**.

### 2. What each CLI command shows today (driven)

| case | `wisp plan` | `wisp progress` | `wisp plan list` |
|---|---|---|---|
| default | *"No active plan."* | *"No active plan. …"* | *"No plans found."* |
| `WISP_WORKSPACE=.` | **the plan** | **the plan** | **the plan** |
| relative / trailing slash / symlink | *"No active plan."* | *"No active plan. …"* | *"No plans found."* |

`wisp plan abort` in the default case prints *"No active plan to abort."*, and the agent's plan stays
**`active`** (measured on disk). **A fourth reader** with the same mismatch, and not in F47's report.

**The defect is exactly as the brief states it.** The CLI reports *"no plan"* in the default
configuration, and nothing distinguishes that from an empty store. It is also **not total**: in the
one configuration whose key happens to be `"."`, the CLI works. For the same reason, a plan written
under `"."` is found by `wisp plan` **from any directory**.

### 3. Every reader of the key (the search)

A search of `wisp/` for `load_active`, `list_plans`, `list_all()`, `PlanStore()`, `PLANS_DIR`,
`cmd_plan` and `cmd_progress` finds these readers and no others:

| reader | key it passes |
|---|---|
| `tool_mark_step_done`, `tool_update_plan` (`wisp/tools/plan.py`) | `session["workspace"]` verbatim, the writer's own string |
| `cmd_plan` (show), `cmd_plan abort`, `cmd_progress` (`__main__.py:693, 707, 733`) | `"."` |
| `cmd_plan list` → `progress.list_plans(".")` | filters `list_all()` on `"."` |
| `PlanStore._rotate` | none: counts **every** plan, whatever its key |

No REPL command, server route, TUI or SDK surface reads the store (searched: `wisp/repl`,
`wisp/tui`, `wisp/server`, `wisp/sdk`, `wisp/cli`). `wisp/repl/commands/agents.py` matched
only on the role name `"planner"`.

The tools are consistent **within a session**. The same directory reached by two spellings (a
symlink one day, the real path the next) is two keys to them too: a new session's `mark_step_done`
finds no plan.

### 4. The candidates, driven

Each candidate was applied in the child process. The CLI ran in the default configuration after the
agent's write. Then ten plans were saved in another workspace, and `wisp plan` ran again.

| | `wisp plan` / `progress` / `plan list` | after ten plans elsewhere |
|---|---|---|
| **today** | nothing, nothing, nothing | nothing |
| **candidate 1**: CLI keys on the agent's workspace | **the plan**, ×3 | *"No active plan."* **The plan was deleted** by rotation |
| **candidate 2**: that, and rotation counted per workspace | **the plan**, ×3 | **the plan**: it survives |
| **candidate 3**: remove the commands | the command is gone | — |

### 5. The decision

**Candidate 3, removal, is refuted.** The commands are not an artifact of a feature that does not
exist:

- They read the agent's plans today in the `WISP_WORKSPACE=.` configuration.
- They have tests (`tests/test_main_cli.py`, `tests/test_main_entry.py`).
- They are the operator's only view of a store the model now reads every time it updates the plan
  (ADR-0063).

Removing them is a CLI-surface change with a user, not the F57-style removal of something nothing
reads.

**Candidate 1 alone is not honest.** It makes a plan visible that ten plans anywhere will silently
delete, and that deletion also takes the plan from the agent's own tools. **Candidate 2 as the brief
states it is incomplete.** Per-workspace rotation must group by *the same* key, and the measured key
has one spelling per way of starting wisp. Grouping by the raw string would count `"."`,
`"<project>/"` and `"<link>"` as three workspaces.

**Decision (ADR-0064), a fourth shape: one resolver, inside the store, for identity, lookup and
rotation.**

| | |
|---|---|
| **the resolver** | `wisp.planner.workspace_key(workspace) -> str` = `str(Path(workspace).expanduser().resolve())`. It lives **in `PlanStore`'s module**, and **the store applies it**: to the query in `load_active`, to each stored key when matching and listing, to `Plan.workspace` on `save`, and to the grouping in `_rotate`. **No caller normalizes**, so no caller can do it differently (the `duplicated-authority` class). |
| **the key's source** | unchanged for the agent: `session["workspace"]` ← `WispConfig().workspace`. The CLI takes it from **the same place**, `WispConfig().workspace`, instead of the literal `"."`, so `WISP_WORKSPACE` and a `config.json` `workspace` mean the same to both. |
| **what the operator sees** | `wisp plan`, `wisp progress`, `wisp plan list` and `wisp plan abort` show and act on the plan the agent wrote for this workspace, under any spelling of it. |
| **rotation scope** | **per workspace**: the ten newest plans of each workspace (by resolved key) are kept. Planning in one project no longer deletes another's. The store's size bound becomes ten plans × workspaces used (each about 1.3 KB measured). |
| **old plans** | stay readable, because the stored key is resolved **when read**, not only when written. An absolute key resolves to itself. A relative key written under `WISP_WORKSPACE=.` resolves against the **reader's** cwd, which is exactly how the `"."` query matched it before. No file is rewritten, and a plan is re-keyed only when it is next saved. |
| **reversal trigger** | (1) a workspace identity that is not a directory path (a remote or containerized workspace whose path means nothing locally); the resolver is then the one place to change. (2) A measured store growth problem under per-workspace rotation (many workspaces), which would need an age bound, not a return to the global count. |

**An ADR is appended (ADR-0064).** A repository cannot carry the key: it comes from the operator's cwd
or setting. But the decision changes **what a resumed session reads** through the model's own tools.
A plan that global rotation would have deleted survives, and a plan written under another spelling
of the same directory is found. It also decides the store's identity and lifetime, which ADR-0063 R3
explicitly left undecided. That is persisted state the model reads on every plan update, so the
corpus's line applies.

### Findings recorded, not fixed

1. **The task parser truncates a description at its first hyphen.** `parse_plan_from_text`'s
   separator alternation includes `\s*-\s*` (a hyphen with *optional* spaces). Driven:
   - `"Add --json — deps: 1"` → `"Add"`
   - `"Use foo-bar module"` → `"Use foo"`
   - `"Fix a - b bug — files: x.py"` → `"Fix a"`
   Seen in every CLI output above (*"○ 2. [medium] Add"*), and in what the model is shown back
   under ADR-0063. The model's own plan text is silently cut.
2. **A plan still never finishes** (`PHASE_F47_PLANSTORE.md` §2.5, finding 2): unchanged by this
   decision.
3. **A legacy `"."`-keyed plan is found from any directory.** That was today's behaviour. Resolving the
   stored key at read time preserves it for such plans until they are next saved (they are then keyed
   absolute). It is recorded, not migrated, because migrating needs the writer's cwd, which was never
   stored.

---

## §3 — Deliverable 2: ADR-0064 applied

### The change

| file | change |
|---|---|
| `wisp/planner.py` | **`workspace_key(workspace)`**: `str(Path(ws).expanduser().resolve())`, `""` for `""`, and `os.path.abspath` if resolution raises. `PlanStore` applies it: **`save`** keys `Plan.workspace`; **`load_active`** compares the resolved query with each resolved stored key; **`list_all(workspace=None)`** gains the optional filter; **`_rotate`** groups by resolved key and keeps the ten newest per group (an unreadable file is its own group, never rotated by another workspace's plans). |
| `wisp/progress.py` | `list_plans` passes its workspace to `list_all` instead of comparing raw strings itself, so the store is the one place a key is compared. |
| `wisp/__main__.py` | `cmd_plan` (show, `list`, `abort`) and `cmd_progress` read **`WispConfig().workspace`**, the agent session's own source, instead of `"."`. |
| `tests/test_main_cli.py` | `test_list_no_plan` now also patches `wisp.progress.PlanStore`, so it no longer reads the operator's real store (§1). |

The tools (`wisp/tools/plan.py`) are **unchanged**. They pass their workspace to the store, and the
store resolves it. No new flag: the concern's switch is the store's key function.

### The guard — `tests/test_plan_cli_sees_agent_plan.py`

| test | holds |
|---|---|
| `test_plan_progress_and_list_show_it` | agent write (production tool path) → `python -m wisp plan` / `progress` / `plan list` in the workspace show the goal. It first asserts the stored key is the agent's workspace, **not `"."`**: the named reason. |
| `test_plan_abort_aborts_it` | `wisp plan abort` sets the agent's plan to `aborted` **on disk** |
| `test_every_spelling_is_one_workspace[link, trailing-slash, dot]` | the agent writes under each spelling, and `wisp plan` in the real directory finds it |
| `test_ten_plans_elsewhere_leave_this_one` | 11 files on disk, and `wisp plan` still shows this workspace's plan |
| `test_the_eleventh_plan_here_still_rotates_the_oldest_here` | rotation still bounds a workspace: 10 remain, the first is gone |
| `test_a_legacy_key_is_found_and_not_rewritten[".", "<project>/"]` | a file in the old scheme is found, and **its bytes are unchanged** by being read |
| `test_an_empty_store_says_so` | no plan → the same messages as before |
| `test_no_reader_is_passed_a_literal_dot` | AST: no `load_active` / `list_plans` call in `wisp/` passes `"."`, with a floor of ≥3 readers found |

- **Observation point** (F96): the real console entry point against a real store in a private `HOME`.
- **Floor** (F81): the agent's file exists before any CLI assertion.
- **F92:** the tests assert the goal the operator reads and the status on disk, not formatting.
- A child's environment never reaches an assertion message. The first cut asserted on the `_py(…)`
  call itself, so pytest's introspection printed the child's environment into the failure report.
  The values were placeholders here, but that is a secrets path, and it was fixed before GREEN.

### Verification — the sets

| check | result |
|---|---|
| RED | **7 failed, 4 passed**, and every failure is ADR-0064's reason: the CLI's `"."` missed the agent's key (4); `abort` left the plan `active`; global rotation (`10 == 11`); the AST named `__main__.py:693, 702, 707, 733`. The 4 that passed hold already: the `"."` spelling, rotation within a workspace, a `"."` legacy key, the empty store. |
| GREEN | **12 passed**; with `test_plan_shown_as_tool_output`, `test_main_cli`, `test_planner`, `test_progress`: **104 passed** |
| **behaviour differential** | 4 spellings × (12 plan-tool steps + 3 CLI commands), on a clean worktree of `f251ed4` and on this change. **Tool results: identical 12/12 in every case.** **Store:** identical 12/12 by default; under `"."`, `"<project>/"` and a symlink the 10 steps with a plan differ **only in the `workspace` field** (`"."` / `"<tmp>/project/"` / `"<tmp>/link"` → `"<tmp>/project"`), identical with that field masked, 12/12. **CLI:** byte-identical under `"."`, and in the three other cases *"No active plan."* → *"Plan: g"* and *"No plans found."* → the plan's row. |
| **test differential** | 10 committed files (`test_main_cli`, `test_main_entry`, `test_planner`, `test_progress`, `test_integration`, `test_tools_registry`, `test_permission_mode`, `test_toolchain_e2e`, `test_prompt_section_trust`, `test_plan_shown_as_tool_output`), each side verified present before running, one pytest per side with `--basetemp`: **291 passed / 291 passed**, no failures either side |
| **probes** | (1) rotation back to one global group → **CAUGHT** (1 failed). (2) `workspace_key` returns its input → **CAUGHT** (5 failed). (3) `cmd_progress` back to `load_active(".")` → **CAUGHT** (1 failed, the AST test). Probe 3 is behaviourally silent when cwd *is* the workspace, because the store now resolves `"."` to the cwd; it diverges only when `WISP_WORKSPACE` points elsewhere, which is the single source R2 fixes. All files restored **byte-identical**, `__pycache__` purged. |
| `ruff` | `wisp/planner.py`, `wisp/progress.py`, the guard: clean |

### The records

- `WISP_MIGRATION_STATUS.md:168`: the F47 row gains the operator's side (ADR-0064), with its guard
  and report.
- `CURRENT_FINDINGS.md`: F47's §Findings *"Findings whose scope a later landing extended"* entry.
  **No `F`-number was coined** (the register is total over F1–F104). The parser truncation is
  recorded in §2 of this report, not numbered.
- `CONTEXT.md`: §0.0.23, the phase table, §3 (backfills `3c573de`, `f251ed4`) and the `HEAD` line;
  38 §12 pins mapped through the diff. §13 and the ADR range landed with deliverable 1 (R7).
- The ADR log and its index gained ADR-0064 with deliverable 1.
