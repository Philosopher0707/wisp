# AUTHORITY MAP — <subject>

The point of this table is to find the rung where ownership is missing,
duplicated, or asserted but not wired.

## The ladder

```
observation -> classification -> verdict -> routing
   -> recovery -> enforcement -> completion -> persistence
```

Fill one row per mechanism. Leave a cell `UNKNOWN` rather than guessing — an
`UNKNOWN` owner is the finding.

| Mechanism | Rung | Owner (module) | Producer | Production consumer | Persists? | Replayable? | Can be overridden by | Cannot decide |
|---|---|---|---|---|---|---|---|---|
| | observation | | | | | | | |
| | classification | | | | | | | |
| | verdict | | | | | | | |
| | routing | | | | | | | |
| | recovery | | | | | | | |
| | enforcement | | | | | | | |
| | completion | | | | | | | |
| | persistence | | | | | | | |

## Rungs with no owner

| Rung | Consequence | Blocking? |
|---|---|---|
| | | |

## Rungs with more than one owner

| Rung | Owners | Do they agree today? | Do they agree by design? | Action |
|---|---|---|---|---|
| | | | | consolidate / pin the delta / leave and document |

> Two owners that agree today are still a defect. They agree by luck, and nothing
> forces them to keep agreeing.

## Consumers: claimed vs actual

| Mechanism | Claimed consumer | Actual production consumer | Verdict |
|---|---|---|---|
| | `<doc / ledger / plan>` | `<none / file:line>` | live / **unconsumed** |

## Durable inputs

The facts a restart needs to reproduce each decision. Anything not listed here is
transient and must not be recorded as if it were an input.

| Decision | Durable inputs | Recorded today? | Required |
|---|---|---|---|
| | | | |

## Boundary summary

| Boundary | Protected by | Consulted at | Can be bypassed? |
|---|---|---|---|
| | | `file:line` | |
