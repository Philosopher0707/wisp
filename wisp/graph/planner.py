"""Graph planner + compiler (Phase 9).

Pipeline: intent → planner (model, UNTRUSTED) → IR dict → compiler
(deterministic, no model calls) → Graph → validator → policy → approval →
executor. The planner proposes; deterministic infrastructure disposes.

GraphIR is deliberately NOT a new type system: it is the strict
YAML/JSON-compatible dict the existing DSL already parses
(``dsl.graph_from_dict``), plus an ``objective``/``execution_shape``
envelope. No duplicate domain model.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any

from wisp.core.events import canonical_event
from wisp.graph.dsl import graph_from_dict
from wisp.graph.types import Graph, GraphPolicy
from wisp.graph.validator import validate_graph

logger = logging.getLogger(__name__)

IR_SCHEMA_VERSION = "1"
MAX_IR_BYTES = 256_000
EXECUTION_SHAPES = ("SINGLE_AGENT", "GRAPH")

PLANNER_SYSTEM = """You are a graph planning component. You propose structure; you do NOT execute.
You have no authority to execute tools, modify policy, bypass approvals, expand
budgets, or grant capabilities. Policies are enforced externally — request only
what the task plausibly needs.

Return ONLY a single JSON object matching the requested Graph IR schema.
Rules:
- one bounded responsibility per node; explicit inputs consumed from named
  upstream outputs (edges need real data dependencies — sequence is not dependency);
- prefer artifact references (artifact://type) over transcript passing;
- verification must come from a node independent of the generator;
- every cycle needs max_iterations (<=5) and an exit condition;
- every router needs a default route; every join needs an explicit policy;
- set execution_shape to SINGLE_AGENT for small tasks with no parallelism,
  GRAPH only for genuine parallelism, dependency structure, or verification needs;
- keep tools/models/providers minimal; omit what you do not need.
"""

IR_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["graph"],
    "properties": {
        "objective": {"type": "string", "maxLength": 4096},
        "execution_shape": {"type": "string", "enum": list(EXECUTION_SHAPES)},
        "graph": {
            "type": "object",
            "required": ["id", "entry", "nodes"],
            "properties": {
                "id": {"type": "string", "maxLength": 128},
                "entry": {"type": "string", "maxLength": 128},
                "nodes": {"type": "object", "maxProperties": 64},
                "edges": {"type": "array", "maxItems": 256},
                "policies": {"type": "object"},
            },
        },
    },
}


class PlanError(Exception):
    """Typed planning/compilation failure (never a generic execution error)."""

    def __init__(self, code: str, message: str, diagnostics: list[str] | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.diagnostics = diagnostics or []


# ── planner (model-assisted, untrusted output) ────────────────────────

def plan_graph(objective: str, provider: Any, model: str = "",
               extra_constraints: str = "") -> dict[str, Any]:
    """Ask the model for a Graph IR dict. Never executes, never validates."""
    if not objective or len(objective) > 8192:
        raise PlanError("INVALID_PLANNER_OUTPUT", "objective must be 1..8192 chars")
    user = f"Engineering objective:\n{objective}\n"
    if extra_constraints:
        user += f"\nCONSTRAINTS:\n{extra_constraints[:2048]}\n"
    user += ("\nRespond with a Graph IR JSON object. Top-level keys: objective, "
             "execution_shape (SINGLE_AGENT|GRAPH), graph {id, entry, nodes, edges}.")
    messages = [{"role": "user", "content": user}]
    raw: Any = None
    if hasattr(provider, "generate_structured"):
        try:
            raw = provider.generate_structured(PLANNER_SYSTEM, messages, IR_JSON_SCHEMA)
        except NotImplementedError:
            raw = None
        except Exception as exc:
            raise PlanError("PLANNING_FAILED", f"provider error: {exc}") from exc
    if raw is None:
        raw = _parse_text_fallback(provider, messages)
    if not isinstance(raw, dict):
        raise PlanError("INVALID_PLANNER_OUTPUT", "planner did not return a JSON object")
    if len(json.dumps(raw, default=str)) > MAX_IR_BYTES:
        raise PlanError("INVALID_PLANNER_OUTPUT", "planner output exceeds size limit")
    return raw


def _parse_text_fallback(provider: Any, messages: list[dict]) -> Any:
    from wisp.multi_agent.schema_validator import extract_json_from_markdown
    try:
        if hasattr(provider, "generate"):
            out = provider.generate(PLANNER_SYSTEM, messages)
            if isinstance(out, dict):  # BaseProvider shape: {message: {content}}
                text = out.get("message", {}).get("content", "")
            else:
                text = out
        else:
            # ADR-0039 R4: canonicalize through the single authority instead
            # of gating on `isinstance(c, dict)`. That gate silently DISCARDED
            # every typed provider event, leaving empty planner text and an
            # `INVALID_PLANNER_OUTPUT` that looked like a bad model (F40-4).
            chunks = [
                canonical_event(c)
                for c in provider.generate_stream_events(PLANNER_SYSTEM, messages)
            ]
            text = "".join(c.get("text", c.get("content", "")) for c in chunks)
    except Exception as exc:
        raise PlanError("PLANNING_FAILED", f"provider error: {exc}") from exc
    if not isinstance(text, str) or len(text) > MAX_IR_BYTES:
        raise PlanError("INVALID_PLANNER_OUTPUT", "planner text output unusable")
    try:
        return extract_json_from_markdown(text)
    except Exception as exc:
        raise PlanError("INVALID_PLANNER_OUTPUT", f"no JSON in planner output: {exc}") from exc


# ── compiler (deterministic, zero model calls) ────────────────────────

def compile_ir(ir: dict[str, Any], policy: GraphPolicy | None = None) -> tuple[Graph, list[str]]:
    """Lower an IR dict to an executable Graph. Deterministic. Raises PlanError."""
    if not isinstance(ir, dict):
        raise PlanError("COMPILATION_FAILED", "IR must be a mapping")
    shape = ir.get("execution_shape", "GRAPH")
    if shape not in EXECUTION_SHAPES:
        raise PlanError("COMPILATION_FAILED", f"bad execution_shape {shape!r}")
    raw_graph = ir.get("graph")
    if not isinstance(raw_graph, dict):
        raise PlanError("COMPILATION_FAILED", "IR missing graph mapping")
    # Policy narrowing BEFORE parse so the executable graph reflects it.
    narrowed_notes: list[str] = []
    if policy is not None:
        raw_graph, narrowed_notes = narrow_ir(raw_graph, policy)
    try:
        graph = graph_from_dict({"graph": raw_graph})
    except ValueError as exc:
        raise PlanError("COMPILATION_FAILED", str(exc)) from exc
    if shape == "SINGLE_AGENT" and len(graph.nodes) != 1:
        raise PlanError("COMPILATION_FAILED",
                        "SINGLE_AGENT shape requires exactly one node")
    errors = validate_graph(graph)
    if errors:
        raise PlanError("COMPILATION_FAILED", "; ".join(errors[:5]), errors)
    return graph, narrowed_notes


def narrow_ir(raw_graph: dict[str, Any], policy: GraphPolicy) -> tuple[dict[str, Any], list[str]]:
    """Intersect planner requests with host policy. Requests, never decisions.

    Drops/rejects: tools, models, providers, workspace outside policy; clamps
    budgets/concurrency/retries. Raises PlanError(POLICY_REJECTED) when the
    narrowed graph would be invalid or meaningless.
    """
    import copy
    notes: list[str] = []
    g = copy.deepcopy(raw_graph)
    nodes = g.get("nodes", {})
    if not isinstance(nodes, dict):
        raise PlanError("POLICY_REJECTED", "nodes must be a mapping")
    for nid, ndef in nodes.items():
        if not isinstance(ndef, dict):
            raise PlanError("POLICY_REJECTED", f"nodes.{nid} must be a mapping")
        tools = ndef.get("allowed_tools", ndef.get("contract", {}).get("allowed_tools", ("all",)))
        if isinstance(tools, str):
            tools = [tools]
        if "all" not in policy.allowed_tools and "all" in list(tools or []):
            raise PlanError("POLICY_REJECTED",
                            f"nodes.{nid}: allowed_tools='all' forbidden by policy")
        kept = [t for t in (tools or []) if t in policy.allowed_tools] \
            if "all" not in policy.allowed_tools else list(tools or [])
        dropped = [t for t in (tools or []) if t not in kept]
        if dropped:
            notes.append(f"nodes.{nid}: tools narrowed, dropped {dropped}")
        if not kept:
            raise PlanError("POLICY_REJECTED",
                            f"nodes.{nid}: no tools remain after narrowing")
        ndef["allowed_tools"] = kept
        model = ndef.get("model", {})
        if isinstance(model, dict):
            if policy.allowed_models and model.get("model") \
                    and model["model"] not in policy.allowed_models:
                raise PlanError("POLICY_REJECTED",
                                f"nodes.{nid}: model {model['model']!r} forbidden")
            if policy.allowed_providers and model.get("provider") \
                    and model["provider"] not in policy.allowed_providers:
                raise PlanError("POLICY_REJECTED",
                                f"nodes.{nid}: provider {model['provider']!r} forbidden")
            model.pop("fallback", None)  # fallbacks re-resolve via host policy at runtime
        retry = ndef.get("retry", {})
        if isinstance(retry, dict) and "max_attempts" in retry:
            try:
                want = int(retry["max_attempts"])
            except (TypeError, ValueError):
                raise PlanError("POLICY_REJECTED", f"nodes.{nid}: bad max_attempts") from None
            allow = min(want, policy.max_retries + 1)
            if allow < want:
                notes.append(f"nodes.{nid}: max_attempts clamped {want}->{allow}")
            retry["max_attempts"] = allow
        for key in ("function", "module", "callable", "import", "command", "shell",
                    "workspace", "approved", "approval"):
            if key in ndef:
                raise PlanError("POLICY_REJECTED",
                                f"nodes.{nid}: field {key!r} is host-controlled")
    pol = g.get("policies", {}) if isinstance(g.get("policies"), dict) else {}
    budget = pol.get("budget", {}) if isinstance(pol.get("budget"), dict) else {}
    for k, cap in (("max_tokens", policy.max_tokens), ("max_cost_usd", policy.max_cost_usd),
                   ("max_runtime_s", policy.max_runtime_s)):
        if cap is not None and k in budget:
            try:
                if float(budget[k]) > float(cap):
                    notes.append(f"budget.{k} clamped {budget[k]}->{cap}")
                    budget[k] = cap
            except (TypeError, ValueError):
                raise PlanError("POLICY_REJECTED", f"budget.{k} malformed") from None
    if budget:
        pol["budget"] = budget
    conc = pol.get("concurrency", {}) if isinstance(pol.get("concurrency"), dict) else {}
    if "graph" in conc:
        try:
            want_c = int(conc["graph"])
        except (TypeError, ValueError):
            raise PlanError("POLICY_REJECTED", "concurrency.graph malformed") from None
        if want_c > policy.max_concurrency:
            notes.append(f"concurrency.graph clamped {want_c}->{policy.max_concurrency}")
            conc["graph"] = policy.max_concurrency
        pol["concurrency"] = conc
    if policy.workspace:
        pol["workspace"] = policy.workspace  # host workspace always wins
    if pol:
        g["policies"] = pol
    return g, notes


# ── quality heuristics (advisory; deterministic) ──────────────────────

def quality_score(graph: Graph) -> dict[str, Any]:
    """Advisory diagnostics: fake deps, granularity, verification, fanout..."""
    from wisp.graph.types import JoinPolicy, NodeType
    findings: list[str] = []
    # Fake dependencies: linear chain where mappings are empty.
    empty_maps = sum(1 for e in graph.edges if not e.mapping)
    if len(graph.edges) > 2 and empty_maps == len(graph.edges):
        findings.append("OPT-001 unnecessary serialization: no edge carries data")
    # Giant nodes: description-only contracts with huge prompts.
    for n in graph.nodes:
        prompt = str(n.config.get("prompt", ""))
        if len(prompt) > 4000:
            findings.append(f"OPT-003 oversized context transfer at {n.id}")
    # Missing verification on multi-agent graphs.
    agents = [n for n in graph.nodes if n.type == NodeType.AGENT]
    verifiers = [n for n in graph.nodes if n.type == NodeType.VERIFIER]
    if len(agents) > 2 and not verifiers:
        findings.append("OPT-002 missing independent verification")
    # Excessive fanout.
    if len(graph.nodes) > 32:
        findings.append("OPT-004 excessive graph size")
    joins_without_policy = [n.id for n in graph.nodes
                            if n.type == NodeType.JOIN
                            and n.join_policy == JoinPolicy.ALL and not any(
                                e.to_node == n.id and not e.condition for e in graph.edges)]
    _ = joins_without_policy
    score = max(0, 100 - 15 * len(findings))
    return {"score": score, "findings": findings,
            "nodes": len(graph.nodes), "edges": len(graph.edges),
            "verifiers": len(verifiers), "parallelism": len(graph.nodes) - len(graph.edges)}


def ir_hash(ir: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(ir, sort_keys=True, default=str).encode()).hexdigest()[:32]


# ── fingerprint-pinned execution (9F) ─────────────────────────────────

async def execute_proposal(proposal: dict[str, Any], executor: Any,
                           inputs: dict[str, Any] | None = None,
                           approve: bool = False) -> dict[str, Any]:
    """Execute the EXACT approved graph. approve must be True (never truthy).

    Recompiles the stored IR and rejects on fingerprint drift — approval can
    never slide onto a regenerated graph.
    """
    from wisp.graph.audit import GraphSecurityAuditor
    if approve is not True:
        raise PlanError("APPROVAL_REQUIRED", "execution requires explicit approval")
    ir = proposal.get("ir")
    if not isinstance(ir, dict):
        raise PlanError("COMPILATION_FAILED", "proposal has no IR")
    policy = None  # narrowing already baked into the stored IR; recompile as-is
    try:
        graph, _, _, _ = compile_optimized(ir, policy)
    except PlanError as exc:
        raise PlanError("COMPILATION_FAILED", f"stored IR no longer compiles: {exc}") from exc
    if graph.fingerprint() != proposal.get("graph_hash", ""):
        try:
            GraphSecurityAuditor(workspace=getattr(executor, "workspace", ".")).emit(
                "graph.proposal_decided", graph_id=graph.id,
                graph_hash=graph.fingerprint(), run_id="",
                allowed=False, reason="fingerprint drift since approval",
                evidence={"approved": str(proposal.get("graph_hash", ""))[:64]})
        except Exception:
            pass
        raise PlanError("STALE_PROPOSAL", "graph changed since approval; re-propose")
    try:
        GraphSecurityAuditor(workspace=getattr(executor, "workspace", ".")).emit(
            "graph.proposal_decided", graph_id=graph.id,
            graph_hash=graph.fingerprint(), run_id="",
            allowed=True, reason="approved fingerprint; executing",
            evidence={"proposal_id": str(proposal.get("proposal_id", ""))[:64]})
    except Exception:
        pass
    return await executor.run(graph, inputs or {})

# ── proposals (persisted, fingerprint-pinned) ─────────────────────────

STATUSES = ("PROPOSED", "VALIDATED", "POLICY_CHECKED", "APPROVAL_REQUIRED",
            "APPROVED", "INVALID", "REJECTED", "EXPIRED", "EXECUTED")


def compile_optimized(ir: dict[str, Any], policy: GraphPolicy | None = None,
                      context: Any = None
                      ) -> tuple[Graph, list[str], dict[str, Any], dict[str, Any]]:
    """Preferred pipeline: compile → optimize → revalidate → fingerprint.

    Returns (final graph, diagnostics, optimizer meta, narrowed IR).
    The narrowed IR (not the raw planner output) is what proposals store and
    execution recompiles — otherwise narrowing itself would look like drift.
    Raises PlanError on any failure. Deterministic.
    """
    import copy
    from wisp.graph.optimizer import OptimizationContext, optimize_graph
    graph, notes = compile_ir(ir, policy)
    ctx = context or OptimizationContext(policy=policy)
    res = optimize_graph(graph, policy, ctx)
    if res.status == "REJECTED":
        raise PlanError("COMPILATION_FAILED",
                        f"optimization rejected: {res.error}",
                        [res.error])
    diags = list(notes)
    for d in res.diagnostics:
        diags.append(f"{d.get('code', '')} {d.get('node', '')}: "
                     f"{d.get('reason', '')}"[:500])
    narrowed = copy.deepcopy(ir)
    if policy is not None and isinstance(narrowed.get("graph"), dict):
        narrowed_graph, _ = narrow_ir(narrowed["graph"], policy)
        narrowed["graph"] = narrowed_graph
    return res.graph, diags, res.meta, narrowed

def new_proposal_id() -> str:
    return f"prop-{uuid.uuid4().hex[:12]}"


def propose(objective: str, provider: Any, policy: GraphPolicy | None = None,
            model: str = "", proposal_id: str = "") -> dict[str, Any]:
    """Full propose path without execution: plan → compile → validate → narrow."""
    ir = plan_graph(objective, provider, model)
    return compile_proposal(ir, objective, policy, provider, model, proposal_id)


def compile_proposal(ir: dict[str, Any], objective: str = "",
                     policy: GraphPolicy | None = None, provider: Any = None,
                     model: str = "", proposal_id: str = "") -> dict[str, Any]:
    """Deterministic half of propose(); split out for testing without models."""
    from wisp.graph.security import scrub
    diags: list[str] = []
    status = "PROPOSED"
    graph: Graph | None = None
    meta: dict[str, Any] = {}
    stored_ir = ir
    try:
        graph, opt_diags, meta, stored_ir = compile_optimized(ir, policy)
        diags.extend(opt_diags)
        status = "POLICY_CHECKED" if policy else "VALIDATED"
    except PlanError as exc:
        return {"proposal_id": proposal_id or new_proposal_id(),
                "status": "INVALID" if exc.code == "COMPILATION_FAILED" else "REJECTED",
                "diagnostics": [str(exc), *exc.diagnostics],
                "ir_hash": ir_hash(ir), "graph_hash": ""}
    quality = quality_score(graph)
    return {"proposal_id": proposal_id or new_proposal_id(),
            "objective": scrub(objective)[:2048],
            "planner_model": scrub(str(model))[:256],
            "planner_provider": scrub(str(getattr(provider, "__class__", type(provider)).__name__))[:128],
            "ir_hash": ir_hash(stored_ir),
            "graph_hash": graph.fingerprint(),
            "graph_id": graph.id,
            "nodes": len(graph.nodes), "edges": len(graph.edges),
            "quality": quality,
            "diagnostics": [scrub(d)[:500] for d in diags],
            "transports": meta.get("artifact_transport", {}).get("transports", {}),
            "status": "APPROVAL_REQUIRED" if status in ("VALIDATED", "POLICY_CHECKED") else status,
            "ir": stored_ir, "created_at": time.time()}
