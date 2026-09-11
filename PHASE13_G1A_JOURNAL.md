# PHASE 13 G1A — TRANSACTIONAL WORKSPACE JOURNAL & RECOVERY

## Problem
G0-NEW-2: per-op journal used truncate/write. SIGKILL mid-write → torn
JSON → `recover()` deleted it as corrupt and reported `"rolled-back:1"`
while 2/60 files stayed applied and no rollback occurred. Future recover
saw clean. Captured pre-fix: `landed=2/60 journals_left=0`,
`recover->rolled-back:1` with residue flag.

## Root Cause
`_write_journal` opened the live path with `"w"` (truncate-in-place).
Crash windows B–E all collapse onto one torn file, and recovery
conflated CORRUPT with ABSENT (delete + clean/rolled-back). The
"always before the first replace" docstring was false once per-op
re-journals (after replaces) existed. Bonus gaps found by the new
battery: `KeyError` crash on content-less items, `UnicodeDecodeError`
crash on undecodable bytes (both escaped `recover()`).

## Design
- Publish: temp-in-same-dir → flush → `os.fsync` → `os.replace` →
  best-effort dir fsync (`_fsync_dir`, explicit weaker-guarantee note).
  Readers see old-complete or new-complete, never torn.
- Read: `_read_journal` → ABSENT / VALID / CORRUPT + fail-closed schema
  (`_journal_schema_error`: types, OPS, `_norm_rel` traversal guard,
  content-required for CREATE/MODIFY).
- Corrupt: `_quarantine_journal` (sanitized `<stem>.corrupt-<ns>-<pid>.json`,
  pinned in jdir, `os.replace`, dir fsync) — never deleted, never
  overwritten; prune excludes evidence.
- Dispositions: `clean` | `completed:N` | `blocked:N:<reason>`.
  `"rolled-back"` removed (recover performs no rollback; the word was a
  lie). Forward-commit failure also quarantines + blocks (no propagation).
- Defense in depth: `_op_applied`/`_apply_op` use `.get()` + ValueError
  (converts to blocked/IO, never KeyError crash); `rollback_changeset`
  routes through `_read_journal` (corrupt → "recover first").

## Recovery State Machine
Before: NO JOURNAL → PUBLISHED → MUTATING → COMPLETE → COMPLETION →
REMOVED, with the killer shortcut MUTATING → TORN → DISCARDED → PARTIAL
+ CLEAN-LIE. After: publish is atomic (TORN unreachable via crash;
only via out-of-band corruption → CORRUPT → QUARANTINED → BLOCKED).
Deletion only via prune of completed records (safe: completed ⇒ mutation
durably complete; crash before/after removal replays to same conclusion
because completion is IN the journal, not in its absence).

## Crash Matrix (measured, SIGKILL unless noted)
| Boundary | Kill | Recovery | WS consistent | Journal consistent | False success |
|---|---|---|---|---|---|
| before journal publish | ✓ | clean, nothing landed | yes | ABSENT | no |
| during temp write (B) | ✓ | clean/completed/blocked, temp swept | yes | old-or-new complete | no |
| after fsync, before replace (C/D) | ✓ (covered by B test) | same as B | yes | same | no |
| after replace (E–G) | ✓ (mid-apply) | completed:1, 60/60 | yes | complete | no |
| after mutation, before completion-write (H) | ✓ (mid-apply variants) | completed:1 forward | yes | complete | no |
| after completion-write | ✓ (idempotence runs) | clean | yes | completed record | no |
| during deletion (prune) | n/a (completed records only) | clean | yes | — | no |
| corrupt injected (9 cases + fuzz) | n/a | blocked:N, evidence kept | yes (unmutated) | quarantined | no |

## Corruption Handling
9-case battery (empty/truncated/invalid/missing-fields/bad-types/
future-schema/drifted-completed/traversal/NUL+oversize) + 300-seed fuzz
(truncation, byte-corruption, soup, dup-key, oversized): never crash,
never clean/completed/rolled-back on corrupt input, evidence kept,
workspace unmutated. `recover()`×3 idempotent (blocked once, then clean
on evidence-only state, no duplication).

## Durability Guarantees
Guaranteed (POSIX/APFS): atomic visibility (replace), per-file durability
(fsync before replace), temp-sweep safety. Weaker (documented, not
pretended): power-loss rename visibility where dir-fsync is refused;
OS-crash last-commit (SQLite NORMAL, pre-existing). NOT guaranteed:
protection against out-of-band journal edits (fail-closed instead),
multi-writer same-changeset (drift-refusal, pre-existing).

## Compatibility
Journal schema unchanged (same keys); old VALID journals recover
identically. Old corrupt journals that the previous code would have
deleted are now quarantined as blocked (intended behavior change —
the fix). Disposition strings: `clean`/`completed:N` unchanged;
`rolled-back*` removed (no production/test depended on it — verified by
grep; `rollback_changeset` API untouched).

## Performance
Apply: 10 files 5.3→5.1ms, 50 files 27.6→28.1ms, 100 files 80.7→65.6ms
(no regression; one extra rename+fsync per journal write, amortized).

## Security
Quarantine names sanitized (alnum+`-_` ≤48 chars, pinned abspath check);
no journal content in names; same-dir storage (no new exposure);
redaction untouched (single `auth.secrets` implementation reused —
nothing new built); plan paths re-validated with `_norm_rel` at read.

## Tests
- `pytest tests/test_journal_recovery.py` — 15 passed (battery+fuzz+idempotence)
- `pytest tests/reliability/test_killpoints.py` — 5 passed (4 G0 + temp-window)
- `pytest tests/test_workspace.py tests/test_checkpoints.py tests/test_runs_recover.py tests/test_enterprise_integration.py` — 82 passed
- `pytest tests/reliability/` — 31 passed
- full suite — 5431 passed / 17 failed (G0-known set) / 2 xfailed
- `ruff check` on touched files — clean

## Remaining Limitations
- Quarantined evidence accumulates until manual cleanup (prune excludes
  it by design); no retention policy yet (G2).
- `blocked` recovery needs operator tooling (inspect quarantine, decide
  re-apply vs manual repair) — G2 dependency (§22 audit event for
  blocked recovery is logged via `logger.warning`, not yet a durable
  audit record).
- Directory-fsync refusal is silent-degraded by OS (documented, uncommon).
- No multi-writer same-journal protocol (pre-existing drift-refusal stands).
