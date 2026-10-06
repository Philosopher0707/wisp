---
name: wisp-merge-pr
description: Merge a wisp PR safely (checkpoint tags, merge commit, refresh other PRs, update the global wisp). Use only when the user has said to merge.
---

# wisp-merge-pr

Merge only when the user says to, and only after green CI on the PR head. Workers never merge on their own.

1. `gh pr view N --json mergeStateStatus,statusCheckRollup`: every check completed and SUCCESS. A docs-only change means CI
   did not run, so say that instead of "green".
2. Tag both states and push only those tags (never `git push --tags`):
   `git tag checkpoint/<date>/main-pre-prN origin/main; git tag checkpoint/<date>/prN-green <head sha>;`
   `git push origin refs/tags/checkpoint/<date>/main-pre-prN refs/tags/checkpoint/<date>/prN-green`
3. `gh pr merge N --merge`. Prefer merge commits (squash replaced the commit the generated pages stamp; the stamp is now the
   merge-base with origin/main so any style is safe, but merge commits keep history). Never delete the remote branch.
4. Every other open PR touches the derived pages: in its own worktree `git merge origin/main`, take main's
   `register.md CURRENT_FLAGS.md CURRENT_AUTHORITIES.md`, keep both sides of `AGENTS_LEARNING.md`, regenerate the pages, run the
   pin tests, push, and wait for CI again before merging it.
5. Update the global command: `git -C ~/dev/wisp-global pull --ff-only` (a dedicated worktree on `global-main`; never check
   another branch out in it; `~/dev/wisp-main` belongs to another session). A running REPL keeps old code: relaunch.
