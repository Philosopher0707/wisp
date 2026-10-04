# VERIFICATION MATRIX — <subject>

One row per case. `Result` is `pass / fail / deferred` and a deferred row needs a
reason and a tripwire.

## STANDARD — the new behaviour

| # | Case | Expected | Evidence | Result |
|---|---|---|---|---|
| S1 | | | | |

## BOUNDARY — the edges of the bound

| # | Case | Expected | Evidence | Result |
|---|---|---|---|---|
| B1 | exactly at the limit | | | |
| B2 | one past the limit | | | |
| B3 | exactly one remaining | | | |
| B4 | the limit reached at another limit's edge | surrender is clean, not fatal | | |

## CONFLICT — every row of the conflict matrix

| # | Condition A | Condition B | Expected | Result |
|---|---|---|---|---|
| C1 | | | | |
| C2 | | | | |
| C3 | the row you expect to lose | | | |

## FAILURE — each input's failure modes

| # | Input | Mode (absent / unavailable / raises / stale / duplicated) | Expected | Result |
|---|---|---|---|---|
| F1 | | | | |
| F2 | | | | |

## REPLAY — reconstruct from durable state

| # | Case | Expected | Result |
|---|---|---|---|
| R1 | reconstruct from the record alone equals the live decision | equal | |
| R2 | restart / new process over the same store | equal | |
| R3 | missing authoritative input | **fails loud** | |
| R4 | duplicate terminal record | frozen, not rewritten | |

## CONCURRENCY — isolation

| # | Case | Expected | Result |
|---|---|---|---|
| N1 | two instances, same code path | no shared counter/state | |
| N2 | interleaved execution | each keeps its own state | |

## REGRESSION — the suite as a set

| | Value |
|---|---|
| Baseline (stable set, path) | |
| Run 1 / run 2 | |
| Union / intersection | |
| **NEW failures** | `<must be empty, or each explained>` |
| **GONE failures** | `<each explained>` |
| Method caveat | `<if the two-run intersection was not achieved, say so>` |

## Architecture preservation

| Claim | How verified | Result |
|---|---|---|
| protected authority unchanged | `<zero-diff on the file(s)>` | |
| single producer of the decision | `<AST ratchet>` | |
| no global mutable state | `<AST: local, not an attribute>` | |
| no second authority | `<the new component's imports are only the message/value type>` | |
| default path unchanged with the flag off | `<the call is byte-for-byte the old call>` | |
