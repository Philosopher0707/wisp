"""Subagent tool — stub implementation.

The actual spawn_subagent logic is handled by the agent core, not the
tool executor. When the agent core processes tool calls, it intercepts
spawn_subagent and routes it to WispAgentCore._spawn_subagent().
"""

import json


def tool_spawn_subagent(**kwargs) -> str:
    """Stub: spawn_subagent is handled by the agent core, not the tool executor.

    When the agent core processes tool calls, it intercepts spawn_subagent
    and routes it to WispAgentCore._spawn_subagent() which creates a
    SubagentContract and delegates to SubagentOrchestrator.

    If you see this message, spawn_subagent was called outside the agent loop
    (e.g. via execute_tool() directly). Use agent.spawn_subagents() instead.
    """
    return json.dumps({
        "status": "error",
        "tool": "spawn_subagent",
        "data": (
            "spawn_subagent must be called through the agent loop. "
            "Use agent.spawn_subagents() or include it in a tool_calls "
            "block from the model response."
        ),
        "metadata": {},
    })
