"""Multi-agent swarm system for Wisp.

Spawn and orchestrate multiple specialized agents that communicate via
a typed message bus, share a workspace with file locking, and coordinate
through an orchestrator.

Unified Subagent API (v3)
-------------------------
``SubagentOrchestrator`` is the single entry point for all subagent execution:

    from wisp.multi_agent import SubagentOrchestrator, SubagentContract

    orch = SubagentOrchestrator(parent_agent=my_agent)

    # Single subagent
    result = await orch.run(SubagentContract(task="Audit auth.py"))

    # Parallel subagents
    results = await orch.run_parallel([contract1, contract2])

    # Map-reduce
    result = await orch.run_map_reduce(
        task="Review codebase",
        items=["src/auth.py", "src/api.py"],
        mapper=lambda item: SubagentContract(task=f"Review {item}"),
        reducer="Synthesize findings",
    )

    # Voting consensus
    result = await orch.run_vote(
        task="Is this vulnerable?",
        agents=[SubagentContract(name=f"auditor-{i}") for i in range(3)],
        consensus_threshold=0.6,
    )

    # Sequential chain with context passing
    result = await orch.run_chain([
        SubagentContract(name="writer", task="Implement feature"),
        SubagentContract(name="reviewer", task="Review code"),
    ], pass_context=True)

Legacy API (deprecated)
-----------------------
``SwarmOrchestrator`` and ``SubagentRunner`` are deprecated. Use
``SubagentOrchestrator`` for new code.
"""

from .protocol import AgentEvent, EventType, TaskAssignment, TaskResult
from .registry import AgentRegistry, AgentRecord, AgentStatus
from .bus import MessageBus
from .roles import AgentRole, ROLE_CONFIGS
from .agent_factory import AgentFactory
from .workspace_lock import WorkspaceLock
from .orchestrator import SwarmOrchestrator, SwarmResult, SubagentOrchestrator
from .task import (SubagentTask, SubagentContract, SubagentResult,
                   SubagentResult as UnifiedSubagentResult,
                   OrchestratorEvent, EventKind)
from .delegation import DelegationAnalyzer, DelegationSignal, get_delegation_analyzer
from .context_partition import ContextPartitioner, partition_context
from .schema_validator import (
    validate_json_schema,
    validate_subagent_output,
    extract_json_from_markdown,
    build_retry_prompt,
    SchemaValidationError,
)

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
    "SubagentOrchestrator",
    "SubagentTask",
    "SubagentContract",
    "SubagentResult",
    "UnifiedSubagentResult",
    "OrchestratorEvent",
    "EventKind",
    "DelegationAnalyzer",
    "DelegationSignal",
    "get_delegation_analyzer",
    "ContextPartitioner",
    "partition_context",
    "validate_json_schema",
    "validate_subagent_output",
    "extract_json_from_markdown",
    "build_retry_prompt",
    "SchemaValidationError",
]
