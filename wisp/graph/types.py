"""Graph-domain types. Immutable (frozen dataclasses) where practical.

A node has one small responsibility, explicit input, structured output, and
a named failure state. Failures are VALUES (NodeFailure), not exceptions —
exceptions are reserved for runtime crashes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeType(str, Enum):
    AGENT = "agent"        # LLM-backed work; executed via injected runner
    FUNCTION = "function"  # deterministic local code (merge, split, format)
    JOIN = "join"          # fan-in decision point with a JoinPolicy
    ROUTER = "router"      # classifier label -> deterministic route table
    VERIFIER = "verifier"  # ALLOW / REJECT / RETRY / ESCALATE, evidence-based
    GATE = "gate"          # pure deterministic predicate over run state
    APPROVAL = "approval"  # human gate; pauses the run until decided


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JoinPolicy(str, Enum):
    ALL = "all"                  # every branch settles successfully
    ANY = "any"                  # first settled branch releases
    QUORUM = "quorum"            # N successful branches (param n)
    MIN_SUCCESS = "min_success"  # at least N successes, failures tolerated
    BEST_EFFORT = "best_effort"  # all settle; continue with successes
    STREAMING = "streaming"      # no barrier; downstream consumes each result


TERMINAL_NODE_STATUSES = frozenset(
    {NodeStatus.SUCCESS, NodeStatus.FAILURE, NodeStatus.TIMEOUT,
     NodeStatus.CANCELLED, NodeStatus.SKIPPED}
)
FAILED_NODE_STATUSES = frozenset(
    {NodeStatus.FAILURE, NodeStatus.TIMEOUT, NodeStatus.CANCELLED}
)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    backoff_base_s: float = 2.0
    backoff_max_s: float = 30.0
    retry_on_timeout: bool = False
    # Non-idempotent side effects must never auto-retry.
    unsafe_side_effects: bool = False

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")


@dataclass(frozen=True)
class BudgetPolicy:
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_runtime_s: float | None = None
    max_attempts: int | None = None


@dataclass(frozen=True)
class ModelPolicy:
    model_class: str = "standard"  # cheap | standard | strong
    provider: str | None = None
    model: str | None = None
    fallback_chain: tuple[str, ...] = ()


@dataclass(frozen=True)
class NodeContract:
    """The fundamental invariant: explicit input, structured output, named failure."""

    id: str
    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    failure_schema: dict[str, Any] = field(default_factory=dict)
    allowed_tools: tuple[str, ...] = ("all",)
    model_policy: ModelPolicy = field(default_factory=ModelPolicy)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_s: float = 300.0
    budget: BudgetPolicy = field(default_factory=BudgetPolicy)
    permissions: tuple[str, ...] = ()
    # Retry safety: False -> never auto-retry; resume marks interrupted as cancelled.
    idempotent: bool = True


@dataclass(frozen=True)
class GraphNode:
    id: str
    type: NodeType = NodeType.AGENT
    contract: NodeContract | None = None
    # For FUNCTION/GATE nodes: dotted path resolved by the host app.
    function: str = ""
    # For JOIN nodes.
    join_policy: JoinPolicy = JoinPolicy.ALL
    join_param: int = 0
    join_timeout_s: float | None = None
    # For ROUTER nodes: label -> node id. Missing label -> "unknown" -> default.
    routes: dict[str, str] = field(default_factory=dict)
    default_route: str = ""
    # For controlled cycles: node ids forming the loop body + exit gate id.
    cycle: CycleSpec | None = None
    # Static prompt / config template (small; large context flows via artifacts).
    config: dict[str, Any] = field(default_factory=dict)

    def effective_contract(self) -> NodeContract:
        if self.contract is not None:
            return self.contract
        return NodeContract(id=self.id, name=self.id)


@dataclass(frozen=True)
class CycleSpec:
    entry: str
    body: tuple[str, ...]
    exit_gate: str
    max_iterations: int = 3


@dataclass(frozen=True)
class EdgeMapping:
    """Why an edge exists: downstream consumes upstream output.

    ``mapping`` maps downstream-input-path -> upstream-output-path, e.g.
    {"findings": "output.findings"}. ``reason`` is mandatory — an edge the
    planner cannot justify is rejected (no accidental serialisation).
    """

    from_node: str
    to_node: str
    reason: str = ""
    mapping: dict[str, str] = field(default_factory=dict)
    # Conditional edge: taken only when predicate(label) matches.
    # Used for verifier.accept / verifier.reject.<lane> / router labels.
    condition: str = ""


GraphEdge = EdgeMapping


@dataclass(frozen=True)
class Route:
    label: str
    target: str


@dataclass(frozen=True)
class GraphPolicy:
    """Narrow-only: a graph can never elevate authority beyond active policy."""

    allowed_nodes: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ("all",)
    allowed_models: tuple[str, ...] = ()
    allowed_providers: tuple[str, ...] = ()
    max_depth: int = 8
    max_nodes: int = 64
    max_concurrency: int = 8
    max_retries: int = 2
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_runtime_s: float | None = None
    workspace: str = ""
    approval_required: tuple[str, ...] = ()


@dataclass(frozen=True)
class Graph:
    id: str
    version: str = "1"
    entrypoint: str = ""
    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[EdgeMapping, ...] = ()
    policies: GraphPolicy = field(default_factory=GraphPolicy)

    def node_map(self) -> dict[str, GraphNode]:
        return {n.id: n for n in self.nodes}

    def fingerprint(self) -> str:
        payload = json.dumps(
            {"id": self.id, "version": self.version,
             "nodes": sorted(n.id for n in self.nodes),
             "edges": sorted(f"{e.from_node}->{e.to_node}:{e.condition}" for e in self.edges)},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass
class NodeResult:
    node_id: str
    status: NodeStatus = NodeStatus.SUCCESS
    output: dict[str, Any] = field(default_factory=dict)
    artifact_ids: list[str] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0
    attempt: int = 1
    error_code: str = ""
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status == NodeStatus.SUCCESS

    def usage(self) -> dict[str, Any]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cost_usd": self.cost_usd, "duration_s": self.duration_s}


@dataclass
class NodeFailure:
    """Typed failure state — a value the graph decides on, not an exception."""

    node_id: str
    failure_code: str = "ERROR"
    retryable: bool = False
    message: str = ""
    evidence_artifact: str = ""
    attempt: int = 1


@dataclass
class VerificationResult:
    decision: str = "REJECT"  # ALLOW | REJECT | RETRY | ESCALATE
    reason_codes: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    message: str = ""

    @property
    def accepted(self) -> bool:
        return self.decision == "ALLOW"


@dataclass
class GateResult:
    allowed: bool
    reason: str = ""
    evidence: list[str] = field(default_factory=list)
    failure_code: str = ""


@dataclass
class GraphArtifact:
    artifact_id: str
    run_id: str
    node_run_id: str
    type: str = "blob"
    schema_version: str = "1"
    content_hash: str = ""
    uri: str = ""
    producer: str = ""
    created_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphCheckpoint:
    run_id: str
    seq: int
    state: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0


@dataclass
class NodeRun:
    node_run_id: str
    run_id: str
    node_id: str
    status: NodeStatus = NodeStatus.PENDING
    attempt: int = 0
    input_hash: str = ""
    result: NodeResult | None = None
    failure: NodeFailure | None = None
    started_at: float = 0.0
    finished_at: float = 0.0


@dataclass
class GraphRun:
    run_id: str
    graph_id: str
    graph_version: str = "1"
    graph_hash: str = ""
    status: RunStatus = RunStatus.QUEUED
    inputs: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    error: str = ""
