"""Per-execution context for the process-wide shared ToolExecutor.

The composition root wires ONE ToolExecutor to the root core and to every
subagent child core (wisp/composition.py `_create_core` + wisp/multi_agent/
_runner.py). `ToolExecutor.execute()` can therefore run for many agents at
once — parent blocking-spawn while a background child executes, two TUI
sessions, a nested fanout. Anything per-call must therefore NOT live on the
executor instance (concurrent calls clobber each other); it lives here, in
ContextVars, which give:

  * task-local isolation — two concurrent execute() calls never see each
    other's values;
  * inheritance into spawned tasks — a child's execute() running inside a
    fanout keeps the parent's stream queue, so nested lifecycle events still
    reach the parent's render loop (the old instance-field design achieved
    the same thing by accident, and broke the moment two streams ran at
    once);
  * a config fallback for direct executor users (tests, ACP) that never go
    through the engine — they carry their agent identity in
    WispConfig._subagent_depth instead (see `_exec_depth()` in
    wisp/tool_executor.py).

Setters:
  * agent_depth / agent_branch — WispAgentCore.turn() at turn start, from
    the executing agent's own config (wisp/core/stateless.py).
  * sub_event_queue — ToolExecutor.execute() streaming branch, for the
    duration of one spawn/fanout/orchestrate call.
  * repeat_key — ToolExecutor._check_repeat_call, consumed by
    _record_repeat_result in the same task.

Full contract: docs/tool-calling-contracts.md.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wisp.core.events import AgentEvent

# Queue a spawn/fanout/orchestrate call's lifecycle events flow through
# while that call is in flight (see ToolExecutor.execute streaming branch).
sub_event_queue: ContextVar[asyncio.Queue[AgentEvent] | None] = ContextVar(
    "wisp_sub_event_queue", default=None
)

# (tool, args) identity of a guarded tool call awaiting its result, so the
# repeat guard caches under the call that produced it, not a concurrent
# sibling's.
repeat_key: ContextVar[str | None] = ContextVar("wisp_repeat_key", default=None)

# Nesting identity of the agent whose turn is currently executing: 0 for
# the top-level agent, N for the N-th level subagent. Set by the engine
# from the executing agent's config at turn start. Default is None (NOT 0)
# so a caller that never goes through the engine (direct executor use in
# tests / ACP) is distinguishable from a genuine top-level agent — the
# reader then falls back to the executor's own config for that identity.
agent_depth: ContextVar[int | None] = ContextVar("wisp_agent_depth", default=None)
agent_branch: ContextVar[int | None] = ContextVar("wisp_agent_branch", default=None)
