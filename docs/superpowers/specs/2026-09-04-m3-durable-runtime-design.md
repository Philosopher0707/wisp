# M3 Design: Durable Local Runtime (rev 1)

Date: 2026-09-04. Parent: ADR `2026-09-04-durable-background-runs.md`.
Mode: semantics over existing substrate; gate green after every commit.

## 0. Ground truth (no reinvention)

- `UnifiedStore` already owns `runs`, `background_runs`, `events`,
  `session_events`, `idempotency` tables + CRUD (`wisp/infra/store.py`).
- `BackgroundAgentManager` keeps source of truth in `self._entries`
  (process memory); never touches the store.
- `SandboxProvider` ABC + `DockerSandbox`/`NoopSandbox` already exist
  (`wisp/sandbox.py:20,47,169`) — M3 only pins the interface with tests.
- M1a `Transition` envelope is the transition-row shape; M1a `RunStatus`
  (4 states) is the produced vocabulary; the 8-state machine below extends
  it (superset mapping: running→running, completed→succeeded, etc.).

## 1. New package `wisp/runs/`

| Module | Contents |
|---|---|
| `record.py` | `RunRecord` (frozen dataclass mirroring `background_runs` columns + `lease_owner`, `lease_expires`, `idempotency_key`); `LEGAL_TRANSITIONS` (8-state machine); `is_legal(from, to)` |
| `store.py` | `RunStore` ABC (create/get/update/list/record_transition/transitions/claim_lease/idempotent_get/put) + `SQLiteRunStore` over `UnifiedStore` + new `run_transitions` table via additive migration in `store.py` |
| `scheduler.py` | Bounded admission (`max_running`), lease acquire/heartbeat/release, idempotency-key guard — pure logic over `RunStore`, no threads |
| `compensation.py` | `EditRecord` (path, unified diff, pre-image hash) + `rollback_preview()` (pure); `reversibility(tool_name)` declaration table |
| `repro.py` | `ReproManifest` (wisp version, model/provider, policy-bundle id, plugin/MCP versions, workspace commit, input/output hashes) — pure |

## 2. Integration points (narrow)

- **J1 — manager persistence:** `launch` inserts a `RunRecord`
  (status `queued→running`), settlement/cancel updates + records a
  transition; new `recover()` lists non-terminal rows at startup and marks
  rows with expired leases `paused` (never auto-resumes effects).
- **J2 — scheduler admission:** `launch` consults `scheduler.admit()` instead
  of the manual `len(running)` count (same limit semantics, durable counts).
- No changes to orchestration, turn engine, or executor dispatch.

## 3. Restart rules (per state)

`queued/planning` → re-enter; `running` → resume from checkpoint +
idempotency keys (effects never replay); `awaiting_approval` → re-present
recorded decision; `paused` → explicit resume only; terminal
(`succeeded/failed/cancelled`) → immutable (transition rejected).

## 4. Tests

`test_runs_record.py` (transition legality incl. terminal immutability),
`test_runs_store.py` (tmp SQLite: CRUD, transitions append-only, lease
expiry, idempotency round-trip), `test_runs_recover.py` (manager recover
marks stale running rows paused; settlement persists), `test_runs_scheduler.py`
(admission bound, lease lifecycle), `test_runs_compensation.py`
(rollback preview from diff), `test_runs_repro.py` (manifest hash stability).

## 5. Deferred

Postgres implementation (M4+), container/VM sandbox backends beyond the
existing Docker adapter, automatic effect replay on resume (replay decisions,
not effects — architectural invariant).
