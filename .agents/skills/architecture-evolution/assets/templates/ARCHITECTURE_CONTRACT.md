# ARCHITECTURE CONTRACT — <subject>

**Status:** draft / ratified · **Decision record:** `<ADR id or path>`

## 1. The questions this contract answers

Each axis as a question. An axis with no answer is a stop condition, not a detail.

| # | Question | Answer | Basis |
|---|---|---|---|
| A1 | Who may declare the outcome? | | |
| A2 | What inputs feed that authority? | | |
| A3 | What happens when inputs disagree? | | |
| A4 | What does an inconclusive / unknown input mean? | | |
| A5 | Is the decision bounded, and by what? | | |
| A6 | When is it evaluated relative to other decisions? | | |
| A7 | What is persisted, and is it replayable? | | |
| A8 | What happens when the feature is off? | | |
| A9 | What happens when an input is absent / unavailable / raises? | | |

## 2. Ownership

| Rung | Owner | Explicitly does NOT own |
|---|---|---|
| | | |

**No new authority is created.** If one is, name it and say which existing
authority it replaces or why a peer is required.

## 3. Precedence

Ordered states, highest first. **No scores, no weights, no severity ranking.**

| # | Condition | Result | Basis |
|---|---|---|---|
| 0 | already-recorded terminal state | frozen | |
| 1 | | | |
| 2 | | | |
| 3 | | | |

**Freezing:** `<mechanism that prevents a later observation rewriting a terminal state>`

**Reachability:** for each row, the inputs that make it reachable, and the ones
that make it unreachable.

| Row | Reachable when | Unreachable when | If unreachable: gap or safeguard? |
|---|---|---|---|
| | | | |

## 4. Conflict matrix

Every combination that can co-occur. **No blank cells.**

| Condition A | Condition B | Required decision | Basis |
|---|---|---|---|
| | | | |

> If a combination cannot be expressed with these states, stop and record it
> rather than writing prose around it.

## 5. Failure semantics

| Input | Absent | Unavailable | Raises | Stale | Duplicated |
|---|---|---|---|---|---|
| | | | | | |

Policy chosen: **fail-open / fail-closed**, per input, with the reason. The
absence is made visible as `<durable marker>`.

## 6. Persistence and replay

```
durable decision inputs : <list>   -> authoritative
transient state         : <list>   -> must NOT be recorded as an input
```

Invariant:

```text
reconstruct(recorded inputs) == live decision made from the same inputs
```

On unreadable or missing inputs: **fail loud**, never guess a plausible default.

## 7. Boundedness

| Bound | Maximum | Owner | Resets | At exhaustion | Cannot become |
|---|---|---|---|---|---|
| logical retry | | | | | |
| iteration | | | | | |
| time | | | | | |
| recursion | | | | | |
| concurrency | | | | | |

## 8. Rollback

| Level | Switch | Effect | Default |
|---|---|---|---|
| 1 | | | |

## 9. Reversal condition

This decision is reversed if: `<evidence that would invalidate it>`.

## 10. Rejected alternatives

| Alternative | Why rejected |
|---|---|
| do nothing | |
| the simplest thing that could work | |
| | |

## 11. Implementation boundary

**May change:** `<files, interfaces, flags>`

**Must not touch:** `<protected authorities, and why>`

## 12. Non-goals

`<explicit list — this is part of the contract>`
