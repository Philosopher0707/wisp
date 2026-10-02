---
name: issue-remediation-loop
description: Resolve a filed GitHub issue end-to-end via TDD — reproduce the failure, write a failing test, implement the minimal fix, verify with targeted suites plus full-suite stash-comparison, then commit, push, and close the issue with evidence. Use when the user says "fix issue #N", "resolve this issue", "implement this follow-up", or points at an audit finding with exit criteria. Trigger on phrases like "fix the issue", "close out #N", "TDD this bug", or "ship the fix".
---

# Issue Remediation Loop

A repeatable loop distilled from resolving audit issues end-to-end: **read → reproduce → red test → minimal fix → layered verification → commit → push → close with evidence**. Every step produces evidence; no step runs on assumption.

## Parameters

| Parameter | Meaning | Example |
|---|---|---|
| `<REPO>` | Absolute repo root | `$(git rev-parse --show-toplevel)` |
| `<N>` | GitHub issue number | `6` |
| `<BASE>` | Integration branch | `main` |

## Step 0 — Baseline the repo state (before touching anything)

```bash
git status --short          # NEVER pipe through head — truncated output hides files
git log --oneline -5
git stash list              # know what exists; NEVER pop blindly (see Gotcha G3)
git worktree list
```

Record: dirty files (yours vs others'), untracked files (yours vs foreign), existing stashes. **Foreign untracked files, foreign stashes, and foreign branches are read-only.** If the tree is dirty with others' work, stop and ask — do not stash over it.

## Step 1 — Read the issue and trace the code

1. `gh issue view <N> --json title,body,state` — extract the **exit criteria** verbatim; they are the acceptance test.
2. Locate the implementation: read the cited files/lines, then trace **callers, callees, and existing tests** (`grep -rn <symbol>`).
3. Determine the precise failure mechanism in your own words before writing any test. If you cannot explain the exact sequence (e.g. *which event arrives out of order and where it gets mis-paired*), keep investigating.

## Step 2 — Reproduce the failure

Prefer a failing **test** over a script; prefer a script over reasoning. Confirm whether the failure is live on the current tree. If a full-suite failure is suspected pre-existing, prove it with stash-comparison (Step 5).

## Step 3 — Write the RED test first

- Mirror repo conventions: same test file as the covered behavior, same fixtures/helpers (`_flat_call`-style builders, `monkeypatch`, `tmp_path`), explicit `@pytest.mark.asyncio` where the repo uses strict asyncio mode.
- **Anti-vacuous assertions**: after writing the test, ask "could this pass while testing nothing?" and add a guard (e.g. assert the triggering log/event actually fired, not just the absence of the bug).
- Place new tests inside the correct class (appended heredocs land in the *last* class — verify with `--collect-only` or a targeted run).
- Run it: it MUST fail for the right reason (read the assertion output, not just the count).

## Step 4 — Implement the minimal fix

- Smallest change that eliminates the root cause **as the issue prescribes** (e.g. "pair by id instead of positionally" — do that, not a larger redesign of neighboring layers).
- Preserve contracts: public APIs, result shapes, error semantics, config defaults. When the fix touches shared helpers, enumerate every caller first.
- After automated fixes (`ruff --fix` etc.): review **every hunk** — automated tools have known unsound corners (see Gotcha G4). Never `git add` a batch you haven't diffed.

## Step 5 — Verify in layers

1. **Targeted**: new tests + the file's suite. Must be green.
2. **Neighbors**: suites covering touched modules and their callers.
3. **Lint/type**: `ruff check` on every touched file; typecheck if the repo gates it.
4. **Full suite** (minus documented live/e2e exclusions). For EVERY failure:
   - Reproduce in isolation. If it passes solo → order-dependence; bisect by halves (see Gotcha G7).
   - **Stash-compare**: run the exact failing subset on pristine HEAD. Identical failures = pre-existing, not yours. Different = stop and investigate (see diagnosis rules below).
   - Never weaken a test to green. If test and code disagree, determine which side holds the contract via docs, consumers, and git history (`git log -S <string>`), then change exactly one side.

### Failure-diagnosis rules

- **Failure only in full suite, passes in isolation**: suspect global-state pollution — module-global monkeypatching without uninstall (e.g. an `install_*()` that patches tool globals), logging filters/handlers installed without teardown, leaked threads/pools. Bisect: halves → quarters → single file → single test, always appending the victim test file.
- **DID NOT RAISE on timeouts / empty captured logs**: suspect the code under test was swapped globally (verify function identity: `fn.__code__.co_filename`), or a global patch changed timeout/logging behavior.
- **NameError/ImportError after automated fixes**: the fixer may have removed a used binding (dual-binding trap, G4). Restore the binding; keep the legitimate removals.
- **Flaky timing asserts**: generous bounds, short-lived orphans (`sleep 2–3`, never bare `Event().wait()`), `shutdown(wait=False)` in `finally`.

## Step 6 — Hostile diff review

`git diff` every hunk as an adversary: unrelated changes? dropped lines (check `url = ...`-style accidents — one deleted line can hide in a large edit)? behavior shifts beyond the issue scope? duplicated logic that belongs in the shared helper? Fix problems immediately, re-verify.

## Step 7 — Commit, push, close

- Stage **explicit paths only** (`git add <paths...>`), never `git add -A` — re-verify `git status` shows no foreign files staged.
- One commit per issue when fixing several; conventional message (`fix(audit): <what> (GH#<N>)`) with a body covering mechanism + verification + deferred items.
- Push only when the user has authorized pushing (establish push mode once, then reuse).
- Close with evidence: `gh issue close <N> --comment "<what changed> (<SHA>). Verified: <suites + counts>. <residuals>"`.
- Verify post-push state: `git status -sb` in sync, issue state CLOSED.

## Verification checklist (all must hold)

- [ ] New tests failed before the fix for the asserted reason, pass after.
- [ ] Neighboring + full suites show zero *new* failures (stash-compared).
- [ ] Lint/type gates clean on every touched file.
- [ ] Diff contains only intended changes; no foreign files staged/committed.
- [ ] Issue closed with SHA + test evidence; push confirmed in sync.

## Common Gotchas / Pitfalls (earned the hard way)

- **G1 — Truncated status lies.** `git status | head` hides modified files. Always read the full status before staging or diagnosing.
- **G2 — Heredoc test placement.** `cat >>` appends land in whatever class is last; test IDs then mislead (`TestX::test_y` running under `TestZ`). Confirm placement, relocate, keep IDs truthful.
- **G3 — Blind `git stash pop`.** The stash stack may hold *other sessions'* entries; popping applies the top one whatever it is. Always `git stash list` first; prefer explicit pathspec stashes, or better, a pristine **worktree** for baseline comparisons (zero stack interaction).
- **G4 — Linter autofix unsoundness.** `ruff --fix` can delete a *used* binding when two scopes bind the same name (module-level + function-local imports of one symbol). Symptom: `NameError` in a file the fixer touched. Cure: diff every hunk, run every touched file's tests; restore the binding, keep the legitimate removals.
- **G5 — Wrong-culprit test assumptions.** Read the helper under test before asserting (`_parse_int` clamps to min, not default). A red test that encodes your wrong assumption wastes a cycle — verify primitives first.
- **G6 — Pinning mechanism instead of behavior.** Tests that mock `asyncio.to_thread` break legitimately when dispatch moves to a pool. When behavior intentionally changes, update mechanism-pinning tests to the new seam (keep asserting routing + names, add the new guarantee).
- **G7 — Order-dependent suite failures.** Bisect deterministically: halves → quarters → file, always with the victim appended. Prime suspects: global monkeypatches without teardown, logging install/filter leaks, leaked thread pools. Prove the polluter with a minimal pair before fixing.
- **G8 — Silent-harness pitfalls.** Long commands piped through `grep/head` may return empty output; redirect to a file and read it separately. `timeout`/`gtimeout` may not exist — use the tool's timeout parameter. Bare `===` breaks zsh — quote separators. Syntax-check heredoc appends immediately (`--collect-only`).
- **G9 — Vacuous green.** A test can pass while exercising nothing (e.g. input shape the code ignores, mock swallowing the call). Pair every absence-assertion with a presence-assertion proving the trigger fired.
- **G10 — Assertion drift.** When test and code disagree on strings/semantics ("Blocked" vs "Denied", `CancelledError` vs verdict exception), the test is not automatically right. Decide by: in-code documentation, consumer handling, and `git log -S` intent history — then change exactly one side.
- **G11 — Global-patch features.** Any `install_*()` that monkeypatches module globals needs a paired `uninstall_*()` wired into shutdown, plus tests pinning both directions — otherwise it silently redefines behavior for the whole process (and every later test).
- **G12 — Orphan cost in tests.** Background threads can't be killed; keep test orphans short-lived (`sleep 2–3`) with `shutdown(wait=False)` in `finally`, or the suite pays at interpreter exit.

## Red lines (never)

- Never modify, stage, commit, or delete another session's files, stashes, or branches.
- Never push without explicit push authorization for the session.
- Never weaken a test (looser asserts, deleted cases, added skips) to make red green — fix the code or prove the test wrong per G10.
- Never close an issue without pasting the commit SHA and verification evidence in the close comment.
- Never present a full-suite failure as "pre-existing" without a stash-compared run proving it.
