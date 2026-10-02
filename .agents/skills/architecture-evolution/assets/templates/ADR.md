# ADR-<NNNN> — <the decision, in one line, as a claim>

**Status:** proposed / accepted / superseded
**Phase:** <phase>
**Amends / supersedes:** `<ADR ids, or "nothing">`

> One decision per record. If this record answers two independent questions,
> split it — a record that decides two things cannot be reversed for one of them.

## Context

What the system does today, with `file:line` evidence. What is undecided. What
the evidence contradicts. Cite the recon artifact rather than restating it.

## Decision

**One sentence, as a claim.** Then the clauses, numbered, each with its basis.

1. `<clause>` — basis: `<evidence>`
2. `<clause>` — basis: `<evidence>`

## Authority

| Mechanism | Role | Authority over | Cannot decide |
|---|---|---|---|
| | | | |

## Precedence

| # | Condition | Result | Basis |
|---|---|---|---|
| | | | |

## Conflict matrix

| Condition A | Condition B | Required decision |
|---|---|---|
| | | |

## Consequences

- `<what becomes true that was not>`
- `<what becomes impossible that was possible>`

## Rejected alternatives

| Alternative | Why rejected |
|---|---|
| | |

## Implementation boundary

**May change:** `<specific>`
**Must not touch:** `<specific>`

## Non-goals

`<explicit>`

## Reversal condition

Reversed if `<evidence>`. Rollback: `<switch, and what it restores>`.

---

## Rules for this record

- **Do not restate the code.** Cite it.
- **Do not describe intent as behaviour.** If it is not wired, say "not wired".
- **Every claim carries evidence** — a `file:line`, a command, or a test.
- **Name the reversal condition.** Without one the decision cannot be revisited
  on evidence, and revisiting decisions on evidence is how defects are found.
- **Append to the repository's existing decision log.** If one exists, do not
  create a second — that is a duplicated authority in the records.
