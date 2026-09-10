"""Graph execution engine — deterministic, observable, durable, governed.

Layer position (per architecture): above the agent loop / subagent
infrastructure, below governance. A model call is a node; this package owns
execution authority: what runs, what waits, what retries, what stops.

Reuse map (do not duplicate):
  - node execution ......... ``wisp.multi_agent.SubagentOrchestrator``
  - static DAG validation .. ``wisp.multi_agent.dag.TaskDAG`` (pattern)
  - durable rows ........... ``wisp.infra.store.UnifiedStore`` DB file
  - run lifecycle .......... ``wisp.runs.record.RunRecord`` states
  - governance ............. ``wisp.auth`` + ``wisp.policy`` (narrow-only)
  - schema validation ...... ``wisp.multi_agent.schema_validator``
  - events ................. ``wisp.core.events`` dict envelope
  - providers .............. ``wisp.providers.protocol.Provider`` (untouched)

Modules:
  types ..... immutable graph-domain types (Graph, nodes, edges, contracts…)
  validator . static validation (structural / contract / governance / resource)
  scheduler . dependency-aware ready-queue (no list-order execution)
  control ... deterministic routing tables, join policies, splitter helpers
  executor .. GraphExecutor (scheduling, budgets, retries, persistence, events)
  store ..... GraphStore (graph_* tables in the UnifiedStore DB file)
  artifacts . content-addressed artifact files + references
  verifier .. verifier-node runners + deterministic gates
  dsl ....... YAML graph definitions
  trace ..... ASCII / JSON / DOT rendering + quality metrics
  reference . coding-agent reference graph
  compat .... legacy orchestrate_* lowered onto graph primitives
  cli ....... ``wisp graph ...`` + REPL ``/graph`` registration
  api ....... typed SDK handle (run / wait / status / cancel / resume / trace)
"""

from wisp.graph.types import (
    BudgetPolicy,
    CycleSpec,
    EdgeMapping,
    Graph,
    GraphArtifact,
    GraphCheckpoint,
    GraphEdge,
    GraphNode,
    GraphPolicy,
    GraphRun,
    JoinPolicy,
    NodeContract,
    NodeFailure,
    NodeResult,
    NodeRun,
    NodeStatus,
    NodeType,
    RetryPolicy,
    Route,
    RunStatus,
    VerificationResult,
)

__all__ = [
    "BudgetPolicy",
    "CycleSpec",
    "EdgeMapping",
    "Graph",
    "GraphArtifact",
    "GraphCheckpoint",
    "GraphEdge",
    "GraphNode",
    "GraphPolicy",
    "GraphRun",
    "JoinPolicy",
    "NodeContract",
    "NodeFailure",
    "NodeResult",
    "NodeRun",
    "NodeStatus",
    "NodeType",
    "RetryPolicy",
    "Route",
    "RunStatus",
    "VerificationResult",
]
