# IMPLEMENTATION PLAN — <subject>

**Contract:** `<path to the ratified contract / ADR>`

## 1. The seam

```
<owner component>
   |  owns: <the decision>
   |  passes: <read-only predicate / value — never the owning object>
   v
<receiving component>
   v
<the exact site, by file:line and by surrounding code>
```

| Property | Value |
|---|---|
| Seam type | existing boundary + parameter / new module / new subsystem |
| What crosses | |
| What must NOT cross | `<the owning object, and why>` |
| Lifetime | per call / per turn / per run |
| State introduced | `<local / none — justify any global>` |

## 2. Change list

| File | Change | Why it belongs in this phase |
|---|---|---|
| | | |

**Nothing else.** Every other file is out of scope.

## 3. Flag

| Property | Value |
|---|---|
| Name | |
| Default | **off** |
| OFF means | previous behaviour, exactly |
| ON means | |
| Read at | one site (`file:line`) |
| Rollback levels | |

## 4. Bound

| Property | Value |
|---|---|
| What is bounded | |
| Maximum | |
| Owner | |
| Resets | |
| At exhaustion | `<honest surrender / escalate — never a harder failure>` |
| Edge interaction checked | `<what happens at the limit of another bound>` |

## 5. Compatibility

| Interface | Call sites affected | Implementations affected | Mitigation |
|---|---|---|---|
| | | `<test doubles, adapters, other backends>` | `<pass only when needed>` |

> An optional parameter is optional at the call site and mandatory at the
> implementation. Name every alternative implementation of the interface.

## 6. Order of work

1. Write the safety net **RED-first**, against the unmodified implementation.
2. `<implement step>`
3. Add the tripwires.
4. Run focused tests.
5. Run the migration/relevant suite.
6. Run the full suite against the stable baseline.

## 7. Non-goals

`<copy from the contract — anything discovered that is not required>`

| Deferred item | Why it does not block | Tripwire added |
|---|---|---|
| | | |
