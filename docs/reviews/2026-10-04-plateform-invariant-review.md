# plateform invariant review (Phase 2, ADR 2026-10-04-fleet-worker-contract)

Question: does `plateform` (13k LOC, "AgentOS Enterprise") hold invariants wisp lacks, so it can be archived
after harvesting? Method: read the security-relevant modules, then run each suspicion as a probe against temp
files (`docs/reviews/2026-10-04-probes/`). **Observed** = a probe ran. **Read** = from source, not executed.

## Result in one paragraph

Do not fold plateform in as code, and do not rely on its README. Its audit chain and approval gate fail the same
probes wisp's audit chain fails. It has two ideas worth taking (below). The more valuable result is about wisp:
**`wisp audit verify` reports `TAMPERED` on wisp's own live log, and the cause is a concurrency bug, not tampering.**

## plateform findings

| # | Claim in README | Evidence |
|---|---|---|
| P1 | "non-repudiation", tamper-evident audit | Observed: `actor_name` is not in the hash input, so a forged actor still verifies; the tail can be truncated and still verifies; an attacker who edits an entry and recomputes the chain needs no key and passes; one corrupt line makes `verify_integrity` raise instead of report. |
| P2 | "Human-in-the-Loop approval gates" | Observed: `resolve_request` has no state guard, so a REJECTED request can be re-resolved to APPROVED by a different reviewer, and "resolving" to PENDING is accepted. Read: `api/routes_hitl.py` checks `status != PENDING` first, but the CLI paths in `cli/interactive.py` do not, and the check-then-update is racy. |
| P3 | RBAC, multi-tenant | Read: `POST /approvals/{id}/decision` and `GET /approvals/{id}` take only `get_current_user`; `require_permission` is imported and unused; no `req.tenant_id == user.tenant_id` check. Any authenticated user can read or decide another tenant's approval. Not executed (needs app wiring). |
| P4 | approval authorises a specific action | Read: `modified_payload` is merged straight into resumed graph state (`resume_state.update(...)`); nothing binds what was approved to what runs. An unknown agent name silently falls back to `customer_support`. |
| P5 | sandbox | Read: the module docstring says "NOT a security boundary". That honesty is correct and rare. It also denies `str.format` attribute reach, a real bypass class. |
| P6 | guardrails (PII, injection) | Read: six regexes. Heuristic only; not evaluated here. |

Not reviewed: checkpointer integrity, rate limiter, memory layers, model-gateway budget/cache.

## wisp findings (same probes, `wisp_audit_probe.py`)

| # | Finding | Evidence |
|---|---|---|
| W1 | Two writers fork the chain. `AuditTrail.record` trusts an in-memory `_last_hash` and takes no file lock; the CLI, server and tests all append to `~/.config/wisp/audit.jsonl`. | Observed: two instances interleaving appends makes `verify()` return entry 2. **Real log: 4338 entries, 8 fork points, 14 broken links since 2026-08-29; every entry's own hash is valid (none edited). `wisp audit verify` says `TAMPERED` at entry 1848, which links back to entry 1825, not 1847.** |
| W2 | Truncation is undetectable. | Observed. |
| W3 | Unkeyed chain: a rewrite plus recomputation passes `verify()`. | Observed. |
| W4 | A false `TAMPERED` trains the operator to ignore the verifier, which hides a real one. | Inference from W1. |

What wisp does better than plateform: the hash covers every field, a corrupt line is reported rather than raised.

## What to harvest

1. **From plateform: re-read the chain head under an exclusive file lock before every append** (`AuditLogger._read_head_hash`
   under `fcntl.flock`). This is the fix for W1. It is the one plateform idea that wisp needs today.
2. **From plateform: bind an approval to a checkpoint id** so resume continues from exactly the state that was shown.
   Pair it with P4's lesson: bind the approved content by digest.
3. **From always-on-worker, not plateform: a keyed MAC on every entry, and a witness stored outside the log's
   directory** (its ADR-0005/0007/0008). Together they fix W2 and W3.
4. Keep plateform's honest sandbox docstring as the house style.

## Recommendation

- Set `plateform` to role `archive` once items 1-2 are in wisp. Nothing in it should run in production before P2-P4 are fixed.
- Fix W1 first, RED-first (a two-writer test), without rewriting the existing log: its history is a record of
  what happened, and the 14 broken links should stay as evidence of the bug.
- Decide separately whether to re-anchor the chain after the fix (a new epoch entry), so `verify` can pass again
  without erasing history.

## Status (updated 2026-10-04)

- **W1 fixed** in PR https://github.com/Philosopher0707/wisp/pull/61 (head re-read under `flock`; RED-first; 337 audit-touching
  tests pass; on a copy of the live log 200 concurrent appends added 0 broken links). Awaiting the human's merge.
- plateform is now role `archive` in `wisp.fleet.toml`.
- **Still open:** W2 (truncation) and W3 (unkeyed chain) need a keyed MAC plus an external witness; harvest item 2
  (approval bound to a checkpoint id and the approved content to a digest) is not started; the live log's 14
  historical breaks are untouched, and whether to anchor a new epoch so `verify` can pass again is a human decision.
- No plateform code was changed.
