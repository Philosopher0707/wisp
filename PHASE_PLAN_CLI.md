# PHASE_PLAN_CLI.md — the plan the operator cannot see

**Mission:** `PHASE_F47_PLANSTORE.md` §2's first recorded finding: *"`wisp plan`, `wisp progress`
and `wisp plan list` never see an agent's plan."*
**Baseline:** branch `plan-cli`, on `f47-planstore` (`3c573de`). `main` has not received the last
four landings. The tree carries the user's WIP from `CONTEXT.md` §8, untouched.
**Decision:** ADR-0064.

---

## §1 — In one page

*Written when deliverable 2 lands (§3).*

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
