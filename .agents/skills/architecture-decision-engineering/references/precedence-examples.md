# Precedence and conflict-matrix shapes

Worked, generic examples for `architecture-decision-engineering`.

## Why ordered states beat a score

A score (`severity=7`) hides the rule. You cannot test it against a case, you
cannot explain it to a reviewer, and two implementers will produce different
orderings from the same numbers. An ordered table is directly testable:

```python
assert arbitrate(success=True, stalled=True) is Outcome.STALLED   # row 4 over row 6
```

## Shape 1 — a total order over outcomes

```
row 0  already-recorded terminal state   -> frozen, never rewritten
row 1  operator cancellation             -> CANCELLED
row 2  escalation required               -> ESCALATED
row 3  hard failure (explicit or fatal)  -> FAILED
row 4  progress/stall condition          -> STALLED
row 5  inconclusive / incomplete         -> UNVERIFIED
row 6  all success conditions hold       -> SUCCEEDED
```

Properties to state, not assume:

- **first match wins**, and the final `return` is reachable (the function is
  total — no input falls through);
- **frozen** — row 0 means a terminal state cannot be rewritten by a later
  observation, a duplicate record, or a replay;
- **reachability** — for each row, which inputs make it reachable. A row that can
  never fire is a gap, not a safeguard.

## Shape 2 — two questions, two authorities

The hardest case is when two mechanisms look like competitors but answer
different questions. Test it: can you write both answers for the same input
without contradiction?

```
mechanism A answers  "is the work complete?"
mechanism B answers  "what should happen next?"
```

If yes, they are **not** competing — they are two tracks. Then the decision is:

- which is evaluated **first** (and therefore cannot be affected by the other);
- that neither may answer the other's question;
- what each is explicitly **unable** to do (usually: B cannot declare completion,
  A cannot schedule recovery).

A single component may feed **both** tracks with different outputs. That is
usually the cleanest reading, and it dissolves the apparent conflict.

## Shape 3 — the conflict matrix

Make it total. No blank cells.

| Condition A | Condition B | Required decision | Basis |
|---|---|---|---|
| success | stall | the stall row wins | row 4 > row 6 |
| hard failure | success evidence arrives later | failure stands | terminal evidence cannot override an explicit failure |
| inconclusive | success evidence | unverified | never silently a pass |
| unknown / not evaluated | success | not blocking; falls through | unknown is not a verdict |
| cancellation | any recovery request | cancelled | row 1; recovery cannot override |
| escalation required | hard failure | escalated | a request for human authority outranks failure |
| timeout | recovery request | failed, reason=timeout | recovery governs the **next** step only |
| budget exhausted | recovery request | failed, reason=budget | same |

If a cell cannot be filled with the states you have, that is the finding: either
add the state, or record that the architecture cannot represent the case.

## Shape 4 — three ways to handle "unknown"

Decide explicitly, because the wrong choice is a silent bug:

| Choice | Meaning | Risk |
|---|---|---|
| **non-blocking** | unknown does not prevent success, and is not converted into success | if a *missing* signal is treated as unknown, absence looks like progress |
| **blocking** | unknown prevents success until resolved | a broken producer becomes a stuck run |
| **orthogonal** | unknown is recorded and does not participate in the decision | the decision cannot explain itself |

Then enforce the invariant that matters most: **unknown must never become a
pass.** "Not evaluated" and "evaluated and fine" must be distinguishable in the
durable record, or replay cannot tell them apart.

## Shape 5 — bounded intervention vs veto

When a mechanism may delay an outcome, decide whether it **delays** or **vetoes**:

| | Delay | Veto |
|---|---|---|
| What happens | withhold for a bounded number of attempts, then surrender | refuse until the condition resolves |
| Terminal outcome | clean success, with the condition recorded | harder failure, or a silent end |
| Requirement | a bound, and a surrender that is not itself a failure | a guarantee the condition can resolve |
| Failure mode | wasted attempts (bounded) | a stuck run, or a false failure |

Test the veto for reachability before choosing it: if the condition **cannot**
resolve within the unit, a veto converts it into a failure or an endless loop.
Prefer delay unless you can prove resolution is possible.

## Shape 6 — rejection criteria

Reject an alternative in writing. Always include these two, because they are the
ones that get re-proposed:

| Alternative | Typical reason to reject |
|---|---|
| do nothing | the gap is live and observable |
| the simplest thing that could work | it answers a different question, or creates a second authority |
| a unified normalizer for all signals | it creates a new vocabulary and a second place that decides what an outcome means |
| a score / severity ranking | untestable, unexplained, and hides the rule |
| let a downstream stage imply an upstream one | "retrying" becomes indistinguishable from "succeeded" |
