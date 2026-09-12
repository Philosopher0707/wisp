# PHASE 13-H3 — Terminal-Event Closure (A) + Failure-Aware Final Content (C)

First remediation after H1/H2 forensics. Minimal diff: **+21/−3 in
`wisp/core/stateless.py` only**. G1B honesty kept (`done` still withheld on
incomplete rounds). TDD: 8 harness tests updated to demand closure first
(RED: 8 fail / 31 pass), then the fix (GREEN: 39/39, ruff F-clean).

## Change

The G1B bare-return branch (`_turn_inner`, no tool calls, `provider_failed`)
now yields one terminal `error_event` (`CODE_PROVIDER_STREAM`,
`recoverable=True`, round-state context) instead of returning silently.
Message is failure-aware (C): partial content → "partial response streamed
above is retained but incomplete"; thinking-only → "reasoning streamed above
is retained but incomplete"; neither → "no usable output was produced"; all
end "; no final answer was produced". Partial content/thinking were already
streamed live and persisted — the behavior defined (not dropped, not
salvaged-as-answer).

## Verification

- H2 harness updated in place: closure-last, counts (stall 1 error; diag paths
2), message variants, N-test reframed (repo-DONE = crash-recovery semantics).
- Targeted: 287 passed (reliability, core-stateless, G1 suites, forensics,
journal, security, breaker, transport-cli).
- Full suite: 5568 passed / 48 failed / 5 teardown-errors. HEAD-worktree
baseline of the same 46 runnable IDs: 44 fail identically → pre-existing.
Remaining 4: 2 in a foreign untracked file absent at HEAD (`rewind`
risk-table gap, untouched by this diff), 2 hook tests that also fail
isolated at baseline (order-dependent). **Regressions attributed: 0.**

## Process incident (disclosed)

Mid-phase I ran `git stash push` + `pop` for baseline isolation; the pop
caught a foreign pre-existing stash and conflicted 13 files, and the prod
edit was concurrently lost (hot shared tree — 36 commits ahead, foreign
stashes on other branches). Recovered without touching foreign work:
restored every foreign path to HEAD, preserved all 3 stash entries, dropped
one stale 1-line WIP (`wisp/tools/registry.py` fanout description; HEAD text
"Launch multiple specialist…" from f93630e kept over the worktree's older
"Spawn multiple specialist…" text — restorable on request), re-applied my
edit verbatim, re-verified green. Baseline isolation was redone with a
disposable HEAD worktree instead. Lesson: no stash operations on this tree;
worktrees only.

## Deferred (deliberate)

- `turn_succeeded`/repo-DONE on error-ended turns: left as crash-recovery
semantics (H4 candidate — replay-behavior risk, needs its own probe).
- Bookkeeping-set mismatch (empty-object `done(natural)`), pruner ceiling,
meter split, `max_reflections`, no-progress: unchanged, still pinned by H2.
- Candidates B/D/E/F/G: see H2 §21 order (G/E observability next).

```text
VERDICT: SHIPPED (A+C) — closure pinned, zero regressions, H4 = success-flag semantics
```
