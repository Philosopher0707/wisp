# Workspace Isolation & ChangeSets Report (Phase 12)

## 1. Before / after

Before: parallel workers shared the canonical workspace (or single-patch
git flows); no ChangeSet, conflict, atomic-apply, or recovery model.
After: snapshot → isolated workers (worktrees or bounded copies) →
ChangeSets → deterministic classify/merge → atomic apply with journal
recovery → post-merge verification, all through the existing graph runtime.

## 2–3. Snapshot + ChangeSet models

`wisp/workspace.py`: `WorkspaceSnapshot{id,root,files{rel→sha256},rev}` —
deterministic id over sorted entries; symlinks untracked; explicit lists
strict, auto-scan lenient. `Change{path,op,base_hash,content|artifact}`
(canonicalized at construction; 1 MB/file). `ChangeSet` frozen,
content-addressed id over base+producer+changes; producer keys restricted
to run_id/node_id/worker (model fields stripped). Caps: 100 files.
Artifacts reused for oversized payloads (no second store).

## 4. ChangeSet ≠ artifact/approval

Artifacts carry bytes; ChangeSets propose mutation; neither grants
authority or approval (approval stays `decision is True` on existing
channels; merge output carries no approval field — tested).

## 5–6. Isolation + path security

Git repos: existing `WorktreeManager` + `worktree_isolated` contracts
(runner passes the flag, surfaces `worktree_patch`). Elsewhere: bounded
temp copies (≤200 files/worker) via `prepare_isolation`, wired through an
`isolated_workspace` input convention (contained, no executor change).
Containment mirrors `_resolve_path` (realpath, no absolute/symlink/NUL/
control chars); copies read-only diffed; temp cleanup prefix-guarded.

## 7–9. Conflicts

NO_CONFLICT / SAME_RESULT / TEXT_CONFLICT / DELETE_MODIFY /
RENAME_CONFLICT / CREATE_COLLISION / STALE_BASE (+INVALID), pairwise,
LLM-free. Symbol refinement via `code_index` (derived spans; ambiguous →
conservative conflict). Same-file disjoint symbols merge; same symbol or
unknown → conflict.

## 10–11. Merge

`merge_changesets`: id-ordered, same-result dedup, any destructive overlap
→ CONFLICT (never last-writer-wins), stale/invalid short-circuit. Repair
lane returns non-conflicting changes for bounded single-round repair.

## 12–14. Graph + verification

Template `parallel-implement-merge`: setup → isolated impls → MERGE router
(merged→test→final; conflict→repair→merge2→test2/final2; else fail node
raising honest FAILURE). Router conditions name target nodes (executor
`taken` = resolved target — pinned behavior). Post-merge tests run against
merged canonical; pre-merge tests prove nothing about the merge.

## 15–17. Atomicity, rollback, recovery

Journal (plan + pre-images + done, fsynced) → idempotent commit
(os.replace per file) → completed mark. Crash: recover() completes forward
(pre-commit journals provably untouched → dropped). Rollback restores
journal pre-images, refusing on drift or missing records. External edits
anywhere in scope refuse the apply — never overwrite.

## 18–20. Concurrency, external changes, provenance

Workers concurrent; canonical apply serialized by the merge node (single
writer per run). Producer identity host-assigned; merge decisions visible
in route events + node results (no new audit taxonomy: outcome classes map
to existing run/route events; tamper/denial paths already audited).

## 21–22. Approval + artifacts

No changeset approval field; operator graph-run authority + checkpoints +
journal. Large payloads via existing ArtifactStore at runtime only.

## 23–25. REPL + conflicts + repair

Progress lines exist; conflict details surface in run summary; repair is
one bounded round then honest failure (no conflict→repair loops).

## 26–28. Tests

93 workspace/security tests (paths, forgery, merge attacks, authority,
fuzz, secrets) + 42 workspace unit/property/failure-injection tests +
5 template e2e (disjoint apply, conflict report, repair path pending
manual-model verification). Full suite **1140 green**, ruff clean.

## 29. Performance

Snapshot 1/9/45 ms (10/200/1000 files); isolate 2/29/145 ms; 8-way
classify+merge 0.1 ms; apply 8 ms; 5k-line conflict 7 ms.

## 30–31. Compatibility + gates

Single-agent path untouched (no isolation cost without parallel mutation).
All gates green (implementation order preserved in commits below).

## 32. Limitations / deferred

Per-file ToolExecutor authorization of merge-apply (operator-run authority
+ checkpoints instead); git-patch (worktree_patch) ingestion into
ChangeSets; multi-round repair; journal pruning automation (`prune_journals`
provided, unwired); EXPIRED proposal reaping (prior phase).

## Invariants

Parallel cognition → isolated mutation → explicit ChangeSets →
deterministic conflicts → deterministic merge → atomic apply → post-merge
verification. Canonical workspace is never a shared scratchpad.

PHASE 12 — COMPLETE
