"""Multi-agent swarm system for Wisp.

Spawn and orchestrate multiple specialized agents that communicate via
a typed message bus, share a workspace with file locking, and coordinate
through an orchestrator.

Example:
    from wisp.multi_agent import SwarmOrchestrator, AgentRole

    orch = SwarmOrchestrator(workspace="/path/to/project")
    orch.spawn_agents([
        AgentRole.CODER,
        AgentRole.REVIEWER,
        AgentRole.TESTER,
    ])
    result = orch.run("Implement a REST API for user management")
"""

from .protocol import AgentEvent, EventType, TaskAssignment, TaskResult
from .registry import AgentRegistry, AgentRecord, AgentStatus
from .bus import MessageBus
from .roles import AgentRole, ROLE_CONFIGS
from .agent_factory import AgentFactory
from .workspace_lock import WorkspaceLock
from .orchestrator import SwarmOrchestrator, SwarmResult
from .task import (SubagentTask, SubagentResult as UnifiedSubagentResult,
                   OrchestratorEvent, EventKind)

__all__ = [
    "AgentEvent",
    "EventType",
    "TaskAssignment",
    "TaskResult",
    "AgentRegistry",
    "AgentRecord",
    "AgentStatus",
    "MessageBus",
    "AgentRole",
    "ROLE_CONFIGS",
    "AgentFactory",
    "WorkspaceLock",
    "SwarmOrchestrator",
    "SwarmResult",
    "SubagentTask",
    "UnifiedSubagentResult",
    "OrchestratorEvent",
    "EventKind",
]
