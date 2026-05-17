"""Multi-agent system for Wisp.

Spawn and orchestrate multiple specialized subagents with proper
guards, caching, telemetry, and composable patterns.

Unified Subagent API
--------------------
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
"""

from .roles import AgentRole, ROLE_CONFIGS
from .subagent_orchestrator import MAX_SUBAGENT_DEPTH, SubagentOrchestrator
from .task import (
    EventKind,
    OrchestratorEvent,
    SubagentContract,
    SubagentResult,
)
from .delegation import DelegationAnalyzer, DelegationSignal, get_delegation_analyzer
from .context_partition import ContextPartitioner, partition_context
from .schema_validator import (
    build_retry_prompt,
    extract_json_from_markdown,
    SchemaValidationError,
    validate_json_schema,
    validate_subagent_output,
)

__all__ = [
    "AgentRole",
    "ROLE_CONFIGS",
    "MAX_SUBAGENT_DEPTH",
    "SubagentOrchestrator",
    "SubagentContract",
    "SubagentResult",
    "EventKind",
    "OrchestratorEvent",
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
