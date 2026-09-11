# PHASE 13 ADJACENT — TOOL IDENTITY PROVENANCE & APPROVAL FORENSICS

## Live Reproduction
Post-hotfix REPL run: `ValueError: refusing provider submission:
tool message has unknown/empty tool_call_id ('')` — the downstream
preflight (kept, never weakened) correctly refusing a malformed state
still created upstream. Preceded by decline, decline, then approved
`spawn_background`. Reproduced bit-for-bit with a scripted harness:
one approved `spawn_background` → `execute()` yields `approval_request`
(ID absent) + `tool_result` (ID `call_SPAWN_1`) → old funnel converted
BOTH to `role:tool` messages → `_build_payload` raised `ValueError('')`.

## First Corruption Boundary
`wisp/core/stateless.py::_turn_inner`, history-append funnel: EVERY event
yielded by `ToolExecutor.execute()` was appended to `tool_results_events`
and serialized as a `role:tool` message with `tr.get("tool_call_id","")`.
`approval_request`, spawn heartbeats (`system_event ⏳…`), and subagent
orchestrator progress events ride that channel for live rendering but
carry no call ID — each became a phantom `""` tool message. The approved
spawn (which emits all three) was the trigger; declines never reach
`execute()` (gate denies first), matching the live order decline,
decline, approve, 400.

## Root Cause
Channel mixing: control-plane events (approval requests, progress,
heartbeats) share the tool-result async channel, and the history funnel
did not discriminate. Second, related: id-less provider calls got a
fresh UUID in the assistant block while results defaulted to `""`.

## Identity Model
- Authoritative provider correlation ID = the assistant `tool_calls[].id`
  issued by the model. Threaded unchanged: call → invocation → result →
  tool message → serialization.
- `tool execution ID / turn ID / task ID / attempt ID / subagent ID /
  request ID`: no parallel ID fields exist on this path; no mapping
  table was needed — nothing is overloaded.
- Id-less provider calls ("not yet applicable", §2 — mocks, some
  gateways): ONE stable ID minted once at intake
  (`_ensure_intake_id`), stamped on the call event, propagated to every
  consumer. Never re-minted, never inferred from position/name. The
  serializer still never invents.
- Failure statuses (ERROR/TIMEOUT/CANCELLED/BLOCKED/BREAKER/EXCEPTION)
  never alter the ID.

## Failure Paths
SUCCESS/ERROR/TIMEOUT/CANCELLED — ID preserved (tested). BLOCKED incl.
user-decline, AUTH_DENIED, SCHEMA_ERROR, plan/danger/perm/hook guards —
ID preserved (tested, incl. the previously ID-less forced-approval
no-handler path, fixed). BREAKER — preserved (tested). EXCEPTION —
preserved (tested). `CancelledError` still propagates (turn ends; no
invalid message).

## Parallelism
Concurrent mixed results keep per-call IDs; out-of-order completion
irrelevant (identity rides the event, never position); no shared
"current call ID" mutable exists on the path (verified by inspection +
tests).

## Retry
G1E semantics kept: a retry is a NEW protocol call with a fresh
authoritative ID; results pair per-attempt; cross-wiring rejected by
the provenance gate (tested both directions).

## Subagents
Child-core traffic keeps child-session IDs; the parent's spawn result
carries only the parent call ID. Progress events that previously leaked
as phantom parent tool messages are now live-only (tested).

## Approval
- Scoping (read, not inferred): per-invocation prompt; `y`/`n` once;
  `Y` adds the tool NAME to session allow-set, `N` to deny-set
  (explicit persistent mode — sanctioned); `a`/`d` flip session policy
  AUTO/BLOCK; `c` raises `ApprovalCancelled` → recorded denial;
  unknown/empty input denies (fail-closed). Pinned by tests.
- Per-turn memo (`_memoize_handler`, keyed (tool,args)): identical
  replay reuses the verdict without re-prompting; different args prompt
  again; fresh memo per turn (tested).
- Observed sequence verdict: PASS. `n` declines persist nothing, so
  the third spawn MUST have prompted (or a prior explicit `Y`/`a`
  applied); declines executed nothing (execution=0 asserted); only the
  approved call dispatched (asserted). No leak: gate-denied calls never
  reach the executor, and memo replays denials as denials.
- Note (P2, not changed): `CLITransport._approval_state` is one
  instance per transport, so `Y`/`N`/`a`/`d` persist for the REPL
  process lifetime across sessions — explicit user-chosen persistence,
  but coarser than the `ApprovalSessionState` ("session") name implies.

## Fix
1. `stateless.py`: funnel appends only `type == "tool_result"` to
   history (live yields untouched — websocket approval + progress UI
   intact).
2. `stateless.py`: `_ensure_intake_id` stamps one stable ID on id-less
   provider calls at both intake points (single + batch fan-out).
3. `stateless.py`: `validate_tool_message_provenance()` (pure) gates
   each appended pair — missing/unknown/duplicate IDs end the turn with
   an explicit protocol-integrity error, never a 400, never a repair.
4. `tool_executor.py`: forced-approval no-handler refusal carries the
   inbound ID (last ID-less emission).
Downstream `_build_payload` preflight untouched (final defense stays).

## Defense in Depth
Upstream provenance gate (origin, named error) + downstream provider
preflight (final, unchanged). Persist path (`runtime._serialize…`)
needs no change: it only consumes `tool_call`/`tool_result` etypes.

## Remaining Unknowns
- Old persisted sessions containing `""` IDs still fail closed at the
  downstream preflight (honest error; previously 400).
- TUI/server approval renderers audited by grep only.
- Mid-stream id-less deltas from exotic gateways now get intake IDs;
  behavior verified by unit test, not live gateway traffic.
