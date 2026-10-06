---
name: wisp-check-state
description: Audit the real state of wisp, the fleet and the machine before reporting done. Use at the end of a task or when asked what is the current state.
---

# wisp-check-state

Check state; do not recall it. An audit once found user merges, a red main and a taken-over worktree that no note mentioned.

- Harness probes (read-only, hermetic): `wisp doctor` (REPL `/doctor harness`; `/doctor` alone is the 7-check preflight,
  `/doctor deep` is the Docker image check). Exit 1 on a failure; warnings are advice.
- Fleet: `wisp fleet status`, `wisp fleet doctor --strict`, `wisp fleet ci`. Manifest: `wisp.fleet.toml`.
- Offer surface: `wisp tools` (what each role and mode is really offered).
- PRs and CI: `gh pr list --state all --limit 40 --json number,state,mergedAt,mergedBy`; `gh run list --branch main --limit 3`.
- Worktrees: `git reflog -8 --date=iso` and `git status --short` in each worktree another session might use.
- **Before running `wisp <word>`** confirm it is a subcommand: `python -c "from wisp.__main__ import _SUBCOMMAND_NAMES as n; print('<word>' in n)"`.
  An unknown word is sent to the model as a prompt.
- Never print `~/.config/wisp/.env`, `.agent/` or `.wisp/` contents; never `tail`/`cat` shell rc files; do not rewrite an audit
  log (its historical breaks are evidence); move a SQLite WAL aside, never unlink it.
Report observed vs inferred vs unknown, and say what could not be verified.
