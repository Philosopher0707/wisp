# PHASE 13-I0 — MALFORMED `disable-model-invocation` CONTRACT

## Contract
| Input (parsed value) | State | Advertisement |
|---|---|---|
| field absent | ABSENT | visible (existing default preserved) |
| boolean `true` | DISABLED | HIDDEN |
| boolean `false` | ENABLED | VISIBLE |
| anything else | INVALID | HIDDEN + diagnostic, fail closed |

## Parser facts (verified, PyYAML safe_load)
`yes/no/on/off` → bool; `1/0/2` → int; quoted → str; `null`/empty →
None; `[]`/`{}` → collections. Contract classifies parsed values with
`type(v) is bool` (never truthiness — `isinstance` would admit ints).

## Implementation
`wisp/skills.py`: `resolve_invocation_visibility()` (4 states) +
`is_model_advertisable()` + `Skill.model_invocable` (default True,
preserves duck-typed doubles); resolved once at the `parse_skill`
boundary; INVALID logs `logger.warning` with name/path/field/value
(no contents, no secrets). `wisp/extensions/skills.py tools()` skips
non-invocable skills. Explicit/human invocation (`find_skill`, REPL
`/skills`) untouched. Authorization untouched.

## Malformed cases tested (16 + adversarial)
Strings `"true" "false" 'true' 'false' "TRUE" "FALSE" "yes" "no"
"random"` (9), ints `0 1 2`, `null`, `~`, `[]`, `{}` (7) — all hidden,
diagnostic asserted. Mandatory regression: `"false"` → NOT invocable
(naive truthiness would advertise it); `"true"` → hidden, no crash.
Valid: absent → visible; `true` → hidden; `false` → visible.
Parser-coerced `yes/no` follow bool semantics (documented, not fought).

## Regression
13-H0 H0-1 inverted to the new contract (was: pins the bug). G1A–G1E
green (71). Skill suites green (121 incl. 32 new gating tests).
Stream contract green. Full suite: see 13-I report §18.

## Production files
`wisp/skills.py`, `wisp/extensions/skills.py`. No parser change, no
discovery change, no executor change.

## Remaining Phase 13 findings
Covered by 13-I-1 (this turn) except 13-I-2..6 (ledger, retry
controls, telemetry, fanout) — explicitly deferred, each needs its
own scoped phase.
