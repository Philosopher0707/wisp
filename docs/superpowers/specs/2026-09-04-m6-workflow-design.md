# M6 Design: Enterprise CLI Workflow (rev 1)

Date: 2026-09-04. Parent: target arch §5. Gate green after every commit.

## 0. Ground truth

- `Plan`/`Task` with to_dict/from_dict (`wisp/planner.py:66,102`).
- `WorktreeManager` create/apply_patch/get_patch/cleanup/reap_orphans
  (`wisp/multi_agent/_worktree_manager.py`).
- `RunStore` + transitions + idempotency (M3); `reversibility` (M3);
  `authorize`/`dry_run`/`explain_denial` (M2/M4); `TOOL_RISK_TABLE` (core).
- `--output-format json|stream-json` precedent; exit codes 0/1/2 in
  policy/trace CLIs; `cmd_task` currently routes to `trace.cli.task_main`.

## 1. New package `wisp/task/`

| Module | Contents |
|---|---|
| `manager.py` | `TaskManager` over `RunStore`: start (creates run + plan stub, returns task id = run id), list, inspect (run + transitions + plan), pause/resume/cancel (legal transitions only), attach_plan |
| `review.py` | Pure review render: proposed files, tools + risk classes, policy obligations (via `dry_run` per action), cost budget, secret/data warnings (`scan_for_secrets`), command previews, rollback instructions (`rollback_preview`), approval capture (`approve_scope`: whole plan or per action-class → recorded decision dict) |
| `profiles.py` | 5 profiles as config-override dicts + `apply_profile(config, name)`: personal, enterprise-managed, offline-secure, read-only-review, ci-headless |
| `cli.py` | `wisp task start/list/inspect/resume/cancel/export-evidence/approve-plan` (+ `--json` global flag); `trace.cli.task_main` becomes a delegating alias (kept for compat, test stays green) |

## 2. Execution semantics (explicitly thin)

- `task start` without a model turn only registers + plans; execution
  reuses the existing turn engine / background manager (no rewiring).
- Plan-approved work executes in a worktree by default: `review.py`
  exposes `provision_worktree(workspace, name)` wrapping
  `WorktreeManager.create`; cleanup via existing `cleanup`/`reap_orphans`.
- Headless/CI is safer by default: `ci-headless` profile forces approval
  matrix deny on exec + network off (mirrors expired-trim posture).

## 3. Output contract

- `--json` emits `{"ok": bool, "data": ..., "error": ...}` envelope;
  exit codes: 0 ok, 1 denied/failed/no-result, 2 usage.
- `wisp completion bash|zsh`: static script, golden-tested.

## 4. Tests

`test_task_manager.py` (lifecycle over tmp store incl. illegal resume),
`test_task_review.py` (golden review incl. warnings + obligations),
`test_task_profiles.py` (each profile's overrides + CI safest),
`test_task_cli.py` (goldens + --json envelope + exit codes + completion).

## 5. Deferred

Interactive TUI plan browser; `task export-evidence` already shipped (M5);
server-side task routes (M6 is CLI-first by spec).
