---
name: observe-then-enforce-rollout
description: Roll out a new harness control (rule, guard, gate) safely - security and workspace integrity enforced from day one, heuristics observe-first and flipped one rule at a time on evidence. Use when adding any control that can withhold, block or rewrite what an agent does, and when reporting its status.
agent_created: true
---

# Observe, then enforce

The owner's posture: **integrity is enforced, heuristics are earned.** Path jail, destructive commands, secrets, dependency/lockfile/CI protection: enforce from the first release, a typo means enforce. Heuristics (success-claim audit, repeat-failure nudges, affordability retry): start in `observe`, measure on baseline runs, flip each to `enforce` on its own once it is shown not to block legitimate work.

## Mechanics (as built for the reasoning core)

- One default mode (`off | observe | enforce`) plus a per-rule override (`R1=enforce,R4=observe`). Unknown rule: ignored. Unknown mode for a known rule: **observe** (a typo never makes a rule more intrusive). `off` as the default wins over every override.
- **Observe changes nothing the user can see** except a journal. Prove it by comparing whole event streams against off. Persist the journal (JSON lines to an operator-set path) or you cannot tell what fired in a live run.
- **The core can degrade, never fail a turn**: every seam is wrapped, an internal error is a `core_error` row and the turn proceeds on the existing guards.
- **Decide at the last gate, not the first**: a rule that withholds `done` runs after the floor and the other completion gates, so none changes and a turn is never nudged twice. Compute its budget there too, or a round another gate handled spends it.
- **Budgeted interventions** (withhold once, then flag and end; retry once; nudge once): a turn can always end. Prove it with a property test over random event sequences.
- **No new authority**: decisions use the existing closed vocabularies (`GoalState`, `FailureClass`, `RecoveryRung`); refuse anything else at construction.
- **One call site per seam**, pinned by an AST test, at the helper every path goes through.

## Enforcement is owed

Observe is not a destination: while a rule only observes, its failure still reaches the user. Keep an **Enforcement roadmap** table in the design doc (rule, built, applied, exit criteria, status) and a project memory entry; say "built and witnessed, running in observe" in every status report until flipped. Exit criteria are evidence, not time: measured false-positive rate on real transcripts, the first ~10 live tasks showing nothing legitimate would have been blocked, a persona row that moves.

## Report honestly

State what is unmeasured (real-model rates, user annoyance), what is decision-only (R2/R3), and what is not designed. Changing a default is the owner's decision: give the trade-off and a recommendation.

See `fault-injection-personas` (the baseline), `mutation-probe` (the proof), `live-model-paired-runs` (the evidence).
