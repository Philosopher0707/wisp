# Multi-Agent API & Technical Contracts

The subagent surface is **async-first**: launching never blocks the parent
turn; waiting is an explicit, single synthesis point.

## 1. Tool API (model-facing)

| Tool | Blocking? | Returns |
|---|---|---|
| `fanout {tasks[], max_concurrent?, mode?}` | **No** (default `mode:"background"`) | `{mode:"background", agents:[{agent_id,label,role}], note}` immediately |
| `spawn_background {task, role?, label?…}` | No | `{agent_id, label, status}` |
| `subagent_wait {agent_ids?, timeout_seconds?=600}` | Yes (bounded 1–3600s) | digest: `{settled:[{agent_id,label,role,ok,elapsed_seconds,error?}], still_running:[…], note}` |
| `subagent_result {agent_id, wait_seconds?=0}` | Optional | full state + output |
| `subagent_list` | No | all entries w/ status |
| `subagent_send {agent_id, message}` | No | re-runs a FINISHED agent in-place |
| `subagent_cancel {agent_id}` | No | cancels a RUNNING agent |
| `spawn {task, role?…}` | Yes | foreground result (quick tasks only) |
| `orchestrate_{vote,map_reduce,chain,dag}` | Yes | pattern-specific aggregate |

**Model protocol** (taught in DEFAULT_SYSTEM → "Subagent protocol"):
launch → do independent work → `subagent_wait` once at the synthesis
point → `subagent_result` for detail → report honestly.

## 2. Execution contract

- Children inherit parent `permission_mode`; tools their mode would
  hard-block are removed from advertised schemas
  (`policy_engine.filter_allowed_for_mode`) — no wasted turns on
  `[Blocked: …]`.
- Every child task is grounded with `[Workspace root: …]` preamble;
  lifecycle rendering strips it (`_strip_workspace_preamble`).
- Depth/branch stamps propagate (`_subagent_depth = parent+1`); the
  orchestrator's depth guard bounds recursion.
- Transient failures (429/rate-limit/connection-reset markers) retry ×2
  with backoff ≤6s inside `run_parallel._guarded`; timeout retries go
  through `spawn_with_guards` at ×1.5 bounded by the parent deadline.
- Settlements are honest: success ⇒ `task_completed`, any failure ⇒
  `task_failed` with role-tagged payload. No fake completions.
- Background registry caps at `MAX_RUNNING_AGENTS=8`; launch beyond that
  fails fast with a collect-first error.

## 3. Event contract (`OrchestratorEvent` → `AgentEvent`)

| EventKind | Payload keys | Rendered (unicode mode) |
|---|---|---|
| `task_started` | role, description(preamble-stripped, ≤100 chars + …) | `🧬 [role] first line of real task…` |
| `task_progress` | role, detail | `· [role] detail` |
| `task_retry` | attempt/retry, backoff_seconds | `↻ [role] retry #n in Xs` |
| `task_completed` | elapsed, files_changed[] | `✓ [role] 12.3s · 2 files` |
| `task_failed` | error(≤200) | `✗ [role] error text` |

Background settlements additionally flow through the manager pub-sub
(`agent_started` / `agent_settled`) to the CLI watcher and the server
WebSocket pusher — one ✓/✗ line each, never per-turn chatter.

## 4. CLI display contract

- While children run: the existing spinner/ticker is the ONLY live
  element; warnings emitted mid-spinner are compacted by
  `_SpinnerAwareHandler` to `  W· message` (no timestamps/logger noise).
- Each settled child prints exactly one line (✓/✗ + label + elapsed +
  short summary/error).
- `subagent_wait` renders nothing while polling — silence is the
  progress indicator; the final digest arrives as the tool result.

## 5. Invariants (test-enforced)

1. fanout default path returns before children finish
   (`test_fanout_returns_immediately_with_ids`).
2. Queue-based progress callbacks never leak onto background contracts
   (`test_fanout_strips_progress_callback_on_bg_path`) — nobody drains
   that queue after the blocking generator exits.
3. `mode:"blocking"` and manager-less executors keep the legacy
   aggregate envelope (five pinned tests across spawn_fanout/events/
   bugs/resilience files).
4. Wait-digest reports `still_running` on timeout instead of lying.
